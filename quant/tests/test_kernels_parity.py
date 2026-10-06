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


# ---------------------------------------------------------------- A5: garch
@pytest.mark.parametrize("kind,params", [
    ("garch", [0.05, 0.02, 0.08, 0.88, 7.0]),
    ("gjr", [0.05, 0.02, 0.03, 0.12, 0.86, 6.5]),
    ("egarch", [0.04, 0.01, 0.12, -0.08, 0.97, 7.5]),
])
def test_garch_nll_parity(both, etf_returns, kind, params):
    y = 100.0 * etf_returns["SPY"].dropna().to_numpy()[-1500:]
    assert on("rust", kernels.garch_nll, params, y, kind) == pytest.approx(
        on("python", kernels.garch_nll, params, y, kind), rel=1e-10)


def test_egarch_fit_parity(both, etf_returns):
    y = 100.0 * etf_returns["SPY"].dropna().to_numpy()[-1000:]
    r, p = on("rust", kernels.garch_fit, y, "egarch", None), on("python", kernels.garch_fit, y, "egarch", None)
    assert r.nll == pytest.approx(p.nll, rel=1e-6)
    np.testing.assert_allclose(r.params, p.params, rtol=1e-3, atol=1e-3)


# ---------------------------------------------------------------- A6: backtest
from ohcamel_quant.backtest.engine import EngineConfig, StrategyContext, run_backtest  # noqa: E402
from ohcamel_quant.backtest.strategies import (  # noqa: E402
    STRATEGIES,
    get_strategy,
    validate_params,
)

_NINE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]


@pytest.mark.parametrize("cfg", [{}, {"cost_bps": 10.0, "borrow_bps": 75.0, "vol_target": 0.10}], ids=["default", "costly"])
@pytest.mark.parametrize("key", sorted(STRATEGIES))
def test_backtest_parity_every_strategy(both, market, key, cfg):
    """compute plan A6: every registered strategy's fixture run, Rust vs the reference loop, to 1e-10;
    the causality audit passes on the Rust path."""
    px = market.prices(_NINE).data
    cols = ["XLK", "QQQ"] if key in ("pairs_coint", "pairs_distance") else _NINE
    spec = get_strategy(key)
    params = validate_params(spec, {}, cols)
    config = EngineConfig(rebalance=spec.default_rebalance, max_gross_leverage=spec.default_max_leverage, **cfg)
    ctx = StrategyContext(px[cols], None, px["SPY"])
    r = on("rust", run_backtest, ctx, spec.fn, params, config)
    p = on("python", run_backtest, ctx, spec.fn, params, config)
    assert r.kernel_engine == "rust" and p.kernel_engine == "python" and r.audit["passed"]
    for name in ("returns_gross", "returns_net", "turnover", "costs", "borrow"):
        np.testing.assert_allclose(getattr(r, name).to_numpy(), getattr(p, name).to_numpy(), rtol=1e-10, atol=1e-14)
    for name in ("weights", "trades", "contributions"):
        np.testing.assert_allclose(getattr(r, name).to_numpy(), getattr(p, name).to_numpy(), rtol=1e-10, atol=1e-14)
    assert r.notes == p.notes


# ---------------------------------------------------------------- A7: options
from test_options_chain_strategy import make_chain  # noqa: E402

from ohcamel_quant.options.chain import analyze_chain  # noqa: E402


def _smiles():
    """(k, w) per expiry: total variance iv^2 T, read as options/surface.build_surface reads the chain."""
    ca = analyze_chain(make_chain())
    for sid, s in ca.slices.iterrows():
        sm = ca.slice_quotes(str(sid))
        sm = sm[sm["use_smile"]]
        yield sm["k"].to_numpy(), sm["iv"].to_numpy() ** 2 * float(s["T"])


@pytest.mark.parametrize("noisy", [False, True], ids=["exact", "perturbed"])
def test_svi_parity(both, noisy):
    rng = np.random.Generator(np.random.PCG64(17))
    for k, w in _smiles():
        if noisy:
            w = w * (1.0 + 0.004 * rng.standard_normal(w.size))
        wt = 1.0 + 0.5 * np.cos(np.arange(w.size))              # non-uniform weights, mean ~1
        r, p = on("rust", kernels.svi_fit, k, w, wt), on("python", kernels.svi_fit, k, w, wt)
        for f in ("a", "b", "rho", "m", "sigma"):
            assert getattr(r, f) == pytest.approx(getattr(p, f), abs=1e-5), f
        assert r.sse == pytest.approx(p.sse, rel=1e-8, abs=1e-14)
        assert r.constrained_a == p.constrained_a


def test_rv_parity(both):
    # 30 sessions x 390 minutes of a deterministic +-1bp zigzag with a drift; RV exact both ways
    day, minute = 86_400 * 10**9, 60 * 10**9
    ts = (np.arange(30)[:, None] * day + 34_200 * 10**9 + np.arange(390)[None, :] * minute).ravel().astype(np.int64)
    px = 100.0 * np.exp(np.cumsum(np.tile([1e-4, -0.8e-4], 30 * 195)))
    bounds = np.column_stack([np.arange(30) * day + 34_200 * 10**9, np.arange(30) * day + 57_600 * 10**9])
    np.testing.assert_allclose(on("rust", kernels.realized_vol_minute, ts, px, bounds),
                               on("python", kernels.realized_vol_minute, ts, px, bounds), rtol=1e-12)
