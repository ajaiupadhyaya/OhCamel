"""cscv_pbo through the dispatcher, held to the vectorised NumPy of
backtest/validation.py as it stood at 0b87dec (frozen below), and its wiring (compute plan A4)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from scipy import stats

from ohcamel_quant import kernels
from ohcamel_quant.backtest import validation as V


def _legacy(returns: np.ndarray, s: int):
    """validation.cscv_pbo's core at 0b87dec, verbatim (the parity target)."""
    m = np.asarray(returns, dtype=float)
    t = (len(m) // s) * s
    mm = m[len(m) - t:]
    blocks = mm.reshape(s, t // s, m.shape[1])
    b1, b2, bn = blocks.sum(axis=1), (blocks ** 2).sum(axis=1), np.full(s, t // s, dtype=float)
    combos = np.array(list(itertools.combinations(range(s), s // 2)))
    mask = np.zeros((len(combos), s))
    mask[np.arange(len(combos))[:, None], combos] = 1.0
    is1, is2, isn = mask @ b1, mask @ b2, mask @ bn
    oos1, oos2, oosn = b1.sum(0) - is1, b2.sum(0) - is2, bn.sum() - isn

    def sr(s1, s2, n):
        mean = s1 / n[:, None]
        var = (s2 - n[:, None] * mean ** 2) / (n[:, None] - 1.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(var > 0, mean / np.sqrt(np.maximum(var, 0.0)), np.nan)

    sr_is, sr_oos = sr(is1, is2, isn), sr(oos1, oos2, oosn)
    best = np.argmax(np.where(np.isfinite(sr_is), sr_is, -np.inf), axis=1)
    rows = np.arange(len(combos))
    ranks = stats.rankdata(np.where(np.isfinite(sr_oos), sr_oos, -np.inf), axis=1, method="average")
    w = ranks[rows, best] / (m.shape[1] + 1.0)
    lam = np.log(w / (1.0 - w))
    return float(np.mean(lam <= 0)), lam, best, sr_is[rows, best], sr_oos[rows, best]


@pytest.fixture(scope="module")
def trials(etf_returns):
    x = etf_returns.dropna().to_numpy()
    return np.column_stack([x, -0.5 * x])          # 18 trials of real returns


def _check(got, want):
    pbo, lam, best, x, y = want
    assert got["pbo"] == pbo
    np.testing.assert_allclose(got["logits"], lam, rtol=1e-10, atol=1e-14)
    assert np.array_equal(got["selected"], best)
    np.testing.assert_allclose(got["is_sharpe"], x, rtol=1e-10, equal_nan=True)
    np.testing.assert_allclose(got["oos_sharpe"], y, rtol=1e-10, equal_nan=True)


@pytest.mark.parametrize("s", [8, 12, 16])
def test_matches_the_vectorised_numpy(trials, s):
    got = kernels.cscv_pbo(trials, s, 2)
    assert got["n_combinations"] == len(list(itertools.combinations(range(s), s // 2)))
    _check(got, _legacy(trials, s))


def test_cscv_constant_trial_matches_legacy(etf_returns):
    # 2,513 rows, S = 8, 70 combinations. The old NumPy's `var > 0` lands on NaN for the constant
    # column in all 70 (in and out of sample) by the sign of its rounding residue; the relative rule
    # (s2 - n mean^2 <= 1e-10 s2) gives NaN on both engines whatever the summation order, so here the
    # kernel equals the frozen legacy exactly. Replayed in float64 for both orders before writing this.
    x = etf_returns[["SPY", "TLT"]].dropna().to_numpy()
    m = np.column_stack([x, np.full(len(x), 0.0001)])
    _check(kernels.cscv_pbo(m, 8, 1), _legacy(m, 8))


@pytest.mark.parametrize("c", [0.0001, 0.1 / 3, np.pi / 1000, -0.002, 0.0, 7e-5, 1 / 7])
def test_cscv_constant_trial_is_minus_inf_for_any_constant(etf_returns, c):
    # SPY against a constant: the constant's Sharpe is NaN -> -inf, so SPY is the in-sample winner of
    # every combination, ranks 2 of 2 out of sample, logit ln 2, PBO 0. The frozen legacy fails this
    # for c = 0.0001 and 0.1/3 (its residue comes out positive and it selects the constant): the
    # stated deviation in Review Focus 5.
    x = etf_returns["SPY"].dropna().to_numpy()
    r = kernels.cscv_pbo(np.column_stack([x, np.full(len(x), c)]), 8, 1)
    assert np.array_equal(r["selected"], np.zeros(70, dtype=np.int64))
    assert np.isfinite(r["is_sharpe"]).all() and r["pbo"] == 0.0
    np.testing.assert_allclose(r["logits"], np.log(2.0), rtol=1e-15)


def test_hand_cases():
    good, bad = [0.02, 0.01, 0.02, 0.01], [-0.01, 0.0, -0.01, 0.0]
    flip = np.array(list(zip(good + bad, bad + good, strict=True)))     # A wins block 0, B wins block 1
    r = kernels.cscv_pbo(flip, 2, 1)
    assert r["pbo"] == 1.0 and r["logits"] == pytest.approx([np.log(0.5)] * 2, abs=1e-15)  # rank 1 of 2 -> w = 1/3
    keep = np.array(list(zip(good + good, bad + bad, strict=True)))     # A wins everywhere
    assert kernels.cscv_pbo(keep, 2, 1)["pbo"] == 0.0     # rank 2 of 2 -> w = 2/3 -> ln 2 > 0


TIED = [[g, g, b] for g, b in zip([0.02, 0.01, 0.02, 0.01] * 4, [-0.01, 0.0, -0.01, 0.0] * 4, strict=True)]


def test_cscv_tied_trials_first_maximum_and_average_rank():
    # Trials [A, A, B] with A dominant in every block, 16 rows, S = 4 (6 combinations). No other
    # CSCV input has a tie, and the selected trial tying only with itself makes the average rank
    # equal the minimum. Here n* = 0 everywhere (first maximum; the last would be 1); A's OOS rank
    # is 1 + (2 + 1)/2 = 2.5 of 3, w = 0.625, logit = ln(5/3), PBO = 0 (a minimum rank gives 2,
    # logit 0, PBO 1). Hand values, not the frozen legacy: BLAS (mask @ b1) is not promised
    # bit-identical across duplicate columns.
    r = kernels.cscv_pbo(np.array(TIED), 4, 1)
    assert np.array_equal(r["selected"], np.zeros(6, dtype=np.int64))
    np.testing.assert_allclose(r["logits"], np.log(5 / 3), rtol=0, atol=1e-15)
    assert r["pbo"] == 0.0


def test_s20_is_allowed_for_jobs(etf_returns):
    m = etf_returns[["SPY", "TLT", "GLD", "XLE"]].dropna().to_numpy()
    r = kernels.cscv_pbo(m, 20, 2)
    assert r["n_combinations"] == 184_756 and 0.0 <= r["pbo"] <= 1.0   # C(20, 10)


def test_cscv_partition_cap_and_parity(trials):
    with pytest.raises(ValueError, match="<= 20"):
        kernels.cscv_pbo(trials, 22, 1)
    with pytest.raises(ValueError, match="even"):
        kernels.cscv_pbo(trials, 7, 1)


def test_cscv_rejects_nan(trials):
    bad = trials.copy()
    bad[3, 2] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        kernels.cscv_pbo(bad, 8, 1)


def test_validation_cscv_pbo_output_is_unchanged_and_records_engine(trials):
    out = V.cscv_pbo(trials, 8)
    pbo, lam, best, x, y = _legacy(trials, 8)
    assert out["pbo"] == pbo and out["n_combinations"] == len(lam)
    hist, _ = np.histogram(lam, bins=20)
    assert out["logits"]["counts"] == hist.tolist()
    assert out["selected_counts"] == np.bincount(best, minlength=trials.shape[1]).tolist()
    assert out["engine"] == kernels.engine_of("cscv_pbo")
