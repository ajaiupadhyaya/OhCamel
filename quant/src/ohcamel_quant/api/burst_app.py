"""The burst door's HTTP app (completion task 3.7): ``ohcamel-quant burst-door`` (compose service ohcamel-burst).

A separate app on purpose. The public app (api/app.py) mounts every module in api/routers/; this module is not one
of them, so the public host -- which proxies only to the public app -- has no route to the door at all. Caddy routes
``/burst/*`` on the password-gated live host here, behind its basic auth, and nowhere else (deploy/Caddyfile). The
rules are jobs/burst_door.py's; this file reads the body under the size cap, checks the signature, and maps a
refusal to its status. No docs, no OpenAPI: nothing here is for browsing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from ..jobs import burst as B
from ..jobs import burst_door as D


def get_door() -> D.Door:
    return D.Door.from_env()


DoorDep = Annotated[D.Door, Depends(get_door)]


async def _read(request: Request) -> bytes:
    """The body, refused (413) as soon as it passes the cap -- before it is all in memory."""
    cap = B.MAX_UPLOAD_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise D.Refused(413, "too_large", f"at most {cap} bytes")
    chunks, n = [], 0
    async for chunk in request.stream():
        n += len(chunk)
        if n > cap:
            raise D.Refused(413, "too_large", f"at most {cap} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def create_burst_app() -> FastAPI:
    app = FastAPI(title="OhCamel burst door", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(D.Refused)
    async def _refused(_: Request, exc: D.Refused) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.get("/burst/health")
    def health(door: DoorDep) -> dict[str, Any]:
        return {"status": "ok", "open": B.usable_secret(door.secret) is not None}

    @app.post("/burst/hold")
    async def hold(request: Request, door: DoorDep) -> Any:
        body = await _read(request)
        D.check_request(door, "POST", request.url.path, request.headers, body)
        return await run_in_threadpool(D.hold, door, body, now=datetime.now(UTC))

    @app.post("/burst/jobs/{job_id}/artifact", status_code=201)
    async def upload(job_id: str, request: Request, door: DoorDep) -> Any:
        body = await _read(request)
        D.check_request(door, "POST", request.url.path, request.headers, body)
        out = await run_in_threadpool(D.accept_upload, door, job_id, body, now=datetime.now(UTC))
        return JSONResponse(status_code=201, content=out)

    @app.get("/burst/artifact/{kind}/{art_id}/{name}")
    async def artifact(kind: str, art_id: str, name: str, request: Request, door: DoorDep) -> Any:
        D.check_request(door, "GET", request.url.path, request.headers, b"")
        f = await run_in_threadpool(D.artifact_file, door, kind, art_id, name)
        return FileResponse(f, media_type="application/octet-stream")

    return app


app = create_burst_app()
