"""Five-field cron (minute hour day-of-month month day-of-week) in New York wall time.

Supports ``*``, lists, ranges and steps (``*/15``, ``10/20``, ``1-5``); day of
week 0 or 7 is Sunday; when both day fields are restricted either may match
(classic cron). DST: a wall time that happens twice (fall back) fires once, at
its first occurrence; a wall time that does not exist (spring forward) fires
once, at the instant the pre-transition offset gives it (02:30 -> 03:30 EDT).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from ..deck.clock import NY


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    doms: frozenset[int]
    months: frozenset[int]
    dows: frozenset[int]
    dom_star: bool
    dow_star: bool


def _field(text: str, lo: int, hi: int) -> frozenset[int]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        base = part
        if "/" in part:
            base, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise ValueError(f"cron step must be >= 1 in {part!r}")
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a, b = (int(x) for x in base.split("-", 1))
        else:
            a = int(base)
            b = hi if "/" in part else a
        if not lo <= a <= b <= hi:
            raise ValueError(f"cron field {part!r} outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return frozenset(out)


def parse_cron(expr: str) -> Cron:
    f = expr.split()
    if len(f) != 5:
        raise ValueError(f"cron needs five fields: {expr!r}")
    try:
        dows = frozenset(d % 7 for d in _field(f[4], 0, 7))
        return Cron(_field(f[0], 0, 59), _field(f[1], 0, 23), _field(f[2], 1, 31), _field(f[3], 1, 12), dows,
                    f[2] == "*", f[4] == "*")
    except ValueError as e:
        raise ValueError(f"bad cron {expr!r}: {e}") from e


def _day_matches(c: Cron, d: date) -> bool:
    if d.month not in c.months:
        return False
    dom_ok = d.day in c.doms
    dow_ok = (d.weekday() + 1) % 7 in c.dows  # Python Monday=0 -> cron Monday=1, Sunday=0
    if c.dom_star or c.dow_star:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def fire_times(c: Cron, d: date) -> list[datetime]:
    """The UTC instants this entry fires on New York date ``d``."""
    if not _day_matches(c, d):
        return []
    out = {datetime(d.year, d.month, d.day, h, m, tzinfo=NY).astimezone(UTC)  # fold=0
           for h in c.hours for m in c.minutes}
    return sorted(out)


def latest_due(c: Cron, after: datetime, now: datetime) -> datetime | None:
    """The newest fire time t with after < t <= now (searching at most 8 days back)."""
    d = now.astimezone(NY).date()
    stop = max(after.astimezone(NY).date(), d - timedelta(days=8))
    while d >= stop:
        due = [t for t in fire_times(c, d) if after < t <= now]
        if due:
            return due[-1]
        d -= timedelta(days=1)
    return None


def next_due(c: Cron, now: datetime, horizon_days: int = 400) -> datetime | None:
    """The first fire time t > now within ``horizon_days`` (a monthly entry can be weeks away), or None."""
    d = now.astimezone(NY).date()
    for _ in range(horizon_days + 1):
        due = [t for t in fire_times(c, d) if t > now]
        if due:
            return due[0]
        d += timedelta(days=1)
    return None
