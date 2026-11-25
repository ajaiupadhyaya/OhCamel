"""Completion task 4.7: DCC(1,1) with GJR-GARCH-t marginals (Engle 2002), two-step quasi-likelihood."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.risk import dcc


def _simulate(t: int, a: float, b: float, seed: int) -> pd.DataFrame:
    """A DCC(1,1) panel with GJR-GARCH(1,1) marginals and unit-variance multivariate-t (nu = 8) shocks:
    Q_t = (1-a-b) Qbar + a z_{t-1} z_{t-1}' + b Q_{t-1}, R_t = diag(Q_t)^-1/2 Q_t diag(Q_t)^-1/2, z_t = chol(R_t) e_t,
    r_it = sigma_it z_it with sigma^2_it = w + (al + g 1[r<0]) r^2 + be sigma^2 (decimal units)."""
    rng = np.random.default_rng(seed)
    qbar = np.array([[1.0, 0.5, 0.3, 0.2, 0.4],
                     [0.5, 1.0, 0.4, 0.3, 0.2],
                     [0.3, 0.4, 1.0, 0.5, 0.3],
                     [0.2, 0.3, 0.5, 1.0, 0.4],
                     [0.4, 0.2, 0.3, 0.4, 1.0]])
    n = qbar.shape[0]
    nu = 8.0
    om, al, ga, be = 2e-6, 0.03, 0.08, 0.90
    s2 = np.full(n, om / (1 - al - ga / 2 - be))
    q = qbar.copy()
    z_prev = np.zeros(n)
    out = np.empty((t, n))
    for i in range(t):
        if i:
            q = (1 - a - b) * qbar + a * np.outer(z_prev, z_prev) + b * q
        d = 1.0 / np.sqrt(np.diag(q))
        r = q * np.outer(d, d)
        e = rng.standard_normal(n) / np.sqrt(rng.chisquare(nu) / nu) * np.sqrt((nu - 2) / nu)
        z = np.linalg.cholesky(r) @ e
        ret = np.sqrt(s2) * z
        out[i] = ret
        s2 = om + (al + ga * (ret < 0)) * ret ** 2 + be * s2
        z_prev = z
    return pd.DataFrame(out, columns=[f"A{k}" for k in range(n)])


@pytest.fixture(scope="module")
def sim_fit():
    return dcc.fit(_simulate(3000, 0.05, 0.90, seed=4))


def test_dcc_recovers_a_and_b_within_0_03(sim_fit):
    assert abs(sim_fit.a - 0.05) < 0.03 and abs(sim_fit.b - 0.90) < 0.03
    assert sim_fit.a >= 0 and sim_fit.b >= 0 and sim_fit.a + sim_fit.b < 1
    assert len(sim_fit.marginals) == 5 and all(m.kind == "gjr" for m in sim_fit.marginals)


def test_dcc_forecast_is_a_positive_definite_covariance_reverting_to_the_target(sim_fit):
    h1 = sim_fit.forecast(1)
    assert h1.shape == (5, 5)
    np.testing.assert_allclose(h1, h1.T)
    assert np.linalg.eigvalsh(h1).min() > 0
    np.linalg.cholesky(h1)
    # Engle's h-step approximation: R_{T+h} -> Rbar as h grows (a + b < 1), the variances -> their GARCH long run
    far = sim_fit.forecast(2000)
    d = 1.0 / np.sqrt(np.diag(far))
    np.testing.assert_allclose(far * np.outer(d, d), sim_fit.rbar, atol=1e-6)
    lr = np.array([m.omega / (1 - m.persistence) for m in sim_fit.marginals]) / 1e4
    np.testing.assert_allclose(np.diag(far), lr, rtol=1e-4)


def test_dcc_one_step_correlation_is_the_engle_recursion_by_hand(sim_fit):
    """R_{T+1} from Q_{T+1} = (1-a-b) Qbar + a z_T z_T' + b Q_T, normalised to unit diagonal."""
    f = sim_fit
    q1 = (1 - f.a - f.b) * f.qbar + f.a * np.outer(f.z[-1], f.z[-1]) + f.b * f.q_last
    d = 1.0 / np.sqrt(np.diag(q1))
    h1 = f.forecast(1)
    s = 1.0 / np.sqrt(np.diag(h1))
    np.testing.assert_allclose(h1 * np.outer(s, s), q1 * np.outer(d, d), atol=1e-10)


@pytest.mark.parametrize(("a", "b"), [(0.02, 0.979999), (0.9999, 0.0), (0.0, 0.9999)])
def test_dcc_forecast_stays_positive_definite_at_the_a_plus_b_boundary(sim_fit, a, b):
    """Near a + b -> 1 the target's weight vanishes; with b ~ 0 Q_{T+1} is nearly the rank-one z z'. The returned
    forecast is still positive definite (Cholesky succeeds) at every horizon."""
    f = dcc.refit_correlation(sim_fit, a, b)
    for h in (1, 2, 21):
        m = f.forecast(h)
        assert np.linalg.eigvalsh(m).min() > 0
        np.linalg.cholesky(m)


def test_dcc_rejects_a_plus_b_at_or_above_one(sim_fit):
    with pytest.raises(ValueError):
        dcc.refit_correlation(sim_fit, 0.5, 0.5)


def test_dcc_on_real_etfs_is_stationary_and_positive_definite(etf_returns):
    f = dcc.fit(etf_returns.dropna().tail(504))
    assert 0 <= f.a and 0 <= f.b and f.a + f.b < 1
    assert np.linalg.eigvalsh(f.forecast(1)).min() > 0
    assert list(f.names) == list(etf_returns.columns)
