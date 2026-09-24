"""The read-only bridge to the OCaml engine (api/routers/engine.py).

The engine is mocked with respx at the HTTP layer, with bodies in the shapes
lib/server.ml writes (json_of_feed_health, json_of_snapshot, json_of_history),
so these tests pin the bridge's own behaviour: the allow-list, the 503s, the
cache and the wrapping -- never the engine's numbers.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.routers import engine as engine_mod

ENGINE = "http://engine.test:8081"

HEALTH = {
    "healthy": True,
    "stale": [],
    "never_seen": [],
    "symbols": [
        {"symbol": "AAPL", "last_tick": "2026-09-24 18:00:00.000000Z", "never_seen": False, "stale": False}
    ],
}
OPS = {
    "mode": "live",
    "uptime_s": 1234,
    "build": {"git_sha": "abc1234", "built_at": "2026-09-24T00:00:00Z"},
    "feed": {"healthy": True, "symbols": 1, "stale": 0, "never_seen": 0},
    "stream": {"frames_sent": 10, "subscribers": 0},
    "gc": {"minor": 1},
}
SNAPSHOT = {
    "as_of": "2026-09-24 18:00:00.000000Z",
    "positions": [{"symbol": "AAPL", "exposure": 10000.0, "component_var": None}],
    "gross_exposure": 10000.0,
    "value_at_risk_notional": None,
    "warming_up": True,
    "nodes_recomputed": 42,
}
HISTORY = {
    "points": 2,
    "capacity": 500,
    "appended": 2,
    "time": [1.0, 2.0],
    "gross": [1.0, 2.0],
    "net": [1.0, 2.0],
    "equity": [1.0, 1.0],
    "drawdown": [0.0, 0.0],
    "var_notional": [None, 3.0],
    "es_notional": [None, 4.0],
}


def _client(url: str | None) -> TestClient:
    app = create_app()
    app.dependency_overrides[engine_mod.get_engine_url] = lambda: url
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache():
    engine_mod._cache.clear()
    yield
    engine_mod._cache.clear()


@pytest.mark.parametrize("path", ["/api/engine/status", "/api/engine/snapshot", "/api/engine/history"])
def test_unconfigured_is_503_with_reason(path):
    r = _client(None).get(path)
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "engine_unavailable"
    assert body["configured"] is False and body["reachable"] is False
    assert "OHCAMEL_QUANT_ENGINE_URL" in body["detail"]


def test_empty_url_counts_as_unset(monkeypatch):
    from ohcamel_quant.config import Settings

    monkeypatch.setattr(engine_mod, "get_settings", lambda: Settings(engine_url="  "))
    assert engine_mod.get_engine_url() is None
    monkeypatch.setattr(engine_mod, "get_settings", lambda: Settings(engine_url="http://x:1/"))
    assert engine_mod.get_engine_url() == "http://x:1"


@respx.mock
def test_status_reports_health_and_ops_subset():
    respx.get(f"{ENGINE}/api/health").mock(return_value=httpx.Response(200, json=HEALTH))
    respx.get(f"{ENGINE}/api/ops").mock(return_value=httpx.Response(200, json=OPS))
    r = _client(ENGINE).get("/api/engine/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True and body["reachable"] is True
    assert body["health"] == HEALTH
    assert body["ops"]["mode"] == "live" and body["ops"]["build"]["git_sha"] == "abc1234"
    assert "gc" not in body["ops"]  # only the documented subset is passed through
    assert body["provenance"][0]["source"] == "ohcamel-engine"
    assert body["provenance"][0]["synthetic"] is False
    assert isinstance(body["latency_ms"], float)


@respx.mock
def test_status_survives_ops_failure_and_says_so():
    respx.get(f"{ENGINE}/api/health").mock(return_value=httpx.Response(200, json=HEALTH))
    respx.get(f"{ENGINE}/api/ops").mock(return_value=httpx.Response(500))
    body = _client(ENGINE).get("/api/engine/status").json()
    assert body["reachable"] is True and body["ops"] is None
    assert any("/api/ops" in n for n in body["notes"])


@respx.mock
def test_demo_mode_is_labelled_synthetic():
    respx.get(f"{ENGINE}/api/health").mock(return_value=httpx.Response(200, json=HEALTH))
    respx.get(f"{ENGINE}/api/ops").mock(return_value=httpx.Response(200, json={**OPS, "mode": "demo"}))
    body = _client(ENGINE).get("/api/engine/status").json()
    assert any("synthetic" in n for n in body["notes"])


@respx.mock
def test_snapshot_and_history_pass_engine_json_through_unchanged():
    respx.get(f"{ENGINE}/api/snapshot").mock(return_value=httpx.Response(200, json=SNAPSHOT))
    respx.get(f"{ENGINE}/api/history").mock(return_value=httpx.Response(200, json=HISTORY))
    c = _client(ENGINE)
    snap = c.get("/api/engine/snapshot").json()
    assert snap["snapshot"] == SNAPSHOT
    assert any("warming up" in n for n in snap["notes"])
    hist = c.get("/api/engine/history").json()
    assert hist["history"] == HISTORY
    assert hist["provenance"][0]["detail"]["path"] == "/api/history"


@respx.mock
def test_connection_refused_is_503():
    respx.get(f"{ENGINE}/api/snapshot").mock(side_effect=httpx.ConnectError("refused"))
    r = _client(ENGINE).get("/api/engine/snapshot")
    assert r.status_code == 503
    body = r.json()
    assert body["configured"] is True and "unreachable" in body["detail"]


@respx.mock
def test_timeout_is_503():
    respx.get(f"{ENGINE}/api/history").mock(side_effect=httpx.ReadTimeout("slow"))
    r = _client(ENGINE).get("/api/engine/history")
    assert r.status_code == 503 and "within" in r.json()["detail"]


@respx.mock
def test_non_200_and_non_json_are_503():
    respx.get(f"{ENGINE}/api/snapshot").mock(return_value=httpx.Response(502, text="bad gateway"))
    r = _client(ENGINE).get("/api/engine/snapshot")
    assert r.status_code == 503 and "HTTP 502" in r.json()["detail"]
    engine_mod._cache.clear()
    respx.get(f"{ENGINE}/api/history").mock(return_value=httpx.Response(200, text="<html>"))
    r = _client(ENGINE).get("/api/engine/history")
    assert r.status_code == 503 and "not JSON" in r.json()["detail"]


@respx.mock
def test_cache_collapses_reads_within_ttl():
    route = respx.get(f"{ENGINE}/api/snapshot").mock(return_value=httpx.Response(200, json=SNAPSHOT))
    c = _client(ENGINE)
    c.get("/api/engine/snapshot")
    c.get("/api/engine/snapshot")
    assert route.call_count == 1


def test_no_mutating_or_desk_route_is_reachable():
    # The allow-list is the whole surface: GET-only, and no desk path.
    assert set(engine_mod.ENGINE_PATHS.values()) == {
        "/api/health",
        "/api/ops",
        "/api/snapshot",
        "/api/history",
    }
    assert not any("desk" in p for p in engine_mod.ENGINE_PATHS.values())
    methods = {m for r in engine_mod.router.routes for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD"}
    c = _client(ENGINE)
    for path in ("/api/engine/desk/orders", "/api/engine/snapshot"):
        assert c.post(path, json={}).status_code in (404, 405)
