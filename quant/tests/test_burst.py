"""Completion task 3.7 (spec R6, "Burst"): a heavy job runs on another machine against last night's export and
publishes its artifact through the burst door on the live host.

One fixture warehouse, one artifact the worker made from it, one export of it, and one burst run on that export,
made once for the module. The droplet (jobs.sqlite, the artifact store, the exports it holds) is a fresh temporary
directory per test; the door is the burst app under the test client, with its paths injected.
"""

from __future__ import annotations

import ast
import base64
import json
import pickle
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ohcamel_quant.config import REPO_ROOT, Settings
from ohcamel_quant.jobs import burst as B
from ohcamel_quant.jobs import burst_door as D
from ohcamel_quant.jobs.artifacts import read_reads
from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.local import run_local
from ohcamel_quant.jobs.provenance import checkout_sha, data_hash, frame_hash, lock_hash
from ohcamel_quant.jobs.queue import claim_next, enqueue, expire_holds, get_job, params_hash
from ohcamel_quant.jobs.regenerate import compare_table
from ohcamel_quant.warehouse.export import export_digest, export_warehouse
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

NOW = datetime(2026, 6, 1, 21, 0, tzinfo=UTC)
ATLAS = {"n_paths": 2_000, "member_paths": 2_000, "members": ["SPY", "QQQ"]}
SECRET = "s" * 40
DAY = "2026-06-01"


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """(worker artifact dir, export dir, burst run) -- the worker ran on the warehouse the export was taken from,
    and the burst run read only the export."""
    root = tmp_path_factory.mktemp("burst")
    wh = build_fixture_warehouse(root / "warehouse.duckdb", requested_start="1990-01-01")
    art = run_local("risk.mc_atlas", ATLAS, artifacts_root=root / "worker-artifacts",
                    settings=Settings(warehouse_path=wh, offline=True, data_dir=root / "worker-data"), threads=1,
                    now=NOW, code_sha=checkout_sha())
    export = export_warehouse(wh, root / "exports", day=DAY)
    rec = json.loads((export / "export.json").read_text())
    rec["exported_at"] = NOW.isoformat(timespec="seconds")       # the nightly export, taken at the worker's clock
    (export / "export.json").write_text(json.dumps(rec, indent=1, sort_keys=True))
    wh.unlink()                                                  # only the export is left for the burst machine
    run = B.run_burst("risk.mc_atlas", ATLAS, export, scratch=root / "scratch", threads=1)
    return art, export, run


# --- the brief's three tests -------------------------------------------------------------------------------------

def test_a_burst_artifact_equals_the_one_the_worker_produces(made):
    art, _, run = made
    want = json.loads((art / "manifest.json").read_text())
    got = B.manifest_for(run, job_id=want["id"], code_sha=checkout_sha())
    assert got["data_hash"] == want["data_hash"] and got["data_hash"]
    assert sorted(got["tables"]) == sorted(want["tables"])
    for t in want["tables"]:
        diff = compare_table(t, pd.read_parquet(art / f"{t}.parquet"), pd.read_parquet(run.staging / f"{t}.parquet"),
                             frozenset())
        assert diff == [], diff
    assert got["host"] == "burst" and got["burst"]["export"]["day"] == DAY
    assert got["started_at"] == iso(NOW)                         # the clock was pinned at the export's moment
    assert got["runtime"]["platform"] and got["burst"]["machine"]["platform"] == got["runtime"]["platform"]


def test_an_upload_for_a_kind_that_is_not_burstable_is_403(door, made):
    client, d = door
    _, _, run = made
    assert client_post(client, "/burst/hold", {"kind": "vol.forecast_league", "params": {}}).status_code == 403
    conn = connect(d.db)
    job, _ = enqueue(conn, kind="vol.forecast_league", params={}, priority=2, mem_class="M", heavy=True,
                     submitted_by="schedule:test", now=NOW)
    body = _body(run, job.id, manifest_changes={"kind": "vol.forecast_league", "params": {},
                                                "params_hash": params_hash({})})
    r = client_post_raw(client, f"/burst/jobs/{job.id}/artifact", body)
    assert r.status_code == 403 and r.json()["error"] == "not_burstable"
    assert get_job(conn, job.id).state == "queued"


@pytest.mark.parametrize("how", ["not_held", "different_bytes"])
def test_an_upload_whose_export_the_droplet_does_not_hold_is_409(door, made, how):
    client, d = door
    _, _, run = made
    if how == "not_held":
        shutil.rmtree(d.exports / DAY)
    else:                                                        # same day, another export: the digest differs
        rec = json.loads((d.exports / DAY / "export.json").read_text())
        rec["exported_at"] = "2026-06-01T21:00:01+00:00"
        (d.exports / DAY / "export.json").write_text(json.dumps(rec))
    jid = _hold(client)
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid))
    assert r.status_code == 409 and r.json()["error"] == "no_such_export", r.text
    assert get_job(connect(d.db), jid).state == "queued"
    assert not (d.root / "risk.mc_atlas" / jid).exists()


# --- the door, end to end ----------------------------------------------------------------------------------------

def test_a_signed_upload_for_a_held_job_is_published_as_a_burst_artifact(door, made):
    client, d = door
    _, _, run = made
    jid = _hold(client)
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid))
    assert r.status_code == 201, r.text
    conn = connect(d.db)
    job = get_job(conn, jid)
    assert job.state == "done" and job.artifact_id == jid and job.submitted_by == "burst"
    assert job.started_at is None                                # the droplet's wall-times report never counts it
    path = d.root / "risk.mc_atlas" / jid
    m = json.loads((path / "manifest.json").read_text())
    assert m["host"] == "burst" and m["kind"] == "risk.mc_atlas" and m["params"] == ATLAS
    assert m["data_hash"] == data_hash(read_reads(path))
    assert sorted(p.name for p in path.iterdir()) == sorted(["manifest.json", "reads.json"]
                                                            + [f"{t}.parquet" for t in m["tables"]])
    # the public API shows it, as a burst artifact
    from ohcamel_quant.api.app import create_app
    from ohcamel_quant.api.routers.artifacts import get_artifacts_db_path

    app = create_app()
    app.dependency_overrides[get_artifacts_db_path] = lambda: d.db
    pub = TestClient(app).get("/api/artifacts/risk.mc_atlas/latest").json()
    assert pub["host"] == "burst" and pub["manifest"]["id"] == jid


def test_the_tool_uploads_through_the_door(door, made):
    client, d = door
    _, _, run = made
    jid = _hold(client)
    out = B.upload(run, jid, client=client, base_url="", secret=SECRET, auth=None)
    assert out["id"] == jid and get_job(connect(d.db), jid).state == "done"
    again = B.upload(run, jid, client=client, base_url="", secret=SECRET, auth=None)   # a retried upload
    assert again["id"] == jid and again.get("already") is True


def test_the_same_upload_twice_is_refused_the_second_time(door, made):
    client, d = door
    _, _, run = made
    jid = _hold(client)
    body = _body(run, jid)
    h = B.signed_headers(SECRET, "POST", f"/burst/jobs/{jid}/artifact", body)
    assert client.post(f"/burst/jobs/{jid}/artifact", content=body, headers=h).status_code == 201
    r = client.post(f"/burst/jobs/{jid}/artifact", content=body, headers=h)       # the same request, replayed
    assert r.status_code == 409 and r.json()["error"] == "replayed"
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", body)              # signed again: the job is done
    assert r.status_code == 409 and r.json()["error"] == "already_done"
    assert r.json()["published"] == {"id": jid, "host": "burst", "data_hash": run.result_data_hash}


def test_a_scheduled_job_is_never_held_or_uploaded_for_queued_or_running(door, made):
    """Fix round 1, I1 path (a): a burst never takes over a job a schedule queued; the worker runs it."""
    client, d = door
    _, _, run = made
    conn = connect(d.db)
    job, _ = enqueue(conn, kind="risk.mc_atlas", params=ATLAS, priority=2, mem_class="M", heavy=True,
                     submitted_by="schedule:risk-atlas-nightly", now=NOW)
    r = client_post(client, "/burst/hold", {"kind": "risk.mc_atlas", "params": ATLAS})
    assert r.status_code == 409 and r.json()["error"] == "scheduled"
    r = client_post_raw(client, f"/burst/jobs/{job.id}/artifact", _body(run, job.id))
    assert r.status_code == 409 and r.json()["error"] == "scheduled"
    assert claim_next(conn, allowed_classes={"S", "M", "L"}, allow_heavy=True, now=NOW).id == job.id
    assert client_post(client, "/burst/hold", {"kind": "risk.mc_atlas", "params": ATLAS}).json()["error"] == "scheduled"
    r = client_post_raw(client, f"/burst/jobs/{job.id}/artifact", _body(run, job.id))
    assert r.status_code == 409 and r.json()["error"] in ("claimed", "scheduled")
    assert get_job(conn, job.id).state == "running"


def test_a_schedule_firing_during_a_burst_hold_gets_its_own_job_and_the_worker_runs_it(door, made):
    """Fix round 1, I1 path (b): the 20:00 risk-atlas-nightly fire while a burst job for the same kind and params
    is held creates its own job (never deduped onto the burst job), the worker claims and publishes it, and the
    burst hold expiring cancels only the burst job."""
    from ohcamel_quant.jobs.artifacts import write_tables
    from ohcamel_quant.jobs.queue import finish
    from ohcamel_quant.jobs.schedules import Schedules, load_schedules, tick

    client, d = door
    conn = connect(d.db)
    entry = next(e for e in load_schedules().entries if e.name == "risk-atlas-nightly")
    assert entry.kind == "risk.mc_atlas" and entry.params == {}
    held = client_post(client, "/burst/hold", {"kind": "risk.mc_atlas", "params": {}}).json()   # 19:00 New York
    assert held["created"] is True
    fire = datetime(2026, 6, 2, 0, 0, 30, tzinfo=UTC)                                           # 20:00:30 New York
    (name, jid, created), = tick(conn, Schedules(entries=(entry,)), fire)
    assert created is True and jid != held["job_id"]
    assert conn.execute("SELECT job_id FROM schedule_runs WHERE name = ?", (name,)).fetchone()[0] == jid
    job = claim_next(conn, allowed_classes={"S", "M", "L"}, allow_heavy=True, now=fire)
    assert job.id == jid and job.submitted_by == "schedule:risk-atlas-nightly"
    staging = d.root / ".staging" / jid
    write_tables(staging, {"summary": pd.DataFrame({"x": [1.0]})})
    final = d.root / "risk.mc_atlas" / jid
    final.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(final)
    assert finish(conn, jid, artifact={"id": jid, "kind": "risk.mc_atlas", "params_hash": job.params_hash,
                                       "finished_at": iso(fire), "path": str(final), "data_asof": None},
                  cpu_seconds=1.0, peak_rss_bytes=1, now=fire)
    end = datetime.fromisoformat(held["hold_until"].replace("Z", "+00:00")) + timedelta(seconds=1)
    expire_holds(conn, now=end)
    assert get_job(conn, jid).state == "done" and get_job(conn, held["job_id"]).state == "cancelled"


def test_expiry_never_cancels_a_job_a_schedule_points_to(door):
    client, d = door
    conn = connect(d.db)
    held = client_post(client, "/burst/hold", {"kind": "cov.league", "params": {}}).json()
    conn.execute("INSERT INTO schedule_runs (name, scheduled_for, job_id, enqueued_at) VALUES (?,?,?,?)",
                 ("cov-league-weekly", iso(NOW), held["job_id"], iso(NOW)))
    expire_holds(conn, now=datetime.fromisoformat(held["hold_until"].replace("Z", "+00:00")) + timedelta(days=1))
    assert get_job(conn, held["job_id"]).state == "queued"


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_a_finished_job_is_refused(door, made, state):
    client, d = door
    _, _, run = made
    jid = _hold(client)
    connect(d.db).execute("UPDATE jobs SET state = ? WHERE id = ?", (state, jid))
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid))
    assert r.status_code == 409 and r.json()["error"] == "not_queued"


@pytest.mark.parametrize("changes", [
    {"params": {**ATLAS, "n_paths": 3_000}},                                  # other params, the job's hash
    {"params": {**ATLAS, "n_paths": 3_000}, "params_hash": params_hash({**ATLAS, "n_paths": 3_000})},
    {"kind": "cov.league"},                                                   # another burstable kind
])
def test_an_artifact_made_with_other_params_or_another_kind_is_refused(door, made, changes):
    client, d = door
    _, _, run = made
    jid = _hold(client)
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid, manifest_changes=changes))
    assert r.status_code == 409 and r.json()["error"] == "job_mismatch", r.text
    assert get_job(connect(d.db), jid).state == "queued"


def test_a_burst_job_is_never_claimed_and_is_cancelled_when_its_hold_expires(door):
    client, d = door
    conn = connect(d.db)
    other = client_post(client, "/burst/hold", {"kind": "cov.league", "params": {}}).json()
    assert other["created"] is True and get_job(conn, other["job_id"]).submitted_by == "burst"
    end = datetime.fromisoformat(other["hold_until"].replace("Z", "+00:00")) + timedelta(days=1)
    assert claim_next(conn, allowed_classes={"S", "M", "L"}, allow_heavy=True, now=end) is None
    expire_holds(conn, now=end)
    j = get_job(conn, other["job_id"])
    assert j.state == "cancelled" and "burst" in (j.error or "")


# --- the door's security ----------------------------------------------------------------------------------------

def test_an_unsigned_or_badly_signed_request_is_refused(door, made):
    client, _ = door
    _, _, run = made
    jid = _hold(client)
    body = _body(run, jid)
    assert client.post(f"/burst/jobs/{jid}/artifact", content=body).status_code == 401
    h = B.signed_headers("t" * 40, "POST", f"/burst/jobs/{jid}/artifact", body)
    assert client.post(f"/burst/jobs/{jid}/artifact", content=body, headers=h).status_code == 401
    h = B.signed_headers(SECRET, "POST", f"/burst/jobs/{jid}/artifact", body)
    assert client.post(f"/burst/jobs/{jid}/artifact", content=body + b" ", headers=h).status_code == 401
    old = B.signed_headers(SECRET, "POST", f"/burst/jobs/{jid}/artifact", body,
                           now=datetime.now(UTC) - timedelta(minutes=10))
    assert client.post(f"/burst/jobs/{jid}/artifact", content=body, headers=old).status_code == 401
    other = B.signed_headers(SECRET, "POST", "/burst/hold", body)            # signed for another path
    assert client.post(f"/burst/jobs/{jid}/artifact", content=body, headers=other).status_code == 401


@pytest.mark.parametrize("secret", [None, "", "short"])
def test_the_door_is_shut_while_its_secret_is_unset(door, made, secret):
    client, d = door
    d.secret = secret
    r = client_post(client, "/burst/hold", {"kind": "risk.mc_atlas", "params": ATLAS}, secret=secret or "x")
    assert r.status_code == 503 and r.json()["error"] == "burst_off"


def test_an_upload_over_the_size_cap_is_413(door, made, monkeypatch):
    client, _ = door
    _, _, run = made
    jid = _hold(client)
    monkeypatch.setattr(B, "MAX_UPLOAD_BYTES", 1000)
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid))
    assert r.status_code == 413


@pytest.mark.parametrize("name", ["../../escape.parquet", "/tmp/abs.parquet", "summary.pkl", "manifest.json",
                                  "extra.parquet", "summary.parquet/../../x.parquet"])
def test_a_file_name_outside_the_kinds_allow_list_is_refused_and_nothing_is_written(door, made, name, tmp_path):
    client, d = door
    _, _, run = made
    jid = _hold(client)
    obj = json.loads(_body(run, jid))
    obj["files"][name] = base64.b64encode(b"PAR1" + b"\0" * 16 + b"PAR1").decode()
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", json.dumps(obj).encode())
    assert r.status_code == 422 and r.json()["error"] == "bad_file", r.text
    assert not list(d.data.parent.rglob("escape.parquet")) and not Path("/tmp/abs.parquet").exists()
    assert not (d.root / "risk.mc_atlas" / jid).exists()


def test_a_table_the_kind_never_produces_is_refused(door, made):
    client, _ = door
    _, _, run = made
    jid = _hold(client)
    obj = json.loads(_body(run, jid, manifest_changes={"tables": [*run.result["tables"], "positions"]}))
    obj["files"]["positions.parquet"] = obj["files"]["summary.parquet"]
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", json.dumps(obj).encode())
    assert r.status_code == 422 and r.json()["error"] == "bad_file"


def test_a_pickle_is_never_loaded(door, made, tmp_path):
    client, d = door
    _, _, run = made
    marker = tmp_path / "pwned"

    class Boom:
        def __reduce__(self):
            return (Path.touch, (marker,))

    jid = _hold(client)
    obj = json.loads(_body(run, jid))
    obj["files"]["summary.parquet"] = base64.b64encode(pickle.dumps(Boom())).decode()
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", json.dumps(obj).encode())
    assert r.status_code == 422 and r.json()["error"] == "bad_file"
    assert not marker.exists()


def test_reads_that_do_not_hash_to_the_manifests_data_hash_are_refused(door, made):
    client, _ = door
    _, _, run = made
    jid = _hold(client)
    obj = json.loads(_body(run, jid))
    reads = json.loads(base64.b64decode(obj["files"]["reads.json"]))
    reads[0]["sha256"] = "0" * 64
    obj["files"]["reads.json"] = base64.b64encode(json.dumps(reads).encode()).decode()
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", json.dumps(obj).encode())
    assert r.status_code == 422 and r.json()["error"] == "inconsistent_manifest", r.text


def test_a_read_no_export_holds_is_refused(door, made):
    client, _ = door
    _, _, run = made
    jid = _hold(client)
    reads = [*run.result["reads"], {"name": "market.quotes{}", "sha256": "a" * 64, "source": "alpaca",
                                    "regenerable": False}]
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid, reads=reads))
    assert r.status_code == 409 and r.json()["error"] == "not_from_export", r.text


def test_a_run_the_facade_served_from_the_committed_fixtures_is_refused(door, made):
    """Offline, a warehouse that was not fresh at the export's moment sends the facade to the committed fixtures --
    real data, but not the export's. The door refuses it (and the tool stops before uploading it)."""
    client, _ = door
    _, _, run = made
    assert not B.fixture_reads(run.result["reads"])               # the fixture run read the export, nothing else
    jid = _hold(client)
    reads = [*run.result["reads"], {"name": "market.returns{}", "sha256": "b" * 64, "source": "derived,fixture:alpaca",
                                    "regenerable": True}]
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid, reads=reads))
    assert r.status_code == 409 and r.json()["error"] == "not_from_export" and "fixtures" in r.json()["detail"]


def test_an_artifact_made_by_other_code_is_refused(door, made):
    client, d = door
    _, _, run = made
    jid = _hold(client)
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid, code_sha="f" * 40))
    assert r.status_code == 409 and r.json()["error"] == "code_drift"


def test_a_manifest_with_a_field_publish_does_not_know_is_refused(door, made):
    client, _ = door
    _, _, run = made
    jid = _hold(client)
    for changes in ({"orders": [{"symbol": "SPY", "qty": 1}]}, {"verdict": "GREAT"}, {"engine": "gpu"},
                    {"host": "worker"}):
        r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid, manifest_changes=changes))
        assert r.status_code == 422, (changes, r.text)


def test_earlier_artifacts_the_run_read_are_checked_against_the_droplets(tmp_path):
    """A farm or Q01 burst carries state from the droplet's earlier artifacts; the door recomputes each lookup
    and each table hash from its own store, and refuses a read the droplet would not have given."""
    from lane_m_harness import publish_artifact

    from ohcamel_quant.jobs.provenance import Recorder
    from ohcamel_quant.products.io import previous_artifact, read_table

    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    old = datetime(2026, 5, 30, tzinfo=UTC)
    aid, path = publish_artifact(db, root, "farm.sweep", {"cells": pd.DataFrame({"x": [1.0]})}, finished=old)
    rec = Recorder()
    with rec.active():
        got = previous_artifact("farm.sweep", db_path=db, worker_only=True)
        read_table(got[1], "cells")
    exported_at = old + timedelta(hours=8)
    D.verify_artifact_reads(rec.reads, db=db, root=root, exported_at=exported_at)          # what the droplet holds
    bad = [dict(r) for r in rec.reads]
    bad[1]["sha256"] = frame_hash(pd.DataFrame({"x": [2.0]}))
    with pytest.raises(D.Refused) as e:
        D.verify_artifact_reads(bad, db=db, root=root, exported_at=exported_at)
    assert e.value.status == 409 and e.value.error == "stale_state"
    # a newer farm artifact on the droplet: the run did not carry the droplet's state forward
    publish_artifact(db, root, "farm.sweep", {"cells": pd.DataFrame({"x": [3.0]})}, finished=old + timedelta(hours=1))
    with pytest.raises(D.Refused) as e:
        D.verify_artifact_reads(rec.reads, db=db, root=root, exported_at=exported_at)
    assert e.value.error == "stale_state"
    # an artifact published after the export: the run would have read state newer than its data
    with pytest.raises(D.Refused) as e:
        D.verify_artifact_reads(rec.reads, db=db, root=root, exported_at=old - timedelta(hours=1))
    assert e.value.error == "stale_state"
    with pytest.raises(D.Refused):                               # a read name the door cannot parse is refused
        D.verify_artifact_reads([{"name": "artifact: ../../x", "sha256": "0" * 64, "source": "artifact"}],
                                db=db, root=root, exported_at=exported_at)


def test_the_droplets_artifacts_are_mirrored_for_the_run(door, tmp_path):
    from lane_m_harness import publish_artifact

    client, d = door
    aid, _ = publish_artifact(d.db, d.root, "farm.sweep", {"cells": pd.DataFrame({"x": [1.0]}),
                                                           "trials": pd.DataFrame({"y": [2]})},
                              finished=NOW - timedelta(days=1))
    held = client_post(client, "/burst/hold", {"kind": "farm.sweep", "params": {"budget_minutes": 1}}).json()
    assert [p["id"] for p in held["previous"]] == [aid]
    local = tmp_path / "data"
    B.mirror(held["previous"], local, client=client, base_url="", secret=SECRET, auth=None)
    from ohcamel_quant.products.io import previous_artifact

    got = previous_artifact("farm.sweep", db_path=local / "jobs.sqlite", worker_only=True)
    assert got[0]["id"] == aid and pd.read_parquet(got[1] / "cells.parquet")["x"].tolist() == [1.0]
    assert client.get(f"/burst/artifact/farm.sweep/{aid}/cells.parquet").status_code == 401     # unsigned
    for name in ("../jobs.sqlite", "reads.json", "nope.parquet"):
        path = f"/burst/artifact/farm.sweep/{aid}/{name}"
        assert client.get(path, headers=B.signed_headers(SECRET, "GET", path, b"")).status_code == 404


# --- fix round 1: Q01's once-only holdout (I2) -------------------------------------------------------------------

def _q01_fakes(monkeypatch, tmp_path, calls):
    """EXP-Q01's run with its config, panel and selection stubbed: no earlier artifact, so the holdout decision
    is "evaluate". ``X.run_holdout`` records the call."""
    from types import SimpleNamespace

    from ohcamel_quant.products import q01

    raw = {"universe": ["SPY"], "methodology_version": 1, "seed": 7}
    cfg = SimpleNamespace(seed=7, universe=["SPY"], benchmark="SPY", grid=[{"a": 1}])
    sel = SimpleNamespace(trials=pd.DataFrame({"t": [1]}), stitched=pd.DataFrame({"v": [1.0]}, index=[0]),
                          selected=0, pbo=0.1)

    def run_holdout(*a, **k):
        calls.append("holdout")
        raise RuntimeError("the holdout ran")

    x = SimpleNamespace(Q01Config=SimpleNamespace(from_dict=lambda r: cfg), build_samples=lambda p, c: None,
                        run_selection=lambda *a, **k: sel, run_holdout=run_holdout, Selection=None)
    monkeypatch.setattr(q01, "load_config", lambda name: raw)
    monkeypatch.setattr(q01, "X", x)
    monkeypatch.setattr(q01, "load_panel", lambda con, t: (None, []))
    monkeypatch.setattr(q01, "DB_PATH", tmp_path / "jobs.sqlite")
    ctx = SimpleNamespace(seed=lambda v, n="seed": v, warehouse=None, threads=1, progress=lambda *a: None)
    return q01, ctx


def test_a_burst_never_evaluates_q01s_holdout(monkeypatch, tmp_path):
    calls: list[str] = []
    q01, ctx = _q01_fakes(monkeypatch, tmp_path, calls)
    with B.burst_context(), pytest.raises(B.HoldoutRefused, match="worker only"):
        q01.run({}, ctx)
    assert calls == []                                    # refused before the holdout was touched
    with pytest.raises(RuntimeError, match="the holdout ran"):   # the worker evaluates it, as before
        q01.run({}, ctx)
    assert calls == ["holdout"]


def test_the_tool_refuses_a_q01_burst_that_would_evaluate_the_holdout_and_deletes_its_scratch(
        made, tmp_path, monkeypatch, capsys):
    _, export, _ = made
    monkeypatch.setenv(B.SECRET_ENV, SECRET)
    monkeypatch.setenv(B.URL_ENV, "https://live.example")
    monkeypatch.setenv(B.AUTH_ENV, "owner:hunter2-password")
    fetched = []

    class Fake:
        def get(self, url, headers=None, auth=None):
            fetched.append(url)
            return SimpleResponse(200, {"previous": []})

    monkeypatch.setattr(B, "_http_client", lambda url, auth: Fake())

    def refuse(*a, **k):
        (tmp_path / "s" / "out").mkdir(parents=True, exist_ok=True)
        (tmp_path / "s" / "out" / "holdout.parquet").write_bytes(b"x")
        raise B.HoldoutRefused("EXP-Q01's holdout would be evaluated: the holdout evaluation runs on the worker only")

    monkeypatch.setattr(B, "run_burst", refuse)
    rc = B.main(["--kind", "models.xs_lgbm", "--export", str(export), "--dry-run", "--scratch", str(tmp_path / "s"),
                 "--keep", "--threads", "1"])
    out = capsys.readouterr()
    text = out.out + out.err
    assert rc == 1 and "worker only" in text and fetched == ["/burst/previous/models.xs_lgbm"]
    assert "verdict" not in text and "hunter2" not in text and SECRET not in text
    assert (tmp_path / "s").is_dir() and list((tmp_path / "s").iterdir()) == []   # emptied, not removed (N4)


def test_a_q01_dry_run_needs_the_droplets_state(made, tmp_path, monkeypatch, capsys):
    _, export, _ = made
    for v in (B.SECRET_ENV, B.URL_ENV, B.AUTH_ENV):
        monkeypatch.delenv(v, raising=False)
    rc = B.main(["--kind", "models.xs_lgbm", "--export", str(export), "--dry-run", "--scratch", str(tmp_path / "s")])
    assert rc == 1 and B.URL_ENV in capsys.readouterr().err


def test_the_door_refuses_a_q01_holdout_the_droplet_did_not_evaluate(tmp_path):
    from lane_m_harness import publish_artifact

    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    ho = pd.DataFrame({"verdict": ["FAIL"], "methodology_version": [1]})
    publish_artifact(db, root, "models.xs_lgbm", {"holdout": ho, "gates": pd.DataFrame({"g": ["dsr"]})},
                     finished=NOW - timedelta(days=3))

    def pq_bytes(df):
        import io
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return buf.getvalue()

    D.check_holdout("models.xs_lgbm", {"holdout": pq_bytes(ho), "gates": pq_bytes(pd.DataFrame({"g": ["dsr"]}))},
                    db=db, root=root)                                       # carried unchanged
    D.check_holdout("models.xs_lgbm", {"scores": pq_bytes(pd.DataFrame({"s": [1.0]}))}, db=db, root=root)
    with pytest.raises(D.Refused) as e:
        D.check_holdout("models.xs_lgbm", {"holdout": pq_bytes(ho.assign(verdict="PASS"))}, db=db, root=root)
    assert e.value.status == 409 and e.value.error == "holdout_worker_only"
    with pytest.raises(D.Refused):                                           # no holdout on the droplet at all
        D.check_holdout("models.xs_lgbm", {"holdout": pq_bytes(ho)}, db=tmp_path / "none.sqlite", root=root)


# --- fix round 1: minors ------------------------------------------------------------------------------------------

def test_caddys_upload_cap_is_the_doors(monkeypatch):
    import re as _re

    text = (REPO_ROOT / "deploy" / "Caddyfile").read_text()
    sizes = _re.findall(r"max_size\s+(\S+)", text)
    assert sizes == [str(B.MAX_UPLOAD_BYTES)]


def test_a_parquet_that_decodes_past_the_cap_is_refused():
    import io

    buf = io.BytesIO()
    pd.DataFrame({"x": [0] * 3_000_000}).to_parquet(buf, index=False, compression="zstd")
    raw = buf.getvalue()
    assert len(raw) < 200_000                                    # small on the wire
    with pytest.raises(D.Refused) as e:
        D._parquet(raw, "summary.parquet", budget=D.DecodeBudget())
    assert e.value.status == 422 and e.value.error == "bad_file" and "decode" in e.value.detail


def test_uploads_are_one_at_a_time(door, made):
    client, _ = door
    _, _, run = made
    jid = _hold(client)
    assert D._UPLOAD_LOCK.acquire(blocking=False)
    try:
        r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid))
        assert r.status_code == 429 and r.json()["error"] == "busy"
    finally:
        D._UPLOAD_LOCK.release()
    assert client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid)).status_code == 201


def test_a_table_whose_columns_differ_from_the_droplets_worker_artifact_is_refused(door, made):
    client, d = door
    art, _, run = made
    conn = connect(d.db)
    wid = art.name
    shutil.copytree(art, d.root / "risk.mc_atlas" / wid)
    conn.execute("INSERT INTO artifacts (id, kind, params_hash, finished_at, path, data_asof) VALUES (?,?,?,?,?,?)",
                 (wid, "risk.mc_atlas", params_hash(ATLAS), iso(NOW - timedelta(days=1)),
                  str(d.root / "risk.mc_atlas" / wid), None))
    jid = _hold(client)
    obj = json.loads(_body(run, jid))
    import io

    s = pd.read_parquet(run.staging / "summary.parquet").rename(columns={"var": "var_renamed"})
    buf = io.BytesIO()
    s.to_parquet(buf, index=False)
    obj["files"]["summary.parquet"] = base64.b64encode(buf.getvalue()).decode()
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", json.dumps(obj).encode())
    assert r.status_code == 422 and r.json()["error"] == "schema_mismatch", r.text
    assert client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(run, jid)).status_code == 201


def test_each_upload_stages_in_its_own_directory(door, made, monkeypatch):
    client, d = door
    _, _, run = made
    seen = []
    real = D.publish_burst

    def spy(conn, job_id, **k):
        seen.append(k["staging"])
        return real(conn, job_id, **k)

    monkeypatch.setattr(D, "publish_burst", spy)
    a, b = _hold(client), _hold(client, kind="cov.league", params={})
    client_post_raw(client, f"/burst/jobs/{a}/artifact", _body(run, a))
    client_post_raw(client, f"/burst/jobs/{b}/artifact", _body(run, b))
    assert seen and all(p.parent == d.root / ".staging" and p.name != f"burst-{a}" for p in seen)


def test_a_replayed_hold_is_refused(door):
    client, _ = door
    body = json.dumps({"kind": "cov.league", "params": {}}).encode()
    h = B.signed_headers(SECRET, "POST", "/burst/hold", body)
    assert client.post("/burst/hold", content=body, headers=h).status_code == 200
    r = client.post("/burst/hold", content=body, headers=h)
    assert r.status_code == 409 and r.json()["error"] == "replayed"


def test_bad_inputs_are_refused_not_500(door):
    client, _ = door
    deep = b"[" * 100_000 + b"]" * 100_000
    assert client_post_raw(client, "/burst/hold", deep).status_code == 422
    deep_params = json.dumps({"kind": "cov.league", "params": {"a": 1}}).replace('{"a": 1}', "[" * 2000 + "]" * 2000)
    assert client_post_raw(client, "/burst/hold", deep_params.encode()).status_code == 422
    h = {B.TS_HEADER: "9" * 5000, B.SIG_HEADER: "0" * 64}
    assert client.post("/burst/hold", content=b"{}", headers=h).status_code == 401
    r = client_post(client, "/burst/hold", {"kind": "farm.sweep", "params": {"budget_minutes": 1e300}})
    assert r.status_code == 422, r.text


def test_retention_always_keeps_the_newest_worker_artifact(tmp_path):
    from lane_m_harness import publish_artifact

    from ohcamel_quant.jobs.artifacts import prune

    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    wid, _ = publish_artifact(db, root, "cov.league", {"league": pd.DataFrame({"x": [1.0]})},
                              finished=NOW - timedelta(hours=3), params={"p": 1})
    bids = []
    for h in (2, 1):
        bid, bpath = publish_artifact(db, root, "cov.league", {"league": pd.DataFrame({"x": [2.0]})},
                                      finished=NOW - timedelta(hours=h), params={"p": 1})
        m = json.loads((bpath / "manifest.json").read_text())
        (bpath / "manifest.json").write_text(json.dumps({**m, "host": "burst"}))
        bids.append(bid)
    prune(connect(db), kind="cov.league", keep=1)
    ids = {r[0] for r in connect(db).execute("SELECT id FROM artifacts")}
    assert wid in ids and bids[-1] in ids


def test_a_lookup_recorded_before_worker_only_still_replays(tmp_path):
    """Artifacts made before this task recorded lookups without " worker_only=True"; regenerate pins by name."""
    from lane_m_harness import publish_artifact

    from ohcamel_quant.jobs.provenance import Recorder
    from ohcamel_quant.products.io import lookup_name, previous_artifact

    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    aid, path = publish_artifact(db, root, "farm.sweep", {"cells": pd.DataFrame({"x": [1.0]})})
    with Recorder(replay={lookup_name("farm.sweep", None, None): str(path)}).active():
        assert previous_artifact("farm.sweep", db_path=db, worker_only=True)[0]["id"] == aid


class SimpleResponse:
    def __init__(self, status, obj):
        self.status_code, self._obj = status, obj
        self.headers = {}
        self.content = json.dumps(obj).encode()
        self.text = self.content.decode()

    def json(self):
        return self._obj


# --- isolation: the public host, the desk -------------------------------------------------------------------------

def test_the_public_app_has_no_burst_route():
    from ohcamel_quant.api.app import create_app

    app = create_app()
    assert not [r for r in app.routes if "burst" in getattr(r, "path", "")]
    c = TestClient(app)
    for path in ("/api/burst/hold", "/burst/hold", "/api/jobs/x/artifact", "/burst/jobs/x/artifact"):
        assert c.post(path, content=b"{}").status_code in (404, 405)


def test_only_the_password_gated_host_routes_to_the_burst_door():
    text = (REPO_ROOT / "deploy" / "Caddyfile").read_text()
    public = text.split("{$OHCAMEL_DEMO_HOST} {", 1)[1].split("\n}\n", 1)[0]
    live = text.split("{$OHCAMEL_LIVE_HOST} {", 1)[1].split("\n}\n", 1)[0]
    assert "burst" not in public
    snippets = (REPO_ROOT / "deploy" / "Caddyfile.snippets").read_text()
    assert "burst" not in snippets.split("(ohcamel_quant) {", 1)[1]
    assert "ohcamel-burst:" in live and live.index("basic_auth") < live.index("ohcamel-burst:")
    assert "ohcamel-burst" not in (REPO_ROOT / "deploy" / "Caddyfile.local").read_text()


def test_the_desk_never_reads_a_burst_artifact(tmp_path):
    from lane_m_harness import publish_artifact

    from ohcamel_quant.products.io import latest_with_path, previous_artifact

    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    wid, _ = publish_artifact(db, root, "cov.league", {"league": pd.DataFrame({"x": [1.0]})},
                              finished=NOW - timedelta(hours=2))
    bid, bpath = publish_artifact(db, root, "cov.league", {"league": pd.DataFrame({"x": [2.0]})},
                                  finished=NOW - timedelta(hours=1))
    m = json.loads((bpath / "manifest.json").read_text())
    (bpath / "manifest.json").write_text(json.dumps({**m, "host": "burst"}))
    assert previous_artifact("cov.league", db_path=db)[0]["id"] == bid
    assert previous_artifact("cov.league", db_path=db, worker_only=True)[0]["id"] == wid
    assert latest_with_path("cov.league", now=NOW, db_path=db, worker_only=True)[0]["id"] == wid


def test_every_artifact_read_on_the_desks_path_is_worker_only():
    """signals/ (the desk's documents, overlay and monthly freeze) and the farm's carry-forward (which the freeze
    reads) look earlier artifacts up worker-only, so a burst artifact never reaches the book."""
    files = sorted((REPO_ROOT / "quant" / "src" / "ohcamel_quant" / "signals").glob("*.py"))
    files.append(REPO_ROOT / "quant" / "src" / "ohcamel_quant" / "products" / "farm.py")
    calls = 0
    for f in files:
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("previous_artifact",
                                                                               "latest_with_path"):
                calls += 1
                kw = {k.arg: k.value for k in node.keywords}
                assert isinstance(kw.get("worker_only"), ast.Constant) and kw["worker_only"].value is True, \
                    f"{f.name}:{node.lineno} looks an artifact up without worker_only=True"
    assert calls >= 7


def test_only_the_four_heavy_kinds_are_burstable():
    from ohcamel_quant.jobs.kinds import REGISTRY

    assert {k for k, s in REGISTRY.items() if s.burstable} == {"farm.sweep", "models.xs_lgbm", "cov.league",
                                                               "risk.mc_atlas"}
    assert set(B.BURST_TABLES) == set(B.LOOKUP_KINDS) == {k for k, s in REGISTRY.items() if s.burstable}


# --- the tool ---------------------------------------------------------------------------------------------------

def test_a_dry_run_stops_before_the_droplet_and_prints_no_secret(made, tmp_path, monkeypatch, capsys):
    _, export, _ = made
    monkeypatch.setenv(B.SECRET_ENV, SECRET)
    monkeypatch.setenv("OHCAMEL_BURST_AUTH", "owner:hunter2-password")

    def no_network(*a, **k):
        raise AssertionError("a dry run must not reach the droplet")

    monkeypatch.setattr(B, "_http_client", no_network)
    rc = B.main(["--kind", "risk.mc_atlas", "--params", json.dumps(ATLAS), "--export", str(export), "--dry-run",
                 "--threads", "1", "--scratch", str(tmp_path / "s")])
    out = capsys.readouterr()
    text = out.out + out.err
    assert rc == 0, text
    assert "dry run" in text and "nothing was held or uploaded" in text
    assert SECRET not in text and "hunter2" not in text


def test_the_tool_refuses_a_kind_that_is_not_burstable(capsys):
    assert B.main(["--kind", "vol.forecast_league", "--dry-run", "--export", "/nonexistent"]) == 2
    assert "not burstable" in capsys.readouterr().err


def test_the_export_digest_covers_every_file(made, tmp_path):
    _, export, _ = made
    copy = tmp_path / "e"
    shutil.copytree(export, copy)
    assert export_digest(copy) == export_digest(export)
    f = next(copy.glob("*.parquet"))
    f.write_bytes(f.read_bytes() + b"\0")
    assert export_digest(copy) != export_digest(export)


# --- helpers ----------------------------------------------------------------------------------------------------

@pytest.fixture
def door(tmp_path, made):
    from ohcamel_quant.api.burst_app import create_burst_app, get_door

    _, export, _ = made
    data = tmp_path / "droplet" / "data"
    exports = tmp_path / "droplet" / "exports"
    shutil.copytree(export, exports / DAY)                        # what the nightly backup left on the droplet
    d = D.Door(db=data / "jobs.sqlite", root=data / "artifacts", exports=exports, secret=SECRET,
               code_sha=checkout_sha(), lock_hash=lock_hash())
    d.data = data
    app = create_burst_app()
    app.dependency_overrides[get_door] = lambda: d
    return TestClient(app), d


def client_post_raw(client, path, body: bytes, secret=SECRET):
    return client.post(path, content=body, headers=B.signed_headers(secret, "POST", path, body))


def client_post(client, path, obj, secret=SECRET):
    return client_post_raw(client, path, json.dumps(obj).encode(), secret=secret)


def _hold(client, kind="risk.mc_atlas", params=ATLAS) -> str:
    r = client_post(client, "/burst/hold", {"kind": kind, "params": params})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def _body(run, job_id, *, manifest_changes=None, reads=None, code_sha=None) -> bytes:
    """The upload the tool would send, with the changes a tampering or confused uploader would make."""
    m = B.manifest_for(run, job_id=job_id, code_sha=code_sha or checkout_sha())
    if reads is not None:
        run = B.BurstRun(**{**run.__dict__, "result": {**run.result, "reads": reads}})
        m = B.manifest_for(run, job_id=job_id, code_sha=code_sha or checkout_sha())
    m.update(manifest_changes or {})
    return B.body_for(run, m)


# --- round 2 -------------------------------------------------------------------------------------------------------

OLD_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS jobs_live_dedupe ON jobs(kind, params_hash) "
             "WHERE state IN ('queued','running')")


def test_cancelling_live_burst_jobs_lets_code_from_before_round_1_open_the_queue(door):
    """N1: code from before ff34e86 creates the old unique index on every connect, which cannot be built while a
    burst job shares (kind, params) with a live scheduled one. `python -m ohcamel_quant.jobs cancel-burst` (the
    runbook's step before a rollback) clears that."""
    import sqlite3

    from ohcamel_quant.jobs.__main__ import main as jobs_cli

    client, d = door
    conn = connect(d.db)
    held = client_post(client, "/burst/hold", {"kind": "risk.mc_atlas", "params": {}}).json()
    enqueue(conn, kind="risk.mc_atlas", params={}, priority=2, mem_class="M", heavy=True,
            submitted_by="schedule:risk-atlas-nightly", now=NOW)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(OLD_INDEX)
    assert jobs_cli(["--db", str(d.db), "cancel-burst"]) == 0
    assert get_job(conn, held["job_id"]).state == "cancelled"
    assert conn.execute("SELECT COUNT(*) FROM job_holds").fetchone()[0] == 0
    conn.execute(OLD_INDEX)                                       # the old code's connect now succeeds
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE state = 'queued'").fetchone()[0] == 1


def test_the_door_checks_any_holdout_table_not_only_holdout(tmp_path):
    """N2: an upload carrying a holdout table without the `holdout` table itself is checked too; an INSUFFICIENT
    DATA run's empty `gates` alone is not a holdout."""
    import io

    from lane_m_harness import publish_artifact

    def pq_bytes(df):
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return buf.getvalue()

    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    rets = pd.DataFrame({"r": [0.01, -0.02]})
    publish_artifact(db, root, "models.xs_lgbm", {"holdout": pd.DataFrame({"verdict": ["FAIL"]}),
                                                   "holdout_returns": rets}, finished=NOW - timedelta(days=3))
    D.check_holdout("models.xs_lgbm", {"holdout_returns": pq_bytes(rets)}, db=db, root=root)
    with pytest.raises(D.Refused) as e:
        D.check_holdout("models.xs_lgbm", {"holdout_returns": pq_bytes(rets * 2)}, db=db, root=root)
    assert e.value.error == "holdout_worker_only"
    empty_gates = pd.DataFrame(columns=["gate", "value", "rule", "passed", "note"])
    D.check_holdout("models.xs_lgbm", {"gates": pq_bytes(empty_gates)}, db=db, root=root)


def test_a_dictionary_column_of_unique_strings_is_counted_once_per_row_group():
    """N3: 200,000 unique strings (~8 MB as Arrow) fit the 96 MiB budget; the dictionary is not counted again in
    every 1,024-row batch."""
    import io

    buf = io.BytesIO()
    pd.DataFrame({"s": [f"cell-{i:08d}-tsmom-us-equity-ix" for i in range(200_000)]}).to_parquet(
        buf, index=False, compression="zstd")
    budget = D.DecodeBudget()
    D._parquet(buf.getvalue(), "cells.parquet", budget=budget)
    assert budget.decoded < 32 * 2**20


def test_the_tool_never_deletes_what_it_did_not_create(made, tmp_path, monkeypatch, capsys):
    """N4: a non-empty --scratch is refused before anything runs; on a holdout refusal an empty --scratch the
    owner named is emptied, not removed."""
    _, export, _ = made
    full = tmp_path / "mine"
    full.mkdir()
    (full / "notes.txt").write_text("keep me")
    assert B.main(["--kind", "risk.mc_atlas", "--params", json.dumps(ATLAS), "--export", str(export), "--dry-run",
                   "--scratch", str(full)]) == 2
    assert (full / "notes.txt").read_text() == "keep me" and "not empty" in capsys.readouterr().err
    monkeypatch.setenv(B.SECRET_ENV, SECRET)
    monkeypatch.setenv(B.URL_ENV, "https://live.example")
    monkeypatch.setenv(B.AUTH_ENV, "owner:pw")
    monkeypatch.setattr(B, "_http_client", lambda url, auth: type("F", (), {
        "get": lambda self, url, headers=None, auth=None: SimpleResponse(200, {"previous": []})})())
    empty = tmp_path / "empty"
    empty.mkdir()

    def refuse(*a, **k):
        (empty / "out").mkdir()
        (empty / "warehouse.duckdb").write_bytes(b"x")
        raise B.HoldoutRefused("the holdout evaluation runs on the worker only")

    monkeypatch.setattr(B, "run_burst", refuse)
    assert B.main(["--kind", "models.xs_lgbm", "--export", str(export), "--dry-run", "--scratch", str(empty)]) == 1
    assert empty.is_dir() and list(empty.iterdir()) == []


def test_schemas_are_compared_only_with_a_worker_artifact_of_the_same_code_and_int_widens_to_float(tmp_path):
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq
    from lane_m_harness import publish_artifact

    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    _, path = publish_artifact(db, root, "cov.league", {"league": pd.DataFrame({"n": [1, 2], "x": [1.0, 2.0]})},
                               finished=NOW - timedelta(days=1))
    m = json.loads((path / "manifest.json").read_text())
    (path / "manifest.json").write_text(json.dumps({**m, "code_sha": "a" * 40}))

    def schema(df):
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return pq.read_schema(pa.BufferReader(buf.getvalue()))

    renamed = {"league": schema(pd.DataFrame({"n": [1], "y": [1.0]}))}
    D.check_schemas("cov.league", renamed, db=db, root=root, code_sha="b" * 40)   # older code made it: not compared
    with pytest.raises(D.Refused) as e:
        D.check_schemas("cov.league", renamed, db=db, root=root, code_sha="a" * 40)
    assert e.value.error == "schema_mismatch"
    widened = {"league": schema(pd.DataFrame({"n": [1.5], "x": [1.0]}))}
    D.check_schemas("cov.league", widened, db=db, root=root, code_sha="a" * 40)


def test_the_tool_retries_a_busy_door_after_its_retry_after(made, monkeypatch):
    _, _, run = made
    answers = [SimpleResponse(429, {"error": "busy", "detail": "another upload"}),
               SimpleResponse(201, {"id": "X", "kind": "risk.mc_atlas"})]
    answers[0].headers = {"Retry-After": "2"}
    slept = []

    class Fake:
        def post(self, url, content=None, headers=None, auth=None):
            return answers.pop(0)

    monkeypatch.setattr(B, "_sleep", slept.append)
    out = B.upload(run, "01JD0000000000000000000000", client=Fake(), base_url="", secret=SECRET, auth=None)
    assert out["id"] == "X" and slept == [2.0]
    busy = [SimpleResponse(429, {"error": "busy", "detail": "x"}) for _ in range(B.BUSY_RETRIES + 1)]
    for b in busy:
        b.headers = {"Retry-After": "999"}

    class Busy:
        def post(self, url, content=None, headers=None, auth=None):
            return busy.pop(0)

    with pytest.raises(B.BurstError, match="429"):
        B.upload(run, "01JD0000000000000000000000", client=Busy(), base_url="", secret=SECRET, auth=None)
    assert all(s <= B.BUSY_MAX_WAIT_S for s in slept)


# --- completion task 2.4: a burst farm run's trials reach the droplet's trial ledger, and nothing else does -------

FARM = {"budget_minutes": 30}
FARM_CELL = ("sma_trend", "fixture")


def _one_farm_cell(mp):
    """The farm narrowed to one cell on the fixture ETFs, on the burst machine and at the door alike."""
    from ohcamel_quant.models import farm as mfarm
    from ohcamel_quant.products import farm as pfarm

    mp.setattr(mfarm, "farm_cells", lambda: [FARM_CELL])
    mp.setattr(pfarm, "farm_cells", lambda: [FARM_CELL])
    mp.setattr(pfarm, "_universe", lambda name: ["SPY", "QQQ", "IWM"])


@pytest.fixture(scope="module")
def farm_run(made, tmp_path_factory):
    _, export, _ = made
    with pytest.MonkeyPatch.context() as mp:
        _one_farm_cell(mp)
        run = B.run_burst("farm.sweep", FARM, export, scratch=tmp_path_factory.mktemp("farm") / "scratch", threads=1)
    led = pd.read_parquet(run.staging / "ledger.parquet")
    assert len(led) == 16 and set(led["source"]) == {"burst"}, led
    return run


def _tampered(run, tmp_path, change):
    staging = tmp_path / "tampered"
    shutil.copytree(run.staging, staging)
    led = pd.read_parquet(staging / "ledger.parquet")
    change(led)
    led.to_parquet(staging / "ledger.parquet")
    return B.BurstRun(**{**run.__dict__, "staging": staging})


def _droplet_ledger(d):
    from ohcamel_quant.models import trial_ledger as L

    return L.ledger_path(d.root)


def test_a_burst_farm_upload_appends_its_trials_once(door, farm_run, monkeypatch):
    from ohcamel_quant.models import trial_ledger as L

    client, d = door
    _one_farm_cell(monkeypatch)
    jid = _hold(client, "farm.sweep", FARM)
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(farm_run, jid))
    assert r.status_code == 201, r.text
    got = L.distinct(_droplet_ledger(d))
    assert len(got) == 16 and set(got["source"]) == {"burst"}
    once = _droplet_ledger(d).read_bytes()
    jid2 = _hold(client, "farm.sweep", {**FARM, "budget_minutes": 31})   # the same trials, another job: nothing new
    run2 = B.BurstRun(**{**farm_run.__dict__, "params": {**FARM, "budget_minutes": 31}})
    r = client_post_raw(client, f"/burst/jobs/{jid2}/artifact", _body(run2, jid2, manifest_changes={
        "params": run2.params, "params_hash": params_hash(run2.params)}))
    assert r.status_code == 201, r.text
    assert _droplet_ledger(d).read_bytes() == once


def _forge_outside_grid(led):
    led.loc[0, "params_hash"] = "sha256:" + "0" * 64


def _forge_sharpe(led):
    led.loc[0, "sharpe"] = float(led.loc[0, "sharpe"]) - 0.01


def _forge_worker(led):
    led.loc[0, "source"] = "worker"


@pytest.mark.parametrize("forge", [_forge_outside_grid, _forge_sharpe, _forge_worker],
                         ids=["outside_the_grid", "sharpe_not_the_runs", "says_worker"])
def test_a_burst_farm_upload_with_a_forged_ledger_row_is_422_and_appends_nothing(door, farm_run, monkeypatch,
                                                                                tmp_path, forge):
    client, d = door
    _one_farm_cell(monkeypatch)
    jid = _hold(client, "farm.sweep", FARM)
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", _body(_tampered(farm_run, tmp_path, forge), jid))
    assert r.status_code == 422 and r.json()["error"] == "bad_file", r.text
    assert not _droplet_ledger(d).exists()
    assert get_job(connect(d.db), jid).state == "queued"
