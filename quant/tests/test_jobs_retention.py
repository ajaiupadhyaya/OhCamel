"""Lane B, B6: artifact retention (II.3), housekeeping, the /api/ops jobs block, backups."""

from __future__ import annotations

import os
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ohcamel_quant.jobs.artifacts import prune, prune_housekeeping
from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.queue import enqueue
from ohcamel_quant.jobs.summary import jobs_summary

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _art(conn, root, art_id, ph, finished, kind="ingest.fred"):
    path = root / kind / art_id
    path.mkdir(parents=True)
    (path / "manifest.json").write_text("{}")
    conn.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?)", (art_id, kind, ph, iso(finished), str(path), None))
    return path


def _every_5_days(k):
    # k = 0..39, one every 5 days from 2025-10-01; k=6 is 2025-10-31, k=10 is 2025-11-20, k=39 is 2026-04-14.
    d = date(2025, 10, 1) + timedelta(days=5 * k)
    return datetime(d.year, d.month, d.day, 11, 0, tzinfo=UTC)


def test_public_kind_with_distinct_params_is_pruned_to_30_plus_monthly(tmp_path, conn):
    """II.3 applies per kind: 40 api.* artifacts, each its own request (its own params_hash).
    Newest 30: k = 10..39. The newest per month adds only October's (k=6); November's newest (k=12,
    2025-11-30) is already kept. Pruned: k = 0..5 and 7..9, i.e. 9. Kept: 31."""
    root = tmp_path / "artifacts"
    for k in range(40):
        _art(conn, root, f"P{k:02d}", f"h{k}", _every_5_days(k), kind="api.backtest_sweep")
    pruned = prune(conn, kind="api.backtest_sweep")
    assert sorted(pruned) == [f"P{k:02d}" for k in (0, 1, 2, 3, 4, 5, 7, 8, 9)]
    left = {r[0] for r in conn.execute("SELECT id FROM artifacts WHERE kind = 'api.backtest_sweep'")}
    assert len(left) == 31 and "P06" in left
    assert not (root / "api.backtest_sweep" / "P00").exists() and (root / "api.backtest_sweep" / "P06").exists()
    assert prune(conn) == []  # idempotent


def test_scheduled_kind_also_keeps_each_series_latest(tmp_path, conn):
    """Same 40 artifacts of one FRED series, plus another series' only artifact on 2025-10-02.
    October's newest is k=6, so by the per-kind rule alone OTHER would go. A scheduled
    (non-public) kind keeps the newest of each params_hash, so it stays."""
    root = tmp_path / "artifacts"
    for k in range(40):
        _art(conn, root, f"A{k:02d}", "h", _every_5_days(k))
    _art(conn, root, "OTHER", "h2", datetime(2025, 10, 2, 11, 0, tzinfo=UTC))
    pruned = prune(conn, kind="ingest.fred")
    assert sorted(pruned) == [f"A{k:02d}" for k in (0, 1, 2, 3, 4, 5, 7, 8, 9)]
    left = {r[0] for r in conn.execute("SELECT id FROM artifacts")}
    assert len(left) == 32 and {"A06", "OTHER"} <= left
    assert prune(conn) == []  # the whole-table pass agrees


def test_housekeeping_drops_old_events_and_abandoned_staging(tmp_path, conn):
    enqueue(conn, kind="ops.selftest", params={}, priority=2, mem_class="S", heavy=False,
            submitted_by="schedule:t", now=NOW - timedelta(days=3))
    enqueue(conn, kind="ops.selftest", params={"n": 2}, priority=2, mem_class="S", heavy=False,
            submitted_by="schedule:t", now=NOW)
    root = tmp_path / "artifacts"
    old, fresh = root / ".staging" / "OLD", root / ".staging" / "FRESH"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    t_old = time.time() - 2 * 86400
    os.utime(old, (t_old, t_old))
    out = prune_housekeeping(conn, root, now=NOW)
    assert out == {"events_deleted": 1, "staging_removed": 1}
    assert conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1
    assert not old.exists() and fresh.exists()


def test_jobs_summary_hand_counted(conn):
    def add(n, state, finished=None, cpu=None):
        j, _ = enqueue(conn, kind="ops.selftest", params={"n": n}, priority=2, mem_class="S", heavy=False,
                       submitted_by="schedule:t", now=NOW - timedelta(hours=30))
        conn.execute("UPDATE jobs SET state=?, finished_at=?, cpu_seconds=? WHERE id=?",
                     (state, iso(finished) if finished else None, cpu, j.id))

    add(1, "queued")
    add(2, "queued")
    add(3, "running")
    add(4, "done", NOW - timedelta(hours=1), 1.0)
    add(5, "done", NOW - timedelta(hours=2), 2.0)
    add(6, "done", NOW - timedelta(hours=23), 3.5)
    add(7, "done", NOW - timedelta(hours=25), 100.0)   # outside 24 h
    add(8, "failed", NOW - timedelta(hours=3), 0.5)
    add(9, "cancelled", NOW - timedelta(hours=1), 9.0)  # neither done nor failed
    # done_24h = 3 (n=4,5,6); failed_24h = 1; cpu = 1.0 + 2.0 + 3.5 + 0.5 = 7.0
    assert jobs_summary(conn, NOW) == {"queued": 2, "running": 1, "done_24h": 3, "failed_24h": 1,
                                       "cpu_seconds_24h": 7.0}


def test_api_ops_has_the_jobs_block(tmp_path):
    from ohcamel_quant.api.app import create_app
    from ohcamel_quant.api.routers.jobs import get_jobs_db_path

    app = create_app()
    db = tmp_path / "jobs.sqlite"
    app.dependency_overrides[get_jobs_db_path] = lambda: db
    client = TestClient(app)
    r = client.get("/api/ops")
    assert r.status_code == 200 and r.json()["jobs"] is None and r.json()["notes"]
    assert not db.exists()  # reading /api/ops never creates the queue
    c = connect(db)
    enqueue(c, kind="ops.selftest", params={}, priority=2, mem_class="S", heavy=False,
            submitted_by="schedule:t", now=datetime.now(UTC))
    c.close()
    j = client.get("/api/ops").json()
    assert j["jobs"] == {"queued": 1, "running": 0, "done_24h": 0, "failed_24h": 0, "cpu_seconds_24h": 0.0}
    assert j["provenance"][0]["source"] == "ohcamel-jobs"


def test_the_worker_prunes_its_kind_after_publishing(tmp_path, monkeypatch):
    from ohcamel_quant.jobs import worker as worker_mod
    from ohcamel_quant.jobs.admission import admit_all

    calls = []
    monkeypatch.setattr(worker_mod, "prune", lambda conn, **kw: calls.append(kw) or [])
    cfg = worker_mod.WorkerConfig(db_path=tmp_path / "jobs.sqlite", artifacts_root=tmp_path / "artifacts",
                                  poll_s=0.05, heartbeat_s=0.2, grace_s=2.0, heartbeat_file=None, scheduler=False)
    c = connect(cfg.db_path)
    j, _ = enqueue(c, kind="ops.selftest", params={"tag": "p"}, priority=2, mem_class="S", heavy=False,
                   submitted_by="schedule:t", now=datetime.now(UTC))
    assert worker_mod.Worker(cfg, admit=admit_all).run_once().status == "done"
    assert calls == [{"kind": j.kind}]  # per kind (II.3), not per params_hash
    c.close()
