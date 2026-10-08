"""Gaussian hidden Markov model by EM (Baum-Welch), own implementation (compute plan M7; EXP-Q02).

Scaled forward-backward (Rabiner 1989): ``alpha[t]`` is the FILTERED
probability P(s_t | x_1..t), the only quantity EXP-Q02 scores. Full covariance
per state, ridge ``reg`` on the diagonal. Restarts are seeded; the highest
likelihood wins. BIC counts (k-1) + k(k-1) + k d + k d(d+1)/2 parameters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.linalg import cho_factor, solve_triangular

LOG2PI = math.log(2.0 * math.pi)


@dataclass(frozen=True)
class HMMFit:
    pi: np.ndarray
    A: np.ndarray
    means: np.ndarray       # (k, d)
    covs: np.ndarray        # (k, d, d)
    loglik: float
    n_iter: int
    converged: bool

    @property
    def k(self) -> int:
        return int(self.pi.size)

    @property
    def d(self) -> int:
        return int(self.means.shape[1])

    def bic(self, n: int) -> float:
        k, d = self.k, self.d
        p = (k - 1) + k * (k - 1) + k * d + k * d * (d + 1) / 2
        return -2.0 * self.loglik + p * math.log(n)


def _log_b(x: np.ndarray, means: np.ndarray, covs: np.ndarray) -> np.ndarray:
    t, d = x.shape
    out = np.empty((t, means.shape[0]))
    for j in range(means.shape[0]):
        c, _ = cho_factor(covs[j], lower=True)
        L = np.tril(c)
        z = solve_triangular(L, (x - means[j]).T, lower=True)
        out[:, j] = -0.5 * (d * LOG2PI + 2.0 * np.log(np.diag(L)).sum() + (z * z).sum(axis=0))
    return out


def _forward_backward(lb: np.ndarray, pi: np.ndarray, A: np.ndarray):
    t_len, k = lb.shape
    m = lb.max(axis=1, keepdims=True)
    b = np.exp(lb - m)
    alpha, beta, c = np.empty((t_len, k)), np.ones((t_len, k)), np.empty(t_len)
    a = pi * b[0]
    c[0] = a.sum()
    alpha[0] = a / c[0]
    for t in range(1, t_len):
        a = (alpha[t - 1] @ A) * b[t]
        c[t] = a.sum()
        alpha[t] = a / c[t]
    for t in range(t_len - 2, -1, -1):
        beta[t] = (A @ (b[t + 1] * beta[t + 1])) / c[t + 1]
    return alpha, beta, b, c, float(np.log(c).sum() + m.sum())


def filtered(fit: HMMFit, x: np.ndarray) -> np.ndarray:
    return _forward_backward(_log_b(np.asarray(x, float), fit.means, fit.covs), fit.pi, fit.A)[0]


def smoothed(fit: HMMFit, x: np.ndarray) -> np.ndarray:
    alpha, beta, *_ = _forward_backward(_log_b(np.asarray(x, float), fit.means, fit.covs), fit.pi, fit.A)
    g = alpha * beta
    return g / g.sum(axis=1, keepdims=True)


def fit_hmm(x: np.ndarray, k: int, seed: int, max_iter: int = 500, tol: float = 1e-6, reg: float = 1e-6) -> HMMFit:
    x = np.asarray(x, dtype=float)
    t_len, d = x.shape
    rng = np.random.default_rng(seed)
    means = x[rng.choice(t_len, size=k, replace=False)].copy()
    base = np.cov(x, rowvar=False).reshape(d, d) + reg * np.eye(d)
    covs = np.repeat(base[None], k, axis=0)
    pi = np.full(k, 1.0 / k)
    A = np.full((k, k), 0.1 / (k - 1)) + np.eye(k) * (0.9 - 0.1 / (k - 1)) if k > 1 else np.ones((1, 1))
    prev, converged, it = -math.inf, False, 0
    while it < max_iter:
        it += 1
        alpha, beta, b, c, ll = _forward_backward(_log_b(x, means, covs), pi, A)
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True)
        xi = np.zeros((k, k))
        for t in range(1, t_len):
            xi += alpha[t - 1][:, None] * A * (b[t] * beta[t])[None, :] / c[t]
        pi = gamma[0]
        A = xi / xi.sum(axis=1, keepdims=True)
        nk = gamma.sum(axis=0)
        means = (gamma.T @ x) / nk[:, None]
        for j in range(k):
            dx = x - means[j]
            covs[j] = (dx * gamma[:, j, None]).T @ dx / nk[j] + reg * np.eye(d)
        if ll - prev < tol * max(1.0, abs(ll)):
            converged = True
            break
        prev = ll
    _, _, _, _, ll = _forward_backward(_log_b(x, means, covs), pi, A)
    return HMMFit(pi, A, means, covs, ll, it, converged)


def fit_best(x: np.ndarray, k: int, restarts: int = 20, seed: int = 0) -> HMMFit:
    fits = []
    for r in range(restarts):
        try:
            fits.append(fit_hmm(x, k, seed + r))
        except np.linalg.LinAlgError:
            continue  # a degenerate start (singular covariance); the other restarts decide
    if not fits:
        raise ValueError(f"every restart of the {k}-state HMM failed")
    return max(fits, key=lambda f: f.loglik)


def select_k(x: np.ndarray, ks: tuple[int, ...], restarts: int = 20, seed: int = 0) -> tuple[HMMFit, pd.DataFrame]:
    fits = {k: fit_best(x, k, restarts, seed) for k in ks}
    table = pd.DataFrame([{"k": k, "loglik": f.loglik, "bic": f.bic(len(x)), "converged": f.converged}
                          for k, f in fits.items()])
    return fits[int(table.loc[table["bic"].idxmin(), "k"])], table


def order_by(fit: HMMFit, col: int) -> HMMFit:
    """Relabel states in ascending order of their mean on column ``col`` (fixes label switching)."""
    o = np.argsort(fit.means[:, col], kind="stable")
    return HMMFit(fit.pi[o], fit.A[np.ix_(o, o)], fit.means[o], fit.covs[o], fit.loglik, fit.n_iter, fit.converged)
