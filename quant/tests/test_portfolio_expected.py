"""Expected-return models: historical, Bayes-Stein, CAPM and Black-Litterman identities."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.portfolio import covariance as cv
from ohcamel_quant.portfolio import expected as er

RF_DAILY = 0.0001  # test input standing in for the caller-supplied rf series


@pytest.fixture(scope="module")
def rets(etf_returns) -> pd.DataFrame:
    return etf_returns.dropna()


@pytest.fixture(scope="module")
def sigma(rets) -> pd.DataFrame:
    return cv.sample_cov(rets).cov


def test_historical_mean(rets):
    h = er.historical_mean(rets)
    assert np.allclose(h.mu.to_numpy(), rets.mean().to_numpy() * 252)
    geo = h.extra["geometric"]
    assert np.all(geo < h.mu + 1e-12)  # AM-GM: compound growth below arithmetic mean
    approx = h.mu - rets.var(ddof=1) * 252 / 2
    # second-order approximation; higher moments (fat tails) account for the gap
    assert np.allclose(geo, approx, atol=0.03)


def test_james_stein_formula(rets):
    js = er.james_stein(rets)
    x = rets.to_numpy()
    t, n = x.shape
    m = x.mean(0)
    s = np.cov(x, rowvar=False) * (t - 1) / (t - n - 2)
    si = np.linalg.inv(s)
    one = np.ones(n)
    mu0 = one @ si @ m / (one @ si @ one)
    w = (n + 2) / ((n + 2) + t * (m - mu0) @ si @ (m - mu0))
    assert js.params["shrinkage"] == pytest.approx(w)
    assert 0 < w < 1
    assert np.allclose(js.mu.to_numpy(), ((1 - w) * m + w * mu0) * 252)
    # shrinkage reduces cross-sectional dispersion
    assert js.mu.std() < er.historical_mean(rets).mu.std()
    avg = er.james_stein(rets, target="average")
    assert avg.params["grand_mean"] == pytest.approx(m.mean() * 252)


def test_capm_equilibrium_benchmark_has_beta_one(rets):
    capm = er.capm_equilibrium(rets, rets["SPY"], RF_DAILY)
    assert capm.extra["beta"]["SPY"] == pytest.approx(1.0)
    prem = (rets["SPY"] - RF_DAILY).mean() * 252
    assert capm.mu["SPY"] == pytest.approx(RF_DAILY * 252 + prem)
    assert capm.extra["beta"]["TLT"] < 0.5 < capm.extra["beta"]["QQQ"]


def test_implied_risk_aversion(rets):
    d = er.implied_risk_aversion(rets["SPY"], RF_DAILY)
    b = rets["SPY"].to_numpy()
    assert d == pytest.approx((b - RF_DAILY).mean() * 252 / (b.var(ddof=1) * 252))
    rf = pd.Series(RF_DAILY, index=rets.index)
    assert er.implied_risk_aversion(rets["SPY"], rf) == pytest.approx(d)


def test_black_litterman_no_views_returns_prior(sigma):
    w = pd.Series(np.linspace(1, 2, 9), index=sigma.columns)
    w = w / w.sum()
    delta, rf = 2.5, 0.03
    bl = er.black_litterman(sigma, delta, w, [], tau=0.05, rf=rf)
    pi = delta * sigma.to_numpy() @ w.to_numpy()
    assert np.allclose(bl.mu.to_numpy(), pi + rf)
    assert np.allclose(bl.extra["prior_excess"].to_numpy(), pi)
    # reverse optimisation: (delta Sigma)^{-1} pi = w_mkt
    assert np.allclose(np.linalg.solve(delta * sigma.to_numpy(), pi), w.to_numpy())
    # zero-confidence views are ignored
    bl0 = er.black_litterman(sigma, delta, w, [er.View("XLE", 0.5, 0.0)], tau=0.05, rf=rf)
    assert np.allclose(bl0.mu.to_numpy(), pi + rf)
    # He-Litterman: no views -> posterior covariance (1 + tau) Sigma
    assert np.allclose(bl.cov.to_numpy(), 1.05 * sigma.to_numpy())


def test_black_litterman_full_confidence_view_holds_exactly(sigma):
    w = pd.Series(1 / 9, index=sigma.columns)
    v = [er.View("XLK", 0.04, 1.0, short="XLE"), er.View("GLD", 0.05, 1.0)]
    bl = er.black_litterman(sigma, 3.0, w, v, tau=0.05)
    post = bl.extra["posterior_excess"]
    assert post["XLK"] - post["XLE"] == pytest.approx(0.04, abs=1e-10)
    assert post["GLD"] == pytest.approx(0.05, abs=1e-10)
    assert all(x["omega"] == 0 for x in bl.extra["views"])


def test_idzorek_confidence_sets_the_tilt(sigma):
    w = pd.Series(1 / 9, index=sigma.columns).to_numpy()
    s = sigma.to_numpy()
    delta, tau = 3.0, 0.05
    pi = delta * s @ w
    p = np.zeros((1, 9))
    p[0, list(sigma.columns).index("XLF")] = 1.0
    q = np.array([0.15])
    w100 = np.linalg.solve(delta * s, er.bl_posterior(pi, s, p, q, np.zeros((1, 1)), tau)[0])
    for c in (0.25, 0.5, 0.9):
        om = er.idzorek_omega(pi, s, p, q, np.array([c]), tau, delta, w)
        mu, _ = er.bl_posterior(pi, s, p, q, np.diag(om), tau)
        wk = np.linalg.solve(delta * s, mu)
        tilt = (wk - w) @ (w100 - w) / ((w100 - w) @ (w100 - w))
        assert tilt == pytest.approx(c, abs=1e-4)
    # more confidence -> smaller omega
    o1 = er.idzorek_omega(pi, s, p, q, np.array([0.2]), tau, delta, w)[0]
    o2 = er.idzorek_omega(pi, s, p, q, np.array([0.8]), tau, delta, w)[0]
    assert o1 > o2 > 0


def test_view_validation(sigma):
    w = pd.Series(1 / 9, index=sigma.columns)
    with pytest.raises(ValueError):
        er.black_litterman(sigma, 3.0, w, [er.View("NOPE", 0.1, 0.5)])
    with pytest.raises(ValueError):
        er.black_litterman(sigma, 3.0, w, [er.View("SPY", 0.1, 0.5, short="SPY")])


def test_black_litterman_absolute_view_is_total_return(sigma):
    """An absolute view 'GLD returns 5%' held with 100% confidence must give GLD a
    TOTAL expected return of 5% (not 5% + rf): the view enters Q as 5% - rf."""
    w = pd.Series(1 / 9, index=sigma.columns)
    rf = 0.04
    v = [er.View("GLD", 0.05, 1.0), er.View("XLK", 0.03, 1.0, short="XLE")]
    bl = er.black_litterman(sigma, 3.0, w, v, tau=0.05, rf=rf)
    assert bl.mu["GLD"] == pytest.approx(0.05, abs=1e-10)
    assert bl.extra["posterior_excess"]["GLD"] == pytest.approx(0.05 - rf, abs=1e-10)
    # relative views are spreads: rf cancels
    assert bl.mu["XLK"] - bl.mu["XLE"] == pytest.approx(0.03, abs=1e-10)
    views = bl.extra["views"]
    assert views[0]["value_excess"] == pytest.approx(0.05 - rf)
    assert views[1]["value_excess"] == pytest.approx(0.03)
    # prior_implied is reported in the same units as the view value
    pi = bl.extra["prior_excess"]
    assert views[0]["prior_implied"] == pytest.approx(pi["GLD"] + rf)
    assert views[1]["prior_implied"] == pytest.approx(pi["XLK"] - pi["XLE"])
