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


# --- round 2: the two committed perf recordings, and every public GET route -------------------------------------

def _perf_sha(name: str) -> str:
    from ohcamel_quant.config import REPO_ROOT

    body = json.loads((REPO_ROOT / "docs" / "perf" / name).read_text())
    rows = body if isinstance(body, list) else body.get("kernels", body)
    if isinstance(rows, list):
        return next(str(r["sha"]) for r in rows if isinstance(r, dict) and r.get("sha"))
    return str(body["sha"])


def test_the_propagation_and_kernel_recordings_carry_no_commit_sha(tmp_path):
    """docs/perf/propagation.json and kernels.json name the commit they were recorded at (12 characters, under
    ``sha``); the files stay as they are, the public routes drop it."""
    prop, kern = _perf_sha("propagation.json"), _perf_sha("kernels.json")
    c = TestClient(_app(tmp_path / "jobs.sqlite"))
    for url, sha in (("/api/engine/propagation", prop), ("/api/ops/kernels", kern)):
        r = c.get(url)
        assert r.status_code == 200, (url, r.text)
        assert leaks(r.text, sha) == [] and '"sha"' not in r.text, url


def test_a_digits_only_value_is_never_taken_for_a_sha():
    from ohcamel_quant.api.redact import redact_payload

    digits = "1234567890123456789012345678901234567890"
    out = redact_payload({"a": digits, "b": "1234567", "c": "n 1234567 m"}, extra=("1234567abc",))
    assert out == {"a": digits, "b": "1234567", "c": "n 1234567 m"}


def test_the_job_event_stream_carries_only_its_fixed_fields(tmp_path):
    """text/event-stream is not redacted (api/redact.py): the job stream's events are a fixed set of fields -- the
    job's id, state, progress and message -- and none of them is a commit SHA."""
    from ohcamel_quant.api.routers.jobs import get_jobs_db_path, get_sse_idle, get_sse_lifetime
    from ohcamel_quant.jobs.db import connect
    from ohcamel_quant.jobs.queue import enqueue

    db = tmp_path / "jobs.sqlite"
    app = create_app()
    app.dependency_overrides[get_jobs_db_path] = lambda: db
    app.dependency_overrides[get_sse_lifetime] = lambda: 1.0
    app.dependency_overrides[get_sse_idle] = lambda: 0.6
    enqueue(connect(db), kind="ops.selftest", params={}, priority=0, mem_class="S", heavy=False, submitted_by="api",
            now=NOW)
    with TestClient(app).stream("GET", "/api/jobs/events", headers={"Last-Event-ID": "0"}) as r:
        text = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    assert events and all(set(e) == {"type", "id", "state", "progress", "message"} for e in events)
    assert leaks(text, BUILD, SHA) == []


ROUTE_PARAMS = {"kind": "cov.league", "table": "league", "ticker": "SPY", "cik": "0001067983"}
QUERY_PARAMS = {"ids": "DGS10", "tickers": "SPY", "q": "apple"}
NOT_WALKED = {"/api/jobs/events"}   # a stream (test_the_job_event_stream_carries_only_its_fixed_fields)


def _commits(tokens: set[str]) -> set[str]:
    """The tokens git resolves to a commit object in this repository."""
    import subprocess

    from ohcamel_quant.config import REPO_ROOT

    if not tokens:
        return set()
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "--batch-check"], input="\n".join(sorted(tokens)),
                         capture_output=True, text=True, check=True).stdout
    found = set()
    for want, line in zip(sorted(tokens), out.splitlines(), strict=True):
        parts = line.split()
        if len(parts) == 3 and parts[1] == "commit":
            found.add(want)
    return found


@pytest.fixture(scope="module")
def walked(tmp_path_factory):
    """Every public GET route the app registers (its OpenAPI paths), each answered with fixture data present: an
    artifact whose manifest names this checkout's HEAD, a job, the machine built from that store, and an engine
    behind the bridge whose build is HEAD too. -> {path: response text}."""
    import subprocess

    from lane_m_harness import publish_artifact
    from test_engine_bridge import HEALTH, HISTORY, OPS, SNAPSHOT

    from ohcamel_quant.api.routers import engine as engine_mod
    from ohcamel_quant.api.routers import machine as machine_router
    from ohcamel_quant.config import REPO_ROOT, Settings
    from ohcamel_quant.jobs.db import connect
    from ohcamel_quant.jobs.queue import enqueue
    from ohcamel_quant.machine.service import MachineCache, build_machine

    head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    root = tmp_path_factory.mktemp("walk")
    db = root / "jobs.sqlite"
    aid, path = publish_artifact(db, root / "artifacts", "cov.league", {"league": pd.DataFrame({"x": [1.0]})},
                                 finished=NOW - timedelta(hours=1))
    m = json.loads((path / "manifest.json").read_text())
    m.update(code_sha=head, notes=[f"made at {head[:9]}"])
    (path / "manifest.json").write_text(json.dumps(m))
    job, _ = enqueue(connect(db), kind="ops.selftest", params={}, priority=0, mem_class="S", heavy=False,
                     submitted_by="api", now=NOW)
    app = _app(db)
    settings = Settings(offline=True, data_dir=root)
    app.dependency_overrides[machine_router.get_machine_db_path] = lambda: db
    app.dependency_overrides[machine_router.get_machine_cache] = lambda: MachineCache(
        lambda now: build_machine(db, settings, now), lambda: None)
    app.dependency_overrides[engine_mod.get_engine_url] = lambda: "http://engine.test:8081"
    app.dependency_overrides[engine_mod.get_book_public] = lambda: False
    engine_mod._cache.clear()
    ops = {**OPS, "build": {"git_sha": head, "built_at": "2026-11-20T00:00:00Z"}}
    params = {**ROUTE_PARAMS, "art_id": aid, "job_id": job.id}
    out = {}
    with respx.mock(assert_all_called=False) as mock:
        for p, body in (("ops", ops), ("health", HEALTH), ("snapshot", SNAPSHOT), ("history", HISTORY)):
            mock.get(f"http://engine.test:8081/api/{p}").mock(return_value=httpx.Response(200, json=body))
        c = TestClient(app)
        for p, item in sorted(app.openapi()["paths"].items()):
            if "get" not in item or not p.startswith("/api/") or p in NOT_WALKED or p.startswith("/api/docs"):
                continue
            url = p.format(**params)
            query = {x["name"]: QUERY_PARAMS[x["name"]] for x in item["get"].get("parameters", [])
                     if x.get("in") == "query" and x.get("required")}
            out[p] = c.get(url, params=query).text
    engine_mod._cache.clear()
    return out


def test_no_public_get_route_names_a_commit_of_this_repository(walked):
    """The class, not the instances: every 7-to-40 hex run (with a letter in it: a run of digits is a number) in
    every public GET route's answer is looked up with git; none may be a commit. In a shallow clone older commits
    are absent and pass unseen; the named-route tests above do not depend on git."""
    assert len(walked) >= 45
    seen = {p: {t.lower() for t in HEX.findall(text) if re.search(r"[a-fA-F]", t)} for p, text in walked.items()}
    hits = {p: sorted(_commits(toks)) for p, toks in seen.items()}
    assert {p: h for p, h in hits.items() if h} == {}
