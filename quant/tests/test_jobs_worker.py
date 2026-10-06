"""Lane B, B2: the worker -- a real spawned child per job, measured and published.

Each test spends one to three child processes (a fresh interpreter that
imports pandas: ~1-3 s each). Admission is ``admit_all`` here; B3 tests the
real rule.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from datetime import timedelta

import pandas as pd
import pytest

from ohcamel_quant.jobs.admission import admit_all
from ohcamel_quant.jobs.artifacts import latest_artifact
from ohcamel_quant.jobs.db import connect, parse_iso, utcnow
from ohcamel_quant.jobs.queue import cancel, claim_next, enqueue, get_job, params_hash, progress_of
from ohcamel_quant.jobs.worker import Worker, WorkerConfig, install_signal_handlers


@pytest.fixture
def cfg(tmp_path):
    return WorkerConfig(db_path=tmp_path / "jobs.sqlite", artifacts_root=tmp_path / "artifacts", poll_s=0.05,
                        heartbeat_s=0.2, grace_s=2.0, heartbeat_file=tmp_path / "hb", code_sha="testsha",
                        scheduler=False)


@pytest.fixture
def conn(cfg):
    c = connect(cfg.db_path)
    yield c
    c.close()


def _q(conn, kind="ops.selftest", params=None):
    job, created = enqueue(conn, kind=kind, params=params or {}, priority=2, mem_class="S", heavy=False,
                           submitted_by="schedule:test", now=utcnow())
    return job


def _wait(pred, timeout=15.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_selftest_runs_end_to_end_in_a_child(cfg, conn):
    j = _q(conn, params={"tag": "a"})
    out = Worker(cfg, admit=admit_all).run_once()
    assert out is not None and out.status == "done", out and out.error
    d = get_job(conn, j.id)
    # A child interpreter that imported pandas uses well over 10 MiB and some CPU.
    assert d.state == "done" and d.artifact_id == j.id and d.cpu_seconds > 0 and d.peak_rss_bytes > 10 * 2**20
    final = cfg.artifacts_root / "ops.selftest" / j.id
    man = json.loads((final / "manifest.json").read_text())
    assert (man["code_sha"], man["tables"], man["engine"], man["id"]) == ("testsha", ["checks"], "python", j.id)
    assert man["cpu_seconds"] == d.cpu_seconds and man["peak_rss_bytes"] == d.peak_rss_bytes
    facts = dict(pd.read_parquet(final / "checks.parquet").itertuples(index=False, name=None))
    assert facts["threads"] == "2" and facts["pid"] != str(os.getpid())
    assert not (cfg.artifacts_root / ".staging" / j.id).exists()


def test_a_failed_newest_job_leaves_the_previous_artifact_latest(cfg, conn, tmp_path):
    marker = tmp_path / "fail-now"
    params = {"tag": "rf1", "fail_if_exists": str(marker)}
    w = Worker(cfg, admit=admit_all)
    j1 = _q(conn, params=params)
    assert w.run_once().status == "done"
    marker.touch()
    j2 = _q(conn, params=params)
    assert j2.id != j1.id
    assert w.run_once().status == "failed"
    assert get_job(conn, j2.id).state == "failed" and "fail_if_exists" in get_job(conn, j2.id).error
    ph = params_hash(params)
    t1 = parse_iso(get_job(conn, j1.id).finished_at)
    got = latest_artifact(conn, "ops.selftest", ph, now=t1 + timedelta(minutes=1), max_age_s=3600)
    assert got["manifest"]["id"] == j1.id and got["stale"] is False
    got = latest_artifact(conn, "ops.selftest", ph, now=t1 + timedelta(hours=2), max_age_s=3600)
    assert got["manifest"]["id"] == j1.id and got["stale"] is True


def test_ingest_fred_one_series_end_to_end(cfg, conn):
    j = _q(conn, kind="ingest.fred", params={"series": "DGS10"})
    out = Worker(cfg, admit=admit_all).run_once()
    assert out.status == "done", out.error
    final = cfg.artifacts_root / "ingest.fred" / j.id
    man = json.loads((final / "manifest.json").read_text())
    # fixtures/macro/macro.parquet: the last DGS10 row is 2026-06-01 = 4.47.
    assert man["data_asof"] == "2026-06-01" and man["provenance"][0]["source"] == "fixture:fred"
    t = pd.read_parquet(final / "series.parquet")
    assert len(t) == 2609 and t["value"].iloc[-1] == 4.47


def test_ingest_failure_is_retried_twice_then_failed(cfg, conn):
    j = _q(conn, kind="ingest.fred", params={"series": "NOSUCHSERIES"})
    w = Worker(cfg, admit=admit_all)
    states = []
    for _ in range(3):
        w.run_once()
        states.append(get_job(conn, j.id).state)
    assert states == ["queued", "queued", "failed"]
    assert "offline mode: FRED series not in fixtures" in get_job(conn, j.id).error


def test_cancel_while_running_stops_the_child(cfg, conn):
    j = _q(conn, params={"tag": "c", "sleep_s": 30})
    w = Worker(cfg, admit=admit_all)
    out = []
    t = threading.Thread(target=lambda: out.append(w.run_once()))
    t.start()
    assert _wait(lambda: (progress_of(conn, j.id) or {}).get("message") == "sleeping")
    cancel(conn, j.id, now=utcnow())
    t.join(20)
    assert not t.is_alive() and out[0].status == "cancelled"
    assert get_job(conn, j.id).state == "cancelled"
    assert not (cfg.artifacts_root / "ops.selftest" / j.id).exists()


def test_stop_interrupts_and_requeues_within_20s(cfg, conn):
    j = _q(conn, params={"tag": "s", "sleep_s": 30})
    w = Worker(cfg, admit=admit_all)
    out = []
    t = threading.Thread(target=lambda: out.append(w.run_once()))
    t.start()
    assert _wait(lambda: (progress_of(conn, j.id) or {}).get("message") == "sleeping")
    t0 = time.monotonic()
    w.request_stop()
    t.join(20)
    assert not t.is_alive() and time.monotonic() - t0 < 20
    assert out[0].status == "interrupted"
    d = get_job(conn, j.id)
    assert (d.state, d.attempts) == ("queued", 0)


def test_killed_child_is_a_failure_with_a_reason(cfg, conn):
    # exit_hard: the child dies with os._exit(137) and no result -- what an OOM kill looks like.
    j = _q(conn, params={"tag": "k", "exit_hard": True})
    out = Worker(cfg, admit=admit_all).run_once()
    assert out.status == "failed"
    err = get_job(conn, j.id).error
    assert "137" in err and "without a result" in err


def test_sigterm_requests_stop(cfg):
    w = Worker(cfg, admit=admit_all)
    previous = install_signal_handlers(w)
    try:
        signal.raise_signal(signal.SIGTERM)
        assert w.stop.is_set()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def test_run_requeues_a_stale_job_on_start_and_runs_it(cfg, conn):
    _q(conn, params={"tag": "stale"})
    ghost = claim_next(conn, allowed_classes=frozenset({"S"}), allow_heavy=True,
                       now=utcnow() - timedelta(minutes=10))  # a worker that died 10 minutes ago
    w = Worker(cfg, admit=admit_all)
    t = threading.Thread(target=w.run)
    t.start()
    try:
        assert _wait(lambda: get_job(conn, ghost.id).state == "done", timeout=30)
        assert cfg.heartbeat_file.exists()
    finally:
        w.request_stop()
        t.join(20)
    assert not t.is_alive()


def test_cli_worker_help_runs():
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "ohcamel_quant", "worker", "--help"], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0 and "ohcamel-quant worker" in r.stdout
