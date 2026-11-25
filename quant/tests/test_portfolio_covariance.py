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


# ------------------------------------------------------------------ completion task 4.7: LW (2020) nonlinear shrinkage
def _spread_truth(n: int, seed: int) -> np.ndarray:
    """Ledoit & Wolf's (2020, section 5) baseline population: 20% of the eigenvalues equal 1, 40% equal 3 and 40%
    equal 10, in a random orthogonal basis -- a dispersed spectrum, where shrinking each eigenvalue by its own
    amount should beat one linear intensity."""
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.standard_normal((n, n)))
    k1, k2 = n // 5, 2 * n // 5
    return (q * np.r_[np.ones(k1), np.full(k2, 3.0), np.full(n - k1 - k2, 10.0)]) @ q.T


def test_nonlinear_shrinkage_beats_linear_ledoit_wolf_on_a_simulated_n50_t100_panel():
    """Ledoit & Wolf (2020): on N = 50, T = 100 Gaussian draws from a known Sigma, the analytical nonlinear
    shrinkage has a mean squared Frobenius loss ||S_hat - Sigma||_F^2 to the truth (the paper's loss) at least 5%
    below linear LW (2004)'s over 20 seeded replications, and it wins in at least 18 of the 20 (a margin, not a
    coin flip; at these seeds it is 14% lower and wins all 20)."""
    n, t = 50, 100
    sigma = _spread_truth(n, 7)
    chol = np.linalg.cholesky(sigma)
    rng = np.random.default_rng(2020)
    nl_loss, lw_loss = [], []
    for _ in range(20):
        x = rng.standard_normal((t, n)) @ chol.T
        nl = cv.nonlinear_shrinkage(pd.DataFrame(x)).to_numpy() / cv.TRADING_DAYS   # annualised -> per period
        lw = LedoitWolf().fit(x).covariance_
        nl_loss.append(np.linalg.norm(nl - sigma) ** 2)
        lw_loss.append(np.linalg.norm(lw - sigma) ** 2)
    nl_loss, lw_loss = np.array(nl_loss), np.array(lw_loss)
    assert nl_loss.mean() < 0.95 * lw_loss.mean()
    assert (nl_loss < lw_loss).sum() >= 18


def test_nonlinear_shrinkage_keeps_the_eigenvectors_and_the_labels_and_is_positive_definite(rets):
    r = rets.tail(252)
    nl = cv.nonlinear_shrinkage(r)
    assert list(nl.index) == list(r.columns) == list(nl.columns)
    np.testing.assert_allclose(nl.to_numpy(), nl.to_numpy().T)
    assert np.linalg.eigvalsh(nl.to_numpy()).min() > 0
    # same eigenvectors as the (T-1)-normalised sample covariance: S and Sigma_NL commute
    s = np.cov(r.to_numpy(), rowvar=False) * cv.TRADING_DAYS
    m = nl.to_numpy()
    assert np.linalg.norm(s @ m - m @ s) < 1e-8 * np.linalg.norm(s) * np.linalg.norm(m)


def test_nonlinear_shrinkage_handles_more_assets_than_observations():
    """N > T: the sample covariance has N - T + 1 zero eigenvalues (after demeaning); the paper's (C.4)-(C.8) give
    them one common positive shrunk value, so the estimate is still positive definite."""
    rng = np.random.default_rng(11)
    x = rng.standard_normal((40, 60)) * 0.01
    nl = cv.nonlinear_shrinkage(pd.DataFrame(x)).to_numpy()
    assert np.isfinite(nl).all() and np.linalg.eigvalsh(nl).min() > 0
    # the sample covariance's 21-dimensional null space (60 - 39) is an eigenspace of the estimate: one shared value
    xc = x - x.mean(axis=0)
    w, u = np.linalg.eigh(xc.T @ xc)
    u0 = u[:, :21]
    assert w[20] < 1e-12 * w[-1] < w[21]
    block = u0.T @ nl @ u0
    np.testing.assert_allclose(block, block[0, 0] * np.eye(21), atol=1e-10 * abs(block[0, 0]))


def test_nonlinear_shrinkage_is_offered_by_the_dispatcher(rets):
    est = cv.estimate_covariance(rets.tail(252), "nonlinear_shrinkage")
    assert est.method == "nonlinear_shrinkage" and "Ledoit & Wolf (2020)" in est.reference
    pd.testing.assert_frame_equal(est.cov, cv.nonlinear_shrinkage(rets.tail(252)))
