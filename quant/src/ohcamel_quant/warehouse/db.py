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
contain a semicolon (statements are split on it).
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
    """Apply every migration newer than ``schema_version``; return the version."""
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


@contextmanager
def open_ro(path: Path | None = None, *, settings: Settings | None = None,
            timeout_s: float = 2.0) -> Iterator[duckdb.DuckDBPyConnection]:
    """A short-lived read-only connection (API and readers). Close it promptly."""
    p = _resolve(path, settings)
    if not p.exists():
        raise WarehouseUnavailable(f"warehouse {p} does not exist yet (no ingest has run)")
    con = _connect(p, read_only=True, config=dict(RO_CONFIG), timeout_s=timeout_s)
    try:
        yield con
    finally:
        con.close()
