"""Liquidity: how long a portfolio takes to unwind, and what unwinding it costs.

The same three figures, with the same rules, as ``lib/liquidity.ml``, the engine's
pure kernel. test_risk_liquidity.py pins that kernel's worked example, so the two
definitions cannot drift apart unnoticed.

* **Days to liquidate**, at a fixed participation rate ``p`` of average daily
  volume: ``days = |q| / (p * ADV)``.
* **Impact**, Toth et al. (2011)'s square-root law, the price concession for
  immediacy: ``impact_fraction = Y * sigma * sqrt(|q| / ADV)``.
* **L-VaR**, Bangia et al. (1999), with the spread-volatility term at zero as the
  engine has it: the portfolio's VaR plus the cost of crossing the half-spread on
  every position, today. Impact is reported beside it and is not part of it.

**Unknown is not zero.** A position with no usable ADV or volatility cannot have
its days or impact computed, and because a total that dropped it would be a partial
sum claiming to be complete, one such line makes those totals ``None``. A position
of zero shares is the opposite edge: nothing to unwind is a known zero.

**The half-spread is an assumption, and is stated as one.** There is no quote
history to measure it from, and the daily high-low estimators do not measure it on
liquid names: over the last year of the fixtures, Corwin & Schultz (2012) and Abdi &
Ranaldo (2017) put SPY at 18 and 15 bps against a half-spread near 1 bp, because
there the day's range is volatility and not spread. So the defaults are the book's
own (``book.example.sexp``), overridable per request, and every line says which it
used.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: The book's ``spread_bps_default`` and ``spread_bps``: a per-fill half-spread.
DEFAULT_HALF_SPREAD_BPS = 5.0
HALF_SPREAD_BPS: Mapping[str, float] = {"SPY": 1.0, "TLT": 1.5}
#: lib/liquidity.ml's worked rate, and the square-root law's empirical constant.
DEFAULT_PARTICIPATION = 0.10
DEFAULT_IMPACT_COEFFICIENT = 1.0
ADV_WINDOW = 20


@dataclass(frozen=True)
class Position:
    ticker: str
    qty: float
    price: float
    adv20: float | None
    daily_stddev: float | None
    half_spread_bps: float


@dataclass(frozen=True)
class Line:
    ticker: str
    days_to_liquidate: float | None
    impact_fraction: float | None
    impact_cost: float | None
    spread_cost: float | None


@dataclass(frozen=True)
class Liquidity:
    lines: tuple[Line, ...]
    spread_cost: float | None
    impact_cost: float | None
    max_days_to_liquidate: float | None


def half_spread(ticker: str, default: float, overrides: Mapping[str, float]) -> tuple[float, str]:
    """The half-spread for a name in bps, and which assumption supplied it."""
    if ticker in overrides:
        return overrides[ticker], "request"
    if ticker in HALF_SPREAD_BPS:
        return HALF_SPREAD_BPS[ticker], "book"
    return default, "default"


def _usable(adv: float, sigma: float, price: float) -> bool:
    return (math.isfinite(adv) and adv > 0.0 and math.isfinite(sigma) and sigma >= 0.0
            and math.isfinite(price) and price > 0.0)


def line(p: Position, participation: float, impact_coefficient: float) -> Line:
    if not math.isfinite(p.half_spread_bps):
        return Line(p.ticker, None, None, None, None)
    exposure = abs(p.qty * p.price)
    spread_cost = exposure * p.half_spread_bps * 1e-4
    if p.qty == 0.0:
        return Line(p.ticker, 0.0, 0.0, 0.0, spread_cost)
    adv, sigma = p.adv20, p.daily_stddev
    if adv is None or sigma is None or not _usable(adv, sigma, p.price):
        return Line(p.ticker, None, None, None, spread_cost)
    aq = abs(p.qty)
    fraction = impact_coefficient * sigma * math.sqrt(aq / adv)
    return Line(p.ticker, aq / (participation * adv), fraction, fraction * exposure, spread_cost)


def _total(values: Sequence[float | None], combine) -> float | None:
    total = 0.0
    for v in values:
        if v is None:
            return None
        total = combine(total, v)
    return total


def compute(
    positions: Sequence[Position],
    participation: float = DEFAULT_PARTICIPATION,
    impact_coefficient: float = DEFAULT_IMPACT_COEFFICIENT,
) -> Liquidity:
    if not (math.isfinite(participation) and 0.0 < participation <= 1.0):
        raise ValueError(f"participation must lie in (0, 1], got {participation}")
    if not (math.isfinite(impact_coefficient) and impact_coefficient >= 0.0):
        raise ValueError(f"impact_coefficient must be finite and >= 0, got {impact_coefficient}")
    lines = tuple(line(p, participation, impact_coefficient) for p in positions)
    return Liquidity(
        lines=lines,
        spread_cost=_total([x.spread_cost for x in lines], lambda a, b: a + b),
        impact_cost=_total([x.impact_cost for x in lines], lambda a, b: a + b),
        max_days_to_liquidate=_total([x.days_to_liquidate for x in lines], max),
    )


def lvar(var: float, liquidity: Liquidity) -> float | None:
    """Bangia with the spread-volatility term at zero; None when the spread cost is unknown."""
    return None if liquidity.spread_cost is None else var + liquidity.spread_cost
