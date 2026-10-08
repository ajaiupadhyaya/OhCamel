"""P4: the strategy farm's grids, cells and leaderboard (compute plan M5).

Grids are fixed in code before any result: each sweepable parameter gets
``levels_for(p)`` evenly spaced values over its declared [min, max] (ints
rounded and de-duplicated), so a strategy never exceeds ``GRID_MAX``
combinations. The leaderboard's DSR deflates each row by the number of trials
in the WHOLE farm and their Sharpe dispersion (Bailey & Lopez de Prado 2014):
ranking thousands of trials and deflating each by its own cell would be the
textbook uncorrected scan.
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
    return [(k, u) for k, s in STRATEGIES.items() if s.category != "benchmark" for u in s.suggested_universes]


def cell_id(strategy: str, universe: str) -> str:
    return f"{strategy}@{universe}"


def farm_dsr(cells: pd.DataFrame, trial_srs: np.ndarray) -> pd.Series:
    t = np.asarray(trial_srs, dtype=float)
    t = t[np.isfinite(t)]
    var = float(np.var(t, ddof=1)) if t.size > 1 else float("nan")
    return pd.Series([M.deflated_sharpe(r.sr_pp, int(r.n), r.skew, r.kurt, int(t.size), var)["dsr"]
                      for r in cells.itertuples()], index=cells.index, dtype=float)


def _cost_curve(raw: Any) -> list[dict[str, Any]] | None:
    """The cell's stored 0/5/15/30 bps curve (JSON records); None when absent or unreadable."""
    if not isinstance(raw, str):
        return None
    try:
        rows = json.loads(raw)
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def leaderboard(cells: pd.DataFrame, regimes: pd.DataFrame, trial_srs: np.ndarray) -> pd.DataFrame:
    ok = cells[cells["status"] == "ok"].copy()
    if ok.empty:
        return pd.DataFrame(columns=["cell_id", "verdict", "detail", "dsr_farm"])
    ok["dsr_farm"] = farm_dsr(ok, trial_srs)
    rows = []
    for r in ok.itertuples():
        reg = regimes[regimes["cell_id"] == r.cell_id]
        v = charter_verdict(holdout_return=r.holdout_return, dsr=r.dsr_farm, psr=r.psr, boot_lo5=r.boot_lo5,
                            regimes=reg, pbo=r.pbo, costs=_cost_curve(getattr(r, "cost_curve", None)))
        rows.append({"cell_id": r.cell_id, "strategy": r.strategy, "universe": r.universe, "verdict": v.value,
                     "detail": v.detail, "dsr_farm": r.dsr_farm, "psr": r.psr, "oos_sharpe_ann": r.oos_sharpe_ann,
                     "holdout_return": r.holdout_return, "boot_lo5": r.boot_lo5, "pbo": r.pbo, "spa_p": r.spa_p,
                     "n_combos": r.n_combos, "selected_params": r.selected_params, "data_asof": r.data_asof,
                     "ran_at": r.ran_at})
    return pd.DataFrame(rows).sort_values(["dsr_farm"], ascending=False, na_position="last").reset_index(drop=True)
