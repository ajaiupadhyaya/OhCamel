"""The job queue (contract II.2): enqueue, claim, heartbeat, finish, fail, cancel.

Every state change appends a ``job_events`` row (the SSE stream's source).
``claim_next`` is one ``UPDATE ... RETURNING`` inside ``BEGIN IMMEDIATE``, so
two claimers can never take the same job.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from .db import iso, tx
from .ulid import new_ulid

MEM_CLASSES = ("S", "M", "L")
PRIORITIES = (0, 1, 2)   # 0 interactive, 1 intraday, 2 nightly
LIVE = ("queued", "running")


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    params: dict[str, Any]
    params_hash: str
    priority: int
    mem_class: str
    heavy: bool
    state: str
    submitted_at: str
    started_at: str | None
    finished_at: str | None
    attempts: int
    error: str | None
    artifact_id: str | None
    cpu_seconds: float | None
    peak_rss_bytes: int | None
    submitted_by: str

    @staticmethod
    def from_row(r: sqlite3.Row) -> Job:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["heavy"] = bool(d["heavy"])
        return Job(**d)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_params(params: Any) -> str:
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    try:
        return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"params are not canonical JSON: {e}") from e


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_params(params).encode()).hexdigest()


def _event(conn: sqlite3.Connection, job_id: str, state: str, now: datetime,
           progress: float | None = None, message: str | None = None) -> None:
    conn.execute("INSERT INTO job_events (job_id, state, progress, message, at) VALUES (?,?,?,?,?)",
                 (job_id, state, progress, message, iso(now)))


def _get(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return Job.from_row(row) if row else None


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    return _get(conn, job_id)


def find_live(conn: sqlite3.Connection, kind: str, phash: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE kind = ? AND params_hash = ? AND state IN ('queued','running')",
                       (kind, phash)).fetchone()
    return Job.from_row(row) if row else None


def enqueue(conn: sqlite3.Connection, *, kind: str, params: dict[str, Any], priority: int, mem_class: str,
            heavy: bool, submitted_by: str, now: datetime, client: str | None = None) -> tuple[Job, bool]:
    """Queue a job, or return the live job with the same (kind, params_hash). -> (job, created)."""
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if mem_class not in MEM_CLASSES:
        raise ValueError(f"mem_class must be one of {MEM_CLASSES}")
    if not (submitted_by == "api" or submitted_by.startswith("schedule:")):
        raise ValueError("submitted_by must be 'api' or 'schedule:<name>'")
    text = canonical_params(params)
    phash = hashlib.sha256(text.encode()).hexdigest()
    with tx(conn):
        live = find_live(conn, kind, phash)
        if live is not None:
            return live, False
        jid = new_ulid(int(now.timestamp() * 1000))
        conn.execute(
            "INSERT INTO jobs (id, kind, params, params_hash, priority, mem_class, heavy, state, submitted_at,"
            " attempts, submitted_by) VALUES (?,?,?,?,?,?,?,'queued',?,0,?)",
            (jid, kind, text, phash, priority, mem_class, 1 if heavy else 0, iso(now), submitted_by))
        if client is not None:
            conn.execute("INSERT INTO job_clients (job_id, client) VALUES (?,?)", (jid, client))
        _event(conn, jid, "queued", now)
        job = _get(conn, jid)
    assert job is not None
    return job, True


def claim_next(conn: sqlite3.Connection, *, allowed_classes: frozenset[str] | set[str], allow_heavy: bool,
               now: datetime) -> Job | None:
    """Atomically take the highest-priority, oldest queued job that admission allows."""
    classes = [c for c in MEM_CLASSES if c in allowed_classes]
    if not classes:
        return None
    marks = ",".join("?" * len(classes))
    with tx(conn):
        rows = conn.execute(
            f"UPDATE jobs SET state='running', started_at=?, attempts=attempts+1"
            f" WHERE id = (SELECT id FROM jobs WHERE state='queued' AND mem_class IN ({marks})"
            f"             AND (heavy = 0 OR ?) ORDER BY priority, submitted_at, id LIMIT 1)"
            f" RETURNING *",
            (iso(now), *classes, 1 if allow_heavy else 0)).fetchall()  # fetchall: finish the statement before COMMIT
        if not rows:
            return None
        job = Job.from_row(rows[0])
        conn.execute(
            "INSERT INTO job_progress (job_id, heartbeat_at, progress, message) VALUES (?,?,0,NULL)"
            " ON CONFLICT(job_id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at, progress=0, message=NULL",
            (job.id, iso(now)))
        _event(conn, job.id, "running", now, 0.0)
    return job


def heartbeat(conn: sqlite3.Connection, job_id: str, *, now: datetime) -> str:
    """Record that the worker is alive on this job; returns the job's state ('cancelled' = stop)."""
    conn.execute("UPDATE job_progress SET heartbeat_at = ? WHERE job_id = ?", (iso(now), job_id))
    row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row["state"] if row else "cancelled"


def set_progress(conn: sqlite3.Connection, job_id: str, fraction: float, message: str | None, *,
                 now: datetime) -> None:
    f = min(max(float(fraction), 0.0), 1.0)
    msg = None if message is None else str(message)[:500]
    with tx(conn):
        conn.execute("UPDATE job_progress SET heartbeat_at=?, progress=?, message=? WHERE job_id=?",
                     (iso(now), f, msg, job_id))
        _event(conn, job_id, "running", now, f, msg)


def finish(conn: sqlite3.Connection, job_id: str, *, artifact: dict[str, Any], cpu_seconds: float,
           peak_rss_bytes: int | None, now: datetime) -> bool:
    """Record the artifact and mark the job done, in one transaction. False if it is no longer running."""
    with tx(conn):
        row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or row["state"] != "running":
            return False
        conn.execute("INSERT INTO artifacts (id, kind, params_hash, finished_at, path, data_asof) VALUES (?,?,?,?,?,?)",
                     (artifact["id"], artifact["kind"], artifact["params_hash"], artifact["finished_at"],
                      artifact["path"], artifact.get("data_asof")))
        conn.execute("UPDATE jobs SET state='done', finished_at=?, artifact_id=?, cpu_seconds=?, peak_rss_bytes=?"
                     " WHERE id=?", (iso(now), artifact["id"], cpu_seconds, peak_rss_bytes, job_id))
        _event(conn, job_id, "done", now, 1.0)
    return True


def fail(conn: sqlite3.Connection, job_id: str, *, error: str, retries: int, cpu_seconds: float,
         peak_rss_bytes: int | None, now: datetime) -> str:
    """The handler raised. Re-queue while attempts <= retries, else 'failed'. Returns the new state."""
    with tx(conn):
        row = conn.execute("SELECT state, attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return "cancelled"
        if row["state"] != "running":
            return row["state"]
        if row["attempts"] <= retries:
            conn.execute("UPDATE jobs SET state='queued', started_at=NULL, error=? WHERE id=?", (error, job_id))
            _event(conn, job_id, "queued", now, None, f"retrying after: {error}"[:500])
            return "queued"
        conn.execute("UPDATE jobs SET state='failed', finished_at=?, error=?, cpu_seconds=?, peak_rss_bytes=?"
                     " WHERE id=?", (iso(now), error, cpu_seconds, peak_rss_bytes, job_id))
        _event(conn, job_id, "failed", now, None, error[:500])
        return "failed"


def cancel(conn: sqlite3.Connection, job_id: str, *, now: datetime) -> Job | None:
    """Queued or running -> cancelled (a running job's worker sees it on its next heartbeat)."""
    with tx(conn):
        job = _get(conn, job_id)
        if job is None:
            return None
        if job.state in LIVE:
            conn.execute("UPDATE jobs SET state='cancelled', finished_at=? WHERE id=?", (iso(now), job_id))
            _event(conn, job_id, "cancelled", now)
            job = _get(conn, job_id)
    return job


def settle_cancelled(conn: sqlite3.Connection, job_id: str, *, cpu_seconds: float, peak_rss_bytes: int | None,
                     now: datetime) -> None:
    """The child stopped because of a cancel: record what it cost."""
    with tx(conn):
        conn.execute("UPDATE jobs SET state='cancelled', finished_at=COALESCE(finished_at, ?), cpu_seconds=?,"
                     " peak_rss_bytes=? WHERE id=? AND state IN ('running','cancelled')",
                     (iso(now), cpu_seconds, peak_rss_bytes, job_id))


def requeue(conn: sqlite3.Connection, job_id: str, *, reason: str, now: datetime) -> None:
    """Back to the queue without spending an attempt (worker shutdown)."""
    with tx(conn):
        cur = conn.execute("UPDATE jobs SET state='queued', started_at=NULL, attempts=MAX(attempts-1, 0), error=?"
                           " WHERE id=? AND state='running'", (reason, job_id))
        if cur.rowcount:
            _event(conn, job_id, "queued", now, None, reason)


def requeue_stale(conn: sqlite3.Connection, *, now: datetime, stale_after_s: float = 300.0,
                  max_attempts: int = 3) -> list[tuple[str, str]]:
    """Running jobs whose last heartbeat is older than ``stale_after_s``: the worker died.

    Re-queued, unless the job has now been attempted ``max_attempts`` times
    (it is probably what kills the worker): then it is failed.
    """
    cutoff = iso(now - timedelta(seconds=stale_after_s))
    out: list[tuple[str, str]] = []
    with tx(conn):
        rows = conn.execute(
            "SELECT j.id, j.attempts FROM jobs j LEFT JOIN job_progress p ON p.job_id = j.id"
            " WHERE j.state = 'running' AND COALESCE(p.heartbeat_at, j.started_at) < ?", (cutoff,)).fetchall()
        for r in rows:
            if r["attempts"] >= max_attempts:
                err = (f"the worker died {r['attempts']} times while running this job (no heartbeat for "
                       f"{int(stale_after_s)} s); not retried again")
                conn.execute("UPDATE jobs SET state='failed', finished_at=?, error=? WHERE id=?",
                             (iso(now), err, r["id"]))
                _event(conn, r["id"], "failed", now, None, err)
                out.append((r["id"], "failed"))
            else:
                err = "re-queued: the worker stopped heartbeating (killed, or out of memory?)"
                conn.execute("UPDATE jobs SET state='queued', started_at=NULL, error=? WHERE id=?", (err, r["id"]))
                _event(conn, r["id"], "queued", now, None, err)
                out.append((r["id"], "queued"))
    return out


def list_jobs(conn: sqlite3.Connection, *, kind: str | None = None, state: str | None = None,
              limit: int = 50) -> list[Job]:
    sql, args = "SELECT * FROM jobs WHERE 1=1", []
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if state:
        sql += " AND state = ?"
        args.append(state)
    sql += " ORDER BY submitted_at DESC, id DESC LIMIT ?"
    args.append(int(limit))
    return [Job.from_row(r) for r in conn.execute(sql, args)]


def progress_of(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT heartbeat_at, progress, message FROM job_progress WHERE job_id = ?",
                       (job_id,)).fetchone()
    return dict(row) if row else None


def client_of(conn: sqlite3.Connection, job_id: str) -> str | None:
    row = conn.execute("SELECT client FROM job_clients WHERE job_id = ?", (job_id,)).fetchone()
    return row["client"] if row else None


def live_count_for_client(conn: sqlite3.Connection, client: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM jobs j JOIN job_clients c ON c.job_id = j.id"
                        " WHERE c.client = ? AND j.state IN ('queued','running')", (client,)).fetchone()[0]


def events_after(conn: sqlite3.Connection, seq: int, limit: int = 500) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT seq, job_id, state, progress, message, at FROM job_events WHERE seq > ? ORDER BY seq LIMIT ?",
        (int(seq), int(limit)))]


def current_seq(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM job_events").fetchone()[0]
