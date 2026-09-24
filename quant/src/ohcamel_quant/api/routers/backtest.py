"""/api/backtest -- strategy research on real adjusted closes.

Endpoints
---------
* ``GET  /backtest/strategies``  catalog: parameter schemas, citations, explanations,
  preset universe names, engine options and caps.
* ``POST /backtest/run``         one backtest: net & gross equity vs benchmark, metrics,
  drawdowns, rolling Sharpe, monthly weights, turnover, exposure, bootstrap Sharpe CI, PSR.
* ``POST /backtest/sweep``       parameter grid (<= 64 combos): Sharpe heatmap, CSCV PBO,
  deflated Sharpe, Hansen SPA p-value.
* ``POST /backtest/walkforward`` walk-forward optimization over a grid.
* ``POST /backtest/costs``       Sharpe / CAGR vs proportional cost, break-even costs.

Prices are ``adj_close`` from :class:`MarketData`; cash earns the daily 3-month
T-bill return from ``MarketData.risk_free_daily`` (or 0 -- stated in ``notes`` --
if it cannot be served). When ``start`` is given, extra history before it is
loaded so look-back windows are filled, and no position is taken before ``start``.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from ...backtest import metrics as M
from ...backtest import validation as V
from ...backtest.engine import (
    REBALANCE_CHOICES,
    BacktestResult,
    EngineConfig,
    LookAheadError,
    StrategyContext,
    run_backtest,
)
from ...backtest.strategies import (
    STRATEGIES,
    StrategySpec,
    catalog,
    get_strategy,
    validate_params,
    warmup_sessions,
)
from ...data.base import DataUnavailable
from ...data.fred import rf_error_reason, rf_series_used
from ...data.market import MarketData, get_market, load_universes
from ..serialize import clean, frame, records, series

router = APIRouter(prefix="/backtest", tags=["backtest"])
Market = Annotated[MarketData, Depends(get_market)]

RF_FFILL_SESSIONS = 5
MAX_TICKERS = 60
MAX_COST_POINTS = 30

# ------------------------------------------------------------------ caching
_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_LOCK = threading.Lock()
_MAX_ENTRIES = 32
_TTL_S = 900.0


def _cached(name: str, body: BaseModel, fn: Callable[[], Any]) -> Any:
    key = name + ":" + hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL_S:
            _CACHE.move_to_end(key)
            return hit[1]
    val = fn()
    with _LOCK:
        _CACHE[key] = (now, val)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return val


# ------------------------------------------------------------------ requests
class BacktestIn(BaseModel):
    strategy: str = Field(description="Strategy key from GET /backtest/strategies")
    params: dict[str, Any] = Field(default_factory=dict)
    tickers: list[str] | None = Field(default=None, max_length=MAX_TICKERS)
    universe: str | None = Field(default=None, description="Preset universe key (universes.json)")
    start: date | None = None
    end: date | None = None
    cost_bps: float = Field(default=5.0, ge=0, le=500, description="One-way cost per unit traded notional (bps)")
    borrow_bps: float = Field(default=25.0, ge=0, le=5000, description="Annual fee on short notional (bps)")
    execution_lag: int = Field(default=1, ge=1, le=10)
    rebalance: str | None = None
    max_gross_leverage: float | None = Field(default=None, gt=0, le=10)
    vol_target: float | None = Field(default=None, gt=0, le=1.0)
    vol_estimator: Literal["ewma", "rolling"] = "ewma"
    vol_halflife: float = Field(default=21.0, ge=2, le=252)
    vol_window: int = Field(default=63, ge=10, le=756)
    benchmark: str = Field(default="SPY", min_length=1, max_length=12)
    use_risk_free: bool = True
    rolling_window: int = Field(default=252, ge=21, le=756)
    bootstrap_reps: int = Field(default=1000, ge=100, le=V.MAX_REPS)
    seed: int = 7

    @field_validator("tickers")
    @classmethod
    def _norm(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return list(dict.fromkeys(t.strip().upper() for t in v if t.strip()))

    @field_validator("benchmark")
    @classmethod
    def _bench(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("rebalance")
    @classmethod
    def _reb(cls, v: str | None) -> str | None:
        if v is not None and v not in REBALANCE_CHOICES:
            raise ValueError(f"rebalance must be one of {REBALANCE_CHOICES}")
        return v


class SweepIn(BacktestIn):
    grid: dict[str, list[float]] = Field(description="1-2 numeric parameters -> candidate values")
    n_partitions: int = Field(default=16, ge=2, le=16, description="CSCV partitions S (even)")
    spa_reps: int = Field(default=1000, ge=100, le=V.MAX_REPS)

    @field_validator("grid")
    @classmethod
    def _grid(cls, v: dict[str, list[float]]) -> dict[str, list[float]]:
        if not 1 <= len(v) <= 2:
            raise ValueError("grid must name one or two parameters")
        return v


class WalkForwardIn(BacktestIn):
    grid: dict[str, list[float]]
    is_days: int = Field(default=756, ge=63, le=2520)
    oos_days: int = Field(default=126, ge=21, le=756)
    anchored: bool = False
    objective: Literal["sharpe", "sortino", "cagr", "calmar"] = "sharpe"

    @field_validator("grid")
    @classmethod
    def _grid(cls, v: dict[str, list[float]]) -> dict[str, list[float]]:
        if not 1 <= len(v) <= 3:
            raise ValueError("grid must name one to three parameters")
        return v


class CostsIn(BacktestIn):
    bps: list[float] = Field(default_factory=lambda: [0, 1, 2, 5, 10, 15, 20, 30, 50, 75, 100],
                             min_length=2, max_length=MAX_COST_POINTS)

    @field_validator("bps")
    @classmethod
    def _bps(cls, v: list[float]) -> list[float]:
        if any(x < 0 or x > 1000 for x in v):
            raise ValueError("cost levels must be within [0, 1000] bps")
        return sorted(set(float(x) for x in v))


# ------------------------------------------------------------------ setup
@dataclass
class Prepared:
    spec: StrategySpec
    params: dict[str, Any]
    ctx: StrategyContext
    fn: Callable[..., pd.DataFrame]
    config: EngineConfig
    rf: pd.Series | None
    bench_ret: pd.Series
    provenance: list[dict[str, Any]]
    notes: list[str]
    tickers: list[str]
    start: pd.Timestamp | None


def _resolve_tickers(body: BacktestIn, spec: StrategySpec) -> list[str]:
    if body.tickers:
        return body.tickers
    if body.universe:
        uni = load_universes().get("universes", {})
        if body.universe not in uni:
            raise ValueError(f"unknown universe {body.universe!r}; choose from {sorted(uni)}")
        return [m["ticker"].upper() for m in uni[body.universe]["members"]]
    return list(spec.default_tickers)


def _masked(fn: Callable[..., pd.DataFrame], start: pd.Timestamp | None) -> Callable[..., pd.DataFrame]:
    if start is None:
        return fn

    def wrapped(ctx: StrategyContext, **kw: Any) -> pd.DataFrame:
        w = fn(ctx, **kw).copy()
        w.loc[w.index < start] = np.nan
        return w

    return wrapped


def _accrual_rf(raw: pd.Series, idx: pd.DatetimeIndex, lag_one: bool) -> pd.Series:
    """Cash return credited on each session of ``idx``.

    ``raw`` is a daily decimal return indexed by observation date. A yield
    series (FRED DGS3MO) is an end-of-day print: the rate observed at the close
    of ``s`` is not known during ``(s-1, s]``, so with ``lag_one`` session ``s``
    accrues the rate known at the close of the PREVIOUS session (the first
    session uses the last print before it). Ken French's RF is already the
    realized T-bill return of day ``s`` (set at the start of the month) and is
    used as is."""
    raw = raw.astype(float).sort_index()
    known = raw.reindex(raw.index.union(idx)).ffill(limit=RF_FFILL_SESSIONS)
    at = known.reindex(idx)
    if not lag_one:
        return at
    out = at.shift(1)
    prior = known.loc[known.index < idx[0]].dropna()
    if len(prior) and (idx[0] - prior.index[-1]).days <= 10:
        out.iloc[0] = prior.iloc[-1]
    return out


def _risk_free(market: MarketData, idx: pd.DatetimeIndex) -> tuple[pd.Series | None, list[dict], list[str]]:
    try:
        ds = market.risk_free_daily((idx[0] - pd.Timedelta(days=14)).date(), idx[-1].date())
    except DataUnavailable as e:
        return None, [], [f"Risk-free rate unavailable ({rf_error_reason(e)}); cash earns 0% and Sharpe/Sortino/PSR use raw "
                          "rather than excess returns."]
    prov = ds.provenance_dicts()
    french = rf_series_used(prov) == "FF_RF"
    rf = _accrual_rf(ds.data, idx, lag_one=not french)
    notes = [] if french else [
        "Cash accrues, on each session, the T-bill rate printed at the previous session's close "
        "(a yield printed at today's close is not known during today)."]
    return rf, prov, notes


def _prepare(body: BacktestIn, market: MarketData) -> Prepared:
    spec = get_strategy(body.strategy)
    tickers = _resolve_tickers(body, spec)
    params = validate_params(spec, body.params, tickers)
    extra = [p for p in (params.get("safe_asset"),) if p]
    notes: list[str] = []
    start_ts = pd.Timestamp(body.start) if body.start else None
    data_start = body.start
    if body.start is not None:
        buf = warmup_sessions(spec, params)
        if body.vol_target is not None:
            buf = max(buf, body.vol_window + 5)
        data_start = body.start - timedelta(days=int(buf * 365.25 / 252) + 10)
    if body.end is not None and body.start is not None and body.end <= body.start:
        raise ValueError("end must be after start")
    load = list(dict.fromkeys(tickers + extra + [body.benchmark]))
    ds = market.prices(load, data_start, body.end)
    panel = ds.data.dropna(how="any")
    provenance = ds.provenance_dicts()
    if len(panel) < 150:
        raise DataUnavailable(f"only {len(panel)} common sessions for {', '.join(load)}")
    rf = None
    if body.use_risk_free:
        rf, rf_prov, rf_notes = _risk_free(market, panel.index)
        provenance += rf_prov
        notes += rf_notes
        if rf is not None:
            bad = rf.isna()
            if bad.any():
                notes.append(f"{int(bad.sum())} session(s) outside the published T-bill range were dropped.")
                panel, rf = panel.loc[~bad], rf.loc[~bad]
    else:
        notes.append("use_risk_free=false: cash earns 0% and Sharpe ratios use raw returns.")
    first_common = panel.index[0]
    if body.start is not None and first_common > pd.Timestamp(data_start) + pd.Timedelta(days=10):
        notes.append(f"Common price history starts {first_common.date()}, after the requested warm-up; "
                     "the first signal may come later than the requested start.")
    ctx = StrategyContext(prices=panel[tickers], rf=rf, market=panel[body.benchmark])
    config = EngineConfig(
        cost_bps=body.cost_bps, borrow_bps=body.borrow_bps, execution_lag=body.execution_lag,
        rebalance=body.rebalance or spec.default_rebalance,
        max_gross_leverage=body.max_gross_leverage or spec.default_max_leverage,
        vol_target=body.vol_target, vol_estimator=body.vol_estimator,
        vol_halflife=body.vol_halflife, vol_window=body.vol_window,
    )
    bench_ret = panel[body.benchmark].pct_change().rename(body.benchmark)
    if any("alpaca" in str(p.get("source", "")).lower() for p in provenance):
        notes.append("Alpaca adj_close is split-adjusted only; dividends are excluded, which understates "
                     "total returns of income-paying assets (bond ETFs especially).")
    notes += list(spec.notes)
    if spec.key == "buy_and_hold" and config.rebalance != "never":
        notes.append(f"rebalance={config.rebalance}: weights are reset to equal on that schedule, so this is an "
                     "equal-weight rebalanced portfolio, not buy-and-hold.")
    if (spec.key == "xsmom" and not params.get("long_short")
            and int(params.get("top_k", 0) or 0) >= len(tickers)):
        notes.append(f"top_k={params.get('top_k')} holds all {len(tickers)} assets, so the momentum ranking never "
                     "changes the portfolio: this run is an equal-weight portfolio. Add assets or lower top_k.")
    return Prepared(spec, params, ctx, _masked(spec.fn, start_ts), config, rf, bench_ret,
                    provenance, notes, tickers, start_ts)


def _run(p: Prepared, config: EngineConfig | None = None) -> BacktestResult:
    try:
        return run_backtest(p.ctx, p.fn, p.params, config or p.config)
    except LookAheadError as e:  # our strategies never trigger this; kept explicit
        raise ValueError(str(e)) from e


def _method(p: Prepared, **extra: Any) -> dict[str, Any]:
    c = p.config
    return {
        "model": p.spec.name, "strategy": p.spec.key, "reference": p.spec.citation, "params": p.params,
        "engine": {
            "execution": f"decide at close t, execute at close t+{c.execution_lag}",
            "rebalance": c.rebalance, "cost_bps": c.cost_bps, "borrow_bps_per_year": c.borrow_bps,
            "max_gross_leverage": c.max_gross_leverage, "vol_target": c.vol_target,
            "vol_estimator": c.vol_estimator if c.vol_target else None,
            "cash": ("1 - sum(w) earns the daily T-bill return, no look-ahead (source in provenance, timing in notes)"
                     if p.rf is not None else "earns 0%"),
        },
        **extra,
    }


def _std_notes(p: Prepared, res: BacktestResult, n_live: int) -> list[str]:
    notes = list(p.notes) + list(res.notes)
    if n_live < 252:
        notes.append(f"Only {n_live} live sessions (< 1 year): statistics are very noisy.")
    notes.append("Hypothetical, simulated-execution results on historical prices: fills at the close, "
                 "no market impact beyond the proportional cost, no taxes.")
    return notes


def _monthly_last(df: pd.DataFrame | pd.Series) -> Any:
    return df.groupby(df.index.to_period("M")).tail(1)


def _weekly_last(df: pd.DataFrame | pd.Series) -> Any:
    return df.groupby(df.index.to_period("W")).tail(1)


def _r(df: pd.DataFrame, nd: int = 6) -> pd.DataFrame:
    """Round for the wire (6 decimals of a return / weight = 0.01 bp)."""
    return df.round(nd)


# ------------------------------------------------------------------ endpoints
@router.get("/strategies")
def strategies() -> dict[str, Any]:
    uni = load_universes().get("universes", {})
    return clean({
        "strategies": catalog(),
        "universes": [{"key": k, "label": v.get("label"), "tickers": [m["ticker"] for m in v.get("members", [])]}
                      for k, v in uni.items()],
        "rebalance_choices": list(REBALANCE_CHOICES),
        "engine_defaults": {"cost_bps": 5.0, "borrow_bps": 25.0, "execution_lag": 1, "benchmark": "SPY",
                            "vol_estimator": "ewma", "vol_halflife": 21, "vol_window": 63},
        "caps": {"grid_combinations": V.MAX_GRID, "bootstrap_reps": V.MAX_REPS, "spa_reps": V.MAX_REPS,
                 "tickers": MAX_TICKERS, "cost_points": MAX_COST_POINTS},
        "method": {"model": "Target-weight daily backtester",
                   "reference": "Bailey, Borwein, Lopez de Prado & Zhu (2017); Bailey & Lopez de Prado (2014); "
                                "Politis & Romano (1994); Hansen (2005)"},
        "notes": ["Cost and borrow defaults are user assumptions for liquid US ETFs, not market data; "
                  "change them to match your broker."],
        "provenance": [],
    })


@router.post("/run")
def run(body: BacktestIn, market: Market) -> dict[str, Any]:
    return _cached("run", body, lambda: _do_run(body, market))


def _do_run(body: BacktestIn, market: Market) -> dict[str, Any]:
    p = _prepare(body, market)
    res = _run(p)
    if res.live_start is None:
        raise ValueError("the strategy never produced a signal in this window (history too short for its look-backs)")
    net, gross = res.live(res.returns_net), res.live(res.returns_gross)
    bench = p.bench_ret.loc[net.index]
    rf = p.rf.loc[net.index] if p.rf is not None else None
    eq = pd.DataFrame({"net": M.equity_curve(net), "gross": M.equity_curve(gross),
                       "benchmark": M.equity_curve(bench)})
    dd = pd.DataFrame({"net": M.drawdown_series(net), "benchmark": M.drawdown_series(bench)})
    ex_net, ex_b = M.excess(net, rf), M.excess(bench, rf)
    rolling = pd.DataFrame({
        "sharpe": M.rolling_sharpe(ex_net, body.rolling_window),
        "benchmark_sharpe": M.rolling_sharpe(ex_b, body.rolling_window),
        "vol": M.rolling_vol(net, 63), "benchmark_vol": M.rolling_vol(bench, 63),
    })
    weights_m = _monthly_last(res.live(res.weights))
    turnover_m = res.live(res.turnover).groupby(res.live(res.turnover).index.to_period("M")).sum()
    turnover_m.index = turnover_m.index.to_timestamp(how="end").normalize()
    trades = res.trade_summary()
    per_asset = trades.pop("per_asset")
    cal = pd.DataFrame({"strategy": M.calendar_returns(net), "benchmark": M.calendar_returns(bench)})
    exposure = res.live(res.exposure)
    try:
        boot = V.bootstrap_sharpe(net, rf, body.bootstrap_reps, seed=body.seed)
    except ValueError as e:
        boot = {"error": str(e)}
    out = {
        "strategy": {k: v for k, v in p.spec.to_dict().items() if k in ("key", "name", "category", "citation",
                                                                      "explanation")},
        "params": p.params,
        "tickers": p.tickers,
        "benchmark": body.benchmark,
        "window": {"data_start": p.ctx.prices.index[0], "live_start": res.live_start, "end": net.index[-1],
                   "live_sessions": len(net), "years": len(net) / M.PERIODS},
        "equity": frame(_r(eq)),
        "drawdown": frame(_r(dd)),
        "metrics": {"net": M.summary(net, rf), "gross": M.summary(gross, rf), "benchmark": M.summary(bench, rf)},
        "relative": M.relative_stats(net, bench, rf),
        "drawdowns": M.drawdown_table(net, 5),
        "rolling": frame(_r(_weekly_last(rolling), 4)),
        "weights_monthly": frame(_r(weights_m, 4)),
        "turnover_monthly": series(turnover_m),
        "exposure": frame(_r(_weekly_last(exposure), 4)),
        "vol_scale": series(res.vol_scale.dropna()) if body.vol_target else None,
        "trades": trades,
        "per_asset": records(per_asset.rename_axis("ticker")),
        "calendar_returns": records(cal),
        "monthly_returns": records(M.monthly_returns(net)),
        "bootstrap_sharpe": boot,
        "psr": {"psr_vs_0": M.summary(net, rf)["psr_vs_0"],
                "reference": "Bailey & Lopez de Prado (2012), The Sharpe Ratio Efficient Frontier"},
        "look_ahead_audit": res.audit,
        "method": _method(p),
        "notes": _std_notes(p, res, len(net)),
        "provenance": p.provenance,
    }
    return clean(out)


def _distinct_trials(rets: pd.DataFrame, tol: float = 1e-10, max_share: float = 0.005) -> int:
    """Number of effectively distinct trial return streams.

    Two trials count as the same when they differ (by more than ``tol``) on fewer than
    ``max_share`` of sessions -- e.g. only on the first live session, because a longer
    look-back starts trading a day later but then holds the same positions.
    """
    m = rets.to_numpy(dtype=float)
    reps: list[np.ndarray] = []
    for j in range(m.shape[1]):
        col = m[:, j]
        if not any(np.mean(np.abs(col - r) > tol) < max_share for r in reps):
            reps.append(col)
    return len(reps)


@router.post("/sweep")
def sweep(body: SweepIn, market: Market) -> dict[str, Any]:
    return _cached("sweep", body, lambda: _do_sweep(body, market))


def _do_sweep(body: SweepIn, market: Market) -> dict[str, Any]:
    if body.n_partitions % 2:
        raise ValueError("n_partitions must be even")
    p = _prepare(body, market)
    sw = V.sweep(p.ctx, p.spec, p.params, body.grid, p.config, p.rf)
    rets = sw.returns
    rf = p.rf.reindex(rets.index) if p.rf is not None else None
    bench = p.bench_ret.loc[rets.index]
    keys = list(body.grid)
    tab = sw.table
    heat: dict[str, Any] = {"x_param": keys[0], "x_values": body.grid[keys[0]], "metric": "sharpe"}
    if len(keys) == 2:
        heat.update({"y_param": keys[1], "y_values": body.grid[keys[1]]})
        z = np.full((len(body.grid[keys[1]]), len(body.grid[keys[0]])), np.nan)
        for _, row in tab.iterrows():
            xi = body.grid[keys[0]].index(row[keys[0]])
            yi = body.grid[keys[1]].index(row[keys[1]])
            z[yi, xi] = row["sharpe"]
        heat["z"] = z
    else:
        z = np.full(len(body.grid[keys[0]]), np.nan)
        for _, row in tab.iterrows():
            z[body.grid[keys[0]].index(row[keys[0]])] = row["sharpe"]
        heat["z"] = z
    notes = list(p.notes) + [f"{len(sw.combos)} combinations evaluated on the {len(rets)} sessions where all are live."]
    if sw.skipped:
        notes.append(f"{len(sw.skipped)} combination(s) skipped: " + "; ".join(s["reason"] for s in sw.skipped[:3]))
    # Combinations whose parameters do not change the positions (e.g. top-k >= number of assets)
    # produce identical return streams. CSCV ranks ties at the median (logit 0), which would
    # report a PBO of 100% for a sweep that selected nothing -- so detect it and say so.
    n_distinct = _distinct_trials(rets)
    if 0 < n_distinct < len(sw.combos):
        notes.append(f"Only {n_distinct} of {len(sw.combos)} combinations produced distinct return streams: "
                     "the other parameter values change nothing on this universe, so the sweep is smaller "
                     "than it looks (identical trials tie in the CSCV ranking).")
    if n_distinct < 2:
        pbo = {"error": f"all {len(sw.combos)} combinations produced identical returns -- the swept parameters "
                        "have no effect on this universe (e.g. top-k at least the number of assets), "
                        "so there is no selection to overfit"}
    else:
        try:
            # rank trials on EXCESS Sharpe, the criterion used for 'best' and the DSR
            pbo = V.cscv_pbo(rets.sub(rf, axis=0) if rf is not None else rets, body.n_partitions)
        except ValueError as e:
            pbo = {"error": str(e)}
    dsr = V.deflated_sharpe_for_grid(rets, None, rf)
    spa = V.spa_test(bench, rets, body.spa_reps, seed=body.seed)
    best = int(tab["sharpe"].idxmax())
    weekly = (1.0 + rets).cumprod()
    weekly = weekly.groupby(weekly.index.to_period("W")).tail(1)
    weekly.columns = [f"#{i}" for i in range(len(sw.combos))]
    out = {
        "strategy": {"key": p.spec.key, "name": p.spec.name, "citation": p.spec.citation},
        "base_params": p.params, "grid": body.grid, "combos": sw.combos,
        "table": records(tab.assign(combo=range(len(tab))).set_index("combo")),
        "heatmap": heat,
        "best": {"combo": best, "params": sw.combos[best], "sharpe": float(tab.loc[best, "sharpe"])},
        "pbo": pbo, "deflated_sharpe": dsr, "spa": spa,
        "benchmark": {"ticker": body.benchmark, "summary": M.summary(bench, rf)},
        "equity_weekly": frame(_r(weekly, 5)),
        "window": {"start": rets.index[0], "end": rets.index[-1], "sessions": len(rets)},
        "caps": {"grid_combinations": V.MAX_GRID, "spa_reps": V.MAX_REPS},
        "method": _method(p, validation={
            "pbo": "CSCV, Bailey, Borwein, Lopez de Prado & Zhu (2017), J. Computational Finance 20(4); "
                   "trials ranked on per-period excess-return Sharpe",
            "dsr": "Bailey & Lopez de Prado (2014), J. Portfolio Management 40(5); n_trials = grid size",
            "spa": "Hansen (2005), JBES 23(4); stationary bootstrap, H0: no combination beats the benchmark",
        }),
        "notes": notes + ["PBO near 0 means the in-sample winner also ranks well out-of-sample; above 0.5 "
                          "means parameter selection is worse than a coin flip.",
                          "SPA benchmark = buy-and-hold of the benchmark ticker (no costs)."],
        "provenance": p.provenance,
    }
    return clean(out)


@router.post("/walkforward")
def walkforward(body: WalkForwardIn, market: Market) -> dict[str, Any]:
    return _cached("walkforward", body, lambda: _do_walkforward(body, market))


def _do_walkforward(body: WalkForwardIn, market: Market) -> dict[str, Any]:
    p = _prepare(body, market)
    sw = V.sweep(p.ctx, p.spec, p.params, body.grid, p.config, p.rf)
    rf = p.rf.reindex(sw.returns.index) if p.rf is not None else None
    ref = next((i for i, c in enumerate(sw.combos) if all(p.params.get(k) == v for k, v in c.items())), None)
    wf = V.walk_forward(sw, body.is_days, body.oos_days, body.anchored, body.objective,
                        p.config.cost_bps, rf, reference=ref, lag=p.config.execution_lag)
    oos = wf["returns"]
    bench = p.bench_ret.loc[oos.index]
    curves = {"walk_forward": M.equity_curve(oos), "benchmark": M.equity_curve(bench)}
    if "reference" in wf:
        curves["base_params"] = M.equity_curve(wf["reference"]["returns"])
    out = {
        "strategy": {"key": p.spec.key, "name": p.spec.name, "citation": p.spec.citation},
        "grid": body.grid, "combos": sw.combos,
        "folds": wf["folds"],
        "equity": frame(_r(pd.DataFrame(curves))),
        "oos_summary": wf["oos_summary"],
        "benchmark_summary": M.summary(bench, rf),
        "reference": {"params": wf["reference"]["params"], "summary": wf["reference"]["summary"]}
        if "reference" in wf else None,
        "walk_forward_efficiency": wf["walk_forward_efficiency"],
        "mean_is_sharpe": wf["mean_is_sharpe"], "oos_sharpe": wf["oos_sharpe"],
        "distinct_choices": wf["distinct_choices"],
        "method": _method(p, validation={
            "walk_forward": "Pardo (2008), The Evaluation and Optimization of Trading Strategies, ch. 11",
            "is_days": body.is_days, "oos_days": body.oos_days, "anchored": body.anchored,
            "objective": body.objective}),
        "notes": list(p.notes) + [
            "Each fold picks the grid combination with the best in-sample objective and applies it to the next "
            "out-of-sample block; switching between combinations pays cost_bps on the weight change.",
            f"The in-sample block ends execution_lag = {p.config.execution_lag} session(s) before the switch close "
            "(no same-close selection).",
            "Walk-forward efficiency = OOS Sharpe / mean in-sample Sharpe of the chosen combinations."],
        "caps": {"grid_combinations": V.MAX_GRID},
        "provenance": p.provenance,
    }
    return clean(out)


@router.post("/costs")
def costs(body: CostsIn, market: Market) -> dict[str, Any]:
    return _cached("costs", body, lambda: _do_costs(body, market))


def _do_costs(body: CostsIn, market: Market) -> dict[str, Any]:
    p = _prepare(body, market)
    res = _run(p)
    if res.live_start is None:
        raise ValueError("the strategy never produced a signal in this window")
    rf = p.rf if p.rf is not None else None
    cs = V.cost_sensitivity(res, body.bps, rf, p.bench_ret)
    net = res.live(res.returns_net)
    out = {
        "strategy": {"key": p.spec.key, "name": p.spec.name, "citation": p.spec.citation},
        "params": p.params,
        "curve": cs["curve"],
        "breakeven_bps_sharpe_zero": cs["breakeven_bps_sharpe_zero"],
        "breakeven_bps_vs_benchmark": cs["breakeven_bps_vs_benchmark"],
        "break_even_case": cs["break_even_case"],
        "break_even_case_vs_benchmark": cs["break_even_case_vs_benchmark"],
        "annual_turnover": cs["annual_turnover"],
        "benchmark": {"ticker": body.benchmark, "summary": M.summary(p.bench_ret.loc[net.index], rf)},
        "window": {"live_start": res.live_start, "end": net.index[-1], "sessions": len(net)},
        "method": _method(p, costs="1 + net = (1 + gross - borrow)(1 - c * turnover); turnover is cost-invariant"),
        "notes": _std_notes(p, res, len(net)) + [
            "Break-even = proportional cost (bps per unit traded) at which the Sharpe ratio reaches 0 or the "
            "CAGR falls to the benchmark's; null when it is not crossed between 0 and 10,000 bps. "
            "break_even_case says why: 'crosses' (a break-even exists), 'always_above' (the Sharpe ratio stays "
            "positive even at 10,000 bps) or 'always_below' (it is <= 0 even at zero cost); "
            "break_even_case_vs_benchmark is the same for the CAGR-vs-benchmark break-even."],
        "caps": {"cost_points": MAX_COST_POINTS},
        "provenance": p.provenance,
    }
    return clean(out)


__all__ = ["router", "STRATEGIES"]
