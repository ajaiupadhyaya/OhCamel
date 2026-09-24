"""Statistical comparison of Sharpe ratios.

* Jobson & Korkie (1981) with the Memmel (2003) correction, "Performance
  hypothesis testing with the Sharpe ratio", *Finance Letters* 1:
  ``z = (SR_a - SR_b) / sqrt(theta)``,
  ``theta = (1/T) [2 (1 - rho) + 1/2 (SR_a^2 + SR_b^2 - 2 SR_a SR_b rho^2)]``
  (iid normal returns assumed).
* Ledoit & Wolf (2008), "Robust performance hypothesis testing with the Sharpe
  ratio", *Journal of Empirical Finance* 15(5), sec. 3.1: the delta method on
  ``u = (mu_a, mu_b, gamma_a, gamma_b)`` (first and second uncentred moments)
  with a heteroskedasticity- and autocorrelation-consistent (HAC) covariance
  ``Psi`` of ``y_t = (r_at - mu_a, r_bt - mu_b, r_at^2 - gamma_a, r_bt^2 - gamma_b)``:
  ``Delta = f(u) = mu_a/sqrt(gamma_a - mu_a^2) - mu_b/sqrt(gamma_b - mu_b^2)``,
  ``se = sqrt(grad f' Psi grad f / T)``. ``Psi`` uses the Bartlett kernel with the
  Newey-West (1994) plug-in lag ``floor(4 (T/100)^(2/9))`` (the paper also
  discusses a prewhitened QS kernel; results are close at daily frequency).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats

TRADING_DAYS = 252


def _clean(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.shape != b.shape:
        raise ValueError("return series must be aligned")
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if a.size < 30:
        raise ValueError("need at least 30 paired observations")
    if a.std() == 0 or b.std() == 0:
        raise ValueError("a return series has zero variance (e.g. held entirely in cash)")
    return a, b


def memmel_test(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    """Jobson-Korkie test with Memmel's (2003) corrected variance, on excess returns."""
    a, b = _clean(a, b)
    t = a.size
    sa, sb = a.mean() / a.std(ddof=1), b.mean() / b.std(ddof=1)
    rho = float(np.corrcoef(a, b)[0, 1])
    theta = (2 * (1 - rho) + 0.5 * (sa ** 2 + sb ** 2 - 2 * sa * sb * rho ** 2)) / t
    z = (sa - sb) / math.sqrt(theta) if theta > 0 else float("nan")
    return {"test": "jobson_korkie_memmel", "z": z, "p_value": float(2 * stats.norm.sf(abs(z))),
            "sharpe_diff_annual": (sa - sb) * math.sqrt(TRADING_DAYS), "correlation": rho,
            "reference": "Jobson & Korkie (1981); Memmel (2003), Finance Letters 1"}


def _hac(y: np.ndarray, lags: int) -> np.ndarray:
    t = y.shape[0]
    psi = y.T @ y / t
    for j in range(1, lags + 1):
        g = y[j:].T @ y[:-j] / t
        psi += (1 - j / (lags + 1)) * (g + g.T)
    return psi


def ledoit_wolf_test(a: np.ndarray, b: np.ndarray, lags: int | None = None) -> dict[str, Any]:
    """Ledoit & Wolf (2008) HAC delta-method test of ``H0: SR_a = SR_b`` on excess returns."""
    a, b = _clean(a, b)
    t = a.size
    mu_a, mu_b = a.mean(), b.mean()
    g_a, g_b = np.mean(a ** 2), np.mean(b ** 2)
    va, vb = g_a - mu_a ** 2, g_b - mu_b ** 2
    delta = mu_a / math.sqrt(va) - mu_b / math.sqrt(vb)
    grad = np.array([g_a / va ** 1.5, -g_b / vb ** 1.5, -0.5 * mu_a / va ** 1.5, 0.5 * mu_b / vb ** 1.5])
    y = np.column_stack([a - mu_a, b - mu_b, a ** 2 - g_a, b ** 2 - g_b])
    lag = int(math.floor(4 * (t / 100) ** (2 / 9))) if lags is None else int(lags)
    psi = _hac(y, lag)
    se = math.sqrt(max(float(grad @ psi @ grad) / t, 0.0))
    z = delta / se if se > 0 else float("nan")
    return {"test": "ledoit_wolf_hac", "z": z, "p_value": float(2 * stats.norm.sf(abs(z))),
            "sharpe_diff_annual": delta * math.sqrt(TRADING_DAYS),
            "se_annual": se * math.sqrt(TRADING_DAYS), "lags": lag, "kernel": "bartlett",
            "reference": "Ledoit & Wolf (2008), 'Robust performance hypothesis testing with the "
                         "Sharpe ratio', J. Empirical Finance 15(5)"}
