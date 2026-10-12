"""signals.daily (Task 14): every farm cell of INC-01's roster as a contract-v1
document, computed with the backtest's own strategy function at the last close,
under parameters frozen once a month (INC-01, *The parameter-freeze rule*).

Offline, on the fixture warehouse (warehouse/fixture_db.py: the nine committed
fixture ETFs, 2016-06-01..2026-06-01, 2514 sessions each) and on farm.sweep
artifacts published the way the worker publishes them (lane_m_harness). The
test roster names only fixture tickers -- every one of them in
book.example.sexp's positions -- plus one name (VTI) that is not."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from lane_m_harness import publish_artifact

from ohcamel_quant.config import Settings
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.jobs.db import connect
from ohcamel_quant.signals import daily, freeze
from ohcamel_quant.signals.contract import V1_SCHEMA_PATH, params_hash, validate
from ohcamel_quant.signals.roster import Cell
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

LAST = date(2026, 6, 1)  # every fixture's last session

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
    universe: fixture
    symbols: [SPY, QQQ, IWM]
    capital_fraction: 0.025
  - slug: farm_static_mix_fixture
    producer: signals.daily
    strategy: static_mix
    universe: fixture
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
    universe: fixture
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
    {"cell_id": "sma_trend@fixture", "verdict": "FAIL", "selected_params": json.dumps({"months": 10}),
     "dsr": 0.41, "psr": 0.62, "pbo": 0.55},
    # static_mix@fixture: 0.8 + 0.6 = 1.4 gross, so the producer scales it to 1.
    {"cell_id": "static_mix@fixture", "verdict": "PASS",
     "selected_params": json.dumps({"weights": {"SPY": 0.8, "QQQ": 0.6}}), "dsr": 0.97, "psr": 0.99, "pbo": 0.1},
    # static_mix@flat: a zero weight -- flat, which is a valid document. No psr or pbo on its row.
    {"cell_id": "static_mix@flat", "verdict": "FAIL",
     "selected_params": json.dumps({"weights": {"SPY": 0.0}})},
    # tsmom@outside has params, but names VTI: refused before anything is read.
    {"cell_id": "tsmom@outside", "verdict": "FAIL", "selected_params": json.dumps({"lookback": 252})},
    # tsmom@fixture is absent: no params, no last month -> refused with the reason.
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
