"""The Machine's graph: the world (names), the clock (every job's hour), the ticker readouts, and the
assembly of spec §5's rings with one rule per edge kind."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from functools import cache

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.jobs.cron import parse_cron
from ohcamel_quant.jobs.schedules import ScheduleEntry, Schedules, load_schedules
from ohcamel_quant.machine.clock import JobClock, job_clock, ny_hour, place_hour
from ohcamel_quant.machine.graph import (
    FEEDS,
    LENS_WHY,
    NOTE_CAP,
    PROD,
    RULES,
    WITHHELD,
    Inputs,
    Product,
    build_graph,
    provenance_symbols,
    public_note,
    public_notes,
)
from ohcamel_quant.machine.lenses import LENS_TABLES, extras, product_lens
from ohcamel_quant.machine.readouts import _peers, cluster_order, ticker_readouts
from ohcamel_quant.machine.world import load_world
from ohcamel_quant.signals.daily import EXECUTION_NOTES
from ohcamel_quant.signals.report import BOOK_COLUMNS
from ohcamel_quant.signals.roster import load_roster
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


def _peers_by_series(corr, name, n_peers):
    """What ``_peers`` replaced, kept as the reference for its output: best first, ties by name, NaN pairs out."""
    others = corr[name].drop(name).dropna().sort_index()
    top = others.sort_values(ascending=False, kind="stable").head(n_peers)
    return [[str(t), round(float(c), 2)] for t, c in top.items()]


@pytest.mark.parametrize("seed", range(12))
def test_peers_are_exactly_a_stable_sort_by_name_then_correlation(seed):
    rng = np.random.default_rng(seed)
    names = [f"T{i:02d}" for i in rng.permutation(40)]         # the columns in no order
    corr = np.round(rng.uniform(-1, 1, (40, 40)), 1)           # one decimal: ties everywhere
    corr = (corr + corr.T) / 2
    gaps = rng.uniform(size=corr.shape) < 0.15
    corr[gaps | gaps.T] = np.nan                               # and NaN pairs, some of them on the diagonal
    frame = pd.DataFrame(corr, index=names, columns=names)
    for n_peers in (0, 1, 6, 39, 60):
        got = _peers(frame, n_peers)
        assert got == {t: _peers_by_series(frame, t, n_peers) for t in names}


def test_peers_put_a_tie_in_name_order():
    corr = pd.DataFrame(np.full((4, 4), 0.5), index=list("DBCA"), columns=list("DBCA"))
    assert [t for t, _ in _peers(corr, 2)["D"]] == ["A", "B"]


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


# ---------------------------------------------------------------- the graph: spec §5's rings and edge rules
RINGS = {"feeds", "daily", "filings", "minute", "options", "clock", "products", "results", "strategies", "limits", "book"}
LIMITS = [{"name": "aapl-cap", "kind": "gross_notional", "scope": "instrument:AAPL"},
          {"name": "dd-cap", "kind": "max_drawdown", "scope": "portfolio"},
          {"name": "tech-cap", "kind": "gross_notional", "scope": "sector:TECH"}]
READOUTS = {"NVDA": {"spark": [1.0, 1.4, 2.1], "ret1y": 1.1, "vol": 0.52, "last": 191.2, "n": 253, "peers": [["AMD", 0.71]]},
            "AAPL": {"spark": [1.0, 1.1], "ret1y": 0.1, "last": 230.1, "n": 2}}   # Ruling 10: no vol, no peers
ORDER = ["NVDA", "AAPL", "MSFT"]   # the cluster order puts these first; the other equities follow alphabetically


@cache
def _static():
    """The real world, clock and roster: the graph's skeleton is the repo's own config."""
    return load_world(), job_clock(load_schedules(), date(2026, 10, 28)), load_roster()


def _daily_cells():
    return [c for c in _static()[2].cells if c.producer == "signals.daily"]


def _product(kind, tables=None, provenance=(), *, stale=False, why=None, **manifest):
    tables = tables or {}
    m = {"id": f"01J{kind.replace('.', '').upper()}", "kind": kind, "code_sha": "a1b2c3d", "data_asof": "2026-10-28",
         "finished_at": "2026-10-28T23:41:00Z", "cpu_seconds": 61.5, "engine": "python", "provenance": list(provenance),
         "notes": [f"{kind}: a note the job wrote"], "tables": list(tables), "verdict": None, "verdict_detail": None,
         **manifest}
    return Product(kind, m, stale, tables, {k: len(v) for k, v in tables.items()}, why)


def _summary(**over):
    row = {"as_of": "2026-10-28T20:31:07Z", "positions": 5, "kill_switch": "armed",
           "trips_on": json.dumps(["dd-cap"]), "limits": json.dumps(LIMITS), **over}
    return pd.DataFrame([row], columns=BOOK_COLUMNS)


def _desk(book_summary):
    live = [{"source": "ohcamel-live", "url": "http://engine", "route": "/api/graph", "read": True}]
    if isinstance(book_summary, pd.DataFrame):
        return _product("desk.report", {"book_summary": book_summary}, live)
    if book_summary == "absent":
        return None
    if book_summary == "untabled":
        return _product("desk.report", {"fills": pd.DataFrame({"symbol": ["AAPL"]})}, live)
    table = {"usable": _summary(), "stale": _summary(), "empty": _summary().iloc[:0],
             "unlimited": _summary(limits=None)}[book_summary]
    return _product("desk.report", {"book_summary": table}, live, stale=book_summary == "stale")


def _bars(symbol, dataset="bars_daily"):
    """A warehouse reader's provenance entry (``warehouse/readers.py`` ``ohlcv``)."""
    return {"source": "warehouse:yahoo", "fetched_at": "", "synthetic": False,
            "detail": {"dataset": dataset, "path": "warehouse", "symbol": symbol}}


def _products():
    cs = _daily_cells()[:3]
    cells = pd.DataFrame({"cell_id": [f"{c.strategy}@{c.universe}" for c in cs], "strategy": [c.strategy for c in cs],
                          "universe": [c.universe for c in cs], "status": ["ok", "ok", "skipped"],
                          "reason": [None, None, "no common history"], "n_combos": [24, 24, 0]})
    board = pd.DataFrame({"cell_id": cells["cell_id"][:2], "verdict": ["FAIL", "FAIL"],
                          "detail": ["DSR BELOW 0.95", "PBO ABOVE 0.5"], "dsr_farm": [0.41, np.nan], "psr": [0.62, 0.48],
                          "oos_sharpe_ann": [0.35, -0.1], "pbo": [0.3, 0.7], "n_combos": [24, 24]})
    vol = pd.DataFrame({"model": ["gjr", "har"], "mcs_rate": [0.8, np.nan], "mean_rank": [1.4, 2.1], "names": [40, 38]})
    league = pd.DataFrame({"universe": ["ndx100", "ndx100", "sectors", "sectors"],
                           "estimator": ["sample", "lw_constant_corr", "sample", "lw_constant_corr"],
                           "realized_vol": [0.18, 0.16, 0.14, 0.13], "rank": [2, 1, 2, 1]})
    atlas = pd.DataFrame([{"book": "core", "method": m, "horizon": h, "alpha": a, "var": 0.01 * h * k, "es": 0.015 * h * k,
                           "var_usd": 1000.0 * h * k, "es_usd": 1500.0 * h * k, "n_paths": 20000}
                          for m in ("fhs", "copula_t") for h in (1, 10) for a, k in ((0.95, 0.5), (0.99, 1))])
    members = pd.DataFrame({"ticker": ["XLK", "XLE", "XLK"], "universe": ["sectors"] * 3})
    trials = pd.DataFrame({"cell_id": ["x@y"] * 48, "combo": range(48), "sr_pp": np.linspace(-0.1, 0.1, 48)})
    special = {
        "farm.sweep": _product("farm.sweep", {"cells": cells, "leaderboard": board, "trials": trials},
                               [{"source": "ohcamel-farm", "cells_run_tonight": 3, "cells_total": 21}],
                               verdict="FAIL", verdict_detail="NO CELL PASSES"),
        "vol.forecast_league": _product("vol.forecast_league", {"summary": vol}, [_bars("SPY")]),
        "cov.league": _product("cov.league", {"league": league},
                               [_bars("NVDA"), {"source": "ohcamel-cov-league", "universe": "ndx100",
                                                "tickers": ["nvda", "MSFT"]}]),
        "risk.mc_atlas": _product("risk.mc_atlas", {"summary": atlas, "members": members}, [_bars("TSLA")]),
        "risk.mc_intraday": _product("risk.mc_intraday", {}, [_bars("QQQ", "bars_minute"), _bars("QQQ")]),
        "signals.daily": _product("signals.daily", {}, [{"source": "farm.sweep", "artifact": "01JFARMSWEEP"},
                                                        {"source": "signals.freeze", "artifact": "01JFREEZE"},
                                                        {"source": "roster", "path": "roster.yaml"}]),
        "signals.overlay": _product("signals.overlay", {}, [
            {"source": "cov.league", "artifact": "01JCOV"}, {"source": "vol.forecast_league", "artifact": "01JVOL"},
            {"source": "roster", "path": "roster.yaml"},
            {"source": "reported only", "regime.hmm": "01JHMM", "mc": "01JMC"}]),
    }
    return {k: special.get(k) or _product(k) for k in PROD if k != "desk.report"}


def _inputs(*, book_summary="usable", drop=None, **over):
    """A small, whole ``Inputs``: the real world, clock and roster; artifacts built from small frames.
    ``book_summary``: usable | absent | untabled | empty | unlimited | stale, or a frame to use as the table.
    ``drop``: an artifact kind with no artifact. Any other keyword replaces that ``Inputs`` field."""
    world, clock, roster = _static()
    products = _products()
    desk = _desk(book_summary)
    if desk is not None:
        products["desk.report"] = desk
    products.pop(drop, None)
    fresh = {f[4]: {"data_asof": "2026-10-28", "last_ok_at": "2026-10-28T22:41:00", "keys": 12, "keys_behind": 1,
                    "stale": False} for f in FEEDS}
    fields = {"world": world, "clock": clock, "roster": roster, "products": products, "freshness": fresh,
              "readouts": READOUTS, "order": ORDER, "why": {}, **over}
    return Inputs(**fields)


def _ix(g):
    return {n["id"]: i for i, n in enumerate(g["nodes"])}


def _ids(g):
    return {n["id"] for n in g["nodes"]}


def _node(g, id_):
    return g["nodes"][_ix(g)[id_]]


def _edge(g, a, b, kind):
    ix = _ix(g)
    return a in ix and b in ix and [ix[a], ix[b], kind] in g["edges"]


def _edges_into(g, b, kind=None):
    """The ids of the nodes with an edge into ``b`` (of ``kind``, when given), in edge order."""
    ix = _ix(g)
    return [g["nodes"][a]["id"] for a, to, k in g["edges"] if to == ix[b] and kind in (None, k)]


def _edges_from(g, a, kind=None):
    ix = _ix(g)
    return [g["nodes"][b]["id"] for fr, b, k in g["edges"] if fr == ix[a] and kind in (None, k)]


def test_every_edge_follows_a_rule():                          # spec §5: "an edge with no rule is a bug"
    g = build_graph(_inputs())
    k = [n["k"] for n in g["nodes"]]
    for a, b, kind in g["edges"]:
        assert (k[a], k[b]) in RULES[kind], (g["nodes"][a]["id"], g["nodes"][b]["id"], kind)
    assert {e[2] for e in g["edges"]} <= set(RULES)
    pairs = {(k[a], k[b], kind) for a, b, kind in g["edges"]}
    assert pairs == {(f, t, kind) for kind, r in RULES.items() for f, t in r}   # and the fixture uses every rule


def test_the_roster_gives_23_strategies():
    g = build_graph(_inputs())
    st = [n for n in g["nodes"] if n["k"] == "strategy"]
    assert len(st) == 23
    assert sum(n["m"]["producer"] == "signals.daily" for n in st) == 21


def test_the_book_comes_from_book_summary():
    g = build_graph(_inputs())
    ids = _ids(g)
    assert {"lim:aapl-cap", "lim:tech-cap", "lim:dd-cap", "kill"} <= ids
    assert _edge(g, "d:AAPL", "lim:aapl-cap", "read") and not _edges_into(g, "lim:tech-cap", "read")
    assert _edge(g, "lim:dd-cap", "kill", "trip") and _edge(g, "kill", "book", "halt")


@pytest.mark.parametrize("why", ["absent", "untabled", "empty", "unlimited", "stale"])
def test_without_a_usable_summary_there_are_no_limits(why):
    g = build_graph(_inputs(book_summary=why))
    assert not [n for n in g["nodes"] if n["k"] in ("limit", "kill")]
    assert not [e for e in g["edges"] if e[2] in ("limit", "trip", "halt")]
    assert _node(g, "book")["m"]["summary_why"]


def test_each_unusable_summary_says_which_case_it_is():
    whys = {w: _node(build_graph(_inputs(book_summary=w)), "book")["m"]["summary_why"]
            for w in ("absent", "untabled", "empty", "unlimited", "stale")}
    assert len(set(whys.values())) == 5                         # five cases, five different sentences
    assert whys["absent"] == "The nightly desk report that carries the book's limits has not published them yet."
    assert whys["empty"] == "The desk report ran but could not read the live book."
    assert "scheduled one has been missed" in whys["stale"] and "limits" in whys["unlimited"]
    assert not any(word in w for w in whys.values() for word in ("book_summary", "desk.report", "artifact", "table", "stale"))
    m = _node(build_graph(_inputs(book_summary="stale")), "book")["m"]
    assert not {"positions", "kill", "limits", "summary_asof"} & set(m)   # a stale row is never shown as the book


def test_a_missing_artifact_is_a_missing_node():
    n = _node(build_graph(_inputs(drop="cov.league")), "p:cov.league")
    assert n["status"] == "missing" and n["why"]


def test_a_missing_artifact_reads_nothing_and_has_no_results():
    g = build_graph(_inputs(drop="cov.league", why={"cov.league": "the artifact root is not mounted"}))
    n = _node(g, "p:cov.league")
    assert n["why"] == "the artifact root is not mounted" and n["m"] == {"kind": "cov.league"}
    assert not _edges_into(g, "j:cov.league", "read")          # no provenance to read from
    assert not [i for i in _ids(g) if i.startswith("ce:")]
    assert _edge(g, "j:cov.league", "p:cov.league", "publish")
    assert _edge(g, "p:cov.league", "j:signals.overlay", "read")   # the overlay's own provenance still names it


def test_an_artifact_missing_a_table_stays_present_and_says_so():   # Review Focus 5
    products = _inputs().products
    farm = products["farm.sweep"]
    cells_only = Product(farm.kind, farm.manifest, False, {"cells": farm.tables["cells"]}, farm.rows,
                         "Part of the latest run could not be read.")
    g = build_graph(_inputs(products={**products, "farm.sweep": cells_only}))
    n = _node(g, "p:farm.sweep")
    assert "status" not in n and n["why"] == "Part of the latest run could not be read." and n["m"]["verdict"] == "FAIL"
    cells = _edges_from(g, "p:farm.sweep", "result")
    assert len(cells) == 3 and not [c for c in cells if "verdict" in _node(g, c)["m"]]


def test_a_malformed_provenance_degrades_only_its_own_node():
    products = _inputs().products
    bad = {"signals.overlay": 5,                                   # not a list at all
           "signals.daily": ["farm.sweep", 7, None,                # entries that are not dicts ...
                             {"source": ["cov.league", "farm.sweep"]}, {"source": {"farm.sweep": 1}}]}  # ... or no str source
    broken = {k: replace(products[k], manifest={**products[k].manifest, "provenance": v}) for k, v in bad.items()}
    broken["kronos.shadow"] = replace(products["kronos.shadow"], manifest=["not", "a", "manifest"])
    g = build_graph(_inputs(products={**products, **broken}))   # builds: one bad artifact never takes down the graph
    json.dumps(g, allow_nan=False)
    for kind in bad:
        n = _node(g, f"p:{kind}")
        assert "status" not in n and n["m"]["verdict"] is None and n["m"]["art"]   # present, with its manifest
        assert not _edges_into(g, f"j:{kind}", "read")             # and nothing read from the bad entries
    assert _node(g, "p:kronos.shadow")["status"] == "missing"     # a manifest that is no mapping is no manifest
    assert _edge(g, "d:NVDA", "j:cov.league", "read") and len(_edges_from(g, "p:signals.daily", "signal")) == 21   # the rest stands
    assert provenance_symbols({"provenance": 5}) == provenance_symbols(["x"]) == []


def test_product_reads_come_from_provenance():
    g = build_graph(_inputs())
    assert _edge(g, "p:farm.sweep", "j:signals.daily", "read")
    assert _edge(g, "p:cov.league", "j:signals.overlay", "read")
    assert _edge(g, "d:NVDA", "j:cov.league", "read")


def test_reported_only_provenance_is_not_a_read():
    g = build_graph(_inputs())
    assert sorted(_edges_into(g, "j:signals.overlay", "read")) == ["p:cov.league", "p:vol.forecast_league"]
    assert _edges_into(g, "j:signals.daily", "read") == ["p:farm.sweep"]   # signals.freeze and the roster are no products


def test_the_desk_report_node_carries_only_its_run_times():
    assert set(_node(build_graph(_inputs()), "p:desk.report")["m"]) == {"kind", "finished", "stale"}


def test_the_wire_shape():
    g = build_graph(_inputs())
    json.dumps(g, allow_nan=False)                              # raises on NaN or inf (the fixture holds two NaNs)
    ids = [n["id"] for n in g["nodes"]]
    idset = set(ids)
    assert len(ids) == len(idset)
    shapes = {"feed": [set()], "book": [set()], "job": [{"h"}], "product": [{"of"}, {"beside"}],
              "result": [{"of", "i", "n"}], "strategy": [{"i", "n"}], "limit": [{"i", "n"}], "kill": [{"i", "n"}],
              "series": [{"sec", "i"}, {"of"}]}
    for n in g["nodes"]:
        assert {"id", "k", "l", "n", "ring", "at", "m"} <= set(n) <= {"id", "k", "l", "n", "ring", "at", "m", "status", "why"}
        assert n["ring"] in RINGS and set(n["at"]) in shapes[n["k"]], n["id"]
        assert isinstance(n["l"], str) and isinstance(n["n"], str) and n["l"] and n["n"], n["id"]
        assert (n["ring"] == "daily") == ("sec" in n["at"]), n["id"]
        assert n["at"].get("of", "book") in idset and n["at"].get("beside", "book") in idset, n["id"]   # names a node
    assert all(type(a) is int and type(b) is int and isinstance(k, str) for a, b, k in g["edges"])
    assert len({tuple(e) for e in g["edges"]}) == len(g["edges"])   # no edge twice


def test_the_book_and_its_limits_read_from_the_summary():
    g = build_graph(_inputs())
    _, _, roster = _static()
    assert _node(g, "book")["m"] == {
        "strategies": 23, "capital": round(sum(c.capital_fraction for c in roster.cells), 6),
        "incubation_cap": roster.incubation_cap, "canary_cap": roster.canary_cap, "vol_target": roster.vol_target,
        "positions": 5, "kill": "armed", "limits": 3, "summary_asof": "2026-10-28T20:31:07Z"}
    assert _node(g, "lim:aapl-cap")["m"] == {"kind": "gross_notional", "scope": "instrument:AAPL", "trips": False}
    assert _node(g, "lim:dd-cap")["m"]["trips"] is True
    assert _node(g, "kill")["m"] == {"state": "armed", "trips_on": ["dd-cap"]}
    assert sorted(_edges_into(g, "book", "limit")) == ["lim:aapl-cap", "lim:dd-cap", "lim:tech-cap"]
    assert _edges_into(g, "kill", "trip") == ["lim:dd-cap"]
    assert _edges_into(g, "lim:aapl-cap") == ["d:AAPL"] and not _edges_into(g, "lim:dd-cap", "read")
    assert sorted((n["at"]["i"], n["at"]["n"]) for n in g["nodes"] if n["k"] in ("limit", "kill")) == [
        (0, 4), (1, 4), (2, 4), (3, 4)]                          # one ring of four slots ...
    assert _node(g, "kill")["at"] == {"i": 3, "n": 4}           # ... the kill switch in the last
    assert _edges_from(g, "book") == ["j:desk.report"]


@pytest.mark.parametrize(("state", "halts"), [("armed", True), ("halted", True), ("disabled", False), (None, False)])
def test_the_kill_switch_halts_the_book_only_when_armed_or_halted(state, halts):
    g = build_graph(_inputs(book_summary=_summary(kill_switch=state)))
    assert _node(g, "kill")["m"]["state"] == state
    assert _edge(g, "kill", "book", "halt") is halts
    assert _edge(g, "lim:dd-cap", "kill", "trip")


def test_an_unread_count_or_trip_list_is_unread_not_zero():
    g = build_graph(_inputs(book_summary=_summary(positions=np.nan, trips_on=None)))
    assert _node(g, "book")["m"]["positions"] is None
    assert _node(g, "kill")["m"]["trips_on"] is None and _node(g, "lim:dd-cap")["m"]["trips"] is None
    assert not [e for e in g["edges"] if e[2] == "trip"]
    json.dumps(g, allow_nan=False)


def test_feeds_pull_their_jobs_and_the_first_writes_the_track():
    g = build_graph(_inputs())
    world, _, _ = _static()
    assert sorted(_edges_from(g, "feed:fred", "pull")) == ["j:ingest.fred", "j:ingest.fred_warehouse"]
    assert sorted(_edges_from(g, "j:ingest.fred_warehouse", "write")) == sorted(f"f:{s}" for s, _ in world.fred)
    assert not _edges_from(g, "j:ingest.fred", "write")
    assert sorted(_edges_from(g, "j:ingest.bars_daily", "write")) == sorted(f"d:{t}" for t in world.daily())
    assert len(_edges_from(g, "j:ingest.sec_facts", "write")) == len(world.equities)
    assert sorted(_edges_from(g, "j:ingest.factors", "write")) == ["ff:FF5", "ff:MOM"]
    assert len(_edges_from(g, "j:ingest.holdings_13f", "write")) == len(world.filers)
    assert len(_edges_from(g, "j:ingest.bars_minute", "write")) == len(world.minute)
    assert len(_edges_from(g, "j:ingest.option_snapshots", "write")) == len(world.options)
    f = _node(g, "feed:prices")
    assert f["m"] == {"who": "Yahoo, Alpaca, Stooq", "dataset": "bars_daily", "keys": 12, "asof": "2026-10-28",
                      "ok": "2026-10-28T22:41:00", "behind": 1, "stale": False}
    assert "status" not in f and f["n"] == "Prices from Yahoo, Alpaca, Stooq"


def test_a_feed_carries_the_warehouses_stale_flag():
    fresh = {k: {**v, "stale": k == "bars_daily"} for k, v in _inputs().freshness.items()}
    g = build_graph(_inputs(freshness=fresh))
    assert _node(g, "feed:prices")["m"]["stale"] is True and _node(g, "feed:fred")["m"]["stale"] is False


def test_a_feed_without_freshness_is_missing():
    g = build_graph(_inputs(freshness=None, why={"warehouse": "warehouse locked by the nightly writer"}))
    feeds = [n for n in g["nodes"] if n["k"] == "feed"]
    assert len(feeds) == len(FEEDS) == 7
    assert all(n["status"] == "missing" and n["why"] == "warehouse locked by the nightly writer" for n in feeds)
    assert _edge(g, "feed:prices", "j:ingest.bars_daily", "pull")   # the wiring is config, not freshness
    fresh = {k: v for k, v in _inputs().freshness.items() if k != "sec_facts"}
    g = build_graph(_inputs(freshness=fresh))
    assert _node(g, "feed:sec")["status"] == "missing" and _node(g, "feed:sec")["why"]
    assert "status" not in _node(g, "feed:sec13f")


def test_series_reads_follow_the_spec_exceptions():
    g = build_graph(_inputs())
    world, _, _ = _static()

    def reads(job):
        return set(_edges_into(g, f"j:{job}", "read"))

    assert reads("farm.sweep") == {f"d:{s}" for c in _daily_cells() for s in c.symbols}
    assert reads("regime.hmm") == {"d:SPY", "f:VIXCLS"}
    assert reads("vol.forecast_league") == {"d:SPY"} | {f"m:{t}" for t in world.minute}
    assert reads("vol.surface_history") == {f"o:{t}" for t in world.options}
    assert reads("ops.data_integrity") == {f"d:{t}" for t in world.daily()}
    assert reads("factors.screen") == {"ff:FF5", "ff:MOM"}
    assert reads("risk.mc_atlas") == {"d:XLK", "d:XLE"}            # its members, not its provenance's TSLA
    assert reads("risk.mc_intraday") == {"m:QQQ", "d:QQQ"}         # a bars_minute entry reads the minute track
    assert reads("cov.league") == {"d:NVDA", "d:MSFT"}             # detail.symbol and tickers, upper-cased
    assert not reads("kronos.shadow") and not reads("models.xs_lgbm")   # no symbols in their provenance


def test_provenance_symbols():
    m = {"provenance": [
        {"source": "warehouse:yahoo", "detail": {"dataset": "bars_daily", "symbol": "nvda"}},
        {"source": "warehouse:alpaca", "detail": {"dataset": "bars_minute", "symbol": "SPY", "tickers": ["QQQ"]}},
        {"source": "ohcamel-cov-league", "tickers": ["AAPL", "NVDA", 7]},
        {"source": "warehouse:bars_daily", "symbol": "IWM"},
        {"source": "ohcamel-cov-league", "detail": {"tickers": 3}},   # a count, not a list: nothing
        {"source": "reported only", "regime.hmm": "01J"}, "not a dict", {"detail": "not a dict"}]}
    assert provenance_symbols(m) == [("bars_daily", "NVDA"), ("bars_minute", "SPY"), ("bars_minute", "QQQ"),
                                     ("bars_daily", "AAPL"), ("bars_daily", "IWM")]
    assert provenance_symbols(None) == provenance_symbols({}) == provenance_symbols({"provenance": None}) == []


def test_results_come_from_their_tables():
    g = build_graph(_inputs())
    cs = _daily_cells()[:3]
    farm = [n for n in g["nodes"] if n["id"].startswith("c:")]
    assert [n["id"] for n in farm] == [f"c:{c.strategy}@{c.universe}" for c in cs]
    assert [n["at"] for n in farm] == [{"of": "p:farm.sweep", "i": i, "n": 3} for i in range(3)]
    first = farm[0]["m"]
    assert first["verdict"] == "FAIL" and first["oos_sharpe_ann"] == 0.35 and first["status"] == "ok"
    assert first["combos"] == 24 and farm[1]["m"]["dsr_farm"] is None   # a NaN is null, never NaN
    assert "verdict" not in farm[2]["m"] and farm[2]["m"]["reason"] == "no common history"   # not on the leaderboard

    for c in cs:
        assert _edge(g, f"c:{c.strategy}@{c.universe}", f"st:{c.slug}", "roster")
    assert sorted(_edges_from(g, "p:vol.forecast_league", "result")) == ["lm:gjr", "lm:har"]
    assert _node(g, "lm:har")["m"] == {"mcs_rate": None, "mean_rank": 2.1, "names": 38}
    assert _node(g, "ce:lw_constant_corr")["m"] == {"ndx100": {"vol": 0.16, "rank": 1}, "sectors": {"vol": 0.13, "rank": 1}}
    assert len(_edges_from(g, "p:cov.league", "result")) == 2
    am = sorted(_edges_from(g, "p:risk.mc_atlas", "result"))
    assert am == ["am:copula_t:1", "am:copula_t:10", "am:fhs:1", "am:fhs:10"]   # alpha 0.99 only
    assert _node(g, "am:fhs:10")["m"] == {"var": 0.1, "es": 0.15, "var_usd": 10000.0, "es_usd": 15000.0, "paths": 20000}


def test_the_daily_ring_in_section_order():
    g = build_graph(_inputs())
    world, _, _ = _static()
    at = {n["id"]: n["at"] for n in g["nodes"]}
    assert [at[f"d:{t}"] for t, _, _ in world.etfs] == [{"sec": "etf", "i": i} for i in range(len(world.etfs))]
    eq = sorted((n for n in g["nodes"] if n["at"].get("sec") == "eq"), key=lambda n: n["at"]["i"])
    assert [n["id"] for n in eq] == [f"d:{t}" for t in ORDER + sorted(set(world.equities) - set(ORDER))]
    assert [n["at"]["i"] for n in eq] == list(range(len(world.equities)))
    assert [at[f"f:{s}"] for s, _ in world.fred] == [{"sec": "fred", "i": i} for i in range(len(world.fred))]
    assert (at["ff:FF5"], at["ff:MOM"]) == ({"sec": "ff", "i": 0}, {"sec": "ff", "i": 1})
    assert [at[f"13f:{f['cik']}"] for f in world.filers] == [{"sec": "13f", "i": i} for i in range(len(world.filers))]
    assert at["s:NVDA"] == {"of": "d:NVDA"} and at["m:SPY"] == {"of": "d:SPY"} and at["o:XLK"] == {"of": "d:XLK"}
    assert {n["ring"] for n in g["nodes"] if n["k"] == "series"} == {"daily", "filings", "minute", "options"}


def test_series_carry_their_readouts():
    g = build_graph(_inputs())
    assert _node(g, "d:NVDA")["m"] == {"track": "daily", **READOUTS["NVDA"]}
    assert _node(g, "d:AAPL")["m"] == {"track": "daily", **READOUTS["AAPL"]}   # no vol, no peers: none invented
    spy = _node(g, "d:SPY")["m"]
    assert spy["unis"] and "spark" not in spy                       # an ETF with its universes; no closes, no readout
    assert _node(g, "m:SPY")["m"] == {"track": "minute"} and _node(g, "s:NVDA")["m"] == {"track": "filings"}
    world, _, _ = _static()
    f = world.filers[0]
    assert _node(g, f"13f:{f['cik']}")["m"] == {"track": "13f", "manager": f["manager"]}


def test_jobs_and_products_sit_on_the_clock():
    g = build_graph(_inputs())
    _, clock, _ = _static()
    j = _node(g, "j:farm.sweep")
    assert j["at"] == {"h": 23.0} and j["l"] == "Strategy farm"
    assert j["m"] == {"kind": "farm.sweep", "fires": [23.0], "cron": list(clock["farm.sweep"].cron), "heavy": True,
                      "mem": "L", "when": ["weekdays"]}
    assert len([n for n in g["nodes"] if n["k"] == "job"]) == len(clock)
    for kind in PROD:
        assert _edge(g, f"j:{kind}", f"p:{kind}", "publish")
        assert _node(g, f"p:{kind}")["at"] == {"of": f"j:{kind}"}
    p = _node(g, "p:farm.sweep")
    assert p["m"].items() >= {"kind": "farm.sweep", "verdict": "FAIL", "detail": "NO CELL PASSES", "code": "a1b2c3d",
                              "asof": "2026-10-28", "finished": "2026-10-28T23:41:00Z", "cpu": 61.5, "engine": "python",
                              "notes": ["farm.sweep: a note the job wrote"], "art": "01JFARMSWEEP",
                              "stale": False}.items()
    r = _node(g, "p:research")
    assert r["at"] == {"beside": "p:signals.daily"} and r["m"] == {"producer": "research-service", "strategies": 2}
    assert sorted(_edges_from(g, "p:research", "signal")) == ["st:exp_a01_spy", "st:exp_a01_tlt"]
    assert len(_edges_from(g, "p:signals.daily", "signal")) == 21
    assert _edges_from(g, "p:signals.overlay", "scale") == ["book"]
    assert len(_edges_into(g, "book", "trade")) == 23


def test_a_skipped_cells_exception_text_is_not_its_reason():
    cells = pd.DataFrame({"cell_id": ["momo@etf"], "strategy": ["momo"], "universe": ["etf"], "status": ["skipped"],
                          "reason": ["warehouse /data/warehouse.duckdb does not exist yet (no ingest has run)"],
                          "n_combos": [0]})
    farm = _product("farm.sweep", {"cells": cells})
    g = build_graph(_inputs(products={"farm.sweep": farm}))
    assert _node(g, "c:momo@etf")["m"]["reason"] is None


# ---------------------------------------------------------------- what a job wrote about itself
# what the readings parse or quote, as the jobs write them: signals/overlay.py:430, :258, :231, :240, :246, daily.py:482
KEPT = [
    "exposure_scale 0.750000 = min(1, 0.1 / 0.133333); covariance lw_constant_corr on 756 sessions, diagonal from P2's "
    "best model by QLIKE (EWMA(0.94) where P2 has none); regime p_high and core MC VaR are reported, never used",
    "no vol.forecast_league artifact: EWMA(0.94) on every name's diagonal",
    "cov.league artifact 01M5ZZ is stale (used, as the newest there is)",
    "no cov.league artifact: lw_constant_corr (Ledoit-Wolf)",
    "cov.league 01M5ZZ has no rank-1 row for core: lw_constant_corr",
    "21 written, 0 written late, 0 written again (the desk did not size the last), 2 held, 0 already present, 0 refused; "
    "parameters frozen for the month (INC-01); documents only on each strategy's own decision sessions; validation from "
    "the newest farm verdict when it tested the frozen parameters",
    "desk unreachable: keyed on the last written document (OHCAMEL_QUANT_LIVE_ENGINE_URL is unset)",
    "desk unreachable: keyed on the last written document (GET /api/research answered 503, not the route's body)",
    "Kronos runtime unavailable (no research/kronos venv): tonight's ledger has the incumbents only",
    "regime gate measured on the selected configuration's full walk-forward OOS record (I-Q01-6)",
    "holdout evidence predates methodology v2 / config ab12cd; re-evaluating the holdout is an owner decision",
    "core: no live mark for NVDA, AMD (held at the previous close)",
    *EXECUTION_NOTES,
]


@pytest.mark.parametrize("note", KEPT)
def test_a_real_caveat_is_kept_word_for_word(note):
    assert public_notes([note]) == [note]


@pytest.mark.parametrize("note", [
    "see /data/warehouse.duckdb", "members skipped: '/tmp/x'", "read ~/secrets", r"C:\jobs\x failed", "at /signals",
    "Traceback (most recent call last):", "boom: ValueError: nope", "Exception in thread", 'File "job.py", line 1',
    "failed (TimeoutExpired): ", "(ReadTimeout: timed out)", "[Errno 2] No such file", 'config {"a": 1}', "{\n}",
    "warehouse wh.duckdb: locked", "unreadable (bad json)",
])
def test_a_note_with_internal_detail_is_withheld(note):
    assert public_note(note) is None and public_notes([note]) == [WITHHELD]


def test_withheld_notes_are_one_sentence_where_the_first_stood():
    assert public_notes(["kept", "at /a/b", "also kept", "at /c/d", "Traceback"]) == ["kept", WITHHELD, "also kept"]


def test_a_note_is_capped_at_a_word():
    long = " ".join(["word"] * 200)
    out = public_note(long)
    assert len(out) <= NOTE_CAP and out.endswith("word…") and long.startswith(out[:-1])
    assert public_note("x" * (NOTE_CAP + 50)) == "x" * (NOTE_CAP - 1) + "…"   # one long word is cut where it stands
    assert public_note("y" * NOTE_CAP) == "y" * NOTE_CAP


@pytest.mark.parametrize("notes", [None, "a string", {"a": 1}, 7])
def test_notes_that_are_not_a_list_are_none(notes):
    assert public_notes(notes) == []


def test_a_note_that_is_not_text_is_dropped():
    assert public_notes(["kept", 7, None, "", {"a": "/etc/passwd"}]) == ["kept"]


def test_the_products_notes_on_the_wire_are_filtered():
    farm = _product("farm.sweep", {}, notes=["a caveat", "EXP-K01 config {\"model_revision\": \"abc\"}"])
    assert _node(build_graph(_inputs(products={"farm.sweep": farm})), "p:farm.sweep")["m"]["notes"] == ["a caveat", WITHHELD]


# ---------------------------------------------------------------- product lenses and the clock's extras
def _lens_tables():
    """One small, awkward frame per lens (unsorted, NaN, inf, unrounded, a float where a count belongs):
    ``{kind: {table: frame}}``, each holding exactly the tables ``LENS_TABLES`` says the lens reads."""
    board = pd.DataFrame({
        "cell_id": ["a@x", "b@x", "c@x", "d@x", "e@x"], "verdict": ["FAIL", "PASS", "FAIL", "FAIL", "FAIL"],
        "detail": ["DSR BELOW 0.95"] * 5,                         # a column the lens leaves out
        "dsr_farm": [0.41, 0.9712345, np.nan, 0.2, 0.3], "psr": [0.62, 0.99, 0.48, 0.5, 0.4],
        "oos_sharpe_ann": [0.35, 1.23456789, np.nan, -0.1, np.inf], "pbo": [0.3, 0.1, 0.7, np.inf, 0.2],
        "n_combos": [24, 24, 24, 24, 24]})
    vol = pd.DataFrame({"model": ["gjr", "har"], "mcs_rate": [0.8123456, np.nan], "mean_rank": [1.4, 2.1],
                        "names": [40.0, 38.0]})                  # whole floats are counts
    league = pd.DataFrame({"universe": ["ndx100", "ndx100", "sectors"], "rank": [2, 1, 1],
                           "estimator": ["sample", "lw_constant_corr", "sample"],
                           "realized_vol": [0.123456, 0.16, np.nan]})
    atlas = pd.DataFrame([{"book": "core", "method": m, "horizon": h, "alpha": a, "var": 0.01 * h * k,
                           "es": 0.015 * h * k, "var_usd": 1000.0 * h, "es_usd": 1500.0 * h, "n_paths": 20000}
                          for m in ("fhs", "copula_t") for h in (1, 10) for a, k in ((0.95, 0.5), (0.99, 1))])
    deciles = pd.DataFrame({"decile": [1, 2, 3], "mean_next_month_return": [-0.0123456, -0.00001, 0.0212345]})
    probs = pd.DataFrame({"date": pd.to_datetime(["2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23"]),
                          "p_high": [np.nan, 0.1234567, 0.8765432, np.nan], "p_low": [np.nan, 0.9, 0.1, np.nan]})
    history = pd.DataFrame({
        "underlying": ["SPY", "QQQ", "SPY", "AAPL"], "asof": ["2026-10-28", "2026-10-28", "2026-10-27", "2026-10-28"],
        "atm_iv_30d": [0.1834567, 0.2, 0.17, np.nan], "n_slices": [11.0, 12.0, 10.0, 9.0],
        "spot": [580.0, 500.0, 579.0, 230.0]})                    # SPY's newest row is its first listed
    return {"farm.sweep": {"leaderboard": board}, "vol.forecast_league": {"summary": vol},
            "cov.league": {"league": league}, "risk.mc_atlas": {"summary": atlas},
            "models.xs_lgbm": {"holdout_deciles": deciles}, "regime.hmm": {"probs": probs},
            "vol.surface_history": {"history": history}}


def _lensed(kind, tables):
    """The product for ``kind``. The farm's trials are counted, not carried (the loader reads the
    parquet metadata): 1008 rows listed, no table."""
    p = _product(kind, tables)
    return replace(p, rows={**p.rows, "trials": 1008}) if kind == "farm.sweep" else p


def test_each_lens_reads_its_table():
    t = _lens_tables()
    assert set(t) == set(LENS_TABLES) and all(set(t[k]) == set(LENS_TABLES[k]) for k in t)   # all it reads, only that
    lens = {k: product_lens(k, _lensed(k, t[k])) for k in t}
    assert all(why is None for _, why in lens.values())
    assert lens["farm.sweep"][0] == {"cells": 5, "trials": 1008, "board": [   # best out-of-sample Sharpe first
        ["b@x", "PASS", 1.2346, 0.9712, 0.99, 0.1, 24], ["a@x", "FAIL", 0.35, 0.41, 0.62, 0.3, 24],
        ["d@x", "FAIL", -0.1, 0.2, 0.5, None, 24],                # an infinite figure is none
        ["c@x", "FAIL", None, None, 0.48, 0.7, 24],                # a cell with no Sharpe is last, and so is
        ["e@x", "FAIL", None, 0.3, 0.4, 0.2, 24]]}                 # one whose Sharpe is infinite (a table order tie)
    assert lens["vol.forecast_league"][0] == {"models": [["gjr", 0.8123, 1.4, 40], ["har", None, 2.1, 38]]}
    assert lens["cov.league"][0] == {"estimators": [["sample", "ndx100", 0.1235, 2],
                                                    ["lw_constant_corr", "ndx100", 0.16, 1],
                                                    ["sample", "sectors", None, 1]]}
    assert lens["risk.mc_atlas"][0] == {"measures": [["fhs", 1, 0.01, 0.015, 20000], ["fhs", 10, 0.1, 0.15, 20000],
                                                     ["copula_t", 1, 0.01, 0.015, 20000],
                                                     ["copula_t", 10, 0.1, 0.15, 20000]]}   # alpha 0.99 only
    assert lens["models.xs_lgbm"][0] == {"deciles": [[1, -0.0123], [2, 0.0], [3, 0.0212]]}
    assert "-0.0]" not in json.dumps(lens["models.xs_lgbm"][0])   # a rounded negative zero is a zero
    assert lens["regime.hmm"][0] == {"date": "2026-10-16", "p_high": 0.8765, "weeks": 4}   # the last non-null p_high
    assert lens["vol.surface_history"][0] == {"underlying": "SPY", "asof": "2026-10-28", "atm30": 0.1835,
                                              "slices": 11, "names": 3}
    for payload, _ in lens.values():
        json.dumps(payload, allow_nan=False)                     # plain JSON: no NaN, no inf, no numpy
    assert type(lens["farm.sweep"][0]["trials"]) is int and type(lens["vol.forecast_league"][0]["models"][0][3]) is int
    assert type(lens["vol.surface_history"][0]["slices"]) is int


def test_the_farm_counts_its_trials_from_the_row_count_and_never_reads_the_table():
    p = _lensed("farm.sweep", _lens_tables()["farm.sweep"])
    assert "trials" not in p.tables and p.rows["trials"] == 1008
    assert product_lens("farm.sweep", p)[0]["trials"] == 1008
    assert product_lens("farm.sweep", replace(p, rows={})) == (None, "no trials table")   # not listed: no count to give
    assert product_lens("farm.sweep", replace(p, rows={"trials": 0}))[0]["trials"] == 0   # an empty table is a real zero


def test_a_lens_does_not_change_its_table():
    t = _lens_tables()["farm.sweep"]
    before = t["leaderboard"].copy()
    product_lens("farm.sweep", _lensed("farm.sweep", t))
    pd.testing.assert_frame_equal(t["leaderboard"], before)       # the board is sorted on a copy


def test_a_lens_with_a_missing_column_is_omitted_with_the_reason():   # Review Focus 5
    board = _lens_tables()["farm.sweep"]["leaderboard"]
    broken = _lensed("farm.sweep", {"leaderboard": board.drop(columns="oos_sharpe_ann")})
    assert product_lens("farm.sweep", broken) == (None, "leaderboard has no column oos_sharpe_ann")
    gone = _lensed("farm.sweep", {"leaderboard": board.drop(columns=["pbo", "psr"])})
    assert product_lens("farm.sweep", gone) == (None, "leaderboard has no columns psr, pbo")   # all of them, in the lens's order
    farm = _inputs().products["farm.sweep"]                        # the fixture's farm, its leaderboard cut down
    cut = replace(farm, tables={**farm.tables, "leaderboard": farm.tables["leaderboard"].drop(columns="oos_sharpe_ann")})
    g = build_graph(_inputs(products={**_inputs().products, "farm.sweep": cut}))
    n = _node(g, "p:farm.sweep")
    assert "status" not in n and n["m"]["verdict"] == "FAIL"      # the node stays, with its manifest
    assert "lens" not in n["m"] and n["m"]["lens_why"] == LENS_WHY         # no table or column is named on the page
    assert len(_edges_from(g, "p:farm.sweep", "result")) == 3     # and its cells, which the cells table gives
    assert "NaN" not in json.dumps(g)                              # the fixture's own NaNs went out as null
    json.dumps(g, allow_nan=False)


@pytest.mark.parametrize(("kind", "column"), [
    ("farm.sweep", "cell_id"), ("farm.sweep", "n_combos"), ("vol.forecast_league", "mcs_rate"),
    ("vol.forecast_league", "names"), ("cov.league", "estimator"), ("cov.league", "rank"),
    ("risk.mc_atlas", "alpha"), ("risk.mc_atlas", "n_paths"), ("models.xs_lgbm", "mean_next_month_return"),
    ("regime.hmm", "p_high"), ("regime.hmm", "date"), ("vol.surface_history", "underlying"),
    ("vol.surface_history", "n_slices"), ("vol.surface_history", "atm_iv_30d")])
def test_each_lens_names_the_column_it_is_missing(kind, column):
    name = LENS_TABLES[kind][0]
    table = _lens_tables()[kind][name].drop(columns=column)
    assert product_lens(kind, _lensed(kind, {name: table})) == (None, f"{name} has no column {column}")


@pytest.mark.parametrize("kind", sorted(LENS_TABLES))
def test_a_lens_without_its_table_or_rows_is_omitted_with_the_reason(kind):
    name = LENS_TABLES[kind][0]
    table = _lens_tables()[kind][name]
    assert product_lens(kind, _lensed(kind, {})) == (None, f"no {name} table")
    assert product_lens(kind, replace(_lensed(kind, {}), tables={name: None})) == (None, f"no {name} table")   # not a frame
    assert product_lens(kind, _lensed(kind, {name: table.iloc[:0]})) == (None, f"{name} has no rows")
    assert product_lens(kind, _lensed(kind, {name: pd.DataFrame()})) == (None, f"{name} has no rows")


def test_a_lens_with_nothing_to_show_says_what_is_missing():
    t = _lens_tables()
    atlas = t["risk.mc_atlas"]["summary"]
    assert product_lens("risk.mc_atlas", _lensed("risk.mc_atlas", {"summary": atlas[atlas["alpha"] != 0.99]})) == (
        None, "summary has no alpha 0.99 rows")
    probs = t["regime.hmm"]["probs"].assign(p_high=np.nan)
    assert product_lens("regime.hmm", _lensed("regime.hmm", {"probs": probs})) == (None, "probs has no p_high value")
    hist = t["vol.surface_history"]["history"]
    no_spy = _lensed("vol.surface_history", {"history": hist[hist["underlying"] != "SPY"]})
    assert product_lens("vol.surface_history", no_spy) == (None, "history has no SPY row")


def test_a_lens_reads_the_dates_as_the_artifacts_write_them():
    t = _lens_tables()
    probs = t["regime.hmm"]["probs"].assign(date=["2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23"])   # as text
    assert product_lens("regime.hmm", _lensed("regime.hmm", {"probs": probs}))[0]["date"] == "2026-10-16"
    hist = t["vol.surface_history"]["history"].assign(asof=lambda d: pd.to_datetime(d["asof"]))              # as dates
    assert product_lens("vol.surface_history", _lensed("vol.surface_history", {"history": hist}))[0]["asof"] == "2026-10-28"


def test_a_lens_never_raises():
    hist = _lens_tables()["vol.surface_history"]["history"].astype({"underlying": object})
    hist.at[1, "underlying"] = ["QQQ", "IWM"]                      # a value no one can count distinct: schema drift
    odd = _lensed("vol.surface_history", {"history": hist})
    lens, why = product_lens("vol.surface_history", odd)
    assert lens is None and why == "vol.surface_history lens could not be read (TypeError)"
    g = build_graph(_inputs(products={**_inputs().products, "vol.surface_history": odd}))
    assert _node(g, "p:vol.surface_history")["m"]["lens_why"] == LENS_WHY   # and the graph builds


def test_a_kind_with_no_lens_has_neither_a_lens_nor_a_reason():
    assert set(LENS_TABLES) == {"farm.sweep", "vol.forecast_league", "cov.league", "risk.mc_atlas", "models.xs_lgbm",
                                "regime.hmm", "vol.surface_history"}
    for kind in set(PROD) - set(LENS_TABLES):
        assert product_lens(kind, _product(kind, {"summary": pd.DataFrame({"x": [1]})})) == (None, None)
    assert product_lens("desk.report", _desk("usable")) == (None, None)   # the desk report is never lensed


def test_product_nodes_carry_their_lens_or_the_reason():
    t = _lens_tables()
    t.pop("models.xs_lgbm")                                        # left as the fixture has it: present, no deciles table
    products = {**_inputs().products, **{k: _lensed(k, v) for k, v in t.items()}}
    g = build_graph(_inputs(products=products))
    for kind in t:
        m = _node(g, f"p:{kind}")["m"]
        assert m["lens"] == product_lens(kind, products[kind])[0] and "lens_why" not in m, kind
    assert _node(g, "p:farm.sweep")["m"]["lens"]["cells"] == 5
    assert _node(g, "p:regime.hmm")["m"]["lens"] == {"date": "2026-10-16", "p_high": 0.8765, "weeks": 4}
    m = _node(g, "p:models.xs_lgbm")["m"]
    assert "lens" not in m and m["lens_why"] == LENS_WHY
    for kind in set(PROD) - set(LENS_TABLES):
        assert not {"lens", "lens_why"} & set(_node(g, f"p:{kind}")["m"]), kind
    assert set(_node(g, "p:desk.report")["m"]) == {"kind", "finished", "stale"}
    gone = _node(build_graph(_inputs(drop="farm.sweep")), "p:farm.sweep")
    assert gone["status"] == "missing" and gone["m"] == {"kind": "farm.sweep"}   # no artifact: no lens and no reason


def test_the_default_fixture_lenses_serialise_without_nan():
    g = build_graph(_inputs())
    farm = _node(g, "p:farm.sweep")["m"]["lens"]
    assert farm["trials"] == 48 and farm["cells"] == 2 and farm["board"][0][2] == 0.35   # sorted: 0.35 before -0.1
    assert _node(g, "p:vol.forecast_league")["m"]["lens"]["models"][1][1] is None        # a NaN mcs_rate is null
    json.dumps(g, allow_nan=False)


def test_extras_list_every_fire():
    inp = _inputs()
    x = extras(inp)
    assert set(x) == {"fires"}
    assert len(x["fires"]) == sum(len(j.fires) for j in inp.clock.values()) > len(inp.clock)   # the overlay fires often
    assert x["fires"] == sorted(x["fires"])                        # by kind, then hour
    assert ["farm.sweep", 23.0] in x["fires"] and ["ingest.bars_daily", 18.5] in x["fires"]
    overlay = [h for k, h in x["fires"] if k == "signals.overlay"]
    assert overlay == sorted(round(h, 3) for h in inp.clock["signals.overlay"].fires) and len(overlay) > 1
    json.dumps(x, allow_nan=False)


def test_extras_round_hours_to_three_places_and_keep_repeats():
    clock = {"b.job": JobClock("b.job", (9.6666667, 9.6666667, 18.3333333), 14.0, ("x",), False, "S", ("daily",)),
             "a.job": JobClock("a.job", (23.0,), 23.0, ("y",), True, "L", ("weekdays",))}
    assert extras(replace(_inputs(), clock=clock)) == {"fires": [["a.job", 23.0], ["b.job", 9.667], ["b.job", 9.667],
                                                                 ["b.job", 18.333]]}
    assert extras(replace(_inputs(), clock={})) == {"fires": []}
