"""Lane C's ingest handlers as Lane B jobs (compute plan II.2, II.5).

Each ``warehouse.ingest.HANDLERS`` entry returns the II.3 manifest's handler
part as a plain ``dict`` (``data_asof``, ``provenance``, ``notes``,
``survivorship``, ``tables``); the worker's child wants an
:class:`ArtifactSpec`. One adapter per kind, named after the part of the kind
after ``ingest.``, so jobs/kinds.py can name it as a dotted string and the
worker's parent never imports DuckDB or pandas.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..artifacts import ArtifactSpec
from ..context import JobContext

KINDS = ("ingest.universes", "ingest.bars_daily", "ingest.bars_minute", "ingest.fred_warehouse",
         "ingest.factors", "ingest.sec_facts", "ingest.holdings_13f", "ingest.option_snapshots")


def as_spec(result: dict[str, Any]) -> ArtifactSpec:
    return ArtifactSpec(tables=dict(result.get("tables") or {}), data_asof=result.get("data_asof"),
                        provenance=list(result.get("provenance") or []), notes=list(result.get("notes") or []),
                        survivorship=result.get("survivorship"))


def _adapter(kind: str) -> Callable[[dict[str, Any], JobContext], ArtifactSpec]:
    def run(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
        from ...warehouse.ingest import HANDLERS

        return as_spec(HANDLERS[kind](params, ctx))

    run.__name__ = kind.split(".", 1)[1]
    run.__doc__ = f"{kind}: Lane C's handler, its result as an ArtifactSpec."
    return run


universes = _adapter("ingest.universes")
bars_daily = _adapter("ingest.bars_daily")
bars_minute = _adapter("ingest.bars_minute")
fred_warehouse = _adapter("ingest.fred_warehouse")
factors = _adapter("ingest.factors")
sec_facts = _adapter("ingest.sec_facts")
holdings_13f = _adapter("ingest.holdings_13f")
option_snapshots = _adapter("ingest.option_snapshots")
