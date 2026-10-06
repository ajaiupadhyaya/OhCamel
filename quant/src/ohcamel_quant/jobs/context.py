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
        """A read-only DuckDB connection to ``Settings.warehouse_path`` (contract II.5, Lane C).

        Raises ``WarehouseUnavailable`` (a ``DataUnavailable``) when no warehouse is
        configured, the file does not exist yet, or an ingest job holds the write
        lock past the reader's timeout. Ingest handlers never use it: they write
        through ``warehouse.db.open_rw``. Held until :meth:`close`, so a handler
        that reads it blocks ingest for its duration."""
        if self._warehouse is None:
            from ..warehouse.db import connect_ro

            self._warehouse = connect_ro(settings=self.market.settings)
        return self._warehouse

    def close(self) -> None:
        if self._warehouse is not None:
            self._warehouse.close()
            self._warehouse = None
