"""``python -m ohcamel_quant.jobs``: the operator's view of the queue (docs/runbooks/compute.md).

  status                              running and queued jobs, then the ten newest settled
  run-schedule NAME [--params JSON]   enqueue a schedules.yaml entry now, as the scheduler would
  cancel ID                           cancel a queued or running job
  wall-times [--days N]               per kind, p95 and max wall time of the last N days' finished runs beside its
                                      wall limit; flags a kind whose slowest run took over 70% of it (read-only)
  cancel-burst                        cancel every live burst job and drop every hold: run before a deploy and
                                      before any rollback (docs/runbooks/burst.md)

``run-schedule`` enqueues with the entry's kind, priority, memory class and
heavy flag, ``submitted_by = schedule:NAME`` (contract II.2 allows only that
and ``api``), so admission treats it exactly like the scheduled run: a heavy
entry still waits for 16:05 New York on a session day. It does not write
``schedule_runs``, so the next scheduled fire is unaffected. A live job with the
same kind and params is returned instead of a second one. ``--params`` replaces
the entry's params (a different params hash, so a different job).

Run inside the worker container, where ``/data`` is mounted:
``docker compose -f deploy/docker-compose.yml exec ohcamel-worker python -m ohcamel_quant.jobs status``.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .db import connect, utcnow
from .queue import cancel, enqueue, list_jobs, progress_of


def _fmt_job(conn, j) -> str:
    p = progress_of(conn, j.id) if j.state == "running" else None
    prog = f" {p['progress'] * 100:.0f}%" if p and p.get("progress") is not None else ""
    msg = f" {p['message']}" if p and p.get("message") else ""
    cost = f" cpu {j.cpu_seconds:.1f}s" if j.cpu_seconds is not None else ""
    err = f" error: {j.error}" if j.state == "failed" and j.error else ""
    return (f"  {j.id}  {j.state:<9} {j.kind:<24} p{j.priority} {j.mem_class}{' heavy' if j.heavy else ''}"
            f"  {j.submitted_by}  {j.submitted_at}{prog}{msg}{cost}{err}")


def _status(conn) -> int:
    running = list_jobs(conn, state="running", limit=50)
    queued = list_jobs(conn, state="queued", limit=500)
    print(f"RUNNING {len(running)}")
    for j in running:
        print(_fmt_job(conn, j))
    print(f"QUEUED {len(queued)}")
    for j in sorted(queued, key=lambda j: (j.priority, j.submitted_at, j.id)):  # claim order
        print(_fmt_job(conn, j))
    settled = [j for j in list_jobs(conn, limit=60) if j.state not in ("queued", "running")][:10]
    print(f"RECENT {len(settled)}")
    for j in settled:
        print(_fmt_job(conn, j))
    return 0


def _run_schedule(conn, schedules_path: Path, name: str, params_text: str | None) -> int:
    from .schedules import load_schedules

    entries = {e.name: e for e in load_schedules(schedules_path).entries}
    e = entries.get(name)
    if e is None:
        print(f"unknown schedule {name!r}; known: {', '.join(sorted(entries))}", file=sys.stderr)
        return 2
    params = e.params
    if params_text is not None:
        try:
            params = json.loads(params_text)
        except ValueError as err:
            print(f"--params is not JSON: {err}", file=sys.stderr)
            return 2
        if not isinstance(params, dict):
            print("--params must be a JSON object", file=sys.stderr)
            return 2
    job, created = enqueue(conn, kind=e.kind, params=params, priority=e.priority, mem_class=e.mem_class,
                           heavy=e.heavy, submitted_by=f"schedule:{e.name}", now=utcnow())
    print(f"{job.id} {job.kind} {job.state}{'' if created else ' (already live)'}")
    return 0


def _cancel(conn, job_id: str) -> int:
    job = cancel(conn, job_id, now=utcnow())
    if job is None:
        print(f"no job {job_id!r}", file=sys.stderr)
        return 1
    print(f"{job.id} {job.kind} {job.state}")
    return 0


#: The share of a kind's wall limit above which ``wall-times`` flags it (fix round 1, I3).
WALL_FLAG = 0.70


def wall_times(db: Path, *, days: int = 30, now: datetime | None = None) -> list[dict[str, Any]]:
    """Per kind, over runs that finished (done or failed) in the last ``days``: count, p95 and max wall time
    (finished_at - started_at of the last attempt, seconds) and the kind's wall limit (of its newest run's params:
    the farm's budget). Opens jobs.sqlite READ-ONLY (a URI ``mode=ro``): nothing is created or written."""
    import sqlite3

    import numpy as np

    from .db import parse_iso
    from .kinds import UnknownKind, get_kind, wall_limit_for

    now = now or utcnow()
    since = (now - timedelta(days=days)).isoformat()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT kind, params, started_at, finished_at FROM jobs WHERE state IN ('done', 'failed') "
                           "AND started_at IS NOT NULL AND finished_at IS NOT NULL AND finished_at >= ? "
                           "ORDER BY finished_at", [since]).fetchall()
    finally:
        con.close()
    by: dict[str, dict[str, Any]] = {}
    for kind, params, started, finished in rows:
        d = by.setdefault(kind, {"kind": kind, "secs": [], "params": "{}"})
        d["secs"].append((parse_iso(finished) - parse_iso(started)).total_seconds())
        d["params"] = params                       # the newest run's, for an L kind's own budget
    out = []
    for kind in sorted(by):
        d = by[kind]
        try:
            limit: float | None = wall_limit_for(get_kind(kind), json.loads(d["params"] or "{}"))
        except (UnknownKind, ValueError):
            limit = None
        secs = np.asarray(d["secs"], float)
        p95, mx = float(np.percentile(secs, 95)), float(secs.max())
        out.append({"kind": kind, "runs": len(secs), "p95_s": p95, "max_s": mx, "limit_s": limit,
                    "flag": limit is not None and mx > WALL_FLAG * limit})
    return out


def _wall_times(db: Path, days: int) -> int:
    rows = wall_times(db, days=days)
    print(f"{'kind':<28} {'runs':>5} {'p95_s':>7} {'max_s':>7} {'limit_s':>7}   (last {days} days; flag: max over "
          f"{WALL_FLAG:.0%} of the limit)")
    for r in rows:
        lim = f"{r['limit_s']:.0f}" if r["limit_s"] is not None else "-"
        flag = f"  OVER {WALL_FLAG:.0%}" if r["flag"] else ""
        print(f"{r['kind']:<28} {r['runs']:>5} {r['p95_s']:>7.0f} {r['max_s']:>7.0f} {lim:>7}{flag}")
    if not rows:
        print("no finished runs in the window")
    return 0


def main(argv: list[str] | None = None) -> int:
    from .schedules import DEFAULT_PATH

    p = argparse.ArgumentParser(prog="python -m ohcamel_quant.jobs", description=__doc__.split("\n\n")[0])
    p.add_argument("--db", type=Path, help="jobs.sqlite (default {data_dir}/jobs.sqlite)")
    p.add_argument("--schedules", type=Path, default=DEFAULT_PATH, help="schedules.yaml (default: the shipped file)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="running, queued and recently settled jobs")
    r = sub.add_parser("run-schedule", help="enqueue a schedules.yaml entry now")
    r.add_argument("name")
    r.add_argument("--params", help="JSON object replacing the entry's params")
    c = sub.add_parser("cancel", help="cancel a queued or running job")
    c.add_argument("job_id")
    wt = sub.add_parser("wall-times", help="per kind, p95 and max wall time beside its wall limit (read-only)")
    wt.add_argument("--days", type=int, default=30)
    sub.add_parser("cancel-burst", help="cancel every live burst job (before a deploy or a rollback)")
    a = p.parse_args(argv)
    db = a.db
    if db is None:
        from .paths import jobs_db_path

        db = jobs_db_path()
    if a.cmd == "wall-times":
        return _wall_times(db, a.days)
    with closing(connect(db)) as conn:
        if a.cmd == "status":
            return _status(conn)
        if a.cmd == "run-schedule":
            return _run_schedule(conn, a.schedules, a.name, a.params)
        if a.cmd == "cancel-burst":
            from .queue import cancel_burst_jobs

            ids = cancel_burst_jobs(conn, now=utcnow())
            print(f"cancelled {len(ids)} live burst job(s)" + (": " + ", ".join(ids) if ids else ""))
            return 0
        return _cancel(conn, a.job_id)


if __name__ == "__main__":
    raise SystemExit(main())
