"""Walk-forward (rolling out-of-sample) comparison of allocation methods.

Protocol (DeMiguel, Garlappi & Uppal 2009, sec. 2):

* Rebalance on the first session of each period (``W``/``M``/``Q``) once
  ``window`` sessions of history exist. At a rebalance session ``t`` the
  estimation sample is the ``window`` sessions STRICTLY BEFORE ``t`` -- the
  decision is taken at the previous close, so weights never see ``r_t``.
* Between rebalances the book drifts (buy-and-hold): value
  ``V_s = sum_i w_i prod_{u<=s}(1 + r_iu) + c prod_{u<=s}(1 + rf_u)`` with cash
  ``c = 1 - sum w``.
* Trading cost at rebalance: ``kappa * sum_i |w_i^new - w_i^drift|`` with
  ``kappa = cost_bps / 10^4`` (proportional costs, DeMiguel et al. eq. 15),
  charged on the rebalance session.
* If a method has no solution at a rebalance (e.g. no asset's expected return
  exceeds rf, so the tangency portfolio does not exist) the book moves to cash
  at the risk-free rate, and the event is counted.

Outputs: daily net returns and equity per method, OOS annualized return /
volatility / Sharpe (excess of rf) / max drawdown / turnover, and Sharpe-difference
tests (Ledoit & Wolf 2008; Jobson-Korkie/Memmel 2003) versus a benchmark method.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import expected as er_mod
from .covariance import estimate_covariance
from .inference import ledoit_wolf_test, memmel_test
from .methods import METHODS, method_weights
from .optimize import Constraints

TRADING_DAYS = 252
FREQS = {"W": "W", "M": "M", "Q": "Q"}


@dataclass
class MethodTrack:
    name: str
    returns: pd.Series  # daily net OOS returns
    weights: pd.DataFrame  # target weights at each rebalance (rows = rebalance dates)
    turnover: pd.Series  # sum |dw| at each rebalance
    costs: pd.Series  # cost fraction charged at each rebalance
    failures: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    tracks: dict[str, MethodTrack]
    rebalance_dates: list[pd.Timestamp]
    rf_daily: pd.Series
    stats: dict[str, dict[str, Any]]
    tests: dict[str, dict[str, Any]]
    params: dict[str, Any]
    notes: list[str]


def rebalance_positions(index: pd.DatetimeIndex, window: int, freq: str = "M") -> list[int]:
    """Row positions of the first session of each period with at least ``window`` prior rows."""
    if freq not in FREQS:
        raise ValueError("rebalance frequency must be one of W, M, Q")
    per = index.to_period(FREQS[freq])
    first = np.r_[True, per[1:] != per[:-1]]
    pos = [int(i) for i in np.flatnonzero(first) if i >= window]
    if not pos:
        raise ValueError(f"not enough history: need more than {window} sessions before the first rebalance")
    return pos


def _rf_series(index: pd.DatetimeIndex, rf_daily: pd.Series | float) -> pd.Series:
    if isinstance(rf_daily, pd.Series):
        rf = rf_daily.reindex(index).ffill()
        if rf.isna().any():
            first_missing = rf.index[rf.isna()][0]
            raise ValueError(f"risk-free series does not cover {first_missing.date()}")
        return rf.astype(float)
    return pd.Series(float(rf_daily), index=index)


def performance(r: pd.Series, rf: pd.Series) -> dict[str, Any]:
    """OOS statistics of a daily net return series."""
    x = r.to_numpy(float)
    ex = x - rf.reindex(r.index).to_numpy(float)
    n = x.size
    wealth = np.cumprod(1 + x)
    dd = wealth / np.maximum.accumulate(wealth) - 1
    vol = float(x.std(ddof=1) * math.sqrt(TRADING_DAYS))
    ex_sd = float(ex.std(ddof=1))
    downside = ex[ex < 0]
    dsd = float(np.sqrt(np.mean(np.minimum(ex, 0) ** 2))) if downside.size else float("nan")
    return {
        "cagr": float(wealth[-1] ** (TRADING_DAYS / n) - 1),
        "annual_return": float(x.mean() * TRADING_DAYS),
        "annual_vol": vol,
        "sharpe": float(ex.mean() / ex_sd * math.sqrt(TRADING_DAYS)) if ex_sd > 0 else None,
        "sortino": float(ex.mean() / dsd * math.sqrt(TRADING_DAYS)) if dsd and dsd > 0 else None,
        "max_drawdown": float(dd.min()),
        "calmar": float((wealth[-1] ** (TRADING_DAYS / n) - 1) / -dd.min()) if dd.min() < 0 else None,
        "total_return": float(wealth[-1] - 1),
        "observations": int(n),
    }


def walk_forward(returns: pd.DataFrame, methods: list[str], *, window: int = 504, freq: str = "M",
                 cost_bps: float = 10.0, rf_daily: pd.Series | float = 0.0,
                 cov_method: str = "lw_constant_corr", mu_method: str = "historical",
                 cons: Constraints | None = None, benchmark: str = "equal_weight",
                 method_kwargs: dict[str, Any] | None = None, ewma_lambda: float = 0.94) -> WalkForwardResult:
    """Run the walk-forward protocol for each method on daily ``returns`` (T x N)."""
    if returns.isna().any().any():
        raise ValueError("returns contain NaN; align sessions first")
    unknown = [m for m in methods if m not in METHODS]
    if unknown:
        raise ValueError(f"unknown methods: {', '.join(unknown)}")
    if mu_method not in ("historical", "james_stein"):
        raise ValueError("walk-forward expected returns: 'historical' or 'james_stein'")
    if cost_bps < 0:
        raise ValueError("cost_bps must be >= 0")
    methods = list(dict.fromkeys([*methods, benchmark]))
    mk = method_kwargs or {}
    if "mean_variance" in methods and all(mk.get(k) is None for k in ("target_return", "target_vol",
                                                                        "risk_aversion")):
        raise ValueError("mean_variance in a walk-forward needs target_return, target_vol or risk_aversion")
    cons = cons or Constraints()
    kw = dict(method_kwargs or {})
    idx = pd.DatetimeIndex(returns.index)
    x = returns.to_numpy(float)
    names = [str(c) for c in returns.columns]
    n = len(names)
    rf = _rf_series(idx, rf_daily)
    rfx = rf.to_numpy(float)
    pos = rebalance_positions(idx, window, freq)
    kappa = cost_bps / 1e4
    bounds = [*pos, len(idx)]
    start = pos[0]
    out_r = {m: np.zeros(len(idx) - start) for m in methods}
    w_hist: dict[str, list[np.ndarray]] = {m: [] for m in methods}
    to_hist: dict[str, list[float]] = {m: [] for m in methods}
    cost_hist: dict[str, list[float]] = {m: [] for m in methods}
    fails: dict[str, list[dict[str, Any]]] = {m: [] for m in methods}
    drift: dict[str, np.ndarray] = {m: np.zeros(n) for m in methods}
    relaxed: dict[str, int] = {m: 0 for m in methods}
    violated: dict[str, int] = {m: 0 for m in methods}
    base_cons = Constraints(cons.lower, cons.upper, cons.max_gross)
    check_cons = not base_cons.is_default(n)
    need_mu = any(METHODS[m].needs_mu for m in methods)
    for k, p in enumerate(pos):
        est = returns.iloc[p - window:p]  # strictly before the rebalance session
        cov = estimate_covariance(est, cov_method, ewma_lambda=ewma_lambda).cov
        rf_ann = float(rfx[p - window:p].mean() * TRADING_DAYS)
        mu = None
        if need_mu:
            mu = (er_mod.historical_mean(est) if mu_method == "historical" else er_mod.james_stein(est)).mu
        seg = slice(p, bounds[k + 1])
        g = np.cumprod(1 + x[seg], axis=0)
        grf = np.cumprod(1 + rfx[seg])
        for m in methods:
            if cons.max_turnover is not None:
                c_m = Constraints(cons.lower, cons.upper, cons.max_gross, cons.max_turnover, drift[m])
                if k == 0:
                    c_m = Constraints(cons.lower, cons.upper, cons.max_gross)
            else:
                c_m = cons
            try:
                w = method_weights(m, est, cov, mu, rf_ann, c_m, **kw)
            except (ValueError, np.linalg.LinAlgError) as e:
                w = None
                if c_m.max_turnover is not None:  # drifted book may make the turnover band infeasible
                    try:
                        w = method_weights(m, est, cov, mu, rf_ann, Constraints(cons.lower, cons.upper,
                                           cons.max_gross), **kw)
                        relaxed[m] += 1
                    except (ValueError, np.linalg.LinAlgError):
                        w = None
                if w is None:
                    w = np.zeros(n)
                    fails[m].append({"date": idx[p], "error": str(e)})
            if check_cons and w.any() and not base_cons.satisfied(w):
                violated[m] += 1  # heuristic methods (1/N, HRP, ERC...) do not take constraints
            turnover = float(np.abs(w - drift[m]).sum())
            cost = kappa * turnover
            cash = 1.0 - w.sum()
            v = g @ w + cash * grf
            v_prev = np.r_[1.0, v[:-1]]
            r_seg = v / v_prev - 1
            r_seg[0] = (1 - cost) * (1 + r_seg[0]) - 1
            out_r[m][p - start:bounds[k + 1] - start] = r_seg
            drift[m] = (w * g[-1]) / v[-1] if v[-1] != 0 else w
            w_hist[m].append(w)
            to_hist[m].append(turnover)
            cost_hist[m].append(cost)
    oos_idx = idx[start:]
    rdates = [idx[p] for p in pos]
    tracks = {m: MethodTrack(m, pd.Series(out_r[m], index=oos_idx, name=m),
                             pd.DataFrame(w_hist[m], index=rdates, columns=names),
                             pd.Series(to_hist[m], index=rdates), pd.Series(cost_hist[m], index=rdates),
                             fails[m]) for m in methods}
    rf_oos = rf.iloc[start:]
    years = len(oos_idx) / TRADING_DAYS
    stats: dict[str, dict[str, Any]] = {}
    for m, tr in tracks.items():
        st = performance(tr.returns, rf_oos)
        # the first rebalance is the initial purchase; exclude it from turnover averages
        st["annual_turnover"] = float(tr.turnover.iloc[1:].sum() / years) if years > 0 else None
        st["cost_drag_annual"] = float(tr.costs.sum() / years) if years > 0 else None
        st["rebalances"] = len(tr.turnover)
        st["failed_rebalances"] = len(tr.failures)
        st["turnover_limit_relaxed"] = relaxed[m]
        st["constraint_violations"] = violated[m]
        stats[m] = st
    tests: dict[str, dict[str, Any]] = {}
    ex_b = tracks[benchmark].returns.to_numpy() - rf_oos.to_numpy()
    for m in methods:
        if m == benchmark:
            continue
        ex_m = tracks[m].returns.to_numpy() - rf_oos.to_numpy()
        try:
            tests[m] = {"vs": benchmark, "ledoit_wolf": ledoit_wolf_test(ex_m, ex_b),
                        "memmel": memmel_test(ex_m, ex_b)}
        except (ValueError, ZeroDivisionError) as e:
            tests[m] = {"vs": benchmark, "error": str(e)}
    notes = [
        f"Out-of-sample {oos_idx[0].date()}..{oos_idx[-1].date()} ({len(oos_idx)} sessions, "
        f"{len(pos)} rebalances); each decision uses only the {window} sessions before the rebalance session.",
        f"Proportional trading cost {cost_bps:g} bps x one-way turnover sum|dw| at each rebalance; "
        "weights drift with prices between rebalances.",
    ]
    broke = [m for m in methods if violated[m]]
    if broke:
        notes.append(f"{', '.join(broke)} ignore the weight bounds/leverage limit (heuristic allocations); "
                     "their weights violated the constraints at some rebalances (see constraint_violations).")
    if any(tr.failures for tr in tracks.values()):
        notes.append("Some rebalances had no feasible solution for a method; the book was held in cash at "
                     "the risk-free rate for that period (see failed_rebalances).")
    return WalkForwardResult(tracks, rdates, rf_oos, stats, tests,
                             {"window": window, "freq": freq, "cost_bps": cost_bps, "cov_method": cov_method,
                              "mu_method": mu_method, "benchmark": benchmark, **kw}, notes)
