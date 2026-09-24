"""DCF identities, sensitivity monotonicity, reverse DCF and beta on real fixture returns."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_fundamentals_analytics import make_annual, make_quarterly

from ohcamel_quant.fundamentals import dcf as d


def test_pv_identity_constant_growth_equals_gordon():
    base, w, g = 100.0, 0.09, 0.03
    for n in (1, 5, 10):
        v = d.dcf_value(base, np.full(n, g), w, g)
        assert v["enterprise_value"] == pytest.approx(d.gordon_value(base, w, g), rel=1e-12)
        assert v["enterprise_value"] == pytest.approx(base * 1.03 / 0.06, rel=1e-12)


def test_dcf_table_and_equity_bridge():
    v = d.dcf_value(100.0, [0.10, 0.05], 0.10, 0.02, net_debt=50.0, shares=10.0, price=100.0)
    f1, f2 = 110.0, 115.5
    pv = f1 / 1.1 + f2 / 1.21
    tv = f2 * 1.02 / 0.08
    ev = pv + tv / 1.21
    assert v["table"][1]["fcff"] == pytest.approx(f2)
    assert v["pv_explicit"] == pytest.approx(pv)
    assert v["terminal_value"] == pytest.approx(tv)
    assert v["enterprise_value"] == pytest.approx(ev)
    assert v["equity_value"] == pytest.approx(ev - 50)
    assert v["value_per_share"] == pytest.approx((ev - 50) / 10)
    assert v["upside"] == pytest.approx((ev - 50) / 10 / 100 - 1)
    assert v["terminal_share"] == pytest.approx(tv / 1.21 / ev)


def test_fade_path():
    p = d.fade_path(0.20, 0.02, 5)
    assert p[0] == pytest.approx(0.20)
    np.testing.assert_allclose(np.diff(p), -0.18 / 5)
    assert p[-1] + (p[1] - p[0]) == pytest.approx(0.02)   # year N+1 grows at terminal g
    np.testing.assert_allclose(d.fade_path(0.03, 0.03, 4), 0.03)


def test_refuses_meaningless_inputs():
    with pytest.raises(ValueError, match="WACC"):
        d.dcf_value(100.0, [0.05], 0.03, 0.03)
    with pytest.raises(ValueError, match="<= 0"):
        d.dcf_value(-10.0, [0.05], 0.08, 0.02)
    with pytest.raises(ValueError):
        d.dcf_value(float("nan"), [0.05], 0.08, 0.02)


def test_sensitivity_monotone():
    s = d.sensitivity(100.0, 0.10, 5, 0.09, 0.025, net_debt=100.0, shares=10.0)
    grid = np.array([[np.nan if v is None else v for v in row] for row in s["value_per_share"]])
    assert grid.shape == (5, 5)
    assert s["wacc"][2] == pytest.approx(0.09) and s["terminal_growth"][2] == pytest.approx(0.025)
    assert np.all(np.diff(grid, axis=0) < 0)      # value falls as WACC rises
    assert np.all(np.diff(grid, axis=1) > 0)      # value rises with g
    centre = d.dcf_value(100.0, d.fade_path(0.10, 0.025, 5), 0.09, 0.025, 100.0, 10.0)
    assert grid[2, 2] == pytest.approx(centre["value_per_share"])


def test_sensitivity_blanks_invalid_cells():
    s = d.sensitivity(100.0, 0.05, 5, 0.03, 0.025, 0.0, 1.0, wacc_step=0.005, g_step=0.005)
    assert s["value_per_share"][0][2] is None   # WACC 2% < g 2.5%


def test_reverse_dcf_round_trip():
    base, n, w, gt, nd = 250.0, 5, 0.085, 0.022, 400.0
    for g in (-0.05, 0.0, 0.07, 0.30):
        cap = d.dcf_value(base, np.full(n, g), w, gt, nd)["equity_value"]
        r = d.reverse_dcf(base, n, w, gt, nd, cap)
        assert r["implied_growth"] == pytest.approx(g, abs=1e-9)


def test_reverse_dcf_out_of_range():
    r = d.reverse_dcf(1.0, 5, 0.08, 0.02, 0.0, 1e12)
    assert r["implied_growth"] is None and "exceeds" in r["reason"]


def test_wacc_and_cost_of_debt():
    assert d.wacc(3000, 1000, 0.10, 0.05, 0.25) == pytest.approx(0.75 * 0.10 + 0.25 * 0.05 * 0.75)
    kd, basis = d.cost_of_debt(12, 450, 450, 0.04)
    assert kd == 0.04 and "floored" in basis
    kd, basis = d.cost_of_debt(45, 500, 400, 0.04)
    assert kd == pytest.approx(0.10) and basis.startswith("interest")
    assert d.cost_of_debt(None, 500, 400, 0.04)[0] == 0.04
    with pytest.raises(ValueError):
        d.wacc(0, 1, 0.1, 0.05, 0.2)


def test_inputs_from_statements():
    a = make_annual()
    assert d.effective_tax_3y(a) == pytest.approx((0.2 + 0.2 + 0.25) / 3)
    g, n = d.revenue_cagr(a, 5)
    assert n == 2 and g == pytest.approx(1.5 ** 0.5 - 1)
    b = d.base_fcff_from_statements(a, None, 0.25, "auto")
    assert b["basis"] == "fy" and b["fcff"] == pytest.approx(280 + 12 * 0.75 - 80)
    q = make_quarterly(8)
    ttm = q.iloc[-4:].sum()
    b2 = d.base_fcff_from_statements(a, ttm, 0.25, "auto")
    assert b2["basis"] == "ttm"
    assert b2["fcff"] == pytest.approx(ttm["cfo"] + ttm["interest_expense"] * 0.75 - ttm["capex"])
    no_int = a.copy()
    no_int["interest_expense"] = np.nan
    b3 = d.base_fcff_from_statements(no_int, None, 0.25, "fy")
    assert b3["fcff"] == pytest.approx(200) and "omitted" in b3["notes"][0]
    assert d.debt_open_close(a) == (450.0, 450.0)


# -------------------------------------------------------------- beta (real)
def test_beta_qqq_vs_spy_real_fixture(market):
    qqq = d.monthly_returns(market.ohlcv("QQQ").data["adj_close"])
    spy = d.monthly_returns(market.ohlcv("SPY").data["adj_close"])
    b = d.estimate_beta(qqq, spy, window=60)
    assert b["n_months"] == 60
    assert 1.0 < b["beta"] < 1.5
    assert b["blume_adjusted"] == pytest.approx(0.67 * b["beta"] + 0.33)
    assert b["r2"] > 0.7
    self_b = d.estimate_beta(spy, spy)
    assert self_b["beta"] == pytest.approx(1.0) and self_b["r2"] == pytest.approx(1.0)
    tlt = d.monthly_returns(market.ohlcv("TLT").data["adj_close"])
    assert d.estimate_beta(tlt, spy)["beta"] < 0.6


def test_beta_matches_numpy_polyfit(market):
    xle = d.monthly_returns(market.ohlcv("XLE").data["adj_close"])
    spy = d.monthly_returns(market.ohlcv("SPY").data["adj_close"])
    b = d.estimate_beta(xle, spy, window=60)
    df = pd.concat([xle, spy], axis=1, join="inner").dropna().iloc[-60:]
    slope = np.polyfit(df.iloc[:, 1], df.iloc[:, 0], 1)[0]
    assert b["beta"] == pytest.approx(slope)


def test_monthly_returns_and_erp():
    idx = pd.bdate_range("2020-01-01", "2020-03-31")
    p = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    m = d.monthly_returns(p)
    assert len(m) == 2    # Jan->Feb, Feb->Mar (March complete)
    m2 = d.monthly_returns(p.iloc[:-3])
    assert len(m2) == 1   # March incomplete -> dropped
    x = pd.Series([0.01, -0.005] * 60, index=pd.date_range("2000-01-31", periods=120, freq="ME"))
    e = d.equity_risk_premium(x)
    assert e["erp"] == pytest.approx(12 * 0.0025) and e["n_months"] == 120
    with pytest.raises(ValueError):
        d.equity_risk_premium(x.iloc[:50])


def test_beta_zero_market_variance_is_a_clear_error():
    ix = pd.date_range("2020-01-31", periods=30, freq="ME")
    with pytest.raises(ValueError, match="zero variance"):
        d.estimate_beta(pd.Series(np.arange(30.0) / 100, index=ix), pd.Series(0.01, index=ix))
