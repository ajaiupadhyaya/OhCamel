"""VaR/ES models: closed forms, textbook identities, and real-data fits."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import integrate, stats

from ohcamel_quant.risk import garch as gm
from ohcamel_quant.risk import var as vm
from ohcamel_quant.risk.core import (
    covariance,
    empirical_var_es,
    ewma_variance_path,
    fit_t_dof,
    portfolio_returns,
    std_t_es,
    std_t_quantile,
    tail_count,
)


@pytest.fixture(scope="module")
def spy(etf_returns):
    return etf_returns["SPY"]


@pytest.fixture(scope="module")
def port(etf_returns):
    return portfolio_returns(etf_returns, {"SPY": 0.5, "TLT": 0.3, "GLD": 0.2})


# ------------------------------------------------------------------ core
def test_portfolio_returns_is_weighted_sum(etf_returns):
    rp = portfolio_returns(etf_returns, {"SPY": 0.6, "TLT": -0.2})
    np.testing.assert_allclose(rp, 0.6 * etf_returns["SPY"] - 0.2 * etf_returns["TLT"])
    with pytest.raises(ValueError):
        portfolio_returns(etf_returns, {"NOPE": 1.0})


def test_historical_convention_on_known_sample():
    losses = np.arange(1, 101, dtype=float)  # 1..100
    # k = ceil(100 * 0.05) = 5 -> VaR = 5th largest = 96, ES = mean(96..100) = 98
    assert tail_count(100, 0.95) == 5
    assert empirical_var_es(losses, 0.95) == (96.0, 98.0)
    r = -losses / 1000
    est = vm.historical(r, 0.95)
    assert est.var == pytest.approx(0.096) and est.es == pytest.approx(0.098)


def test_overlapping_returns_compound():
    r = np.array([0.01, -0.02, 0.03, 0.0])
    out = vm.overlapping_returns(r, 2)
    np.testing.assert_allclose(out, [(1.01 * 0.98) - 1, (0.98 * 1.03) - 1, 1.03 - 1])


# ------------------------------------------------------------------ Gaussian
def test_gaussian_var_is_z_sigma(spy):
    x = spy.to_numpy()
    mu, s = x.mean(), x.std(ddof=1)
    for a in (0.95, 0.99, 0.975):
        est = vm.gaussian(x, a)
        z = stats.norm.ppf(a)
        assert est.var == pytest.approx(-mu + z * s, rel=1e-12)
        assert est.es == pytest.approx(-mu + s * stats.norm.pdf(z) / (1 - a), rel=1e-12)
        assert est.es > est.var
    # 99% z is the textbook 2.3263
    assert stats.norm.ppf(0.99) == pytest.approx(2.326348, abs=1e-6)


def test_gaussian_sqrt_time(spy):
    x = spy.to_numpy()
    e1, e10 = vm.gaussian(x, 0.99, 1), vm.gaussian(x, 0.99, 10)
    mu = x.mean()
    assert e10.var + 10 * mu == pytest.approx(math.sqrt(10) * (e1.var + mu), rel=1e-12)


# ------------------------------------------------------------------ Student-t
@pytest.mark.parametrize("nu", [3.0, 4.5, 8.0, 30.0])
@pytest.mark.parametrize("alpha", [0.95, 0.99])
def test_t_es_closed_form_vs_numerical_integration(nu, alpha):
    c = math.sqrt((nu - 2) / nu)             # unit-variance scale
    q = std_t_quantile(alpha, nu)
    assert stats.t.cdf(q / c, nu) == pytest.approx(alpha, abs=1e-12)
    num, _ = integrate.quad(lambda x: x * stats.t.pdf(x / c, nu) / c, q, np.inf)
    assert std_t_es(alpha, nu) == pytest.approx(num / (1 - alpha), rel=1e-7)
    # unit variance
    var, _ = integrate.quad(lambda x: x * x * stats.t.pdf(x / c, nu) / c, -np.inf, np.inf)
    assert var == pytest.approx(1.0, rel=1e-6)


def test_t_dof_mle_recovers_heavy_tails_and_matches_variance(spy):
    x = spy.to_numpy()
    nu = fit_t_dof(x)
    assert 2.05 < nu < 10  # daily equity returns are heavy-tailed
    est = vm.student_t(x, 0.99)
    assert est.params["nu"] == pytest.approx(nu)
    # variance of the fitted law equals the sample variance
    sc = est.params["scale"]
    assert sc * sc * nu / (nu - 2) == pytest.approx(x.var(ddof=1), rel=1e-12)
    # MLE: the profile likelihood is lower at nearby dof
    m, s = x.mean(), x.std(ddof=1)

    def ll(n):
        c = s * math.sqrt((n - 2) / n)
        return np.sum(stats.t.logpdf((x - m) / c, n) - math.log(c))

    assert ll(nu) >= ll(nu * 1.1) and ll(nu) >= ll(nu * 0.9)


def test_t_es_exceeds_gaussian_es_at_99(spy):
    x = spy.to_numpy()
    assert vm.student_t(x, 0.99).es > vm.gaussian(x, 0.99).es


# ------------------------------------------------------------------ Cornish-Fisher
def test_cornish_fisher_reduces_to_gaussian_when_moments_are_normal():
    for p in (0.05, 0.01, 0.025):
        z = stats.norm.ppf(p)
        assert vm.cornish_fisher_z(z, 0.0, 0.0) == pytest.approx(z)
        M, g = vm.cornish_fisher_tail_mean(p, 0.0, 0.0)
        assert g == pytest.approx(z)
        assert M == pytest.approx(-stats.norm.pdf(z) / p, rel=1e-12)
    assert vm.cornish_fisher_valid(0.0, 0.0)


@pytest.mark.parametrize("S,K", [(-0.5, 2.0), (0.3, 1.0), (-1.0, 4.0)])
def test_modified_es_matches_edgeworth_integral(S, K):
    """Boudt-Peterson-Croux tail mean = (1/p) int_{-inf}^{g} x e(x) dx with the Edgeworth density."""
    p = 0.025
    M, g = vm.cornish_fisher_tail_mean(p, S, K)

    def he(x):
        h3 = x ** 3 - 3 * x
        h4 = x ** 4 - 6 * x ** 2 + 3
        h6 = x ** 6 - 15 * x ** 4 + 45 * x ** 2 - 15
        return stats.norm.pdf(x) * (1 + S / 6 * h3 + K / 24 * h4 + S * S / 72 * h6)

    num, _ = integrate.quad(lambda x: x * he(x), -np.inf, g)
    assert M == pytest.approx(num / p, rel=1e-8)


def test_cornish_fisher_validity_matches_monotonicity():
    z = np.linspace(-8, 8, 4001)
    for S in np.linspace(-1.2, 1.2, 9):
        for K in np.linspace(-1, 12, 14):
            mono = bool(np.all(np.diff(vm.cornish_fisher_z(z, S, K)) > -1e-12))
            if vm.cornish_fisher_valid(S, K):
                assert mono, (S, K)
    # classic example: excess kurtosis 10 is outside the domain even with zero skew
    assert not vm.cornish_fisher_valid(0.0, 10.0)
    assert vm.cornish_fisher_valid(0.0, 4.0)


def test_cornish_fisher_on_real_data_flags_domain(spy):
    est = vm.cornish_fisher(spy.to_numpy(), 0.99)
    assert est.params["excess_kurtosis"] > 5
    assert est.valid == est.params["valid_domain"]
    if est.valid:
        assert est.es >= est.var
    else:
        assert any("domain of validity" in n for n in est.notes)
        assert math.isnan(est.es) and math.isfinite(est.var)


def test_cornish_fisher_multi_day_moment_aggregation(spy):
    x = spy.to_numpy()
    e = vm.cornish_fisher(x, 0.99, 10)
    S, K = e.params["skew"], e.params["excess_kurtosis"]
    g = vm.cornish_fisher_z(stats.norm.ppf(0.01), S / math.sqrt(10), K / 10)
    assert e.var == pytest.approx(-(10 * x.mean() + math.sqrt(10) * x.std(ddof=1) * g))


# ------------------------------------------------------------------ EWMA
def test_ewma_recursion_matches_loop(spy):
    x = spy.to_numpy()[:300]
    lam = 0.94
    path = ewma_variance_path(x, lam)
    s2 = np.mean(x[:30] ** 2)
    ref = [s2]
    for r in x:
        s2 = lam * s2 + (1 - lam) * r * r
        ref.append(s2)
    np.testing.assert_allclose(path, ref, rtol=1e-12)
    est = vm.ewma(x, 0.99, 1, lam)
    assert est.var == pytest.approx(math.sqrt(path[-1]) * stats.norm.ppf(0.99))


def test_ewma_covariance_weights(etf_returns):
    sub = etf_returns[["SPY", "TLT"]].iloc[-400:]
    mu, cov = covariance(sub, "ewma", 0.94)
    assert np.all(mu == 0)
    x = sub.to_numpy()
    w = 0.06 * 0.94 ** np.arange(len(x))[::-1]
    w /= w.sum()
    assert cov[0, 1] == pytest.approx(np.sum(w * x[:, 0] * x[:, 1]))
    assert np.all(np.linalg.eigvalsh(cov) > 0)


# ------------------------------------------------------------------ EVT
def test_gpd_mle_matches_scipy_likelihood(etf_returns):
    for c in ("SPY", "TLT", "XLE"):
        L = -etf_returns[c].to_numpy()
        u = np.quantile(L, 0.9)
        y = L[L > u] - u
        xi, beta = vm.fit_gpd(y)
        sx, _, sb = stats.genpareto.fit(y, floc=0)

        def ll(a, b, y=y):
            return stats.genpareto.logpdf(y, a, 0, b).sum()

        assert ll(xi, beta) >= ll(sx, sb) - 1e-6


def test_gpd_tail_formulas_are_consistent():
    # If the tail is exactly GPD above u, VaR at the threshold's own tail prob equals u.
    u, xi, beta, n, nu = 0.02, 0.2, 0.01, 1000, 100
    var, es = vm.gpd_var_es(u, xi, beta, n, nu, 1 - nu / n)
    assert var == pytest.approx(u)
    assert es == pytest.approx(u + beta / (1 - xi))  # mean excess of a GPD = beta/(1-xi)
    # xi -> 0 limit is continuous
    a = vm.gpd_var_es(u, 1e-10, beta, n, nu, 0.999)[0]
    b = vm.gpd_var_es(u, 1e-5, beta, n, nu, 0.999)[0]
    assert a == pytest.approx(b, rel=1e-3)


def test_evt_on_spy_heavy_tail(spy):
    est = vm.evt_pot(spy.to_numpy(), 0.99)
    assert est.params["xi"] > 0          # equity loss tails are Frechet-type
    assert est.params["exceedances"] == pytest.approx(0.1 * len(spy), abs=2)
    assert est.es > est.var > 0
    small = vm.evt_pot(spy.to_numpy()[:300], 0.99)
    assert any("exceedances" in n for n in small.notes)


# ------------------------------------------------------------------ GARCH
@pytest.fixture(scope="module")
def spy_fits(spy):
    return {k: gm.fit_garch(spy, k) for k in ("garch", "gjr")}


def test_garch_on_real_spy_is_stationary(spy_fits):
    for fit in spy_fits.values():
        assert fit.converged
        assert fit.alpha + fit.beta < 1
        assert fit.persistence < 1
        assert fit.nu > 2
        assert fit.persistence > 0.9   # volatility clustering in real equity returns
    # leverage effect in equities: gamma > 0 and GJR fits better
    assert spy_fits["gjr"].gamma > 0
    assert spy_fits["gjr"].loglik > spy_fits["garch"].loglik


def test_garch_var_es_and_term_structure(spy_fits):
    fit = spy_fits["gjr"]
    est = gm.garch_var_es(fit, 0.99, 1)
    s = math.sqrt(fit.next_variance_pct) / 100
    assert est.var == pytest.approx(-fit.mu / 100 + s * std_t_quantile(0.99, fit.nu))
    assert est.es > est.var > 0
    ts = gm.term_structure(fit, 2000)
    ub = math.sqrt(fit.unconditional_variance_pct) / 100
    assert ts["daily_vol"].iloc[-1] == pytest.approx(ub, rel=1e-3)
    assert ts["daily_vol"].iloc[0] == pytest.approx(s)
    # arch's own analytic forecast agrees with the closed-form term structure
    f = fit.result.forecast(horizon=10, reindex=False).variance.to_numpy()[-1]
    np.testing.assert_allclose(gm.variance_forecast(fit, 10), f, rtol=1e-6)
    e10 = gm.garch_var_es(fit, 0.99, 10)
    assert e10.var > est.var


def test_fast_garch_matches_arch(spy):
    x = spy.to_numpy()
    for kind in ("garch", "gjr"):
        for sl in (slice(0, 500), slice(1500, 2000)):
            a = gm.fit_garch(x[sl], kind)
            b = gm.fit_garch_fast(x[sl], kind)
            # the analytic-gradient fit is never worse than arch's optimum
            assert b.loglik >= a.loglik - 1e-3
            if abs(b.loglik - a.loglik) < 1e-2:   # same optimum -> same forecast
                assert b.next_variance_pct == pytest.approx(a.next_variance_pct, rel=2e-2)
                assert b.beta == pytest.approx(a.beta, abs=5e-3)


def test_filter_variance_reproduces_in_sample_path(spy_fits, spy):
    fit = spy_fits["gjr"]
    eps = 100 * spy.to_numpy() - fit.mu
    s2 = (fit.cond_vol * 100) ** 2
    pars = {"omega": fit.omega, "alpha": fit.alpha, "gamma": fit.gamma, "beta": fit.beta}
    out = gm.filter_variance(pars, eps[100:200], s2[100])
    np.testing.assert_allclose(out, s2[100:200], rtol=1e-8)


def test_fhs_one_day_is_exact_rescaling(spy_fits):
    fit = spy_fits["gjr"]
    est = gm.fhs_var_es(fit, 0.99, 1)
    losses = -(fit.mu + math.sqrt(fit.next_variance_pct) * fit.std_resid) / 100
    assert (est.var, est.es) == empirical_var_es(losses, 0.99)
    e5 = gm.fhs_var_es(fit, 0.99, 5, simulations=4000)
    assert e5.var > est.var and e5.es > e5.var
    assert gm.fhs_var_es(fit, 0.99, 5, simulations=4000).var == e5.var  # seeded, reproducible


def test_news_impact_curve_is_asymmetric(spy_fits):
    nic = gm.news_impact_curve(spy_fits["gjr"])
    neg = nic[nic.shock < 0].next_vol.iloc[0]
    pos = nic[nic.shock > 0].next_vol.iloc[-1]
    assert neg > pos
