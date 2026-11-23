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

    gross_s     = w' R_s + (1 - 1'w) rf_s                  (cash earns rf; borrowed cash pays it)
    borrow_s    = (b / 10^4 / 252) * sum_i max(-w_i, 0)     (short-borrow fee, b bps/yr)
    financing_s = (f / 10^4 / 252) * max(sum_i |w_i| - 1, 0) (financing spread, f bps/yr on gross above 1)
    g_s         = gross_s - borrow_s - financing_s
    w~          = w * (1 + R_s) / (1 + g_s)                (drifted weights)

and if an execution falls on ``s`` with (overlay-scaled, leverage-capped)
target ``w*``::

    cost_s     = sum_i (c_i / 10^4) |w*_i - w~_i|,   turnover_s = sum_i |w*_i - w~_i|
    1 + net_s  = (1 + g_s) (1 - cost_s)

so each name's half-spread ``c_i`` bps is charged on its traded notional after
drift. Between executions weights drift with prices (buy-and-hold inside the period).

Costs (completion task 2.3): ``c_i``, ``b`` and ``f`` come from the one cost file,
``research/config/costs.yaml`` (:mod:`.costs`): ``c_i`` is the half-spread of name
``i``'s tier (a name with no tier raises :class:`~.costs.NoCostTier`), ``b`` the
borrow fee on shorts, ``f`` the financing spread -- so a book at gross 2 pays the
risk-free rate on its borrowed unit through the cash term plus ``f`` on it. An
:class:`EngineConfig` number overrides the file (``cost_bps`` one cost on every
name, as the public backtest form lets a visitor choose).

Volatility-target overlay (optional)
------------------------------------
At each decision ``d`` the target is multiplied by ``k_d = sigma* / sqrt(252
w*' S_d w*)`` where ``S_d`` is an EWMA (RiskMetrics 1996) or rolling sample
covariance of asset returns through ``d`` -- an ex-ante, lagged estimate --
then gross leverage ``sum |w|`` is capped. See Moreira & Muir (2017) and
Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018), "The Impact
of Volatility Targeting", *Journal of Portfolio Management* 45(1).

Single names over dated membership (completion task 3.8, spec R5)
-----------------------------------------------------------------
A universe of index members built from today's list holds only the survivors, so
single-name backtests are REFUSED (docs/CHARTER.md, Data) unless the warehouse can
show it carries the names that left: :func:`run_members_backtest` runs only when
the delisted-names switch is on (``OHCAMEL_DELISTED_SOURCE=eodhd``), the acceptance
test on known failures has passed on the vendor (``tools/accept_delisted.py``), and
at least ``COVERAGE_REQUIRED`` (95%) of the universe's exits have prices to the
exit (``readers.survivorship_free``). Every result's notes carry the coverage.

The investable set on a decision date is ``readers.members_asof`` that day; a target
on any other name is zeroed. A name whose series has ENDED by the execution session
(no bar then or later) is not bought, and a holding in it is sold at its last price:
it earns its last bar's return, is valued at that last print afterwards -- no return,
no interest -- and is sold at the next execution, paying costs. A name with no bar on
an execution session whose series goes on (a halt, a vendor gap) is left untraded at
that execution, never sold at a price that did not print (``run_weights(untraded=)``).

The last print OVERSTATES the outcome of a failure whose last exchange print exceeds
its recovery (SVB last printed about $106 on Nasdaq; its shareholders got nearly
nothing), so every such result also carries a LOWER BOUND (:func:`lower_bound_run`):
the same run with each LONG holding whose series ends inside the test marked to zero on
the first session it no longer trades. Both totals are reported side by side; the headline
stays the last-print run, and the bound is a bound, not an estimate. A SHORT in a name
whose series ends keeps its last print: its loss has no upper limit, so no bound covers
it, and the note names it. No delisting-
return model is applied (the vendor serves none).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from .. import kernels
from ..data.base import DataUnavailable
from . import costs as cost_file
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
    #: Dated membership (completion task 3.8): True where a name is an index member that day (``readers.members_panel``,
    #: point in time). None for a fixed universe.
    members: pd.DataFrame | None = None

    @property
    def returns(self) -> pd.DataFrame:
        return self.prices.pct_change()

    def truncate(self, end: pd.Timestamp) -> StrategyContext:
        return StrategyContext(
            prices=self.prices.loc[:end],
            rf=None if self.rf is None else self.rf.loc[:end],
            market=None if self.market is None else self.market.loc[:end],
            members=None if self.members is None else self.members.loc[:end],
        )


WeightsFn = Callable[..., pd.DataFrame]


@dataclass(frozen=True)
class EngineConfig:
    #: Costs (completion task 2.3): None reads the one cost file (``costs``, else research/config/costs.yaml) --
    #: each name's tiered half-spread, its borrow and its financing spread. A number overrides the file's.
    cost_bps: float | None = None         # one-way cost on every name's traded notional; None = each name's tier
    borrow_bps: float | None = None       # annual fee on short notional; None = the file's
    financing_bps: float | None = None    # annual spread on gross above 1, over rf; None = the file's
    costs: cost_file.CostModel | None = None   # the model to read; None = costs.load(); .scaled(m) for the sweep
    execution_lag: int = 1                # sessions between decision close and execution close
    rebalance: str = "monthly"
    max_gross_leverage: float = 2.0       # cap on sum |w|
    vol_target: float | None = None       # annualized, e.g. 0.10; None = no overlay
    vol_estimator: Literal["ewma", "rolling"] = "ewma"
    vol_halflife: float = 21.0            # sessions (ewma)
    vol_window: int = 63                  # sessions (rolling) and minimum history (ewma)
    signal_tolerance: float = 1e-6        # |dw| that counts as a signal change
    audit_points: int = 4                 # truncated re-runs for the look-ahead audit (0 = off)

    def model(self) -> cost_file.CostModel:
        return self.costs if self.costs is not None else cost_file.load()

    def resolved(self, tickers: Any) -> tuple[np.ndarray, float, float]:
        """``(half-spread per ticker in bps, borrow bps/yr, financing bps/yr)``: each override, else the cost file.
        Raises :class:`~.costs.NoCostTier` for a ticker the file holds no tier for (only when no override)."""
        names = list(tickers)
        spread = (np.full(len(names), float(self.cost_bps)) if self.cost_bps is not None
                  else self.model().half_spreads(names))
        borrow = float(self.borrow_bps) if self.borrow_bps is not None else self.model().borrow_bps
        fin = float(self.financing_bps) if self.financing_bps is not None else self.model().financing_spread_bps
        return spread, borrow, fin

    def __post_init__(self) -> None:
        if self.execution_lag < 1:
            raise ValueError("execution_lag must be >= 1 session (same-close execution is look-ahead)")
        if self.rebalance not in REBALANCE_CHOICES:
            raise ValueError(f"rebalance must be one of {REBALANCE_CHOICES}")
        if any(x is not None and not (math.isfinite(x) and x >= 0)
               for x in (self.cost_bps, self.borrow_bps, self.financing_bps)):
            raise ValueError("costs must be finite and non-negative")
        if self.max_gross_leverage <= 0:
            raise ValueError("max_gross_leverage must be positive")
        if self.vol_target is not None and not (0 < self.vol_target <= 2.0):
            raise ValueError("vol_target must be in (0, 2]")


@dataclass
class BacktestResult:
    returns_gross: pd.Series          # before transaction costs, borrow and financing fees
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
    kernel_engine: str = "python"       # which kernels engine ran the accounting loop (compute plan A6)
    #: Completion task 3.8, fix round 1 (I2): a single-name run over dated membership also runs with every holding
    #: whose series ends inside the test marked to zero at its end -- a lower bound, beside the headline.
    lower_bound: dict[str, Any] | None = None
    #: Completion task 2.3: the financing spread on gross above 1, as a fraction of NAV (zero below gross 1).
    financing: pd.Series | None = None

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
            "total_financing_paid": float(self.live(self.financing).sum()) if self.financing is not None else 0.0,
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
                rf: pd.Series | None = None, *, untraded: pd.DataFrame | None = None) -> BacktestResult:
    """Simulate the accounting in the module docstring for given target weights
    (row ``d`` = decision at the close of ``d``; all-NaN rows = no signal).

    ``untraded`` (completion task 3.8, fix round 1): a boolean frame on the SESSION index, True where a name cannot
    trade at an execution on that session (no bar that day though the series goes on: a halt or a vendor gap).
    At such an execution that name's target is replaced by its drifted weight, so it is neither bought nor sold;
    the other names trade as targeted. Applied after the overlay and the leverage cap (a held weight is never
    rescaled), one execution at a time in order, re-running the accounting from it."""
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

    spread, borrow_bps, fin_bps = config.resolved(prices.columns)
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

    # ---- accounting loop: the backtest_weights kernel (Rust, or reference.backtest_weights,
    # which is this loop verbatim)
    ex = np.array(sorted(exec_at), dtype=np.int64)
    tw = np.vstack([exec_at[int(s)] for s in ex]) if len(ex) else np.zeros((0, n_a))
    path = kernels.backtest_weights(px, tw, ex, spread, borrow_bps, rf_arr, fin_bps)
    if untraded is not None and len(ex):
        hold = untraded.reindex(index=idx, columns=prices.columns).fillna(False).to_numpy(dtype=bool)
        for k, sess in enumerate(ex):
            cols = np.flatnonzero(hold[sess])
            if not len(cols):
                continue
            drifted = tw[k] - path.trades[sess]          # trades = w* - w~ at the execution
            if np.array_equal(tw[k, cols], drifted[cols]):
                continue
            tw[k, cols] = drifted[cols]
            path = kernels.backtest_weights(px, tw, ex, spread, borrow_bps, rf_arr, fin_bps)
    gross, net, W = path.gross, path.net, path.weights
    turnover, trades, costs, borrow = path.turnover, path.trades, path.costs, path.borrow
    # arithmetic attribution w_{s-1,i} R_{s,i} (W[s-1] is the book carried into s)
    contrib = np.zeros((n_t, n_a))
    contrib[1:] = W[:-1] * rets[1:]
    if path.ruined:
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
        financing=pd.Series(path.financing, idx, name="financing"),
        vol_scale=pd.Series(scale, idx, name="vol_scale"),
        decisions=decisions,
        executions=executions,
        live_start=live_start,
        contributions=pd.DataFrame(contrib, idx, cols),
        notes=notes,
        config=config,
        trades=pd.DataFrame(trades, idx, cols),
        kernel_engine=kernels.engine_of("backtest_weights"),
    )


# ------------------------------------------------------- single names over dated membership (task 3.8, R5)
#: The share of a universe's exits that must have prices to the exit before single names run (spec R5).
COVERAGE_REQUIRED = 0.95


class SingleNameRefused(DataUnavailable):
    """A single-name backtest over index members was refused: the data cannot show it is survivorship-free."""


def _pct(x: float) -> int:
    """Whole percent, rounded DOWN: 94.9% never reads as 95%."""
    return int(math.floor(x * 100.0 + 1e-9))


def survivorship_gate(con: Any, universe: str, settings: Any = None) -> str:
    """The note every result over ``universe`` carries, or :class:`SingleNameRefused` saying why it cannot run
    (module docstring). Reads the switch from ``settings`` and the coverage from the warehouse."""
    from ..warehouse import readers

    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    u = universe.strip().lower()
    if settings.delisted_source != "eodhd":
        raise SingleNameRefused(
            f"single-name backtests over {u} members are refused: the delisted-names source is off "
            "(OHCAMEL_DELISTED_SOURCE is not set), so the members' prices are the survivors' (docs/CHARTER.md, Data)")
    accepted, coverage = readers.survivorship_free(con, u)
    said = f"{_pct(coverage)}% of members have prices to their exit"
    if not accepted:
        raise SingleNameRefused(
            f"single-name backtests over {u} members are refused: the delisted-names source has not passed its "
            f"acceptance test on known failures (tools/accept_delisted.py); {said}")
    if coverage < COVERAGE_REQUIRED:
        raise SingleNameRefused(
            f"single-name backtests over {u} members are refused: {said}, and {_pct(COVERAGE_REQUIRED)}% are needed")
    return (f"Survivorship: {_pct(coverage)}% of {u} members have prices to their exit (delisted names from "
            f"{settings.delisted_source}, accepted on known failures); the rest are absent from this result.")


def members_weights(prices: pd.DataFrame, targets: pd.DataFrame, members: pd.DataFrame, *,
                    execution_lag: int = 1) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str], pd.DataFrame]:
    """The accounting panel and the tradable targets for names that enter and leave (module docstring).

    ``prices``: closes with NaN where a name has no bar. Returns ``(acc, tgt, ended, untraded)``: ``acc`` is
    ``prices`` with each name's last close carried forward after its last bar (no return) and over any gap, and its
    first close carried back before its first bar (never held there); ``tgt`` is ``targets`` with every decision's
    weight zeroed for a name that is not a member that day or whose series has ended by the session it would execute
    (rows with no signal stay NaN); ``ended`` maps each name whose bars stop before the panel's last session to its
    last bar's date; ``untraded`` (session index) is True where a name has no bar on a session although a later bar
    exists -- passed to ``run_weights``, such a name is left untraded at an execution that day."""
    px = prices.sort_index()
    has_bar = px.notna()
    m = members.reindex(index=px.index, columns=px.columns).fillna(False).astype(bool)
    t = targets.reindex(index=px.index, columns=px.columns)
    signal = t.notna().any(axis=1)
    pos = np.arange(len(px.index))
    last_pos = {c: (int(np.flatnonzero(has_bar[c].to_numpy())[-1]) if has_bar[c].any() else -1) for c in px.columns}
    # by the execution of the decision at row d (row d + lag): has the series ended (no bar then or later)?
    gone = pd.DataFrame({c: (pos + execution_lag) > last_pos[c] for c in px.columns}, index=px.index)
    tgt = t.fillna(0.0).where(m & ~gone, 0.0)
    tgt[~signal] = np.nan
    untraded = pd.DataFrame({c: (~has_bar[c].to_numpy()) & (pos < last_pos[c]) for c in px.columns}, index=px.index)
    ended = {c: str(px.index[last_pos[c]].date()) for c in px.columns
             if 0 <= last_pos[c] < len(pos) - 1}
    acc = px.ffill().bfill()
    return acc, tgt, ended, untraded


#: The price a holding is marked to in the lower bound: 1e-12 of its last print (the engine refuses a zero price;
#: the position's value is gone to twelve digits).
LOWER_BOUND_MARK = 1e-12


def lower_bound_run(acc: pd.DataFrame, tgt: pd.DataFrame, ended: dict[str, str], config: EngineConfig | None = None,
                    rf: pd.Series | None = None, *, untraded: pd.DataFrame | None = None,
                    weights: pd.DataFrame | None = None) -> BacktestResult:
    """The same run with every LONG holding carried past its series' last bar marked to zero (``LOWER_BOUND_MARK`` of
    its last print) from the first session after that bar: it loses its whole value there (module docstring). A
    SHORT carried past the end keeps its last print: marking it to zero would credit it the whole position and lift
    the bound above the headline, and a short in a vanished name has no upper limit on its loss, so no bound covers
    it (fix round 2). ``weights``: the headline run's post-trade weights (the two runs agree up to each last bar);
    computed when not given. ``result.lower_bound`` carries ``longs`` and ``shorts``: the ended series held each way
    when their bars stopped. With nothing ended or nothing long, it is the headline run."""
    if weights is None:
        weights = run_weights(acc, tgt, config, rf, untraded=untraded).weights
    low = acc.copy()
    longs: list[str] = []
    shorts: list[str] = []
    for c, d in ended.items():
        held = float(weights.at[pd.Timestamp(d), c]) if c in weights.columns else 0.0
        if held < -1e-12:
            shorts.append(c)
            continue
        if held > 1e-12:
            longs.append(c)
            low.loc[low.index > pd.Timestamp(d), c] = float(acc.loc[pd.Timestamp(d), c]) * LOWER_BOUND_MARK
    out = run_weights(low, tgt, config, rf, untraded=untraded)
    out.lower_bound = {"longs": longs, "shorts": shorts}
    return out


def _total(res: BacktestResult) -> float:
    return float(np.prod(1.0 + res.live(res.returns_net).to_numpy()) - 1.0)


def ended_phrase(name: str, last: str, fetched_to: Any) -> str:
    """How a result names a series that ends inside it (fix round 1, I4): a delisting only when the series stops
    trading well before the end of what was fetched; a series fetched to a date (a name removed from the index but
    still trading) ends at that date and is said so."""
    if fetched_to is not None:
        if int(np.busday_count(pd.Timestamp(last).date(), fetched_to)) <= 5:
            return f"{name} (series ends {last}, fetched to {fetched_to})"
        return f"{name} (stops trading {last}, before the series was fetched to {fetched_to}: a delisting)"
    return f"{name} (series ends {last})"


def run_members_backtest(con: Any, universe: str, fn: WeightsFn, params: dict[str, Any], start: Any, end: Any,
                         config: EngineConfig | None = None, *, settings: Any = None,
                         rf: pd.Series | None = None) -> BacktestResult:
    """A single-name backtest whose investable set is ``readers.members_asof`` on each date (module docstring).
    Refused (:class:`SingleNameRefused`) unless :func:`survivorship_gate` passes; the strategy sees the members'
    adjusted closes (NaN where a name has no bar) and ``ctx.members``; the look-ahead audit runs as in
    :func:`run_backtest`. The cost file (research/config/costs.yaml) holds tiers for the site's ETFs only, so a
    member without one raises :class:`~.costs.NoCostTier` unless ``config.cost_bps`` sets a cost for every name
    (completion task 2.3: a single-name tier is the owner's to add)."""
    from ..warehouse import readers

    config = config or EngineConfig()
    note = survivorship_gate(con, universe, settings)
    days = con.execute("SELECT DISTINCT date FROM bars_daily WHERE date >= ? AND date <= ? ORDER BY date",
                       [start, end]).fetchall()
    index = pd.DatetimeIndex([pd.Timestamp(d) for (d,) in days]).astype("datetime64[ns]").rename("date")
    members = readers.members_panel(con, universe, index)
    names = [c for c in members.columns if members[c].any()]
    prices, repairs = readers.bars_panel(con, names, start, end, field="adj_close")
    absent = sorted(set(names) - set(prices.columns))
    prices = prices.reindex(index)
    members = members.reindex(columns=prices.columns)
    ctx = StrategyContext(prices=prices, rf=rf, members=members)
    targets = fn(ctx, **params)
    audit = audit_causality(fn, ctx, params, targets, config.audit_points)
    acc, tgt, ended, untraded = members_weights(prices, targets, members, execution_lag=config.execution_lag)
    res = run_weights(acc, tgt, config, rf, untraded=untraded)
    res.audit = audit
    low = lower_bound_run(acc, tgt, ended, config, rf, untraded=untraded, weights=res.weights)
    carried = [c for c, d in ended.items()
               if (res.weights[c].loc[res.weights.index > pd.Timestamp(d)].abs() > 1e-12).any()]
    held_into_gap = [(c, d) for d in res.executions for c in res.weights.columns
                     if bool(untraded.at[d, c]) and abs(float(res.weights.at[d, c])) > 1e-12]
    fetched = readers.fetched_to(con, list(ended))
    head, bound = _total(res), _total(low)
    shorts = (low.lower_bound or {}).get("shorts", [])
    res.lower_bound = {"total_net": head, "total_net_lower_bound": bound, "ended_held": carried,
                       "longs": (low.lower_bound or {}).get("longs", []), "shorts": shorts,
                       "returns_net": low.returns_net}
    notes = [note]
    if absent:
        notes.append(f"{len(absent)} member(s) in the window have no bars and are left out: "
                     + ", ".join(absent[:12]) + (" ..." if len(absent) > 12 else ""))
    if carried:
        notes.append(f"{len(carried)} holding(s) outlived their series: each was valued at its last print from its "
                     "last bar and sold there at the next execution. A last print overstates the outcome of a "
                     "failure whose last exchange print exceeds its recovery: "
                     + ", ".join(ended_phrase(c, ended[c], fetched.get(c)) for c in carried[:12]))
    notes.append(f"Total net return {head:+.1%} with holdings whose series ended valued at their last print; "
                 f"{bound:+.1%} with each long one marked to zero at its end -- a lower bound for long holdings, not "
                 "an estimate"
                 + (" (no holding's series ended inside this test, so the two are equal)." if not carried else ".")
                 + (f" The bound covers long holdings only: a short in a vanished name has no upper limit on its loss, "
                    f"and {', '.join(shorts[:12])} {'was' if len(shorts) == 1 else 'were'} short when the series "
                    "ended (kept at the last print in both figures)." if shorts else ""))
    if held_into_gap:
        notes.append(f"{len(held_into_gap)} position(s) were left untraded at an execution because the name had no "
                     "bar that session though its series goes on (a halt or a vendor gap); because such a name cannot "
                     "trade, gross exposure can exceed the leverage cap on that session: "
                     + ", ".join(f"{c} {d.date()}" for c, d in held_into_gap[:12]))
    res.notes = [*notes, *res.notes, *repairs[:20]]
    return res
