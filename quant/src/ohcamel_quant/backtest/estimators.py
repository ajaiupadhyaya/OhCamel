"""Causal risk estimators shared by the engine overlay and the strategies.

Every function here returns, at row ``t``, an estimate that uses returns with
index ``<= t`` only (never a centred or full-sample statistic).

References
----------
* J.P. Morgan/Reuters (1996). *RiskMetrics -- Technical Document*, 4th ed.
  (EWMA covariance ``S_t = lam S_{t-1} + (1 - lam) r_t r_t'``).
* Moskowitz, T., Ooi, Y. H. & Pedersen, L. H. (2012). Time Series Momentum.
  *Journal of Financial Economics* 104(2), eq. (1) -- EWMA variance with
  centre of mass 60 days, annualized with 261.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def halflife_to_lambda(halflife: float) -> float:
    """Decay ``lam = 0.5 ** (1 / halflife)`` (weight halves every ``halflife`` sessions)."""
    return 0.5 ** (1.0 / halflife)


def ewma_cov_at(returns: np.ndarray, rows: np.ndarray, halflife: float, min_obs: int) -> dict[int, np.ndarray]:
    """Zero-mean EWMA covariance (RiskMetrics 1996) evaluated at the given row
    positions. The recursion starts from the sample second-moment matrix of the
    first ``min_obs`` rows; rows ``< min_obs - 1`` get no estimate.

    ``S_t = lam S_{t-1} + (1 - lam) r_t r_t'`` with ``lam = 0.5**(1/halflife)``.
    Rows of ``returns`` containing NaN leave the estimate unchanged.
    """
    lam = halflife_to_lambda(halflife)
    want = set(int(r) for r in rows)
    out: dict[int, np.ndarray] = {}
    if len(returns) < min_obs or not want:
        return out
    last = max(want)
    head = returns[:min_obs]
    head = head[np.all(np.isfinite(head), axis=1)]
    if len(head) < 2:
        return out
    s = head.T @ head / len(head)
    if min_obs - 1 in want:
        out[min_obs - 1] = s.copy()
    for t in range(min_obs, last + 1):
        r = returns[t]
        if np.all(np.isfinite(r)):
            s = lam * s + (1.0 - lam) * np.outer(r, r)
        if t in want:
            out[t] = s.copy()
    return out


def rolling_cov_at(returns: np.ndarray, rows: np.ndarray, window: int) -> dict[int, np.ndarray]:
    """Sample covariance (ddof=1) of the trailing ``window`` rows ending at each
    requested row (inclusive); rows with fewer than ``window`` finite rows get none."""
    out: dict[int, np.ndarray] = {}
    for t in rows:
        t = int(t)
        if t + 1 < window:
            continue
        blk = returns[t + 1 - window: t + 1]
        blk = blk[np.all(np.isfinite(blk), axis=1)]
        if len(blk) < window:
            continue
        out[t] = np.atleast_2d(np.cov(blk, rowvar=False, ddof=1))
    return out


def ewma_vol(returns: pd.DataFrame | pd.Series, com: float = 60.0, periods: float = 261.0,
             min_periods: int | None = None) -> pd.DataFrame | pd.Series:
    """Ex-ante annualized volatility of Moskowitz, Ooi & Pedersen (2012), eq. (1)::

        sigma_t^2 = periods * sum_i (1 - d) d^i (r_{t-i} - rbar_t)^2,  d/(1-d) = com

    (centre of mass ``com`` = 60 days, ``periods`` = 261 in the paper). Uses
    pandas' exponentially weighted, bias-corrected variance, which is causal."""
    mp = min_periods if min_periods is not None else int(com)
    return returns.ewm(com=com, min_periods=mp).std() * math.sqrt(periods)


def rolling_cov_path(returns: pd.DataFrame, window: int, min_periods: int | None = None) -> np.ndarray:
    """Array ``C[t]`` (T x N x N) of trailing-window sample covariances computed
    from cumulative sums (O(T N^2)); NaN where fewer than ``min_periods`` rows.
    Missing values are treated as absent rows (pairwise counts)."""
    x = returns.to_numpy(dtype=float)
    mp = window if min_periods is None else min_periods
    row_ok = np.isfinite(x).all(axis=1)
    xz = np.where(row_ok[:, None], x, 0.0)
    # trailing-window sums as differences of cumulative sums (in place: T x N x N)
    cnt = np.cumsum(row_ok.astype(float))
    c = cnt.copy()
    c[window:] -= cnt[:-window]
    s1 = np.cumsum(xz, axis=0)
    m1 = s1.copy()
    m1[window:] -= s1[:-window]
    s2 = np.einsum("ti,tj->tij", xz, xz)
    np.cumsum(s2, axis=0, out=s2)
    m2 = s2.copy()
    m2[window:] -= s2[:-window]
    del s2
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = m1 / c[:, None]
        m2 -= c[:, None, None] * (mean[:, :, None] * mean[:, None, :])
        m2 /= (c - 1.0)[:, None, None]
    m2[c < max(mp, 2)] = np.nan
    return m2
