"""ingest.fred: one FRED series through the MarketData facade, stored as a dated artifact.

Params: ``series`` (FRED id, e.g. DGS10) and optional ``start`` (ISO date).
The table ``series`` has columns ``date`` and ``value`` exactly as FRED
publishes them (yields in percent); FRED's holidays stay missing -- nothing
is filled. ``data_asof`` is the last date with a value. The warehouse copy is
Lane C's (contract II.5); this artifact is the job system's own record.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ...data.base import DataUnavailable
from ..artifacts import ArtifactSpec
from ..context import JobContext

SERIES = re.compile(r"^[A-Z0-9_]{1,32}$")
ALLOWED = {"series", "start"}


def run(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    unknown = set(params) - ALLOWED
    if unknown:
        raise ValueError(f"ingest.fred: unknown params {sorted(unknown)}")
    series = str(params.get("series", "")).strip().upper()
    if not SERIES.match(series):
        raise ValueError("ingest.fred: series must be a FRED id like DGS10")
    try:
        start = date.fromisoformat(params["start"]) if params.get("start") else None
    except ValueError as e:
        raise ValueError(f"ingest.fred: start must be an ISO date: {e}") from e
    ctx.progress(0.1, f"fetching {series}")
    ds = ctx.market.fred([series], start=start)
    s = ds.data[series].astype(float)
    valid = s.dropna()
    if valid.empty:
        raise DataUnavailable(f"FRED returned no observations for {series}")
    import pandas as pd

    table = pd.DataFrame({"date": s.index.date, "value": s.to_numpy()})
    prov = ds.provenance_dicts()
    notes = [f"{series} as published by FRED; {int(s.isna().sum())} dates without a value are left missing."]
    if any(str(p.get("source", "")).startswith("fixture:") for p in prov):
        notes.append("Served from the committed fixture (offline), not a live FRED fetch.")
    ctx.progress(0.9, "writing")
    return ArtifactSpec(tables={"series": table}, data_asof=valid.index[-1].date().isoformat(),
                        provenance=prov, notes=notes)
