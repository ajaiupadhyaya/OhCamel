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
