"""The Machine's loader: the latest artifact of each kind read safely inside the artifacts root, the warehouse
read-only and tolerant of being locked or absent, the roster, and the last day's job runs. Everything read
from disk is untrusted, and a source that is missing or fails degrades only its own node."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import pytest
from lane_m_harness import publish_artifact

from ohcamel_quant.config import Settings
from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.queue import claim_next, enqueue, fail, set_progress
from ohcamel_quant.machine import load
from ohcamel_quant.machine.graph import PROD, build_graph
from ohcamel_quant.machine.lenses import LENS_TABLES
from ohcamel_quant.machine.load import load_inputs, load_products, load_runs, load_warehouse
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
