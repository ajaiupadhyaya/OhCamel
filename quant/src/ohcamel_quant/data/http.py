"""Shared HTTP layer for every online data provider.

One pooled :class:`httpx.Client` per (User-Agent, timeout), with

* **retries** on HTTP 429, 5xx and transport errors (connect/read timeouts,
  resets), up to ``Settings.http_retries`` extra attempts, with *full-jitter*
  exponential backoff ``sleep = U(0, min(cap, base * 2**attempt))``
  (Brooker, 2015, "Exponential Backoff And Jitter", AWS Architecture Blog),
  honouring a numeric ``Retry-After`` header when the server sends one;
* a **per-host minimum interval** between request starts, so we stay inside
  each vendor's fair-access policy (SEC EDGAR allows at most 10 requests per
  second per client: we use 0.12 s; OpenFIGI without an API key allows 25
  requests per minute: 2.5 s; everything else 0.05 s);
* errors converted to :class:`FetchError` (a :class:`DataUnavailable`) whose
  message names the host, status and a short body excerpt, so the API can
  tell the user *why* a chart is empty.

Offline mode (``Settings.offline``) refuses every request: tests and CI must
never touch the network by accident.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from .base import DataUnavailable

log = logging.getLogger("ohcamel_quant.data.http")

#: Minimum seconds between request *starts* to the same host.
HOST_MIN_INTERVAL: dict[str, float] = {
    "www.sec.gov": 0.12,
    "data.sec.gov": 0.12,
    "efts.sec.gov": 0.12,
    "api.openfigi.com": 2.5,
}
DEFAULT_MIN_INTERVAL = 0.05
RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 520, 522, 524})
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 20.0
RETRY_AFTER_CAP_S = 30.0

# Indirection so tests can replace sleeping/clock without waiting.
_sleep = time.sleep
_monotonic = time.monotonic

_clients: dict[tuple[str, float], httpx.Client] = {}
_clients_lock = threading.Lock()
_host_lock = threading.Lock()
_host_next: dict[str, float] = {}


class FetchError(DataUnavailable):
    """A request failed after retries. ``status`` is the last HTTP status (or None)."""

    def __init__(self, message: str, status: int | None = None, url: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.url = url


def client(settings: Settings) -> httpx.Client:
    """The shared pooled client for these settings (created lazily, thread-safe)."""
    key = (settings.user_agent, float(settings.http_timeout_s))
    with _clients_lock:
        c = _clients.get(key)
        if c is None or c.is_closed:
            c = httpx.Client(
                headers={"User-Agent": settings.user_agent, "Accept-Encoding": "gzip, deflate"},
                timeout=httpx.Timeout(settings.http_timeout_s, connect=min(10.0, settings.http_timeout_s)),
                follow_redirects=True,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            _clients[key] = c
        return c


def close_all() -> None:
    """Close every pooled client (used at shutdown and by tests)."""
    with _clients_lock:
        for c in _clients.values():
            c.close()
        _clients.clear()


def reset_rate_limits() -> None:
    with _host_lock:
        _host_next.clear()


def _throttle(host: str, interval: float) -> None:
    """Block until ``interval`` seconds have passed since the previous request
    to ``host`` started (a reservation scheme, so concurrent threads queue)."""
    if interval <= 0:
        return
    with _host_lock:
        now = _monotonic()
        start = max(now, _host_next.get(host, 0.0))
        _host_next[host] = start + interval
    wait = start - now
    if wait > 0:
        _sleep(wait)


def backoff_delay(attempt: int, retry_after: str | None = None) -> float:
    """Seconds to wait before retry ``attempt`` (0-based): a numeric
    ``Retry-After`` (capped) wins; otherwise full jitter
    ``U(0, min(cap, base * 2**attempt))``."""
    if retry_after:
        try:
            return min(RETRY_AFTER_CAP_S, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return random.uniform(0.0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2**attempt)))


def _excerpt(resp: httpx.Response, n: int = 160) -> str:
    try:
        text = resp.text
    except Exception:  # noqa: BLE001 - undecodable body
        return ""
    text = " ".join(text.split())
    return text[:n]


def request(
    method: str,
    url: str,
    settings: Settings,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json: Any = None,
    min_interval: float | None = None,
    retries: int | None = None,
    source: str | None = None,
) -> httpx.Response:
    """Perform one HTTP request with throttling and retries; return a 2xx response.

    Raises :class:`FetchError` (a ``DataUnavailable``) on a non-retryable
    status (e.g. 403/404), or when retries are exhausted.
    """
    if settings.offline:
        raise FetchError(f"offline mode: network disabled ({url})", url=url)
    host = urlsplit(url).netloc
    label = source or host
    interval = HOST_MIN_INTERVAL.get(host, DEFAULT_MIN_INTERVAL) if min_interval is None else min_interval
    n_retries = settings.http_retries if retries is None else retries
    c = client(settings)
    last_err = "unknown error"
    last_status: int | None = None
    for attempt in range(n_retries + 1):
        _throttle(host, interval)
        try:
            resp = c.request(method, url, params=params, headers=headers, json=json)
        except httpx.TransportError as e:  # connect/read timeouts, resets, DNS
            last_err, last_status = f"{type(e).__name__}: {e}", None
            if attempt < n_retries:
                d = backoff_delay(attempt)
                log.info("%s: %s; retry %d in %.2fs", label, last_err, attempt + 1, d)
                _sleep(d)
                continue
            break
        if resp.status_code < 400:
            return resp
        last_status = resp.status_code
        last_err = f"HTTP {resp.status_code}" + (f": {_excerpt(resp)}" if _excerpt(resp) else "")
        if resp.status_code in RETRY_STATUS and attempt < n_retries:
            d = backoff_delay(attempt, resp.headers.get("Retry-After"))
            log.info("%s: %s; retry %d in %.2fs", label, last_err, attempt + 1, d)
            _sleep(d)
            continue
        raise FetchError(f"{label}: {last_err}", status=last_status, url=url)
    raise FetchError(
        f"{label}: {last_err} (after {n_retries + 1} attempts)", status=last_status, url=url
    )


def get(url: str, settings: Settings, **kw: Any) -> httpx.Response:
    return request("GET", url, settings, **kw)


def get_json(url: str, settings: Settings, **kw: Any) -> Any:
    resp = get(url, settings, **kw)
    try:
        return resp.json()
    except ValueError as e:
        raise FetchError(
            f"{kw.get('source') or urlsplit(url).netloc}: response is not JSON ({_excerpt(resp, 80)})",
            status=resp.status_code, url=url,
        ) from e


def get_text(url: str, settings: Settings, **kw: Any) -> str:
    return get(url, settings, **kw).text


def get_bytes(url: str, settings: Settings, **kw: Any) -> bytes:
    return get(url, settings, **kw).content


def post_json(url: str, settings: Settings, payload: Any, **kw: Any) -> Any:
    resp = request("POST", url, settings, json=payload, **kw)
    try:
        return resp.json()
    except ValueError as e:
        raise FetchError(f"{urlsplit(url).netloc}: response is not JSON", url=url) from e
