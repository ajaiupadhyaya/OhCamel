"""The charter's gates that `fdq`'s runner does not compute, as pure functions
of a return series, plus the two reported metrics (`turnover`, `capacity`)
Task 13's report needs and `fdq` does not provide.

Every number a function here is compared against comes from `THRESHOLDS`
below -- the charter's gates (`docs/CHARTER.md`), applied literally, in
exactly one place. No other threshold literal appears in this module, so a
gate here and the report that reads its verdict can never disagree about the
line. DSR and PBO are computed by `fdq` (Task 11's job), not here, but their
thresholds live in this same table for the same reason: one place, read by
both the code that gates and the prose that reports.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from fdq.validation.dsr import probabilistic_sharpe
from fdq.validation.metrics import sharpe


@dataclass(frozen=True)
class Thresholds:
    """The charter's gates, literally (docs/CHARTER.md, "The gates" table).
    Every gate function below reads its comparison value from an instance of
    this class (`THRESHOLDS`); nothing here is a parameter a caller tunes per
    strategy. `dsr_min` and `pbo_fragile_above` are not used by any function
    in this module -- DSR and PBO are `fdq`'s -- but they are recorded here
    so Task 11's verdict and Task 13's report read the same numbers this
    module does, instead of a second copy that could drift."""

    psr_min: float = 0.70
    dsr_min: float = 0.30
    bootstrap_resamples: int = 1000
    bootstrap_block: int = 21
    bootstrap_lower_percentile: float = 5.0
    regime_min_positive: int = 3
    cost_sweep_bps_round_trip: tuple[float, ...] = (0.0, 5.0, 15.0, 30.0)
    pbo_fragile_above: float = 0.5  # ruling 9: "pass, fragile" above this


THRESHOLDS = Thresholds()

REGIMES: dict[str, tuple[date, date]] = {
    "2018_q4_selloff": (date(2018, 10, 1), date(2018, 12, 31)),
    "2020_covid": (date(2020, 2, 19), date(2020, 6, 30)),
    "2022_rate_shock": (date(2022, 1, 1), date(2022, 12, 31)),
    "2023_recovery": (date(2023, 1, 1), date(2023, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
}


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool | None  # None: reported, not gated (the cost sweep)
    value: float | None
    detail: dict[str, Any] = field(default_factory=dict)


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
    seed: int = 42,
) -> GateResult:
    """Lower 5th percentile of the bootstrapped annualised Sharpe must be > 0."""
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
    skipped and listed, not counted as a pass."""
    idx = pd.to_datetime(returns.index)
    covered, positive, by_regime = [], 0, {}
    for name, (a, b) in regimes.items():
        m = (idx >= pd.Timestamp(a)) & (idx <= pd.Timestamp(b))
        if not m.any():
            continue
        covered.append(name)
        cum = float((1.0 + returns[m]).prod() - 1.0)
        by_regime[name] = cum
        positive += cum > 0
    return GateResult(
        "regimes_positive",
        positive >= min_positive,
        float(positive),
        {"covered": covered, "by_regime": by_regime, "min_positive": min_positive},
    )


def cost_sweep(
    run_at_bps: Callable[[float], pd.Series],
    bps_levels: tuple[float, ...] = THRESHOLDS.cost_sweep_bps_round_trip,
) -> GateResult:
    """Sharpe at each round-trip cost level in `cost_sweep_bps_round_trip`.
    How a level becomes a friction setting is Task 11's job; this function
    only calls what it is handed and reports what came back. Reported, never
    gated: the charter wants the sensitivity shown, not one assumption
    blessed."""
    by = {str(b): float(sharpe(run_at_bps(b))) for b in bps_levels}
    return GateResult(
        "cost_sweep", None, None, {"bps_levels": list(bps_levels), "sharpe_by_bps": by}
    )


def turnover(weights: pd.DataFrame) -> float:
    """Annualised one-way turnover: half the sum of absolute day-over-day
    weight changes (summed across symbols), scaled by 252 / (days in the
    sample) -- the same days-in-sample convention `fdq.validation.metrics`
    uses to annualise. The first row has no prior weight to diff against, so
    it is dropped rather than counted as a same-day change."""
    if weights.empty:
        return 0.0
    daily_abs_change = weights.diff().abs().sum(axis=1, skipna=True).iloc[1:]
    one_way = float(daily_abs_change.sum()) / 2.0
    days = len(weights)
    return one_way * 252.0 / days


def capacity(trades: pd.Series, adv_dollars: pd.Series) -> float:
    """The capital at which the median trade reaches 1% of that symbol's
    20-day dollar ADV on its day. Per trade, capital = 0.01 * ADV / |Delta
    weight|; a zero weight change is not a trade and is excluded before the
    median is taken."""
    delta = trades.abs()
    traded = delta > 0
    if not traded.any():
        return 0.0
    per_trade_capital = 0.01 * adv_dollars[traded] / delta[traded]
    return float(per_trade_capital.median())
