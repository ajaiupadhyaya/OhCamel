"""/api/performance -- portfolio performance analytics on real prices.

Portfolio returns come from ``adj_close`` of every holding on common sessions,
aggregated with :func:`ohcamel_quant.factors.performance.portfolio_returns`
(daily / periodic rebalancing or buy-and-hold drift); cash ``1 - sum(w)``
earns the daily risk-free rate from ``MarketData.risk_free_daily`` (3-month
T-bill), or 0 -- stated in ``notes`` -- when that series cannot be served.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import Field

from ...data.base import DataUnavailable
from ...data.fred import rf_error_reason, rf_series_used
from ...data.market import MarketData, get_market
from ...factors import performance as perf
from ..models import PortfolioIn
from ..serialize import clean, frame, records

router = APIRouter(prefix="/performance", tags=["performance"])
Market = Annotated[MarketData, Depends(get_market)]
RebalanceT = Literal["daily", "weekly", "monthly", "quarterly", "annual", "none"]

RF_FFILL_SESSIONS = 5  # carry the last published T-bill yield across at most 5 sessions

# ------------------------------------------------------------------ caching
_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_LOCK = threading.Lock()
_MAX_ENTRIES = 64


def cached(name: str, body: Any, fn: Callable[[], Any], ttl_s: float = 900.0) -> Any:
    """Small in-process TTL/LRU cache keyed on the endpoint name and request body."""
    raw = body.model_dump_json() if hasattr(body, "model_dump_json") else json.dumps(body, default=str, sort_keys=True)
    key = name + ":" + hashlib.sha256(raw.encode()).hexdigest()
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl_s:
            _CACHE.move_to_end(key)
            return hit[1]
    val = fn()
    with _LOCK:
        _CACHE[key] = (now, val)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return val


# ------------------------------------------------------------------ helpers
def data_notes(provenance: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    if any("alpaca" in str(p.get("source", "")).lower() for p in provenance):
        notes.append("Alpaca adj_close is split-adjusted only; dividends are excluded from returns.")
    return notes


def risk_free(market: MarketData, index: pd.DatetimeIndex) -> tuple[pd.Series | None, list[dict], list[str]]:
    """Daily rf aligned to ``index`` (carried forward over <= 5 sessions past the
    last print), or ``None`` with a note when no source can serve it."""
    try:
        ds = market.risk_free_daily(index[0].date(), index[-1].date())
    except DataUnavailable as e:
        return None, [], [f"Risk-free rate unavailable ({rf_error_reason(e)}). rf = 0 was used, so Sharpe, "
                          "Sortino, alpha and M2 are computed on raw rather than excess returns, and cash "
                          "earns 0%."]
    rf = ds.data.reindex(index.union(ds.data.index)).sort_index().ffill(limit=RF_FFILL_SESSIONS).reindex(index)
    notes = []
    missing = int(rf.isna().sum())
    if missing:
        notes.append(f"Risk-free rate missing on {missing} session(s) outside its published range; "
                     "those sessions were dropped.")
    return rf.astype(float), ds.provenance_dicts(), notes


def rf_source(rf: pd.Series | None, rf_prov: list[dict[str, Any]]) -> str:
    """Honest label of the risk-free source actually used (FRED DGS3MO, or the
    Ken French RF fallback that ``MarketData.risk_free_daily`` switches to)."""
    if rf is None:
        return "none (0)"
    if rf_series_used(rf_prov) == "FF_RF":
        return "Kenneth French daily RF (1-month T-bill; FRED DGS3MO unavailable)"
    return "FRED DGS3MO -> (1 + y)^(1/252) - 1"


def check_exposure(weights: dict[str, float]) -> None:
    """Reject a portfolio with no risky exposure (all weights zero after
    aggregating duplicate tickers): every risk statistic would be undefined."""
    if not any(abs(x) > 1e-12 for x in weights.values()):
        raise ValueError("portfolio has zero gross exposure (all holding weights are 0 after netting "
                         "duplicate tickers); nothing to analyse")


def load_returns(market: MarketData, tickers: list[str], start: date | None, end: date | None,
                 min_obs: int = 60) -> tuple[pd.DataFrame, list[dict]]:
    ds = market.returns(tickers, start, end)
    rets = ds.data.dropna(how="any")
    if len(rets) < min_obs:
        raise DataUnavailable(f"only {len(rets)} common return observations for {', '.join(tickers)}")
    return rets, ds.provenance_dicts()


def portfolio_meta(weights: dict[str, float], rebalance: str) -> dict[str, Any]:
    return {
        "holdings": [{"ticker": t, "weight": x} for t, x in weights.items()],
        "gross_exposure": float(sum(abs(x) for x in weights.values())),
        "net_exposure": float(sum(weights.values())),
        "cash_weight": 1.0 - float(sum(weights.values())),
        "rebalance": rebalance,
    }


REBALANCE_NOTE = {
    "daily": "Weights are reset to target every session (constant-mix).",
    "weekly": "Weights drift with prices and are reset to target at each week's last session.",
    "monthly": "Weights drift with prices and are reset to target at each month's last session.",
    "quarterly": "Weights drift with prices and are reset to target at each quarter's last session.",
    "annual": "Weights drift with prices and are reset to target at each year's last session.",
    "none": "Buy-and-hold: initial weights drift with prices and are never rebalanced.",
}


# ------------------------------------------------------------------ request
class PerformanceIn(PortfolioIn):
    rebalance: RebalanceT = "monthly"
    rolling_window: int = Field(default=252, ge=21, le=1260)
    var_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    omega_threshold: float = Field(default=0.0, ge=-0.5, le=1.0, description="annual decimal")
    top_drawdowns: int = Field(default=5, ge=1, le=25)
    sr_benchmark: float = Field(default=0.0, ge=-3.0, le=5.0, description="annualised PSR hurdle")
    psr_confidence: float = Field(default=0.95, gt=0.5, lt=1.0)
    n_trials: int = Field(default=1, ge=1, le=10_000_000)
    sr_variance: float | None = Field(default=None, ge=0.0, description="annualised variance of trial Sharpes")
    nw_lags: int | None = Field(default=None, ge=0, le=100)


# ------------------------------------------------------------------ endpoint
@router.post("/analyze")
def analyze(body: PerformanceIn, market: Market) -> dict[str, Any]:
    """Full tear sheet: returns, risk-adjusted ratios with Lo (2002) inference,
    PSR/MinTRL/DSR, drawdown episodes, tails, monthly table, rolling stats and
    benchmark-relative statistics."""
    return cached("analyze", body, lambda: _analyze(body, market))


def _analyze(body: PerformanceIn, market: MarketData) -> dict[str, Any]:
    bench = body.benchmark.strip().upper()
    weights = body.weights
    check_exposure(weights)
    tickers = list(dict.fromkeys([*weights, *([bench] if bench else [])]))
    rets, prov = load_returns(market, tickers, body.start, body.end)
    notes = data_notes(prov)
    rf, rf_prov, rf_notes = risk_free(market, rets.index)
    prov += rf_prov
    notes += rf_notes
    if rf is not None:
        keep = rf.notna()
        rets, rf = rets[keep], rf[keep]
    rp, wpath = perf.portfolio_returns(rets, weights, body.rebalance, rf, return_weights=True)
    b = rets[bench] if bench else None
    report = perf.performance_report(
        rp, b, rf, var_level=body.var_level, omega_threshold=body.omega_threshold,
        top_drawdowns=body.top_drawdowns, rolling_window=body.rolling_window,
        sr_benchmark=body.sr_benchmark, psr_confidence=body.psr_confidence,
        n_trials=body.n_trials, sr_variance=body.sr_variance, nw_lags=body.nw_lags)

    notes.append(REBALANCE_NOTE[body.rebalance])
    notes.append("Cash (1 - sum of weights) earns the daily risk-free rate." if rf is not None
                 else "Cash (1 - sum of weights) earns 0%.")
    if len(rp) < 252:
        notes.append(f"only {len(rp)} observations (< 1 year): annualised figures are unreliable")
    if len(rp) < body.rolling_window:
        notes.append(f"history shorter than the {body.rolling_window}-day rolling window; rolling stats omitted")
    if body.n_trials > 1 and body.sr_variance is None:
        notes.append("Deflated Sharpe needs sr_variance (variance of the trial Sharpe ratios); not computed.")
    notes.append("Up/down capture uses calendar-month compounded returns (Morningstar convention); "
                 "daily versions are also reported.")

    equity = pd.DataFrame({"portfolio": report["equity"]})
    dd = pd.DataFrame({"portfolio": report["drawdown"]})
    bench_summary = None
    if b is not None:
        equity["benchmark"] = perf.equity_curve(b)
        dd["benchmark"] = perf.drawdown_series(b)
        br = perf.performance_report(b, None, rf, var_level=body.var_level,
                                     omega_threshold=body.omega_threshold, top_drawdowns=1,
                                     rolling_window=len(b) + 1, nw_lags=body.nw_lags)
        bench_summary = br["summary"]

    to_stats: dict[str, Any] | None = None
    if body.rebalance not in ("daily", "none"):
        to = perf.turnover(wpath, weights, rets, rf, rebalance=body.rebalance)
        years = len(rp) / perf.TRADING_DAYS
        to_stats = {"rebalances": len(to), "mean_per_rebalance": float(to.mean()) if len(to) else 0.0,
                    "annual": float(to.sum()) / years if years > 0 else None}
    drifted = wpath.iloc[-1] * (1.0 + pd.concat(
        [rets.iloc[-1][list(weights)], pd.Series({"CASH": float(rf.iloc[-1]) if rf is not None else 0.0})]))
    current = (drifted / drifted.sum()).rename("weight")

    monthly = report["monthly_table"]
    return clean({
        "portfolio": {**portfolio_meta(weights, body.rebalance), "benchmark": bench or None,
                      "observations": len(rp), "start": rp.index[0], "end": rp.index[-1],
                      "current_weights": current.to_dict(), "turnover": to_stats},
        "summary": report["summary"],
        "benchmark_summary": bench_summary,
        "sharpe_inference": report["sharpe_inference"],
        "relative": report["relative"],
        "drawdowns": records(report["drawdowns"]),
        "monthly_table": {"columns": list(monthly.columns), "rows": records(monthly)},
        "equity": frame(equity),
        "drawdown": frame(dd),
        "rolling": frame(report["rolling"]) if report["rolling"] is not None else None,
        "rolling_window": body.rolling_window,
        "returns_histogram": _histogram(rp),
        "method": {
            "returns": "simple daily returns from adj_close on common sessions",
            "rebalance": body.rebalance,
            "risk_free": rf_source(rf, rf_prov),
            "sharpe_se": "Lo (2002), 'The Statistics of Sharpe Ratios', FAJ 58(4): IID, non-normal "
                         "(Mertens 2002) and GMM/Newey-West HAC",
            "psr_mintrl": "Bailey & Lopez de Prado (2012), 'The Sharpe Ratio Efficient Frontier', J. Risk 15(2)",
            "dsr": "Bailey & Lopez de Prado (2014), 'The Deflated Sharpe Ratio', JPM 40(5)",
            "capm": "Jensen (1968) alpha, OLS with Newey-West (1987) HAC t-stats",
            "m2": "Modigliani & Modigliani (1997), 'Risk-Adjusted Performance', JPM 23(2)",
            "sortino": "Sortino & Price (1994); MAR = risk-free rate",
            "omega": "Keating & Shadwick (2002)",
            "var": f"historical {body.var_level:.1%} VaR/CVaR of daily returns (positive = loss)",
            "annualisation": "arithmetic x252 for means, sqrt(252) for vols; geometric for CAGR",
        },
        "notes": notes,
        "provenance": prov,
    })


def _histogram(r: pd.Series, bins: int = 60) -> dict[str, Any]:
    counts, edges = np.histogram(r.to_numpy(), bins=bins)
    return {"counts": counts.tolist(), "edges": edges.tolist()}
