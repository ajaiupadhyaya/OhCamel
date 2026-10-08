"""P4 job: ``farm.sweep`` (heavy, class L): the stalest cells first until the time budget is spent.

Cells never run come first (registry order), then the oldest ``ran_at``. Every
cell's row, regime rows and trial Sharpes are carried forward from the
previous artifact, so the leaderboard always covers the whole farm. The
artifact's ``data_asof`` is the OLDEST cell's (a stale cell never makes the
farm look current; Review Focus 4 of the compute plan).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..backtest import metrics as M
from ..backtest.engine import EngineConfig, StrategyContext, run_backtest
from ..backtest.strategies import buy_and_hold, get_strategy
from ..backtest.validation import (
    bootstrap_sharpe,
    cost_sensitivity,
    cscv_pbo,
    spa_test,
    sweep,
    walk_forward,
)
from ..data.base import DataUnavailable
from ..jobs.artifacts import ArtifactSpec
from ..models.farm import GRID_MAX, cell_id, farm_cells, leaderboard, strategy_grid
from ..models.verdict import COST_GRID_BPS, regime_table

WF_IS, WF_OOS, PBO_S = 1260, 252, 16
FARM_SWEEP_BYTES = 512 * 2**20       # class L is 1152 MiB; the interpreter, pandas and the engine take the rest
BUDGET_MINUTES = 90
DB_PATH: Path | None = None          # tests point the previous-artifact lookup at a temp jobs.sqlite


def _universe(name: str) -> list[str]:
    from ..data.market import load_universes

    return [m["ticker"] for m in load_universes()["universes"][name]["members"]]


def _skipped(strategy: str, universe: str, reason: str) -> dict[str, Any]:
    return {"cell_id": cell_id(strategy, universe), "strategy": strategy, "universe": universe, "status": "skipped",
            "reason": reason, "ran_at": datetime.now(UTC).isoformat()}


def run_cell(strategy: str, universe: str, ctx: Any,
             tickers: list[str] | None = None) -> tuple[dict[str, Any], pd.DataFrame, np.ndarray]:
    spec = get_strategy(strategy)
    try:
        tk = tickers or _universe(universe)
        px = ctx.market.prices(tk).data.dropna()
        try:
            rf = ctx.market.risk_free_daily(px.index[0].date(), px.index[-1].date()).data.reindex(px.index).fillna(0.0)
        except DataUnavailable:
            rf = None
        mkt = ctx.market.prices(["SPY"]).data["SPY"].reindex(px.index) if spec.needs_market else None
        sctx = StrategyContext(prices=px, rf=rf, market=mkt)
        cfg = EngineConfig(rebalance=spec.default_rebalance, max_gross_leverage=spec.default_max_leverage)
        sw = sweep(sctx, spec, {}, strategy_grid(spec), cfg, rf, max_combos=GRID_MAX, max_bytes=FARM_SWEEP_BYTES)
        if len(sw.returns) < WF_IS + WF_OOS:
            raise ValueError(f"{len(sw.returns)} common live sessions (< {WF_IS + WF_OOS} for walk-forward)")
        wf = walk_forward(sw, WF_IS, WF_OOS, cost_bps=cfg.cost_bps, rf=rf)
    except (DataUnavailable, ValueError) as e:
        return _skipped(strategy, universe, str(e)), pd.DataFrame(), np.array([])
    oos = wf["returns"]
    ex = M.excess(oos, rf)
    sr, (sk, ku) = M.sharpe_per_period(ex), M.moments(ex)
    bench = run_backtest(sctx, buy_and_hold, {}, EngineConfig(rebalance="never")).returns_net.reindex(sw.returns.index)
    pick = wf["folds"][-1]["combo"]
    costs = cost_sensitivity(sw.results[pick], list(COST_GRID_BPS), rf)
    boot = bootstrap_sharpe(oos, rf, reps=1000, alpha=0.10, use_kernels=True, threads=ctx.threads)
    trials = np.array([M.sharpe_per_period(M.excess(sw.returns[c], rf)) for c in sw.returns.columns])
    reg = regime_table(oos)
    row = {"cell_id": cell_id(strategy, universe), "strategy": strategy, "universe": universe, "status": "ok",
           "reason": None, "ran_at": datetime.now(UTC).isoformat(), "data_asof": str(px.index[-1].date()),
           "n_combos": int(len(sw.combos)), "holdout_return": M.total_return(oos), "sr_pp": sr, "n": int(len(ex)),
           "skew": sk, "kurt": ku, "psr": M.probabilistic_sharpe(sr, len(ex), sk, ku), "boot_lo5": boot["ci_low"],
           "pbo": cscv_pbo(sw.returns, PBO_S, threads=ctx.threads)["pbo"],
           "spa_p": spa_test(bench.fillna(0.0), sw.returns)["pvalue_consistent"],
           "oos_sharpe_ann": M.sharpe(ex),
           "cost_curve": json.dumps(costs["curve"]), "selected_params": json.dumps(sw.combos[pick])}
    return row, reg.assign(cell_id=row["cell_id"]), trials


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    from .io import previous_artifact, read_table

    budget = float(params.get("budget_minutes", BUDGET_MINUTES)) * 60.0
    t0 = time.monotonic()
    prev = previous_artifact("farm.sweep", db_path=DB_PATH)
    cells = read_table(prev[1], "cells") if prev else None
    regimes = read_table(prev[1], "regimes") if prev else None
    trials = read_table(prev[1], "trials") if prev else None
    cells = cells if cells is not None else pd.DataFrame(columns=["cell_id", "ran_at"])
    regimes = regimes if regimes is not None else pd.DataFrame(columns=["cell_id"])
    trials = trials if trials is not None else pd.DataFrame(columns=["cell_id", "combo", "sr_pp"])
    ran_at = dict(zip(cells["cell_id"], cells["ran_at"], strict=True))
    order = sorted(farm_cells(), key=lambda c: (cell_id(*c) in ran_at, ran_at.get(cell_id(*c), "")))
    done = 0
    for s, u in order:
        if time.monotonic() - t0 >= budget:
            break
        ctx.check_cancelled()
        row, reg, tr = run_cell(s, u, ctx)
        cid = row["cell_id"]
        cells = pd.concat([cells[cells["cell_id"] != cid], pd.DataFrame([row])], ignore_index=True)
        regimes = pd.concat([regimes[regimes["cell_id"] != cid], reg], ignore_index=True)
        trials = pd.concat([trials[trials["cell_id"] != cid],
                            pd.DataFrame({"cell_id": cid, "combo": range(tr.size), "sr_pp": tr})], ignore_index=True)
        done += 1
        ctx.progress(min(1.0, (time.monotonic() - t0) / budget) if budget else 1.0, cid)
    board = leaderboard(cells, regimes, trials["sr_pp"].to_numpy(dtype=float))
    n_pass = int((board["verdict"] == "PASS").sum()) if len(board) else 0
    n_fail = int((board["verdict"] == "FAIL").sum()) if len(board) else 0
    n_skip = int((cells.get("status") == "skipped").sum()) if len(cells) else 0
    asof = cells.loc[cells.get("status") == "ok", "data_asof"].min() if len(board) else None
    verdict = "INSUFFICIENT DATA" if board.empty else "PASS" if n_pass else "FAIL"
    return ArtifactSpec(
        tables={"cells": cells, "regimes": regimes, "trials": trials, "leaderboard": board},
        data_asof=None if asof is None or pd.isna(asof) else str(asof),
        provenance=[{"source": "ohcamel-farm", "cells_run_tonight": done, "cells_total": len(farm_cells())}],
        notes=[f"{done} cells run tonight; DSR deflated by all {len(trials)} trials in the farm; walk-forward "
               f"{WF_IS}/{WF_OOS} sessions; costs from the engine's default ({EngineConfig().cost_bps} bps)"],
        verdict=verdict,
        verdict_detail=f"{n_pass} PASS · {n_fail} FAIL · {n_skip} SKIPPED · ADVISORY")
