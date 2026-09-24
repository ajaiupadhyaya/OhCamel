"""Black-Scholes-Merton (continuous yield) and Black-76 pricing, greeks and implied volatility.

Model (Merton 1973, "Theory of Rational Option Pricing"; Black 1976, "The pricing of
commodity contracts")::

    F  = S e^{(r-q)T},  D = e^{-rT}
    d1 = [ln(F/K) + sigma^2 T / 2] / (sigma sqrt T),  d2 = d1 - sigma sqrt T
    V  = D [phi F N(phi d1) - phi K N(phi d2)]          phi = +1 call, -1 put

so BSM on a spot with yield ``q`` and Black-76 on the forward ``F`` with discount
factor ``D`` are the same formula. Greeks follow Haug (2007), *The Complete Guide to
Option Pricing Formulas*, 2nd ed., ch. 2:

* delta  = phi e^{-qT} N(phi d1)
* gamma  = e^{-qT} n(d1) / (S sigma sqrt T)
* vega   = S e^{-qT} n(d1) sqrt T                          (per 1.00 of vol)
* theta  = -S e^{-qT} n(d1) sigma / (2 sqrt T) - phi r K e^{-rT} N(phi d2)
           + phi q S e^{-qT} N(phi d1)                     (dV/dt, per year)
* rho    = phi K T e^{-rT} N(phi d2)                       (per 1.00 of rate)
* vanna  = d delta / d sigma = -e^{-qT} n(d1) d2 / sigma
* volga  = d vega / d sigma  = vega d1 d2 / sigma
* charm  = d delta / dt = phi q e^{-qT} N(phi d1)
           - e^{-qT} n(d1) [2 (r-q) T - d2 sigma sqrt T] / (2 T sigma sqrt T)   (per year)

``theta_day`` is theta / 365 (calendar-day decay, the convention used by most
brokers). All functions are vectorized with numpy broadcasting.

Implied volatility
------------------
:func:`implied_vol_black` inverts the *out-of-the-money equivalent* undiscounted
price: by put-call parity the time value ``u - max(phi (F-K), 0)`` of any option equals
the undiscounted price of the OTM option at the same strike, which is far better
conditioned than an ITM price. Steps:

1. arbitrage bounds: undiscounted price must satisfy ``max(phi(F-K),0) < u < F``
   (call) / ``< K`` (put); otherwise NaN (no volatility reproduces the quote);
2. initial guess from Corrado & Miller (1996), "A note on a simple, accurate
   formula to compute implied standard deviations", J. Banking & Finance 20;
3. safeguarded Newton (Press et al., *Numerical Recipes*, ``rtsafe``) on
   ``ln V(sigma) - ln V*`` -- the log transform linearizes the far wings, as in
   Jaeckel (2015) "Let's be rational" -- falling back to bisection whenever a
   Newton step leaves the running bracket;
4. any element not converged after the vectorized pass is solved with Brent's
   method (Brent 1973) on the bracket.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.optimize import brentq
from scipy.special import ndtr

SQRT_2PI = np.sqrt(2.0 * np.pi)
DAYS_PER_YEAR = 365.0
IV_MAX = 20.0  # 2000% vol: upper end of the search bracket (numerical, not a market input)


def _npdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x * x) / SQRT_2PI


def option_sign(option_type: Any) -> np.ndarray:
    """Map 'C'/'P' (any case, also 'call'/'put'), booleans (True = call) or +/-1 to +1/-1."""
    a = np.asarray(option_type)
    if a.dtype.kind in ("U", "S", "O"):
        first = np.array([str(v).strip().upper()[:1] for v in a.ravel()], dtype="<U1").reshape(a.shape)
        if not np.all(np.isin(first, ["C", "P"])):
            raise ValueError("option type must be 'C' or 'P'")
        return np.where(first == "C", 1.0, -1.0)
    if a.dtype.kind == "b":
        return np.where(a, 1.0, -1.0)
    out = np.sign(a.astype(float))
    if np.any(out == 0):
        raise ValueError("option sign must be +1 (call) or -1 (put)")
    return out


# ------------------------------------------------------------------ pricing
def black76_price(F: Any, K: Any, T: Any, sigma: Any, D: Any, option_type: Any) -> np.ndarray:
    """Black (1976) price ``D [phi F N(phi d1) - phi K N(phi d2)]``.

    At ``T <= 0`` or ``sigma <= 0`` returns the discounted intrinsic value.
    """
    F, K, T, sigma, D = (np.asarray(x, dtype=float) for x in (F, K, T, sigma, D))
    phi = option_sign(option_type)
    F, K, T, sigma, D, phi = np.broadcast_arrays(F, K, T, sigma, D, phi)
    intrinsic = np.maximum(phi * (F - K), 0.0)
    sd = sigma * np.sqrt(np.maximum(T, 0.0))
    live = sd > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = np.where(live, (np.log(F / K) + 0.5 * sd * sd) / np.where(live, sd, 1.0), 0.0)
    d2 = d1 - sd
    val = phi * (F * ndtr(phi * d1) - K * ndtr(phi * d2))
    return D * np.where(live, np.maximum(val, intrinsic), intrinsic)


def forward_and_discount(S: Any, T: Any, r: Any, q: Any) -> tuple[np.ndarray, np.ndarray]:
    """``F = S e^{(r-q)T}``, ``D = e^{-rT}`` (continuous compounding)."""
    S, T, r, q = (np.asarray(x, dtype=float) for x in (S, T, r, q))
    return S * np.exp((r - q) * T), np.exp(-r * T)


def bsm_price(S: Any, K: Any, T: Any, sigma: Any, r: Any, q: Any, option_type: Any) -> np.ndarray:
    """Black-Scholes-Merton price with continuous dividend/borrow yield ``q``."""
    F, D = forward_and_discount(S, T, r, q)
    return black76_price(F, K, T, sigma, D, option_type)


def bsm_greeks(S: Any, K: Any, T: Any, sigma: Any, r: Any, q: Any, option_type: Any) -> dict[str, np.ndarray]:
    """Price and greeks (see module docstring for the formulas).

    Returns a dict of arrays: price, delta, gamma, vega, theta (per year),
    theta_day (per calendar day), rho, vanna, volga, charm (per year),
    charm_day, d1, d2. Requires ``T > 0`` and ``sigma > 0`` (NaN otherwise).
    """
    S, K, T, sigma, r, q = (np.asarray(x, dtype=float) for x in (S, K, T, sigma, r, q))
    phi = option_sign(option_type)
    S, K, T, sigma, r, q, phi = np.broadcast_arrays(S, K, T, sigma, r, q, phi)
    ok = (T > 0) & (sigma > 0) & (S > 0) & (K > 0)
    T_ = np.where(ok, T, np.nan)
    sig = np.where(ok, sigma, np.nan)
    sqT = np.sqrt(T_)
    sd = sig * sqT
    dq, dr = np.exp(-q * T_), np.exp(-r * T_)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + (r - q + 0.5 * sig * sig) * T_) / sd
    d2 = d1 - sd
    nd1 = _npdf(d1)
    Nd1, Nd2 = ndtr(phi * d1), ndtr(phi * d2)
    price = phi * (S * dq * Nd1 - K * dr * Nd2)
    delta = phi * dq * Nd1
    gamma = dq * nd1 / (S * sd)
    vega = S * dq * nd1 * sqT
    theta = -S * dq * nd1 * sig / (2.0 * sqT) - phi * r * K * dr * Nd2 + phi * q * S * dq * Nd1
    rho = phi * K * T_ * dr * Nd2
    vanna = -dq * nd1 * d2 / sig
    volga = vega * d1 * d2 / sig
    charm = phi * q * dq * Nd1 - dq * nd1 * (2.0 * (r - q) * T_ - d2 * sd) / (2.0 * T_ * sd)
    return {
        "price": price, "delta": delta, "gamma": gamma, "vega": vega,
        "theta": theta, "theta_day": theta / DAYS_PER_YEAR, "rho": rho,
        "vanna": vanna, "volga": volga, "charm": charm, "charm_day": charm / DAYS_PER_YEAR,
        "d1": d1, "d2": d2,
    }


# ------------------------------------------------------------------ implied volatility
def _otm_undiscounted(F: np.ndarray, K: np.ndarray, sd: np.ndarray, phi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Undiscounted Black price and d(price)/d(sd) at total std-dev ``sd``."""
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = np.log(F / K) / sd + 0.5 * sd
    d2 = d1 - sd
    p = phi * (F * ndtr(phi * d1) - K * ndtr(phi * d2))
    return np.maximum(p, 0.0), F * _npdf(d1)


def corrado_miller_guess(c: np.ndarray, F: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Corrado & Miller (1996) total std-dev guess from an undiscounted CALL price
    ``c`` on forward ``F`` (their formula with ``S -> F``, ``X -> K``)::

        sigma sqrt T ~ sqrt(2 pi)/(F+K) [c - (F-K)/2 + sqrt((c-(F-K)/2)^2 - (F-K)^2/pi)]

    A negative radicand is clipped at zero (their recommended patch); non-positive
    results fall back to the Brenner-Subrahmanyam (1988) ATM value ``sqrt(2 pi) c / F``.
    """
    h = c - 0.5 * (F - K)
    rad = np.maximum(h * h - (F - K) ** 2 / np.pi, 0.0)
    g = SQRT_2PI / (F + K) * (h + np.sqrt(rad))
    bs = SQRT_2PI * c / F
    return np.where(np.isfinite(g) & (g > 1e-8), g, np.where(bs > 1e-8, bs, 0.2))


def implied_vol_black(
    price: Any, F: Any, K: Any, T: Any, D: Any, option_type: Any,
    tol: float = 1e-10, max_iter: int = 60,
) -> np.ndarray:
    """Black-76 implied volatility of discounted prices (vectorized over a chain).

    NaN where the price is outside the no-arbitrage bounds
    ``D max(phi(F-K),0) < price < D F`` (call) / ``D K`` (put), or inputs are invalid.
    """
    price, F, K, T, D = (np.asarray(x, dtype=float) for x in (price, F, K, T, D))
    phi = option_sign(option_type)
    price, F, K, T, D, phi = np.broadcast_arrays(price, F, K, T, D, phi)
    shape = price.shape
    price, F, K, T, D, phi = (np.ravel(x).astype(float) for x in (price, F, K, T, D, phi))
    out = np.full(price.shape, np.nan)

    valid = np.isfinite(price) & (F > 0) & (K > 0) & (T > 0) & (D > 0) & np.isfinite(D)
    u = np.where(valid, price / np.where(valid, D, 1.0), np.nan)
    intrinsic = np.maximum(phi * (F - K), 0.0)
    upper = np.where(phi > 0, F, K)
    tv = u - intrinsic                       # = undiscounted OTM price at this strike
    otm_phi = np.where(K >= F, 1.0, -1.0)    # OTM option type at this strike
    otm_upper = np.where(otm_phi > 0, F, K)
    valid &= (tv > 0) & (u < upper) & (tv < otm_upper)
    idx = np.flatnonzero(valid)
    if idx.size == 0:
        return out.reshape(shape)

    f, k, t, p, ph = F[idx], K[idx], T[idx], tv[idx], otm_phi[idx]
    call_und = p + np.maximum(f - k, 0.0)    # undiscounted call at this strike (parity)
    x = corrado_miller_guess(call_und, f, k)
    lo = np.zeros_like(x)
    hi = np.full_like(x, IV_MAX) * np.sqrt(t)
    x = np.clip(x, 1e-6, hi * 0.5)
    lp = np.log(p)
    done = np.zeros(x.shape, dtype=bool)
    for _ in range(max_iter):
        val, dv = _otm_undiscounted(f, k, x, ph)
        with np.errstate(divide="ignore", invalid="ignore"):
            g = np.log(val) - lp
        g = np.where(np.isfinite(g), g, -np.inf)  # val == 0 underflow: sd far too small
        done |= np.abs(g) < 1e-14
        active = ~done
        above = g > 0
        hi = np.where(active & above, np.minimum(hi, x), hi)
        lo = np.where(active & ~above, np.maximum(lo, x), lo)
        with np.errstate(divide="ignore", invalid="ignore"):
            step = g * val / dv                  # Newton on ln V: dlnV/dsd = V'/V
        x_new = x - step
        bad = ~np.isfinite(x_new) | (x_new < lo) | (x_new > hi)
        x_new = np.where(bad, 0.5 * (lo + hi), x_new)
        conv = (~bad) & (np.abs(x_new - x) <= tol * np.maximum(x, 1e-6))
        x = np.where(active, x_new, x)
        done |= active & conv
        if done.all():
            break

    # Brent fallback for anything not converged
    for j in np.flatnonzero(~done):
        def fn(s: float, j: int = j) -> float:
            v, _ = _otm_undiscounted(f[j:j + 1], k[j:j + 1], np.array([s]), ph[j:j + 1])
            return float(v[0] - p[j])
        a, b = 1e-12, float(IV_MAX * np.sqrt(t[j]))
        try:
            if fn(a) < 0 < fn(b):
                x[j] = brentq(fn, a, b, xtol=1e-14, rtol=1e-12, maxiter=200)
            else:
                x[j] = np.nan
        except (ValueError, RuntimeError):
            x[j] = np.nan
    out[idx] = x / np.sqrt(t)
    return out.reshape(shape)


def implied_vol(price: Any, S: Any, K: Any, T: Any, r: Any, q: Any, option_type: Any, **kw: Any) -> np.ndarray:
    """BSM implied volatility with continuous yield ``q`` (via Black-76 on ``F, D``)."""
    F, D = forward_and_discount(S, T, r, q)
    return implied_vol_black(price, F, K, T, D, option_type, **kw)
