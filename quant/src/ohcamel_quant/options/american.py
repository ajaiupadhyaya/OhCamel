"""American option prices and implied volatility: a CRR binomial tree with escrowed dividends.

Model (Cox, Ross & Rubinstein 1979, "Option pricing: a simplified approach", J. Financial
Economics 7; Hull 2018, *Options, Futures, and Other Derivatives*, 10th ed., ch. 21)::

    S* = S - sum_{0 < t_d < T} D_d e^{-r t_d}            escrowed spot (discrete cash dividends)
    u = e^{sigma sqrt(dt)},  d = 1/u,  p = (e^{(r-q) dt} - d) / (u - d),  dt = T / N

``S*`` follows the tree with continuous yield ``q``; at a node at time ``t_i`` the holder may
exercise against the cum-dividend price ``S*_node + sum_{t_i <= t_d < T} D_d e^{-r (t_d - t_i)}``
(a dividend paid at ``t_i`` itself still counts: exercising just before the ex-date is allowed).
Dividends at or before now, or at or after expiry, do not enter.

Accuracy: the plain tree's error oscillates with N (5e-3 at the money on a 100 underlying at 400
steps). The last step is replaced by the Black-Scholes value (BBS) and two trees are combined by
Richardson extrapolation, ``2 V_N - V_{N/2}`` (BBSR; Broadie & Detemple 1996, "American option
valuation: new bounds, approximations, and a comparison of existing methods", Rev. Financial
Studies 9), then floored at immediate exercise. At N = 400 this is within about 1e-3 of the limit
on a 100 underlying and smooth in sigma, which Brent's method needs.

Implied volatility is Brent's method (Brent 1973) on ``price(sigma) - quote`` (see
:func:`ohcamel_quant.kernels.american_iv`). The heavy loops run in the Rust kernel
(``native/kernels/src/american.rs``) with the NumPy reference as its definition.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import numpy as np

from .. import kernels

#: The tree size the package prices and inverts with.
STEPS = 400


def iv_threads() -> int:
    """Worker threads for a chain's inversions (results do not depend on it)."""
    return max(1, min(4, os.cpu_count() or 1))


def _divs(dividends: Sequence[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    d = np.asarray(list(dividends), dtype=float).reshape(-1, 2) if len(dividends) else np.empty((0, 2))
    if not (np.all(np.isfinite(d)) and np.all(d[:, 1] >= 0)):
        raise ValueError("each dividend must be a finite (time in years, non-negative cash amount) pair")
    return np.ascontiguousarray(d[:, 0]), np.ascontiguousarray(d[:, 1])


def prices(S: Any, K: Any, T: Any, r: Any, q: Any, sigma: Any, is_call: Any,
           dividends: Sequence[tuple[float, float]] = (), steps: int = STEPS, threads: int | None = None) -> np.ndarray:
    """Vectorized :func:`price` (broadcast over contracts; NaN for an invalid contract)."""
    dt, da = _divs(dividends)
    return kernels.american_price(S, K, T, r, q, sigma, is_call, dt, da, steps,
                                  iv_threads() if threads is None else threads)


def implied_vols(price: Any, S: Any, K: Any, T: Any, r: Any, q: Any, is_call: Any,
                 dividends: Sequence[tuple[float, float]] = (), steps: int = STEPS,
                 threads: int | None = None) -> np.ndarray:
    """Vectorized :func:`implied_vol` (NaN where no volatility reproduces the price)."""
    dt, da = _divs(dividends)
    return kernels.american_iv(price, S, K, T, r, q, is_call, dt, da, steps,
                               iv_threads() if threads is None else threads)


def price(S: float, K: float, T: float, r: float, sigma: float, *, is_call: bool,
          dividends: Sequence[tuple[float, float]] = (), q: float = 0.0, steps: int = STEPS) -> float:
    """American option value: CRR tree with escrowed discrete ``dividends`` ((time in years, cash)
    pairs) and continuous yield ``q``; BBS last step, Richardson-extrapolated against ``steps/2``.
    NaN for non-positive S, K, T or sigma."""
    return float(prices(S, K, T, r, q, sigma, is_call, dividends, steps, threads=1)[()])


def implied_vol(price: float, S: float, K: float, T: float, r: float, *, is_call: bool,
                dividends: Sequence[tuple[float, float]] = (), q: float = 0.0) -> float:
    """Volatility at which :func:`price` (``STEPS`` steps) equals ``price``, by Brent's method; NaN
    where none does (at or below immediate exercise, at or above S for a call or K for a put)."""
    return float(implied_vols(price, S, K, T, r, q, is_call, dividends, STEPS, threads=1)[()])
