"""JobContext (contract II.2): what a handler may use.

Task 3.3: every read a handler makes through the context is recorded (jobs/provenance.py). ``ctx.warehouse`` and
``ctx.market`` hand out recording wrappers, ``ctx.read(name, frame)`` records a read the handler made itself,
and ``ctx.seed`` / ``ctx.rng`` record the seed a random draw used. The child writes the record into the result,
and the manifest names it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .provenance import Recorder, RecordingConnection, RecordingMarket


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
    recorder: Recorder = field(default_factory=Recorder, repr=False)
    _market_rec: Any = field(default=None, repr=False)

    def read(self, name: str, frame: Any, *, source: str = "unknown") -> Any:
        """Record ``frame`` (its canonical SHA-256) as a read named ``name`` and return it unchanged. ``source``
        says where it came from: ``warehouse``/``fixture``/``artifact`` reads can be served again from an export
        and this checkout; anything else (a vendor, the live engine) marks the read not regenerable."""
        return self.recorder.record(name, frame, source=source)

    def seed(self, value: Any, name: str = "seed") -> int:
        """The seed a random draw uses, recorded under ``name``; ``None`` is refused (``UnseededDraw``)."""
        return self.recorder.seed(value, name)

    def rng(self, seed: Any, name: str = "seed") -> Any:
        """``numpy.random.default_rng(seed)``, the seed recorded first."""
        import numpy as np

        return np.random.default_rng(self.seed(seed, name))

    def progress(self, fraction: float, message: str | None = None) -> None:
        self.progress_fn(fraction, message)

    def cancelled(self) -> bool:
        return bool(self.cancelled_fn())

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled("cancelled")

    @property
    def market(self) -> Any:
        """The MarketData facade, every data method's result recorded (``RecordingMarket``)."""
        if self._market is None:
            from ..data.market import MarketData

            self._market = MarketData()
        if self._market_rec is None:
            self._market_rec = RecordingMarket(self._market, self.recorder)
        return self._market_rec

    @property
    def signals_dir(self) -> Path | None:
        """``Settings.signals_dir`` (``OHCAMEL_QUANT_SIGNALS_DIR``): where the signal
        producers write the desk's documents; None when unset, and then they refuse to run."""
        d = self.market.settings.signals_dir
        return None if d is None else Path(d)

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
        if not isinstance(self._warehouse, RecordingConnection):
            self._warehouse = RecordingConnection(self._warehouse, self.recorder)
        return self._warehouse

    def close(self) -> None:
        if self._warehouse is not None:
            self._warehouse.close()
            self._warehouse = None
