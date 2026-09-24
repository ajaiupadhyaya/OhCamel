"""Is the US equity session open, and when does it next open or close?

Alpaca's ``/v2/clock`` answers exactly, holidays and early closes included,
when data keys are configured. Without them -- offline, CI, a keyless host --
the answer is the regular session read off New York wall clock
(09:30-16:00, Monday to Friday), and the result says it cannot know holidays.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings

log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
OPEN, CLOSE = dtime(9, 30), dtime(16, 0)
ALPACA_CLOCK = "https://paper-api.alpaca.markets/v2/clock"
CACHE_S = 30.0
RULE_NOTE = ("session from New York wall clock (09:30-16:00, Mon-Fri); exchange holidays and "
             "early closes are not known without Alpaca's clock")


@dataclass(frozen=True)
class Clock:
    is_open: bool
    now: str               # ISO-8601 UTC
    next_open: str         # ISO-8601 UTC
    next_close: str        # ISO-8601 UTC
    session: str           # the New York date of the current or most recent session, YYYY-MM-DD
    source: str            # "alpaca" | "rule"
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _next_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def rule_clock(now: datetime) -> Clock:
    """The regular session only, from New York wall clock."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ny = now.astimezone(NY)
    d = ny.date()
    weekday = d.weekday() < 5
    open_today = datetime.combine(d, OPEN, NY)
    close_today = datetime.combine(d, CLOSE, NY)
    is_open = weekday and open_today <= ny < close_today

    if is_open:
        nxt_open = datetime.combine(_next_weekday(d), OPEN, NY)
        nxt_close = close_today
    elif weekday and ny < open_today:
        nxt_open, nxt_close = open_today, close_today
    else:
        nd = _next_weekday(d)
        nxt_open, nxt_close = datetime.combine(nd, OPEN, NY), datetime.combine(nd, CLOSE, NY)

    # The session a reading belongs to: today once it has opened, else the last weekday.
    if weekday and ny >= open_today:
        session = d
    else:
        session = d - timedelta(days=1)
        while session.weekday() >= 5:
            session -= timedelta(days=1)
    return Clock(is_open, _iso(now), _iso(nxt_open), _iso(nxt_close), session.isoformat(), "rule", RULE_NOTE)


def parse_alpaca_clock(body: dict[str, Any], now: datetime) -> Clock:
    """``{"timestamp", "is_open", "next_open", "next_close"}`` -> Clock."""
    nxt_open = datetime.fromisoformat(body["next_open"])
    nxt_close = datetime.fromisoformat(body["next_close"])
    is_open = bool(body["is_open"])
    ts = datetime.fromisoformat(body["timestamp"]) if body.get("timestamp") else now
    ny = ts.astimezone(NY)
    if is_open:
        session = ny.date()
    else:
        # Closed: the most recent session is the weekday before next_open's
        # day, or today if today's session has already run.
        session = ny.date() if ny.weekday() < 5 and ny.time() >= CLOSE else ny.date() - timedelta(days=1)
        while session.weekday() >= 5:
            session -= timedelta(days=1)
    return Clock(is_open, _iso(ts), _iso(nxt_open), _iso(nxt_close), session.isoformat(), "alpaca")


_lock = threading.Lock()
_cached: tuple[float, Clock] | None = None


def session_clock(settings: Settings, now: datetime | None = None) -> Clock:
    """Alpaca's clock when keys exist and it answers (cached 30 s), else the rule."""
    global _cached
    now = now or datetime.now(UTC)
    if settings.offline or not (settings.alpaca_key_id and settings.alpaca_secret_key):
        return rule_clock(now)
    with _lock:
        if _cached is not None and time.monotonic() - _cached[0] < CACHE_S:
            return _cached[1]
    from ..data import http
    from ..data.prices import _alpaca_headers

    try:
        body = http.get_json(ALPACA_CLOCK, settings, headers=_alpaca_headers(settings), source="alpaca")
        clk = parse_alpaca_clock(body, now)
    except Exception as e:  # noqa: BLE001 - any failure falls back to the stated rule
        log.info("alpaca clock failed, using the wall-clock rule: %s", e)
        rule = rule_clock(now)
        return Clock(**{**rule.to_dict(), "note": f"Alpaca's clock did not answer ({type(e).__name__}); {RULE_NOTE}"})
    with _lock:
        _cached = (time.monotonic(), clk)
    return clk
