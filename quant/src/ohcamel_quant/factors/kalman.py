"""A time-varying (random-walk) market beta by the Kalman filter (completion task 4.7).

State-space form (Harvey 1989, *Forecasting, Structural Time Series Models and the Kalman Filter*, ch. 3;
Durbin & Koopman 2012, *Time Series Analysis by State Space Methods*, 2nd ed., ch. 4 and 7):

    y_t = alpha_t + beta_t x_t + eps_t,            eps_t ~ N(0, sigma^2)
    theta_t = theta_{t-1} + w_t,  theta = (alpha, beta)',  w_t ~ N(0, q sigma^2 diag(s^2, 1))

with ``s = ALPHA_SCALE = 0.01``: the intercept's state noise is the slope's on a 1% market day, so one
signal-to-noise ratio ``q`` moves both coefficients on a comparable footing and no data-dependent scale enters
the filter (a sample variance there would make the filtered value at ``t`` depend on later data).

* Filter: the standard prediction-error recursion. Everything is in units of ``sigma^2``, so ``sigma^2`` is
  concentrated out of the likelihood: ``sigma^2_hat = (1/(n-d)) sum_{t>d} v_t^2 / F_t`` and
  ``log L_c(q) = -(n-d)/2 (log 2 pi + 1 + log sigma^2_hat) - 1/2 sum_{t>d} log F_t`` (Harvey 1989, eq. 3.4.8),
  the first ``d = 2`` observations being absorbed by the diffuse prior ``theta_0 = 0``,
  ``P_0 = KAPPA diag(s^2, 1)`` (Durbin & Koopman 2012, sec. 5.1, the big-kappa approximation).
* ``q`` by maximum likelihood when not given: a grid over ``log10 q in [-6, 6]`` (``q`` is per unit of ``sigma^2``, and
  with ``x`` in daily decimal returns the slope's natural ``q`` is of order ``1 / E[x^2] ~ 1e4``) then a bounded Brent search on
  the best bracket.
* Smoother: Rauch, Tung & Striebel (1965), ``theta_{t|n} = theta_{t|t} + P_{t|t} P_{t+1|t}^{-1}
  (theta_{t+1|n} - theta_{t|t})``. The smoothed path uses the whole sample: it is for display only and never
  feeds a score. Only :attr:`KalmanBeta.filtered` (``E[beta_t | y_1..y_t]``) is a forecast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

ALPHA_SCALE = 0.01
KAPPA = 1e8
DIFFUSE = 2
MIN_OBS = 30
LOG10_Q_GRID = np.arange(-6.0, 6.01, 0.5)
REFERENCE = ("Harvey (1989), Forecasting, Structural Time Series Models and the Kalman Filter; Durbin & Koopman "
             "(2012), Time Series Analysis by State Space Methods; RTS smoother: Rauch, Tung & Striebel (1965)")


@dataclass
class KalmanBeta:
    """Filtered and smoothed coefficients of ``y_t = alpha_t + beta_t x_t + eps_t``."""

    filtered: pd.Series            # E[beta_t | data to t] -- the only forecast
    smoothed: pd.Series            # E[beta_t | all data] -- display only
    filtered_alpha: pd.Series
    smoothed_alpha: pd.Series
    filtered_se: pd.Series         # sd of beta_t | data to t
    q: float
    sigma2: float
    loglik: float
    q_estimated: bool


def _filter(y: np.ndarray, x: np.ndarray, q: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Forward pass in sigma^2 units. Returns filtered states (n x 2), filtered covariances (n x 3: p11, p12, p22),
    prediction errors v and their variances F."""
    n = y.size
    qa, qb = q * ALPHA_SCALE ** 2, q
    a, b = 0.0, 0.0
    p11, p12, p22 = KAPPA * ALPHA_SCALE ** 2, 0.0, KAPPA
    st = np.empty((n, 2))
    pc = np.empty((n, 3))
    v_out = np.empty(n)
    f_out = np.empty(n)
    for t in range(n):
        xt = float(x[t])
        p11 += qa
        p22 += qb
        v = float(y[t]) - a - b * xt
        k1 = p11 + p12 * xt
        k2 = p12 + p22 * xt
        f = k1 + k2 * xt + 1.0
        a += k1 * v / f
        b += k2 * v / f
        p11 -= k1 * k1 / f
        p12 -= k1 * k2 / f
        p22 -= k2 * k2 / f
        st[t, 0], st[t, 1] = a, b
        pc[t, 0], pc[t, 1], pc[t, 2] = p11, p12, p22
        v_out[t], f_out[t] = v, f
    return st, pc, v_out, f_out


def _concentrated(v: np.ndarray, f: np.ndarray) -> tuple[float, float]:
    """(log L_c, sigma^2_hat) from the prediction errors, the first DIFFUSE observations excluded."""
    v, f = v[DIFFUSE:], f[DIFFUSE:]
    m = v.size
    s2 = float(np.sum(v * v / f) / m)
    if not s2 > 0:
        return -math.inf, 0.0
    return float(-0.5 * m * (math.log(2.0 * math.pi) + 1.0 + math.log(s2)) - 0.5 * np.sum(np.log(f))), s2


def concentrated_loglik(y: np.ndarray, x: np.ndarray, q: float) -> float:
    """The concentrated log-likelihood ``log L_c(q)`` (Harvey 1989, eq. 3.4.8)."""
    _, _, v, f = _filter(np.asarray(y, dtype=float), np.asarray(x, dtype=float), float(q))
    return _concentrated(v, f)[0]


def ml_q(y: np.ndarray, x: np.ndarray) -> float:
    """``argmax_q log L_c(q)``: grid over ``log10 q`` then a bounded Brent search on the best bracket."""
    y, x = np.asarray(y, dtype=float), np.asarray(x, dtype=float)
    grid = [concentrated_loglik(y, x, 10.0 ** g) for g in LOG10_Q_GRID]
    k = int(np.argmax(grid))
    lo = LOG10_Q_GRID[max(k - 1, 0)]
    hi = LOG10_Q_GRID[min(k + 1, len(LOG10_Q_GRID) - 1)]
    res = minimize_scalar(lambda g: -concentrated_loglik(y, x, 10.0 ** g), bounds=(lo, hi), method="bounded",
                          options={"xatol": 0.01})
    best = float(res.x) if -float(res.fun) >= grid[k] else float(LOG10_Q_GRID[k])
    return 10.0 ** best


def _smooth(st: np.ndarray, pc: np.ndarray, q: float) -> np.ndarray:
    n = st.shape[0]
    out = st.copy()
    qa, qb = q * ALPHA_SCALE ** 2, q
    for t in range(n - 2, -1, -1):
        p11, p12, p22 = pc[t]
        # P_{t+1|t} = P_{t|t} + Q; C = P_{t|t} P_{t+1|t}^-1
        m11, m12, m22 = p11 + qa, p12, p22 + qb
        det = m11 * m22 - m12 * m12
        i11, i12, i22 = m22 / det, -m12 / det, m11 / det
        c11, c12 = p11 * i11 + p12 * i12, p11 * i12 + p12 * i22
        c21, c22 = p12 * i11 + p22 * i12, p12 * i12 + p22 * i22
        d0, d1 = out[t + 1, 0] - st[t, 0], out[t + 1, 1] - st[t, 1]
        out[t, 0] = st[t, 0] + c11 * d0 + c12 * d1
        out[t, 1] = st[t, 1] + c21 * d0 + c22 * d1
    return out


def dynamic_beta(y: pd.Series, x: pd.Series, *, q: float | None = None) -> KalmanBeta:
    """Random-walk ``(alpha_t, beta_t)`` of ``y`` on ``x`` (aligned daily return series, same index, no NaN).

    ``q`` (state-noise to observation-noise ratio) by maximum likelihood when ``None``. Note that an ML ``q``
    is estimated on the whole of ``y``/``x``: a caller scoring forecasts estimates it on data up to the forecast
    date (as products/beta_league.py does); with ``q`` fixed, the filtered value at ``t`` uses no data after ``t``.
    """
    if not (isinstance(y, pd.Series) and isinstance(x, pd.Series)):
        raise TypeError("y and x must be Series")
    if len(y) != len(x) or not y.index.equals(x.index):
        raise ValueError("y and x must share one index")
    yv, xv = y.to_numpy(dtype=float), x.to_numpy(dtype=float)
    if not (np.isfinite(yv).all() and np.isfinite(xv).all()):
        raise ValueError("y and x contain NaN/inf")
    if yv.size < MIN_OBS:
        raise ValueError(f"need at least {MIN_OBS} observations; have {yv.size}")
    estimated = q is None
    qq = ml_q(yv, xv) if q is None else float(q)
    if qq < 0 or not math.isfinite(qq):
        raise ValueError(f"q must be finite and >= 0; got {q}")
    st, pc, v, f = _filter(yv, xv, qq)
    ll, s2 = _concentrated(v, f)
    sm = _smooth(st, pc, qq)
    idx = y.index
    return KalmanBeta(
        filtered=pd.Series(st[:, 1], idx, name="beta_filtered"),
        smoothed=pd.Series(sm[:, 1], idx, name="beta_smoothed"),
        filtered_alpha=pd.Series(st[:, 0], idx, name="alpha_filtered"),
        smoothed_alpha=pd.Series(sm[:, 0], idx, name="alpha_smoothed"),
        filtered_se=pd.Series(np.sqrt(np.maximum(pc[:, 2], 0.0) * s2), idx, name="beta_filtered_se"),
        q=qq, sigma2=s2, loglik=ll, q_estimated=estimated)


__all__ = ["ALPHA_SCALE", "KalmanBeta", "REFERENCE", "concentrated_loglik", "dynamic_beta", "ml_q"]
