"""Optimizers on real ETF returns, checked against closed forms, KKT conditions and brute force."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.portfolio import covariance as cv
from ohcamel_quant.portfolio import expected as er
from ohcamel_quant.portfolio import optimize as opt
from ohcamel_quant.portfolio.methods import METHODS, run_method

RF = 0.02  # an arbitrary test input for the rf argument (the API takes rf from FRED)
WIDE = opt.Constraints(-10.0, 10.0)


@pytest.fixture(scope="module")
def rets(etf_returns) -> pd.DataFrame:
    return etf_returns.dropna()


@pytest.fixture(scope="module")
def sigma(rets) -> pd.DataFrame:
    return cv.lw_constant_correlation(rets).cov


@pytest.fixture(scope="module")
def mu(rets) -> pd.Series:
    return er.historical_mean(rets).mu


@pytest.fixture(scope="module")
def random_feasible() -> np.ndarray:
    return np.random.default_rng(7).dirichlet(np.ones(9) * 0.5, size=4000)


def _vol(w, s):
    return float(np.sqrt(w @ s.to_numpy() @ w))


def test_min_variance_kkt_and_beats_random(sigma, random_feasible):
    w = opt.min_variance(sigma)
    s = sigma.to_numpy()
    assert w.sum() == pytest.approx(1.0, abs=1e-8)
    assert np.all(w >= -1e-10)
    g = s @ w  # KKT: (Sigma w)_i = lambda on the support, >= lambda off it
    active = w > 1e-6
    lam = g[active].mean()
    assert np.allclose(g[active], lam, rtol=1e-4, atol=1e-8)
    assert np.all(g[~active] >= lam - 1e-8)
    v = w @ s @ w
    vr = np.einsum("ij,jk,ik->i", random_feasible, s, random_feasible)
    assert v <= vr.min() + 1e-12


def test_min_variance_unconstrained_closed_form(sigma):
    s = sigma.to_numpy()
    inv1 = np.linalg.solve(s, np.ones(9))
    closed = inv1 / inv1.sum()
    w = opt.min_variance(sigma, WIDE)
    assert np.allclose(w, closed, atol=1e-5)


def test_tangency_matches_closed_form_when_unconstrained(sigma, mu):
    s = sigma.to_numpy()
    # the closed form is the tangency (not the minimum-Sharpe point) only when rf < mu_GMV
    b = np.linalg.solve(s, np.ones(9))
    rf = float(mu.to_numpy() @ b / b.sum()) - 0.01
    z = np.linalg.solve(s, mu.to_numpy() - rf)
    assert z.sum() > 0
    closed = z / z.sum()
    assert np.all(np.abs(closed) < 10)
    w = opt.max_sharpe(mu, sigma, rf, WIDE)
    assert np.allclose(w, closed, atol=1e-4)


def test_tangency_long_only_dominates(sigma, mu, random_feasible):
    w = opt.max_sharpe(mu, sigma, RF)
    s, m = sigma.to_numpy(), mu.to_numpy()
    sr = (m @ w - RF) / np.sqrt(w @ s @ w)
    srs = (random_feasible @ m - RF) / np.sqrt(np.einsum("ij,jk,ik->i", random_feasible, s, random_feasible))
    assert sr >= srs.max() - 1e-9
    fr = opt.efficient_frontier(mu, sigma, points=25, rf=RF)
    assert sr >= max(p["sharpe"] for p in fr["points"]) - 1e-7
    assert fr["tangency"]["sharpe"] == pytest.approx(sr, rel=1e-6)


def test_tangency_requires_excess_return(sigma, mu):
    with pytest.raises(ValueError, match="risk-free"):
        opt.max_sharpe(mu, sigma, float(mu.max()) + 0.01)


def test_mean_variance_modes(sigma, mu):
    m, s = mu.to_numpy(), sigma.to_numpy()
    target = 0.08
    w = opt.mean_variance(mu, sigma, target_return=target)
    assert m @ w == pytest.approx(target, abs=1e-6)
    w2 = opt.mean_variance(mu, sigma, target_vol=0.10)
    assert _vol(w2, sigma) == pytest.approx(0.10, abs=1e-5)
    # risk aversion, unconstrained: w = Sigma^{-1}(mu - eta 1)/gamma with eta s.t. 1'w = 1
    g = 5.0
    a, b = np.linalg.solve(s, m), np.linalg.solve(s, np.ones(9))
    eta = (a.sum() - g) / b.sum()
    closed = (a - eta * b) / g
    w3 = opt.mean_variance(mu, sigma, WIDE, risk_aversion=g)
    assert np.allclose(w3, closed, atol=1e-5)
    with pytest.raises(ValueError):
        opt.mean_variance(mu, sigma, target_return=5.0)
    with pytest.raises(ValueError):
        opt.mean_variance(mu, sigma, target_vol=0.001)
    with pytest.raises(ValueError):
        opt.mean_variance(mu, sigma)


def test_constraints_bounds_turnover_leverage(sigma, mu):
    cur = np.full(9, 1 / 9)
    c = opt.Constraints(0.0, 0.3, max_turnover=0.2, current=cur)
    w = opt.max_sharpe(mu, sigma, RF, c)
    assert c.satisfied(w)
    assert np.abs(w - cur).sum() <= 0.2 + 1e-6
    unconstrained = opt.max_sharpe(mu, sigma, RF, opt.Constraints(0.0, 0.3))
    assert np.abs(unconstrained - cur).sum() > 0.2  # the limit binds
    c2 = opt.Constraints(-0.5, 1.0, max_gross=1.5)
    w2 = opt.min_variance(sigma, c2)
    assert np.abs(w2).sum() <= 1.5 + 1e-6 and w2.sum() == pytest.approx(1.0)
    w3 = opt.max_sharpe(mu, sigma, RF, c2)
    assert np.abs(w3).sum() <= 1.5 + 1e-5
    with pytest.raises(ValueError, match="infeasible"):
        opt.min_variance(sigma, opt.Constraints(0.0, 0.1))
    with pytest.raises(ValueError):
        opt.min_variance(sigma, opt.Constraints(0.0, 1.0, max_turnover=0.1))


def test_risk_parity_equal_and_budgeted_contributions(sigma):
    w, it = opt.risk_parity(sigma)
    s = sigma.to_numpy()
    rc = opt.risk_contributions(w, s)
    assert rc.sum() == pytest.approx(np.sqrt(w @ s @ w))
    assert np.allclose(rc / rc.sum(), 1 / 9, atol=1e-8)
    assert it < 50 and np.all(w > 0)
    b = np.arange(1, 10, dtype=float)
    b /= b.sum()
    wb, _ = opt.risk_parity(sigma, b)
    rcb = opt.risk_contributions(wb, s)
    assert np.allclose(rcb / rcb.sum(), b, atol=1e-8)


def test_max_diversification(sigma, random_feasible):
    w = opt.max_diversification(sigma)
    s = sigma.to_numpy()
    sd = np.sqrt(np.diag(s))
    dr = (w @ sd) / np.sqrt(w @ s @ w)
    drs = (random_feasible @ sd) / np.sqrt(np.einsum("ij,jk,ik->i", random_feasible, s, random_feasible))
    assert dr >= drs.max() - 1e-9
    # Choueifaty-Coignard: MDP = min-variance on the correlation matrix, rescaled by 1/sigma
    corr = pd.DataFrame(cv.cov_to_corr(s), index=sigma.index, columns=sigma.columns)
    y = opt.min_variance(corr)
    alt = (y / sd) / (y / sd).sum()
    assert np.allclose(w, alt, atol=1e-4)


def test_min_cvar_lp_equals_empirical_cvar(rets):
    alpha = 0.95
    scen = rets.iloc[-750:]
    w, info = opt.min_cvar(scen, alpha)
    losses = -scen.to_numpy() @ w
    assert info["cvar_daily"] == pytest.approx(opt.empirical_cvar(losses, alpha), rel=1e-7)
    # (1-alpha) T is not an integer here -> RU CVaR is the mix; check against the tail mean bound
    k = int(np.ceil((1 - alpha) * len(losses)))
    assert np.sort(losses)[-k:].mean() <= info["cvar_daily"] + 1e-12
    # it is the minimum: beats 1/N, inverse vol and GMV on the same scenarios
    s = cv.sample_cov(scen).cov
    for other in (opt.equal_weight(9), opt.inverse_volatility(s), opt.min_variance(s)):
        assert info["cvar_daily"] <= opt.empirical_cvar(-scen.to_numpy() @ other, alpha) + 1e-10
    assert w.sum() == pytest.approx(1.0) and np.all(w >= -1e-9)


def test_frontier_monotone_and_cml(sigma, mu):
    fr = opt.efficient_frontier(mu, sigma, points=30, rf=RF)
    r = np.array([p["ret"] for p in fr["points"]])
    v = np.array([p["vol"] for p in fr["points"]])
    assert np.all(np.diff(r) > 0)
    assert np.all(np.diff(v) >= -1e-7)
    assert v[0] == pytest.approx(fr["gmv"]["vol"])
    assert r[-1] == pytest.approx(mu.max(), abs=1e-6)  # long-only max return = best asset
    t = fr["tangency"]
    cml = fr["cml"]
    slope = (cml[-1]["ret"] - cml[0]["ret"]) / (cml[-1]["vol"] - cml[0]["vol"])
    assert slope == pytest.approx(t["sharpe"])
    assert cml[0]["ret"] == pytest.approx(RF)
    # the CML lies weakly above the frontier
    assert np.all(RF + t["sharpe"] * v >= r - 1e-7)


def test_diagnostics_and_every_method(rets, sigma, mu):
    for name in METHODS:
        kw = {"target_return": 0.08} if name == "mean_variance" else {}
        res = run_method(name, rets, sigma, mu, RF, **kw)
        w = res.weights.to_numpy()
        assert w.sum() == pytest.approx(1.0, abs=1e-6), name
        assert res.risk_contributions.sum() == pytest.approx(res.volatility, rel=1e-9), name
        assert 1.0 - 1e-9 <= res.effective_bets <= 9 + 1e-9
        assert 1.0 - 1e-9 <= res.effective_n <= 9 + 1e-9
        assert res.diversification_ratio >= 1.0 - 1e-9
        assert res.reference
    ew = run_method("equal_weight", rets, sigma, mu, RF)
    assert ew.effective_n == pytest.approx(9.0)
    hrp = run_method("hrp", rets, sigma, mu, RF)
    assert "dendrogram" in hrp.extra and len(hrp.extra["dendrogram"]["order"]) == 9
    # heuristics flag, rather than silently break, constraints they cannot honour
    capped = run_method("equal_weight", rets, sigma, mu, RF, opt.Constraints(0.0, np.r_[0.05, np.ones(8)]))
    assert any("does not take constraints" in n for n in capped.notes)


def test_meucci_enb_closed_forms():
    s = np.diag([0.04, 0.09, 0.16])
    w = np.array([1 / 0.2, 1 / 0.3, 1 / 0.4])
    w /= w.sum()  # inverse vol on uncorrelated assets = equal risk -> ENB = N
    assert opt.effective_bets(w, s) == pytest.approx(3.0)
    assert opt.effective_bets(np.array([1.0, 0, 0]), s) == pytest.approx(1.0)


def test_active_set_qp_matches_slsqp(rets):
    """The active-set QP (used for box constraints) and SLSQP agree on real sub-universes."""
    rng = np.random.default_rng(3)
    for _ in range(15):
        cols = list(rng.choice(rets.columns, size=int(rng.integers(3, 9)), replace=False))
        start = int(rng.integers(0, len(rets) - 300))
        s = cv.sample_cov(rets.iloc[start:start + 300][cols]).cov.to_numpy()
        s = s / np.mean(np.diag(s))
        n = len(cols)
        c = opt.Constraints(float(rng.choice([0.0, -0.3])), max(1 / n + 0.05, float(rng.choice([1.0, 0.4]))))
        lo, hi = c.bounds(n)
        w1 = opt.active_set_qp(2 * s, np.zeros(n), np.ones(n), 1.0, lo, hi)
        w2 = opt._solve(n, lambda w, s=s: float(w @ s @ w), lambda w, s=s: 2 * s @ w, c)
        assert c.satisfied(w1)
        assert w1 @ s @ w1 <= w2 @ s @ w2 * (1 + 1e-9)
