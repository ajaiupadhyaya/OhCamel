"""The Machine's graph, built from repo config: the world (names) and the clock (every job's hour)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ohcamel_quant.jobs.cron import parse_cron
from ohcamel_quant.jobs.schedules import ScheduleEntry, Schedules, load_schedules
from ohcamel_quant.machine.clock import job_clock, ny_hour, place_hour
from ohcamel_quant.machine.world import load_world
from ohcamel_quant.warehouse.constituents import load_constituents


def test_jobs_land_at_their_new_york_hour():   # spec §6.10: the prototype's UTC bug
    c = job_clock(load_schedules(), date(2026, 10, 28))
    assert c["vol.forecast_league"].hour == 21.5
    assert c["farm.sweep"].hour == 23.0
    assert c["ingest.bars_daily"].hour == 18.5


@pytest.mark.parametrize("day", [date(2026, 3, 8), date(2026, 11, 1), date(2027, 1, 13)])
def test_hours_hold_across_daylight_saving(day):            # Review Focus 2
    assert job_clock(load_schedules(), day)["farm.sweep"].hour == 23.0


@pytest.mark.parametrize("day", [date(2026, 3, 8), date(2026, 11, 1)])
def test_a_daily_job_holds_its_hour_on_the_changeover_day_itself(day):   # farm.sweep skips these Sundays
    c = job_clock(load_schedules(), day)["ingest.fred_warehouse"]
    assert c.when == ("daily",) and c.hour == 7.0


def test_a_weekly_job_is_placed_on_the_day_it_fires():      # Review Focus 2
    c = job_clock(load_schedules(), date(2026, 10, 28))
    assert all(j.fires for j in c.values())                # every scheduled kind has a time of day


def test_entries_of_a_kind_merge_and_each_cadence_is_named():
    s = load_schedules()
    c = job_clock(s, date(2026, 10, 28))                   # a Wednesday
    assert set(c) == {e.kind for e in s.entries}
    assert (c["regime.hmm"].when, c["regime.hmm"].hour) == (("weekly",), 9.0)           # Saturday 09:00
    assert (c["models.xs_lgbm"].when, c["models.xs_lgbm"].hour) == (("monthly",), 22.5)  # the 1st, 22:30
    assert c["ops.selftest"].when == ("daily",)
    o = c["signals.overlay"]
    assert o.when == ("weekdays",) and len(o.cron) == 6 and list(o.fires) == sorted(o.fires)
    assert (c["farm.sweep"].heavy, c["farm.sweep"].mem) == (True, "L")
    assert (c["ingest.bars_daily"].heavy, c["ingest.bars_daily"].mem) == (False, "S")


def test_a_kind_that_never_fires_within_a_month_is_left_out():
    leap = ScheduleEntry("never", "0 0 29 2 *", parse_cron("0 0 29 2 *"), "ops.selftest", {}, 1, "S", False)
    assert job_clock(Schedules((leap,)), date(2026, 10, 28)) == {}


def test_place_hour():
    assert place_hour([18.5]) == 18.5
    assert place_hour([9.5, 10.0, 15.75]) == pytest.approx(12.625)   # a run under 12 h apart: its middle
    assert place_hour([9.6667, 18.8333]) == pytest.approx(14.25)      # 9.2 h apart is still under 12 h: its middle
    assert place_hour([6.0, 19.0]) == 19.0                            # spread wider than 12 h: the last fire
    assert place_hour([6.0, 18.0]) == 18.0                            # exactly 12 h is not under 12 h


def test_ny_hour():
    assert ny_hour(datetime(2026, 10, 28, 22, 30, tzinfo=UTC)) == 18.5


def test_the_world_is_disjoint_and_complete():
    w = load_world()
    etf = {t for t, _, _ in w.etfs}
    assert etf.isdisjoint(w.equities) and len({f for f, _ in w.fred}) == len(w.fred)
    assert set(w.minute) <= etf | set(w.equities) and set(w.options) <= etf | set(w.equities)
    assert len(w.filers) == 6 and all(f["cik"] and f["manager"] for f in w.filers)


def test_the_world_is_named_as_the_config_names_it():
    w = load_world()
    fred = dict(w.fred)
    assert fred["DGS1MO"] == "Treasury yield, 1-month" and fred["DGS30"] == "Treasury yield, 30-year"
    assert not fred["DGS10"].startswith("Treasury yield")   # macro/series.json names it; that name wins
    ids = [i for i, _ in w.fred]
    assert ids.index("WALCL") < ids.index("DGS1MO")          # series.json first, then the Treasury tenors
    assert w.etfs[0] == ("SPY", "SPDR S&P 500 ETF Trust", ("US equity indices",))
    assert next(iter(w.equities)) == load_constituents("sp500").members[0][0]
    assert w.daily() == [t for t, _, _ in w.etfs] + list(w.equities)
    assert [k for k, _ in w.factors] == ["FF5", "MOM"]
    assert set(w.filers[0]) == {"cik", "name", "manager"}
