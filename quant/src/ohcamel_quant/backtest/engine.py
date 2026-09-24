"""Daily, close-to-close, target-weight backtesting engine.

Timeline (no look-ahead by construction)
----------------------------------------
* A strategy maps the information set at the close of session ``d`` (adjusted
  closes, risk-free and market series with index ``<= d``) to TARGET WEIGHTS
  ``w*_d`` (fractions of NAV, signed; cash = ``1 - sum w``).
* Decisions are taken only on rebalance sessions (schedule below). A decision
  taken at ``d`` is EXECUTED at the close of ``d + L`` (``L = execution_lag >= 1``
  sessions) -- the engine shifts, the strategy never sees ``d + 1``.
* Structural audit: the strategy is re-run on the panel TRUNCATED at several
  checkpoints; if any truncated last row differs from the full-panel row, the
  strategy used future data and :class:`LookAheadError` is raised
  (a "peeking" strategy cannot pass).

Accounting for session ``s`` (``R_s`` asset simple returns close ``s-1`` -> ``s``)
---------------------------------------------------------------------------------
Let ``w`` be post-trade weights at the close of ``s - 1``::

    gross_s  = w' R_s + (1 - 1'w) rf_s                  (cash / financing earns rf)
    borrow_s = (b / 10^4 / 252) * sum_i max(-w_i, 0)     (short-borrow fee, b bps/yr)
    g_s      = gross_s - borrow_s
    w~       = w * (1 + R_s) / (1 + g_s)                (drifted weights)

and if an execution falls on ``s`` with (overlay-scaled, leverage-capped)
target ``w*``::

    turnover_s = sum_i |w*_i - w~_i|,   cost_s = (c / 10^4) * turnover_s
    1 + net_s  = (1 + g_s) (1 - cost_s)

so proportional costs ``c`` bps are charged on traded notional after drift.
Between executions weights drift with prices (buy-and-hold inside the period).

Volatility-target overlay (optional)
------------------------------------
At each decision ``d`` the target is multiplied by ``k_d = sigma* / sqrt(252
w*' S_d w*)`` where ``S_d`` is an EWMA (RiskMetrics 1996) or rolling sample
covariance of asset returns through ``d`` -- an ex-ante, lagged estimate --
then gross leverage ``sum |w|`` is capped. See Moreira & Muir (2017) and
Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018), "The Impact
of Volatility Targeting", *Journal of Portfolio Management* 45(1).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from .estimators import ewma_cov_at, rolling_cov_at

Rebalance = Literal["daily", "weekly", "monthly", "quarterly", "signal", "never"]
REBALANCE_CHOICES: tuple[str, ...] = ("daily", "weekly", "monthly", "quarterly", "signal", "never")
PERIODS = 252


class LookAheadError(RuntimeError):
    """The strategy's weights at some date depend on data after that date."""


@dataclass(frozen=True)
class StrategyContext:
    """Information available to a strategy: adjusted closes of the tradable
    assets, optional daily risk-free DECIMAL returns and optional market
    (benchmark) adjusted closes, all on the same session index."""

    prices: pd.DataFrame
    rf: pd.Series | None = None
    market: pd.Series | None = None

    @property
    def returns(self) -> pd.DataFrame:
        return self.prices.pct_change()

    def truncate(self, end: pd.Timestamp) -> StrategyContext:
        return StrategyContext(
            prices=self.prices.loc[:end],
            rf=None if self.rf is None else self.rf.loc[:end],
            market=None if self.market is None else self.market.loc[:end],
        )


WeightsFn = Callable[..., pd.DataFrame]


@dataclass(frozen=True)
class EngineConfig:
    cost_bps: float = 5.0                 # one-way proportional cost on traded notional
    borrow_bps: float = 0.0               # annual fee on short notional
    execution_lag: int = 1                # sessions between decision close and execution close
    rebalance: str = "monthly"
    max_gross_leverage: float = 2.0       # cap on sum |w|
    vol_target: float | None = None       # annualized, e.g. 0.10; None = no overlay
    vol_estimator: Literal["ewma", "rolling"] = "ewma"
    vol_halflife: float = 21.0            # sessions (ewma)
    vol_window: int = 63                  # sessions (rolling) and minimum history (ewma)
    signal_tolerance: float = 1e-6        # |dw| that counts as a signal change
    audit_points: int = 4                 # truncated re-runs for the look-ahead audit (0 = off)

    def __post_init__(self) -> None:
        if self.execution_lag < 1:
            raise ValueError("execution_lag must be >= 1 session (same-close execution is look-ahead)")
        if self.rebalance not in REBALANCE_CHOICES:
            raise ValueError(f"rebalance must be one of {REBALANCE_CHOICES}")
        if self.cost_bps < 0 or self.borrow_bps < 0:
            raise ValueError("costs must be non-negative")
        if self.max_gross_leverage <= 0:
            raise ValueError("max_gross_leverage must be positive")
        if self.vol_target is not None and not (0 < self.vol_target <= 2.0):
            raise ValueError("vol_target must be in (0, 2]")


@dataclass
class BacktestResult:
    returns_gross: pd.Series          # before transaction costs and borrow fees
    returns_net: pd.Series            # after all costs
    weights: pd.DataFrame             # post-trade weights at each close
    turnover: pd.Series               # sum |dw| traded at each close
    costs: pd.Series                  # transaction cost as a fraction of NAV
    borrow: pd.Series                 # borrow fee as a fraction of NAV
    vol_scale: pd.Series              # overlay multiplier at each execution (NaN otherwise)
    decisions: pd.DatetimeIndex       # decision closes that led to executions
    executions: pd.DatetimeIndex
    live_start: pd.Timestamp | None   # first execution close (the entry trade; returns before it are cash)
    contributions: pd.DataFrame       # w_{s-1,i} R_{s,i}, arithmetic P&L attribution
    audit: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    config: EngineConfig | None = None
    trades: pd.DataFrame | None = None  # signed w*_i - w~_i traded at each close (0 elsewhere)

    def live(self, s: pd.Series | pd.DataFrame) -> Any:
        """Slice a series/frame to the live period, which STARTS at the first
        execution close: that session's return carries the entry trade's cost
        (``(1 + rf)(1 - c * turnover) - 1``), so the cost of building the
        initial book is not silently dropped from live returns and stats."""
        if self.live_start is None:
            return s.iloc[0:0]
        return s.loc[s.index >= self.live_start]

    @property
    def exposure(self) -> pd.DataFrame:
        w = self.weights
        return pd.DataFrame({
            "gross": w.abs().sum(axis=1),
            "net": w.sum(axis=1),
            "long": w.clip(lower=0).sum(axis=1),
            "short": w.clip(upper=0).sum(axis=1),
            "cash": 1.0 - w.sum(axis=1),
            "holdings": (w.abs() > 1e-8).sum(axis=1),
        })

    def trade_summary(self) -> dict[str, Any]:
        """Aggregate trading statistics over the live period."""
        to = self.live(self.turnover)
        years = max(len(to), 1) / PERIODS
        if self.trades is not None:
            dw = self.trades.abs()
        else:  # results built without per-asset trades: weight change incl. drift
            dw = self.weights.diff().abs()
        ex_rows = self.live(dw.loc[dw.index.isin(self.executions)])
        per_asset = pd.DataFrame({
            "trades": (ex_rows > 1e-8).sum(),
            "traded_notional": ex_rows.sum(),
            "avg_weight": self.live(self.weights).mean(),
            "pct_time_long": (self.live(self.weights) > 1e-8).mean(),
            "pct_time_short": (self.live(self.weights) < -1e-8).mean(),
            "pnl_contribution": self.contributions.sum(),
        })
        exp = self.live(self.exposure)
        return {
            "executions": len(self.executions),
            "asset_trades": int((ex_rows > 1e-8).to_numpy().sum()),
            "avg_turnover_per_execution": float(to[to > 0].mean()) if (to > 0).any() else 0.0,
            "annual_turnover": float(to.sum() / years) if len(to) else 0.0,
            "total_cost_paid": float(self.live(self.costs).sum()),
            "annual_cost_drag": float(self.live(self.costs).sum() / years) if len(to) else 0.0,
            "total_borrow_paid": float(self.live(self.borrow).sum()),
            "avg_gross_exposure": float(exp["gross"].mean()) if len(exp) else float("nan"),
            "avg_net_exposure": float(exp["net"].mean()) if len(exp) else float("nan"),
            "avg_holdings": float(exp["holdings"].mean()) if len(exp) else float("nan"),
            "per_asset": per_asset,
        }


def rebalance_mask(index: pd.DatetimeIndex, schedule: str) -> np.ndarray:
    """Boolean decision-session mask for a calendar schedule.

    ``weekly``/``monthly``/``quarterly`` decide on the LAST session of each
    period present in the index (the exchange calendar is known in advance;
    prices are not). The final session of the panel is never a period end.
    ``daily`` = every session; ``signal``/``never`` are handled by the engine.
    """
    n = len(index)
    if schedule in ("daily", "signal", "never"):
        return np.ones(n, dtype=bool)
    freq = {"weekly": "W", "monthly": "M", "quarterly": "Q"}[schedule]
    per = index.to_period(freq).asi8
    mask = np.zeros(n, dtype=bool)
    mask[:-1] = per[:-1] != per[1:]
    return mask


def _prep_targets(targets: pd.DataFrame, prices: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    t = targets.reindex(index=prices.index, columns=prices.columns)
    arr = t.to_numpy(dtype=float)
    valid = np.isfinite(arr).any(axis=1)
    arr = np.where(np.isfinite(arr), arr, 0.0)
    return arr, valid


def audit_causality(fn: WeightsFn, ctx: StrategyContext, params: dict[str, Any], full: pd.DataFrame,
                    points: int = 4, tol: float = 1e-8) -> dict[str, Any]:
    """Re-run ``fn`` on the context truncated at ``points`` checkpoints (spread
    over the valid part of the sample) and compare the truncated panel's LAST
    row with the full-panel row at the same date. A difference means weights at
    ``t`` depend on data after ``t``: :class:`LookAheadError`."""
    arr, valid = _prep_targets(full, ctx.prices)
    pos = np.flatnonzero(valid)
    if points <= 0 or len(pos) < 2:
        return {"checked": [], "max_abs_diff": None, "passed": True, "points": 0}
    qs = np.linspace(0.3, 0.95, points)
    cps = sorted(set(int(pos[int(q * (len(pos) - 1))]) for q in qs))
    worst = 0.0
    checked = []
    for cp in cps:
        d = ctx.prices.index[cp]
        tr = fn(ctx.truncate(d), **params)
        tr_arr, tr_valid = _prep_targets(tr, ctx.prices.loc[:d])
        diff = float(np.max(np.abs(tr_arr[-1] - arr[cp]))) if tr_valid[-1] == valid[cp] else float("inf")
        worst = max(worst, diff)
        checked.append(d)
        if diff > tol:
            raise LookAheadError(
                f"strategy weights at {d.date()} change when data after that date are removed "
                f"(max |dw| = {diff:.3g}): the strategy uses future information")
    return {"checked": checked, "max_abs_diff": worst, "passed": True, "points": len(checked)}


def run_backtest(ctx: StrategyContext, fn: WeightsFn, params: dict[str, Any],
                 config: EngineConfig | None = None) -> BacktestResult:
    """Run strategy ``fn(ctx, **params) -> target weights`` through the engine,
    after the structural look-ahead audit."""
    config = config or EngineConfig()
    targets = fn(ctx, **params)
    audit = audit_causality(fn, ctx, params, targets, config.audit_points)
    res = run_weights(ctx.prices, targets, config, ctx.rf)
    res.audit = audit
    return res


def run_weights(prices: pd.DataFrame, targets: pd.DataFrame, config: EngineConfig | None = None,
                rf: pd.Series | None = None) -> BacktestResult:
    """Simulate the accounting in the module docstring for given target weights
    (row ``d`` = decision at the close of ``d``; all-NaN rows = no signal)."""
    config = config or EngineConfig()
    prices = prices.sort_index()
    if prices.isna().any().any():
        raise ValueError("price panel contains missing values; align sessions first")
    if (prices <= 0).any().any():
        raise ValueError("non-positive prices in panel")
    idx = prices.index
    n_t, n_a = prices.shape
    if n_t < config.execution_lag + 2:
        raise ValueError("price history too short for the execution lag")
    px = prices.to_numpy(dtype=float)
    rets = np.zeros_like(px)
    rets[1:] = px[1:] / px[:-1] - 1.0
    rf_arr = np.zeros(n_t) if rf is None else rf.reindex(idx).fillna(0.0).to_numpy(dtype=float)

    tgt, valid = _prep_targets(targets, prices)
    sched = rebalance_mask(idx, config.rebalance)
    notes: list[str] = []

    # ---- decision sessions
    cand = np.flatnonzero(valid & sched)
    if config.rebalance == "signal":
        keep, prev = [], None
        for d in cand:
            if prev is None or np.max(np.abs(tgt[d] - prev)) > config.signal_tolerance:
                keep.append(d)
                prev = tgt[d]
        cand = np.asarray(keep, dtype=int)
    elif config.rebalance == "never":
        cand = cand[:1]
    cand = cand[cand + config.execution_lag < n_t]

    # ---- overlay + leverage cap at each decision
    final = {int(d): tgt[d].copy() for d in cand}
    scale = np.full(n_t, np.nan)
    if config.vol_target is not None and len(cand):
        if config.vol_estimator == "ewma":
            covs = ewma_cov_at(rets[1:], cand - 1, config.vol_halflife, config.vol_window)
            covs = {k + 1: v for k, v in covs.items()}
        else:
            covs = rolling_cov_at(rets[1:], cand - 1, config.vol_window)
            covs = {k + 1: v for k, v in covs.items()}
        dropped = 0
        for d in list(final):
            cov = covs.get(d)
            w = final[d]
            if cov is None:
                del final[d]
                dropped += 1
                continue
            pv = float(w @ cov @ w)
            k = config.vol_target / math.sqrt(pv * PERIODS) if pv > 0 else 0.0
            final[d] = w * k
            scale[d + config.execution_lag] = k
        if dropped:
            notes.append(f"Vol-target overlay: {dropped} early decision(s) skipped until "
                         f"{config.vol_window} sessions of returns were available.")
    capped = 0
    for d, w in final.items():
        g = float(np.abs(w).sum())
        if g > config.max_gross_leverage + 1e-12:
            final[d] = w * (config.max_gross_leverage / g)
            capped += 1
    if capped:
        notes.append(f"Gross leverage capped at {config.max_gross_leverage:g}x on {capped} decision(s).")

    exec_at: dict[int, np.ndarray] = {d + config.execution_lag: w for d, w in final.items()}
    decisions = idx[sorted(final)]

    # ---- accounting loop (scalar-light: this runs once per session per backtest)
    w = np.zeros(n_a)
    W = np.zeros((n_t, n_a))
    gross = np.zeros(n_t)
    net = np.zeros(n_t)
    turnover = np.zeros(n_t)
    trades = np.zeros((n_t, n_a))
    costs = np.zeros(n_t)
    borrow = np.zeros(n_t)
    c = config.cost_bps / 1e4
    b = config.borrow_bps / 1e4 / PERIODS
    ruined = False
    first = min(exec_at) if exec_at else n_t
    # before the first execution the book is all cash: it earns rf, nothing else
    gross[1:first] = rf_arr[1:first]
    net[1:first] = rf_arr[1:first]
    rf_l = rf_arr.tolist()
    for s in range(first, n_t):
        if s > 0:
            r = rets[s]
            wr = w * r
            wsum = float(w.sum())
            gs = float(wr.sum()) + (1.0 - wsum) * rf_l[s]
            # short notional sum max(-w, 0) = (sum|w| - sum w) / 2
            bs = b * 0.5 * (float(np.abs(w).sum()) - wsum) if b else 0.0
            gross[s] = gs
            borrow[s] = bs
            g = gs - bs
            if 1.0 + g <= 0:
                net[s] = -1.0
                ruined = True
                W[s:] = 0.0
                break
            w = (w + wr) / (1.0 + g)
            net[s] = g
        tw = exec_at.get(s)
        if tw is not None:
            dw = tw - w
            trades[s] = dw
            to = float(np.abs(dw).sum())
            turnover[s] = to
            costs[s] = c * to
            net[s] = (1.0 + net[s]) * (1.0 - costs[s]) - 1.0
            w = tw.copy()
        W[s] = w
    # arithmetic attribution w_{s-1,i} R_{s,i} (W[s-1] is the book carried into s)
    contrib = np.zeros((n_t, n_a))
    contrib[1:] = W[:-1] * rets[1:]
    if ruined:
        notes.append("Portfolio NAV was wiped out (a session loss >= 100%); the backtest stops there.")

    executions = idx[sorted(exec_at)]
    live_start = executions[0] if len(executions) else None
    if live_start is None:
        notes.append("The strategy never produced an executable signal in this window.")
    cols = prices.columns
    return BacktestResult(
        returns_gross=pd.Series(gross, idx, name="gross"),
        returns_net=pd.Series(net, idx, name="net"),
        weights=pd.DataFrame(W, idx, cols),
        turnover=pd.Series(turnover, idx, name="turnover"),
        costs=pd.Series(costs, idx, name="costs"),
        borrow=pd.Series(borrow, idx, name="borrow"),
        vol_scale=pd.Series(scale, idx, name="vol_scale"),
        decisions=decisions,
        executions=executions,
        live_start=live_start,
        contributions=pd.DataFrame(contrib, idx, cols),
        notes=notes,
        config=config,
        trades=pd.DataFrame(trades, idx, cols),
    )
