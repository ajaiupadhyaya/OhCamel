"""Statements, ratios and scores on a hand-built statements frame.

The frame follows the documented ``MarketData.company_facts`` contract
(standardized line items, fiscal-period-end index) with round numbers chosen so
every expected value below can be computed by hand. It is a numerical test
fixture of the contract, not market data.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.fundamentals import ratios as rt
from ohcamel_quant.fundamentals import scores as sc
from ohcamel_quant.fundamentals import statements as st

YEARS = ["2022-12-31", "2023-12-31", "2024-12-31"]
VALUES: dict[str, tuple[float, float, float]] = {
    "revenue": (800, 1000, 1200),
    "cost_of_revenue": (480, 600, 660),
    "gross_profit": (320, 400, 540),
    "sga": (100, 120, 132),
    "rnd": (40, 50, 60),
    "operating_income": (150, 200, 300),
    "d_and_a": (40, 50, 60),
    "ebitda": (190, 250, 360),
    "interest_expense": (10, 10, 12),
    "pretax_income": (140, 190, 288),
    "income_tax": (28, 38, 72),
    "net_income": (112, 152, 216),
    "eps_diluted": (1.12, 1.52, 2.16),
    "shares_diluted": (100, 100, 100),
    "total_assets": (1600, 2000, 2400),
    "current_assets": (600, 700, 900),
    "cash": (200, 250, 300),
    "receivables": (100, 150, 150),
    "inventory": (80, 100, 120),
    "ppe_net": (500, 600, 700),
    "total_liabilities": (800, 1000, 1100),
    "current_liabilities": (300, 400, 450),
    "long_term_debt": (400, 400, 400),
    "short_term_debt": (50, 50, 50),
    "total_debt": (450, 450, 450),
    "equity": (800, 1000, 1300),
    "retained_earnings": (400, 500, 650),
    "cfo": (150, 200, 280),
    "capex": (60, 70, 80),
    "fcf": (90, 130, 200),
    "dividends_paid": (20, 30, 40),
    "buybacks": (10, 20, 30),
}


def make_annual(**overrides: tuple[float, float, float]) -> pd.DataFrame:
    vals = {**VALUES, **overrides}
    df = pd.DataFrame(vals, index=pd.DatetimeIndex(pd.to_datetime(YEARS), name="period_end")).astype(float)
    return df


def make_quarterly(n: int = 8) -> pd.DataFrame:
    """n consecutive calendar quarters; flows = annual/4 of the latest year
    scaled by quarter number, stocks = latest-year balances + quarter number."""
    ends = pd.date_range("2023-03-31", periods=n, freq="QE")
    rows = []
    for k in range(n):
        r = {c: VALUES[c][2] / 4 + k for c in st.FLOW_ITEMS if c in VALUES}
        r.update({c: VALUES[c][2] + k for c in st.STOCK_ITEMS if c in VALUES})
        r["eps_diluted"] = 0.5 + 0.01 * k
        r["shares_diluted"] = 100 + k
        rows.append(r)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(ends, name="period_end")).astype(float)


# ---------------------------------------------------------------- statements
def test_yoy_growth_and_nonpositive_base():
    a = make_annual(net_income=(-10, 152, 216))
    g = st.yoy_growth(st.ensure_columns(a))
    assert g.loc["2024-12-31", "revenue"] == pytest.approx(0.2)
    assert g.loc["2023-12-31", "revenue"] == pytest.approx(0.25)
    assert math.isnan(g.loc["2022-12-31", "revenue"])            # no prior year
    assert math.isnan(g.loc["2023-12-31", "net_income"])         # negative base


def test_ttm_sums_flows_takes_latest_stocks():
    q = make_quarterly(6)
    end, row = st.latest_ttm(q)
    last4 = q.iloc[-4:]
    assert end == q.index[-1]
    assert row["revenue"] == pytest.approx(last4["revenue"].sum())
    assert row["cfo"] == pytest.approx(last4["cfo"].sum())
    assert row["eps_diluted"] == pytest.approx(last4["eps_diluted"].sum())
    assert row["shares_diluted"] == pytest.approx(last4["shares_diluted"].mean())
    assert row["total_assets"] == q["total_assets"].iloc[-1]
    assert len(st.rolling_ttm(q)) == 3


def test_ttm_missing_quarter_is_none_never_imputed():
    q = make_quarterly(4)
    q.loc[q.index[1], "capex"] = np.nan
    end, row = st.latest_ttm(q)
    assert math.isnan(row["capex"]) and math.isnan(row["fcf"]) is False
    # a gap in the quarter sequence voids the window
    q2 = make_quarterly(5).drop(index=pd.Timestamp("2023-06-30"))
    assert st.ttm_at(q2, q2.index[3]) is None


def test_build_statements_shapes_and_none():
    a = make_annual()
    a["rnd"] = np.nan
    out = st.build_statements(a, make_quarterly(8), "annual")
    inc = out["income_statement"]
    assert inc["periods"] == ["2022-12-31", "2023-12-31", "2024-12-31"]
    rnd = next(r for r in inc["rows"] if r["item"] == "rnd")
    assert rnd["values"] == [None, None, None] and rnd["available"] is False
    rev = next(r for r in inc["rows"] if r["item"] == "revenue")
    assert rev["yoy"][-1] == pytest.approx(0.2)
    ttm = st.build_statements(a, make_quarterly(8), "ttm")
    assert len(ttm["balance_sheet"]["periods"]) == 5
    with pytest.raises(ValueError):
        st.build_statements(a, make_quarterly(8), "weekly")


# -------------------------------------------------------------------- ratios
def test_row_ratios_by_hand():
    h = rt.ratio_history(make_annual())
    r = h.loc["2024-12-31"]
    assert r["gross_margin"] == pytest.approx(0.45)
    assert r["operating_margin"] == pytest.approx(0.25)
    assert r["net_margin"] == pytest.approx(0.18)
    assert r["fcf_margin"] == pytest.approx(200 / 1200)
    assert r["roe"] == pytest.approx(216 / 1150)
    assert r["roa"] == pytest.approx(216 / 2200)
    assert r["effective_tax_rate"] == pytest.approx(0.25)
    assert r["nopat"] == pytest.approx(225)
    assert r["roic"] == pytest.approx(225 / ((1450 + 1200) / 2))
    assert r["debt_to_equity"] == pytest.approx(450 / 1300)
    assert r["net_debt_to_ebitda"] == pytest.approx(150 / 360)
    assert r["interest_coverage"] == pytest.approx(25)
    assert r["current_ratio"] == pytest.approx(2.0)
    assert r["quick_ratio"] == pytest.approx(780 / 450)
    assert r["cash_ratio"] == pytest.approx(300 / 450)
    assert r["asset_turnover"] == pytest.approx(1200 / 2200)
    assert r["dso_days"] == pytest.approx(150 / 1200 * 365)
    assert r["dio_days"] == pytest.approx(110 / 660 * 365)
    assert r["revenue_growth"] == pytest.approx(0.2)
    assert r["eps_growth"] == pytest.approx(2.16 / 1.52 - 1)
    assert r["book_value_per_share"] == pytest.approx(13.0)
    assert r["payout_ratio"] == pytest.approx(40 / 216)
    assert r["total_payout_ratio"] == pytest.approx(70 / 216)


def test_dupont_identity():
    h = rt.ratio_history(make_annual()).dropna(subset=["roe"])
    prod = h["dupont_net_margin"] * h["dupont_asset_turnover"] * h["dupont_equity_multiplier"]
    np.testing.assert_allclose(prod.to_numpy(), h["roe"].to_numpy(), rtol=1e-12)


def test_first_period_average_ratios_are_none():
    r = rt.ratio_history(make_annual()).loc["2022-12-31"]
    assert math.isnan(r["roe"]) and math.isnan(r["roic"]) and math.isnan(r["dso_days"])
    assert r["gross_margin"] == pytest.approx(0.4)


def test_negative_equity_and_loss_give_none():
    a = make_annual(equity=(800, 1000, -50), pretax_income=(140, 190, -10))
    r = rt.ratio_history(a).loc["2024-12-31"]
    assert math.isnan(r["debt_to_equity"])
    assert math.isnan(r["effective_tax_rate"]) and math.isnan(r["roic"])


def test_cagr_and_growth_table():
    assert rt.cagr(800, 1200, 2) == pytest.approx(1.5 ** 0.5 - 1)
    assert rt.cagr(-1, 1200, 2) is None and rt.cagr(100, 0, 1) is None
    g = rt.growth_table(make_annual(), horizons=(1, 2, 3))
    assert g["revenue"]["1y"] == pytest.approx(0.2)
    assert g["revenue"]["2y"] == pytest.approx(1.5 ** 0.5 - 1)
    assert g["revenue"]["3y"] is None


def test_market_multiples():
    row = make_annual().iloc[-1]
    m = rt.market_multiples(30.0, 100.0, row)
    assert m["market_cap"] == 3000
    assert m["enterprise_value"] == 3000 + 450 - 300
    assert m["pe"] == pytest.approx(3000 / 216)
    assert m["pe_eps"] == pytest.approx(30 / 2.16)
    assert m["ev_ebitda"] == pytest.approx(3150 / 360)
    assert m["ev_sales"] == pytest.approx(3150 / 1200)
    assert m["price_to_book"] == pytest.approx(3000 / 1300)
    assert m["fcf_yield"] == pytest.approx(200 / 3000)
    assert m["earnings_yield"] == pytest.approx(216 / 3000)
    assert m["shareholder_yield"] == pytest.approx(70 / 3000)
    loss = row.copy()
    loss["net_income"] = -5.0
    assert rt.market_multiples(30.0, 100.0, loss)["pe"] is None
    assert rt.market_multiples(30.0, 100.0, loss)["earnings_yield"] == pytest.approx(-5 / 3000)
    with pytest.raises(ValueError):
        rt.market_multiples(0.0, 100.0, row)


# -------------------------------------------------------------------- scores
def _signals(res: dict) -> dict[str, int | None]:
    return {s["name"]: s["value"] for s in res["signals"]}


def test_piotroski_by_hand():
    res = sc.piotroski(make_annual())
    s = _signals(res)
    assert s == {"F_ROA": 1, "F_CFO": 1, "F_dROA": 1, "F_ACCRUAL": 1, "F_dLEVER": 1, "F_dLIQUID": 1,
                 "F_EQ_OFFER": 1, "F_dMARGIN": 1, "F_dTURN": 0}
    assert res["score"] == 8 and res["missing"] == []


@pytest.mark.parametrize(("override", "signal"), [
    ({"net_income": (112, 152, -1)}, "F_ROA"),
    ({"cfo": (150, 200, -5)}, "F_CFO"),
    ({"net_income": (112, 200, 190)}, "F_dROA"),
    ({"cfo": (150, 200, 200)}, "F_ACCRUAL"),               # 200/2000 = .10 < ROA .108
    ({"long_term_debt": (400, 400, 600)}, "F_dLEVER"),
    ({"current_assets": (600, 700, 700)}, "F_dLIQUID"),    # 700/450 < 1.75
    ({"shares_diluted": (100, 100, 110)}, "F_EQ_OFFER"),
    ({"gross_profit": (320, 400, 450)}, "F_dMARGIN"),      # .375 < .40
])
def test_piotroski_each_signal_flips(override, signal):
    base = _signals(sc.piotroski(make_annual()))
    flipped = _signals(sc.piotroski(make_annual(**override)))
    assert base[signal] == 1 and flipped[signal] == 0


def test_piotroski_dturn_flips_on():
    s = _signals(sc.piotroski(make_annual(revenue=(800, 1000, 1400))))   # 1400/2000 = .7 > .625
    assert s["F_dTURN"] == 1


def test_piotroski_missing_inputs_listed():
    a = make_annual()
    a["long_term_debt"] = np.nan
    res = sc.piotroski(a)
    assert res["score"] is None and res["n_available"] == 8
    assert any(m.startswith("F_dLEVER") for m in res["missing"])
    assert sc.piotroski(make_annual().iloc[-2:])["score"] is None


def test_altman_z_formula():
    res = sc.altman_z(make_annual(), 3000.0)
    x = {"X1": 450 / 2400, "X2": 650 / 2400, "X3": 300 / 2400, "X4": 3000 / 1100, "X5": 0.5}
    z = 1.2 * x["X1"] + 1.4 * x["X2"] + 3.3 * x["X3"] + 0.6 * x["X4"] + 1.0 * x["X5"]
    assert res["z"] == pytest.approx(z)
    assert res["zone"] == ("safe" if z > 2.99 else "grey" if z >= 1.81 else "distress")
    assert sum(c["contribution"] for c in res["components"]) == pytest.approx(z)
    assert sc.altman_z(make_annual(), None)["z"] is None
    assert sc.altman_z(make_annual(), None)["missing"] == ["X4"]


def test_altman_z2_formula_and_zones():
    res = sc.altman_z2(make_annual())
    z = 6.56 * 450 / 2400 + 3.26 * 650 / 2400 + 6.72 * 300 / 2400 + 1.05 * 1300 / 1100
    assert res["z"] == pytest.approx(z) and res["zone"] == "safe"
    bad = sc.altman_z2(make_annual(retained_earnings=(0, 0, -2000), operating_income=(1, 1, -200)))
    assert bad["zone"] == "distress"


def test_beneish_indices_by_hand():
    a = make_annual()
    idx = sc.beneish_indices(a.iloc[-1], a.iloc[-2])
    expect = {
        "DSRI": (150 / 1200) / (150 / 1000),
        "GMI": 0.40 / 0.45,
        "AQI": (1 - 1600 / 2400) / (1 - 1300 / 2000),
        "SGI": 1.2,
        "DEPI": (50 / 650) / (60 / 760),
        "SGAI": (132 / 1200) / (120 / 1000),
        "LVGI": (850 / 2400) / (800 / 2000),
        "TATA": (216 - 280) / 2400,
    }
    for k, v in expect.items():
        assert idx[k] == pytest.approx(v), k
    res = sc.beneish(a)
    m = -4.84 + 0.920 * expect["DSRI"] + 0.528 * expect["GMI"] + 0.404 * expect["AQI"] + 0.892 * expect["SGI"] \
        + 0.115 * expect["DEPI"] - 0.172 * expect["SGAI"] + 4.679 * expect["TATA"] - 0.327 * expect["LVGI"]
    assert res["m"] == pytest.approx(m)
    assert res["flag"] is (m > -1.78)
    assert 0 < res["probability"] < 1


def test_beneish_missing_sga_is_none():
    a = make_annual()
    a["sga"] = np.nan
    res = sc.beneish(a)
    assert res["m"] is None and res["missing"] == ["SGAI"]


def test_sloan_accruals():
    res = sc.sloan_accruals(make_annual())
    assert res["ratio"] == pytest.approx((216 - 280) / 2200)
    assert res["interpretation"] == "moderate"


def test_ohlson_o_score():
    a = make_annual()
    res = sc.ohlson_o(a, 800.0)
    x = {
        "SIZE": math.log(2400 / 800.0), "TLTA": 1100 / 2400, "WCTA": 450 / 2400, "CLCA": 450 / 900,
        "OENEG": 0.0, "NITA": 216 / 2400, "FUTL": 280 / 1100, "INTWO": 0.0, "CHIN": (216 - 152) / (216 + 152),
    }
    o = -1.32 + sum(sc.OHLSON_COEF[k] * v for k, v in x.items())
    assert res["o"] == pytest.approx(o)
    assert res["probability"] == pytest.approx(1 / (1 + math.exp(-o)))
    none = sc.ohlson_o(a, None)
    assert none["o"] is None and none["missing"] == ["SIZE (GDP deflator unavailable)"]


def test_ohlson_size_in_dollars_gives_low_probability_for_healthy_firm():
    """Ohlson (1980) p. 118: SIZE = log(total assets in dollars / price-level
    index, 1968 = 100). A healthy USD 50m firm (TL/TA 0.5, ROA 5%, index 120)
    must score O ~ -4.45 (P ~ 1%); the millions scaling gave P ~ 33%."""
    a = make_annual(total_assets=(5e7,) * 3, total_liabilities=(2.5e7,) * 3, current_assets=(2.5e7,) * 3,
                    current_liabilities=(1e7,) * 3, net_income=(2e6, 2.5e6, 2.5e6), cfo=(5e6,) * 3)
    res = sc.ohlson_o(a, 120.0)
    size = next(c["value"] for c in res["components"] if c["name"] == "SIZE")
    assert size == pytest.approx(math.log(5e7 / 120.0))
    assert res["o"] == pytest.approx(-4.455, abs=0.01)
    assert res["probability"] < 0.02


def test_price_level_index_uses_prior_year_rebased_to_1968():
    idx = pd.date_range("1967-01-01", "2024-10-01", freq="QS")
    defl = pd.Series(np.linspace(10.0, 130.0, len(idx)), index=idx)
    v = sc.price_level_index_1968(defl, pd.Timestamp("2024-09-28"))
    expect = defl[idx.year == 2023].mean() / defl[idx.year == 1968].mean() * 100
    assert v == pytest.approx(expect)
    assert sc.price_level_index_1968(defl[idx.year >= 1970], pd.Timestamp("2024-09-28")) is None
