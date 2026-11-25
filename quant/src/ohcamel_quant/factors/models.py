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

import math
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.regression.rolling import RollingOLS

from .hac import newey_west_lrv, nw_lag_rule

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


# ------------------------------------------------- cross-sectional tests
@dataclass(frozen=True)
class FamaMacBeth:
    """Fama & MacBeth (1973) premia with Newey-West and Shanken (1992) standard errors.

    ``premia`` is the time-series mean of the per-period cross-sectional OLS slopes (``intercept`` first).
    ``t_nw`` is the PLAIN t: mean / Newey-West standard error of that mean. ``t_shanken`` divides by the same
    standard error times sqrt(c), c = 1 + lambda' Sigma_f^-1 lambda (slopes only; the intercept is not
    inflated). ``se_nw`` is NOT inflated; ``se_shanken`` is.
    """

    terms: tuple[str, ...]
    premia: pd.Series
    se_nw: pd.Series
    t_nw: pd.Series
    se_shanken: pd.Series
    t_shanken: pd.Series
    shanken_c: float
    nw_lags: int
    n_periods: int
    mean_assets: float
    mean_r2: float
    lambdas: pd.DataFrame
    sigma_f_source: str

    def table(self) -> pd.DataFrame:
        return pd.DataFrame({"premium": self.premia, "se_nw": self.se_nw, "t_nw": self.t_nw,
                             "se_shanken": self.se_shanken, "t_shanken": self.t_shanken})


def fama_macbeth(returns: pd.DataFrame, exposures: dict[str, pd.DataFrame], *, nw_lags: int | None = None,
                 factors: pd.DataFrame | None = None, shanken: bool = True) -> FamaMacBeth:
    """Per-period cross-sectional OLS of returns on exposures (with an intercept), then time-series averages.

    **No-lookahead convention.** Row ``t`` of every exposure frame must be information available BEFORE period
    ``t`` begins (e.g. betas estimated on data ending at ``t-1``, a signal known at the prior close); this
    function aligns rows by label and never shifts, so the caller owns the timing. Dates or assets where any
    exposure or the return is missing are dropped; a date needs at least ``K + 2`` assets.

    Standard errors: Newey & West (1987) on the series of slopes, lag ``floor(4 (T/100)^(2/9))`` unless
    ``nw_lags`` is given. Shanken (1992): ``c = 1 + lambda' Sigma_f^-1 lambda`` multiplies the variance of the
    slopes. ``Sigma_f`` is the sample covariance of ``factors`` (aligned to the periods used) when given, which
    is the right object when the exposures are betas on those factors; otherwise it is proxied by the sample
    covariance of the slope series itself (reported in ``sigma_f_source``), which is NOT the Shanken correction
    and understates c; that path emits a ``UserWarning``. For characteristic exposures (a signal, not an
    estimated beta on an observed factor) pass ``shanken=False``: se_shanken, t_shanken and c are NaN and
    ``sigma_f_source`` says the correction was not applied.
    """
    names = list(exposures)
    if not names:
        raise ValueError("fama_macbeth: no exposures")
    k = len(names)
    idx = returns.index
    for n in names:
        idx = idx.intersection(exposures[n].index)
    cols = returns.columns
    for n in names:
        cols = cols.intersection(exposures[n].columns)
    y = returns.loc[idx, cols].to_numpy(float)
    xs = np.stack([exposures[n].loc[idx, cols].to_numpy(float) for n in names], axis=2)   # T x N x K
    rows: list[np.ndarray] = []
    dates, n_assets, r2s = [], [], []
    for t in range(len(idx)):
        ok = np.isfinite(y[t]) & np.isfinite(xs[t]).all(axis=1)
        if ok.sum() < k + 2:
            continue
        x = np.column_stack([np.ones(ok.sum()), xs[t][ok]])
        coef = np.linalg.lstsq(x, y[t][ok], rcond=None)[0]
        res = y[t][ok] - x @ coef
        tss = float(((y[t][ok] - y[t][ok].mean()) ** 2).sum())
        rows.append(coef)
        dates.append(idx[t])
        n_assets.append(int(ok.sum()))
        r2s.append(1.0 - float(res @ res) / tss if tss > 0 else np.nan)
    if len(rows) < 3:
        raise ValueError(f"fama_macbeth: too few usable periods ({len(rows)}) or assets per period")
    terms = ("intercept", *names)
    lam = pd.DataFrame(np.array(rows), index=pd.DatetimeIndex(dates), columns=list(terms))
    t_n = len(lam)
    lags = nw_lag_rule(t_n) if nw_lags is None else int(nw_lags)
    mean = lam.mean()
    dev = (lam - mean).to_numpy()
    var_mean = np.diag(newey_west_lrv(dev, lags)) / t_n
    se = pd.Series(np.sqrt(var_mean), index=list(terms))
    slopes = mean.iloc[1:].to_numpy()
    if not shanken:
        c, src = float("nan"), "not applied (characteristic exposures, not estimated betas)"
        sigma_f = None
    elif factors is not None:
        f = factors.reindex(lam.index).dropna()
        if len(f) < max(3, k + 1) or f.shape[1] != k:
            raise ValueError("fama_macbeth: factors must have one column per exposure and cover the periods")
        sigma_f, src = np.atleast_2d(f.cov().to_numpy()), "factor covariance"
    else:
        sigma_f, src = np.atleast_2d(lam.iloc[:, 1:].cov().to_numpy()), "covariance of the slope series (proxy)"
        warnings.warn("fama_macbeth: no `factors` given, so the Shanken c uses the slope series' covariance as a "
                      "proxy for the factor covariance; that is not Shanken's correction and understates c",
                      UserWarning, stacklevel=2)
    if sigma_f is not None:
        c = float(1.0 + slopes @ np.linalg.solve(sigma_f, slopes))
    infl = pd.Series([1.0] + [math.sqrt(c)] * k, index=list(terms)) if shanken else pd.Series(np.nan, index=list(terms))
    se_s = se * infl
    return FamaMacBeth(terms, mean, se, mean / se, se_s, mean / se_s, c, lags, t_n,
                       float(np.mean(n_assets)), float(np.nanmean(r2s)), lam, src)


@dataclass(frozen=True)
class GRSResult:
    statistic: float
    p_value: float
    n_assets: int
    n_factors: int
    n_obs: int
    alphas: pd.Series
    mean_abs_alpha: float


def grs(alphas, resid_cov, factor_means, factor_cov, T: int, *, mle: bool = True) -> tuple[float, float]:
    """Gibbons, Ross & Shanken (1989) test that all N alphas are zero, ``F(N, T-N-L)``.

    ``F = (T-N-L)/N * a' S^-1 a / (1 + m' O^-1 m)`` with ``S`` the residual covariance and ``O`` the factor
    covariance, both with the MLE divisor ``T`` (``mle=True``, the form in the paper). With ``mle=False`` the
    inputs are the unbiased forms (``S`` divided by ``T-L-1``, ``O`` by ``T-1``) and are converted to MLE, so
    the statistic is the same. It equals the common textbook form
    ``(T/N)(T-N-L)/(T-L-1) a' S_u^-1 a / (1 + m' O_mle^-1 m)`` (unbiased S, MLE O; using the unbiased O in
    the denominator instead is a slightly different, non-GRS statistic).
    """
    a = np.asarray(alphas, float).ravel()
    m = np.asarray(factor_means, float).ravel()
    n, l_ = a.size, m.size
    s = np.asarray(resid_cov, float).reshape(n, n)
    o = np.asarray(factor_cov, float).reshape(l_, l_)
    if T - n - l_ <= 0:
        raise ValueError(f"grs: needs T > N + L (T={T}, N={n}, L={l_})")
    if not mle:
        s = s * (T - l_ - 1) / T
        o = o * (T - 1) / T
    stat = (T - n - l_) / n * float(a @ np.linalg.solve(s, a)) / (1.0 + float(m @ np.linalg.solve(o, m)))
    return stat, float(stats.f.sf(stat, n, T - n - l_))


def grs_test(excess: pd.DataFrame | np.ndarray, factors: pd.DataFrame | np.ndarray) -> GRSResult:
    """Run the N time-series regressions ``r_i = a_i + b_i' f + e_i`` (one least-squares solve) and test a = 0.

    ``excess`` is T x N excess returns, ``factors`` T x L; rows with any missing value are dropped together."""
    ycols = list(excess.columns) if isinstance(excess, pd.DataFrame) else [f"r{i}" for i in range(np.shape(excess)[1])]
    y = np.asarray(excess, float)
    f = np.asarray(factors, float)
    if f.ndim == 1:
        f = f[:, None]
    ok = np.isfinite(y).all(axis=1) & np.isfinite(f).all(axis=1)
    y, f = y[ok], f[ok]
    t, n = y.shape
    x = np.column_stack([np.ones(t), f])
    coef = np.linalg.solve(x.T @ x, x.T @ y)
    resid = y - x @ coef
    a = coef[0]
    stat, p = grs(a, resid.T @ resid / t, f.mean(axis=0), np.atleast_2d(np.cov(f.T, ddof=0)), t)
    return GRSResult(stat, p, n, f.shape[1], t, pd.Series(a, index=ycols), float(np.abs(a).mean()))


__all__ = [
    "FACTOR_MODELS", "FACTOR_DESCRIPTIONS", "FactorModelSpec", "FactorRegression", "LongShortFactor",
    "TRADING_DAYS", "FamaMacBeth", "GRSResult", "fama_macbeth", "grs", "grs_test", "factor_regression", "long_short_factors", "model_spec", "return_attribution",
    "risk_decomposition", "rolling_regression",
]
