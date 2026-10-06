"""Jobs for the heavy synchronous endpoints, when a request exceeds their synchronous cap (B5).

Each handler validates the same request model the endpoint uses, calls the
endpoint's own private function with the job-sized cap, and stores the JSON
payload the endpoint would have returned as a one-row table ``result``
(column ``payload``) -- served by ``GET /api/jobs/{id}/result``.
"""

from __future__ import annotations

import json
from typing import Any

from ..artifacts import ArtifactSpec
from ..context import JobContext

JOB_NOTE = ("Run as a job: the request exceeded the endpoint's synchronous cap. The payload is exactly what "
            "the endpoint returns.")
ASOF_NOTE = "data_asof is null: the result covers the request's own window, stated in its payload."


def _spec(payload: dict[str, Any]) -> ArtifactSpec:
    import pandas as pd
    from fastapi.encoders import jsonable_encoder

    body = jsonable_encoder(payload)
    text = json.dumps(body, allow_nan=False)
    return ArtifactSpec(tables={"result": pd.DataFrame({"payload": [text]})}, data_asof=None,
                        provenance=list(body.get("provenance") or []),
                        notes=[*(body.get("notes") or []), JOB_NOTE, ASOF_NOTE])


def risk_backtest(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ...api.routers.risk import JOB_MAX_REFITS, BacktestIn, _backtest

    ctx.progress(0.05, "loading")
    return _spec(_backtest(BacktestIn(**params), ctx.market, max_refits=JOB_MAX_REFITS, over_cap="coarsen"))


def backtest_sweep(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ...api.routers.backtest import JOB_MAX_GRID, JOB_SWEEP_BYTES, SweepIn, _do_sweep

    ctx.progress(0.05, "loading")
    return _spec(_do_sweep(SweepIn(**params), ctx.market, max_combos=JOB_MAX_GRID, max_bytes=JOB_SWEEP_BYTES))


def backtest_walkforward(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ...api.routers.backtest import (
        JOB_MAX_GRID,
        JOB_SWEEP_BYTES,
        WalkForwardIn,
        _do_walkforward,
    )

    ctx.progress(0.05, "loading")
    return _spec(_do_walkforward(WalkForwardIn(**params), ctx.market, max_combos=JOB_MAX_GRID,
                                 max_bytes=JOB_SWEEP_BYTES))


def portfolio_compare(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ...api.routers.portfolio import CompareIn, _compare

    ctx.progress(0.05, "loading")
    return _spec(_compare(CompareIn(**params), ctx.market))
