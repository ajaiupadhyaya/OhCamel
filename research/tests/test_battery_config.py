"""The experiment config is validated on load (``battery/config.py``): a
config the runner cannot honour exactly is refused before anything runs.

EXP-A01's own config is loaded here and never run: its validation, and the
fold-trial counts its arithmetic gives, are all this file asks of it.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
import yaml
from conftest import SMOKE_CONFIG

from ohcamel_research import REPO_ROOT
from ohcamel_research.battery.config import (
    DEFAULT_BARS,
    ConfigError,
    expected_fold_trials,
    load_experiment_config,
    validate_config,
)
from ohcamel_research.battery.gates import THRESHOLDS

EXP_A01 = REPO_ROOT / "research" / "experiments" / "EXP-A01" / "config.yaml"


def _doc() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(SMOKE_CONFIG))


def _refused(doc: dict[str, Any], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        validate_config(doc, REPO_ROOT)


def test_the_smoke_config_is_accepted():
    cfg = validate_config(_doc(), REPO_ROOT)
    assert [s.slug for s in cfg.strategies] == ["smoke_spy", "smoke_tlt"]
    assert cfg.cost_levels == (0.0, 5.0, 15.0, 30.0)
    assert expected_fold_trials(cfg) == {"smoke_spy": 4 + 2 * 2, "smoke_tlt": 2 * 2}


def test_exp_a01_validates_and_counts_56_fold_trials_for_spy_and_12_for_tlt():
    """Ruling 1b, from EXP-A01's own config (loaded, never run): SPY's prior
    is EXP-002's two grids, (9 + 2) configurations x 4 folds = 44, plus its
    own 3 x 4 = 12; TLT has no prior, 3 x 4 = 12."""
    cfg = load_experiment_config(EXP_A01, REPO_ROOT)
    assert expected_fold_trials(cfg) == {"exp_a01_spy": 56, "exp_a01_tlt": 12}
    assert cfg.priors["EXP-002-SPY"].expected_fold_trials == 44
    assert cfg.selection.start == date(2016, 6, 1) and cfg.selection.end == date(2022, 5, 31)
    assert cfg.holdout.start == date(2022, 6, 1) and cfg.holdout.end == date(2026, 6, 1)
    assert cfg.bars == DEFAULT_BARS == "fixtures/history"
    assert cfg.macro == "fixtures/macro/macro.parquet"
    assert cfg.friction_file == "research/config/friction_v1.yaml"
    assert (cfg.n_folds, cfg.tier, cfg.seed) == (4, 50000.0, 42)


def test_a_sweep_missing_a_charter_level_is_refused():
    for level in THRESHOLDS.cost_sweep_bps_round_trip:
        doc = _doc()
        doc["cost_sweep_bps_round_trip"] = [
            x for x in doc["cost_sweep_bps_round_trip"] if float(x) != level
        ]
        _refused(doc, f"the charter's level {level:g} bps is missing")


def test_a_sweep_may_add_levels_beyond_the_charters():
    doc = _doc()
    doc["cost_sweep_bps_round_trip"] = [0, 5, 10, 15, 30, 50]
    assert validate_config(doc, REPO_ROOT).cost_levels == (0.0, 5.0, 10.0, 15.0, 30.0, 50.0)


def test_other_bootstrap_resamples_are_refused():
    for n in (THRESHOLDS.bootstrap_resamples - 1, THRESHOLDS.bootstrap_resamples + 1, 100):
        doc = _doc()
        doc["bootstrap"]["resamples"] = n
        _refused(doc, "bootstrap.resamples")


def test_a_bootstrap_other_than_stationary_block_is_refused():
    for method in ("iid", "circular_block", "moving_block", None):
        doc = _doc()
        doc["bootstrap"]["method"] = method
        _refused(doc, "bootstrap.method")


def test_a_null_or_missing_macro_is_refused():
    doc = _doc()
    doc["macro"] = None
    _refused(doc, "macro: is null")
    doc = _doc()
    del doc["macro"]
    _refused(doc, "missing key 'macro'")
    doc = _doc()
    doc["macro"] = "fixtures/macro/nowhere.parquet"
    _refused(doc, "macro: .* does not exist")


def test_an_expected_fold_trial_count_its_grids_do_not_give_is_refused():
    doc = _doc()
    doc["prior"]["PRIOR-SPY"]["expected_fold_trials"] = 5
    _refused(doc, "expected_fold_trials: 5, but its grids give 2 configurations x 2 folds = 4")


def test_a_dsr_unit_other_than_fold_trials_is_refused():
    doc = _doc()
    doc["dsr"]["unit"] = "configurations"
    _refused(doc, "dsr.unit")


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d.update(surprise=1), "unknown key 'surprise'"),
        (lambda d: d.pop("seed"), "missing key 'seed'"),
        (lambda d: d.update(mode="single"), "walkforward only"),
        (lambda d: d.update(capital_tiers=[5, 50000]), "exactly one tier"),
        (lambda d: d["holdout_window"].update(start="2019-12-31"), "must start after"),
        (lambda d: d["selection_window"].update(end="2017-01-01"), "start is after end"),
        (lambda d: d["friction"].update(file="/etc/friction.yaml"), "inside the repository"),
        (lambda d: d.update(bars="../fixtures/bars"), "inside the repository"),
        (lambda d: d["strategies"][0].update(slug="Smoke-SPY"), "does not match"),
        (lambda d: d["strategies"][1].update(slug="smoke_spy"), "slugs must be unique"),
        (lambda d: d["strategies"][0].update(prior="NOPE"), "not a key of prior"),
        (
            lambda d: d["strategies"][1]["ma_crossover"]["grid"].update(slow=[50]),
            "PBO needs at least two",
        ),
        (lambda d: d["strategies"][1].update(bollinger={}), "exactly one fdq strategy key"),
        (lambda d: d["prior"]["PRIOR-SPY"].update(window="holdout"), "only 'selection'"),
        (lambda d: d.update(cost_sweep_bps_round_trip=[0, 5, 5, 15, 30]), "distinct"),
        (lambda d: d.update(seed=True), "seed: expected an integer"),
    ],
)
def test_a_config_the_runner_cannot_honour_is_refused(mutate, match):
    doc = _doc()
    mutate(doc)
    _refused(doc, match)
