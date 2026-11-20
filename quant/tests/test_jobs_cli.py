"""The operator's job CLI (``python -m ohcamel_quant.jobs``), the commands docs/runbooks/compute.md uses:
``status``, ``run-schedule NAME [--params JSON]`` and ``cancel ID``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ohcamel_quant.jobs.__main__ import main
from ohcamel_quant.jobs.db import connect
from ohcamel_quant.jobs.queue import claim_next, get_job, list_jobs
from ohcamel_quant.jobs.schedules import load_schedules

SCHEDULES = """
max_age: {}
schedules:
  - name: nightly-thing
    cron: "5 4 * * *"
    kind: ops.selftest
    params: {tag: nightly}
    priority: 2
    mem_class: S
    heavy: true
"""


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    s = tmp_path / "schedules.yaml"
    s.write_text(SCHEDULES)
    return tmp_path / "jobs.sqlite", s


def run(paths: tuple[Path, Path], *args: str) -> int:
    db, sched = paths
    return main(["--db", str(db), "--schedules", str(sched), *args])


def test_run_schedule_enqueues_the_entry_as_the_scheduler_would(paths, capsys):
    assert run(paths, "run-schedule", "nightly-thing") == 0
    with connect(paths[0]) as conn:
        [job] = list_jobs(conn)
    assert (job.kind, job.params, job.priority, job.mem_class, job.heavy, job.submitted_by) == (
        "ops.selftest", {"tag": "nightly"}, 2, "S", True, "schedule:nightly-thing")
    out = capsys.readouterr().out
    assert job.id in out and "queued" in out


def test_run_schedule_does_not_touch_the_schedulers_idempotency_record(paths):
    run(paths, "run-schedule", "nightly-thing")
    with connect(paths[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schedule_runs").fetchone()[0] == 0


def test_run_schedule_twice_returns_the_live_job_not_a_second(paths, capsys):
    run(paths, "run-schedule", "nightly-thing")
    assert run(paths, "run-schedule", "nightly-thing") == 0
    with connect(paths[0]) as conn:
        assert len(list_jobs(conn)) == 1
    assert "already live" in capsys.readouterr().out


def test_run_schedule_params_override_replaces_the_entrys_params(paths):
    assert run(paths, "run-schedule", "nightly-thing", "--params", json.dumps({"tag": "manual"})) == 0
    with connect(paths[0]) as conn:
        [job] = list_jobs(conn)
    assert job.params == {"tag": "manual"} and job.submitted_by == "schedule:nightly-thing"


def test_run_schedule_unknown_name_exits_2_and_lists_the_names(paths, capsys):
    assert run(paths, "run-schedule", "nope") == 2
    err = capsys.readouterr().err
    assert "unknown schedule 'nope'" in err and "nightly-thing" in err


def test_run_schedule_bad_params_exit_2(paths, capsys):
    assert run(paths, "run-schedule", "nightly-thing", "--params", "[1, 2]") == 2
    assert "JSON object" in capsys.readouterr().err
    assert run(paths, "run-schedule", "nightly-thing", "--params", "{not json") == 2


def test_status_lists_running_and_queued_jobs_with_progress(paths, capsys):
    run(paths, "run-schedule", "nightly-thing")
    run(paths, "run-schedule", "nightly-thing", "--params", '{"tag": "b"}')
    from ohcamel_quant.jobs.db import utcnow

    with connect(paths[0]) as conn:
        claimed = claim_next(conn, allowed_classes={"S"}, allow_heavy=True, now=utcnow())
    capsys.readouterr()
    assert run(paths, "status") == 0
    out = capsys.readouterr().out
    assert "RUNNING 1" in out and "QUEUED 1" in out and claimed.id in out


def test_status_on_an_empty_queue_says_so(paths, capsys):
    assert run(paths, "status") == 0
    out = capsys.readouterr().out
    assert "RUNNING 0" in out and "QUEUED 0" in out


def test_cancel_a_queued_job(paths, capsys):
    run(paths, "run-schedule", "nightly-thing")
    with connect(paths[0]) as conn:
        [job] = list_jobs(conn)
    assert run(paths, "cancel", job.id) == 0
    with connect(paths[0]) as conn:
        assert get_job(conn, job.id).state == "cancelled"


def test_cancel_an_unknown_job_exits_1(paths, capsys):
    assert run(paths, "cancel", "01NOPE") == 1
    assert "no job" in capsys.readouterr().err


def test_every_shipped_schedule_name_is_accepted(tmp_path):
    from ohcamel_quant.jobs.schedules import DEFAULT_PATH

    entries = load_schedules(DEFAULT_PATH).entries
    db = tmp_path / "jobs.sqlite"
    for e in entries:
        assert main(["--db", str(db), "run-schedule", e.name]) == 0
    with connect(db) as conn:
        # two entries with the same kind and params (e.g. the minute bars' nightly and intraday) share one live job
        assert {j.kind for j in list_jobs(conn, limit=500)} == {e.kind for e in entries}


def test_wall_times_prints_p95_and_max_beside_each_kinds_limit_and_flags_above_70_percent(paths, capsys):
    """Fix round 1, I3: the owner's read-only check before the deploy that brings the wall limits -- per kind, the
    p95 and the max wall time of the last 30 days' finished runs from jobs.sqlite, beside wall_limit_s, and a flag
    on any kind whose slowest run took more than 70% of its limit."""
    import sqlite3
    from datetime import UTC, datetime, timedelta

    db, _ = paths
    connect(db).close()                                          # the schema, as the worker makes it
    now = datetime.now(UTC)
    rows = []

    def add(kind, mem, seconds, days_ago=1, state="done", params="{}"):
        start = now - timedelta(days=days_ago)
        rows.append((f"{kind}-{len(rows)}", kind, params, "h", 2, mem, 0, state, start.isoformat(),
                     start.isoformat(), (start + timedelta(seconds=seconds)).isoformat(), 1, "schedule:x"))

    for s in range(1, 21):
        add("ops.selftest", "S", s)                              # p95 19 s, max 20 s of 600: fine
    add("cov.league", "M", 2000)                                 # 74% of 2700: flagged
    add("cov.league", "M", 600)
    add("cov.league", "M", 2690, days_ago=40)                    # outside the 30 days: not counted
    add("farm.sweep", "L", 5000, params='{"budget_minutes": 90}')  # 83% of 6000: flagged
    add("ingest.bars_daily", "S", 700, state="failed")           # a run killed at its limit is counted too
    with sqlite3.connect(db) as c:
        c.executemany("INSERT INTO jobs (id, kind, params, params_hash, priority, mem_class, heavy, state, "
                      "submitted_at, started_at, finished_at, attempts, submitted_by) VALUES "
                      "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    before = db.read_bytes()
    assert run(paths, "wall-times") == 0
    out = capsys.readouterr().out
    lines = {ln.split()[0]: ln for ln in out.splitlines() if ln and ln.split()[0] in
             ("ops.selftest", "cov.league", "farm.sweep", "ingest.bars_daily")}
    assert lines["ops.selftest"].split()[1:5] == ["20", "19", "20", "600"] and "OVER 70%" not in lines["ops.selftest"]
    assert lines["cov.league"].split()[1:5] == ["2", "1930", "2000", "2700"] and "OVER 70%" in lines["cov.league"]
    assert lines["farm.sweep"].split()[4] == "6000" and "OVER 70%" in lines["farm.sweep"]
    assert lines["ingest.bars_daily"].split()[3:5] == ["700", "600"] and "OVER 70%" in lines["ingest.bars_daily"]
    assert db.read_bytes() == before                             # read-only
