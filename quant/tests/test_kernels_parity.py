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


# ---------------------------------------------------------------- A3: bootstrap
def test_bootstrap_parity(both, etf_returns):
    x = etf_returns["SPY"].dropna().to_numpy()[-250:]
    r = on("rust", kernels.stationary_bootstrap_means, x, 5.0, 200_000, 1, 2)
    p = on("python", kernels.stationary_bootstrap_means, x, 5.0, 200_000, 2, 1)
    H.assert_same_distribution(r, p, 0.01)


def test_bootstrap_bit_identical(both, etf_returns):
    x = etf_returns["SPY"].dropna().to_numpy()[-250:]
    a = on("rust", kernels.stationary_bootstrap_means, x, 5.0, 50_000, 3, 2)
    assert np.array_equal(a, on("rust", kernels.stationary_bootstrap_means, x, 5.0, 50_000, 3, 2))


# ---------------------------------------------------------------- A4: cscv
@pytest.mark.parametrize("s", [8, 12, 16])
def test_cscv_parity(both, etf_returns, s):
    x = etf_returns.dropna().to_numpy()
    m = np.column_stack([x, -0.5 * x])
    r, p = on("rust", kernels.cscv_pbo, m, s, 2), on("python", kernels.cscv_pbo, m, s, 1)
    assert r["pbo"] == p["pbo"] and np.array_equal(r["selected"], p["selected"])
    np.testing.assert_allclose(r["logits"], p["logits"], rtol=1e-10, atol=1e-14)
    np.testing.assert_allclose(r["is_sharpe"], p["is_sharpe"], rtol=1e-10, equal_nan=True)
    np.testing.assert_allclose(r["oos_sharpe"], p["oos_sharpe"], rtol=1e-10, equal_nan=True)


@pytest.mark.parametrize("c", [0.0001, 0.1 / 3, np.pi / 1000])
def test_cscv_constant_trial_parity(both, etf_returns, c):
    # the zero-variance rule is the same on both engines whatever their summation order
    x = etf_returns[["SPY", "TLT"]].dropna().to_numpy()
    m = np.column_stack([x, np.full(len(x), c)])
    r, p = on("rust", kernels.cscv_pbo, m, 8, 1), on("python", kernels.cscv_pbo, m, 8, 1)
    assert r["pbo"] == p["pbo"] and np.array_equal(r["selected"], p["selected"])
    np.testing.assert_allclose(r["logits"], p["logits"], rtol=1e-10, atol=1e-14)


def test_cscv_tie_parity(both):
    # trials [A, A, B], A dominant everywhere, S = 4: each engine against the hand values
    # (first maximum -> n* = 0; averaged rank 2.5 of 3 -> logit ln(5/3); PBO 0), not each other
    tied = np.array([[g, g, b] for g, b in zip([0.02, 0.01, 0.02, 0.01] * 4, [-0.01, 0.0, -0.01, 0.0] * 4, strict=True)])
    for eng in ("rust", "python"):
        r = on(eng, kernels.cscv_pbo, tied, 4, 1)
        assert np.array_equal(r["selected"], np.zeros(6, dtype=np.int64)), eng
        np.testing.assert_allclose(r["logits"], np.log(5 / 3), rtol=0, atol=1e-15, err_msg=eng)
        assert r["pbo"] == 0.0, eng
