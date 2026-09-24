"""Heteroskedasticity- and autocorrelation-consistent (HAC) long-run covariance.

Newey & West (1987), "A Simple, Positive Semi-definite, Heteroskedasticity and
Autocorrelation Consistent Covariance Matrix", *Econometrica* 55(3), 703-708.

For a (T x k) array of mean-zero moment contributions ``u_t`` the Bartlett-
kernel long-run covariance is

    S = Gamma_0 + sum_{j=1}^{L} w_j (Gamma_j + Gamma_j'),
    Gamma_j = (1/T) sum_{t=j+1}^{T} u_t u_{t-j}',   w_j = 1 - j / (L + 1),

which is positive semi-definite by construction. The default bandwidth is the
Newey & West (1994) Bartlett-kernel rule ``L = floor(4 (T/100)^(2/9))`` (their
non-parametric pilot bandwidth, also the default in e.g. EViews/Greene).
"""

from __future__ import annotations

import math

import numpy as np


def nw_lag_rule(n_obs: int) -> int:
    """Default Newey-West truncation lag ``floor(4 (T/100)^(2/9))``.

    Newey & West (1994), "Automatic Lag Selection in Covariance Matrix
    Estimation", *Review of Economic Studies* 61(4), 631-653: the ``n = 4``,
    ``T^(2/9)`` pilot rate for the Bartlett kernel. (Stock & Watson's textbook
    rule ``0.75 T^(1/3)`` is a different, similar-sized choice.)
    """
    if n_obs <= 0:
        return 0
    return int(math.floor(4.0 * (n_obs / 100.0) ** (2.0 / 9.0)))


def newey_west_lrv(u: np.ndarray, lags: int) -> np.ndarray:
    """Bartlett-kernel long-run covariance ``S`` of mean-zero moments ``u`` (T x k).

    ``u`` is NOT demeaned here: regression scores ``x_t e_t`` are mean-zero by
    construction; callers passing other moments should centre them first.
    Returns a (k x k) matrix (the 1/T-normalised estimate).
    """
    x = np.asarray(u, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    t = x.shape[0]
    if t == 0:
        raise ValueError("newey_west_lrv: no observations")
    lags = max(0, min(int(lags), t - 1))
    s = x.T @ x / t
    for j in range(1, lags + 1):
        g = x[j:].T @ x[:-j] / t
        s += (1.0 - j / (lags + 1.0)) * (g + g.T)
    return s


def ols_hac_cov(x: np.ndarray, resid: np.ndarray, lags: int, small_sample: bool = False) -> np.ndarray:
    """Newey-West sandwich covariance of OLS coefficients.

        V = (X'X)^{-1} [T * S] (X'X)^{-1},  S = newey_west_lrv(X * e, L)

    optionally times ``T / (T - k)`` (``small_sample``). With the default
    ``small_sample=False`` this equals statsmodels'
    ``OLS(...).fit(cov_type='HAC', cov_kwds={'maxlags': L})``.
    """
    x = np.asarray(x, dtype=float)
    e = np.asarray(resid, dtype=float)
    t, k = x.shape
    xtx_inv = np.linalg.inv(x.T @ x)
    s = newey_west_lrv(x * e[:, None], lags)
    v = xtx_inv @ (t * s) @ xtx_inv
    if small_sample:
        v *= t / (t - k)
    return v


__all__ = ["nw_lag_rule", "newey_west_lrv", "ols_hac_cov"]
