"""The one cost file (completion task 2.3; spec, "Costs."): ``research/config/costs.yaml``.

The backtest engine, the strategy farm and the desk's daily signals read their costs here and nowhere else:

* a half-spread per ticker, by tier (1 bp, 3 bp, 8 bp), paid one way on traded notional -- a ticker the file holds no
  tier for raises :class:`NoCostTier` rather than being given a guess;
* ``borrow_bps`` a year on short notional;
* ``financing_spread_bps`` a year on gross exposure above 1, over the risk-free rate the engine's cash term charges on
  borrowed cash (cash = 1 - sum w earns or pays the caller's risk-free series);
* ``sweep``: the half-spread multiples (0, 1, 3, 6) the farm re-prices the walk-forward out-of-sample series at;
* capacity: the capital at which the largest rebalance trade is ``participation`` (1%) of the name's ``adv_window``
  (20) session median dollar volume (:func:`capacity`).

The file says, row by row, whether a tier was measured (``tools/measure_spreads.py``: the median IEX quoted
half-spread over the warehouse's minute-bar sessions, rounded up to the tier) or assigned by the stated conservative
rule (``provisional``).
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..config import REPO_ROOT

COSTS_PATH = REPO_ROOT / "research" / "config" / "costs.yaml"
#: The path as pages and provenance name it (repo-relative).
COSTS_REL = "research/config/costs.yaml"
SOURCES = ("measured", "provisional")


class NoCostTier(ValueError):
    """The cost file holds no tier for a ticker: its trades cannot be costed, so nothing runs on it."""


@dataclass(frozen=True)
class CostModel:
    tiers_bps: Mapping[int, float]
    tickers: Mapping[str, int]                       # ticker -> tier
    sources: Mapping[str, str] = field(default_factory=dict)
    borrow_bps: float = 50.0
    financing_spread_bps: float = 50.0
    sweep: tuple[float, ...] = (0.0, 1.0, 3.0, 6.0)
    participation: float = 0.01
    adv_window: int = 20
    version: str = ""
    mult: float = 1.0                                # the sweep's multiple of every half-spread

    def tier(self, ticker: str) -> int:
        t = str(ticker).upper().strip()
        if t not in self.tickers:
            raise NoCostTier(f"{t} has no cost tier in {COSTS_REL}: its trades cannot be costed")
        return int(self.tickers[t])

    def source(self, ticker: str) -> str:
        self.tier(ticker)
        return self.sources.get(str(ticker).upper().strip(), "provisional")

    def half_spread_bps(self, ticker: str) -> float:
        """One-way half-spread in bp on traded notional, times this model's sweep multiple."""
        return float(self.tiers_bps[self.tier(ticker)]) * self.mult

    def half_spreads(self, tickers: Iterable[str]) -> np.ndarray:
        return np.array([self.half_spread_bps(t) for t in tickers], dtype=float)

    def scaled(self, mult: float) -> CostModel:
        """The same model with every half-spread multiplied by ``mult`` (borrow and financing unchanged)."""
        m = float(mult)
        if not (math.isfinite(m) and m >= 0):
            raise ValueError(f"a half-spread multiple must be finite and non-negative; got {mult!r}")
        return dataclasses.replace(self, mult=self.mult * m)


def _parse(doc: Mapping, where: str) -> CostModel:
    try:
        tiers = {int(k): float(v) for k, v in doc["tiers_bps"].items()}
        rows = doc["tickers"]
        tickers = {str(t).upper(): int(r["tier"]) for t, r in rows.items()}
        sources = {str(t).upper(): str(r.get("source", "provisional")) for t, r in rows.items()}
        cap = doc["capacity"]
        model = CostModel(tiers_bps=tiers, tickers=tickers, sources=sources, borrow_bps=float(doc["borrow_bps"]),
                          financing_spread_bps=float(doc["financing_spread_bps"]),
                          sweep=tuple(float(x) for x in doc["sweep"]), participation=float(cap["participation"]),
                          adv_window=int(cap["adv_window"]), version=str(doc["version"]))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ValueError(f"{where}: not a cost file ({type(e).__name__}: {e})") from e
    bad = [t for t, k in tickers.items() if k not in tiers]
    if bad:
        raise ValueError(f"{where}: tier of {bad[0]} is not one of {sorted(tiers)}")
    odd = [t for t, s in sources.items() if s not in SOURCES]
    if odd:
        raise ValueError(f"{where}: source of {odd[0]} is {sources[odd[0]]!r}, not one of {SOURCES}")
    nums = [*tiers.values(), model.borrow_bps, model.financing_spread_bps, *model.sweep]
    if not all(math.isfinite(x) and x >= 0 for x in nums):
        raise ValueError(f"{where}: costs and sweep multiples must be finite and non-negative")
    if not (0 < model.participation <= 1 and model.adv_window >= 1):
        raise ValueError(f"{where}: capacity needs 0 < participation <= 1 and adv_window >= 1")
    return model


@lru_cache(maxsize=4)
def _load(path: Path) -> CostModel:
    return _parse(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))


def load(path: Path | None = None) -> CostModel:
    """The cost model in ``path`` (default: the one cost file)."""
    return _load(Path(path or COSTS_PATH).resolve())


def tier_for(half_spread_bps: float, model: CostModel | None = None) -> int:
    """The tier a measured median half-spread rounds UP to: the first tier whose half-spread covers it. Above the
    widest tier there is no tier (:class:`NoCostTier`): such a name needs the owner's decision, not a guess."""
    m = model or load()
    x = float(half_spread_bps)
    if not (math.isfinite(x) and x >= 0):
        raise ValueError(f"a half-spread must be finite and non-negative; got {half_spread_bps!r}")
    for tier, bps in sorted(m.tiers_bps.items(), key=lambda kv: kv[1]):
        if x <= bps:
            return int(tier)
    raise NoCostTier(f"a {x:.2f} bp half-spread is wider than every tier in {COSTS_REL}")


# ------------------------------------------------------------------ capacity
def median_dollar_volume(dollar_volume: pd.DataFrame, window: int) -> pd.DataFrame:
    """The ``window``-session median of each name's daily dollar volume through the session BEFORE each row: what is
    known at a decision taken the previous close, never the trade day's own volume. NaN until ``window`` sessions."""
    return dollar_volume.sort_index().rolling(window, min_periods=window).median().shift(1)


def capacity_of_trades(trades: pd.DataFrame, dollar_volume: pd.DataFrame, participation: float | None = None,
                       window: int | None = None) -> float:
    """The capital at which the largest trade -- relative to its own name's median dollar volume -- is
    ``participation`` of that volume: ``min over trades of participation x MDV / |trade|``, trades as fractions of
    capital. NaN when nothing traded, or when any trade has no median dollar volume (a capacity is never stated from
    the trades that happen to have one: a missing one could bind)."""
    m = load()
    p = m.participation if participation is None else float(participation)
    win = m.adv_window if window is None else int(window)
    t = trades.abs()
    mdv = median_dollar_volume(dollar_volume, win).reindex(index=t.index, columns=t.columns)
    traded = (t > 1e-12).to_numpy()
    if not traded.any():
        return float("nan")
    vol = mdv.to_numpy(dtype=float)[traded]
    if not np.all(np.isfinite(vol) & (vol > 0)):
        return float("nan")
    return float(np.min(p * vol / t.to_numpy(dtype=float)[traded]))


def rebalance_trades(weights: pd.DataFrame) -> pd.DataFrame:
    """The trade at each decision row of a target-weight frame (NaN rows: no decision): the change from the previous
    decision's targets, the first from cash. Zero on rows without a decision."""
    w = weights.sort_index()
    decided = w.notna().any(axis=1)
    rows = w.loc[decided].fillna(0.0)
    out = pd.DataFrame(0.0, index=w.index, columns=w.columns)
    out.loc[decided] = rows.diff().fillna(rows).to_numpy()
    return out


def capacity(weights: pd.DataFrame, dollar_volume: pd.DataFrame, participation: float | None = None) -> float:
    """Capacity of a strategy's target weights (rows = sessions, NaN rows = no decision) against daily dollar volume
    (``close x volume``): :func:`capacity_of_trades` of :func:`rebalance_trades`. ``participation`` defaults to the
    cost file's (1%)."""
    return capacity_of_trades(rebalance_trades(weights), dollar_volume, participation)


__all__ = ["COSTS_PATH", "COSTS_REL", "CostModel", "NoCostTier", "capacity", "capacity_of_trades", "load",
           "median_dollar_volume", "rebalance_trades", "tier_for"]
