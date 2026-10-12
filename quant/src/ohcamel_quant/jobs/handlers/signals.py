"""The signal producers as Lane B jobs (design §4.2; INC-01).

``signals.daily`` is scheduled (schedules.yaml, weekdays 18:45 New York);
``signals.overlay`` at 09:40, every 15 minutes 10:00-15:45, and 18:50.
``signals.freeze`` is an artifact kind ``signals.daily`` publishes itself, on
its first run in a month (signals/freeze.py); it is registered so retention and
``previous_artifact`` know it, and is never scheduled or run as a job.
"""

from __future__ import annotations

from typing import Any

from ..artifacts import ArtifactSpec
from ..context import JobContext


def daily(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ...signals.daily import run

    return run(params, ctx)


def overlay(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    from ...signals.overlay import run

    return run(params, ctx)


def freeze(params: dict[str, Any], ctx: JobContext) -> ArtifactSpec:
    raise RuntimeError("signals.freeze is published by signals.daily on its first run in a month; "
                       "it is never run as a job")
