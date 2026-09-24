"""Recession probability (yield-curve probit) and the Sahm rule.

Probit (Estrella & Mishkin 1998)
--------------------------------
``P(REC_{t+h} = 1 | x_t) = Phi(a + b * spread_t [+ g' z_t])``

with ``REC`` the NBER recession indicator (FRED ``USREC``, monthly),
``spread`` the 10-year minus 3-month Treasury spread (monthly average of
FRED ``T10Y3M`` where it exists, from 1982; before that ``GS10 - TB3MS``,
both monthly), ``h`` months ahead (12 by default, the horizon at which
Estrella & Mishkin find the spread most informative). Estimation is by
maximum likelihood (statsmodels ``Probit``); because consecutive h-ahead
targets overlap, standard errors are Newey-West HAC with ``h`` lags.

Fit quality is reported as the Estrella (1998) pseudo-R2 used in the paper,
``1 - (logL_u / logL_c)^(-(2/n) logL_c)``, alongside McFadden's.

Sahm rule (Sahm 2019)
---------------------
``SAHM_t = MA3(u)_t - min(MA3(u)_{t-12}, ..., MA3(u)_{t-1})``; a recession
signal when ``SAHM_t >= 0.50`` percentage points (the rule's definition).

References
----------
* Estrella, A. & Mishkin, F. S. (1998), "Predicting U.S. Recessions:
  Financial Variables as Leading Indicators", Review of Economics and
  Statistics 80(1).
* Estrella, A. (1998), "A New Measure of Fit for Equations with Dichotomous
  Dependent Variables", J. Business & Economic Statistics 16(2).
* Newey, W. & West, K. (1987), "A Simple, Positive Semi-definite,
  Heteroskedasticity and Autocorrelation Consistent Covariance Matrix",
  Econometrica 55(3).
* Sahm, C. (2019), "Direct Stimulus Payments to Individuals", in *Recession
  Ready*, Brookings / Hamilton Project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

SAHM_THRESHOLD = 0.50  # percentage points -- part of the rule's definition (Sahm 2019)

__all__ = [
    "monthly_mean",
    "term_spread_monthly",
    "align_forecast",
    "ProbitResult",
    "fit_probit",
    "estrella_r2",
    "sahm_rule",
    "SAHM_THRESHOLD",
    "recession_episodes",
]


def monthly_mean(s: pd.Series) -> pd.Series:
    """Calendar-month average of a (daily/weekly) series, indexed at month start."""
    s = s.dropna()
    out = s.groupby(s.index.to_period("M")).mean()
    out.index = out.index.to_timestamp()
    out.index.name = "date"
    return out


def term_spread_monthly(t10y3m_daily: pd.Series | None, gs10: pd.Series | None,
                        tb3ms: pd.Series | None) -> tuple[pd.Series, list[str]]:
    """10y-3m spread, percent, monthly: monthly mean of daily ``T10Y3M``
    where available, spliced with ``GS10 - TB3MS`` for earlier months."""
    notes: list[str] = []
    parts: list[pd.Series] = []
    if t10y3m_daily is not None and t10y3m_daily.notna().any():
        parts.append(monthly_mean(t10y3m_daily))
    if gs10 is not None and tb3ms is not None:
        early = (gs10 - tb3ms).dropna()
        early.index = early.index.to_period("M").to_timestamp()
        if parts:
            early = early[early.index < parts[0].index.min()]
        if not early.empty:
            parts.insert(0, early)
            splice = parts[1].index.min().date().isoformat() if len(parts) > 1 else "end of sample"
            notes.append(f"before {splice}: GS10 - TB3MS (TB3MS is a discount-basis secondary-market "
                         "rate; T10Y3M uses the bond-equivalent CMT 3m)")
    if not parts:
        raise ValueError("no term-spread data")
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.name = "spread"
    return out, notes


def align_forecast(y: pd.Series, x: pd.DataFrame, h: int) -> tuple[pd.Series, pd.DataFrame]:
    """Pair ``x_t`` with ``y_{t+h}`` on a monthly index: returns ``(y_lead, x)``
    restricted to months where both exist. ``y_lead.index`` is the forecast
    ORIGIN ``t``."""
    if h < 0:
        raise ValueError("h must be >= 0")
    y = y.copy()
    y.index = y.index.to_period("M").to_timestamp()
    x = x.copy()
    x.index = x.index.to_period("M").to_timestamp()
    idx = x.index.union(y.index)
    y_full = y.reindex(pd.date_range(idx.min(), idx.max() + pd.DateOffset(months=h), freq="MS"))
    y_lead = y_full.shift(-h).reindex(x.index)
    both = y_lead.notna() & x.notna().all(axis=1)
    return y_lead[both], x[both]


def estrella_r2(llf: float, llnull: float, n: int) -> float:
    """Estrella (1998) pseudo-R2: ``1 - (logL_u/logL_c)^(-(2/n) logL_c)``."""
    return float(1.0 - (llf / llnull) ** (-(2.0 / n) * llnull))


@dataclass
class ProbitResult:
    h: int
    params: pd.Series
    bse: pd.Series
    pvalues: pd.Series
    llf: float
    llnull: float
    nobs: int
    mcfadden_r2: float
    estrella_r2: float
    fitted: pd.Series           # P(REC_{t+h}) indexed by ORIGIN month t (all months with x)
    regressors: list[str]
    notes: list[str] = field(default_factory=list)

    def predict(self, x: pd.DataFrame) -> pd.Series:
        from scipy.stats import norm

        X = sm.add_constant(x[self.regressors], has_constant="add")
        return pd.Series(norm.cdf(X.to_numpy() @ self.params.to_numpy()), index=x.index)

    def to_dict(self) -> dict[str, Any]:
        return {"h": self.h, "params": self.params.to_dict(), "std_errors": self.bse.to_dict(),
                "pvalues": self.pvalues.to_dict(), "loglik": self.llf, "loglik_null": self.llnull,
                "nobs": self.nobs, "pseudo_r2_mcfadden": self.mcfadden_r2,
                "pseudo_r2_estrella": self.estrella_r2, "regressors": self.regressors}


def fit_probit(y: pd.Series, x: pd.DataFrame, h: int, hac: bool = True) -> ProbitResult:
    """Fit ``P(y_{t+h}=1) = Phi(c + x_t' b)`` by MLE (statsmodels ``Probit``)
    on all aligned months; HAC (Newey-West, ``h`` lags) standard errors."""
    y_lead, xa = align_forecast(y, x, h)
    if len(y_lead) < 60:
        raise ValueError("fewer than 60 aligned monthly observations")
    if y_lead.nunique() < 2:
        raise ValueError("target has no variation")
    X = sm.add_constant(xa, has_constant="add")
    model = sm.Probit(y_lead.to_numpy(dtype=float), X)
    kw: dict[str, Any] = {"disp": 0, "maxiter": 200}
    if hac and h > 0:
        res = model.fit(cov_type="HAC", cov_kwds={"maxlags": h}, **kw)
    else:
        res = model.fit(**kw)
    names = list(X.columns)
    params = pd.Series(np.asarray(res.params), index=names)
    out = ProbitResult(
        h=h, params=params, bse=pd.Series(np.asarray(res.bse), index=names),
        pvalues=pd.Series(np.asarray(res.pvalues), index=names), llf=float(res.llf),
        llnull=float(res.llnull), nobs=int(res.nobs), mcfadden_r2=float(res.prsquared),
        estrella_r2=estrella_r2(float(res.llf), float(res.llnull), int(res.nobs)),
        fitted=pd.Series(dtype=float), regressors=list(xa.columns))
    xx = x.copy()
    xx.index = xx.index.to_period("M").to_timestamp()
    out.fitted = out.predict(xx.dropna())
    if hac and h > 0:
        out.notes.append(f"Newey-West HAC standard errors with {h} lags (overlapping {h}-month targets)")
    return out


def sahm_rule(unrate: pd.Series) -> pd.DataFrame:
    """Sahm (2019) indicator from the monthly unemployment rate (percent):
    ``ma3_t - min(ma3_{t-12..t-1})`` and the trigger flag ``>= 0.50``.

    Windows are CALENDAR months (the rule's definition), not observation
    counts: the series is placed on a monthly grid so that a month with no
    release (e.g. October 2025, when the CPS was not collected) does not
    silently stretch a "3-month" average over four months or the 12-month
    look-back over thirteen. ``ma3`` averages the observations available in
    the 3 calendar months ending at ``t`` (at least 2 of 3); months without
    a release are not reported.
    """
    u = unrate.dropna().astype(float)
    u.index = u.index.to_period("M").to_timestamp()
    u = u[~u.index.duplicated(keep="last")].sort_index()
    if u.empty:
        return pd.DataFrame(columns=["unrate", "ma3", "prior_12m_min", "sahm", "triggered"])
    grid = pd.date_range(u.index[0], u.index[-1], freq="MS")
    g = u.reindex(grid)
    ma3 = g.rolling(3, min_periods=2).mean()
    ma3.iloc[:2] = np.nan  # the first full 3-month window ends at the third month
    prior_min = ma3.shift(1).rolling(12).min()
    sahm = ma3 - prior_min
    out = pd.DataFrame({"unrate": g, "ma3": ma3, "prior_12m_min": prior_min, "sahm": sahm,
                        "triggered": sahm >= SAHM_THRESHOLD})
    return out[g.notna()].dropna(subset=["sahm"])


def recession_episodes(usrec: pd.Series) -> list[dict[str, str]]:
    """Contiguous runs of ``USREC == 1`` as ``[{start, end}]`` (month starts)."""
    s = usrec.dropna().astype(int)
    out: list[dict[str, str]] = []
    start = None
    prev = None
    for d, v in s.items():
        if v == 1 and start is None:
            start = d
        if v == 0 and start is not None:
            out.append({"start": pd.Timestamp(start).date().isoformat(),
                        "end": pd.Timestamp(prev).date().isoformat()})
            start = None
        prev = d
    if start is not None:
        out.append({"start": pd.Timestamp(start).date().isoformat(),
                    "end": pd.Timestamp(prev).date().isoformat()})
    return out
