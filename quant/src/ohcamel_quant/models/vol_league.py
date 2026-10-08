"""P2: volatility forecast league (compute plan M3).

Forecasts are out of sample: each day's variance forecast uses returns up to
the previous close. GARCH-family parameters are refitted every ``refit_every``
sessions on the last ``est_window`` sessions (kernel ``garch_fit``, Student-t)
and filtered forward with fixed parameters between refits. ``variance_path``
is the kernels' own recursion (native/kernels/src/garch.rs, reference
``kernels.reference._variance_path``), pinned equal to the kernel's
``next_variance`` by a test. QLIKE (Patton 2011) is robust to a noisy RV
proxy, so names without minute bars are scored on squared daily returns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats
from scipy.signal import lfilter

from .. import kernels
from ..factors.hac import newey_west_lrv, nw_lag_rule
from ..risk.core import ewma_variance_path

MODELS = ("garch", "gjr", "egarch", "har", "ewma")
GARCH_KINDS = ("garch", "gjr", "egarch")
EST_WINDOW = 1000
HAR_WINDOW = 250
EVAL_DAYS = 250
REFIT_EVERY = 21
EWMA_LAMBDA = 0.94
MCS_SIZE = 0.10
SQRT2_OV_PI = math.sqrt(2.0 / math.pi)
LNSIGMA_MAX = math.log(np.finfo(float).max)


def _backcast(y: np.ndarray) -> float:
    e = y - y.mean()
    tau = min(75, e.size)
    w = 0.94 ** np.arange(tau)
    return float(np.sum(e[:tau] ** 2 * w / w.sum()))


def variance_path(params: np.ndarray, y: np.ndarray, kind: str) -> np.ndarray:
    """sigma^2_t for t = 0..n (percent^2): element t uses y[:t]; element n forecasts the day after y.
    The backcast uses the first 75 observations, so extending ``y`` never moves earlier elements."""
    p = np.asarray(params, dtype=float)
    y = np.asarray(y, dtype=float)
    mu, om, n = float(p[0]), float(p[1]), y.size
    bc = _backcast(y)  # reads y[:75] only: the same value as the kernel's backcast over the fitted window
    s2 = np.empty(n + 1)
    if kind == "egarch":
        al, ga, be = float(p[2]), float(p[3]), float(p[4])
        lns = om + be * math.log(bc)
        s2[0] = math.exp(lns)
        for t in range(1, n + 1):
            if not s2[t - 1] > 0.0:          # underflow: infeasible from here on (the kernel returns None)
                s2[t:] = np.nan
                break
            z = (y[t - 1] - mu) / math.sqrt(s2[t - 1])
            lns = min(om + al * (abs(z) - SQRT2_OV_PI) + ga * z + be * lns, LNSIGMA_MAX)
            s2[t] = math.exp(lns)
        return s2
    al = float(p[2])
    ga = float(p[3]) if kind == "gjr" else 0.0
    be = float(p[-2])
    e = y - mu
    s2[0] = om + (al + 0.5 * ga + be) * bc
    u = om + (al + ga * (e < 0)) * e * e
    s2[1:], _ = lfilter([1.0], [1.0, -be], u, zi=[be * s2[0]])
    return s2


def garch_oos(r: np.ndarray, kind: str, *, est_window: int = EST_WINDOW, eval_days: int = EVAL_DAYS,
              refit_every: int = REFIT_EVERY) -> tuple[np.ndarray, float]:
    """1-day-ahead variance forecasts (DECIMAL^2) for the last ``eval_days`` days, and the next-day forecast."""
    y = 100.0 * np.asarray(r, dtype=float)
    n = y.size
    start = n - eval_days
    if start < est_window:
        raise ValueError(f"need {est_window + eval_days} returns; have {n}")
    out = np.empty(eval_days)
    x0 = None
    for s in range(start, n, refit_every):
        fit = kernels.garch_fit(y[s - est_window:s], kind, x0)
        x0 = fit.params
        e = min(s + refit_every, n)
        out[s - start:e - start] = variance_path(fit.params, y[s - est_window:e], kind)[est_window:est_window + e - s]
    fit = kernels.garch_fit(y[n - est_window:], kind, x0)
    return out / 1e4, float(fit.next_variance) / 1e4


def ewma_oos(r: np.ndarray, eval_days: int, lam: float = EWMA_LAMBDA) -> tuple[np.ndarray, float]:
    path = ewma_variance_path(np.asarray(r, dtype=float), lam)  # element t forecasts day t (risk.core)
    n = len(r)
    return path[n - eval_days:n], float(path[n])


def _har_x(rv: np.ndarray, t: int) -> np.ndarray:
    return np.array([1.0, rv[t], rv[t - 4:t + 1].mean(), rv[t - 21:t + 1].mean()])


def _ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(X, y, rcond=None)[0]


def har_oos(rv: np.ndarray, *, window: int = HAR_WINDOW, eval_days: int = EVAL_DAYS,
            refit_every: int = REFIT_EVERY) -> tuple[np.ndarray, float]:
    """HAR-RV (Corsi 2009): rv_{t+1} on [1, rv_t, mean 5d, mean 22d], OLS on the last ``window`` pairs
    known before each refit. Forecasts are floored at the smallest positive rv in the fitting window."""
    rv = np.asarray(rv, dtype=float)
    n = rv.size
    start = n - eval_days
    if start - window < 22:
        raise ValueError(f"HAR needs {window + eval_days + 22} days of realized variance; have {n}")
    out = np.empty(eval_days)
    beta, floor = None, None

    def fit(s: int) -> tuple[np.ndarray, float]:
        ts = np.arange(s - window - 1, s - 1)              # pairs (x_t, rv_{t+1}) with t + 1 <= s - 1
        b = _ols(np.vstack([_har_x(rv, t) for t in ts]), rv[ts + 1])
        pos = rv[s - window:s]
        return b, float(pos[pos > 0].min())

    for d in range(start, n):
        if (d - start) % refit_every == 0:
            beta, floor = fit(d)
        out[d - start] = max(float(_har_x(rv, d - 1) @ beta), floor)
    beta, floor = fit(n)
    return out, max(float(_har_x(rv, n - 1) @ beta), floor)


def har_22d(rv: np.ndarray, window: int = HAR_WINDOW) -> float:
    """Direct monthly HAR: mean rv over the next 22 days on the same regressors; the latest forecast (daily units)."""
    rv = np.asarray(rv, dtype=float)
    n = rv.size
    ts = np.arange(max(21, n - 22 - window), n - 22)
    y = np.array([rv[t + 1:t + 23].mean() for t in ts])
    b = _ols(np.vstack([_har_x(rv, t) for t in ts]), y)
    pos = rv[-window:]
    return max(float(_har_x(rv, n - 1) @ b), float(pos[pos > 0].min()))


def qlike(rv: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Patton (2011) QLIKE in the form ln F + RV/F. It differs from RV/F - ln(RV/F) - 1 only by a term in RV
    alone, so model rankings, DM tests and the MCS are identical, and it stays finite on a day with RV = 0
    (a zero daily return). Minimised over F at F = RV."""
    rv, f = np.asarray(rv, dtype=float), np.asarray(f, dtype=float)
    return np.log(f) + rv / f


def mse(rv: np.ndarray, f: np.ndarray) -> np.ndarray:
    return (np.asarray(rv, dtype=float) - np.asarray(f, dtype=float)) ** 2


def dm_test(la: np.ndarray, lb: np.ndarray) -> dict[str, float]:
    """Diebold-Mariano (1995): t-stat of mean(la - lb) with Newey-West variance; two-sided normal p-value.
    Negative stat: model a has the smaller loss."""
    d = np.asarray(la, dtype=float) - np.asarray(lb, dtype=float)
    n = d.size
    lrv = float(newey_west_lrv((d - d.mean())[:, None], nw_lag_rule(n))[0, 0])
    if lrv <= 0:
        return {"stat": 0.0 if d.mean() == 0 else math.copysign(math.inf, d.mean()), "pvalue": float(d.mean() == 0)}
    stat = float(d.mean() / math.sqrt(lrv / n))
    return {"stat": stat, "pvalue": float(2.0 * stats.norm.sf(abs(stat)))}


def mcs_members(losses: pd.DataFrame, size: float = MCS_SIZE, seed: int = 7) -> set[str]:
    """Hansen, Lunde & Nason (2011) Model Confidence Set at level ``size`` (``arch``'s MCS, range statistic,
    stationary bootstrap, block length Politis-White on the mean loss)."""
    from arch.bootstrap import MCS

    from ..backtest.validation import optimal_block

    block = int(max(1, round(optimal_block(losses.mean(axis=1).to_numpy()))))
    m = MCS(losses, size=size, reps=1000, block_size=block, method="R", bootstrap="stationary", seed=seed)
    m.compute()
    return {str(c) for c in m.included}


@dataclass
class NameLeague:
    rows: list[dict] = field(default_factory=list)
    dm: list[dict] = field(default_factory=list)
    forecast: dict = field(default_factory=dict)
    dropped: dict = field(default_factory=dict)     # model -> reason it was not scored


def league_for(r: np.ndarray, rv: np.ndarray, *, has_minute: bool, est_window: int = EST_WINDOW,
               eval_days: int = EVAL_DAYS, refit_every: int = REFIT_EVERY) -> NameLeague:
    """``r`` daily returns; ``rv`` the realized-variance target on the same days (minute RV or r^2)."""
    target = np.asarray(rv, dtype=float)[-eval_days:]
    fc: dict[str, np.ndarray] = {}
    nxt: dict[str, float] = {}
    out = NameLeague()
    for k in GARCH_KINDS:
        f, nx = garch_oos(r, k, est_window=est_window, eval_days=eval_days, refit_every=refit_every)
        if np.all(np.isfinite(f) & (f > 0)) and math.isfinite(nx) and nx > 0:
            fc[k], nxt[k] = f, nx
        else:  # e.g. an EGARCH refit whose variance path underflows: reported, never scored as a number
            out.dropped[k] = "non-finite variance forecast from a degenerate fit; not scored"
    fc["ewma"], nxt["ewma"] = ewma_oos(r, eval_days)
    if has_minute:
        fc["har"], nxt["har"] = har_oos(rv, eval_days=eval_days, refit_every=refit_every)
        out.forecast["har_22d"] = har_22d(rv)
    losses = pd.DataFrame({m: qlike(target, f) for m, f in fc.items()})
    try:
        keep: set[str] | None = mcs_members(losses) if losses.shape[1] > 1 else set(losses.columns)
    except (IndexError, ValueError) as e:  # arch's MCS can fail on degenerate loss sets: say so, never guess
        keep = None
        out.dropped["mcs"] = f"model confidence set not computable: {e}"
    ranks = losses.mean().rank()
    for m, f in fc.items():
        out.rows.append({"model": m, "qlike": float(losses[m].mean()), "mse": float(mse(target, f).mean()),
                         "n": int(eval_days), "in_mcs": None if keep is None else m in keep,
                         "rank_qlike": int(ranks[m]),
                         "target": "minute_rv" if has_minute else "squared_return"})
    names = list(fc)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            out.dm.append({"a": a, "b": b, **dm_test(losses[a].to_numpy(), losses[b].to_numpy())})
    out.forecast.update({f"var_1d_{m}": v for m, v in nxt.items()})
    return out
