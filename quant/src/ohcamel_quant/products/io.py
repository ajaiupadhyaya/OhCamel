"""Earlier artifacts, read back by a later run (contract II.3; Lane M).

Handlers use these to carry state forward: a farm's evaluated cells, an
experiment's stored selection and once-only holdout, the surface history.
Read-only: nothing here writes jobs.sqlite or an artifact directory.

Inside a job (Task 3.3) both reads are recorded by the job's recorder: a lookup as
``artifact-lookup: <kind> ...`` hashed over the artifact id it found (or none), a table as
``artifact: <kind>/<id>/<table>`` hashed over its rows. ``tools/regenerate.py`` replays a run with every
lookup pinned to the artifact the original run found, so a later artifact cannot change the re-run.

``worker_only`` (completion task 3.7) skips artifacts a burst machine made (manifest ``host: burst``). The desk's
readers (signals/) and the farm's carry-forward, which the monthly freeze reads, pass it, so a burst artifact never
reaches the book; the lookup's recorded name says so (`` worker_only=True``), and the burst door recomputes the
lookup with the same flag when it checks what a burst run read.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

_READ_ONLY = False


@contextmanager
def read_only_db() -> Iterator[None]:
    """Inside the block, artifact lookups open jobs.sqlite read-only (``jobs.db.open_ro``): no WAL pragma, no schema
    DDL, no write of any kind. For a tool that reads the live host's artifacts and must change nothing."""
    global _READ_ONLY
    was, _READ_ONLY = _READ_ONLY, True
    try:
        yield
    finally:
        _READ_ONLY = was


def _rows(db: Path, kind: str, params_hash: str | None) -> list[Any]:
    from ..jobs.db import connect, open_ro

    sql, args = "SELECT path, finished_at FROM artifacts WHERE kind = ?", [kind]
    if params_hash is not None:
        sql += " AND params_hash = ?"
        args.append(params_hash)
    with closing(open_ro(db) if _READ_ONLY else connect(db)) as conn:
        return conn.execute(sql + " ORDER BY finished_at DESC, id DESC", args).fetchall()


def previous_artifact(kind: str, params_hash: str | None = None, *, db_path: Path | None = None,
                      with_table: str | None = None, worker_only: bool = False) -> tuple[dict[str, Any], Path] | None:
    """The newest artifact of ``kind`` whose directory still has its manifest.

    With ``with_table``, the newest one that also holds that table. Experiments look their once-only holdout up
    this way: an INSUFFICIENT DATA run (a vendor outage, a short history) publishes no holdout, and reading the
    newest artifact instead would let the next run score the holdout a second time."""
    from ..jobs.provenance import current

    rec = current()
    lookup = lookup_name(kind, params_hash, with_table, worker_only)
    if rec is not None and rec.replay is not None:
        legacy = lookup_name(kind, params_hash, with_table)    # recorded before worker_only existed (task 3.7)
        if lookup not in rec.replay and worker_only and legacy in rec.replay:
            lookup = legacy
        if lookup not in rec.replay:
            raise LookupError(f"the re-run looked up {lookup}, which the original run did not")
        pinned = rec.replay[lookup]
        got = None if pinned is None else (json.loads((Path(pinned) / "manifest.json").read_text()), Path(pinned))
    else:
        got = _newest(kind, params_hash, db_path, with_table, worker_only)
    if rec is not None:
        found = got[0].get("id") if got else None
        rec.record(lookup, {"artifact": found}, source="artifact")
        rec.reads[-1]["artifact"] = None if got is None else f"{got[1].parent.name}/{got[1].name}"
    return got


def lookup_name(kind: str, params_hash: str | None, with_table: str | None, worker_only: bool = False) -> str:
    """The recorded name of a lookup (the read ``previous_artifact`` records, and regenerate's replay key)."""
    return (f"artifact-lookup: {kind} params_hash={params_hash} with_table={with_table}"
            + (" worker_only=True" if worker_only else ""))


def _newest(kind: str, params_hash: str | None, db_path: Path | None,
            with_table: str | None, worker_only: bool = False) -> tuple[dict[str, Any], Path] | None:
    from ..jobs.paths import jobs_db_path

    db = Path(db_path) if db_path is not None else jobs_db_path()
    if not db.exists():
        return None
    for row in _rows(db, kind, params_hash):
        path = Path(row["path"])
        if with_table is not None and not (path / f"{with_table}.parquet").exists():
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text())
        except FileNotFoundError:
            continue
        if worker_only and manifest.get("host") == "burst":
            continue
        return manifest, path
    return None


def latest_with_path(kind: str, *, now: datetime, db_path: Path | None = None,
                     worker_only: bool = False) -> tuple[dict[str, Any], Path, bool] | None:
    """``(manifest, path, stale)``, with stale per schedules.yaml's ``max_age`` (never stale if none)."""
    from ..jobs.db import parse_iso
    from ..jobs.schedules import load_schedules

    got = previous_artifact(kind, db_path=db_path, worker_only=worker_only)
    if got is None:
        return None
    manifest, path = got
    max_age = load_schedules().max_age_s.get(kind)
    age = (now - parse_iso(manifest["finished_at"])).total_seconds()
    return manifest, path, max_age is not None and age > max_age


def read_table(path: Path, name: str) -> pd.DataFrame | None:
    from ..jobs.provenance import current

    f = Path(path) / f"{name}.parquet"
    df = pd.read_parquet(f) if f.exists() else None
    rec = current()
    if rec is not None:
        rec.record(f"artifact: {Path(path).parent.name}/{Path(path).name}/{name}", df, source="artifact")
    return df
