"""The job kinds the worker can run, and what each declares.

A kind names its handler as a dotted ``module:function`` string, so the
worker's parent process can read a kind's memory class and retry count
without importing the handler (and pandas with it). Only the child imports
handlers. ``public`` kinds may be submitted through ``POST /api/jobs``
(B5); ``validate`` names a pydantic model the API checks params against.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class UnknownKind(ValueError):
    pass


@dataclass(frozen=True)
class KindSpec:
    name: str
    handler: str              # "package.module:function"
    mem_class: str            # 'S' | 'M' | 'L' (contract II.2)
    heavy: bool
    public: bool = False
    validate: str | None = None   # "package.module:PydanticModel"

    @property
    def retries(self) -> int:
        return retries_for(self.name)


REGISTRY: dict[str, KindSpec] = {}


def retries_for(kind: str) -> int:
    """Compute plan B1: 2 retries for ingest kinds, 0 for everything else."""
    return 2 if kind.startswith("ingest.") else 0


def register(spec: KindSpec) -> KindSpec:
    if spec.mem_class not in ("S", "M", "L"):
        raise ValueError(f"{spec.name}: mem_class must be S, M or L")
    if spec.name in REGISTRY and REGISTRY[spec.name] != spec:
        raise ValueError(f"kind {spec.name!r} is already registered differently")
    REGISTRY[spec.name] = spec
    return spec


def get_kind(name: str) -> KindSpec:
    try:
        return REGISTRY[name]
    except KeyError:
        raise UnknownKind(f"unknown job kind {name!r}") from None


def _resolve(dotted: str) -> Any:
    module, _, attr = dotted.partition(":")
    return getattr(importlib.import_module(module), attr)


def load_handler(spec: KindSpec) -> Callable[..., Any]:
    return _resolve(spec.handler)


def validate_params(spec: KindSpec, params: dict[str, Any]) -> None:
    """Raise ValueError when ``params`` do not satisfy the kind's model (API submissions)."""
    if spec.validate is None:
        return
    model = _resolve(spec.validate)
    try:
        model(**params)
    except Exception as e:  # pydantic.ValidationError is a ValueError subclass; keep the message
        raise ValueError(f"invalid params for {spec.name}: {e}") from e


# Public (ship integration): the end-to-end smoke posts it. Only ``tag`` may be
# submitted; the test-only params (sleep_s, fail_if_exists, exit_hard) stay
# reachable from the queue alone.
register(KindSpec("ops.selftest", "ohcamel_quant.jobs.handlers.selftest:run", "S", heavy=False, public=True,
                  validate="ohcamel_quant.jobs.handlers.selftest:SelftestIn"))
register(KindSpec("ingest.fred", "ohcamel_quant.jobs.handlers.fred:run", "S", heavy=False))
_H = "ohcamel_quant.jobs.handlers.api_heavy"
register(KindSpec("api.risk_backtest", f"{_H}:risk_backtest", "M", heavy=False, public=True,
                  validate="ohcamel_quant.api.routers.risk:BacktestIn"))
register(KindSpec("api.backtest_sweep", f"{_H}:backtest_sweep", "M", heavy=False, public=True,
                  validate="ohcamel_quant.api.routers.backtest:SweepIn"))
register(KindSpec("api.backtest_walkforward", f"{_H}:backtest_walkforward", "M", heavy=False, public=True,
                  validate="ohcamel_quant.api.routers.backtest:WalkForwardIn"))
register(KindSpec("api.portfolio_compare", f"{_H}:portfolio_compare", "M", heavy=False, public=True,
                  validate="ohcamel_quant.api.routers.portfolio:CompareIn"))

# Lane C's warehouse ingest (compute plan C2-C6), the only writers of the
# DuckDB file. Private: they run on schedules.yaml, never from the API. The
# memory classes and heavy flags are warehouse/schedules.py's, pinned equal by
# tests/test_jobs_integration.py (this module must not import DuckDB).
_W = "ohcamel_quant.jobs.handlers.warehouse"
for _kind, _mem, _heavy in (("ingest.universes", "S", False), ("ingest.bars_daily", "S", False),
                            ("ingest.bars_minute", "S", False), ("ingest.fred_warehouse", "S", False),
                            ("ingest.factors", "S", False), ("ingest.sec_facts", "M", True),
                            ("ingest.holdings_13f", "S", False), ("ingest.option_snapshots", "S", False)):
    register(KindSpec(_kind, f"{_W}:{_kind.split('.', 1)[1]}", _mem, heavy=_heavy))

# Lane M's products (compute plan M2-M8; ship plan Lane M). Private: scheduled only.
_P = "ohcamel_quant.products"
register(KindSpec("risk.mc_atlas", f"{_P}.atlas:run", "M", heavy=True))
register(KindSpec("risk.mc_intraday", f"{_P}.atlas:run_intraday", "S", heavy=False))
register(KindSpec("vol.forecast_league", f"{_P}.vol_league:run", "M", heavy=True))
register(KindSpec("cov.league", f"{_P}.cov_league:run", "M", heavy=True))
register(KindSpec("farm.sweep", f"{_P}.farm:run", "L", heavy=True))
