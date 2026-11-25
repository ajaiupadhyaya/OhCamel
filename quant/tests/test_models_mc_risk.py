"""Lane M, M2: Monte Carlo risk on real fixture returns (compute plan M2; contract II.4)."""

from __future__ import annotations

import numpy as np
import pytest

from ohcamel_quant.models.mc_risk import (
    NU_BOUNDS,
    book_risk,
    copula_nu,
    euler_es,
    fhs_1d,
    fit_gjr,
    residual_matrix,
)
from ohcamel_quant.risk.garch import fhs_var_es


@pytest.fixture(scope="module")
def rets(etf_returns):
    return etf_returns[["SPY", "TLT", "GLD"]].dropna().tail(1000)


def test_single_asset_fhs_1d_equals_the_existing_estimator(rets):
    """Reference implementation: risk.garch.fhs_var_es on the same fit (1-day is exact, no simulation)."""
    fit = fit_gjr(rets["SPY"].to_numpy())
    for a in (0.95, 0.99):
        var, es, _ = fhs_1d([fit], np.array([1.0]), a)
        ref = fhs_var_es(fit, a, 1)
        assert var == pytest.approx(ref.var, rel=1e-12) and es == pytest.approx(ref.es, rel=1e-12)


def test_euler_contributions_sum_to_es(rets):
    """Tasche (2000): with the tail rows fixed, sum_i E[L_i | tail] = E[L | tail] = ES, exactly."""
    x = rets.to_numpy()
    fits = [fit_gjr(x[:, j]) for j in range(3)]
    w = np.array([0.6, 0.3, 0.1])
    var, es, al = fhs_1d(fits, w, 0.99)
    c = euler_es(al, 0.99)
    assert c.sum() == pytest.approx(es, rel=1e-12)
    assert residual_matrix(fits).shape[1] == 3


def test_copula_nu_is_clipped_to_its_bounds(rets):
    nu = copula_nu(rets.to_numpy())
    assert NU_BOUNDS[0] <= nu <= NU_BOUNDS[1]
    rng = np.random.default_rng(1)  # a Gaussian copula: the likelihood is flat toward the ceiling, so nu lands high
    assert copula_nu(rng.standard_normal((2000, 2))) >= 20.0


def test_book_risk_is_deterministic_and_complete(rets):
    w = np.array([0.6, 0.3, 0.1])
    a = book_risk(rets, w, n_paths=20_000, seed=11, threads=1)
    b = book_risk(rets, w, n_paths=20_000, seed=11, threads=1)
    assert a.rows == b.rows  # same (seed, threads) -> bit-identical (II.4)
    keys = {(r["method"], r["horizon"], r["alpha"]) for r in a.rows}
    assert keys == {(m, h, al) for m in ("fhs", "copula_t") for h in (1, 10) for al in (0.95, 0.975, 0.99)}
    for r in a.rows:
        assert 0 < r["var"] <= r["es"]  # ES >= VaR by construction
    ten = {r["alpha"]: r["var"] for r in a.rows if r["method"] == "fhs" and r["horizon"] == 10}
    one = {r["alpha"]: r["var"] for r in a.rows if r["method"] == "fhs" and r["horizon"] == 1}
    assert all(ten[al] > one[al] for al in ten)  # ten days of risk exceed one


def test_fhs_only_skips_the_copula_and_matches_the_full_run(rets, monkeypatch):
    """No busy-work: methods=("fhs",) never calls the copula kernel; its FHS rows equal the full run's."""
    from ohcamel_quant import kernels

    w = np.array([0.6, 0.3, 0.1])
    full = book_risk(rets, w, n_paths=20_000, seed=11, threads=1)
    monkeypatch.setattr(kernels, "copula_t_paths", lambda *a, **k: 1 / 0)
    only = book_risk(rets, w, n_paths=20_000, seed=11, threads=1, methods=("fhs",))
    assert only.rows == [r for r in full.rows if r["method"] == "fhs"]
    assert {r["method"] for r in only.rows} == {"fhs"}
    with pytest.raises(ValueError):
        book_risk(rets, w, n_paths=1_000, seed=11, threads=1, methods=("bogus",))


# ---------------------------------------------------------------- Task 4.4: the copula's nu
def _t_copula_gaussian_marginals(n: int, nu: float, rho: float, seed: int) -> np.ndarray:
    """Simulated t-copula (known truth) with standard-normal marginals."""
    from scipy import stats

    rng = np.random.default_rng(seed)
    k = 3
    corr = np.full((k, k), rho) + (1 - rho) * np.eye(k)
    z = rng.standard_normal((n, k)) @ np.linalg.cholesky(corr).T
    w = rng.chisquare(nu, size=n) / nu
    u = stats.t.cdf(z / np.sqrt(w)[:, None], nu)
    return stats.norm.ppf(u)


def test_copula_nu_is_the_copula_likelihood_not_the_marginals():
    """Gaussian marginals make every per-asset t MLE run to its ceiling (the old median rule returned the clip,
    30); the profile likelihood of the COPULA recovers the simulated nu = 5."""
    x = _t_copula_gaussian_marginals(3000, 5.0, 0.5, seed=7)
    assert 4.0 <= copula_nu(x) <= 6.5


def test_copula_nu_profile_is_exposed_and_peaks_at_the_estimate():
    from ohcamel_quant.models.mc_risk import copula_nu_profile

    x = _t_copula_gaussian_marginals(3000, 5.0, 0.5, seed=7)
    grid, ll = copula_nu_profile(x)
    assert grid[0] == NU_BOUNDS[0] and grid[-1] == NU_BOUNDS[1] and np.all(np.diff(grid) > 0)
    assert abs(grid[int(np.argmax(ll))] - copula_nu(x)) < 1.5     # the grid peak is near the refined optimum
    assert ll.max() - ll[-1] > 20                                  # nu = 30 is decisively worse than the peak


def test_the_ten_day_fhs_path_is_conditional_where_the_copula_is_iid(rets):
    """On a high-volatility start the filtered path carries today's variance into the horizon; the copula's
    iid unconditional draws do not. FHS 10-day P&L variance > iid draws' (same book, same series)."""
    from ohcamel_quant import kernels
    from ohcamel_quant.kernels.types import GarchParams

    x = rets[["SPY"]].to_numpy()
    fit = fit_gjr(x[:, 0])
    usual = float(np.var(100 * x[:, 0]))
    z, w = residual_matrix([fit]), np.array([1.0])

    def start(mult: float):
        g = GarchParams.from_fits([fit])
        g.sigma2_next[:] = mult * usual
        return g

    hot = kernels.fhs_paths(z, start(9.0), w, 10, 40_000, 5, 1)      # three times the usual volatility
    calm = kernels.fhs_paths(z, start(1.0), w, 10, 40_000, 5, 1)
    hot_1d = kernels.fhs_paths(z, start(9.0), w, 1, 40_000, 5, 1)
    iid = kernels.copula_t_paths(x, w, 6.0, 10, 40_000, 5, 1)
    assert np.var(hot) > np.var(iid)
    assert np.var(hot) > 2 * np.var(calm)                  # the start matters: today's variance is carried forward
    # ... but it is carried THROUGH the horizon, not held: variance mean-reverts, so 10 days of it is less than 10
    # constant-rescaled days of the hot first day
    assert np.var(hot) < 0.95 * 10 * np.var(hot_1d)
