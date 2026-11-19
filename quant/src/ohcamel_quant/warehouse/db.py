"""Connections to the DuckDB warehouse (compute plan II.5).

DuckDB allows ONE read-write process per file, and a read-only process holds a
shared lock that blocks the writer. So:

* the worker's ingest jobs are the only writers (``open_rw``), and they hold the
  connection only while writing -- never across a vendor request;
* the API opens ``read_only=True`` (``open_ro``) per request and closes at once;
* a lock held by the other side is retried with backoff up to ``timeout_s``
  (2 s for readers, 30 s for the writer), then raised as
  :class:`WarehouseUnavailable` -- a ``DataUnavailable``, so routers answer 503
  and ``MarketData`` falls back to its providers. Never a crash, never a hang.

Migrations are ``migrations/NNN_name.sql`` applied in order inside a
transaction; ``schema_version`` records each. Comments in those files must not
contain a semicolon (statements are split on it). A migration that replaces a
table too large to copy in one transaction (003: ``option_snapshots``) renames
it and creates the new one; :func:`migrate` then moves the rows in bounded
transactions (:func:`_move_option_snapshots`) and drops the old table, resuming
on the next open if interrupted.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from ..config import Settings, get_settings
from ..data.base import DataUnavailable

MIGRATIONS = Path(__file__).with_name("migrations")
RO_CONFIG = {"threads": 1, "memory_limit": "128MB"}
RW_MEMORY_LIMIT = "256MB"


class WarehouseUnavailable(DataUnavailable):
    """The warehouse is not configured, does not exist yet, or is locked by the other side."""


def warehouse_path(settings: Settings | None = None) -> Path | None:
    s = settings or get_settings()
    return Path(s.warehouse_path) if s.warehouse_path is not None else None


def _resolve(path: Path | None, settings: Settings | None) -> Path:
    p = path if path is not None else warehouse_path(settings)
    if p is None:
        raise WarehouseUnavailable("warehouse not configured (OHCAMEL_QUANT_WAREHOUSE_PATH unset)")
    return Path(p)


def _is_lock_error(e: Exception) -> bool:
    return "lock" in str(e).lower()


def _connect(path: Path, *, read_only: bool, config: dict, timeout_s: float) -> duckdb.DuckDBPyConnection:
    deadline = time.monotonic() + timeout_s
    delay = 0.05
    while True:
        try:
            return duckdb.connect(str(path), read_only=read_only, config=config)
        except (duckdb.IOException, duckdb.ConnectionException) as e:
            locked = _is_lock_error(e)
            if not locked or time.monotonic() >= deadline:
                why = "locked by the other process (writer or reader)" if locked else str(e)
                raise WarehouseUnavailable(f"warehouse {path.name}: {why}") from e
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def migration_files() -> list[tuple[int, Path]]:
    return sorted((int(p.name.split("_", 1)[0]), p) for p in MIGRATIONS.glob("*.sql"))


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def migrate(con: duckdb.DuckDBPyConnection) -> int:
    """Apply every migration newer than ``schema_version`` and finish any table move; return the version."""
    current = _apply_sql(con)
    _move_option_snapshots(con)
    return current


def _apply_sql(con: duckdb.DuckDBPyConnection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_at TIMESTAMP NOT NULL)")
    current = int(con.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0])
    for number, path in migration_files():
        if number <= current:
            continue
        con.execute("BEGIN TRANSACTION")
        try:
            for stmt in _statements(path.read_text()):
                con.execute(stmt)
            con.execute("INSERT INTO schema_version VALUES (?, ?)",
                        [number, datetime.now(UTC).replace(tzinfo=None)])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        current = number
    return current


#: 003_option_quotes.sql's old table: present only between that migration and the end of the move.
OPTION_SNAPSHOTS_OLD = "option_snapshots_v2"
_OLD_COLUMNS = 'underlying, "asof", expiry, strike, cp, bid, ask, last, volume, open_interest, source'


def move_incomplete(con: object) -> bool:
    """True while migration 003's option-table move is unfinished (``option_snapshots_v2`` still exists): the new
    ``option_snapshots`` then holds only part of the history, so every reader of it refuses rather than read a
    part. ``python -m ohcamel_quant.warehouse migrate`` finishes it (docs/runbooks/compute.md)."""
    return bool(con.execute("SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'main' AND table_name = ?",
                            [OPTION_SNAPSHOTS_OLD]).fetchone()[0])


def _move_option_snapshots(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Move every row of the pre-003 ``option_snapshots`` (renamed ``option_snapshots_v2``) into the new table as
    the after-close pull (``pull = '1620'``, the new columns NULL), ONE SNAPSHOT DAY PER TRANSACTION, then drop the
    old table. A day already in the new table was moved whole (its transaction committed), so a resumed move
    copies only the days still missing. Returns ``(days, rows)`` moved. Rationale and measurements:
    003_option_quotes.sql."""
    if not move_incomplete(con):
        return 0, 0
    days = [d for (d,) in con.execute(
        f'SELECT DISTINCT "asof" FROM {OPTION_SNAPSHOTS_OLD} EXCEPT '
        "SELECT DISTINCT \"asof\" FROM option_snapshots WHERE pull = '1620' ORDER BY 1").fetchall()]
    rows = 0
    for d in days:
        con.execute("BEGIN TRANSACTION")
        try:
            rows += int(con.execute(f"INSERT INTO option_snapshots ({_OLD_COLUMNS}, pull) SELECT {_OLD_COLUMNS}, "
                                    f"'1620' FROM {OPTION_SNAPSHOTS_OLD} WHERE \"asof\" = ?", [d]).fetchone()[0])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    con.execute(f"DROP TABLE {OPTION_SNAPSHOTS_OLD}")
    return len(days), rows


def apply_migrations(path: Path | None = None, *, settings: Settings | None = None,
                     timeout_s: float = 30.0) -> dict[str, float | int]:
    """``python -m ohcamel_quant.warehouse migrate``: open the warehouse as the writer (its 256 MB limit), apply
    every pending migration and finish any table move, and say what was done -- ``{version, days_moved,
    rows_moved, seconds}``. The owner runs it off-hours right after a deploy that brings a migration
    (docs/runbooks/compute.md); skipped, the first writer to open the file does the same work inside its job."""
    p = _resolve(path, settings)
    con = _connect(p, read_only=False, timeout_s=timeout_s, config={
        "threads": 1, "memory_limit": RW_MEMORY_LIMIT, "preserve_insertion_order": False})
    try:
        t0 = time.monotonic()
        version = _apply_sql(con)
        days, rows = _move_option_snapshots(con)
        return {"version": version, "days_moved": days, "rows_moved": rows,
                "seconds": round(time.monotonic() - t0, 3)}
    finally:
        con.close()


@contextmanager
def open_rw(path: Path | None = None, *, settings: Settings | None = None, threads: int = 1,
            timeout_s: float = 30.0) -> Iterator[duckdb.DuckDBPyConnection]:
    """The writer's connection (worker ingest jobs only); creates and migrates the file."""
    p = _resolve(path, settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = _connect(p, read_only=False, timeout_s=timeout_s, config={
        "threads": max(1, int(threads)), "memory_limit": RW_MEMORY_LIMIT, "preserve_insertion_order": False})
    try:
        migrate(con)
        yield con
    finally:
        con.close()


def connect_ro(path: Path | None = None, *, settings: Settings | None = None,
               timeout_s: float = 2.0) -> duckdb.DuckDBPyConnection:
    """A read-only connection the caller closes (``JobContext.warehouse``); prefer :func:`open_ro`."""
    p = _resolve(path, settings)
    if not p.exists():
        raise WarehouseUnavailable(f"warehouse {p} does not exist yet (no ingest has run)")
    return _connect(p, read_only=True, config=dict(RO_CONFIG), timeout_s=timeout_s)


@contextmanager
def open_ro(path: Path | None = None, *, settings: Settings | None = None,
            timeout_s: float = 2.0) -> Iterator[duckdb.DuckDBPyConnection]:
    """A short-lived read-only connection (API and readers). Close it promptly."""
    con = connect_ro(path, settings=settings, timeout_s=timeout_s)
    try:
        yield con
    finally:
        con.close()
