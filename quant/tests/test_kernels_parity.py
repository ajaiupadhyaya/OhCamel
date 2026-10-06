"""Every kernel on fixed inputs through both engines (compute plan A1-A7, contract II.4).

Deterministic kernels: 1e-10 relative. Random kernels: kernels_harness's mean/sd
(3 standard errors) and binomial quantile rule (2 standard errors). The
cross-engine tests need the ohcamel_kernels wheel (make kernels-dev) and skip
without it; CI's `native` job installs it and runs this file with
OHCAMEL_QUANT_KERNELS=rust and again with =python.
"""

from __future__ import annotations

import kernels_harness as H
import numpy as np
import pytest

from ohcamel_quant import kernels
from ohcamel_quant.risk.core import tail_count as core_tail_count


@pytest.fixture
def both():
    """Skip unless both engines can run in this process."""
    if kernels._RUST is None:
        pytest.skip("ohcamel_kernels wheel not loaded (make kernels-dev; or OHCAMEL_QUANT_KERNELS=python)")


def on(engine, fn, *args, **kw):
    with kernels.forced(engine):
        return fn(*args, **kw)


# ---------------------------------------------------------------- harness
def test_harness_accepts_two_samples_of_one_law():
    a = np.random.Generator(np.random.PCG64(1)).standard_normal(200_000)
    b = np.random.Generator(np.random.PCG64(2)).standard_normal(200_000)
    H.assert_same_distribution(a, b, 0.01)


def test_harness_rejects_a_mean_shift():
    # a 0.05 sd shift against a 3-se band of 3 sqrt(2/200,000) = 0.0095 sd
    a = np.random.Generator(np.random.PCG64(1)).standard_normal(200_000)
    b = np.random.Generator(np.random.PCG64(2)).standard_normal(200_000) + 0.05
    with pytest.raises(AssertionError):
        H.assert_same_mean_sd(a, b)


def test_harness_rejects_a_scale_change_in_the_tail():
    # 1% quantile moves 2.326 x 0.05 = 0.116; the rank band of +-2 sqrt(2 n p (1-p)) = +-126
    # ranks spans about 126 / (n phi(2.326)) = 0.024 in value
    a = np.random.Generator(np.random.PCG64(1)).standard_normal(200_000) * 1.05
    b = np.random.Generator(np.random.PCG64(2)).standard_normal(200_000)
    with pytest.raises(AssertionError):
        H.assert_same_quantile(a, b, 0.01)


# ---------------------------------------------------------------- tail rule
@pytest.mark.parametrize("n,alpha", [(1000, 0.99), (250, 0.99), (99, 0.99), (1, 0.99), (500, 0.95),
                                     (1_000_000, 0.975), (37, 0.6)])
def test_tail_count_parity(both, n, alpha):
    # the reference IS risk.core.tail_count; Rust must agree everywhere
    assert on("rust", kernels.tail_count, n, alpha) == core_tail_count(n, alpha)
    assert on("python", kernels.tail_count, n, alpha) == core_tail_count(n, alpha)


# ---------------------------------------------------------------- A2: risk
from ohcamel_quant.kernels.types import GarchParams  # noqa: E402
from ohcamel_quant.risk import garch as gm  # noqa: E402


@pytest.fixture(scope="module")
def fits(etf_returns):
    r = etf_returns[["SPY", "TLT"]].dropna()
    return [gm.fit_garch(r[c].to_numpy()[-1500:], "gjr") for c in r.columns]


@pytest.mark.parametrize("alpha", [0.95, 0.99])
def test_var_es_parity(both, etf_returns, alpha):
    pnl = etf_returns["SPY"].dropna().to_numpy()
    r, p = on("rust", kernels.var_es_from_pnl, pnl, alpha), on("python", kernels.var_es_from_pnl, pnl, alpha)
    assert r == pytest.approx(p, rel=1e-10)


def test_fhs_parity_one_asset(both, fits):
    f = fits[0]
    args = (f.std_resid, GarchParams.from_fits([f]), [1.0], 10, 200_000)
    H.assert_same_distribution(on("rust", kernels.fhs_paths, *args, 1, 2),
                               on("python", kernels.fhs_paths, *args, 2, 1), 0.01)


def test_fhs_parity_two_assets_joint_rows(both, fits):
    z = np.column_stack([f.std_resid for f in fits])
    args = (z, GarchParams.from_fits(fits), [0.6, 0.4], 10, 200_000)
    H.assert_same_distribution(on("rust", kernels.fhs_paths, *args, 3, 2),
                               on("python", kernels.fhs_paths, *args, 4, 1), 0.01)


def test_fhs_reference_matches_arch_bootstrap(fits):
    """The kernel path keeps fhs_var_es's semantics: same law as arch's residual bootstrap."""
    f = fits[0]
    arch_cum, _ = gm._fhs_paths(f, 10, 200_000, 20_240_805)
    ref = on("python", kernels.fhs_paths, f.std_resid, GarchParams.from_fits([f]), [1.0], 10, 200_000, 9, 1)
    H.assert_same_distribution(ref, arch_cum, 0.01)


def test_fhs_threads_change_stream_not_law(both, fits):
    f = fits[0]
    args = (f.std_resid, GarchParams.from_fits([f]), [1.0], 10, 200_000, 5)
    one, two = on("rust", kernels.fhs_paths, *args, 1), on("rust", kernels.fhs_paths, *args, 2)
    assert not np.array_equal(one, two)
    H.assert_same_mean_sd(one, two)


def test_kendall_corr_parity(both, etf_returns):
    x = etf_returns[["SPY", "TLT", "GLD", "XLE"]].dropna().to_numpy()
    np.testing.assert_allclose(on("rust", kernels._kendall_corr, x), on("python", kernels._kendall_corr, x),
                               rtol=1e-12, atol=1e-15)


@pytest.mark.parametrize("horizon", [1, 5])
def test_copula_parity(both, etf_returns, horizon):
    x = etf_returns[["SPY", "TLT", "GLD", "XLE"]].dropna().to_numpy()
    args = (x, [0.4, 0.3, 0.2, 0.1], 6.0, horizon, 200_000)
    H.assert_same_distribution(on("rust", kernels.copula_t_paths, *args, 1, 2),
                               on("python", kernels.copula_t_paths, *args, 2, 1), 0.01)
