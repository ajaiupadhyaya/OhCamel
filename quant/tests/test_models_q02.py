"""Lane M, M7: EXP-Q02 features, target and scoring (pre-registration + I-Q02-1..7)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.data import fixtures
from ohcamel_quant.models.features import assert_point_in_time
from ohcamel_quant.models.q02 import (
    FEATURES,
    ewma_month,
    score,
    standardize_expanding,
    target_end,
    target_rv,
    week_ends,
    weekly_features,
)


@pytest.fixture(scope="module")
def spy():
    return fixtures.history("SPY")[0]["adj_close"]


@pytest.fixture(scope="module")
def macro():
    return fixtures.macro(["DGS10", "DGS2", "VIXCLS"])[0]


def test_week_ends_are_the_last_session_of_complete_weeks(spy):
    we = week_ends(spy.index)
    assert all(spy.index.get_loc(d) + 1 < len(spy) for d in we)
    assert (pd.Series(we).dt.isocalendar().week.diff().dropna() != 0).all()


def test_features_by_hand_and_point_in_time(spy, macro):
    f = weekly_features(spy, macro["DGS10"], macro["DGS2"], macro["VIXCLS"])  # VIXCLS: any daily FRED series here
    assert_point_in_time(f)
    we = f.index
    i = 60
    assert f["ret_4w"].iloc[i] == pytest.approx(spy[we[i]] / spy[we[i - 4]] - 1)
    lr = np.log(spy).diff()
    win = lr[(lr.index > we[i - 4]) & (lr.index <= we[i])]
    assert f["rv_4w"].iloc[i] == pytest.approx(np.sqrt(252 * (win ** 2).mean()))
    sl = (macro["DGS10"] - macro["DGS2"]).dropna()
    src = sl[sl.index <= we[i] - pd.offsets.BDay(1)]       # published one business day after its date
    assert f["slope"].iloc[i] == pytest.approx(src.iloc[-1])


def test_target_and_ewma_by_hand(spy):
    we = week_ends(spy.index)
    lr = np.log(spy).diff()
    p = spy.index.get_loc(we[100])
    assert target_rv(spy, we, 21).iloc[100] == pytest.approx((lr.iloc[p + 1:p + 22] ** 2).sum())
    assert np.isnan(target_rv(spy, we, 21).iloc[-1])  # the future is not known yet
    assert target_end(spy, we, 21).iloc[100] == spy.index[p + 21]  # the window's last session
    assert pd.isna(target_end(spy, we, 21).iloc[-1])
    assert ewma_month(spy, we, 0.94, 21).iloc[100] > 0


def test_standardize_uses_only_the_past():
    df = pd.DataFrame({"x": np.arange(100.0)}, index=pd.date_range("2020-01-03", periods=100, freq="W-FRI"))
    z = standardize_expanding(df, 52)
    assert z["x"].iloc[:51].isna().all()
    z2 = standardize_expanding(df.iloc[:60], 52)
    pd.testing.assert_series_equal(z2["x"], z["x"].iloc[:60])  # appending weeks never moves earlier values


def test_score_gates_on_constructed_frames():
    """Constructed weekly frames (no market data): P carries the target's variation beyond EWMA, so both
    gates pass; then a P with no information leaves the verdict DESCRIPTIVE ONLY."""
    idx = pd.date_range("2005-01-07", "2025-12-26", freq="W-FRI")
    rng = np.random.default_rng(0)
    ewma = rng.uniform(1e-4, 4e-4, len(idx))
    p = rng.uniform(0, 1, len(idx))
    rv = ewma + 5e-4 * p + rng.normal(0, 2e-5, len(idx))
    good = pd.DataFrame({"rv": rv, "rv_end": idx + pd.offsets.BDay(21), "ewma": ewma, "p_high": p}, index=idx)
    row, _, _, v = score(good, selection_end=pd.Timestamp("2021-12-31"), holdout_start=pd.Timestamp("2022-01-01"),
                         min_selection=260, min_holdout=26)
    assert v.value == "PASS" and row["hac_t_p"] > 2 and row["dm_pvalue"] < 0.05
    noise = good.assign(p_high=rng.uniform(0, 1, len(idx)), rv=ewma + rng.normal(0, 2e-5, len(idx)).clip(-9e-5))
    _, _, _, v2 = score(noise, selection_end=pd.Timestamp("2021-12-31"), holdout_start=pd.Timestamp("2022-01-01"),
                        min_selection=260, min_holdout=26)
    assert v2.value == "DESCRIPTIVE ONLY" and "ADDS NOTHING OVER EWMA" in v2.detail
    short = good.loc[:"2022-03-31"]
    assert score(short, selection_end=pd.Timestamp("2021-12-31"), holdout_start=pd.Timestamp("2022-01-01"),
                 min_selection=260, min_holdout=26)[3].value == "INSUFFICIENT DATA"
    assert set(FEATURES) == {"ret_4w", "rv_4w", "slope", "d_oas"}


def test_q02_selection_drops_weeks_whose_target_reaches_the_holdout():
    """Review Focus 2 / I-Q02-6: a week-end in December 2021 whose 21-session target runs into January 2022
    is not a selection week. Poisoning exactly those targets moves no selection estimate."""
    idx = pd.date_range("2005-01-07", "2025-12-26", freq="W-FRI")
    rng = np.random.default_rng(1)
    ewma = rng.uniform(1e-4, 4e-4, len(idx))
    p = rng.uniform(0, 1, len(idx))
    ends = pd.DatetimeIndex(idx + pd.offsets.BDay(21))
    f = pd.DataFrame({"rv": ewma + 5e-4 * p + rng.normal(0, 2e-5, len(idx)), "rv_end": ends, "ewma": ewma,
                      "p_high": p}, index=idx)
    kw = dict(selection_end=pd.Timestamp("2021-12-31"), holdout_start=pd.Timestamp("2022-01-01"),
              min_selection=260, min_holdout=26)
    straddle = (idx <= "2021-12-31") & (ends > "2021-12-31")
    assert straddle.sum() >= 4                                   # Dec 2021 week-ends (and late Nov)
    row, reg, fc, _ = score(f, **kw)
    assert row["selection_weeks"] == int((ends <= "2021-12-31").sum())
    assert row["boundary_weeks_purged"] == int(straddle.sum())
    assert row["selection_last_target_end"] <= "2021-12-31"
    poisoned = f.copy()
    poisoned.loc[straddle, "rv"] = 1e-9                         # would move the floor and the OLS if used
    row2, reg2, fc2, _ = score(poisoned, **kw)
    pd.testing.assert_frame_equal(reg2, reg)
    assert row2["hac_t_p"] == row["hac_t_p"]
    pd.testing.assert_frame_equal(fc2, fc)                     # holdout forecasts, incl. the floor, unchanged
    assert fc["date"].min() >= pd.Timestamp("2022-01-01")


def test_reported_qlike_is_the_preregistered_form():
    """I-Q02-6 states QLIKE = RV/F - ln(RV/F) - 1 (Patton 2011): the reported levels use that form, recomputed
    here from the holdout forecasts the scorer returns (vol_league's ln F + RV/F differs by a term in RV)."""
    idx = pd.date_range("2005-01-07", "2025-12-26", freq="W-FRI")
    rng = np.random.default_rng(0)
    ewma = rng.uniform(1e-4, 4e-4, len(idx))
    p = rng.uniform(0, 1, len(idx))
    f = pd.DataFrame({"rv": ewma + 5e-4 * p + rng.normal(0, 2e-5, len(idx)), "rv_end": idx + pd.offsets.BDay(21),
                      "ewma": ewma, "p_high": p}, index=idx)
    row, _, fc, _ = score(f, selection_end=pd.Timestamp("2021-12-31"), holdout_start=pd.Timestamp("2022-01-01"),
                          min_selection=260, min_holdout=26)
    for col, key in (("f_ewma", "qlike_ewma"), ("f_ewma_p", "qlike_ewma_p")):
        x = fc["rv"] / fc[col]
        assert row[key] == pytest.approx(float((x - np.log(x) - 1).mean()), rel=1e-12)
        assert row[key] >= 0  # the Patton form is a loss with minimum 0 at F = RV
