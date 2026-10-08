"""Purged cross-validation (Lopez de Prado 2018, *Advances in Financial Machine Learning*, ch. 7).

Sample ``i`` is decided at session position ``t0[i]``; its label is known at
``t1[i] >= t0[i]``. A training sample is purged when its interval ``[t0, t1]``
overlaps the test span ``[min t0(test), max t1(test)]``, and embargoed when it
starts within ``embargo`` sessions after that span.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Fold:
    train: np.ndarray
    test: np.ndarray
    test_start: pd.Timestamp | None = None


def purge(train: np.ndarray, t0: np.ndarray, t1: np.ndarray, test: np.ndarray, embargo: int) -> np.ndarray:
    lo, hi = int(t0[test].min()), int(t1[test].max())
    keep = ~((t0[train] <= hi) & (t1[train] >= lo))
    tr = train[keep]
    return tr[~((t0[tr] > hi) & (t0[tr] <= hi + embargo))]


def _check(t0: np.ndarray, t1: np.ndarray) -> None:
    if len(t0) != len(t1) or np.any(np.diff(t0) < 0) or np.any(t1 < t0):
        raise ValueError("t0 must be sorted and t1 >= t0, of one length")


def purged_kfold(t0: np.ndarray, t1: np.ndarray, n_splits: int, embargo: int) -> list[Fold]:
    t0, t1 = np.asarray(t0), np.asarray(t1)
    _check(t0, t1)
    every = np.arange(len(t0))
    return [Fold(purge(np.setdiff1d(every, b), t0, t1, b, embargo), b)
            for b in np.array_split(every, n_splits)]


def walk_forward(dates: pd.DatetimeIndex, t0: np.ndarray, t1: np.ndarray, *, train_months: int, test_months: int,
                 step_months: int, first_test: pd.Timestamp, last_test_end: pd.Timestamp, embargo: int,
                 allow_partial: bool = False) -> list[Fold]:
    """Rolling windows on sample dates: train ``[start - train_months, start)``, test
    ``[start, start + test_months)``, starts every ``step_months``. A window must end
    on or before ``last_test_end`` unless ``allow_partial`` (the holdout's last fold).

    ``dates`` is the date that places a sample in a window. Pass the date its label starts
    accruing (EXP-Q01: the entry session), not the decision date, so that a test window's
    labels are realised inside it and never reach into the next window (M6, I-Q01-1)."""
    t0, t1 = np.asarray(t0), np.asarray(t1)
    _check(t0, t1)
    dates = pd.DatetimeIndex(dates)
    folds: list[Fold] = []
    start = pd.Timestamp(first_test)
    while True:
        end = start + pd.DateOffset(months=test_months)
        if end - pd.Timedelta(days=1) > last_test_end and not allow_partial:
            break
        test = np.flatnonzero((dates >= start) & (dates < end) & (dates <= last_test_end))
        if test.size == 0:
            break
        train = np.flatnonzero((dates >= start - pd.DateOffset(months=train_months)) & (dates < start))
        folds.append(Fold(purge(train, t0, t1, test, embargo), test, start))
        start = start + pd.DateOffset(months=step_months)
    return folds


def owned_months(folds: list[Fold], step_months: int) -> dict[int, int]:
    """Sample position -> the newest fold whose test covers it (I-Q01-4): each fold owns
    ``[test_start, test_start + step)``; the last fold owns its whole test window."""
    own: dict[int, int] = {}
    for k, f in enumerate(folds):
        for i in f.test:
            own[int(i)] = k  # later folds overwrite: the newest model wins
    return own
