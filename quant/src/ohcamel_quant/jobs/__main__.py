"""``python -m ohcamel_quant.jobs``: the operator's view of the queue (docs/runbooks/compute.md).

  status                              running and queued jobs, then the ten newest settled
  run-schedule NAME [--params JSON]   enqueue a schedules.yaml entry now, as the scheduler would
  cancel ID                           cancel a queued or running job

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
from pathlib import Path

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
    a = p.parse_args(argv)
    db = a.db
    if db is None:
        from .paths import jobs_db_path

        db = jobs_db_path()
    with closing(connect(db)) as conn:
        if a.cmd == "status":
            return _status(conn)
        if a.cmd == "run-schedule":
            return _run_schedule(conn, a.schedules, a.name, a.params)
        return _cancel(conn, a.job_id)


if __name__ == "__main__":
    raise SystemExit(main())
