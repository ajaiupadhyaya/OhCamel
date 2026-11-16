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

American-style options (single stocks, ETFs; :func:`exercise_style`) satisfy parity
only as bounds (Hull 2018, ch. 11; continuous yield ``q``)::

    S e^{-qT} - K  <=  C - P  <=  S - K D

the early-exercise premium of in-the-money puts bends ``C - P`` in ``K`` (on the
2026-11-15 SPY chain the European regression's RMSE grew from $0.11 at 33 days to
$4.31 at two years), so neither the slope (``D``) nor a location over many strikes
(``F``) is unbiased. For those underlyings callers pass ``rate_curve`` (the Treasury
curve, as the Cboe VIX white paper does) and ``D`` is fixed from it; ``F`` comes from
the strike pair nearest the money ``K*`` alone, solved with American prices
(:func:`american_pair_forward`): ``(sigma, q)`` such that the American call and put
at ``K*`` (:mod:`.american`, on spot with the slice's ``r``) both reprice their mids,
so the pair shares one volatility; ``F = S e^{(r-q)T}``. Elsewhere parity is only
the bounds above (:func:`bound_violations`). No dividend schedule is used: discrete
dividends are carried by the pair-implied continuous ``q``.

Timing: ``T`` is ACT/365 from the quote instant to the settlement instant, both
US/Eastern wall-clock: 16:00 for PM-settled contracts, 09:30 (the opening print
used for the special opening quotation) for AM-settled index roots (SPX, NDX,
RUT, DJX monthly roots and VIX). Days are counted as 24-hour calendar days of
wall-clock time (a DST switch does not add or remove an hour), which is the Cboe
VIX white paper's minute count (1,440 minutes per calendar day, 525,600 per year).
Early-close sessions (13:00 ET) are not modelled: T is overstated by 3 hours there.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.special import ndtr

SECONDS_PER_YEAR = 365.0 * 86400.0
AM_SETTLED_ROOTS = frozenset({"SPX", "NDX", "RUT", "DJX", "VIX", "VIXW"})
EUROPEAN_UNDERLYINGS = frozenset({"SPX", "XSP", "NDX", "RUT", "MRUT", "DJX", "VIX", "XEO", "NANOS"})


Exercise = Literal["american", "european"]


class InsufficientQuotes(ValueError):
    """Not enough two-sided quotes to identify the forward / discount factor."""


def exercise_style(underlying: str) -> Exercise:
    """'european' for cash-settled index roots (SPX, NDX, RUT, ... -- ``EUROPEAN_UNDERLYINGS``),
    'american' for everything else (ETF and single-name options). The one classification."""
    return "european" if str(underlying).upper().lstrip("_^") in EUROPEAN_UNDERLYINGS else "american"


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
    residual_rmse: float        # of C - P, USD (NaN for an American fit: chain.py computes it)
    rate_source: str = "regression"   # 'regression' | 'borrowed' | 'treasury'
    k_star: float = float("nan")      # strike with the smallest |C - P|
    strikes: list[float] = field(default_factory=list)
    exercise: str = "european"        # 'american': F from the K* pair with American prices
    pair_vol: float = float("nan")    # American: the vol the K* call and put share

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


def american_pair_forward(
    c_mid: float, p_mid: float, K: float, spot: float, T: float, D: float, maxiter: int = 100,
) -> tuple[float, float, float]:
    """``(F, q, sigma)`` at which the American call and put struck at ``K`` (:mod:`.american`, on
    ``spot`` with ``r = -ln D / T`` and continuous yield ``q``) both reprice their mids.

    Start from European parity (``F0 = K + (C - P)/D``, ``sigma0`` its Black-76 vol) and iterate
    Newton steps whose Jacobian is the Black-Scholes one (vega, and ``dV/dq = -phi T S e^{-qT}
    N(phi d1)``) -- exact for the European part, so the iteration contracts at the rate the
    early-exercise premium moves with ``(sigma, q)``. Stops when ``|dq| T < 1e-13`` and ``|dsigma| <
    1e-11``; :class:`InsufficientQuotes` if the pair has no such solution."""
    from . import american, bsm

    r = -np.log(D) / T
    F0 = K + (c_mid - p_mid) / D
    if not F0 > 0:
        raise InsufficientQuotes(f"the K={K:g} pair implies a non-positive forward")
    q = float(r - np.log(F0 / spot) / T)
    sig = float(bsm.implied_vol_black(c_mid, F0, K, T, D, "C"))
    if not np.isfinite(sig):
        raise InsufficientQuotes(f"the K={K:g} call has no implied volatility")
    typ = np.array([True, False])
    for _ in range(maxiter):
        a = american.prices(spot, K, T, r, q, sig, typ, threads=1)
        rc, rp = float(a[0]) - c_mid, float(a[1]) - p_mid
        g = T * spot * np.exp(-q * T)
        sd = sig * np.sqrt(T)
        d1 = (np.log(spot / K) + (r - q + 0.5 * sig * sig) * T) / sd
        vega = spot * np.exp(-q * T) * np.exp(-0.5 * d1 * d1) / np.sqrt(2.0 * np.pi) * np.sqrt(T)
        dq = (rc - rp) / g
        dsig = (-rc + g * float(ndtr(d1)) * dq) / vega
        if not (np.isfinite(dq) and np.isfinite(dsig)):
            break
        q, sig = q + dq, sig + dsig
        if not sig > 0:
            break
        if abs(dq) * T < 1e-13 and abs(dsig) < 1e-11:
            return float(spot * np.exp((r - q) * T)), q, sig
    raise InsufficientQuotes(f"no (vol, yield) reprices the K={K:g} American call and put")


#: The front expiry implies the underlying only while q T and early exercise are negligible there.
IMPLIED_SPOT_MAX_T = 7.0 / 365.0


@dataclass
class ImpliedSpot:
    """The underlying level the option quotes imply, and whether the chain uses it."""

    level: float              # the option-implied level (NaN when it cannot be formed)
    used: float               # what the American IVs use: ``level``, or the vendor spot on fallback
    source: str               # 'implied' | 'vendor'
    slice: Any = None         # the front expiry it came from
    half_spread: float = float("nan")   # the front pair's (call + put) half-spread at K*
    reason: str = ""


def implied_spot(
    slices: dict[Any, tuple[pd.DataFrame, float]], spot: float, n_strikes: int = 12, rate_curve: Any = None,
    max_T: float = IMPLIED_SPOT_MAX_T, pairs: dict[Any, pd.DataFrame] | None = None,
) -> ImpliedSpot:
    """The underlying level at the option quotes' own time, for American pricing.

    A vendor spot is the stock's last trade (Cboe's ``current_price``: the 16:00 ET close after the
    bell) while equity option quotes run to 16:15 ET, so the two can disagree; Black-76 never sees
    spot, but an American tree exercises against it.

    How the level is implied, and from which quotes: from the FRONT expiry -- the shortest one
    within ``max_T`` (7 days) with a two-sided call and put at one strike -- and there from the
    call and put at ``K*``, the strike minimizing ``|C - P|`` (the pair nearest the money), by
    European parity: ``S = (K* + (C - P)/D) D = K* D + C - P``, ``D`` from ``rate_curve`` when
    given, else the expiry's own parity regression.

    Separation: one pair identifies only ``S e^{-qT}`` (with its early-exercise premia), never S
    and q apart. So the level is taken where q and early exercise are negligible -- the front
    expiry, assuming q = 0 and no early-exercise premium there (at 0.7 days on the 2026-11-15 SPY
    chain, q T is ~1e-5 and the at-the-money premia are fractions of a cent) -- and then held
    fixed while every expiry's q comes from its own pair (:func:`american_pair_forward`). A
    dividend going ex inside the front expiry would be absorbed into the level.

    Falls back to the vendor spot when no expiry within ``max_T`` has a usable pair, or when the
    implied level is within that pair's half-spread ``(c_spread + p_spread) / 2`` of the spot.
    ``pairs`` may pass precomputed :func:`pair_quotes` results by slice.
    """
    front = sorted(((T, sid) for sid, (_, T) in slices.items() if 0 < T <= max_T), key=lambda x: x[0])
    for T, sid in front:
        q = slices[sid][0]
        pq = pair_quotes(q) if pairs is None else pairs[sid]
        if pq.empty:
            continue
        try:
            D = (float(np.exp(-float(rate_curve(T)) * T)) if rate_curve is not None
                 else _fit_european(q, spot, T, n_strikes, None, pq, "regression").discount)
        except InsufficientQuotes:
            continue
        k_star = float((pq["c_mid"] - pq["p_mid"]).abs().idxmin())
        row = pq.loc[k_star]
        level = float(k_star * D + row["c_mid"] - row["p_mid"])
        hs = float((row["c_spread"] + row["p_spread"]) / 2.0)
        if not (np.isfinite(level) and level > 0):
            continue
        if abs(level - spot) < hs:
            return ImpliedSpot(level, spot, "vendor", sid, hs,
                               f"the option-implied level {level:.2f} (the {sid} call and put at {k_star:g}) "
                               f"is within their half-spread {hs:.3f} of it")
        return ImpliedSpot(level, level, "implied", sid, hs, "")
    return ImpliedSpot(float("nan"), spot, "vendor", None, float("nan"),
                       f"no expiry within {max_T * 365:.0f} days has a two-sided near-the-money pair to imply "
                       "the underlying from")


def bound_violations(paired: pd.DataFrame, spot: float, T: float, D: float, q: float) -> int:
    """Strikes whose two-sided quotes cross the American parity bounds tradeably:
    ``C_bid - P_ask > S - K D`` or ``C_ask - P_bid < S e^{-qT} - K`` (``paired`` from
    :func:`pair_quotes`; bid/ask = mid -/+ spread / 2)."""
    K = paired.index.to_numpy(dtype=float)
    cb, ca = paired["c_mid"] - paired["c_spread"] / 2, paired["c_mid"] + paired["c_spread"] / 2
    pb, pa = paired["p_mid"] - paired["p_spread"] / 2, paired["p_mid"] + paired["p_spread"] / 2
    upper = (cb - pa).to_numpy() > spot - K * D
    lower = (ca - pb).to_numpy() < spot * np.exp(-q * T) - K
    return int((upper | lower).sum())


def fit_parity(
    slice_quotes: pd.DataFrame, spot: float, T: float, n_strikes: int = 12,
    fixed_discount: float | None = None, paired: pd.DataFrame | None = None,
    rate_source: str = "borrowed", exercise: Exercise = "european",
) -> ParityFit:
    """Estimate ``F, D`` for one expiry from strike-paired two-sided mids.

    Uses the ``n_strikes`` strikes closest (in log-strike) to ``K*``, the strike
    minimizing ``|C - P|``. With ``fixed_discount`` the slope is fixed and only
    ``F`` is estimated (weighted Huber location of ``K + (C - P)/D``). ``paired`` may
    pass a precomputed :func:`pair_quotes` result. With ``exercise='american'``, ``D``
    is found as above and ``F`` then comes from the ``K*`` pair alone
    (:func:`american_pair_forward`); ``residual_rmse`` is left to the caller (NaN).
    """
    fit = _fit_european(slice_quotes, spot, T, n_strikes, fixed_discount, paired, rate_source)
    if exercise != "american":
        return fit
    pq = pair_quotes(slice_quotes) if paired is None else paired
    row = pq.loc[fit.k_star]
    F, q, sig = american_pair_forward(float(row["c_mid"]), float(row["p_mid"]), fit.k_star, spot, T, fit.discount)
    f_se = float(np.sqrt((row["c_spread"] ** 2 + row["p_spread"] ** 2) / 12.0) / fit.discount)
    return replace(fit, forward=F, div_yield=q, forward_se=f_se, residual_rmse=float("nan"),
                   exercise="american", pair_vol=sig)


def _fit_european(
    slice_quotes: pd.DataFrame, spot: float, T: float, n_strikes: int,
    fixed_discount: float | None, paired: pd.DataFrame | None, rate_source: str,
) -> ParityFit:
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
            rate_source=rate_source, k_star=k_star, strikes=K.tolist(),
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
    max_rate_se: float = 0.005, rate_curve: Any = None, exercise: Exercise = "european",
    pairs: dict[Any, pd.DataFrame] | None = None,
) -> tuple[dict[Any, ParityFit], dict[Any, str]]:
    """Fit every slice; slices with an unidentified rate borrow it (see module doc).

    ``slices`` maps a slice id to ``(quotes, T)``. With ``rate_curve`` (a callable
    ``T -> continuously-compounded r``, e.g. the Treasury curve), every slice fixes
    ``D = exp(-r(T) T)`` and estimates only ``F`` (``rate_source = 'treasury'``).
    ``exercise`` is passed to :func:`fit_parity`; ``pairs`` may pass precomputed :func:`pair_quotes`
    results by slice. Returns (fits, errors-by-slice).
    """
    fits: dict[Any, ParityFit] = {}
    errors: dict[Any, str] = {}
    if pairs is None:
        pairs = {sid: pair_quotes(q) for sid, (q, _) in slices.items()}
    if rate_curve is not None:
        for sid, (q, T) in slices.items():
            r = float(rate_curve(T))
            try:
                fits[sid] = fit_parity(q, spot, T, n_strikes, fixed_discount=float(np.exp(-r * T)),
                                       paired=pairs[sid], rate_source="treasury", exercise=exercise)
            except InsufficientQuotes as e:
                errors[sid] = str(e)
        return fits, errors
    for sid, (q, T) in slices.items():
        try:
            fits[sid] = fit_parity(q, spot, T, n_strikes, paired=pairs[sid], exercise=exercise)
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
            fits[sid] = fit_parity(q, spot, T, n_strikes, fixed_discount=float(np.exp(-r * T)), paired=pairs[sid],
                                   exercise=exercise)
            errors.pop(sid, None)
        except InsufficientQuotes as e:
            errors[sid] = str(e)
            fits.pop(sid, None)
    return fits, errors
