"""signals.daily (Task 14): every farm cell of INC-01's roster as a contract-v1
document, computed with the backtest's own strategy function at the last close,
under parameters frozen once a month (INC-01, *The parameter-freeze rule*).

Offline, on the fixture warehouse (warehouse/fixture_db.py: the nine committed
fixture ETFs, 2016-06-01..2026-06-01, 2514 sessions each) and on farm.sweep
artifacts published the way the worker publishes them (lane_m_harness). The
test roster names only fixture tickers -- every one of them in
book.example.sexp's positions -- plus one name (VTI) that is not."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from lane_m_harness import publish_artifact

from ohcamel_quant.backtest import strategies
from ohcamel_quant.config import Settings
from ohcamel_quant.data.market import Dataset, MarketData
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.jobs.db import connect
from ohcamel_quant.signals import daily, freeze
from ohcamel_quant.signals.contract import V1_SCHEMA_PATH, params_hash, validate
from ohcamel_quant.signals.roster import Cell
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

LAST = date(2026, 6, 1)  # every fixture's last session
# The test cells' universes, standing in for universes.json (whose groups name non-fixture ETFs).
UNIVERSES = {"eq3": ["SPY", "QQQ", "IWM"], "eq2": ["SPY", "QQQ"], "flat": ["SPY"], "bonds": ["TLT", "IEF", "GLD"],
             "outside": ["SPY", "VTI"], "one": ["SPY"]}

ROSTER = """\
version: 1
incubation_cap: 0.60
canary_cap: 0.10
vol_target: 0.10
strategies:
  - slug: exp_a01_spy
    producer: research-service
    capital_fraction: 0.025
  - slug: farm_sma_trend_fixture
    producer: signals.daily
    strategy: sma_trend
    universe: eq3
    symbols: [SPY, QQQ, IWM]
    capital_fraction: 0.025
  - slug: farm_sma_trend_mismatch
    producer: signals.daily
    strategy: sma_trend
    universe: eq3
    symbols: [SPY, IWM]
    capital_fraction: 0.025
  - slug: farm_static_mix_fixture
    producer: signals.daily
    strategy: static_mix
    universe: eq2
    symbols: [SPY, QQQ]
    capital_fraction: 0.025
  - slug: farm_static_mix_flat
    producer: signals.daily
    strategy: static_mix
    universe: flat
    symbols: [SPY]
    capital_fraction: 0.025
  - slug: farm_tsmom_fixture
    producer: signals.daily
    strategy: tsmom
    universe: bonds
    symbols: [TLT, IEF, GLD]
    capital_fraction: 0.025
  - slug: farm_tsmom_outside
    producer: signals.daily
    strategy: tsmom
    universe: outside
    symbols: [SPY, VTI]
    capital_fraction: 0.025
"""


def _farm_tables(rows: list[dict]) -> dict[str, pd.DataFrame]:
    """A farm.sweep artifact's cells and leaderboard, as products/farm.py writes them:
    ``selected_params`` is the JSON of the chosen combo, the verdict lives on the leaderboard."""
    cells = pd.DataFrame([{"cell_id": r["cell_id"], "strategy": r["cell_id"].split("@")[0],
                           "universe": r["cell_id"].split("@")[1], "status": r.get("status", "ok"),
                           "selected_params": r.get("selected_params"), "psr": r.get("psr"),
                           "pbo": r.get("pbo")} for r in rows])
    board = pd.DataFrame([{"cell_id": r["cell_id"], "verdict": r["verdict"], "dsr_farm": r.get("dsr")}
                          for r in rows if r.get("status", "ok") == "ok"])
    return {"cells": cells, "leaderboard": board}


FARM_ROWS = [
    # sma_trend@fixture: a FAIL row with every gate input; its validation block must say fail.
    {"cell_id": "sma_trend@eq3", "verdict": "FAIL", "selected_params": json.dumps({"months": 10}),
     "dsr": 0.41, "psr": 0.62, "pbo": 0.55},
    # static_mix@fixture: 0.8 + 0.6 = 1.4 gross, so the producer scales it to 1.
    {"cell_id": "static_mix@eq2", "verdict": "PASS",
     "selected_params": json.dumps({"weights": {"SPY": 0.8, "QQQ": 0.6}}), "dsr": 0.97, "psr": 0.99, "pbo": 0.1},
    # static_mix@flat: a zero weight -- flat, which is a valid document. No psr or pbo on its row.
    {"cell_id": "static_mix@flat", "verdict": "FAIL",
     "selected_params": json.dumps({"weights": {"SPY": 0.0}})},
    # tsmom@outside has params, but names VTI: refused before anything is read.
    {"cell_id": "tsmom@outside", "verdict": "FAIL", "selected_params": json.dumps({"lookback": 252})},
    # tsmom@bonds is absent: no params, no last month -> refused with the reason.
]


def _ctx(world: dict, *, signals_dir: Path | None) -> JobContext:
    md = MarketData(Settings(offline=True, data_dir=world["tmp"], warehouse_path=world["wh"],
                             signals_dir=signals_dir))
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None,
                      cancelled_fn=lambda: False, _market=md)


def _run(world: dict, *, signals_dir: Path | None) -> object:
    ctx = _ctx(world, signals_dir=signals_dir)
    try:
        return daily.run({}, ctx)
    finally:
        ctx.close()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("signals_daily")
    mp = pytest.MonkeyPatch()
    db, root = tmp / "jobs.sqlite", tmp / "artifacts"
    roster = tmp / "roster.yaml"
    roster.write_text(ROSTER)
    mp.setattr(freeze, "DB_PATH", db)
    mp.setattr(daily, "ROSTER_PATH", roster)
    mp.setattr(daily, "_today", lambda: date(2026, 6, 1))
    mp.setattr(daily, "_universe", UNIVERSES.__getitem__)
    farm_id, _ = publish_artifact(db, root, "farm.sweep", _farm_tables(FARM_ROWS), verdict="PASS",
                                  finished=datetime(2026, 5, 29, 23, 0, tzinfo=UTC))
    w = {"tmp": tmp, "db": db, "root": root, "wh": build_fixture_warehouse(tmp / "wh.duckdb"),
         "signals": tmp / "signals", "farm_id": farm_id}
    w["spec"] = _run(w, signals_dir=w["signals"])
    yield w
    mp.undo()


def _doc(world: dict, slug: str) -> dict:
    return json.loads((world["signals"] / f"{slug}-{LAST}.json").read_text())


def _refused(spec) -> dict[str, str]:
    return dict(zip(spec.tables["refused"]["slug"], spec.tables["refused"]["reason"], strict=True))


def _emitted(spec) -> pd.DataFrame:
    return spec.tables["emitted"].set_index("slug")


def test_two_cells_emit_two_documents_that_validate_against_v1(world):
    names = sorted(p.name for p in world["signals"].glob("*.json"))
    assert names == [f"farm_sma_trend_fixture-{LAST}.json", f"farm_static_mix_fixture-{LAST}.json",
                     f"farm_static_mix_flat-{LAST}.json"]
    for slug in ("farm_sma_trend_fixture", "farm_static_mix_fixture"):
        doc = _doc(world, slug)
        validate(doc, V1_SCHEMA_PATH)
        assert doc["strategy"] == slug and doc["schema_version"] == 1
    assert not list(world["signals"].glob(".*"))  # no temp file survives
    assert set(_emitted(world["spec"]).index) == {"farm_sma_trend_fixture", "farm_static_mix_fixture",
                                                   "farm_static_mix_flat"}


def test_as_of_is_the_last_session(world):
    # The fixtures' last common session is 2026-06-01; the targets are that row's.
    assert _doc(world, "farm_sma_trend_fixture")["as_of"] == "2026-06-01"
    assert _emitted(world["spec"]).loc["farm_sma_trend_fixture", "as_of"] == "2026-06-01"
    assert world["spec"].data_asof == "2026-06-01"


def test_sequence_counts_spy_sessions(world):
    # SPY's fixture runs 2016-06-01..2026-06-01 with 2514 rows in bars_daily, so the
    # session ordinal at as_of 2026-06-01 is 2514 -- the same for every cell that day.
    for slug in ("farm_sma_trend_fixture", "farm_static_mix_fixture", "farm_static_mix_flat"):
        assert _doc(world, slug)["sequence"] == 2514
        assert _emitted(world["spec"]).loc[slug, "sequence"] == 2514


def test_gross_scaled_recorded(world):
    # static_mix {"SPY": 0.8, "QQQ": 0.6}: gross 0.8 + 0.6 = 1.4 > 1, so each weight is
    # divided by 1.4: SPY 0.8 / 1.4 = 0.571428..., QQQ 0.6 / 1.4 = 0.428571...; gross 1.
    doc = _doc(world, "farm_static_mix_fixture")
    w = {t["symbol"]: t["weight"] for t in doc["targets"]}
    assert w == pytest.approx({"QQQ": 0.6 / 1.4, "SPY": 0.8 / 1.4}, abs=1e-12)
    assert sum(abs(v) for v in w.values()) == pytest.approx(1.0, abs=1e-12)
    assert doc["params_hash"] == params_hash({"weights": {"SPY": 0.8, "QQQ": 0.6}, "gross_scaled": True})
    em = _emitted(world["spec"])
    assert bool(em.loc["farm_static_mix_fixture", "gross_scaled"]) is True
    assert bool(em.loc["farm_sma_trend_fixture", "gross_scaled"]) is False
    # sma_trend holds 1/3 of each name above its SMA: gross <= 1, never scaled, its params as frozen.
    sma = _doc(world, "farm_sma_trend_fixture")
    assert sma["params_hash"] == params_hash({"months": 10})
    assert sum(abs(t["weight"]) for t in sma["targets"]) <= 1.0 + 1e-12


def test_freeze_reads_selected_params_once_per_month(tmp_path, monkeypatch):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(freeze, "DB_PATH", db)
    cell = Cell("farm_sma_trend_fixture", "signals.daily", "sma_trend", "fixture", ("SPY",), 0.025)

    def farm(months: int, day: int, status: str = "ok", verdict: str = "FAIL") -> None:
        publish_artifact(db, root, "farm.sweep", _farm_tables(
            [{"cell_id": "sma_trend@fixture", "status": status, "verdict": verdict,
              "selected_params": None if status != "ok" else json.dumps({"months": months})}]),
            finished=datetime(2026, 7, day, 23, 0, tzinfo=UTC), params={"run": day})

    def n_freezes() -> int:
        conn = connect(db)
        try:
            return conn.execute("SELECT COUNT(*) FROM artifacts WHERE kind = 'signals.freeze'").fetchone()[0]
        finally:
            conn.close()

    farm(10, 1)
    july = date(2026, 7, 1)
    assert freeze.frozen_params(None, month=july, cells=[cell]) == ({cell.slug: {"months": 10}}, [])
    farm(12, 2)  # the farm re-selects nightly; inside the month nothing changes
    assert freeze.frozen_params(None, month=date(2026, 7, 20), cells=[cell]) == ({cell.slug: {"months": 10}}, [])
    assert n_freezes() == 1
    # August: the newest farm artifact (months 12) is read once, into a second freeze.
    assert freeze.frozen_params(None, month=date(2026, 8, 3), cells=[cell]) == ({cell.slug: {"months": 12}}, [])
    assert n_freezes() == 2
    # September: the farm reports the cell SKIPPED -- August's parameters are kept.
    farm(0, 3, status="skipped", verdict="SKIPPED")
    got, refused = freeze.frozen_params(None, month=date(2026, 9, 1), cells=[cell])
    assert (got, refused) == ({cell.slug: {"months": 12}}, [])
    assert n_freezes() == 3


def test_cell_without_params_is_refused_with_reason(world):
    reason = _refused(world["spec"])["farm_tsmom_fixture"]
    assert "not emitted" in reason and "no 2026-05 parameters" in reason
    assert not (world["signals"] / f"farm_tsmom_fixture-{LAST}.json").exists()


def test_symbol_outside_the_book_is_refused(world):
    assert _refused(world["spec"])["farm_tsmom_outside"] == "symbol VTI is not in the book"
    assert not list(world["signals"].glob("farm_tsmom_outside-*"))
    assert "SPY" in daily.book_symbols() and "VTI" not in daily.book_symbols()


def test_validation_fail_from_a_fail_row(world):
    block = _doc(world, "farm_sma_trend_fixture")["validation"]
    assert block == {"status": "fail", "gates_version": "2026-09-02", "dsr": 0.41, "psr": 0.62, "pbo": 0.55,
                     "manifest": world["farm_id"]}
    assert _doc(world, "farm_static_mix_fixture")["validation"]["status"] == "pass"
    # Absent dsr/psr/pbo are null, never invented.
    flat = _doc(world, "farm_static_mix_flat")["validation"]
    assert flat["status"] == "fail" and flat["dsr"] is None and flat["psr"] is None and flat["pbo"] is None
    cell = Cell("x_y", "signals.daily", "sma_trend", "fixture", ("SPY",), 0.025)
    rows = pd.DataFrame([{"cell_id": "sma_trend@fixture", "verdict": "FAIL", "dsr": None, "psr": 0.5,
                          "pbo": float("nan")}])
    assert daily.validation_for(cell, rows, manifest="A1") == {
        "status": "fail", "gates_version": "2026-09-02", "dsr": None, "psr": 0.5, "pbo": None, "manifest": "A1"}
    # Anything but PASS or FAIL, or no row at all, is unvalidated.
    assert daily.validation_for(cell, rows.assign(verdict="INSUFFICIENT DATA"), manifest="A1")["status"] \
        == "unvalidated"
    assert daily.validation_for(cell, rows.iloc[:0], manifest="A1") == {
        "status": "unvalidated", "gates_version": "2026-09-02", "dsr": None, "psr": None, "pbo": None,
        "manifest": None}


def test_research_service_rows_are_skipped(world):
    spec = world["spec"]
    assert "exp_a01_spy" not in set(spec.tables["emitted"]["slug"]) | set(spec.tables["refused"]["slug"])
    assert not list(world["signals"].glob("exp_a01_spy*"))


def test_flat_weights_emit_an_empty_targets_list(world):
    doc = _doc(world, "farm_static_mix_flat")
    validate(doc, V1_SCHEMA_PATH)
    assert doc["targets"] == []
    assert _emitted(world["spec"]).loc["farm_static_mix_flat", "n_targets"] == 0


def test_re_run_is_idempotent(world):
    path = world["signals"] / f"farm_sma_trend_fixture-{LAST}.json"
    before = path.read_bytes()
    spec = _run(world, signals_dir=world["signals"])
    assert path.read_bytes() == before  # computed_at would differ had it been rewritten
    em = _emitted(spec)
    assert set(em["status"]) == {"already_present"}
    assert set(_emitted(world["spec"])["status"]) == {"written"}
    conn = connect(world["db"])
    try:  # the month's freeze is read, not taken again
        assert conn.execute("SELECT COUNT(*) FROM artifacts WHERE kind = 'signals.freeze'").fetchone()[0] == 1
    finally:
        conn.close()


def test_no_signals_dir_refuses_the_run(world):
    before = sorted(world["signals"].iterdir())
    spec = _run(world, signals_dir=None)
    assert spec.tables["emitted"].empty
    assert "OHCAMEL_QUANT_SIGNALS_DIR" in spec.tables["refused"]["reason"].iloc[0]
    assert spec.verdict == "INSUFFICIENT DATA" and any("OHCAMEL_QUANT_SIGNALS_DIR" in n for n in spec.notes)
    assert sorted(world["signals"].iterdir()) == before  # nothing written, anywhere it knows of


# ------------------------------------------------------------------ fix round 1
_HEAD = "version: 1\nincubation_cap: 0.60\ncanary_cap: 0.10\nvol_target: 0.10\nstrategies:\n"


def _row(slug: str, strategy: str, universe: str) -> str:
    syms = ", ".join(UNIVERSES[universe])
    return (f"  - slug: {slug}\n    producer: signals.daily\n    strategy: {strategy}\n    universe: {universe}\n"
            f"    symbols: [{syms}]\n    capital_fraction: 0.025\n")


class _CutMarket(MarketData):
    """The fixture market as it stood at the close of ``end``: the panel stops there."""

    def __init__(self, settings: Settings, end: date) -> None:
        super().__init__(settings)
        self.end = end

    def prices(self, tickers, start=None, end=None, field="adj_close", *, warehouse_first=False):
        ds = super().prices(tickers, start, end, field, warehouse_first=warehouse_first)
        return Dataset(ds.data.loc[: pd.Timestamp(self.end)], ds.provenance)


@pytest.fixture(scope="module")
def wh(tmp_path_factory):
    return build_fixture_warehouse(tmp_path_factory.mktemp("wh") / "wh.duckdb")


def _mini(tmp: Path, monkeypatch, wh: Path, rows: str, farm_rows: list[dict]):
    """A roster, a farm artifact and a signals directory of their own; returns run_at(end)."""
    db = tmp / "jobs.sqlite"
    (tmp / "roster.yaml").write_text(_HEAD + rows)
    monkeypatch.setattr(freeze, "DB_PATH", db)
    monkeypatch.setattr(daily, "ROSTER_PATH", tmp / "roster.yaml")
    monkeypatch.setattr(daily, "_today", lambda: date(2026, 5, 29))
    monkeypatch.setattr(daily, "_universe", UNIVERSES.__getitem__)
    publish_artifact(db, tmp / "artifacts", "farm.sweep", _farm_tables(farm_rows), verdict="FAIL",
                     finished=datetime(2026, 5, 1, 23, 0, tzinfo=UTC))
    sig = tmp / "signals"

    def run_at(end: date):
        md = _CutMarket(Settings(offline=True, data_dir=tmp, warehouse_path=wh, signals_dir=sig), end)
        ctx = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None,
                         cancelled_fn=lambda: False, _market=md)
        try:
            return daily.run({}, ctx)
        finally:
            ctx.close()

    return run_at, sig


def _fake(monkeypatch, key: str, fn, rebalance: str) -> None:
    """A strategy registered for one test: static_mix's spec with its own function and cadence."""
    monkeypatch.setitem(strategies.STRATEGIES, key, dataclasses.replace(
        strategies.STRATEGIES["static_mix"], key=key, fn=fn, default_rebalance=rebalance))


def _wctx(tmp: Path, wh: Path) -> JobContext:
    md = MarketData(Settings(offline=True, data_dir=tmp, warehouse_path=wh))
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None,
                      cancelled_fn=lambda: False, _market=md)


def _delete_spy(wh: Path, where: str) -> None:
    from ohcamel_quant.warehouse.db import open_rw

    with open_rw(wh) as con:
        con.execute(f"DELETE FROM bars_daily WHERE ticker = 'SPY' AND {where}")


def test_a_missing_spy_day_still_yields_a_strictly_increasing_sequence(tmp_path):
    wh = build_fixture_warehouse(tmp_path / "wh.duckdb")
    # The 2026-05-29 document went out with sequence 2513 (SPY's 2513th session). Then a
    # re-fetch loses SPY's 05-29 bar: the count through 06-01 is 2514 - 1 = 2513, the same
    # number again -- R5 would reject it. max(2513, 2513 + 1) = 2514.
    _delete_spy(wh, "date = DATE '2026-05-29'")
    ctx = _wctx(tmp_path, wh)
    try:
        assert daily._sequence(ctx, date(2026, 6, 1), {"sequence": 2513}) == 2514
        # And a session SPY has no bar for is refused, never numbered.
        with pytest.raises(daily.CellRefused, match="SPY has no bar on 2026-05-29"):
            daily._sequence(ctx, date(2026, 5, 29), {"sequence": 2512})
    finally:
        ctx.close()


def test_a_shrunken_count_yields_last_plus_one(tmp_path):
    wh = build_fixture_warehouse(tmp_path / "wh.duckdb")
    _delete_spy(wh, "date < DATE '2020-01-01'")  # a partial re-fetch: the count falls far below 2514
    ctx = _wctx(tmp_path, wh)
    try:
        # Last document 3000 -> 3001, whatever the table now counts; first document ever -> the count.
        assert daily._sequence(ctx, LAST, {"sequence": 3000}) == 3001
        n = ctx.warehouse.execute("SELECT COUNT(*) FROM bars_daily WHERE ticker = 'SPY'").fetchone()[0]
        assert n < 2514 and daily._sequence(ctx, LAST, None) == n
    finally:
        ctx.close()


def test_an_all_nan_last_row_is_held_and_an_all_zero_row_is_flat(tmp_path, monkeypatch, wh):
    def nan_from_june(ctx, weights=None):  # a decision on every session up to 05-29, none from 06-01
        w = strategies.static_mix(ctx, weights)
        w.loc[w.index >= pd.Timestamp("2026-06-01")] = np.nan
        return w

    _fake(monkeypatch, "nan_last", nan_from_june, "daily")
    _fake(monkeypatch, "zero_daily", strategies.static_mix, "daily")
    run_at, sig = _mini(tmp_path, monkeypatch, wh, _row("farm_nan_last", "nan_last", "one")
                        + _row("farm_zero_daily", "zero_daily", "one"),
                        [{"cell_id": "nan_last@one", "verdict": "FAIL", "selected_params": '{"weights": {"SPY": 0.5}}'},
                         {"cell_id": "zero_daily@one", "verdict": "FAIL",
                          "selected_params": '{"weights": {"SPY": 0.0}}'}])
    em = _emitted(run_at(LAST))
    assert (em.loc["farm_nan_last", "status"], em.loc["farm_nan_last", "note"]) == ("held", "no decision")
    assert not list(sig.glob("farm_nan_last-*"))  # never an empty document: that would read as "go flat"
    assert em.loc["farm_zero_daily", "status"] == "written"
    assert json.loads((sig / f"farm_zero_daily-{LAST}.json").read_text())["targets"] == []


def test_a_monthly_strategy_emits_on_the_months_last_session_and_holds_otherwise(tmp_path, monkeypatch, wh):
    # sma_trend rebalances monthly. NYSE sessions around the month end: Mon 05-25 is Memorial Day;
    # 05-27 (Wed) -> next 05-28, 05-28 (Thu) -> next 05-29, both May: not period ends.
    # 05-29 (Fri) -> next Mon 06-01, June: May's period end. 06-01 -> next 06-02: not one.
    # SPY sessions through 05-27 / 05-29: 2514 - 3 = 2511 / 2514 - 1 = 2513.
    run_at, sig = _mini(tmp_path, monkeypatch, wh, _row("farm_sma_trend_eq3", "sma_trend", "eq3"),
                        [{"cell_id": "sma_trend@eq3", "verdict": "FAIL", "selected_params": '{"months": 10}'}])
    first = _emitted(run_at(date(2026, 5, 27))).loc["farm_sma_trend_eq3"]
    assert (first["status"], first["sequence"]) == ("written", 2511)  # the first run ever always emits
    held = _emitted(run_at(date(2026, 5, 28))).loc["farm_sma_trend_eq3"]
    assert (held["status"], held["last_rebalance"]) == ("held", "2026-05-27") and "monthly" in held["note"]
    end = _emitted(run_at(date(2026, 5, 29))).loc["farm_sma_trend_eq3"]
    assert (end["status"], end["sequence"]) == ("written", 2513)
    june = _emitted(run_at(LAST)).loc["farm_sma_trend_eq3"]
    assert (june["status"], june["last_rebalance"]) == ("held", "2026-05-29")
    assert sorted(p.name for p in sig.glob("*.json")) == ["farm_sma_trend_eq3-2026-05-27.json",
                                                          "farm_sma_trend_eq3-2026-05-29.json"]
    # The calendar knows the rule holidays: 2026-07-03 (July 4 is a Saturday) and Good Friday 2026-04-03.
    assert daily.next_session(date(2026, 7, 2)) == date(2026, 7, 6)
    assert daily.next_session(date(2026, 4, 2)) == date(2026, 4, 6)
    assert daily._period_end(date(2026, 7, 2), "weekly") and not daily._period_end(date(2026, 7, 1), "weekly")


def test_a_signal_strategy_emits_on_a_change_and_holds_on_a_repeat(tmp_path, monkeypatch, wh):
    def step(ctx, weights=None):  # SPY 0.5 until 05-28, 0.6 from 05-29: causal, it reads only the date
        w = strategies.static_mix(ctx, {"SPY": 0.5})
        w.loc[w.index >= pd.Timestamp("2026-05-29")] = 0.6
        return w

    _fake(monkeypatch, "step_signal", step, "signal")
    run_at, sig = _mini(tmp_path, monkeypatch, wh, _row("farm_step_signal", "step_signal", "one"),
                        [{"cell_id": "step_signal@one", "verdict": "FAIL", "selected_params": "{}"}])
    statuses = [_emitted(run_at(d)).loc["farm_step_signal", "status"]
                for d in (date(2026, 5, 27), date(2026, 5, 28), date(2026, 5, 29), LAST)]
    # first ever; 0.5 again (|dw| 0 <= 1e-6); 0.5 -> 0.6 (|dw| 0.1); 0.6 again.
    assert statuses == ["written", "held", "written", "held"]
    w = json.loads((sig / "farm_step_signal-2026-05-29.json").read_text())["targets"]
    assert w == [{"symbol": "SPY", "weight": 0.6}]


def test_validation_is_unvalidated_when_the_farm_re_selected_mid_month():
    cell = Cell("x_y", "signals.daily", "sma_trend", "eq3", ("SPY",), 0.025)
    rows = pd.DataFrame([{"cell_id": "sma_trend@eq3", "verdict": "PASS", "selected_params": '{"months": 12}',
                          "dsr": 0.99, "psr": 0.99, "pbo": 0.01}])
    frozen, newest = params_hash({"months": 10}), params_hash({"months": 12})
    assert daily.validation_for(cell, rows, manifest="F1", params={"months": 10}) == {
        "status": "unvalidated", "gates_version": "2026-09-02", "dsr": None, "psr": None, "pbo": None,
        "manifest": f"F1: farm re-selected mid-month (frozen {frozen}, newest {newest})"}
    assert daily.validation_for(cell, rows, manifest="F1", params={"months": 12})["status"] == "pass"


def test_the_last_row_is_the_backtests_own_decision(market):
    from ohcamel_quant.backtest.engine import EngineConfig, StrategyContext, run_backtest

    px = market.prices(["SPY", "QQQ", "IWM"]).data.dropna()
    cell = Cell("farm_sma_trend_eq3", "signals.daily", "sma_trend", "eq3", ("SPY", "QQQ", "IWM"), 0.025)
    res = run_backtest(StrategyContext(prices=px), strategies.sma_trend, {"months": 10},
                       EngineConfig(rebalance="monthly"))
    d, e = res.decisions[-1], res.executions[-1]  # the last month-end decision and its execution close
    got, as_of = daily.targets_for(cell, {"months": 10}, px.loc[:d], None)
    assert as_of == d.date()
    engine = {s: float(v) for s, v in res.weights.loc[e].items() if abs(v) > 0}
    assert got == pytest.approx(engine, abs=1e-12)  # gross <= 1 here: nothing scaled


def test_a_short_history_bad_params_or_a_peeking_strategy_is_refused(market, monkeypatch):
    px = market.prices(["SPY", "QQQ", "IWM"]).data.dropna()
    cell = Cell("farm_sma_trend_eq3", "signals.daily", "sma_trend", "eq3", ("SPY", "QQQ", "IWM"), 0.025)
    # months 24 (the parameter's maximum): warm-up 24 x 21 + 5 = 509 sessions; cut the panel to 500.
    with pytest.raises(daily.CellRefused, match="500 sessions of history < the 509-session warm-up"):
        daily.targets_for(cell, {"months": 24}, px.iloc[:500], None)
    with pytest.raises(daily.CellRefused, match="parameters: months=100 is above the maximum 24"):
        daily.targets_for(cell, {"months": 100}, px, None)
    with pytest.raises(daily.CellRefused, match="parameters: unknown parameter"):
        daily.targets_for(cell, {"bogus": 1}, px, None)

    def peek(ctx, weights=None):  # tomorrow's sign: the engine's audit must catch it
        up = (ctx.prices.shift(-1) > ctx.prices).astype(float) / ctx.prices.shape[1]
        return up.where(ctx.prices.shift(-1).notna())

    _fake(monkeypatch, "peek", peek, "daily")
    with pytest.raises(daily.CellRefused, match="look-ahead audit"):
        daily.targets_for(dataclasses.replace(cell, strategy="peek"), {}, px, None)


def test_roster_symbols_that_differ_from_the_universe_are_refused(world):
    assert _refused(world["spec"])["farm_sma_trend_mismatch"] == "roster symbols differ from the farm's universe"


def test_a_missed_month_end_is_caught_up_late_and_a_kept_one_holds(tmp_path, monkeypatch, wh):
    # sma_trend, monthly. Both cells' first document is 05-27 (first run ever). May's period end is
    # 05-29 (Fri; next session Mon 06-01). The "kept" cell runs on 05-29 and emits; the "missed" one
    # does not (the worker was down for it). On 06-01 -- not a period end (next 06-02) -- the missed
    # cell's last document (05-27) is older than the last period end (05-29): emitted late, sequence
    # 2514 (SPY sessions through 06-01); the kept cell's last document is 05-29: it holds.
    rows = [{"cell_id": "sma_trend@eq3", "verdict": "FAIL", "selected_params": '{"months": 10}'}]
    run_at, sig = _mini(tmp_path, monkeypatch, wh, _row("farm_sma_trend_eq3", "sma_trend", "eq3"), rows)
    assert _emitted(run_at(date(2026, 5, 27))).loc["farm_sma_trend_eq3", "status"] == "written"
    assert _emitted(run_at(date(2026, 5, 29))).loc["farm_sma_trend_eq3", "status"] == "written"
    kept = _emitted(run_at(LAST)).loc["farm_sma_trend_eq3"]
    assert (kept["status"], kept["last_rebalance"]) == ("held", "2026-05-29")

    (sig / "farm_sma_trend_eq3-2026-05-29.json").unlink()  # the month-end document never went out
    late = _emitted(run_at(LAST)).loc["farm_sma_trend_eq3"]
    assert (late["status"], late["sequence"]) == ("late", 2514)
    assert "2026-05-29" in late["note"] and "2026-05-27" in late["note"]
    assert json.loads((sig / f"farm_sma_trend_eq3-{LAST}.json").read_text())["as_of"] == "2026-06-01"
