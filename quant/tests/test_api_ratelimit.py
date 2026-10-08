"""Harden H4: a per-client token bucket in front of the expensive synchronous POST endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ohcamel_quant.api.ratelimit import EXEMPT_POSTS, RateLimit, TokenBuckets

IP_A = {"X-Forwarded-For": "203.0.113.7"}
IP_B = {"X-Forwarded-For": "203.0.113.8"}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _app(per_min=60.0, burst=3, clock=None):
    app = FastAPI()

    @app.post("/api/portfolio/optimize")
    def optimize() -> dict:
        return {"ok": True}

    @app.post("/api/jobs")
    def jobs() -> dict:
        return {"ok": True}

    @app.post("/api/deck/reading")
    def reading() -> dict:
        return {"ok": True}

    @app.get("/api/market/overview")
    def overview() -> dict:
        return {"ok": True}

    app.add_middleware(RateLimit, per_min=per_min, burst=burst, clock=clock or Clock())
    return app


def test_bucket_takes_refills_and_caps():
    c = Clock()
    b = TokenBuckets(per_min=60.0, burst=2, clock=c)
    assert b.take("a") == 0.0 and b.take("a") == 0.0
    wait = b.take("a")
    assert wait == pytest.approx(1.0)  # 60/min -> one token a second
    c.t += 0.5
    assert b.take("a") == pytest.approx(0.5)
    c.t += 0.5
    assert b.take("a") == 0.0
    c.t += 3600
    assert b.take("a") == 0.0 and b.take("a") == 0.0 and b.take("a") > 0  # never more than the burst
    assert b.take("b") == 0.0  # per client


def test_bucket_forgets_idle_clients():
    c = Clock()
    b = TokenBuckets(per_min=60.0, burst=2, clock=c, max_clients=3)
    for k in "abc":
        b.take(k)
    c.t += 10  # every bucket full again: forgettable
    b.take("d")
    assert len(b.buckets) <= 3 and "d" in b.buckets


def test_expensive_posts_get_429_with_retry_after_beyond_the_burst():
    c = Clock()
    client = TestClient(_app(per_min=30.0, burst=3, clock=c))
    for _ in range(3):
        assert client.post("/api/portfolio/optimize", headers=IP_A).status_code == 200
    r = client.post("/api/portfolio/optimize", headers=IP_A)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    assert r.headers["retry-after"] == "2"  # 30/min: one token every 2 s
    assert client.post("/api/portfolio/optimize", headers=IP_B).status_code == 200  # another client
    c.t += 2.0
    assert client.post("/api/portfolio/optimize", headers=IP_A).status_code == 200


def test_spoofed_forwarded_entries_share_the_last_hops_bucket():
    client = TestClient(_app(burst=2))
    for i in range(2):
        assert client.post("/api/portfolio/optimize",
                           headers={"X-Forwarded-For": f"10.9.{i}.1, 203.0.113.7"}).status_code == 200
    r = client.post("/api/portfolio/optimize", headers={"X-Forwarded-For": "10.9.9.9, 203.0.113.7"})
    assert r.status_code == 429


def test_gets_exempt_posts_and_loopback_are_not_limited():
    client = TestClient(_app(burst=1))
    for _ in range(5):
        assert client.get("/api/market/overview", headers=IP_A).status_code == 200
        assert client.post("/api/jobs", headers=IP_A).status_code == 200          # its own per-client cap
        assert client.post("/api/deck/reading", headers=IP_A).status_code == 200  # the deck's poll
    assert {"/api/jobs", "/api/deck/reading", "/api/options/price", "/api/macro/bond"} <= EXEMPT_POSTS
    loop = TestClient(_app(burst=1), client=("127.0.0.1", 5000))  # vite dev / local scripts: no proxy, no limit
    for _ in range(5):
        assert loop.post("/api/portfolio/optimize").status_code == 200
    proxied = TestClient(_app(burst=1), client=("127.0.0.1", 5000))  # through a proxy: the forwarded client counts
    assert proxied.post("/api/portfolio/optimize", headers=IP_A).status_code == 200
    assert proxied.post("/api/portfolio/optimize", headers=IP_A).status_code == 429


def test_zero_rate_disables_the_limit():
    client = TestClient(_app(per_min=0, burst=1))
    for _ in range(5):
        assert client.post("/api/portfolio/optimize", headers=IP_A).status_code == 200


def test_the_app_mounts_the_limit_from_settings(monkeypatch):
    from ohcamel_quant.api.app import create_app
    from ohcamel_quant.config import get_settings

    monkeypatch.setenv("OHCAMEL_QUANT_RATE_LIMIT_PER_MIN", "30")
    monkeypatch.setenv("OHCAMEL_QUANT_RATE_LIMIT_BURST", "2")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        codes = [client.post("/api/portfolio/optimize", json={}, headers=IP_A).status_code for _ in range(3)]
        assert codes[:2] == [422, 422] and codes[2] == 429  # the bucket is spent before validation runs
        assert client.post("/api/options/price", json={}, headers=IP_A).status_code != 429
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_settings_defaults():
    from ohcamel_quant.config import Settings

    s = Settings(_env_file=None, rate_limit_per_min=30.0, rate_limit_burst=30)
    assert s.rate_limit_per_min == 30.0 and s.rate_limit_burst == 30
    fields = Settings.model_fields
    assert fields["rate_limit_per_min"].default == 30.0 and fields["rate_limit_burst"].default == 30
