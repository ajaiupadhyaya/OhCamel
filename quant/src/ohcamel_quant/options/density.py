"""Risk-neutral density of ``S_T`` from an implied-volatility smile.

Breeden & Litzenberger (1978), "Prices of state-contingent claims implicit in
option prices", J. Business 51::

    q(K) = (1 / D) d^2 C(K) / dK^2,          P(S_T <= K) = 1 + (1 / D) dC / dK

where ``C(K)`` is the discounted call price. Call prices come from Black-76 on a
fine strike grid using the fitted smile ``sigma(k) = sqrt(w(k) / T)``
(interpolating quotes through a smooth, arbitrage-checked smile rather than
differencing noisy quotes, cf. Shimko 1993; Ait-Sahalia & Lo 1998). Derivatives
are central finite differences on a uniform grid in ``k = ln(K/F)``.

Two sanity checks are reported, never forced: ``integral`` (should be ~1: mass
outside the grid or butterfly arbitrage makes it differ) and ``mean`` (should be
~F: the martingale condition). Probabilities use the first-derivative CDF (made
monotone and clipped to [0, 1]); quantiles invert it by linear interpolation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from .bsm import black76_price

DEFAULT_QUANTILES = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)


@dataclass
class RNDensity:
    strikes: np.ndarray
    density: np.ndarray        # q(K), per USD
    cdf: np.ndarray            # P(S_T <= K)
    forward: float
    discount: float
    T: float
    integral: float
    mean: float
    negative_mass: float       # integral of max(-q, 0): > 0 signals butterfly arbitrage
    k_range: tuple[float, float] = (float("nan"), float("nan"))  # log-moneyness grid ends

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
    less than ``tail_tol`` probability.

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
    k_lo, k_hi = -n_sd * s, n_sd * s
    for _ in range(0 if k_range is not None else 12):
        lo_ok = cdf_at(F * np.exp(k_lo)) < tail_tol
        hi_ok = 1.0 - cdf_at(F * np.exp(k_hi)) < tail_tol
        if lo_ok and hi_ok:
            break
        k_lo = k_lo if lo_ok else 1.5 * k_lo
        k_hi = k_hi if hi_ok else 1.5 * k_hi
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
    cdf = np.clip(np.maximum.accumulate(np.clip(cdf, 0.0, 1.0)), 0.0, 1.0)
    integral = float(np.trapezoid(qd, Ki))
    mean = float(np.trapezoid(Ki * qd, Ki))
    neg = float(np.trapezoid(np.clip(-qd, 0.0, None), Ki))
    return RNDensity(strikes=Ki, density=qd, cdf=cdf, forward=F, discount=D, T=T,
                     integral=integral, mean=mean, negative_mass=neg,
                     k_range=(float(k_lo), float(k_hi)))
