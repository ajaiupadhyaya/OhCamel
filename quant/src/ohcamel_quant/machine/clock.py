"""The clock: every scheduled job at its New York time of day.

``jobs.cron.fire_times`` returns UTC instants; a job's place on the clock is the
hour in New York (``ny_hour``), which is what schedules.yaml writes and what holds
across daylight saving. A weekly or monthly job has no fire on an arbitrary day, so
it is placed on the next day it does fire. Entries of one kind merge into one job.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..deck.clock import NY
from ..jobs.cron import Cron, fire_times
from ..jobs.schedules import ScheduleEntry, Schedules

SEARCH_DAYS = 31  # the longest gap between fires of a monthly entry
SPREAD_H = 12.0  # fires closer together than this are one run, placed at its middle
WEEKDAYS = frozenset({1, 2, 3, 4, 5})

PRETTY = {
    "ops.selftest": "Self-test", "ingest.fred": "FRED cache", "ingest.universes": "Universes", "ingest.bars_daily": "Daily bars",
    "ingest.bars_minute": "Minute bars", "ingest.fred_warehouse": "Macro series", "ingest.factors": "Factors", "ingest.sec_facts": "SEC filings",
    "ingest.holdings_13f": "13F holdings", "ingest.option_snapshots": "Option chains", "risk.mc_atlas": "Risk atlas", "risk.mc_intraday": "Intraday risk",
    "vol.forecast_league": "Vol forecast league", "cov.league": "Covariance league", "farm.sweep": "Strategy farm", "vol.surface_history": "Vol surface",
    "models.xs_lgbm": "Cross-section model", "regime.hmm": "Regime model", "ops.data_integrity": "Data integrity", "kronos.shadow": "Kronos shadow",
    "factors.screen": "Factor screen", "signals.daily": "Daily signals", "signals.overlay": "Exposure overlay", "desk.report": "Desk report",
}


@dataclass(frozen=True)
class JobClock:
    kind: str
    fires: tuple[float, ...]  # New York hours of the day, sorted
    hour: float  # where the job sits on the clock (place_hour of its fires)
    cron: tuple[str, ...]
    heavy: bool
    mem: str
    when: tuple[str, ...]  # daily, weekdays, weekly or monthly, one per distinct cadence


def ny_hour(t: datetime) -> float:
    """The hour of day in New York, minutes as a fraction: 18:30 -> 18.5."""
    n = t.astimezone(NY)
    return n.hour + n.minute / 60


def place_hour(fires: Sequence[float]) -> float:
    """Where a job sits on the clock: a run of fires under 12 h apart at its middle (a lone fire is
    its own middle), a run spread wider at its last fire."""
    first, last = min(fires), max(fires)
    return (first + last) / 2 if last - first < SPREAD_H else last


def _when(c: Cron) -> str:
    if c.dow_star and c.dom_star:
        return "daily"
    if not c.dom_star:
        return "monthly"
    return "weekdays" if c.dows == WEEKDAYS else "weekly"


def _hours(c: Cron, day: date) -> list[float]:
    """The New York hours the entry fires on ``day``, or on the first day after it that it does."""
    for k in range(SEARCH_DAYS + 1):
        fires = fire_times(c, day + timedelta(days=k))
        if fires:
            return [ny_hour(t) for t in fires]
    return []


def job_clock(schedules: Schedules, day: date) -> dict[str, JobClock]:
    """One ``JobClock`` per scheduled kind, hours read on New York date ``day``. A kind that does not
    fire within a month of ``day`` has no time of day and is left out."""
    entries: dict[str, list[ScheduleEntry]] = {}
    for e in schedules.entries:
        entries.setdefault(e.kind, []).append(e)
    out: dict[str, JobClock] = {}
    for kind, es in entries.items():
        fires = sorted(h for e in es for h in _hours(e.parsed, day))
        if fires:
            out[kind] = JobClock(kind, tuple(fires), place_hour(fires), tuple(e.cron for e in es),
                                 es[0].heavy, es[0].mem_class, tuple(sorted({_when(e.parsed) for e in es})))
    return out
