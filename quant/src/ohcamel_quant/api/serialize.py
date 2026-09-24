"""JSON helpers shared by every router.

Routers return plain dicts built with these helpers so the wire format is
uniform: dates as ISO strings, NaN/inf as null, numpy scalars as Python
numbers, and every payload carrying ``provenance``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def clean(x: Any) -> Any:
    """Recursively convert to JSON-safe Python (NaN/inf -> None)."""
    if x is None or isinstance(x, (str, bool)):
        return x
    if isinstance(x, (float, np.floating)):
        f = float(x)
        return f if math.isfinite(f) else None
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (pd.Timestamp, np.datetime64)):
        ts = pd.Timestamp(x)
        return None if pd.isna(ts) else (ts.date().isoformat() if ts == ts.normalize() else ts.isoformat())
    if hasattr(x, "isoformat"):
        return x.isoformat()
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return [clean(v) for v in x.tolist()]
    if isinstance(x, pd.Series):
        return series(x)
    if isinstance(x, pd.DataFrame):
        return frame(x)
    if hasattr(x, "__dataclass_fields__"):
        from dataclasses import asdict
        return clean(asdict(x))
    return x


def series(s: pd.Series) -> dict[str, list[Any]]:
    """{index: [...], values: [...]} -- compact for charts."""
    return {"index": clean(list(s.index)), "values": clean(s.to_numpy())}


def frame(df: pd.DataFrame) -> dict[str, Any]:
    """{index: [...], columns: [...], data: {col: [...]}} -- column-major for charts."""
    return {
        "index": clean(list(df.index)),
        "columns": [str(c) for c in df.columns],
        "data": {str(c): clean(df[c].to_numpy()) for c in df.columns},
    }


def records(df: pd.DataFrame, index_name: str | None = None) -> list[dict[str, Any]]:
    """Row-major list of dicts -- for tables."""
    out = df.reset_index() if index_name is not None or df.index.name else df
    if index_name and df.index.name is None:
        out = out.rename(columns={"index": index_name})
    return [clean(r) for r in out.to_dict(orient="records")]
