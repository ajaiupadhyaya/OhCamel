"""/api/jobs -- the job queue over HTTP (compute plan contract II.2, task B5).

* ``POST /api/jobs {kind, params}`` -> 202 ``{id, state, ...}``. Only kinds
  marked public in jobs/kinds.py; params are checked against the kind's
  request model; 20 live (queued or running) jobs per client address; an
  identical live job is returned instead of a duplicate.
* ``GET /api/jobs/{id}``, ``GET /api/jobs?kind=&state=&limit=``,
  ``DELETE /api/jobs/{id}`` (only the client that submitted an API job may
  cancel it; scheduled jobs cannot be cancelled here).
* ``GET /api/jobs/{id}/result`` -- the payload of a finished ``api.*`` job.
* ``GET /api/jobs/schedules`` -- schedules.yaml with each entry's next run
  (compute plan D12's schedule table).
* ``GET /api/jobs/events`` -- SSE, one ``event: job`` per state or progress
  change, ``id:`` = the job_events sequence (``Last-Event-ID`` resumes).

The client address is the rightmost ``X-Forwarded-For`` entry (of the last
such header) -- what Caddy, the only way in, saw and appended -- falling back
to the socket peer. Params are at most ``MAX_PARAMS_BYTES`` of JSON (413).
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ...data.base import Provenance
from ...jobs.artifacts import inside
from ...jobs.cron import next_due
from ...jobs.db import connect, iso, utcnow
from ...jobs.kinds import UnknownKind, get_kind, validate_params
from ...jobs.paths import jobs_db_path
from ...jobs.queue import (
    Job,
    cancel,
    client_of,
    current_seq,
    enqueue,
    events_after,
    find_live,
    get_job,
    list_jobs,
    live_count_for_client,
    params_hash,
    progress_of,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])
MAX_LIVE_PER_CLIENT = 20
MAX_PARAMS_BYTES = 16_384  # Harden H4: every public kind's params fit in well under this
SSE_LIFETIME_S = 300.0
OVER_CAP_NOTE = ("This request exceeds the endpoint's synchronous cap, so it runs as a job on the batch worker; "
                 "poll status_url or listen on /api/jobs/events, then read result_url.")


def get_jobs_db_path() -> Path:
    return jobs_db_path()


def get_sse_lifetime() -> float:
    return SSE_LIFETIME_S


JobsDb = Annotated[Path, Depends(get_jobs_db_path)]


def client_id(request: Request) -> str:
    xff = request.headers.getlist("x-forwarded-for")
    if xff:
        last = xff[-1].split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _meta() -> dict[str, Any]:
    return {"provenance": [Provenance.now("ohcamel-jobs", store="jobs.sqlite").to_dict()], "notes": []}


def _view(conn: Any, job: Job) -> dict[str, Any]:
    p = progress_of(conn, job.id) or {}
    d = job.to_dict()
    d.update(progress=p.get("progress"), message=p.get("message"), heartbeat_at=p.get("heartbeat_at"))
    d["result_url"] = (f"/api/jobs/{job.id}/result"
                       if job.state == "done" and job.kind.startswith("api.") else None)
    return d


def _error(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


def _enqueue_public(db: Path, kind: str, params: dict[str, Any], request: Request) -> tuple[Job, bool] | JSONResponse:
    try:
        spec = get_kind(kind)
    except UnknownKind as e:
        return _error(422, "unknown_kind", str(e))
    if not spec.public:
        return _error(422, "kind_not_allowed", f"{kind} cannot be submitted through the API")
    if len(json.dumps(params, separators=(",", ":"), default=str)) > MAX_PARAMS_BYTES:
        return _error(413, "params_too_large", f"params are at most {MAX_PARAMS_BYTES} bytes of JSON")
    validate_params(spec, params)  # ValueError -> 422 invalid_input (app handler)
    client = client_id(request)
    with closing(connect(db)) as conn:
        live = find_live(conn, kind, params_hash(params))
        if live is not None:
            return live, False
        if live_count_for_client(conn, client) >= MAX_LIVE_PER_CLIENT:
            return _error(429, "too_many_jobs",
                          f"at most {MAX_LIVE_PER_CLIENT} queued or running jobs per client; wait for one to finish")
        return enqueue(conn, kind=kind, params=params, priority=0, mem_class=spec.mem_class, heavy=spec.heavy,
                       submitted_by="api", now=utcnow(), client=client)


class SubmitIn(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("", status_code=202)
def submit(body: SubmitIn, request: Request, db: JobsDb) -> Any:
    got = _enqueue_public(db, body.kind, body.params, request)
    if isinstance(got, JSONResponse):
        return got
    job, created = got
    return JSONResponse(status_code=202, content={
        "id": job.id, "state": job.state, "kind": job.kind, "created": created,
        "status_url": f"/api/jobs/{job.id}", **_meta()})


def submit_over_cap(kind: str, params: dict[str, Any], request: Request, db: Path) -> JSONResponse:
    """Used by the heavy endpoints: 202 {job} instead of 422 when a request is over its synchronous cap."""
    got = _enqueue_public(db, kind, params, request)
    if isinstance(got, JSONResponse):
        return got
    job, _ = got
    with closing(connect(db)) as conn:
        view = _view(conn, job)
    meta = _meta()
    return JSONResponse(status_code=202, content={"job": view, "status_url": f"/api/jobs/{job.id}",
                                                  "provenance": meta["provenance"], "notes": [OVER_CAP_NOTE]})


@router.get("")
def list_(db: JobsDb, kind: str | None = None, state: str | None = None,
          limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    with closing(connect(db)) as conn:
        return {"jobs": [_view(conn, j) for j in list_jobs(conn, kind=kind, state=state, limit=limit)], **_meta()}


@router.get("/schedules")
def schedules() -> dict[str, Any]:
    from ...jobs.schedules import load_schedules

    now = utcnow()
    rows = []
    for e in load_schedules().entries:
        nxt = next_due(e.parsed, now)
        rows.append({"name": e.name, "kind": e.kind, "cron": e.cron, "priority": e.priority,
                     "mem_class": e.mem_class, "heavy": e.heavy, "next_run": iso(nxt) if nxt else None})
    return {"schedules": rows, "timezone": "America/New_York", **_meta()}


@router.get("/events")
async def events(request: Request, db: JobsDb, lifetime: float = Depends(get_sse_lifetime)) -> StreamingResponse:
    last = request.headers.get("last-event-id", "")
    start = int(last) if last.isdigit() else None

    async def gen():
        conn = connect(db)
        try:
            after = current_seq(conn) if start is None else start
            yield "retry: 3000\n\n"
            t0 = last_ping = time.monotonic()
            while time.monotonic() - t0 < lifetime:
                if await request.is_disconnected():
                    break
                for e in events_after(conn, after, limit=200):
                    after = e["seq"]
                    data = {"type": "job", "id": e["job_id"], "state": e["state"], "progress": e["progress"],
                            "message": e["message"]}
                    yield f"id: {e['seq']}\nevent: job\ndata: {json.dumps(data)}\n\n"
                if time.monotonic() - last_ping >= 15.0:
                    last_ping = time.monotonic()
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
        finally:
            conn.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{job_id}")
def one(job_id: str, db: JobsDb) -> Any:
    with closing(connect(db)) as conn:
        job = get_job(conn, job_id)
        if job is None:
            return _error(404, "not_found", f"no job {job_id}")
        return {**_view(conn, job), **_meta()}


@router.delete("/{job_id}")
def delete(job_id: str, request: Request, db: JobsDb) -> Any:
    with closing(connect(db)) as conn:
        job = get_job(conn, job_id)
        if job is None:
            return _error(404, "not_found", f"no job {job_id}")
        if job.submitted_by != "api" or client_of(conn, job_id) != client_id(request):
            return _error(403, "forbidden", "only the client that submitted a job may cancel it")
        job = cancel(conn, job_id, now=utcnow())
        return {**_view(conn, job), **_meta()}


@router.get("/{job_id}/result")
def result(job_id: str, db: JobsDb) -> Any:
    import pyarrow.parquet as pq

    with closing(connect(db)) as conn:
        job = get_job(conn, job_id)
        if job is None:
            return _error(404, "not_found", f"no job {job_id}")
        if job.state != "done" or not job.kind.startswith("api."):
            return JSONResponse(status_code=409, content={"error": "not_done", "state": job.state,
                                                          "detail": "the job has no result yet"})
        row = conn.execute("SELECT path FROM artifacts WHERE id = ?", (job.artifact_id,)).fetchone()
    path = Path(row["path"]) / "result.parquet" if row else None
    if path is not None and not inside(db.parent / "artifacts", path):  # Harden H4 (see routers/artifacts.py)
        return _error(404, "outside_root", "artifact files must lie under the artifacts directory")
    if path is None or not path.exists():
        return _error(410, "pruned", "this job's artifact has been pruned (retention: compute plan II.3)")
    return JSONResponse(content=json.loads(pq.read_table(path).column("payload")[0].as_py()))
