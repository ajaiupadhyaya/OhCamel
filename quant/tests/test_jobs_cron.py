"""Lane B, B4: five-field cron in New York wall time, across both 2026-27 DST transitions.

UTC instants are hand-derived: EDT is UTC-4 (until 2026-11-01 02:00 EDT and
from 2027-03-14 02:00 EST), EST is UTC-5.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ohcamel_quant.jobs.cron import fire_times, latest_due, parse_cron


def u(*a):
    return datetime(*a, tzinfo=UTC)


def test_parse_fields():
    c = parse_cron("*/15 9-16 * * 1-5")
    assert c.minutes == frozenset({0, 15, 30, 45}) and c.hours == frozenset(range(9, 17))
    assert c.dows == frozenset({1, 2, 3, 4, 5})
    assert parse_cron("0 0 * * 7").dows == frozenset({0})  # 7 is Sunday too
    assert parse_cron("5,35 1 1 1,7 *").months == frozenset({1, 7})
    assert parse_cron("10/20 * * * *").minutes == frozenset({10, 30, 50})


@pytest.mark.parametrize("bad", ["", "* * * *", "61 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *",
                                 "* * * * 8", "*/0 * * * *", "5-1 * * * *", "a * * * *"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        parse_cron(bad)


def test_dom_and_dow_follow_cron_or_rule():
    # Both restricted: either matches (cron semantics). 2026-10-01 is a Thursday (dow 4).
    c = parse_cron("0 12 15 * 4")
    assert fire_times(c, date(2026, 10, 1)) == [u(2026, 10, 1, 16, 0)]   # Thursday, not the 15th
    assert fire_times(c, date(2026, 10, 15)) == [u(2026, 10, 15, 16, 0)]  # the 15th (also a Thursday)
    assert fire_times(c, date(2026, 10, 14)) == []
    # Only dow restricted: dow alone decides.
    assert fire_times(parse_cron("0 12 * * 6"), date(2026, 10, 10)) == [u(2026, 10, 10, 16, 0)]  # Saturday


def test_fall_back_2026_11_01():
    daily = parse_cron("30 6 * * *")
    assert fire_times(daily, date(2026, 10, 31)) == [u(2026, 10, 31, 10, 30)]  # 06:30 EDT
    assert fire_times(daily, date(2026, 11, 2)) == [u(2026, 11, 2, 11, 30)]    # 06:30 EST
    # 01:30 happens twice on 2026-11-01; it runs once, at the first (EDT) occurrence.
    assert fire_times(parse_cron("30 1 * * *"), date(2026, 11, 1)) == [u(2026, 11, 1, 5, 30)]


def test_spring_forward_2027_03_14():
    c = parse_cron("30 2 * * *")
    assert fire_times(c, date(2027, 3, 13)) == [u(2027, 3, 13, 7, 30)]  # 02:30 EST
    # 02:30 does not exist on 2027-03-14; it runs once, an hour later in wall terms (03:30 EDT = 07:30Z).
    assert fire_times(c, date(2027, 3, 14)) == [u(2027, 3, 14, 7, 30)]
    assert fire_times(c, date(2027, 3, 15)) == [u(2027, 3, 15, 6, 30)]  # 02:30 EDT


def test_latest_due_is_the_newest_in_the_half_open_window():
    hourly = parse_cron("0 * * * *")
    assert latest_due(hourly, u(2026, 10, 5, 10, 0), u(2026, 10, 5, 17, 20)) == u(2026, 10, 5, 17, 0)
    assert latest_due(hourly, u(2026, 10, 5, 17, 0), u(2026, 10, 5, 17, 20)) is None  # (after, now]
    assert latest_due(hourly, u(2026, 10, 5, 16, 59), u(2026, 10, 5, 17, 0)) == u(2026, 10, 5, 17, 0)
    weekly = parse_cron("0 6 * * 1")  # Mondays 06:00 New York
    assert latest_due(weekly, u(2026, 9, 30), u(2026, 10, 7)) == u(2026, 10, 5, 10, 0)


def test_next_due_is_the_first_fire_strictly_after_now():
    from ohcamel_quant.jobs.cron import next_due

    c = parse_cron("0 23 * * *")  # 23:00 New York nightly
    # 2026-10-07 21:00 EDT = 2026-10-08 01:00 UTC; next is 23:00 EDT = 03:00 UTC on the 8th
    assert next_due(c, u(2026, 10, 8, 1, 0)) == u(2026, 10, 8, 3, 0)
    # exactly on a fire time: the next one, not this one
    assert next_due(c, u(2026, 10, 8, 3, 0)) == u(2026, 10, 9, 3, 0)


def test_next_due_reaches_a_monthly_entry_weeks_away():
    from ohcamel_quant.jobs.cron import next_due

    c = parse_cron("30 22 1 * *")  # monthly on the 1st, 22:30 New York
    # from 2026-10-02 the next is 2026-11-01 22:30 EST (after the fall-back) = 2026-11-02 03:30 UTC
    assert next_due(c, u(2026, 10, 2, 12, 0)) == u(2026, 11, 2, 3, 30)


def test_next_due_none_for_an_impossible_date():
    from ohcamel_quant.jobs.cron import next_due

    assert next_due(parse_cron("0 0 31 2 *"), u(2026, 10, 8, 0, 0)) is None
