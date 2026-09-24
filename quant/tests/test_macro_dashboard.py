"""Dashboard transforms, summaries and the Taylor rule on toy frames."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.macro import dashboard as dash


def _monthly(vals, start="2020-01-01"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="MS"), dtype=float)


def test_series_config_is_valid():
    cfg = dash.load_series_config()
    ids = [s["id"] for s in cfg["series"]]
    assert len(ids) == len(set(ids)) >= 26
    for must in ("CPIAUCSL", "PCEPILFE", "PAYEMS", "GDPC1", "BAMLH0A0HYM2", "WALCL", "DFII10"):
        assert must in ids
    for s in cfg["series"]:
        assert s["transform"] in dash.TRANSFORMS and s["name"] and s["units"]
    assert next(s for s in cfg["series"] if s["id"] == "PAYEMS")["transform"] == "diff"


def test_transforms_monthly():
    s = _monthly(100 * 1.01 ** np.arange(25))
    yoy = dash.apply_transform(s, "yoy_pct")
    assert yoy.index[0] == pd.Timestamp("2021-01-01")
    assert np.allclose(yoy, 100 * (1.01**12 - 1))
    mom = dash.apply_transform(s, "mom_ann")
    assert np.allclose(mom, 100 * (1.01**12 - 1))
    d = dash.apply_transform(_monthly([1, 4, 9]), "diff")
    assert list(d) == [3, 5]
    assert dash.apply_transform(_monthly([1, np.nan, 3]), "level").tolist() == [1, 3]
    with pytest.raises(ValueError):
        dash.apply_transform(s, "bogus")  # type: ignore[arg-type]


def test_yoy_quarterly_weekly_daily():
    q = pd.Series([100, 101, 102, 103, 104, 105.0], index=pd.date_range("2020-01-01", periods=6, freq="QS"))
    assert dash.apply_transform(q, "yoy_pct").iloc[0] == pytest.approx(4.0)
    assert dash.infer_periods_per_year(q) == 4
    w = pd.Series(np.arange(60, dtype=float) + 100, index=pd.date_range("2020-01-04", periods=60, freq="W-SAT"))
    yw = dash.apply_transform(w, "yoy_pct")
    # 2021-01-02 has no observation on/before 2020-01-02 -> first yoy is 2021-01-09 vs 2020-01-04 (5 days, in tolerance)
    assert yw.index[0] == pd.Timestamp("2021-01-09")
    assert yw.iloc[0] == pytest.approx(100 * (153 / 100 - 1))
    assert dash.infer_periods_per_year(w) == 52
    d = pd.Series(1.0, index=pd.bdate_range("2020-01-01", "2021-03-01"))
    assert dash.infer_periods_per_year(d) == 252
    assert np.allclose(dash.apply_transform(d, "yoy_pct"), 0.0)


def test_value_one_period_ago_tolerance():
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2020-01-01", "2021-03-01"]))
    prior = dash.value_one_period_ago(s, pd.DateOffset(years=1))
    assert np.isnan(prior.iloc[1])  # 2020-03-01 target is 60 days after the last obs


def test_summarize():
    s = _monthly(np.arange(1, 150, dtype=float), "2014-01-01")
    out = dash.summarize(s)
    assert out["latest"] == 149.0
    assert out["change"] == {"1M": 1.0, "3M": 3.0, "1Y": 12.0}
    assert out["percentile_10y"] == 1.0
    pw = out["percentile_window"]
    assert pw["complete"] is True and pw["years_covered"] == pytest.approx(10, abs=0.1) and pw["n"] == 120
    assert out["sparkline"]["values"][-1] == 149.0
    assert len(out["sparkline"]["values"]) <= 80
    short = dash.summarize(_monthly([1.0, 2.0]))
    assert short["change"]["1Y"] is None


def test_taylor_rule_toy():
    idx = pd.date_range("2018-01-01", periods=36, freq="MS")
    pce = pd.Series(100 * 1.03 ** (np.arange(36) / 12), index=idx)  # 3% y/y
    q = pd.date_range("2018-01-01", periods=12, freq="QS")
    gdp = pd.Series(101.0, index=q)
    pot = pd.Series(100.0, index=q)                                  # gap = +1%
    dff = pd.Series(4.0, index=pd.bdate_range("2018-01-01", "2020-12-31"))
    df, notes = dash.taylor_rule(pce, gdp, pot, r_star=2.0, pi_star=2.0, fed_funds=dff)
    assert df.index[0] == pd.Timestamp("2019-01-01")
    # 2 + 3 + 0.5*(3-2) + 0.5*1 = 6.0 ; balanced: 6.5
    assert np.allclose(df["taylor_1993"], 6.0)
    assert np.allclose(df["balanced_approach"], 6.5)
    assert np.allclose(df["gap_taylor_minus_ff"], 2.0)
    assert dash.TAYLOR_1993 == {"r_star": 2.0, "pi_star": 2.0}
    assert dash.output_gap(gdp, pot).iloc[0] == pytest.approx(1.0)


def test_summarize_mixed_frequency_short_horizons_are_null():
    """Regression: for quarterly GDP the "1M" change was silently the q/q change."""
    q = pd.Series(np.arange(20, dtype=float), index=pd.date_range("2020-01-01", periods=20, freq="QS"))
    out = dash.summarize(q)
    assert out["change"]["1M"] is None
    assert out["change"]["3M"] == pytest.approx(1.0) and out["change"]["1Y"] == pytest.approx(4.0)
    m = dash.summarize(_monthly(np.arange(30)))
    assert m["change"]["1M"] == pytest.approx(1.0)
