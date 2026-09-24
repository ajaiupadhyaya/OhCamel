"""/api/portfolio -- portfolio construction on real returns.

Every endpoint takes a universe of tickers (``UniverseIn``) and options, loads
daily adjusted-close returns on common sessions from :class:`MarketData`, and
runs the pure analytics in :mod:`ohcamel_quant.portfolio`. The risk-free rate
comes from ``MarketData.risk_free_daily`` (FRED DGS3MO, or Ken French RF); a
caller may instead supply ``risk_free_rate`` explicitly (annual decimal), which is
reported as user-supplied. Nothing is ever substituted for missing data.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator

from ...data.base import DataUnavailable
from ...data.fred import rf_error_reason, rf_series_used
from ...data.market import MarketData, get_market
from ...portfolio import covariance as cv
from ...portfolio import expected as er_mod
from ...portfolio import hrp as hrp_mod
from ...portfolio import walkforward as wf
from ...portfolio.methods import METHODS, run_method
from ...portfolio.optimize import Constraints, efficient_frontier
from ...portfolio.optimize import evaluate as opt_evaluate
from ..models import UniverseIn
from ..serialize import clean, frame, records, series

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
Market = Annotated[MarketData, Depends(get_market)]
TRADING_DAYS = 252
RF_FFILL_SESSIONS = 5  # rows a risk-free print may be carried forward (holidays / 1-2 day FRED lag)

MethodName = Literal["equal_weight", "inverse_volatility", "min_variance", "max_sharpe", "mean_variance",
                     "risk_parity", "hrp", "herc", "max_diversification", "min_cvar"]
CovName = Literal["sample", "ewma", "lw_constant_corr", "lw_identity", "oas", "mp_denoise"]
ReturnsModel = Literal["historical", "james_stein", "capm", "black_litterman"]
LinkageName = Literal["single", "ward", "average", "complete"]

# ------------------------------------------------------------------ caching
_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_LOCK = threading.Lock()
_TTL_S = 900.0
_MAX_ENTRIES = 64


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
def _finite_map(v: dict | None, what: str) -> dict | None:
    """Upper-case ticker keys and reject NaN/inf values (JSON parsers accept NaN)."""
    if v is None:
        return None
    out: dict[str, Any] = {}
    for k, val in v.items():
        nums = val if isinstance(val, (tuple, list)) else (val,)
        if not all(math.isfinite(float(x)) for x in nums):
            raise ValueError(f"{what} for {k} must be finite numbers")
        out[k.strip().upper()] = val
    return out


class ConstraintsIn(BaseModel):
    """Budget ``sum w = 1`` always holds. Shorting is allowed by a negative ``min_weight``."""

    min_weight: float = Field(default=0.0, ge=-2.0, le=1.0)
    max_weight: float = Field(default=1.0, gt=0.0, le=3.0)
    bounds: dict[str, tuple[float, float]] | None = None  # per-ticker [lo, hi] overrides
    max_gross: float | None = Field(default=None, ge=1.0, le=10.0)  # sum |w| <= L
    max_turnover: float | None = Field(default=None, ge=0.0, le=4.0)  # sum |w - w0| <= T
    current_weights: dict[str, float] | None = None

    @field_validator("bounds", "current_weights")
    @classmethod
    def _upper(cls, v: dict | None) -> dict | None:
        return _finite_map(v, "bounds/current_weights")

    def build(self, tickers: list[str]) -> Constraints:
        lo = np.full(len(tickers), self.min_weight)
        hi = np.full(len(tickers), self.max_weight)
        for t, (a, b) in (self.bounds or {}).items():
            if t not in tickers:
                raise ValueError(f"bounds given for {t}, which is not in the universe")
            i = tickers.index(t)
            lo[i], hi[i] = a, b
        cur = None
        if self.current_weights is not None:
            unknown = set(self.current_weights) - set(tickers)
            if unknown:
                raise ValueError(f"current weights for tickers outside the universe: {', '.join(sorted(unknown))}")
            cur = np.array([float(self.current_weights.get(t, 0.0)) for t in tickers])
        if self.max_turnover is not None and cur is None:
            raise ValueError("max_turnover requires current_weights")
        return Constraints(lo, hi, self.max_gross, self.max_turnover, cur)


class ViewIn(BaseModel):
    """Absolute view (``long`` returns ``value`` p.a.) or relative (``long`` beats ``short`` by ``value``)."""

    long: str
    short: str | None = None
    value: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("long", "short")
    @classmethod
    def _norm(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else None


class EstimationIn(UniverseIn):
    cov_method: CovName = "lw_constant_corr"
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)
    returns_model: ReturnsModel = "historical"
    benchmark: str = "SPY"
    risk_free_rate: float | None = Field(default=None, ge=-0.05, le=0.5,
                                         description="annual decimal; omit to use FRED DGS3MO")
    views: list[ViewIn] = Field(default_factory=list, max_length=20)
    tau: float = Field(default=0.05, gt=0.0, le=1.0)
    prior_weights: dict[str, float] | None = None
    prior_risk_aversion: float | None = Field(default=None, gt=0.0, le=50.0)
    constraints: ConstraintsIn = Field(default_factory=ConstraintsIn)

    @field_validator("benchmark")
    @classmethod
    def _bench(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("prior_weights")
    @classmethod
    def _pw(cls, v: dict | None) -> dict | None:
        v = _finite_map(v, "prior_weights")
        return None if v is None else {k: float(x) for k, x in v.items()}


class MethodParams(BaseModel):
    target_return: float | None = Field(default=None, ge=-1.0, le=5.0)
    target_vol: float | None = Field(default=None, gt=0.0, le=5.0)
    risk_aversion: float | None = Field(default=None, gt=0.0, le=1000.0)
    risk_budgets: dict[str, float] | None = None
    linkage: LinkageName | None = None
    n_clusters: int | None = Field(default=None, ge=1, le=60)
    cvar_alpha: float = Field(default=0.95, gt=0.5, lt=1.0)

    @field_validator("risk_budgets")
    @classmethod
    def _rb(cls, v: dict | None) -> dict | None:
        v = _finite_map(v, "risk_budgets")
        return None if v is None else {k: float(x) for k, x in v.items()}

    def kwargs(self) -> dict[str, Any]:
        return self.model_dump(include={"target_return", "target_vol", "risk_aversion", "risk_budgets",
                                        "linkage", "n_clusters", "cvar_alpha"})


class OptimizeIn(EstimationIn, MethodParams):
    method: MethodName = "max_sharpe"


class EvaluateIn(EstimationIn):
    """Given weights (fully invested: they must sum to 1; negative = short) evaluated
    under the same estimation options as /optimize. ``constraints`` are only checked."""

    weights: dict[str, float] = Field(min_length=1, max_length=60)

    @field_validator("weights")
    @classmethod
    def _w(cls, v: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, x in (_finite_map(v, "weights") or {}).items():
            out[k] = out.get(k, 0.0) + float(x)
        return out

    @model_validator(mode="after")
    def _check(self) -> EvaluateIn:
        unknown = sorted(set(self.weights) - set(self.tickers))
        if unknown:
            raise ValueError(f"weights given for tickers outside the universe: {', '.join(unknown)}")
        tot = sum(self.weights.values())
        if abs(tot - 1.0) > 1e-4:
            raise ValueError(f"weights must sum to 1 (fully invested, as every optimizer here); got {tot:.6f}")
        return self


class FrontierIn(EstimationIn):
    points: int = Field(default=40, ge=5, le=120)
    overlay_methods: list[MethodName] = Field(
        default_factory=lambda: ["equal_weight", "inverse_volatility", "min_variance", "risk_parity",
                                 "hrp", "max_diversification"])


class CovarianceIn(UniverseIn):
    estimator: CovName = "lw_constant_corr"
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)
    linkage: LinkageName = "single"
    mp_bandwidth: float = Field(default=0.01, gt=0.0, le=1.0)


class CompareIn(UniverseIn, MethodParams):
    methods: list[MethodName] = Field(
        default_factory=lambda: ["equal_weight", "inverse_volatility", "min_variance", "max_sharpe",
                                 "risk_parity", "hrp", "max_diversification", "min_cvar"],
        min_length=1, max_length=10)
    benchmark_method: MethodName = "equal_weight"
    window: int = Field(default=504, ge=60, le=2520)
    rebalance: Literal["W", "M", "Q"] = "M"
    cost_bps: float = Field(default=10.0, ge=0.0, le=500.0)
    cov_method: CovName = "lw_constant_corr"
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)
    mu_method: Literal["historical", "james_stein"] = "historical"
    risk_free_rate: float | None = Field(default=None, ge=-0.05, le=0.5)
    min_weight: float = Field(default=0.0, ge=-2.0, le=1.0)
    max_weight: float = Field(default=1.0, gt=0.0, le=3.0)
    max_gross: float | None = Field(default=None, ge=1.0, le=10.0)
    max_turnover: float | None = Field(default=None, ge=0.0, le=4.0)

    @model_validator(mode="after")
    def _mv(self) -> CompareIn:
        if "mean_variance" in self.methods and all(
                x is None for x in (self.target_return, self.target_vol, self.risk_aversion)):
            raise ValueError("mean_variance needs target_return, target_vol or risk_aversion")
        return self


# ------------------------------------------------------------------ data helpers
def _data_notes(prov: list[dict[str, Any]], n_obs: int, n_assets: int) -> list[str]:
    notes: list[str] = []
    if any("alpaca" in str(p.get("source", "")).lower() for p in prov):
        notes.append("Alpaca adj_close is split-adjusted only; dividends are excluded from returns "
                     "(understates income-heavy assets such as bond ETFs).")
    if n_obs < 250:
        notes.append(f"only {n_obs} common daily observations (< 250): estimates are imprecise")
    if n_obs < 10 * n_assets:
        notes.append(f"T/N = {n_obs / n_assets:.1f} is small: the sample covariance is noisy; prefer a "
                     "shrinkage or denoised estimator.")
    return notes


def _load(tickers: list[str], start: Any, end: Any, market: MarketData,
          extra: list[str] | None = None, min_obs: int = 60) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    all_t = list(dict.fromkeys([*tickers, *(extra or [])]))
    ds = market.returns(all_t, start, end)
    full = ds.data.dropna(how="any")
    need = max(min_obs, len(tickers) + 3)
    if len(full) < need:
        raise DataUnavailable(f"only {len(full)} common return observations for {', '.join(all_t)} "
                              f"(need >= {need})")
    return full[tickers], full, ds.provenance_dicts()


def _risk_free(market: MarketData, index: pd.DatetimeIndex, override: float | None,
               required: bool) -> tuple[pd.Series | None, float | None, dict[str, Any], list[dict], list[str]]:
    """Daily rf aligned to ``index`` (decimal), its annualized window mean, a description,
    provenance and notes. ``required=False`` returns ``None`` when unavailable."""
    if override is not None:
        # every model annualizes daily means arithmetically (mean x 252), so the caller's
        # annual rate becomes the daily rate r/252: the daily series (CAPM/BL excess returns,
        # walk-forward cash) and the scalar used in Sharpe ratios / the CML are then ONE rate
        daily = override / TRADING_DAYS
        s = pd.Series(daily, index=index)
        return s, float(override), {"source": "user", "annual": override, "annual_arithmetic": float(override),
                                    "daily": daily}, [], [
            f"Risk-free rate {override:.2%} p.a. supplied by the caller (not market data); applied as "
            f"{override:.2%}/252 per session."]
    try:
        # start a few sessions early so a bond-market holiday on day one (e.g. Columbus Day,
        # when equities trade) is forward-filled from the prior print rather than left empty
        ds = market.risk_free_daily((index[0] - pd.Timedelta(days=10)).date(), index[-1].date())
    except DataUnavailable as e:
        if required:
            raise DataUnavailable(f"risk-free rate unavailable ({rf_error_reason(e)}); supply risk_free_rate explicitly") from e
        return None, None, {"source": None, "error": str(e)}, [], [
            f"Risk-free rate unavailable ({rf_error_reason(e)}); Sharpe ratios are not reported."]
    # carry the last print across at most RF_FFILL_SESSIONS rows (holidays, publication lag);
    # a longer gap is missing data, never a guess (hard rule 1)
    rf = ds.data.reindex(ds.data.index.union(index)).ffill(limit=RF_FFILL_SESSIONS).reindex(index)
    if rf.isna().any():
        gaps = rf.index[rf.isna()]
        missing = len(gaps)
        if required:
            raise DataUnavailable(f"risk-free series does not cover {missing} sessions of the window "
                                  f"({gaps[0].date()}..{gaps[-1].date()}); supply risk_free_rate explicitly")
        return None, None, {"source": None}, ds.provenance_dicts(), [
            "Risk-free series does not cover the window; Sharpe ratios are not reported."]
    ann = float(rf.mean() * TRADING_DAYS)
    return rf.astype(float), ann, {"source": "market", "annual": ann, "annual_arithmetic": ann,
                                   "series": rf_series_used(ds.provenance_dicts()) or "DGS3MO (or French RF)"}, \
        ds.provenance_dicts(), []


def _market_weights(tickers: list[str], market: MarketData, prior: dict[str, float] | None
                    ) -> tuple[pd.Series, str, list[dict], list[str]]:
    """Black-Litterman prior weights: SEC shares outstanding x latest price when every
    ticker has them; else caller's prior weights; else equal weights (uninformative)."""
    caps: dict[str, float] = {}
    prov: list[dict] = []
    missing: list[str] = []
    for t in tickers:
        try:
            facts = market.company_facts(t)
            so = facts.data.get("shares_outstanding")
            if not so:
                missing.append(t)
                continue
            q = market.quotes([t])
            caps[t] = float(so) * float(q.data.loc[t, "price"])
            prov += facts.provenance_dicts() + q.provenance_dicts()
        except (DataUnavailable, KeyError, TypeError, ValueError):
            missing.append(t)
    if not missing:
        w = pd.Series(caps)
        return w / w.sum(), "market capitalisation (SEC shares outstanding x latest price)", prov, []
    notes = [f"No SEC shares-outstanding/price for {', '.join(missing)} (e.g. ETFs); "
             "market-cap prior weights unavailable."]
    if prior:
        miss = [t for t in tickers if t not in prior]
        if miss:
            raise ValueError(f"prior_weights missing for {', '.join(miss)}")
        w = pd.Series({t: prior[t] for t in tickers}, dtype=float)
        if (w < 0).any() or w.sum() <= 0:
            raise ValueError("prior_weights must be non-negative and not all zero")
        return w / w.sum(), "caller-supplied prior weights", [], notes
    notes.append("Prior weights are EQUAL (1/N): an uninformative prior, not a market equilibrium; "
                 "supply prior_weights for a meaningful Black-Litterman prior.")
    return pd.Series(1.0 / len(tickers), index=tickers), "equal weights (uninformative)", [], notes


def _estimate(body: EstimationIn, market: MarketData, need_rf: bool) -> dict[str, Any]:
    """Load data and build (Sigma, mu, rf) per the request."""
    tickers = body.tickers
    rm = body.returns_model
    extra = [body.benchmark] if rm in ("capm", "black_litterman") else []
    rets, full, prov = _load(tickers, body.start, body.end, market, extra)
    notes = _data_notes(prov, len(rets), len(tickers))
    need_rf = need_rf or rm in ("capm", "black_litterman")
    rf_s, rf_ann, rf_info, rf_prov, rf_notes = _risk_free(market, rets.index, body.risk_free_rate, need_rf)
    prov += rf_prov
    notes += rf_notes
    cov_est = cv.estimate_covariance(rets, body.cov_method, ewma_lambda=body.ewma_lambda)
    notes += cov_est.notes
    sigma = cov_est.cov
    views = [er_mod.View(v.long, v.value, v.confidence, v.short) for v in body.views]
    if views and rm != "black_litterman":
        raise ValueError("views are only used by returns_model='black_litterman'")
    for v in views:
        for t in (v.long, v.short):
            if t and t not in tickers:
                raise ValueError(f"view asset {t} is not in the universe")
    if rm == "historical":
        er = er_mod.historical_mean(rets)
    elif rm == "james_stein":
        er = er_mod.james_stein(rets)
    elif rm == "capm":
        er = er_mod.capm_equilibrium(rets, full[body.benchmark], rf_s)  # type: ignore[arg-type]
    else:
        bench = full[body.benchmark]
        if body.prior_risk_aversion is not None:
            delta, delta_src = body.prior_risk_aversion, "caller-supplied"
        else:
            delta = er_mod.implied_risk_aversion(bench, rf_s)  # type: ignore[arg-type]
            delta_src = f"(E[r_{body.benchmark}] - rf) / var over the window"
            if not math.isfinite(delta) or delta <= 0:
                raise ValueError(f"implied risk aversion from {body.benchmark} is {delta:.2f} (<= 0: the "
                                 "benchmark did not beat the risk-free rate over the window); choose another "
                                 "window or supply prior_risk_aversion")
        w_mkt, src, wprov, wnotes = _market_weights(tickers, market, body.prior_weights)
        prov += wprov
        notes += wnotes
        er = er_mod.black_litterman(sigma, float(delta), w_mkt, views, body.tau, float(rf_ann),  # type: ignore[arg-type]
                                    prior_source=src)
        er.params["delta_source"] = delta_src
        notes.append("Optimizers use the Black-Litterman posterior covariance Sigma + M (He & Litterman 1999).")
    notes += er.notes
    opt_sigma = er.cov if er.cov is not None else sigma
    return {"rets": rets, "full": full, "prov": prov, "notes": notes, "rf_series": rf_s, "rf": rf_ann,
            "rf_info": rf_info, "cov_est": cov_est, "sigma": opt_sigma, "er": er}


def _expected_payload(er: er_mod.ExpectedReturns) -> dict[str, Any]:
    out: dict[str, Any] = {"model": er.method, "mu": er.mu.to_dict(), "params": er.params,
                           "reference": er.reference}
    for k, v in er.extra.items():
        out[k] = v.to_dict() if isinstance(v, pd.Series) else v
    return out


def _asset_table(rets: pd.DataFrame, sigma: pd.DataFrame, mu: pd.Series, rf: float | None) -> list[dict]:
    vol = np.sqrt(np.diag(sigma.to_numpy()))
    df = pd.DataFrame({"expected_return": mu.reindex(rets.columns).to_numpy(), "volatility": vol,
                       "historical_mean": rets.mean().to_numpy() * TRADING_DAYS},
                      index=pd.Index(rets.columns, name="ticker"))
    df["sharpe"] = (df["expected_return"] - rf) / df["volatility"] if rf is not None else np.nan
    return records(df)


def _universe_meta(rets: pd.DataFrame) -> dict[str, Any]:
    return {"tickers": list(rets.columns), "start": rets.index[0], "end": rets.index[-1],
            "observations": len(rets)}


def _echo_params(kw: dict[str, Any], methods: list[str]) -> dict[str, Any]:
    """Method parameters worth echoing: set ones only, and ``cvar_alpha`` only when a
    method that uses it (min-CVaR) is involved -- it always has a default."""
    out = {k: v for k, v in kw.items() if v is not None}
    if "min_cvar" not in methods:
        out.pop("cvar_alpha", None)
    return out


def _weekly_last(df: pd.DataFrame) -> pd.DataFrame:
    """Last observation of each calendar week (weeks ending Friday), stamped with
    that observation's own date (not the week end); the very first row is kept too
    so the series still starts on its first session."""
    if df.empty:
        return df
    wk = pd.DatetimeIndex(df.index).to_period("W-FRI")
    keep = ~wk.duplicated(keep="last")
    keep[0] = True
    return df[keep]


# ------------------------------------------------------------------ endpoints
@router.get("/methods")
def methods() -> dict[str, Any]:
    """Catalog of allocation methods, covariance estimators and expected-return models."""
    return clean({
        "methods": [m.to_dict() for m in METHODS.values()],
        "covariance_estimators": [{"name": k, "reference": v} for k, v in cv.REFERENCES.items()],
        "returns_models": [{"name": k, "reference": v} for k, v in er_mod.REFERENCES.items()],
        "walk_forward": {"reference": "DeMiguel, Garlappi & Uppal (2009), RFS 22(5)",
                         "tests": ["Ledoit & Wolf (2008) HAC Sharpe-difference test",
                                   "Jobson & Korkie (1981) / Memmel (2003)"]},
        "provenance": [],
    })


@router.post("/covariance")
def covariance(body: CovarianceIn, market: Market) -> dict[str, Any]:
    """Covariance/correlation under the chosen estimator, estimator comparison, HRP order and MP spectrum."""
    return _cached("covariance", body, lambda: _covariance(body, market))


def _covariance(body: CovarianceIn, market: MarketData) -> dict[str, Any]:
    rets, _, prov = _load(body.tickers, body.start, body.end, market)
    notes = _data_notes(prov, len(rets), len(body.tickers))
    est = cv.estimate_covariance(rets, body.estimator, body.ewma_lambda, body.mp_bandwidth)
    notes += est.notes
    corr = est.correlation()
    h = hrp_mod.cluster(est.cov, body.linkage)
    order = [h.labels[i] for i in h.order]
    comparison = []
    for name in cv.ESTIMATORS:
        e = est if name == body.estimator else cv.estimate_covariance(rets, name, body.ewma_lambda,
                                                                      body.mp_bandwidth)
        c = e.correlation().to_numpy()
        n = c.shape[0]
        comparison.append({"estimator": name, "shrinkage": e.shrinkage, "condition_number": e.condition_number(),
                           "average_correlation": float((c.sum() - n) / (n * (n - 1))),
                           "reference": e.reference})
    spec = cv.spectrum(rets, body.mp_bandwidth)
    sample_eig = spec["eigenvalues"]
    spec_out = {**spec, "mp_grid": spec["mp_grid"], "mp_density": spec["mp_density"],
                "estimator_eigenvalues": np.sort(np.linalg.eigvalsh(corr.to_numpy()))[::-1],
                "sample_eigenvalues": sample_eig}
    vols = pd.Series(np.sqrt(np.diag(est.cov.to_numpy())), index=est.cov.columns)
    return clean({
        "universe": _universe_meta(rets),
        "estimator": body.estimator, "shrinkage": est.shrinkage, "params": est.params,
        "covariance": frame(est.cov), "correlation": frame(corr),
        "volatility": vols.to_dict(),
        "hrp_order": order, "correlation_ordered": frame(corr.loc[order, order]),
        "dendrogram": h.dendrogram(),
        "spectrum": spec_out, "comparison": comparison,
        "method": {"estimator": body.estimator, "reference": est.reference, "annualization": 252,
                   "linkage": body.linkage, "distance": "sqrt(0.5 (1 - rho)), Lopez de Prado (2016)",
                   "spectrum": "Marcenko-Pastur fit to the sample correlation eigenvalues (Lopez de Prado 2020, ch. 2)"},
        "notes": notes, "provenance": prov,
    })


@router.post("/optimize")
def optimize(body: OptimizeIn, market: Market) -> dict[str, Any]:
    """Optimal weights under one method with constraints, expected-return model and views."""
    return _cached("optimize", body, lambda: _optimize(body, market))


def _optimize(body: OptimizeIn, market: MarketData) -> dict[str, Any]:
    spec = METHODS[body.method]
    e = _estimate(body, market, need_rf=spec.needs_rf)
    rets, sigma, er = e["rets"], e["sigma"], e["er"]
    cons = body.constraints.build(list(rets.columns))
    kw = body.kwargs()
    if body.method == "mean_variance" and all(kw[k] is None for k in ("target_return", "target_vol",
                                                                       "risk_aversion")):
        raise ValueError("mean_variance needs target_return, target_vol or risk_aversion")
    res = run_method(body.method, rets, sigma, er.mu, e["rf"], cons, **kw)
    notes = e["notes"] + res.notes
    alloc = pd.DataFrame({"weight": res.weights, "risk_contribution": res.risk_contributions,
                          "risk_contribution_pct": res.risk_contributions_pct,
                          "expected_return": er.mu.reindex(res.weights.index)},
                         index=pd.Index(res.weights.index, name="ticker"))
    rp = rets.to_numpy() @ res.weights.to_numpy()
    growth = pd.Series(np.cumprod(1 + rp), index=rets.index)
    notes.append("The 'in_sample_growth' path applies today's weights to the SAME data used to estimate "
                 "them: it is look-ahead by construction. Use /portfolio/compare for out-of-sample evidence.")
    return clean({
        "universe": _universe_meta(rets),
        "result": res.to_dict(), "allocation": records(alloc),
        "expected_returns": _expected_payload(er),
        "covariance": {"estimator": e["cov_est"].method, "shrinkage": e["cov_est"].shrinkage,
                       "condition_number": e["cov_est"].condition_number(), "reference": e["cov_est"].reference},
        "assets": _asset_table(rets, sigma, er.mu, e["rf"]),
        "risk_free": e["rf_info"],
        "in_sample_growth": series(growth),
        "method": {"name": body.method, "label": spec.label, "reference": spec.reference,
                   "cov_method": body.cov_method, "returns_model": body.returns_model,
                   "params": _echo_params(kw, [body.method]),
                   "constraints": body.constraints.model_dump()},
        "notes": notes, "provenance": e["prov"],
    })


@router.post("/evaluate")
def evaluate_weights(body: EvaluateIn, market: Market) -> dict[str, Any]:
    """Ex-ante return, volatility, Sharpe, Euler risk contributions, effective bets and
    diversification ratio of GIVEN weights under the chosen estimators (no optimization)."""
    return _cached("evaluate", body, lambda: _evaluate(body, market))


def _evaluate(body: EvaluateIn, market: MarketData) -> dict[str, Any]:
    e = _estimate(body, market, need_rf=False)
    rets, sigma, er = e["rets"], e["sigma"], e["er"]
    names = list(rets.columns)
    w = np.array([body.weights.get(t, 0.0) for t in names], dtype=float)
    res = opt_evaluate("given", w, sigma, er.mu, e["rf"],
                       reference="Euler risk decomposition; Meucci (2009) effective bets; "
                                 "Choueifaty & Coignard (2008) diversification ratio")
    notes = list(e["notes"])
    zero = [t for t in names if t not in body.weights]
    if zero:
        notes.append(f"no weight given for {', '.join(zero)}: held at 0 (still part of the estimation universe)")
    cons = body.constraints.build(names)
    satisfied = cons.satisfied(w)
    if not cons.is_default(len(names)) and not satisfied:
        notes.append("the given weights violate the stated constraints (they are evaluated anyway)")
    if res.sharpe is None:
        notes.append("Sharpe ratio not reported: risk-free rate unavailable")
    alloc = pd.DataFrame({"weight": res.weights, "risk_contribution": res.risk_contributions,
                          "risk_contribution_pct": res.risk_contributions_pct,
                          "expected_return": er.mu.reindex(res.weights.index)},
                         index=pd.Index(res.weights.index, name="ticker"))
    rp = rets.to_numpy() @ w
    growth = pd.Series(np.cumprod(1 + rp), index=rets.index)
    notes.append("Ex-ante figures use the estimated (mu, Sigma) over the window; 'in_sample_growth' is the "
                 "historical path of these fixed, daily-rebalanced weights over the same window.")
    return clean({
        "universe": _universe_meta(rets),
        "result": res.to_dict(), "allocation": records(alloc),
        "constraints_satisfied": satisfied,
        "expected_returns": _expected_payload(er),
        "covariance": {"estimator": e["cov_est"].method, "shrinkage": e["cov_est"].shrinkage,
                       "condition_number": e["cov_est"].condition_number(), "reference": e["cov_est"].reference},
        "assets": _asset_table(rets, sigma, er.mu, e["rf"]),
        "risk_free": e["rf_info"],
        "in_sample_growth": series(growth),
        "method": {"name": "evaluate", "cov_method": body.cov_method, "returns_model": body.returns_model,
                   "volatility": "sqrt(w' Sigma w)", "risk_contributions": "RC_i = w_i (Sigma w)_i / sigma_p",
                   "effective_bets": "exp(-sum p_k ln p_k), PCA variance shares (Meucci 2009)",
                   "diversification_ratio": "sum |w_i| sigma_i / sigma_p (Choueifaty & Coignard 2008)",
                   "reference": res.reference, "constraints": body.constraints.model_dump()},
        "notes": notes, "provenance": e["prov"],
    })


@router.post("/frontier")
def frontier(body: FrontierIn, market: Market) -> dict[str, Any]:
    """Constrained efficient frontier, tangency portfolio / CML, assets and other methods in mean-vol space."""
    return _cached("frontier", body, lambda: _frontier(body, market))


def _frontier(body: FrontierIn, market: MarketData) -> dict[str, Any]:
    e = _estimate(body, market, need_rf=False)
    rets, sigma, er, rf = e["rets"], e["sigma"], e["er"], e["rf"]
    cons = body.constraints.build(list(rets.columns))
    notes = e["notes"]
    fr = efficient_frontier(er.mu, sigma, cons, body.points, rf)
    if rf is not None and "tangency" not in fr:
        notes.append("No feasible portfolio beats the risk-free rate: no tangency portfolio / CML.")
    overlay = []
    for m in body.overlay_methods:
        try:
            r = run_method(m, rets, sigma, er.mu, rf, cons, with_diagnostics=False)
            overlay.append({"method": m, "label": METHODS[m].label, "ret": r.expected_return,
                            "vol": r.volatility, "sharpe": r.sharpe, "weights": r.weights.to_dict()})
        except ValueError as ex:
            overlay.append({"method": m, "label": METHODS[m].label, "error": str(ex)})
    s = sigma.to_numpy()
    assets = [{"ticker": t, "ret": float(er.mu[t]), "vol": float(np.sqrt(s[i, i]))}
              for i, t in enumerate(rets.columns)]
    notes.append("Frontier points solve min w'Sigma w s.t. mu'w >= R on an even grid of R from the GMV "
                 "return to the maximum feasible return, under the stated constraints.")
    return clean({
        "universe": _universe_meta(rets),
        "frontier": fr["points"], "gmv": fr["gmv"], "tangency": fr.get("tangency"), "cml": fr.get("cml"),
        "assets": assets, "overlay": overlay, "risk_free": e["rf_info"],
        "expected_returns": _expected_payload(er),
        "method": {"frontier": "Markowitz (1952) constrained mean-variance frontier",
                   "cml": "Sharpe (1964) capital market line through the tangency portfolio",
                   "cov_method": body.cov_method, "returns_model": body.returns_model,
                   "reference": "Markowitz (1952); Tobin (1958); Sharpe (1964)",
                   "constraints": body.constraints.model_dump()},
        "notes": notes, "provenance": e["prov"],
    })


@router.post("/compare")
def compare(body: CompareIn, market: Market) -> dict[str, Any]:
    """Walk-forward out-of-sample comparison with costs and Sharpe-difference tests."""
    return _cached("compare", body, lambda: _compare(body, market))


def _compare(body: CompareIn, market: MarketData) -> dict[str, Any]:
    rets, _, prov = _load(body.tickers, body.start, body.end, market, min_obs=body.window + 60)
    notes = _data_notes(prov, len(rets), len(body.tickers))
    rf_s, _, rf_info, rf_prov, rf_notes = _risk_free(market, rets.index, body.risk_free_rate, True)
    prov += rf_prov
    notes += rf_notes
    cons = Constraints(body.min_weight, body.max_weight, body.max_gross, body.max_turnover, None)
    cons.bounds(len(body.tickers))
    kw = _echo_params(body.kwargs(), [*body.methods, body.benchmark_method])
    res = wf.walk_forward(rets, list(body.methods), window=body.window, freq=body.rebalance,
                          cost_bps=body.cost_bps, rf_daily=rf_s, cov_method=body.cov_method,  # type: ignore[arg-type]
                          mu_method=body.mu_method, cons=cons, benchmark=body.benchmark_method,
                          method_kwargs=kw, ewma_lambda=body.ewma_lambda)
    notes += res.notes
    notes.append("Expected returns for mean-based methods are re-estimated each rebalance from the trailing "
                 f"window ({body.mu_method}); sample means are notoriously noisy (Merton 1980).")
    if body.max_turnover is not None:
        notes.append("Turnover limit applies from the second rebalance (the first is the initial purchase).")
    equity = pd.DataFrame({m: np.cumprod(1 + tr.returns.to_numpy()) for m, tr in res.tracks.items()},
                          index=res.rf_daily.index)
    dd = equity / equity.cummax() - 1   # on DAILY equity, so each weekly point is the exact drawdown that day
    n_daily = len(equity)
    equity, dd = _weekly_last(equity), _weekly_last(dd)
    notes.append(f"equity and drawdown series are downsampled to weekly (last session of each week, plus "
                 f"the first session; {len(equity)} of {n_daily} daily points) to keep the payload small; drawdowns are computed "
                 "on daily equity before sampling, and every statistic (incl. max drawdown) uses daily returns.")
    return clean({
        "universe": _universe_meta(rets),
        "stats": [{"method": m, "label": METHODS[m].label, **st} for m, st in res.stats.items()],
        "tests": res.tests,
        "equity": frame(equity), "drawdown": frame(dd),
        "weights": {m: frame(tr.weights) for m, tr in res.tracks.items()},
        "turnover": {m: series(tr.turnover) for m, tr in res.tracks.items()},
        "failures": {m: tr.failures for m, tr in res.tracks.items() if tr.failures},
        "rebalance_dates": res.rebalance_dates, "risk_free": rf_info,
        "method": {"design": "rolling-window out-of-sample (walk-forward)", **res.params,
                   "labels": {m: METHODS[m].label for m in res.tracks},
                   "reference": "DeMiguel, Garlappi & Uppal (2009), RFS 22(5); Ledoit & Wolf (2008), JEF 15(5); "
                                "Memmel (2003)"},
        "notes": notes, "provenance": prov,
    })
