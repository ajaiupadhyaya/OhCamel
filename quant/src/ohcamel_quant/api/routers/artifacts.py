"""/api/artifacts -- the products' artifacts, read-only (compute plan II.3, amended by Lane M M1).

* ``GET /api/artifacts/{kind}/latest[?params_hash=]``: the newest artifact and
  whether it is stale (schedules.yaml ``max_age``).
* ``GET /api/artifacts/{kind}/{id}``: one manifest.
* ``GET /api/artifacts/{kind}/{id}/{table}[?limit=&offset=]``: one table as a
  column-major frame (``api.serialize.frame``: ``index``, ``columns``, ``data``
  at the top level, as II.3 says), plus ``table``, ``rows``, ``offset``.

Every payload leads with ``verdict``. Paths are never built from the request:
the kind must be registered, the id a ULID found in the ``artifacts`` table
under that kind, and the table one the manifest lists (Review Focus 4).
Every file read must also resolve (symlinks and ``..`` followed) under
``<data>/artifacts`` -- the directory beside jobs.sqlite, as jobs/paths.py lays
them out -- so a tampered row or a link on disk is refused, not followed
(Harden H4).

**The live book stays off this API.** ``desk.report`` copies the desk's fills,
per-strategy P&L and the account's closing figures into its tables and notes, and
``signals.overlay``'s ``names`` is the desk's combined target weight by symbol. Every
route here is public and the owner's rule is no position name or size on the public
site, so for those kinds ``PUBLIC_TABLES`` lists what may be served. It is an
allowlist so that a table added later is private until someone decides otherwise.
The files stay on disk: ``/api/machine`` reads ``book_summary`` from them itself,
and the desk's own reporting is untouched.
"""

from __future__ import annotations

import json
import re
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ...jobs.artifacts import TABLE_NAME, inside
from ...jobs.db import connect, utcnow
from ...jobs.kinds import REGISTRY
from ...jobs.paths import jobs_db_path
from ...jobs.schedules import read_latest
from ..serialize import frame

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ROWS = 50_000

#: The tables a kind may serve, for the kinds that hold the live book. A kind absent
#: here serves every table its manifest lists.
PUBLIC_TABLES: dict[str, frozenset[str]] = {
    "desk.report": frozenset({"book_summary"}),
    "signals.overlay": frozenset({"overlay", "sources"}),
}
#: Kinds whose notes quote the book: the session's equity, gross, net and P&L.
NOTES_WITHHELD = frozenset({"desk.report"})
WITHHELD_NOTE = "This report's other tables and its notes carry the live book and are not public."


def get_artifacts_db_path() -> Path:
    return jobs_db_path()


Db = Annotated[Path, Depends(get_artifacts_db_path)]


def _root(db: Path) -> Path:
    return db.parent / "artifacts"  # jobs/paths.py: jobs.sqlite and artifacts/ share Settings.data_dir


def _outside() -> JSONResponse:
    return _err(404, "outside_root", "artifact files must lie under the artifacts directory")


def _err(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


def _public(kind: str, m: dict[str, Any]) -> dict[str, Any]:
    """The manifest as this API may show it: listed tables only, and no notes that quote the book."""
    allowed = PUBLIC_TABLES.get(kind)
    if allowed is None:
        return m
    out = {**m, "tables": [t for t in m.get("tables") or [] if t in allowed]}
    if kind in NOTES_WITHHELD:
        out["notes"] = [WITHHELD_NOTE]
    return out


def _head(m: dict[str, Any]) -> dict[str, Any]:
    return {"verdict": m.get("verdict"), "verdict_detail": m.get("verdict_detail")}


def _meta(m: dict[str, Any]) -> dict[str, Any]:
    return {"provenance": m.get("provenance") or [], "notes": m.get("notes") or []}


def _unknown(kind: str) -> JSONResponse | None:
    return None if kind in REGISTRY else _err(404, "unknown_kind", f"no job kind {kind!r}")


def _manifest(db: Path, kind: str, art_id: str) -> tuple[dict[str, Any], Path] | JSONResponse:
    if not ULID_RE.match(art_id) or not db.exists():
        return _err(404, "not_found", f"no {kind} artifact {art_id}")
    with closing(connect(db)) as conn:
        row = conn.execute("SELECT path FROM artifacts WHERE id = ? AND kind = ?", (art_id, kind)).fetchone()
    if row is None:
        return _err(404, "not_found", f"no {kind} artifact {art_id}")
    path = Path(row["path"])
    if not inside(_root(db), path / "manifest.json"):
        return _outside()
    try:
        return json.loads((path / "manifest.json").read_text()), path
    except FileNotFoundError:
        return _err(410, "pruned", "this artifact has been pruned (retention: compute plan II.3)")


@router.get("/{kind}/latest")
def latest(kind: str, db: Db, params_hash: str | None = None) -> Any:
    if (bad := _unknown(kind)) is not None:
        return bad
    if params_hash is not None and not HASH_RE.match(params_hash):
        return _err(422, "invalid_input", "params_hash must be 64 lowercase hex characters")
    if not db.exists():
        return _err(404, "not_found", f"no {kind} artifact yet")
    with closing(connect(db)) as conn:
        got = read_latest(conn, kind, params_hash, now=utcnow(), root=_root(db))
    if got is None:
        return _err(404, "not_found", f"no {kind} artifact yet")
    m = _public(kind, got["manifest"])
    return {**_head(m), "stale": got["stale"], "manifest": m, **_meta(m)}


@router.get("/{kind}/{art_id}")
def manifest(kind: str, art_id: str, db: Db) -> Any:
    if (bad := _unknown(kind)) is not None:
        return bad
    got = _manifest(db, kind, art_id)
    if isinstance(got, JSONResponse):
        return got
    m = _public(kind, got[0])
    return {**_head(m), "manifest": m, **_meta(m)}


@router.get("/{kind}/{art_id}/{table}")
def table(kind: str, art_id: str, table: str, db: Db, limit: int = Query(default=5000, ge=1, le=MAX_ROWS),
          offset: int = Query(default=0, ge=0)) -> Any:
    if (bad := _unknown(kind)) is not None:
        return bad
    got = _manifest(db, kind, art_id)
    if isinstance(got, JSONResponse):
        return got
    m, path = _public(kind, got[0]), got[1]
    if not TABLE_NAME.match(table) or table not in (m.get("tables") or []):
        return _err(404, "no_table", f"artifact {art_id} has no table {table!r}")
    f = path / f"{table}.parquet"
    if not inside(_root(db), f):
        return _outside()
    if not f.exists():
        return _err(410, "pruned", "this artifact's table has been pruned")
    df = pd.read_parquet(f)
    page = df.iloc[offset:offset + limit].reset_index(drop=True)
    # The frame's own keys (index, columns, data) at the top level: contract II.3's "JSON frame" shape, which
    # web/src/lib/artifacts.ts frameRecords reads, with the verdict first and the paging fields beside it.
    return {**_head(m), **frame(page), "table": table, "rows": int(len(df)), "offset": offset, **_meta(m)}
