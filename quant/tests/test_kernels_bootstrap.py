"""stationary_bootstrap_means through the dispatcher and bootstrap_sharpe's kernel path
(compute plan A3)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant import kernels
from ohcamel_quant.backtest import validation as V
from ohcamel_quant.kernels import reference


@pytest.fixture(scope="module")
def spy(etf_returns):
    return etf_returns["SPY"].dropna()


def test_reference_block_length_is_pinned():
    # geometric with p = 1/5 -> mean 5; 40 walks of 10,000: ~80,000 blocks, se 0.016, plus < 0.01
    # truncation bias (one cut block per walk). A new block is any step that is not idx+1 mod n
    # (a fresh start equal to idx+1 has probability 1/n = 1e-4: negligible).
    rng = np.random.Generator(np.random.PCG64(3))
    idx = reference._sb_indices(rng, 10_000, 0.2, 40)
    starts = 40 + int(np.count_nonzero(idx[:, 1:] != (idx[:, :-1] + 1) % 10_000))
    assert abs(idx.size / starts - 5.0) < 0.06


def test_means_are_unbiased(spy):
    # circular stationary bootstrap: E*[mean*] = mean(x) exactly; check within 3 se of 20,000 reps
    x = spy.to_numpy()[-2000:]
    m = kernels.stationary_bootstrap_means(x, 5.0, 20_000, 1, 2)
    assert abs(m.mean() - x.mean()) <= 3 * m.std(ddof=1) / math.sqrt(m.size)


def test_one_d_in_one_d_out_and_columns_share_indices(spy):
    x = spy.to_numpy()[-300:]
    one = kernels.stationary_bootstrap_means(x, 4.0, 1000, 8, 1)
    two = kernels.stationary_bootstrap_means(np.column_stack([x, 2.0 * x]), 4.0, 1000, 8, 1)
    assert one.shape == (1000,) and two.shape == (1000, 2)
    # the stream does not depend on the values, and each column is reduced on its own (m, n)
    # gather on both engines, so column 0 is bit-identical to the 1-D run (reference.py docstring)
    assert np.array_equal(two[:, 0], one)
    np.testing.assert_allclose(two[:, 1], 2.0 * two[:, 0], rtol=1e-12)


@pytest.mark.parametrize("kw,msg", [({"mean_block": 0.5}, "mean_block"), ({"reps": 0}, "reps"),
                                    ({"x": np.ones(1)}, "n >= 2"), ({"x": np.array([1.0, np.nan])}, "finite")])
def test_rejects_bad_input(kw, msg):
    args = {"x": np.arange(10.0), "mean_block": 2.0, "reps": 10, "seed": 1, "threads": 1, **kw}
    with pytest.raises(ValueError, match=msg):
        kernels.stationary_bootstrap_means(**args)


def test_bootstrap_sharpe_default_is_unchanged(spy):
    out = V.bootstrap_sharpe(spy, reps=500, seed=7)
    assert "engine" not in out and out["reps_cap"] == V.MAX_REPS and out["reps"] == 500


def test_bootstrap_sharpe_on_the_kernel(spy):
    legacy = V.bootstrap_sharpe(spy, reps=1000, seed=7)
    job = V.bootstrap_sharpe(spy, reps=20_000, seed=7, use_kernels=True, threads=2, max_reps=V.JOB_MAX_REPS)
    assert job["reps"] == 20_000 and job["reps_cap"] == V.JOB_MAX_REPS
    assert job["engine"] == kernels.engine_of("stationary_bootstrap_means")
    assert job["sharpe"] == legacy["sharpe"]                    # the point estimate is f(x), untouched
    assert job["expected_block_length"] == legacy["expected_block_length"]
    # same law, other draws: the 1000-rep legacy CI ends carry ~0.03 of noise; 0.15 is far outside it
    assert abs(job["ci_low"] - legacy["ci_low"]) < 0.15 and abs(job["ci_high"] - legacy["ci_high"]) < 0.15
    assert job["std_error"] == pytest.approx(legacy["std_error"], rel=0.15)


def test_bootstrap_sharpe_kernel_needs_enough_data():
    with pytest.raises(ValueError, match="60 observations"):
        V.bootstrap_sharpe(pd.Series(np.linspace(-0.01, 0.01, 30)), use_kernels=True)
