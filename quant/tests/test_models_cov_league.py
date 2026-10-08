"""Lane M, M4: covariance league mechanics (compute plan M4)."""

from __future__ import annotations

import numpy as np
import pytest

from ohcamel_quant.models.cov_league import (
    ESTIMATORS,
    gmv_weights,
    league_returns,
    pca_factor_cov,
    variance_test,
)


def test_gmv_on_a_diagonal_covariance_is_inverse_variance():
    # Sigma = diag(1, 4): w ~ (1/1, 1/4) normalised -> (0.8, 0.2) by hand.
    np.testing.assert_allclose(gmv_weights(np.diag([1.0, 4.0])), [0.8, 0.2])


def test_pca_factor_cov_keeps_the_diagonal_and_is_psd(etf_returns):
    x = etf_returns.dropna().tail(252).to_numpy()
    c = pca_factor_cov(x, k=2)
    s = np.cov(x, rowvar=False)
    np.testing.assert_allclose(np.diag(c), np.diag(s), rtol=1e-10)  # specific variances fill the diagonal exactly
    assert np.linalg.eigvalsh(c).min() > 0


def test_league_returns_hold_weights_for_the_holding_period(etf_returns):
    r = etf_returns.dropna().tail(252 + 63)
    daily, gross = league_returns(r, estimators=("sample", "pca_factor"), est_window=252, hold=21, eval_sessions=63)
    assert list(daily.columns) == ["sample", "pca_factor"] and len(daily) == 63 and len(gross) == 3
    w = gmv_weights(np.cov(r.iloc[:252].to_numpy(), rowvar=False))
    np.testing.assert_allclose(daily["sample"].iloc[:21].to_numpy(), r.iloc[252:273].to_numpy() @ w, rtol=1e-10)


def test_variance_test_is_scale_exact_and_antisymmetric(etf_returns):
    a = etf_returns["SPY"].dropna().tail(500).to_numpy()
    t = variance_test(a, 2.0 * a)
    assert t["diff_log_var"] == pytest.approx(-np.log(4.0))  # Var(2a) = 4 Var(a), by hand
    u = variance_test(etf_returns["TLT"].dropna().tail(500).to_numpy(), a)
    v = variance_test(a, etf_returns["TLT"].dropna().tail(500).to_numpy())
    assert u["stat"] == pytest.approx(-v["stat"]) and u["pvalue"] == pytest.approx(v["pvalue"])
    assert set(ESTIMATORS) >= {"sample", "ewma", "lw_constant_corr", "lw_identity", "oas", "mp_denoise", "pca_factor"}
