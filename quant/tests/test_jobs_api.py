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
    ({"kind": "ingest.bars_daily", "params": {}}, "kind_not_allowed"),
    ({"kind": "ops.selftest", "params": {"exit_hard": True}}, "invalid_input"),
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


def test_schedules_lists_every_entry_with_its_next_run(api):
    from ohcamel_quant.jobs.schedules import load_schedules

    client, _ = api
    r = client.get("/api/jobs/schedules")
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["schedules"]
    want = load_schedules()
    assert [x["name"] for x in rows] == [e.name for e in want.entries]
    first, entry = rows[0], want.entries[0]
    assert first["cron"] == entry.cron and first["kind"] == entry.kind
    assert first["heavy"] == entry.heavy and first["mem_class"] == entry.mem_class
    assert first["next_run"] is None or first["next_run"].endswith("Z")
    assert all(x["next_run"] for x in rows)  # every shipped entry fires again
    assert body["timezone"] == "America/New_York"
    assert body["provenance"][0]["source"] == "ohcamel-jobs"


def test_result_never_reads_outside_the_artifacts_root(api, tmp_path):
    """Harden H4: a row path or a result.parquet link that leaves <data>/artifacts is refused."""
    client, db = api
    jid = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": SWEEP}).json()["id"]
    conn = connect(db)
    job = claim_next(conn, allowed_classes=frozenset({"M"}), allow_heavy=True, now=utcnow())
    out = tmp_path / "outside"
    write_tables(out, {"result": pd.DataFrame({"payload": ['{"secret": "s3cr3t"}']})})
    art = tmp_path / "artifacts" / job.kind / job.id
    art.mkdir(parents=True)
    (art / "result.parquet").symlink_to(out / "result.parquet")
    t = utcnow() + timedelta(seconds=1)
    finish(conn, job.id, artifact={"id": job.id, "kind": job.kind, "params_hash": job.params_hash,
                                   "finished_at": iso(t), "path": str(art), "data_asof": None},
           cpu_seconds=1.0, peak_rss_bytes=1, now=t)
    r = client.get(f"/api/jobs/{jid}/result")
    assert r.status_code == 404 and "s3cr3t" not in r.text
    for bad in (str(out), str(tmp_path / "artifacts" / ".." / "outside")):
        conn.execute("UPDATE artifacts SET path = ? WHERE id = ?", (bad, job.id))
        conn.commit()
        r = client.get(f"/api/jobs/{jid}/result")
        assert r.status_code == 404 and "s3cr3t" not in r.text, bad


# Harden H4 (security): /api/jobs allow-list, the per-client cap under X-Forwarded-For spoofing, size limits.
PRIVATE_KINDS = ["ingest.fred", "ingest.universes", "ingest.bars_daily", "ingest.sec_facts", "risk.mc_atlas",
                 "farm.sweep", "models.xs_lgbm", "regime.hmm", "vol.surface_history", "cov.league"]


def test_every_private_kind_is_refused_and_nothing_is_queued(api):
    from ohcamel_quant.jobs.kinds import REGISTRY

    client, db = api
    private = sorted(k for k, s in REGISTRY.items() if not s.public)
    assert set(PRIVATE_KINDS) <= set(private)
    public = sorted(k for k, s in REGISTRY.items() if s.public)
    assert public == ["api.backtest_sweep", "api.backtest_walkforward", "api.portfolio_compare",
                      "api.risk_backtest", "ops.selftest"]  # a new public kind is a reviewed change
    for k in private:
        r = client.post("/api/jobs", json={"kind": k, "params": {}})
        assert r.status_code == 422 and r.json()["error"] == "kind_not_allowed", k
    assert not db.exists() or connect(db).execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_rotating_spoofed_forwarded_entries_share_one_cap(api):
    """Caddy appends the address it saw; only that last hop counts, so rotating the left entries is useless."""
    client, _ = api
    for i in range(20):
        h = {"X-Forwarded-For": f"10.0.{i}.1, 198.51.100.{i}, 203.0.113.7"}
        assert client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": _sweep(i)},
                           headers=h).status_code == 202
    for i in range(20, 25):
        h = {"X-Forwarded-For": f"203.0.113.{i}, 203.0.113.7"}
        r = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": _sweep(i)}, headers=h)
        assert r.status_code == 429, i


def test_a_duplicated_forwarded_header_counts_its_last_hop(api):
    """Two X-Forwarded-For headers: the client is the last entry of the last one (the one Caddy wrote)."""
    from starlette.requests import Request

    from ohcamel_quant.api.routers.jobs import client_id

    def req(headers):
        raw = [(k.lower().encode(), v.encode()) for k, v in headers]
        return Request({"type": "http", "headers": raw, "client": ("172.18.0.5", 1234)})

    assert client_id(req([("X-Forwarded-For", "6.6.6.6"), ("X-Forwarded-For", "1.1.1.1, 203.0.113.7")])) == \
        "203.0.113.7"
    assert client_id(req([("X-Forwarded-For", "6.6.6.6, 203.0.113.7")])) == "203.0.113.7"
    assert client_id(req([("X-Forwarded-For", "6.6.6.6, ")])) == "172.18.0.5"  # empty last hop: the peer
    assert client_id(req([])) == "172.18.0.5"


def test_params_over_the_size_limit_are_refused(api):
    client, db = api
    big = {**SWEEP, "params": {"pad": "x" * 20_000}}
    r = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": big})
    assert r.status_code == 413 and r.json()["error"] == "params_too_large"
    deep = {**SWEEP, "grid": {"lookback": list(range(5000))}}
    r = client.post("/api/jobs", json={"kind": "api.backtest_sweep", "params": deep})
    assert r.status_code == 413
    assert not db.exists() or connect(db).execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    long_kind = client.post("/api/jobs", json={"kind": "k" * 65, "params": {}})
    assert long_kind.status_code == 422


@pytest.mark.parametrize("params", [
    {"tag": "t" * 65},
    {"tag": 7},
    {"tag": ["a"]},
    {"sleep_s": 60},
    {"fail_if_exists": "/etc/passwd"},
    {"exit_hard": True},
    {"tag": "ok", "extra": 1},
])
def test_selftest_accepts_only_a_bounded_tag(api, params):
    client, _ = api
    r = client.post("/api/jobs", json={"kind": "ops.selftest", "params": params})
    assert r.status_code == 422 and r.json()["error"] == "invalid_input", params


def test_selftest_tag_at_the_bound_is_accepted(api):
    client, _ = api
    assert client.post("/api/jobs", json={"kind": "ops.selftest", "params": {"tag": "t" * 64}}).status_code == 202


# Harden H4 (security): SSE fan-out cap and idle timeout.
def test_sse_gate_counts_total_and_per_client():
    from ohcamel_quant.api.routers.jobs import SseGate

    g = SseGate(max_total=3, max_per_client=2)
    a1, a2 = g.acquire("a"), g.acquire("a")
    assert a1 is not None and a2 is not None
    assert g.acquire("a") == "per_client"
    b1 = g.acquire("b")
    assert b1 is not None and g.acquire("c") == "total"
    a1.release()
    a1.release()  # idempotent: the stream's finally and the response's background both release
    assert g.total == 2 and g.per == {"a": 1, "b": 1}
    assert g.acquire("c") is not None
    for s in (a2, b1):
        s.release()
    assert g.per == {"c": 1}


def test_sse_refuses_beyond_the_fan_out_cap(api):
    from ohcamel_quant.api.routers.jobs import SseGate, get_sse_gate

    client, _ = api
    gate = SseGate(max_total=2, max_per_client=1)
    client.app.dependency_overrides[get_sse_gate] = lambda: gate
    held = gate.acquire("203.0.113.7")
    r = client.get("/api/jobs/events", headers=IP_A)
    assert r.status_code == 429 and r.json()["error"] == "too_many_streams" and r.headers["retry-after"]
    gate.acquire("198.51.100.1")
    r = client.get("/api/jobs/events", headers=IP_B)
    assert r.status_code == 503 and r.json()["error"] == "streams_full" and r.headers["retry-after"]
    held.release()
    r = client.get("/api/jobs/events", headers=IP_A)  # a slot frees: served, then released at its end
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert gate.total == 1 and "203.0.113.7" not in gate.per


def test_sse_closes_an_idle_stream_before_its_lifetime(api):
    import time as _time

    from ohcamel_quant.api.routers.jobs import get_sse_idle

    client, _ = api
    client.app.dependency_overrides[get_sse_lifetime] = lambda: 30.0
    client.app.dependency_overrides[get_sse_idle] = lambda: 0.6
    t0 = _time.monotonic()
    r = client.get("/api/jobs/events")
    assert r.status_code == 200 and _time.monotonic() - t0 < 5.0
    assert "event: job" not in r.text and ": idle" in r.text
