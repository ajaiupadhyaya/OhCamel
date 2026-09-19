"""Probability of Backtest Overfitting by CSCV (Bailey, Borwein, Lopez de
Prado and Zhu), ported from fdq 1.0.0's ``fdq/validation/pbo.py`` (pinned
at 652474c) with one change (ruling 11c).

**The change.** fdq's ``_sharpe_cols`` gives a zero-variance column (std
below 1e-12) a Sharpe of NaN, and ``np.argsort`` sorts NaN after every
number. So a configuration that is flat through an out-of-sample half -- a
long moving average that never went long there -- was ranked *best* out of
sample, and an in-sample winner that went flat out of sample read as "not
overfit". Here a zero-variance column's Sharpe is **0.0, never NaN**, in
sample and out of sample: a flat strategy earned nothing, and ranks where
nothing ranks.

**Everything else is fdq's, exactly:** the ``n_splits`` contiguous blocks
(trimmed to a multiple of ``n_splits``, then ``np.array_split``), every
combination of half the blocks as the in-sample half, the in-sample best by
``np.nanargmax``, the out-of-sample rank ``argsort(argsort(...))`` over
``N - 1``, clipped to [1e-6, 1 - 1e-6], its logit, and the overfit test
``logit <= 0``. On a matrix with no flat column the two agree to the bit
(``tests/test_battery_pbo.py``).

One refusal fdq does not make: a non-finite value in the matrix raises,
because a NaN return would reach the ranking as the NaN this port exists
to keep out of it.
"""

from __future__ import annotations

import itertools

import numpy as np

_STD_EPS = 1e-12  # fdq's own zero-variance line


def sharpe_cols(block: np.ndarray) -> np.ndarray:
    """Per-column per-period Sharpe (mean / sample std), as fdq's
    ``_sharpe_cols`` -- except that a column whose std is below 1e-12 has a
    Sharpe of exactly 0.0 instead of NaN."""
    mean = block.mean(axis=0)
    std = block.std(axis=0, ddof=1)
    flat = std < _STD_EPS
    out = np.zeros(mean.shape, dtype=float)
    out[~flat] = mean[~flat] / std[~flat]
    return out


def probability_backtest_overfitting(returns_matrix: np.ndarray, n_splits: int = 16) -> float:
    """PBO from a (T, N) matrix of per-period returns across N
    configurations: the share of in-sample/out-of-sample splits in which the
    in-sample best lands at or below the out-of-sample median rank."""
    m = np.asarray(returns_matrix, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        msg = "returns_matrix must be (T, N) with N >= 2 configurations"
        raise ValueError(msg)
    if not np.all(np.isfinite(m)):
        raise ValueError("returns_matrix holds a non-finite value; PBO cannot rank it")
    s = n_splits if n_splits % 2 == 0 else n_splits - 1
    t = m.shape[0]
    if s < 2 or t < s:
        msg = "n_splits must be even, >= 2, and <= number of observations"
        raise ValueError(msg)
    blocks = np.array_split(m[: t - (t % s)], s, axis=0)
    half = s // 2
    logits: list[float] = []
    for combo in itertools.combinations(range(s), half):
        oos_idx = [i for i in range(s) if i not in combo]
        is_block = np.vstack([blocks[i] for i in combo])
        oos_block = np.vstack([blocks[i] for i in oos_idx])
        is_perf = sharpe_cols(is_block)
        oos_perf = sharpe_cols(oos_block)
        best = int(np.nanargmax(is_perf))
        order = np.argsort(np.argsort(oos_perf))  # ascending ranks, 0 = worst
        rank = order[best] / (len(oos_perf) - 1)  # in [0, 1]
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(float(np.log(rank / (1 - rank))))
    if not logits:
        return 0.5
    return float(np.mean(np.array(logits) <= 0.0))
