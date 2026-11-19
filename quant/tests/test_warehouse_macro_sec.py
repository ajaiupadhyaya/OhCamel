"""FRED, French factors, SEC facts and 13F ingest (compute plan C5), offline
with vendor-format samples (respx) taken from the data layer's own tests."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import fred, french, http, sec
from ohcamel_quant.data.base import DataUnavailable, Provenance
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
    assert len(macro.DEFAULT_SERIES) == 36  # 26 dashboard + 11 tenors - DGS2, DGS10 shared + BAA10Y (EXP-Q02 v2)
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


def test_fred_ingest_carries_the_model_series():
    """EXP-Q02 v2 reads BAA10Y from the warehouse; the nightly FRED ingest must fetch it."""
    from ohcamel_quant.products import experiment
    from ohcamel_quant.warehouse.ingest import macro

    assert "BAA10Y" in macro.DEFAULT_SERIES
    for sid in experiment.load_config("EXP-Q02")["fred"].values():
        assert sid in macro.DEFAULT_SERIES, sid


# ------------------------------------------- vintages, read as first published (completion task 3.5)
DATA = Path(__file__).parent / "data"
# Committed ALFRED slices (sidecars say what was kept): UNRATE as served on five vintage dates, and on the six
# Fridays of May 2020 for the keyless backfill.
VINTAGES = DATA / "alfred_unrate_vintages_2026-11-19.csv"
VINTAGE_DATES = [date(2020, 5, 7), date(2020, 5, 8), date(2020, 6, 5), date(2021, 1, 8), date(2026, 11, 19)]
FRIDAYS = DATA / "alfred_unrate_fridays_2020-05_2026-11-19.csv"


def _vintage_rows():
    return fred.parse_alfredgraph_csv(VINTAGES.read_text(), "UNRATE", VINTAGE_DATES)


def _warehouse_with_vintages(tmp_path):
    from ohcamel_quant.warehouse.db import open_rw

    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        macro.append_vintages(con, "UNRATE", _vintage_rows(), "alfred-graph")
    return path


def test_alfred_fixture_parses_to_vintage_rows():
    meta = json.loads((DATA / (VINTAGES.name + ".meta.json")).read_text())
    assert meta["synthetic"] is False and meta["url"].startswith(fred.ALFREDGRAPH) and len(meta["original_sha256"]) == 64
    rows = _vintage_rows()
    april = rows[rows["date"] == "2020-04-01"][["realtime_start", "value"]].to_numpy().tolist()
    # absent on 2020-05-07, first published 14.7 on 2020-05-08, revised to 14.8 on 2021-01-08, unchanged since
    assert [(str(d.date()), v) for d, v in april] == [("2020-05-08", 14.7), ("2021-01-08", 14.8)]
    jan = rows[rows["date"] == "2020-01-01"]["value"].tolist()
    assert jan == [3.6, 3.5, 3.6]  # 3.6 (2020-05-07), revised 3.5 (2021-01-08), 3.6 again today


def test_alfred_refuses_a_column_for_another_date():
    # asked for a date before a series' first vintage, ALFRED answers today's data under today's name
    text = "observation_date,USREC_20261119\n2007-06-01,0\n"
    with pytest.raises(DataUnavailable, match="no vintage of USREC on 20120601"):
        fred.parse_alfredgraph_csv(text, "USREC", [date(2012, 6, 1)])


def test_fred_asof_reads_april_2020_as_first_published(tmp_path):
    path = _warehouse_with_vintages(tmp_path)
    with open_ro(path) as con:
        may = readers.fred_asof(con, "UNRATE", date(2020, 5, 15))
        before = readers.fred_asof(con, "UNRATE", date(2020, 5, 7))
        now = readers.fred_asof(con, "UNRATE", date(2026, 11, 19))
        feb21 = readers.fred_asof(con, "UNRATE", date(2021, 2, 1))
        feb21_first = readers.fred_asof(con, "UNRATE", date(2021, 2, 1), first_release=True)
    assert may[pd.Timestamp("2020-04-01")] == 14.7          # the first release, not today's 14.8
    assert now[pd.Timestamp("2020-04-01")] == 14.8
    assert pd.Timestamp("2020-04-01") not in before.index    # not yet published on 2020-05-07
    assert may.index.max() == pd.Timestamp("2020-04-01")
    # as it stood on 2021-02-01 (January 2021's seasonal revision known) vs every month's first release
    assert feb21[pd.Timestamp("2020-01-01")] == 3.5 and feb21_first[pd.Timestamp("2020-01-01")] == 3.6
    with open_ro(path) as con, pytest.raises(DataUnavailable, match="no vintages"):
        readers.fred_asof(con, "NFCI", date(2021, 2, 1))


def test_vintages_are_appended_never_deleted(tmp_path):
    from ohcamel_quant.warehouse.db import open_rw

    path = _warehouse_with_vintages(tmp_path)
    q_all = "SELECT date, realtime_start, value FROM fred_vintages ORDER BY ALL"
    first = q(tmp_path, q_all)
    with open_rw(path) as con:
        assert macro.append_vintages(con, "UNRATE", _vintage_rows(), "alfred-graph") == 0   # nothing new
        # the Fridays of May 2020: the same April figure seen a week later says nothing new; May's first
        # release (2020-06-05, 13.3) is already stored; nothing is replaced
        fr = fred.parse_alfredgraph_csv(FRIDAYS.read_text(), "UNRATE", macro.friday_grid(date(2020, 5, 1),
                                                                                          date(2020, 6, 5)))
        assert macro.append_vintages(con, "UNRATE", fr, "alfred-graph") > 0  # 2020-05-01 dates earlier rows
    after = q(tmp_path, q_all)
    assert set(first) <= set(after)  # every earlier row is still there
    with open_ro(path) as con:
        assert readers.fred_asof(con, "UNRATE", date(2020, 5, 15))[pd.Timestamp("2020-04-01")] == 14.7


def test_api_vintages_parse_and_page(tmp_path):
    # the API's own shape (series/observations with a real-time period), April 2020 as ALFRED records it
    page1 = {"count": 3, "offset": 0, "limit": 2, "observations": [
        {"realtime_start": "2020-05-08", "realtime_end": "2021-01-07", "date": "2020-04-01", "value": "14.7"},
        {"realtime_start": "2021-01-08", "realtime_end": "9999-12-31", "date": "2020-04-01", "value": "14.8"}]}
    page2 = {"count": 3, "offset": 2, "limit": 2, "observations": [
        {"realtime_start": "2020-06-05", "realtime_end": "9999-12-31", "date": "2020-05-01", "value": "."}]}
    s = Settings(offline=False, data_dir=tmp_path, http_retries=0, FRED_API_KEY="test-not-a-key")
    with respx.mock:
        route = respx.get(f"{fred.API}/series/observations").mock(
            side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
        rows, prov = fred.fetch_vintages("UNRATE", s)
        assert route.calls[0].request.url.params["realtime_start"] == "1776-07-04"
        assert route.calls[1].request.url.params["offset"] == "2"   # the second page asked for
    assert rows["value"].tolist() == [14.7, 14.8] and str(rows["realtime_start"].iloc[1].date()) == "2021-01-08"
    assert macro._vintage_source(prov) == "alfred-api"              # a missing value ('.') is dropped
    with pytest.raises(DataUnavailable, match="no vintages"):
        fred.parse_api_vintages(page2, "UNRATE")


@respx.mock
def test_fred_ingest_appends_todays_vintage_without_a_key(tmp_path, monkeypatch):
    monkeypatch.setattr(fred, "_today_ny", lambda: date(2026, 11, 19))
    unrate = "observation_date,UNRATE\n2020-03-01,4.4\n2020-04-01,14.8\n"
    respx.get(fred.FREDGRAPH).mock(return_value=httpx.Response(200, text=unrate))
    # today's column of the committed slice, as ALFRED serves one vintage
    lines = VINTAGES.read_text().splitlines()
    today = "\n".join(["observation_date,UNRATE_20261119"] + [f"{ln.split(',')[0]},{ln.split(',')[-1]}"
                                                              for ln in lines[1:]]) + "\n"
    route = respx.get(fred.ALFREDGRAPH).mock(return_value=httpx.Response(200, text=today))
    spec = macro.run_fred({"series": ["UNRATE"]}, ctx(tmp_path))
    assert route.calls[0].request.url.params["vintage_date"] == "2026-11-19"
    got = q(tmp_path, "SELECT realtime_start, value, source FROM fred_vintages WHERE date = DATE '2020-04-01'")
    assert got == [(date(2026, 11, 19), 14.8, "alfred-graph")]
    assert ("fred_vintages", "UNRATE", "ok") in q(tmp_path, "SELECT dataset, key, status FROM ingest_log")
    assert any("fred_vintages" in n for n in spec["notes"])
    macro.run_fred({"series": ["UNRATE"]}, ctx(tmp_path))          # again: nothing new is appended
    assert q(tmp_path, "SELECT count(*) FROM fred_vintages")[0][0] == len(lines) - 1


@respx.mock
def test_keyless_backfill_resumes_after_its_last_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(fred, "ALFRED_BATCH", 6)
    route = respx.get(fred.ALFREDGRAPH).mock(return_value=httpx.Response(200, text=FRIDAYS.read_text()))
    s = Settings(offline=False, data_dir=tmp_path, http_retries=0, warehouse_path=tmp_path / "w.duckdb",
                 FRED_API_KEY=None)
    out = macro.backfill_vintages("UNRATE", s, since=date(2020, 5, 1), until=date(2020, 6, 5), progress=lambda m: None)
    assert out["batches"] == 1 and out["through"] == "2020-06-05" and out["source"] == "alfred-graph"
    assert route.calls[0].request.url.params["vintage_date"] == ("2020-05-01,2020-05-08,2020-05-15,2020-05-22,"
                                                                 "2020-05-29,2020-06-05")
    assert q(tmp_path, "SELECT realtime_start, value FROM fred_vintages WHERE date = DATE '2020-04-01'") == [
        (date(2020, 5, 8), 14.7)]                                   # first seen on its release Friday
    again = macro.backfill_vintages("UNRATE", s, until=date(2020, 6, 5), progress=lambda m: None)
    assert again["batches"] == 0 and len(route.calls) == 1         # resumes after 2020-06-05: nothing left
