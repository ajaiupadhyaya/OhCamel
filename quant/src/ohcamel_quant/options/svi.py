"""Raw SVI volatility smiles, their calibration and static-arbitrage diagnostics.

Raw SVI (Gatheral 2004, "A parsimonious arbitrage-free implied volatility
parametrization with application to the valuation of volatility derivatives")
for total implied variance ``w = sigma_BS^2 T`` in log-forward-moneyness
``k = ln(K / F)``::

    w(k) = a + b ( rho (k - m) + sqrt((k - m)^2 + sigma^2) )

with ``b >= 0``, ``|rho| < 1``, ``sigma > 0`` and ``a + b sigma sqrt(1 - rho^2) >= 0``
(non-negative minimum variance).

Calibration -- quasi-explicit method (De Marco & Martini / Zeliade Systems 2009,
"Quasi-explicit calibration of Gatheral's SVI model"). With ``y = (k - m)/sigma``::

    w = a + d y + c sqrt(y^2 + 1),     c = b sigma,  d = rho b sigma

is *linear* in ``(a, c, d)`` for fixed ``(m, sigma)``. The inner problem is a
weighted least squares over the Zeliade domain ``0 <= c <= 4 sigma``,
``|d| <= c``, ``|d| <= 4 sigma - c``, ``a <= max(w_i)`` (the slope cap 4 is the
Rogers & Tehranchi 2010 bound ``|dw/dk| <= 4``). Substituting ``u = c + d``,
``v = c - d`` turns the domain into a box ``u, v in [0, 4 sigma]`` and the
convex QP is solved *exactly* by enumerating the faces of the box (the minimizer
lies in the relative interior of one face). If the fitted smile has negative
minimum variance, the inner problem is re-solved with ``a >= 0`` (Zeliade's
original domain, which guarantees it). The outer problem over ``(m, sigma)`` is a
coarse grid search followed by Nelder-Mead (Nelder & Mead 1965).

Static arbitrage (Gatheral & Jacquier 2014, "Arbitrage-free SVI volatility
surfaces", Quantitative Finance 14):

* butterfly: the risk-neutral density is non-negative iff (Durrleman 2005)::

      g(k) = (1 - k w'/(2w))^2 - (w'^2/4)(1/w + 1/4) + w''/2 >= 0

* calendar spread: ``w(k, T)`` non-decreasing in ``T`` at every fixed ``k``.

The surface interpolates total variance linearly in ``T`` at fixed ``k`` between
fitted slices (which preserves calendar monotonicity when the slices satisfy it;
Gatheral & Jacquier 2014 section 5).
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize

SLOPE_CAP = 4.0


@dataclass(frozen=True)
class SVIParams:
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def w(self, k: Any) -> np.ndarray:
        """Total implied variance ``w(k)``."""
        x = np.asarray(k, dtype=float) - self.m
        return self.a + self.b * (self.rho * x + np.sqrt(x * x + self.sigma**2))

    def dw(self, k: Any) -> np.ndarray:
        x = np.asarray(k, dtype=float) - self.m
        return self.b * (self.rho + x / np.sqrt(x * x + self.sigma**2))

    def d2w(self, k: Any) -> np.ndarray:
        x = np.asarray(k, dtype=float) - self.m
        return self.b * self.sigma**2 / (x * x + self.sigma**2) ** 1.5

    def implied_vol(self, k: Any, T: float) -> np.ndarray:
        w = self.w(k)
        return np.sqrt(np.where(w > 0, w, np.nan) / T)

    def min_variance(self) -> float:
        return self.a + self.b * self.sigma * np.sqrt(1.0 - self.rho**2)

    def wing_slopes(self) -> tuple[float, float]:
        """Asymptotic ``dw/dk`` as k -> -inf and +inf: ``b(1-rho)``, ``b(1+rho)``
        (Lee 2004 moment formula: each must be <= 2)."""
        return self.b * (1.0 - self.rho), self.b * (1.0 + self.rho)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def butterfly_g(p: SVIParams, k: Any) -> np.ndarray:
    """Durrleman's function ``g(k)`` (see module docstring); density = g n(d2)/sqrt(2 pi w)."""
    k = np.asarray(k, dtype=float)
    w, w1, w2 = p.w(k), p.dw(k), p.d2w(k)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (1.0 - k * w1 / (2.0 * w)) ** 2 - (w1 * w1 / 4.0) * (1.0 / w + 0.25) + w2 / 2.0


def butterfly_check(p: SVIParams, k_grid: Any, tol: float = 1e-10) -> dict[str, Any]:
    """Minimum of ``g`` on ``k_grid`` and the sub-intervals where ``g < 0``."""
    k = np.asarray(k_grid, dtype=float)
    g = butterfly_g(p, k)
    w = p.w(k)
    bad = (g < -tol) | (w <= 0)
    i = int(np.nanargmin(g)) if np.isfinite(g).any() else 0
    return {
        "arbitrage_free": bool(not bad.any()),
        "g_min": float(np.nanmin(g)) if np.isfinite(g).any() else float("nan"),
        "k_at_g_min": float(k[i]),
        "violation_k": k[bad].tolist()[:: max(1, int(bad.sum()) // 25)] if bad.any() else [],
        "k_range": [float(k.min()), float(k.max())],
    }


@dataclass
class SVIFit:
    params: SVIParams
    T: float
    n: int
    rmse_w: float                # weighted RMSE in total variance
    rmse_vol_pts: float          # unweighted RMSE in vol points (1 = 1%)
    max_abs_err_vol_pts: float
    k_min: float
    k_max: float
    constrained_a: bool = False
    butterfly: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["params"] = self.params.to_dict()
        d["wing_slopes"] = list(self.params.wing_slopes())
        d["min_variance"] = self.params.min_variance()
        return d


# ------------------------------------------------------------------ inner QP
_FACES_A_FREE = list(itertools.product((0, 2), (0, 1, 2), (0, 1, 2)))     # a: free/upper
_FACES_A_BOXED = list(itertools.product((0, 1, 2), (0, 1, 2), (0, 1, 2)))  # 0 free, 1 lower, 2 upper


def _group_faces(faces: list[tuple[int, ...]]) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Group faces by their free-variable set: ``(free idx, fixed idx, codes (n_faces, n_fixed))``
    so that every face sharing a free set is solved with ONE factorization (multiple RHS)."""
    groups: dict[tuple[int, ...], list[tuple[int, ...]]] = {}
    for face in faces:
        groups.setdefault(tuple(i for i, s in enumerate(face) if s == 0), []).append(face)
    out = []
    for free, fs in groups.items():
        fixed = tuple(i for i in range(3) if i not in free)
        codes = np.array([[f[i] for i in fixed] for f in fs], dtype=int).reshape(len(fs), len(fixed))
        out.append((np.array(free, dtype=int), np.array(fixed, dtype=int), codes))
    return out


def _box_qp(G: np.ndarray, h: np.ndarray, lo: np.ndarray, hi: np.ndarray,
            faces: list[tuple[int, ...]]) -> np.ndarray:
    """Exact minimizer of ``x'Gx - 2h'x`` over ``lo <= x <= hi`` (3 variables) by
    enumerating faces; ``lo`` may be -inf for variables never fixed at the lower bound.
    Faces with the same free set share one linear solve (vectorized over the faces)."""
    try:  # fast path: interior unconstrained minimizer is the global one when feasible
        x0 = np.linalg.solve(G, h)
        if np.all(x0 >= lo - 1e-12) and np.all(x0 <= hi + 1e-12):
            return x0
    except np.linalg.LinAlgError:
        pass
    groups = _FACE_GROUPS.get(id(faces))
    if groups is None:
        groups = _group_faces(faces)
    best, best_x = np.inf, None
    for free, fixed, codes in groups:
        X = np.empty((codes.shape[0], 3))
        if fixed.size:
            X[:, fixed] = np.where(codes == 1, lo[fixed], hi[fixed])
        if free.size:
            rhs = h[free][:, None] - (G[np.ix_(free, fixed)] @ X[:, fixed].T if fixed.size else 0.0)
            try:
                X[:, free] = np.linalg.solve(G[np.ix_(free, free)], rhs).T
            except np.linalg.LinAlgError:
                continue
        feas = np.all((X >= lo - 1e-12) & (X <= hi + 1e-12), axis=1)
        if not feas.any():
            continue
        Xf = X[feas]
        obj = np.einsum("ij,jk,ik->i", Xf, G, Xf) - 2.0 * Xf @ h
        j = int(np.argmin(obj))
        if obj[j] < best:
            best, best_x = float(obj[j]), Xf[j].copy()
    if best_x is None:
        raise np.linalg.LinAlgError("SVI inner problem: no feasible face")
    return best_x


_FACE_GROUPS = {id(_FACES_A_FREE): _group_faces(_FACES_A_FREE), id(_FACES_A_BOXED): _group_faces(_FACES_A_BOXED)}


def _inner(
    k: np.ndarray, w: np.ndarray, wt: np.ndarray, m: float, s: float, a_nonneg: bool = False,
) -> tuple[np.ndarray, float, bool]:
    """Best (a, c, d) for fixed (m, sigma), its weighted SSE, and whether ``a >= 0`` was imposed."""
    y = (k - m) / s
    z = np.sqrt(y * y + 1.0)
    A = np.column_stack([np.ones_like(y), 0.5 * (y + z), 0.5 * (z - y)])   # columns: a, u, v
    Aw = A * wt[:, None]
    G = A.T @ Aw
    h = Aw.T @ w
    wmax = float(w.max())
    hi = np.array([wmax, SLOPE_CAP * s, SLOPE_CAP * s])
    lo = np.array([0.0 if a_nonneg else -np.inf, 0.0, 0.0])
    x = _box_qp(G, h, lo, hi, _FACES_A_BOXED if a_nonneg else _FACES_A_FREE)
    a, u, v = x
    if not a_nonneg and a + np.sqrt(max(u * v, 0.0)) < 0:
        return _inner(k, w, wt, m, s, a_nonneg=True)
    res = A @ x - w
    return np.array([a, 0.5 * (u + v), 0.5 * (u - v)]), float(np.sum(wt * res * res)), a_nonneg


def _to_params(x: np.ndarray, m: float, s: float) -> SVIParams:
    a, c, d = (float(v) for v in x)
    b = c / s
    rho = float(np.clip(d / c, -0.999999, 0.999999)) if c > 1e-14 else 0.0
    return SVIParams(a=a, b=b, rho=rho, m=float(m), sigma=float(s))


def fit_svi(
    k: Any, iv: Any, T: float, weights: Any | None = None, butterfly_grid: Any | None = None,
) -> SVIFit:
    """Calibrate raw SVI to implied vols ``iv`` at log-moneyness ``k`` (one expiry).

    ``weights`` (optional, e.g. inverse bid-ask spread) multiply the squared
    total-variance errors; they are normalized to mean 1. Needs >= 5 points.
    """
    k = np.asarray(k, dtype=float)
    iv = np.asarray(iv, dtype=float)
    ok = np.isfinite(k) & np.isfinite(iv) & (iv > 0)
    wt = np.ones_like(k) if weights is None else np.asarray(weights, dtype=float)
    ok &= np.isfinite(wt) & (wt > 0)
    k, iv, wt = k[ok], iv[ok], wt[ok]
    if k.size < 5:
        raise ValueError(f"SVI needs at least 5 quotes, got {k.size}")
    if T <= 0:
        raise ValueError("T must be positive")
    wt = wt / wt.mean()
    w = iv * iv * T
    kmin, kmax = float(k.min()), float(k.max())
    span = max(kmax - kmin, 1e-3)
    m_lo, m_hi = kmin - span, kmax + span
    s_lo, s_hi = 1e-4, 4.0 * span + 1.0

    cache: dict[tuple[float, float], tuple[np.ndarray, float, bool]] = {}

    def obj(theta: np.ndarray) -> float:
        m, ls = float(theta[0]), float(theta[1])
        s = float(np.exp(ls))
        pen = 0.0
        if m < m_lo or m > m_hi:
            pen += 1e3 * (max(m_lo - m, m - m_hi)) ** 2
            m = min(max(m, m_lo), m_hi)
        if s < s_lo or s > s_hi:
            pen += 1e3 * (max(np.log(s_lo) - ls, ls - np.log(s_hi))) ** 2
            s = min(max(s, s_lo), s_hi)
        key = (round(m, 12), round(s, 12))
        if key not in cache:
            try:
                cache[key] = _inner(k, w, wt, m, s)
            except np.linalg.LinAlgError:
                cache[key] = (np.full(3, np.nan), np.inf, False)
        return cache[key][1] + pen

    m_grid = np.linspace(kmin - 0.25 * span, kmax + 0.25 * span, 9)
    s_grid = np.exp(np.linspace(np.log(0.01 * span + 1e-4), np.log(1.5 * span + 1e-3), 7))
    starts = sorted((obj(np.array([m, np.log(s)])), m, s) for m in m_grid for s in s_grid)[:3]
    best = None
    for _, m0, s0 in starts:
        r = minimize(obj, np.array([m0, np.log(s0)]), method="Nelder-Mead",
                     options={"xatol": 1e-7, "fatol": 1e-14, "maxiter": 600})
        if best is None or r.fun < best.fun:
            best = r
    m = float(np.clip(best.x[0], m_lo, m_hi))
    s = float(np.clip(np.exp(best.x[1]), s_lo, s_hi))
    x, sse, constrained = _inner(k, w, wt, m, s)
    p = _to_params(x, m, s)
    fit_iv = p.implied_vol(k, T)
    err = (fit_iv - iv) * 100.0
    grid = np.linspace(kmin - 0.5 * span, kmax + 0.5 * span, 401) if butterfly_grid is None else butterfly_grid
    return SVIFit(
        params=p, T=float(T), n=int(k.size), rmse_w=float(np.sqrt(sse / k.size)),
        rmse_vol_pts=float(np.sqrt(np.nanmean(err * err))), max_abs_err_vol_pts=float(np.nanmax(np.abs(err))),
        k_min=kmin, k_max=kmax, constrained_a=constrained, butterfly=butterfly_check(p, grid),
    )


# ------------------------------------------------------------------ surface
def calendar_check(slices: list[tuple[float, SVIParams]], k_grid: Any, tol: float = 1e-8) -> list[dict[str, Any]]:
    """Adjacent-pair total-variance monotonicity ``w(k, T2) >= w(k, T1)`` on ``k_grid``.

    Returns one record per adjacent pair: T1, T2, min difference, the k where
    it is smallest and whether the pair violates the condition.
    """
    k = np.asarray(k_grid, dtype=float)
    ss = sorted(slices, key=lambda t: t[0])
    out = []
    for (T1, p1), (T2, p2) in itertools.pairwise(ss):
        d = p2.w(k) - p1.w(k)
        i = int(np.argmin(d))
        out.append({"T1": T1, "T2": T2, "min_dw": float(d[i]), "k_at_min": float(k[i]),
                    "violation": bool(d[i] < -tol),
                    "violation_k": k[d < -tol].tolist()[:: max(1, int((d < -tol).sum()) // 15)]})
    return out


def total_variance_surface(slices: list[tuple[float, SVIParams]], k: Any, T: Any) -> np.ndarray:
    """``w(k, T)`` on a grid (rows = T, columns = k): linear in ``T`` between slices at
    fixed ``k``; NaN outside ``[T_first, T_last]`` (no extrapolation)."""
    ss = sorted(slices, key=lambda t: t[0])
    k = np.asarray(k, dtype=float)
    T = np.atleast_1d(np.asarray(T, dtype=float))
    Ts = np.array([t for t, _ in ss])
    W = np.vstack([p.w(k) for _, p in ss])          # (n_slices, n_k)
    out = np.full((T.size, k.size), np.nan)
    for i, t in enumerate(T):
        if t < Ts[0] - 1e-12 or t > Ts[-1] + 1e-12:
            continue
        j = int(np.clip(np.searchsorted(Ts, t), 1, len(Ts) - 1)) if len(Ts) > 1 else 0
        if len(Ts) == 1:
            out[i] = W[0]
            continue
        t0, t1 = Ts[j - 1], Ts[j]
        lam = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        out[i] = (1 - lam) * W[j - 1] + lam * W[j]
    return out


def implied_vol_surface(slices: list[tuple[float, SVIParams]], k: Any, T: Any) -> np.ndarray:
    """Implied vols ``sqrt(w(k, T) / T)`` on the (T, k) grid of :func:`total_variance_surface`."""
    T = np.atleast_1d(np.asarray(T, dtype=float))
    w = total_variance_surface(slices, k, T)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.sqrt(np.where(w > 0, w, np.nan) / T[:, None])
