"""Lane B, B4: schedules.yaml, the scheduler tick, and the latest read with staleness."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ohcamel_quant.deck.clock import rule_clock
from ohcamel_quant.jobs.admission import decide
from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.kinds import REGISTRY
from ohcamel_quant.jobs.queue import claim_next, get_job, list_jobs
from ohcamel_quant.jobs.schedules import (
    DEFAULT_PATH,
    load_schedules,
    parse_duration,
    read_latest,
    tick,
)


def u(*a):
    return datetime(*a, tzinfo=UTC)


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "schedules.yaml"
    p.write_text(body)
    return p


ENTRY = """
  - name: {name}
    cron: "{cron}"
    kind: ops.selftest
    params: {{tag: {name}}}
    priority: 2
    mem_class: S
    heavy: {heavy}
"""


def sched(tmp_path, *entries, max_age="max_age: {}"):
    return load_schedules(write(tmp_path, max_age + "\nschedules:\n" + "".join(entries)))


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def test_the_shipped_file_loads_and_names_registered_kinds():
    s = load_schedules(DEFAULT_PATH)
    assert s.entries and all(e.kind in REGISTRY for e in s.entries)
    assert all(k in REGISTRY for k in s.max_age_s)
    names = [e.name for e in s.entries]
    assert len(names) == len(set(names))
    assert s.max_age_s["ingest.fred"] == 30 * 3600 and s.max_age_s["ops.selftest"] == 26 * 3600


def test_parse_duration():
    assert parse_duration("45s") == 45 and parse_duration("90m") == 5400
    assert parse_duration("30h") == 108000 and parse_duration("7d") == 604800
    for bad in ("", "30", "h", "-1h", "1.5h", "3w"):
        with pytest.raises(ValueError):
            parse_duration(bad)


@pytest.mark.parametrize("body", [
    "schedules:\n" + ENTRY.format(name="a", cron="0 * * *", heavy="false"),                  # bad cron
    "schedules:\n" + ENTRY.format(name="a", cron="0 * * * *", heavy="maybe"),                # heavy not bool
    "schedules:\n" + ENTRY.format(name="a", cron="0 * * * *", heavy="false") * 2,            # duplicate name
    "schedules:\n" + ENTRY.format(name="a", cron="0 * * * *", heavy="false").replace("ops.selftest", "nope.x"),
    "max_age: {nope.x: 1h}\nschedules: []",                                                 # unknown kind
    "schedules:\n" + ENTRY.format(name="a", cron="0 * * * *", heavy="false").replace("mem_class: S", "mem_class: XL"),
    "schedules:\n" + ENTRY.format(name="a", cron="0 * * * *", heavy="false").replace("priority: 2", "priority: 0"),
])
def test_invalid_files_are_refused(tmp_path, body):
    with pytest.raises(ValueError):
        load_schedules(write(tmp_path, body))


def test_a_missed_run_after_downtime_is_enqueued_once(tmp_path, conn):
    s = sched(tmp_path, ENTRY.format(name="hourly", cron="0 * * * *", heavy="false"))
    assert [x[0] for x in tick(conn, s, u(2026, 10, 5, 10, 0, 30))] == ["hourly"]  # the 10:00 run
    # Worker down from 10:01 to 17:20: seven hourly runs missed; one job, for 17:00.
    first = get_job(conn, list_jobs(conn)[0].id)
    claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=u(2026, 10, 5, 10, 1))
    conn.execute("UPDATE jobs SET state='done', finished_at=? WHERE id=?", (iso(u(2026, 10, 5, 10, 2)), first.id))
    out = tick(conn, s, u(2026, 10, 5, 17, 20))
    assert len(out) == 1 and out[0][2] is True
    runs = conn.execute("SELECT scheduled_for FROM schedule_runs ORDER BY scheduled_for").fetchall()
    assert [r[0] for r in runs] == [iso(u(2026, 10, 5, 10, 0)), iso(u(2026, 10, 5, 17, 0))]
    assert tick(conn, s, u(2026, 10, 5, 17, 25)) == []  # idempotent per (name, scheduled_for)
    assert len(list_jobs(conn)) == 2


def test_dst_night_enqueues_exactly_once(tmp_path, conn):
    s = sched(tmp_path, ENTRY.format(name="night", cron="30 1 * * *", heavy="false"))
    # Yesterday's run (2026-10-31 01:30 EDT = 05:30Z) is already recorded, so the 26 h catch-up
    # window does not reach back to it.
    conn.execute("INSERT INTO schedule_runs VALUES (?,?,?,?)",
                 ("night", iso(u(2026, 10, 31, 5, 30)), "PREV", iso(u(2026, 10, 31, 5, 30))))
    # Tick every 10 minutes from 2026-11-01 03:00Z (23:00 EDT Oct 31) to 10:00Z (05:00 EST).
    t, created = u(2026, 11, 1, 3, 0), 0
    while t <= u(2026, 11, 1, 10, 0):
        created += sum(1 for *_, c in tick(conn, s, t) if c)
        t += timedelta(minutes=10)
    assert created == 1
    new = conn.execute("SELECT scheduled_for FROM schedule_runs WHERE job_id != 'PREV'").fetchall()
    assert [r[0] for r in new] == [iso(u(2026, 11, 1, 5, 30))]  # the first (EDT) 01:30


def test_a_fresh_install_runs_last_nights_entry_once(tmp_path, conn):
    s = sched(tmp_path, ENTRY.format(name="nightly", cron="30 6 * * *", heavy="false"))
    out = tick(conn, s, u(2026, 10, 6, 15, 0))  # 11:00 EDT; today's 06:30 EDT = 10:30Z is due
    assert len(out) == 1
    assert conn.execute("SELECT scheduled_for FROM schedule_runs").fetchone()[0] == iso(u(2026, 10, 6, 10, 30))


def test_heavy_entry_at_10_is_refused_then_runs_at_1605(tmp_path, conn):
    s = sched(tmp_path, ENTRY.format(name="heavy10", cron="0 10 * * 1-5", heavy="true"))
    t_10 = u(2026, 10, 6, 14, 0, 30)  # Tuesday 10:00:30 EDT
    [(name, job_id, created)] = tick(conn, s, t_10)
    big_host = {"latest": {"mem_available": 2 * 2**30}}
    a = decide(t_10, big_host, rule_clock(t_10))
    assert claim_next(conn, allowed_classes=a.allowed_classes, allow_heavy=a.allow_heavy, now=t_10) is None
    t_1605 = u(2026, 10, 6, 20, 5, 0)  # 16:05:00 EDT
    a = decide(t_1605, big_host, rule_clock(t_1605))
    got = claim_next(conn, allowed_classes=a.allowed_classes, allow_heavy=a.allow_heavy, now=t_1605)
    assert got is not None and got.id == job_id and got.heavy is True


def test_read_latest_uses_the_declared_max_age(tmp_path, conn):
    s = sched(tmp_path, max_age="max_age: {ingest.fred: 30h}")
    art = tmp_path / "artifacts" / "ingest.fred" / "A1"
    art.mkdir(parents=True)
    (art / "manifest.json").write_text('{"id": "A1"}')
    t = u(2026, 10, 6, 10, 30)
    conn.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?)", ("A1", "ingest.fred", "h", iso(t), str(art), "2026-10-05"))
    assert read_latest(conn, "ingest.fred", "h", now=t + timedelta(hours=29), schedules=s)["stale"] is False
    assert read_latest(conn, "ingest.fred", "h", now=t + timedelta(hours=31), schedules=s) == {
        "manifest": {"id": "A1"}, "stale": True}
    # ops.selftest declares no max age in this file: never stale.
    conn.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?)", ("B1", "ops.selftest", "h", iso(t), str(art), None))
    assert read_latest(conn, "ops.selftest", "h", now=t + timedelta(days=9), schedules=s)["stale"] is False


def test_worker_runs_its_scheduler(tmp_path):
    from ohcamel_quant.jobs.admission import admit_all
    from ohcamel_quant.jobs.worker import Worker, WorkerConfig

    path = write(tmp_path, "max_age: {}\nschedules:\n" + ENTRY.format(name="every", cron="* * * * *", heavy="false"))
    cfg = WorkerConfig(db_path=tmp_path / "jobs.sqlite", artifacts_root=tmp_path / "artifacts", poll_s=0.05,
                       heartbeat_s=0.2, grace_s=2.0, heartbeat_file=None, scheduler=True, schedules_path=path)
    w = Worker(cfg, admit=admit_all)
    t = threading.Thread(target=w.run)
    t.start()
    conn = connect(cfg.db_path)
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            done = [j for j in list_jobs(conn) if j.submitted_by == "schedule:every" and j.state == "done"]
            if done:
                break
            time.sleep(0.1)
        assert done
    finally:
        w.request_stop()
        t.join(20)
        conn.close()
