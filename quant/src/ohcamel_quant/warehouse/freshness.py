"""When is warehouse data current? Session datasets are judged against the
New York session clock; exchange holidays are not known, so a session counts as
covered when the last successful ingest ran after it was due and found nothing
newer (one session of slack, never more)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
#: A session's daily bars are expected from vendors by 16:20 New York
#: (16:00 close + Alpaca's 16-minute SIP delay + slack).
VENDOR_DUE = time(16, 20)


def _now() -> datetime:
    """Current UTC time (indirection so tests can pin the clock)."""
    return datetime.now(UTC)


def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def expected_session(now: datetime, due: time) -> date:
    """The latest weekday whose ``due`` New York time has passed at ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ny = now.astimezone(NY)
    d = ny.date()
    if d.weekday() >= 5 or ny.time() < due:
        d = _prev_weekday(d)  # Sat/Sun -> Fri; a weekday before `due` -> the previous weekday
    return d


def is_fresh(data_asof: date | None, last_ok_ran_at: datetime | None, now: datetime, due: time) -> bool:
    """``data_asof`` reaches the expected session, or it is exactly one session
    behind and the last successful ingest ran after that session was due."""
    if data_asof is None:
        return False
    exp = expected_session(now, due)
    if data_asof >= exp:
        return True
    if last_ok_ran_at is None or data_asof < _prev_weekday(exp):
        return False
    ran = last_ok_ran_at if last_ok_ran_at.tzinfo else last_ok_ran_at.replace(tzinfo=UTC)
    return ran >= datetime.combine(exp, due, NY)
