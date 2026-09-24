"""Historical scenario replay on real prices, and Kupiec (1998) conditional stress."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.risk import stress as st


@pytest.fixture(scope="module")
def closes(market):
    return {t: market.ohlcv(t).data["adj_close"] for t in ("SPY", "QQQ", "TLT", "GLD", "XLE", "XLF")}


def test_scenario_catalogue_is_well_formed():
    sc = st.load_scenarios()
    ids = [s["id"] for s in sc]
    assert len(ids) == len(set(ids)) == 17
    for s in sc:
        assert s["start"] <= s["end"]
        assert s["name"] and s["description"]
    assert st.get_scenario("covid_2020")["start"] == pd.Timestamp("2020-02-19")
    with pytest.raises(ValueError):
        st.get_scenario("nope")


def test_scenario_windows_match_real_spy_sessions(closes):
    """Every window inside the fixture's coverage starts and ends on a real SPY
    session; peak-to-trough windows start at the window's highest close and end
    at its lowest; SPY fell over every window."""
    spy = closes["SPY"]
    covered = [s for s in st.load_scenarios() if s["start"] >= spy.index[0]]
    assert len(covered) >= 7
    for s in covered:
        w = spy.loc[s["start"]:s["end"]]
        assert w.index[0] == s["start"] and w.index[-1] == s["end"], s["id"]
        assert w.idxmax() == s["start"], s["id"]
        if s["id"] != "svb_2023":  # an event window (bank failures), not peak-to-trough
            assert w.idxmin() == s["end"], s["id"]
        assert w.iloc[-1] < w.iloc[0], s["id"]


def test_covid_replay_matches_actual_prices(closes):
    s = st.get_scenario("covid_2020")
    w = {"SPY": 0.6, "TLT": 0.4}
    res = st.replay(s, w, closes, "SPY", closes["SPY"], notional=1e6)
    spy = closes["SPY"]
    spy_ret = spy.loc["2020-03-23"] / spy.loc["2020-02-19"] - 1
    tlt_ret = closes["TLT"].loc["2020-03-23"] / closes["TLT"].loc["2020-02-19"] - 1
    assert res["complete"] and not res["proxied"]
    assert res["benchmark_return"] == pytest.approx(spy_ret)
    assert -0.36 < spy_ret < -0.32   # S&P 500 fell ~34% peak-to-trough
    pos = {p["ticker"]: p for p in res["positions"]}
    assert pos["SPY"]["return"] == pytest.approx(spy_ret)
    assert pos["TLT"]["return"] == pytest.approx(tlt_ret)
    assert res["portfolio_return"] == pytest.approx(0.6 * spy_ret + 0.4 * tlt_ret)
    assert res["pnl_usd"] == pytest.approx(1e6 * (0.6 * spy_ret + 0.4 * tlt_ret))
    assert res["base_date"] == pd.Timestamp("2020-02-19") and res["end_date"] == pd.Timestamp("2020-03-23")
    # path ends at the total; drawdown at least as deep as the end-point loss
    assert res["path"]["portfolio"][-1] == pytest.approx(res["portfolio_return"])
    assert res["path"]["portfolio"][0] == pytest.approx(0.0)
    assert res["max_drawdown"] <= min(res["portfolio_return"], 0) + 1e-12
    assert res["worst_day"]["return"] < -0.05


def test_prior_close_anchor_includes_start_day(closes):
    s = {"id": "x", "name": "x", "start": pd.Timestamp("2020-03-16"), "end": pd.Timestamp("2020-03-16"),
         "anchor": "prior_close"}
    res = st.replay(s, {"SPY": 1.0}, closes, "SPY", closes["SPY"])
    spy = closes["SPY"]
    assert res["portfolio_return"] == pytest.approx(spy.loc["2020-03-16"] / spy.loc["2020-03-13"] - 1)
    assert res["portfolio_return"] < -0.10   # 16 Mar 2020, one of the worst days on record


def test_missing_holding_is_proxied_by_beta_and_flagged(closes):
    # QQQ's real history truncated to begin after the window: it must be proxied
    s = st.get_scenario("covid_2020")
    qqq = closes["QQQ"].loc["2021-01-01":]
    prices = {"SPY": closes["SPY"], "QQQ": qqq}
    res = st.replay(s, {"SPY": 0.5, "QQQ": 0.5}, prices, "SPY", closes["SPY"])
    assert res["complete"] and res["proxied"] == ["QQQ"]
    pos = {p["ticker"]: p for p in res["positions"]}
    assert pos["QQQ"]["source"] == "proxy"
    pb = st.proxy_beta(qqq, closes["SPY"], s["start"], s["end"])
    assert pos["QQQ"]["beta"] == pytest.approx(pb["beta"])
    assert pos["QQQ"]["return"] == pytest.approx(pb["beta"] * res["benchmark_return"])
    assert pb["obs"] == 756 and pb["from"] >= pd.Timestamp("2021-01-01")
    assert any("PROXIED" in n for n in res["notes"])


def test_no_data_means_incomplete_never_invented(closes):
    res = st.replay(st.get_scenario("gfc_2007"), {"SPY": 1.0}, closes, "SPY", closes["SPY"])
    assert res["complete"] is False and res["portfolio_return"] is None and res["pnl_usd"] is None
    # a holding with real window data but no benchmark and no proxy -> still incomplete
    s = st.get_scenario("covid_2020")
    res2 = st.replay(s, {"SPY": 0.5, "ZZZ": 0.5}, {"SPY": closes["SPY"], "ZZZ": None}, "SPY", closes["SPY"])
    assert res2["complete"] is False and res2["missing"] == ["ZZZ"] and res2["portfolio_return"] is None


def test_conditional_stress_formula(etf_returns):
    sub = etf_returns[["SPY", "TLT", "GLD", "XLE"]]
    w = {"TLT": 0.5, "GLD": 0.3, "XLE": 0.2}
    res = st.conditional_stress(sub, w, {"SPY": -0.10}, method="sample")
    cov = np.cov(sub.to_numpy(), rowvar=False)
    beta = cov[1:, 0] / cov[0, 0]
    moves = {p["ticker"]: p["move"] for p in res["positions"]}
    for i, t in enumerate(["TLT", "GLD", "XLE"]):
        assert moves[t] == pytest.approx(beta[i] * -0.10, rel=1e-10)
    assert res["portfolio_return"] == pytest.approx(sum(w[t] * moves[t] for t in w))
    # shocked asset itself moves by the shock (not held -> no P&L)
    spy = next(p for p in res["positions"] if p["ticker"] == "SPY")
    assert spy["move"] == -0.10 and spy["pnl"] == 0.0
    # two-asset shock: E[r_o | r_s] = S_os S_ss^-1 s
    res2 = st.conditional_stress(sub, w, {"SPY": -0.1, "TLT": 0.05}, method="ewma")
    from ohcamel_quant.risk.core import covariance

    cols = ["SPY", "TLT", "GLD", "XLE"]
    _, C = covariance(sub[cols], "ewma")
    B = C[2:, :2] @ np.linalg.inv(C[:2, :2])
    exp = B @ np.array([-0.1, 0.05])
    got = {p["ticker"]: p["move"] for p in res2["positions"]}
    assert got["GLD"] == pytest.approx(exp[0]) and got["XLE"] == pytest.approx(exp[1])


def test_conditional_stress_rejects_collinear_shocks(etf_returns):
    df = etf_returns[["SPY", "TLT"]].copy()
    df["SPY2"] = df["SPY"]
    with pytest.raises(ValueError):
        st.conditional_stress(df, {"TLT": 1.0}, {"SPY": -0.1, "SPY2": -0.1}, method="sample")
