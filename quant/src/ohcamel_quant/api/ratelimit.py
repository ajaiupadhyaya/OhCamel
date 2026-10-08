"""A per-client token bucket in front of the expensive synchronous POST endpoints (Harden H4).

Every ``POST /api/...`` takes one token from its client's bucket before
anything else runs (validation included); an empty bucket answers 429
``{"error": "rate_limited"}`` with ``Retry-After`` in whole seconds. A bucket
holds ``burst`` tokens and refills at ``per_min`` a minute; ``per_min`` 0
turns the limit off. The client is the one /api/jobs counts
(``routers.jobs.client_id``: the last X-Forwarded-For hop, which Caddy
appends, else the socket peer).

Not limited: GETs; the POSTs in ``EXEMPT_POSTS`` (``/api/jobs`` has its own
per-client queue cap, the deck polls ``/api/deck/reading``, and the option and
bond calculators are closed forms); and a loopback peer that came through no
proxy (vite dev and local scripts -- in compose every request arrives from
Caddy with X-Forwarded-For set).

A pure ASGI middleware, so the SSE stream passes through untouched. State is
per process: the API runs one uvicorn worker.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

EXEMPT_POSTS = frozenset({"/api/jobs", "/api/deck/reading", "/api/options/price", "/api/options/strategy",
                          "/api/macro/bond"})
LOOPBACK = frozenset({"127.0.0.1", "::1"})


class TokenBuckets:
    """``take(key)`` -> 0.0 when a token was taken, else the seconds until one is available."""

    def __init__(self, *, per_min: float, burst: int, clock: Callable[[], float] = time.monotonic,
                 max_clients: int = 10_000) -> None:
        self.rate = per_min / 60.0
        self.burst = float(max(1, burst))
        self.clock = clock
        self.max_clients = max_clients
        self.buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, at)
        self._lock = threading.Lock()

    def _level(self, tokens: float, at: float, now: float) -> float:
        return min(self.burst, tokens + (now - at) * self.rate)

    def take(self, key: str) -> float:
        now = self.clock()
        with self._lock:
            tokens, at = self.buckets.get(key, (self.burst, now))
            tokens = self._level(tokens, at, now)
            if tokens >= 1.0:
                self.buckets[key] = (tokens - 1.0, now)
                if len(self.buckets) > self.max_clients:
                    self._forget(now)
                return 0.0
            self.buckets[key] = (tokens, now)
            return (1.0 - tokens) / self.rate

    def _forget(self, now: float) -> None:
        """Drop the buckets that have refilled to full: forgetting them changes nothing."""
        for k in [k for k, (t, at) in self.buckets.items() if self._level(t, at, now) >= self.burst]:
            del self.buckets[k]


class RateLimit:
    def __init__(self, app: ASGIApp, *, per_min: float, burst: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.app = app
        self.buckets = TokenBuckets(per_min=per_min, burst=burst, clock=clock) if per_min > 0 else None

    def _limited(self, scope: Scope) -> bool:
        path = scope.get("path", "")
        return (self.buckets is not None and scope["type"] == "http" and scope.get("method") == "POST"
                and path.startswith("/api/") and path.rstrip("/") not in EXEMPT_POSTS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._limited(scope):
            await self.app(scope, receive, send)
            return
        from .routers.jobs import client_id

        request = Request(scope)
        peer = request.client.host if request.client else ""
        if peer in LOOPBACK and not request.headers.getlist("x-forwarded-for"):
            await self.app(scope, receive, send)
            return
        assert self.buckets is not None
        wait = self.buckets.take(client_id(request))
        if wait <= 0.0:
            await self.app(scope, receive, send)
            return
        await _too_many(send, max(1, math.ceil(wait - 1e-9)))


async def _too_many(send: Send, retry_after: int) -> None:
    body: dict[str, Any] = {"error": "rate_limited",
                            "detail": f"too many requests from this address; retry in {retry_after} s"}
    raw = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": 429,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
                            (b"retry-after", str(retry_after).encode())]})
    await send({"type": "http.response.body", "body": raw})
