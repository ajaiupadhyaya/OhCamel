"""The exchange calendar (completion task 3.6): Alpaca's ``GET /v2/calendar``, cached in ``exchange_calendar``.

Alpaca's trading API answers, for a date range, one row per session -- ``{"date": "2026-11-27", "open": "09:30",
"close": "13:00", ...}`` -- holidays left out and early closes stated. It is the same source as the desk's
``/v2/clock`` (deck/clock.py) and needs the same keys (``APCA_API_KEY_ID``/``APCA_API_SECRET_KEY``, mapped from
the droplet's ``ALPACA_*``). Unscheduled closures are in it once Alpaca publishes them, which the rule-based
holiday list in signals/calendar.py cannot know.

* :func:`parse_alpaca_calendar` turns a response into rows and refuses one that is empty, names a weekend, a
  duplicate date or a close not after its open.
* :func:`write_calendar` replaces the rows of the range it was asked for (a closure added later removes a day).
* :func:`load_calendar` is the reader freshness and ``ops.data_integrity`` use: ``None`` when the table is
  missing (a warehouse before migration 005) or empty -- the caller then says it is counting weekdays, never
  silently. A date outside ``[first, last]`` is not covered: the calendar says nothing about it.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from ..data.base import DataUnavailable

ALPACA_CALENDAR = "https://paper-api.alpaca.markets/v2/calendar"
DATASET = "exchange_calendar"


def _hhmm(text: Any, what: str) -> time:
    s = str(text).strip()
    if len(s) == 4 and s.isdigit():          # session_open's "0400" form, should the regular fields ever use it
        s = f"{s[:2]}:{s[2:]}"
    try:
        return time.fromisoformat(s)
    except ValueError as e:
        raise DataUnavailable(f"alpaca calendar: {what} {text!r} is not a time") from e


def parse_alpaca_calendar(payload: Any) -> Any:
    """``/v2/calendar``'s JSON as a frame ``date`` (Timestamp), ``open``, ``close`` (``datetime.time``, New York),
    sorted by date. Raises :class:`DataUnavailable` on anything a calendar cannot be."""
    import pandas as pd

    if not isinstance(payload, list) or not payload:
        raise DataUnavailable("alpaca calendar: the response holds no session")
    rows = []
    for r in payload:
        try:
            d = date.fromisoformat(str(r["date"]))
        except (KeyError, TypeError, ValueError) as e:
            raise DataUnavailable(f"alpaca calendar: a row without a date ({r!r})") from e
        o, c = _hhmm(r.get("open"), f"{d} open"), _hhmm(r.get("close"), f"{d} close")
        if d.weekday() >= 5:
            raise DataUnavailable(f"alpaca calendar: {d} is a weekend day")
        if not o < c:
            raise DataUnavailable(f"alpaca calendar: {d} closes ({c}) before it opens ({o})")
        rows.append({"date": pd.Timestamp(d), "open": o, "close": c})
    frame = pd.DataFrame(rows, columns=["date", "open", "close"]).sort_values("date", ignore_index=True)
    if frame["date"].duplicated().any():
        raise DataUnavailable("alpaca calendar: a date is listed twice")
    return frame


def write_calendar(con: Any, frame: Any, start: date, end: date, *, source: str, fetched_at: datetime) -> int:
    """Replace ``exchange_calendar`` over ``[start, end]`` with ``frame``'s rows (the caller's transaction)."""
    inside = frame[(frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)]
    if len(inside) != len(frame):
        raise DataUnavailable(f"alpaca calendar: {len(frame) - len(inside)} session(s) outside the range asked for "
                              f"({start}..{end})")
    con.execute('DELETE FROM exchange_calendar WHERE "date" BETWEEN ? AND ?', [start, end])
    con.executemany("INSERT INTO exchange_calendar VALUES (?, ?, ?, ?, ?)",
                    [[d.date(), o, c, source, fetched_at] for d, o, c in inside.itertuples(index=False)])
    return len(inside)


@dataclass(frozen=True)
class Calendar:
    """Sessions with their New York open and close; ``first``..``last`` is what the calendar covers."""

    days: tuple[date, ...]
    hours: dict[date, tuple[time, time]] = field(hash=False)
    source: str = "alpaca"

    @property
    def first(self) -> date:
        return self.days[0]

    @property
    def last(self) -> date:
        return self.days[-1]

    def covers(self, d: date) -> bool:
        return self.first <= d <= self.last

    def is_session(self, d: date) -> bool:
        return d in self.hours

    def close(self, d: date) -> time | None:
        h = self.hours.get(d)
        return h[1] if h else None

    def previous(self, d: date) -> date | None:
        """The latest session before ``d``, or None when the calendar starts at or after it."""
        i = bisect.bisect_left(self.days, d)
        return self.days[i - 1] if i > 0 else None

    def sessions(self, start: date, end: date) -> list[date]:
        """The sessions in ``[start, end]`` that the calendar covers."""
        return list(self.days[bisect.bisect_left(self.days, start):bisect.bisect_right(self.days, end)])


def load_calendar(con: Any) -> Calendar | None:
    """The cached calendar, or None when there is none to read (no table before migration 005, or no rows yet)."""
    try:
        # the times as text: a job's recorded read hashes what it fetched, and a datetime.time has no canonical form
        rows = con.execute('SELECT "date", CAST(open AS VARCHAR), CAST(close AS VARCHAR), source '
                           'FROM exchange_calendar ORDER BY "date"').fetchall()
    except Exception as e:  # noqa: BLE001 - duckdb.CatalogException without importing duckdb here
        if "exchange_calendar" in str(e):
            return None
        raise
    if not rows:
        return None
    return Calendar(tuple(r[0] for r in rows), {r[0]: (time.fromisoformat(r[1]), time.fromisoformat(r[2])) for r in rows},
                    source=str(rows[-1][3] or "alpaca"))
