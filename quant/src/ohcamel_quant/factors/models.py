"""Time-series factor models: estimation, rolling betas, risk decomposition, attribution.

The time-series regression of an asset's (or portfolio's) excess return on
factor returns,

    y_t = r_t - rf_t = a + sum_k b_k f_{k,t} + e_t,

is estimated by OLS with Newey & West (1987) HAC standard errors (Bartlett
kernel, default lag ``floor(4 (T/100)^(2/9))``). Supported factor sets, taken
from Kenneth French's data library (``MarketData.ff_factors``):

* ``capm``     -- Mkt-RF (Sharpe 1964; Lintner 1965)
* ``ff3``      -- Mkt-RF, SMB, HML (Fama & French 1993, "Common risk factors in
  the returns on stocks and bonds", *JFE* 33)
* ``carhart4`` -- ff3 + Mom (Carhart 1997, "On Persistence in Mutual Fund
  Performance", *JF* 52)
* ``ff5``      -- Mkt-RF, SMB, HML, RMW, CMA (Fama & French 2015, "A five-factor
  asset pricing model", *JFE* 116)
* ``ff5mom``   -- ff5 + Mom

and *custom tradable* factors built from real ETF returns as long/short pairs
(:func:`long_short_factors`), so a factor analysis still runs when French's
server is unreachable.

Everything here is pure (numpy / pandas / statsmodels); no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.regression.rolling import RollingOLS

from .hac import nw_lag_rule

TRADING_DAYS = 252


# --------------------------------------------------------------------- models
@dataclass(frozen=True)
class FactorModelSpec:
    key: str
    label: str
    factors: tuple[str, ...]
    french_model: str      # argument to MarketData.ff_factors
    momentum: bool
    reference: str


FACTOR_MODELS: dict[str, FactorModelSpec] = {
    "capm": FactorModelSpec(
        "capm", "CAPM", ("Mkt-RF",), "ff3", False,
        "Sharpe (1964), 'Capital Asset Prices', JF 19; Lintner (1965), REStat 47"),
    "ff3": FactorModelSpec(
        "ff3", "Fama-French 3-factor", ("Mkt-RF", "SMB", "HML"), "ff3", False,
        "Fama & French (1993), 'Common risk factors in the returns on stocks and bonds', JFE 33"),
    "carhart4": FactorModelSpec(
        "carhart4", "Carhart 4-factor", ("Mkt-RF", "SMB", "HML", "Mom"), "ff3", True,
        "Carhart (1997), 'On Persistence in Mutual Fund Performance', JF 52"),
    "ff5": FactorModelSpec(
        "ff5", "Fama-French 5-factor", ("Mkt-RF", "SMB", "HML", "RMW", "CMA"), "ff5", False,
        "Fama & French (2015), 'A five-factor asset pricing model', JFE 116"),
    "ff5mom": FactorModelSpec(
        "ff5mom", "Fama-French 5-factor + momentum", ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"),
        "ff5", True,
        "Fama & French (2015), JFE 116; Carhart (1997), JF 52; Fama & French (2018), 'Choosing "
        "factors', JFE 128"),
}

FACTOR_DESCRIPTIONS: dict[str, str] = {
    "Mkt-RF": "Value-weighted US market (CRSP) minus 1-month T-bill",
    "SMB": "Small minus big (size)",
    "HML": "High minus low book-to-market (value)",
    "RMW": "Robust minus weak operating profitability",
    "CMA": "Conservative minus aggressive investment",
    "Mom": "Winners minus losers, 12-2 month momentum",
}


def model_spec(model: str) -> FactorModelSpec:
    key = model.lower().strip().replace("-", "").replace("_", "").replace("+", "")
    aliases = {"carhart": "carhart4", "ff4": "carhart4", "ff6": "ff5mom", "ff5m": "ff5mom"}
    key = aliases.get(key, key)
    if key not in FACTOR_MODELS:
        raise ValueError(f"unknown factor model {model!r}; choose one of {', '.join(FACTOR_MODELS)}")
    return FACTOR_MODELS[key]


# ---------------------------------------------------------------- regression
@dataclass
class FactorRegression:
    """Result of :func:`factor_regression` (daily units unless stated)."""

    factors: list[str]
    params: pd.Series          # 'alpha' + factor loadings
    std_errors: pd.Series      # Newey-West HAC
    t_stats: pd.Series
    p_values: pd.Series
    conf_int: pd.DataFrame     # 95% (normal) interval, columns lower/upper
    r2: float
    adj_r2: float
    n_obs: int
    nw_lags: int
    resid: pd.Series
    fitted: pd.Series
    y: pd.Series
    x: pd.DataFrame
    periods_per_year: int = TRADING_DAYS
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def alpha(self) -> float:
        return float(self.params["alpha"])

    @property
    def betas(self) -> pd.Series:
        return self.params[self.factors]

    @property
    def alpha_annualized(self) -> float:
        """Arithmetic annualisation ``a * P`` (P periods per year)."""
        return self.alpha * self.periods_per_year

    @property
    def alpha_t(self) -> float:
        return float(self.t_stats["alpha"])

    @property
    def resid_var(self) -> float:
        """Sample variance of the residuals (ddof = 1)."""
        return float(self.resid.var(ddof=1))

    def table(self) -> pd.DataFrame:
        """Loadings table: estimate, NW s.e., t, p, 95% CI (alpha also annualised)."""
        df = pd.DataFrame({
            "estimate": self.params, "std_error": self.std_errors, "t_stat": self.t_stats,
            "p_value": self.p_values, "ci_lower": self.conf_int["lower"],
            "ci_upper": self.conf_int["upper"],
        })
        df.index.name = "term"
        return df


def _align(y: pd.Series, x: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    joined = pd.concat([y.rename("__y__"), x], axis=1, join="inner").dropna(how="any")
    return joined["__y__"], joined.drop(columns="__y__")


def _check_full_rank(X: pd.DataFrame) -> None:
    """Reject a singular design (duplicate / collinear factors or a constant
    factor): OLS loadings are not identified and statsmodels' pseudo-inverse
    would silently split them."""
    arr = X.to_numpy()
    scale = np.linalg.norm(arr, axis=0)
    scale[scale == 0] = 1.0
    sv = np.linalg.svd(arr / scale, compute_uv=False)
    if sv[-1] <= sv[0] * 1e-10:
        raise ValueError("factor regressors are perfectly collinear (duplicate factors, a constant factor, "
                         "or factors that are linear combinations of each other); loadings are not identified")


def factor_regression(
    y: pd.Series,
    factors: pd.DataFrame,
    lags: int | None = None,
    periods_per_year: int = TRADING_DAYS,
) -> FactorRegression:
    """OLS ``y_t = a + B f_t + e_t`` with Newey-West (1987) HAC inference.

    Parameters
    ----------
    y : excess return series (decimal, per period).
    factors : factor return frame (decimal, per period); rows are inner-joined
        with ``y`` and incomplete rows dropped.
    lags : Newey-West truncation lag; ``None`` -> ``floor(4 (T/100)^(2/9))``.

    Standard errors are the Bartlett-kernel sandwich
    ``(X'X)^-1 [sum_j w_j sum_t x_t e_t e_{t-j} x_{t-j}'] (X'X)^-1`` computed by
    statsmodels (``cov_type='HAC'``); p-values and the 95% interval use the
    normal limit. ``R^2`` and adjusted ``R^2 = 1 - (1 - R^2)(T - 1)/(T - k - 1)``
    are the usual OLS quantities.
    """
    yy, xx = _align(y.astype(float), factors.astype(float))
    k = xx.shape[1]
    if len(yy) < k + 10:
        raise ValueError(f"factor regression needs more observations than factors: {len(yy)} rows, {k} factors")
    lag = nw_lag_rule(len(yy)) if lags is None else int(lags)
    if lag < 0:
        raise ValueError("Newey-West lag must be >= 0")
    X = sm.add_constant(xx, prepend=True, has_constant="add").rename(columns={"const": "alpha"})
    _check_full_rank(X)
    fit = sm.OLS(yy, X).fit(cov_type="HAC", cov_kwds={"maxlags": lag})
    ci = fit.conf_int(0.05)
    ci.columns = ["lower", "upper"]
    return FactorRegression(
        factors=list(xx.columns),
        params=fit.params.rename("estimate"),
        std_errors=fit.bse.rename("std_error"),
        t_stats=fit.tvalues.rename("t_stat"),
        p_values=fit.pvalues.rename("p_value"),
        conf_int=ci,
        r2=float(fit.rsquared),
        adj_r2=float(fit.rsquared_adj),
        n_obs=int(fit.nobs),
        nw_lags=lag,
        resid=fit.resid.rename("residual"),
        fitted=fit.fittedvalues.rename("fitted"),
        y=yy.rename("y"),
        x=xx,
        periods_per_year=periods_per_year,
    )


def rolling_regression(
    y: pd.Series,
    factors: pd.DataFrame,
    window: int = TRADING_DAYS,
    periods_per_year: int = TRADING_DAYS,
) -> pd.DataFrame:
    """Rolling-window OLS loadings (beta paths).

    Uses statsmodels ``RollingOLS``, which updates ``X'X`` / ``X'y`` by
    rank-one additions/removals rather than refitting each window. Returns a
    frame indexed by the window's last date with columns ``alpha_ann``
    (= a * P), one column per factor, and ``r2``; the first ``window - 1``
    rows (incomplete windows) are dropped.
    """
    yy, xx = _align(y.astype(float), factors.astype(float))
    if window < xx.shape[1] + 5:
        raise ValueError(f"rolling window {window} too short for {xx.shape[1]} factors")
    if len(yy) < window:
        raise ValueError(f"only {len(yy)} observations for a {window}-day rolling window")
    X = sm.add_constant(xx, prepend=True, has_constant="add").rename(columns={"const": "alpha"})
    res = RollingOLS(yy, X, window=window).fit(params_only=False)
    out = res.params.copy()
    out["alpha"] = out["alpha"] * periods_per_year
    out = out.rename(columns={"alpha": "alpha_ann"})
    out["r2"] = res.rsquared
    return out.dropna(how="all")


# ------------------------------------------------------- risk decomposition
def risk_decomposition(reg: FactorRegression, periods_per_year: int | None = None) -> dict[str, Any]:
    """Factor risk decomposition of the regressand's variance.

        Var(y) = b' Sigma_f b + Var(e)

    (exact in-sample for OLS with an intercept, because residuals are
    orthogonal to the factors and all moments share ddof = 1). The systematic
    part is split with Euler's theorem for the homogeneous-of-degree-2
    function ``b' Sigma_f b``:

        c_k = b_k (Sigma_f b)_k,     sum_k c_k = b' Sigma_f b,

    so ``c_k`` can be negative (a hedging factor). Shares are ``c_k / Var(y)``.
    Menchero & Davis (2011), "Risk Contribution Is Exposure Times Volatility
    Times Correlation", *JPM* 37(2); Grinold & Kahn (2000), *Active Portfolio
    Management*, ch. 3.
    """
    p = periods_per_year or reg.periods_per_year
    b = reg.betas.to_numpy()
    sigma = reg.x.cov(ddof=1).to_numpy()
    sb = sigma @ b
    contrib = b * sb
    systematic = float(b @ sb)
    idio = reg.resid_var
    total = systematic + idio
    table = pd.DataFrame({
        "beta": b,
        "factor_vol_ann": np.sqrt(np.diag(sigma) * p),
        "variance_contribution": contrib,
        "share_of_total": contrib / total if total > 0 else np.nan,
        "vol_contribution_ann": contrib / np.sqrt(total) * np.sqrt(p) if total > 0 else np.nan,
    }, index=pd.Index(reg.factors, name="factor"))
    return {
        "table": table,
        "total_variance": total,
        "systematic_variance": systematic,
        "idiosyncratic_variance": idio,
        "sample_variance": float(reg.y.var(ddof=1)),
        "total_vol_ann": float(np.sqrt(total * p)),
        "systematic_vol_ann": float(np.sqrt(max(systematic, 0.0) * p)),
        "idiosyncratic_vol_ann": float(np.sqrt(idio * p)),
        "systematic_share": systematic / total if total > 0 else float("nan"),
        "factor_correlation": reg.x.corr(),
    }


# -------------------------------------------------------------- attribution
def return_attribution(reg: FactorRegression) -> dict[str, pd.DataFrame]:
    """Per-period and cumulative return attribution.

    Per period (exact identity from the fitted regression):

        y_t = a + sum_k b_k f_{k,t} + e_t.

    ``daily`` holds columns ``<factor>`` = b_k f_{k,t}, ``alpha`` = a,
    ``residual`` = e_t, and ``total`` = y_t (their row sum).

    ``cumulative_arithmetic`` is the running sum of each column (sums to the
    running sum of y). ``cumulative_linked`` uses Carino (1999) logarithmic
    linking, "Combining Attribution Effects Over Time", *JPM* 7(4):

        k_t = ln(1 + y_t) / y_t,    K_T = ln(1 + Y_T) / Y_T,   Y_T = prod(1 + y_t) - 1,
        C_{i,T} = sum_{t<=T} c_{i,t} k_t / K_T,

    so that sum_i C_{i,T} = Y_T exactly: contributions add up to the
    *compounded* excess return at every date.
    """
    y = reg.y
    b = reg.betas
    daily = reg.x.mul(b, axis=1)
    daily["alpha"] = reg.alpha
    daily["residual"] = reg.resid
    daily["total"] = y
    comps = daily.drop(columns="total")
    cum_arith = comps.cumsum()
    cum_arith["total"] = y.cumsum()

    def _k(r: pd.Series | np.ndarray) -> np.ndarray:
        r = np.asarray(r, dtype=float)
        out = np.ones_like(r)
        nz = np.abs(r) > 1e-12
        out[nz] = np.log1p(r[nz]) / r[nz]
        return out

    k_t = _k(y.to_numpy())
    wealth = np.cumprod(1.0 + y.to_numpy()) - 1.0
    k_cap = _k(wealth)
    linked = comps.mul(k_t, axis=0).cumsum().div(k_cap, axis=0)
    linked["total"] = wealth
    return {"daily": daily, "cumulative_arithmetic": cum_arith, "cumulative_linked": linked}


# ----------------------------------------------------------- custom factors
@dataclass(frozen=True)
class LongShortFactor:
    """A tradable factor ``r_long - r_short`` (``short=None`` -> ``r_long - rf``)."""

    name: str
    long: str
    short: str | None = None

    @property
    def tickers(self) -> list[str]:
        return [self.long] + ([self.short] if self.short else [])


def long_short_factors(
    returns: pd.DataFrame,
    definitions: Iterable[LongShortFactor | Mapping[str, Any]],
    rf: pd.Series | None = None,
) -> pd.DataFrame:
    """Build tradable factor returns from real asset returns.

    ``f_t = r_{long,t} - r_{short,t}``, a zero-investment long/short spread;
    when ``short`` is omitted the factor is the long leg's excess return
    ``r_{long,t} - rf_t`` (or its raw return when ``rf`` is None). This is the
    ETF-based analogue of the Fama-French construction: tradable, daily, and
    available whenever prices are (cf. Huij & Verbeek 2009, "On the Use of
    Multifactor Models to Evaluate Mutual Fund Performance", *JFQA* 44).
    """
    defs = [d if isinstance(d, LongShortFactor) else LongShortFactor(
        name=str(d["name"]), long=str(d["long"]).upper(),
        short=(str(d["short"]).upper() if d.get("short") else None)) for d in definitions]
    if not defs:
        raise ValueError("no factor definitions")
    names = [d.name for d in defs]
    if len(set(names)) != len(names):
        raise ValueError("factor names must be unique")
    cols: dict[str, pd.Series] = {}
    for d in defs:
        missing = [t for t in d.tickers if t not in returns.columns]
        if missing:
            raise ValueError(f"factor {d.name}: no returns for {', '.join(missing)}")
        if d.short:
            if d.short == d.long:
                raise ValueError(f"factor {d.name}: long and short legs are the same ticker")
            cols[d.name] = returns[d.long] - returns[d.short]
        elif rf is not None:
            cols[d.name] = returns[d.long] - rf.reindex(returns.index)
        else:
            cols[d.name] = returns[d.long]
    out = pd.DataFrame(cols).dropna(how="any")
    out.index.name = returns.index.name or "date"
    return out


__all__ = [
    "FACTOR_MODELS", "FACTOR_DESCRIPTIONS", "FactorModelSpec", "FactorRegression", "LongShortFactor",
    "TRADING_DAYS", "factor_regression", "long_short_factors", "model_spec", "return_attribution",
    "risk_decomposition", "rolling_regression",
]
