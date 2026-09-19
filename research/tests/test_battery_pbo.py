"""PBO by CSCV, ported from fdq into ``battery/pbo.py`` with one change
(ruling 11c): a zero-variance column's Sharpe is 0.0, never NaN."""

from __future__ import annotations

from math import comb

import numpy as np
import pytest
from fdq.validation import pbo as fdq_pbo

from ohcamel_research.battery.pbo import probability_backtest_overfitting, sharpe_cols


def _random_matrix(seed: int, t: int, n: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    m = rng.normal(0.0003, 0.01, size=(t, n)) + rng.normal(0.0, 0.0005, size=n)
    assert np.all(m.std(axis=0, ddof=1) > 1e-6)  # no flat column
    return m


@pytest.mark.parametrize(
    ("seed", "t", "n"),
    [(0, 160, 2), (1, 300, 3), (42, 300, 3), (7, 1511, 3), (3, 97, 5)],
)
def test_the_port_is_fdq_at_sixteen_splits_on_a_matrix_with_no_flat_column(seed, t, n):
    # Sixteen splits, as the runner calls it: 12870 combinations each.
    m = _random_matrix(seed, t, n)
    ours = probability_backtest_overfitting(m)
    theirs = fdq_pbo.probability_backtest_overfitting(m)
    assert abs(ours - theirs) <= 1e-12


@pytest.mark.parametrize("seed", range(20))
def test_the_port_is_fdq_at_eight_splits_over_many_seeds(seed):
    m = _random_matrix(seed, 120 + seed, 2 + seed % 4)
    ours = probability_backtest_overfitting(m, n_splits=8)
    theirs = fdq_pbo.probability_backtest_overfitting(m, n_splits=8)
    assert abs(ours - theirs) <= 1e-12


def test_a_flat_columns_sharpe_is_zero_not_nan():
    block = np.array([[0.0, 0.01], [0.0, -0.01], [0.0, 0.02]])
    assert np.isnan(fdq_pbo._sharpe_cols(block)[0])
    assert sharpe_cols(block)[0] == 0.0
    assert sharpe_cols(block)[1] == fdq_pbo._sharpe_cols(block)[1]


def _flat_out_of_sample_matrix() -> np.ndarray:
    """16 blocks of 10 rows, two configurations, built so every split is
    worked out by hand:
    - A: 0.01 +/- 0.001 in blocks 0-7, exactly 0 in blocks 8-15. Over any
      union holding a share f > 0 of active rows its Sharpe is about
      sqrt(f / (1 - f)) >= 0.38; over blocks 0-7 alone it is about 10.
    - B: 0.001 +/- 0.01 alternating, in every block: Sharpe about 0.099 over
      any union of blocks.
    """
    rows = np.arange(160)
    alternating = np.where(rows % 2 == 0, 1.0, -1.0)
    a = np.where(rows < 80, 0.01 + 0.001 * alternating, 0.0)
    b = 0.001 + 0.01 * alternating
    return np.column_stack([a, b])


def test_an_in_sample_winner_flat_out_of_sample_is_overfit_in_the_port_and_not_in_fdq():
    """Of the C(16, 8) = 12870 splits:
    - in-sample = blocks 8-15: A is flat there, B is best, and A (Sharpe
      about 10 out of sample) ranks above it. Overfit, in both.
    - in-sample = blocks 0-7: A is best and flat out of sample. fdq's NaN
      sorts above B's 0.099, so A ranks best: "not overfit". The port's 0.0
      sorts below it, so A ranks worst: overfit.
    - every other split: A holds active rows on both sides, is best in
      sample and ranks above B out of sample. Not overfit, in both.
    """
    m = _flat_out_of_sample_matrix()
    total = comb(16, 8)
    assert fdq_pbo.probability_backtest_overfitting(m) == pytest.approx(1 / total, abs=1e-15)
    assert probability_backtest_overfitting(m) == pytest.approx(2 / total, abs=1e-15)

    # The split itself: A flat out of sample ranks 0, worst, in the port.
    blocks = np.array_split(m, 16)
    oos = np.vstack(blocks[8:])
    ours = sharpe_cols(oos)
    assert ours[0] == 0.0 and ours[1] > 0.0
    assert np.argsort(np.argsort(ours))[0] == 0
    assert np.argsort(np.argsort(fdq_pbo._sharpe_cols(oos)))[0] == 1  # fdq: best


def test_a_non_finite_return_is_refused():
    m = _flat_out_of_sample_matrix()
    m[5, 1] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        probability_backtest_overfitting(m)
