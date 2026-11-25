"""The charter's gates that `fdq`'s runner does not compute, as pure functions
of a return series, plus the two reported metrics (`turnover`, `capacity`)
Task 13's report needs and `fdq` does not provide.

Every number a function here is compared against comes from `THRESHOLDS`
below -- the charter's gates (`docs/CHARTER.md`), applied literally, in
exactly one place. No other threshold literal appears in this module, so a
gate here and the report that reads its verdict can never disagree about the
line. DSR and PBO are computed by `fdq`, not here: `dsr_gate` calls fdq's
`deflated_sharpe` unchanged and only compares its answer with the table's
line, and PBO is reported, never gated (its fragility line is the manifest's
verdict rule's). `holdout_gate` is Alpha's "holdout positive", read from the
holdout series alone.

`capacity` additionally assumes a fixed 1% ADV participation cap
(`MAX_ADV_PARTICIPATION` below), the same default the desk itself holds for
live trading (`Desk_spec.max_adv_participation`, `lib/config.ml`). Since
completion task 2.3 it is read from the one cost file,
`research/config/costs.yaml` (`capacity.participation`), the file the quant
backtest, the farm and the desk's daily signals read their costs and capacity
from -- not a copy held here. It is still held separately from the desk's live
copy: a book.sexp can override that per book, and doing so does not move this
one. `capacity` reports what 1% headroom looks like, not whatever a book
currently sets. The manifest hashes the cost file (`manifest.COSTS_REL`), so
editing it stales the evidence like editing this module.

Completion task 2.5 (spec ruling R3, awaiting the owner): PSR and DSR at 0.95; the excess gate
(`excess_gate`: Hansen's SPA p-value against the universe's equal-weight
buy-and-hold at most 0.10) -- this battery has no SPA test yet (fdq ships
none), so the runner fails it, saying so, rather than leave a charter gate
unapplied; and `REGIMES` is the one regime table,
`research/config/regimes.yaml` (`regimes.py`), which the quant battery reads
too. The manifest hashes that file (`manifest.REGIMES_REL`). Fewer than
`regime_min_positive` regimes with data is recorded on the regime gate
(`insufficient_data`) and judged INSUFFICIENT DATA by `manifest.compute_verdict`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from fdq.validation.dsr import deflated_sharpe, probabilistic_sharpe
from fdq.validation.metrics import sharpe

from ohcamel_research.battery import regimes as _regime_file


@dataclass(frozen=True)
class Thresholds:
    """The charter's gates, literally (docs/CHARTER.md, "The gates" table).
    Every gate function below reads its comparison value from an instance of
    this class (`THRESHOLDS`); nothing here is a parameter a caller tunes per
    strategy. `dsr_min` and `pbo_fragile_above` are not used by any function
    in this module -- DSR and PBO are `fdq`'s -- but they are recorded here
    so Task 11's verdict and Task 13's report read the same numbers this
    module does, instead of a second copy that could drift."""

    psr_min: float = 0.95
    dsr_min: float = 0.95
    # completion task 2.5 (R3): Hansen's SPA consistent p-value against the
    # universe's equal-weight buy-and-hold, at or below this, passes `excess`.
    spa_max_p: float = 0.10
    bootstrap_resamples: int = 1000
    bootstrap_block: int = 21
    bootstrap_lower_percentile: float = 5.0
    regime_min_positive: int = 3
    cost_sweep_bps_round_trip: tuple[float, ...] = (0.0, 5.0, 15.0, 30.0)
    # ruling 1, the owner's EXP-A01 decision: hypothesis.md's "pass, fragile"
    # above this, not promoted without a second, independent window.
    pbo_fragile_above: float = 0.5


# docs/CHARTER.md, "The gates" table, literally. Changing any value here is
# the owner's decision, argued before a result -- never a task's, and never
# in response to a result this table already produced.
THRESHOLDS = Thresholds()

# The desk's own live participation cap defaults to 1% of a symbol's 20-day
# ADV (`Desk_spec.max_adv_participation`, `lib/config.ml` around line 185 in
# the main OhCamel checkout), and a book.sexp can override that copy per
# book. The research layer's assumption for `capacity`'s headroom estimate is
# read from the one cost file (completion task 2.3) -- the path is resolved
# from this file's own location, because nothing under battery/ may import
# the package root (tests/test_battery_boundary.py) -- and does not track
# whatever a book currently sets for the desk.
COSTS_PATH = Path(__file__).resolve().parents[4] / "research" / "config" / "costs.yaml"
MAX_ADV_PARTICIPATION = float(
    yaml.safe_load(COSTS_PATH.read_text(encoding="utf-8"))["capacity"]["participation"]
)

# The one regime table (completion task 2.5): research/config/regimes.yaml,
# read through regimes.py -- never a copy held here. Both batteries read it.
REGIMES: dict[str, tuple[date, date]] = {
    name: (date.fromisoformat(a), date.fromisoformat(b)) for name, a, b in _regime_file.load()
}
EXCESS_RULE = f"SPA p <= {THRESHOLDS.spa_max_p:.2f} vs buy-and-hold"


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool | None  # None: reported, not gated (the cost sweep)
    value: float | None
    detail: dict[str, Any] = field(default_factory=dict)


def holdout_gate(returns: pd.Series) -> GateResult:
    """Alpha's "holdout positive": the compounded cumulative return of the
    holdout series, and only the holdout series, is strictly greater than
    zero. A flat holdout (all cash) is not a positive one. An empty series is
    a runner bug, not a result, and raises."""
    if len(returns) == 0:
        raise ValueError("holdout_gate: the holdout series is empty")
    cum = float((1.0 + returns).prod() - 1.0)
    idx = pd.DatetimeIndex(returns.index)
    return GateResult(
        "holdout_positive",
        cum > 0.0,
        cum,
        {
            "sharpe": float(sharpe(returns)),
            "bars": len(returns),
            "first": idx[0].date().isoformat(),
            "last": idx[-1].date().isoformat(),
        },
    )


def dsr_gate(
    returns: pd.Series,
    trial_sharpes: np.ndarray,
    threshold: float = THRESHOLDS.dsr_min,
) -> GateResult:
    """fdq's Deflated Sharpe Ratio, `deflated_sharpe(returns, trial_sharpes)`,
    called unchanged, against the charter's `dsr_min` (at or above passes).
    fdq takes the actual trial Sharpes, not a count: the number of trials is
    their length and the deflation reads their variance. No trials at all is
    refused -- fdq would answer 0.0, which reads as a result."""
    trials = np.asarray(trial_sharpes, dtype=float)
    if trials.size == 0:
        raise ValueError("dsr_gate: no trial Sharpes; a DSR deflated by nothing is not a DSR")
    dsr = float(deflated_sharpe(returns, trials))
    return GateResult(
        "dsr", dsr >= threshold, dsr, {"threshold": threshold, "trial_count": int(trials.size)}
    )


def psr_gate(returns: pd.Series, threshold: float = THRESHOLDS.psr_min) -> GateResult:
    """Probabilistic Sharpe Ratio against a zero benchmark: Pr(true SR > 0)."""
    psr = float(probabilistic_sharpe(returns, 0.0))
    return GateResult("psr", psr >= threshold, psr, {"threshold": threshold})


def _block_bootstrap_sharpes(
    returns: np.ndarray, n_samples: int, block: int, rng: np.random.Generator
) -> np.ndarray:
    """Stationary block bootstrap (Politis & Romano 1994): blocks of geometric
    length with mean `block`, wrapped circularly, so autocorrelation in the
    series survives resampling."""
    n = len(returns)
    out = np.empty(n_samples)
    p = 1.0 / block
    for i in range(n_samples):
        idx = np.empty(n, dtype=int)
        pos = rng.integers(n)
        for t in range(n):
            if t > 0 and rng.random() < p:
                pos = rng.integers(n)
            idx[t] = pos
            pos = (pos + 1) % n
        sample = returns[idx]
        sd = sample.std(ddof=1)
        out[i] = 0.0 if sd == 0 else (sample.mean() / sd) * np.sqrt(252.0)
    return out


def bootstrap_gate(
    returns: pd.Series,
    n_samples: int = THRESHOLDS.bootstrap_resamples,
    block: int = THRESHOLDS.bootstrap_block,
    *,
    seed: int,
) -> GateResult:
    """Lower 5th percentile of the bootstrapped annualised Sharpe must be > 0.

    `seed` has no default: the experiment's own `config.yaml` (`seed:`) is
    its only home, so a caller cannot silently fall back to a seed the
    config never chose."""
    rng = np.random.default_rng(seed)
    sharpes = _block_bootstrap_sharpes(returns.to_numpy(dtype=float), n_samples, block, rng)
    lo, med, hi = (
        float(np.percentile(sharpes, q)) for q in (THRESHOLDS.bootstrap_lower_percentile, 50, 95)
    )
    return GateResult(
        "bootstrap_sharpe_lower5",
        lo > 0.0,
        lo,
        {
            "n_samples": n_samples,
            "block": block,
            "seed": seed,
            "p50": med,
            "p95": hi,
            "lower_percentile": THRESHOLDS.bootstrap_lower_percentile,
        },
    )


def regime_gate(
    returns: pd.Series,
    regimes: dict[str, tuple[date, date]] = REGIMES,
    min_positive: int = THRESHOLDS.regime_min_positive,
) -> GateResult:
    """Positive cumulative return in at least `min_positive` of the named
    regimes the series actually covers. A regime the series does not reach is
    skipped and listed, not counted as a pass. "Positive" is the compounded
    cumulative return over the regime being strictly greater than zero -- not
    a majority of up days, not the arithmetic sum of daily returns, and not
    zero itself (an all-cash regime is not a positive one).

    No coverage threshold is imposed here: the charter states none, and the
    regime set is an open question for the owner, not this module's to
    decide by silently failing thin regimes. Instead every regime's bar
    count is reported in `detail["bar_counts"]`, including 0 for one the
    series does not reach, so the verdict discloses its own coverage."""
    idx = pd.to_datetime(returns.index)
    covered, positive, by_regime, bar_counts = [], 0, {}, {}
    for name, (a, b) in regimes.items():
        m = (idx >= pd.Timestamp(a)) & (idx <= pd.Timestamp(b))
        n = int(m.sum())
        bar_counts[name] = n
        if n == 0:
            continue
        covered.append(name)
        cum = float((1.0 + returns[m]).prod() - 1.0)
        by_regime[name] = cum
        positive += cum > 0
    return GateResult(
        "regimes_positive",
        positive >= min_positive,
        float(positive),
        {
            "covered": covered,
            "by_regime": by_regime,
            "min_positive": min_positive,
            "bar_counts": bar_counts,
            # completion task 2.5 (R3): fewer regimes with data than the gate
            # needs positive is INSUFFICIENT DATA, not FAIL -- the gate is not
            # passed, and manifest.compute_verdict reads this flag.
            "insufficient_data": len(covered) < min_positive,
        },
    )


def excess_gate(spa_p: float | None, reason: str | None = None) -> GateResult:
    """The charter's excess gate (completion task 2.5, spec ruling R3, awaiting the owner):
    Hansen's SPA consistent p-value of the strategy's trials against the
    universe's equal-weight buy-and-hold, filled and costed as the strategy
    is, at or below `THRESHOLDS.spa_max_p` passes. `None` -- a p-value that
    was not computed -- fails, with `reason` in the detail: a charter gate
    that cannot be computed is never passed."""
    ok = spa_p is not None and np.isfinite(spa_p) and spa_p <= THRESHOLDS.spa_max_p
    detail: dict[str, Any] = {
        "threshold": THRESHOLDS.spa_max_p,
        "rule": EXCESS_RULE,
        "benchmark": "the universe's equal-weight buy-and-hold",
    }
    if reason is not None:
        detail["reason"] = reason
    return GateResult("excess", bool(ok), None if spa_p is None else float(spa_p), detail)


def cost_sweep(
    run_at_bps: Callable[[float], pd.Series],
    bps_levels: tuple[float, ...] = THRESHOLDS.cost_sweep_bps_round_trip,
) -> GateResult:
    """Sharpe at each round-trip cost level in `cost_sweep_bps_round_trip`.
    How a level becomes a friction setting is Task 11's job; this function
    only calls what it is handed and reports what came back. Reported, never
    gated: the charter wants the sensitivity shown, not one assumption
    blessed."""
    # str(float(b)), not str(b): a YAML config's levels come back as ints
    # (`[0, 5, 15, 30]`), and this table's own default is floats. Without the
    # cast, the two sources would key the same sweep under different strings
    # ("0" vs "0.0") and a report reading one config's sweep against another
    # would silently miss every level.
    by = {str(float(b)): float(sharpe(run_at_bps(b))) for b in bps_levels}
    return GateResult(
        "cost_sweep", None, None, {"bps_levels": list(bps_levels), "sharpe_by_bps": by}
    )


def turnover(weights: pd.DataFrame) -> float | None:
    """Annualised one-way turnover: half the sum of absolute day-over-day
    weight changes (summed across symbols), scaled by 252 / (N-1), where N-1
    is the number of Delta-weight terms actually summed -- the first of N
    rows has no prior weight to diff against, so it is dropped rather than
    counted as a same-day change, and N-1 (not N) is the count of terms left.
    `fillna(0)` runs before `.diff()`, not after: a symbol's first recorded
    weight, entering from no prior position (NaN, not 0.0), is itself counted
    as a change from 0 rather than silently skipped as "no change" when its
    NaN predecessor is dropped by `skipna=True`.

    Row 0 is the starting book, not a trade: its weights have nothing before
    them in the series, so a position already held on the first row is never
    counted. A backtest must start flat (a warm-up of zero weights) for its
    first entry to be counted. Fewer than two rows give no Delta-weight term
    at all, so the rate is undefined and the answer is `None` -- never 0.0,
    which would read as "never traded", the same convention as `capacity`."""
    if len(weights) < 2:
        return None
    daily_abs_change = weights.fillna(0.0).diff().abs().sum(axis=1, skipna=True).iloc[1:]
    one_way = float(daily_abs_change.sum()) / 2.0
    days = len(weights)
    return one_way * 252.0 / (days - 1)


def capacity(trades: pd.Series, adv_dollars: pd.Series) -> float | None:
    """The capital at which the median trade reaches `MAX_ADV_PARTICIPATION`
    of that symbol's 20-day dollar ADV on its day. Per trade, capital =
    MAX_ADV_PARTICIPATION * ADV / |Delta weight|; a zero weight change is not
    a trade and is excluded before the median is taken.

    A frame with no trades returns `None`, not 0.0 -- 0.0 would read as "zero
    capacity" (nothing could be traded), when the true answer is "no trades
    to measure capacity from" at all.

    An unknown (NaN) ADV on any day actually traded (|Delta weight| > 0)
    raises `ValueError` naming the first such day, rather than silently
    dropping it from the median. The desk's own participation rule fails
    closed the same way on an unknown ADV: `Config.adv20 = None` refuses the
    order outright (`desk/rules.ml` around line 166, `fail "adv" "%s's
    twenty-day volume is unknown"`); this function's median must not treat
    an unknown ADV as if it simply were not there.

    A traded day absent from `adv_dollars` altogether is an unknown ADV too:
    the series is reindexed onto the trades' days first, so an ADV built with
    `rolling(20).mean().dropna()` cannot drop a trade by omission. A NaN
    `trades` value is an unknown trade, not "no trade", and raises as well:
    trades must be `weights.fillna(0).diff()`, as `turnover` computes them."""
    if trades.isna().any():
        first = trades[trades.isna()].index[0]
        raise ValueError(f"capacity: the trade on {first} is unknown (NaN)")
    adv_dollars = adv_dollars.reindex(trades.index)
    delta = trades.abs()
    traded = delta > 0
    if not traded.any():
        return None
    unknown_adv = traded & adv_dollars.isna()
    if unknown_adv.any():
        first = unknown_adv[unknown_adv].index[0]
        raise ValueError(f"capacity: {first} traded but its ADV is unknown (NaN)")
    per_trade_capital = MAX_ADV_PARTICIPATION * adv_dollars[traded] / delta[traded]
    return float(per_trade_capital.median())
