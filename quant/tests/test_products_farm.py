"""Lane M, M5: farm cells on fixture ETFs; incremental runs, budget and memory refusal."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from lane_m_harness import publish_artifact

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
    assert row["costs_reported"] and len(json.loads(row["cost_curve"])) == 4  # 0/5/15/30 bps
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
    assert spec.verdict_detail.startswith(("0 PASS", "1 PASS"))
