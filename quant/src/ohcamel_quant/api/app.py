"""FastAPI application.

Routers are auto-discovered: every module in ``ohcamel_quant.api.routers``
that defines a module-level ``router`` (an ``APIRouter``) is mounted under
``/api``. Adding a domain never edits this file.

The built single-page app (``quant/web/dist``) is served at ``/`` with an
index.html fallback for client-side routes.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import get_settings
from ..data.base import DataUnavailable
from . import routers as routers_pkg

log = logging.getLogger("ohcamel_quant")

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Periodic cache refresh of the landing-page datasets (no-op offline).
    from ..data.market import start_background_refresh, stop_background_refresh

    start_background_refresh()
    try:
        yield
    finally:
        stop_background_refresh()


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=_lifespan,
        title="OhCamel Quant API",
        version=__version__,
        description="Real-data quantitative finance: risk, portfolios, factors, backtests, options, rates, fundamentals.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.exception_handler(DataUnavailable)
    async def _unavailable(_: Request, exc: DataUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": "data_unavailable", "detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad_input(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_input", "detail": str(exc)})

    @app.get("/api/health")
    def health() -> dict:
        s = get_settings()
        return {"status": "ok", "version": __version__, "offline": s.offline}

    for mod in pkgutil.iter_modules(routers_pkg.__path__):
        m = importlib.import_module(f"{routers_pkg.__name__}.{mod.name}")
        router = getattr(m, "router", None)
        if router is not None:
            app.include_router(router, prefix="/api")
            log.info("mounted router %s", mod.name)

    if WEB_DIST.exists():
        assets = WEB_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.api_route(
            "/{path:path}", methods=["GET", "HEAD"], include_in_schema=False, response_model=None
        )
        def spa(path: str) -> FileResponse | JSONResponse:
            if path == "api" or path.startswith("api/"):
                return JSONResponse(status_code=404, content={"error": "not_found", "detail": f"/{path}"})
            candidate = WEB_DIST / path
            if path and candidate.is_file() and WEB_DIST in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()
