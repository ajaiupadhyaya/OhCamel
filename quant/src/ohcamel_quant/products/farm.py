"""P4 job: ``farm.sweep`` (heavy, class L): the stalest cells first until the time budget is spent.

Cells never run come first (registry order), then the oldest ``ran_at``. Every
cell's row, regime rows and trial Sharpes are carried forward from the
previous artifact, so the leaderboard always covers the whole farm. The
artifact's ``data_asof`` is the OLDEST cell's (a stale cell never makes the
farm look current; Review Focus 4 of the compute plan).

Costs (completion task 2.3) come from the one cost file, research/config/costs.yaml:
every grid run pays each name's tiered half-spread, borrow on shorts and financing on
gross above 1, and the walk-forward's switches pay the same half-spreads. The sweep
re-prices the walk-forward OUT-OF-SAMPLE series -- the one the gates judge -- at 0, 1,
3 and 6 times every half-spread (``sr_at_<m>x``); the charter's 0/5/15/30 bps curve is
re-priced on the same series. ``turnover`` (one-way, a year) and ``capacity_usd`` (the
capital at which the largest trade on that path is 1% of its name's 20-session median
dollar volume) describe it too. A universe holding a name with no tier is skipped, its
reason saying so.

The traded configuration (completion task 2.4, spec R3): every cell is scored at gross 1
(``max_gross_leverage=1``), decided at the close of d and filled at the OPEN of d+1
(``fill="open"``, lag 1) -- what ``signals.daily`` targets and the desk's OMS fills -- so the
book is marked open to open on the opens put on the closes' adjustment (``open x adj_close /
close``); a session with no open print for some name is dropped, as a missing close is. The
equal-weight buy-and-hold benchmark of the SPA test is filled the same way.

Every trial is counted (task 2.4): each cell's trials -- one per grid combination run -- are
appended to the trial ledger (``models/trial_ledger.py``, ``<artifact root>/trial_ledger.jsonl``)
as soon as the cell has run, and the leaderboard's DSR takes ``N`` and ``V[SR]`` from the ledger's
distinct trials, every trial the farm has ever run, not tonight's grids. The artifact carries the
ledger's distinct trials as its ``ledger`` table; a run first seeds its ledger from the previous
artifact's (a burst machine's ledger starts empty; on the worker it adds nothing), and the burst
door appends a burst artifact's new ``ledger`` rows to the droplet's once each is shown to be a trial of
this grid the upload ran (``burst_door.record_burst_trials``). A carried-forward cell from before
the ledger (trials without ``params_hash``) is counted from the artifact until it runs again.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..backtest import costs as cost_file
from ..backtest import metrics as M
from ..backtest.engine import EngineConfig, StrategyContext, run_backtest
from ..backtest.strategies import buy_and_hold, get_strategy
from ..backtest.validation import (
    BOOTSTRAP_SEED,
    bootstrap_sharpe,
    cscv_pbo,
    oos_at_cost,
    oos_trades,
    oos_turnover,
    spa_test,
    sweep,
    walk_forward,
)
from ..data.base import DataUnavailable
from ..jobs.artifacts import ArtifactSpec
from ..models import trial_ledger
from ..models.farm import (
    GRID_MAX,
    cell_id,
    farm_cells,
    leaderboard,
    strategy_grid,
    traded_configuration,
)
from ..models.verdict import COST_GRID_BPS, regime_table
from ..signals.contract import params_hash

WF_IS, WF_OOS, PBO_S = 1260, 252, 16
FARM_SWEEP_BYTES = 512 * 2**20       # class L is 1152 MiB; the interpreter, pandas and the engine take the rest
BUDGET_MINUTES = 90
DB_PATH: Path | None = None          # tests point the previous-artifact lookup at a temp jobs.sqlite
LEDGER_PATH: Path | None = None      # the trial ledger; None = <artifact root>/trial_ledger.jsonl (tests: a temp file)
#: The traded configuration (completion task 2.4): what signals.daily targets and the desk fills.
FILL, EXECUTION_LAG, MAX_GROSS = "open", 1, 1.0
TRIAL_COLUMNS = ["cell_id", "combo", "sr_pp", "grid_version", "params_hash", "n_obs"]


def _universe(name: str) -> list[str]:
    from ..data.market import load_universes

    return [m["ticker"] for m in load_universes()["universes"][name]["members"]]


def _ledger_path() -> Path:
    return LEDGER_PATH if LEDGER_PATH is not None else trial_ledger.ledger_path()


def _prices_and_opens(ctx: Any, tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Adjusted closes (the strategy's input) and the opens on their adjustment (``open x adj_close / close``: the
    warehouse's open and close are split-adjusted, adj_close also for dividends, and a dividend's factor scales a
    session's open and close alike), on the sessions all three have for every name."""
    px = ctx.market.prices(tickers, warehouse_first=True).data.dropna()  # never the vendors when stored
    op = ctx.market.prices(tickers, field="open", warehouse_first=True).data
    cl = ctx.market.prices(tickers, field="close", warehouse_first=True).data
    idx = px.index.intersection(op.index).intersection(cl.index)
    opens = (op.reindex(index=idx, columns=px.columns) * px.loc[idx] / cl.reindex(index=idx, columns=px.columns))
    keep = idx[opens.notna().all(axis=1).to_numpy() & (opens > 0).all(axis=1).to_numpy()]
    return px.loc[keep], opens.loc[keep]


def _skipped(strategy: str, universe: str, reason: str) -> dict[str, Any]:
    return {"cell_id": cell_id(strategy, universe), "strategy": strategy, "universe": universe, "status": "skipped",
            "reason": reason, "ran_at": datetime.now(UTC).isoformat()}


def _dollar_volume(ctx: Any, tickers: list[str], index: pd.DatetimeIndex) -> pd.DataFrame:
    """Daily dollar volume, ``close x volume`` (split-repaired close and volume: their product is the day's traded
    value either way), warehouse first like the prices."""
    close = ctx.market.prices(tickers, field="close", warehouse_first=True).data
    volume = ctx.market.prices(tickers, field="volume", warehouse_first=True).data
    return (close * volume).reindex(index)


def _curve(r: pd.Series, rf: pd.Series | None, bps: float, turnover: pd.Series) -> dict[str, Any]:
    """One row of the charter's cost curve: the out-of-sample series at a flat ``bps`` on every traded unit."""
    return {"cost_bps": float(bps), "sharpe": M.sharpe(M.excess(r, rf)), "cagr": M.cagr(r), "ann_vol": M.ann_vol(r),
            "max_drawdown": M.max_drawdown(r),
            "annual_cost_drag": float((bps / 1e4 * turnover).sum() / max(len(turnover), 1) * M.PERIODS)}


def cost_figures(sw: Any, wf: dict[str, Any], px: pd.DataFrame, rf: pd.Series | None, model: cost_file.CostModel,
                 dollar_volume: pd.DataFrame | None) -> dict[str, Any]:
    """The cell's cost figures on its walk-forward out-of-sample path (completion task 2.3): ``sr_at_<m>x`` for each of
    the cost file's half-spread multiples, the charter's 0/5/15/30 bps ``cost_curve``, ``turnover`` (one-way, a
    year) and ``capacity_usd`` (NaN when dollar volume is unavailable or a trade has none). ``px`` is the panel the
    book was filled and marked at -- the opens, for the farm's open fill (task 2.4) -- from which the trades are
    rebuilt."""
    spreads = model.half_spreads(px.columns)
    out: dict[str, Any] = {f"sr_at_{m:g}x": M.sharpe(M.excess(oos_at_cost(sw, wf, spreads, m), rf)) for m in model.sweep}
    to = oos_turnover(sw, wf)
    out["cost_curve"] = json.dumps([_curve(oos_at_cost(sw, wf, spreads, flat_bps=b), rf, b, to) for b in COST_GRID_BPS])
    out["turnover"] = float(to.sum() / (len(to) / M.PERIODS)) if len(to) else float("nan")
    out["capacity_usd"] = (float("nan") if dollar_volume is None else
                           cost_file.capacity_of_trades(oos_trades(sw, wf, px), dollar_volume, model.participation,
                                                        model.adv_window))
    out["costs_version"] = model.version
    return out


def _no_trials() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=float if c == "sr_pp" else object) for c in TRIAL_COLUMNS})


def run_cell(strategy: str, universe: str, ctx: Any,
             tickers: list[str] | None = None) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """One cell -> ``(row, regimes, trials)``; ``trials`` has one row per grid combination run, with its identity
    in the trial ledger (``TRIAL_COLUMNS``)."""
    spec = get_strategy(strategy)
    model = cost_file.load()
    try:
        tk = tickers or _universe(universe)
        px, opens = _prices_and_opens(ctx, tk)
        spreads = model.half_spreads(px.columns)        # NoCostTier (a ValueError): skipped, never guessed
        try:
            rf = ctx.market.risk_free_daily(px.index[0].date(), px.index[-1].date()).data.reindex(px.index).fillna(0.0)
        except DataUnavailable:
            rf = None
        mkt = ctx.market.prices(["SPY"], warehouse_first=True).data["SPY"].reindex(px.index) if spec.needs_market else None
        sctx = StrategyContext(prices=px, rf=rf, market=mkt, opens=opens)
        grid = strategy_grid(spec)
        cfg = EngineConfig(rebalance=spec.default_rebalance, max_gross_leverage=MAX_GROSS, costs=model, fill=FILL,
                           execution_lag=EXECUTION_LAG)
        sw = sweep(sctx, spec, {}, grid, cfg, rf, max_combos=GRID_MAX, max_bytes=FARM_SWEEP_BYTES)
        if len(sw.returns) < WF_IS + WF_OOS:
            raise ValueError(f"{len(sw.returns)} common live sessions (< {WF_IS + WF_OOS} for walk-forward)")
        wf = walk_forward(sw, WF_IS, WF_OOS, cost_bps=spreads, rf=rf, lag=EXECUTION_LAG)
    except (DataUnavailable, ValueError) as e:
        return _skipped(strategy, universe, str(e)), pd.DataFrame(), _no_trials()
    oos = wf["returns"]
    ex = M.excess(oos, rf)
    sr, (sk, ku) = M.sharpe_per_period(ex), M.moments(ex)
    bench = run_backtest(sctx, buy_and_hold, {}, EngineConfig(rebalance="never", costs=model, fill=FILL,
                                                              execution_lag=EXECUTION_LAG)
                         ).returns_net.reindex(sw.returns.index)
    pick = wf["folds"][-1]["combo"]
    try:
        dv = _dollar_volume(ctx, list(px.columns), px.index)
    except (DataUnavailable, KeyError):
        dv = None
    costs = cost_figures(sw, wf, opens, rf, model, dv)
    seed = ctx.seed(BOOTSTRAP_SEED, "bootstrap")   # the bootstrap's and SPA's fixed seed, recorded (Task 3.3)
    boot = bootstrap_sharpe(oos, rf, reps=1000, alpha=0.10, seed=seed, use_kernels=True, threads=ctx.threads)
    cid = cell_id(strategy, universe)
    trials = pd.DataFrame({
        "cell_id": cid, "combo": range(len(sw.returns.columns)),
        "sr_pp": [M.sharpe_per_period(M.excess(sw.returns[c], rf)) for c in sw.returns.columns],
        "grid_version": trial_ledger.grid_version(grid), "params_hash": [params_hash(c) for c in sw.combos],
        "n_obs": len(sw.returns)}, columns=TRIAL_COLUMNS)
    reg = regime_table(oos)
    row = {"cell_id": cid, "strategy": strategy, "universe": universe, "status": "ok",
           "reason": None, "ran_at": datetime.now(UTC).isoformat(), "data_asof": str(px.index[-1].date()),
           "n_combos": int(len(sw.combos)), "holdout_return": M.total_return(oos), "sr_pp": sr, "n": int(len(ex)),
           "skew": sk, "kurt": ku, "psr": M.probabilistic_sharpe(sr, len(ex), sk, ku), "boot_lo5": boot["ci_low"],
           "pbo": cscv_pbo(sw.returns, PBO_S, threads=ctx.threads)["pbo"],
           "spa_p": spa_test(bench.fillna(0.0), sw.returns, seed=seed)["pvalue_consistent"],
           "oos_sharpe_ann": M.sharpe(ex), **costs, "selected_params": json.dumps(sw.combos[pick]),
           # the configuration the verdict was earned at (task 2.4): a reader can tell it from an older row's
           "fill": FILL, "max_gross": MAX_GROSS}
    return row, reg.assign(cell_id=row["cell_id"]), trials


def _costs_provenance(m: cost_file.CostModel) -> dict[str, Any]:
    """The cost file as the page reads it: path, version, and how many tiers are provisional (fix round 1, M2)."""
    return {"source": "costs", "path": cost_file.COSTS_REL, "version": m.version, "tiers": len(m.tickers),
            "provisional_tiers": sum(1 for t in m.tickers if m.source(t) == "provisional")}


def _costs_note(m: cost_file.CostModel) -> str:
    tiers = ", ".join(f"tier {k} {v:g} bp" for k, v in sorted(m.tiers_bps.items()))
    prov = sum(1 for t in m.tickers if m.source(t) == "provisional")
    return (f"costs from {cost_file.COSTS_REL} v{m.version}: half-spread by tier ({tiers}; {prov} of {len(m.tickers)} "
            f"tiers provisional, not yet measured), borrow {m.borrow_bps:g} bp a year on shorts, financing "
            f"{m.financing_spread_bps:g} bp a year over the T-bill rate on gross above 1; sweep at "
            + "/".join(f"{x:g}" for x in m.sweep) + "x the half-spread on the out-of-sample series; capacity at "
            f"{m.participation:.0%} of {m.adv_window}-session median dollar volume")


def _counted_trials(ledger: pd.DataFrame, trials: pd.DataFrame) -> pd.DataFrame:
    """The trials the DSR counts, one row each with its ``sharpe``: the ledger's distinct trials, plus the carried
    trials of a cell last run before the ledger (no ``params_hash``), until that cell runs again."""
    legacy = trials[trials["params_hash"].isna()] if "params_hash" in trials.columns else trials
    if legacy.empty:
        return ledger[["sharpe"]]
    return pd.concat([ledger[["sharpe"]], legacy[["sr_pp"]].rename(columns={"sr_pp": "sharpe"})], ignore_index=True)


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    from ..jobs.burst import in_burst
    from .io import previous_artifact, read_table

    ctx.seed(BOOTSTRAP_SEED, "bootstrap")   # recorded even when no cell runs tonight (Task 3.3)
    budget = float(params.get("budget_minutes", BUDGET_MINUTES)) * 60.0
    t0 = time.monotonic()
    # worker_only (task 3.7): the monthly freeze reads the newest worker farm artifact for the desk, so the worker's
    # farm never carries a burst run's cells forward into it; a burst farm run carries the worker's state forward
    prev = previous_artifact("farm.sweep", db_path=DB_PATH, worker_only=True)
    cells = read_table(prev[1], "cells") if prev else None
    regimes = read_table(prev[1], "regimes") if prev else None
    trials = read_table(prev[1], "trials") if prev else None
    carried_ledger = read_table(prev[1], "ledger") if prev else None
    cells = cells if cells is not None else pd.DataFrame(columns=["cell_id", "ran_at"])
    regimes = regimes if regimes is not None else pd.DataFrame(columns=["cell_id"])
    trials = trials if trials is not None else _no_trials()
    path = _ledger_path()
    if carried_ledger is not None and len(carried_ledger):
        trial_ledger.append_many(path, carried_ledger)   # a burst machine's ledger starts from the droplet's
    who = "burst" if in_burst() else "worker"
    ran_at = dict(zip(cells["cell_id"], cells["ran_at"], strict=True))
    order = sorted(farm_cells(), key=lambda c: (cell_id(*c) in ran_at, ran_at.get(cell_id(*c), "")))
    done = 0
    for s, u in order:
        if time.monotonic() - t0 >= budget:
            break
        ctx.check_cancelled()
        row, reg, tr = run_cell(s, u, ctx)
        cid = row["cell_id"]
        if len(tr):                                      # counted once run, whatever happens to the job after
            trial_ledger.append_many(path, tr.rename(columns={"sr_pp": "sharpe"}), source=who)
        cells = pd.concat([cells[cells["cell_id"] != cid], pd.DataFrame([row])], ignore_index=True)
        regimes = pd.concat([regimes[regimes["cell_id"] != cid], reg], ignore_index=True)
        kept = trials[trials["cell_id"] != cid]
        trials = pd.concat([kept, tr], ignore_index=True) if len(tr) else kept.reset_index(drop=True)
        done += 1
        ctx.progress(min(1.0, (time.monotonic() - t0) / budget) if budget else 1.0, cid)
    # the ledger as read: the farm's own earlier trials (and a burst's, through the door) -- derived, not market data
    ledger = ctx.read(f"trial-ledger: {trial_ledger.LEDGER_NAME}", trial_ledger.distinct(path), source="derived")
    counted = _counted_trials(ledger, trials)
    unledgered = len(counted) - len(ledger)
    board = leaderboard(cells, regimes, counted)
    n_pass = int((board["verdict"] == "PASS").sum()) if len(board) else 0
    n_fail = int((board["verdict"] == "FAIL").sum()) if len(board) else 0
    n_short = int((board["verdict"] == "INSUFFICIENT DATA").sum()) if len(board) else 0
    n_skip = int((cells.get("status") == "skipped").sum()) if len(cells) else 0
    asof = cells.loc[cells.get("status") == "ok", "data_asof"].min() if len(board) else None
    # PASS when a cell passes; FAIL when one fails and none passes; INSUFFICIENT DATA when no scored cell could be
    # judged (task 2.5: too few regimes with out-of-sample data is not a FAIL)
    verdict = "PASS" if n_pass else "FAIL" if n_fail else "INSUFFICIENT DATA"
    traded, scored = traded_configuration(cells)
    config = (f"{traded} of {scored} scored cells at gross {MAX_GROSS:g}, decided at the close and filled at the next "
              "open" + (f"; the other {scored - traded} were scored before that (the strategy's default leverage, "
                        "filled at the close) and await a re-run" if scored - traded else ""))
    return ArtifactSpec(
        tables={"cells": cells, "regimes": regimes, "trials": trials, "leaderboard": board, "ledger": ledger},
        data_asof=None if asof is None or pd.isna(asof) else str(asof),
        provenance=[{"source": "ohcamel-farm", "cells_run_tonight": done, "cells_total": len(farm_cells())},
                    _costs_provenance(cost_file.load()),
                    {"source": "trial-ledger", "path": trial_ledger.LEDGER_NAME, "distinct_trials": len(ledger),
                     "carried_unledgered": unledgered}],
        notes=[f"{done} cells run tonight; DSR deflated by {len(counted)} trials: every trial the farm has run "
               f"({len(ledger)} distinct in the trial ledger"
               + (f", {unledgered} carried from before it" if unledgered else "") + f"); {config}; walk-forward "
               f"{WF_IS}/{WF_OOS} sessions", _costs_note(cost_file.load())],
        verdict=verdict,
        verdict_detail=f"{n_pass} PASS · {n_fail} FAIL · {n_short} INSUFFICIENT DATA · {n_skip} SKIPPED · ADVISORY")
