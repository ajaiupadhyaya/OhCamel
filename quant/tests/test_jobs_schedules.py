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


def test_corp_actions_run_weekdays_at_1820_before_the_bars_and_alone_in_their_minute():
    """Completion task 3.2: ingest.corp_actions, weekdays 18:20, class S, before the 18:30 daily bars it is
    checked against, with no other job at 18:20 (the nightly backup is 06:30 UTC, deploy/systemd)."""
    from zoneinfo import ZoneInfo

    from ohcamel_quant.jobs.cron import fire_times, parse_cron

    ny = ZoneInfo("America/New_York")
    s = load_schedules(DEFAULT_PATH)
    (e,) = [x for x in s.entries if x.kind == "ingest.corp_actions"]
    assert (e.cron, e.mem_class, e.heavy, e.priority, e.params) == ("20 18 * * 1-5", "S", False, 2, {})
    spec = REGISTRY["ingest.corp_actions"]
    assert (spec.mem_class, spec.heavy, spec.public, spec.retries) == ("S", False, False, 2)
    assert s.max_age_s["ingest.corp_actions"] == 78 * 3600
    monday = datetime(2026, 11, 16).date()
    at = {(x.name, (t.astimezone(ny).hour, t.astimezone(ny).minute))
          for x in s.entries for t in fire_times(parse_cron(x.cron), monday)}
    assert {n for n, hm in at if hm == (18, 20)} == {e.name}
    (bars,) = [x for x in s.entries if x.kind == "ingest.bars_daily"]
    assert [hm for n, hm in at if n == bars.name] == [(18, 30)]



def test_option_chains_are_pulled_at_1545_and_1620_new_york_in_both_offsets():
    """Completion task 3.4: one ingest.option_snapshots pull before the close (15:42 New York -- the "15:45 pull" --
    priority 1 like the other intraday entries, light so the 09:25-16:05 heavy window never holds it, and in a
    minute of its own: risk.mc_intraday and signals.overlay fire at 15:45) and one after it (16:20), both every
    weekday, at the same New York minute in a winter (EST) and a summer (EDT) week."""
    from zoneinfo import ZoneInfo

    from ohcamel_quant.jobs.cron import fire_times, parse_cron
    from ohcamel_quant.jobs.provenance import WAREHOUSE_WRITERS
    from ohcamel_quant.warehouse.ingest.options import OPTION_UNDERLYINGS

    ny = ZoneInfo("America/New_York")
    s = load_schedules(DEFAULT_PATH)
    pulls = {e.params.get("pull"): e for e in s.entries if e.kind == "ingest.option_snapshots"}
    assert set(pulls) == {"1545", "1620"}
    pre, post = pulls["1545"], pulls["1620"]
    assert (pre.cron, pre.priority, pre.mem_class, pre.heavy) == ("42 15 * * 1-5", 1, "S", False)
    assert (post.cron, post.priority, post.mem_class, post.heavy) == ("20 16 * * 1-5", 2, "S", False)
    assert pre.params["underlyings"] == post.params["underlyings"] == list(OPTION_UNDERLYINGS)
    assert "ingest.option_snapshots" in WAREHOUSE_WRITERS     # so the export-window test below covers both
    for monday in (datetime(2026, 1, 5).date(), datetime(2026, 7, 6).date()):
        for e, hm in ((pre, (15, 42)), (post, (16, 20))):
            fires = [t.astimezone(ny) for i in range(7)
                     for t in fire_times(parse_cron(e.cron), monday + timedelta(days=i))]
            assert sorted(f.weekday() for f in fires) == [0, 1, 2, 3, 4]
            assert {(f.hour, f.minute) for f in fires} == {hm}
    # no other priority-1 job shares the pre-close pull's minute (one worker runs one job at a time)
    monday = datetime(2026, 11, 16).date()
    at = {(x.name, (t.astimezone(ny).hour, t.astimezone(ny).minute))
          for x in s.entries if x.priority == 1 for t in fire_times(parse_cron(x.cron), monday)}
    assert {n for n, hm in at if hm == (15, 42)} == {pre.name}


def test_the_surface_league_runs_weekdays_at_2110_class_m_heavy_after_the_surface_history():
    """Completion task 4.5: vol.surface_league, weekdays 21:10 New York, class M, heavy, nightly priority -- ten
    minutes after vol.surface_history (21:00), which reads the same snapshots."""
    from zoneinfo import ZoneInfo

    from ohcamel_quant.jobs.cron import fire_times, parse_cron

    ny = ZoneInfo("America/New_York")
    s = load_schedules(DEFAULT_PATH)
    (e,) = [x for x in s.entries if x.kind == "vol.surface_league"]
    assert (e.cron, e.mem_class, e.heavy, e.priority) == ("10 21 * * 1-5", "M", True, 2)
    spec = REGISTRY["vol.surface_league"]
    assert (spec.mem_class, spec.heavy, spec.public) == ("M", True, False)
    assert s.max_age_s["vol.surface_league"] == 78 * 3600
    for monday in (datetime(2026, 1, 5).date(), datetime(2026, 7, 6).date()):
        fires = [t.astimezone(ny) for i in range(7) for t in fire_times(parse_cron(e.cron), monday + timedelta(days=i))]
        assert sorted(f.weekday() for f in fires) == [0, 1, 2, 3, 4]
        assert {(f.hour, f.minute) for f in fires} == {(21, 10)}


def test_the_beta_league_runs_saturdays_at_0830_class_m_heavy():
    """Completion task 4.7: beta.league, Saturday 08:30 New York, class M, heavy, weekly priority and max age."""
    from zoneinfo import ZoneInfo

    from ohcamel_quant.jobs.cron import fire_times, parse_cron

    ny = ZoneInfo("America/New_York")
    s = load_schedules(DEFAULT_PATH)
    (e,) = [x for x in s.entries if x.kind == "beta.league"]
    assert (e.cron, e.mem_class, e.heavy, e.priority) == ("30 8 * * 6", "M", True, 2)
    spec = REGISTRY["beta.league"]
    assert (spec.mem_class, spec.heavy, spec.public) == ("M", True, False)
    assert s.max_age_s["beta.league"] == 204 * 3600
    for monday in (datetime(2026, 1, 5).date(), datetime(2026, 7, 6).date()):
        fires = [t.astimezone(ny) for i in range(7) for t in fire_times(parse_cron(e.cron), monday + timedelta(days=i))]
        assert [(f.weekday(), f.hour, f.minute) for f in fires] == [(5, 8, 30)]


def test_no_warehouse_writer_is_scheduled_inside_the_nightly_export_window():
    """Task 3.3 fix round 1: the warehouse export opens it read-only at the backup timer's time (06:30 UTC) and
    holds the shared lock for up to EXPORT_BUDGET_MIN; a writer that starts then waits 30 s and fails its keys.
    So no warehouse-writing cron may fire inside [timer, timer + budget) in either New York offset (EST, EDT);
    a writer already running when the export starts is waited for by the export itself."""
    import re
    from datetime import date, timedelta

    from ohcamel_quant.config import REPO_ROOT
    from ohcamel_quant.jobs.cron import fire_times, parse_cron
    from ohcamel_quant.jobs.provenance import WAREHOUSE_WRITERS
    from ohcamel_quant.jobs.schedules import load_schedules
    from ohcamel_quant.warehouse.export import EXPORT_BUDGET_MIN

    timer = (REPO_ROOT / "deploy/systemd/ohcamel-backup.timer").read_text()
    h, m = map(int, re.search(r"^OnCalendar=\*-\*-\* (\d\d):(\d\d):00 UTC$", timer, re.M).groups())
    start, end = h * 60 + m, h * 60 + m + EXPORT_BUDGET_MIN
    clashes = []
    for e in load_schedules().entries:
        if e.kind not in WAREHOUSE_WRITERS:
            continue
        c = parse_cron(e.cron)
        for first in (date(2026, 1, 5), date(2026, 7, 6)):          # a winter week (EST), a summer week (EDT)
            for i in range(7):
                for t in fire_times(c, first + timedelta(days=i)):
                    minute = t.hour * 60 + t.minute
                    if start <= minute < end or start <= minute + 1440 < end:
                        clashes.append(f"{e.name} {t.isoformat()}")
    assert not clashes, f"warehouse writers inside the export window {h:02d}:{m:02d}+{EXPORT_BUDGET_MIN}m UTC: {clashes}"
    assert WAREHOUSE_WRITERS and "ingest.fred" not in WAREHOUSE_WRITERS   # ingest.fred writes an artifact only


def test_the_backup_catch_up_holds_for_every_warehouse_writer_and_the_session_window():
    """Completion task 3.6: deploy/backup.sh --catch-up starts a missed backup only outside the trading session
    window (09:25-17:10 New York) and not within the export's window before a warehouse writer's schedule. Its list
    of writers (CATCHUP_WRITERS, New York times) must be exactly schedules.yaml's warehouse-writing entries, its hold
    the export's budget, and its slot the nightly timer's 06:30 UTC."""
    import re

    from ohcamel_quant.config import REPO_ROOT
    from ohcamel_quant.jobs.cron import parse_cron
    from ohcamel_quant.jobs.provenance import WAREHOUSE_WRITERS
    from ohcamel_quant.jobs.schedules import load_schedules
    from ohcamel_quant.warehouse.export import EXPORT_BUDGET_MIN

    def expand(field, top):      # the forms schedules.yaml's writers use: N, */N, A-B
        if field.startswith("*/"):
            return range(0, top, int(field[2:]))
        if "-" in field:
            a, b = map(int, field.split("-"))
            return range(a, b + 1)
        return [int(field)]

    script = (REPO_ROOT / "deploy/backup.sh").read_text()
    shell = re.search(r'^CATCHUP_WRITERS="([^"]+)"$', script, re.M).group(1).split()
    want = set()
    for e in load_schedules().entries:
        if e.kind not in WAREHOUSE_WRITERS:
            continue
        parse_cron(e.cron)
        minute, hour, dom, month, dow = e.cron.split()
        assert (dom, month) == ("*", "*") and (dow in ("*", "1-5") or dow.isdigit()), e.cron  # the shell reads no more
        want |= {f"{dow}@{h:02d}:{m:02d}" for h in expand(hour, 24) for m in expand(minute, 60)}
    assert len(shell) == len(set(shell))
    shell = set(shell)
    assert shell == want, f"backup.sh CATCHUP_WRITERS {sorted(shell)} != schedules.yaml writers {sorted(want)}"
    assert int(re.search(r"^CATCHUP_HOLD_MIN=(\d+)$", script, re.M).group(1)) == EXPORT_BUDGET_MIN
    assert int(re.search(r"^CATCHUP_SESSION_FROM=(\d+)$", script, re.M).group(1)) == 9 * 60 + 25
    assert int(re.search(r"^CATCHUP_SESSION_TO=(\d+)$", script, re.M).group(1)) == 17 * 60 + 10
    timer = (REPO_ROOT / "deploy/systemd/ohcamel-backup.timer").read_text()
    h, m = map(int, re.search(r"^OnCalendar=\*-\*-\* (\d\d):(\d\d):00 UTC$", timer, re.M).groups())
    assert int(re.search(r"^BACKUP_SLOT_UTC_S=(\d+)$", script, re.M).group(1)) == (h * 60 + m) * 60
