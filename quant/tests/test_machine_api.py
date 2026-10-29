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

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lane_m_harness import publish_artifact

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.ratelimit import RateLimit
from ohcamel_quant.api.routers import machine as machine_router
from ohcamel_quant.config import Settings
from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.queue import claim_next, enqueue, fail, set_progress
from ohcamel_quant.machine import load
from ohcamel_quant.machine.graph import PROD, build_graph
from ohcamel_quant.machine.lenses import LENS_TABLES
from ohcamel_quant.machine.load import load_inputs, load_products, load_runs, load_warehouse
from ohcamel_quant.machine.service import MachineCache, build_machine, sentence, watermark
from ohcamel_quant.machine.world import load_world
from ohcamel_quant.signals.report import BOOK_COLUMNS
from ohcamel_quant.warehouse.db import WarehouseUnavailable
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

NOW = datetime(2026, 6, 2, 15, 0, tzinfo=UTC)   # the day after the fixture warehouse's last bar
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
    publish(store, "cov.league", {"league": LEAGUE}, finished=NOW - timedelta(days=30))   # max age 204 h
    assert load_products(store.db, NOW)["cov.league"].stale is True


# ------------------------------------------------------------------ what is read from disk is untrusted
def test_a_malformed_manifest_degrades_only_its_own_node(store):
    publish(store, "cov.league", {"league": LEAGUE})
    _, bad = publish(store, "regime.hmm", {"probs": pd.DataFrame({"date": ["2026-05-30"], "p_high": [0.2]})})
    (bad / "manifest.json").write_text("{not json")
    products = load_products(store.db, NOW)
    assert products["regime.hmm"].manifest is None and "manifest" in products["regime.hmm"].why
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


def test_a_table_that_leaves_the_root_is_not_read(store, tmp_path):
    _, d = publish(store, "cov.league", {"league": LEAGUE})
    outside = tmp_path / "outside.parquet"
    LEAGUE.to_parquet(outside, index=False)
    (d / "league.parquet").unlink()
    (d / "league.parquet").symlink_to(outside)
    p = load_products(store.db, NOW)["cov.league"]
    assert p.manifest is not None and "league" not in p.tables and "league" not in p.rows
    assert "league" in p.why and "outside" in p.why and str(tmp_path) not in p.why


def test_an_unreadable_table_costs_only_that_table(store):
    _, d = publish(store, "farm.sweep", {"leaderboard": BOARD, "cells": CELLS, "trials": TRIALS})
    (d / "leaderboard.parquet").write_bytes(b"this is not parquet")
    farm = load_products(store.db, NOW)["farm.sweep"]
    assert set(farm.tables) == {"cells"} and farm.rows == {"cells": 2, "trials": 5}
    assert "leaderboard" in farm.why and str(d) not in farm.why
    node = nodes(build_graph(load_inputs(store.db, store.settings, NOW)))["p:farm.sweep"]
    assert "status" not in node and "leaderboard" in node["why"] and node["m"]["lens_why"]


@pytest.mark.parametrize("art_id", [["a"], {"a": 1}, 7, None, "01NOSUCHARTIFACT"])
def test_a_manifest_naming_no_artifact_has_no_tables(store, art_id):
    _, d = publish(store, "cov.league", {"league": LEAGUE})
    manifest = json.loads((d / "manifest.json").read_text())
    manifest["id"] = art_id
    (d / "manifest.json").write_text(json.dumps(manifest))
    p = load_products(store.db, NOW)["cov.league"]
    assert p.manifest is not None and not p.tables and "located" in p.why


def test_a_table_the_manifest_does_not_list_is_missing_not_guessed(store):
    _, d = publish(store, "cov.league", {"league": LEAGUE, "extra": LEAGUE})
    manifest = json.loads((d / "manifest.json").read_text())
    manifest["tables"] = ["extra"]                              # league.parquet is on disk but the manifest omits it
    (d / "manifest.json").write_text(json.dumps(manifest))
    p = load_products(store.db, NOW)["cov.league"]
    assert not p.tables and p.why == "no league table"


def test_a_hostile_table_name_in_the_manifest_is_never_opened(store):
    _, d = publish(store, "cov.league", {"league": LEAGUE})
    manifest = json.loads((d / "manifest.json").read_text())
    manifest["tables"] = ["../../manifest", "League", 7, None, "league"]
    (d / "manifest.json").write_text(json.dumps(manifest))
    p = load_products(store.db, NOW)["cov.league"]
    assert p.rows == {"league": 2} and set(p.tables) == {"league"}


def test_a_runaway_table_is_not_loaded(store, monkeypatch):
    publish(store, "cov.league", {"league": LEAGUE})
    monkeypatch.setattr(load, "MAX_TABLE_ROWS", 1)
    p = load_products(store.db, NOW)["cov.league"]
    assert not p.tables and p.rows == {"league": 2} and "league" in p.why and "rows" in p.why


def test_a_corrupt_jobs_database_gives_every_product_missing(store):
    store.db.write_bytes(b"not a sqlite file at all" * 100)
    inp = load_inputs(store.db, store.settings, NOW)
    assert all(p.manifest is None and p.why for p in inp.products.values()) and inp.why["jobs"]
    assert load_runs(store.db, NOW, {"ops.selftest"}) == []


# ------------------------------------------------------------------ the warehouse
def test_a_locked_warehouse_leaves_the_graph_whole(store, monkeypatch):      # Review Focus 1
    def locked(*a, **k):
        raise WarehouseUnavailable("locked")

    monkeypatch.setattr(load, "open_ro", locked)
    inp = load_inputs(store.db, store.settings, NOW)
    assert inp.freshness is None and inp.readouts == {} and inp.why["warehouse"] == "locked"
    assert list(inp.order) == sorted(inp.world.equities)
    by = nodes(build_graph(inp))
    feeds = [n for n in by.values() if n["k"] == "feed"]
    assert feeds and all(n["status"] == "missing" and n["why"] == "locked" for n in feeds)
    assert "d:SPY" in by and "spark" not in by["d:SPY"]["m"]


def test_an_absent_warehouse_is_unavailable_and_names_no_path(tmp_path):
    gone = tmp_path / "deep" / "dir" / "wh.duckdb"
    fresh, readouts, order, why = load_warehouse(Settings(warehouse_path=gone, data_dir=tmp_path), load_world(), NOW)
    assert fresh is None and readouts == {} and order == sorted(load_world().equities)
    assert "wh.duckdb" in why["warehouse"] and str(tmp_path) not in why["warehouse"]


def test_an_unconfigured_warehouse_is_unavailable(tmp_path):
    fresh, readouts, _, why = load_warehouse(Settings(warehouse_path=None, data_dir=tmp_path), load_world(), NOW)
    assert fresh is None and readouts == {} and "not configured" in why["warehouse"]


def test_a_warehouse_without_its_tables_is_unavailable_not_a_crash(tmp_path):
    import duckdb

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


def test_it_builds_once_per_ttl_and_on_a_new_artifact(store):
    build = Counted()
    cache = MachineCache(build, lambda: watermark(store.db))
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
    cache = MachineCache(build, lambda: None)
    cache.get(NOW)
    cache.get(NOW + timedelta(seconds=299.9))
    assert len(build.calls) == 1
    cache.get(NOW + timedelta(seconds=300))
    assert len(build.calls) == 2
    cache.get(NOW + timedelta(seconds=300 + 299.9))                                    # the TTL runs from the new build
    assert len(build.calls) == 2


def test_a_clock_that_went_backwards_rebuilds_rather_than_serving_a_future_build():
    build = Counted()
    cache = MachineCache(build, lambda: None)
    cache.get(NOW)
    cache.get(NOW - timedelta(hours=3))
    assert len(build.calls) == 2


def test_an_artifact_that_lands_during_a_build_is_not_hidden_by_it():
    """The mark is read before the build, so an artifact published while the graph is built shows at the next get."""
    store = {"mark": "a"}
    calls = []

    def build(now):
        calls.append(now)
        store["mark"] = "b"                                      # the artifact lands mid-build, after the graph read "a"
        return {}

    cache = MachineCache(build, lambda: store["mark"])
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


def test_a_failed_build_is_not_cached():
    build = Counted(fail_first=True)
    cache = MachineCache(build, lambda: None)
    with pytest.raises(RuntimeError):
        cache.get(NOW)
    assert cache.get(NOW) == {"built": 2}                        # the next request tries again, and succeeds
    assert cache.get(NOW) == {"built": 2} and len(build.calls) == 2


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
    assert notes[0] == "No jobs database exists on this host yet."
    assert notes[1].startswith("desk.report has published no artifact") and len(notes) == 2
    assert all(n.endswith(".") for n in notes)


def test_the_notes_are_empty_when_nothing_is_missing(store):
    publish(store, "desk.report", {"book_summary": pd.DataFrame([{
        "as_of": "2026-06-02T14:31:00Z", "positions": 5, "kill_switch": "armed", "trips_on": json.dumps([]),
        "limits": json.dumps([{"name": "dd-cap", "kind": "drawdown", "scope": "portfolio"}])}], columns=BOOK_COLUMNS)})
    assert build_machine(store.db, store.settings, NOW)["notes"] == []


def test_the_notes_carry_the_warehouse_reason_without_its_path(store, tmp_path):
    gone = Settings(warehouse_path=tmp_path / "deep" / "dir" / "wh.duckdb", data_dir=tmp_path)
    connect(store.db).close()
    notes = build_machine(store.db, gone, NOW)["notes"]
    (warehouse,) = [n for n in notes if "wh.duckdb" in n]
    assert warehouse[0].isupper() and warehouse.endswith(".") and str(tmp_path) not in json.dumps(notes)


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

    return MachineCache(build, lambda: watermark(s.db))


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
    assert "No jobs database exists on this host yet." in r.json()["notes"]


# ------------------------------------------------------------------ nothing private reaches a public body
def test_nothing_private_is_in_the_body(store, tmp_path, monkeypatch):
    gone = Settings(warehouse_path=tmp_path / "deep" / "dir" / "wh.duckdb", data_dir=tmp_path)
    served = serve(Store(store.db, store.root, gone), monkeypatch)
    publish(store, "cov.league", {"league": LEAGUE})
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
    assert "failed" in {run["state"] for run in body["runs"]} and any("wh.duckdb" in n for n in body["notes"])
    for private in (str(tmp_path), str(store.root), "hunter2", "SECRETERROR", "Traceback"):
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
