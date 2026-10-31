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
    "hostname": "a1b2c3d4",
    "pid": 7,
    "process": {"nodes_recomputed": 9000, "stabilizes": 400, "nodes_created": 12},
    "graph": {"named": {"distinct": 53, "total": 800, "hottest": [{"name": "exposure:AAPL", "n": 9}]}},
    "stabilize_ms": {"n": 4096, "p50": 0.03, "p99": 0.4, "max": 1.2, "window": 4096},
    "alerts": {"enabled": True, "kill_switch": "armed"},
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


def _client(url: str | None, *, book_public: bool = True) -> TestClient:
    app = create_app()
    app.dependency_overrides[engine_mod.get_engine_url] = lambda: url
    app.dependency_overrides[engine_mod.get_book_public] = lambda: book_public
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
    assert body["ops"]["feed"] == {"healthy": True, "symbols": 1, "stale": 0, "never_seen": 0}
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


# ---------------------------------------------------------------- the book stays private
def test_the_book_is_private_unless_the_deployment_says_otherwise():
    from ohcamel_quant.config import Settings

    assert Settings().engine_book_public is False


@pytest.mark.parametrize("path", ["/api/engine/snapshot", "/api/engine/history"])
@respx.mock
def test_a_private_book_never_reaches_the_engine(path):
    route = respx.get(url__startswith=ENGINE)
    r = _client(ENGINE, book_public=False).get(path)
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "engine_book_private"
    # The answer the pages already draw as BRIDGE OFF.
    assert body["configured"] is False and body["reachable"] is False
    assert not route.called


@respx.mock
def test_status_without_the_book_keeps_counts_and_loses_names():
    respx.get(f"{ENGINE}/api/health").mock(return_value=httpx.Response(200, json=HEALTH))
    respx.get(f"{ENGINE}/api/ops").mock(return_value=httpx.Response(200, json=OPS))
    r = _client(ENGINE, book_public=False).get("/api/engine/status")
    assert r.status_code == 200
    assert "AAPL" not in r.text
    body = r.json()
    assert body["health"] == {"healthy": True}
    assert body["ops"]["feed"]["symbols"] == 1
    assert body["book_public"] is False


@respx.mock
def test_status_names_what_it_copies_so_a_new_nested_field_stays_behind():
    ops = {**OPS, "feed": {**OPS["feed"], "staleness_threshold_s": 90.0},
           "build": {**OPS["build"], "executable": "/app/secret-path"},
           "stream": {**OPS["stream"], "next_thing": "AAPL"}}
    respx.get(f"{ENGINE}/api/health").mock(return_value=httpx.Response(200, json=HEALTH))
    respx.get(f"{ENGINE}/api/ops").mock(return_value=httpx.Response(200, json=ops))
    r = _client(ENGINE, book_public=False).get("/api/engine/status")
    for private in ("staleness_threshold_s", "secret-path", "AAPL"):
        assert private not in r.text


@respx.mock
def test_a_cached_read_reports_the_real_round_trip_not_zero():
    respx.get(f"{ENGINE}/api/ops").mock(return_value=httpx.Response(200, json=OPS))
    client = _client(ENGINE, book_public=False)
    first = client.get("/api/engine/vitals").json()["latency_ms"]
    assert client.get("/api/engine/vitals").json()["latency_ms"] == first


# ------------------------------------------------------------------------- vitals
@respx.mock
def test_vitals_copy_numbers_and_nothing_else():
    respx.get(f"{ENGINE}/api/ops").mock(return_value=httpx.Response(200, json=OPS))
    r = _client(ENGINE, book_public=False).get("/api/engine/vitals")
    assert r.status_code == 200
    body = r.json()
    assert body["stabilize_ms"] == {"n": 4096, "p50": 0.03, "p99": 0.4, "max": 1.2, "window": 4096}
    assert body["process"] == {"nodes_recomputed": 9000, "stabilizes": 400}
    assert body["feed"] == {"healthy": True, "symbols": 1, "stale": 0, "never_seen": 0}
    assert body["mode"] == "live" and body["git_sha"] == "abc1234" and body["uptime_s"] == 1234
    assert body["reachable"] is True and body["book_public"] is False
    for private in ("AAPL", "exposure", "hostname", "a1b2c3d4", "kill_switch"):
        assert private not in r.text


@respx.mock
def test_vitals_refuse_a_field_of_the_wrong_type():
    ops = {**OPS, "mode": "AAPL long 400", "uptime_s": "AAPL",
           "build": {"git_sha": "AAPL"},
           "stabilize_ms": {"n": "AAPL", "p50": None, "p99": True, "max": 1.0, "window": 4096}}
    respx.get(f"{ENGINE}/api/ops").mock(return_value=httpx.Response(200, json=ops))
    r = _client(ENGINE, book_public=False).get("/api/engine/vitals")
    assert r.status_code == 200
    assert "AAPL" not in r.text
    body = r.json()
    assert body["mode"] is None and body["uptime_s"] is None and body["git_sha"] is None
    assert body["stabilize_ms"] == {"n": None, "p50": None, "p99": None, "max": 1.0, "window": 4096}


def test_vitals_are_503_when_the_bridge_is_off():
    r = _client(None).get("/api/engine/vitals")
    assert r.status_code == 503
    assert r.json()["configured"] is False


# -------------------------------------------------------------------- the recording
def _with_recording(path):
    app = create_app()
    app.dependency_overrides[engine_mod.get_propagation_path] = lambda: path
    return TestClient(app)


def test_the_recording_is_served_with_what_it_is(tmp_path):
    f = tmp_path / "propagation.json"
    f.write_text('{"schema": 1, "sha": "abc", "machine": "a laptop", "events": [], "graph": {"nodes": []}}')
    body = _with_recording(f).get("/api/engine/propagation").json()
    assert body["schema"] == 1 and body["machine"] == "a laptop"
    assert body["provenance"][0]["source"] == "docs/perf/propagation.json"
    assert any("recording" in n for n in body["notes"])


@pytest.mark.parametrize("content", [None, "{ not json"])
def test_a_missing_or_bad_recording_is_503(tmp_path, content):
    f = tmp_path / "propagation.json"
    if content is not None:
        f.write_text(content)
    r = _with_recording(f).get("/api/engine/propagation")
    assert r.status_code == 503
    assert r.json()["error"] == "propagation_unavailable"


def test_the_committed_recording_is_consistent_with_its_own_graph():
    import json

    d = json.loads(engine_mod.PROPAGATION.read_text())
    nodes = {n["name"]: n for n in d["graph"]["nodes"]}
    feeds: dict[str, set[str]] = {}
    for src, dst in d["graph"]["edges"]:
        feeds.setdefault(src, set()).add(dst)

    def downstream(names: set[str]) -> set[str]:
        seen, todo = set(names), list(names)
        while todo:
            for x in feeds.get(todo.pop(), ()):
                if x not in seen:
                    seen.add(x)
                    todo.append(x)
        return seen

    assert len(d["events"]) == 47
    for e in d["events"]:
        ran, changed = set(e["ran"]), set(e["changed"])
        assert ran <= set(nodes), e["id"]
        assert changed <= set(nodes), e["id"]
        inputs = {x for x in changed if nodes[x]["family"] == "input"}
        # Nothing ran that the event's own input cells cannot reach, and no cell ran.
        assert inputs, e["id"]
        assert ran <= downstream(inputs), e["id"]
        assert not any(nodes[x]["family"] == "input" for x in ran), e["id"]
    # A rise from the high-water mark cannot move drawdown, and a fall does: the page says which.
    assert "current_drawdown" not in next(e for e in d["events"] if e["id"] == "tick:SPY")["changed"]
    fall = next(e for e in d["events"] if e["id"] == "fall:SPY")
    assert "current_drawdown" in fall["changed"] and "limit:dd-cap" in fall["ran"]
    # The claim the page makes: one tick is a fraction of the graph.
    tick = next(e for e in d["events"] if e["id"] == "tick:SPY")
    assert 0 < len(tick["ran"]) < d["graph"]["counts"]["named"]
    rows = d["scaling"]
    assert [r["instruments"] for r in rows] == [10, 100, 400]
    assert all(r["incremental_ns"] < r["full_ns"] for r in rows)
