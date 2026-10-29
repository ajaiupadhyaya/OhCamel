"""The Machine's graph, built from repo config: the world (names) and the clock (every job's hour)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.jobs.cron import parse_cron
from ohcamel_quant.jobs.schedules import ScheduleEntry, Schedules, load_schedules
from ohcamel_quant.machine.clock import job_clock, ny_hour, place_hour
from ohcamel_quant.machine.readouts import cluster_order, ticker_readouts
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


# ---------------------------------------------------------------- readouts and the cluster order
GROWTH = np.log(1.001)    # A's daily log growth: 0.1% a day
WOBBLE = 1e-4             # A's only variation: +/- this, alternating, so it sums to zero over any even run


@pytest.fixture
def closes() -> pd.DataFrame:
    """300 business days. A grows 0.1% a day (almost: a correlation needs some variation to rank on,
    and the alternating wobble cancels exactly over the 252-return window); B is 2*A plus small
    noise; C's returns are A's negated plus its own noise; D has no closes at all."""
    n = 300
    rng = np.random.default_rng(7)
    a_ret = GROWTH + WOBBLE * (-1.0) ** np.arange(n)
    a = 100 * np.exp(np.cumsum(a_ret))
    b = 2 * a * (1 + 2e-5 * rng.standard_normal(n))
    c = 100 * np.exp(np.cumsum(-a_ret + 5e-5 * rng.standard_normal(n)))
    return pd.DataFrame({"A": a, "B": b, "C": c, "D": np.nan}, index=pd.bdate_range(end="2026-10-28", periods=n))


def peer_names(readout: dict) -> list[str]:
    return [p for p, _ in readout["peers"]]


def test_readouts_on_known_series(closes):
    r = ticker_readouts(closes)
    a = r["A"]
    assert a["vol"] < 0.01                                        # constant growth: next to no volatility
    assert a["ret1y"] == round(1.001**252 - 1, 4)                 # 252 returns in the 253-close window
    assert a["last"] == round(closes["A"].iloc[-1], 2)
    assert a["n"] == 253 == r["B"]["n"]                           # window + 1 closes: a full year
    assert len(a["spark"]) <= 61 and a["spark"][0] == 1.0         # every 5th of 253 closes (51), plus the last
    assert a["spark"][-1] == round(closes["A"].iloc[-1] / closes["A"].iloc[-253], 4)
    assert set(r) == {"A", "B", "C"}                              # D has no closes, so no key
    assert peer_names(a) == ["B", "C"]                            # B first; itself never its own peer
    assert a["peers"][0][1] >= 0.9 and a["peers"][1][1] < 0       # C moves against A, so it comes last


def test_volatility_is_the_annualised_sample_std_of_daily_log_returns(closes):
    walk = np.exp(np.cumsum(0.02 * np.random.default_rng(11).standard_normal(len(closes))))
    v = pd.Series(100 * walk, index=closes.index)
    expected = np.log(v.iloc[-253:]).diff().std() * np.sqrt(252)   # pandas' std is ddof 1
    assert ticker_readouts(closes.assign(V=v))["V"]["vol"] == round(float(expected), 4)
    assert 0.2 < expected < 0.4                                    # a real-looking number, not ~0


def test_peers_are_the_top_n_best_first(closes):
    rng = np.random.default_rng(5)
    noisy = {f"X{k}": closes["B"] * (1 + k * 1e-4 * rng.standard_normal(len(closes))) for k in range(1, 6)}
    peers = ticker_readouts(closes.assign(**noisy), n_peers=3)["A"]["peers"]
    assert len(peers) == 3 and peers[0][0] == "B"
    assert [c for _, c in peers] == sorted((c for _, c in peers), reverse=True)


def test_peers_need_overlap(closes):
    e = np.full(len(closes), np.nan)
    e[-50:] = 3 * closes["A"].iloc[-50:] * (1 + 2e-5 * np.random.default_rng(3).standard_normal(50))
    with_e = closes.assign(E=e)
    r = ticker_readouts(with_e)
    assert {"spark", "ret1y", "vol", "last", "n", "peers"} <= set(r["E"])   # it has a readout ...
    assert r["E"]["n"] == 50                                             # ... of 50 closes, not a year ...
    assert r["E"]["peers"] == []                                         # ... no peers ...
    assert all("E" not in peer_names(x) for x in r.values())             # ... and is no one's
    assert r["A"]["peers"]
    assert "E" in peer_names(ticker_readouts(with_e, min_overlap=40)["A"])   # 49 shared returns clears 40


def test_a_ticker_that_never_moves_has_no_correlation(closes):
    still = closes.assign(F=50.0)
    r = ticker_readouts(still)
    assert r["F"]["vol"] == 0.0 and r["F"]["ret1y"] == 0.0 and r["F"]["peers"] == []
    assert all("F" not in peer_names(x) for x in r.values())
    assert cluster_order(still, ["F", "A", "B", "C"])[-1] == "F"       # nothing to cluster it by


def test_readouts_are_plain_json_with_no_nan(closes):
    short = closes.assign(G=np.nan, H=np.nan)
    short.loc[short.index[-2:], "G"] = [10.0, 11.0]               # two closes: one return, so no volatility
    short.loc[short.index[-1], "H"] = 10.0                        # one close: no readout
    r = ticker_readouts(short)
    assert "H" not in r and "vol" not in r["G"] and r["G"]["ret1y"] == 0.1 and r["G"]["spark"] == [1.0, 1.1]
    assert r["G"]["n"] == 2 and type(r["G"]["n"]) is int          # an int, so it serialises as one
    json.dumps(r, allow_nan=False)                                # raises on NaN or inf
    assert type(r["A"]["vol"]) is float and type(r["A"]["spark"][0]) is float


def test_non_positive_and_infinite_closes_are_absent(closes):
    bad = closes.copy()
    bad.iloc[-5:, 0] = [0.0, -1.0, np.inf, -np.inf, np.nan]       # A's last five rows
    a = ticker_readouts(bad)["A"]
    assert a["last"] == round(closes["A"].iloc[-6], 2)            # the newest usable close
    assert a["n"] == 253 - 5                                      # and n counts only the usable ones
    json.dumps(a, allow_nan=False)


def test_cluster_order_keeps_the_pair_together(closes):
    order = cluster_order(closes, ["A", "B", "C", "D"])
    assert abs(order.index("A") - order.index("B")) == 1
    assert order[-1] == "D" and len(order) == 4


def test_cluster_order_lists_every_input_name_once(closes):
    order = cluster_order(closes, ["D", "C", "Z", "B", "A", "B"])   # Z is not in closes; B is listed twice
    assert sorted(order) == ["A", "B", "C", "D", "Z"] and order[-2:] == ["D", "Z"]   # the rest, alphabetically
    assert cluster_order(closes, []) == [] and cluster_order(closes, ["Z", "Y"]) == ["Y", "Z"]
    assert cluster_order(closes, ["C"]) == ["C"]                  # one name has nothing to cluster against
    assert cluster_order(closes.iloc[:0], ["B", "A"]) == ["A", "B"]
