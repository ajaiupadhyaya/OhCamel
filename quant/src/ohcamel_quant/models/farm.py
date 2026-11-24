"""P4: the strategy farm's grids, cells and leaderboard (compute plan M5).

Grids are fixed in code before any result: each sweepable parameter gets
``levels_for(p)`` evenly spaced values over its declared [min, max] (ints
rounded and de-duplicated), so a strategy never exceeds ``GRID_MAX``
combinations. The leaderboard's DSR deflates each row by the number of trials
in the WHOLE farm and their Sharpe dispersion (Bailey & Lopez de Prado 2014):
ranking thousands of trials and deflating each by its own cell would be the
textbook uncorrected scan. Since completion task 2.4 those trials are the trial
ledger's (:mod:`.trial_ledger`): every trial the farm has ever run, not only
tonight's grids.

A cell is a (strategy, universe) pair from the strategy's ``suggested_universes``
whose universe holds between ``min_assets`` and ``max_assets`` names;
:func:`farm_cells` refuses one that does not (``pairs_coint`` takes two tickers,
so ``pairs_coint x sectors``, eleven, is not a cell).
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from ..backtest import metrics as M
from ..backtest.strategies import STRATEGIES, StrategySpec
from .verdict import charter_verdict

GRID_MAX = 256
MAX_LEVELS = 16


def levels_for(n_params: int) -> int:
    return max(2, min(MAX_LEVELS, int(math.floor(GRID_MAX ** (1.0 / n_params) + 1e-9))))


def strategy_grid(spec: StrategySpec) -> dict[str, list]:
    ps = [p for p in spec.params if p.sweepable]
    if not ps:
        return {}
    n = levels_for(len(ps))
    grid: dict[str, list] = {}
    for p in ps:
        vals = np.linspace(float(p.min), float(p.max), n)
        grid[p.name] = (sorted({int(round(v)) for v in vals}) if p.type == "int"
                        else sorted({round(float(v), 6) for v in vals}))
    return grid


def farm_cells() -> list[tuple[str, str]]:
    """Every non-benchmark strategy on each universe it suggests. Raises ``ValueError`` naming a cell whose
    universe's size is outside the strategy's ``[min_assets, max_assets]``: it could never run."""
    from ..data.market import load_universes

    size = {k: len(v["members"]) for k, v in load_universes()["universes"].items()}
    cells = [(k, u) for k, s in STRATEGIES.items() if s.category != "benchmark" for u in s.suggested_universes]
    for k, u in cells:
        spec, n = STRATEGIES[k], size.get(u)
        if n is None or n < spec.min_assets or (spec.max_assets is not None and n > spec.max_assets):
            raise ValueError(f"{cell_id(k, u)} is not a farm cell: {u} holds {n} names and {k} takes "
                             f"{spec.min_assets} to {spec.max_assets or 'any number of'}")
    return cells


def cell_id(strategy: str, universe: str) -> str:
    return f"{strategy}@{universe}"


def _trial_sharpes(trials: pd.DataFrame | np.ndarray) -> np.ndarray:
    """One per-period Sharpe per distinct trial: the ledger's ``sharpe`` column (``trial_ledger.distinct``), or an
    array of them."""
    if isinstance(trials, pd.DataFrame):
        return trials["sharpe"].to_numpy(dtype=float)
    return np.asarray(trials, dtype=float).reshape(-1)


def farm_dsr(cells: pd.DataFrame, trials: pd.DataFrame | np.ndarray) -> pd.Series:
    """Each row's DSR with ``N`` = the number of distinct trials (``trials``: the ledger's distinct frame, one row a
    trial -- completion task 2.4 -- or their Sharpes) and ``V[SR]`` = the variance of the finite ones. A trial with
    no Sharpe (a flat book) still counts in ``N``. Fewer than two trials, or fewer than two finite Sharpes, leave the
    DSR missing (``expected_max_sharpe`` raises): a missing DSR fails its gate, where the old benchmark of 0 made it
    the PSR."""
    t = _trial_sharpes(trials)
    fin = t[np.isfinite(t)]
    n = int(t.size)
    var = float(np.var(fin, ddof=1)) if fin.size > 1 else float("nan")
    try:
        M.expected_max_sharpe(n, var)
    except ValueError:
        return pd.Series(np.nan, index=cells.index, dtype=float)
    return pd.Series([M.deflated_sharpe(r.sr_pp, int(r.n), r.skew, r.kurt, n, var)["dsr"]
                      for r in cells.itertuples()], index=cells.index, dtype=float)


def leaderboard_cost_columns() -> tuple[str, ...]:
    """The leaderboard's cost columns (completion task 2.3): one-way annual turnover and capacity of the out-of-sample
    path, and its Sharpe at each of the cost file's half-spread multiples (``sr_at_0x`` ... ``sr_at_6x``)."""
    from ..backtest import costs

    return ("turnover", "capacity_usd", *(f"sr_at_{m:g}x" for m in costs.load().sweep))


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return f if math.isfinite(f) else float("nan")


def _cost_curve(raw: Any) -> list[dict[str, Any]] | None:
    """The cell's stored 0/5/15/30 bps curve (JSON records); None when absent or unreadable."""
    if not isinstance(raw, str):
        return None
    try:
        rows = json.loads(raw)
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def leaderboard(cells: pd.DataFrame, regimes: pd.DataFrame, trials: pd.DataFrame | np.ndarray) -> pd.DataFrame:
    """The verdict table; ``trials`` as :func:`farm_dsr` takes them (the ledger's distinct trials)."""
    ok = cells[cells["status"] == "ok"].copy()
    if ok.empty:
        return pd.DataFrame(columns=["cell_id", "verdict", "detail", "dsr_farm"])
    ok["dsr_farm"] = farm_dsr(ok, trials)
    extra = leaderboard_cost_columns()
    rows = []
    for r in ok.itertuples():
        reg = regimes[regimes["cell_id"] == r.cell_id]
        v = charter_verdict(holdout_return=r.holdout_return, dsr=r.dsr_farm, psr=r.psr, boot_lo5=r.boot_lo5,
                            regimes=reg, pbo=r.pbo, costs=_cost_curve(getattr(r, "cost_curve", None)))
        rows.append({"cell_id": r.cell_id, "strategy": r.strategy, "universe": r.universe, "verdict": v.value,
                     "detail": v.detail, "dsr_farm": r.dsr_farm, "psr": r.psr, "oos_sharpe_ann": r.oos_sharpe_ann,
                     "holdout_return": r.holdout_return, "boot_lo5": r.boot_lo5, "pbo": r.pbo, "spa_p": r.spa_p,
                     "n_combos": r.n_combos, "selected_params": r.selected_params, "data_asof": r.data_asof,
                     "ran_at": r.ran_at,
                     # a cell carried forward from before the cost file has none: NaN, never a made-up figure
                     **{k: _num(getattr(r, k, None)) for k in extra}})
    return pd.DataFrame(rows).sort_values(["dsr_farm"], ascending=False, na_position="last").reset_index(drop=True)
