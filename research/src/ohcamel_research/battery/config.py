"""An experiment's ``config.yaml``, validated on load. A config the runner
cannot honour exactly is refused before anything runs, never half-read: an
unknown key, a missing one, a charter level missing from the cost sweep, a
bootstrap that is not the charter's, a prior whose fold-trial count does not
follow from its own grids, or a macro series left out.

The config is the pre-registration's machine half and is hashed into every
manifest (``manifest.compute_hashes``), so every choice the runner makes that
is not a constant of ``battery/`` comes from here.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ohcamel_research.battery.gates import THRESHOLDS

# The bars the runner reads when a config names none: the ten-year history
# Task 8 vendored. EXP-A01's config names none, so this is what it runs on.
# A test config may name another directory (``bars:``); the config is
# hashed, and so is every bar file read, so either way the manifest records it.
DEFAULT_BARS = "fixtures/history"

# The fdq strategies this runner can evaluate: single-symbol, long or flat,
# which is what its turnover and capacity arithmetic assume.
STRATEGY_KEYS = ("ma_crossover", "donchian")

# The signal schema's strategy pattern (interface/signal.schema.json).
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}$")

_REQUIRED = (
    "id",
    "title",
    "friction",
    "macro",
    "mode",
    "n_folds",
    "strategies",
    "dsr",
    "capital_tiers",
    "selection_window",
    "holdout_window",
    "cost_sweep_bps_round_trip",
    "bootstrap",
    "seed",
)
_OPTIONAL = ("prior", "stress_multipliers", "bars")


class ConfigError(ValueError):
    """A config the runner refuses to run."""


@dataclass(frozen=True)
class Window:
    start: date
    end: date


@dataclass(frozen=True)
class GridSpec:
    """One fdq strategy over one symbol and a grid: ``walk_forward``'s
    ``strategy_name``, ``base_params`` ({"symbol": ...}) and ``grid``."""

    key: str
    symbol: str
    grid: dict[str, list[Any]]

    @property
    def base_params(self) -> dict[str, Any]:
        return {"symbol": self.symbol}

    @property
    def n_combos(self) -> int:
        return math.prod(len(v) for v in self.grid.values())


@dataclass(frozen=True)
class Strategy:
    slug: str
    spec: GridSpec
    prior: str | None


@dataclass(frozen=True)
class Prior:
    """Earlier trials on this family, re-run over the selection window so
    their per-fold-trial Sharpes deflate this experiment's DSR (ruling 1b)."""

    name: str
    rerun: tuple[GridSpec, ...]
    window: str
    expected_fold_trials: int


@dataclass(frozen=True)
class ExperimentConfig:
    id: str
    title: str
    friction_version: str
    friction_file: str
    macro: str
    bars: str
    n_folds: int
    strategies: tuple[Strategy, ...]
    priors: dict[str, Prior]
    dsr_unit: str
    tier: float
    selection: Window
    holdout: Window
    cost_levels: tuple[float, ...]
    bootstrap_resamples: int
    bootstrap_method: str
    seed: int
    stress_multipliers: tuple[float, ...] | None

    def prior_of(self, strategy: Strategy) -> Prior | None:
        return None if strategy.prior is None else self.priors[strategy.prior]


def expected_fold_trials(cfg: ExperimentConfig) -> dict[str, int]:
    """Each strategy's DSR trial count in fold-trials (ruling 1b): its prior's
    ``expected_fold_trials``, if it names one, plus its own configurations x
    ``n_folds``. fdq's ``walk_forward`` appends one trial Sharpe per (fold,
    configuration) and skips none, so this is exactly the count ``run``
    must see reach ``deflated_sharpe``; for EXP-A01 it is 44 + 3 x 4 = 56 for
    SPY and 3 x 4 = 12 for TLT."""
    out: dict[str, int] = {}
    for s in cfg.strategies:
        prior = cfg.prior_of(s)
        own = s.spec.n_combos * cfg.n_folds
        out[s.slug] = own + (prior.expected_fold_trials if prior is not None else 0)
    return out


def load_experiment_config(config_path: Path, repo_root: Path) -> ExperimentConfig:
    """Read and validate ``config_path``; every file it names is resolved
    against ``repo_root`` and must exist. Raises ``ConfigError`` naming the
    first problem found."""
    try:
        doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"{config_path}: cannot be read ({e})") from e
    return validate_config(doc, repo_root)


def validate_config(doc: Any, repo_root: Path) -> ExperimentConfig:
    d = _mapping(doc, "config")
    missing = [k for k in _REQUIRED if k not in d]
    if missing:
        raise ConfigError(f"config: missing key {missing[0]!r}")
    unknown = sorted(set(d) - set(_REQUIRED) - set(_OPTIONAL))
    if unknown:
        raise ConfigError(f"config: unknown key {unknown[0]!r}; refusing to ignore it")

    exp_id = _str(d["id"], "id")
    title = _str(d["title"], "title")

    friction = _mapping(d["friction"], "friction")
    _exact_keys(friction, ("version", "file"), "friction")
    friction_version = _str(friction["version"], "friction.version")
    friction_file = _repo_file(friction["file"], "friction.file", repo_root)

    if d["macro"] is None:
        raise ConfigError(
            "macro: is null; the VIX series is how fdq widens the spread, EXP-002 ran with "
            "it, and a run without it would be a cheaper friction than the one registered"
        )
    macro = _repo_file(d["macro"], "macro", repo_root)

    bars = _inside_repo(d.get("bars", DEFAULT_BARS), "bars")
    if not (repo_root / bars).is_dir():
        raise ConfigError(f"bars: {bars!r} is not a directory under the repository")

    if d["mode"] != "walkforward":
        raise ConfigError(f"mode: {d['mode']!r}; this runner runs walkforward only")
    n_folds = _int(d["n_folds"], "n_folds")
    if n_folds < 1:
        raise ConfigError("n_folds: must be at least 1")

    priors: dict[str, Prior] = {}
    if "prior" in d:
        for name, raw in _mapping(d["prior"], "prior").items():
            priors[_str(name, "prior key")] = _prior(name, raw, n_folds)

    strategies = _strategies(d["strategies"], priors)

    dsr = _mapping(d["dsr"], "dsr")
    _exact_keys(dsr, ("unit",), "dsr")
    if dsr["unit"] != "fold_trials":
        raise ConfigError(f"dsr.unit: {dsr['unit']!r}; ruling 1b counts fold_trials")

    tiers = d["capital_tiers"]
    if not isinstance(tiers, list) or len(tiers) != 1:
        raise ConfigError("capital_tiers: exactly one tier; the runner evaluates one")
    tier = _number(tiers[0], "capital_tiers[0]")
    if tier <= 0:
        raise ConfigError("capital_tiers[0]: must be positive")

    selection = _window(d["selection_window"], "selection_window")
    holdout = _window(d["holdout_window"], "holdout_window")
    if not selection.end < holdout.start:
        raise ConfigError("holdout_window must start after selection_window ends")

    levels_raw = d["cost_sweep_bps_round_trip"]
    if not isinstance(levels_raw, list) or not levels_raw:
        raise ConfigError("cost_sweep_bps_round_trip: a non-empty list of bps levels")
    levels = tuple(_number(x, f"cost_sweep_bps_round_trip[{i}]") for i, x in enumerate(levels_raw))
    if any(x < 0 for x in levels) or len(set(levels)) != len(levels):
        raise ConfigError("cost_sweep_bps_round_trip: levels must be distinct and non-negative")
    absent = [x for x in THRESHOLDS.cost_sweep_bps_round_trip if x not in levels]
    if absent:
        raise ConfigError(
            f"cost_sweep_bps_round_trip: the charter's level {absent[0]:g} bps is missing"
        )

    boot = _mapping(d["bootstrap"], "bootstrap")
    _exact_keys(boot, ("resamples", "method"), "bootstrap")
    resamples = _int(boot["resamples"], "bootstrap.resamples")
    if resamples != THRESHOLDS.bootstrap_resamples:
        raise ConfigError(
            f"bootstrap.resamples: {resamples}; the charter's is {THRESHOLDS.bootstrap_resamples}"
        )
    if boot["method"] != "stationary_block":
        raise ConfigError(
            f"bootstrap.method: {boot['method']!r}; the charter's is stationary_block"
        )

    seed = _int(d["seed"], "seed")

    stress: tuple[float, ...] | None = None
    if "stress_multipliers" in d:
        raw_stress = d["stress_multipliers"]
        if not isinstance(raw_stress, list):
            raise ConfigError("stress_multipliers: expected a list")
        stress = tuple(_number(x, "stress_multipliers[]") for x in raw_stress)

    return ExperimentConfig(
        id=exp_id,
        title=title,
        friction_version=friction_version,
        friction_file=friction_file,
        macro=macro,
        bars=bars,
        n_folds=n_folds,
        strategies=strategies,
        priors=priors,
        dsr_unit="fold_trials",
        tier=tier,
        selection=selection,
        holdout=holdout,
        cost_levels=levels,
        bootstrap_resamples=resamples,
        bootstrap_method="stationary_block",
        seed=seed,
        stress_multipliers=stress,
    )


def _strategies(raw: Any, priors: Mapping[str, Prior]) -> tuple[Strategy, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("strategies: a non-empty list")
    out: list[Strategy] = []
    for i, entry in enumerate(raw):
        where = f"strategies[{i}]"
        e = _mapping(entry, where)
        if "slug" not in e:
            raise ConfigError(f"{where}: missing key 'slug'")
        slug = _str(e["slug"], f"{where}.slug")
        if not _SLUG_RE.match(slug):
            raise ConfigError(f"{where}.slug: {slug!r} does not match {_SLUG_RE.pattern}")
        prior = e.get("prior")
        if prior is not None and prior not in priors:
            raise ConfigError(f"{where}.prior: {prior!r} is not a key of prior:")
        rest = {k: v for k, v in e.items() if k not in ("slug", "prior")}
        spec = _grid_spec(rest, where)
        if spec.n_combos < 2:
            raise ConfigError(
                f"{where}: {spec.n_combos} configuration; PBO needs at least two to compare"
            )
        out.append(Strategy(slug=slug, spec=spec, prior=prior))
    slugs = [s.slug for s in out]
    if len(set(slugs)) != len(slugs):
        raise ConfigError("strategies: slugs must be unique")
    return tuple(out)


def _prior(name: str, raw: Any, n_folds: int) -> Prior:
    where = f"prior.{name}"
    p = _mapping(raw, where)
    _exact_keys(p, ("rerun", "window", "expected_fold_trials"), where)
    if p["window"] != "selection":
        raise ConfigError(f"{where}.window: {p['window']!r}; only 'selection' is supported")
    reruns = p["rerun"]
    if not isinstance(reruns, list) or not reruns:
        raise ConfigError(f"{where}.rerun: a non-empty list")
    specs = tuple(
        _grid_spec(_mapping(r, f"{where}.rerun[{i}]"), f"{where}.rerun[{i}]")
        for i, r in enumerate(reruns)
    )
    expected = _int(p["expected_fold_trials"], f"{where}.expected_fold_trials")
    arithmetic = sum(s.n_combos for s in specs) * n_folds
    if expected != arithmetic:
        raise ConfigError(
            f"{where}.expected_fold_trials: {expected}, but its grids give "
            f"{sum(s.n_combos for s in specs)} configurations x {n_folds} folds = {arithmetic}"
        )
    return Prior(name=name, rerun=specs, window="selection", expected_fold_trials=expected)


def _grid_spec(entry: Mapping[str, Any], where: str) -> GridSpec:
    if len(entry) != 1:
        raise ConfigError(f"{where}: exactly one fdq strategy key, got {sorted(entry)}")
    ((key, body),) = entry.items()
    if key not in STRATEGY_KEYS:
        raise ConfigError(f"{where}: {key!r} is not one of {STRATEGY_KEYS}")
    b = _mapping(body, f"{where}.{key}")
    _exact_keys(b, ("symbol", "grid"), f"{where}.{key}")
    symbol = _str(b["symbol"], f"{where}.{key}.symbol")
    if not _SYMBOL_RE.match(symbol):
        raise ConfigError(f"{where}.{key}.symbol: {symbol!r} is not a ticker")
    grid_raw = _mapping(b["grid"], f"{where}.{key}.grid")
    if not grid_raw:
        raise ConfigError(f"{where}.{key}.grid: empty")
    grid: dict[str, list[Any]] = {}
    for g, values in grid_raw.items():
        if g == "symbol":
            raise ConfigError(f"{where}.{key}.grid: symbol is not a grid parameter")
        if not isinstance(values, list) or not values:
            raise ConfigError(f"{where}.{key}.grid.{g}: a non-empty list")
        for v in values:
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ConfigError(f"{where}.{key}.grid.{g}: {v!r} is not a positive integer")
        if len(set(values)) != len(values):
            raise ConfigError(f"{where}.{key}.grid.{g}: repeated value")
        grid[str(g)] = list(values)
    return GridSpec(key=key, symbol=symbol, grid=grid)


def _window(raw: Any, where: str) -> Window:
    w = _mapping(raw, where)
    _exact_keys(w, ("start", "end"), where)
    try:
        start = date.fromisoformat(_str(w["start"], f"{where}.start"))
        end = date.fromisoformat(_str(w["end"], f"{where}.end"))
    except ValueError as e:
        raise ConfigError(f"{where}: {e}") from e
    if start > end:
        raise ConfigError(f"{where}: start is after end")
    return Window(start, end)


def _inside_repo(raw: Any, where: str) -> str:
    rel = _str(raw, where)
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise ConfigError(f"{where}: {rel!r} must be a path inside the repository")
    return rel


def _repo_file(raw: Any, where: str, repo_root: Path) -> str:
    rel = _inside_repo(raw, where)
    if not (repo_root / rel).is_file():
        raise ConfigError(f"{where}: {rel!r} does not exist under the repository")
    return rel


def _mapping(v: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(v, dict):
        raise ConfigError(f"{where}: expected a mapping")
    return v


def _exact_keys(m: Mapping[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [k for k in keys if k not in m]
    if missing:
        raise ConfigError(f"{where}: missing key {missing[0]!r}")
    unknown = sorted(set(m) - set(keys))
    if unknown:
        raise ConfigError(f"{where}: unknown key {unknown[0]!r}")


def _str(v: Any, where: str) -> str:
    if not isinstance(v, str) or not v:
        raise ConfigError(f"{where}: expected a non-empty string")
    return v


def _int(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"{where}: expected an integer")
    return v


def _number(v: Any, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ConfigError(f"{where}: expected a finite number")
    return float(v)
