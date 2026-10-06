"""JobContext (contract II.2): what a handler may use."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class JobCancelled(Exception):
    """Raised by a handler (via ``ctx.check_cancelled()``) when the job was cancelled or the worker is stopping."""


@dataclass
class JobContext:
    job_id: str
    params: dict[str, Any]
    threads: int
    progress_fn: Callable[[float, str | None], None]
    cancelled_fn: Callable[[], bool]
    _market: Any = field(default=None, repr=False)
    _warehouse: Any = field(default=None, repr=False)

    def progress(self, fraction: float, message: str | None = None) -> None:
        self.progress_fn(fraction, message)

    def cancelled(self) -> bool:
        return bool(self.cancelled_fn())

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled("cancelled")

    @property
    def market(self) -> Any:
        if self._market is None:
            from ..data.market import MarketData

            self._market = MarketData()
        return self._market

    @property
    def warehouse(self) -> Any:
        """A read-only DuckDB connection to {data_dir}/warehouse.duckdb (contract II.5, Lane C)."""
        if self._warehouse is None:
            try:
                import duckdb
            except ImportError as e:
                raise RuntimeError("the warehouse is not available in this build (duckdb is not installed; "
                                   "Lane C adds it)") from e
            from ..config import get_settings

            self._warehouse = duckdb.connect(str(get_settings().data_dir / "warehouse.duckdb"), read_only=True)
        return self._warehouse

    def close(self) -> None:
        if self._warehouse is not None:
            self._warehouse.close()
            self._warehouse = None
