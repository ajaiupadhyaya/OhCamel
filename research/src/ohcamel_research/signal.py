"""Emit a signal from an fdq strategy, in the contract's shape.

Point-in-time by construction: the strategy is handed only bars dated on or
before ``as_of``, and ``data_hash`` is computed over exactly that frame, so
the hash is a statement about what the strategy could have seen.

fdq's long/flat strategies are state machines (``should_rebalance`` flips a
pending state on a crossover), so a single call at ``as_of`` would not
reproduce what a daily run would have held. The machine is therefore walked
forward over every bar date up to ``as_of``, which is what a daily process
would have done, and the weights read at the end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from fdq.strategies.trend import Donchian, MACrossover

from ohcamel_research.battery.data import wide
from ohcamel_research.contract import GATES_VERSION, check, data_hash, params_hash

__all__ = ["REGISTRY", "UNVALIDATED", "emit", "wide"]

REGISTRY: dict[str, type] = {"ma_crossover": MACrossover, "donchian": Donchian}

UNVALIDATED: dict[str, Any] = {
    "status": "unvalidated",
    "gates_version": GATES_VERSION,
    "dsr": None,
    "psr": None,
    "pbo": None,
    "manifest": None,
}


def emit(
    strategy: str,
    params: dict[str, Any],
    as_of: date,
    bars_long: pd.DataFrame,
    sequence: int,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if strategy not in REGISTRY:
        raise KeyError(f"unknown strategy {strategy!r}; known: {sorted(REGISTRY)}")
    hist = bars_long.loc[bars_long["date"] <= as_of].reset_index(drop=True)
    if hist.empty:
        raise ValueError(f"no bars on or before {as_of}")
    strat = REGISTRY[strategy](dict(params))
    w = wide(hist)
    for d in sorted(hist["date"].unique()):
        strat.should_rebalance(d, w)
    weights = strat.target_weights(as_of, w)
    targets = [
        {"symbol": str(sym), "weight": float(x)} for sym, x in weights.items() if float(x) != 0.0
    ]
    doc = {
        "schema_version": 1,
        "strategy": strategy,
        "params_hash": params_hash(params),
        "as_of": as_of.isoformat(),
        "computed_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "data_hash": data_hash(hist),
        "sequence": int(sequence),
        "validation": dict(validation) if validation is not None else dict(UNVALIDATED),
        "targets": targets,
    }
    problems = check(doc)
    if problems:
        raise ValueError("refusing to emit an invalid signal: " + "; ".join(problems))
    return doc
