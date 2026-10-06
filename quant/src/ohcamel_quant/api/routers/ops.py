"""The host's telemetry, proxied from ``ohcamel-hostd`` (``native/hostd``).

``hostd`` is a small Rust daemon on the internal Docker network
(``http://ohcamel-hostd:9100``) that samples ``/proc`` and the three cgroup
slices of the compute plan's I.3 (``ohcamel-rt.slice``, ``ohcamel-web.slice``,
``ohcamel-batch.slice``) every few seconds. This router serves its snapshot at
``GET /api/ops/host`` so the site can show what the box is doing, and
``read_host()`` gives in-process callers -- the worker's admission control
(compute plan Lane B, B3) -- the same reading. It has the engine bridge's
shape (``engine.py``):

* **GET only, one fixed upstream path**: ``/v1/host``. Nothing about the
  request is forwarded.
* **Short timeouts** (connect 0.5 s, total 2 s) and a 2-second in-process
  cache, so many viewers cost hostd one read every two seconds.
* **503, never a stand-in.** Unset ``OHCAMEL_QUANT_HOSTD_URL``, a refused
  connection, a timeout, a non-200 answer or a body that is not a JSON object
  all return HTTP 503 ``{"error": "hostd_unavailable", "detail": ...,
  "configured": bool}``.

Wire shape (contract II.6 of docs/superpowers/plans/2026-09-24-quant-compute-
program.md), returned unchanged with ``provenance`` and ``notes`` added::

    {"version": 1, "interval_s": 5, "cpus": 2, "now_ms": epoch_ms,
     "latest": null | {"t_ms", "cpu", "steal", "iowait", "load1", "mem_total",
                       "mem_available", "swap_used",
                       "groups": {slice: null | {"cpu": cores | null, "mem": bytes}}},
     "history": [{"t_ms", "cpu", "steal", "mem_available",
                  "groups_cpu": {slice: cores | null}}]}

``cpu``, ``steal`` and ``iowait`` are fractions of all CPUs over the last
interval; a group's ``cpu`` is in cores; memory is in bytes. History holds up
to 720 samples (an hour at 5 s).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ... import kernels
from ...config import REPO_ROOT, get_settings
from ...data.base import Provenance
from ...kernels import bench as kernel_bench

router = APIRouter(prefix="/ops", tags=["ops"])

# The ONLY hostd path this module ever requests.
HOSTD_PATH = "/v1/host"
TIMEOUT = httpx.Timeout(2.0, connect=0.5)
CACHE_TTL_S = 2.0

_cache: dict[str, tuple[float, dict[str, Any], str]] = {}


class HostdUnavailable(Exception):
    """hostd could not be read; ``detail`` says why, for the 503 body."""

    def __init__(self, detail: str, *, configured: bool = True) -> None:
        super().__init__(detail)
        self.detail = detail
        self.configured = configured


def get_hostd_url() -> str | None:
    """The configured hostd base URL (``OHCAMEL_QUANT_HOSTD_URL``), or None.

    An empty string counts as unset, as for the engine bridge.
    """
    url = (get_settings().hostd_url or "").strip()
    return url.rstrip("/") or None


def _fetch(base: str | None) -> tuple[dict[str, Any], str]:
    """GET hostd's ``/v1/host``; returns (json, fetched_at ISO)."""
    if base is None:
        raise HostdUnavailable(
            "host telemetry is not configured on this server (OHCAMEL_QUANT_HOSTD_URL is unset)",
            configured=False,
        )
    now = time.monotonic()
    hit = _cache.get(base)
    if hit is not None and now - hit[0] < CACHE_TTL_S:
        return hit[1], hit[2]
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            resp = client.get(f"{base}{HOSTD_PATH}", headers={"Accept": "application/json"})
    except httpx.TimeoutException as e:
        raise HostdUnavailable(f"hostd did not answer {HOSTD_PATH} within 2 s ({type(e).__name__})") from e
    except httpx.HTTPError as e:
        raise HostdUnavailable(f"hostd is unreachable on {HOSTD_PATH}: {type(e).__name__}") from e
    if resp.status_code != 200:
        raise HostdUnavailable(f"hostd answered {HOSTD_PATH} with HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as e:
        raise HostdUnavailable(f"hostd's {HOSTD_PATH} answer is not JSON") from e
    if not isinstance(body, dict):
        raise HostdUnavailable(f"hostd's {HOSTD_PATH} answer is not a JSON object")
    fetched_at = Provenance.now("ohcamel-hostd").fetched_at
    _cache[base] = (now, body, fetched_at)
    return body, fetched_at


def _notes(body: dict[str, Any]) -> list[str]:
    notes = [
        "cpu, steal and iowait are fractions of all the host's CPUs over the last sampling "
        "interval; a slice's cpu is in cores; memory is in bytes.",
    ]
    latest = body.get("latest")
    if latest is None:
        notes.append("hostd has not completed its first sampling interval yet; latest is null.")
    elif isinstance(latest, dict):
        groups = latest.get("groups") or {}
        missing = sorted(name for name, g in groups.items() if g is None)
        if missing:
            notes.append(
                "These slices could not be read (no such cgroup, or no service placed in it): "
                + ", ".join(missing)
                + "."
            )
    return notes


def read_host(base: str | None = None) -> dict[str, Any]:
    """hostd's snapshot with ``provenance`` and ``notes``, for in-process callers.

    ``base`` defaults to the configured URL. Raises ``HostdUnavailable`` --
    the caller decides what an unreadable host means (admission control
    refuses to start a job it cannot budget).
    """
    if base is None:
        base = get_hostd_url()
    body, fetched_at = _fetch(base)
    provenance = Provenance(
        source="ohcamel-hostd",
        fetched_at=fetched_at,
        detail={"path": HOSTD_PATH, "read_only": True},
    ).to_dict()
    return {**body, "provenance": [provenance], "notes": _notes(body)}


@router.get("/host")
def host(base: str | None = Depends(get_hostd_url)) -> Any:
    """The host's CPU, steal, memory and per-slice use (hostd's ``/v1/host``).

    503 ``{"error": "hostd_unavailable", "detail", "configured"}`` when hostd
    is unset or unreachable.
    """
    if base is None:
        exc = HostdUnavailable(
            "host telemetry is not configured on this server (OHCAMEL_QUANT_HOSTD_URL is unset)",
            configured=False,
        )
    else:
        try:
            return read_host(base)
        except HostdUnavailable as e:
            exc = e
    return JSONResponse(
        status_code=503,
        content={"error": "hostd_unavailable", "detail": exc.detail, "configured": exc.configured},
    )


# ------------------------------------------------------------------ kernels
#: The committed benchmark table (compute plan gate GA; the Ship plan's Lane A
#: delta). quant/Dockerfile copies it to the same repo-relative path in the image.
KERNELS_TABLE = REPO_ROOT / "docs" / "perf" / "kernels.json"


def get_kernels_table_path() -> Path:
    return KERNELS_TABLE


@router.get("/kernels")
def kernels_table(path: Annotated[Path, Depends(get_kernels_table_path)]) -> Any:
    """The kernel benchmark table: Python reference vs Rust at one and two threads.

    ``{"kernels": [{name, python_ms, rust_1t_ms, rust_2t_ms, measured_on, sha}],
    "engine", "engines", "provenance", "notes"}``. 503
    ``{"error": "kernels_table_unavailable", "detail"}`` when the file is absent
    from this build or invalid.
    """
    try:
        rows = kernel_bench.validate_table(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return JSONResponse(status_code=503, content={
            "error": "kernels_table_unavailable", "detail": f"{path.name} is not present in this build"})
    except ValueError as e:  # json.JSONDecodeError is a ValueError
        return JSONResponse(status_code=503, content={
            "error": "kernels_table_unavailable", "detail": f"{path.name} is invalid: {e}"})
    notes = [
        "python_ms is the NumPy reference; rust_1t_ms and rust_2t_ms are ohcamel_kernels at one and two "
        "threads; rust_2t_ms is null for single-threaded kernels. Times are medians in milliseconds.",
        "measured_on names the machine; only rows measured on the droplet's CPU class speak for production.",
    ]
    if not rows:
        notes.append("no benchmark run is recorded yet")
    provenance = Provenance.now("docs/perf/kernels.json", rows=len(rows)).to_dict()
    return {
        "kernels": rows,
        "engine": kernels.ENGINE,
        "engines": {name: kernels.engine_of(name) for name in kernels.API},
        "provenance": [provenance],
        "notes": notes,
    }
