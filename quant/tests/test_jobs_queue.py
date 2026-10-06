"""Lane B, B1: the job queue in jobs.sqlite (compute plan contract II.2).

Times are fixed instants; nothing here sleeps. The two-claimer test uses four
real threads on four connections to one WAL database.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.kinds import retries_for
from ohcamel_quant.jobs.queue import (
    cancel,
    claim_next,
    enqueue,
    events_after,
    fail,
    finish,
    get_job,
    heartbeat,
    list_jobs,
    params_hash,
    requeue,
    requeue_stale,
    set_progress,
)
from ohcamel_quant.jobs.ulid import new_ulid

T0 = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
ALL = frozenset({"S", "M", "L"})


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _q(conn, kind="ops.selftest", params=None, priority=2, mem_class="S", heavy=False,
       now=T0, by="schedule:test", client=None):
    return enqueue(conn, kind=kind, params={} if params is None else params, priority=priority,
                   mem_class=mem_class, heavy=heavy, submitted_by=by, now=now, client=client)


def _claim(conn, now=T0):
    return claim_next(conn, allowed_classes=ALL, allow_heavy=True, now=now)


def _art(job, finished_at):
    return {"id": job.id, "kind": job.kind, "params_hash": job.params_hash, "finished_at": iso(finished_at),
            "path": f"/data/artifacts/{job.kind}/{job.id}", "data_asof": "2026-10-06"}


def test_ulid_is_26_crockford_chars_and_sorts_by_time():
    a, b = new_ulid(1_790_000_000_000), new_ulid(1_790_000_000_001)
    assert len(a) == 26 and set(a) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert a < b
    # The first 10 characters encode the 48-bit millisecond timestamp alone.
    assert new_ulid(1_790_000_000_000)[:10] == a[:10]
    with pytest.raises(ValueError):
        new_ulid(2**48)


def test_schema_is_contract_ii2_and_wal(conn):
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(jobs)")]
    assert cols == ["id", "kind", "params", "params_hash", "priority", "mem_class", "heavy", "state",
                    "submitted_at", "started_at", "finished_at", "attempts", "error", "artifact_id",
                    "cpu_seconds", "peak_rss_bytes", "submitted_by"]
    art = [r["name"] for r in conn.execute("PRAGMA table_info(artifacts)")]
    assert art == ["id", "kind", "params_hash", "finished_at", "path", "data_asof"]
    idx = {r["name"] for r in conn.execute("PRAGMA index_list(jobs)")}
    assert {"jobs_state", "jobs_live_dedupe"} <= idx
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_iso_is_fixed_width_utc():
    assert iso(datetime(2026, 10, 6, 10, 0, 0, 123456, tzinfo=UTC)) == "2026-10-06T10:00:00.123Z"
    with pytest.raises(ValueError):
        iso(datetime(2026, 10, 6))


def test_params_hash_is_canonical_sha256():
    assert params_hash({"b": 1, "a": [1, 2]}) == params_hash({"a": [1, 2], "b": 1})
    assert params_hash({"a": 1}) != params_hash({"a": 2})
    # hashlib.sha256(b'{"a":1}').hexdigest(): sorted keys, no spaces.
    assert params_hash({"a": 1}) == "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"


def test_retries_are_two_for_ingest_and_zero_for_others():
    assert retries_for("ingest.fred") == 2
    assert retries_for("ingest.bars_daily") == 2
    assert retries_for("risk.mc_atlas") == 0
    assert retries_for("ops.selftest") == 0


def test_enqueue_dedupes_on_kind_and_params_hash_while_live(conn):
    j1, c1 = _q(conn, params={"x": 1})
    j2, c2 = _q(conn, params={"x": 1})
    assert c1 and not c2 and j1.id == j2.id
    j3, c3 = _q(conn, kind="ingest.fred", params={"x": 1})
    assert c3 and j3.id != j1.id
    cancel(conn, j1.id, now=T0)  # no longer live: the same request queues afresh
    j4, c4 = _q(conn, params={"x": 1})
    assert c4 and j4.id != j1.id


def test_enqueue_rejects_bad_fields(conn):
    with pytest.raises(ValueError):
        _q(conn, priority=3)
    with pytest.raises(ValueError):
        _q(conn, mem_class="XL")
    with pytest.raises(ValueError):
        _q(conn, by="someone")
    with pytest.raises(ValueError):
        _q(conn, params=[1, 2])
    with pytest.raises(ValueError):
        _q(conn, params={"x": float("nan")})


def test_claim_takes_highest_priority_then_oldest(conn):
    nightly, _ = _q(conn, params={"n": 1}, priority=2, now=T0)
    inter, _ = _q(conn, params={"n": 2}, priority=0, now=T0 + timedelta(seconds=5))
    intraday, _ = _q(conn, params={"n": 3}, priority=1, now=T0 + timedelta(seconds=1))
    got = [_claim(conn, T0 + timedelta(minutes=1)).id for _ in range(3)]
    assert got == [inter.id, intraday.id, nightly.id]
    assert _claim(conn) is None


def test_claim_marks_running_and_counts_attempts(conn):
    j, _ = _q(conn)
    c = _claim(conn, T0 + timedelta(seconds=30))
    assert (c.id, c.state, c.attempts, c.started_at) == (j.id, "running", 1, iso(T0 + timedelta(seconds=30)))


def test_claim_respects_the_admission_filter(conn):
    _q(conn, params={"n": 1}, mem_class="L", priority=0)
    heavy, _ = _q(conn, params={"n": 2}, heavy=True, priority=0)
    small, _ = _q(conn, params={"n": 3}, priority=2)
    sm = frozenset({"S", "M"})
    assert claim_next(conn, allowed_classes=sm, allow_heavy=False, now=T0).id == small.id
    assert claim_next(conn, allowed_classes=sm, allow_heavy=False, now=T0) is None
    assert claim_next(conn, allowed_classes=frozenset(), allow_heavy=True, now=T0) is None
    assert claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True, now=T0).id == heavy.id


def test_two_claimers_never_get_the_same_job(tmp_path):
    path = tmp_path / "jobs.sqlite"
    seed = connect(path)
    for n in range(200):
        _q(seed, params={"n": n})
    seed.close()
    claimed: list[list[str]] = [[] for _ in range(4)]

    def claimer(i: int) -> None:
        c = connect(path)
        try:
            while (j := claim_next(c, allowed_classes=ALL, allow_heavy=True, now=T0)) is not None:
                claimed[i].append(j.id)
        finally:
            c.close()

    threads = [threading.Thread(target=claimer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    flat = [x for xs in claimed for x in xs]
    assert len(flat) == 200 and len(set(flat)) == 200


def test_finish_records_artifact_and_measurements(conn):
    j, _ = _q(conn)
    _claim(conn)
    t1 = T0 + timedelta(minutes=2)
    assert finish(conn, j.id, artifact=_art(j, t1), cpu_seconds=1.5, peak_rss_bytes=123_000_000, now=t1)
    d = get_job(conn, j.id)
    assert (d.state, d.artifact_id, d.cpu_seconds, d.peak_rss_bytes, d.finished_at) == (
        "done", j.id, 1.5, 123_000_000, iso(t1))
    assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1


def test_finish_refuses_a_job_cancelled_meanwhile(conn):
    j, _ = _q(conn)
    _claim(conn)
    cancel(conn, j.id, now=T0)
    assert finish(conn, j.id, artifact=_art(j, T0), cpu_seconds=1.0, peak_rss_bytes=1, now=T0) is False
    assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert get_job(conn, j.id).state == "cancelled"


@pytest.mark.parametrize("kind,states", [("ingest.fred", ["queued", "queued", "failed"]),
                                         ("ops.selftest", ["failed"])])
def test_fail_retries_ingest_twice_and_others_never(conn, kind, states):
    j, _ = _q(conn, kind=kind)
    seen = []
    for _ in states:
        assert _claim(conn) is not None
        seen.append(fail(conn, j.id, error="RuntimeError: vendor down", retries=retries_for(kind),
                         cpu_seconds=0.1, peak_rss_bytes=1, now=T0))
    assert seen == states
    d = get_job(conn, j.id)
    assert d.attempts == len(states) and d.error == "RuntimeError: vendor down" and d.finished_at == iso(T0)
    assert _claim(conn) is None


def test_cancel_queued_running_and_terminal(conn):
    q, _ = _q(conn, params={"n": 1})
    r, _ = _q(conn, params={"n": 2}, priority=0)
    assert _claim(conn).id == r.id
    assert cancel(conn, q.id, now=T0).state == "cancelled"
    assert cancel(conn, r.id, now=T0).state == "cancelled"
    assert heartbeat(conn, r.id, now=T0) == "cancelled"  # the worker learns on its next beat
    f, _ = _q(conn, params={"n": 3})
    _claim(conn)
    fail(conn, f.id, error="x", retries=0, cpu_seconds=0.0, peak_rss_bytes=0, now=T0)
    assert cancel(conn, f.id, now=T0).state == "failed"  # terminal states are left alone
    assert cancel(conn, "NOPE", now=T0) is None


def test_stale_running_job_is_requeued(conn):
    j, _ = _q(conn)
    _claim(conn, T0)
    heartbeat(conn, j.id, now=T0 + timedelta(minutes=1))
    # Last beat at T0+1m; 300 s later is T0+6m. 4m59s old: kept. 5m01s old: re-queued.
    assert requeue_stale(conn, now=T0 + timedelta(minutes=5, seconds=59)) == []
    assert requeue_stale(conn, now=T0 + timedelta(minutes=6, seconds=1)) == [(j.id, "queued")]
    d = get_job(conn, j.id)
    assert d.state == "queued" and d.started_at is None and "heartbeat" in d.error


def test_a_job_that_keeps_killing_the_worker_is_failed_not_looped(conn):
    j, _ = _q(conn)
    t = T0
    res = []
    for _ in range(3):  # attempts 1 and 2 re-queue; attempt 3 reaches max_attempts=3
        assert _claim(conn, t).id == j.id
        t += timedelta(minutes=10)
        res = requeue_stale(conn, now=t)
    assert res == [(j.id, "failed")]
    d = get_job(conn, j.id)
    assert d.state == "failed" and "3 times" in d.error


def test_requeue_on_shutdown_does_not_spend_an_attempt(conn):
    j, _ = _q(conn)
    _claim(conn)
    requeue(conn, j.id, reason="interrupted by worker shutdown", now=T0)
    d = get_job(conn, j.id)
    assert (d.state, d.attempts, d.started_at) == ("queued", 0, None)


def test_progress_and_events_are_ordered(conn):
    j, _ = _q(conn)
    _claim(conn)
    set_progress(conn, j.id, 0.5, "half", now=T0)
    set_progress(conn, j.id, 7.0, None, now=T0)  # clamped to 1.0
    ev = events_after(conn, 0)
    assert [(e["job_id"], e["state"], e["progress"], e["message"]) for e in ev] == [
        (j.id, "queued", None, None), (j.id, "running", 0.0, None),
        (j.id, "running", 0.5, "half"), (j.id, "running", 1.0, None)]
    seqs = [e["seq"] for e in ev]
    assert seqs == sorted(seqs) and events_after(conn, seqs[1]) == ev[2:]


def test_list_jobs_filters_and_orders_newest_first(conn):
    a, _ = _q(conn, params={"n": 1}, now=T0)
    b, _ = _q(conn, kind="ingest.fred", params={"n": 2}, now=T0 + timedelta(seconds=1))
    c, _ = _q(conn, params={"n": 3}, now=T0 + timedelta(seconds=2))
    assert [j.id for j in list_jobs(conn)] == [c.id, b.id, a.id]
    assert [j.id for j in list_jobs(conn, kind="ingest.fred")] == [b.id]
    assert [j.id for j in list_jobs(conn, state="queued", limit=1)] == [c.id]
