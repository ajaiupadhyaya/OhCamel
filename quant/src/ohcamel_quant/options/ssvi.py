"""SSVI: a whole volatility surface free of static arbitrage by construction (Gatheral & Jacquier 2014).

Surface SVI (Gatheral & Jacquier 2014, "Arbitrage-free SVI volatility surfaces", Quantitative Finance 14,
section 4, eq. 4.1) for total implied variance ``w = sigma_BS^2 T`` at log-forward-moneyness ``k = ln(K/F)``::

    w(k, theta_T) = theta_T / 2 * (1 + rho phi k + sqrt((phi k + rho)^2 + 1 - rho^2)),   phi = phi(theta_T)

``theta_T = w(0, T)`` is the ATM total variance. Here ``phi`` is the power law (their Remark 4.4 / the example
after Corollary 4.1)::

    phi(theta) = eta theta^{-gamma} (1 + theta)^{gamma - 1}

Conditions enforced -- every :class:`SSVIParams` refuses values outside them (``ValueError``), and :func:`fit`
searches only inside them:

1. ``|rho| < 1``, ``eta > 0``, ``theta_i > 0``, maturities strictly increasing.
2. ``theta`` non-decreasing in ``T``. With (3), this is Theorem 4.1's calendar condition: SSVI is free of
   calendar-spread arbitrage iff ``d theta / dT >= 0`` and ``0 <= d(theta phi)/d theta <= (1 + sqrt(1 - rho^2))
   phi / rho^2``. For the power law ``d(theta phi)/d theta = (1 - gamma) phi / (1 + theta)``, which is
   ``>= 0`` and ``<= phi <= (1 + sqrt(1 - rho^2)) phi / rho^2`` for any ``gamma`` in ``[0, 1]``.
3. ``gamma`` in ``(0, 1/2]`` and ``eta (1 + |rho|) <= 2``: the sufficient condition for no butterfly arbitrage
   (Theorem 4.2: ``theta phi (1 + |rho|) < 4`` and ``theta phi^2 (1 + |rho|) <= 4`` for every ``theta > 0``;
   for the power law ``theta phi = eta (theta/(1+theta))^{1-gamma} < eta`` and, with ``gamma <= 1/2``,
   ``theta phi^2 = eta^2 (theta/(1+theta))^{1-2 gamma} / (1+theta) < eta^2``, so ``eta (1 + |rho|) <= 2``
   gives ``theta phi (1+|rho|) < 2`` and ``theta phi^2 (1+|rho|) < 4 / (1+|rho|) <= 4``).

Under 1-3 the surface is free of static arbitrage (Gatheral & Jacquier 2014, Corollary 4.1 / Remark 4.4) at
every ``k`` and every ``theta``, hence at every ``T`` of the interpolation below. The tests and the nightly
league (``products.surface_league``) still measure the arbitrage itself -- ``w`` non-decreasing in ``T`` on a
``k`` grid, and Durrleman's ``g >= 0`` on ``[-svi.K_MAX, svi.K_MAX]`` by the refined scan ``svi._g_min``, the
check every per-slice SVI fit passes -- not only these inequalities.

Between fitted maturities ``theta`` is linear in ``T`` (non-decreasing, so (2) holds there); before the first
it is ``theta_1 T / T_1`` (to zero at ``T = 0``) and after the last ``theta_n T / T_n`` (the last ATM implied
vol held), both non-decreasing.

Each slice is a raw SVI (their Lemma 3.2): ``a = theta (1 - rho^2) / 2``, ``b = theta phi / 2``, ``rho``,
``m = -rho / phi``, ``sigma = sqrt(1 - rho^2) / phi`` (:func:`slice_svi`), so the per-slice SVI checks apply
unchanged and the two models' violations are counted by the same code.

Calibration: bounded nonlinear least squares (``scipy.optimize.least_squares``, trust-region reflective) over
``(rho, gamma, u, theta_1, delta_2, ..., delta_n)`` with ``eta = 2 u / (1 + |rho|)``, ``u`` in ``(0, 1]``,
``gamma`` in ``(0, 1/2]``, ``theta_i = theta_1 + delta_2 + ... + delta_i`` and every ``delta >= 0`` -- the
conditions are the box, so no candidate outside them is ever evaluated and the boundary itself is reachable.
Residuals are in implied vol, ``sqrt(w_model / T) - iv``, times the square root of each quote's weight
(normalised to mean 1 within its slice, as ``svi.fit_svi`` does): equal footing across maturities, in the unit
the RMSE is reported in. Starts: ``theta`` from each slice's quoted ATM total variance (running maximum), three
``(rho, gamma, u)`` starts; the lowest cost wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from .svi import SVIParams

ETA_RHO_CAP = 2.0       # eta (1 + |rho|) <= 2 (Gatheral & Jacquier 2014, power-law case of Theorem 4.2)
GAMMA_MAX = 0.5         # gamma in (0, 1/2]
_REL_TOL = 1e-12        # rounding slack on the eta-rho product, so the boundary itself is accepted
_STARTS = ((-0.6, 0.4, 0.6), (-0.3, 0.25, 0.3), (-0.8, 0.5, 0.95))   # (rho, gamma, u)

CONDITIONS = {
    "calendar": "theta non-decreasing in T; power-law phi with gamma in (0, 1] (Gatheral & Jacquier 2014, Thm 4.1)",
    "butterfly": "eta (1 + |rho|) <= 2 with gamma in (0, 1/2] (Gatheral & Jacquier 2014, Thm 4.2, Cor. 4.1 / "
                 "Remark 4.4)",
    "reference": "Gatheral, J. & Jacquier, A. (2014), Arbitrage-free SVI volatility surfaces, Quantitative Finance 14",
}


def phi(theta: Any, eta: float, gamma: float) -> np.ndarray:
    """The power law ``eta theta^{-gamma} (1 + theta)^{gamma - 1}``."""
    th = np.asarray(theta, dtype=float)
    return eta * th ** (-gamma) * (1.0 + th) ** (gamma - 1.0)


def _w(theta: Any, k: Any, rho: float, eta: float, gamma: float) -> np.ndarray:
    th = np.asarray(theta, dtype=float)
    k = np.asarray(k, dtype=float)
    ph = phi(th, eta, gamma)
    z = ph * k + rho
    return 0.5 * th * (1.0 + rho * ph * k + np.sqrt(z * z + 1.0 - rho * rho))


@dataclass(frozen=True)
class SSVIParams:
    rho: float
    eta: float
    gamma: float
    theta: np.ndarray     # ATM total variance at each fitted maturity
    T: np.ndarray         # the fitted maturities (years), strictly increasing

    def __post_init__(self) -> None:
        th = np.atleast_1d(np.asarray(self.theta, dtype=float)).copy()
        T = np.atleast_1d(np.asarray(self.T, dtype=float)).copy()
        object.__setattr__(self, "theta", th)
        object.__setattr__(self, "T", T)
        rho, eta, gamma = float(self.rho), float(self.eta), float(self.gamma)
        if not (np.isfinite(rho) and abs(rho) < 1.0):
            raise ValueError(f"SSVI: |rho| must be < 1, got rho={rho}")
        if not (np.isfinite(eta) and eta > 0.0):
            raise ValueError(f"SSVI: eta must be positive, got eta={eta}")
        if eta * (1.0 + abs(rho)) > ETA_RHO_CAP * (1.0 + _REL_TOL):
            raise ValueError(f"SSVI: eta (1 + |rho|) = {eta * (1 + abs(rho)):.6g} > {ETA_RHO_CAP:g}, "
                             "outside the no-butterfly condition")
        if not (np.isfinite(gamma) and 0.0 < gamma <= GAMMA_MAX):
            raise ValueError(f"SSVI: gamma must be in (0, {GAMMA_MAX:g}], got gamma={gamma}")
        if th.size == 0 or th.size != T.size:
            raise ValueError("SSVI: theta and T must be non-empty and of one length")
        if not (np.all(np.isfinite(T)) and np.all(T > 0) and np.all(np.diff(T) > 0)):
            raise ValueError("SSVI: maturities T must be positive and strictly increasing")
        if not (np.all(np.isfinite(th)) and np.all(th > 0)):
            raise ValueError("SSVI: theta must be positive")
        if np.any(np.diff(th) < 0):
            raise ValueError("SSVI: theta must be non-decreasing in T (calendar condition)")

    def theta_at(self, T: Any) -> np.ndarray:
        """``theta(T)``: linear between fitted maturities, ``theta_1 T/T_1`` before, ``theta_n T/T_n`` after."""
        t = np.asarray(T, dtype=float)
        inside = np.interp(t, self.T, self.theta)
        return np.where(t < self.T[0], self.theta[0] * t / self.T[0],
                        np.where(t > self.T[-1], self.theta[-1] * t / self.T[-1], inside))

    def to_dict(self) -> dict[str, Any]:
        return {"rho": float(self.rho), "eta": float(self.eta), "gamma": float(self.gamma),
                "theta": self.theta.tolist(), "T": self.T.tolist(),
                "eta_one_plus_abs_rho": float(self.eta * (1.0 + abs(self.rho)))}


def total_variance(p: SSVIParams, k: Any, T: float) -> np.ndarray:
    """``w(k, T)`` of the surface (module docstring), at the scalar maturity ``T``."""
    return _w(p.theta_at(float(T)), k, p.rho, p.eta, p.gamma)


def implied_vol(p: SSVIParams, k: Any, T: float) -> np.ndarray:
    w = total_variance(p, k, T)
    return np.sqrt(np.where(w > 0, w, np.nan) / float(T))


def slice_svi(p: SSVIParams, T: float) -> SVIParams:
    """The slice at ``T`` as raw SVI (Gatheral & Jacquier 2014, Lemma 3.2)."""
    th = float(p.theta_at(float(T)))
    ph = float(phi(th, p.eta, p.gamma))
    r = float(p.rho)
    return SVIParams(a=0.5 * th * (1.0 - r * r), b=0.5 * th * ph, rho=r, m=-r / ph, sigma=np.sqrt(1.0 - r * r) / ph)


@dataclass
class SSVIFit:
    params: SSVIParams
    n: int
    rmse_vol_pts: float               # unweighted RMSE in vol points (1 = 1%), all quotes pooled
    max_abs_err_vol_pts: float
    slice_rmse_vol_pts: list[float]   # per fitted maturity, in the order of params.T
    slice_n: list[int]
    cost: float                       # the weighted least-squares objective at the optimum
    nfev: int
    status: int
    conditions: dict[str, str] = field(default_factory=lambda: dict(CONDITIONS))

    def to_dict(self) -> dict[str, Any]:
        return {"params": self.params.to_dict(), "n": self.n, "rmse_vol_pts": self.rmse_vol_pts,
                "max_abs_err_vol_pts": self.max_abs_err_vol_pts, "slice_rmse_vol_pts": self.slice_rmse_vol_pts,
                "slice_n": self.slice_n, "cost": self.cost, "nfev": self.nfev, "status": self.status,
                "conditions": self.conditions}


def _clean(slices: list[tuple[float, np.ndarray, np.ndarray, np.ndarray]]):
    out = []
    for T, k, iv, wt in slices:
        k, iv = np.asarray(k, dtype=float), np.asarray(iv, dtype=float)
        wt = np.ones_like(k) if wt is None else np.asarray(wt, dtype=float)
        ok = np.isfinite(k) & np.isfinite(iv) & (iv > 0) & np.isfinite(wt) & (wt > 0)
        k, iv, wt = k[ok], iv[ok], wt[ok]
        if k.size == 0:
            continue
        out.append((float(T), k, iv, wt / wt.mean()))
    out.sort(key=lambda s: s[0])
    return out


def _atm_w(T: float, k: np.ndarray, iv: np.ndarray) -> float:
    o = np.argsort(k)
    w = iv[o] ** 2 * T
    return float(np.interp(0.0, k[o], w))


def fit(slices: list[tuple[float, np.ndarray, np.ndarray, np.ndarray]]) -> SSVIFit:
    """SSVI on ``slices`` = ``[(T, k, iv, weights), ...]`` (one per maturity; ``weights`` may be None), within the
    conditions of the module docstring. Needs at least two maturities, all distinct, and 5 quotes in all."""
    ss = _clean(slices)
    if len(ss) < 2:
        raise ValueError(f"SSVI needs quotes at two or more maturities, got {len(ss)}")
    Ts = np.array([s[0] for s in ss])
    if np.any(np.diff(Ts) <= 0) or Ts[0] <= 0:
        raise ValueError("SSVI: maturities must be positive and distinct")
    n_sl = len(ss)
    k_all = np.concatenate([s[1] for s in ss])
    iv_all = np.concatenate([s[2] for s in ss])
    sw_all = np.sqrt(np.concatenate([s[3] for s in ss]))
    idx = np.concatenate([np.full(s[1].size, i) for i, s in enumerate(ss)])
    T_all = Ts[idx]
    if k_all.size < 5:
        raise ValueError(f"SSVI needs at least 5 quotes, got {k_all.size}")

    def unpack(x: np.ndarray) -> tuple[float, float, float, np.ndarray]:
        rho, gamma, u = float(x[0]), float(x[1]), float(x[2])
        theta = np.cumsum(x[3:])
        return rho, 2.0 * u / (1.0 + abs(rho)), gamma, theta

    def resid(x: np.ndarray) -> np.ndarray:
        rho, eta, gamma, theta = unpack(x)
        w = _w(theta[idx], k_all, rho, eta, gamma)
        return sw_all * (np.sqrt(np.maximum(w, 0.0) / T_all) - iv_all)

    th0 = np.maximum.accumulate(np.array([max(_atm_w(T, k, iv), 1e-6) for T, k, iv, _ in ss]))
    inc0 = np.concatenate([[th0[0]], np.diff(th0)])
    lo = np.concatenate([[-0.999, 1e-4, 1e-6, 1e-8], np.zeros(n_sl - 1)])
    hi = np.concatenate([[0.999, GAMMA_MAX, 1.0], np.full(n_sl, np.inf)])
    best = None
    for rho0, g0, u0 in _STARTS:
        x0 = np.clip(np.concatenate([[rho0, g0, u0], inc0]), lo, hi)
        r = least_squares(resid, x0, bounds=(lo, hi), method="trf", x_scale="jac",
                          ftol=1e-15, xtol=1e-15, gtol=1e-15, max_nfev=4000)
        if best is None or r.cost < best.cost:
            best = r
    rho, eta, gamma, theta = unpack(best.x)
    while eta * (1.0 + abs(rho)) > ETA_RHO_CAP:      # the product is 2 u <= 2 up to one rounding
        eta = float(np.nextafter(eta, 0.0))
    p = SSVIParams(rho=rho, eta=eta, gamma=gamma, theta=theta, T=Ts)
    err = (np.sqrt(np.maximum(_w(theta[idx], k_all, rho, eta, gamma), 0.0) / T_all) - iv_all) * 100.0
    per = [float(np.sqrt(np.mean(err[idx == i] ** 2))) for i in range(n_sl)]
    return SSVIFit(params=p, n=int(k_all.size), rmse_vol_pts=float(np.sqrt(np.mean(err * err))),
                   max_abs_err_vol_pts=float(np.max(np.abs(err))), slice_rmse_vol_pts=per,
                   slice_n=[int(s[1].size) for s in ss], cost=float(best.cost), nfev=int(best.nfev),
                   status=int(best.status))
