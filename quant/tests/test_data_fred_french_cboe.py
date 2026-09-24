"""FRED CSV/API parsing and the risk-free conversion; Ken French CSV parsing
(preamble, padded headers, footer, annual block); Cboe chain parsing."""

from __future__ import annotations

import io
import zipfile

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import cboe, fred, french, http
from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.data.market import MarketData


def online(tmp_path, **kw) -> Settings:
    base = dict(offline=False, data_dir=tmp_path, http_retries=0, APCA_API_KEY_ID=None,
                APCA_API_SECRET_KEY=None, FRED_API_KEY=None)
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()


# -------------------------------------------------------------------- FRED
FREDGRAPH_NEW = """observation_date,DGS3MO,DGS10
2024-01-01,,
2024-01-02,5.46,3.95
2024-01-03,5.48,3.91
2024-01-04,5.48,3.99
2024-01-05,5.47,4.05
"""
FREDGRAPH_OLD = """DATE,DGS3MO
2023-12-29,5.40
2024-01-01,.
2024-01-02,5.46
"""


def test_parse_fredgraph_both_header_styles_and_missing_markers():
    a = fred.parse_fredgraph_csv(FREDGRAPH_NEW)
    assert list(a.columns) == ["DGS3MO", "DGS10"] and a.index.name == "date"
    assert np.isnan(a.loc["2024-01-01", "DGS3MO"]) and a.loc["2024-01-05", "DGS10"] == 4.05
    b = fred.parse_fredgraph_csv(FREDGRAPH_OLD)
    assert np.isnan(b.loc["2024-01-01", "DGS3MO"]) and b["DGS3MO"].dtype == float
    with pytest.raises(DataUnavailable):
        fred.parse_fredgraph_csv("<html>blocked</html>")


def test_parse_api_observations():
    payload = {"observations": [
        {"realtime_start": "2024-06-01", "realtime_end": "2024-06-01", "date": "2024-01-01", "value": "."},
        {"realtime_start": "2024-06-01", "realtime_end": "2024-06-01", "date": "2024-01-02", "value": "5.46"}]}
    s = fred.parse_api_observations(payload, "DGS3MO")
    assert np.isnan(s.iloc[0]) and s.iloc[1] == 5.46
    with pytest.raises(DataUnavailable, match="Bad Request"):
        fred.parse_api_observations({"error_code": 400, "error_message": "Bad Request.  Series does not exist"},
                                    "X")


def test_yield_to_daily_return_formula():
    r = fred.yield_to_daily_return(pd.Series([5.0]))
    assert r.iloc[0] == pytest.approx(1.05 ** (1 / 252) - 1)
    assert (1 + r.iloc[0]) ** 252 == pytest.approx(1.05)


@respx.mock
def test_fetch_series_caches_full_history_and_windows(tmp_path):
    s = online(tmp_path)
    route = respx.get(fred.FREDGRAPH).mock(return_value=httpx.Response(200, text=FREDGRAPH_NEW))
    ds = fred.fetch_series(["dgs10", "DGS3MO"], pd.Timestamp("2024-01-03").date(), None, s)
    assert list(ds.data.columns) == ["DGS10", "DGS3MO"] and ds.data.index[0] == pd.Timestamp("2024-01-03")
    assert route.calls[0].request.url.params["id"] == "DGS10,DGS3MO"
    fred.fetch_series(["DGS10"], None, None, s)
    assert route.call_count == 1  # served from the store


@respx.mock
def test_fetch_series_uses_api_when_key_present(tmp_path):
    s = online(tmp_path, FRED_API_KEY="abc")
    route = respx.get(f"{fred.API}/series/observations").mock(return_value=httpx.Response(
        200, json={"observations": [{"date": "2024-01-02", "value": "3.95"}]}))
    ds = fred.fetch_series(["DGS10"], None, None, s)
    assert ds.data["DGS10"].iloc[0] == 3.95
    assert route.calls[0].request.url.params["api_key"] == "abc"


@respx.mock
def test_risk_free_daily_ffills_holidays_only_inside_window(tmp_path):
    s = online(tmp_path)
    respx.get(fred.FREDGRAPH).mock(return_value=httpx.Response(200, text=FREDGRAPH_NEW))
    ds = MarketData(s).risk_free_daily()
    rf = ds.data
    assert rf.name == "RF"
    # 2024-01-01 (holiday, NaN) precedes the first print, so it is not filled
    assert rf.index[0] == pd.Timestamp("2024-01-02") and rf.index[-1] == pd.Timestamp("2024-01-05")
    assert rf.loc["2024-01-02"] == pytest.approx(1.0546 ** (1 / 252) - 1)
    assert ds.provenance[-1].detail["formula"] == "(1 + y/100)**(1/252) - 1"
    assert ds.provenance[-1].detail["rf_series"] == "DGS3MO"
    assert fred.rf_series_used(ds.provenance_dicts()) == "DGS3MO"


@respx.mock
def test_risk_free_falls_back_to_french(tmp_path):
    s = online(tmp_path)
    respx.get(fred.FREDGRAPH).mock(return_value=httpx.Response(503))
    respx.get(url__startswith=french.BASE).mock(return_value=httpx.Response(200, content=_zip(FF3_DAILY)))
    ds = MarketData(s).risk_free_daily()
    assert ds.data.iloc[0] == pytest.approx(0.00022)
    assert "Ken French RF" in ds.provenance[-1].detail["note"]
    assert ds.provenance[-1].detail["rf_series"] == "FF_RF"
    assert fred.rf_series_used(ds.provenance_dicts()) == "FF_RF"


# ------------------------------------------------------------------ French
FF3_DAILY = """This file was created by CMPT_ME_BEME_RETS_DAILY using the 202405 CRSP database.
The 1-month TBill return is from Ibbotson and Associates, Inc.

,Mkt-RF,SMB,HML,RF
20240102,   -0.53,    0.93,    0.58,   0.022
20240103,   -0.84,   -0.25,    0.14,   0.022
20240104,   -0.31,    0.11,   -0.10,   0.022

Copyright 2024 Kenneth R. French
"""
FF5_DAILY = """This file was created by CMPT_ME_BEME_OP_INV_RETS_DAILY using the 202405 CRSP database.
The Tbill return is the simple daily rate that, over the number of trading days
in the month, compounds to 1-month TBill rate from Ibbotson and Associates Inc.

,Mkt-RF,SMB,HML,RMW,CMA,RF
20240102,-0.53,0.70,0.58,0.71,0.30,0.022
20240103,-0.84,-0.33,0.14,0.12,0.19,0.022
20240104,-0.31,0.06,-0.10,-0.03,0.22,0.022
"""
MOM_DAILY = """This file was created by CMPT_ME_PRIOR_RETS_DAILY using the 202405 CRSP database.
It contains a momentum factor, constructed from six value-weight portfolios formed using
independent sorts on size and prior return of NYSE, AMEX, and NASDAQ stocks.
Missing data are indicated by -99.99 or -999.

,Mom
20240102,   -2.46
20240103,   -0.51
20240105,    0.40

Copyright 2024 Kenneth R. French
"""
MONTHLY_WITH_ANNUAL = """preamble line

,Mkt-RF,SMB,HML,RF
202401,    0.70,   -5.09,   -2.38,    0.47
202402,    5.06,   -0.24,   -3.49,    0.42

 Annual Factors: January-December
,Mkt-RF,SMB,HML,RF
  2023,   21.97,   -3.33,  -13.91,    5.02
"""


def _zip(text: str, name: str = "F-F.CSV") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_parse_french_daily_percent_to_decimal_and_footer():
    df = french.parse_french_csv(FF3_DAILY)
    assert list(df.columns) == ["Mkt-RF", "SMB", "HML", "RF"]
    assert len(df) == 3 and df.index[0] == pd.Timestamp("2024-01-02")
    assert df.loc["2024-01-02", "Mkt-RF"] == pytest.approx(-0.0053)
    assert df.loc["2024-01-02", "RF"] == pytest.approx(0.00022)


def test_parse_french_padded_column_names_and_missing_codes():
    df = french.parse_french_csv(MOM_DAILY.replace("0.40", "-99.99"))
    assert list(df.columns) == ["Mom"] and np.isnan(df["Mom"].iloc[-1])


def test_parse_french_monthly_and_annual_tables():
    tables = french.parse_french_tables(MONTHLY_WITH_ANNUAL)
    assert len(tables) == 2
    monthly, annual = tables[0][1], tables[1][1]
    assert monthly.index[0] == pd.Timestamp("2024-01-31")
    assert annual.index[0] == pd.Timestamp("2023-12-31") and "Annual" in tables[1][0]
    assert annual.iloc[0, 0] == pytest.approx(0.2197)


@respx.mock
def test_fetch_factors_ff5_with_momentum_inner_join(tmp_path):
    s = online(tmp_path)
    respx.get(french.BASE + "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip").mock(
        return_value=httpx.Response(200, content=_zip(FF5_DAILY)))
    respx.get(french.BASE + "F-F_Momentum_Factor_daily_CSV.zip").mock(
        return_value=httpx.Response(200, content=_zip(MOM_DAILY)))
    ds = MarketData(s).ff_factors("ff5", momentum=True)
    assert list(ds.data.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom", "RF"]
    assert list(ds.data.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert len(ds.provenance) == 2
    with pytest.raises(ValueError):
        french.fetch_factors("ff7", False, s)


def test_french_bad_zip(tmp_path):
    with pytest.raises(DataUnavailable, match="not a zip"):
        french._read_zip(b"<html>")


# -------------------------------------------------------------------- Cboe
CBOE_SPY = {
    "timestamp": "2024-06-07 16:15:03",
    "data": {
        "symbol": "SPY", "current_price": 534.01, "prev_day_close": 534.66, "iv30": 11.2,
        "options": [
            {"option": "SPY240607C00400000", "bid": 133.5, "bid_size": 10, "ask": 134.9, "ask_size": 10,
             "iv": 0.0, "open_interest": 305, "volume": 12, "delta": 1.0, "gamma": 0.0, "theta": -0.01,
             "rho": 0.01, "vega": 0.0, "theo": 134.0, "last_trade_price": 134.2,
             "last_trade_time": "2024-06-07T15:59:59", "tick": "no_change"},
            {"option": "SPY240621P00530000", "bid": 3.1, "ask": 3.15, "iv": 0.1123, "open_interest": 25000,
             "volume": 8123, "delta": -0.38, "gamma": 0.03, "theta": -0.2, "vega": 0.3, "rho": -0.1,
             "last_trade_price": 3.12},
            {"option": "garbage", "bid": 1},
        ],
    },
}


def test_cboe_symbol_mapping():
    assert cboe.cboe_symbol("SPX") == "_SPX" and cboe.cboe_symbol("^VIX") == "_VIX"
    assert cboe.cboe_symbol("spy") == "SPY" and cboe.cboe_symbol("_NDX") == "_NDX"


def test_parse_occ_variable_root():
    assert cboe.parse_occ("SPXW240607P05000000") == ("SPXW", pd.Timestamp("2024-06-07"), "P", 5000.0)
    assert cboe.parse_occ("AAPL1240621C00182500") == ("AAPL1", pd.Timestamp("2024-06-21"), "C", 182.5)
    with pytest.raises(ValueError):
        cboe.parse_occ("SPY")


def test_parse_chain():
    ch = cboe.parse_chain(CBOE_SPY, "SPY")
    q = ch.quotes
    assert ch.spot == 534.01 and ch.as_of == pd.Timestamp("2024-06-07 16:15:03")
    assert len(q) == 2  # the malformed symbol is skipped
    call = q[q["type"] == "C"].iloc[0]
    assert call["strike"] == 400.0 and np.isnan(call["vendor_iv"])  # iv 0 -> NaN
    put = q[q["type"] == "P"].iloc[0]
    assert put["vendor_iv"] == pytest.approx(0.1123) and put["vendor_delta"] == -0.38
    assert put["expiry"] == pd.Timestamp("2024-06-21") and put["last"] == 3.12
    for col in ("expiry", "strike", "type", "bid", "ask", "last", "volume", "open_interest", "vendor_iv",
                "contract"):
        assert col in q.columns


@respx.mock
def test_fetch_chain_index_underscore_and_cache(tmp_path):
    s = online(tmp_path)
    payload = {"timestamp": "2024-06-07 16:15:03", "data": {**CBOE_SPY["data"], "symbol": "_SPX",
                                                             "current_price": 5346.99}}
    payload["data"]["options"] = [dict(o, option=o["option"].replace("SPY", "SPXW"))
                                  for o in CBOE_SPY["data"]["options"]]
    route = respx.get("https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json").mock(
        return_value=httpx.Response(200, json=payload))
    ds = MarketData(s).option_chain("SPX")
    assert ds.data.spot == 5346.99 and ds.data.underlying == "SPX"
    assert set(ds.data.quotes["root"]) == {"SPXW"}
    MarketData(s).option_chain("SPX")
    assert route.call_count == 1
    assert ds.provenance[0].detail["delay"].startswith("15 minutes")


def test_rf_error_reason_strips_repeated_phrase():
    e = DataUnavailable("risk-free rate unavailable: fred: offline; french: offline")
    assert fred.rf_error_reason(e) == "fred: offline; french: offline"
    assert fred.rf_error_reason(DataUnavailable("risk-free rate unavailable in window: x")) == "x"
    assert fred.rf_error_reason(DataUnavailable("timeout")) == "timeout"
