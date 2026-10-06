"""``GET /api/warehouse/freshness``: ``data_asof`` and staleness per warehouse
dataset (compute plan C6). Opens the warehouse read-only for one query and
closes it; 503 ``warehouse_unavailable`` (never a stand-in) when it is not
configured, does not exist yet, or the writer holds it past 2 s."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...config import get_settings
from ...data.base import Provenance
from ...warehouse import freshness
from ...warehouse.db import WarehouseUnavailable, open_ro

router = APIRouter(prefix="/warehouse", tags=["warehouse"])


@router.get("/freshness", response_model=None)
def warehouse_freshness() -> dict | JSONResponse:
    s = get_settings()
    try:
        with open_ro(settings=s) as con:
            body = freshness.dataset_freshness(con, freshness._now())
    except WarehouseUnavailable as e:
        return JSONResponse(status_code=503, content={"error": "warehouse_unavailable", "detail": str(e),
                                                      "configured": s.warehouse_path is not None})
    body["provenance"] = [Provenance.now("warehouse", path=str(s.warehouse_path), table="ingest_log").to_dict()]
    body["notes"] = ["data_asof is the newest date any key reached; keys_behind lag it",
                     "session datasets are stale after one missed nightly run (exchange holidays allowed one "
                     "session of slack); calendar datasets after max_age without a successful run"]
    return body
