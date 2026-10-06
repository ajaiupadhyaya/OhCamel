"""Lane B, B5: /api/jobs (contract II.2) against a temporary jobs.sqlite."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.routers.jobs import get_jobs_db_path, get_sse_lifetime
from ohcamel_quant.jobs.artifacts import write_tables
from ohcamel_quant.jobs.db import connect, iso, utcnow
from ohcamel_quant.jobs.queue import claim_next, enqueue, finish, get_job

SWEEP = {"strategy": "tsmom", "tickers": ["SPY", "TLT"], "grid": {"lookback": [126, 252]}}
IP_A = {"X-Forwarded-For": "203.0.113.7"}
IP_B = {"X-Forwarded-For": "203.0.113.8"}


@pytest.fixture
def api(tmp_path):
    app = create_app()
    db = tmp_path / "jobs.sqlite"
    app.dependency_overrides[get_jobs_db_path] = lambda: db
    app.dependency_overrides[get_sse_lifetime] = lambda: 1.5
    return TestClient(app), db


def _sweep(i):
    return {**SWEEP, "grid": {"lookback": [126, 252 + i]}}


def test_post_queues_an_allow_listed_kind(api):
    client, db = api
    r = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": SWEEP}, headers=IP_A)
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["state"] == "queued" and j["created"] is True and j["status_url"] == f"/api/jobs/{j['id']}"
    assert "provenance" in j and "notes" in j
    row = get_job(connect(db), j["id"])
    assert (row.submitted_by, row.priority, row.mem_class, row.heavy) == ("api", 0, "M", False)
    again = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": SWEEP}, headers=IP_B).json()
    assert again["id"] == j["id"] and again["created"] is False


@pytest.mark.parametrize("body,error", [
    ({"kind": "nope.x", "params": {}}, "unknown_kind"),
    ({"kind": "ingest.fred", "params": {"series": "DGS10"}}, "kind_not_allowed"),
    ({"kind": "ops.selftest", "params": {}}, "kind_not_allowed"),
    ({"kind": "api.backtest_sweep", "params": {"strategy": "tsmom"}}, "invalid_input"),
])
def test_post_refuses_what_is_not_allowed(api, body, error):
    client, _ = api
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 422 and r.json()["error"] == error


def test_twenty_live_jobs_per_client(api):
    client, _ = api
    for i in range(20):
        assert client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": _sweep(i)},
                           headers=IP_A).status_code == 202
    r = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": _sweep(20)}, headers=IP_A)
    assert r.status_code == 429 and r.json()["error"] == "too_many_jobs"
    # The rightmost X-Forwarded-For entry is the client Caddy saw; a spoofed left entry changes nothing.
    r = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": _sweep(21)},
                    headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
    assert r.status_code == 429
    assert client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": _sweep(22)},
                       headers=IP_B).status_code == 202


def test_get_one_and_list(api):
    client, _ = api
    jid = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": SWEEP}).json()["id"]
    r = client.get(f"/api/jobs/{jid}")
    assert r.status_code == 200
    j = r.json()
    assert j["id"] == jid and j["state"] == "queued" and j["progress"] is None and "provenance" in j
    assert client.get("/api/jobs/NOPE").status_code == 404
    lst = client.get("/api/jobs", params={"state": "queued", "kind": "api.backtest_sweep", "limit": 5}).json()
    assert [x["id"] for x in lst["jobs"]] == [jid] and "notes" in lst
    assert client.get("/api/jobs", params={"limit": 1000}).status_code == 422


def test_only_the_submitting_client_may_cancel(api):
    client, db = api
    jid = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": SWEEP}, headers=IP_A).json()["id"]
    assert client.delete(f"/api/jobs/{jid}", headers=IP_B).status_code == 403
    r = client.delete(f"/api/jobs/{jid}", headers=IP_A)
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    sched, _ = enqueue(connect(db), kind="ops.selftest", params={}, priority=2, mem_class="S", heavy=False,
                       submitted_by="schedule:nightly", now=utcnow())
    assert client.delete(f"/api/jobs/{sched.id}", headers=IP_A).status_code == 403
    assert client.delete("/api/jobs/NOPE").status_code == 404


def test_result_of_a_done_api_job(api, tmp_path):
    client, db = api
    jid = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": SWEEP}).json()["id"]
    assert client.get(f"/api/jobs/{jid}/result").status_code == 409
    conn = connect(db)
    job = claim_next(conn, allowed_classes=frozenset({"M"}), allow_heavy=True, now=utcnow())
    art = tmp_path / "artifacts" / job.kind / job.id
    write_tables(art, {"result": pd.DataFrame({"payload": ['{"pbo": {"pbo": 0.25}, "notes": []}']})})
    (art / "manifest.json").write_text("{}")
    t = utcnow() + timedelta(seconds=1)
    finish(conn, job.id, artifact={"id": job.id, "kind": job.kind, "params_hash": job.params_hash,
                                   "finished_at": iso(t), "path": str(art), "data_asof": None},
           cpu_seconds=1.0, peak_rss_bytes=1, now=t)
    assert client.get(f"/api/jobs/{jid}").json()["result_url"] == f"/api/jobs/{jid}/result"
    r = client.get(f"/api/jobs/{jid}/result")
    assert r.status_code == 200 and r.json() == {"pbo": {"pbo": 0.25}, "notes": []}
    (art / "result.parquet").unlink()
    assert client.get(f"/api/jobs/{jid}/result").status_code == 410
    assert client.get("/api/jobs/NOPE/result").status_code == 404


def test_events_stream_replays_and_is_not_gzipped(api):
    client, _ = api
    jid = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": SWEEP}).json()["id"]
    r = client.get("/api/jobs/events", headers={"Last-Event-ID": "0", "Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in r.headers
    assert "event: job" in r.text and f'"id": "{jid}"' in r.text and '"type": "job"' in r.text
    assert '"state": "queued"' in r.text and "\nid: 1\n" in "\n" + r.text


def test_the_default_queue_is_never_the_real_data_dir():
    """conftest points the API's default jobs.sqlite at a session temp dir, so a test that
    posts an over-cap request through the shared `client` fixture never writes {data_dir}/jobs.sqlite."""
    from ohcamel_quant.config import get_settings
    from ohcamel_quant.jobs.paths import jobs_db_path

    assert get_jobs_db_path() != jobs_db_path()
    assert Path(get_settings().data_dir).resolve() not in get_jobs_db_path().resolve().parents
