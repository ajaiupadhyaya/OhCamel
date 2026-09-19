"""The runner: one pre-registered experiment through the charter's battery,
to one manifest per strategy, with both of the owner's decisions (rulings 1
and 1b) built in rather than left to whoever runs it.

In order, and in two phases so that no holdout is evaluated until every
strategy's selection has passed every check:

**Selection, on the selection window alone.** The bars are sliced to
``selection_window`` and fdq's ``walk_forward`` runs with ``n_folds`` on that
slice and nothing else. ``selected_params`` is walk_forward's own rule --
the highest per-bar Sharpe on the train span, the first in grid order on a
tie -- applied to the whole selection window, i.e. the choice walk_forward's
next fold would make, trained on every bar the selection window holds. It is
read from fdq's ``in_sample_return_matrix`` over that window, the same runs
PBO's CSCV reads. Each fold's own choice (``fold_params``) is recorded beside
it. The DSR's trial Sharpes are counted here, in fold-trials: the prior's
re-run (same code, bars, friction, macro, seed and tier, selection window
only) plus this experiment's own ``wf.trial_sharpes``. A count that differs
from the config's arithmetic stops the run before any holdout is touched.

**The holdout, run once, with ``selected_params``.** Its moving average
warms up on bars before ``holdout_window.start`` (the strategy reads every
bar up to each day), but its returns are exactly the holdout window's bars
and nothing earlier: ``holdout_returns`` refuses any other set of dates.
"Holdout positive" reads that series alone.

**The gates.** DSR (fdq's ``deflated_sharpe``, unchanged), PSR, the
bootstrap and the regimes read the walk-forward's out-of-sample series
concatenated with the holdout series. PBO is fdq's CSCV over the selection
window's configurations, ported into ``pbo.py`` so that a flat column's
Sharpe is 0 rather than a NaN ranked best (ruling 11c). The cost sweep
re-evaluates, at each round-trip level, the series the gates read -- each
fold's own ``fold_params`` over its test span, then ``selected_params`` over
the holdout -- and never re-selects (ruling 11d). Turnover and capacity come
from each fold's own ``fold_params`` backtest. The verdict is
``manifest.compute_verdict``'s, never a second copy.

**Every spread handed to fdq is doubled** (``FDQ_EXIT_SPREAD_CORRECTION``):
fdq 1.0.0 books a full exit's half-spread to the ledger but not to equity,
and the pre-registration states the spread as a round trip paid in full.

The run refuses to start unless ``battery/`` is exactly what git committed
(``manifest.assert_battery_committed``, the first thing ``run`` does).
"""

from __future__ import annotations

import math
import platform
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fdq.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from fdq.frictions.config import FrictionConfig, load_friction_config
from fdq.strategies.benchmarks import build_strategy
from fdq.validation.metrics import max_drawdown, sharpe
from fdq.validation.walkforward import (
    Fold,
    WalkForwardResult,
    grid_combos,
    in_sample_return_matrix,
    make_folds,
    walk_forward,
)

from ohcamel_research.battery import GATES_VERSION
from ohcamel_research.battery.config import (
    ExperimentConfig,
    GridSpec,
    Strategy,
    Window,
    expected_fold_trials,
    load_experiment_config,
)
from ohcamel_research.battery.data import (
    bar_files,
    dollar_adv,
    load_bars,
    load_macro,
    slice_bars,
    wide,
)
from ohcamel_research.battery.gates import (
    THRESHOLDS,
    GateResult,
    bootstrap_gate,
    capacity,
    cost_sweep,
    dsr_gate,
    holdout_gate,
    psr_gate,
    regime_gate,
    turnover,
)
from ohcamel_research.battery.pbo import probability_backtest_overfitting
from ohcamel_research.manifest import (
    BATTERY_REL,
    DsrRecord,
    Manifest,
    assert_battery_committed,
    compute_hashes,
    compute_verdict,
    manifest_path,
)
from ohcamel_research.manifest import Window as ManifestWindow

# What the DSR, PSR, bootstrap and regime gates read, named in the manifest.
RETURNS_SERIES = "walk_forward_oos+holdout"
DSR_UNIT = "fold_trials"

NONE_MEASURED = "none measured"

FDQ_EXIT_SPREAD_CORRECTION = 2.0
"""The factor every spread the runner hands fdq is multiplied by: the
friction file's ``spread_bps_default`` and each of its per-symbol spreads
(the base friction, used for selection, the walk-forward, the prior's
re-run, the holdout, and turnover and capacity), and each cost-sweep level
(``spread_bps_for_round_trip``). The file itself is never edited; it is
hashed.

Why: fdq 1.0.0's ``fdq/backtest/engine.py::_execute_rebalance`` credits a
sale with ``proceeds = sell_notional - fill.regulatory_fees``, where
``sell_notional`` is the position valued at the open's mid, and on a full
exit deletes the position, so the sell's half-spread
(``BrokerEmulator.apply_fill``: ``spread_bps / 1e4 / 2`` per fill) is booked
to the cost ledger but never to equity. A round trip therefore paid half its
spread. The pre-registration states the spread as that many basis points
round trip, halved per fill; the owner ruled that its economics govern.
Doubling the spread makes the entry fill carry the whole round trip.

Exact, to first order, for the trades the runner makes. Every fdq strategy
it runs (``ma_crossover``, ``donchian``: both ``_LongFlat``) enters from
flat to 90% of equity or exits in full, never partially and never short
(fdq's engine refuses a short). The exit's half is charged on the entry's
notional at the entry day's VIX rather than on the exit's, and a position
still open when a backtest ends has paid an exit it never made: both
conservative or second order. If fdq ever charges the exit itself, this
factor double-charges, and this test in ``tests/test_battery_run.py`` fails
to say so:
``test_fdq_1_0_0_books_a_full_exits_half_spread_to_the_ledger_but_not_to_equity``."""

# Disclosed in every manifest, beside the evidence the correction shaped.
SPREAD_NOTES = (
    "spread correction: fdq 1.0.0 books a full exit's half-spread to the ledger but not "
    "to equity (engine.py::_execute_rebalance credits the mid less fees), so every spread "
    "the runner hands fdq -- the friction file's, per symbol and default, and each cost "
    f"sweep level -- is multiplied by FDQ_EXIT_SPREAD_CORRECTION = {FDQ_EXIT_SPREAD_CORRECTION}"
    " and the entry fill carries the whole round trip",
    "friction_v1.yaml calls its spreads 'Half-spread in basis points'; fdq's code treats the "
    "value as the whole spread and halves it per fill, and the pre-registration states it as "
    "a round trip, halved per fill; the pre-registration governs",
    "the VIX widening (x1.5 above 25) on the whole round trip's spread is read on the entry "
    "day, not the exit day",
)


PBO_NOTE = (
    "PBO: fdq's CSCV ported into battery/pbo.py with one change -- a zero-variance "
    "column's Sharpe is 0.0, never NaN, in and out of sample (fdq's NaN sorted above every "
    "number, so a configuration flat out of sample ranked best and read as not overfit)"
)

SWEEP_NOTE = (
    "cost sweep: at each level, each fold's own fold_params over its test span and "
    "selected_params over the holdout -- the series the gates read, re-evaluated with "
    "parameters chosen at the base friction, never re-selected; sharpe_by_bps and "
    "total_return_by_bps cover the joined series, holdout_sharpe_by_bps and "
    "holdout_total_return_by_bps the holdout alone"
)


class RunRefused(RuntimeError):
    """The run cannot produce honest evidence and stops, writing nothing."""


def default_repo_root() -> Path:
    """The checkout this battery lives in: battery/ -> ohcamel_research ->
    src -> research -> the repository."""
    return Path(__file__).resolve().parents[4]


# --------------------------------------------------------------------------
# The two phases' records.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """Everything decided on the selection window, before any holdout is run."""

    strategy: Strategy
    read: tuple[Path, ...]
    sel_long: pd.DataFrame
    sel_bars: pd.DataFrame
    hold_bars: pd.DataFrame
    wf: WalkForwardResult
    trial_sharpes: np.ndarray
    sources: dict[str, int]
    prior_fold_params: dict[str, list[dict[str, Any]]]
    selected_params: dict[str, Any]
    pbo: float
    configurations: int


@dataclass(frozen=True)
class Evaluation:
    selection: Selection
    gates: tuple[GateResult, ...]
    dsr: DsrRecord
    psr: float
    pbo: float
    turnover: float | None
    capacity: float | None


# --------------------------------------------------------------------------
# The run.
# --------------------------------------------------------------------------


def run(
    experiment_dir: Path, *, write: bool = False, repo_root: Path | None = None
) -> list[Manifest]:
    """Run ``experiment_dir/config.yaml`` through the battery and return one
    manifest per strategy, in the config's order. The manifests are written
    to ``experiment_dir`` only when ``write`` is true, and only after every
    strategy has been judged, so a run that stops part-way writes nothing.

    ``repo_root`` exists for tests, which run on a copy of the repository;
    the CLI never passes it. Whatever it is, the battery it holds must be
    byte-identical to the battery running, or the manifest would hash other
    code than the code that judged."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    assert_battery_committed(root)
    _require_running_battery_is_hashed(root)

    config_path = (experiment_dir / "config.yaml").resolve()
    try:
        config_path.relative_to(root)
    except ValueError as e:
        raise RunRefused(f"{config_path}: the experiment must live inside {root}") from e
    cfg = load_experiment_config(config_path, root)
    friction = load_friction(root / cfg.friction_file, cfg.friction_version)
    macro_path = root / cfg.macro
    macro = load_macro(macro_path)
    bars_dir = root / cfg.bars

    selections = [select_strategy(cfg, s, friction, macro, bars_dir) for s in cfg.strategies]
    ran_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifests = []
    for sel in selections:
        ev = judge_strategy(cfg, sel, friction, macro)
        hashes = compute_hashes(root, config_path, read_fixtures=[*sel.read, macro_path])
        manifests.append(_manifest(cfg, ev, hashes, ran_at))

    if write:
        for m in manifests:
            m.dump(manifest_path(experiment_dir, m.slug))
    return manifests


def select_strategy(
    cfg: ExperimentConfig,
    strategy: Strategy,
    friction: FrictionConfig,
    macro: pd.DataFrame | None,
    bars_dir: Path,
) -> Selection:
    """Phase one: everything the selection window decides, and every count
    checked, before the holdout is evaluated."""
    macro = _require_macro(macro)
    spec = strategy.spec
    prior = cfg.prior_of(strategy)
    symbols = sorted({spec.symbol, *(r.symbol for r in prior.rerun)} if prior else {spec.symbol})
    read = tuple(bar_files(bars_dir, symbols))
    long = load_bars(bars_dir, symbols)
    sel_long = slice_bars(long, cfg.selection.start, cfg.selection.end)
    sel_bars = {s: _wide_for(sel_long, s) for s in symbols}
    hold_bars = _wide_for(slice_bars(long, cfg.selection.start, cfg.holdout.end), spec.symbol)
    for frame in (*sel_bars.values(), hold_bars):
        _require_macro_covers(macro, pd.DatetimeIndex(frame.index))

    own_bars = sel_bars[spec.symbol]
    wf = walk_forward(
        spec.key,
        spec.base_params,
        spec.grid,
        own_bars,
        cfg.tier,
        friction,
        macro,
        n_folds=cfg.n_folds,
        seed=cfg.seed,
    )
    own_name = f"{cfg.id} {strategy.slug} walk_forward"
    _require_count(own_name, wf, spec.n_combos * cfg.n_folds)

    sources: dict[str, int] = {}
    parts: list[np.ndarray] = []
    prior_fold_params: dict[str, list[dict[str, Any]]] = {}
    if prior is not None:
        for i, r in enumerate(prior.rerun):
            pwf = walk_forward(
                r.key,
                r.base_params,
                r.grid,
                sel_bars[r.symbol],
                cfg.tier,
                friction,
                macro,
                n_folds=cfg.n_folds,
                seed=cfg.seed,
            )
            name = f"{prior.name} rerun[{i}] {r.key} {r.symbol}"
            _require_count(name, pwf, r.n_combos * cfg.n_folds)
            sources[name] = int(pwf.trial_sharpes.size)
            parts.append(pwf.trial_sharpes)
            prior_fold_params[name] = [dict(p) for p in pwf.fold_params]
        got = sum(sources.values())
        if got != prior.expected_fold_trials:
            raise RunRefused(
                f"{prior.name}: the re-run gave {got} fold-trials, expected_fold_trials is "
                f"{prior.expected_fold_trials}; the run stops"
            )
    sources[own_name] = int(wf.trial_sharpes.size)
    parts.append(wf.trial_sharpes)
    trial_sharpes = np.concatenate(parts).astype(float)
    expected = expected_fold_trials(cfg)[strategy.slug]
    if trial_sharpes.size != expected:
        raise RunRefused(
            f"{strategy.slug}: {trial_sharpes.size} fold-trials would reach deflated_sharpe, "
            f"the config's arithmetic says {expected}; the run stops"
        )

    matrix, is_sharpes = in_sample_return_matrix(
        spec.key,
        spec.base_params,
        spec.grid,
        own_bars,
        cfg.tier,
        friction,
        macro,
        cfg.selection.start,
        cfg.selection.end,
        cfg.seed,
    )
    selected = select_params(spec, is_sharpes)
    pbo = float(probability_backtest_overfitting(matrix))

    return Selection(
        strategy=strategy,
        read=read,
        sel_long=sel_long,
        sel_bars=own_bars,
        hold_bars=hold_bars,
        wf=wf,
        trial_sharpes=trial_sharpes,
        sources=sources,
        prior_fold_params=prior_fold_params,
        selected_params=selected,
        pbo=pbo,
        configurations=int(matrix.shape[1]),
    )


def judge_strategy(
    cfg: ExperimentConfig,
    sel: Selection,
    friction: FrictionConfig,
    macro: pd.DataFrame | None,
) -> Evaluation:
    """Phase two: the holdout, once, then every gate, the cost sweep, and
    turnover and capacity."""
    macro = _require_macro(macro)
    spec = sel.strategy.spec
    holdout = holdout_returns(
        spec.key,
        sel.selected_params,
        sel.hold_bars,
        cfg.holdout,
        cfg.tier,
        friction,
        macro,
        cfg.seed,
    )
    oos = sel.wf.oos_returns
    returns = join_series(oos, holdout, cfg.holdout.start)

    hg = holdout_gate(holdout)
    hg = replace(
        hg,
        detail={
            **hg.detail,
            "params": sel.selected_params,
            "warm_up_from": cfg.selection.start.isoformat(),
        },
    )
    dg = dsr_gate(returns, sel.trial_sharpes)
    dg = replace(
        dg,
        detail={
            **dg.detail,
            "unit": DSR_UNIT,
            "sources": dict(sel.sources),
            "returns_series": RETURNS_SERIES,
            "series": {
                "walk_forward_oos": _summary(oos),
                "holdout": _summary(holdout),
                RETURNS_SERIES: _summary(returns),
            },
            "fold_params": [dict(p) for p in sel.wf.fold_params],
            "prior_fold_params": sel.prior_fold_params,
        },
    )
    pg = psr_gate(returns)
    bg = bootstrap_gate(returns, n_samples=cfg.bootstrap_resamples, seed=cfg.seed)
    rg = regime_gate(returns)
    pbo_g = GateResult(
        "pbo",
        None,
        sel.pbo,
        {
            "fragile_above": THRESHOLDS.pbo_fragile_above,
            "high": sel.pbo > THRESHOLDS.pbo_fragile_above,
            "configurations": sel.configurations,
            "window": {
                "start": cfg.selection.start.isoformat(),
                "end": cfg.selection.end.isoformat(),
            },
        },
    )

    folds = make_folds(pd.DatetimeIndex(sel.sel_bars.index), cfg.n_folds)
    cs = _cost_sweep_gate(cfg, sel, folds, friction, macro)

    fold_results = [
        run_backtest(
            build_strategy(spec.key, dict(fp)),
            sel.sel_bars,
            BacktestConfig(starting_capital=cfg.tier, friction=friction, seed=cfg.seed),
            macro,
            f.test_start,
            f.test_end,
        )
        for f, fp in zip(folds, sel.wf.fold_params, strict=True)
    ]
    rerun = concat_oos([r.returns for r in fold_results])
    if not (
        len(rerun) == len(oos)
        and bool((rerun.index == oos.index).all())
        and np.array_equal(rerun.to_numpy(), oos.to_numpy())
    ):
        raise RunRefused(
            "the folds re-run with their fold_params did not reproduce walk_forward's "
            "out-of-sample returns; turnover and capacity would describe other trades"
        )
    weights = [trade_weights(r, spec.symbol) for r in fold_results]
    to = pooled_turnover(weights)
    cap = pooled_capacity(weights, dollar_adv(sel.sel_long, spec.symbol), spec.symbol)

    dsr = DsrRecord(
        value=float(dg.value),  # type: ignore[arg-type]
        unit=DSR_UNIT,
        trial_count=int(sel.trial_sharpes.size),
        trial_sharpe_sources=dict(sel.sources),
        returns_series=RETURNS_SERIES,
    )
    return Evaluation(
        selection=sel,
        gates=(hg, dg, pg, bg, rg, pbo_g, cs),
        dsr=dsr,
        psr=float(pg.value),  # type: ignore[arg-type]
        pbo=sel.pbo,
        turnover=to,
        capacity=cap,
    )


# --------------------------------------------------------------------------
# Selection, the holdout, and the series the gates read.
# --------------------------------------------------------------------------


def select_params(spec: GridSpec, sharpes: Sequence[float] | np.ndarray) -> dict[str, Any]:
    """walk_forward's own selection rule (``fdq/validation/walkforward.py``):
    the configuration with the highest per-bar Sharpe, the first in grid
    order on a tie (walk_forward keeps a strictly greater one only;
    ``np.argmax`` returns the first maximum). ``sharpes`` is one per
    ``grid_combos(spec.grid)``, in that order, over the whole selection
    window (``in_sample_return_matrix``)."""
    combos = grid_combos(spec.grid)
    s = np.asarray(sharpes, dtype=float)
    if s.size != len(combos):
        raise RunRefused(f"select_params: {s.size} Sharpes for {len(combos)} configurations")
    if not np.all(np.isfinite(s)):
        raise RunRefused("select_params: a configuration's Sharpe is not a finite number")
    return {**spec.base_params, **combos[int(np.argmax(s))]}


def holdout_returns(
    key: str,
    params: Mapping[str, Any],
    bars: pd.DataFrame,
    window: Window,
    tier: float,
    friction: FrictionConfig,
    macro: pd.DataFrame | None,
    seed: int,
) -> pd.Series:
    """One backtest of ``params`` over ``window``. ``bars`` may, and should,
    hold bars before ``window.start``: fdq's strategies read every bar up to
    each day, so the moving average warms up on them. Its returns are the
    window's bars exactly -- the first is dated at the first bar on or after
    ``window.start`` and none earlier -- and any other set of dates is refused,
    never trimmed into shape."""
    macro = _require_macro(macro)
    idx = pd.DatetimeIndex(bars.index)
    start, end = pd.Timestamp(window.start), pd.Timestamp(window.end)
    expected = idx[(idx >= start) & (idx <= end)]
    if len(expected) == 0:
        raise RunRefused(f"no bars in the holdout window {window.start}..{window.end}")
    res = run_backtest(
        build_strategy(key, dict(params)),
        bars,
        BacktestConfig(starting_capital=tier, friction=friction, seed=seed),
        macro,
        window.start,
        window.end,
    )
    r = res.returns
    if len(r) != len(expected) or not bool((pd.DatetimeIndex(r.index) == expected).all()):
        raise RunRefused(
            "the holdout series' dates are not exactly the holdout window's bars; "
            "a return from outside the window would enter the holdout"
        )
    return r


def join_series(oos: pd.Series, holdout: pd.Series, holdout_start: date) -> pd.Series:
    """The walk-forward's out-of-sample series followed by the holdout's:
    what DSR, PSR, the bootstrap and the regimes read (``RETURNS_SERIES``).
    Every walk-forward return must predate the holdout and every holdout
    return must not, so neither can stand in for the other."""
    start = pd.Timestamp(holdout_start)
    if bool((pd.DatetimeIndex(oos.index) >= start).any()):
        raise RunRefused("a walk-forward return is dated inside the holdout")
    if bool((pd.DatetimeIndex(holdout.index) < start).any()):
        raise RunRefused("a holdout return is dated before the holdout starts")
    out = pd.concat([oos, holdout]).rename("returns")
    if not out.index.is_monotonic_increasing or out.index.has_duplicates:
        raise RunRefused("the joined series is not strictly increasing in date")
    return out


def concat_oos(parts: Sequence[pd.Series]) -> pd.Series:
    """Fold returns joined exactly as walk_forward joins its own."""
    out = pd.concat(list(parts)).sort_index()
    return out[~out.index.duplicated(keep="first")]


# --------------------------------------------------------------------------
# The cost sweep: re-evaluated, never re-selected.
# --------------------------------------------------------------------------


def spread_bps_for_round_trip(level_bps: float) -> float:
    """fdq's ``spread_bps_default`` for a cost-sweep level given in basis
    points round trip: the level times ``FDQ_EXIT_SPREAD_CORRECTION``. fdq's
    ``BrokerEmulator.apply_fill`` charges ``spread_bps / 1e4 / 2`` per fill,
    so fdq's ``spread_bps`` is a round trip halved per fill, as the
    pre-registration says; but fdq's engine drops the exit's half from
    equity, so the setting is doubled and the entry fill pays the whole
    level. One round trip at level L then costs L bps of equity."""
    return float(level_bps) * FDQ_EXIT_SPREAD_CORRECTION


def sweep_friction(base: FrictionConfig, level_bps: float) -> FrictionConfig:
    """The base friction with every symbol's spread set to the level: the
    default set by ``spread_bps_for_round_trip`` and the per-symbol table
    cleared, so no symbol keeps its own spread. Fees, the VIX widening
    (x1.5 above 25), the stress multiplier (1), the cash buffer, the minimum
    order and settlement are all kept."""
    return replace(
        base,
        spread_bps_default=spread_bps_for_round_trip(level_bps),
        spread_bps_by_symbol={},
    )


def evaluate_fixed(
    key: str,
    fold_params: Sequence[Mapping[str, Any]],
    holdout_params: Mapping[str, Any],
    sel_bars: pd.DataFrame,
    folds: Sequence[Fold],
    hold_bars: pd.DataFrame,
    holdout: Window,
    tier: float,
    friction: FrictionConfig,
    macro: pd.DataFrame | None,
    seed: int,
) -> tuple[pd.Series, pd.Series]:
    """The series the gates read, rebuilt at ``friction`` from parameters
    already chosen at the base friction and never chosen here: each fold's
    own ``fold_params`` over its own test span (walk_forward's out-of-sample
    construction), then ``holdout_params`` (``selected_params``) over the
    holdout. Nothing in this function compares configurations."""
    macro = _require_macro(macro)
    parts = [
        run_backtest(
            build_strategy(key, dict(p)),
            sel_bars,
            BacktestConfig(starting_capital=tier, friction=friction, seed=seed),
            macro,
            f.test_start,
            f.test_end,
        ).returns
        for f, p in zip(folds, fold_params, strict=True)
    ]
    hold = holdout_returns(key, holdout_params, hold_bars, holdout, tier, friction, macro, seed)
    return concat_oos(parts), hold


def _cost_sweep_gate(
    cfg: ExperimentConfig,
    sel: Selection,
    folds: Sequence[Fold],
    friction: FrictionConfig,
    macro: pd.DataFrame,
) -> GateResult:
    spec = sel.strategy.spec
    seen: dict[float, tuple[pd.Series, pd.Series]] = {}

    def at(level: float) -> pd.Series:
        oos, hold = evaluate_fixed(
            spec.key,
            sel.wf.fold_params,
            sel.selected_params,
            sel.sel_bars,
            folds,
            sel.hold_bars,
            cfg.holdout,
            cfg.tier,
            sweep_friction(friction, level),
            macro,
            cfg.seed,
        )
        seen[float(level)] = (oos, hold)
        return join_series(oos, hold, cfg.holdout.start)

    g = cost_sweep(at, cfg.cost_levels)
    total_by: dict[str, float] = {}
    holdout_sharpe_by: dict[str, float] = {}
    holdout_by: dict[str, float] = {}
    for level, (oos, hold) in seen.items():
        joined = join_series(oos, hold, cfg.holdout.start)
        total_by[str(level)] = _compounded(joined)
        holdout_sharpe_by[str(level)] = float(sharpe(hold))
        holdout_by[str(level)] = _compounded(hold)
    return replace(
        g,
        detail={
            **g.detail,
            "total_return_by_bps": total_by,
            "holdout_sharpe_by_bps": holdout_sharpe_by,
            "holdout_total_return_by_bps": holdout_by,
            "params": {
                "folds": [dict(p) for p in sel.wf.fold_params],
                "holdout": sel.selected_params,
            },
            "reselected": False,
            "covers": (
                "sharpe_by_bps and total_return_by_bps: the joined series the gates read, "
                "walk_forward_oos+holdout; holdout_sharpe_by_bps and "
                "holdout_total_return_by_bps: the holdout alone"
            ),
            "spans": (
                "each walk-forward fold's own fold_params over its test span, then "
                "selected_params over the holdout; all chosen at the base friction"
            ),
            "conversion": (
                "spread_bps_default = the level in bps round trip x FDQ_EXIT_SPREAD_CORRECTION, "
                "per-symbol table cleared, so the entry fill pays the whole level; "
                "fees and the VIX widening kept"
            ),
            "exit_spread_correction": FDQ_EXIT_SPREAD_CORRECTION,
        },
    )


# --------------------------------------------------------------------------
# Turnover and capacity, from each fold's own fold_params backtest.
# --------------------------------------------------------------------------


def trade_weights(result: BacktestResult, symbol: str) -> pd.DataFrame:
    """The weight in ``symbol`` a backtest actually held, stepping only when
    it traded: each trade moves the weight by its signed notional over the
    equity at the previous close (the equity the order was sized from --
    fdq sizes the day's orders at the prior close and fills them at the
    open). Between trades a held position drifts with its price; drift is
    not a trade, so it moves nothing here, and a rejected order (no fill, no
    row in ``trades``) moves nothing either.

    One column, ``symbol``, indexed like the equity curve. Row 0 is the
    starting book: fdq's backtest starts in cash and cannot fill on its
    first day, so it is 0 -- which ``gates.turnover`` requires to count the
    first entry."""
    eq = result.equity_curve
    if eq.empty:
        raise RunRefused("trade_weights: an empty backtest")
    dw = pd.Series(0.0, index=pd.DatetimeIndex(eq.index))
    prev = eq.shift(1)
    trades = result.trades
    if not trades.empty:
        for row in trades.itertuples(index=False):
            if row.symbol != symbol:
                raise RunRefused(f"trade_weights: a trade in {row.symbol}, expected {symbol}")
            if row.side not in ("buy", "sell"):
                raise RunRefused(f"trade_weights: unknown side {row.side!r}")
            ts = pd.Timestamp(row.date)
            if ts not in dw.index:
                raise RunRefused(f"trade_weights: a trade on {ts.date()} outside the backtest")
            base = float(prev.loc[ts])
            if not (math.isfinite(base) and base > 0):
                raise RunRefused(f"trade_weights: no prior-close equity for {ts.date()}'s trade")
            sign = 1.0 if row.side == "buy" else -1.0
            dw.loc[ts] += sign * float(row.notional) / base
    if dw.iloc[0] != 0.0:
        raise RunRefused("trade_weights: the backtest traded on its first day; it cannot")
    return dw.cumsum().to_frame(symbol)


def pooled_turnover(weights: Sequence[pd.DataFrame]) -> float | None:
    """``gates.turnover`` over every fold's own day-over-day changes, and
    none across a fold boundary: each fold is a fresh fdq backtest that
    starts in cash, so the drop from one fold's last position to the next
    fold's empty book is not a trade anyone paid for (the next fold's
    re-entry is, and is counted). Each fold's annualised rate is weighted by
    its number of changes, which is ``gates.turnover``'s own formula applied
    to the pooled changes: 252 x (sum of one-way turnover) / (sum of N-1).
    ``None`` when no fold has two rows: nothing measured."""
    num, den = 0.0, 0
    for w in weights:
        t = turnover(w)
        if t is None:
            continue
        num += t * (len(w) - 1)
        den += len(w) - 1
    return None if den == 0 else num / den


def pooled_capacity(
    weights: Sequence[pd.DataFrame], adv_dollars: pd.Series, symbol: str
) -> float | None:
    """``gates.capacity`` over every fold's trades, each trade the day's
    weight change (``weights.fillna(0).diff()``, as ``gates.turnover``
    computes it), row 0 -- the starting book -- dropped from each fold. The
    ADV series is passed whole, unknown days and all: ``capacity`` refuses a
    traded day whose ADV is unknown, and that refusal is the honest answer.
    ``None`` when nothing traded: none measured."""
    parts = [w[symbol].fillna(0.0).diff().iloc[1:] for w in weights if len(w) > 1]
    if not parts:
        return None
    trades = pd.concat(parts)
    if trades.index.has_duplicates:
        raise RunRefused("pooled_capacity: two folds share a day")
    return capacity(trades, adv_dollars)


# --------------------------------------------------------------------------
# The verdict's words.
# --------------------------------------------------------------------------


def verdict_line(verdict: str, gates: Sequence[Mapping[str, Any]], pbo: float) -> str:
    """One sentence, at most 200 characters, that leads with the verdict and
    the gates and never with a return: every failed gate is named, and PBO is
    always reported, with "above 0.5" when it is."""
    decided = [g for g in gates if g["passed"] is not None]
    failed = [g["name"] for g in decided if g["passed"] is False]
    fragile = THRESHOLDS.pbo_fragile_above
    pbo_part = f"PBO {pbo:.3f}" + (f", above {fragile:g}" if pbo > fragile else "")
    if verdict == "fail":
        if not decided:
            line = f"fail: no gate was decided; {pbo_part}"
        else:
            line = (
                f"fail: {len(failed)} of {len(decided)} gates failed ({', '.join(failed)}); "
                f"{pbo_part}"
            )
    elif verdict == "pass, fragile":
        line = (
            f"pass, fragile: all {len(decided)} gates passed; {pbo_part}, "
            "so not promoted without a second window"
        )
    elif verdict == "pass":
        line = f"pass: all {len(decided)} gates passed; {pbo_part}"
    else:
        raise ValueError(f"verdict_line: unknown verdict {verdict!r}")
    if len(line) > 200:
        raise ValueError(f"verdict_line: {len(line)} characters, over 200")
    return line


def describe_measure(value: float | None, fmt: str = "{:,.2f}") -> str:
    """How a report states turnover or capacity: ``None`` is "none
    measured", never 0 -- nothing was measured, which is not the same as
    measuring nothing."""
    return NONE_MEASURED if value is None else fmt.format(value)


# --------------------------------------------------------------------------
# Assembly and guards.
# --------------------------------------------------------------------------


def _manifest(
    cfg: ExperimentConfig, ev: Evaluation, hashes: dict[str, str], ran_at: str
) -> Manifest:
    sel = ev.selection
    spec = sel.strategy.spec
    gates = tuple(_gate_dict(g) for g in ev.gates)
    verdict = compute_verdict(gates, ev.pbo)
    notes = [
        "selected_params: walk_forward's rule (highest per-bar Sharpe, first in grid order "
        "on a tie) over the whole selection window; each fold's own choice is in the dsr "
        "gate's detail as fold_params",
        *SPREAD_NOTES,
        PBO_NOTE,
        SWEEP_NOTE,
        _coverage_note(sel.sel_bars.index, cfg.n_folds),
    ]
    if cfg.stress_multipliers is not None:
        notes.append(
            f"stress_multipliers {[_num(x) for x in cfg.stress_multipliers]} are not read: "
            "the cost sweep is cost_sweep_bps_round_trip, in bps round trip"
        )
    if ev.turnover is None:
        notes.append(f"turnover: {NONE_MEASURED}")
    if ev.capacity is None:
        notes.append(f"capacity: {NONE_MEASURED}")
    return Manifest(
        experiment=cfg.id,
        slug=sel.strategy.slug,
        strategy=spec.key,
        symbol=spec.symbol,
        selected_params=_plain(sel.selected_params),
        selection_window=ManifestWindow(
            cfg.selection.start.isoformat(), cfg.selection.end.isoformat()
        ),
        holdout_window=ManifestWindow(cfg.holdout.start.isoformat(), cfg.holdout.end.isoformat()),
        gates_version=GATES_VERSION,
        gates=gates,
        dsr=ev.dsr,
        psr=ev.psr,
        pbo=ev.pbo,
        turnover=ev.turnover,
        capacity=ev.capacity,
        verdict=verdict,
        verdict_line=verdict_line(verdict, gates, ev.pbo),
        hashes=hashes,
        ran_at=ran_at,
        python_version=platform.python_version(),
        notes=tuple(notes),
    )


def _gate_dict(g: GateResult) -> dict[str, Any]:
    return {
        "name": g.name,
        "passed": None if g.passed is None else bool(g.passed),
        "value": None if g.value is None else _plain(float(g.value)),
        "detail": _plain(g.detail),
    }


def _plain(x: Any) -> Any:
    """JSON's own types, with every number finite: a NaN in the evidence is
    a bug in whatever computed it, refused here rather than written."""
    if isinstance(x, Mapping):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        f = float(x)
        if not math.isfinite(f):
            raise RunRefused(f"a non-finite number ({f}) in the evidence")
        return f
    if isinstance(x, pd.Timestamp):
        return x.date().isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if x is None or isinstance(x, str):
        return x
    raise TypeError(f"_plain: {type(x).__name__} is not JSON-shaped")


def _num(x: float) -> float | int:
    return int(x) if float(x).is_integer() else x


def _coverage_note(index: pd.Index, n_folds: int) -> str:
    """What walk_forward's out-of-sample series leaves out, stated from the
    selection window's own bars: ``make_folds`` cuts ``n`` bars into
    ``n_folds + 1`` blocks of ``n // (n_folds + 1)``; the first block only
    ever trains, and the ``n mod (n_folds + 1)`` bars after the last block
    fall in no fold's test span."""
    idx = pd.DatetimeIndex(index)
    n = len(idx)
    block = n // (n_folds + 1)
    left = n - (n_folds + 1) * block
    if left:
        tail = f"{idx[-left].date()}..{idx[-1].date()}" if left > 1 else f"{idx[-1].date()}"
        tail_words = f"the last {left} ({tail}) fall in no fold's test span"
    else:
        tail_words = "no bar at the end is left out"
    return (
        f"walk-forward coverage: {n} selection bars in {n_folds + 1} blocks of {block}; the "
        f"first {block} ({idx[0].date()}..{idx[block - 1].date()}) only train, and "
        f"{tail_words}; each fold's out-of-sample backtest restarts in cash and re-enters"
    )


def _summary(s: pd.Series) -> dict[str, Any]:
    """What a report needs of a return series, so it never has to recompute
    anything outside the battery: its span, fdq's annualised Sharpe, fdq's
    max drawdown of the compounded curve, and the compounded return."""
    idx = pd.DatetimeIndex(s.index)
    # The curve starts at 1 before the first return, so a first-day loss is
    # a drawdown and a first-day return is compounded.
    curve = pd.Series(np.concatenate([[1.0], np.cumprod(1.0 + s.to_numpy(dtype=float))]))
    return {
        "first": idx[0].date().isoformat(),
        "last": idx[-1].date().isoformat(),
        "bars": len(s),
        "sharpe": float(sharpe(s)),
        "max_drawdown": float(max_drawdown(curve)),
        "total_return": _compounded(s),
    }


def _compounded(s: pd.Series) -> float:
    """Every return compounded, the first included: holdout_gate's arithmetic."""
    return float((1.0 + s).prod() - 1.0)


def _wide_for(long: pd.DataFrame, symbol: str) -> pd.DataFrame:
    rows = long.loc[long["symbol"] == symbol]
    if rows.empty:
        raise RunRefused(f"no {symbol} bars in the window")
    return wide(rows)


def _require_count(name: str, wf: WalkForwardResult, expected: int) -> None:
    if wf.n_trials != expected or wf.trial_sharpes.size != expected:
        raise RunRefused(
            f"{name}: fdq returned {wf.trial_sharpes.size} fold-trials (n_trials "
            f"{wf.n_trials}), the grid and n_folds give {expected}; the run stops"
        )


def _require_macro(macro: pd.DataFrame | None) -> pd.DataFrame:
    if macro is None:
        raise ValueError(
            "macro is None: fdq would drop the VIX spread widening EXP-002 ran with; refused"
        )
    if "vix" not in macro.columns:
        raise ValueError("macro has no 'vix' column: the VIX widening cannot run; refused")
    return macro


def _require_macro_covers(macro: pd.DataFrame, index: pd.DatetimeIndex) -> None:
    m = pd.DatetimeIndex(macro.index)
    if m[0] > index[0] or m[-1] < index[-1]:
        raise RunRefused(
            f"the macro series ({m[0].date()}..{m[-1].date()}) does not cover the bars "
            f"({index[0].date()}..{index[-1].date()}); the VIX widening would lapse"
        )


def load_friction(path: Path, version: str) -> FrictionConfig:
    """The base friction the runner hands fdq: the vendored friction file,
    loaded by its own path -- never fdq's relative default, which resolves
    against whatever the working directory happens to be -- with its default
    spread and every per-symbol spread multiplied by
    ``FDQ_EXIT_SPREAD_CORRECTION``. Everything else is the file's."""
    raw = load_friction_config(path, stress_multiplier=1.0)
    if raw.version != version:
        raise RunRefused(
            f"{path.name} is friction version {raw.version}, the config names {version}"
        )
    return replace(
        raw,
        spread_bps_default=raw.spread_bps_default * FDQ_EXIT_SPREAD_CORRECTION,
        spread_bps_by_symbol={
            k: v * FDQ_EXIT_SPREAD_CORRECTION for k, v in raw.spread_bps_by_symbol.items()
        },
    )


def _battery_bytes(d: Path) -> dict[str, bytes]:
    return {
        p.relative_to(d).as_posix(): p.read_bytes()
        for p in sorted(d.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def _require_running_battery_is_hashed(root: Path) -> None:
    here = Path(__file__).resolve().parent
    there = (root / BATTERY_REL).resolve()
    if here == there:
        return
    if _battery_bytes(here) != _battery_bytes(there):
        raise RunRefused(
            f"{there} is not the battery that is running ({here}); the manifest would "
            "hash other code than the code that judged"
        )


# Re-exported for the CLI and the tests, which name them from here.
__all__ = [
    "DSR_UNIT",
    "FDQ_EXIT_SPREAD_CORRECTION",
    "NONE_MEASURED",
    "PBO_NOTE",
    "RETURNS_SERIES",
    "SPREAD_NOTES",
    "SWEEP_NOTE",
    "Evaluation",
    "RunRefused",
    "Selection",
    "concat_oos",
    "default_repo_root",
    "describe_measure",
    "evaluate_fixed",
    "holdout_returns",
    "join_series",
    "judge_strategy",
    "load_friction",
    "pooled_capacity",
    "pooled_turnover",
    "run",
    "select_params",
    "select_strategy",
    "spread_bps_for_round_trip",
    "sweep_friction",
    "trade_weights",
    "verdict_line",
]
