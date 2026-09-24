"""Shared data-layer types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


class DataUnavailable(RuntimeError):
    """No configured source could supply the requested data.

    The API maps this to HTTP 503 with the message, so a user sees *why*
    (e.g. "yahoo: 429, stooq: no rows for XYZ") rather than a blank chart.
    Never catch this to substitute made-up numbers.
    """


@dataclass(frozen=True)
class Provenance:
    source: str                      # e.g. "yahoo", "fred", "cboe", "sec-edgar", "fixture:alpaca"
    fetched_at: str                  # ISO-8601 UTC
    synthetic: bool = False          # always False in this package; kept explicit
    detail: dict[str, Any] = field(default_factory=dict)  # url, symbols, window, ...

    @staticmethod
    def now(source: str, **detail: Any) -> Provenance:
        return Provenance(source=source, fetched_at=datetime.now(UTC).isoformat(), detail=detail)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
