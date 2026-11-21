"""Delisted names behind a switch (completion task 3.8, spec R5): the switch, the plan of what to fetch, one company
across a rename and two across a reused ticker, the write path, the coverage the gate reads, and the acceptance
list and its judge. Everything here runs on committed real data; nothing needs a vendor response."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import survivorship_harness as H

from ohcamel_quant.config import Settings
from ohcamel_quant.data import eodhd
from ohcamel_quant.data.base import DataUnavailable, Provenance
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.jobs.kinds import get_kind
from ohcamel_quant.jobs.schedules import load_schedules
from ohcamel_quant.products.data_integrity import vendor_changes
from ohcamel_quant.warehouse import accept, readers
from ohcamel_quant.warehouse import schedules as wh_schedules
from ohcamel_quant.warehouse.constituents import (
    Change,
    load_changes,
    load_constituents,
    load_renames,
    membership_intervals,
)
from ohcamel_quant.warehouse.db import open_ro, open_rw
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
from ohcamel_quant.warehouse.ingest import HANDLERS, delisted
from ohcamel_quant.warehouse.ingest.base import KeyState, LocalContext
from ohcamel_quant.warehouse.ingest.universes import membership_rows

DATA = Path(__file__).parent / "data"
ON = {"OHCAMEL_DELISTED_SOURCE": "eodhd"}


# ------------------------------------------------------------------ the switch
def test_the_switch_is_off_by_default_and_reads_the_specs_variable(monkeypatch):
    monkeypatch.delenv("OHCAMEL_DELISTED_SOURCE", raising=False)
    assert Settings().delisted_source == ""
    monkeypatch.setenv("OHCAMEL_DELISTED_SOURCE", "eodhd")
    monkeypatch.setenv("EODHD_API_KEY", "k3y-in-env")
    s = Settings()
    assert s.delisted_source == "eodhd" and s.eodhd_api_key == "k3y-in-env" and "k3y-in-env" not in repr(s)
    monkeypatch.setenv("OHCAMEL_DELISTED_SOURCE", "yahoo")
    with pytest.raises(ValueError):
        Settings()


def test_switch_off_the_job_is_not_scheduled(monkeypatch):
    monkeypatch.delenv("OHCAMEL_DELISTED_SOURCE", raising=False)
    assert not [e for e in load_schedules().entries if e.kind == "ingest.delisted"]
    on = [e for e in load_schedules(settings=Settings(**ON)).entries if e.kind == "ingest.delisted"]
    assert len(on) == 1
    e = on[0]
    # Sunday 04:00 New York is ingest.sec_facts' minute; the nearest free quarter-hour after it (04:05 is the
    # selftest, 04:30 the 13F ingest)
    assert (e.cron, e.priority, e.mem_class, e.heavy) == ("15 4 * * 0", 2, "M", False)
    assert get_kind("ingest.delisted").wall_limit_s == 2700


def test_the_two_schedule_declarations_agree_and_share_no_minute():
    s = load_schedules(settings=Settings(**ON))
    want = next(w for w in wh_schedules.SCHEDULES if w["kind"] == "ingest.delisted")
    got = next(e for e in s.entries if e.name == want["name"])
    assert (got.cron, got.params, got.priority, got.mem_class, got.heavy) == (
        want["cron"], want["params"], want["priority"], want["mem_class"], want["heavy"])
    sunday = [e for e in s.entries if e.cron.split()[4] in ("0", "*") and e.kind != "ingest.delisted"]
    assert not [e for e in sunday if e.cron.split()[:2] == ["15", "4"]]


def test_switch_off_the_handler_refuses(tmp_path):
    ctx = LocalContext(market=MarketData(Settings(warehouse_path=tmp_path / "w.duckdb")))
    with pytest.raises(DataUnavailable, match="OHCAMEL_DELISTED_SOURCE"):
        HANDLERS["ingest.delisted"]({}, ctx)


def test_switch_on_without_a_key_the_handler_refuses(tmp_path):
    ctx = LocalContext(market=MarketData(Settings(warehouse_path=tmp_path / "w.duckdb", EODHD_API_KEY=None, **ON)))
    with pytest.raises(DataUnavailable, match="EODHD_API_KEY"):
        HANDLERS["ingest.delisted"]({}, ctx)


# ------------------------------------------------------------------ the rename map
def test_the_rename_map_is_sourced_and_carries_a_sidecar():
    rn = load_renames("sp500")
    meta = json.loads((Path(rn.path).with_name(Path(rn.path).name + ".meta.json")).read_text())
    assert meta["synthetic"] is False and meta["rows"] == len(rn.renames)
    assert all(r.source_url.startswith("https://") for r in rn.renames)
    assert ("FB", "META", date(2022, 6, 9)) in {(r.old, r.new, r.effective) for r in rn.renames}


def test_one_company_across_a_rename_is_one_series():
    frame, _ = membership_rows("sp500")
    meta = frame[frame["series"] == "META"].sort_values("start")
    # Facebook joined on 2013-12-23 as FB and is META from 2022-06-09: two rows, one company, one price series
    assert list(meta["ticker"]) == ["FB", "META"]
    assert meta["start"].iloc[0] == date(2013, 12, 23) and meta["end"].iloc[0] == date(2022, 6, 9)
    assert meta["start"].iloc[1] == date(2022, 6, 9) and pd.isna(meta["end"].iloc[1])


def test_a_ticker_reused_by_a_later_company_is_not_spliced(tmp_path):
    frame, _ = membership_rows("sp500")
    # IR: Ingersoll-Rand plc (a member from 2010, renamed Trane Technologies, TT, on 2020-03-02) and Ingersoll Rand
    # Inc. (the former Gardner Denver, a member from 2020-03-03): two rows under IR, two price series
    ir = frame[frame["ticker"] == "IR"].sort_values("start")
    assert len(ir) == 2 and set(ir["series"]) == {"TT", "IR"}
    # AGN: Allergan Inc. (left 2015-03-23) and Allergan plc (the former Actavis, AGN from 2015-06-15)
    agn = frame[frame["ticker"] == "AGN"]
    assert len(agn) == 2 and set(agn["series"]) == {"AGN", "AGN@2015-03-23"}
    # the delisted ingest never asks the vendor for the earlier company under the later one's code
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_ro(path) as con:
        p = delisted.plan(con)
    keys = {w.series: w for w in p.keys}
    assert "AGN" in keys and keys["AGN"].code == "AGN.US" and keys["AGN"].exit == date(2020, 5, 12)
    assert "AGN@2015-03-23" not in keys
    assert any(n.startswith("AGN@2015-03-23") and "reused" in n for n in p.skipped)


def test_the_map_resolves_the_rows_the_walk_could_not_place():
    c, ch = load_constituents("sp500"), load_changes("sp500")
    current = [t for t, _ in c.members]
    _, before = membership_intervals(current, c.as_of, ch.changes)
    _, after = membership_intervals(current, c.as_of, ch.changes, load_renames("sp500").renames)
    assert len(before) == 28 and len(after) == 9
    late = [n for n in after if int(n.split()[2][:4]) >= 2011]
    assert len(late) == 1 and late[0].startswith("FOX added 2015-09-18")   # 21st Century Fox: a missing removal


def test_member_counts_with_the_map(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_ro(path) as con:
        counts = [len(readers.members_asof(con, "sp500", date(y, 6, 30))) for y in range(2011, 2027)]
        assert "META" not in readers.members_asof(con, "sp500", date(2013, 6, 28))   # not before FB joined
        assert "META" in readers.members_asof(con, "sp500", date(2014, 6, 30))
    assert all(497 <= n <= 506 for n in counts), counts


def test_the_walk_reads_a_rename_both_ways():
    renames = load_renames("sp500").renames
    stints, notes = membership_intervals(["META"], date(2026, 1, 1), [Change(date(2013, 12, 23), "FB", "TER")],
                                         [r for r in renames if r.old == "FB"])
    assert not notes
    got = {(s.ticker, s.start, s.end, s.series) for s in stints}
    assert ("FB", date(2013, 12, 23), date(2022, 6, 9), "META") in got and ("META", date(2022, 6, 9), None, "META") in got


# ------------------------------------------------------------------ the plan and the write path
def test_the_plan_takes_exits_without_bars_to_their_exit(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_ro(path) as con:
        p = delisted.plan(con)
    keys = {w.series for w in p.keys}
    # failures and acquisitions inside coverage, under the ticker whose prices the vendor keeps (META not FB)
    assert {"SIVB", "FRC", "TWTR", "ATVI", "ANDV", "CPRI", "JOY"} <= keys
    assert not {"FB", "DLPH", "KORS", "JOYG", "TSO"} & keys
    assert "META" not in keys and "AAPL" not in keys   # current members are the nightly bars' business
    for w in p.keys:
        assert w.exit >= date(2011, 1, 1) and w.code == eodhd.code_for(w.series)


def test_resumable_a_series_with_bars_to_its_exit_is_not_asked_again(tmp_path):
    from ohcamel_quant.warehouse.ingest.base import insert_frame

    path = H.warehouse(tmp_path, covered=4, uncovered=1)
    with open_rw(path) as con:   # a test stint of the committed AAPL series, its exit the session after its last bar
        insert_frame(con, 'INSERT INTO universe_membership (universe, ticker, "start", "end", source) '
                          'SELECT universe, ticker, CAST("start" AS DATE), CAST("end" AS DATE), source FROM incoming',
                     pd.DataFrame([{"universe": H.UNIVERSE, "ticker": "AAPL", "start": date(2020, 8, 3),
                                    "end": date(2020, 9, 11), "source": "t"}]))
    with open_ro(path) as con:
        p = delisted.plan(con, universes=[H.UNIVERSE])
    assert {w.series for w in p.keys} == {"AAPL"}
    # SPY's exit after the committed bars stop is uncovered, but another vendor serves SPY: never mixed
    assert len(p.skipped) == 1 and p.skipped[0].startswith("SPY: served by")
    with open_rw(path) as con:
        con.execute("BEGIN TRANSACTION")
        delisted.write_delisted(con, "AAPL", KeyState(), _aapl_payload())
        con.execute("COMMIT")
    with open_ro(path) as con:
        assert delisted.plan(con, universes=[H.UNIVERSE]).keys == []   # written once, not asked again


def _aapl_payload() -> delisted.DelistedPayload:
    base = DATA / "eodhd_{}_AAPL.US_2020-07-31_2020-09-10.json"
    served = eodhd.parse_eod(json.loads(Path(str(base).format("eod")).read_bytes()))
    splits = eodhd.parse_splits(json.loads(Path(str(base).format("splits")).read_bytes()))
    divs = eodhd.parse_dividends(json.loads(Path(str(base).format("div")).read_bytes()))
    prov = Provenance("eodhd", "2026-11-20T00:00:00Z", detail={"fixture": True})
    return delisted.DelistedPayload(eodhd.raw_bars(served, splits), eodhd.as_actions(splits, divs), prov,
                                    date(1990, 1, 1), date(2020, 9, 10), True)


def test_a_series_is_written_wholly_from_the_vendor(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_rw(path) as con:
        con.execute("BEGIN TRANSACTION")
        w = delisted.write_delisted(con, "AAPL", KeyState(), _aapl_payload())
        con.execute("COMMIT")
    with open_ro(path) as con:
        src = {r[0] for r in con.execute("SELECT DISTINCT source FROM bars_daily WHERE ticker = 'AAPL'").fetchall()}
        raw = {r[0] for r in con.execute("SELECT DISTINCT source FROM bars_raw WHERE ticker = 'AAPL'").fetchall()}
        acts = con.execute("SELECT kind, ratio, cash, source FROM corp_actions WHERE ticker = 'AAPL' "
                           "ORDER BY ex_date").fetchall()
        assert vendor_changes(con, ["AAPL"]) == []   # one vendor from first bar to last: no vendor_change
        adj = readers.bars_panel(con, ["AAPL"], None, None)[0]["AAPL"]
    assert src == raw == {"eodhd"} and w.data_asof == date(2020, 9, 10)
    assert ("split", 4.0, None, "eodhd") in acts and any(a[0] == "dividend" and a[2] == pytest.approx(0.82) for a in acts)
    assert adj.index[-1] == pd.Timestamp("2020-09-10") and adj.notna().all()


def test_a_name_another_vendor_serves_is_never_mixed(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_ro(path) as con:
        before = con.execute("SELECT count(*), min(source) FROM bars_daily WHERE ticker = 'SPY'").fetchone()
    with open_rw(path) as con:
        con.execute("BEGIN TRANSACTION")
        with pytest.raises(DataUnavailable, match="never half one vendor"):
            delisted.write_delisted(con, "SPY", KeyState(), _aapl_payload())
        con.execute("ROLLBACK")
    with open_ro(path) as con:
        assert con.execute("SELECT count(*), min(source) FROM bars_daily WHERE ticker = 'SPY'").fetchone() == before
        assert con.execute("SELECT count(*) FROM bars_raw WHERE ticker = 'SPY' AND source = 'eodhd'").fetchone()[0] == 0


def test_an_ended_series_is_not_called_stale(tmp_path):
    from ohcamel_quant.products import data_integrity as DI

    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_rw(path) as con:
        con.execute("BEGIN TRANSACTION")
        delisted.write_delisted(con, "AAPL", KeyState(), _aapl_payload())
        con.execute("COMMIT")
    with open_ro(path) as con:
        assert DI.ended_series(con) == {"AAPL"}


# ------------------------------------------------------------------ coverage
def test_coverage_comes_from_membership_and_bars(tmp_path):
    path = H.warehouse(tmp_path, covered=4, uncovered=1)
    with open_ro(path) as con:
        assert readers.survivorship_free(con, H.UNIVERSE) == (False, pytest.approx(0.80))
    H.record_acceptance(path, accept.acceptance_list_sha256())
    with open_ro(path) as con:
        assert readers.survivorship_free(con, H.UNIVERSE) == (True, pytest.approx(0.80))


def test_acceptance_of_another_list_is_not_acceptance(tmp_path):
    path = H.warehouse(tmp_path, covered=33, uncovered=1)
    H.record_acceptance(path, "0" * 64)
    with open_ro(path) as con:
        accepted, cov = readers.survivorship_free(con, H.UNIVERSE)
    assert accepted is False and cov == pytest.approx(33 / 34)


def test_coverage_refuses_a_universe_without_dated_membership(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_ro(path) as con, pytest.raises(DataUnavailable, match="no dated membership"):
        readers.survivorship_free(con, "ndx100")


# ------------------------------------------------------------------ the acceptance list and its judge
def test_the_acceptance_list_is_26_sourced_known_failures():
    rows = accept.load_acceptance_list()
    assert len(rows) == 26 and len({r.ticker for r in rows}) == 26
    named = {"LEH", "BSC", "WM", "SIVB", "FRC", "BBBY"}            # the spec's six
    assert named <= {r.ticker for r in rows}
    # fix round 1 (I6): FNM and FRE went on over the counter as the same companies; replaced by two failures among
    # the exits the ingest itself requests
    assert not {"FNM", "FRE"} & {r.ticker for r in rows} and {"ANR", "ENDP"} <= {r.ticker for r in rows}
    assert sum(r.kind == "failure" for r in rows) >= 10 and sum(r.kind == "acquisition" for r in rows) >= 10
    for r in rows:
        assert r.source_url.startswith("https://") and r.reason and r.last_trading_date < date(2026, 11, 20)
    raw = accept.ACCEPTANCE_LIST.read_bytes()
    meta = json.loads(accept.ACCEPTANCE_LIST.with_name(accept.ACCEPTANCE_LIST.name + ".meta.json").read_text())
    assert meta["synthetic"] is False and meta["sha256"] == hashlib.sha256(raw).hexdigest() == accept.acceptance_list_sha256()
    with accept.ACCEPTANCE_LIST.open() as f:
        header = next(csv.reader(line for line in f if not line.startswith("#")))
    assert header[:4] == ["ticker", "name", "last_trading_date", "reason"]


@pytest.mark.parametrize("dates,ok", [
    (["2008-09-15", "2008-09-16", "2008-09-17"], True),        # stops on the day
    (["2008-09-10", "2008-09-11"], True),                     # stops four sessions early
    (["2008-09-02", "2008-09-03"], False),                    # stops two weeks early
    ([], False),                                              # nothing
    (["2008-09-16", "2008-09-17", "2008-10-15"], False),      # runs on: another company under the code
])
def test_the_judge(dates, ok):
    got, why = accept.judge(date(2008, 9, 17), [date.fromisoformat(d) for d in dates])
    assert got is ok and why


def _rows_dates(drop: int):
    rows = accept.load_acceptance_list()
    gone = {r.ticker for r in rows[:drop]}
    return lambda r: [] if r.ticker in gone else [r.last_trading_date]


def test_acceptance_passes_at_24_of_26_and_records_it(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    code, summary = accept.run_acceptance(path, _rows_dates(2), source="eodhd")
    assert code == 0 and summary["passed"] == 24
    with open_ro(path) as con:
        rows = con.execute("SELECT passed, total, list_sha256 FROM delisted_acceptance").fetchall()
    assert rows == [(24, 26, accept.acceptance_list_sha256())]


def test_acceptance_fails_at_23_and_records_nothing(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    code, summary = accept.run_acceptance(path, _rows_dates(3), source="eodhd")
    assert code == 1 and summary["passed"] == 23 and len(summary["failed"]) == 3
    with open_ro(path) as con:
        assert con.execute("SELECT count(*) FROM delisted_acceptance").fetchone()[0] == 0


def test_members_panel_is_members_asof_on_every_date(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    days = pd.DatetimeIndex(["2011-01-03", "2013-12-20", "2013-12-23", "2015-03-23", "2020-03-02", "2020-03-03",
                             "2022-06-08", "2022-06-09", "2026-11-19"])
    with open_ro(path) as con:
        panel = readers.members_panel(con, "sp500", days)
        for d in days:
            assert sorted(panel.columns[panel.loc[d].to_numpy()]) == readers.members_asof(con, "sp500", d.date())
        with pytest.raises(DataUnavailable, match="not known"):
            readers.members_panel(con, "sp500", pd.DatetimeIndex(["2010-12-31"]))



# ------------------------------------------------------------------ fix round 1
def test_the_replacement_acceptance_names_are_exits_the_ingest_requests(tmp_path):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_ro(path) as con:
        keys = {w.series: w for w in delisted.plan(con).keys}
    assert keys["ANR"].exit == date(2012, 10, 2) and keys["ENDP"].exit == date(2017, 3, 2)
    rows = {r.ticker: r for r in accept.load_acceptance_list()}
    assert rows["ANR"].kind == rows["ENDP"].kind == "failure"


def test_a_name_removed_but_still_trading_is_fetched_100_days_past_its_exit():
    assert delisted.AFTER_EXIT_DAYS == 100   # covers quarterly rebalancing: the next decision is within ~92 days


def test_a_vendor_series_that_starts_after_the_company_joined_is_refused():
    """I4's reuse guard: under a code, a vendor series that begins after the company was already a member is another
    company's (a later holder of the ticker): refused, never written."""
    raw = _aapl_payload().raw                                   # starts 2020-07-31
    ok = delisted.Want("AAPL", "AAPL.US", date(2020, 9, 11), ("t",), first=date(2020, 8, 3))
    delisted.check_continuity(ok, raw)
    late = delisted.Want("AAPL", "AAPL.US", date(2020, 9, 11), ("t",), first=date(2019, 1, 2))
    with pytest.raises(DataUnavailable, match="another company"):
        delisted.check_continuity(late, raw)


def test_the_fetch_end_is_recorded_and_read_back(tmp_path):
    from ohcamel_quant.warehouse.ingest.base import log_row

    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    pay = _aapl_payload()
    pay.fetched_to = date(2020, 9, 10)
    with open_rw(path) as con:
        con.execute("BEGIN TRANSACTION")
        w = delisted.write_delisted(con, "AAPL", KeyState(), pay)
        log_row(con, delisted.DATASET, "AAPL", "ok", w.rows, json.dumps(w.detail, default=str), w.data_asof)
        con.execute("COMMIT")
    with open_ro(path) as con:
        assert readers.fetched_to(con, ["AAPL", "SPY"]) == {"AAPL": date(2020, 9, 10)}


def test_membership_keeps_its_columns_so_the_previous_code_still_writes_it(tmp_path):
    """I5: 006 leaves universe_membership's column list alone (the series live in membership_series), so the
    previous commit's positional insert still works on a migrated warehouse -- a rollback keeps ingest.universes."""
    from ohcamel_quant.warehouse.ingest.base import insert_frame

    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    frame = pd.DataFrame([{"universe": "sp500", "ticker": "ZZZZ", "start": date(2020, 1, 2), "end": None,
                           "source": "previous code"}])
    with open_rw(path) as con:
        con.execute("DELETE FROM universe_membership WHERE universe = 'sp500'")
        insert_frame(con, 'INSERT INTO universe_membership SELECT universe, ticker, CAST("start" AS DATE), '
                          'CAST("end" AS DATE), source FROM incoming', frame)   # 4736d8c's statement, verbatim
    with open_ro(path) as con:
        assert readers.members_asof(con, "sp500", date(2024, 1, 2)) == ["ZZZZ"]


def _stint_universe(path, name, start, end):
    from ohcamel_quant.warehouse.ingest.base import insert_frame

    with open_rw(path) as con:
        insert_frame(con, 'INSERT INTO universe_membership (universe, ticker, "start", "end", source) SELECT '
                          'universe, ticker, CAST("start" AS DATE), CAST("end" AS DATE), source FROM incoming',
                     pd.DataFrame([{"universe": name, "ticker": "AAPL", "start": start, "end": end, "source": "t"}]))


def test_coverage_needs_bars_across_the_stint_not_only_its_last_week(tmp_path):
    """M3: the committed AAPL slice runs 2020-07-31..2020-09-10. A stint from 2020-08-03 is covered; one from 2019
    has only its last six weeks and is not."""
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_rw(path) as con:
        con.execute("BEGIN TRANSACTION")
        delisted.write_delisted(con, "AAPL", KeyState(), _aapl_payload())
        con.execute("COMMIT")
    _stint_universe(path, "t-short", date(2020, 8, 3), date(2020, 9, 11))
    _stint_universe(path, "t-long", date(2019, 1, 2), date(2020, 9, 11))
    with open_ro(path) as con:
        assert readers.survivorship_free(con, "t-short")[1] == 1.0
        assert readers.survivorship_free(con, "t-long")[1] == 0.0


def test_a_pass_the_warehouse_was_too_busy_to_record_says_so(tmp_path, monkeypatch):
    """M5: a passing run that cannot record is its own outcome (exit 3, run again), not a failure (exit 1)."""
    from ohcamel_quant.warehouse import db as wdb

    path = build_fixture_warehouse(tmp_path / "w.duckdb")

    def busy(*a, **k):
        raise wdb.WarehouseUnavailable("warehouse w.duckdb: locked by the other process (writer or reader)")

    monkeypatch.setattr(wdb, "open_rw", busy)
    code, summary = accept.run_acceptance(path, _rows_dates(0), source="eodhd")
    assert code == 3 and summary["passed"] == 26 and summary["recorded"] is False and "locked" in summary["why"]
    monkeypatch.undo()
    with open_ro(path) as con:
        assert con.execute("SELECT count(*) FROM delisted_acceptance").fetchone()[0] == 0



# ------------------------------------------------------------------ fix round 2
def test_a_continuous_run_past_the_date_is_the_same_company_and_passes():
    """N3: a vendor that stitches the company's over-the-counter continuation onto the exchange code (ENDPQ onto
    ENDP) serves it correctly: a continuous run past the date passes, and the verdict says it ran on."""
    days = [d.date() for d in pd.bdate_range("2008-09-02", "2008-10-15")]
    ok, why = accept.judge(date(2008, 9, 17), days)
    assert ok is True and "ran on" in why and "2008-10-15" in why
    # prints after a gap of more than 5 weekdays are another company under the code
    gapped = [d for d in days if d <= date(2008, 9, 17) or d >= date(2008, 10, 1)]
    ok, why = accept.judge(date(2008, 9, 17), gapped)
    assert ok is False and "gap" in why


def test_a_reuser_inside_the_window_is_cut_off_at_the_gap():
    """N2: across a gap of more than 5 weekdays on or after the exit the vendor's code may belong to another company;
    the series is cut at the last bar before the gap. The committed AAPL slice, with 2020-08-21..2020-09-03 removed
    as a delisting and re-listing would remove them."""
    raw = _aapl_payload().raw
    gapped = raw[(raw.index <= "2020-08-20") | (raw.index >= "2020-09-04")]
    cut, at = delisted.cut_at_gap(gapped, date(2020, 8, 21))
    assert at == date(2020, 8, 20) and cut.index[-1] == pd.Timestamp("2020-08-20")
    same, none = delisted.cut_at_gap(raw, date(2020, 8, 21))      # no gap: unchanged
    assert none is None and same.equals(raw)
    early, none = delisted.cut_at_gap(gapped, date(2020, 9, 8))  # the gap ends before the exit: not this one
    assert none is None and early.equals(gapped)


# ------------------------------------------------------------------ fix round 3
def test_a_series_with_no_bar_on_or_before_the_date_fails():
    """N3a: a series that starts after the last trading date is not the company's prices up to its delisting."""
    days = [d.date() for d in pd.bdate_range("2008-09-18", "2008-10-17")]
    ok, why = accept.judge(date(2008, 9, 17), days)
    assert ok is False and "on or before the last trading date" in why


def test_a_continuous_run_on_passes_for_a_failure_and_fails_for_an_acquisition():
    """N3b: the same company carries on over the counter only after a failure; an acquired company's code never
    continues, so prints more than 5 weekdays past the date fail for an acquisition."""
    days = [d.date() for d in pd.bdate_range("2008-09-02", "2008-10-15")]
    ok, why = accept.judge(date(2008, 9, 17), days, kind="failure")
    assert ok is True and "ran on" in why
    ok, why = accept.judge(date(2008, 9, 17), days, kind="acquisition")
    assert ok is False and "acquisition" in why and "2008-10-15" in why
    # an acquisition whose prints stop within tolerance past the date still passes
    near = [d.date() for d in pd.bdate_range("2008-09-02", "2008-09-22")]
    assert accept.judge(date(2008, 9, 17), near, kind="acquisition")[0] is True
