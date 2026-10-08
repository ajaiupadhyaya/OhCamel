"""EXP-Q02 (research/experiments/EXP-Q02/preregistration.md, APPROVED 2026-10-06), as written.

A descriptive risk model: does the FILTERED probability of the high-volatility
HMM state add to an EWMA forecast of next-month SPY realized variance?
Interpretation points I-Q02-1..7 fix the open details (config.yaml).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ..factors.hac import newey_west_lrv, nw_lag_rule, ols_hac_cov
from ..risk.core import ewma_variance_path
from .features import ASOF, asof_join
from .hmm import filtered, order_by, select_k, smoothed
from .verdict import Gate, Verdict

FEATURES = ("ret_4w", "rv_4w", "slope", "d_oas")
VOL_COL = FEATURES.index("rv_4w")


def qlike(rv: np.ndarray, f: np.ndarray) -> np.ndarray:
    """I-Q02-6's QLIKE, ``RV/F - ln(RV/F) - 1`` (Patton 2011): zero at F = RV. (vol_league's ``ln F + RV/F``
    differs only by a term in RV, so the DM test is the same; the reported levels follow the config's form.)"""
    x = np.asarray(rv, dtype=float) / np.asarray(f, dtype=float)
    return x - np.log(x) - 1.0


def week_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(index, index=index)
    iso = index.isocalendar()
    last = s.groupby([iso["year"].to_numpy(), iso["week"].to_numpy()]).max()
    return pd.DatetimeIndex(sorted(last.to_numpy()))[:-1]


def weekly_features(spy: pd.Series, dgs10: pd.Series, dgs2: pd.Series, oas: pd.Series) -> pd.DataFrame:
    we = week_ends(spy.index)
    px = spy.reindex(we)
    lr = np.log(spy).diff()
    pos = spy.index.get_indexer(we)
    rv = [math.sqrt(252.0 * float((lr.iloc[pos[i - 4] + 1:pos[i] + 1] ** 2).mean())) if i >= 4 else np.nan
          for i in range(len(we))]
    slope = asof_join((dgs10 - dgs2).dropna(), we)
    o = asof_join(oas, we)
    f = pd.DataFrame({"ret_4w": px / px.shift(4) - 1.0, "rv_4w": rv, "slope": slope["value"],
                      "d_oas": o["value"] - o["value"].shift(1)}, index=we)
    f["ret_4w" + ASOF] = we
    f["rv_4w" + ASOF] = we
    f["slope" + ASOF] = slope[ASOF]
    f["d_oas" + ASOF] = o[ASOF]
    f.index.name = "date"
    return f


def standardize_expanding(df: pd.DataFrame, min_periods: int = 52) -> pd.DataFrame:
    cols = [c for c in df.columns if not c.endswith(ASOF)]
    m = df[cols].expanding(min_periods=min_periods).mean()
    s = df[cols].expanding(min_periods=min_periods).std(ddof=1)
    return (df[cols] - m) / s


def target_rv(spy: pd.Series, weeks: pd.DatetimeIndex, n: int = 21) -> pd.Series:
    lr2 = (np.log(spy).diff() ** 2).to_numpy()
    pos = spy.index.get_indexer(weeks)
    return pd.Series([float(lr2[p + 1:p + 1 + n].sum()) if p + n < len(lr2) else np.nan for p in pos], index=weeks)


def target_end(spy: pd.Series, weeks: pd.DatetimeIndex, n: int = 21) -> pd.Series:
    """The last session of each week's target window (I-Q02-4, I-Q02-6); NaT while it is in the future."""
    pos = spy.index.get_indexer(weeks)
    return pd.Series([spy.index[p + n] if p + n < len(spy) else pd.NaT for p in pos], index=weeks,
                     dtype="datetime64[ns]")


def ewma_month(spy: pd.Series, weeks: pd.DatetimeIndex, lam: float = 0.94, n: int = 21) -> pd.Series:
    r = np.log(spy).diff().dropna()
    path = ewma_variance_path(r.to_numpy(), lam)       # element t forecasts day t of r
    pos = r.index.get_indexer(weeks)
    return pd.Series([n * float(path[p + 1]) if p >= 0 else np.nan for p in pos], index=weeks)


def refit_dates(weeks: pd.DatetimeIndex, first: pd.Timestamp, every: int) -> pd.DatetimeIndex:
    return weeks[weeks >= first][::every]


def oos_probabilities(z: pd.DataFrame, refits: pd.DatetimeIndex, *, ks: tuple[int, ...], restarts: int, seed: int,
                      min_train: int) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    """Filtered P(high-vol) at each week from the latest refit on or before it (I-Q02-5)."""
    x = z.to_numpy(dtype=float)
    p_high = pd.Series(np.nan, index=z.index)
    states = pd.DataFrame(np.nan, index=z.index, columns=[f"p{j}" for j in range(max(ks))])
    fits, last = [], None
    for j, r in enumerate(refits):
        n = int(z.index.searchsorted(r, side="right"))
        if n < min_train:
            continue
        fit, table = select_k(x[:n], ks, restarts, seed)
        fit = order_by(fit, VOL_COL)
        nxt = int(z.index.searchsorted(refits[j + 1], side="left")) if j + 1 < len(refits) else len(z)
        f = filtered(fit, x[:nxt])
        seg = slice(n - 1, nxt)
        p_high.iloc[seg] = f[n - 1:nxt, -1]
        states.iloc[seg, :fit.k] = f[n - 1:nxt]
        fits.append({"refit": r, "k": fit.k, "loglik": fit.loglik, "bic": fit.bic(n), "weeks": n,
                     "converged": fit.converged})
        last = fit
    out = states.assign(p_high=p_high)
    if last is not None:
        out["p_high_smoothed_history"] = smoothed(last, x)[:, -1]  # uses later data: display only, labelled
    return p_high, out, pd.DataFrame(fits)


def _ols(y: np.ndarray, x: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray, float]:
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    e = y - x @ beta
    r2 = 1.0 - float(e @ e) / float(((y - y.mean()) ** 2).sum())
    return beta, ols_hac_cov(x, e, lags), r2


def score(frame: pd.DataFrame, *, selection_end: pd.Timestamp, holdout_start: pd.Timestamp, min_selection: int,
          min_holdout: int) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, Verdict]:
    """``frame``: weekly ``rv`` (target), ``rv_end`` (its window's last session), ``ewma``, ``p_high``.
    Returns (row, regression, forecasts, verdict).

    I-Q02-6: a week is in selection only if its whole target window ends by ``selection_end``; the holdout
    is the weeks from ``holdout_start`` (targets wholly after it). Weeks whose target straddles the boundary
    are purged from both, so no selection estimate (OLS, HAC t, QLIKE floor) sees holdout-period returns."""
    df = frame[["rv", "rv_end", "ewma", "p_high"]].dropna()
    sel = df[df["rv_end"] <= selection_end]
    hold = df[df.index >= holdout_start]
    purged = int(((df.index <= selection_end) & (df["rv_end"] > selection_end)).sum())
    if len(sel) < min_selection or len(hold) < min_holdout:
        v = Verdict("INSUFFICIENT DATA", f"{len(sel)} selection and {len(hold)} holdout weeks with targets "
                                         f"(need {min_selection} and {min_holdout})")
        return {}, pd.DataFrame(), pd.DataFrame(), v
    lags = max(4, nw_lag_rule(len(sel)))
    xa = np.column_stack([np.ones(len(sel)), sel["ewma"]])
    xb = np.column_stack([xa, sel["p_high"]])
    ba, _, r2a = _ols(sel["rv"].to_numpy(), xa, lags)
    bb, cov_b, r2b = _ols(sel["rv"].to_numpy(), xb, lags)
    t_p = float(bb[2] / math.sqrt(cov_b[2, 2]))
    floor = float(sel["rv"].min())
    ha = np.column_stack([np.ones(len(hold)), hold["ewma"]])
    fa = np.maximum(ha @ ba, floor)
    fb = np.maximum(np.column_stack([ha, hold["p_high"]]) @ bb, floor)
    rv = hold["rv"].to_numpy()
    d = qlike(rv, fb) - qlike(rv, fa)                       # negative: the model with P has lower loss
    hl = max(4, nw_lag_rule(len(d)))
    lrv = float(newey_west_lrv((d - d.mean())[:, None], hl)[0, 0])
    dm = float(d.mean() / math.sqrt(lrv / len(d))) if lrv > 0 else 0.0
    p = float(stats.norm.cdf(dm))                           # one-sided: H1 mean(d) < 0
    sst = float(((rv - rv.mean()) ** 2).sum())
    oos_a, oos_b = 1 - float(((rv - fa) ** 2).sum()) / sst, 1 - float(((rv - fb) ** 2).sum()) / sst
    gates = (Gate("selection_hac_t_on_p", t_p, "> 2", t_p > 2.0),
             Gate("holdout_dm_one_sided_p", p, "< 0.05 and lower QLIKE", bool(d.mean() < 0 and p < 0.05)),
             Gate("incremental_r2_selection", r2b - r2a, "reported", None),
             Gate("incremental_r2_holdout", oos_b - oos_a, "reported", None))
    ok = all(g.passed for g in gates if g.passed is not None)
    v = (Verdict("PASS", "P(HIGH VOL) ADDS TO EWMA · SELECTION AND HOLDOUT · RISK MODEL · NEVER SIZED",
                 gates) if ok else
         Verdict("DESCRIPTIVE ONLY", "ADDS NOTHING OVER EWMA · " + ", ".join(
             f"{g.name.upper()} {g.value:.3g} (NEEDS {g.rule.upper()})" for g in gates if g.passed is False), gates))
    row = {"selection_weeks": len(sel), "selection_last_target_end": str(sel["rv_end"].max().date()),
           "boundary_weeks_purged": purged, "holdout_weeks": len(hold), "hac_t_p": t_p, "hac_lags": lags,
           "r2_ewma": r2a, "r2_ewma_p": r2b, "dm_stat": dm, "dm_pvalue": p, "qlike_ewma": float(qlike(rv, fa).mean()),
           "qlike_ewma_p": float(qlike(rv, fb).mean()), "oos_r2_ewma": oos_a, "oos_r2_ewma_p": oos_b,
           "holdout_start": str(hold.index[0].date()), "holdout_end": str(hold.index[-1].date())}
    reg = pd.DataFrame([{"model": "ewma", "term": t, "coef": float(c)} for t, c in zip(("const", "ewma"), ba, strict=True)]
                       + [{"model": "ewma_p", "term": t, "coef": float(c)}
                          for t, c in zip(("const", "ewma", "p_high"), bb, strict=True)])
    fc = pd.DataFrame({"date": hold.index, "rv": rv, "ewma": hold["ewma"].to_numpy(), "f_ewma": fa, "f_ewma_p": fb})
    return row, reg, fc, v
