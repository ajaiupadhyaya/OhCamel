"""Universes (compute plan C2): S&P 500 + Nasdaq-100 current constituents from
committed, dated lists, plus the site's 45 instruments from universes.json;
names from SEC company_tickers where it lists the ticker."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import http, sec
from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.data.market import MarketData, load_universes
from ohcamel_quant.warehouse import readers
from ohcamel_quant.warehouse.constituents import (
    COVERAGE_START,
    SURVIVORSHIP,
    Change,
    load_changes,
    load_constituents,
    membership_intervals,
    parse_changes_wikitext,
    parse_constituents_wikitext,
    write_constituents_csv,
)
from ohcamel_quant.warehouse.db import open_ro, open_rw
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
from ohcamel_quant.warehouse.ingest import HANDLERS
from ohcamel_quant.warehouse.ingest.base import LocalContext, insert_frame
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


# ------------------------------------------------- dated membership (completion task 3.5)
DATA = Path(__file__).parent / "data"
# A committed slice of Wikipedia's "Historical components of the S&P 500" changes table (2023-01-03 .. 2026-10-30,
# revision 1385951946; the sidecar says what was kept), paired with sp500.csv from the same day.
CHANGES_SLICE = DATA / "wikipedia_sp500_changes_2026-11-19.wikitext"


def _slice() -> list[Change]:
    return parse_changes_wikitext(CHANGES_SLICE.read_text())


def test_changes_fixture_is_a_real_sidecarred_slice():
    meta = json.loads((DATA / (CHANGES_SLICE.name + ".meta.json")).read_text())
    assert meta["synthetic"] is False and "oldid=" in meta["url"] and len(meta["original_sha256"]) == 64
    ch = _slice()
    assert len(ch) == 79 and ch[0] == Change(date(2026, 10, 30), "OKTA", "KVUE")
    assert Change(date(2024, 9, 23), "PLTR", "AAL") in ch and Change(date(2024, 9, 23), "DELL", "ETSY") in ch
    # spin-offs add without removing, a shrink removes without adding
    assert Change(date(2024, 4, 1), "SOLV", "") in ch and Change(date(2024, 4, 3), "", "VFC") in ch


def test_committed_changes_table_agrees_with_the_slice():
    full = load_changes("sp500")
    assert full.source.startswith("https://en.wikipedia.org/") and "oldid=" in full.source
    assert [c for c in full.changes if c.effective >= date(2023, 1, 1)] == _slice()
    assert len(full.changes) > 400 and min(c.effective for c in full.changes) < COVERAGE_START["sp500"]


def _membership_db(tmp_path, changes):
    import pandas as pd

    c = load_constituents("sp500")
    stints, notes = membership_intervals([t for t, _ in c.members], c.as_of, changes)
    frame = pd.DataFrame([{"universe": "sp500", "ticker": s.ticker, "start": s.start, "end": s.end, "source": "t"}
                          for s in stints])
    path = tmp_path / "m.duckdb"
    with open_rw(path) as con:
        insert_frame(con, 'INSERT INTO universe_membership SELECT universe, ticker, CAST("start" AS DATE), '
                          'CAST("end" AS DATE), source FROM incoming', frame)
    return path, notes


def test_a_name_removed_in_2024_is_a_member_before_and_not_after(tmp_path):
    path, notes = _membership_db(tmp_path, _slice())
    with open_ro(path) as con:
        before = readers.members_asof(con, "sp500", date(2023, 6, 30))
        after = readers.members_asof(con, "sp500", date(2025, 1, 2))
    # Etsy left on 2024-09-23 (replaced by Dell); Palantir joined the same day
    assert "ETSY" in before and "ETSY" not in after
    assert "PLTR" not in before and "PLTR" in after
    # Illumina left on 2024-06-24 and came back on 2026-09-21: two stints
    assert "ILMN" in before and "ILMN" not in after
    with open_ro(path) as con:
        assert "ILMN" in readers.members_asof(con, "sp500", date(2026, 11, 19))
    assert 495 <= len(before) <= 515 and 495 <= len(after) <= 515
    # one 2023+ row does not place: SATS (EchoStar) joined on 2026-03-23 and is not on the list under that ticker
    assert len(notes) == 1 and notes[0].startswith("SATS added 2026-03-23")


def test_members_asof_refuses_before_coverage_and_without_stints(tmp_path):
    path, _ = _membership_db(tmp_path, _slice())
    with open_ro(path) as con:
        with pytest.raises(DataUnavailable, match="not known"):
            readers.members_asof(con, "sp500", date(2010, 12, 31))
        with pytest.raises(DataUnavailable, match="no dated membership"):
            readers.members_asof(con, "ndx100", date(2024, 1, 2))


def test_the_walk_says_what_it_cannot_place():
    # a ticker added under a name it no longer trades under, and a removal of a name already a member later
    stints, notes = membership_intervals(["META", "AGN"], date(2026, 1, 1), [
        Change(date(2015, 3, 23), "", "AGN"), Change(date(2013, 12, 23), "FB", "XX"),
        Change(date(2027, 1, 1), "NEW", "META")])
    assert any("FB added 2013-12-23" in n for n in notes)
    assert any("AGN removed 2015-03-23" in n for n in notes)
    assert any("not applied" in n for n in notes)       # a change after the list's date is not applied
    got = {(s.ticker, s.start, s.end) for s in stints}
    assert ("META", None, None) in got and ("XX", None, date(2013, 12, 23)) in got
    assert ("AGN", date(2015, 3, 23), None) in got and ("AGN", None, date(2015, 3, 23)) in got


@respx.mock
def test_run_universes_writes_dated_membership(tmp_path):
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(200, json=COMPANY_TICKERS))
    spec = run_universes({}, ctx(tmp_path))
    with open_ro(tmp_path / "w.duckdb") as con:
        assert "ETSY" in readers.members_asof(con, "sp500", date(2023, 6, 30))
        assert "ETSY" not in readers.members_asof(con, "sp500", date(2025, 1, 2))
        src = con.execute("SELECT DISTINCT source FROM universe_membership").fetchall()
        logged = con.execute("SELECT key, status FROM ingest_log WHERE dataset = 'universe_membership'").fetchall()
    assert len(src) == 1 and "Historical_components" in src[0][0] and "coverage from 2011-01-01" in src[0][0]
    assert logged == [("sp500", "ok")]
    assert any("universe_membership" in n and "could not be placed" in n for n in spec["notes"])
    run_universes({}, ctx(tmp_path))  # a re-run replaces the stints, not duplicates them
    with open_ro(tmp_path / "w.duckdb") as con:
        n1 = con.execute("SELECT count(*) FROM universe_membership").fetchone()[0]
    c = load_constituents("sp500")
    assert n1 == len(membership_intervals([t for t, _ in c.members], c.as_of, load_changes("sp500").changes)[0])
