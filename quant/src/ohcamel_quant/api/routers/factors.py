"""/api/factors -- factor models on real data.

* ``POST /factors/regression`` -- Fama-French / Carhart time-series regression
  of a portfolio or single ticker on Kenneth French's daily factors.
* ``POST /factors/custom``     -- the same analysis on *tradable* long/short
  ETF factors built from real prices (works when French's server is down).
* ``POST /factors/cross-section`` -- Fama-MacBeth premia (Newey-West, Shanken) and the GRS test of a set of
  test assets on tradable ETF factors, monthly, betas estimated before each month.
* ``GET  /factors/library``    -- the French factor dashboard.
* ``GET  /factors/models``     -- supported models and the default ETF preset.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from ...data.base import DataUnavailable
from ...data.market import MarketData, get_market
from ...factors import library as lib
from ...factors import models as fm
from ...factors import performance as perf
from ..models import Holding
from ..serialize import clean, frame, records
from .performance import (
    REBALANCE_NOTE,
    RebalanceT,
    cached,
    check_exposure,
    data_notes,
    portfolio_meta,
    rf_source,
    risk_free,
)

router = APIRouter(prefix="/factors", tags=["factors"])
Market = Annotated[MarketData, Depends(get_market)]

# Default tradable ETF factor set (a modelling choice, not market data). Each is a
# zero-investment long/short spread of real ETF returns; the market factor is the
# S&P 500 ETF's excess return over the T-bill.
ETF_PRESET: list[dict[str, Any]] = [
    {"name": "MKT", "long": "SPY", "short": None, "label": "US equity market (SPY) excess return"},
    {"name": "SIZE", "long": "IWM", "short": "SPY", "label": "Small caps (Russell 2000) minus large caps"},
    {"name": "VALUE", "long": "IWD", "short": "IWF", "label": "Russell 1000 Value minus Russell 1000 Growth"},
    {"name": "MOM", "long": "MTUM", "short": "SPY", "label": "MSCI USA Momentum minus market"},
    {"name": "QUALITY", "long": "QUAL", "short": "SPY", "label": "MSCI USA Quality minus market"},
    {"name": "TERM", "long": "TLT", "short": "IEF", "label": "Long (20y+) minus intermediate (7-10y) Treasuries"},
    {"name": "CREDIT", "long": "HYG", "short": "IEF", "label": "High-yield corporates minus Treasuries"},
]


# ------------------------------------------------------------------ requests
class TargetIn(BaseModel):
    """Either a single ``ticker`` or a weighted ``holdings`` portfolio."""

    ticker: str | None = Field(default=None, max_length=12)
    holdings: list[Holding] | None = Field(default=None, max_length=100)
    start: date | None = None
    end: date | None = None
    rebalance: RebalanceT = "monthly"
    rolling_window: int = Field(default=252, ge=60, le=1260)
    nw_lags: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _one_target(self) -> TargetIn:
        if bool(self.ticker and self.ticker.strip()) == bool(self.holdings):
            raise ValueError("pass exactly one of 'ticker' or 'holdings'")
        if self.ticker:
            self.ticker = self.ticker.strip().upper()
        check_exposure(self.weights)
        return self

    @property
    def weights(self) -> dict[str, float]:
        if self.ticker:
            return {self.ticker: 1.0}
        out: dict[str, float] = {}
        for h in self.holdings or []:
            out[h.ticker] = out.get(h.ticker, 0.0) + h.weight
        return out


class RegressionIn(TargetIn):
    model: Literal["capm", "ff3", "carhart4", "ff5", "ff5mom"] = "ff5"


class FactorDef(BaseModel):
    name: str = Field(min_length=1, max_length=24)
    long: str = Field(min_length=1, max_length=12)
    short: str | None = Field(default=None, max_length=12)

    @field_validator("long", "short")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if v and v.strip() else None


class CustomIn(TargetIn):
    factors: list[FactorDef] | None = Field(default=None, min_length=1, max_length=10)


# ------------------------------------------------------------------ helpers
def _returns_tolerant(market: MarketData, tickers: list[str], start: date | None, end: date | None
                      ) -> tuple[pd.DataFrame, list[dict], dict[str, str]]:
    """Daily adj_close returns for every ticker that can be served (others
    reported in ``failed``), inner-joined on common sessions."""
    px: dict[str, pd.Series] = {}
    prov: list[dict] = []
    failed: dict[str, str] = {}
    for t in dict.fromkeys(tickers):
        try:
            ds = market.ohlcv(t, start, end)
        except DataUnavailable as e:
            failed[t] = str(e)
            continue
        px[t] = ds.data["adj_close"]
        prov += ds.provenance_dicts()
    if not px:
        return pd.DataFrame(), prov, failed
    wide = pd.DataFrame(px).dropna(how="any").sort_index()
    rets = wide.pct_change().iloc[1:]
    rets.index.name = "date"
    return rets, prov, failed


def _regression_payload(y: pd.Series, x: pd.DataFrame, body: TargetIn) -> dict[str, Any]:
    reg = fm.factor_regression(y, x, lags=body.nw_lags)
    table = reg.table()
    table["estimate_ann"] = table["estimate"]
    table.loc["alpha", "estimate_ann"] = reg.alpha_annualized
    resid_vol_ann = float(np.sqrt(reg.resid_var * fm.TRADING_DAYS))
    rd = fm.risk_decomposition(reg)
    attr = fm.return_attribution(reg)
    rolling = None
    if reg.n_obs >= body.rolling_window:
        rolling = fm.rolling_regression(reg.y, reg.x, body.rolling_window)
    fitted_vs_actual = pd.DataFrame({
        "actual": np.cumprod(1.0 + reg.y.to_numpy()) - 1.0,
        "fitted_no_alpha": np.cumprod(1.0 + (reg.fitted - reg.alpha).to_numpy()) - 1.0,
    }, index=reg.y.index)
    return {
        "loadings": records(table.reset_index()),
        "stats": {
            "alpha_daily": reg.alpha, "alpha_ann": reg.alpha_annualized, "alpha_t": reg.alpha_t,
            "alpha_p": float(reg.p_values["alpha"]), "r2": reg.r2, "adj_r2": reg.adj_r2,
            "n_obs": reg.n_obs, "nw_lags": reg.nw_lags, "residual_vol_ann": resid_vol_ann,
            "appraisal_ratio": reg.alpha_annualized / resid_vol_ann if resid_vol_ann > 0 else None,
            "start": reg.y.index[0], "end": reg.y.index[-1],
            "excess_return_ann": float(reg.y.mean()) * fm.TRADING_DAYS,
        },
        "risk_decomposition": {
            **{k: v for k, v in rd.items() if k not in ("table", "factor_correlation")},
            "table": records(rd["table"]),
            "factor_correlation": {"factors": list(rd["factor_correlation"].columns),
                                   "matrix": rd["factor_correlation"].to_numpy().tolist()},
        },
        "attribution": {
            "cumulative_linked": frame(attr["cumulative_linked"]),
            "cumulative_arithmetic": frame(attr["cumulative_arithmetic"]),
            "totals_linked": attr["cumulative_linked"].iloc[-1].to_dict(),
            "totals_arithmetic": attr["cumulative_arithmetic"].iloc[-1].to_dict(),
        },
        "rolling": frame(rolling) if rolling is not None else None,
        "rolling_window": body.rolling_window,
        "fitted_vs_actual": frame(fitted_vs_actual),
    }


def _regression_notes(n_obs: int, body: TargetIn) -> list[str]:
    notes = []
    if n_obs < 252:
        notes.append(f"only {n_obs} observations (< 1 year): loadings and alpha are imprecise")
    if n_obs < body.rolling_window:
        notes.append(f"fewer observations than the {body.rolling_window}-day rolling window; rolling betas omitted")
    notes.append("Attribution: 'cumulative_linked' uses Carino (1999) log-linking so factor, alpha and "
                 "residual contributions sum exactly to the compounded excess return; "
                 "'cumulative_arithmetic' sums daily contributions.")
    return notes


# ------------------------------------------------------------------ endpoints
@router.get("/models")
def models() -> dict[str, Any]:
    """Supported Fama-French/Carhart models and the default tradable ETF factor preset."""
    return clean({
        "models": [{"key": s.key, "label": s.label, "factors": list(s.factors), "reference": s.reference}
                   for s in fm.FACTOR_MODELS.values()],
        "factor_descriptions": fm.FACTOR_DESCRIPTIONS,
        "etf_preset": ETF_PRESET,
        "provenance": [],
    })


@router.post("/regression")
def regression(body: RegressionIn, market: Market) -> dict[str, Any]:
    """Time-series factor regression on Kenneth French daily factors with
    Newey-West t-stats, rolling betas, risk decomposition and attribution."""
    return cached("factors.regression", body, lambda: _regression(body, market), ttl_s=1800.0)


def _regression(body: RegressionIn, market: MarketData) -> dict[str, Any]:
    spec = fm.model_spec(body.model)
    ff = market.ff_factors(spec.french_model, spec.momentum)
    fdf = ff.data
    weights = body.weights
    ds = market.returns(list(weights), body.start, body.end)
    rets = ds.data.dropna(how="any")
    prov = ds.provenance_dicts() + ff.provenance_dicts()
    common = rets.index.intersection(fdf.index)
    if len(common) < 60:
        raise DataUnavailable(
            f"only {len(common)} sessions where both prices and French factors exist "
            f"(prices {rets.index[0].date()}..{rets.index[-1].date()}, "
            f"factors {fdf.index[0].date()}..{fdf.index[-1].date()})")
    rets = rets.loc[common]
    rf = fdf["RF"].loc[common]
    rp = perf.portfolio_returns(rets, weights, body.rebalance, rf)
    y = (rp - rf).rename("excess")
    x = fdf.loc[common, list(spec.factors)]
    out = _regression_payload(y, x, body)
    notes = data_notes(prov)
    if rets.index[-1] < ds.data.index[-1]:
        notes.append(f"Kenneth French factors end {fdf.index[-1].date()} (published with a lag); "
                     f"price data after that date is not used.")
    notes.append(REBALANCE_NOTE[body.rebalance] if body.ticker is None else
                 f"Single asset {body.ticker}.")
    notes.append("Excess returns use Ken French's RF (1-month T-bill), consistent with Mkt-RF.")
    notes += _regression_notes(out["stats"]["n_obs"], body)
    return clean({
        "target": {**portfolio_meta(weights, body.rebalance), "ticker": body.ticker},
        "model": {"key": spec.key, "label": spec.label, "factors": list(spec.factors)},
        **out,
        "method": {
            "model": spec.label, "reference": spec.reference,
            "estimator": "OLS; Newey & West (1987) HAC standard errors, Bartlett kernel",
            "nw_lags": out["stats"]["nw_lags"],
            "lag_rule": "floor(4 (T/100)^(2/9)) unless overridden",
            "risk_decomposition": "Var(y) = b' Sigma_f b + Var(e); Euler contributions b_k (Sigma_f b)_k",
            "attribution": "b_k f_k,t + alpha + e_t; Carino (1999) linking",
            "factor_source": "Kenneth R. French Data Library (daily)",
        },
        "notes": notes,
        "provenance": prov,
    })


@router.post("/custom")
def custom(body: CustomIn, market: Market) -> dict[str, Any]:
    """Factor regression on tradable long/short ETF factors built from real
    prices (default: the ETF preset, dropping any factor whose ETFs cannot be
    served)."""
    return cached("factors.custom", body, lambda: _custom(body, market), ttl_s=1800.0)


def _custom(body: CustomIn, market: MarketData) -> dict[str, Any]:
    weights = body.weights
    user_defs = body.factors is not None
    defs = [fm.LongShortFactor(d.name, d.long, d.short) for d in body.factors] if user_defs else [
        fm.LongShortFactor(d["name"], d["long"], d["short"]) for d in ETF_PRESET]
    factor_tickers = [t for d in defs for t in d.tickers]
    rets, prov, failed = _returns_tolerant(market, [*weights, *factor_tickers], body.start, body.end)
    notes: list[str] = []
    missing_target = [t for t in weights if t in failed]
    if missing_target:
        raise DataUnavailable("; ".join(failed[t] for t in missing_target))
    dropped_out: list[dict[str, Any]] = []
    if failed:
        dropped = [d for d in defs if any(t in failed for t in d.tickers)]
        defs = [d for d in defs if d not in dropped]
        dropped_out = [{"name": d.name, "long": d.long, "short": d.short,
                        "reason": "; ".join(failed[t] for t in d.tickers if t in failed)} for d in dropped]
        kind = "Requested" if user_defs else "Preset"
        notes.append(f"{kind} factors dropped because their ETF history is unavailable here: "
                     + ", ".join(f"{d.name} ({'/'.join(d.tickers)})" for d in dropped))
        if not defs:
            raise DataUnavailable(f"no {kind.lower()} ETF factor could be built: " + "; ".join(failed.values()))
        used = set(weights) | {t for d in defs for t in d.tickers}
        # Re-join on the tickers actually used so dropped ETFs don't shorten the sample.
        rets, prov, _ = _returns_tolerant(market, list(used), body.start, body.end)
    rets = rets.dropna(how="any")
    if len(rets) < 60:
        raise DataUnavailable(f"only {len(rets)} common return observations")
    rf, rf_prov, rf_notes = risk_free(market, rets.index)
    prov += rf_prov
    notes += rf_notes
    if rf is not None:
        keep = rf.notna()
        rets, rf = rets[keep], rf[keep]
    x = fm.long_short_factors(rets, defs, rf)
    rp = perf.portfolio_returns(rets, weights, body.rebalance, rf)
    y = (rp - rf if rf is not None else rp).rename("excess")
    out = _regression_payload(y, x, body)
    notes = data_notes(prov) + notes
    notes.append(REBALANCE_NOTE[body.rebalance] if body.ticker is None else f"Single asset {body.ticker}.")
    notes.append("Tradable ETF factors are long/short return spreads of real ETFs; they proxy but do not "
                 "equal the academic Fama-French factors (different universes, weighting and fees).")
    overlap = sorted(set(weights) & {t for d in defs for t in d.tickers})
    if overlap:
        notes.append(f"{', '.join(overlap)} is both a holding and a factor leg: R^2 is mechanically inflated.")
    notes += _regression_notes(out["stats"]["n_obs"], body)
    return clean({
        "target": {**portfolio_meta(weights, body.rebalance), "ticker": body.ticker},
        "model": {"key": "custom", "label": "Tradable ETF factors",
                  "factors": [{"name": d.name, "long": d.long, "short": d.short} for d in defs]},
        "dropped": dropped_out,
        **out,
        "method": {
            "model": "Custom long/short tradable factors: f = r_long - r_short (or r_long - rf)",
            "reference": "Huij & Verbeek (2009), JFQA 44; Newey & West (1987)",
            "estimator": "OLS; Newey & West (1987) HAC standard errors, Bartlett kernel",
            "nw_lags": out["stats"]["nw_lags"],
            "risk_free": rf_source(rf, rf_prov),
            "attribution": "b_k f_k,t + alpha + e_t; Carino (1999) linking",
        },
        "notes": notes,
        "provenance": prov,
    })


# ------------------------------------------------------------------ cross-section
SECTION_ASSETS = ["XLE", "XLF", "XLK", "QQQ", "GLD", "IEF"]
SECTION_FACTORS = [{"name": "MKT", "long": "SPY", "short": None}, {"name": "SIZE", "long": "IWM", "short": "SPY"}]


class CrossSectionIn(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: list(SECTION_ASSETS), min_length=4, max_length=30)
    factors: list[FactorDef] | None = Field(default=None, min_length=1, max_length=4)
    start: date | None = None
    end: date | None = None
    beta_window: int = Field(default=36, ge=12, le=120)
    nw_lags: int | None = Field(default=None, ge=0, le=24)

    @field_validator("tickers")
    @classmethod
    def _tickers(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(t.strip().upper() for t in v if t.strip()))


def _monthly(daily: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Compounded calendar-month returns; a trailing month that has not finished is dropped."""
    m = (1.0 + daily).groupby(daily.index.to_period("M")).prod() - 1.0
    last = daily.index[-1]
    if (last + pd.offsets.BDay(1)).month == last.month:
        m = m.iloc[:-1]
    m.index = m.index.to_timestamp("M")
    return m


def _prior_betas(excess: pd.DataFrame, factors: pd.DataFrame, window: int) -> dict[str, pd.DataFrame]:
    """Row ``t`` = OLS betas on the ``window`` months BEFORE month ``t`` (month ``t`` itself is never used)."""
    n_m, n_a, k = len(excess), excess.shape[1], factors.shape[1]
    out = np.full((n_m, n_a, k), np.nan)
    y, f = excess.to_numpy(float), factors.to_numpy(float)
    for t in range(window, n_m):
        x = np.column_stack([np.ones(window), f[t - window:t]])
        out[t] = np.linalg.lstsq(x, y[t - window:t], rcond=None)[0][1:].T
    return {n: pd.DataFrame(out[:, :, i], index=excess.index, columns=excess.columns) for i, n in enumerate(factors.columns)}


@router.post("/cross-section")
def cross_section(body: CrossSectionIn, market: Market) -> dict[str, Any]:
    """Fama-MacBeth premia and the GRS test for ETF test assets on tradable ETF factors."""
    return cached("factors.cross_section", body, lambda: _cross_section(body, market), ttl_s=1800.0)


def _cross_section(body: CrossSectionIn, market: MarketData) -> dict[str, Any]:
    defs = [fm.LongShortFactor(d.name, d.long, d.short) for d in body.factors] if body.factors else [
        fm.LongShortFactor(d["name"], d["long"], d["short"]) for d in SECTION_FACTORS]
    legs = [t for d in defs for t in d.tickers]
    clash = sorted(set(body.tickers) & set(legs))
    if clash:
        raise ValueError(f"{', '.join(clash)} is both a test asset and a factor leg; the factors would explain it by construction")
    rets, prov, failed = _returns_tolerant(market, [*body.tickers, *legs], body.start, body.end)
    if failed:
        raise DataUnavailable("; ".join(failed.values()))
    rets = rets.dropna(how="any")
    if len(rets) < 60:
        raise DataUnavailable(f"only {len(rets)} common return observations")
    rf, rf_prov, rf_notes = risk_free(market, rets.index)
    prov += rf_prov
    m_rets = _monthly(rets)
    m_rf = _monthly(rf.fillna(0.0)) if rf is not None else None
    factors = fm.long_short_factors(m_rets, defs, m_rf)
    excess = m_rets[body.tickers] - (m_rf.reindex(m_rets.index).to_numpy()[:, None] if m_rf is not None else 0.0)
    need = body.beta_window + 24
    if len(excess) < need:
        raise DataUnavailable(f"only {len(excess)} complete months; a {body.beta_window}-month beta window needs {need}")
    exposures = _prior_betas(excess, factors, body.beta_window)
    res = fm.fama_macbeth(excess, exposures, nw_lags=body.nw_lags, factors=factors)
    g = fm.grs_test(excess, factors)
    notes = data_notes(prov) + rf_notes
    notes.append(f"A cross-section of {len(body.tickers)} assets: the premia are imprecise and the GRS test has "
                 f"{g.n_assets} assets against {g.n_obs} months; read them as a worked example, not as prices of risk.")
    notes.append(f"Betas for month t come from the {body.beta_window} months before t (nothing from month t), so each "
                 "cross-section uses only what was known at the start of the month.")
    notes.append("Premia are per month, per unit of beta, on excess returns; the Shanken correction multiplies the "
                 "slope variances by c = 1 + lambda' Sigma_f^-1 lambda with Sigma_f the sample covariance of the factors.")
    notes.append("Tradable ETF factors proxy but do not equal the academic Fama-French factors.")
    tab = res.table().reset_index().rename(columns={"index": "term"})
    return clean({
        "model": {"factors": [{"name": d.name, "long": d.long, "short": d.short} for d in defs],
                  "tickers": body.tickers},
        "premia": records(tab),
        "grs": {"statistic": g.statistic, "p_value": g.p_value, "n_assets": g.n_assets, "n_factors": g.n_factors,
                "n_obs": g.n_obs, "mean_abs_alpha_monthly": g.mean_abs_alpha,
                "alphas": [{"ticker": k, "alpha_monthly": float(v)} for k, v in g.alphas.items()]},
        "stats": {"start": res.lambdas.index[0], "end": res.lambdas.index[-1], "n_periods": res.n_periods,
                  "nw_lags": res.nw_lags, "shanken_c": res.shanken_c, "beta_window": body.beta_window,
                  "mean_assets": res.mean_assets, "mean_r2": res.mean_r2,
                  "grs_start": excess.index[0], "grs_end": excess.index[-1]},
        "method": {
            "premia": "Fama & MacBeth (1973): monthly cross-sectional OLS of excess returns on prior-window betas "
                      "(with intercept), time-series mean; Newey & West (1987) standard errors, lag floor(4 (T/100)^(2/9)) "
                      "unless overridden",
            "shanken": "Shanken (1992) correction c = 1 + lambda' Sigma_f^-1 lambda applied to the slope variances",
            "grs": "Gibbons, Ross & Shanken (1989): F(N, T-N-L), MLE covariances, full-sample alphas",
            "nw_lags": res.nw_lags,
        },
        "notes": notes,
        "provenance": prov,
    })


@router.get("/library")
def library(
    market: Market,
    model: Literal["ff3", "ff5"] = "ff5",
    momentum: bool = True,
    window: Annotated[str, Query(description="1M|3M|6M|YTD|1Y|3Y|5Y|10Y|20Y|MAX")] = "5Y",
    corr_window: Annotated[int, Query(ge=21, le=1260)] = 252,
) -> dict[str, Any]:
    """Kenneth French factor dashboard: cumulative returns and drawdowns over a
    selectable window, trailing returns/Sharpe, rolling correlations."""
    if window.upper() not in lib.WINDOWS:
        raise ValueError(f"unknown window {window!r}; choose one of {', '.join(lib.WINDOWS)}")
    key = {"model": model, "momentum": momentum, "window": window.upper(), "corr_window": corr_window}
    return cached("factors.library", key, lambda: _library(market, model, momentum, window, corr_window),
                  ttl_s=6 * 3600.0)


def _library(market: MarketData, model: str, momentum: bool, window: str, corr_window: int) -> dict[str, Any]:
    ds = market.ff_factors(model, momentum)
    out = lib.factor_library(ds.data, window, corr_window)
    trailing = out["trailing"]
    return clean({
        "factors": [{"name": c, "description": fm.FACTOR_DESCRIPTIONS.get(c, c)} for c in out["factors"]],
        "window": out["window"], "start": out["start"], "end": out["end"],
        "first_date": out["first_date"], "last_date": out["last_date"],
        "cumulative": frame(out["cumulative"]),
        "drawdowns": frame(out["drawdowns"]),
        "max_drawdown_window": out["max_drawdown_window"].to_dict(),
        "trailing": records(trailing),
        "rolling_corr": frame(out["rolling_corr"]),
        "corr_window": corr_window,
        "corr_window_matrix": {"factors": out["factors"], "matrix": out["corr_window_matrix"].to_numpy().tolist()},
        "corr_full_matrix": {"factors": out["factors"], "matrix": out["corr_full_matrix"].to_numpy().tolist()},
        "full_sample": out["full_sample"],
        "method": {
            "source": "Kenneth R. French Data Library, daily research factors",
            "sharpe": "mean / std * sqrt(252) (factors are zero-investment/excess returns)",
            "t_stat": "mean premium / Newey-West (1987) standard error",
            "annualized": "geometric for horizons >= 1Y",
            "references": ["Fama & French (1993) JFE 33", "Carhart (1997) JF 52", "Fama & French (2015) JFE 116"],
        },
        "notes": [
            "Factor returns are gross of trading costs; they are academic long/short portfolios, not investable funds.",
            "Series are thinned to at most 1500 points for charts; cumulative values are exact at plotted dates.",
            "Sharpe ratios over 1M-3M horizons are very noisy.",
        ],
        "provenance": ds.provenance_dicts(),
    })
