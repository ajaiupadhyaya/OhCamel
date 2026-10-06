"""The NumPy reference for every kernel (contract II.4). Each function has the
dispatcher's signature and is the semantic definition the Rust kernel is
tested against. Random kernels draw from np.random.Generator(PCG64(seed)) and
ignore ``threads`` (their output does not depend on it). No I/O."""

from __future__ import annotations

import math

import numpy as np

#: Rows per chunk, as in Rust (compute plan A2: 16k).
CHUNK = 16_384


def tail_count(n: int, alpha: float) -> int:
    """risk.core.tail_count, verbatim: ``max(1, ceil(n (1 - alpha) - 1e-9))``."""
    return max(1, math.ceil(n * (1.0 - alpha) - 1e-9))


# ------------------------------------------------------------------ A2: risk
def var_es_from_pnl(pnl: np.ndarray, alpha: float) -> tuple[float, float]:
    """risk.core.empirical_var_es on the losses -pnl (non-finite dropped)."""
    from ..risk.core import empirical_var_es

    return empirical_var_es(-pnl, alpha)


def fhs_paths(std_resid: np.ndarray, sigma_path_params, weights: np.ndarray, horizon: int,
              n_paths: int, seed: int, threads: int) -> np.ndarray:
    """Filtered historical simulation (Barone-Adesi et al. 1999): see native/kernels/src/fhs.rs."""
    mu, om, al, ga, be, s2n = sigma_path_params.arrays()
    z = std_resid
    n = z.shape[0]
    rng = np.random.Generator(np.random.PCG64(seed))
    out = np.empty(n_paths)
    for lo in range(0, n_paths, CHUNK):
        m = min(CHUNK, n_paths - lo)
        s2 = np.broadcast_to(s2n, (m, s2n.size)).copy()
        logg = np.zeros(m)
        wiped = np.zeros(m, dtype=bool)
        for _ in range(horizon):
            e = np.sqrt(s2) * z[rng.integers(0, n, size=m)]
            rp = ((mu + e) / 100.0) @ weights
            bad = rp <= -1.0
            wiped |= bad
            logg += np.log1p(np.where(bad, 0.0, rp))
            s2 = om + (al + ga * (e < 0)) * e * e + be * s2
        out[lo:lo + m] = np.where(wiped, -1.0, np.expm1(logg))
    return out


def _kendall_corr(returns: np.ndarray, threads: int = 1) -> np.ndarray:
    """R_ij = sin(pi tau_b / 2) (Lindskog, McNeil & Schmock 2003); tau_b by scipy."""
    from scipy.stats import kendalltau

    n, k = returns.shape
    if n < 2:
        raise ValueError(f"returns must be n x k with n >= 2; got {returns.shape}")
    for i in range(k):
        if np.all(returns[:, i] == returns[0, i]):
            raise ValueError(f"column {i} is constant: Kendall's tau is undefined")
    r = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            tau = float(kendalltau(returns[:, i], returns[:, j]).statistic)
            r[i, j] = r[j, i] = math.sin(math.pi / 2.0 * tau)
    return r


def copula_t_paths(returns: np.ndarray, weights: np.ndarray, nu: float, horizon: int, n_paths: int,
                   seed: int, threads: int) -> np.ndarray:
    """Student-t copula with empirical marginals: see native/kernels/src/copula.rs."""
    from scipy.stats import t as student_t

    n, k = returns.shape
    try:
        chol = np.linalg.cholesky(_kendall_corr(returns))
    except np.linalg.LinAlgError:
        raise ValueError("the Kendall sin-transform correlation matrix is not positive definite") from None
    cols = np.sort(returns, axis=0)
    rng = np.random.Generator(np.random.PCG64(seed))
    out = np.empty(n_paths)
    ks = np.arange(k)
    for lo in range(0, n_paths, CHUNK):
        m = min(CHUNK, n_paths - lo)
        logg = np.zeros(m)
        wiped = np.zeros(m, dtype=bool)
        for _ in range(horizon):
            z = rng.standard_normal((m, k)) @ chol.T
            s = np.sqrt(rng.chisquare(nu, size=m) / nu)
            u = student_t.cdf(z / s[:, None], nu)
            idx = np.clip(np.ceil(u * n).astype(np.int64) - 1, 0, n - 1)
            rp = cols[idx, ks] @ weights
            bad = rp <= -1.0
            wiped |= bad
            logg += np.log1p(np.where(bad, 0.0, rp))
        out[lo:lo + m] = np.where(wiped, -1.0, np.expm1(logg))
    return out
