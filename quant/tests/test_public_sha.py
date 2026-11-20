"""The owner's ruling of 2026-11-20: the public host shows no commit SHA.

The Quant app (api/app.py) is what the public host serves (deploy/Caddyfile: the public block proxies to
ohcamel-quant and nothing else; the gated live host proxies to the OCaml engine and the burst door). So every JSON
response of the Quant app passes one redaction on its way out (api/redact.py): a commit SHA -- full or abbreviated,
under any key, nested anywhere, inside a sentence -- never leaves it. The stored manifests keep ``code_sha``, the
gated host's burst door still returns the deployment's, and regenerate reads the files.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest
import respx
from fastapi.testclient import TestClient

from ohcamel_quant.api.app import create_app

SHA = "4f1c2a9be07d3356c8e1f0a2b4c6d8e0f1a2b3c4"        # an artifact's code_sha
BUILD = "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d"      # this image's OHCAMEL_GIT_SHA
ENGINE_SHA = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"  # the live engine's build
HEX = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{7,40}(?![0-9a-fA-F])")
NOW = datetime(2026, 6, 1, 21, 0, tzinfo=UTC)


def leaks(text: str, *shas: str) -> list[str]:
    """Every 7-to-40 hex run in ``text`` that equals or prefixes one of ``shas``."""
    return [t for t in HEX.findall(text) if any(s.startswith(t.lower()) for s in shas)]


@pytest.fixture
def store(tmp_path, monkeypatch):
    from lane_m_harness import publish_artifact

    monkeypatch.setenv("OHCAMEL_GIT_SHA", BUILD)
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    aid, path = publish_artifact(db, root, "cov.league", {"league": pd.DataFrame({"x": [1.0]})},
                                 finished=NOW - timedelta(hours=1))
    m = json.loads((path / "manifest.json").read_text())
    m.update(code_sha=SHA, notes=[f"made by {SHA[:7]} on the droplet"],
             provenance=[{"source": "ohcamel", "commit": SHA[:12], "nested": [{"code": SHA}]}],
             host="burst", burst={"machine": {"platform": "macOS"}, "built_from": SHA})
    (path / "manifest.json").write_text(json.dumps(m))
    return db, aid, path


def _app(db):
    from ohcamel_quant.api.routers.artifacts import get_artifacts_db_path
    from ohcamel_quant.api.routers.jobs import get_jobs_db_path

    app = create_app()
    app.dependency_overrides[get_artifacts_db_path] = lambda: db
    app.dependency_overrides[get_jobs_db_path] = lambda: db
    return app


def test_the_artifacts_api_carries_no_commit_sha(store):
    db, aid, path = store
    c = TestClient(_app(db))
    for url in ("/api/artifacts/cov.league/latest", f"/api/artifacts/cov.league/{aid}",
                f"/api/artifacts/cov.league/{aid}/league"):
        r = c.get(url)
        assert r.status_code == 200, (url, r.text)
        assert leaks(r.text, SHA, BUILD) == [], (url, leaks(r.text, SHA, BUILD))
        assert "code_sha" not in r.text
    body = c.get(f"/api/artifacts/cov.league/{aid}").json()
    assert body["manifest"]["notes"] and body["manifest"]["host"] == "burst"          # the rest is served
    assert json.loads((path / "manifest.json").read_text())["code_sha"] == SHA        # the stored manifest keeps it


def test_the_machine_api_carries_no_commit_sha(store):
    from ohcamel_quant.api.routers import machine as machine_router
    from ohcamel_quant.machine.service import MachineCache

    db, _, _ = store
    graph = {"nodes": [{"id": "p3", "k": "product", "n": "Covariance league",
                        "m": {"kind": "cov.league", "code": SHA, "verdict": "PASS", "engine": "python"}}],
             "edges": [], "asof": NOW.isoformat()}
    app = _app(db)
    app.dependency_overrides[machine_router.get_machine_db_path] = lambda: db
    app.dependency_overrides[machine_router.get_machine_cache] = lambda: MachineCache(lambda now: graph, lambda: None)
    r = TestClient(app).get("/api/machine")
    assert r.status_code == 200, r.text
    assert leaks(r.text, SHA, BUILD) == [] and r.json()["nodes"][0]["m"]["verdict"] == "PASS"
    assert "code" not in r.json()["nodes"][0]["m"]


def test_ops_and_the_engine_bridge_carry_no_commit_sha(store, monkeypatch):
    from ohcamel_quant.api.routers import engine as engine_mod

    db, _, _ = store
    app = _app(db)
    app.dependency_overrides[engine_mod.get_engine_url] = lambda: "http://engine.test:8081"
    app.dependency_overrides[engine_mod.get_book_public] = lambda: False
    engine_mod._cache.clear()
    c = TestClient(app)
    ops = c.get("/api/ops")
    assert ops.status_code == 200 and leaks(ops.text, BUILD) == [] and "git_sha" not in ops.text
    with respx.mock:
        respx.get("http://engine.test:8081/api/ops").mock(return_value=httpx.Response(200, json={
            "mode": "live", "uptime_s": 5, "build": {"git_sha": ENGINE_SHA, "built_at": "2026-11-20T00:00:00Z"}}))
        respx.get("http://engine.test:8081/api/health").mock(return_value=httpx.Response(200, json={"healthy": True}))
        for url in ("/api/engine/vitals", "/api/engine/status"):
            r = c.get(url)
            assert leaks(r.text, ENGINE_SHA, BUILD) == [], (url, r.text)
    engine_mod._cache.clear()


def test_the_ledger_and_health_carry_no_commit_sha(store):
    db, _, _ = store
    c = TestClient(_app(db))
    for url in ("/api/health", "/api/ledger"):
        r = c.get(url)
        assert leaks(r.text, SHA, BUILD) == [], (url, r.text)


def test_data_that_only_looks_like_hex_is_untouched():
    """A CIK, a date, a 64-hex data hash and a ULID are not commit SHAs; only a known SHA (or a full 40-hex SHA-1)
    is removed."""
    from ohcamel_quant.api.redact import redact_payload

    data = {"cik": "0000320193", "day": "20260601", "data_hash": "a" * 64, "id": "01JD0000000000000000000000",
            "word": "defaced1", "code_sha": SHA, "note": f"by {SHA[:9]}", "full": BUILD, "list": [SHA, 1]}
    out = redact_payload(data, extra=())
    assert out["cik"] == "0000320193" and out["day"] == "20260601" and out["data_hash"] == "a" * 64
    assert out["id"] == data["id"] and out["word"] == "defaced1"
    assert "code_sha" not in out and "full" not in out and SHA[:9] not in out["note"] and out["list"] == [None, 1]


def test_the_gated_hosts_burst_door_still_returns_the_deployments_sha(tmp_path):
    """The live host is unchanged: the burst tool needs the deployment's SHA to refuse other code before a run."""
    from ohcamel_quant.api.burst_app import create_burst_app, get_door
    from ohcamel_quant.jobs import burst as B
    from ohcamel_quant.jobs import burst_door as D

    d = D.Door(db=tmp_path / "jobs.sqlite", root=tmp_path / "artifacts", exports=tmp_path / "exports",
               secret="s" * 40, code_sha=SHA, lock_hash=None)
    app = create_burst_app()
    app.dependency_overrides[get_door] = lambda: d
    body = json.dumps({"kind": "cov.league", "params": {}}).encode()
    r = TestClient(app).post("/burst/hold", content=body, headers=B.signed_headers("s" * 40, "POST", "/burst/hold", body))
    assert r.status_code == 200 and r.json()["code_sha"] == SHA
