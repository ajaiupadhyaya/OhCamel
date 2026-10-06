"""Universes (compute plan C2): S&P 500 + Nasdaq-100 current constituents from
committed, dated lists, plus the site's 45 instruments from universes.json;
names from SEC company_tickers where it lists the ticker."""

from __future__ import annotations

import re
from datetime import date

import httpx
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import http, sec
from ohcamel_quant.data.market import MarketData, load_universes
from ohcamel_quant.warehouse.constituents import (
    SURVIVORSHIP,
    load_constituents,
    parse_constituents_wikitext,
    write_constituents_csv,
)
from ohcamel_quant.warehouse.db import open_ro
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
from ohcamel_quant.warehouse.ingest import HANDLERS
from ohcamel_quant.warehouse.ingest.base import LocalContext
from ohcamel_quant.warehouse.ingest.universes import run_universes

# Excerpts of the two Wikipedia tables the committed lists were built from
# (List_of_S&P_500_companies and List_of_NASDAQ-100_companies, action=raw).
SP500_WIKI = """== S&P 500 component stocks ==
{| class="wikitable sortable mw-collapsible sticky-header" id="constituents"
|-
![[Ticker symbol|Symbol]]
! Security !! [[Global Industry Classification Standard|GICS]] Sector !! GICS Sub-Industry
|-
|| {{NyseSymbol|MMM}}
|| [[3M]]|
|| Industrials
|| Industrial Conglomerates
|-
|| {{NyseSymbol|BRK.B}} <!-- DO NOT CHANGE THIS TICKER TO BRK-B. IT IS NOT CORRECT AND WILL BE REVERTED. -->
|| [[Berkshire Hathaway]]
|| Financials
|| Multi-Sector Holdings
|-
|| {{BZX link|CBOE}}
|| [[Cboe Global Markets]]
|| Financials
|| Financial Exchanges & Data
|-
|| {{NasdaqSymbol|GOOGL}}
|| [[Alphabet Inc.|Alphabet Inc. (Class A)]]
|| Communication Services
|| Interactive Media & Services
|}
"""
NDX_WIKI = """{| class="wikitable sortable" id="constituents"
|-
! Ticker !! Company !! [[Industry Classification Benchmark|ICB]] Industry<ref name=":23">{{Cite web |title=x}}</ref>!! ICB Subsector
|-
| ADBE || [[Adobe Inc.]] || Technology || Software
|-
| AMD || [[AMD|Advanced Micro Devices]] || Technology || Semiconductors
|-
| GOOGL || [[Alphabet Inc.]] (Class A) || Technology || Software
|}
"""
COMPANY_TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                   "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()


def ctx(tmp_path):
    s = Settings(offline=False, data_dir=tmp_path, http_retries=0, warehouse_path=tmp_path / "w.duckdb",
                 APCA_API_KEY_ID=None, APCA_API_SECRET_KEY=None, FRED_API_KEY=None)
    return LocalContext(market=MarketData(s))


def test_parse_sp500_wikitext():
    assert parse_constituents_wikitext(SP500_WIKI) == [
        ("MMM", "3M"), ("BRK.B", "Berkshire Hathaway"), ("CBOE", "Cboe Global Markets"),
        ("GOOGL", "Alphabet Inc. (Class A)")]


def test_parse_ndx_wikitext():
    assert parse_constituents_wikitext(NDX_WIKI) == [
        ("ADBE", "Adobe Inc."), ("AMD", "Advanced Micro Devices"), ("GOOGL", "Alphabet Inc. (Class A)")]


def test_parse_rejects_bad_input():
    with pytest.raises(ValueError, match="no constituents table"):
        parse_constituents_wikitext("no table here")
    with pytest.raises(ValueError, match="bad ticker"):
        parse_constituents_wikitext(NDX_WIKI.replace("| ADBE ||", "| adbe!! ||"))


def test_csv_round_trip(tmp_path):
    p = tmp_path / "x.csv"
    write_constituents_csv(p, [("MMM", "3M"), ("BRK.B", "Berkshire Hathaway")], "https://example.test/x",
                           date(2026, 10, 6))
    c = load_constituents(p)
    assert c.as_of == date(2026, 10, 6) and c.source == "https://example.test/x"
    assert c.members == [("MMM", "3M"), ("BRK.B", "Berkshire Hathaway")]


@pytest.mark.parametrize("name, lo, hi", [("sp500", 495, 510), ("ndx100", 98, 104)])
def test_committed_lists_are_sane(name, lo, hi):
    # S&P 500 has ~503 lines (dual share classes); Nasdaq-100 has 100-102 (GOOG/GOOGL).
    c = load_constituents(name)
    tickers = [t for t, _ in c.members]
    assert lo <= len(tickers) <= hi and len(set(tickers)) == len(tickers)
    assert all(re.fullmatch(r"[A-Z]{1,5}(\.[A-Z]{1,2})?", t) for t in tickers)
    assert {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"} <= set(tickers)
    assert date(2026, 10, 1) <= c.as_of <= date.today() and c.source.startswith("https://en.wikipedia.org/")


@respx.mock
def test_run_universes_writes_three_universes(tmp_path):
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(200, json=COMPANY_TICKERS))
    spec = run_universes({}, ctx(tmp_path))
    sp, nd = load_constituents("sp500"), load_constituents("ndx100")
    n_site = len({m["ticker"] for u in load_universes()["universes"].values() for m in u["members"]})
    assert n_site == 45  # the site's universes.json today
    with open_ro(tmp_path / "w.duckdb") as con:
        counts = dict(con.execute("SELECT universe, count(*) FROM universe_members GROUP BY universe").fetchall())
        aapl = con.execute("SELECT name, source, survivorship, added FROM universe_members "
                           "WHERE universe = 'sp500' AND ticker = 'AAPL'").fetchone()
        logged = con.execute("SELECT key, status FROM ingest_log ORDER BY key").fetchall()
    assert counts == {"sp500": len(sp.members), "ndx100": len(nd.members), "site": 45}
    assert aapl[0] == "Apple Inc." and "sec:company_tickers" in aapl[1]  # SEC registrant name
    assert aapl[2] == SURVIVORSHIP and aapl[3] == sp.as_of
    assert ("ndx100", "ok") in logged and ("*", "ok") in logged
    assert spec["survivorship"] == SURVIVORSHIP


@respx.mock
def test_sec_down_still_writes_with_list_names(tmp_path):
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(503))
    spec = run_universes({}, ctx(tmp_path))
    assert any("SEC company_tickers unavailable" in n for n in spec["notes"])
    with open_ro(tmp_path / "w.duckdb") as con:
        name, source = con.execute("SELECT name, source FROM universe_members "
                                   "WHERE universe = 'sp500' AND ticker = 'AAPL'").fetchone()
    assert name == dict(load_constituents("sp500").members)["AAPL"] and "sec:" not in source


@respx.mock
def test_rerun_replaces_not_duplicates(tmp_path):
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(200, json=COMPANY_TICKERS))
    c = ctx(tmp_path)
    run_universes({}, c)
    run_universes({}, c)
    with open_ro(tmp_path / "w.duckdb") as con:
        n = con.execute("SELECT count(*) FROM universe_members").fetchone()[0]
    assert n == len(load_constituents("sp500").members) + len(load_constituents("ndx100").members) + 45


def test_handler_registered_and_fixture_has_universes(tmp_path):
    assert HANDLERS["ingest.universes"] is run_universes
    path = build_fixture_warehouse(tmp_path / "fx.duckdb")
    with open_ro(path) as con:
        assert con.execute("SELECT count(*) FROM universe_members WHERE universe = 'site'").fetchone()[0] == 45
