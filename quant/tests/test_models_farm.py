"""Lane M, M5: farm grids, cells and farm-wide deflation (compute plan M5; Review Focus 5)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.backtest import metrics as M
from ohcamel_quant.backtest.strategies import STRATEGIES, get_strategy
from ohcamel_quant.models.farm import GRID_MAX, farm_cells, farm_dsr, levels_for, strategy_grid


def test_levels_by_hand():
    # floor(256^(1/p)) capped at 16 and floored at 2: p=1 -> 16, 2 -> 16, 3 -> 6 (216), 4 -> 4 (256), 6 -> 2 (64).
    assert [levels_for(p) for p in (1, 2, 3, 4, 6)] == [16, 16, 6, 4, 2]


def test_every_grid_fits_the_cap_and_stays_in_bounds():
    for key, spec in STRATEGIES.items():
        g = strategy_grid(spec)
        assert math.prod(len(v) for v in g.values()) <= GRID_MAX, key
        for name, vals in g.items():
            p = spec.param(name)
            assert min(vals) >= p.min and max(vals) <= p.max and vals == sorted(set(vals)), (key, name)
    assert strategy_grid(get_strategy("sma_trend")) == {"months": [2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18, 20, 21, 23, 24]}


def test_cells_skip_benchmarks():
    cells = farm_cells()
    assert ("buy_and_hold", "us_equity_indices") not in cells and ("xsmom", "sectors") in cells
    assert all(STRATEGIES[s].category != "benchmark" for s, _ in cells)


def test_farm_dsr_deflates_by_every_trial_in_the_farm():
    """One row; the same Sharpe deflated by 16 trials (its cell) and by 4,000 (the farm) -- the farm's is lower.
    Reference: backtest.metrics.deflated_sharpe with n_trials and V[SR] of all trials."""
    rng = np.random.default_rng(2)
    trial = rng.normal(0.0, 0.02, 4000)
    row = pd.DataFrame([{"sr_pp": 0.06, "n": 1500, "skew": -0.3, "kurt": 5.0}])
    farm = farm_dsr(row, trial).iloc[0]
    cell = M.deflated_sharpe(0.06, 1500, -0.3, 5.0, 16, float(np.var(trial[:16], ddof=1)))["dsr"]
    ref = M.deflated_sharpe(0.06, 1500, -0.3, 5.0, 4000, float(np.var(trial, ddof=1)))["dsr"]
    assert farm == pytest.approx(ref) and farm < cell
