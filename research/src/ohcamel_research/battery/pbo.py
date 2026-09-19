"""Probability of Backtest Overfitting by CSCV (Bailey, Borwein, Lopez de
Prado and Zhu), ported from fdq 1.0.0's ``fdq/validation/pbo.py`` (pinned
at 652474c) with one change (ruling 11c), and read beside fdq's own figure
(ruling 11c, amended).

**The change.** fdq's ``_sharpe_cols`` gives a zero-variance column (std
below 1e-12) a Sharpe of NaN, and ``np.argsort`` sorts NaN after every
number, so a flat column ranks *best* out of sample. Here a zero-variance
column's Sharpe is **0.0, never NaN**, in sample and out of sample: a flat
strategy earned nothing, and ranks where nothing ranks.

**It moves PBO both ways, so neither figure alone sets the label.**
- *Up*: an in-sample winner that went flat out of sample was ranked best by
  fdq ("not overfit"); here it ranks by its 0, often last (overfit).
- *Down*: a flat column that was *not* the in-sample best sat above the best
  in fdq's out-of-sample ranking, pushing the best's rank down (overfit);
  here it ranks by its 0, often below the best (not overfit). And in sample
  fdq's ``nanargmax`` never picks a flat column, while here a flat column's
  0 can beat columns that lost money and be picked -- which moves the split
  either way.

So the runner reports PBO as **max(port, fdq)** (``run.pbo_for_verdict``):
neither tool's defect can flatter the fragile label. Where fdq raises -- an
in-sample half in which every column is flat, all-NaN for its
``nanargmax`` -- the port's figure stands alone, and the manifest says so.

**Everything else is fdq's, exactly:** the ``n_splits`` contiguous blocks
(trimmed to a multiple of ``n_splits``, then ``np.array_split``), every
combination of half the blocks as the in-sample half, the in-sample best by
``np.nanargmax``, the out-of-sample rank ``argsort(argsort(...))`` over
``N - 1``, clipped to [1e-6, 1 - 1e-6], its logit, and the overfit test
``logit <= 0``. ``overfit_by_split(..., flat_as_nan=True)`` is fdq's rule
split by split (its mean is fdq's figure, checked where it is used), which
is how the runner counts the splits the two disagree on.

One refusal fdq does not make: a non-finite value in the matrix raises,
because a NaN return would reach the ranking as the NaN this port exists
to keep out of it.
"""

from __future__ import annotations

import itertools

import numpy as np

_STD_EPS = 1e-12  # fdq's own zero-variance line


def sharpe_cols(block: np.ndarray, *, flat_as_nan: bool = False) -> np.ndarray:
    """Per-column per-period Sharpe (mean / sample std). A column whose std
    is below 1e-12 has a Sharpe of exactly 0.0 -- or NaN with
    ``flat_as_nan``, which is fdq's ``_sharpe_cols`` exactly."""
    mean = block.mean(axis=0)
    std = block.std(axis=0, ddof=1)
    flat = std < _STD_EPS
    out = np.full(mean.shape, np.nan) if flat_as_nan else np.zeros(mean.shape, dtype=float)
    out[~flat] = mean[~flat] / std[~flat]
    return out


def overfit_by_split(
    returns_matrix: np.ndarray, n_splits: int = 16, *, flat_as_nan: bool = False
) -> list[bool]:
    """For every CSCV split, in fdq's order, whether the in-sample best
    lands at or below the out-of-sample median (``logit <= 0``). With
    ``flat_as_nan`` this is fdq 1.0.0's rule, and raises where fdq raises."""
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
    out: list[bool] = []
    for combo in itertools.combinations(range(s), half):
        oos_idx = [i for i in range(s) if i not in combo]
        is_block = np.vstack([blocks[i] for i in combo])
        oos_block = np.vstack([blocks[i] for i in oos_idx])
        is_perf = sharpe_cols(is_block, flat_as_nan=flat_as_nan)
        oos_perf = sharpe_cols(oos_block, flat_as_nan=flat_as_nan)
        best = int(np.nanargmax(is_perf))
        order = np.argsort(np.argsort(oos_perf))  # ascending ranks, 0 = worst
        rank = order[best] / (len(oos_perf) - 1)  # in [0, 1]
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        out.append(bool(np.log(rank / (1 - rank)) <= 0.0))
    return out


def probability_backtest_overfitting(returns_matrix: np.ndarray, n_splits: int = 16) -> float:
    """PBO from a (T, N) matrix of per-period returns across N
    configurations: the share of in-sample/out-of-sample splits in which the
    in-sample best lands at or below the out-of-sample median rank, with a
    flat column's Sharpe 0.0."""
    overfit = overfit_by_split(returns_matrix, n_splits)
    if not overfit:
        return 0.5
    return float(np.mean(np.array(overfit)))
