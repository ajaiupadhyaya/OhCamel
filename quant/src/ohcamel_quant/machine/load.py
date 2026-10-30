"""The Machine's loader: every read the graph's ``Inputs`` needs, in one module.

Three stores, each read safely. The jobs database gives the latest artifact of each kind (through
``read_latest``, which skips any row whose files leave the artifacts root) and the last day's runs.
The warehouse is opened read-only and gives dataset freshness and a year of daily closes. The roster,
the world and the schedules are the repo's own config.

Everything read from a store is untrusted, and a source that is absent or fails degrades only its
own node (spec §6.1, "Honesty"): a manifest that will not parse, a table whose path leaves the root
or whose file will not open, a locked or missing warehouse, a missing jobs database. Each gives a
``why`` as a plain sentence for the page and never an exception. The specific text, a table's name or
a path, goes to the log. Nothing is ever created or written: a host with no ``jobs.sqlite`` stays
without one.

A product is stale when a scheduled run has been missed since it (``missed_fire``), not at
schedules.yaml's ``max_age``, which stays the other pages' lamp: 35 minutes for an intraday kind marks
a healthy system stale every evening and weekend, and this page says what is broken, not what is asleep.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Collection
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import Settings
from ..jobs.artifacts import TABLE_NAME, inside
from ..jobs.cron import next_due
from ..jobs.db import connect, iso, parse_iso
from ..jobs.schedules import Schedules, load_schedules, read_latest
from ..signals.roster import load_roster
from ..warehouse.db import WarehouseUnavailable, open_ro
from ..warehouse.freshness import dataset_freshness
from .clock import NY, job_clock
from .graph import PROD, Inputs, Product
from .lenses import LENS_TABLES
from .readouts import cluster_order, ticker_readouts
from .world import World, load_world

log = logging.getLogger("ohcamel_quant.machine.load")

# the tables the graph reads beyond the lenses' (``LENS_TABLES``): the atlas's members, the farm's cells
# (its leaderboard is a lens table) and the desk report's book summary
GRAPH_TABLES: dict[str, tuple[str, ...]] = {
    "risk.mc_atlas": ("members",), "farm.sweep": ("cells",), "desk.report": ("book_summary",),
}
MAX_TABLE_ROWS = 500_000   # a table longer than this is counted, not loaded: no lens table is near it
LOOKBACK_DAYS = 380        # calendar days of closes before the newest bar: a year of sessions, with slack
RUN_WINDOW = timedelta(hours=24)
GRACE = timedelta(hours=2)   # a scheduled run has this long past its fire to publish before it counts as missed

# what the page says, each a whole sentence; the specific text goes to the log
UNREADABLE = "The warehouse could not be read."
WAREHOUSE_LOCKED = "The warehouse is busy with its nightly update."
WAREHOUSE_EMPTY = "The warehouse has no data yet."
NO_WAREHOUSE = "This host has no warehouse."
NO_JOBS = "The job system has not started on this host yet."
JOBS_UNREADABLE = "The job system's records could not be read."
RECORD_UNREADABLE = "The latest run's record could not be read."
FILES_GONE = "The latest run's files could not be found."
PART_UNREADABLE = "Part of the latest run could not be read."


def missed_fire(kind: str, finished: Any, now: datetime, schedules: Schedules) -> bool:
    """Whether a scheduled run of ``kind`` has been missed since ``finished``: the next fire of any of the
    kind's schedule entries after that time, plus ``GRACE``, has passed. A kind with no schedule entry is
    never stale, and neither is a time that does not read (a manifest is untrusted)."""
    try:
        done = parse_iso(finished)
        fires = [t for e in schedules.entries if e.kind == kind and (t := next_due(e.parsed, done)) is not None]
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(fires) and now > min(fires) + GRACE


def wanted(kind: str) -> tuple[str, ...]:
    """The tables of ``kind``'s artifact the graph loads, each once."""
    return tuple(dict.fromkeys((*LENS_TABLES.get(kind, ()), *GRAPH_TABLES.get(kind, ()))))


# ------------------------------------------------------------------ products: the latest artifact of each kind
def _tables(root: Path, directory: Path, manifest: dict, kind: str) -> tuple[dict[str, pd.DataFrame], dict[str, int], list[str]]:
    """The wanted tables the manifest lists, loaded; the row count of every table it lists (from the parquet
    footer, so a table of any size costs nothing); and what went wrong, in words. A name that is not a plain
    table name is never turned into a path, and a file that resolves outside ``root`` is never opened."""
    listed = manifest.get("tables")
    names = list(dict.fromkeys(n for n in listed if isinstance(n, str) and TABLE_NAME.match(n))) \
        if isinstance(listed, list) else []
    want = wanted(kind)
    tables: dict[str, pd.DataFrame] = {}
    rows: dict[str, int] = {}
    problems: list[str] = []
    for name in names:
        f = directory / f"{name}.parquet"
        if not inside(root, f):
            problems.append(f"{name} lies outside the artifacts directory")
            continue
        try:
            with pq.ParquetFile(f) as pf:
                rows[name] = pf.metadata.num_rows
            if name in want and rows[name] > MAX_TABLE_ROWS:
                problems.append(f"{name} has {rows[name]:,} rows, too many to read")
            elif name in want:
                tables[name] = pd.read_parquet(f)
        except (OSError, ValueError, pa.ArrowException):
            problems.append(f"{name} could not be read")
    problems += [f"no {name} table" for name in want if name not in names]
    return tables, rows, problems


def _product(conn: sqlite3.Connection, root: Path, kind: str, now: datetime, schedules: Schedules) -> Product:
    """The latest artifact of ``kind`` as a ``Product``. Only a failure of the database itself escapes."""
    try:
        got = read_latest(conn, kind, now=now, schedules=schedules, root=root)
    except (ValueError, OSError, RecursionError):   # a manifest that is not JSON or not UTF-8, or a bad timestamp
        log.warning("%s: its latest manifest could not be read", kind, exc_info=True)
        return Product(kind, None, why=RECORD_UNREADABLE)
    if got is None:
        return Product(kind, None)                   # nothing published: the graph says so
    manifest = got["manifest"]
    if not isinstance(manifest, dict):
        log.warning("%s: its latest manifest is not a mapping", kind)
        return Product(kind, None, why=RECORD_UNREADABLE)
    stale = missed_fire(kind, manifest.get("finished_at"), now, schedules)
    art_id = manifest.get("id")
    row = conn.execute("SELECT path FROM artifacts WHERE id = ? AND kind = ?", (art_id, kind)).fetchone() \
        if isinstance(art_id, str) else None
    if row is None:
        log.warning("%s: the latest artifact's files could not be located", kind)
        return Product(kind, manifest, stale, why=FILES_GONE)
    tables, rows, problems = _tables(root, Path(row["path"]), manifest, kind)
    if problems:
        log.warning("%s: %s", kind, "; ".join(problems))
    return Product(kind, manifest, stale, tables, rows, PART_UNREADABLE if problems else None)


def _read_products(db: Path, now: datetime, schedules: Schedules) -> tuple[dict[str, Product], str | None]:
    """Every product, and why the jobs database could not be read (None when it could)."""
    if not db.exists():   # ``connect`` would create it, and a read of the machine must not
        why = NO_JOBS
    else:
        try:
            with closing(connect(db)) as conn:
                return {k: _product(conn, db.parent / "artifacts", k, now, schedules) for k in PROD}, None
        except sqlite3.Error:
            log.warning("jobs database unreadable; every product is shown missing", exc_info=True)
            why = JOBS_UNREADABLE
    return {k: Product(k, None, why=why) for k in PROD}, why


def load_products(db: Path, now: datetime, schedules: Schedules | None = None) -> dict[str, Product]:
    """One ``Product`` per ``PROD`` kind (``desk.report`` among them): the latest artifact's manifest, stale
    flag (``missed_fire``), the tables the graph reads and the row count of every table it lists. The artifacts root is
    ``db.parent / "artifacts"``, where ``jobs.sqlite`` keeps them."""
    return _read_products(db, now, schedules or load_schedules())[0]


# ------------------------------------------------------------------ the warehouse: freshness, closes, readouts
def _closes(con: Any, tickers: list[str]) -> pd.DataFrame:
    """Daily ``adj_close`` of ``tickers`` from ``LOOKBACK_DAYS`` before the warehouse's newest bar, as one
    wide frame (dates ascending by ticker; NaN where a ticker has no bar). One SQL read."""
    marks = ",".join("?" * len(tickers))
    df = con.execute(
        f"SELECT ticker, date, adj_close FROM bars_daily WHERE ticker IN ({marks}) "
        f"AND date >= (SELECT max(date) FROM bars_daily) - {LOOKBACK_DAYS}", tickers).fetchdf()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot(index="date", columns="ticker", values="adj_close").sort_index()


def _public_why(e: WarehouseUnavailable, settings: Settings) -> str:
    """Why the warehouse is unavailable, as a sentence the public may read. The three sentences ``warehouse/db.py``
    writes by hand (not configured, does not exist yet, locked) each have their plain one. Anything else (a DuckDB
    error with the file's own path in it, a message a later change adds) is the one fixed ``UNREADABLE``. The
    server's own text goes to the log, whole."""
    path, text = settings.warehouse_path, str(e)
    if path is None:
        authored = {"warehouse not configured (OHCAMEL_QUANT_WAREHOUSE_PATH unset)": NO_WAREHOUSE}
    else:
        authored = {
            f"warehouse {Path(path)} does not exist yet (no ingest has run)": WAREHOUSE_EMPTY,
            f"warehouse {Path(path).name}: locked by the other process (writer or reader)": WAREHOUSE_LOCKED,
        }
    if text in authored:
        log.info("warehouse unavailable: %s", text)
        return authored[text]
    log.warning("warehouse unavailable; the page says only that it could not be read", exc_info=e)
    return UNREADABLE


def load_warehouse(settings: Settings, world: World, now: datetime) -> tuple[dict | None, dict[str, dict], list[str], dict[str, str]]:
    """``(freshness datasets, readouts, equities in cluster order, why)``, read-only. A warehouse that is
    locked, absent, unconfigured or unreadable gives ``(None, {}, the equities sorted, {"warehouse": why})``."""
    equities = list(world.equities)
    try:
        with open_ro(settings=settings) as con:
            fresh = dataset_freshness(con, now)["datasets"]
            closes = _closes(con, world.daily())
    except WarehouseUnavailable as e:
        return None, {}, sorted(equities), {"warehouse": _public_why(e, settings)}
    except duckdb.Error:   # opened, but it is not a warehouse we can read
        log.warning("warehouse unreadable", exc_info=True)
        return None, {}, sorted(equities), {"warehouse": UNREADABLE}
    return fresh, ticker_readouts(closes), cluster_order(closes, equities), {}


# ------------------------------------------------------------------ the day's runs
def load_runs(db: Path, now: datetime, kinds: Collection[str]) -> list[dict]:
    """The runs of ``kinds`` (the clock's) that are queued or running, or started in the last 24 hours,
    oldest first, each as ``{id, kind, state, start, end, cpu, progress, msg}``. A run carries neither its
    ``error`` nor its params. A finished job is whole (progress 1.0, as the job stream reports it). With no
    jobs database, or one that cannot be read, there are no runs."""
    if not kinds or not db.exists():
        return []
    ks = sorted(kinds)
    sql = ("SELECT j.id, j.kind, j.state, j.started_at, j.finished_at, j.cpu_seconds, p.progress, p.message "
           "FROM jobs j LEFT JOIN job_progress p ON p.job_id = j.id "
           f"WHERE j.kind IN ({','.join('?' * len(ks))}) AND (j.state IN ('queued', 'running') OR j.started_at >= ?) "
           "ORDER BY COALESCE(j.started_at, j.submitted_at), j.id")
    try:
        with closing(connect(db)) as conn:
            found = conn.execute(sql, (*ks, iso(now - RUN_WINDOW))).fetchall()
    except sqlite3.Error:
        log.warning("jobs database unreadable; no runs shown", exc_info=True)
        return []
    return [{"id": r["id"], "kind": r["kind"], "state": r["state"], "start": r["started_at"],
             "end": r["finished_at"], "cpu": r["cpu_seconds"],
             "progress": 1.0 if r["state"] == "done" else r["progress"], "msg": r["message"]} for r in found]


# ------------------------------------------------------------------ everything the graph is built from
def load_inputs(db: Path, settings: Settings, now: datetime) -> Inputs:
    """The graph's ``Inputs``. The clock's day is New York's date of ``now`` (a timezone-aware time); on
    the spring-forward Sunday a 02:00 cron reads 3.0, one day a year. A store that is missing or fails
    never makes this raise: its products, feeds and readouts are missing, with a reason in ``why``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    schedules = load_schedules()
    world = load_world()
    products, jobs_why = _read_products(db, now, schedules)
    fresh, readouts, order, why = load_warehouse(settings, world, now)
    if jobs_why:
        why = {**why, "jobs": jobs_why}
    return Inputs(world, job_clock(schedules, now.astimezone(NY).date()), load_roster(), products, fresh, readouts,
                  order, why)
