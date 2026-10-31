"""The Machine's loader and its endpoint. The loader: the latest artifact of each kind read safely inside the
artifacts root, the warehouse read-only and tolerant of being locked or absent, the roster, and the last day's
job runs. Everything read from disk is untrusted, and a source that is missing or fails degrades only its own
node. The endpoint, ``GET /api/machine``: the graph built at most every five minutes or when a newer artifact
appears, the runs always fresh, and nothing private in the public body."""

from __future__ import annotations

import gzip
import json
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient
from lane_m_harness import publish_artifact

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.ratelimit import RateLimit
from ohcamel_quant.api.routers import machine as machine_router
from ohcamel_quant.config import Settings
from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.queue import claim_next, enqueue, fail, set_progress
from ohcamel_quant.jobs.schedules import load_schedules
from ohcamel_quant.machine import load
from ohcamel_quant.machine.graph import LENS_WHY, PROD, WITHHELD, build_graph
from ohcamel_quant.machine.lenses import LENS_TABLES
from ohcamel_quant.machine.load import (
    load_inputs,
    load_products,
    load_runs,
    load_warehouse,
    missed_fire,
)
from ohcamel_quant.machine.service import MachineCache, build_machine, degraded, sentence, watermark
from ohcamel_quant.machine.world import load_world
from ohcamel_quant.signals.report import BOOK_COLUMNS
from ohcamel_quant.warehouse.db import WarehouseUnavailable
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

NOW = datetime(2026, 6, 2, 15, 0, tzinfo=UTC)   # the day after the fixture warehouse's last bar
ET = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)
LEAGUE = pd.DataFrame({"estimator": ["sample", "ledoit_wolf"], "universe": ["core", "core"],
                       "realized_vol": [0.11, 0.1], "rank": [2, 1]})
BOARD = pd.DataFrame({"cell_id": ["momo@etf", "revert@etf"], "verdict": ["PASS", "FAIL"], "detail": ["a", "b"],
                      "oos_sharpe_ann": [0.8, -0.1], "dsr_farm": [0.97, 0.2], "psr": [0.99, 0.3],
                      "pbo": [0.2, 0.7], "n_combos": [40, 40]})
CELLS = pd.DataFrame({"cell_id": ["momo@etf", "revert@etf"], "strategy": ["momo", "revert"],
                      "universe": ["etf", "etf"], "status": ["ok", "ok"], "reason": [None, None], "n_combos": [40, 40]})
TRIALS = pd.DataFrame({"cell_id": ["momo@etf"] * 5, "trial": range(5), "sharpe": [0.1, 0.2, 0.3, 0.4, 0.5]})


class Store(NamedTuple):
    db: Path
    root: Path
    settings: Settings


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    return build_fixture_warehouse(tmp_path_factory.mktemp("wh") / "wh.duckdb")


@pytest.fixture
def store(tmp_path, warehouse):
    return Store(tmp_path / "jobs.sqlite", tmp_path / "artifacts",
                 Settings(warehouse_path=warehouse, data_dir=tmp_path))


def publish(s, kind, tables, **kw):
    kw.setdefault("finished", NOW - HOUR)
    return publish_artifact(s.db, s.root, kind, tables, **kw)


def nodes(g):
    return {n["id"]: n for n in g["nodes"]}


# ------------------------------------------------------------------ the graph, end to end
def test_an_empty_store_gives_missing_products(store):
    connect(store.db).close()                                  # a jobs database with no artifacts in it
    g = build_graph(load_inputs(store.db, store.settings, NOW))
    by = nodes(g)
    for kind in PROD:
        assert by[f"p:{kind}"]["status"] == "missing" and by[f"p:{kind}"]["why"]
    assert len([n for n in g["nodes"] if n["k"] == "strategy"]) == 23 and "book" in by


def test_a_fresh_host_with_no_jobs_database_gives_every_product_missing(store):
    assert not store.db.exists()
    inp = load_inputs(store.db, store.settings, NOW)
    assert not store.db.exists()                               # reading the machine never creates the store
    assert all(p.manifest is None and p.why for p in inp.products.values())
    assert set(inp.products) == set(PROD) and inp.why["jobs"]
    by = nodes(build_graph(inp))
    assert all(by[f"p:{k}"]["status"] == "missing" for k in PROD)


def test_published_artifacts_are_read(store):
    publish(store, "cov.league", {"league": LEAGUE}, verdict="DESCRIPTIVE ONLY")
    g = build_graph(load_inputs(store.db, store.settings, NOW))
    p = nodes(g)["p:cov.league"]
    assert "status" not in p and p["m"]["verdict"] == "DESCRIPTIVE ONLY" and p["m"]["stale"] is False
    assert [e[0] for e in p["m"]["lens"]["estimators"]] == ["sample", "ledoit_wolf"]


def test_the_farms_trials_are_counted_not_read(store):
    publish(store, "farm.sweep", {"leaderboard": BOARD, "cells": CELLS, "trials": TRIALS})
    products = load_products(store.db, NOW)
    farm = products["farm.sweep"]
    assert farm.rows == {"leaderboard": 2, "cells": 2, "trials": 5} and farm.why is None
    assert set(farm.tables) == {"leaderboard", "cells"}        # trials is only counted
    g = build_graph(load_inputs(store.db, store.settings, NOW))
    assert nodes(g)["p:farm.sweep"]["m"]["lens"]["trials"] == 5
    assert {"c:momo@etf", "c:revert@etf"} <= set(nodes(g))


def test_only_the_tables_the_graph_reads_are_loaded(store):
    other = pd.DataFrame({"x": [1, 2, 3]})
    publish(store, "risk.mc_atlas", {"summary": pd.DataFrame({"alpha": [0.99]}), "members": pd.DataFrame({"ticker": ["SPY"]}),
                                     "paths": other})
    atlas = load_products(store.db, NOW)["risk.mc_atlas"]
    assert set(atlas.tables) == {"summary", "members"} and atlas.rows == {"summary": 1, "members": 1, "paths": 3}


def test_the_tables_each_kind_needs(store):
    wanted = {k: load.wanted(k) for k in PROD}
    assert all(set(LENS_TABLES.get(k, ())) <= set(w) for k, w in wanted.items())
    assert set(wanted["risk.mc_atlas"]) == {"summary", "members"} and set(wanted["farm.sweep"]) == {"leaderboard", "cells"}
    assert wanted["desk.report"] == ("book_summary",) and wanted["ops.data_integrity"] == ()
    assert len(wanted["risk.mc_atlas"]) == 2                    # no table twice


def test_the_book_summary_reaches_the_graph(store):
    row = {"as_of": "2026-06-02T14:31:00Z", "positions": 5, "kill_switch": "armed", "trips_on": json.dumps(["dd-cap"]),
           "limits": json.dumps([{"name": "dd-cap", "kind": "drawdown", "scope": "portfolio"}])}
    publish(store, "desk.report", {"book_summary": pd.DataFrame([row], columns=BOOK_COLUMNS)})
    by = nodes(build_graph(load_inputs(store.db, store.settings, NOW)))
    assert by["kill"]["m"]["state"] == "armed" and "lim:dd-cap" in by and by["book"]["m"]["positions"] == 5


def test_an_old_artifact_is_stale(store):
    publish(store, "cov.league", {"league": LEAGUE}, finished=NOW - timedelta(days=30))   # weekly: fires were missed
    assert load_products(store.db, NOW)["cov.league"].stale is True


# ------------------------------------------------------------------ stale: a scheduled fire was missed (a normal week)
def ny(day, hour, minute=0):
    """New York wall time in the week of Monday 2 November 2026 (day 2), and the Friday before it (30 October)."""
    return datetime(2026, 10 if day == 30 else 11, day, hour, minute, tzinfo=ET)


def missed(kind, finished, now):
    return missed_fire(kind, iso(finished), now, load_schedules())


def test_a_weekend_is_not_a_missed_fire():
    friday_last = ny(30, 20, 12)                                       # the overlay's last fire of the week is 20:10
    assert not missed("signals.overlay", friday_last, ny(1, 12))        # Sunday noon: nothing was due
    assert not missed("risk.mc_intraday", ny(30, 15, 46), ny(1, 12))
    assert not missed("desk.report", ny(30, 16, 31), ny(1, 12))
    assert not missed("signals.overlay", friday_last, ny(2, 9, 30))     # Monday's first fire is 9:20: still in its grace


def test_a_quiet_weekday_evening_is_not_a_missed_fire():
    assert not missed("risk.mc_intraday", ny(2, 15, 46), ny(2, 19))     # 35 m after it, yet its next fire is tomorrow


def test_a_missed_night_is_a_missed_fire_by_the_next_morning():
    friday = ny(30, 23, 40)                                             # the farm fires at 23:00 on weekdays
    assert not missed("farm.sweep", friday, ny(2, 23, 30))              # Monday night: in its grace
    assert missed("farm.sweep", friday, ny(3, 12))                      # Tuesday, and Monday night's run never came


def test_a_missed_weekday_report_is_a_missed_fire():
    friday = ny(30, 16, 31)
    assert not missed("desk.report", friday, ny(2, 18, 29))             # 16:30 plus two hours
    assert missed("desk.report", friday, ny(2, 18, 31))
    assert missed("desk.report", ny(30, 16, 31), ny(3, 9))


def test_the_earliest_entry_of_a_kind_decides():
    assert missed("risk.mc_intraday", ny(30, 15, 46), ny(2, 11, 46))    # 9:45's entry, not the 10:00-15:45 one
    assert not missed("risk.mc_intraday", ny(30, 15, 46), ny(2, 11, 44))


@pytest.mark.parametrize("finished", [None, 7, "", "not a time", "2026-10-30T12:00:00", "0001-01-01T00:00:00+00:00",
                                      "9999-12-31T23:59:59+00:00", "9999-12-31T02:00:00+00:00"])
def test_a_time_that_does_not_read_is_not_stale(finished):
    assert missed_fire("desk.report", finished, ny(3, 9), load_schedules()) is False


def test_a_kind_with_no_schedule_entry_is_never_stale():
    assert not missed("api.anything", NOW - timedelta(days=400), NOW)


def test_the_loader_uses_the_schedule_and_not_max_age(store):
    publish(store, "signals.overlay", {"overlay": pd.DataFrame({"x": [1]})}, finished=ny(30, 20, 12))   # max_age is 35 m
    assert load_products(store.db, ny(1, 12))["signals.overlay"].stale is False
    assert load_products(store.db, ny(3, 12))["signals.overlay"].stale is True


def book_row():
    return pd.DataFrame([{"as_of": iso(ny(30, 16, 30)), "positions": 5, "kill_switch": "armed", "trips_on": json.dumps([]),
                          "limits": json.dumps([{"name": "dd-cap", "kind": "drawdown", "scope": "portfolio"}])}],
                        columns=BOOK_COLUMNS)


def test_the_book_is_shown_all_weekend_and_withheld_when_a_report_is_missed(store):
    publish(store, "desk.report", {"book_summary": book_row()}, finished=ny(30, 16, 31))    # max_age is 30 h
    weekend = nodes(build_graph(load_inputs(store.db, store.settings, ny(2, 12))))
    assert weekend["book"]["m"]["positions"] == 5 and "kill" in weekend
    missed_one = nodes(build_graph(load_inputs(store.db, store.settings, ny(3, 12))))
    assert "kill" not in missed_one and "positions" not in missed_one["book"]["m"]
    assert missed_one["book"]["m"]["summary_why"] == (f"The last desk report is from {iso(ny(30, 16, 31))}, "
                                                      "and a scheduled one has been missed since.")


# ------------------------------------------------------------------ what is read from disk is untrusted
def test_a_malformed_manifest_degrades_only_its_own_node(store):
    publish(store, "cov.league", {"league": LEAGUE})
    _, bad = publish(store, "regime.hmm", {"probs": pd.DataFrame({"date": ["2026-05-30"], "p_high": [0.2]})})
    (bad / "manifest.json").write_text("{not json")
    products = load_products(store.db, NOW)
    assert products["regime.hmm"].manifest is None and products["regime.hmm"].why == load.RECORD_UNREADABLE
    assert products["cov.league"].manifest is not None
    assert nodes(build_graph(load_inputs(store.db, store.settings, NOW)))["p:regime.hmm"]["status"] == "missing"


@pytest.mark.parametrize("text", ["[1, 2]", '"a string"', "null", "[" * 100_000])
def test_a_manifest_that_is_not_a_mapping_is_no_manifest(store, text):
    _, d = publish(store, "cov.league", {"league": LEAGUE})
    (d / "manifest.json").write_text(text)
    p = load_products(store.db, NOW)["cov.league"]
    assert p.manifest is None and p.why and not p.tables


def test_a_row_that_leaves_the_root_is_absent(store, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "01ESCAPE"
    elsewhere.mkdir(parents=True)
    (elsewhere / "manifest.json").write_text(json.dumps({"id": "01ESCAPE", "kind": "cov.league", "tables": ["league"]}))
    LEAGUE.to_parquet(elsewhere / "league.parquet", index=False)
    with closing(connect(store.db)) as conn:
        conn.execute("INSERT INTO artifacts (id, kind, params_hash, finished_at, path, data_asof) VALUES (?,?,?,?,?,?)",
                     ("01ESCAPE", "cov.league", "h" * 64, iso(NOW - HOUR), str(elsewhere), None))
    p = load_products(store.db, NOW)["cov.league"]
    assert p.manifest is None and not p.tables


def test_a_table_that_leaves_the_root_is_not_read(store, tmp_path, caplog):
    _, d = publish(store, "cov.league", {"league": LEAGUE})
    outside = tmp_path / "outside.parquet"
    LEAGUE.to_parquet(outside, index=False)
    (d / "league.parquet").unlink()
    (d / "league.parquet").symlink_to(outside)
    with caplog.at_level("WARNING", logger="ohcamel_quant.machine.load"):
        p = load_products(store.db, NOW)["cov.league"]
    assert p.manifest is not None and "league" not in p.tables and "league" not in p.rows
    assert p.why == load.PART_UNREADABLE and "league lies outside" in caplog.text    # the page's words, the log's detail


def test_an_unreadable_table_costs_only_that_table(store):
    _, d = publish(store, "farm.sweep", {"leaderboard": BOARD, "cells": CELLS, "trials": TRIALS})
    (d / "leaderboard.parquet").write_bytes(b"this is not parquet")
    farm = load_products(store.db, NOW)["farm.sweep"]
    assert set(farm.tables) == {"cells"} and farm.rows == {"cells": 2, "trials": 5}
    assert farm.why == load.PART_UNREADABLE
    node = nodes(build_graph(load_inputs(store.db, store.settings, NOW)))["p:farm.sweep"]
    assert "status" not in node and node["why"] == load.PART_UNREADABLE and node["m"]["lens_why"] == LENS_WHY


@pytest.mark.parametrize("art_id", [["a"], {"a": 1}, 7, None, "01NOSUCHARTIFACT"])
def test_a_manifest_naming_no_artifact_has_no_tables(store, art_id):
    _, d = publish(store, "cov.league", {"league": LEAGUE})
    manifest = json.loads((d / "manifest.json").read_text())
    manifest["id"] = art_id
    (d / "manifest.json").write_text(json.dumps(manifest))
    p = load_products(store.db, NOW)["cov.league"]
    assert p.manifest is not None and not p.tables and p.why == load.FILES_GONE


def test_a_table_the_manifest_does_not_list_is_missing_not_guessed(store, caplog):
    _, d = publish(store, "cov.league", {"league": LEAGUE, "extra": LEAGUE})
    manifest = json.loads((d / "manifest.json").read_text())
    manifest["tables"] = ["extra"]                              # league.parquet is on disk but the manifest omits it
    (d / "manifest.json").write_text(json.dumps(manifest))
    with caplog.at_level("WARNING", logger="ohcamel_quant.machine.load"):
        p = load_products(store.db, NOW)["cov.league"]
    assert not p.tables and p.why == load.PART_UNREADABLE and "no league table" in caplog.text


def test_a_hostile_table_name_in_the_manifest_is_never_opened(store):
    _, d = publish(store, "cov.league", {"league": LEAGUE})
    manifest = json.loads((d / "manifest.json").read_text())
    manifest["tables"] = ["../../manifest", "League", 7, None, "league"]
    (d / "manifest.json").write_text(json.dumps(manifest))
    p = load_products(store.db, NOW)["cov.league"]
    assert p.rows == {"league": 2} and set(p.tables) == {"league"}


def test_a_runaway_table_is_not_loaded(store, monkeypatch, caplog):
    publish(store, "cov.league", {"league": LEAGUE})
    monkeypatch.setattr(load, "MAX_TABLE_ROWS", 1)
    with caplog.at_level("WARNING", logger="ohcamel_quant.machine.load"):
        p = load_products(store.db, NOW)["cov.league"]
    assert not p.tables and p.rows == {"league": 2} and p.why == load.PART_UNREADABLE and "2 rows" in caplog.text


def test_a_corrupt_jobs_database_gives_every_product_missing(store):
    store.db.write_bytes(b"not a sqlite file at all" * 100)
    inp = load_inputs(store.db, store.settings, NOW)
    assert all(p.manifest is None and p.why for p in inp.products.values()) and inp.why["jobs"]
    assert load_runs(store.db, NOW, {"ops.selftest"}) == []


# ------------------------------------------------------------------ the warehouse
def test_a_locked_warehouse_leaves_the_graph_whole(store, monkeypatch):      # Review Focus 1
    said = load.WAREHOUSE_LOCKED

    def locked(*a, **k):
        raise WarehouseUnavailable(f"warehouse {store.settings.warehouse_path.name}: locked by the other process (writer or reader)")

    monkeypatch.setattr(load, "open_ro", locked)
    inp = load_inputs(store.db, store.settings, NOW)
    assert inp.freshness is None and inp.readouts == {} and inp.why["warehouse"] == said
    assert list(inp.order) == sorted(inp.world.equities)
    by = nodes(build_graph(inp))
    feeds = [n for n in by.values() if n["k"] == "feed"]
    assert feeds and all(n["status"] == "missing" and n["why"] == said for n in feeds)
    assert "d:SPY" in by and "spark" not in by["d:SPY"]["m"]


def test_a_real_lock_keeps_the_authored_sentence_and_not_duckdbs(warehouse, tmp_path, monkeypatch):
    """A lock is recognised by the sentence ``warehouse/db.py`` writes when DuckDB reports one, not by DuckDB's own."""
    real = load.open_ro
    monkeypatch.setattr(load, "open_ro", lambda **kw: real(timeout_s=0, **kw))      # no 2 s of retries

    def busy(*a, **k):
        raise duckdb.IOException(f'IO Error: Could not set lock on file "{warehouse}": Conflicting lock is held in /usr/bin/python3 (PID 7)')

    monkeypatch.setattr(duckdb, "connect", busy)
    _, readouts, _, why = load_warehouse(Settings(warehouse_path=warehouse, data_dir=tmp_path), load_world(), NOW)
    assert readouts == {} and why == {"warehouse": "The warehouse is busy with its nightly update."}


def test_a_warehouse_failure_with_no_authored_words_is_one_fixed_sentence(tmp_path, monkeypatch, caplog):
    raw = 'warehouse wh.duckdb: IO Error: The file "/srv/data/wh.duckdb" exists, but it is not a valid DuckDB database file!'

    def broken(*a, **k):
        raise WarehouseUnavailable(raw)

    monkeypatch.setattr(load, "open_ro", broken)
    with caplog.at_level("WARNING", logger="ohcamel_quant.machine.load"):
        _, _, _, why = load_warehouse(Settings(warehouse_path=tmp_path / "wh.duckdb", data_dir=tmp_path), load_world(), NOW)
    assert why == {"warehouse": load.UNREADABLE}
    assert "/srv" not in why["warehouse"] and "/srv/data/wh.duckdb" in caplog.text    # whole, in the server's log only


@pytest.mark.parametrize("how", ["relative", "symlinked"])
def test_a_corrupt_warehouse_file_tells_the_public_only_that_it_could_not_be_read(store, tmp_path, monkeypatch, caplog, how):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "wh.duckdb").write_bytes(b"this is not a duckdb database file at all" * 50)
    if how == "relative":
        monkeypatch.chdir(real_dir)
        path = Path("wh.duckdb")
    else:
        (tmp_path / "link").symlink_to(real_dir, target_is_directory=True)
        path = tmp_path / "link" / "wh.duckdb"
    connect(store.db).close()
    with caplog.at_level("WARNING", logger="ohcamel_quant.machine.load"):
        m = build_machine(store.db, Settings(warehouse_path=path, data_dir=tmp_path), NOW)
    feeds = [n for n in m["nodes"] if n["k"] == "feed"]
    assert feeds and all(n["status"] == "missing" and n["why"] == "The warehouse could not be read." for n in feeds)
    public = json.dumps([m["notes"], [n["why"] for n in feeds]])
    assert "The warehouse could not be read." in m["notes"]
    for private in ("/", "IO Error", "wh.duckdb", "duckdb", "valid"):
        assert private not in public
    assert "IO Error" in caplog.text                                                  # the original is kept, in the log


def test_an_absent_warehouse_at_a_relative_path_has_the_plain_sentence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, _, _, why = load_warehouse(Settings(warehouse_path=Path("deep/dir/wh.duckdb"), data_dir=tmp_path), load_world(), NOW)
    assert why == {"warehouse": "The warehouse has no data yet."}


def test_an_absent_warehouse_is_unavailable_in_plain_words(tmp_path):
    gone = tmp_path / "deep" / "dir" / "wh.duckdb"
    fresh, readouts, order, why = load_warehouse(Settings(warehouse_path=gone, data_dir=tmp_path), load_world(), NOW)
    assert fresh is None and readouts == {} and order == sorted(load_world().equities)
    assert why == {"warehouse": "The warehouse has no data yet."}


def test_an_unconfigured_warehouse_is_unavailable(tmp_path):
    fresh, readouts, _, why = load_warehouse(Settings(warehouse_path=None, data_dir=tmp_path), load_world(), NOW)
    assert fresh is None and readouts == {} and why == {"warehouse": "This host has no warehouse."}


def test_a_warehouse_without_its_tables_is_unavailable_not_a_crash(tmp_path):
    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()
    fresh, readouts, order, why = load_warehouse(Settings(warehouse_path=path, data_dir=tmp_path), load_world(), NOW)
    assert fresh is None and readouts == {} and why["warehouse"] and str(tmp_path) not in why["warehouse"]
    assert order == sorted(load_world().equities)


def test_readouts_come_from_the_fixture_warehouse(store):
    inp = load_inputs(store.db, store.settings, NOW)
    by = nodes(build_graph(inp))
    m = by["d:SPY"]["m"]
    assert {"spark", "ret1y", "vol"} <= set(m) and m["n"] == 253
    assert "warehouse" not in inp.why
    assert inp.freshness["bars_daily"]["data_asof"] == "2026-06-01"
    feed = by["feed:prices"]
    assert "status" not in feed and feed["m"]["asof"] == "2026-06-01"


def test_the_closes_are_one_read_of_the_last_380_days(warehouse, tmp_path, monkeypatch):
    seen, frames = [], []
    real_open, real_closes = load.open_ro, load._closes

    class Spy:
        def __init__(self, con):
            self.con = con

        def execute(self, sql, *args):
            seen.append(sql)
            return self.con.execute(sql, *args)

    @contextmanager
    def spy(*a, **k):
        with real_open(*a, **k) as con:
            yield Spy(con)

    def keep(con, tickers):
        frames.append(real_closes(con, tickers))
        return frames[-1]

    monkeypatch.setattr(load, "open_ro", spy)
    monkeypatch.setattr(load, "_closes", keep)
    _, readouts, order, _ = load_warehouse(Settings(warehouse_path=warehouse, data_dir=tmp_path), load_world(), NOW)
    assert len([s for s in seen if "adj_close" in s]) == 1
    (closes,) = frames
    assert 250 < len(closes) < 270 and closes.index.min() >= pd.Timestamp("2026-06-01") - pd.Timedelta(days=380)
    assert closes.index.is_monotonic_increasing and {"SPY", "QQQ"} <= set(closes.columns)   # not the whole decade
    assert readouts["SPY"]["n"] == 253 and sorted(order) == sorted(load_world().equities)


def test_a_warehouse_with_no_bars_gives_no_readouts_and_alphabetical_equities(tmp_path):
    from ohcamel_quant.warehouse.db import open_rw

    path = tmp_path / "bare.duckdb"
    with open_rw(path):
        pass                                                    # migrated, no rows
    fresh, readouts, order, why = load_warehouse(Settings(warehouse_path=path, data_dir=tmp_path), load_world(), NOW)
    assert fresh is not None and readouts == {} and order == sorted(load_world().equities) and not why


# ------------------------------------------------------------------ the day's runs
def test_runs_are_the_last_day_and_the_live(store):
    publish(store, "ops.selftest", {"summary": pd.DataFrame({"x": [1]})}, finished=NOW - 30 * HOUR)   # too old
    publish(store, "cov.league", {"league": LEAGUE}, finished=NOW - 2 * HOUR)
    with closing(connect(store.db)) as conn:
        enqueue(conn, kind="risk.mc_atlas", params={"p": 1}, priority=2, mem_class="S", heavy=False,
                submitted_by="schedule:test", now=NOW - HOUR)
        job = claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=NOW - HOUR)
        set_progress(conn, job.id, 0.4, "paths", now=NOW - HOUR / 2)
    kinds = {"ops.selftest", "cov.league", "risk.mc_atlas"}
    runs = load_runs(store.db, NOW, kinds)
    assert [r["state"] for r in runs] == ["done", "running"]
    assert [r["kind"] for r in runs] == ["cov.league", "risk.mc_atlas"]
    done, running = runs
    assert running["progress"] == 0.4 and running["msg"] == "paths" and running["end"] is None
    assert running["start"] == iso(NOW - HOUR) and done["end"] == iso(NOW - 2 * HOUR) and done["cpu"] == 1.0
    assert done["progress"] == 1.0                              # a finished job is whole, as the job stream reports it


def test_a_run_is_exactly_the_wire_shape(store):
    publish(store, "cov.league", {"league": LEAGUE}, finished=NOW - HOUR)
    (run,) = load_runs(store.db, NOW, {"cov.league"})
    assert set(run) == {"id", "kind", "state", "start", "end", "cpu", "progress", "msg"}
    assert run["state"] == "done" and run["msg"] is None


def test_a_running_job_stays_in_however_long_it_has_run(store):
    with closing(connect(store.db)) as conn:
        enqueue(conn, kind="farm.sweep", params={}, priority=2, mem_class="L", heavy=True,
                submitted_by="schedule:test", now=NOW - 40 * HOUR)
        claim_next(conn, allowed_classes=frozenset({"L"}), allow_heavy=True, now=NOW - 39 * HOUR)
    assert [r["state"] for r in load_runs(store.db, NOW, {"farm.sweep"})] == ["running"]


def test_a_queued_job_is_listed_with_no_start(store):
    with closing(connect(store.db)) as conn:
        enqueue(conn, kind="farm.sweep", params={}, priority=2, mem_class="L", heavy=True,
                submitted_by="schedule:test", now=NOW - 3 * HOUR)
    (run,) = load_runs(store.db, NOW, {"farm.sweep"})
    assert (run["state"], run["start"], run["end"], run["progress"]) == ("queued", None, None, None)


def test_runs_are_ordered_by_start_and_only_for_the_kinds_given(store):
    with closing(connect(store.db)) as conn:
        for i, (kind, hours) in enumerate([("risk.mc_atlas", 5), ("cov.league", 9), ("ops.selftest", 7), ("api.heavy", 1)]):
            enqueue(conn, kind=kind, params={"i": i}, priority=2, mem_class="S", heavy=False,
                    submitted_by="api" if kind.startswith("api.") else "schedule:test", now=NOW - hours * HOUR)
            claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=NOW - hours * HOUR + timedelta(seconds=1))
    runs = load_runs(store.db, NOW, {"risk.mc_atlas", "cov.league", "ops.selftest"})
    assert [r["kind"] for r in runs] == ["cov.league", "ops.selftest", "risk.mc_atlas"]   # api.heavy is not the clock's
    assert load_runs(store.db, NOW, set()) == []


def test_a_failed_run_never_carries_its_error_or_params(store):
    with closing(connect(store.db)) as conn:
        enqueue(conn, kind="cov.league", params={"secret_param": "hunter2"}, priority=2, mem_class="S", heavy=False,
                submitted_by="schedule:test", now=NOW - 2 * HOUR)
        job = claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=NOW - 2 * HOUR)
        fail(conn, job.id, error="Traceback: /data/artifacts/boom SECRETERROR", retries=0, cpu_seconds=2.5,
             peak_rss_bytes=1, now=NOW - HOUR)
    (run,) = load_runs(store.db, NOW, {"cov.league"})
    assert run["state"] == "failed" and run["cpu"] == 2.5
    assert "SECRETERROR" not in json.dumps(run) and "hunter2" not in json.dumps(run)


def test_runs_without_a_jobs_database_are_none_and_create_nothing(store):
    assert load_runs(store.db, NOW, {"cov.league"}) == [] and not store.db.exists()


def test_a_database_lock_does_not_take_the_page_down(store, monkeypatch):
    publish(store, "cov.league", {"league": LEAGUE})

    def locked(path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(load, "connect", locked)
    assert load_runs(store.db, NOW, {"cov.league"}) == []
    inp = load_inputs(store.db, store.settings, NOW)
    assert all(p.manifest is None and p.why for p in inp.products.values()) and inp.why["jobs"]


# ------------------------------------------------------------------ the clock and the roster
def test_the_clocks_day_is_new_yorks(store, monkeypatch):
    seen = []
    real = load.job_clock
    monkeypatch.setattr(load, "job_clock", lambda schedules, day: seen.append(day) or real(schedules, day))
    load_inputs(store.db, store.settings, datetime(2026, 10, 30, 2, 0, tzinfo=UTC))     # 22:00 on the 29th in New York
    load_inputs(store.db, store.settings, datetime(2026, 10, 29, 15, 0, tzinfo=UTC))
    assert seen == [date(2026, 10, 29), date(2026, 10, 29)]


def test_a_naive_time_is_refused_not_read_as_local(store, tmp_path):
    bare = Settings(warehouse_path=None, data_dir=tmp_path)    # no warehouse, so no freshness check can refuse it first
    with pytest.raises(ValueError, match="timezone"):
        load_inputs(store.db, bare, datetime(2026, 6, 2, 15, 0))


def test_the_inputs_are_the_repos_own_config(store):
    inp = load_inputs(store.db, store.settings, NOW)
    assert len(inp.roster.cells) == 23 and "farm.sweep" in inp.clock and inp.world.equities
    assert set(inp.products) == set(PROD)


# ------------------------------------------------------------------ the cache
class Counted:
    """A builder that counts its calls, each with the time it was asked to build for."""

    def __init__(self, pause: float = 0.0, fail_first: bool = False) -> None:
        self.calls: list[datetime] = []
        self.pause, self.fail_first = pause, fail_first

    def __call__(self, now: datetime) -> dict:
        self.calls.append(now)
        time.sleep(self.pause)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("the first build fails")
        return {"built": len(self.calls)}


def run_now(refresh):
    """A cache's ``spawn`` for a test that wants to see a refresh's result: it runs there and then."""
    refresh()


def now_cache(build, mark=lambda: None, **kw):
    return MachineCache(build, mark, spawn=run_now, **kw)


def test_the_watermark_is_the_newest_artifact_and_creates_nothing(store):
    assert watermark(store.db) is None and not store.db.exists()       # no jobs database: nothing, and none made
    connect(store.db).close()
    assert watermark(store.db) is None                                  # a store with no artifact yet
    publish(store, "cov.league", {"league": LEAGUE}, finished=NOW - 2 * HOUR)
    assert watermark(store.db) == iso(NOW - 2 * HOUR)
    publish(store, "regime.hmm", {"probs": pd.DataFrame({"date": ["2026-05-30"], "p_high": [0.2]})}, finished=NOW - HOUR)
    assert watermark(store.db) == iso(NOW - HOUR)


def test_the_watermark_of_a_corrupt_store_is_none_not_a_crash(store):
    store.db.write_bytes(b"not a sqlite file at all" * 100)
    assert watermark(store.db) is None


def test_a_public_job_does_not_move_the_watermark(store):
    publish(store, "cov.league", {"league": LEAGUE}, finished=NOW - 2 * HOUR)
    publish(store, "api.heavy", {"t": pd.DataFrame({"x": [1]})}, finished=NOW - HOUR)     # any visitor's job
    assert watermark(store.db) == iso(NOW - 2 * HOUR)
    publish(store, "ops.selftest", {"t": pd.DataFrame({"x": [1]})}, finished=NOW - HOUR)  # a job the graph draws no product for
    assert watermark(store.db) == iso(NOW - 2 * HOUR)


def test_it_builds_once_per_ttl_and_on_a_new_artifact(store):
    build = Counted()
    cache = now_cache(build, lambda: watermark(store.db))
    first = cache.get(NOW)
    assert cache.get(NOW + timedelta(seconds=1)) is first and len(build.calls) == 1   # two gets, one build
    publish(store, "cov.league", {"league": LEAGUE}, finished=NOW)                    # the watermark moves
    assert cache.get(NOW + timedelta(seconds=2)) is not first and len(build.calls) == 2
    cache.get(NOW + timedelta(seconds=3))
    assert len(build.calls) == 2
    cache.get(NOW + timedelta(seconds=2, minutes=6))                                   # past the five minutes
    assert len(build.calls) == 3


def test_the_ttl_is_five_minutes_to_the_second():
    build = Counted()
    cache = now_cache(build)
    cache.get(NOW)
    cache.get(NOW + timedelta(seconds=299.9))
    assert len(build.calls) == 1
    cache.get(NOW + timedelta(seconds=300))
    assert len(build.calls) == 2
    cache.get(NOW + timedelta(seconds=300 + 299.9))                                    # the TTL runs from the new build
    assert len(build.calls) == 2


def test_a_clock_that_went_backwards_rebuilds_rather_than_serving_a_future_build():
    build = Counted()
    cache = now_cache(build)
    cache.get(NOW)
    cache.get(NOW - timedelta(hours=3))
    assert len(build.calls) == 2


def test_a_clock_a_little_behind_the_build_is_handed_it():
    build = Counted()
    cache = now_cache(build)
    cache.get(NOW)
    cache.get(NOW - timedelta(microseconds=100))
    cache.get(NOW - timedelta(seconds=59))
    assert len(build.calls) == 1
    cache.get(NOW - timedelta(seconds=61))                      # past the skew: a clock that was stepped back
    assert len(build.calls) == 2


def test_waiters_whose_clocks_read_earlier_are_handed_the_build_they_waited_for():
    """Each request takes its ``now`` before it waits for the lock, so lock order is not clock order."""
    build = Counted(pause=0.05)
    cache = MachineCache(build, lambda: None)
    nows = [NOW + timedelta(milliseconds=ms) for ms in (3, 2, 1, 0)]      # the latest clock wins the lock, the earlier wait
    got, threads = [], []
    for now in nows:
        t = threading.Thread(target=lambda now=now: got.append(cache.get(now)))
        t.start()
        threads.append(t)
        time.sleep(0.005)
    for t in threads:
        t.join(timeout=10)
    assert build.calls == [nows[0]] and len(got) == 4 and all(g is got[0] for g in got)


def test_an_artifact_that_lands_during_a_build_is_not_hidden_by_it():
    """The mark is read before the build, so an artifact published while the graph is built shows at the next get."""
    store = {"mark": "a"}
    calls = []

    def build(now):
        calls.append(now)
        store["mark"] = "b"                                      # the artifact lands mid-build, after the graph read "a"
        return {}

    cache = now_cache(build, lambda: store["mark"])
    cache.get(NOW)
    cache.get(NOW)
    assert len(calls) == 2
    cache.get(NOW)
    assert len(calls) == 2                                       # and then it is settled


def test_two_first_requests_build_once():
    build = Counted(pause=0.2)
    cache = MachineCache(build, lambda: None)
    go, got = threading.Barrier(4), []

    def request():
        go.wait()
        got.append(cache.get(NOW))

    threads = [threading.Thread(target=request) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(build.calls) == 1 and len(got) == 4 and all(g is got[0] for g in got)


def test_a_failed_first_build_goes_to_the_caller_and_is_not_kept():
    build = Counted(fail_first=True)
    cache = MachineCache(build, lambda: None)
    with pytest.raises(RuntimeError):
        cache.get(NOW)
    assert cache.get(NOW) == {"built": 2}                        # the next request tries again, and succeeds
    assert cache.get(NOW) == {"built": 2} and len(build.calls) == 2


class Gated:
    """A build that waits at a gate from its ``hold``-th call on, and says when it has got there."""

    def __init__(self, hold=2):
        self.calls, self.hold, self.gate, self.there = [], hold, threading.Event(), threading.Event()

    def __call__(self, now):
        self.calls.append(now)
        if len(self.calls) >= self.hold:
            self.there.set()
            assert self.gate.wait(10)
        return {"built": len(self.calls)}


def threaded(threads):
    """A cache's ``spawn`` that runs on a thread and keeps it, so that a test can wait for it."""
    def spawn(refresh):
        threads.append(threading.Thread(target=refresh))
        threads[-1].start()
    return spawn


def test_a_refresh_runs_behind_the_old_build_and_only_one_runs():
    build, threads = Gated(), []
    cache = MachineCache(build, lambda: None, spawn=threaded(threads))
    first = cache.get(NOW)
    later = NOW + timedelta(minutes=6)
    assert cache.get(later) is first                             # expired: answered at once, and a refresh started
    assert build.there.wait(10)
    assert cache.get(later + timedelta(seconds=1)) is first      # it is still running: served the same, and no second one
    assert len(build.calls) == 2 and len(threads) == 1
    build.gate.set()
    threads[0].join(10)
    assert cache.get(later + timedelta(seconds=2)) == {"built": 2} and len(build.calls) == 2


def test_the_first_request_waits_for_the_build_a_warm_started():
    build, threads, got = Gated(hold=1), [], []
    cache = MachineCache(build, lambda: None, spawn=threaded(threads))
    cache.refresh(NOW)                                           # returns while the build sits at its gate
    assert build.there.wait(10)
    visitor = threading.Thread(target=lambda: got.append(cache.get(NOW)))
    visitor.start()
    time.sleep(0.05)
    assert got == []                                             # there is nothing to serve, so it waits
    build.gate.set()
    visitor.join(10)
    threads[0].join(10)
    assert got == [{"built": 1}] and len(build.calls) == 1


def test_a_failed_refresh_keeps_the_old_build_logs_and_backs_off_for_a_minute(caplog):
    outcomes = iter([{"built": 1}, RuntimeError("the second fails"), {"built": 3}])
    calls = []

    def build(now):
        calls.append(now)
        got = next(outcomes)
        if isinstance(got, Exception):
            raise got
        return got

    cache = now_cache(build)
    first = cache.get(NOW)
    failed = NOW + timedelta(seconds=301)
    with caplog.at_level("ERROR", logger="ohcamel_quant.machine.service"):
        assert cache.get(failed) is first and len(calls) == 2
    assert "the last good build stays" in caplog.text and "the second fails" in caplog.text
    assert cache.get(failed + timedelta(seconds=59)) is first and len(calls) == 2    # no retry for a minute
    assert cache.get(failed + timedelta(seconds=60)) == {"built": 3} and len(calls) == 3


def test_a_refresh_landing_during_the_backoff_check_does_not_raise():
    class Racing(MachineCache):
        """A refresh on another thread lands, clearing ``_retry_at``, just after the check first reads it."""

        @property
        def _retry_at(self):
            value, self._stored = self._stored, None
            return value

        @_retry_at.setter
        def _retry_at(self, value):
            self._stored = value

    cache = Racing(Counted(), lambda: None, spawn=run_now)
    cache._retry_at = NOW + timedelta(seconds=30)
    assert cache._backing_off(NOW) is True


def test_a_refresh_that_cannot_start_a_thread_does_not_wedge_the_cache():
    build = Counted()
    tries = [RuntimeError("can't start new thread"), None]

    def spawn(refresh):
        if (e := tries.pop(0)) is not None:
            raise e
        refresh()

    cache = MachineCache(build, lambda: None, spawn=spawn)
    first = cache.get(NOW)
    assert cache.get(NOW + timedelta(minutes=6)) is first and len(build.calls) == 1
    assert cache.get(NOW + timedelta(minutes=6)) == {"built": 2}


def test_a_degraded_build_lasts_a_minute_and_a_whole_one_five():
    feeds = [{"k": "feed", "status": "missing"}, {"k": "feed", "status": "missing"}]
    outcomes = iter([{"nodes": feeds}, {"nodes": [{"k": "feed"}]}, {"nodes": [{"k": "feed"}, {"k": "n"}]}])
    calls = []

    def build(now):
        calls.append(now)
        return next(outcomes)

    cache = now_cache(build, degraded=degraded)
    cache.get(NOW)
    cache.get(NOW + timedelta(seconds=59))
    assert len(calls) == 1
    cache.get(NOW + timedelta(seconds=60))                       # the warehouse has come back
    assert len(calls) == 2
    cache.get(NOW + timedelta(seconds=60 + 299))
    assert len(calls) == 2
    cache.get(NOW + timedelta(seconds=60 + 300))
    assert len(calls) == 3


def test_a_locked_warehouse_makes_a_degraded_build_and_a_whole_one_does_not(store, monkeypatch):
    connect(store.db).close()
    assert not degraded(build_machine(store.db, store.settings, NOW))

    def locked(*a, **k):
        raise WarehouseUnavailable(f"warehouse {store.settings.warehouse_path.name}: locked by the other process (writer or reader)")

    monkeypatch.setattr(load, "open_ro", locked)
    assert degraded(build_machine(store.db, store.settings, NOW))


def test_the_process_cache_knows_what_a_degraded_build_is(monkeypatch, tmp_path):
    monkeypatch.setattr(machine_router, "jobs_db_path", lambda: tmp_path / "jobs.sqlite")
    assert machine_router.new_machine_cache()._degraded is degraded


# ------------------------------------------------------------------ warming at startup
class Warmable:
    def __init__(self, raises=False):
        self.at, self.raises = [], raises

    def refresh(self, now):
        self.at.append(now)
        if self.raises:
            raise RuntimeError("no warm for you")


def test_warming_starts_the_process_caches_build(monkeypatch):
    cache = Warmable()
    monkeypatch.setattr(machine_router, "get_settings", lambda: Settings(offline=False))
    monkeypatch.setattr(machine_router, "get_machine_cache", lambda: cache)
    monkeypatch.setattr(machine_router, "utcnow", lambda: NOW)
    machine_router.warm_machine()
    assert cache.at == [NOW]


def test_warming_never_fails_the_app(monkeypatch, caplog):
    monkeypatch.setattr(machine_router, "get_settings", lambda: Settings(offline=False))
    monkeypatch.setattr(machine_router, "get_machine_cache", lambda: Warmable(raises=True))
    with caplog.at_level("ERROR", logger="ohcamel_quant.api.machine"):
        machine_router.warm_machine()
    assert "could not be warmed" in caplog.text


def test_tests_and_laptops_do_not_warm(monkeypatch):
    cache = Warmable()
    monkeypatch.setattr(machine_router, "get_machine_cache", lambda: cache)
    machine_router.warm_machine()                                # this suite runs offline
    assert cache.at == []


def test_the_app_warms_the_machine_as_it_starts(monkeypatch):
    warmed = []
    monkeypatch.setattr(machine_router, "warm_machine", lambda: warmed.append(True))
    assert warmed == []                                          # building the app reads nothing
    with TestClient(create_app()):
        assert warmed == [True]


# ------------------------------------------------------------------ build_machine
def test_the_machine_is_the_graph_with_its_extras_provenance_and_notes(store):
    m = build_machine(store.db, store.settings, NOW)
    assert set(m) == {"asof", "nodes", "edges", "x", "provenance", "notes"}
    assert m["asof"] == iso(NOW)
    assert m["x"]["fires"] and all(isinstance(k, str) and isinstance(h, float) for k, h in m["x"]["fires"])
    (p,) = m["provenance"]
    assert p["source"] == "ohcamel-machine" and p["detail"] == {"store": "jobs.sqlite+warehouse"} and p["synthetic"] is False
    direct = build_graph(load_inputs(store.db, store.settings, NOW))
    assert m["nodes"] == direct["nodes"] and m["edges"] == direct["edges"]


def test_the_notes_say_in_words_what_is_missing(store):
    # a fresh host: no jobs database, a warehouse, and no desk report. The notes say the first and the last
    notes = build_machine(store.db, store.settings, NOW)["notes"]
    assert notes == ["The job system has not started on this host yet.",
                     "The nightly desk report that carries the book's limits has not published them yet."]


def test_the_notes_are_empty_when_nothing_is_missing(store):
    publish(store, "desk.report", {"book_summary": pd.DataFrame([{
        "as_of": "2026-06-02T14:31:00Z", "positions": 5, "kill_switch": "armed", "trips_on": json.dumps([]),
        "limits": json.dumps([{"name": "dd-cap", "kind": "drawdown", "scope": "portfolio"}])}], columns=BOOK_COLUMNS)})
    assert build_machine(store.db, store.settings, NOW)["notes"] == []


def test_the_notes_carry_the_warehouse_reason_without_its_path(store, tmp_path):
    gone = Settings(warehouse_path=tmp_path / "deep" / "dir" / "wh.duckdb", data_dir=tmp_path)
    connect(store.db).close()
    notes = build_machine(store.db, gone, NOW)["notes"]
    assert "The warehouse has no data yet." in notes and str(tmp_path) not in json.dumps(notes)


def test_a_note_is_a_sentence():
    assert sentence("locked") == "Locked."
    assert sentence("  the jobs database could not be read ") == "The jobs database could not be read."
    assert sentence("desk.report has published no artifact") == "desk.report has published no artifact."   # a name keeps its case
    assert sentence("book_summary has no row: the engine was away") == "book_summary has no row: the engine was away."
    assert sentence("Already whole.") == "Already whole."


# ------------------------------------------------------------------ GET /api/machine
class Served(NamedTuple):
    client: TestClient
    cache: MachineCache
    builds: list       # the time of every build
    clock: list        # [now]: the endpoint's time, which a test may move


def cache_for(s: Store, builds: list) -> MachineCache:
    def build(now):
        builds.append(now)
        return build_machine(s.db, s.settings, now)

    return MachineCache(build, lambda: watermark(s.db), spawn=run_now)


def serve(s: Store, monkeypatch) -> Served:
    """The real app, with its cache and its jobs database pointed at ``s`` and its clock in the test's hand."""
    clock, builds = [NOW], []
    monkeypatch.setattr(machine_router, "utcnow", lambda: clock[0])
    cache = cache_for(s, builds)
    app = create_app()
    app.dependency_overrides[machine_router.get_machine_db_path] = lambda: s.db
    app.dependency_overrides[machine_router.get_machine_cache] = lambda: cache
    return Served(TestClient(app), cache, builds, clock)


@pytest.fixture
def served(store, monkeypatch):
    return serve(store, monkeypatch)


def test_the_response_schema(served, store):
    publish(store, "cov.league", {"league": LEAGUE})
    r = served.client.get("/api/machine")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert set(body) == {"asof", "nodes", "edges", "runs", "x", "provenance", "notes"}
    assert body["asof"] == iso(NOW)
    assert [(r["kind"], r["state"]) for r in body["runs"]] == [("cov.league", "done")]    # the job that published it
    assert set(body["runs"][0]) == {"id", "kind", "state", "start", "end", "cpu", "progress", "msg"}
    required = {"id", "k", "l", "n", "ring", "at", "m"}
    assert body["nodes"] and all(required <= set(n) <= required | {"status", "why"} for n in body["nodes"])
    assert len({n["id"] for n in body["nodes"]}) == len(body["nodes"])
    count = len(body["nodes"])
    assert body["edges"] and all(
        len(e) == 3 and type(e[0]) is int and type(e[1]) is int and isinstance(e[2], str) and 0 <= e[0] < count
        and 0 <= e[1] < count for e in body["edges"])
    assert [p["source"] for p in body["provenance"]] == ["ohcamel-machine"]
    assert body["notes"] and all(isinstance(n, str) for n in body["notes"])


def fat_readouts(tickers: list[str]) -> dict[str, dict]:
    """A readout for every ticker at its widest: a 61-value spark (a year of 253 closes gives 52), ``ret1y``,
    ``vol``, ``last``, ``n`` and six peers. Random digits compress worse than a real series does, so this is a
    ceiling on the page's weight, not an average."""
    rng = np.random.default_rng(7)
    out = {}
    for t in tickers:
        walk = np.cumprod(1 + rng.normal(0.0004, 0.012, 60))
        others = [p for p in rng.choice(tickers, 8, replace=False) if p != t][:6]
        out[t] = {"spark": [1.0, *(round(float(x), 4) for x in walk)], "ret1y": round(float(walk[-1] - 1), 4),
                  "vol": round(float(rng.uniform(0.05, 0.6)), 4), "last": round(float(rng.uniform(5, 900)), 2),
                  "n": 253, "peers": [[str(p), round(float(rng.uniform(0.3, 0.95)), 2)] for p in others]}
    return out


def publish_every_lens(s: Store) -> None:
    """One artifact of every kind the graph reads tables from, shaped as the jobs write them."""
    from ohcamel_quant.models.farm import cell_id, farm_cells

    cells = farm_cells()
    ids = [cell_id(st, u) for st, u in cells]
    n = len(ids)
    publish(s, "farm.sweep", {
        "leaderboard": pd.DataFrame({"cell_id": ids, "verdict": (["PASS", "FAIL", "PASS", "WEAK"] * n)[:n],
                                     "detail": "DSR ABOVE FLOOR", "oos_sharpe_ann": np.linspace(1.1, -0.4, n),
                                     "dsr_farm": np.linspace(0.97, 0.1, n), "psr": np.linspace(0.99, 0.2, n),
                                     "pbo": np.linspace(0.1, 0.8, n), "n_combos": 40}),
        "cells": pd.DataFrame({"cell_id": ids, "strategy": [c[0] for c in cells], "universe": [c[1] for c in cells],
                               "status": "ok", "reason": None, "n_combos": 40}),
        "trials": pd.DataFrame({"cell_id": np.repeat(ids, 40), "trial": np.tile(range(40), n), "sharpe": 0.1})})
    publish(s, "vol.forecast_league", {"summary": pd.DataFrame({
        "model": ["har", "garch", "ewma", "naive", "rv5", "egarch"], "mcs_rate": np.linspace(0.8, 0.2, 6),
        "mean_rank": np.linspace(1.5, 5.5, 6), "names": 30})})
    publish(s, "cov.league", {"league": pd.DataFrame({
        "estimator": np.repeat(["sample", "ledoit_wolf", "oas", "shrink_const", "factor"], 3),
        "universe": ["core", "etf", "equity"] * 5, "realized_vol": np.linspace(0.1, 0.13, 15), "rank": range(1, 16)})})
    publish(s, "risk.mc_atlas", {
        "summary": pd.DataFrame({"method": ["fhs", "copula_t"] * 4, "horizon": [1, 1, 5, 5, 10, 10, 21, 21],
                                 "alpha": 0.99, "var": 0.02, "es": 0.03, "var_usd": 2000.0, "es_usd": 3000.0,
                                 "n_paths": 100_000}),
        "members": pd.DataFrame({"ticker": ["SPY", "QQQ", "TLT"]})})
    publish(s, "models.xs_lgbm", {"holdout_deciles": pd.DataFrame({
        "decile": range(1, 11), "mean_next_month_return": np.linspace(-0.01, 0.02, 10)})})
    publish(s, "regime.hmm", {"probs": pd.DataFrame({
        "date": pd.date_range("2015-01-04", periods=600, freq="7D").strftime("%Y-%m-%d"),
        "p_high": np.linspace(0.05, 0.3, 600)})})
    publish(s, "vol.surface_history", {"history": pd.DataFrame({
        "underlying": "SPY", "asof": pd.date_range("2026-05-01", periods=30).strftime("%Y-%m-%d"),
        "atm_iv_30d": np.linspace(0.14, 0.18, 30), "n_slices": 9})})
    publish(s, "desk.report", {"book_summary": pd.DataFrame([{
        "as_of": "2026-06-02T14:31:00Z", "positions": 12, "kill_switch": "armed", "trips_on": json.dumps(["dd-cap"]),
        "limits": json.dumps([{"name": f"lim-{i}", "kind": "weight", "scope": "portfolio"} for i in range(7)]
                             + [{"name": "dd-cap", "kind": "drawdown", "scope": "portfolio"}])}],
        columns=BOOK_COLUMNS)})


def test_the_app_gzips_at_level_six(served, store):
    gzip_layer = next(m for m in create_app().user_middleware if m.cls is GZipMiddleware)
    assert gzip_layer.kwargs["compresslevel"] == 6
    publish_every_lens(store)
    r = served.client.get("/api/machine", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"


def test_it_fits_the_budget(served, store, monkeypatch):
    """The page's whole weight on a realistic book: a readout for every daily ticker of the world (the fixture
    warehouse has a handful of names and would pass any payload) and every lens artifact published."""
    publish_every_lens(store)
    real = load.load_warehouse

    def every_ticker(settings, world, now):
        fresh, _, order, why = real(settings, world, now)
        return fresh, fat_readouts(world.daily()), order, why

    monkeypatch.setattr(load, "load_warehouse", every_ticker)
    r = served.client.get("/api/machine")
    assert r.status_code == 200
    nodes = r.json()["nodes"]
    by = {n["id"]: n for n in nodes}
    daily = [n for n in nodes if n["id"].startswith("d:") and "peers" in n["m"]]
    assert len(daily) >= 500 and all(len(n["m"]["spark"]) == 61 and len(n["m"]["peers"]) == 6 for n in daily)
    assert all("lens" in by[f"p:{kind}"]["m"] for kind in LENS_TABLES)      # every lens artifact was read, none omitted
    assert by["kill"]["m"]["state"] == "armed"
    size = len(gzip.compress(r.content))
    print(f"/api/machine realistic payload: {len(r.content):,} bytes, {size:,} gzipped, {len(nodes)} nodes")
    assert size <= 250_000


def test_runs_are_fresh_inside_the_ttl(served, store):
    first = served.client.get("/api/machine").json()
    assert first["runs"] == [] and len(served.builds) == 1
    with closing(connect(store.db)) as conn:
        enqueue(conn, kind="risk.mc_atlas", params={"p": 1}, priority=2, mem_class="S", heavy=False,
                submitted_by="schedule:test", now=NOW - HOUR)
        claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=NOW - HOUR)
    served.clock[0] = NOW + timedelta(minutes=1)
    second = served.client.get("/api/machine").json()
    assert [(r["kind"], r["state"]) for r in second["runs"]] == [("risk.mc_atlas", "running")]
    assert len(served.builds) == 1                                        # a run is not an artifact: no rebuild
    assert second["nodes"] == first["nodes"] and second["asof"] == first["asof"]   # the graph is the cached one


def test_only_the_clocks_kinds_show_as_runs(served, store):
    with closing(connect(store.db)) as conn:
        for kind, by in (("risk.mc_atlas", "schedule:test"), ("api.heavy", "api")):
            enqueue(conn, kind=kind, params={}, priority=2, mem_class="S", heavy=False, submitted_by=by, now=NOW - HOUR)
    body = served.client.get("/api/machine").json()
    assert "risk.mc_atlas" in {n["m"]["kind"] for n in body["nodes"] if n["k"] == "job"}
    assert [r["kind"] for r in body["runs"]] == ["risk.mc_atlas"]


def test_a_new_artifact_rebuilds_the_graph_and_the_ttl_does_too(served, store):
    served.client.get("/api/machine")
    served.client.get("/api/machine")
    assert len(served.builds) == 1
    publish(store, "cov.league", {"league": LEAGUE}, finished=NOW)
    body = served.client.get("/api/machine").json()
    assert len(served.builds) == 2
    assert "status" not in {n["id"]: n for n in body["nodes"]}["p:cov.league"]
    served.clock[0] = NOW + timedelta(minutes=6)
    assert served.client.get("/api/machine").json()["asof"] == iso(NOW + timedelta(minutes=6))
    assert len(served.builds) == 3


def test_the_response_does_not_change_what_is_cached(served):
    served.client.get("/api/machine")
    assert "runs" not in served.cache.get(NOW)


def test_it_is_public_and_not_rate_limited(client, store):
    cache = cache_for(store, [])
    client.app.dependency_overrides[machine_router.get_machine_db_path] = lambda: store.db
    client.app.dependency_overrides[machine_router.get_machine_cache] = lambda: cache
    try:
        seen = [client.get("/api/machine", headers={"X-Forwarded-For": "203.0.113.9"}).status_code for _ in range(25)]
    finally:
        client.app.dependency_overrides.pop(machine_router.get_machine_db_path, None)
        client.app.dependency_overrides.pop(machine_router.get_machine_cache, None)
    assert seen == [200] * 25                                           # no credentials sent, none asked for


def test_a_tight_rate_limit_does_not_reach_it(store):
    app = FastAPI()
    app.add_middleware(RateLimit, per_min=1, burst=1)
    app.include_router(machine_router.router, prefix="/api")
    cache = cache_for(store, [])
    app.dependency_overrides[machine_router.get_machine_db_path] = lambda: store.db
    app.dependency_overrides[machine_router.get_machine_cache] = lambda: cache
    c = TestClient(app)
    assert [c.get("/api/machine", headers={"X-Forwarded-For": "203.0.113.9"}).status_code for _ in range(5)] == [200] * 5


def test_without_overrides_it_reads_the_hosts_own_stores(tmp_path, monkeypatch):
    """The default wiring: the jobs database at ``jobs_db_path()``, the settings' warehouse, one cache per process."""
    monkeypatch.setattr(machine_router, "jobs_db_path", lambda: tmp_path / "jobs.sqlite")
    monkeypatch.setattr(machine_router, "_cache", machine_router.new_machine_cache())
    app = FastAPI()
    app.include_router(machine_router.router, prefix="/api")
    r = TestClient(app).get("/api/machine")
    assert r.status_code == 200 and not (tmp_path / "jobs.sqlite").exists()   # reading the machine creates nothing
    assert "The job system has not started on this host yet." in r.json()["notes"]


# ------------------------------------------------------------------ nothing private reaches a public body
# each real shape of a note that carries a job's internals: the file and line that writes it, with invented values
PRIVATE_NOTES = [
    "Kronos subprocess failed (CalledProcessError): stderr tail with no path and no error word",   # kronos_shadow.py:195
    "Kronos subprocess failed (TimeoutExpired): ",                                                  # kronos_shadow.py:195
    "no warehouse (warehouse /data/warehouse.duckdb does not exist yet (no ingest has run)): universes.json ETFs only",   # vol_league.py:34
    "members skipped: warehouse warehouse.duckdb: locked by the other process (writer or reader)",  # atlas.py:85
    "skipped overlay-3.json: unreadable ([Errno 13] Permission denied: '/signals/overlay-3.json')", # overlay.py:121
    "skipped overlay-4.json: unreadable (Expecting value: line 1 column 1 (char 0))",               # overlay.py:121
    "reported risk.mc_atlas: unreadable (KeyError: 'var'); reported as null",                       # overlay.py:373
    "desk unreachable: keyed on the last written document (ConnectError: [Errno 111] refused)",     # daily.py:319
    "desk unreachable: keyed on the last written document (ConnectTimeout: timed out)",             # daily.py:319
    'EXP-K01 config {"model_revision": "abc", "n_samples": 30}',                                    # kronos_shadow.py:223
    'Traceback (most recent call last):\n  File "/app/job.py", line 3, in run\nValueError: boom',
]


def with_notes(directory, notes):
    """The artifact in ``directory`` with these notes in its manifest, as a job writes them."""
    manifest = json.loads((directory / "manifest.json").read_text())
    (directory / "manifest.json").write_text(json.dumps({**manifest, "notes": notes}))


def test_nothing_private_is_in_the_body(store, tmp_path, monkeypatch):
    gone = Settings(warehouse_path=tmp_path / "deep" / "dir" / "wh.duckdb", data_dir=tmp_path)
    served = serve(Store(store.db, store.root, gone), monkeypatch)
    _, league = publish(store, "cov.league", {"league": LEAGUE})
    with_notes(league, PRIVATE_NOTES)
    _, bad = publish(store, "regime.hmm", {"probs": pd.DataFrame({"date": ["2026-05-30"], "p_high": [0.2]})})
    (bad / "manifest.json").write_text("{not json")
    with closing(connect(store.db)) as conn:
        enqueue(conn, kind="risk.mc_atlas", params={"secret_param": "hunter2"}, priority=2, mem_class="S", heavy=False,
                submitted_by="schedule:test", now=NOW - 2 * HOUR)
        job = claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=NOW - 2 * HOUR)
        fail(conn, job.id, error=f"Traceback: {tmp_path}/artifacts/boom SECRETERROR", retries=0, cpu_seconds=1.0,
             peak_rss_bytes=1, now=NOW - HOUR)
    r = served.client.get("/api/machine")
    body = r.json()
    assert "failed" in {run["state"] for run in body["runs"]} and "The warehouse has no data yet." in body["notes"]
    assert {n["id"]: n for n in body["nodes"]}["p:cov.league"]["m"]["notes"] == [WITHHELD]    # every shape, one sentence
    for private in (str(tmp_path), str(store.root), "hunter2", "SECRETERROR", "Traceback", "duckdb", "stderr tail",
                    "model_revision", "ConnectError", "Permission denied", "TimeoutExpired", "CalledProcessError"):
        assert private not in r.text


def test_a_build_that_fails_is_a_plain_503_that_says_nothing(store, tmp_path):
    def broken(now):
        raise ValueError(f"cannot open {tmp_path}/secret.parquet")        # the app's ValueError handler would echo this

    app = FastAPI()
    app.include_router(machine_router.router, prefix="/api")
    app.dependency_overrides[machine_router.get_machine_db_path] = lambda: store.db
    app.dependency_overrides[machine_router.get_machine_cache] = lambda: MachineCache(broken, lambda: None)
    r = TestClient(app).get("/api/machine")
    assert r.status_code == 503 and r.json()["error"] == "machine_unavailable"
    assert "secret" not in r.text and str(tmp_path) not in r.text


def test_a_refresh_that_fails_still_answers_with_the_last_good_graph(served, store, monkeypatch):
    first = served.client.get("/api/machine").json()

    def broken(*a, **k):
        raise ValueError("cannot read")

    monkeypatch.setattr("ohcamel_quant.machine.service.load_inputs", broken)
    served.clock[0] = NOW + timedelta(minutes=6)
    r = served.client.get("/api/machine")
    assert r.status_code == 200 and r.json()["asof"] == first["asof"]
