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

Out of sample (completion task 3.5)
-----------------------------------
:func:`fit_expanding` refits the probit at every month ``t`` on what was known at the end of ``t`` and only
then predicts ``REC_{t+h}``: the pairs ``(x_s, REC_{s+h})`` with ``s + h <= t``, each label as the NBER
chronology stood that day (:func:`usrec_known_at`: final values up to the last turning point announced by
then, the regime it opened after it -- NBER dates a turning point 4 to 21 months after it happens, and until then
FRED's USREC read 0 through a recession and 1 past its trough). The spread is a Treasury yield difference,
never revised. ``oos_brier`` is the mean squared error of those probabilities against today's USREC; it is the
number the page leads with, beside ``baseline_brier`` -- the constant forecast available at each origin (the share
of recession months among the labels that origin's fit used), scored on the same targets -- and ``brier_skill``,
``1 - oos_brier / baseline_brier``. A hit rate is shown with ``base_hit_rate``, the share of scored months with no
recession: never forecasting one scores that. The in-sample fit (:func:`fit_probit`, every month's final label, every month's
data) stays as the comparison it is.

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

import re
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
    "sahm_realtime",
    "SAHM_THRESHOLD",
    "recession_episodes",
    "NBER_ANNOUNCEMENTS",
    "parse_nber_announcements",
    "usrec_known_at",
    "RecessionOOS",
    "fit_expanding",
]

#: Every NBER business-cycle turning point with its announcement date, from the Business Cycle Dating Committee's
#: announcements page (https://www.nber.org/research/business-cycle-dating/business-cycle-dating-committee-
#: announcements, fetched 2026-11-19; tests/data/nber_announcements_2026-11-19.txt, which
#: tests/test_macro_recession.py parses back into this table). ``(kind, turning-point month, announced)``. The
#: committee's first announcement is June 1980; the turning points before 1980 are taken as known by then.
NBER_ANNOUNCEMENTS: tuple[tuple[str, str, str], ...] = (
    ("peak", "1980-01-01", "1980-06-03"),
    ("trough", "1980-07-01", "1981-07-08"),
    ("peak", "1981-07-01", "1982-01-06"),
    ("trough", "1982-11-01", "1983-07-08"),
    ("peak", "1990-07-01", "1991-04-25"),
    ("trough", "1991-03-01", "1992-12-22"),
    ("peak", "2001-03-01", "2001-11-26"),
    ("trough", "2001-11-01", "2003-07-17"),
    ("peak", "2007-12-01", "2008-12-01"),
    ("trough", "2009-06-01", "2010-09-20"),
    ("peak", "2020-02-01", "2020-06-08"),
    ("trough", "2020-04-01", "2021-07-19"),
)

_ANNOUNCED = re.compile(r"^(?:Announcement|Determination) of (?:the )?([A-Z][a-z]+ \d{4}) "
                        r"(?:business cycle )?(peak|trough)", re.I)


def parse_nber_announcements(text: str) -> tuple[tuple[str, str, str], ...]:
    """The announcements page's list (a date line, then its title line) -> ``NBER_ANNOUNCEMENTS``' shape, oldest
    first. Memos (no turning point) are skipped; a title naming a turning point the parser cannot read is refused."""
    from datetime import datetime as _dt

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: list[tuple[str, str, str]] = []
    for when, title in zip(lines[0::2], lines[1::2], strict=True):
        announced = _dt.strptime(when, "%B %d, %Y").date()
        if title.lower().startswith("memo"):
            continue
        m = _ANNOUNCED.match(title)
        if not m:
            raise ValueError(f"unreadable announcement title {title!r}")
        month = _dt.strptime(m.group(1), "%B %Y").date()
        out.append((m.group(2).lower(), month.isoformat(), announced.isoformat()))
    return tuple(sorted(out, key=lambda r: r[1]))


def usrec_known_at(usrec: pd.Series, asof: Any,
                   announcements: tuple[tuple[str, str, str], ...] = NBER_ANNOUNCEMENTS, *,
                   through: Any = None) -> pd.Series | None:
    """USREC as it read on ``asof``: final values through the last turning point announced on or before
    ``asof``, then the regime that turning point opened (1 after a peak, 0 after a trough) through ``asof``'s
    month (or ``through``'s: the same reading carried to a later month, for a caller that cuts it itself).
    ``None`` before the first announcement. FRED's convention, checked on the final series: the peak month
    reads 0 and the trough month 1."""
    asof = pd.Timestamp(asof)
    known = [(k, pd.Timestamp(m)) for k, m, a in announcements if pd.Timestamp(a) <= asof]
    if not known:
        return None
    kind, month = max(known, key=lambda r: r[1])
    u = usrec.dropna().astype(float).copy()
    u.index = u.index.to_period("M").to_timestamp()
    u = u[~u.index.duplicated(keep="last")].sort_index()
    end = pd.Timestamp(through if through is not None else asof).to_period("M").to_timestamp()
    grid = pd.date_range(min(u.index[0], month), end, freq="MS")
    out = u.reindex(grid)
    out[out.index > month] = 1.0 if kind == "peak" else 0.0
    return out.dropna()


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


def _probit_mle(X: np.ndarray, y: np.ndarray, beta0: np.ndarray | None = None,
                tol: float = 1e-10, maxiter: int = 100) -> np.ndarray | None:
    """Probit maximum likelihood by Newton-Raphson (the score and Hessian of Greene, Econometric Analysis, 17.3);
    the same estimate as statsmodels' ``Probit`` (tests/test_macro_recession.py) at a fraction of its cost, which
    matters for ~550 refits. ``None`` when it does not converge (a separated sample)."""
    from scipy.stats import norm

    beta = np.zeros(X.shape[1]) if beta0 is None else beta0.astype(float).copy()
    q = 2.0 * y - 1.0
    for _ in range(maxiter):
        xb = X @ beta
        lam = q * np.exp(norm.logpdf(q * xb) - norm.logcdf(q * xb))   # d logL / d xb
        grad = X.T @ lam
        w = lam * (lam + xb)                                          # -d2 logL / d xb2, > 0
        hess = (X * w[:, None]).T @ X
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            return None
        beta = beta + step
        if not np.all(np.isfinite(beta)) or np.abs(beta).max() > 50:
            return None
        if np.abs(step).max() < tol:
            return beta
    return None


@dataclass
class RecessionOOS:
    """:func:`fit_expanding`'s result. Every series is indexed by the TARGET month ``t + h``."""

    h: int
    oos_prob: pd.Series          # P(REC_{t+h}) from the fit made at the end of month t
    origin: pd.Series            # t
    fit_end: pd.Series           # the last label month that fit used (<= t)
    nobs: pd.Series              # pairs in that fit
    oos_brier: float             # mean (oos_prob - final USREC)^2 over the scored targets
    oos_hit_rate: float          # share of scored targets where (oos_prob >= 0.5) == final USREC
    insample_prob: pd.Series     # the full-sample fit's probability for the same targets
    insample_brier: float
    insample_hit_rate: float
    n_scored: int
    scored: tuple[str, str]      # first and last scored target month
    # fix round 1: what the scores are measured against, on the same scored targets
    baseline: pd.Series          # the real-time constant forecast: the mean of the labels the origin's fit used
    baseline_brier: float        # its Brier score
    brier_skill: float           # 1 - oos_brier / baseline_brier (> 0: the probit beats the constant)
    base_hit_rate: float         # 1 - mean(final USREC): the hit rate of never predicting a recession
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"h": self.h, "oos_brier": self.oos_brier, "oos_hit_rate": self.oos_hit_rate,
                "insample_brier": self.insample_brier, "insample_hit_rate": self.insample_hit_rate,
                "baseline_brier": self.baseline_brier, "brier_skill": self.brier_skill,
                "base_hit_rate": self.base_hit_rate,
                "n_scored": self.n_scored, "scored": {"start": self.scored[0], "end": self.scored[1]}}


def _brier_hit(p: pd.Series, y: pd.Series) -> tuple[float, float]:
    return float(((p - y) ** 2).mean()), float(((p >= 0.5).astype(float) == y).mean())


def fit_expanding(usrec: pd.Series, x: pd.DataFrame, h: int, *, min_obs: int = 60,
                  announcements: tuple[tuple[str, str, str], ...] = NBER_ANNOUNCEMENTS) -> RecessionOOS:
    """The probit refit at every month ``t`` on what was known at the end of ``t`` (module docstring), each
    fit predicting ``REC_{t+h}`` from ``x_t``. Origins start at the first month end on or after NBER's first
    announcement; a month whose known sample is shorter than ``min_obs`` or has no recession is skipped.
    Scored against the final ``usrec`` wherever it has the target month."""
    from scipy.stats import norm

    if h < 1:
        raise ValueError("h must be >= 1 (a fit that ends before the date it predicts)")
    xm = x.copy()
    xm.index = xm.index.to_period("M").to_timestamp()
    xm = xm[~xm.index.duplicated(keep="last")].sort_index().dropna()
    final = usrec.dropna().astype(float).copy()
    final.index = final.index.to_period("M").to_timestamp()
    final = final[~final.index.duplicated(keep="last")].sort_index()
    cols = list(xm.columns)
    probs: dict[pd.Timestamp, float] = {}
    constant: dict[pd.Timestamp, float] = {}
    origin: dict[pd.Timestamp, pd.Timestamp] = {}
    fit_end: dict[pd.Timestamp, pd.Timestamp] = {}
    nobs: dict[pd.Timestamp, int] = {}
    beta = None
    off = pd.DateOffset(months=h)
    # One monthly grid; x_s pairs with the label at grid position s + h. Labels as known at t come from
    # usrec_known_at, which changes only when an announcement falls due, so it is recomputed only then.
    grid = pd.date_range(min(xm.index[0], final.index[0]), xm.index[-1], freq="MS")
    pos = {d: i for i, d in enumerate(grid)}
    xg = xm.reindex(grid)[cols].to_numpy(float)
    has_x = ~np.isnan(xg).any(axis=1)
    due = sorted(pd.Timestamp(a) for _, _, a in announcements)
    state, lab = -1, None
    for t in xm.index:
        known_at = t + pd.offsets.MonthEnd(0)
        k = sum(1 for a in due if a <= known_at)
        if k == 0:
            continue
        if k != state:
            state = k
            known = usrec_known_at(final, known_at, announcements, through=grid[-1])
            lab = known.reindex(grid).to_numpy(float)
        i = pos[t]
        # pairs (x_s, label_{s+h}) with s + h <= t, each label as it read at the end of t
        last = i - h
        if last < 0:
            continue
        s = np.arange(last + 1)
        y = lab[s + h]
        ok = has_x[s] & ~np.isnan(y)
        s, y = s[ok], y[ok]
        if len(s) < min_obs or np.unique(y).size < 2:
            continue
        X = np.column_stack([np.ones(len(s)), xg[s]])
        b = _probit_mle(X, y, beta)
        if b is None:
            continue
        beta = b
        target = t + off
        probs[target] = float(norm.cdf(np.r_[1.0, xg[i]] @ b))
        constant[target] = float(y.mean())   # the same labels, known at t, forecast as a constant
        origin[target], fit_end[target], nobs[target] = t, grid[s[-1] + h], len(s)
    if not probs:
        raise ValueError("no origin had a known sample to fit")
    oos = pd.Series(probs, name="oos_prob").sort_index()
    oos.index.name = "date"
    base = fit_probit(final, xm, h, hac=False)
    ins = base.fitted.copy()
    ins.index = ins.index + off
    scored = oos.index.intersection(final.index).intersection(ins.index)
    if len(scored) == 0:
        raise ValueError("no target month has a final USREC value to score against")
    yb = final.loc[scored]
    ob, oh = _brier_hit(oos.loc[scored], yb)
    ib, ih = _brier_hit(ins.loc[scored], yb)
    const = pd.Series(constant, name="baseline").sort_index()
    const.index.name = "date"
    bb, _ = _brier_hit(const.loc[scored], yb)
    notes = [f"out of sample: each probability comes from a probit fitted at the end of its origin month on "
             f"the labels NBER had announced by then, {len(oos)} refits",
             f"scored on {len(scored)} target months {scored[0].date()}..{scored[-1].date()} against today's "
             "USREC; the recent zeros in it are provisional until NBER dates the next peak"]
    return RecessionOOS(h=h, oos_prob=oos, origin=pd.Series(origin).sort_index(),
                        fit_end=pd.Series(fit_end).sort_index(), nobs=pd.Series(nobs).sort_index(),
                        oos_brier=ob, oos_hit_rate=oh, insample_prob=ins.loc[oos.index.intersection(ins.index)],
                        insample_brier=ib, insample_hit_rate=ih, n_scored=len(scored),
                        scored=(scored[0].date().isoformat(), scored[-1].date().isoformat()),
                        baseline=const, baseline_brier=bb, brier_skill=1.0 - ob / bb,
                        base_hit_rate=1.0 - float(yb.mean()), notes=notes)


def sahm_realtime(vintages: pd.DataFrame) -> pd.DataFrame:
    """The Sahm rule as it read when each month's unemployment rate was first published: for every month ``t``,
    :func:`sahm_rule` on the vintage in effect on ``t``'s first release (ALFRED; ``vintages`` has columns
    ``date, realtime_start, value``), its value at ``t``. Same columns as :func:`sahm_rule`, plus
    ``published`` (the first-release date). Months already in the earliest vintage given were published before
    the record begins (ALFRED's UNRATE starts 1960-03-15), so they are left out rather than dated by it.

    One sweep through the vintages in publication order keeps the series as it stood; at each first release the
    rule is evaluated on the last :data:`_SAHM_SPAN` calendar months only (it reads ``t-14..t``), which gives
    the same value as the whole series (tests/test_macro_recession.py) at a fraction of the cost."""
    v = vintages[["date", "realtime_start", "value"]].dropna().copy()
    v["date"] = pd.to_datetime(v["date"]).dt.to_period("M").dt.to_timestamp()
    v["realtime_start"] = pd.to_datetime(v["realtime_start"])
    v = v.sort_values(["realtime_start", "date"], kind="stable")
    state: dict[pd.Timestamp, float] = {}
    rows = []
    record_starts = v["realtime_start"].min() if len(v) else None
    for published, grp in v.groupby("realtime_start", sort=True):
        new = [d for d in grp["date"] if d not in state]
        state.update(zip(grp["date"], grp["value"].astype(float), strict=True))
        if published == record_starts:
            continue
        for t in sorted(set(new)):
            lo = t - pd.DateOffset(months=_SAHM_SPAN - 1)
            window = pd.Series({d: x for d, x in state.items() if lo <= d <= t}).sort_index()
            s = sahm_rule(window)
            if len(s) and s.index[-1] == t:
                rows.append({**s.iloc[-1].to_dict(), "date": t, "published": published})
    cols = ["unrate", "ma3", "prior_12m_min", "sahm", "triggered", "published"]
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows).set_index("date")[cols].sort_index()
    out["triggered"] = out["triggered"].astype(bool)
    return out


#: Calendar months sahm_realtime evaluates the rule on: the rule reads t-14..t, plus one month of margin.
_SAHM_SPAN = 16


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
    # The rate is published to 0.1, so the indicator is a multiple of 1/30: rounded to 10 places, the float noise
    # of the averaging cannot put a month exactly at 0.50 on either side of the threshold (April 2008 as first
    # published reads 0.50 exactly; summed in another order it came out 0.4999999999999996).
    sahm = (ma3 - prior_min).round(10)
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
