"""Shared by the battery runner's tests: a mirror of the repository in a tmp
directory, holding a byte-for-byte copy of ``battery/``, the vendored
friction file, the macro series, ``uv.lock`` and some of the 2018-2020
``fixtures/bars/`` slice, plus a small experiment config. The runner runs
there, never in ``research/experiments/``, and never on EXP-A01's own
configuration (Task 13's job)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from ohcamel_research import REPO_ROOT
from ohcamel_research.manifest import BATTERY_REL

# The 2018-2020 slice, split into a two-year selection window and a
# one-year holdout, with a two-configuration grid per symbol and a tiny
# prior on SPY: the shape of EXP-A01's config at a size a test can afford.
# The numbers this produces are not a result and no test reads them as one.
SMOKE_CONFIG = """\
id: EXP-SMOKE
title: smoke
friction: {version: "1.0.0", file: research/config/friction_v1.yaml}
macro: fixtures/macro/macro.parquet
bars: fixtures/bars
mode: walkforward
n_folds: 2
strategies:
  - slug: smoke_spy
    ma_crossover: {symbol: SPY, grid: {fast: [1], slow: [50, 100]}}
    prior: PRIOR-SPY
  - {slug: smoke_tlt, ma_crossover: {symbol: TLT, grid: {fast: [1], slow: [50, 100]}}}
prior:
  PRIOR-SPY:
    rerun:
      - ma_crossover: {symbol: SPY, grid: {fast: [10], slow: [50]}}
      - donchian: {symbol: SPY, grid: {window: [20]}}
    window: selection
    expected_fold_trials: 4
dsr: {unit: fold_trials}
capital_tiers: [50000]
selection_window: {start: "2018-01-02", end: "2019-12-31"}
holdout_window: {start: "2020-01-02", end: "2020-12-31"}
stress_multipliers: [1, 2, 5]
cost_sweep_bps_round_trip: [0, 5, 15, 30]
bootstrap: {resamples: 1000, method: stationary_block}
seed: 42
"""

# One strategy, no prior: the cheapest config the runner accepts.
TINY_CONFIG = """\
id: EXP-TINY
title: tiny
friction: {version: "1.0.0", file: research/config/friction_v1.yaml}
macro: fixtures/macro/macro.parquet
bars: fixtures/bars
mode: walkforward
n_folds: 2
strategies:
  - {slug: tiny_spy, ma_crossover: {symbol: SPY, grid: {fast: [1], slow: [50, 100]}}}
dsr: {unit: fold_trials}
capital_tiers: [50000]
selection_window: {start: "2018-01-02", end: "2019-12-31"}
holdout_window: {start: "2020-01-02", end: "2020-12-31"}
cost_sweep_bps_round_trip: [0, 5, 15, 30]
bootstrap: {resamples: 1000, method: stationary_block}
seed: 42
"""

_COPIED = (
    "research/config/friction_v1.yaml",
    "research/uv.lock",
    "fixtures/macro/macro.parquet",
    "fixtures/macro/macro.parquet.meta.json",
    "fixtures/bars/SPY.parquet",
    "fixtures/bars/SPY.parquet.meta.json",
    "fixtures/bars/TLT.parquet",
    "fixtures/bars/TLT.parquet.meta.json",
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def build_mirror(root: Path, config_text: str = SMOKE_CONFIG, *, commit: bool = True) -> Path:
    """Build the mirror under ``root`` and return its experiment directory.
    With ``commit`` it is a git repository with everything committed, so the
    runner's committed-battery check passes; without it, ``battery/`` is
    left untracked, so that check must refuse."""
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / BATTERY_REL,
        root / BATTERY_REL,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for rel in _COPIED:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, root / rel)
    exp = root / "research" / "experiments" / "EXP-SMOKE"
    exp.mkdir(parents=True)
    (exp / "config.yaml").write_text(config_text)
    _git(root, "init", "-q")
    if commit:
        _git(root, "add", ".")
    else:
        _git(root, "add", "research/config", "research/uv.lock", "fixtures", "research/experiments")
    _git(root, "commit", "-q", "-m", "mirror")
    return exp


@pytest.fixture(scope="session")
def mirror_builder() -> Callable[..., Path]:
    return build_mirror
