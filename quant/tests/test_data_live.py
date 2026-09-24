"""Live checks against the real vendors (run with ``OHCAMEL_QUANT_LIVE_TESTS=1
pytest -m live``). Skipped by default; each uses a private cache directory."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.config import Settings
from ohcamel_quant.data import prices
from ohcamel_quant.data.market import MarketData, load_universes

pytestmark = pytest.mark.live


@pytest.fixture
def live(tmp_path) -> MarketData:
    return MarketData(Settings(offline=False, data_dir=tmp_path))


def test_live_spy_from_yahoo(tmp_path):
    s = Settings(offline=False, data_dir=tmp_path)
    df, prov = prices._fetch_yahoo("SPY", date(2000, 1, 1), None, s)
    assert prov.source == "yahoo" and df.index[0].year == 2000 and len(df) > 6000
    assert (df.index[-1] - pd.Timestamp(date.today())).days > -10
    assert (df["adj_close"] <= df["close"] * 1.0001).all()  # dividends only ever lower adj history
    assert df.index.is_monotonic_increasing and not df["close"].isna().any()


def test_live_index_and_crypto(live):
    vix = live.ohlcv("^VIX", date.today() - timedelta(days=60)).data
    btc = live.ohlcv("BTC-USD", date.today() - timedelta(days=30)).data
    assert 5 < vix["close"].iloc[-1] < 150 and (btc.index.dayofweek >= 5).any()


def test_live_fred_dgs10_and_curve(live):
    d = live.fred(["DGS10"], date(2020, 1, 1)).data
    assert d["DGS10"].notna().sum() > 1000 and 0 < d["DGS10"].dropna().iloc[-1] < 20
    curve = live.treasury_curve(date.today() - timedelta(days=30)).data
    assert list(curve.columns) == sorted(curve.columns) and 30.0 in curve.columns
    rf = live.risk_free_daily(date(2024, 1, 1)).data
    assert 0 <= rf.iloc[-1] < 0.001


def test_live_ff5_daily(live):
    ff = live.ff_factors("ff5", momentum=True).data
    assert list(ff.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom", "RF"]
    assert ff.index[0] >= pd.Timestamp("1963-07-01") and ff["Mkt-RF"].abs().max() < 0.25


def test_live_spy_cboe_chain(live):
    ch = live.option_chain("SPY").data
    q = ch.quotes
    assert ch.spot > 100 and len(q) > 1000 and set(q["type"]) == {"C", "P"}
    assert (q["expiry"] >= pd.Timestamp(date.today()) - pd.Timedelta(days=1)).all()
    spx = live.option_chain("SPX").data
    assert spx.spot > 1000 and spx.quotes["root"].isin(["SPX", "SPXW"]).all()


def test_live_aapl_companyfacts(live):
    d = live.company_facts("AAPL").data
    assert d["cik"] == "0000320193"
    a = d["annual"]
    assert a["revenue"].dropna().iloc[-1] > 3e11 and a["total_assets"].dropna().iloc[-1] > 3e11
    assert d["shares_outstanding"] and d["shares_outstanding"] > 1e10
    q = d["quarterly"]["revenue"].dropna()
    # Apple's quarterly revenue was ~$7-13B in 2008 and has been > $50B since FY2019
    assert len(q) >= 8 and (q.loc["2019":] > 5e10).all() and (q > 0).all()
    # quarters of the latest complete fiscal year sum to the annual figure
    fy_end = a["revenue"].dropna().index[-1]
    last4 = q.loc[:fy_end].tail(4)
    assert last4.sum() == pytest.approx(a.loc[fy_end, "revenue"], rel=1e-6)


def test_live_berkshire_13f(live):
    d = live.holdings_13f("1067983").data
    h = d["holdings"]
    assert "BERKSHIRE" in d["filer"].upper() and len(h) > 20
    assert h.loc[h["put_call"].isna(), "weight"].sum() == pytest.approx(1.0)
    assert h["value_usd"].sum() > 1e11  # >$100bn in USD, i.e. units handled
    assert "AAPL" in set(h["ticker"].dropna()) or h["ticker"].isna().all()


def test_live_notable_filer_ciks_match_sec_names(tmp_path):
    from ohcamel_quant.data import http
    from ohcamel_quant.data.sec import SUBMISSIONS_URL

    s = Settings(offline=False, data_dir=tmp_path)
    for f in load_universes()["notable_13f_filers"]:
        payload = http.get_json(SUBMISSIONS_URL.format(cik=f["cik"]), s, source="sec-edgar")
        assert f["sec_name_contains"] in payload["name"].upper(), (f["cik"], payload["name"])
        assert any(x.startswith("13F") for x in payload["filings"]["recent"]["form"]), f["cik"]


def test_live_search(live):
    hits = live.search("apple").data
    assert hits[0]["ticker"] == "AAPL"
    assert np.all([h["cik"] and len(h["cik"]) == 10 for h in hits])
