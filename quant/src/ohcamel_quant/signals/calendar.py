"""The NYSE session calendar the signal producers share (``signals.daily``'s
decision sessions, ``signals.overlay``'s bars freshness): weekdays less NYSE's
full-day holidays by rule, from pandas' own holiday rules (no new dependency;
the package has no exchange calendar -- warehouse/freshness.py and
deck/clock.py both count weekdays and say they cannot know holidays).
Unscheduled closures (a national day of mourning, a storm) are not known."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Any

__all__ = ["holidays", "is_session", "next_session", "previous_session"]


@lru_cache(maxsize=1)
def _nyse_calendar() -> Any:
    from pandas.tseries.holiday import (
        AbstractHolidayCalendar,
        GoodFriday,
        Holiday,
        USLaborDay,
        USMartinLutherKingJr,
        USMemorialDay,
        USPresidentsDay,
        USThanksgivingDay,
        nearest_workday,
        sunday_to_monday,
    )

    class NYSE(AbstractHolidayCalendar):
        rules = [
            Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),  # Saturday: no Friday close
            USMartinLutherKingJr, USPresidentsDay, GoodFriday, USMemorialDay,
            Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
            Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
            USLaborDay, USThanksgivingDay,
            Holiday("Christmas", month=12, day=25, observance=nearest_workday),
        ]

    return NYSE()


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset[date]:
    days = _nyse_calendar().holidays(start=f"{year}-01-01", end=f"{year}-12-31")
    return frozenset(d.date() for d in days)


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_session(d: date) -> date:
    """The next weekday after ``d`` that is not an NYSE holiday."""
    n = d + timedelta(days=1)
    while not is_session(n):
        n += timedelta(days=1)
    return n


def previous_session(d: date) -> date:
    """The last weekday before ``d`` that is not an NYSE holiday."""
    p = d - timedelta(days=1)
    while not is_session(p):
        p -= timedelta(days=1)
    return p
