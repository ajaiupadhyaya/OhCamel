"""Statistical parity helpers for random kernels (compute plan A1, contract II.4).

Rust and the NumPy reference draw from different generators (ChaCha20 vs
PCG64), so a random kernel's two outputs are two independent samples of one
distribution. With a the Rust sample and b the reference one:

* means: |mean_a - mean_b| <= k sqrt(se_a^2 + se_b^2), se = sd / sqrt(n), k = 3;
* standard deviations: |sd_a - sd_b| <= k sqrt(v_a + v_b), v = (m4 - sd^4) / (4 sd^2 n)
  (the delta method for the sample sd, m4 the fourth central moment), k = 3;
* the quantile at tail probability p (the binomial rule): a's order statistic at
  rank ceil(n_a p) lies between b's order statistics at ranks
  n_b p -+ k sqrt(2 n_b p (1 - p)), k = 2. The sqrt(2) is there because both
  quantiles are estimates; the rank's standard error alone is sqrt(n p (1 - p)).

The tests use fixed seeds, so each assertion is deterministic.
"""

from __future__ import annotations

import math

import numpy as np


def _sd_and_se(x: np.ndarray) -> tuple[float, float]:
    d = x - x.mean()
    s2 = float(np.mean(d * d))
    m4 = float(np.mean(d**4))
    return math.sqrt(s2), math.sqrt(max(m4 - s2 * s2, 0.0) / (4.0 * s2 * x.size))


def assert_same_mean_sd(a: np.ndarray, b: np.ndarray, k: float = 3.0) -> None:
    a, b = np.asarray(a, float), np.asarray(b, float)
    se = math.sqrt(a.var() / a.size + b.var() / b.size)
    assert abs(a.mean() - b.mean()) <= k * se, (a.mean(), b.mean(), se)
    sa, va = _sd_and_se(a)
    sb, vb = _sd_and_se(b)
    se_sd = math.sqrt(va * va + vb * vb)
    assert abs(sa - sb) <= k * se_sd, (sa, sb, se_sd)


def assert_same_quantile(a: np.ndarray, b: np.ndarray, p: float, k: float = 2.0) -> None:
    a, b = np.sort(np.asarray(a, float)), np.sort(np.asarray(b, float))
    qa = a[max(0, math.ceil(a.size * p) - 1)]
    n = b.size
    half = k * math.sqrt(2.0 * n * p * (1.0 - p))
    lo = min(n - 1, max(0, math.floor(n * p - half) - 1))
    hi = min(n - 1, max(0, math.ceil(n * p + half) - 1))
    assert b[lo] <= qa <= b[hi], (qa, b[lo], b[hi])


def assert_same_distribution(a: np.ndarray, b: np.ndarray, p: float = 0.01) -> None:
    assert_same_mean_sd(a, b)
    assert_same_quantile(a, b, p)
