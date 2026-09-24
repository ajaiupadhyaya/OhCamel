"""Implied forward, discount factor, rate and dividend yield from put-call parity.

For European options on the same expiry, put-call parity (Stoll 1969, "The
relationship between put and call option prices", J. Finance 24) reads::

    C(K) - P(K) = D (F - K) = D F - D K

so a regression of ``y = C_mid - P_mid`` on ``K`` across strikes has intercept
``alpha = D F`` and slope ``beta = -D``. We fit it by weighted, robust (Huber
IRLS; Huber 1964, "Robust estimation of a location parameter") least squares over
the near-the-money strikes that have two-sided markets in both the call and the
put, weighting by the inverse of the combined bid-ask spread. Then::

    D = -beta,  F = -alpha / beta,  r = -ln(D) / T,  q = r - ln(F / S) / T

This is the approach of the Cboe VIX white paper (forward from the strike where
``|C - P|`` is smallest) generalized to a regression, and of OptionMetrics'
implied-forward construction; it removes any need for an external rate or dividend
forecast. For very short expiries the slope (``1 - D ~ rT``) is not identified by
quotes with finite spreads: if the rate's standard error exceeds
``max_rate_se`` the slice keeps its regression forward but borrows ``r`` from the
reliably-fitted slices (linear interpolation in T, flat extrapolation) and
re-estimates ``F`` with ``D`` fixed; the slice is flagged ``rate_source =
'borrowed'``.

American-style options (single stocks, ETFs) satisfy parity only as an inequality;
the implied ``q`` then also absorbs the early-exercise premium near the money --
callers must say so.

Timing: ``T`` is ACT/365 from the quote instant to the settlement instant, both
US/Eastern wall-clock: 16:00 for PM-settled contracts, 09:30 (the opening print
used for the special opening quotation) for AM-settled index roots (SPX, NDX,
RUT, DJX monthly roots and VIX). Days are counted as 24-hour calendar days of
wall-clock time (a DST switch does not add or remove an hour), which is the Cboe
VIX white paper's minute count (1,440 minutes per calendar day, 525,600 per year).
Early-close sessions (13:00 ET) are not modelled: T is overstated by 3 hours there.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

SECONDS_PER_YEAR = 365.0 * 86400.0
AM_SETTLED_ROOTS = frozenset({"SPX", "NDX", "RUT", "DJX", "VIX", "VIXW"})
EUROPEAN_UNDERLYINGS = frozenset({"SPX", "XSP", "NDX", "RUT", "MRUT", "DJX", "VIX", "XEO", "NANOS"})


class InsufficientQuotes(ValueError):
    """Not enough two-sided quotes to identify the forward / discount factor."""


def settlement_style(root: str) -> str:
    """'AM' for AM-settled index roots, else 'PM'."""
    return "AM" if str(root).upper() in AM_SETTLED_ROOTS else "PM"


def expiry_timestamp(expiry: Any, root: str) -> pd.Timestamp:
    """Settlement instant (tz-naive US/Eastern) of an expiry date for ``root``."""
    d = pd.Timestamp(expiry).normalize()
    return d + (pd.Timedelta(hours=9, minutes=30) if settlement_style(root) == "AM" else pd.Timedelta(hours=16))


def expiry_timestamps(expiry: pd.Series, root: pd.Series) -> pd.Series:
    """Vectorized :func:`expiry_timestamp`."""
    am = root.astype(str).str.upper().isin(AM_SETTLED_ROOTS).to_numpy()
    off = np.where(am, 9.5 * 3600, 16 * 3600).astype("int64")
    return pd.to_datetime(expiry).dt.normalize() + pd.to_timedelta(off, unit="s")


def year_fraction(as_of: pd.Timestamp, expiry_ts: Any) -> np.ndarray:
    """ACT/365 year fraction from ``as_of`` to each settlement instant (seconds precision)."""
    e = pd.to_datetime(pd.Series(np.atleast_1d(expiry_ts)))
    return ((e - pd.Timestamp(as_of)).dt.total_seconds() / SECONDS_PER_YEAR).to_numpy(dtype=float)


@dataclass
class ParityFit:
    """Implied forward & discounting for one expiry slice."""

    T: float
    forward: float
    discount: float
    rate: float                 # continuously-compounded, decimal
    div_yield: float            # continuously-compounded implied dividend/borrow yield, decimal
    n_strikes: int
    rate_se: float              # standard error of r implied by the slope (NaN if not estimated)
    forward_se: float
    residual_rmse: float        # of C - P, USD
    rate_source: str = "regression"   # 'regression' | 'borrowed'
    k_star: float = float("nan")      # strike with the smallest |C - P|
    strikes: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _huber_wls(
    X: np.ndarray, y: np.ndarray, w: np.ndarray, meas_var: np.ndarray | None = None,
    c: float = 1.345, iters: int = 50,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Huber M-estimator by IRLS on top of prior weights ``w``.

    Returns (coef, covariance, final weights). The IRLS scale is the normalized
    MAD of the weighted residuals. The covariance is the heteroskedasticity-robust
    sandwich ``B^{-1} X'W V W X B^{-1}`` with ``B = X'WX`` (White 1980) where each
    observation's variance is ``max(e_i^2, meas_var_i)``: the squared residual,
    floored by the quote's own measurement variance (a mid is only known to within
    its bid-ask spread; uniform on the spread gives ``spread^2 / 12``).
    """
    rw = np.ones_like(y)
    coef = np.zeros(X.shape[1])
    for _ in range(iters):
        ww = w * rw
        sw = np.sqrt(ww)
        coef_new, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
        res = (y - X @ coef_new) * np.sqrt(w)
        s = 1.4826 * np.median(np.abs(res - np.median(res)))
        if not np.isfinite(s) or s <= 1e-14:
            coef = coef_new
            break
        u = np.abs(res) / (c * s)
        rw = np.where(u <= 1.0, 1.0, 1.0 / np.maximum(u, 1e-12))
        if np.allclose(coef_new, coef, rtol=1e-12, atol=1e-14):
            coef = coef_new
            break
        coef = coef_new
    ww = w * rw
    res = y - X @ coef
    n, p = X.shape
    v = res * res * n / max(n - p, 1)
    if meas_var is not None:
        v = np.maximum(v, meas_var)
    try:
        binv = np.linalg.inv(X.T @ (X * ww[:, None]))
        meat = X.T @ (X * (ww * ww * v)[:, None])
        cov = binv @ meat @ binv
    except np.linalg.LinAlgError:
        cov = np.full((p, p), np.nan)
    return coef, cov, ww


def pair_quotes(slice_quotes: pd.DataFrame) -> pd.DataFrame:
    """Strike-aligned call/put mids with two-sided markets in both.

    Input columns: strike, type ('C'|'P'), bid, ask. Output indexed by strike with
    c_mid, p_mid, c_spread, p_spread.
    """
    q = slice_quotes
    two = (q["bid"] > 0) & (q["ask"] > q["bid"]) & np.isfinite(q["bid"]) & np.isfinite(q["ask"])
    q = q[two]
    mid = (q["bid"] + q["ask"]) / 2.0
    spr = q["ask"] - q["bid"]
    c = pd.DataFrame({"strike": q["strike"], "mid": mid, "spread": spr})[q["type"] == "C"]
    p = pd.DataFrame({"strike": q["strike"], "mid": mid, "spread": spr})[q["type"] == "P"]
    c = c.groupby("strike").mean()
    p = p.groupby("strike").mean()
    j = c.join(p, lsuffix="_c", rsuffix="_p", how="inner")
    return j.rename(columns={"mid_c": "c_mid", "mid_p": "p_mid", "spread_c": "c_spread", "spread_p": "p_spread"})


def fit_parity(
    slice_quotes: pd.DataFrame, spot: float, T: float, n_strikes: int = 12,
    fixed_discount: float | None = None, paired: pd.DataFrame | None = None,
) -> ParityFit:
    """Estimate ``F, D`` for one expiry from strike-paired two-sided mids.

    Uses the ``n_strikes`` strikes closest (in log-strike) to ``K*``, the strike
    minimizing ``|C - P|``. With ``fixed_discount`` the slope is fixed and only
    ``F`` is estimated (weighted Huber location of ``K + (C - P)/D``). ``paired`` may
    pass a precomputed :func:`pair_quotes` result.
    """
    if T <= 0:
        raise InsufficientQuotes("expiry is not in the future")
    pq = pair_quotes(slice_quotes) if paired is None else paired
    if pq.empty:
        raise InsufficientQuotes("no strike has two-sided call AND put quotes")
    diff = pq["c_mid"] - pq["p_mid"]
    k_star = float(diff.abs().idxmin())
    K_all = pq.index.to_numpy(dtype=float)
    order = np.argsort(np.abs(np.log(K_all / k_star)))
    sel = np.sort(order[: max(n_strikes, 2)])
    K = K_all[sel]
    y = diff.to_numpy()[sel]
    cs, ps = pq["c_spread"].to_numpy()[sel], pq["p_spread"].to_numpy()[sel]
    spread = cs + ps
    meas_var = (cs * cs + ps * ps) / 12.0
    w = 1.0 / np.maximum(spread, 1e-4)
    w = w / w.mean()

    if fixed_discount is not None:
        D = float(fixed_discount)
        fk = K + y / D
        X = np.ones((len(fk), 1))
        coef, cov, _ = _huber_wls(X, fk, w, meas_var / D**2)
        F = float(coef[0])
        f_se = float(np.sqrt(cov[0, 0])) if np.isfinite(cov[0, 0]) else float("nan")
        res = y - D * (F - K)
        r = -np.log(D) / T
        return ParityFit(
            T=T, forward=F, discount=D, rate=r, div_yield=r - np.log(F / spot) / T, n_strikes=len(K),
            rate_se=float("nan"), forward_se=f_se, residual_rmse=float(np.sqrt(np.mean(res * res))),
            rate_source="borrowed", k_star=k_star, strikes=K.tolist(),
        )

    if len(K) < 3:
        raise InsufficientQuotes(f"only {len(K)} strike(s) with two-sided call and put quotes")
    X = np.column_stack([np.ones_like(K), K])
    coef, cov, _ = _huber_wls(X, y, w, meas_var)
    alpha, beta = float(coef[0]), float(coef[1])
    if not (beta < 0):
        raise InsufficientQuotes("parity regression slope is not negative (D <= 0)")
    D = -beta
    F = -alpha / beta
    # delta method: r = -ln(-beta)/T  -> se_r = se_beta / (D T); F = -alpha/beta
    se_beta = float(np.sqrt(cov[1, 1])) if np.isfinite(cov[1, 1]) else float("nan")
    grad = np.array([-1.0 / beta, alpha / beta**2])
    f_se = float(np.sqrt(grad @ cov @ grad)) if np.all(np.isfinite(cov)) else float("nan")
    r = -np.log(D) / T
    res = y - (alpha + beta * K)
    return ParityFit(
        T=T, forward=F, discount=D, rate=float(r), div_yield=float(r - np.log(F / spot) / T),
        n_strikes=len(K), rate_se=se_beta / (D * T), forward_se=f_se,
        residual_rmse=float(np.sqrt(np.mean(res * res))), rate_source="regression",
        k_star=k_star, strikes=K.tolist(),
    )


def fit_all(
    slices: dict[Any, tuple[pd.DataFrame, float]], spot: float, n_strikes: int = 12,
    max_rate_se: float = 0.005,
) -> tuple[dict[Any, ParityFit], dict[Any, str]]:
    """Fit every slice; slices with an unidentified rate borrow it (see module doc).

    ``slices`` maps a slice id to ``(quotes, T)``. Returns (fits, errors-by-slice).
    """
    fits: dict[Any, ParityFit] = {}
    errors: dict[Any, str] = {}
    pairs = {sid: pair_quotes(q) for sid, (q, _) in slices.items()}
    for sid, (q, T) in slices.items():
        try:
            fits[sid] = fit_parity(q, spot, T, n_strikes, paired=pairs[sid])
        except InsufficientQuotes as e:
            errors[sid] = str(e)
    good = {s: f for s, f in fits.items()
            if np.isfinite(f.rate_se) and f.rate_se <= max_rate_se and 0.5 < f.discount <= 1.2}
    if not good and fits:
        # no slice identifies r tightly: use the most precise one as the anchor
        best = min(fits, key=lambda s: fits[s].rate_se if np.isfinite(fits[s].rate_se) else np.inf)
        if np.isfinite(fits[best].rate_se):
            good = {best: fits[best]}
    if not good:
        return fits, errors
    gT = np.array([f.T for f in good.values()])
    gr = np.array([f.rate for f in good.values()])
    o = np.argsort(gT)
    gT, gr = gT[o], gr[o]
    for sid, (q, T) in slices.items():
        if sid in good:
            continue
        r = float(np.interp(T, gT, gr))  # flat extrapolation outside the fitted range
        try:
            fits[sid] = fit_parity(q, spot, T, n_strikes, fixed_discount=float(np.exp(-r * T)), paired=pairs[sid])
            errors.pop(sid, None)
        except InsufficientQuotes as e:
            errors[sid] = str(e)
            fits.pop(sid, None)
    return fits, errors
