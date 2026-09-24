"""/api/risk -- market-risk measurement, validation and stress testing on real prices.

Portfolio returns are ``r_p = sum_i w_i r_i`` on the sessions common to every
holding, with weights held constant (daily rebalanced); ``1 - sum w`` is cash
at zero return. VaR/ES are positive loss fractions of equity and USD amounts
of ``notional``.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import Field, field_validator

from ...data.base import DataUnavailable
from ...data.market import MarketData, get_market
from ...risk import backtest as bt
from ...risk import decomposition as dec
from ...risk import garch as gm
from ...risk import stress as st
from ...risk import var as vm
from ...risk.core import (
    TRADING_DAYS,
    RiskEstimate,
    data_notes,
    ewma_variance_path,
    portfolio_returns,
)
from ..models import PortfolioIn
from ..serialize import clean, frame, records, series

router = APIRouter(prefix="/risk", tags=["risk"])
Market = Annotated[MarketData, Depends(get_market)]

# ------------------------------------------------------------------ caching
_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_LOCK = threading.Lock()
_TTL_S = 900.0
_MAX_ENTRIES = 64


def _cached(name: str, body: Any, fn: Callable[[], Any]) -> Any:
    raw = body.model_dump_json() if hasattr(body, "model_dump_json") else json.dumps(body, default=str)
    key = name + ":" + hashlib.sha256(raw.encode()).hexdigest()
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
class SummaryIn(PortfolioIn):
    alphas: list[float] = Field(default_factory=lambda: [0.99, 0.95], min_length=1, max_length=4)
    horizon: int = Field(default=1, ge=1, le=250)
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)
    evt_threshold: float = Field(default=0.90, ge=0.5, lt=0.995)
    fhs_simulations: int = Field(default=10_000, ge=1_000, le=50_000)

    @field_validator("alphas")
    @classmethod
    def _alphas(cls, v: list[float]) -> list[float]:
        for a in v:
            if not 0.5 < a < 1:
                raise ValueError("each alpha must be in (0.5, 1)")
        return sorted(set(v), reverse=True)


class BacktestIn(PortfolioIn):
    alpha: float = Field(default=0.99, gt=0.5, lt=1.0)
    window: int = Field(default=500, ge=100, le=2000)
    refit_every: int = Field(default=20, ge=1, le=250)
    models: list[str] | None = None
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)
    evt_threshold: float = Field(default=0.90, ge=0.5, lt=0.995)


class DecompositionIn(PortfolioIn):
    alpha: float = Field(default=0.99, gt=0.5, lt=1.0)
    horizon: int = Field(default=1, ge=1, le=250)
    cov_method: Literal["sample", "ewma"] = "sample"
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)


class GarchIn(PortfolioIn):
    alpha: float = Field(default=0.99, gt=0.5, lt=1.0)
    horizon: int = Field(default=10, ge=1, le=250)
    forecast_days: int = Field(default=63, ge=1, le=504)
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)


class HistoricalStressIn(PortfolioIn):
    scenarios: list[str] | None = None


class ConditionalStressIn(PortfolioIn):
    shocks: dict[str, float] = Field(min_length=1, max_length=20)
    cov_method: Literal["sample", "ewma"] = "ewma"
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)

    @field_validator("shocks")
    @classmethod
    def _shocks(cls, v: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, x in v.items():
            t = k.strip().upper()
            if not t:
                continue
            if t in out:
                raise ValueError(f"duplicate shock for {t} (tickers are case/space-insensitive)")
            out[t] = float(x)
        for k, x in out.items():
            if not -1.0 <= x <= 5.0:
                raise ValueError(f"shock for {k} must be a decimal return in [-1, 5]")
        return out


# ------------------------------------------------------------------ helpers
# Budget of rolling re-estimations per backtest: each refit fits t, GARCH, GJR and
# a GPD (~15 ms together); ~150 keeps a 10-year backtest to a few seconds on 2 vCPU.
_MAX_REFITS = 150


def _check_weights(p: PortfolioIn) -> None:
    bad = [t for t, x in p.weights.items() if not math.isfinite(x)]
    if bad:
        raise ValueError(f"weight must be a finite number for: {', '.join(bad)}")
    if p.start is not None and p.end is not None and p.end <= p.start:
        raise ValueError(f"end ({p.end}) must be after start ({p.start})")


def _load_portfolio(p: PortfolioIn, market: MarketData, extra: list[str] | None = None):
    _check_weights(p)
    tickers = list(dict.fromkeys([*p.tickers, *(extra or [])]))
    ds = market.returns(tickers, p.start, p.end)
    rets = ds.data.dropna(how="any")
    if len(rets) < 60:
        raise DataUnavailable(f"only {len(rets)} common return observations for {', '.join(tickers)}")
    rp = portfolio_returns(rets, p.weights)
    if not float(rp.std(ddof=1)) > 0:
        raise ValueError("portfolio returns have zero variance (are all weights zero?); risk is undefined")
    prov = ds.provenance_dicts()
    notes = data_notes(prov)
    notes.append("Portfolio returns use constant weights (daily rebalanced); 1 - sum(weights) is cash at 0%.")
    if len(rp) < 250:
        notes.append(f"only {len(rp)} observations (< 250): tail estimates are imprecise")
    return rets, rp, prov, notes


def _portfolio_meta(p: PortfolioIn, rp: pd.Series) -> dict[str, Any]:
    w = p.weights
    return {
        "holdings": [{"ticker": t, "weight": x} for t, x in w.items()],
        "gross_exposure": float(sum(abs(x) for x in w.values())),
        "net_exposure": float(sum(w.values())),
        "notional": p.notional, "benchmark": p.benchmark,
        "observations": int(len(rp)), "start": rp.index[0], "end": rp.index[-1],
    }


def _usd(x: float | None, notional: float) -> float | None:
    return None if x is None or not math.isfinite(x) else x * notional


def _run(fn: Callable[[], RiskEstimate], name: str, alpha: float, horizon: int) -> dict[str, Any]:
    try:
        return fn().to_dict()
    except (ValueError, np.linalg.LinAlgError) as e:
        return {"model": name, "alpha": alpha, "horizon": horizon, "var": None, "es": None,
                "valid": False, "error": str(e), "notes": [str(e)]}


# ------------------------------------------------------------------ endpoints
@router.post("/summary")
def summary(body: SummaryIn, market: Market) -> dict[str, Any]:
    """All VaR/ES models side by side at every requested confidence level."""
    return _cached("summary", body, lambda: _summary(body, market))


def _summary(body: SummaryIn, market: MarketData) -> dict[str, Any]:
    _, rp, prov, notes = _load_portfolio(body, market)
    r = rp.to_numpy()
    h = body.horizon
    fits: dict[str, gm.GarchFit | None] = {}
    fit_err: dict[str, str] = {}
    for kind in ("garch", "gjr"):
        try:
            fits[kind] = gm.fit_garch(rp, kind)
        except (ValueError, np.linalg.LinAlgError) as e:
            fits[kind], fit_err[kind] = None, str(e)

    def garch_est(kind: str, a: float) -> RiskEstimate:
        if fits[kind] is None:
            raise ValueError(fit_err[kind])
        return gm.garch_var_es(fits[kind], a, h)

    def fhs_est(a: float) -> RiskEstimate:
        if fits["gjr"] is None:
            raise ValueError(fit_err["gjr"])
        return gm.fhs_var_es(fits["gjr"], a, h, simulations=body.fhs_simulations)

    estimates: list[dict[str, Any]] = []
    for a in body.alphas:
        runners: list[tuple[str, Callable[[], RiskEstimate]]] = [
            ("historical", lambda a=a: vm.historical(r, a, h)),
            ("gaussian", lambda a=a: vm.gaussian(r, a, h)),
            ("student_t", lambda a=a: vm.student_t(r, a, h)),
            ("cornish_fisher", lambda a=a: vm.cornish_fisher(r, a, h)),
            ("ewma", lambda a=a: vm.ewma(r, a, h, body.ewma_lambda)),
            ("garch", lambda a=a: garch_est("garch", a)),
            ("gjr_garch", lambda a=a: garch_est("gjr", a)),
            ("fhs", lambda a=a: fhs_est(a)),
            ("evt_pot", lambda a=a: vm.evt_pot(r, a, h, body.evt_threshold)),
        ]
        for name, fn in runners:
            est = _run(fn, name, a, h)
            est["var_usd"] = _usd(est.get("var"), body.notional)
            est["es_usd"] = _usd(est.get("es"), body.notional)
            estimates.append(est)

    table: dict[str, dict[str, Any]] = {}
    for e in estimates:
        table.setdefault(e["model"], {})[f"{e['alpha']:.6g}"] = {
            "var": e.get("var"), "es": e.get("es"), "var_usd": e.get("var_usd"), "es_usd": e.get("es_usd"),
            "valid": e.get("valid", True)}

    from scipy import stats as sps

    counts, edges = np.histogram(r, bins=80)
    centers = (edges[:-1] + edges[1:]) / 2
    width = edges[1] - edges[0]
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    nu = next((e["params"].get("nu") for e in estimates if e["model"] == "student_t" and e.get("params")), None)
    t_pdf = (sps.t.pdf((centers - mu) / (sd * math.sqrt((nu - 2) / nu)), nu) / (sd * math.sqrt((nu - 2) / nu))
             if nu else None)
    dist = {
        "centers": centers, "counts": counts,
        "normal_expected": sps.norm.pdf(centers, mu, sd) * r.size * width,
        "t_expected": t_pdf * r.size * width if t_pdf is not None else None,
    }
    wealth = np.cumprod(1 + r)
    stats_block = {
        "mean_daily": mu, "vol_daily": sd, "vol_annualized": sd * math.sqrt(TRADING_DAYS),
        "skew": float(sps.skew(r, bias=False)), "excess_kurtosis": float(sps.kurtosis(r, bias=False)),
        "min": float(r.min()), "max": float(r.max()),
        "jarque_bera": dict(zip(("stat", "p_value"), map(float, sps.jarque_bera(r)), strict=True)),
        "max_drawdown": float(np.min(wealth / np.maximum.accumulate(wealth) - 1)),
    }
    if h > 1:
        notes.append(f"Horizon {h} days: see each model's notes for its scaling rule.")
    return clean({
        "portfolio": _portfolio_meta(body, rp),
        "alphas": body.alphas, "horizon": h,
        "estimates": estimates, "table": table, "stats": stats_block,
        "distribution": dist, "returns": series(rp),
        "method": {
            "models": list(table),
            "sign_convention": "VaR/ES are positive loss fractions of equity; USD = fraction x notional",
            "es_definition": "ES_alpha = E[L | L >= VaR_alpha]",
            "ewma_lambda": body.ewma_lambda, "evt_threshold_quantile": body.evt_threshold,
            "fhs_filter": "GJR-GARCH(1,1,1)-t", "garch_fit_units": "percent returns",
            "reference": "Jorion (2007); McNeil, Frey & Embrechts (2015) QRM; model-specific references in each estimate",
        },
        "notes": notes,
        "provenance": prov,
    })


@router.post("/backtest")
def backtest(body: BacktestIn, market: Market) -> dict[str, Any]:
    """Rolling out-of-sample 1-day VaR/ES backtest with coverage, independence and ES tests."""
    return _cached("backtest", body, lambda: _backtest(body, market))


def _backtest(body: BacktestIn, market: MarketData) -> dict[str, Any]:
    _, rp, prov, notes = _load_portfolio(body, market)
    models = tuple(body.models) if body.models else bt.ALL_MODELS
    refit = body.refit_every
    if any(m in models for m in ("student_t", "garch", "gjr_garch", "fhs", "evt_pot")):
        refit = max(refit, math.ceil(max(len(rp) - body.window, 1) / _MAX_REFITS))
    if refit != body.refit_every:
        notes.append(f"refit frequency capped: re-estimating every {refit} sessions instead of "
                     f"{body.refit_every} (at most {_MAX_REFITS} refits per backtest on this server); "
                     "GARCH variances are still filtered daily between refits.")
    t0 = time.perf_counter()
    fc = bt.rolling_forecasts(rp.to_numpy(), body.alpha, body.window, refit, models,
                              body.ewma_lambda, body.evt_threshold)
    elapsed = time.perf_counter() - t0
    oos = rp.iloc[fc.start:]
    r = oos.to_numpy()
    cards: dict[str, Any] = {}
    exc: dict[str, list[Any]] = {}
    for m in fc.var:
        card = bt.scorecard(r, fc.var[m], fc.es[m], body.alpha)
        card["info"] = fc.info.get(m, {})
        cards[m] = card
        exc[m] = list(oos.index[(-r > fc.var[m])])
    ranking = sorted(cards, key=lambda m: (cards[m]["fz0_loss"] if cards[m]["fz0_loss"] is not None
                                           and math.isfinite(cards[m]["fz0_loss"]) else math.inf))
    notes.append(f"Out-of-sample period {oos.index[0].date()}..{oos.index[-1].date()} ({len(oos)} days) after a "
                 f"{body.window}-day initial estimation window; each forecast uses only the previous "
                 f"{body.window} sessions.")
    notes.append(f"Student-t dof, GARCH/GJR/FHS and EVT are re-estimated every {refit} sessions; "
                 "GARCH variances are filtered daily with frozen parameters between refits.")
    if abs(body.alpha - 0.99) > 1e-9:
        notes.append("Basel regulatory (plus-factor) view is defined for 99% VaR only; omitted.")
    return clean({
        "portfolio": _portfolio_meta(body, rp),
        "alpha": body.alpha, "window": body.window, "refit_every": body.refit_every,
        "refit_every_effective": refit,
        "scorecards": cards, "ranking_by_fz0": ranking,
        "series": {
            "dates": list(oos.index), "returns": r, "pnl_usd": r * body.notional,
            "var": {m: v for m, v in fc.var.items()}, "es": {m: v for m, v in fc.es.items()},
            "exceptions": exc,
        },
        "compute_seconds": elapsed,
        "method": {
            "design": "rolling-window out-of-sample one-day forecasts",
            "tests": {
                "kupiec": "Kupiec (1995) POF LR ~ chi2(1)",
                "christoffersen": "Christoffersen (1998) independence LR ~ chi2(1), conditional coverage ~ chi2(2)",
                "traffic_light": "BCBS (1996) zones from binomial CDF (green < 95%, yellow < 99.99%)",
                "dq": "Engle & Manganelli (2004) dynamic quantile, const + 4 lags + VaR, ~ chi2(rank X) (6 at full rank)",
                "z2": "Acerbi & Szekely (2014) Z2 ES statistic",
                "scores": "quantile (tick) loss, Gneiting (2011); FZ0 loss, Patton, Ziegel & Chen (2019)",
            },
        },
        "notes": notes,
        "provenance": prov,
    })


@router.post("/decomposition")
def decomposition(body: DecompositionIn, market: Market) -> dict[str, Any]:
    """Euler VaR/ES contributions, incremental and standalone VaR, beta, tracking error."""
    return _cached("decomposition", body, lambda: _decomposition(body, market))


def _decomposition(body: DecompositionIn, market: MarketData) -> dict[str, Any]:
    rets, rp, prov, notes = _load_portfolio(body, market, [body.benchmark])
    d = dec.decompose(rets, body.weights, body.benchmark, body.alpha, body.horizon,
                      body.cov_method, body.ewma_lambda)
    table = d["table"].copy()
    for col in ("component_var", "component_es", "hist_component_es", "incremental_var",
                "incremental_hist_es", "standalone_var", "standalone_es"):
        table[col + "_usd"] = table[col] * body.notional
    totals = dict(d["totals"])
    for k in ("parametric_var", "parametric_es", "historical_var", "historical_es",
              "sum_standalone_var", "diversification_benefit_var"):
        totals[k + "_usd"] = totals[k] * body.notional
    if body.cov_method == "ewma":
        notes.append(f"EWMA covariance (lambda={body.ewma_lambda}) with zero mean (RiskMetrics).")
    if body.horizon > 1:
        notes.append("h-day parametric figures scale mean by h and covariance by h (iid).")
    notes.append("Historical ES components are the negative average position P&L on the portfolio's "
                 "worst k = ceil(n(1-alpha)) days; they sum to historical ES exactly.")
    if body.horizon > 1:
        notes.append(f"Historical {body.horizon}-day figures use OVERLAPPING {body.horizon}-day sums of daily "
                     "position P&L (constant exposure); tail dates are window end dates. Overlap makes the "
                     "effective sample ~n/h.")
    tail_dates = list(rets.index[d["historical_tail_days"]])
    return clean({
        "portfolio": _portfolio_meta(body, rp),
        "alpha": body.alpha, "horizon": body.horizon,
        "positions": records(table, "ticker"), "totals": totals,
        "correlation": frame(d["correlation"]), "benchmark": d.get("benchmark"),
        "historical_tail_dates": tail_dates,
        "method": {
            "euler": "Tasche (2000); component = w_i * dRisk/dw_i, sums to total",
            "parametric": "normal VaR/ES with " + ("EWMA" if body.cov_method == "ewma" else "sample") + " covariance",
            "incremental": "Risk(w) - Risk(w with position i removed)",
            "diversification_ratio": "Choueifaty & Coignard (2008): sum|w_i| sigma_i / sigma_p",
            "tracking_error": "ex-ante sqrt(a' Sigma a), a = w - e_benchmark (Grinold & Kahn 2000)",
            "cov_method": body.cov_method,
        },
        "notes": notes,
        "provenance": prov,
    })


@router.post("/garch")
def garch(body: GarchIn, market: Market) -> dict[str, Any]:
    """GARCH(1,1)-t and GJR-GARCH(1,1,1)-t fits: parameters, vol path, forecast term structure, VaR/ES."""
    return _cached("garch", body, lambda: _garch(body, market))


def _garch(body: GarchIn, market: MarketData) -> dict[str, Any]:
    from scipy import stats as sps

    _, rp, prov, notes = _load_portfolio(body, market)
    out_models: dict[str, Any] = {}
    fits: dict[str, gm.GarchFit] = {}
    for kind in ("garch", "gjr"):
        f = gm.fit_garch(rp, kind)
        fits[kind] = f
        out_models[kind] = {
            "params": f.params_dict(),
            "var_es_1d": gm.garch_var_es(f, body.alpha, 1).to_dict(body.notional),
            "var_es_horizon": gm.garch_var_es(f, body.alpha, body.horizon).to_dict(body.notional),
            "term_structure": frame(gm.term_structure(f, body.forecast_days).set_index("step")),
            "conditional_vol_annualized": series(pd.Series(f.cond_vol * math.sqrt(TRADING_DAYS), index=rp.index)),
            "news_impact": frame(gm.news_impact_curve(f).set_index("shock")),
            "reference": gm.REFERENCES[kind],
        }
    lr = 2 * (fits["gjr"].loglik - fits["garch"].loglik)
    for kind, f in fits.items():
        if f.persistence >= gm.NEAR_UNIT_ROOT:
            notes.append(gm.near_unit_root_note(kind, f.persistence))
    ew = np.sqrt(ewma_variance_path(rp.to_numpy(), body.ewma_lambda)[1:] * TRADING_DAYS)
    fhs = gm.fhs_var_es(fits["gjr"], body.alpha, body.horizon)
    return clean({
        "portfolio": _portfolio_meta(body, rp),
        "alpha": body.alpha, "horizon": body.horizon, "forecast_days": body.forecast_days,
        "models": out_models,
        "comparison": {
            "aic": {k: f.aic for k, f in fits.items()}, "bic": {k: f.bic for k, f in fits.items()},
            "lr_gjr_vs_garch": max(lr, 0.0), "lr_p_value": float(sps.chi2.sf(max(lr, 0.0), 1)),
            "preferred_by_bic": min(fits, key=lambda k: fits[k].bic),
        },
        "fhs": fhs.to_dict(body.notional),
        "ewma_vol_annualized": series(pd.Series(ew, index=rp.index)),
        "realized_abs_return_annualized": series(rp.abs() * math.sqrt(TRADING_DAYS)),
        "method": {
            "estimation": "MLE on percent returns via the arch package; Student-t innovations",
            "mean": "constant", "persistence": "alpha + gamma/2 + beta",
            "term_structure": "E_T[sigma^2_{T+k}] = sigma_bar^2 + P^{k-1}(sigma^2_{T+1} - sigma_bar^2)",
            "reference": "Bollerslev (1986); Glosten, Jagannathan & Runkle (1993); Engle & Ng (1993)",
        },
        "notes": notes + ["LR test of GJR vs GARCH (H0: gamma = 0) uses the chi2(1) reference; "
                          "the p-value is indicative when alpha is at its zero bound."],
        "provenance": prov,
    })


@router.get("/scenarios")
def scenarios() -> dict[str, Any]:
    """Catalogue of historical stress windows."""
    rows = [{k: v for k, v in s.items()} for s in st.load_scenarios()]
    return clean({
        "scenarios": rows,
        "method": {"replay": "actual cumulative returns over each window; beta proxy for missing names",
                   "anchors": {"start_close": "from the close on the start date",
                               "prior_close": "from the close of the session before the start date"}},
        "notes": ["Windows are dated historical events; returns are computed from real prices at request time."],
        "provenance": [],
    })


@router.post("/stress/historical")
def stress_historical(body: HistoricalStressIn, market: Market) -> dict[str, Any]:
    """Replay dated historical crises on the portfolio with real prices."""
    return _cached("stress_hist", body, lambda: _stress_historical(body, market))


def _stress_historical(body: HistoricalStressIn, market: MarketData) -> dict[str, Any]:
    scen = st.load_scenarios()
    if body.scenarios:
        wanted = set(body.scenarios)
        unknown = wanted - {s["id"] for s in scen}
        if unknown:
            raise ValueError(f"unknown scenario ids: {sorted(unknown)}")
        scen = [s for s in scen if s["id"] in wanted]
    _check_weights(body)
    prov: list[dict[str, Any]] = []
    prices: dict[str, pd.Series | None] = {}
    fetch_notes: list[str] = []
    for t in dict.fromkeys([*body.tickers, body.benchmark]):
        try:
            ds = market.ohlcv(t)
            prices[t] = ds.data["adj_close"].dropna()
            prov.extend(ds.provenance_dicts())
        except DataUnavailable as e:
            prices[t] = None
            fetch_notes.append(f"{t}: {e}")
    bench = prices.get(body.benchmark)
    results = [st.replay(s, body.weights, prices, body.benchmark, bench, body.notional) for s in scen]
    summary_rows = [{
        "id": r["id"], "name": r["name"], "start": r["start"], "end": r["end"], "complete": r["complete"],
        "portfolio_return": r.get("portfolio_return"), "pnl_usd": r.get("pnl_usd"),
        "benchmark_return": r.get("benchmark_return"), "max_drawdown": r.get("max_drawdown"),
        "proxied": r.get("proxied", []), "missing": r.get("missing", []),
    } for r in results]
    notes = data_notes(prov) + [
        "Scenario P&L = sum_i w_i x actual cumulative return of holding i over the window (buy-and-hold from the base close).",
        "Holdings without prices in a window are proxied by beta x benchmark return (OLS beta on the nearest "
        "756 overlapping real sessions) and flagged; if no proxy is possible the scenario is incomplete.",
        "For pre-1993 windows (before SPY listed) use an index benchmark such as ^GSPC.",
    ] + fetch_notes
    return clean({
        "portfolio": {"holdings": [{"ticker": t, "weight": w} for t, w in body.weights.items()],
                      "notional": body.notional, "benchmark": body.benchmark},
        "summary": summary_rows, "scenarios": results,
        "method": {"model": "historical scenario replay", "reference": "BCBS (2009) Principles for sound stress "
                   "testing practices and supervision; Jorion (2007) ch. 14"},
        "notes": notes,
        "provenance": prov,
    })


@router.post("/stress/conditional")
def stress_conditional(body: ConditionalStressIn, market: Market) -> dict[str, Any]:
    """Kupiec (1998) conditional stress: shock some assets, move others by their conditional mean."""
    return _cached("stress_cond", body, lambda: _stress_conditional(body, market))


def _stress_conditional(body: ConditionalStressIn, market: MarketData) -> dict[str, Any]:
    rets, rp, prov, notes = _load_portfolio(body, market, list(body.shocks))
    res = st.conditional_stress(rets, body.weights, body.shocks, body.cov_method, body.ewma_lambda,
                                body.notional)
    notes.append("Non-shocked assets move by E[r_o | r_s = s] = Sigma_os Sigma_ss^-1 s (zero mean); "
                 "P&L is linear in the moves (no convexity).")
    big = [t for t, z in res["shock_zscores"].items() if abs(z) > 10]
    if big:
        notes.append(f"shocks larger than 10 daily standard deviations for {big}: the linear-Gaussian "
                     "relationship estimated in normal times may understate crisis co-movement.")
    return clean({
        "portfolio": _portfolio_meta(body, rp),
        "shocks": body.shocks, **res,
        "method": {"model": "conditional (factor) stress test", "covariance": body.cov_method,
                   "ewma_lambda": body.ewma_lambda if body.cov_method == "ewma" else None,
                   "reference": "Kupiec (1998) Stress testing in a value at risk framework, J. Derivatives 6(1)"},
        "notes": notes,
        "provenance": prov,
    })
