"""FRED, French factors, SEC facts and 13F ingest (compute plan C5), offline
with vendor-format samples (respx) taken from the data layer's own tests."""

from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import numpy as np
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import fred, french, http, sec
from ohcamel_quant.data.base import Provenance
from ohcamel_quant.data.market import Dataset, MarketData
from ohcamel_quant.warehouse import readers
from ohcamel_quant.warehouse.db import open_ro
from ohcamel_quant.warehouse.ingest import HANDLERS, filings, macro
from ohcamel_quant.warehouse.ingest.base import LocalContext


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()


def ctx(tmp_path, **kw):
    base = dict(offline=False, data_dir=tmp_path, http_retries=0, warehouse_path=tmp_path / "w.duckdb",
                APCA_API_KEY_ID=None, APCA_API_SECRET_KEY=None, FRED_API_KEY=None)
    base.update(kw)
    return LocalContext(market=MarketData(Settings(**base)))


def q(tmp_path, sql, args=()):
    with open_ro(tmp_path / "w.duckdb") as con:
        return con.execute(sql, list(args)).fetchall()


# ------------------------------------------------------------------- FRED
DGS10 = "observation_date,DGS10\n2024-01-01,\n2024-01-02,3.95\n2024-01-03,3.91\n"


def test_default_series_are_dashboard_plus_treasury():
    assert len(macro.DEFAULT_SERIES) == 35  # 26 dashboard + 11 tenors - DGS2, DGS10 shared
    assert {"DGS3MO", "CPIAUCSL", "WALCL"} <= set(macro.DEFAULT_SERIES)


@respx.mock
def test_fred_stores_observations_and_replaces_revisions(tmp_path):
    route = respx.get(fred.FREDGRAPH).mock(return_value=httpx.Response(200, text=DGS10))
    c = ctx(tmp_path)
    spec = macro.run_fred({"series": ["DGS10"]}, c)
    assert q(tmp_path, "SELECT date, value FROM fred ORDER BY date") == [
        (date(2024, 1, 2), 3.95), (date(2024, 1, 3), 3.91)]  # 2024-01-01 is a FRED holiday (blank): not stored
    assert spec["data_asof"] == "2024-01-03" and route.calls[0].request.url.params["id"] == "DGS10"
    route.mock(return_value=httpx.Response(200, text=DGS10.replace("3.91", "3.92")))  # a revision
    macro.run_fred({"series": ["DGS10"]}, c)
    assert q(tmp_path, "SELECT value FROM fred WHERE date = DATE '2024-01-03'") == [(3.92,)]


@respx.mock
def test_fred_accepts_series_as_a_string(tmp_path):
    # Lane B's shape {"series": "DGS10"} is one series, not the keys D, G, S, 1, 0.
    route = respx.get(fred.FREDGRAPH).mock(return_value=httpx.Response(200, text=DGS10))
    spec = macro.run_fred({"series": "dgs10"}, ctx(tmp_path))
    assert [c.request.url.params["id"] for c in route.calls] == ["DGS10"]
    assert q(tmp_path, "SELECT DISTINCT key FROM ingest_log ORDER BY key") == [("*",), ("DGS10",)]
    assert spec["data_asof"] == "2024-01-03"


@respx.mock
def test_fred_unknown_series_fails_one_key(tmp_path):
    def by_id(request):  # FRED answers 400 for an unknown id
        ok = request.url.params["id"] == "DGS10"
        return httpx.Response(200, text=DGS10) if ok else httpx.Response(400)

    respx.get(fred.FREDGRAPH).mock(side_effect=by_id)
    spec = macro.run_fred({"series": ["DGS10", "NOPE"]}, ctx(tmp_path))
    assert any("NOPE" in n for n in spec["notes"])
    assert q(tmp_path, "SELECT status FROM ingest_log WHERE key = '*'") == [("partial",)]


# ---------------------------------------------------------------- factors
FF5 = """preamble

,Mkt-RF,SMB,HML,RMW,CMA,RF
20240102,-0.53,0.70,0.58,0.71,0.30,0.022
20240103,-0.84,-0.33,0.14,0.12,0.19,0.022
20240104,-0.31,0.06,-0.10,-0.03,0.22,0.022
"""
MOM = """preamble

,Mom
20240102,   -2.46
20240103,   -0.51
20240105,    0.40
"""


def _zip(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("F-F.CSV", text)
    return buf.getvalue()


@respx.mock
def test_factors_store_decimals_per_dataset(tmp_path):
    respx.get(french.BASE + french.FILES["ff5"] + "_CSV.zip").mock(return_value=httpx.Response(200, content=_zip(FF5)))
    respx.get(french.BASE + french.FILES["mom"] + "_CSV.zip").mock(return_value=httpx.Response(200, content=_zip(MOM)))
    macro.run_factors({}, ctx(tmp_path))
    counts = dict(q(tmp_path, "SELECT dataset, count(*) FROM factors GROUP BY dataset"))
    assert counts == {"ff5_daily": 18, "mom_daily": 3}  # 6 factors x 3 days; 3 momentum days
    (v,), = q(tmp_path, "SELECT value FROM factors WHERE factor = 'Mkt-RF' AND date = DATE '2024-01-02'")
    assert v == pytest.approx(-0.0053)  # -0.53 percent as published
    assert q(tmp_path, "SELECT DISTINCT factor FROM factors WHERE dataset = 'mom_daily'") == [("Mom",)]
    assert dict(q(tmp_path, "SELECT key, data_asof FROM ingest_log WHERE key <> '*'")) == {
        "ff5_daily": date(2024, 1, 4), "mom_daily": date(2024, 1, 5)}


# -------------------------------------------------------------- SEC facts
# Apple's filings, as in quant/tests/test_data_sec.py.
FACTS = {"cik": 320193, "entityName": "Apple Inc.", "facts": {
    "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2023-10-20", "val": 15552752000, "form": "10-K", "filed": "2023-11-03"}]}}},
    "us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"start": "2021-09-26", "end": "2022-09-24", "val": 394328e6, "form": "10-K", "filed": "2022-10-28"},
            {"start": "2021-09-26", "end": "2022-09-24", "val": 394330e6, "form": "10-K", "filed": "2023-11-03"},
            {"start": "2023-01-01", "end": "2023-04-01", "val": 94836e6, "form": "10-Q", "filed": "2023-05-05"},
            {"start": "2022-09-25", "end": "2023-04-01", "val": 211990e6, "form": "10-Q", "filed": "2023-05-05"}]}},
        "Assets": {"units": {"USD": [
            {"end": "2023-09-30", "val": 352583e6, "form": "10-K", "filed": "2023-11-03"},
            {"end": "2023-09-30", "val": 352583e6, "form": "8-K", "filed": "2023-11-03"}]}},
        "NotAStandardizedTag": {"units": {"USD": [{"end": "2023-09-30", "val": 1.0, "form": "10-K",
                                                   "filed": "2023-11-03"}]}},
    }}}
COMPANY_TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}


def test_flatten_keeps_quarter_and_ytd_rows():
    df, conflicts = filings.flatten_company_facts(FACTS, "0000320193", "AAPL")
    # 1 dei + 4 revenue + 1 assets (the 8-K duplicate has the same key and value); the unknown tag is skipped
    assert len(df) == 6 and conflicts == 0
    q2 = df[(df["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax")
            & (df["period_end"] == np.datetime64("2023-04-01"))]
    assert sorted(q2["value"]) == [94836e6, 211990e6]  # quarter and year-to-date both kept
    a = df[df["tag"] == "Assets"].iloc[0]
    assert a["period_start"] == a["period_end"]  # instant fact (contract II.5 amendment)


def test_flatten_counts_conflicting_duplicates():
    bad = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
        {"end": "2023-09-30", "val": 1.0, "form": "10-K", "filed": "2023-11-03"},
        {"end": "2023-09-30", "val": 2.0, "form": "10-K/A", "filed": "2023-11-03"}]}}}}}
    df, conflicts = filings.flatten_company_facts(bad, "1", "X")
    assert len(df) == 1 and conflicts == 1 and df["value"].iloc[0] == 1.0  # first kept, conflict counted


@respx.mock
def test_sec_facts_ingest_and_point_in_time_read(tmp_path):
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(200, json=COMPANY_TICKERS))
    respx.get(sec.FACTS_URL.format(cik="0000320193")).mock(return_value=httpx.Response(200, json=FACTS))
    spec = filings.run_sec_facts({"tickers": ["AAPL"]}, ctx(tmp_path))
    assert spec["data_asof"] == "2023-11-03"  # newest filed date
    tag = ["RevenueFromContractWithCustomerExcludingAssessedTax"]
    with open_ro(tmp_path / "w.duckdb") as con:
        fy22 = lambda asof: readers.sec_facts_asof(con, "AAPL", asof, tag).query(  # noqa: E731
            "period_end == '2022-09-24'")["value"].tolist()
        assert fy22(date(2022, 10, 27)) == []          # not yet filed (II.5 point-in-time rule)
        assert fy22(date(2023, 1, 1)) == [394328e6]    # as first reported, filed 2022-10-28
        assert fy22(date(2023, 12, 1)) == [394330e6]   # the FY2023 10-K's restatement, filed 2023-11-03


@respx.mock
def test_sec_facts_skips_tickers_sec_does_not_list(tmp_path):
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(200, json=COMPANY_TICKERS))
    respx.get(sec.FACTS_URL.format(cik="0000320193")).mock(return_value=httpx.Response(200, json=FACTS))
    spec = filings.run_sec_facts({"tickers": ["AAPL", "SPY"]}, ctx(tmp_path))
    assert any("SPY" in n and "no SEC CIK" in n for n in spec["notes"])


# -------------------------------------------------------------------- 13F
NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"
XML = (f'<informationTable xmlns="{NS}">'
       "<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip>"
       "<value>1500</value><shrsOrPrnAmt><sshPrnamt>15</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
       "</infoTable>"
       "<infoTable><nameOfIssuer>SPDR S&amp;P 500 ETF TR</nameOfIssuer><titleOfClass>TR</titleOfClass>"
       "<cusip>78462F103</cusip><value>2000</value><shrsOrPrnAmt><sshPrnamt>4</sshPrnamt>"
       "<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Put</putCall></infoTable>"
       "</informationTable>")


def fake_13f(cik, settings, map_tickers=True, figi_budget_s=20.0):
    filed = date(2024, 5, 15)
    h = sec.aggregate_holdings(sec.parse_info_table(XML.encode()), filed)
    h["ticker"] = h["cusip"].map({"037833100": "AAPL"})
    return Dataset({"filer": "BERKSHIRE HATHAWAY INC", "cik": sec.cik10(cik), "period": "2024-03-31",
                    "filed": filed.isoformat(), "form": "13F-HR", "accession": "0000950123-24-005483",
                    "url": "https://www.sec.gov/x", "holdings": h, "amendments": [], "notes": []},
                   [Provenance.now("sec-edgar")])


def test_13f_rows_keyed_by_accession(tmp_path, monkeypatch):
    monkeypatch.setattr(sec, "fetch_13f", fake_13f)
    c = ctx(tmp_path)
    spec = filings.run_holdings_13f({"ciks": ["0001067983"]}, c)
    filings.run_holdings_13f({"ciks": ["0001067983"]}, c)  # weekly re-run: idempotent
    got = q(tmp_path, "SELECT cusip, put_call, ticker, value_usd, weight FROM holdings_13f ORDER BY cusip")
    # filed 2024-05-15 >= 2023-01-03, so value is in whole dollars (data/sec.py value_multiplier)
    assert got == [("037833100", "", "AAPL", 1500.0, 1.0), ("78462F103", "Put", None, 2000.0, None)]
    assert spec["data_asof"] == "2024-05-15"


def test_handlers_registered():
    for kind in ("ingest.fred_warehouse", "ingest.factors", "ingest.sec_facts", "ingest.holdings_13f"):
        assert kind in HANDLERS
    # Lane B owns ingest.fred (its smoke job); its register() refuses a second spec under one kind.
    assert "ingest.fred" not in HANDLERS
