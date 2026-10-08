"""Lane M, M1: purged k-fold and purged walk-forward (Lopez de Prado 2018, ch. 7; Review Focus 2)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ohcamel_quant.models.cv import owned_months, purged_kfold, walk_forward


def test_purged_kfold_twenty_day_example():
    """20 samples, label of sample i spans sessions [i, i+3], 4 folds of 5, embargo 2.

    Fold 1 tests 5..9; the test span is [5, 9+3] = [5, 12]. Overlap purges train
    i with i <= 12 and i+3 >= 5, i.e. 2,3,4 and 10,11,12; the embargo removes the
    two samples starting just after 12, i.e. 13 and 14. Left: 0, 1, 15..19."""
    t0 = np.arange(20)
    t1 = t0 + 3
    folds = purged_kfold(t0, t1, n_splits=4, embargo=2)
    assert folds[1].test.tolist() == [5, 6, 7, 8, 9]
    assert folds[1].train.tolist() == [0, 1, 15, 16, 17, 18, 19]
    assert folds[0].train.tolist() == list(range(10, 20))   # span [0,7]: purge 5,6,7; embargo 8,9
    assert folds[3].train.tolist() == list(range(0, 12))    # span [15,22]: purge 12,13,14; nothing after 22


def test_walk_forward_purges_the_label_that_reaches_into_the_test_year():
    sessions = pd.bdate_range("2007-01-01", "2016-12-31")
    me = pd.Series(sessions, index=sessions).groupby(sessions.to_period("M")).max()
    dates = pd.DatetimeIndex(me.to_numpy()[:-1])
    pos = sessions.get_indexer(dates)
    t0 = pos
    t1 = np.r_[pos[1:] + 1, len(sessions) - 1]          # exit = session after the next month-end (I-Q01-1)
    folds = walk_forward(dates, t0, t1, train_months=60, test_months=12, step_months=6,
                         first_test=pd.Timestamp("2013-01-01"), last_test_end=pd.Timestamp("2014-12-31"), embargo=5)
    f0 = folds[0]
    assert dates[f0.test].min() == pd.Timestamp("2013-01-31") and dates[f0.test].max() == pd.Timestamp("2013-12-31")
    # Train window 2008-01..2012-12 (60 samples); 2012-12-31's label exits 2013-02-01, inside the test span -> purged.
    assert len(f0.train) == 59 and dates[f0.train].max() == pd.Timestamp("2012-11-30")
    assert [f.test_start for f in folds] == [pd.Timestamp("2013-01-01"), pd.Timestamp("2013-07-01"),
                                             pd.Timestamp("2014-01-01")]
    own = owned_months(folds, step_months=6)
    assert own[int(np.flatnonzero(dates == pd.Timestamp("2013-08-30"))[0])] == 1   # newest model covering it
    assert own[int(np.flatnonzero(dates == pd.Timestamp("2014-12-31"))[0])] == 2   # last fold owns its whole year
