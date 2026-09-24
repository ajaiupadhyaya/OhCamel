"""Fixed-coupon bond analytics (price/yield, risk measures, curve pricing, KRDs).

Conventions (U.S. Treasury street convention)
---------------------------------------------
* Coupons ``c`` (annual rate, decimal) paid ``f`` times a year on a regular
  schedule rolled back from maturity in steps of ``12/f`` months
  (end-of-month rule when maturity is a month end).
* Accrued interest: ACT/ACT (ICMA Rule 251), the Treasury convention:
  ``AI = (c/f) * face * (days(last coupon, settle) / days(last coupon, next coupon))``.
* Yield-to-maturity ``y`` is compounded ``f`` times a year. With ``w`` the
  fraction of the current coupon period remaining
  (``days(settle, next)/days(last, next)``) and ``n`` remaining coupons,

  ``Dirty(y) = sum_{k=1..n} CF_k / (1 + y/f)^{k-1+w}``,  ``Clean = Dirty - AI``.

* Macaulay duration ``D = sum t_k PV_k / Dirty`` with ``t_k = (k-1+w)/f``;
  modified duration ``D* = D / (1 + y/f)``;
  convexity ``C = sum t_k (t_k + 1/f) PV_k / (Dirty (1 + y/f)^2)``;
  ``DV01 = D* x Dirty x 1e-4`` (price change per 1bp, per 100 face).
* Curve pricing uses continuously-compounded zero rates on an ACT/365.25 time
  axis from settlement: ``Dirty = sum CF_k exp(-z(t_k) t_k)``; the Z-spread
  ``s`` solves ``Dirty_mkt = sum CF_k exp(-(z(t_k) + s) t_k)``.
* Key-rate durations (Ho 1992): the zero curve is bumped by 1bp at one key
  tenor with a triangular (tent) weight that is 1 at the key and falls
  linearly to 0 at the neighbouring keys (flat beyond the first/last key);
  the tents sum to one everywhere, so the KRDs sum to the effective duration
  for a parallel shift (to second order).

References
----------
* Fabozzi, F. J. *Bond Markets, Analysis, and Strategies* -- price/yield,
  duration and convexity.
* ICMA Rule 251 (ACT/ACT accrued interest); SIFMA *Standard Formulas* for
  price/yield of Treasury securities.
* Ho, T. S. Y. (1992), "Key Rate Durations: Measures of Interest Rate
  Risks", Journal of Fixed Income 2(2).
* Brent, R. P. (1973), *Algorithms for Minimization Without Derivatives*.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy import optimize

from .curve import ZeroCurve

DEFAULT_KEY_TENORS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0)
DAYS_PER_YEAR_CURVE = 365.25

__all__ = [
    "Bond",
    "coupon_schedule",
    "cashflows",
    "accrued_interest",
    "price_from_yield",
    "yield_from_price",
    "risk_measures",
    "curve_price",
    "z_spread",
    "key_rate_durations",
    "effective_duration",
    "DEFAULT_KEY_TENORS",
]


@dataclass(frozen=True)
class Bond:
    """A fixed-coupon bullet bond. ``coupon`` is the annual rate as a DECIMAL."""

    coupon: float
    maturity: pd.Timestamp
    freq: int = 2
    face: float = 100.0

    def __post_init__(self) -> None:
        if self.freq not in (1, 2, 4, 12):
            raise ValueError("freq must be 1, 2, 4 or 12")
        if self.coupon < 0:
            raise ValueError("coupon must be non-negative")
        object.__setattr__(self, "maturity", pd.Timestamp(self.maturity).normalize())


def _roll(d: pd.Timestamp, months: int, eom: bool) -> pd.Timestamp:
    out = d + pd.DateOffset(months=months)
    if eom:
        out = out + pd.offsets.MonthEnd(0)
    return out.normalize()


def coupon_schedule(bond: Bond, settle: pd.Timestamp | date) -> tuple[pd.Timestamp, list[pd.Timestamp]]:
    """``(previous coupon date, [remaining coupon dates > settle])``, rolled
    back from maturity by ``12/freq`` months (EOM rule if maturity is a
    month end)."""
    s = pd.Timestamp(settle).normalize()
    if s >= bond.maturity:
        raise ValueError("settlement must be before maturity")
    step = 12 // bond.freq
    eom = bond.maturity.is_month_end
    dates = [bond.maturity]
    k = 1
    while True:
        d = _roll(bond.maturity, -step * k, eom)
        if d <= s:
            return d, sorted(dates)
        dates.append(d)
        k += 1


def _period_fraction(bond: Bond, settle: pd.Timestamp) -> tuple[pd.Timestamp, list[pd.Timestamp], float, float]:
    prev, nxt = coupon_schedule(bond, settle)
    period_days = (nxt[0] - prev).days
    accrued_days = (settle - prev).days
    return prev, nxt, accrued_days / period_days, (nxt[0] - settle).days / period_days


def accrued_interest(bond: Bond, settle: pd.Timestamp | date) -> float:
    """ACT/ACT (ICMA) accrued interest per ``face``:
    ``AI = face * (c/f) * (settle - last coupon) / (next coupon - last coupon)``."""
    s = pd.Timestamp(settle).normalize()
    _, _, acc, _ = _period_fraction(bond, s)
    return bond.face * bond.coupon / bond.freq * acc


def cashflows(bond: Bond, settle: pd.Timestamp) -> tuple[np.ndarray, np.ndarray, float, list[pd.Timestamp]]:
    """Cash flows, their times in coupon periods ``k-1+w``, AI, dates."""
    _, dates, acc, w = _period_fraction(bond, settle)
    n = len(dates)
    cf = np.full(n, bond.face * bond.coupon / bond.freq)
    cf[-1] += bond.face
    periods = np.arange(n) + w
    ai = bond.face * bond.coupon / bond.freq * acc
    return cf, periods, ai, dates


def price_from_yield(bond: Bond, settle: pd.Timestamp | date, ytm: float) -> dict[str, float]:
    """Dirty/clean price per ``face`` at yield ``ytm`` (decimal, compounded ``freq``)."""
    s = pd.Timestamp(settle).normalize()
    cf, per, ai, _ = cashflows(bond, s)
    dirty = float(np.sum(cf / (1.0 + ytm / bond.freq) ** per))
    return {"dirty": dirty, "clean": dirty - ai, "accrued": ai}


def yield_from_price(bond: Bond, settle: pd.Timestamp | date, clean_price: float) -> float:
    """YTM (decimal) solving ``Dirty(y) = clean + AI`` by Brent's method."""
    s = pd.Timestamp(settle).normalize()
    ai = accrued_interest(bond, s)
    target = clean_price + ai

    def f(y: float) -> float:
        return price_from_yield(bond, s, y)["dirty"] - target

    lo, hi = -0.9 * bond.freq + 1e-6, 5.0
    if f(lo) * f(hi) > 0:
        raise ValueError("no yield reproduces that price")
    return float(optimize.brentq(f, lo, hi, xtol=1e-14, maxiter=500))


def risk_measures(bond: Bond, settle: pd.Timestamp | date, ytm: float) -> dict[str, float]:
    """Macaulay/modified duration, convexity (years^2) and DV01 at ``ytm``."""
    s = pd.Timestamp(settle).normalize()
    cf, per, ai, _ = cashflows(bond, s)
    f = bond.freq
    disc = (1.0 + ytm / f) ** per
    pv = cf / disc
    dirty = pv.sum()
    t = per / f
    mac = float(np.sum(t * pv) / dirty)
    mod = mac / (1.0 + ytm / f)
    conv = float(np.sum(t * (t + 1.0 / f) * pv) / (dirty * (1.0 + ytm / f) ** 2))
    return {
        "macaulay_duration": mac,
        "modified_duration": mod,
        "convexity": conv,
        "dv01": mod * dirty * 1e-4,
        "dirty": float(dirty),
        "clean": float(dirty - ai),
        "accrued": ai,
    }


def _curve_times(bond: Bond, settle: pd.Timestamp) -> tuple[np.ndarray, np.ndarray]:
    cf, _, _, dates = cashflows(bond, settle)
    t = np.array([(d - settle).days for d in dates], dtype=float) / DAYS_PER_YEAR_CURVE
    return cf, t


def _price_on_zero(cf: np.ndarray, t: np.ndarray, zero_fn: Callable[[np.ndarray], np.ndarray],
                   spread: float = 0.0) -> float:
    z = zero_fn(t) / 100.0
    return float(np.sum(cf * np.exp(-(z + spread) * t)))


def curve_price(bond: Bond, settle: pd.Timestamp | date, curve: ZeroCurve) -> dict[str, float]:
    """Curve-implied (fair) dirty and clean price: ``sum CF_k P(t_k)``."""
    s = pd.Timestamp(settle).normalize()
    cf, t = _curve_times(bond, s)
    dirty = float(np.sum(cf * curve.discount(t)))
    ai = accrued_interest(bond, s)
    return {"dirty": dirty, "clean": dirty - ai, "accrued": ai}


def z_spread(bond: Bond, settle: pd.Timestamp | date, curve: ZeroCurve, clean_price: float) -> float:
    """Z-spread (decimal, continuous) over the zero curve that reprices the
    bond to ``clean_price`` (Brent)."""
    s = pd.Timestamp(settle).normalize()
    cf, t = _curve_times(bond, s)
    target = clean_price + accrued_interest(bond, s)

    def f(sp: float) -> float:
        return _price_on_zero(cf, t, curve.zero, sp) - target

    # price is strictly decreasing in the spread: widen the bracket until it
    # contains the root, else fail with a clear message (not scipy's
    # "f(a) and f(b) must have different signs")
    lo, hi = -0.5, 1.0
    while f(hi) > 0 and hi < 50.0:
        hi *= 2.0
    while f(lo) < 0 and lo > -5.0:
        lo *= 2.0
    if f(lo) * f(hi) > 0:
        raise ValueError(f"no Z-spread in [{lo * 1e4:.0f}, {hi * 1e4:.0f}] bp reprices the bond to "
                         f"clean {clean_price:g}; check the price")
    return float(optimize.brentq(f, lo, hi, xtol=1e-14, maxiter=500))


def _tent(t: np.ndarray, keys: Sequence[float], i: int) -> np.ndarray:
    k = np.asarray(keys, dtype=float)
    w = np.zeros_like(t)
    left = k[i - 1] if i > 0 else None
    right = k[i + 1] if i < len(k) - 1 else None
    ki = k[i]
    if left is None:
        w = np.where(t <= ki, 1.0, w)
    else:
        m = (t > left) & (t <= ki)
        w = np.where(m, (t - left) / (ki - left), w)
    if right is None:
        w = np.where(t >= ki, 1.0, w)
    else:
        m = (t > ki) & (t < right)
        w = np.where(m, (right - t) / (right - ki), w)
    return w


def effective_duration(bond: Bond, settle: pd.Timestamp | date, curve: ZeroCurve,
                       spread: float = 0.0, bump_bp: float = 1.0) -> dict[str, float]:
    """Effective duration and convexity for a parallel shift of the zero curve:
    ``D = (P- - P+)/(2 P0 dz)``, ``C = (P- + P+ - 2P0)/(P0 dz^2)``."""
    s = pd.Timestamp(settle).normalize()
    cf, t = _curve_times(bond, s)
    dz = bump_bp * 1e-4
    p0 = _price_on_zero(cf, t, curve.zero, spread)
    pu = _price_on_zero(cf, t, curve.zero, spread + dz)
    pd_ = _price_on_zero(cf, t, curve.zero, spread - dz)
    return {"effective_duration": (pd_ - pu) / (2 * p0 * dz),
            "effective_convexity": (pd_ + pu - 2 * p0) / (p0 * dz * dz)}


def key_rate_durations(bond: Bond, settle: pd.Timestamp | date, curve: ZeroCurve,
                       keys: Sequence[float] = DEFAULT_KEY_TENORS, spread: float = 0.0,
                       bump_bp: float = 1.0) -> pd.DataFrame:
    """Ho (1992) key-rate durations (central differences of a tent bump):
    ``KRD_i = (P(z - dz w_i) - P(z + dz w_i)) / (2 P0 dz)``; ``KRD01_i =
    KRD_i P0 1e-4`` is the dollar value per 100 face."""
    s = pd.Timestamp(settle).normalize()
    cf, t = _curve_times(bond, s)
    dz = bump_bp * 1e-4
    z = curve.zero(t) / 100.0 + spread
    p0 = float(np.sum(cf * np.exp(-z * t)))
    rows: list[dict[str, Any]] = []
    for i, k in enumerate(keys):
        w = _tent(t, keys, i)
        pu = float(np.sum(cf * np.exp(-(z + dz * w) * t)))
        pdn = float(np.sum(cf * np.exp(-(z - dz * w) * t)))
        krd = (pdn - pu) / (2 * p0 * dz)
        rows.append({"tenor": float(k), "krd": krd, "krd01": krd * p0 * 1e-4})
    return pd.DataFrame(rows)
