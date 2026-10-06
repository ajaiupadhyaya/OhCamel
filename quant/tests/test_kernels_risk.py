"""var_es_from_pnl, fhs_paths and copula_t_paths through the dispatcher (whatever
engine this process runs), and their wiring into risk.garch.fhs_var_es (compute plan A2)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ohcamel_quant import kernels
from ohcamel_quant.kernels.types import GarchParams
from ohcamel_quant.risk import garch as gm
from ohcamel_quant.risk.core import empirical_var_es


def in_support(x: np.ndarray, support: np.ndarray, rtol: float = 1e-12) -> bool:
    """Every x equals some support value up to rounding: a one-day path's P&L goes
    through expm1(log1p(r)), which can move r by a few ulps (~1e-16 relative)."""
    s = np.sort(support)
    i = np.clip(np.searchsorted(s, x), 1, s.size - 1)
    near = np.where(np.abs(s[i] - x) < np.abs(s[i - 1] - x), s[i], s[i - 1])
    return bool(np.all(np.abs(near - x) <= rtol * np.maximum(np.abs(x), 1e-300)))


@pytest.fixture(scope="module")
def spy(etf_returns):
    return etf_returns["SPY"].dropna().to_numpy()


@pytest.fixture(scope="module")
def gjr(spy):
    return gm.fit_garch(spy[-1500:], "gjr")


# ------------------------------------------------------------- var_es_from_pnl
def test_tail_count_pin_1000_at_99():
    # compute plan A2: n = 1000, alpha = 0.99 -> ceil(9.999999999) = 10
    assert kernels.tail_count(1000, 0.99) == 10


def test_var_es_hand():
    # losses 0.001..1.000; k = 10: VaR = 0.991, ES = mean(0.991..1.000) = 0.9955
    v, e = kernels.var_es_from_pnl(-np.arange(1, 1001) / 1000.0, 0.99)
    assert v == pytest.approx(0.991, abs=1e-15) and e == pytest.approx(0.9955, abs=1e-12)


@pytest.mark.parametrize("alpha", [0.95, 0.975, 0.99])
def test_var_es_is_empirical_var_es(etf_returns, alpha):
    pnl = etf_returns.dropna().to_numpy() @ np.full(9, 1 / 9)
    got = kernels.var_es_from_pnl(pnl, alpha)
    want = empirical_var_es(-pnl, alpha)   # the reference rule, risk.core
    assert got == pytest.approx(want, rel=1e-12)


def test_var_es_drops_non_finite():
    assert kernels.var_es_from_pnl([-0.5, np.nan, 0.1, np.inf], 0.6) == (0.5, 0.5)
    v, e = kernels.var_es_from_pnl([np.nan], 0.99)
    assert math.isnan(v) and math.isnan(e)


# ------------------------------------------------------------- fhs_paths
def test_fhs_horizon_one_is_the_scaled_residual_set(gjr):
    z = gjr.std_resid[np.isfinite(gjr.std_resid)]
    pnl = kernels.fhs_paths(z, GarchParams.from_fits([gjr]), [1.0], 1, 5000, 7, 1)
    support = (gjr.mu + math.sqrt(gjr.next_variance_pct) * z) / 100.0   # r = (mu + sigma z_j)/100
    assert in_support(pnl, support)


def test_fhs_same_seed_same_output(gjr):
    z = gjr.std_resid
    p = GarchParams.from_fits([gjr])
    a = kernels.fhs_paths(z, p, [1.0], 10, 20_000, 5, 2)
    assert np.array_equal(a, kernels.fhs_paths(z, p, [1.0], 10, 20_000, 5, 2))


@pytest.mark.parametrize("kw,msg", [
    ({"std_resid": np.array([0.1, np.nan, 0.2])}, "finite"),
    ({"weights": [0.5, 0.5]}, "columns"),
    ({"threads": 0}, "threads"),
    ({"n_paths": 0}, "n_paths"),
    ({"n_paths": 10**9}, "n_paths"),
    ({"horizon": 0}, "horizon"),
])
def test_fhs_rejects_bad_input(gjr, kw, msg):
    args = {"std_resid": gjr.std_resid, "sigma_path_params": GarchParams.from_fits([gjr]), "weights": [1.0],
            "horizon": 5, "n_paths": 100, "seed": 1, "threads": 1, **kw}
    with pytest.raises(ValueError, match=msg):
        kernels.fhs_paths(**args)


# ------------------------------------------------------------- copula_t_paths
def test_kendall_corr_hand():
    # x = 1,2,3,4 vs y = 1,3,2,4: tau = 2/3, R = sin(pi/3)
    r = kernels._kendall_corr(np.array([[1.0, 1.0], [2.0, 3.0], [3.0, 2.0], [4.0, 4.0]]))
    assert r.shape == (2, 2) and r[0, 1] == pytest.approx(math.sin(math.pi / 3), abs=1e-15)


def test_copula_one_asset_draws_observed_returns(spy):
    x = spy[-500:]
    pnl = kernels.copula_t_paths(x, [1.0], 5.0, 1, 5000, 3, 1)
    assert in_support(pnl, x)


def test_copula_rejects_constant_column(spy):
    x = np.column_stack([spy[-300:], np.zeros(300)])
    with pytest.raises(ValueError, match="constant"):
        kernels.copula_t_paths(x, [0.5, 0.5], 5.0, 1, 100, 1, 1)


@pytest.mark.parametrize("nu", [0.0, -1.0, float("inf")])
def test_copula_rejects_bad_nu(spy, nu):
    with pytest.raises(ValueError, match="nu"):
        kernels.copula_t_paths(spy[-300:], [1.0], nu, 1, 100, 1, 1)


# ------------------------------------------------------------- wiring: fhs_var_es
def test_fhs_var_es_default_path_is_unchanged(gjr):
    """Synchronous callers keep arch's bootstrap and its numbers (gate GA)."""
    est = gm.fhs_var_es(gjr, 0.99, 10, simulations=20_000)
    cum, _ = gm._fhs_paths(gjr, 10, 20_000, 20_240_805)
    assert (est.var, est.es) == empirical_var_es(-cum, 0.99)
    assert "engine" not in est.params


def test_fhs_var_es_on_the_kernel_records_engine_and_lifts_the_cap_for_jobs(gjr):
    # the synchronous cap at horizon 20 is 2.5M / 20 = 125,000 paths
    sync = gm.fhs_var_es(gjr, 0.99, 20, simulations=200_000)
    assert sync.params["simulations"] == 125_000
    job = gm.fhs_var_es(gjr, 0.99, 20, simulations=200_000, use_kernels=True, threads=2,
                        max_cells=gm.JOB_FHS_MAX_CELLS)
    assert job.params["simulations"] == 200_000
    assert job.params["engine"] == kernels.engine_of("fhs_paths")
    assert not any("capped" in n for n in job.notes)
    assert 0 < job.var < job.es


def test_job_cap_is_larger_than_the_synchronous_one():
    assert gm.JOB_FHS_MAX_CELLS > gm.FHS_MAX_CELLS
