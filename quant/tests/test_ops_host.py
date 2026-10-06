"""The host telemetry proxy (api/routers/ops.py): GET /api/ops/host.

hostd is mocked with respx at the HTTP layer, with a body in contract II.6's
shape (docs/superpowers/plans/2026-09-24-quant-compute-program.md), so these
tests pin the proxy's own behaviour -- the 503s, the fixed upstream path, the
2-second cache and the provenance wrapping -- never hostd's numbers.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.routers import ops as ops_mod
from ohcamel_quant.config import Settings

HOSTD = "http://hostd.test:9100"

BODY = {
    "version": 1,
    "interval_s": 5,
    "cpus": 2,
    "now_ms": 1790000000000,
    "latest": {
        "t_ms": 1789999999000,
        "cpu": 0.43,
        "steal": 0.01,
        "iowait": 0.0,
        "load1": 0.8,
        "mem_total": 4105236480,
        "mem_available": 1530000000,
        "swap_used": 242221056,
        "groups": {
            "ohcamel-rt.slice": {"cpu": 0.02, "mem": 28000000},
            "ohcamel-batch.slice": None,
        },
    },
    "history": [
        {
            "t_ms": 1789999999000,
            "cpu": 0.43,
            "steal": 0.01,
            "mem_available": 1530000000,
            "groups_cpu": {"ohcamel-rt.slice": 0.02, "ohcamel-batch.slice": None},
        }
    ],
}


def _client(url: str | None) -> TestClient:
    app = create_app()
    app.dependency_overrides[ops_mod.get_hostd_url] = lambda: url
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache():
    ops_mod._cache.clear()
    yield
    ops_mod._cache.clear()


def test_unset_is_503_not_configured():
    r = _client(None).get("/api/ops/host")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "hostd_unavailable"
    assert body["configured"] is False
    assert "OHCAMEL_QUANT_HOSTD_URL" in body["detail"]


@respx.mock
def test_refused_connection_is_503_configured():
    respx.get(f"{HOSTD}/v1/host").mock(side_effect=httpx.ConnectError("refused"))
    r = _client(HOSTD).get("/api/ops/host")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "hostd_unavailable"
    assert body["configured"] is True
    assert "/v1/host" in body["detail"]


@respx.mock
@pytest.mark.parametrize(
    "response",
    [httpx.Response(500, json={}), httpx.Response(200, text="not json"), httpx.Response(200, json=[1, 2])],
)
def test_bad_upstream_answer_is_503(response):
    respx.get(f"{HOSTD}/v1/host").mock(return_value=response)
    r = _client(HOSTD).get("/api/ops/host")
    assert r.status_code == 503
    assert r.json()["configured"] is True


@respx.mock
def test_ok_wraps_the_body_with_provenance():
    route = respx.get(f"{HOSTD}/v1/host").mock(return_value=httpx.Response(200, json=BODY))
    r = _client(HOSTD).get("/api/ops/host")
    assert r.status_code == 200
    body = r.json()
    # hostd's body, unchanged, plus provenance and notes.
    assert body["latest"]["cpu"] == 0.43
    assert body["latest"]["groups"]["ohcamel-batch.slice"] is None
    assert body["history"] == BODY["history"]
    assert {k: body[k] for k in BODY} == BODY
    assert body["provenance"][0]["source"] == "ohcamel-hostd"
    assert body["provenance"][0]["detail"]["path"] == "/v1/host"
    assert isinstance(body["notes"], list) and body["notes"]
    # The upstream path is fixed: exactly /v1/host was requested.
    assert route.call_count == 1
    assert route.calls[0].request.url.path == "/v1/host"


@respx.mock
def test_a_null_latest_is_said_in_notes():
    respx.get(f"{HOSTD}/v1/host").mock(return_value=httpx.Response(200, json={**BODY, "latest": None, "history": []}))
    body = _client(HOSTD).get("/api/ops/host").json()
    assert body["latest"] is None
    assert any("first" in n for n in body["notes"])


def test_post_is_405():
    assert _client(HOSTD).post("/api/ops/host").status_code == 405


@respx.mock
def test_two_gets_within_two_seconds_cost_one_upstream_call():
    route = respx.get(f"{HOSTD}/v1/host").mock(return_value=httpx.Response(200, json=BODY))
    c = _client(HOSTD)
    assert c.get("/api/ops/host").status_code == 200
    assert c.get("/api/ops/host").status_code == 200
    assert route.call_count == 1


@respx.mock
def test_read_host_for_in_process_callers():
    respx.get(f"{HOSTD}/v1/host").mock(return_value=httpx.Response(200, json=BODY))
    got = ops_mod.read_host(HOSTD)
    assert got["latest"]["mem_available"] == 1530000000
    assert got["provenance"][0]["source"] == "ohcamel-hostd"
    with pytest.raises(ops_mod.HostdUnavailable) as e:
        ops_mod.read_host(None)
    assert e.value.configured is False


def test_blank_hostd_url_is_unset(monkeypatch):
    monkeypatch.setenv("OHCAMEL_QUANT_HOSTD_URL", "  ")
    assert Settings().hostd_url is None
    monkeypatch.setenv("OHCAMEL_QUANT_HOSTD_URL", "http://ohcamel-hostd:9100")
    assert Settings().hostd_url == "http://ohcamel-hostd:9100"
