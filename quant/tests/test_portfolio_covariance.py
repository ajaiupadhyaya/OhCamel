"""Covariance estimators on the committed real ETF returns."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.covariance import LedoitWolf

from ohcamel_quant.portfolio import covariance as cv


@pytest.fixture(scope="module")
def rets(etf_returns) -> pd.DataFrame:
    return etf_returns.dropna()


def _lw_cc_direct(x: np.ndarray) -> float:
    """Ledoit & Wolf (2004) 'Honey' shrinkage intensity with explicit loops over the paper's sums."""
    t, n = x.shape
    m = x.mean(axis=0)
    y = x - m
    s = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            s[i, j] = sum(y[k, i] * y[k, j] for k in range(t)) / t
    r = np.array([[s[i, j] / np.sqrt(s[i, i] * s[j, j]) for j in range(n)] for i in range(n)])
    rbar = sum(r[i, j] for i in range(n) for j in range(i + 1, n)) * 2 / (n * (n - 1))
    f = np.array([[s[i, i] if i == j else rbar * np.sqrt(s[i, i] * s[j, j]) for j in range(n)] for i in range(n)])
    pi = 0.0
    pi_ii = np.zeros(n)
    for i in range(n):
        for j in range(n):
            v = np.mean((y[:, i] * y[:, j] - s[i, j]) ** 2)
            pi += v
            if i == j:
                pi_ii[i] = v
    rho = pi_ii.sum()
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            th_ii = np.mean((y[:, i] ** 2 - s[i, i]) * (y[:, i] * y[:, j] - s[i, j]))
            th_jj = np.mean((y[:, j] ** 2 - s[j, j]) * (y[:, i] * y[:, j] - s[i, j]))
            rho += rbar / 2 * (np.sqrt(s[j, j] / s[i, i]) * th_ii + np.sqrt(s[i, i] / s[j, j]) * th_jj)
    gamma = np.sum((f - s) ** 2)
    kappa = (pi - rho) / gamma
    return max(0.0, min(1.0, kappa / t))


def test_all_estimators_psd_symmetric_annualized(rets):
    for m in cv.ESTIMATORS:
        est = cv.estimate_covariance(rets, m)
        c = est.cov.to_numpy()
        assert est.cov.shape == (9, 9) and list(est.cov.columns) == list(rets.columns)
        assert np.allclose(c, c.T)
        assert np.linalg.eigvalsh(c).min() > 0, m
        # annualized: typical ETF vols in (5%, 60%)
        vol = np.sqrt(np.diag(c))
        assert np.all((vol > 0.03) & (vol < 0.8)), (m, vol)
    s = cv.sample_cov(rets).cov.to_numpy()
    assert np.allclose(s, np.cov(rets.to_numpy(), rowvar=False) * 252)


def test_lw_constant_correlation_matches_paper_formula(rets):
    sub = rets.iloc[-300:, :5]
    est = cv.lw_constant_correlation(sub)
    assert 0.0 <= est.shrinkage <= 1.0
    assert est.shrinkage == pytest.approx(_lw_cc_direct(sub.to_numpy()), rel=1e-9, abs=1e-12)
    # Sigma = delta F + (1 - delta) S, both with MLE S
    x = sub.to_numpy()
    y = x - x.mean(0)
    s = y.T @ y / len(x)
    sd = np.sqrt(np.diag(s))
    corr = s / np.outer(sd, sd)
    rbar = (corr.sum() - 5) / 20
    f = rbar * np.outer(sd, sd)
    np.fill_diagonal(f, np.diag(s))
    d = est.shrinkage
    assert np.allclose(est.cov.to_numpy() / 252, d * f + (1 - d) * s)
    # full sample too
    full = cv.lw_constant_correlation(rets)
    assert 0.0 <= full.shrinkage <= 1.0
    # shrinkage leaves the variances untouched
    assert np.allclose(np.diag(full.cov), np.diag(rets.cov(ddof=0).to_numpy() * 252))


def test_lw_identity_and_oas(rets):
    lw = cv.lw_identity(rets)
    ref = LedoitWolf().fit(rets.to_numpy())
    assert lw.shrinkage == pytest.approx(ref.shrinkage_)
    assert np.allclose(lw.cov.to_numpy(), ref.covariance_ * 252)
    o = cv.oas(rets)
    assert 0.0 <= o.shrinkage <= 1.0
    x = rets.to_numpy()
    s = np.cov(x, rowvar=False, ddof=0)
    p, t = s.shape[0], x.shape[0]
    tr, tr2 = np.trace(s), np.trace(s @ s)
    rho = min(1.0, ((1 - 2 / p) * tr2 + tr ** 2) / ((t + 1 - 2 / p) * (tr2 - tr ** 2 / p)))
    assert o.shrinkage == pytest.approx(rho)
    assert np.allclose(o.cov.to_numpy() / 252, (1 - rho) * s + rho * tr / p * np.eye(p))
    # shrinking towards a scaled identity improves conditioning
    assert o.condition_number() <= cv.sample_cov(rets).condition_number()
    assert lw.condition_number() < cv.sample_cov(rets).condition_number()


def test_ewma_matches_recursion(rets):
    lam = 0.94
    sub = rets.iloc[-400:]
    est = cv.ewma_cov(sub, lam)
    x = sub.to_numpy()
    s = np.zeros((9, 9))
    for r in x:  # Sigma_t = lam Sigma_{t-1} + (1 - lam) r r'
        s = lam * s + (1 - lam) * np.outer(r, r)
    s /= 1 - lam ** len(x)  # the estimator renormalises the finite-sample weights
    assert np.allclose(est.cov.to_numpy() / 252, s)
    with pytest.raises(ValueError):
        cv.ewma_cov(sub, 1.2)


def test_marcenko_pastur_density_and_spectrum(rets):
    q = 5.0
    lo, hi = cv.mp_bounds(1.0, q)
    x = np.linspace(lo, hi, 20001)
    assert np.trapezoid(cv.mp_pdf(x, 1.0, q), x) == pytest.approx(1.0, abs=2e-3)
    spec = cv.spectrum(rets)
    ev = spec["eigenvalues"]
    assert ev.sum() == pytest.approx(9.0)
    assert np.all(np.diff(ev) <= 0)
    assert 0 < spec["sigma2"] < 1
    assert spec["lambda_plus"] == pytest.approx(spec["sigma2"] * (1 + np.sqrt(1 / spec["q"])) ** 2)
    assert spec["n_signal"] == int(np.sum(ev > spec["lambda_plus"]))
    assert spec["n_signal"] >= 1  # the market factor dominates real ETF returns


def test_mp_denoise_constant_residual(rets):
    est = cv.mp_denoise(rets)
    k = est.params["n_signal"]
    c = est.correlation().to_numpy()
    assert np.allclose(np.diag(c), 1.0)
    dn = est.extra["denoised_eigenvalues"]
    raw = est.extra["spectrum"]["eigenvalues"]
    assert dn.sum() == pytest.approx(raw.sum())  # trace preserved
    assert np.allclose(dn[k:], raw[k:].mean())  # noise eigenvalues replaced by their average
    assert np.allclose(dn[:k], raw[:k])
    # variances equal the sample variances
    assert np.allclose(np.diag(est.cov), rets.var(ddof=1).to_numpy() * 252)


def test_input_validation(rets):
    with pytest.raises(ValueError):
        cv.estimate_covariance(rets, "nope")
    with pytest.raises(ValueError):
        cv.sample_cov(rets.iloc[:5])
    bad = rets.copy()
    bad.iloc[3, 2] = np.nan
    with pytest.raises(ValueError):
        cv.sample_cov(bad)


def test_zero_variance_asset_rejected(rets):
    """A constant-price column makes correlations undefined; the constant-correlation
    target used to return an all-NaN matrix silently."""
    bad = rets.iloc[-300:, :3].copy()
    bad.iloc[:, 2] = 0.0
    for m in cv.ESTIMATORS:
        with pytest.raises(ValueError, match="zero return variance"):
            cv.estimate_covariance(bad, m)
