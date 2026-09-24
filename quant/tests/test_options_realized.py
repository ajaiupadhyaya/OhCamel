"""Realized-volatility estimators, cone and VRP on the committed REAL SPY OHLCV fixture."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.options import realized as rv


@pytest.fixture(scope="module")
def spy(market) -> pd.DataFrame:
    return rv.adjusted_ohlc(market.ohlcv("SPY").data)


def test_estimators_positive_and_sensibly_ordered(spy):
    assert len(spy) > 2400
    e = rv.all_estimators(spy, 21).dropna()
    assert (e > 0).all().all()
    med = e.median()
    # SPY vol is ~10-25% annualized in the median month
    assert ((med > 0.05) & (med < 0.30)).all()
    # range estimators ignore the overnight gap and so run below close-to-close;
    # Yang-Zhang adds it back and tracks close-to-close
    for x in ("parkinson", "garman_klass", "rogers_satchell"):
        assert med[x] < med["close_to_close"]
    assert med["yang_zhang"] == pytest.approx(med["close_to_close"], rel=0.15)
    assert med["garman_klass"] == pytest.approx(med["parkinson"], rel=0.15)
    # COVID crash: every estimator spikes above 50% in March 2020
    assert (e.loc["2020-03-16":"2020-04-01"].max() > 0.5).all()


def test_parkinson_and_close_to_close_match_hand_calculation(spy):
    rows = spy.iloc[100:105]      # 5 real sessions -> 4 daily terms
    hl = [math.log(h / lo) ** 2 for h, lo in zip(rows["high"].iloc[1:], rows["low"].iloc[1:], strict=True)]
    park = math.sqrt(252 * sum(hl) / len(hl) / (4 * math.log(2)))
    assert float(rv.rolling_vol(rows, 4, "parkinson").iloc[-1]) == pytest.approx(park, rel=1e-12)
    r = [math.log(b / a) for a, b in zip(rows["close"].iloc[:-1], rows["close"].iloc[1:], strict=True)]
    m = sum(r) / len(r)
    cc = math.sqrt(252 * sum((x - m) ** 2 for x in r) / (len(r) - 1))
    assert float(rv.rolling_vol(rows, 4, "close_to_close").iloc[-1]) == pytest.approx(cc, rel=1e-12)
    # Rogers-Satchell on the same rows
    rs = [math.log(h / c) * math.log(h / o) + math.log(lo / c) * math.log(lo / o)
          for o, h, lo, c in rows[["open", "high", "low", "close"]].iloc[1:].itertuples(index=False)]
    assert float(rv.rolling_vol(rows, 4, "rogers_satchell").iloc[-1]) == pytest.approx(
        math.sqrt(252 * sum(rs) / 4), rel=1e-12)


def test_yang_zhang_weights():
    # with no overnight gaps and no intraday range, YZ reduces to k * open-to-close variance
    idx = pd.bdate_range("2024-01-01", periods=30)
    c = 100 * np.exp(np.cumsum(np.tile([0.01, -0.01], 15)))
    o = np.r_[100.0, c[:-1]]
    df = pd.DataFrame({"open": o, "close": c, "high": np.maximum(o, c), "low": np.minimum(o, c)}, index=idx)
    n = 10
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    t = rv.daily_terms(df)
    oc_var = t["open_close"].iloc[-n:].var(ddof=1)
    rs_mean = t["rs"].iloc[-n:].mean()
    expected = math.sqrt(252 * (0 + k * oc_var + (1 - k) * rs_mean))
    assert float(rv.rolling_vol(df, n, "yang_zhang").iloc[-1]) == pytest.approx(expected, rel=1e-12)
    with pytest.raises(ValueError):
        rv.rolling_vol(df, 5, "nope")


def test_vol_cone(spy):
    cone = rv.vol_cone(spy)
    assert list(cone.index) == list(rv.CONE_HORIZONS)
    cols = ["min", "p10", "p25", "p50", "p75", "p90", "max"]
    assert (cone[cols].diff(axis=1).iloc[:, 1:] >= 0).all().all()
    assert ((cone["current"] >= cone["min"]) & (cone["current"] <= cone["max"])).all()
    # cone narrows with horizon (Burghardt & Lane): short-horizon extremes are wider
    assert cone.loc[10, "max"] > cone.loc[252, "max"] and cone.loc[10, "min"] < cone.loc[252, "min"]


def test_vrp_history_with_real_vix(market, spy):
    vix = market.fred(["VIXCLS"]).data["VIXCLS"].dropna() / 100.0
    h = rv.vrp_history(vix, spy, 21)
    assert len(h) > 2000
    # the variance risk premium is positive on average (Carr & Wu 2009): VIX > subsequent realized vol
    assert h["premium_forward"].mean() > 0 and (h["premium_forward"].dropna() > 0).mean() > 0.6
    # forward RV at t equals trailing RV 21 sessions later
    s = rv.rolling_vol(spy, 21, "close_to_close")
    t0 = h.index[500]
    j = s.index.get_loc(t0)
    assert h.loc[t0, "forward_rv"] == pytest.approx(s.iloc[j + 21])
    v = rv.vrp(0.2, 0.15)
    assert v["vol_premium"] == pytest.approx(0.05) and v["variance_premium"] == pytest.approx(0.04 - 0.0225)


def test_adjusted_ohlc_removes_split_jump():
    idx = pd.bdate_range("2024-01-01", periods=4)
    df = pd.DataFrame({"open": [200, 202, 101, 102], "high": [204, 206, 103, 104], "low": [198, 200, 100, 101],
                       "close": [202, 204, 102, 103], "adj_close": [101, 102, 102, 103]}, index=idx, dtype=float)
    a = rv.adjusted_ohlc(df)
    r = np.log(a["close"]).diff().dropna()
    assert (r.abs() < 0.02).all()
    assert a["high"].iloc[0] == pytest.approx(102.0)
