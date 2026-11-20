"""The job kinds the worker can run, and what each declares.

A kind names its handler as a dotted ``module:function`` string, so the
worker's parent process can read a kind's memory class and retry count
without importing the handler (and pandas with it). Only the child imports
handlers. ``public`` kinds may be submitted through ``POST /api/jobs``
(B5); ``validate`` names a pydantic model the API checks params against.
"""

from __future__ import annotations

import importlib
import math
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
    #: Task 3.3: the kind draws random numbers, so its manifest must record at least one seed (``ctx.seed``);
    #: a manifest without one is refused (jobs/provenance.py).
    draws_random: bool = False
    #: Completion task 3.6: an L kind's compute budget in seconds (its wall limit is this plus WALL_MARGIN_S).
    budget_s: int | None = None
    #: Completion task 3.7 (spec R6): the kind may run on a burst machine against the nightly warehouse export and
    #: have its artifact uploaded through the burst door (jobs/burst_door.py). Only the four heavy research kinds.
    burstable: bool = False

    @property
    def retries(self) -> int:
        return retries_for(self.name)

    @property
    def wall_limit_s(self) -> int:
        """The wall-clock seconds a run may take before the worker kills it (spec, Operations): S 10 min, M 45 min,
        L its budget plus 10 min."""
        if self.mem_class == "L":
            assert self.budget_s is not None
            return int(self.budget_s) + WALL_MARGIN_S
        return WALL_LIMIT_S[self.mem_class]


REGISTRY: dict[str, KindSpec] = {}
#: Completion task 3.6 (spec, Operations): every job has a wall-clock limit. A run past it is killed with its whole
#: process group and failed with a reason naming the limit (jobs/runner.py); the worker takes the next job. The
#: limit is per kind, not per memory class's admission budget: S 10 min, M 45 min, L its compute budget plus 10.
WALL_LIMIT_S = {"S": 600, "M": 2700}
WALL_MARGIN_S = 600


def retries_for(kind: str) -> int:
    """Compute plan B1: 2 retries for ingest kinds, 0 for everything else."""
    return 2 if kind.startswith("ingest.") else 0


def wall_limit_for(spec: KindSpec, params: dict[str, Any]) -> float:
    """The wall limit of one run: the kind's, except that an L kind run with its own ``budget_minutes`` (the farm's
    schedule passes one) is held to that budget plus the margin."""
    if spec.mem_class == "L" and params.get("budget_minutes") is not None:
        try:
            minutes = float(params["budget_minutes"])
        except (TypeError, ValueError):
            minutes = math.nan
        if math.isfinite(minutes) and minutes > 0:
            return minutes * 60.0 + WALL_MARGIN_S
        # fix round 1, M10: a malformed budget is the kind's own limit, never no limit at all
    return float(spec.wall_limit_s)


def register(spec: KindSpec) -> KindSpec:
    if spec.mem_class not in ("S", "M", "L"):
        raise ValueError(f"{spec.name}: mem_class must be S, M or L")
    if spec.mem_class == "L" and not spec.budget_s:
        raise ValueError(f"{spec.name}: an L kind declares its compute budget (budget_s), its wall limit's base")
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
                  validate="ohcamel_quant.api.routers.backtest:SweepIn", draws_random=True))
register(KindSpec("api.backtest_walkforward", f"{_H}:backtest_walkforward", "M", heavy=False, public=True,
                  validate="ohcamel_quant.api.routers.backtest:WalkForwardIn", draws_random=True))
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
                            ("ingest.holdings_13f", "S", False), ("ingest.option_snapshots", "S", False),
                            ("ingest.corp_actions", "S", False)):
    register(KindSpec(_kind, f"{_W}:{_kind.split('.', 1)[1]}", _mem, heavy=_heavy))

# Lane M's products (compute plan M2-M8; ship plan Lane M). Private: scheduled only.
_P = "ohcamel_quant.products"
# burstable (completion task 3.7, spec R6): the four heavy research kinds may run on a burst machine against the
# nightly export (tools/burst.py) and publish through the burst door. The desk's readers (signals/: the overlay
# reads cov.league and risk.mc_atlas, the monthly freeze reads farm.sweep) look artifacts up worker_only, so a
# burst artifact (manifest host: burst) never reaches the book.
# draws_random (Task 3.3): Monte Carlo paths, bootstraps, MCS/SPA resampling, LightGBM, HMM restarts, Kronos
# sampling -- each records the seed it already used (none was changed).
register(KindSpec("risk.mc_atlas", f"{_P}.atlas:run", "M", heavy=True, draws_random=True, burstable=True))
register(KindSpec("risk.mc_intraday", f"{_P}.atlas:run_intraday", "S", heavy=False, draws_random=True))
register(KindSpec("vol.forecast_league", f"{_P}.vol_league:run", "M", heavy=True, draws_random=True))
register(KindSpec("cov.league", f"{_P}.cov_league:run", "M", heavy=True, burstable=True))
# farm.sweep's budget is products/farm.py's BUDGET_MINUTES (90; this module must not import pandas, so the number
# is restated and tests/test_jobs_worker.py holds the two equal).
register(KindSpec("farm.sweep", f"{_P}.farm:run", "L", heavy=True, draws_random=True, budget_s=5400,
                  burstable=True))
register(KindSpec("vol.surface_history", f"{_P}.surface_history:run", "M", heavy=True))
register(KindSpec("models.xs_lgbm", f"{_P}.q01:run", "M", heavy=True, draws_random=True, burstable=True))
register(KindSpec("regime.hmm", f"{_P}.q02:run", "M", heavy=True, draws_random=True))
# 2026-10-19 (docs/superpowers/plans/2026-10-19-kronos-qlib-rdagent.md): Kronos in prospective shadow (EXP-K01's
# design; torch runs in research/kronos's own venv, a subprocess), the owner's factor ledger re-screened on the
# warehouse universe, and the nightly data-integrity sweep. Private: scheduled only.
# kronos.shadow declares no budget of its own: its Kronos subprocess is bounded at 30 min (run_kronos's timeout_s)
# and the bars and incumbent fits around it are minutes. Its budget is the M limit, 45 min, so an L kind is never
# held tighter than an M one (wall limit 55 min).
register(KindSpec("kronos.shadow", f"{_P}.kronos_shadow:run", "L", heavy=True, draws_random=True, budget_s=2700))
register(KindSpec("factors.screen", f"{_P}.factor_screen:run", "M", heavy=True))
register(KindSpec("ops.data_integrity", f"{_P}.data_integrity:run", "S", heavy=False))

# INC-01's producers (design §4.2). Private: scheduled only. signals.freeze is
# published by signals.daily itself (signals/freeze.py), once a month, and is
# registered for retention and reads; it has no schedule and never runs as a job.
_S = "ohcamel_quant.jobs.handlers.signals"
register(KindSpec("signals.daily", f"{_S}:daily", "S", heavy=False))
register(KindSpec("signals.freeze", f"{_S}:freeze", "S", heavy=False))
register(KindSpec("signals.overlay", f"{_S}:overlay", "S", heavy=False))
# The session's report (design §4.2): read from the live engine at 16:30, light,
# so it may run inside the session window.
register(KindSpec("desk.report", f"{_S}:report", "S", heavy=False))
