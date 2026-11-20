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
    assert client_post_raw(client, f"/burst/jobs/{jid}/artifact", body).status_code == 201
    r = client_post_raw(client, f"/burst/jobs/{jid}/artifact", body)
    assert r.status_code == 409 and r.json()["error"] == "already_done"
    assert r.json()["published"] == {"id": jid, "host": "burst", "data_hash": run.result_data_hash}


def test_a_job_the_worker_has_claimed_is_refused(door, made):
    client, d = door
    _, _, run = made
    conn = connect(d.db)
    job, _ = enqueue(conn, kind="risk.mc_atlas", params=ATLAS, priority=2, mem_class="M", heavy=True,
                     submitted_by="schedule:mc-atlas", now=NOW)
    assert claim_next(conn, allowed_classes={"S", "M", "L"}, allow_heavy=True, now=NOW).id == job.id
    assert client_post(client, "/burst/hold", {"kind": "risk.mc_atlas", "params": ATLAS}).status_code == 409
    r = client_post_raw(client, f"/burst/jobs/{job.id}/artifact", _body(run, job.id))
    assert r.status_code == 409 and r.json()["error"] == "claimed"
    assert get_job(conn, job.id).state == "running"


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


def test_a_held_job_is_not_claimed_until_its_hold_expires_and_a_burst_job_never_is(door):
    client, d = door
    conn = connect(d.db)
    job, _ = enqueue(conn, kind="risk.mc_atlas", params=ATLAS, priority=2, mem_class="M", heavy=True,
                     submitted_by="schedule:mc-atlas", now=NOW)
    held = client_post(client, "/burst/hold", {"kind": "risk.mc_atlas", "params": ATLAS}).json()
    assert held["job_id"] == job.id and held["created"] is False
    later = datetime.fromisoformat(held["hold_until"].replace("Z", "+00:00"))
    assert claim_next(conn, allowed_classes={"S", "M", "L"}, allow_heavy=True, now=later - timedelta(seconds=1)) is None
    assert claim_next(conn, allowed_classes={"S", "M", "L"}, allow_heavy=True, now=later).id == job.id
    # a job the burst door created runs nowhere but on the burst machine; its hold expiring cancels it
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
