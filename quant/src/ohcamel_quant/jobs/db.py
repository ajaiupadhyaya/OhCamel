"""jobs.sqlite: the schema of contract II.2-II.3 plus Lane B's additive tables.

The ``jobs`` and ``artifacts`` DDL are the compute plan's, verbatim (with
IF NOT EXISTS). The additive tables:

* ``job_progress``  one row per job ever claimed: the last heartbeat (stale
  detection, 5 minutes) and the last progress fraction/message.
* ``job_events``    an append-only log of state and progress changes, read
  by the SSE stream by sequence number.
* ``job_clients``   which client address submitted an API job (the per-IP
  limit and the cancel check).
* ``schedule_runs`` one row per (schedule name, scheduled instant): the
  scheduler's idempotency key.

Timestamps are fixed-width UTC strings (``iso``), so string order is time order.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,
  params        TEXT NOT NULL,
  params_hash   TEXT NOT NULL,
  priority      INTEGER NOT NULL,
  mem_class     TEXT NOT NULL CHECK (mem_class IN ('S','M','L')),
  heavy         INTEGER NOT NULL,
  state         TEXT NOT NULL CHECK (state IN ('queued','running','done','failed','cancelled')),
  submitted_at  TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  error         TEXT,
  artifact_id   TEXT,
  cpu_seconds   REAL, peak_rss_bytes INTEGER,
  submitted_by  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, priority, submitted_at);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_live_dedupe ON jobs(kind, params_hash) WHERE state IN ('queued','running');
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, params_hash TEXT NOT NULL,
  finished_at TEXT NOT NULL, path TEXT NOT NULL, data_asof TEXT
);
CREATE INDEX IF NOT EXISTS artifacts_latest ON artifacts(kind, params_hash, finished_at);
CREATE TABLE IF NOT EXISTS job_progress (
  job_id TEXT PRIMARY KEY, heartbeat_at TEXT NOT NULL, progress REAL, message TEXT
);
CREATE TABLE IF NOT EXISTS job_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, state TEXT NOT NULL,
  progress REAL, message TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_clients (job_id TEXT PRIMARY KEY, client TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS job_clients_client ON job_clients(client);
CREATE TABLE IF NOT EXISTS schedule_runs (
  name TEXT NOT NULL, scheduled_for TEXT NOT NULL, job_id TEXT NOT NULL, enqueued_at TEXT NOT NULL,
  PRIMARY KEY (name, scheduled_for)
);
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("iso() needs a timezone-aware datetime")
    u = dt.astimezone(UTC)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) jobs.sqlite in WAL mode, autocommit; ``tx`` for transactions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT: takes the write lock up front, so a read-then-write is atomic."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
