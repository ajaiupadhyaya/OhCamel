"""/api/ledger -- the Ledger, built from the system it describes (``machine.ledger``).

``GET /api/ledger`` answers ``{as_of, stale_after_days, rows, provenance, notes}``: the prose rows of
``machine/ledger.yaml`` with the date each was last checked, and the rows computed on this request from the
backup's record, the latest desk report, the two hosts' builds and the packaged pre-registrations. Each row is
``{section, item, detail, state, source, checked, stale, kind, methodology}``; ``state`` is ``static`` for prose
and ``ok``, ``attention`` or ``unknown`` for a computed row.

Public, like every ``/api`` GET: it takes no input, runs no job, writes nothing and creates no store. The only
outbound read is one GET of the live engine's ``/api/ops`` through the bridge's URL, cached 30 s. When the
prose file itself cannot be read the answer is a 503 with a fixed sentence, never the exception.
"""

from __future__ import annotations

import logging
from typing import Annotated

import yaml
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...config import Settings, get_settings
from ...jobs.db import utcnow
from ...machine import ledger

router = APIRouter(prefix="/ledger", tags=["ledger"])
log = logging.getLogger("ohcamel_quant.api.ledger")


def get_ledger_settings() -> Settings:
    return get_settings()


Config = Annotated[Settings, Depends(get_ledger_settings)]


@router.get("")
def read_ledger(settings: Config) -> JSONResponse:
    try:
        built = ledger.build(settings, now=utcnow(), static=ledger.STATIC)
    except (OSError, ValueError, yaml.YAMLError):   # the prose file is part of the image; a broken one is a bad build
        log.exception("the ledger could not be built")
        return JSONResponse(status_code=503, content={"error": "ledger_unavailable",
                                                      "detail": "the ledger could not be built just now"})
    return JSONResponse(built.to_dict())
