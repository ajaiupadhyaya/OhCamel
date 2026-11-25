"""Completion task 4.7: a random-walk (time-varying) beta by the Kalman filter, q by maximum likelihood."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.factors import kalman


def _step_panel(seed: int = 3, n: int = 400, at: int = 200) -> tuple[pd.Series, pd.Series]:
    """beta = 0.5 then 1.5 from observation ``at``; market x ~ N(0, 1%^2), idiosyncratic noise sd 0.5%."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    x = rng.standard_normal(n) * 0.01
    beta = np.where(np.arange(n) < at, 0.5, 1.5)
    y = 0.0001 + beta * x + rng.standard_normal(n) * 0.005
    return pd.Series(y, idx, name="y"), pd.Series(x, idx, name="x")


def test_kalman_beta_tracks_a_step_from_0_5_to_1_5_within_40_observations():
    """q by ML. Before the break the filtered beta sits on 0.5; it crosses the midpoint 1.0 within 40 observations;
    over observations 240-249 (40 after the break) it averages within 0.25 of 1.5, and from there on within 0.1.
    (Every criterion also holds for seeds 1-20, so the seed is not chosen to pass.)"""
    y, x = _step_panel()
    kb = kalman.dynamic_beta(y, x)
    assert kb.q > 0 and kb.q_estimated
    f = kb.filtered.to_numpy()
    assert abs(f[190:200].mean() - 0.5) < 0.2
    assert (f[200:240] > 1.0).any()
    assert abs(f[240:250].mean() - 1.5) < 0.25
    assert abs(f[240:].mean() - 1.5) < 0.1
    assert list(kb.filtered.index) == list(y.index) and list(kb.smoothed.index) == list(y.index)


def test_kalman_filtered_value_at_t_uses_no_data_after_t():
    """The truncation test: filter on data[:t] and on data[:t+k] with the same q; the filtered beta (and alpha)
    at every date up to t is identical to float precision. The smoother is the opposite: it moves."""
    y, x = _step_panel(seed=9)
    t, k = 250, 60
    a = kalman.dynamic_beta(y.iloc[:t], x.iloc[:t], q=1e-3)
    b = kalman.dynamic_beta(y.iloc[:t + k], x.iloc[:t + k], q=1e-3)
    np.testing.assert_array_equal(a.filtered.to_numpy(), b.filtered.iloc[:t].to_numpy())
    np.testing.assert_array_equal(a.filtered_alpha.to_numpy(), b.filtered_alpha.iloc[:t].to_numpy())
    assert not np.allclose(a.smoothed.to_numpy(), b.smoothed.iloc[:t].to_numpy())


def test_kalman_with_q_zero_is_recursive_least_squares():
    """q = 0: constant coefficients, so the last filtered (alpha, beta) is the full-sample OLS fit (up to the
    diffuse prior's 1/kappa)."""
    y, x = _step_panel(seed=5, n=300, at=10_000)
    kb = kalman.dynamic_beta(y, x, q=0.0)
    xm = np.column_stack([np.ones(len(x)), x.to_numpy()])
    ols = np.linalg.lstsq(xm, y.to_numpy(), rcond=None)[0]
    assert kb.filtered.iloc[-1] == pytest.approx(ols[1], abs=1e-6)
    assert kb.filtered_alpha.iloc[-1] == pytest.approx(ols[0], abs=1e-8)
    np.testing.assert_allclose(kb.smoothed.to_numpy(), kb.filtered.iloc[-1], atol=1e-6)  # nothing to smooth


def test_kalman_ml_q_is_the_argmax_of_the_concentrated_likelihood():
    y, x = _step_panel(seed=12)
    kb = kalman.dynamic_beta(y, x)
    ll = kalman.concentrated_loglik(y.to_numpy(), x.to_numpy(), kb.q)
    for f in (0.5, 2.0):
        assert kalman.concentrated_loglik(y.to_numpy(), x.to_numpy(), kb.q * f) <= ll + 1e-9
    assert kb.loglik == pytest.approx(ll)


def test_kalman_rejects_misaligned_or_short_input():
    y, x = _step_panel()
    with pytest.raises(ValueError):
        kalman.dynamic_beta(y, x.iloc[:-1])
    with pytest.raises(ValueError):
        kalman.dynamic_beta(y.iloc[:5], x.iloc[:5])
