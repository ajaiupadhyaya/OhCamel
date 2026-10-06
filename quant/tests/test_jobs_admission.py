"""Lane B, B3: admission control -- memory budget from hostd, heavy jobs outside the session window.

A fake ``read_host`` and the wall-clock rule (deck/clock.rule_clock) make
every case deterministic. Byte values are derived in the comments.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ohcamel_quant.api.routers.ops import HostdUnavailable
from ohcamel_quant.deck.clock import rule_clock
from ohcamel_quant.jobs.admission import MiB, class_budget, decide, in_heavy_window, make_admit
from ohcamel_quant.jobs.db import connect, utcnow
from ohcamel_quant.jobs.queue import claim_next, enqueue

TUE_1400Z = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)  # Tuesday 10:00 EDT: inside the window
SAT_1400Z = datetime(2026, 10, 10, 14, 0, tzinfo=UTC)  # Saturday: not a session day


def host(avail):
    return {"version": 1, "latest": {"mem_available": avail}}


def test_class_budgets_are_the_contract_classes():
    assert (class_budget("S"), class_budget("M"), class_budget("L")) == (256 * MiB, 640 * MiB, 1152 * MiB)


@pytest.mark.parametrize("avail,allowed", [
    # 1.2 GiB = int(1.2 * 2**30) = 1288490188 B; minus 512 MiB (536870912) = 751619276 B = 716.8 MiB
    # >= S 256 and M 640, < L 1152: the gate's "1280 MiB-class job refused at 1.2 GiB".
    (1288490188, {"S", "M"}),
    # II.6's example reading 1530000000 B - 536870912 = 993129088 B = 947.1 MiB: S, M.
    (1530000000, {"S", "M"}),
    # 2 GiB - 512 MiB = 1536 MiB >= 1152: everything.
    (2 * 2**30, {"S", "M", "L"}),
    # exactly 512 + 256 MiB: S fits (>=), M does not.
    ((512 + 256) * MiB, {"S"}),
    ((512 + 255) * MiB, set()),
])
def test_memory_budget(avail, allowed):
    a = decide(SAT_1400Z, host(avail), rule_clock(SAT_1400Z))
    assert a.allowed_classes == frozenset(allowed)
    assert a.allow_heavy is True and a.threads == 2


def test_unreadable_host_starts_nothing():
    a = decide(SAT_1400Z, None, rule_clock(SAT_1400Z), host_error="hostd is unreachable")
    assert a.allowed_classes == frozenset() and "hostd is unreachable" in a.reason
    a = decide(SAT_1400Z, {"version": 1, "latest": None}, rule_clock(SAT_1400Z))
    assert a.allowed_classes == frozenset() and "mem_available" in a.reason


@pytest.mark.parametrize("when,inside", [
    (datetime(2026, 10, 6, 13, 24, 59, tzinfo=UTC), False),  # 09:24:59 EDT
    (datetime(2026, 10, 6, 13, 25, 0, tzinfo=UTC), True),    # 09:25:00 EDT
    (datetime(2026, 10, 6, 20, 4, 59, tzinfo=UTC), True),    # 16:04:59 EDT
    (datetime(2026, 10, 6, 20, 5, 0, tzinfo=UTC), False),    # 16:05:00 EDT
    (datetime(2026, 12, 1, 14, 25, 0, tzinfo=UTC), True),    # 09:25 EST (UTC-5), Tuesday
    (datetime(2026, 12, 1, 13, 25, 0, tzinfo=UTC), False),   # 08:25 EST
    (SAT_1400Z, False),                                      # Saturday 10:00 EDT
])
def test_heavy_window(when, inside):
    assert in_heavy_window(when, rule_clock(when)) is inside
    a = decide(when, host(2 * 2**30), rule_clock(when))
    assert a.allow_heavy is (not inside)
    assert a.threads == (1 if inside else 2)


def test_make_admit_reads_host_and_clock():
    calls = []

    def fake_read_host():
        calls.append(1)
        return host(2 * 2**30)

    admit = make_admit(read_host=fake_read_host, clock=rule_clock)
    a = admit(TUE_1400Z)
    assert calls == [1] and a.allowed_classes == frozenset({"S", "M", "L"}) and not a.allow_heavy and a.threads == 1

    def down():
        raise HostdUnavailable("hostd is unreachable on /v1/host: ConnectError")

    assert make_admit(read_host=down, clock=rule_clock)(TUE_1400Z).allowed_classes == frozenset()


def test_an_l_job_waits_while_smaller_jobs_behind_it_run(tmp_path):
    conn = connect(tmp_path / "jobs.sqlite")
    big, _ = enqueue(conn, kind="ops.selftest", params={"n": 1}, priority=0, mem_class="L", heavy=False,
                     submitted_by="schedule:t", now=utcnow())
    small, _ = enqueue(conn, kind="ops.selftest", params={"n": 2}, priority=2, mem_class="S", heavy=False,
                       submitted_by="schedule:t", now=utcnow())
    a = decide(SAT_1400Z, host(1288490188), rule_clock(SAT_1400Z))  # 1.2 GiB available
    got = claim_next(conn, allowed_classes=a.allowed_classes, allow_heavy=a.allow_heavy, now=utcnow())
    assert got.id == small.id
    assert claim_next(conn, allowed_classes=a.allowed_classes, allow_heavy=a.allow_heavy, now=utcnow()) is None
    conn.close()


def test_worker_gives_one_thread_inside_the_window(tmp_path):
    import pandas as pd

    from ohcamel_quant.jobs.worker import Worker, WorkerConfig

    cfg = WorkerConfig(db_path=tmp_path / "jobs.sqlite", artifacts_root=tmp_path / "artifacts", poll_s=0.05,
                       heartbeat_s=0.2, grace_s=2.0, heartbeat_file=None, scheduler=False)
    conn = connect(cfg.db_path)
    j, _ = enqueue(conn, kind="ops.selftest", params={"tag": "w"}, priority=2, mem_class="S", heavy=False,
                   submitted_by="schedule:t", now=TUE_1400Z)
    w = Worker(cfg, admit=make_admit(read_host=lambda: host(2 * 2**30), clock=rule_clock), now=lambda: TUE_1400Z)
    assert w.run_once().status == "done"
    facts = dict(pd.read_parquet(cfg.artifacts_root / "ops.selftest" / j.id / "checks.parquet")
                 .itertuples(index=False, name=None))
    assert facts["threads"] == "1"
    conn.close()
