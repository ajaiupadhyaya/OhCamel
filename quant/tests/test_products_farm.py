"""Lane M, M5: farm cells on fixture ETFs; incremental runs, budget and memory refusal."""

from __future__ import annotations

import json

import numpy as np
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
    # each trial carries its identity in the trial ledger (completion task 2.4)
    assert list(trials.columns) == ["cell_id", "combo", "sr_pp", "grid_version", "params_hash", "n_obs"]
    from ohcamel_quant.backtest.strategies import get_strategy
    from ohcamel_quant.models.farm import strategy_grid
    from ohcamel_quant.models.trial_ledger import grid_version

    assert trials["params_hash"].nunique() == 16
    assert set(trials["grid_version"]) == {grid_version(strategy_grid(get_strategy("sma_trend")))}
    assert (trials["n_obs"] > row["n"]).all()                 # the sweep's window; the gates judge its OOS part
    assert (row["fill"], row["max_gross"]) == ("open", 1.0)    # the configuration the desk trades
    for k in ("holdout_return", "psr", "boot_lo5", "pbo", "spa_p", "sr_pp", "n", "skew", "kurt", "oos_sharpe_ann"):
        assert row[k] is not None, k
    assert [c["cost_bps"] for c in json.loads(row["cost_curve"])] == [0.0, 5.0, 15.0, 30.0]
    assert "costs_reported" not in row  # the gate reads the table, never a flag
    assert set(regimes["regime"]) == {"2008", "2015-16", "2020", "2022", "2024"}
    assert regimes.set_index("regime").loc["2008", "positive"] is None  # fixtures start 2016: no 2008 data


COST_COLUMNS = ("turnover", "capacity_usd", "sr_at_0x", "sr_at_1x", "sr_at_3x", "sr_at_6x")


def test_a_cell_reports_turnover_capacity_and_the_sweep_on_its_out_of_sample_series(cell):
    """Completion task 2.3: the sweep multiplies every half-spread by 0, 1, 3 and 6 and re-prices the walk-forward
    out-of-sample series the gates judge, so its 1x Sharpe is the gated one; turnover and capacity describe it too."""
    from ohcamel_quant.backtest import costs

    row, _, _ = cell
    for k in COST_COLUMNS:
        assert isinstance(row[k], float) and np.isfinite(row[k]), k
    assert row["sr_at_1x"] == pytest.approx(row["oos_sharpe_ann"], rel=1e-12)
    assert row["sr_at_0x"] >= row["sr_at_1x"] >= row["sr_at_3x"] >= row["sr_at_6x"]
    assert row["sr_at_0x"] > row["sr_at_6x"]
    assert row["turnover"] > 0 and row["capacity_usd"] > 0
    assert row["costs_version"] == costs.load().version


def test_leaderboard_carries_turnover_capacity_and_the_sweep(cell):
    from ohcamel_quant.models.farm import leaderboard

    row, regimes, trials = cell
    board = leaderboard(pd.DataFrame([row]), regimes, trials["sr_pp"].to_numpy())
    for k in COST_COLUMNS:
        assert board[k].iloc[0] == pytest.approx(row[k], rel=1e-12), k
    before = {k: v for k, v in row.items() if k not in COST_COLUMNS}   # a cell carried forward from an older run
    old = leaderboard(pd.DataFrame([before]), regimes, trials["sr_pp"].to_numpy())
    assert all(k in old.columns and old[k].isna().all() for k in COST_COLUMNS)


def test_a_cell_is_deterministic(market, cell):
    again = farm.run_cell("sma_trend", "fixture", ctx(market), tickers=["SPY", "QQQ", "IWM"])
    assert {k: v for k, v in again[0].items() if k != "ran_at"} == {k: v for k, v in cell[0].items() if k != "ran_at"}


def test_farm_cell_over_memory_budget_is_skipped_not_run(market, monkeypatch):
    monkeypatch.setattr(farm, "FARM_SWEEP_BYTES", 1024)
    row, regimes, trials = farm.run_cell("sma_trend", "fixture", ctx(market), tickers=["SPY", "QQQ", "IWM"])
    assert row["status"] == "skipped" and "MiB" in row["reason"] and len(trials) == 0


def test_incremental_run_carries_cells_forward_and_respects_the_budget(market, tmp_path, monkeypatch, cell):
    row, regimes, trials = cell
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(farm, "DB_PATH", db)
    monkeypatch.setattr(farm, "LEDGER_PATH", tmp_path / "trial_ledger.jsonl")
    prev_cells = pd.DataFrame([row])
    publish_artifact(db, root, "farm.sweep", {
        "cells": prev_cells, "regimes": regimes.assign(cell_id=row["cell_id"]),
        # an artifact from before the trial ledger: its trials carry no identity
        "trials": pd.DataFrame({"cell_id": row["cell_id"], "combo": range(16), "sr_pp": trials["sr_pp"]}),
        "leaderboard": pd.DataFrame({"cell_id": [row["cell_id"]]})}, verdict="FAIL")
    spec = farm.run({"budget_minutes": 0}, ctx(market))  # no time: nothing new runs
    # its 16 trials are counted until the cell runs again and enters the ledger (completion task 2.4)
    led = [p for p in spec.provenance if p.get("source") == "trial-ledger"]
    assert led == [{"source": "trial-ledger", "path": "trial_ledger.jsonl", "distinct_trials": 0,
                    "carried_unledgered": 16}]
    assert "16 trials" in spec.notes[0]
    assert "1 of 1 scored cells at gross 1" in spec.notes[0] and "await a re-run" not in spec.notes[0]
    assert spec.tables["leaderboard"][["fill", "max_gross"]].iloc[0].tolist() == ["open", 1.0]
    assert spec.tables["cells"]["cell_id"].tolist() == [row["cell_id"]]
    assert spec.tables["leaderboard"]["verdict"].iloc[0] in ("PASS", "FAIL")
    assert spec.verdict == spec.tables["leaderboard"]["verdict"].iloc[0]
    assert spec.verdict_detail.startswith(("0 PASS", "1 PASS")) and is_label(spec.verdict_detail)
    # fix round 1 (M2): the page says "provisional" from this count, so the word goes when the owner measures
    from ohcamel_quant.backtest import costs

    m = costs.load()
    assert [p for p in spec.provenance if p.get("source") == "costs"] == [
        {"source": "costs", "path": "research/config/costs.yaml", "version": m.version, "tiers": 42,
         "provisional_tiers": sum(1 for t in m.tickers if m.source(t) == "provisional")}]


def test_leaderboard_cost_gate_reads_the_cells_cost_curve(cell):
    from ohcamel_quant.models.farm import leaderboard

    row, regimes, trials = cell
    curve = json.loads(row["cost_curve"])
    t = trials["sr_pp"].to_numpy()
    full = leaderboard(pd.DataFrame([row]), regimes, t)
    cut = leaderboard(pd.DataFrame([{**row, "cost_curve": json.dumps(curve[:2])}]), regimes, t)
    gone = leaderboard(pd.DataFrame([{**row, "cost_curve": None}]), regimes, t)
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


# --- completion task 2.4: the traded configuration, and every trial counted ---------------------------------------

TK = ["SPY", "QQQ", "IWM"]


def _one_cell_farm(monkeypatch, tmp_path, strategy="sma_trend"):
    monkeypatch.setattr(farm, "DB_PATH", tmp_path / "jobs.sqlite")       # no earlier artifact
    monkeypatch.setattr(farm, "LEDGER_PATH", tmp_path / "trial_ledger.jsonl")
    monkeypatch.setattr(farm, "farm_cells", lambda: [(strategy, "fixture")])
    monkeypatch.setattr(farm, "_universe", lambda name: list(TK))
    return tmp_path / "trial_ledger.jsonl"


def _ledger_note(spec):
    return [p for p in spec.provenance if p.get("source") == "trial-ledger"][0]


def test_running_the_farm_twice_doubles_no_trial_and_a_changed_grid_adds_trials(market, monkeypatch, tmp_path):
    from ohcamel_quant.models import trial_ledger as L

    path = _one_cell_farm(monkeypatch, tmp_path)
    first = farm.run({"budget_minutes": 60}, ctx(market))
    assert len(L.distinct(path)) == 16 and _ledger_note(first)["distinct_trials"] == 16
    once = path.read_bytes()
    second = farm.run({"budget_minutes": 60}, ctx(market))
    assert path.read_bytes() == once and _ledger_note(second)["distinct_trials"] == 16
    assert second.tables["ledger"].drop(columns="recorded_at").equals(L.distinct(path).drop(columns="recorded_at"))
    # a changed grid (four of the same months, a new grid version) is four new trials, appended
    monkeypatch.setattr(farm, "strategy_grid", lambda spec: {"months": [3, 6, 9, 12]})
    third = farm.run({"budget_minutes": 60}, ctx(market))
    assert path.read_bytes().startswith(once) and len(L.distinct(path)) == 20
    assert _ledger_note(third)["distinct_trials"] == 20 and "20 trials" in third.notes[0]


def test_the_leaderboards_dsr_counts_every_trial_in_the_ledger(market, monkeypatch, tmp_path):
    """Trials the farm ran before (another grid, another cell) deflate tonight's rows: N is the ledger's."""
    from ohcamel_quant.models import trial_ledger as L
    from ohcamel_quant.models.farm import farm_dsr

    path = _one_cell_farm(monkeypatch, tmp_path)
    rng = np.random.default_rng(11)
    L.append_many(path, [{"cell_id": "xsmom@sectors", "grid_version": "old", "params_hash": f"sha256:{i}",
                          "sharpe": float(s), "n_obs": 2000} for i, s in enumerate(rng.normal(0, 0.02, 500))])
    spec = farm.run({"budget_minutes": 60}, ctx(market))
    led = L.distinct(path)
    assert len(led) == 516 and _ledger_note(spec)["distinct_trials"] == 516
    cells = spec.tables["cells"]
    want = farm_dsr(cells[cells["status"] == "ok"], led).iloc[0]
    assert spec.tables["leaderboard"]["dsr_farm"].iloc[0] == pytest.approx(want, rel=1e-12)
    assert want < farm_dsr(cells[cells["status"] == "ok"], led[led["cell_id"] != "xsmom@sectors"]).iloc[0]


def _hand_open_fill(opens, targets, rebalance_ok, spreads_bps, borrow_bps, fin_bps, rf, gross_cap):
    """The book filled at the next session's open, marked open to open, by hand: a decision at the close of d
    executes at the open of d + 1, capped at ``gross_cap``; each name pays its half-spread on what it trades after
    drift, shorts pay borrow and gross above 1 pays financing, both a 252nd of the annual rate a session."""
    o = opens.to_numpy(float)
    n, a = o.shape
    t = targets.reindex(index=opens.index, columns=opens.columns).to_numpy(float)
    valid = np.isfinite(t).any(axis=1)
    t = np.where(np.isfinite(t), t, 0.0)
    fills = {}
    for d in range(n - 1):
        if valid[d] and rebalance_ok[d]:
            w = t[d].copy()
            g = np.abs(w).sum()
            fills[d + 1] = w * (gross_cap / g) if g > gross_cap else w
    c = np.asarray(spreads_bps, float) / 1e4
    w = np.zeros(a)
    net, book = np.zeros(n), np.zeros((n, a))
    for s in range(n):
        r = o[s] / o[s - 1] - 1.0 if s else np.zeros(a)
        g = (w @ r + (1.0 - w.sum()) * rf[s] - borrow_bps / 1e4 / 252 * np.clip(-w, 0, None).sum()
             - fin_bps / 1e4 / 252 * max(np.abs(w).sum() - 1.0, 0.0))
        drifted = w * (1.0 + r) / (1.0 + g)
        if s in fills:
            net[s] = (1.0 + g) * (1.0 - (c * np.abs(fills[s] - drifted)).sum()) - 1.0
            w = fills[s]
        else:
            net[s], w = g, drifted
        book[s] = w
    return pd.Series(net, opens.index), pd.DataFrame(book, opens.index, opens.columns), fills


def test_a_cells_out_of_sample_series_is_a_hand_backtest_filled_at_the_next_open_at_gross_1(market, monkeypatch):
    """Spec R3: the farm scores each cell at gross 1, filled at the open of d+1 -- what signals.daily trades.
    st_reversal's book is +1 long and -1 short (gross 2): every decision is cut to gross 1, and it shorts."""
    from ohcamel_quant.backtest import costs
    from ohcamel_quant.backtest.engine import StrategyContext
    from ohcamel_quant.backtest.strategies import get_strategy, validate_params
    from ohcamel_quant.data.base import DataUnavailable

    seen = {}
    real = farm.walk_forward

    def spy(sw, *a, **k):
        seen["sw"], seen["wf"] = sw, real(sw, *a, **k)
        return seen["wf"]

    monkeypatch.setattr(farm, "walk_forward", spy)
    row, _, _ = farm.run_cell("st_reversal", "fixture", ctx(market), tickers=TK)
    assert row["status"] == "ok", row.get("reason")
    sw, wf = seen["sw"], seen["wf"]

    # what the farm reads: adjusted closes decide; the open, on the same adjustment (open x adj_close / close), fills
    adj, op, cl = (market.prices(TK, field=f).data for f in ("adj_close", "open", "close"))
    idx = adj.index.intersection(op.index).intersection(cl.index)
    px, opens = adj.loc[idx], (op * adj / cl).loc[idx]
    try:
        rf = market.risk_free_daily(idx[0].date(), idx[-1].date()).data.reindex(idx).fillna(0.0)
    except DataUnavailable:
        rf = None
    rf_arr = np.zeros(len(idx)) if rf is None else rf.to_numpy(float)
    m = costs.load()
    spreads = m.half_spreads(TK)
    weekly = np.zeros(len(idx), dtype=bool)                      # the last session of each week decides
    per = idx.to_period("W").asi8
    weekly[:-1] = per[:-1] != per[1:]
    spec = get_strategy("st_reversal")
    hand_net, hand_book, hand_fills = {}, {}, {}
    for combo in {f["combo"] for f in wf["folds"]}:
        tg = spec.fn(StrategyContext(prices=px, rf=rf), **validate_params(spec, sw.combos[combo], TK))
        hand_net[combo], hand_book[combo], hand_fills[combo] = _hand_open_fill(
            opens, tg, weekly, spreads, m.borrow_bps, m.financing_spread_bps, rf_arr, 1.0)
    pieces, prev = [], None
    aligned = sw.returns.index
    c = spreads / 1e4
    for f in wf["folds"]:
        k = f["combo"]
        win = aligned[(aligned >= f["oos_start"]) & (aligned <= f["oos_end"])]
        r = hand_net[k].reindex(win).copy()
        before = aligned[aligned.get_loc(win[0]) - 1]
        w_new = hand_book[k].loc[before].to_numpy()
        w_old = np.zeros_like(w_new) if prev is None else hand_book[prev].loc[before].to_numpy()
        switch = float((c * np.abs(w_new - w_old)).sum()) if prev != k else 0.0
        r.iloc[0] = (1.0 + r.iloc[0]) * (1.0 - switch) - 1.0
        pieces.append(r)
        prev = k
    hand = pd.concat(pieces)
    got = wf["returns"]
    assert got.index.equals(hand.index) and len(got) >= 252
    np.testing.assert_allclose(got.to_numpy(), hand.to_numpy(), rtol=0, atol=1e-12)
    assert row["holdout_return"] == pytest.approx(float((1.0 + hand).prod() - 1.0), rel=1e-10)
    # gross 1, at the open: every fill is cut from gross 2 to gross 1, and it is not the close-filled series
    fills = [w for k in hand_fills for w in hand_fills[k].values()]
    assert fills and all(np.abs(w).sum() == pytest.approx(1.0, abs=1e-12) for w in fills)
    assert any((w < 0).any() for w in fills)
    close_filled, _, _ = _hand_open_fill(px, spec.fn(StrategyContext(prices=px, rf=rf),
                                                     **validate_params(spec, sw.combos[wf["folds"][0]["combo"]], TK)),
                                         weekly, spreads, m.borrow_bps, m.financing_spread_bps, rf_arr, 1.0)
    assert not np.allclose(close_filled.reindex(hand.index).to_numpy()[:252], hand.to_numpy()[:252])
