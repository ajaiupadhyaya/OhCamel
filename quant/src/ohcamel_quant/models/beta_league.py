"""Beta forecast league (completion task 4.7): rolling OLS betas against a Kalman (random-walk) beta.

At every session ``t`` of the evaluation span each model forecasts the beta of ``y`` (an instrument's daily
returns) on ``x`` (the market's) and is scored against the REALISED beta over the next ``HORIZON = 21`` sessions:
the OLS slope of ``y`` on ``x`` over ``t+1 .. t+21``, which begins strictly after the forecast date, so no
forecast shares a return with its target.

* ``ols_w`` (``w`` in ``WINDOWS = (60, 120, 250)``): the OLS slope over ``t-w+1 .. t``.
* ``kalman``: :func:`ohcamel_quant.factors.kalman.dynamic_beta`'s FILTERED beta at ``t`` (a random walk's
  forecast of every later beta is its current filtered value). ``q`` is re-estimated by maximum likelihood every
  ``REFIT_EVERY = 21`` sessions on the trailing ``KALMAN_WINDOW = 1000`` sessions up to the refit date ``d``, and
  the filter runs from the window's start to ``t`` with that ``q`` -- so the forecast at ``t`` uses no data after
  ``t``. The smoothed beta is never used here.

Loss: squared error ``(beta_hat_t - beta_real_t)^2``. Comparison: the Diebold & Mariano (1995) test of equal
expected loss on ``d_t = L_kalman,t - L_ols_w,t``, ``DM = mean(d) / sqrt(S_NW / n)``, ``S_NW`` the Newey-West
(1987) Bartlett long-run variance of ``d`` with lag ``HAC_LAG = 40``. The 21-session target windows of
consecutive forecasts overlap in 20 sessions, so the loss differentials are autocorrelated at least to lag 20
(an MA(20) under the null); a Bartlett lag of exactly 20 would give lag 20 a weight of 1/21, so the lag is twice
that, ``2 x (21 - 1) = 40`` (weight 0.51 at lag 20). Two-sided normal p-value.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

from ..factors import kalman
from ..factors.hac import newey_west_lrv

WINDOWS = (60, 120, 250)
HORIZON = 21
HAC_LAG = 2 * (HORIZON - 1)
KALMAN_WINDOW = 1000
REFIT_EVERY = 21
EVAL_SESSIONS = 2520
MODELS = ("kalman", *(f"ols_{w}" for w in WINDOWS))


def rolling_ols_beta(y: pd.Series, x: pd.Series, window: int) -> pd.Series:
    """``cov_w(y, x) / var_w(x)`` over ``t-w+1 .. t`` (NaN for the first ``w - 1`` sessions)."""
    return y.rolling(window).cov(x) / x.rolling(window).var()


def forward_beta(y: pd.Series, x: pd.Series, horizon: int = HORIZON) -> pd.Series:
    """At ``t``, the OLS slope of ``y`` on ``x`` over ``t+1 .. t+horizon`` (NaN for the last ``horizon``)."""
    return rolling_ols_beta(y, x, horizon).shift(-horizon)


def kalman_forecasts(y: pd.Series, x: pd.Series, *, start: int, window: int = KALMAN_WINDOW,
                     refit_every: int = REFIT_EVERY) -> pd.DataFrame:
    """The filtered Kalman beta (and the ``q`` behind it) at positions ``start .. len-1``, point in time.

    Blocks begin at ``d = start, start + refit_every, ...``; for a block, ``q_d`` is the ML ``q`` on positions
    ``max(0, d-window+1) .. d`` and one filter pass with ``q_d`` over ``max(0, d-window+1) .. end of block``
    gives the filtered beta at each ``t`` of the block (a filtered value depends only on data up to ``t``).
    """
    yv, xv = y.to_numpy(dtype=float), x.to_numpy(dtype=float)
    n = yv.size
    out_b, out_q, idx = [], [], []
    for d in range(start, n, refit_every):
        lo = max(0, d - window + 1)
        end = min(n, d + refit_every)
        q = kalman.ml_q(yv[lo:d + 1], xv[lo:d + 1])
        kb = kalman.dynamic_beta(y.iloc[lo:end], x.iloc[lo:end], q=q)
        f = kb.filtered.to_numpy()[d - lo:]
        out_b.extend(f)
        out_q.extend([q] * f.size)
        idx.extend(y.index[d:end])
    return pd.DataFrame({"beta": out_b, "q": out_q}, index=pd.Index(idx, name=y.index.name))


def dm_test(loss_a: np.ndarray, loss_b: np.ndarray, *, lag: int = HAC_LAG) -> dict[str, float]:
    """Diebold-Mariano test of ``E[loss_a - loss_b] = 0`` with a Newey-West long-run variance at ``lag``."""
    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    n = d.size
    m = float(d.mean())
    s = float(newey_west_lrv(d - m, lag)[0, 0])
    se = math.sqrt(max(s, 0.0) / n)
    stat = m / se if se > 0 else 0.0
    return {"mean_diff": m, "stat": stat, "pvalue": float(2.0 * stats.norm.sf(abs(stat))), "lag": int(lag),
            "n": int(n)}


def score_pair(y: pd.Series, x: pd.Series, *, eval_sessions: int = EVAL_SESSIONS,
               kalman_window: int = KALMAN_WINDOW, refit_every: int = REFIT_EVERY) -> dict[str, pd.DataFrame]:
    """Forecasts, league and DM tests for one instrument ``y`` against the market ``x`` (aligned, no NaN).

    Forecast dates: every session with a full ``max(WINDOWS)``-session history and a full ``HORIZON``-session
    target, the last ``eval_sessions`` of them.
    """
    if not y.index.equals(x.index):
        raise ValueError("y and x must share one index")
    n = len(y)
    first = max(WINDOWS) - 1
    last = n - HORIZON - 1
    if last - first + 1 < max(HORIZON, 2 * HAC_LAG):
        raise ValueError(f"need at least {max(WINDOWS) + HORIZON + 2 * HAC_LAG} common sessions; have {n}")
    start = max(first, last - eval_sessions + 1)
    f = pd.DataFrame(index=y.index[start:last + 1])
    f["target"] = forward_beta(y, x).iloc[start:last + 1]
    kf = kalman_forecasts(y.iloc[:last + 1], x.iloc[:last + 1], start=start, window=kalman_window,
                          refit_every=refit_every)
    f["kalman"] = kf["beta"]
    for w in WINDOWS:
        f[f"ols_{w}"] = rolling_ols_beta(y, x, w).iloc[start:last + 1]
    f["q"] = kf["q"]
    loss = {m: (f[m] - f["target"]).to_numpy() ** 2 for m in MODELS}
    lg = pd.DataFrame([{"model": m, "mse": float(loss[m].mean()),
                        "mae": float(np.abs(f[m] - f["target"]).mean()), "n": int(len(f))} for m in MODELS])
    lg["rank"] = lg["mse"].rank(method="first").astype(int)
    tests = pd.DataFrame([{"model": "kalman", "vs": f"ols_{w}", **dm_test(loss["kalman"], loss[f"ols_{w}"])}
                          for w in WINDOWS])
    return {"forecasts": f, "league": lg, "tests": tests, "losses": pd.DataFrame(loss, index=f.index)}


def holm(pvalues: list[float]) -> list[float]:
    """Holm (1979) step-down adjusted p-values (family-wise error across the comparisons)."""
    p = np.asarray(pvalues, dtype=float)
    order = np.argsort(p)
    m = p.size
    adj = np.empty(m)
    run = 0.0
    for k, i in enumerate(order):
        run = max(run, min(1.0, (m - k) * p[i]))
        adj[i] = run
    return adj.tolist()


__all__ = ["EVAL_SESSIONS", "HAC_LAG", "HORIZON", "KALMAN_WINDOW", "MODELS", "REFIT_EVERY", "WINDOWS", "dm_test",
           "forward_beta", "holm", "kalman_forecasts", "rolling_ols_beta", "score_pair"]
