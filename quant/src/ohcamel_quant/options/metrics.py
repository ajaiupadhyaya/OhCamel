"""Smile and term-structure metrics: ATM vol, 25-delta risk reversal / butterfly,
skew slope, model-free implied variance (Cboe VIX methodology), implied moves.

* **ATM**: at-the-money-forward, ``k = ln(K/F) = 0``; ``sigma_ATM = sqrt(w(0)/T)``.
* **25-delta RR / BF** (market convention, e.g. Clark 2011 *Foreign Exchange
  Option Pricing*, ch. 3; equity desks quote the same with BSM spot delta)::

      RR25 = sigma(Delta_C = +0.25) - sigma(Delta_P = -0.25)
      BF25 = (sigma(Delta_C = +0.25) + sigma(Delta_P = -0.25)) / 2 - sigma_ATM

  with spot delta ``Delta = phi e^{-qT} N(phi d1)``, ``d1 = (-k + w/2)/sqrt(w)`` and
  ``w = w(k)`` from the fitted smile (a fixed point in ``k``, solved by root-finding).
* **Skew slope**: ``d sigma / dk`` at ``k = 0`` = ``w'(0) / (2 sqrt(w(0) T))``.
* **Model-free implied variance** (Demeterfi, Derman, Kamal & Zou 1999, "More
  than you ever wanted to know about volatility swaps"; Britten-Jones & Neuberger
  2000; Cboe *VIX Mathematics Methodology* white paper)::

      sigma^2 = (2/T) sum_i (Delta K_i / K_i^2) e^{RT} Q(K_i) - (1/T) (F/K_0 - 1)^2

  ``K_0`` = first listed strike at or below ``F``; ``Q`` = OTM mid (puts below
  ``K_0``, calls above, the call/put average at ``K_0``); zero-bid options are
  skipped and the strip stops after two consecutive zero bids;
  ``Delta K_i = (K_{i+1} - K_{i-1})/2`` (one-sided at the ends). ``e^{RT} = 1/D``
  and ``F`` come from put-call parity (Cboe uses Treasury rates; stated).
* **30-day constant maturity** (same white paper)::

      sigma_30^2 = [T1 s1^2 (T2 - T30)/(T2 - T1) + T2 s2^2 (T30 - T1)/(T2 - T1)] / T30

  reported as ``100 sigma_30`` -- a "VIX-style" index for any optionable underlying.
* **Implied move** to expiry: ATM straddle (strike nearest F) divided by spot.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.optimize import brentq
from scipy.special import ndtr

from .svi import SVIParams


def atm_vol(p: SVIParams, T: float) -> float:
    w0 = float(p.w(0.0))
    return float(np.sqrt(w0 / T)) if w0 > 0 else float("nan")


def skew_slope(p: SVIParams, T: float) -> float:
    """``d sigma_BS / dk`` at ``k = 0`` (vol per unit of log-moneyness)."""
    w0 = float(p.w(0.0))
    return float(p.dw(0.0)) / (2.0 * np.sqrt(w0 * T)) if w0 > 0 else float("nan")


def atm_vol_market(k: np.ndarray, iv: np.ndarray) -> float:
    """Linear interpolation of quoted OTM implied vols at ``k = 0`` (NaN if 0 is not bracketed)."""
    k, iv = np.asarray(k, float), np.asarray(iv, float)
    ok = np.isfinite(k) & np.isfinite(iv)
    k, iv = k[ok], iv[ok]
    if k.size < 2 or k.min() > 0 or k.max() < 0:
        return float("nan")
    o = np.argsort(k)
    return float(np.interp(0.0, k[o], iv[o]))


def _spot_delta(p: SVIParams, k: np.ndarray, q: float, T: float, phi: float) -> np.ndarray:
    w = np.maximum(p.w(k), 1e-16)
    d1 = (-k + 0.5 * w) / np.sqrt(w)
    return phi * np.exp(-q * T) * ndtr(phi * d1)


def delta_strike(p: SVIParams, T: float, q: float, target: float, phi: float) -> float:
    """Log-moneyness ``k`` at which the smile's spot delta equals ``target``
    (``phi = +1`` call with target in (0, e^{-qT}); ``-1`` put with target in (-e^{-qT}, 0)).
    Among multiple roots the one nearest ATM is returned; NaN if none."""
    s0 = np.sqrt(max(float(p.w(0.0)), 1e-12))
    grid = np.linspace(-8.0 * s0, 8.0 * s0, 801)
    f = _spot_delta(p, grid, q, T, phi) - target
    idx = np.flatnonzero(np.sign(f[:-1]) * np.sign(f[1:]) < 0)
    if idx.size == 0:
        return float("nan")
    i = idx[np.argmin(np.abs(grid[idx]))]
    return float(brentq(lambda x: float(_spot_delta(p, np.array([x]), q, T, phi)[0] - target),
                        grid[i], grid[i + 1], xtol=1e-12))


def risk_reversal_butterfly(p: SVIParams, T: float, F: float, q: float, delta: float = 0.25) -> dict[str, float]:
    """RR and BF at ``delta`` (e.g. 0.25) from the fitted smile; strikes reported in price units."""
    kc = delta_strike(p, T, q, delta, +1.0)
    kp = delta_strike(p, T, q, -delta, -1.0)
    ivc = float(p.implied_vol(kc, T)) if np.isfinite(kc) else float("nan")
    ivp = float(p.implied_vol(kp, T)) if np.isfinite(kp) else float("nan")
    atm = atm_vol(p, T)
    return {
        "delta": delta, "k_call": kc, "k_put": kp, "strike_call": F * np.exp(kc), "strike_put": F * np.exp(kp),
        "iv_call": ivc, "iv_put": ivp, "iv_atm": atm,
        "risk_reversal": ivc - ivp, "butterfly": 0.5 * (ivc + ivp) - atm,
    }


def model_free_variance(
    strikes: Any, call_bid: Any, call_ask: Any, put_bid: Any, put_ask: Any, F: float, D: float, T: float,
) -> dict[str, Any]:
    """Cboe VIX-methodology variance for one expiry (see module docstring).

    Inputs are aligned per strike (NaN where a side is not listed). Returns
    ``sigma2`` (annualized variance), ``K0``, the strikes used and their
    contributions ``(2/T) dK/K^2 e^{RT} Q``.
    """
    K = np.asarray(strikes, float)
    o = np.argsort(K)
    K = K[o]
    cb, ca, pb, pa = (np.asarray(x, float)[o] for x in (call_bid, call_ask, put_bid, put_ask))
    if T <= 0 or not np.isfinite(F) or not np.isfinite(D):
        raise ValueError("model-free variance needs T > 0 and finite F, D")
    below = np.flatnonzero(K <= F)
    if below.size == 0 or below[-1] == len(K) - 1:
        raise ValueError("forward is outside the listed strike range")
    i0 = int(below[-1])
    K0 = float(K[i0])
    cm, pm = (cb + ca) / 2.0, (pb + pa) / 2.0
    used: list[int] = []
    qv: dict[int, float] = {}
    # K0: average of call and put mids
    at = [x for x, b in ((cm[i0], cb[i0]), (pm[i0], pb[i0])) if np.isfinite(x) and np.isfinite(b) and b > 0]
    if at:
        used.append(i0)
        qv[i0] = float(np.mean(at))
    for direction, mids, bids in ((-1, pm, pb), (+1, cm, cb)):
        zeros = 0
        i = i0 + direction
        while 0 <= i < len(K):
            b = bids[i]
            if not np.isfinite(b) or b <= 0:
                zeros += 1
                if zeros >= 2:
                    break
            else:
                zeros = 0
                if np.isfinite(mids[i]) and mids[i] > 0:
                    used.append(i)
                    qv[i] = float(mids[i])
            i += direction
    used = sorted(set(used))
    if len(used) < 3:
        raise ValueError(f"only {len(used)} usable strikes for the model-free variance strip")
    Ku = K[used]
    dK = np.empty_like(Ku)
    dK[1:-1] = (Ku[2:] - Ku[:-2]) / 2.0
    dK[0] = Ku[1] - Ku[0]
    dK[-1] = Ku[-1] - Ku[-2]
    Q = np.array([qv[i] for i in used])
    contrib = (2.0 / T) * dK / Ku**2 * Q / D
    sigma2 = float(contrib.sum() - (F / K0 - 1.0) ** 2 / T)
    return {"sigma2": sigma2, "vol": float(np.sqrt(sigma2)) if sigma2 > 0 else float("nan"), "K0": K0,
            "n_strikes": len(used), "k_min": float(Ku[0]), "k_max": float(Ku[-1]),
            "strikes": Ku.tolist(), "contributions": contrib.tolist()}


def constant_maturity_variance(
    terms: list[tuple[float, float]], target_T: float = 30.0 / 365.0, min_near_T: float = 7.0 / 365.0,
) -> dict[str, Any]:
    """Interpolate annualized variances ``[(T, sigma^2), ...]`` to ``target_T`` in total
    variance (Cboe formula). Prefers the bracketing pair (near term >= ``min_near_T``);
    otherwise extrapolates from the two nearest terms on one side (flagged)."""
    ts = sorted((T, v) for T, v in terms if np.isfinite(v) and v > 0 and T >= min_near_T)
    if len(ts) < 2:
        if len(ts) == 1 and abs(ts[0][0] - target_T) < 1e-9:
            T1, v1 = ts[0]
            return {"sigma2": v1, "index": 100 * np.sqrt(v1), "T1": T1, "T2": T1, "extrapolated": False}
        raise ValueError("need two expiries to build a constant-maturity variance")
    lower = [t for t in ts if t[0] <= target_T]
    upper = [t for t in ts if t[0] > target_T]
    extrap = False
    if lower and upper:
        (T1, v1), (T2, v2) = lower[-1], upper[0]
    elif upper:
        (T1, v1), (T2, v2) = upper[0], upper[1]
        extrap = True
    else:
        (T1, v1), (T2, v2) = lower[-2], lower[-1]
        extrap = True
    w = (T1 * v1 * (T2 - target_T) + T2 * v2 * (target_T - T1)) / (T2 - T1)
    s2 = w / target_T
    return {"sigma2": float(s2), "index": float(100.0 * np.sqrt(s2)) if s2 > 0 else float("nan"),
            "T1": T1, "T2": T2, "extrapolated": extrap, "target_days": target_T * 365.0}


def implied_move(strikes: Any, call_mid: Any, put_mid: Any, F: float, spot: float) -> dict[str, float]:
    """ATM straddle (strike nearest ``F`` with both mids) as a fraction of spot.

    Under a normal approximation ``E|S_T - F| ~ sigma_ATM sqrt(T) F sqrt(2/pi)``, so the
    straddle is ~0.8 standard deviations of the terminal price."""
    K, c, p = (np.asarray(x, float) for x in (strikes, call_mid, put_mid))
    ok = np.isfinite(c) & np.isfinite(p) & (c > 0) & (p > 0)
    if not ok.any():
        return {"strike": float("nan"), "straddle": float("nan"), "move": float("nan")}
    i = np.flatnonzero(ok)[np.argmin(np.abs(K[ok] - F))]
    st = float(c[i] + p[i])
    return {"strike": float(K[i]), "straddle": st, "move": st / spot,
            "lower": spot - st, "upper": spot + st}
