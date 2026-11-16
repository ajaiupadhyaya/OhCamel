"""Risk-neutral density of ``S_T`` from an implied-volatility smile.

Breeden & Litzenberger (1978), "Prices of state-contingent claims implicit in
option prices", J. Business 51::

    q(K) = (1 / D) d^2 C(K) / dK^2,          P(S_T <= K) = 1 + (1 / D) dC / dK

where ``C(K)`` is the discounted call price. Call prices come from Black-76 on a
fine strike grid using the fitted smile ``sigma(k) = sqrt(w(k) / T)``
(interpolating quotes through a smooth, arbitrage-checked smile rather than
differencing noisy quotes, cf. Shimko 1993; Ait-Sahalia & Lo 1998). Derivatives
are central finite differences on a uniform grid in ``k = ln(K/F)``.

The grid never extends past ``|k| = K_MAX``, the range on which a fitted SVI
slice is certified free of butterfly arbitrage (:mod:`.svi`). The probability
beyond each end is reported (``tail_mass_left = P(S_T < K_lo)``,
``tail_mass_right = P(S_T > K_hi)``, both from the first-derivative CDF). The
first-derivative CDF is used as computed: one that falls anywhere, or leaves
[0, 1], is not a distribution, and :class:`DensityError` says so instead of the
CDF being clipped into one. Quantiles invert it by linear interpolation.

``integral + tail_mass_left + tail_mass_right`` is information, not a check: ``q``
is the derivative of the same CDF whose end values are the tails, so the sum
telescopes to one (to O(h^2)) for ANY smile, arbitrageable or not. So are the
tail masses: a tail below the CDF tolerance has already raised. The checks that
can fail are :func:`density_checks`: no negative mass (butterfly arbitrage), the
mean against the forward (the martingale condition), and a numerical cross-check
of the finite-difference density against the smile's closed-form density on the
same grid (it catches a broken stencil or an unresolved grid; it cannot detect
arbitrage, since both are the same function of the same smile). Reported, never
forced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from .bsm import black76_price
from .svi import K_MAX

#: How far the first-derivative CDF may fall, or leave [0, 1], before it is not a distribution
#: (with the grid step h: the central difference of C carries a relative bias of h^2 / 6 -- the
#: CDF is -h^2 / 6 at a deep in-the-money end -- so the tolerance is max(CDF_TOL, h^2)).
CDF_TOL = 1e-8


NEGATIVE_MASS_TOL = 1e-6   # integral of max(-q, 0) above this: butterfly arbitrage
ANALYTIC_TOL = 1e-3        # numerical cross-check: |grid integral of finite-difference q - closed form's|
MEAN_TOL = 0.005           # |mean / F - 1|


class DensityError(ValueError):
    """The smile's first-derivative CDF is not a distribution function (butterfly arbitrage)."""


DEFAULT_QUANTILES = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)


@dataclass
class RNDensity:
    strikes: np.ndarray
    density: np.ndarray        # q(K), per USD
    cdf: np.ndarray            # P(S_T <= K)
    forward: float
    discount: float
    T: float
    integral: float            # of q over the grid
    mean: float                # of K q over the grid
    negative_mass: float       # integral of max(-q, 0): > 0 signals butterfly arbitrage
    k_range: tuple[float, float] = (float("nan"), float("nan"))  # log-moneyness grid ends
    tail_mass_left: float = float("nan")    # P(S_T < first strike): mass below the grid
    tail_mass_right: float = float("nan")   # P(S_T > last strike): mass above the grid

    def prob_below(self, level: Any) -> np.ndarray:
        """``P(S_T < level)``."""
        return np.interp(np.asarray(level, float), self.strikes, self.cdf, left=0.0, right=1.0)

    def prob_move(self, spot: float, x: Any) -> np.ndarray:
        """``P(|S_T / spot - 1| > x)``."""
        x = np.asarray(x, float)
        return self.prob_below(spot * (1.0 - x)) + 1.0 - self.prob_below(spot * (1.0 + x))

    def quantiles(self, probs: Any = DEFAULT_QUANTILES) -> np.ndarray:
        cdf, idx = np.unique(self.cdf, return_index=True)
        return np.interp(np.asarray(probs, float), cdf, self.strikes[idx])

    def moments(self) -> dict[str, float]:
        """Mean, stdev, skewness and excess kurtosis of ``S_T / F - 1`` under Q
        (density clipped at zero and renormalized for the moments)."""
        x = self.strikes / self.forward - 1.0
        q = np.clip(self.density, 0.0, None)
        z = np.trapezoid(q, self.strikes)
        if z <= 0:
            return {"mean": float("nan"), "std": float("nan"), "skew": float("nan"), "excess_kurtosis": float("nan")}
        q = q / z
        m = np.trapezoid(x * q, self.strikes)
        v = np.trapezoid((x - m) ** 2 * q, self.strikes)
        sd = np.sqrt(v)
        sk = np.trapezoid((x - m) ** 3 * q, self.strikes) / sd**3
        ku = np.trapezoid((x - m) ** 4 * q, self.strikes) / sd**4 - 3.0
        return {"mean": float(m), "std": float(sd), "skew": float(sk), "excess_kurtosis": float(ku)}


def breeden_litzenberger(
    F: float, D: float, T: float, total_variance: Callable[[np.ndarray], np.ndarray],
    n: int = 4001, n_sd: float = 8.0, tail_tol: float = 1e-6,
    k_range: tuple[float, float] | None = None,
) -> RNDensity:
    """Density on a uniform log-strike grid of ``n`` points (derivatives in ``K`` by the
    chain rule ``C_K = C_k / K``, ``C_KK = (C_kk - C_k) / K^2``), starting at
    ``F exp(+-n_sd sqrt(w(0)))`` and widened until each tail beyond the grid holds
    less than ``tail_tol`` probability, but never past ``|k| = K_MAX``: what lies
    beyond is reported as ``tail_mass_left`` / ``tail_mass_right``. Raises
    :class:`DensityError` when the CDF falls or leaves [0, 1] by more than ``max(CDF_TOL, h^2)``.

    ``total_variance(k)`` is ``w(k) = sigma^2(k) T`` in ``k = ln(K/F)`` (e.g.
    ``SVIParams.w``). ``n_sd``/``tail_tol`` set the numerical-integration range only.
    ``k_range`` fixes the log-moneyness grid ends instead (e.g. to evaluate a second
    density on exactly the strikes of a first one, ``k_range=first.k_range``).
    """
    if T <= 0 or F <= 0 or D <= 0:
        raise ValueError("need T > 0, F > 0, D > 0")
    w0 = float(total_variance(np.array([0.0]))[0])
    if not np.isfinite(w0) or w0 <= 0:
        raise ValueError("smile has non-positive ATM variance")
    s = np.sqrt(w0)

    def calls(K: np.ndarray) -> np.ndarray:
        w = np.maximum(np.asarray(total_variance(np.log(K / F)), float), 1e-14)
        return black76_price(F, K, T, np.sqrt(w / T), D, "C")

    def cdf_at(K: float) -> float:
        h = 1e-4 * K
        c = calls(np.array([K - h, K + h]))
        return 1.0 + (c[1] - c[0]) / (2.0 * h * D)

    # widen the range until both tails hold less than `tail_tol` probability (skewed smiles
    # put much more mass in the left tail than a lognormal with the ATM vol)
    k_lo, k_hi = max(-n_sd * s, -K_MAX), min(n_sd * s, K_MAX)
    for _ in range(0 if k_range is not None else 12):
        lo_ok = k_lo <= -K_MAX or cdf_at(F * np.exp(k_lo)) < tail_tol
        hi_ok = k_hi >= K_MAX or 1.0 - cdf_at(F * np.exp(k_hi)) < tail_tol
        if lo_ok and hi_ok:
            break
        k_lo = k_lo if lo_ok else max(1.5 * k_lo, -K_MAX)
        k_hi = k_hi if hi_ok else min(1.5 * k_hi, K_MAX)
    if k_range is not None:
        k_lo, k_hi = (float(x) for x in k_range)
        if not (np.isfinite(k_lo) and np.isfinite(k_hi) and k_lo < k_hi):
            raise ValueError("k_range must be finite with k_lo < k_hi")
    k = np.linspace(k_lo, k_hi, n)
    K = F * np.exp(k)
    C = calls(K)
    h = k[1] - k[0]
    Ck = (C[2:] - C[:-2]) / (2.0 * h)
    Ckk = (C[2:] - 2.0 * C[1:-1] + C[:-2]) / h**2
    Ki = K[1:-1]
    dC = Ck / Ki                      # chain rule for K = F e^k
    d2C = (Ckk - Ck) / Ki**2
    qd, cdf = d2C / D, 1.0 + dC / D
    drop = np.maximum.accumulate(cdf) - cdf        # how far the CDF has fallen below its running peak
    tol = max(CDF_TOL, h * h)
    if drop.max() > tol or cdf[0] < -tol or cdf[-1] > 1.0 + tol:
        i = int(np.argmax(drop))
        raise DensityError(
            f"the smile's CDF is not monotone in [0, 1] on k in [{k_lo:.3g}, {k_hi:.3g}] (butterfly arbitrage): "
            f"it falls by {drop[i]:.2e} (to K = {Ki[i]:.6g}), starts at {cdf[0]:.3e}, ends at {cdf[-1]:.6f}")
    integral = float(np.trapezoid(qd, Ki))
    mean = float(np.trapezoid(Ki * qd, Ki))
    neg = float(np.trapezoid(np.clip(-qd, 0.0, None), Ki))
    return RNDensity(strikes=Ki, density=qd, cdf=cdf, forward=F, discount=D, T=T,
                     integral=integral, mean=mean, negative_mass=neg,
                     k_range=(float(k_lo), float(k_hi)),
                     tail_mass_left=float(cdf[0]), tail_mass_right=float(1.0 - cdf[-1]))


def density_checks(d: RNDensity, analytic: np.ndarray) -> dict[str, Any]:
    """The checks that can fail, with ``analytic`` an independent density on ``d.strikes`` (per USD;
    for an SVI smile ``svi.density_k(p, ln(K/F)) / K``, the closed form in Gatheral & Jacquier 2014):

    * ``negative_mass``: ``d.negative_mass <= NEGATIVE_MASS_TOL`` (butterfly arbitrage);
    * ``analytic``: a numerical cross-check -- the grid integrals of the finite-difference ``d.density``
      and of ``analytic`` agree to ``ANALYTIC_TOL``. Both are d2C/dK2 of the same smile, so this catches
      a broken stencil or a grid that does not resolve the smile; it cannot detect arbitrage;
    * ``mean``: ``|mean / F - 1| <= MEAN_TOL`` (the martingale condition).

    A non-monotone CDF, or a tail mass below the CDF tolerance, never gets here:
    :func:`breeden_litzenberger` raises. ``integral_plus_tails`` and the tail masses are information
    only (module docstring)."""
    an = float(np.trapezoid(np.asarray(analytic, float), d.strikes))
    return {
        "integral_plus_tails": d.integral + d.tail_mass_left + d.tail_mass_right,
        "analytic_integral": an,
        "passed": {
            "negative_mass": bool(d.negative_mass <= NEGATIVE_MASS_TOL),
            "analytic": bool(abs(d.integral - an) <= ANALYTIC_TOL),
            "mean": bool(abs(d.mean / d.forward - 1.0) <= MEAN_TOL),
        },
    }
