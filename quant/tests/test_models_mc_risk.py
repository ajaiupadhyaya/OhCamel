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
    rng = np.random.default_rng(1)  # Gaussian draws: the t MLE runs to its upper end; the clip holds it at 30
    assert copula_nu(rng.standard_normal((2000, 2))) == NU_BOUNDS[1]


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
