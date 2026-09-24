"""The runner (``battery/run.py``), on the 2018-2020 ``fixtures/bars/`` slice.

Everything here asserts the SHAPE of what comes out and the discipline that
produced it -- which window selected, which series reached which gate, how
many trials reached ``deflated_sharpe``, that the cost sweep never
re-selected, that no return from before the holdout entered it -- never a
value: the numbers on this slice are not a result and must not be treated as
one. No test runs EXP-A01's own configuration (Task 13's job).
"""

from __future__ import annotations

import inspect
import platform
import subprocess
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml
from click.testing import CliRunner
from conftest import SMOKE_CONFIG, TINY_CONFIG
from fdq.backtest.engine import BacktestConfig, run_backtest
from fdq.frictions.config import FrictionConfig, load_friction_config
from fdq.frictions.emulator import BrokerEmulator
from fdq.strategies.base import Strategy as FdqStrategy
from fdq.strategies.base import StrategySpec
from fdq.strategies.trend import MACrossover
from fdq.validation import pbo as fdq_pbo_mod
from fdq.validation import walkforward as walkforward_mod
from fdq.validation.metrics import sharpe as fdq_sharpe
from fdq.validation.walkforward import WalkForwardResult, grid_combos, make_folds

import ohcamel_research.battery.gates as gates_mod
import ohcamel_research.battery.pbo as pbo_mod
import ohcamel_research.battery.run as run_mod
from ohcamel_research import REPO_ROOT
from ohcamel_research.battery import GATES_VERSION, Refused
from ohcamel_research.battery.config import (
    ConfigError,
    GridSpec,
    Window,
    validate_config,
)
from ohcamel_research.battery.data import ProvenanceError, load_bars, load_macro, wide
from ohcamel_research.battery.run import (
    FDQ_EXIT_SPREAD_CORRECTION,
    NONE_MEASURED,
    PBO_NOTE,
    SPREAD_NOTES,
    SWEEP_NOTE,
    RunRefused,
    describe_measure,
    evaluate_fixed,
    holdout_returns,
    join_series,
    load_friction,
    pooled_capacity,
    pooled_turnover,
    run,
    select_params,
    select_strategy,
    sweep_friction,
    trade_weights,
    verdict_line,
)
from ohcamel_research.cli import cli
from ohcamel_research.manifest import BATTERY_REL, Manifest, compute_verdict, manifest_path
from ohcamel_research.manifest import Window as ManifestWindow

BARS = REPO_ROOT / "fixtures" / "bars"
FRICTION_FILE = REPO_ROOT / "research" / "config" / "friction_v1.yaml"
# The file as fdq reads it, uncorrected: for tests of fdq itself and of
# helpers handed an explicit friction.
FRICTION = load_friction_config(FRICTION_FILE)
# The base friction the runner hands fdq: every spread in the file doubled.
BASE = load_friction(FRICTION_FILE, "1.0.0")
# The same, built here from the file and a literal 2, independently of the
# runner's code, so a test comparing against it cannot agree with a bug.
DOUBLED = replace(
    FRICTION,
    spread_bps_default=2.0 * FRICTION.spread_bps_default,
    spread_bps_by_symbol={k: 2.0 * v for k, v in FRICTION.spread_bps_by_symbol.items()},
)
MACRO = load_macro(REPO_ROOT / "fixtures" / "macro" / "macro.parquet")
SPY = wide(load_bars(BARS, ["SPY"]))
GATE_ORDER = [
    "holdout_positive",
    "dsr",
    "psr",
    "bootstrap_sharpe_lower5",
    "regimes_positive",
    "pbo",
    "cost_sweep",
]


def _smoke_cfg(text: str = SMOKE_CONFIG):
    return validate_config(yaml.safe_load(text), REPO_ROOT)


def _fixture_days(symbol: str, start: str, end: str) -> list[pd.Timestamp]:
    w = wide(load_bars(BARS, [symbol]))
    idx = pd.DatetimeIndex(w.index)
    return list(idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))])


# --------------------------------------------------------------------------
# One instrumented run of the smoke config, shared by the tests below.
# --------------------------------------------------------------------------


@dataclass
class Backtest:
    params: dict[str, Any]
    friction: FrictionConfig
    start: date | None
    end: date | None
    macro: Any


@dataclass
class Recorded:
    root: Path
    exp: Path
    manifests: list[Manifest] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    macro_loaded: Any = None
    macros: list[Any] = field(default_factory=list)
    wf_calls: list[dict[str, Any]] = field(default_factory=list)
    is_calls: list[dict[str, Any]] = field(default_factory=list)
    is_frictions: list[FrictionConfig] = field(default_factory=list)
    selection_spans: list[tuple[pd.Timestamp, pd.Timestamp]] = field(default_factory=list)
    inner_frictions: list[FrictionConfig] = field(default_factory=list)
    backtests: list[Backtest] = field(default_factory=list)
    pbo_matrices: list[np.ndarray] = field(default_factory=list)
    pbo_port: list[tuple[np.ndarray, float]] = field(default_factory=list)
    pbo_fdq: list[tuple[np.ndarray, float]] = field(default_factory=list)
    sweep_calls: list[tuple[tuple[Any, ...], dict[str, Any], tuple[pd.Series, pd.Series]]] = field(
        default_factory=list
    )
    dsr_calls: list[tuple[pd.Series, np.ndarray]] = field(default_factory=list)
    psr_series: list[pd.Series] = field(default_factory=list)
    bootstrap_calls: list[tuple[pd.Series, tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list
    )
    regime_series: list[pd.Series] = field(default_factory=list)
    verdict_calls: list[tuple[list[dict[str, Any]], float]] = field(default_factory=list)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory, mirror_builder) -> Recorded:
    root = tmp_path_factory.mktemp("mirror")
    exp = mirror_builder(root)
    rec = Recorded(root=root, exp=exp)

    real_commit = run_mod.assert_battery_committed
    real_load = run_mod.load_experiment_config
    real_macro = run_mod.load_macro
    real_wf = run_mod.walk_forward
    real_is = run_mod.in_sample_return_matrix
    real_inner = walkforward_mod.run_backtest
    real_bt = run_mod.run_backtest
    real_pbo = run_mod.probability_backtest_overfitting
    real_fdq_pbo = run_mod.fdq_probability_backtest_overfitting
    real_eval = run_mod.evaluate_fixed
    real_dsr = gates_mod.deflated_sharpe
    real_psr = run_mod.psr_gate
    real_boot = run_mod.bootstrap_gate
    real_regime = run_mod.regime_gate
    real_verdict = run_mod.compute_verdict

    def commit(r):
        rec.order.append("assert_battery_committed")
        return real_commit(r)

    def load(*a, **k):
        rec.order.append("load_experiment_config")
        return real_load(*a, **k)

    def macro_loaded(path):
        rec.macro_loaded = real_macro(path)
        return rec.macro_loaded

    def wf(name, base, grid, bars, tier, friction, macro, **kw):
        rec.selection_spans.append((bars.index.min(), bars.index.max()))
        rec.macros.append(macro)
        res = real_wf(name, base, grid, bars, tier, friction, macro, **kw)
        rec.wf_calls.append(
            {"name": name, "base": dict(base), "grid": grid, "friction": friction, "result": res}
        )
        return res

    def in_sample(name, base, grid, bars, tier, friction, macro, is_start, is_end, seed):
        rec.selection_spans.append((bars.index.min(), bars.index.max()))
        rec.is_frictions.append(friction)
        rec.macros.append(macro)
        matrix, sharpes = real_is(
            name, base, grid, bars, tier, friction, macro, is_start, is_end, seed
        )
        rec.is_calls.append(
            {
                "name": name,
                "symbol": base["symbol"],
                "grid": grid,
                "span": (is_start, is_end),
                "matrix": matrix,
                "sharpes": sharpes,
            }
        )
        return matrix, sharpes

    def inner(strategy, bars, config, macro=None, start=None, end=None):
        rec.inner_frictions.append(config.friction)
        rec.macros.append(macro)
        return real_inner(strategy, bars, config, macro, start, end)

    def backtest(strategy, bars, config, macro=None, start=None, end=None):
        rec.backtests.append(Backtest(dict(strategy.params), config.friction, start, end, macro))
        rec.macros.append(macro)
        return real_bt(strategy, bars, config, macro, start, end)

    def pbo(matrix, *a, **k):
        rec.pbo_matrices.append(np.array(matrix, copy=True))
        out = real_pbo(matrix, *a, **k)
        rec.pbo_port.append((np.array(matrix, copy=True), out))
        return out

    def fdq_pbo(matrix, *a, **k):
        out = real_fdq_pbo(matrix, *a, **k)
        rec.pbo_fdq.append((np.array(matrix, copy=True), out))
        return out

    def evaluate(*a, **k):
        out = real_eval(*a, **k)
        rec.sweep_calls.append((a, dict(k), out))
        return out

    def dsr(returns, trial_sharpes):
        rec.dsr_calls.append((returns.copy(), np.array(trial_sharpes, copy=True)))
        return real_dsr(returns, trial_sharpes)

    def psr(returns, *a, **k):
        rec.psr_series.append(returns.copy())
        return real_psr(returns, *a, **k)

    def boot(returns, *a, **k):
        rec.bootstrap_calls.append((returns.copy(), a, dict(k)))
        return real_boot(returns, *a, **k)

    def regime(returns, *a, **k):
        rec.regime_series.append(returns.copy())
        return real_regime(returns, *a, **k)

    def verdict(gates, pbo):
        rec.verdict_calls.append((list(gates), pbo))
        return real_verdict(gates, pbo)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_mod, "compute_verdict", verdict)
        mp.setattr(run_mod, "assert_battery_committed", commit)
        mp.setattr(run_mod, "load_experiment_config", load)
        mp.setattr(run_mod, "load_macro", macro_loaded)
        mp.setattr(run_mod, "walk_forward", wf)
        mp.setattr(run_mod, "in_sample_return_matrix", in_sample)
        mp.setattr(walkforward_mod, "run_backtest", inner)
        mp.setattr(run_mod, "run_backtest", backtest)
        mp.setattr(run_mod, "probability_backtest_overfitting", pbo)
        mp.setattr(run_mod, "fdq_probability_backtest_overfitting", fdq_pbo)
        mp.setattr(run_mod, "evaluate_fixed", evaluate)
        mp.setattr(gates_mod, "deflated_sharpe", dsr)
        mp.setattr(run_mod, "psr_gate", psr)
        mp.setattr(run_mod, "bootstrap_gate", boot)
        mp.setattr(run_mod, "regime_gate", regime)
        rec.manifests = run(exp, write=True, repo_root=root)
    return rec


def _own_wf(rec: Recorded, symbol: str) -> WalkForwardResult:
    (call,) = [
        c
        for c in rec.wf_calls
        if c["base"]["symbol"] == symbol and c["grid"] == {"fast": [1], "slow": [50, 100]}
    ]
    return call["result"]


def _folds(symbol: str):
    return make_folds(pd.DatetimeIndex(_fixture_days(symbol, "2018-01-02", "2019-12-31")), 2)


def _fold_spans(symbol: str) -> set[tuple[date, date]]:
    return {(f.test_start, f.test_end) for f in _folds(symbol)}


def test_one_manifest_per_strategy_in_the_configs_order_and_shape(recorded):
    ms = recorded.manifests
    assert [m.slug for m in ms] == ["smoke_spy", "smoke_tlt"]
    for m, symbol in zip(ms, ["SPY", "TLT"], strict=True):
        assert (m.experiment, m.strategy, m.symbol) == ("EXP-SMOKE", "ma_crossover", symbol)
        combos = [{"symbol": symbol, **c} for c in grid_combos({"fast": [1], "slow": [50, 100]})]
        assert m.selected_params in combos
        assert m.selection_window == ManifestWindow("2018-01-02", "2019-12-31")
        assert m.holdout_window == ManifestWindow("2020-01-02", "2020-12-31")
        assert m.gates_version == GATES_VERSION
        assert [g["name"] for g in m.gates] == GATE_ORDER
        for g in m.gates:
            if g["name"] in ("pbo", "cost_sweep"):
                assert g["passed"] is None
            else:
                assert isinstance(g["passed"], bool)
        assert m.verdict == compute_verdict(m.gates, m.pbo)
        assert m.verdict_line.startswith(m.verdict + ":")
        assert m.dsr.unit == "fold_trials"
        assert m.dsr.returns_series == "walk_forward_oos+holdout"
        assert 0.0 <= m.psr <= 1.0 and 0.0 <= m.pbo <= 1.0
        assert m.turnover is None or m.turnover >= 0.0
        assert m.capacity is None or m.capacity > 0.0
        assert m.python_version == platform.python_version()
        assert all(note in m.notes for note in SPREAD_NOTES)
        assert PBO_NOTE in m.notes and SWEEP_NOTE in m.notes
        sweep = m.gates[GATE_ORDER.index("cost_sweep")]["detail"]
        assert set(sweep["sharpe_by_bps"]) == {"0.0", "5.0", "15.0", "30.0"}
        assert set(sweep["total_return_by_bps"]) == {"0.0", "5.0", "15.0", "30.0"}
        assert set(sweep["holdout_total_return_by_bps"]) == {"0.0", "5.0", "15.0", "30.0"}
        assert set(sweep["holdout_sharpe_by_bps"]) == {"0.0", "5.0", "15.0", "30.0"}
        assert sweep["exit_spread_correction"] == FDQ_EXIT_SPREAD_CORRECTION == 2.0


def test_the_verdict_is_the_manifest_modules_rule_over_every_gate(recorded):
    # One call per strategy, handed the whole gate table and the PBO: the
    # runner keeps no rule of its own.
    assert len(recorded.verdict_calls) == len(recorded.manifests)
    for m, (gates, pbo) in zip(recorded.manifests, recorded.verdict_calls, strict=True):
        assert tuple(gates) == m.gates
        assert pbo == m.pbo


def test_the_committed_check_runs_before_anything_is_read(recorded):
    assert recorded.order[0] == "assert_battery_committed"
    assert recorded.order.index("load_experiment_config") > 0


def test_the_run_wrote_manifests_that_load_back_equal(recorded):
    for m in recorded.manifests:
        path = manifest_path(recorded.exp, m.slug)
        assert path.is_file()
        assert Manifest.load(path) == m


def test_the_hashes_record_the_battery_the_config_and_every_fixture_each_strategy_read(recorded):
    spy, tlt = recorded.manifests
    battery = sorted(
        p.relative_to(recorded.root).as_posix()
        for p in (recorded.root / BATTERY_REL).rglob("*.py")
        if "__pycache__" not in p.parts
    )
    shared = [
        "research/experiments/EXP-SMOKE/config.yaml",
        "research/config/friction_v1.yaml",
        "fixtures/macro/macro.parquet",
        "fixtures/macro/macro.parquet.meta.json",
        "research/uv.lock",
    ]
    for m in (spy, tlt):
        assert [k for k in m.hashes if k.startswith(BATTERY_REL.as_posix())] == battery
        for key in shared:
            assert key in m.hashes
    assert {"fixtures/bars/SPY.parquet", "fixtures/bars/SPY.parquet.meta.json"} <= set(spy.hashes)
    assert {"fixtures/bars/TLT.parquet", "fixtures/bars/TLT.parquet.meta.json"} <= set(tlt.hashes)
    assert "fixtures/bars/TLT.parquet" not in spy.hashes
    assert "fixtures/bars/SPY.parquet" not in tlt.hashes


def test_selection_sees_the_selection_window_alone(recorded):
    # Every walk-forward and in-sample run -- this experiment's own and the
    # prior's re-run -- was handed the selection window's bars and nothing
    # else: not one bar of the holdout reached a selection.
    assert len(recorded.selection_spans) == 4 + 2
    for first, last in recorded.selection_spans:
        assert (first, last) == (pd.Timestamp("2018-01-02"), pd.Timestamp("2019-12-31"))
    for c in recorded.wf_calls:
        assert c["result"].oos_returns.index.max() <= pd.Timestamp("2019-12-31")


def test_every_fold_trial_in_the_family_reaches_deflated_sharpe(recorded):
    assert len(recorded.dsr_calls) == 2
    spy_calls = [c for c in recorded.wf_calls if c["base"]["symbol"] == "SPY"]
    own, prior_ma, prior_donchian = (c["result"] for c in spy_calls)
    assert [c["name"] for c in spy_calls] == ["ma_crossover", "ma_crossover", "donchian"]

    _, spy_trials = recorded.dsr_calls[0]
    # 2 + 2 re-run fold-trials from the prior, plus 2 configurations x 2 folds.
    assert spy_trials.size == 8
    np.testing.assert_array_equal(
        spy_trials,
        np.concatenate([prior_ma.trial_sharpes, prior_donchian.trial_sharpes, own.trial_sharpes]),
    )
    spy = recorded.manifests[0]
    assert spy.dsr.trial_count == 8
    assert spy.dsr.trial_sharpe_sources == {
        "PRIOR-SPY rerun[0] ma_crossover SPY": 2,
        "PRIOR-SPY rerun[1] donchian SPY": 2,
        "EXP-SMOKE smoke_spy walk_forward": 4,
    }

    _, tlt_trials = recorded.dsr_calls[1]
    np.testing.assert_array_equal(tlt_trials, _own_wf(recorded, "TLT").trial_sharpes)
    assert tlt_trials.size == 4
    assert recorded.manifests[1].dsr.trial_sharpe_sources == {"EXP-SMOKE smoke_tlt walk_forward": 4}


def test_the_dsr_reads_the_walk_forward_series_then_the_holdout_series(recorded):
    for m, (returns, _) in zip(recorded.manifests, recorded.dsr_calls, strict=True):
        start = pd.Timestamp(m.holdout_window.start)
        oos = returns[returns.index < start]
        wf = _own_wf(recorded, m.symbol).oos_returns
        assert list(oos.index) == list(wf.index)
        np.testing.assert_array_equal(oos.to_numpy(), wf.to_numpy())
        hold = returns[returns.index >= start]
        assert list(hold.index) == _fixture_days(m.symbol, "2020-01-02", "2020-12-31")


def test_the_manifest_carries_each_series_numbers_apart(recorded):
    # Task 13's report reads the walk-forward's and the holdout's own numbers
    # from the manifest, never from a recomputation outside the battery.
    for m, (returns, _) in zip(recorded.manifests, recorded.dsr_calls, strict=True):
        series = m.gates[GATE_ORDER.index("dsr")]["detail"]["series"]
        assert set(series) == {"walk_forward_oos", "holdout", "walk_forward_oos+holdout"}
        for part in series.values():
            assert set(part) == {"first", "last", "bars", "sharpe", "max_drawdown", "total_return"}
            assert part["max_drawdown"] <= 0.0
        hold = series["holdout"]
        assert hold["first"] == "2020-01-02"
        assert hold["total_return"] == pytest.approx(m.gates[0]["value"])
        joined = series["walk_forward_oos+holdout"]
        assert joined["bars"] == len(returns) == series["walk_forward_oos"]["bars"] + hold["bars"]
        assert joined["total_return"] == pytest.approx(float((1.0 + returns).prod() - 1.0))


def test_no_return_dated_before_the_holdout_start_enters_the_holdout(recorded):
    # The holdout gate's own series starts on the first bar of 2020, not the
    # last bar of 2019 (one bar early) and not the second bar of 2020 (one
    # bar late), and holds every bar of the window.
    days = _fixture_days("SPY", "2020-01-02", "2020-12-31")
    assert days[0] == pd.Timestamp("2020-01-02")
    for m in recorded.manifests:
        detail = m.gates[GATE_ORDER.index("holdout_positive")]["detail"]
        assert detail["first"] == "2020-01-02"
        assert detail["last"] == "2020-12-31"
        assert detail["bars"] == len(days)
        assert detail["params"] == m.selected_params


def test_the_cost_sweep_re_evaluates_the_selected_params_and_never_re_selects(recorded):
    # Every selection-side run -- walk_forward, in_sample_return_matrix, and
    # every backtest inside them -- saw the base friction only: its
    # per-symbol table intact (doubled), never a sweep level's cleared one.
    selection_tables = (
        [c["friction"].spread_bps_by_symbol for c in recorded.wf_calls]
        + [f.spread_bps_by_symbol for f in recorded.is_frictions]
        + [f.spread_bps_by_symbol for f in recorded.inner_frictions]
    )
    assert selection_tables
    assert all(t == DOUBLED.spread_bps_by_symbol for t in selection_tables)
    # Selection ran once per strategy (plus the prior's two re-runs), not
    # once per sweep level.
    assert len(recorded.wf_calls) == 4
    assert len(recorded.is_frictions) == 2

    sweep = [b for b in recorded.backtests if not b.friction.spread_bps_by_symbol]
    assert len(sweep) == 2 * 4 * (2 + 1)  # strategies x levels x (folds + holdout)
    holdout_span = (date(2020, 1, 2), date(2020, 12, 31))
    for m in recorded.manifests:
        wf = _own_wf(recorded, m.symbol)
        folds = _folds(m.symbol)
        mine = [b for b in sweep if b.params["symbol"] == m.symbol]
        # Each fold's span ran that fold's own fold_params -- the series the
        # gates read -- and the holdout ran selected_params: all chosen at
        # the base friction, none chosen at a sweep level.
        for b in mine:
            span = (b.start, b.end)
            if span == holdout_span:
                assert b.params == m.selected_params
            else:
                i = [(f.test_start, f.test_end) for f in folds].index(span)
                assert b.params == dict(wf.fold_params[i])
        assert Counter(b.friction.spread_bps_default for b in mine) == {
            0.0: 3,
            10.0: 3,
            30.0: 3,
            60.0: 3,
        }  # each round-trip level L reaches fdq as 2L (FDQ_EXIT_SPREAD_CORRECTION)
        assert Counter((b.start, b.end) for b in mine) == Counter(
            {**{(f.test_start, f.test_end): 4 for f in folds}, holdout_span: 4}
        )
        for b in mine:
            assert b.friction == replace(
                DOUBLED,
                spread_bps_default=b.friction.spread_bps_default,
                spread_bps_by_symbol={},
            )
        detail = m.gates[GATE_ORDER.index("cost_sweep")]["detail"]
        assert detail["params"] == {
            "folds": [dict(p) for p in wf.fold_params],
            "holdout": m.selected_params,
        }
        assert detail["reselected"] is False


def test_every_base_friction_the_run_handed_fdq_doubles_every_spread_in_the_file(recorded):
    # Selection, the walk-forward, the prior's re-run (inside walk_forward),
    # the holdout and the turnover/capacity re-runs: all at BASE, the file
    # with its default and every per-symbol spread doubled, nothing else moved.
    handed = (
        [c["friction"] for c in recorded.wf_calls]
        + recorded.is_frictions
        + recorded.inner_frictions
        + [b.friction for b in recorded.backtests if b.friction.spread_bps_by_symbol]
    )
    assert len(handed) > 6
    assert all(f == DOUBLED for f in handed)


def test_turnover_and_capacity_come_from_each_folds_own_params(recorded):
    for m in recorded.manifests:
        fold_params = _own_wf(recorded, m.symbol).fold_params
        spans = _fold_spans(m.symbol)
        base_fold_runs = [
            b
            for b in recorded.backtests
            if b.friction.spread_bps_by_symbol
            and b.params["symbol"] == m.symbol
            and (b.start, b.end) in spans
        ]
        assert [b.params for b in base_fold_runs] == [dict(p) for p in fold_params]
        assert sorted((b.start, b.end) for b in base_fold_runs) == sorted(spans)


def test_psr_the_bootstrap_and_the_regimes_read_the_series_the_dsr_reads(recorded):
    n = len(recorded.manifests)
    assert len(recorded.psr_series) == len(recorded.bootstrap_calls) == n
    assert len(recorded.regime_series) == len(recorded.dsr_calls) == n
    for i, (dsr_returns, _) in enumerate(recorded.dsr_calls):
        for series in (
            recorded.psr_series[i],
            recorded.bootstrap_calls[i][0],
            recorded.regime_series[i],
        ):
            assert list(series.index) == list(dsr_returns.index)
            np.testing.assert_array_equal(series.to_numpy(), dsr_returns.to_numpy())


def test_the_bootstrap_receives_the_configs_seed_and_resamples(recorded):
    # The smoke config's seed is 7, not a default anyone would type.
    for _, args, kwargs in recorded.bootstrap_calls:
        assert args == ()
        assert kwargs == {"n_samples": 1000, "seed": 7}
    for m in recorded.manifests:
        detail = m.gates[GATE_ORDER.index("bootstrap_sharpe_lower5")]["detail"]
        assert (detail["n_samples"], detail["seed"]) == (1000, 7)


def test_every_call_into_fdq_reads_the_loaded_macro_series(recorded):
    loaded = recorded.macro_loaded
    assert loaded is not None and "vix" in loaded.columns
    assert len(recorded.macros) > 20
    assert all(m is loaded for m in recorded.macros)
    holdouts = [
        b for b in recorded.backtests if (b.start, b.end) == (date(2020, 1, 2), date(2020, 12, 31))
    ]
    assert len(holdouts) == 2 * (1 + 4)  # per strategy: the verdict's, then one per level
    assert all(b.macro is loaded for b in holdouts)


def test_selected_params_is_the_selection_rule_over_the_recorded_in_sample_sharpes(recorded):
    for m in recorded.manifests:
        (call,) = [c for c in recorded.is_calls if c["symbol"] == m.symbol]
        spec = GridSpec("ma_crossover", m.symbol, call["grid"])
        assert m.selected_params == select_params(spec, call["sharpes"])
    # On this slice the whole-window choice is not the last fold's, so a
    # runner that took fold_params[-1] fails the assertion above.
    spy = recorded.manifests[0]
    assert spy.selected_params != dict(_own_wf(recorded, "SPY").fold_params[-1])


def test_pbo_reads_the_whole_selection_windows_matrix(recorded):
    days = _fixture_days("SPY", "2018-01-02", "2019-12-31")
    assert len(recorded.pbo_matrices) == len(recorded.is_calls) == 2
    for matrix, call in zip(recorded.pbo_matrices, recorded.is_calls, strict=True):
        assert call["span"] == (date(2018, 1, 2), date(2019, 12, 31))
        assert matrix.shape == (len(days), 2)
        np.testing.assert_array_equal(matrix, call["matrix"])


def test_the_verdicts_pbo_is_the_higher_of_the_port_and_fdq(recorded):
    # The runner holds both functions, by identity...
    assert run_mod.probability_backtest_overfitting is pbo_mod.probability_backtest_overfitting
    assert (
        run_mod.fdq_probability_backtest_overfitting is fdq_pbo_mod.probability_backtest_overfitting
    )
    # ...calls each once per strategy on the whole-window matrix, and the
    # manifest's pbo (which sets the fragile label) is the higher.
    assert len(recorded.pbo_port) == len(recorded.pbo_fdq) == len(recorded.manifests)
    for m, (pm, port), (fm, fdq), call in zip(
        recorded.manifests, recorded.pbo_port, recorded.pbo_fdq, recorded.is_calls, strict=True
    ):
        np.testing.assert_array_equal(pm, call["matrix"])
        np.testing.assert_array_equal(fm, call["matrix"])
        assert m.pbo == max(port, fdq)
        detail = m.gates[GATE_ORDER.index("pbo")]["detail"]
        assert (detail["pbo_port"], detail["pbo_fdq"], detail["pbo_fdq_error"]) == (port, fdq, None)
        assert detail["rule"] == "max(port, fdq)"
        assert m.gates[GATE_ORDER.index("pbo")]["value"] == m.pbo
        assert m.verdict == compute_verdict(m.gates, max(port, fdq))


def _rerun_at_base_friction(call) -> tuple[pd.Series, pd.Series]:
    args, kwargs, _ = call
    bound = inspect.signature(evaluate_fixed).bind(*args, **kwargs)
    bound.arguments["friction"] = BASE
    return evaluate_fixed(*bound.args, **bound.kwargs)


def test_the_sweep_at_the_base_friction_is_exactly_the_series_the_gates_read(recorded):
    # Every sweep call, re-run with the very arguments the sweep passed but
    # at the base friction, rebuilds the DSR's series bar for bar: the same
    # fold params over the same fold bars (warm-up included), the same
    # holdout params over the same holdout bars (warm-up included).
    assert len(recorded.sweep_calls) == 2 * 4
    for m, (dsr_returns, _) in zip(recorded.manifests, recorded.dsr_calls, strict=True):
        mine = [c for c in recorded.sweep_calls if c[0][2]["symbol"] == m.symbol]
        assert len(mine) == 4
        for call in mine:
            joined = join_series(*_rerun_at_base_friction(call), date(2020, 1, 2))
            assert list(joined.index) == list(dsr_returns.index)
            np.testing.assert_array_equal(joined.to_numpy(), dsr_returns.to_numpy())


def test_each_sweep_number_covers_the_series_it_names(recorded):
    for m in recorded.manifests:
        detail = m.gates[GATE_ORDER.index("cost_sweep")]["detail"]
        mine = [c for c in recorded.sweep_calls if c[0][2]["symbol"] == m.symbol]
        for args, _, (oos, hold) in mine:
            friction = args[8]
            key = str(friction.spread_bps_default / FDQ_EXIT_SPREAD_CORRECTION)
            joined = join_series(oos, hold, date(2020, 1, 2))
            assert detail["holdout_sharpe_by_bps"][key] == float(fdq_sharpe(hold))
            assert detail["sharpe_by_bps"][key] == float(fdq_sharpe(joined))
            assert detail["holdout_total_return_by_bps"][key] == float((1.0 + hold).prod() - 1.0)
            assert detail["total_return_by_bps"][key] == float((1.0 + joined).prod() - 1.0)


def test_the_out_of_sample_dates_are_the_union_of_the_folds_test_spans(recorded):
    for m in recorded.manifests:
        days = pd.DatetimeIndex(_fixture_days(m.symbol, "2018-01-02", "2019-12-31"))
        spans = _folds(m.symbol)
        covered = [d for d in days if any(f.test_start <= d.date() <= f.test_end for f in spans)]
        assert list(_own_wf(recorded, m.symbol).oos_returns.index) == covered
        # 503 bars in 3 blocks of 167: the first block only trains, and the
        # last 2 bars fall in no fold's test span.
        assert len(days) == 503
        assert covered[0] == days[167] and covered[-1] == days[500]
        assert (
            "walk-forward coverage: 503 selection bars in 3 blocks of 167; the first 167 "
            "(2018-01-02..2018-08-29) only train, and the last 2 (2019-12-30..2019-12-31) fall "
            "in no fold's test span; each fold's out-of-sample backtest restarts in cash and "
            "re-enters"
        ) in m.notes


# --------------------------------------------------------------------------
# The run's refusals.
# --------------------------------------------------------------------------


def test_a_run_writes_nothing_unless_asked(mirror_builder, tmp_path):
    exp = mirror_builder(tmp_path / "repo", TINY_CONFIG)
    manifests = run(exp, repo_root=tmp_path / "repo")
    assert [m.slug for m in manifests] == ["tiny_spy"]
    assert sorted(p.name for p in exp.iterdir()) == ["config.yaml"]


def test_the_runner_refuses_an_uncommitted_battery_before_reading_anything(
    mirror_builder, tmp_path, monkeypatch
):
    exp = mirror_builder(tmp_path / "repo", commit=False)
    loaded: list[Any] = []
    monkeypatch.setattr(run_mod, "load_experiment_config", lambda *a, **k: loaded.append(a))
    with pytest.raises(RuntimeError, match="is not clean"):
        run(exp, write=True, repo_root=tmp_path / "repo")
    assert loaded == []
    assert sorted(p.name for p in exp.iterdir()) == ["config.yaml"]


def test_the_runner_refuses_a_battery_other_than_the_one_running(mirror_builder, tmp_path):
    root = tmp_path / "repo"
    exp = mirror_builder(root)
    gates = root / BATTERY_REL / "gates.py"
    gates.write_text(gates.read_text() + "\n# not the gates that are running\n")
    for args in (["add", "."], ["commit", "-q", "-m", "edit"]):
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )
    with pytest.raises(RunRefused, match="not the battery that is running"):
        run(exp, repo_root=root)


def test_the_runner_refuses_an_experiment_outside_the_repository(mirror_builder, tmp_path):
    root = tmp_path / "repo"
    mirror_builder(root)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "config.yaml").write_text(TINY_CONFIG)
    with pytest.raises(RunRefused, match="must live inside"):
        run(outside, repo_root=root)


def test_a_null_macro_stops_the_run(mirror_builder, tmp_path):
    root = tmp_path / "repo"
    exp = mirror_builder(
        root, TINY_CONFIG.replace("macro: fixtures/macro/macro.parquet", "macro: null")
    )
    with pytest.raises(ConfigError, match="macro"):
        run(exp, repo_root=root)


def test_macro_none_is_refused_by_every_runner_entry():
    cfg = _smoke_cfg()
    s = cfg.strategies[0]
    with pytest.raises(ValueError, match="macro is None"):
        select_strategy(cfg, s, FRICTION, None, BARS)
    with pytest.raises(ValueError, match="macro is None"):
        holdout_returns(
            "ma_crossover",
            {"symbol": "SPY", "fast": 1, "slow": 50},
            SPY,
            cfg.holdout,
            50000.0,
            FRICTION,
            None,
            42,
        )
    with pytest.raises(ValueError, match="macro is None"):
        evaluate_fixed(
            "ma_crossover",
            [],
            {"symbol": "SPY", "fast": 1, "slow": 50},
            SPY,
            [],
            SPY,
            cfg.holdout,
            50000.0,
            FRICTION,
            None,
            42,
        )


def _fake_walk_forward(counts: list[int]):
    """A walk_forward that returns ``counts[i]`` trial Sharpes on its i-th call."""
    it = iter(counts)

    def fake(name, base, grid, bars, tier, friction, macro, n_folds=4, seed=42):
        n = next(it)
        idx = pd.DatetimeIndex(bars.index[-5:])
        oos = pd.Series(0.0, index=idx)
        return WalkForwardResult(
            oos, tier * (1.0 + oos).cumprod(), [dict(base)] * n_folds, np.zeros(n), n
        )

    return fake


def test_a_walk_forward_that_counts_other_fold_trials_stops_the_run(monkeypatch):
    cfg = _smoke_cfg()
    holdouts: list[Any] = []
    monkeypatch.setattr(run_mod, "holdout_returns", lambda *a, **k: holdouts.append(a))
    monkeypatch.setattr(run_mod, "walk_forward", _fake_walk_forward([3]))
    with pytest.raises(RunRefused, match="fold-trials"):
        select_strategy(cfg, cfg.strategies[0], FRICTION, MACRO, BARS)
    # The prior's second re-run returns one trial where its grid gives two.
    monkeypatch.setattr(run_mod, "walk_forward", _fake_walk_forward([4, 2, 1]))
    with pytest.raises(RunRefused, match="fold-trials"):
        select_strategy(cfg, cfg.strategies[0], FRICTION, MACRO, BARS)
    assert holdouts == []


def test_a_mismatch_against_expected_fold_trials_stops_the_run(monkeypatch):
    # The config's own arithmetic refuses this on load; built by hand here to
    # show the run stops on it too, before any holdout.
    cfg = _smoke_cfg()
    prior = cfg.priors["PRIOR-SPY"]
    cfg = replace(cfg, priors={"PRIOR-SPY": replace(prior, expected_fold_trials=5)})
    holdouts: list[Any] = []
    monkeypatch.setattr(run_mod, "holdout_returns", lambda *a, **k: holdouts.append(a))
    monkeypatch.setattr(run_mod, "walk_forward", _fake_walk_forward([4, 2, 2]))
    with pytest.raises(RunRefused, match="expected_fold_trials is 5"):
        select_strategy(cfg, cfg.strategies[0], FRICTION, MACRO, BARS)
    assert holdouts == []


# --------------------------------------------------------------------------
# The holdout, in isolation.
# --------------------------------------------------------------------------

_MA100 = {"symbol": "SPY", "fast": 1, "slow": 100}


def _holdout(bars: pd.DataFrame, start: date, end: date = date(2019, 12, 31)) -> pd.Series:
    return holdout_returns(
        "ma_crossover", _MA100, bars, Window(start, end), 50000.0, FRICTION, MACRO, 42
    )


def test_the_holdout_series_is_exactly_the_windows_bars():
    r = _holdout(SPY, date(2019, 7, 1))
    # 2019-07-01 is a Monday and a trading day: the first return is dated on
    # it. One bar early would be 2019-06-28; one bar late, 2019-07-02.
    assert r.index[0] == pd.Timestamp("2019-07-01")
    assert pd.Timestamp("2019-06-28") in SPY.index
    assert pd.Timestamp("2019-06-28") not in r.index
    assert bool((r.index >= pd.Timestamp("2019-07-01")).all())
    assert list(r.index) == _fixture_days("SPY", "2019-07-01", "2019-12-31")


def test_a_holdout_starting_on_a_weekend_starts_on_the_next_bar():
    r = _holdout(SPY, date(2019, 6, 29))  # a Saturday
    assert r.index[0] == pd.Timestamp("2019-07-01")
    assert pd.Timestamp("2019-06-28") not in r.index


def test_the_holdout_warms_up_on_bars_before_its_start():
    warm = _holdout(SPY, date(2019, 7, 1))
    cold = _holdout(SPY.loc[SPY.index >= pd.Timestamp("2019-07-01")], date(2019, 7, 1))
    # SPY closed above its 100-day average going into July 2019, so with the
    # bars before the start the rule is long within its first days...
    assert bool((warm.iloc[:5] != 0.0).any())
    # ...and without them the 100-day average does not exist for 100 bars.
    assert bool((cold.iloc[:100] == 0.0).all())


@pytest.mark.parametrize("shift", [-1, 1])
def test_the_holdout_refuses_a_series_off_by_one_bar(monkeypatch, shift):
    real = run_mod.run_backtest

    def off_by_one(strategy, bars, config, macro=None, start=None, end=None):
        idx = pd.DatetimeIndex(bars.index)
        i = int(idx.searchsorted(pd.Timestamp(start)))
        return real(strategy, bars, config, macro, idx[i + shift].date(), end)

    monkeypatch.setattr(run_mod, "run_backtest", off_by_one)
    with pytest.raises(RunRefused, match="holdout window's bars"):
        _holdout(SPY, date(2019, 7, 1))


def test_the_joined_series_keeps_the_walk_forward_before_the_holdout():
    days = pd.DatetimeIndex(["2019-12-30", "2019-12-31", "2020-01-02", "2020-01-03"])
    oos = pd.Series([0.01, 0.02], index=days[:2])
    hold = pd.Series([0.03, 0.04], index=days[2:])
    joined = join_series(oos, hold, date(2020, 1, 2))
    assert list(joined.index) == list(days)
    with pytest.raises(RunRefused, match="dated before the holdout"):
        join_series(oos.iloc[:1], pd.Series([0.02, 0.03], index=days[1:3]), date(2020, 1, 2))
    with pytest.raises(RunRefused, match="inside the holdout"):
        join_series(pd.Series([0.02, 0.03], index=days[1:3]), hold.iloc[1:], date(2020, 1, 2))


# --------------------------------------------------------------------------
# Selection.
# --------------------------------------------------------------------------

_SPEC = GridSpec("ma_crossover", "SPY", {"fast": [1], "slow": [150, 200, 250]})


def test_select_params_takes_the_highest_sharpe():
    assert select_params(_SPEC, [0.01, 0.03, 0.02]) == {"symbol": "SPY", "fast": 1, "slow": 200}


def test_select_params_takes_the_first_in_grid_order_on_a_tie():
    # walk_forward keeps a strictly greater Sharpe only, so the first wins.
    assert select_params(_SPEC, [0.02, 0.03, 0.03]) == {"symbol": "SPY", "fast": 1, "slow": 200}


def test_select_params_refuses_a_wrong_count_or_a_non_finite_sharpe():
    with pytest.raises(RunRefused):
        select_params(_SPEC, [0.01, 0.02])
    with pytest.raises(RunRefused):
        select_params(_SPEC, [0.01, float("nan"), 0.02])


def test_fdq_counts_every_fold_trial_including_fast_equal_to_slow():
    """Ruling 1b's arithmetic, against fdq itself: EXP-002's two SPY grids,
    four folds, on the test slice (not EXP-A01's window). walk_forward keeps
    one trial Sharpe per (fold, configuration) and skips none -- not even
    50/50, which can never be long (``fast_ma > slow_ma`` is strict) and
    whose four fold-trials are exactly 0. 36 + 8 = 44 = 11 configurations x
    4 folds."""
    ma = walkforward_mod.walk_forward(
        "ma_crossover",
        {"symbol": "SPY"},
        {"fast": [10, 20, 50], "slow": [50, 100, 200]},
        SPY,
        50000.0,
        FRICTION,
        MACRO,
        n_folds=4,
        seed=42,
    )
    donchian = walkforward_mod.walk_forward(
        "donchian", {"symbol": "SPY"}, {"window": [20, 50]}, SPY, 50000.0, FRICTION, MACRO, 4
    )
    assert (ma.n_trials, ma.trial_sharpes.size) == (36, 36)
    assert (donchian.n_trials, donchian.trial_sharpes.size) == (8, 8)
    combos = grid_combos({"fast": [10, 20, 50], "slow": [50, 100, 200]})
    by_fold = ma.trial_sharpes.reshape(4, len(combos))  # fdq's order is fold-major
    fifty_fifty = combos.index({"fast": 50, "slow": 50})
    assert by_fold[:, fifty_fifty].tolist() == [0.0, 0.0, 0.0, 0.0]


# --------------------------------------------------------------------------
# The cost sweep's conversion.
# --------------------------------------------------------------------------


def test_the_base_friction_handed_to_fdq_doubles_every_spread_in_the_file():
    assert BASE == DOUBLED
    raw = load_friction_config(FRICTION_FILE)
    assert BASE.spread_bps_default == 2.0 * raw.spread_bps_default == 10.0
    assert set(BASE.spread_bps_by_symbol) == set(raw.spread_bps_by_symbol)
    for symbol, bps in raw.spread_bps_by_symbol.items():
        assert BASE.spread_bps_by_symbol[symbol] == 2.0 * bps
    assert (BASE.spread_bps("SPY"), BASE.spread_bps("TLT")) == (4.0, 6.0)
    # Nothing but the spreads moved: fees, VIX, buffer, minimum, settlement.
    assert (
        replace(
            BASE,
            spread_bps_default=raw.spread_bps_default,
            spread_bps_by_symbol=raw.spread_bps_by_symbol,
        )
        == raw
    )
    # And the hashed file itself is untouched.
    assert "  SPY: 2.0\n" in FRICTION_FILE.read_text()


def test_a_sweep_level_sets_every_symbols_spread_and_keeps_everything_else():
    f = sweep_friction(BASE, 15)
    assert (f.spread_bps_default, f.spread_bps_by_symbol) == (30.0, {})  # 15 round trip, x2
    assert f.spread_bps("SPY") == f.spread_bps("TLT") == 30.0
    assert f.spread_bps("SPY", vix=30.0) == pytest.approx(45.0)  # the VIX widening kept
    assert replace(f, spread_bps_default=5.0, spread_bps_by_symbol={}) == replace(
        BASE, spread_bps_default=5.0, spread_bps_by_symbol={}
    )
    assert BASE.spread_bps_by_symbol["SPY"] == 4.0  # the base is untouched


def test_fdq_halves_its_spread_per_fill_and_the_runner_puts_the_round_trip_on_the_entry():
    # fdq itself: a spread of 30 is 15 bps a fill.
    raw = BrokerEmulator(replace(FRICTION, spread_bps_default=30.0, spread_bps_by_symbol={}))
    assert raw.apply_fill(100.0, "buy", 1000.0, "SPY").execution_price == pytest.approx(100.15)
    assert raw.apply_fill(100.0, "sell", 1000.0, "SPY").execution_price == pytest.approx(99.85)
    # The runner at level 30: the entry fill pays the whole 30 bps round trip.
    ours = BrokerEmulator(sweep_friction(BASE, 30))
    assert ours.apply_fill(100.0, "buy", 1000.0, "SPY").execution_price == pytest.approx(100.30)


class _OneRoundTrip(FdqStrategy):
    """Long at the first close, flat at the third: one buy, one full exit."""

    spec = StrategySpec("probe", "probe", "probe", ["SPY"])

    def __init__(self) -> None:
        super().__init__({})
        self.n, self.on = 0, False

    def should_rebalance(self, asof, bars):
        self.n += 1
        if self.n in (1, 3):
            self.on = self.n == 1
            return True
        return False

    def target_weights(self, asof, bars):
        return pd.Series({"SPY": 1.0}) if self.on else pd.Series(dtype=float)


def _no_fees(f: FrictionConfig) -> FrictionConfig:
    return replace(
        f, sec_fee_rate=0.0, finra_taf_per_share=0.0, sec_fee_minimum=0.0, finra_taf_minimum=0.0
    )


def _one_round_trip(friction: FrictionConfig):
    return run_backtest(
        _OneRoundTrip(),
        SPY,
        BacktestConfig(starting_capital=50000.0, friction=friction),
        None,
        date(2019, 1, 2),
        date(2019, 1, 31),
    )


def _lost_bps(free, costly) -> float:
    """Equity lost to the spread over one round trip, in bps of the
    position's value at the exit's mid in the spread-free run."""
    exit_value = float(free.trades.iloc[1]["notional"])
    assert list(free.trades["side"]) == ["buy", "sell"]
    return (free.ending_equity - costly.ending_equity) / exit_value * 1e4


def test_fdq_1_0_0_books_a_full_exits_half_spread_to_the_ledger_but_not_to_equity():
    """fdq itself, uncorrected, pinned: with fees zeroed, one round trip at a
    spread of 100 bps loses about 50 bps of equity (the buy's half) while the
    cost ledger records both halves. The runner's FDQ_EXIT_SPREAD_CORRECTION
    exists because of this. If an fdq upgrade starts charging the exit, this
    fails, and the correction would then double-charge: remove it."""
    no_fees = _no_fees(FRICTION)
    free = _one_round_trip(replace(no_fees, spread_bps_default=0.0, spread_bps_by_symbol={}))
    costly = _one_round_trip(replace(no_fees, spread_bps_default=100.0, spread_bps_by_symbol={}))
    assert 49.0 < _lost_bps(free, costly) < 51.0
    buy_notional = float(costly.trades.iloc[0]["notional"])
    ledger_bps = costly.cost_ledger.spread_cents / 100 / buy_notional * 1e4
    assert 99.0 < ledger_bps < 102.0


@pytest.mark.parametrize("level", [5.0, 30.0, 100.0])
def test_one_round_trip_at_a_sweep_level_costs_that_level_in_equity(level):
    """The corrected runner: one round trip at level L, fees zeroed, loses L
    bps of equity. The entry fill pays the whole L and the exit pays nothing
    in fdq, so against the exit's value the loss is exactly L/(1 + L/1e4):
    within 1% of L at 100 bps, and closer below."""
    no_fees = _no_fees(BASE)
    free = _one_round_trip(sweep_friction(no_fees, 0.0))
    costly = _one_round_trip(sweep_friction(no_fees, level))
    lost = _lost_bps(free, costly)
    assert lost == pytest.approx(level, rel=0.01)
    assert lost == pytest.approx(level / (1.0 + level / 1e4), rel=1e-9)


def test_one_round_trip_at_level_zero_costs_nothing():
    no_fees = _no_fees(BASE)
    zero = _one_round_trip(sweep_friction(no_fees, 0.0))
    free = _one_round_trip(replace(no_fees, spread_bps_default=0.0, spread_bps_by_symbol={}))
    assert abs(zero.ending_equity - free.ending_equity) < 1e-9
    assert zero.cost_ledger.spread_cents == 0.0


# --------------------------------------------------------------------------
# Turnover and capacity.
# --------------------------------------------------------------------------


def test_trade_weights_move_only_on_the_days_a_trade_filled():
    res = run_backtest(
        MACrossover({"symbol": "SPY", "fast": 1, "slow": 50}),
        SPY,
        BacktestConfig(starting_capital=50000.0, friction=FRICTION),
        MACRO,
        date(2019, 1, 2),
        date(2019, 12, 31),
    )
    w = trade_weights(res, "SPY")["SPY"]
    assert w.iloc[0] == 0.0
    changed = set(w.index[w.diff().fillna(0.0) != 0.0])
    traded = set(pd.DatetimeIndex(pd.to_datetime(res.trades["date"])))
    assert traded and changed == traded
    first = res.trades.iloc[0]
    ts = pd.Timestamp(first["date"])
    prev_equity = res.equity_curve.shift(1).loc[ts]
    assert first["side"] == "buy"
    assert w.loc[ts] == pytest.approx(first["notional"] / prev_equity)
    assert w.loc[ts] == pytest.approx(0.9)  # fdq keeps a 10% cash buffer


def _frame(values: list[float], start: str) -> pd.DataFrame:
    return pd.DataFrame({"SPY": values}, index=pd.bdate_range(start, periods=len(values)))


def test_pooled_turnover_counts_no_change_across_a_fold_boundary():
    a = _frame([0.0, 0.9, 0.9], "2019-01-01")
    b = _frame([0.0, 0.0, 0.9], "2019-02-01")
    # Within the folds: 0.9 in, then 0.9 in again -> one-way 0.9 over 4 changes.
    assert pooled_turnover([a, b]) == pytest.approx(252.0 * 0.9 / 4)
    # Counting the fall from a's last 0.9 to b's empty first book would add a
    # sale no backtest made: 252 x 1.35 / 5.
    assert pooled_turnover([a, b]) != pytest.approx(252.0 * 1.35 / 5)


def test_pooled_turnover_is_none_measured_when_no_fold_has_two_rows():
    assert pooled_turnover([_frame([0.0], "2019-01-01")]) is None
    assert describe_measure(pooled_turnover([])) == NONE_MEASURED


def test_pooled_capacity_reads_each_trade_against_its_days_adv():
    a = _frame([0.0, 0.9, 0.9], "2019-01-01")
    adv = pd.Series(1.0e9, index=a.index)
    assert pooled_capacity([a], adv, "SPY") == pytest.approx(0.01 * 1.0e9 / 0.9)


def test_pooled_capacity_refuses_a_traded_day_whose_adv_is_unknown():
    a = _frame([0.0, 0.9, 0.9], "2019-01-01")
    adv = pd.Series([1.0e9, np.nan, 1.0e9], index=a.index)
    # gates.capacity's ValueError, named a refusal so the CLI states it in one line.
    with pytest.raises(RunRefused, match="ADV is unknown"):
        pooled_capacity([a], adv, "SPY")


def test_pooled_capacity_is_none_measured_when_nothing_traded():
    a = _frame([0.0, 0.0, 0.0], "2019-01-01")
    assert pooled_capacity([a], pd.Series(1.0e9, index=a.index), "SPY") is None


# --------------------------------------------------------------------------
# The verdict's words, and "none measured".
# --------------------------------------------------------------------------


def _gates(**passed: bool | None) -> list[dict[str, Any]]:
    return [{"name": k, "passed": v, "value": 0.0, "detail": {}} for k, v in passed.items()]


_ALL = ("holdout_positive", "dsr", "psr", "bootstrap_sharpe_lower5", "regimes_positive")


def test_a_fail_names_every_failed_gate_first():
    gates = _gates(holdout_positive=False, dsr=True, psr=False, pbo=None, cost_sweep=None)
    line = verdict_line("fail", gates, 0.3)
    assert line == "fail: 2 of 3 gates failed (holdout_positive, psr); PBO 0.300"


def test_the_longest_fail_fits_in_200_characters():
    gates = _gates(**dict.fromkeys(_ALL, False), pbo=None, cost_sweep=None)
    line = verdict_line("fail", gates, 0.999)
    assert len(line) <= 200
    assert line.startswith("fail: 5 of 5 gates failed (")
    assert all(name in line for name in _ALL)
    assert "above 0.5" in line


def test_a_pass_with_a_high_pbo_is_named_fragile():
    gates = _gates(**dict.fromkeys(_ALL, True), pbo=None, cost_sweep=None)
    assert verdict_line("pass, fragile", gates, 0.62) == (
        "pass, fragile: all 5 gates passed; PBO 0.620, above 0.5, "
        "so not promoted without a second window"
    )
    assert verdict_line("pass", gates, 0.2) == "pass: all 5 gates passed; PBO 0.200"


def test_a_fail_with_no_decided_gate_says_so():
    assert verdict_line("fail", _gates(pbo=None), 0.1) == "fail: no gate was decided; PBO 0.100"


def test_none_is_none_measured_and_zero_is_zero():
    assert describe_measure(None) == "none measured"
    assert describe_measure(0.0) == "0.00"


# --------------------------------------------------------------------------
# The command line.
# --------------------------------------------------------------------------


def test_the_cli_passes_only_the_experiment_directory(tmp_path, monkeypatch):
    calls: list[Any] = []
    m = SimpleNamespace(slug="x_spy", verdict_line="fail: ...", turnover=None, capacity=None)

    def fake_run(experiment_dir, **kwargs):
        calls.append((experiment_dir, kwargs))
        return [m]

    monkeypatch.setattr(run_mod, "run", fake_run)
    result = CliRunner().invoke(cli, ["battery", "run", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls == [(tmp_path, {"write": True})]
    assert "turnover none measured" in result.output
    assert "capacity none measured" in result.output
    command = cli.commands["battery"].commands["run"]  # type: ignore[attr-defined]
    assert [p.name for p in command.params] == ["experiment_dir"]


@pytest.mark.parametrize(
    ("error", "line"),
    [
        (RunRefused("the run stops"), "Error: refused: the run stops"),
        (ConfigError("macro: is null"), "Error: refused: macro: is null"),
        (ProvenanceError("SPY.parquet: no sidecar"), "Error: refused: SPY.parquet: no sidecar"),
        (
            RunRefused("battery is not clean:\n?? a.py\n M b.py"),
            "Error: refused: battery is not clean:; ?? a.py; M b.py",
        ),
    ],
)
def test_the_cli_states_a_refusal_in_one_line_and_exits_1(tmp_path, monkeypatch, error, line):
    def refuse(experiment_dir, **kwargs):
        raise error

    monkeypatch.setattr(run_mod, "run", refuse)
    result = CliRunner().invoke(cli, ["battery", "run", str(tmp_path)])
    assert result.exit_code == 1
    assert result.output.splitlines() == [line]
    assert "Traceback" not in result.output


@pytest.mark.parametrize("error", [ValueError("a bug"), RuntimeError("a bug"), KeyError("a bug")])
def test_the_cli_lets_anything_but_a_refusal_keep_its_traceback(tmp_path, monkeypatch, error):
    def broken(experiment_dir, **kwargs):
        raise error

    monkeypatch.setattr(run_mod, "run", broken)
    result = CliRunner().invoke(cli, ["battery", "run", str(tmp_path)])
    assert result.exception is error  # not caught, not turned into a refusal
    assert "refused" not in result.output


def test_the_runners_own_errors_are_refusals():
    assert all(issubclass(e, Refused) for e in (RunRefused, ConfigError, ProvenanceError))
