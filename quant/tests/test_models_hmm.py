"""Lane M, M7: the Gaussian HMM (own EM). Hand-built inputs check the algorithm; none reaches an artifact."""

from __future__ import annotations

import numpy as np

from ohcamel_quant.models.hmm import HMMFit, filtered, fit_best, order_by, select_k, smoothed


def _two_state():
    return HMMFit(pi=np.array([0.5, 0.5]), A=np.array([[0.9, 0.1], [0.2, 0.8]]), means=np.array([[0.0], [2.0]]),
                  covs=np.array([[[1.0]], [[1.0]]]), loglik=0.0, n_iter=0, converged=True)


def test_forward_filter_by_hand():
    """x = (0, 2). t=0: P ~ (0.5 phi(0), 0.5 phi(-2)) -> (e^2, 1)/(1 + e^2) = (0.880797, 0.119203).
    t=1: predicted = P0 A = (0.816558, 0.183442); times (phi(0), phi(2)) at x=2 ~ (e^-2, 1):
    (0.816558 e^-2, 0.183442) normalised = (0.375944, 0.624056)."""
    f = filtered(_two_state(), np.array([[0.0], [2.0]]))
    np.testing.assert_allclose(f[0], [0.88079708, 0.11920292], rtol=1e-7)
    np.testing.assert_allclose(f[1], [0.37594377, 0.62405623], rtol=1e-7)
    s = smoothed(_two_state(), np.array([[0.0], [2.0]]))
    np.testing.assert_allclose(s[1], f[1], rtol=1e-12)  # the last smoothed equals the last filtered
    np.testing.assert_allclose(s.sum(axis=1), 1.0)


def _simulate(n=3000, seed=4):
    rng = np.random.default_rng(seed)
    A = np.array([[0.95, 0.05], [0.10, 0.90]])
    s = np.zeros(n, dtype=int)
    for t in range(1, n):
        s[t] = rng.choice(2, p=A[s[t - 1]])
    x = np.where(s == 0, rng.normal(0.0, 1.0, n), rng.normal(3.0, 1.0, n))
    return x[:, None], A


def test_em_recovers_known_parameters():
    x, A = _simulate()
    fit = order_by(fit_best(x, 2, restarts=5, seed=1), 0)
    np.testing.assert_allclose(fit.means[:, 0], [0.0, 3.0], atol=0.15)
    np.testing.assert_allclose(np.diag(fit.A), np.diag(A), atol=0.03)
    assert fit.converged


def test_bic_picks_two_states_and_ordering_is_consistent():
    x, _ = _simulate()
    best, table = select_k(x, (2, 3), restarts=3, seed=1)
    assert best.k == 2 and list(table["k"]) == [2, 3]
    rev = HMMFit(best.pi[::-1], best.A[::-1, ::-1], best.means[::-1], best.covs[::-1], best.loglik, best.n_iter, True)
    np.testing.assert_allclose(filtered(order_by(rev, 0), x), filtered(order_by(best, 0), x), rtol=1e-10)
