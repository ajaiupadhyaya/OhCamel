"""Dynamic conditional correlation: DCC(1,1) with GJR-GARCH(1,1)-t marginals (completion task 4.7).

Engle (2002), "Dynamic conditional correlation: a simple class of multivariate generalized autoregressive
conditional heteroskedasticity models", *J. Business & Economic Statistics* 20(3), 339-350; the two-step
estimator's properties in Engle & Sheppard (2001), "Theoretical and empirical properties of dynamic conditional
correlation multivariate GARCH", NBER WP 8554.

Model, for a ``T x N`` panel of DAILY decimal returns ``r_t``:

* marginals (step 1): each column is a GJR-GARCH(1,1) with unit-variance Student-t innovations, fitted by
  :func:`ohcamel_quant.risk.garch.fit_garch_fast` (the same in-house MLE the vol league's rolling refits use);
  ``z_it = eps_it / sigma_it`` are the standardised residuals;
* correlations (step 2), with correlation targeting:
  ``Q_t = (1 - a - b) Qbar + a z_{t-1} z_{t-1}' + b Q_{t-1}``, ``Q_1 = Qbar = (1/T) sum_t z_t z_t'``,
  ``R_t = diag(Q_t)^{-1/2} Q_t diag(Q_t)^{-1/2}``;
  ``(a, b)`` maximise the correlation part of the Gaussian quasi-likelihood (Engle 2002, eq. 26)
  ``L_C = -1/2 sum_t (log|R_t| + z_t' R_t^{-1} z_t - z_t' z_t)`` subject to ``a, b >= 0`` and
  ``a + b <= MAX_PERSISTENCE < 1`` (so ``Q_t`` mean-reverts to ``Qbar``);
* ``H_t = D_t R_t D_t``, ``D_t = diag(sigma_it)``.

Forecast (:meth:`DCCFit.forecast`): ``Q_{T+1}`` is known at ``T``; for ``h > 1`` Engle & Sheppard's (2001)
approximation ``E_T[R_{T+h}] ~ (1 - (a+b)^{h-1}) Rbar + (a+b)^{h-1} R_{T+1}`` (``Rbar`` = ``Qbar`` normalised), and
each variance by the GARCH h-step formula ``sbar + P^{h-1} (sigma^2_{T+1} - sbar)``. A convex combination of
correlation matrices that are positive definite is positive definite, and ``Q_{T+1}`` is (``Qbar`` is, when
``T > N``, and ``z z'`` is PSD); a spectral floor (:data:`EIG_FLOOR`) then guards the case where it holds only in
exact arithmetic (``a + b`` at its bound with ``b ~ 0`` makes ``Q_{T+1}`` nearly rank one), so the returned
forecast is always positive definite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import lfilter

from .garch import FastGarch, fit_garch_fast

#: Upper bound on ``a + b`` in the optimiser (``fit_garch_fast``'s persistence bound, 0.9999).
MAX_PERSISTENCE = 0.9999
#: Smallest eigenvalue allowed in a returned correlation forecast, relative to the largest.
EIG_FLOOR = 1e-8
REFERENCE = ("Engle (2002), 'Dynamic conditional correlation', JBES 20(3); two-step QML and the h-step "
             "forecast: Engle & Sheppard (2001), NBER WP 8554; GJR-GARCH-t marginals: Glosten, Jagannathan & "
             "Runkle (1993)")


def _normalise(q: np.ndarray) -> np.ndarray:
    d = 1.0 / np.sqrt(np.diagonal(q, axis1=-2, axis2=-1))
    return q * d[..., :, None] * d[..., None, :]


def _pd_correlation(r: np.ndarray) -> np.ndarray:
    """``r`` with its eigenvalues floored at ``EIG_FLOOR x`` the largest, rescaled to unit diagonal (a congruence,
    so positive definiteness is kept). Returned unchanged when it already clears the floor."""
    r = 0.5 * (r + r.T)
    w, v = np.linalg.eigh(r)
    if w[0] >= EIG_FLOOR * w[-1]:
        return r
    w = np.maximum(w, EIG_FLOOR * w[-1])
    return _normalise((v * w) @ v.T)


def _q_path(a: float, b: float, z: np.ndarray, qbar: np.ndarray) -> np.ndarray:
    """``Q_1 .. Q_T`` (``T x N x N``) by the DCC recursion with ``Q_1 = Qbar``, as one first-order IIR filter
    over the flattened entries."""
    t, n = z.shape
    flat = qbar.ravel()
    out = np.empty((t, n * n))
    out[0] = flat
    if t > 1:
        zz = (z[:-1, :, None] * z[:-1, None, :]).reshape(t - 1, n * n)
        x = (1.0 - a - b) * flat + a * zz
        out[1:], _ = lfilter([1.0], [1.0, -b], x, axis=0, zi=(b * flat)[None, :])
    return out.reshape(t, n, n)


def correlation_nll(theta: np.ndarray, z: np.ndarray, qbar: np.ndarray) -> float:
    """Minus the correlation quasi-log-likelihood ``-L_C(a, b)`` (Engle 2002, eq. 26), per observation."""
    a, b = float(theta[0]), float(theta[1])
    if a < 0 or b < 0 or a + b >= 1:
        return 1e10
    r = _normalise(_q_path(a, b, z, qbar))
    try:
        c = np.linalg.cholesky(r)
    except np.linalg.LinAlgError:
        return 1e10
    logdet = 2.0 * np.log(np.diagonal(c, axis1=1, axis2=2)).sum(axis=1)
    u = np.linalg.solve(c, z[:, :, None])[:, :, 0]            # c u = z  ->  z' R^-1 z = u'u
    ll = -0.5 * np.sum(logdet + np.sum(u * u, axis=1) - np.sum(z * z, axis=1))
    return float(-ll / z.shape[0])


@dataclass
class DCCFit:
    """A fitted DCC(1,1)-GJR-GARCH-t. Covariances are DAILY, DECIMAL^2."""

    names: list[str]
    index: pd.Index
    marginals: list[FastGarch]
    a: float
    b: float
    qbar: np.ndarray            # Qbar = (1/T) sum z_t z_t'
    z: np.ndarray               # T x N standardised residuals
    q_last: np.ndarray          # Q_T, the last in-sample Q
    loglik: float               # correlation quasi-log-likelihood L_C at (a, b)
    converged: bool
    notes: list[str] = field(default_factory=list)

    @property
    def rbar(self) -> np.ndarray:
        return _normalise(self.qbar)

    @property
    def persistence(self) -> float:
        return self.a + self.b

    def correlation_forecast(self, h: int = 1) -> np.ndarray:
        """``E_T[R_{T+h}]`` by Engle & Sheppard's (2001) approximation, floored to positive definite."""
        if h < 1:
            raise ValueError("forecast horizon must be >= 1")
        z_t = self.z[-1]
        q1 = (1.0 - self.a - self.b) * self.qbar + self.a * np.outer(z_t, z_t) + self.b * self.q_last
        r1 = _normalise(q1)
        w = self.persistence ** (h - 1)
        return _pd_correlation(w * r1 + (1.0 - w) * self.rbar)

    def variance_forecast(self, h: int = 1) -> np.ndarray:
        """Each marginal's ``E_T[sigma^2_{T+h}]`` (decimal^2): ``sbar + P^{h-1} (sigma^2_{T+1} - sbar)``."""
        out = []
        for m in self.marginals:
            p = m.persistence
            s1 = m.next_variance_pct
            out.append(s1 if p >= 1 else m.omega / (1 - p) + p ** (h - 1) * (s1 - m.omega / (1 - p)))
        return np.asarray(out) / 1e4

    def forecast(self, h: int = 1) -> np.ndarray:
        """``H_{T+h} = D R D``: the h-step-ahead conditional covariance, daily decimal^2, positive definite."""
        d = np.sqrt(self.variance_forecast(h))
        return self.correlation_forecast(h) * np.outer(d, d)

    def mean_forecast(self, horizon: int) -> np.ndarray:
        """``(1/H) sum_{h=1}^{H} H_{T+h}``: the average covariance over a holding period of ``H`` sessions."""
        return sum(self.forecast(h) for h in range(1, horizon + 1)) / horizon


def refit_correlation(fit: DCCFit, a: float, b: float) -> DCCFit:
    """``fit`` with the correlation parameters replaced by ``(a, b)`` (the marginals and ``Qbar`` kept) and
    ``Q_T`` recomputed. ``a, b >= 0`` and ``a + b < 1`` are required."""
    if a < 0 or b < 0 or a + b >= 1:
        raise ValueError(f"DCC needs a, b >= 0 and a + b < 1; got a={a}, b={b}")
    q = _q_path(a, b, fit.z, fit.qbar)
    return replace(fit, a=float(a), b=float(b), q_last=q[-1],
                   loglik=-correlation_nll(np.array([a, b]), fit.z, fit.qbar) * fit.z.shape[0])


def fit(returns: pd.DataFrame) -> DCCFit:
    """Two-step quasi-maximum-likelihood DCC(1,1) on a ``T x N`` frame of daily decimal returns (no NaN).

    Step 1 fits each column's GJR-GARCH(1,1)-t; step 2 maximises ``L_C`` over ``(a, b)`` from the best of a small
    grid of starts by SLSQP with ``a, b in [0, MAX_PERSISTENCE]`` and ``a + b <= MAX_PERSISTENCE``.
    """
    if not isinstance(returns, pd.DataFrame):
        raise TypeError("returns must be a DataFrame (T x N)")
    x = returns.to_numpy(dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("returns contain NaN/inf; drop or align sessions first")
    t, n = x.shape
    if n < 2:
        raise ValueError("DCC needs at least two assets")
    if t < max(100, n + 2):
        raise ValueError(f"DCC needs at least max(100, N + 2) = {max(100, n + 2)} observations; have {t}")
    notes: list[str] = []
    marginals = [fit_garch_fast(x[:, i], "gjr") for i in range(n)]
    for name, m in zip(returns.columns, marginals, strict=True):
        if not m.converged:
            notes.append(f"{name}: GJR-GARCH optimiser did not report convergence")
    z = np.column_stack([m.std_resid for m in marginals])
    qbar = z.T @ z / t
    starts = [(a0, b0) for a0 in (0.01, 0.03, 0.06) for b0 in (0.80, 0.90, 0.95) if a0 + b0 < MAX_PERSISTENCE]
    x0 = min(starts, key=lambda s: correlation_nll(np.array(s), z, qbar))
    res = minimize(correlation_nll, np.array(x0), args=(z, qbar), method="SLSQP",
                   bounds=[(0.0, MAX_PERSISTENCE), (0.0, MAX_PERSISTENCE)],
                   constraints=[{"type": "ineq", "fun": lambda th: MAX_PERSISTENCE - th[0] - th[1],
                                 "jac": lambda th: np.array([-1.0, -1.0])}],
                   options={"maxiter": 200, "ftol": 1e-12})
    a, b = (float(v) for v in np.clip(res.x, 0.0, MAX_PERSISTENCE))
    if a + b > MAX_PERSISTENCE:          # SLSQP may step a hair past the linear constraint
        s = MAX_PERSISTENCE / (a + b)
        a, b = a * s, b * s
    if not res.success:
        notes.append(f"DCC step-2 optimiser: {res.message}")
    q = _q_path(a, b, z, qbar)
    ll = -correlation_nll(np.array([a, b]), z, qbar) * t
    if not math.isfinite(ll):
        raise ValueError("DCC correlation likelihood is not finite at the optimum")
    return DCCFit(names=[str(c) for c in returns.columns], index=returns.index, marginals=marginals, a=a, b=b,
                  qbar=qbar, z=z, q_last=q[-1], loglik=ll, converged=bool(res.success), notes=notes)


__all__ = ["DCCFit", "EIG_FLOOR", "MAX_PERSISTENCE", "REFERENCE", "correlation_nll", "fit", "refit_correlation"]
