"""Lane M, M1: point-in-time features and the leakage guard (Review Focus 2)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.backtest.engine import LookAheadError
from ohcamel_quant.data import fixtures
from ohcamel_quant.models.features import (
    ASOF,
    FEATURES,
    Panel,
    asof_join,
    assert_point_in_time,
    build_features,
    month_ends,
)

TICKERS = ["SPY", "QQQ", "TLT", "GLD"]


@pytest.fixture(scope="module")
def panel() -> Panel:
    frames = {t: fixtures.history(t)[0] for t in TICKERS}
    f = {c: pd.DataFrame({t: frames[t][c] for t in TICKERS}) for c in ("open", "close", "adj_close", "volume")}
    return Panel(open=f["open"], close=f["close"], adj_close=f["adj_close"], volume=f["volume"])


def test_month_ends_drop_the_incomplete_final_month(panel):
    me = month_ends(panel.adj_close.index)
    assert me[-1] < panel.adj_close.index[-1].to_period("M").start_time  # the last month present is not complete
    assert all(panel.adj_close.index.get_loc(d) + 1 < len(panel.adj_close) for d in me)


def test_features_by_hand(panel):
    d = month_ends(panel.adj_close.index)[20]
    f = build_features(panel, pd.DatetimeIndex([d]))
    px = panel.adj_close
    p = px.index.get_loc(d)
    spy = f.loc[(d, "SPY")]
    assert spy["mom_12_1"] == pytest.approx(px["SPY"].iloc[p - 21] / px["SPY"].iloc[p - 252] - 1)
    assert spy["rev_1m"] == pytest.approx(px["SPY"].iloc[p] / px["SPY"].iloc[p - 21] - 1)
    lr = np.log(px["SPY"]).diff().iloc[p - 59:p + 1]
    assert spy["vol_60d"] == pytest.approx(lr.std(ddof=1) * np.sqrt(252))
    assert spy["beta_252d"] == pytest.approx(1.0)  # SPY on itself
    dv = (panel.close["SPY"].iloc[p - 19:p + 1] * panel.volume["SPY"].iloc[p - 19:p + 1]).mean()
    assert spy["dollar_vol_20d"] == pytest.approx(dv)
    assert all(spy[c + ASOF] == d for c in FEATURES)


def test_features_are_prefix_invariant(panel):
    """A feature at t computed on a panel that ends at t equals the one computed on the full panel."""
    dates = month_ends(panel.adj_close.index)[15:30]
    full = build_features(panel, dates)
    for d in dates[::5]:
        cut = build_features(panel.truncate(d), pd.DatetimeIndex([d]))
        pd.testing.assert_frame_equal(cut, full.loc[[d]])


def test_leaked_feature_raises(panel):
    dates = month_ends(panel.adj_close.index)[15:18]
    f = build_features(panel, dates)
    assert_point_in_time(f)  # clean
    leaked = f.copy()
    nxt = panel.adj_close.index[panel.adj_close.index.get_indexer(dates) + 1]
    leaked["rev_1m" + ASOF] = np.repeat(nxt, len(TICKERS))  # built with t+1 data
    with pytest.raises(LookAheadError, match="rev_1m"):
        assert_point_in_time(leaked)
    with pytest.raises(ValueError, match="source timestamp"):
        assert_point_in_time(f.drop(columns=["vol_60d" + ASOF]))


def test_asof_join_lags_by_one_business_day():
    s = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-07"]))  # Mon Tue Wed
    out = asof_join(s, pd.DatetimeIndex(pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-09"])))
    # Monday: nothing published yet; Tuesday: Monday's value; Friday: Wednesday's (available Thursday).
    assert np.isnan(out["value"].iloc[0]) and out["value"].iloc[1] == 1.0 and out["value"].iloc[2] == 3.0
    assert out[ASOF].iloc[1] == pd.Timestamp("2026-10-06")
