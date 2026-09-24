"""CUSIP -> exchange ticker mapping via the OpenFIGI API (Bloomberg).

``POST https://api.openfigi.com/v3/mapping`` with a JSON array of jobs
``{"idType": "ID_CUSIP", "idValue": "037833100"}``; the response is one
element per job: ``{"data": [{"figi", "ticker", "exchCode", "marketSector",
"securityType", ...}]}``, ``{"warning": "No identifier found."}`` or
``{"error": ...}``.

Limits (documented at https://www.openfigi.com/api): without an API key, 10
jobs per request and 25 requests per minute; with ``X-OPENFIGI-APIKEY`` (read
from the ``OPENFIGI_API_KEY`` environment variable), 100 jobs per request and
25 requests per 6 seconds. Mappings are cached permanently (a CUSIP's ticker
rarely changes; a definitive "not found" is cached as ``None`` too, transient
errors are not). A per-call time budget keeps API requests responsive: CUSIPs
not reached stay unmapped this time and are filled on a later call.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from ..config import Settings
from . import http
from .base import DataUnavailable, Provenance
from .store import get_store

log = logging.getLogger("ohcamel_quant.data.openfigi")

URL = "https://api.openfigi.com/v3/mapping"
FAMILY = "openfigi"
KEY = "cusip_map"
_lock = threading.Lock()


def _api_key() -> str | None:
    return os.environ.get("OPENFIGI_API_KEY") or None


def normalize_ticker(t: str) -> str:
    """OpenFIGI writes share classes with a slash (``BRK/B``); we use a dot."""
    return t.strip().upper().replace("/", ".").replace(" ", "")


def pick_ticker(data: list[dict[str, Any]]) -> str | None:
    """Prefer the US composite listing (``exchCode == 'US'``) of an equity."""
    if not data:
        return None

    def score(d: dict[str, Any]) -> tuple[int, int]:
        return (0 if d.get("exchCode") == "US" else 1, 0 if d.get("marketSector") == "Equity" else 1)

    for d in sorted(data, key=score):
        if d.get("ticker"):
            return normalize_ticker(str(d["ticker"]))
    return None


def parse_mapping_response(cusips: list[str], payload: list[Any]) -> dict[str, str | None]:
    """Response array -> {cusip: ticker | None}; jobs that errored are omitted
    (so they are retried later), definitive 'not found' map to None."""
    out: dict[str, str | None] = {}
    for cusip, res in zip(cusips, payload or [], strict=False):
        if not isinstance(res, dict):
            continue
        if "data" in res:
            out[cusip] = pick_ticker(res["data"])
        elif "warning" in res:
            out[cusip] = None
    return out


def cached_map(settings: Settings) -> dict[str, str | None]:
    hit = get_store(settings).get(FAMILY, KEY, None)
    return dict(hit[0]) if hit else {}


def map_cusips(cusips: list[str], settings: Settings, budget_s: float = 20.0) -> dict[str, str | None]:
    """{cusip: ticker|None} for every CUSIP already cached or mapped within
    ``budget_s`` seconds. CUSIPs missing from the result were not attempted."""
    wanted = list(dict.fromkeys(c.strip().upper() for c in cusips if c and c.strip()))
    known = cached_map(settings)
    todo = [c for c in wanted if c not in known]
    if not todo or settings.offline:
        return {c: known[c] for c in wanted if c in known}
    key = _api_key()
    batch = 100 if key else 10
    headers = {"Content-Type": "application/json"}
    min_interval = None
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
        min_interval = 0.25
    t0 = time.monotonic()
    fresh: dict[str, str | None] = {}
    for i in range(0, len(todo), batch):
        if time.monotonic() - t0 > budget_s:
            log.info("openfigi: budget exhausted, %d CUSIPs left for later", len(todo) - i)
            break
        chunk = todo[i:i + batch]
        jobs = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
        try:
            payload = http.post_json(URL, settings, jobs, headers=headers, min_interval=min_interval,
                                     source="openfigi", retries=2)
        except DataUnavailable as e:
            log.warning("openfigi: %s", e)
            break
        fresh.update(parse_mapping_response(chunk, payload))
    if fresh:
        with _lock:
            merged = {**cached_map(settings), **fresh}
            get_store(settings).put(FAMILY, KEY, merged, Provenance.now("openfigi", url=URL, n=len(merged)))
        known.update(fresh)
    return {c: known[c] for c in wanted if c in known}
