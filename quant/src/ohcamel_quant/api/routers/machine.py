"""/api/machine -- The Machine, OhCamel's lineage graph (the public front page's one request).

``GET /api/machine`` answers ``{asof, nodes, edges, runs, x, provenance, notes}``. The graph (``nodes``,
``edges``, ``x``, ``provenance``, ``notes``) is built by ``machine.service``: one cache serves the whole process
with the last good build and refreshes it in the background, about every five minutes or when a newer product
has finished. The app warms it at startup. ``runs`` (the clock's jobs queued, running or started in the last
day) is read fresh on every request: it is one small query.

The endpoint is public, like every ``/api`` GET: it takes no input, runs no job, opens the warehouse
read-only and creates no store. When there is nothing to serve and the build fails, it answers 503 with a fixed
sentence, never the exception, which could carry a path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...config import get_settings
from ...jobs.db import utcnow
from ...jobs.paths import jobs_db_path
from ...machine.load import load_runs
from ...machine.service import MachineCache, build_machine, degraded, watermark

router = APIRouter(prefix="/machine", tags=["machine"])
log = logging.getLogger("ohcamel_quant.api.machine")


def get_machine_db_path() -> Path:
    return jobs_db_path()


def new_machine_cache() -> MachineCache:
    """A cache over this host's own stores: the jobs database at ``jobs_db_path()`` and the settings' warehouse."""
    return MachineCache(lambda now: build_machine(get_machine_db_path(), get_settings(), now),
                        lambda: watermark(get_machine_db_path()), degraded=degraded)


_cache = new_machine_cache()   # one per process


def get_machine_cache() -> MachineCache:
    return _cache


def warm_machine() -> None:
    """Start the first build in the background, so that no visitor pays for it after a restart. It never blocks
    or fails the app's start, and offline (tests, a laptop) it does nothing: nothing is read until a request asks."""
    if get_settings().offline:
        return
    try:
        get_machine_cache().refresh(utcnow())
    except Exception:   # noqa: BLE001 -- a front page that cannot warm is no reason not to start
        log.exception("the machine could not be warmed")


Db = Annotated[Path, Depends(get_machine_db_path)]
Cache = Annotated[MachineCache, Depends(get_machine_cache)]


@router.get("")
def machine(db: Db, cache: Cache) -> JSONResponse:
    now = utcnow()
    try:
        graph = cache.get(now)
    except Exception:   # noqa: BLE001 -- whatever broke, the public sees one fixed sentence; the log has the rest
        log.exception("the machine could not be built")
        return JSONResponse(status_code=503, content={"error": "machine_unavailable",
                                                      "detail": "the machine could not be built just now"})
    kinds = {n["m"]["kind"] for n in graph["nodes"] if n["k"] == "job"}   # the clock's jobs, as the graph drew them
    return JSONResponse({**graph, "runs": load_runs(db, now, kinds)})
