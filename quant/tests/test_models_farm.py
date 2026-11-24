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


def test_every_cell_fits_its_strategys_asset_count_and_pairs_coint_on_sectors_is_not_a_cell():
    """Completion task 2.4: pairs_coint takes exactly two tickers and sectors holds eleven, so it was never a cell
    that could run; every cell left fits min_assets <= len(universe) <= max_assets."""
    from ohcamel_quant.data.market import load_universes

    members = {k: len(v["members"]) for k, v in load_universes()["universes"].items()}
    cells = farm_cells()
    assert ("pairs_coint", "sectors") not in cells
    for s, u in cells:
        spec = STRATEGIES[s]
        assert spec.min_assets <= members[u] <= (spec.max_assets or members[u]), (s, u)


def test_a_cell_that_does_not_fit_its_strategy_is_refused(monkeypatch):
    import dataclasses

    bad = dataclasses.replace(STRATEGIES["pairs_coint"], suggested_universes=("sectors",))
    monkeypatch.setitem(STRATEGIES, "pairs_coint", bad)
    with pytest.raises(ValueError, match="pairs_coint@sectors"):
        farm_cells()


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


def _ledger(n: int, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"cell_id": [f"c{i % 7}@u" for i in range(n)], "grid_version": "g1",
                         "params_hash": [f"sha256:{i}" for i in range(n)], "sharpe": rng.normal(0.0, 0.02, n),
                         "n_obs": 1500})


def test_farm_dsr_counts_the_ledgers_distinct_trials():
    """Completion task 2.4 (spec: "DSR's N is the ledger's distinct trials, not tonight's grid"): the ledger's
    frame gives N (its rows: one a distinct trial) and V[SR] (their Sharpes)."""
    led = _ledger(900)
    row = pd.DataFrame([{"sr_pp": 0.06, "n": 1500, "skew": -0.3, "kurt": 5.0}])
    got = farm_dsr(row, led).iloc[0]
    ref = M.deflated_sharpe(0.06, 1500, -0.3, 5.0, 900, float(np.var(led["sharpe"], ddof=1)))["dsr"]
    assert got == pytest.approx(ref, rel=1e-12)
    assert farm_dsr(row, led.iloc[:16]).iloc[0] > got            # tonight's one grid would deflate less


def test_a_trial_without_a_sharpe_is_still_a_trial():
    led = _ledger(40)
    led.loc[[3, 9], "sharpe"] = np.nan
    row = pd.DataFrame([{"sr_pp": 0.06, "n": 1500, "skew": 0.0, "kurt": 3.0}])
    ref = M.deflated_sharpe(0.06, 1500, 0.0, 3.0, 40, float(np.nanvar(led["sharpe"], ddof=1)))["dsr"]
    assert farm_dsr(row, led).iloc[0] == pytest.approx(ref, rel=1e-12)


def test_farm_dsr_with_fewer_than_two_trials_is_missing_not_the_psr():
    row = pd.DataFrame([{"sr_pp": 0.06, "n": 1500, "skew": 0.0, "kurt": 3.0}])
    assert farm_dsr(row, _ledger(1)).isna().all() and farm_dsr(row, _ledger(0)).isna().all()


def test_the_traded_configuration_is_counted_from_each_row():
    """Fix round 1 (M2): rows scored before completion task 2.4 carry no fill; the farm's note counts, never claims."""
    from ohcamel_quant.models.farm import traded_configuration

    cells = pd.DataFrame([{"status": "ok", "fill": "open", "max_gross": 1.0}, {"status": "ok"},
                          {"status": "skipped", "fill": "open", "max_gross": 1.0}])
    assert traded_configuration(cells) == (1, 2)
    assert traded_configuration(pd.DataFrame([{"status": "ok"}])) == (0, 1)
    assert traded_configuration(pd.DataFrame()) == (0, 0)
