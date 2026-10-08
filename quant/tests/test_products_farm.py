"""Lane M, M5: farm cells on fixture ETFs; incremental runs, budget and memory refusal."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from lane_m_harness import is_label, publish_artifact

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import farm


def ctx(market):
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=market)


@pytest.fixture(scope="module")
def cell(market):
    return farm.run_cell("sma_trend", "fixture", ctx(market), tickers=["SPY", "QQQ", "IWM"])


def test_a_cell_carries_every_gate_input(cell):
    row, regimes, trials = cell
    assert row["status"] == "ok" and row["n_combos"] == 16 and len(trials) == 16
    for k in ("holdout_return", "psr", "boot_lo5", "pbo", "spa_p", "sr_pp", "n", "skew", "kurt", "oos_sharpe_ann"):
        assert row[k] is not None, k
    assert [c["cost_bps"] for c in json.loads(row["cost_curve"])] == [0.0, 5.0, 15.0, 30.0]
    assert "costs_reported" not in row  # the gate reads the table, never a flag
    assert set(regimes["regime"]) == {"2008", "2015-16", "2020", "2022", "2024"}
    assert regimes.set_index("regime").loc["2008", "positive"] is None  # fixtures start 2016: no 2008 data


def test_a_cell_is_deterministic(market, cell):
    again = farm.run_cell("sma_trend", "fixture", ctx(market), tickers=["SPY", "QQQ", "IWM"])
    assert {k: v for k, v in again[0].items() if k != "ran_at"} == {k: v for k, v in cell[0].items() if k != "ran_at"}


def test_farm_cell_over_memory_budget_is_skipped_not_run(market, monkeypatch):
    monkeypatch.setattr(farm, "FARM_SWEEP_BYTES", 1024)
    row, regimes, trials = farm.run_cell("sma_trend", "fixture", ctx(market), tickers=["SPY", "QQQ", "IWM"])
    assert row["status"] == "skipped" and "MiB" in row["reason"] and trials.size == 0


def test_incremental_run_carries_cells_forward_and_respects_the_budget(market, tmp_path, monkeypatch, cell):
    row, regimes, trials = cell
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(farm, "DB_PATH", db)
    prev_cells = pd.DataFrame([row])
    publish_artifact(db, root, "farm.sweep", {
        "cells": prev_cells, "regimes": regimes.assign(cell_id=row["cell_id"]),
        "trials": pd.DataFrame({"cell_id": row["cell_id"], "combo": range(16), "sr_pp": trials}),
        "leaderboard": pd.DataFrame({"cell_id": [row["cell_id"]]})}, verdict="FAIL")
    spec = farm.run({"budget_minutes": 0}, ctx(market))  # no time: nothing new runs
    assert spec.tables["cells"]["cell_id"].tolist() == [row["cell_id"]]
    assert spec.tables["leaderboard"]["verdict"].iloc[0] in ("PASS", "FAIL")
    assert spec.verdict == spec.tables["leaderboard"]["verdict"].iloc[0]
    assert spec.verdict_detail.startswith(("0 PASS", "1 PASS")) and is_label(spec.verdict_detail)


def test_leaderboard_cost_gate_reads_the_cells_cost_curve(cell):
    from ohcamel_quant.models.farm import leaderboard

    row, regimes, trials = cell
    curve = json.loads(row["cost_curve"])
    full = leaderboard(pd.DataFrame([row]), regimes, trials)
    cut = leaderboard(pd.DataFrame([{**row, "cost_curve": json.dumps(curve[:2])}]), regimes, trials)
    gone = leaderboard(pd.DataFrame([{**row, "cost_curve": None}]), regimes, trials)
    assert "COST_SWEEP" not in full["detail"].iloc[0]
    assert cut["verdict"].iloc[0] == "FAIL" and "COST_SWEEP 2.00" in cut["detail"].iloc[0]
    assert gone["verdict"].iloc[0] == "FAIL" and "COST_SWEEP 0.00" in gone["detail"].iloc[0]


def test_a_cell_reads_prices_from_the_warehouse_first(tmp_path, monkeypatch):
    """On the droplet the farm must not hammer the vendors: a fresh warehouse that covers the window serves it."""
    from datetime import UTC, date, datetime

    from ohcamel_quant.config import Settings
    from ohcamel_quant.data.base import DataUnavailable
    from ohcamel_quant.data.market import MarketData
    from ohcamel_quant.warehouse import freshness
    from ohcamel_quant.warehouse.db import open_rw
    from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
    from ohcamel_quant.warehouse.ingest.base import log_row

    wh = build_fixture_warehouse(tmp_path / "wh.duckdb")
    with open_rw(wh) as con:  # what a full backfill logs: the vendor was asked from 1990
        for t in ("SPY", "QQQ", "IWM"):
            log_row(con, "bars_daily", t, "ok", 0, json.dumps({"mode": "full", "requested_start": "1990-01-01"}),
                    date(2026, 6, 1), ran_at=datetime(2026, 6, 1, 21, 0))
    monkeypatch.setattr(freshness, "_now", lambda: datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    md = MarketData(Settings(offline=True, data_dir=tmp_path, warehouse_path=wh))
    calls: list[str] = []

    def vendor(ticker, start=None, end=None):
        calls.append(ticker)
        raise DataUnavailable(f"{ticker}: vendor must not be called")

    monkeypatch.setattr(md, "ohlcv", vendor)
    c = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                   _market=md)
    row, _, trials = farm.run_cell("sma_trend", "fixture", c, tickers=["SPY", "QQQ", "IWM"])
    assert row["status"] == "ok", row.get("reason")
    assert calls == [] and len(trials) == 16
