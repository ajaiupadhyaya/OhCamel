"""Read-only bridge to the OCaml real-time risk engine (``lib/server.ml``).

The engine is the repository's original project: an Incremental dependency
graph that recomputes a book's exposure, VaR/ES and limits on every tick. On
the droplet it runs as ``ohcamel-live`` on the internal Docker network
(``http://ohcamel-live:8081``), behind a password on its own host. This router
lets the public Quant app show what it is doing without a second origin, CORS,
or the password -- and it is deliberately narrow:

* **GET only, and only three engine paths**: ``/api/health``, ``/api/ops``
  (best effort, for the build/mode line), ``/api/snapshot`` and
  ``/api/history``. The path is never taken from the request, so no desk route
  (``/api/desk/orders``, ``/api/desk/cancel``, ``/api/desk/kill`` ...) and no
  other engine route can be reached through here, whatever a caller sends.
* **Short timeouts** (connect 1.5 s, total 4 s) and a 2-second in-process
  cache per path, so many public viewers cost the engine one read per path
  every two seconds, and a wedged engine costs this app four seconds at most.
* **503, never a stand-in.** Unset ``OHCAMEL_QUANT_ENGINE_URL``, a refused
  connection, a timeout, a non-200 answer or a body that is not JSON all
  return HTTP 503 ``{"error": "engine_unavailable", "detail": ..., "configured":
  bool, "reachable": false}``. Nothing is ever synthesised in its place.

Engine wire shapes (from ``lib/server.ml``; all money in USD as plain floats,
``null`` where a number does not exist yet, e.g. while the covariance window
warms up -- never a zero standing in for it):

``GET /api/health`` -> ``json_of_feed_health``::

    {"healthy": bool, "stale": [symbol], "never_seen": [symbol],
     "symbols": [{"symbol", "last_tick": ISO-UTC | null, "never_seen": bool,
                  "stale": bool}]}

``GET /api/ops`` (subset used here): ``mode`` ("demo" | "live"), ``uptime_s``,
``build`` {``git_sha``, ``built_at``}, ``feed`` {``healthy``, ``symbols``,
``stale``, ``never_seen``, ...}, ``stream`` {``frames_sent``,
``subscribers``, ...}. It deliberately carries no symbol names.

``GET /api/snapshot`` -> ``json_of_snapshot``::

    {"as_of": ISO-UTC, "factor": str,
     "positions": [{"symbol", "sector", "exposure", "weight", "component_var",
                    "price", "qty", "marginal", "standalone", "risk_share",
                    "risk_over_money"}],
     "sectors": [{"sector", "exposure", "component_var", "risk_share"}],
     "gross_exposure", "net_exposure", "equity", "current_drawdown",
     "historical_var", "expected_shortfall", "parametric_var",
     "parametric_var_ewma", "ewma_lambda", "value_at_risk_notional",
     "expected_shortfall_notional", "portfolio_beta", "portfolio_gamma",
     "portfolio_vega", "vega_by_bucket": {bucket: vega},
     "diversification_ratio", "euler_residual", "attribution_covariance",
     "by_node": {node_name: value}, "quiet": [symbol], "warming_up": bool,
     "feed": <health object above>, "limits": [{"name", "scope", "unit",
     "observed", "threshold", "excess", "breached", "utilisation"}],
     "unevaluated": [name], "nodes_recomputed": int, "stabilizes": int,
     "recomputed": null, "changed": null, "stabilizes_delta": null,
     "nodes_recomputed_delta": null, ...alerts/desk fields appended by the
     server}

  VaR/ES fields are at the engine's configured confidence (``Config``'s
  default 0.95; the book can change it), in return space (``historical_var``,
  ``parametric_var``...) and in dollars (``*_notional``); vega is per 1.00 of
  annualised vol (the engine's unit, not per vol point). ``nodes_recomputed``
  rising between two reads is the engine's evidence that the graph is live.

``GET /api/history`` -> ``json_of_history`` (column-major, in-memory, lost on
engine restart)::

    {"points": int, "capacity": int, "appended": int,
     "time": [epoch_ms], "gross": [...], "net": [...], "equity": [...],
     "drawdown": [...], "var_notional": [float|null], "es_notional": [...]}

Payloads returned here wrap the engine's JSON unchanged (``snapshot`` /
``history`` / ``health``) and add ``provenance`` and ``notes``.

Reference: Minsky, Y. et al. (2015), *Incremental: a library for incremental
computations* (Jane Street) -- the self-adjusting computation model the engine
is built on (Acar, U. A. (2005), *Self-Adjusting Computation*, PhD thesis, CMU).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...config import get_settings
from ...data.base import Provenance

router = APIRouter(prefix="/engine", tags=["engine"])

# The ONLY engine paths this module ever requests. Fixed strings, never built
# from request input -- that is what makes the bridge read-only by construction.
ENGINE_PATHS: dict[str, str] = {
    "health": "/api/health",
    "ops": "/api/ops",
    "snapshot": "/api/snapshot",
    "history": "/api/history",
}

TIMEOUT = httpx.Timeout(4.0, connect=1.5)
CACHE_TTL_S = 2.0

_cache: dict[tuple[str, str], tuple[float, Any, str]] = {}


class EngineUnavailable(Exception):
    """The engine could not be read; ``detail`` says why, for the 503 body."""

    def __init__(self, detail: str, *, configured: bool = True) -> None:
        super().__init__(detail)
        self.detail = detail
        self.configured = configured


def get_engine_url() -> str | None:
    """The configured engine base URL (``OHCAMEL_QUANT_ENGINE_URL``), or None.

    An empty string counts as unset, so the deployment can switch the bridge
    off with ``OHCAMEL_QUANT_ENGINE_URL=`` without removing the variable.
    """
    url = (get_settings().engine_url or "").strip()
    return url.rstrip("/") or None


def _unavailable(exc: EngineUnavailable) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "engine_unavailable",
            "detail": exc.detail,
            "configured": exc.configured,
            "reachable": False,
        },
    )


async def _fetch(base: str | None, key: str) -> tuple[Any, str]:
    """GET one allow-listed engine path; returns (json, fetched_at ISO)."""
    if base is None:
        raise EngineUnavailable(
            "the OCaml engine bridge is not configured on this server "
            "(OHCAMEL_QUANT_ENGINE_URL is unset)",
            configured=False,
        )
    path = ENGINE_PATHS[key]
    now = time.monotonic()
    hit = _cache.get((base, key))
    if hit is not None and now - hit[0] < CACHE_TTL_S:
        return hit[1], hit[2]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(f"{base}{path}", headers={"Accept": "application/json"})
    except httpx.TimeoutException as e:
        raise EngineUnavailable(f"the engine did not answer {path} within 4 s ({type(e).__name__})") from e
    except httpx.HTTPError as e:
        raise EngineUnavailable(f"the engine is unreachable on {path}: {type(e).__name__}") from e
    if resp.status_code != 200:
        raise EngineUnavailable(f"the engine answered {path} with HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as e:
        raise EngineUnavailable(f"the engine's {path} answer is not JSON") from e
    fetched_at = Provenance.now("ohcamel-engine").fetched_at
    _cache[(base, key)] = (now, body, fetched_at)
    return body, fetched_at


def _provenance(path: str, fetched_at: str, mode: str | None = None) -> dict[str, Any]:
    detail: dict[str, Any] = {"path": path, "read_only": True}
    if mode:
        detail["mode"] = mode
    return Provenance(source="ohcamel-engine", fetched_at=fetched_at, detail=detail).to_dict()


def _mode_notes(mode: str | None) -> list[str]:
    if mode == "live":
        return [
            "Live mode: marks from Alpaca's real-time feed (IEX unless the engine is configured "
            "for SIP) and rates from FRED; outside US market hours marks do not move.",
        ]
    if mode == "demo":
        return [
            "This engine is running in DEMO mode on a synthetic feed; its numbers are not "
            "market data.",
        ]
    return []


@router.get("/status")
async def status(base: str | None = Depends(get_engine_url)) -> Any:
    """Is the engine reachable, and how healthy is its feed?

    Returns ``{"configured", "reachable", "latency_ms", "health", "ops",
    "provenance", "notes"}`` where ``health`` is the engine's ``/api/health``
    body and ``ops`` a subset of ``/api/ops`` (mode, uptime, build, feed and
    stream counters) or ``null`` if that read failed. 503 when unset or
    unreachable.
    """
    t0 = time.perf_counter()
    try:
        health, fetched_at = await _fetch(base, "health")
    except EngineUnavailable as e:
        return _unavailable(e)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    ops: dict[str, Any] | None = None
    try:
        raw_ops, _ = await _fetch(base, "ops")
        if isinstance(raw_ops, dict):
            ops = {k: raw_ops.get(k) for k in ("mode", "uptime_s", "build", "feed", "stream")}
    except EngineUnavailable:
        ops = None
    mode = ops.get("mode") if ops else None
    notes = _mode_notes(mode)
    if ops is None:
        notes.append("The engine's /api/ops could not be read; mode and build are unknown.")
    if isinstance(health, dict) and health.get("healthy") is False:
        notes.append("The engine reports stale or never-seen symbols (see health.stale).")
    return {
        "configured": True,
        "reachable": True,
        "latency_ms": round(latency_ms, 1),
        "health": health,
        "ops": ops,
        "provenance": [_provenance(ENGINE_PATHS["health"], fetched_at, mode)],
        "notes": notes,
    }


@router.get("/snapshot")
async def snapshot(base: str | None = Depends(get_engine_url)) -> Any:
    """The engine's whole book as of now (its ``/api/snapshot``, unchanged).

    Returns ``{"snapshot": <engine JSON>, "provenance", "notes"}``; 503 when
    the engine is unset or unreachable.
    """
    try:
        body, fetched_at = await _fetch(base, "snapshot")
    except EngineUnavailable as e:
        return _unavailable(e)
    notes: list[str] = []
    if isinstance(body, dict) and body.get("warming_up"):
        notes.append(
            "The engine is warming up: VaR/ES and attribution are null until its return "
            "window fills."
        )
    notes.append(
        "VaR and ES are computed by the engine at its configured confidence (95% by default); "
        "vega is per 1.00 of annualised volatility."
    )
    return {
        "snapshot": body,
        "provenance": [_provenance(ENGINE_PATHS["snapshot"], fetched_at)],
        "notes": notes,
    }


@router.get("/history")
async def history(base: str | None = Depends(get_engine_url)) -> Any:
    """The engine's in-memory trail (its ``/api/history``, unchanged).

    Returns ``{"history": <engine JSON>, "provenance", "notes"}``; 503 when the
    engine is unset or unreachable.
    """
    try:
        body, fetched_at = await _fetch(base, "history")
    except EngineUnavailable as e:
        return _unavailable(e)
    notes = [
        "The engine keeps this trail in memory only: it starts empty at every engine restart "
        "and holds at most `capacity` points."
    ]
    return {
        "history": body,
        "provenance": [_provenance(ENGINE_PATHS["history"], fetched_at)],
        "notes": notes,
    }
