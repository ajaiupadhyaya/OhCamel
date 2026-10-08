"""Jobs for the heavy synchronous endpoints, when a request exceeds their synchronous cap (B5).

Each handler validates the same request model the endpoint uses, calls the
endpoint's own private function with the job-sized cap, and stores the JSON
payload the endpoint would have returned as a one-row table ``result``
(column ``payload``) -- served by ``GET /api/jobs/{id}/result``.

``portfolio_compare`` needs a risk-free series (FRED DGS3MO, then Ken French RF).
Offline neither exists and none is substituted: the job fails saying so, and runs
when the caller supplies ``risk_free_rate`` (docs/runbooks/compute.md).
"""

from __future__ import annotations

import json
from typing import Any

from ..artifacts import ArtifactSpec
from ..context import JobContext

JOB_NOTE = ("Run as a job: the request exceeded the endpoint's synchronous cap. The payload is exactly what "
            "the endpoint returns.")
ASOF_NOTE = "data_asof is null: the result covers the request's own window, stated in its payload."


def _spec(payload: dict[str, Any], engine: str = "python") -> ArtifactSpec:
    import pandas as pd
    from fastapi.encoders import jsonable_encoder

    body = jsonable_encoder(payload)
    text = json.dumps(body, allow_nan=False)
    return ArtifactSpec(tables={"result": pd.DataFrame({"payload": [text]})}, data_asof=None,
                        provenance=list(body.get("provenance") or []),
                        notes=[*(body.get("notes") or []), JOB_NOTE, ASOF_NOTE], engine=engine)


def risk_backtest(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ... import kernels
    from ...api.routers.risk import JOB_MAX_REFITS, BacktestIn, _backtest

    ctx.progress(0.05, "loading")
    payload = _backtest(BacktestIn(**params), ctx.market, max_refits=JOB_MAX_REFITS, over_cap="coarsen",
                        use_kernels=True)  # GARCH refits on the garch_fit kernel (compute plan A5)
    return _spec(payload, kernels.engine_of("garch_fit"))


def backtest_sweep(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ... import kernels
    from ...api.routers.backtest import JOB_MAX_GRID, JOB_SWEEP_BYTES, SweepIn, _do_sweep

    ctx.progress(0.05, "loading")
    payload = _do_sweep(SweepIn(**params), ctx.market, max_combos=JOB_MAX_GRID, max_bytes=JOB_SWEEP_BYTES,
                        threads=ctx.threads)  # CSCV on the cscv_pbo kernel with admission's threads (A4)
    return _spec(payload, kernels.engine_of("backtest_weights"))


def backtest_walkforward(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ...api.routers.backtest import (
        JOB_MAX_GRID,
        JOB_SWEEP_BYTES,
        WalkForwardIn,
        _do_walkforward,
    )

    ctx.progress(0.05, "loading")
    from ... import kernels

    return _spec(_do_walkforward(WalkForwardIn(**params), ctx.market, max_combos=JOB_MAX_GRID,
                                 max_bytes=JOB_SWEEP_BYTES), kernels.engine_of("backtest_weights"))


def portfolio_compare(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ...api.routers.portfolio import CompareIn, _compare

    ctx.progress(0.05, "loading")
    return _spec(_compare(CompareIn(**params), ctx.market))
