"""/api/market router, offline against the committed real fixtures."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.api.routers import market as mr
from ohcamel_quant.data.market import load_universes, start_background_refresh
from ohcamel_quant.data.warm import warm


@pytest.fixture(autouse=True)
def _fresh_cache():
    mr.clear_cache()


def test_universes_definitions(client):
    j = client.get("/api/market/universes").json()
    u = j["universes"]
    for key in ("us_equity_indices", "sectors", "style_factors", "rates", "credit", "international",
                "commodities", "crypto", "volatility"):
        assert u[key]["members"], key
    assert [m["ticker"] for m in u["sectors"]["members"]][:3] == ["XLK", "XLF", "XLE"]
    assert len(u["sectors"]["members"]) == 11
    assert {m["ticker"] for m in u["volatility"]["members"]} == {"^VIX"}
    filers = j["notable_13f_filers"]
    assert len(filers) == 6 and all(len(f["cik"]) == 10 and f["cik"].isdigit() for f in filers)
    assert "provenance" in j


def test_overview_metrics_match_closed_forms(client, market):
    j = client.get("/api/market/overview", params={"tickers": "SPY,TLT,GLD"}).json()
    assert [r["ticker"] for r in j["rows"]] == ["SPY", "TLT", "GLD"]
    spy = j["rows"][0]
    df = market.ohlcv("SPY").data
    px = df["adj_close"]
    t = px.index[-1]
    assert spy["as_of"] == t.date().isoformat() and spy["last"] == df["close"].iloc[-1]
    assert spy["ret_1d"] == pytest.approx(px.iloc[-1] / px.iloc[-2] - 1)
    assert spy["ret_1y"] == pytest.approx(px.iloc[-1] / px.loc[:t - pd.DateOffset(years=1)].iloc[-1] - 1)
    assert spy["ret_ytd"] == pytest.approx(px.iloc[-1] / px.loc[:f"{t.year - 1}-12-31"].iloc[-1] - 1)
    base3 = px.loc[:t - pd.DateOffset(years=3)]
    days = (t - base3.index[-1]).days
    assert spy["ret_3y_ann"] == pytest.approx((px.iloc[-1] / base3.iloc[-1]) ** (365.25 / days) - 1)
    assert spy["sma200_pos"] == pytest.approx(px.iloc[-1] / px.tail(200).mean() - 1)
    lr = np.log(px).diff().loc[t - pd.DateOffset(months=1):].iloc[1:]
    assert spy["vol_1m_ann"] == pytest.approx(lr.std(ddof=1) * np.sqrt(spy["periods_per_year"]))
    assert 240 <= spy["periods_per_year"] <= 256
    assert spy["sparkline"]["values"][-1] == pytest.approx(px.iloc[-1], rel=1e-6)
    # the fixture's 2026-02-02 SPY low of 69.005 (decimal-shift bad print) is
    # repaired at load (data/fixtures.py) and the repair is stated in provenance
    assert spy["low_52w"] > 400 and spy["bad_ticks_52w"] == 0
    assert any("2026-02-02" in r for p in j["provenance"] for r in p["detail"].get("repairs", []))
    c = np.array(j["correlation_1y"]["matrix"])
    assert np.allclose(np.diag(c), 1) and np.allclose(c, c.T) and j["correlation_1y"]["n_obs"] >= 200
    assert j["provenance"] and all(p["synthetic"] is False for p in j["provenance"])
    assert "method" in j


def test_overview_universe_partial_errors_are_reported(client):
    j = client.get("/api/market/overview", params={"universe": "us_equity_indices"}).json()
    rows = {r["ticker"]: r for r in j["rows"]}
    assert rows["SPY"]["error"] is None and rows["QQQ"]["error"] is None
    assert rows["DIA"]["error"] and "offline" in rows["DIA"]["error"]
    assert any("could not be loaded" in n for n in j["notes"])


def test_overview_states_fixture_split_repair(client):
    j = client.get("/api/market/overview", params={"universe": "sectors"}).json()
    xlk = [p for p in j["provenance"] if p["detail"].get("symbol") == "XLK"]
    assert xlk and any("split-adjusted 2:1" in r for r in xlk[0]["detail"]["repairs"])
    assert not any("XLK" in n and "corporate action" in n for n in j["notes"])


def test_overview_unknown_universe_and_cache(client):
    assert client.get("/api/market/overview", params={"universe": "nope"}).status_code == 404
    a = client.get("/api/market/overview", params={"tickers": "SPY"}).json()
    assert len(mr._cache) == 1
    b = client.get("/api/market/overview", params={"tickers": "SPY"}).json()
    assert a == b and len(mr._cache) == 1


def test_history(client):
    r = client.get("/api/market/history/SPY", params={"start": "2025-01-02", "end": "2025-12-31"})
    assert r.status_code == 200
    j = r.json()
    assert j["first"] >= "2025-01-02" and j["last"] <= "2025-12-31" and j["n"] == len(j["ohlcv"]["index"])
    assert j["ohlcv"]["columns"] == ["open", "high", "low", "close", "adj_close", "volume"]
    assert j["provenance"][0]["source"].startswith("fixture")
    assert client.get("/api/market/history/SPY", params={"start": "2025-02-01", "end": "2025-01-01"}).status_code == 422
    assert client.get("/api/market/history/NOPE").status_code == 503


def test_quotes_and_search(client):
    j = client.get("/api/market/quotes", params={"tickers": "spy,QQQ"}).json()
    assert [q["ticker"] for q in j["quotes"]] == ["SPY", "QQQ"]
    q = j["quotes"][0]
    assert q["change"] == pytest.approx(q["price"] - q["prev_close"])
    assert j["notes"] and "offline" in j["notes"][0]
    s = client.get("/api/market/search", params={"q": "xl"}).json()
    assert {r["ticker"] for r in s["results"]} == {"XLE", "XLF", "XLK"}
    assert client.get("/api/market/search", params={"q": ""}).status_code == 422


def test_13f_endpoints_offline(client):
    j = client.get("/api/market/13f/filers").json()
    assert j["filers"][0]["cik"] == "0001067983"
    r = client.get("/api/market/13f/1067983")
    assert r.status_code == 503 and "offline" in r.json()["detail"]
    assert client.get("/api/market/13f/berkshire").status_code == 422


def test_13f_summary_math(monkeypatch):
    from ohcamel_quant.data.base import Provenance
    from ohcamel_quant.data.market import Dataset

    h = pd.DataFrame({
        "issuer": ["A", "B", "C", "D"], "cusip": ["1", "2", "3", "4"], "title": ["COM"] * 4,
        "ticker": ["A", "B", None, "D"], "value_usd": [600.0, 300.0, 100.0, 500.0],
        "shares": [1.0, 1.0, 1.0, 1.0], "shares_type": ["SH"] * 4, "put_call": [None, None, None, "Put"],
        "weight": [0.6, 0.3, 0.1, np.nan],
    })

    class Stub:
        def holdings_13f(self, cik):
            return Dataset({"filer": "X", "cik": "0000000001", "period": "2024-09-30", "filed": "2024-11-14",
                            "form": "13F-HR", "accession": "a", "url": "u", "holdings": h,
                            "amendments": [], "notes": []}, [Provenance.now("sec-edgar")])

    j = mr._f13(Stub(), "1", 2)
    s = j["summary"]
    assert s["hhi"] == pytest.approx(0.36 + 0.09 + 0.01)
    assert s["effective_n"] == pytest.approx(1 / 0.46)
    assert s["top10_weight"] == pytest.approx(1.0) and s["n_positions"] == 3 and s["n_option_rows"] == 1
    assert s["total_value_usd"] == 1000.0 and len(j["holdings"]) == 2


def test_clean_range_clips_only_bad_prints():
    df = pd.DataFrame({"open": [100.0, 100.0], "close": [101.0, 99.0], "high": [102.0, 500.0],
                       "low": [10.0, 98.0]})
    hi, lo, n = mr.clean_range(df)
    assert n == 2 and hi.tolist() == [102.0, 100.0] and lo.tolist() == [100.0, 98.0]


def test_warm_never_raises_and_reports(market):
    summary = warm(market, include_13f=True)
    assert "ohlcv:SPY" in summary["ok"]
    assert "ohlcv:DIA" in summary["failed"] and "ff5+mom" in summary["failed"]
    assert "13f:0001067983" in summary["failed"]
    assert set(summary["seconds"]) >= set(summary["ok"])


def test_background_refresh_is_noop_offline(market):
    assert start_background_refresh(market=market) is None


def test_universe_tickers_are_unique_per_universe():
    for key, spec in load_universes()["universes"].items():
        tickers = [m["ticker"] for m in spec["members"]]
        assert len(tickers) == len(set(tickers)), key
