"""ops.selftest: proves the job pipeline end to end and records what it checked.

It runs once a day (schedules.yaml) and its artifact is shown on the Compute
page: which Python, which platform, how many threads admission gave the job,
and which process ran it. No market data is involved.

Test-only parameters, never scheduled:
* ``sleep_s`` (0-60): wait, checking for cancellation every 50 ms.
* ``fail_if_exists`` (path): raise if that file exists (the "failed newest job" test).
* ``exit_hard`` (bool): ``os._exit(137)`` with no result (what an OOM kill looks like).
"""

from __future__ import annotations

import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...data.base import Provenance
from ..artifacts import ArtifactSpec
from ..context import JobContext

ALLOWED = {"tag", "sleep_s", "fail_if_exists", "exit_hard"}


class SelftestIn(BaseModel):
    """What ``POST /api/jobs`` may submit: a tag, nothing else."""

    model_config = ConfigDict(extra="forbid")
    tag: str = Field(default="", max_length=64)


def run(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    unknown = set(params) - ALLOWED
    if unknown:
        raise ValueError(f"ops.selftest: unknown params {sorted(unknown)}")
    tag = str(params.get("tag", ""))
    if len(tag) > 64:
        raise ValueError("ops.selftest: tag is at most 64 characters")
    sleep_s = float(params.get("sleep_s", 0.0))
    if not 0.0 <= sleep_s <= 60.0:
        raise ValueError("ops.selftest: sleep_s must be within [0, 60]")
    marker = params.get("fail_if_exists")
    if marker and Path(str(marker)).exists():
        raise RuntimeError(f"ops.selftest: fail_if_exists file {marker} exists")
    if params.get("exit_hard"):
        os._exit(137)
    t_end = time.monotonic() + sleep_s
    while time.monotonic() < t_end:
        ctx.check_cancelled()
        ctx.progress(1.0 - (t_end - time.monotonic()) / sleep_s, "sleeping")
        time.sleep(0.05)
    import pandas as pd

    now = datetime.now(UTC)
    checks = pd.DataFrame({
        "check": ["python", "platform", "threads", "pid", "tag"],
        "value": [sys.version.split()[0], platform.platform(), str(ctx.threads), str(os.getpid()), tag],
    })
    return ArtifactSpec(
        tables={"checks": checks},
        data_asof=now.date().isoformat(),
        provenance=[Provenance.now("ohcamel-worker", kind="ops.selftest").to_dict()],
        notes=["A self-test of the job pipeline: a child process ran, wrote this table and was measured. "
               "No market data is involved."],
    )
