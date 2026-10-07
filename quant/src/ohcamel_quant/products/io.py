"""Earlier artifacts, read back by a later run (contract II.3; Lane M).

Handlers use these to carry state forward: a farm's evaluated cells, an
experiment's stored selection and once-only holdout, the surface history.
Read-only: nothing here writes jobs.sqlite or an artifact directory.
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _rows(db: Path, kind: str, params_hash: str | None) -> list[Any]:
    from ..jobs.db import connect

    sql, args = "SELECT path, finished_at FROM artifacts WHERE kind = ?", [kind]
    if params_hash is not None:
        sql += " AND params_hash = ?"
        args.append(params_hash)
    with closing(connect(db)) as conn:
        return conn.execute(sql + " ORDER BY finished_at DESC, id DESC", args).fetchall()


def previous_artifact(kind: str, params_hash: str | None = None, *, db_path: Path | None = None,
                      with_table: str | None = None) -> tuple[dict[str, Any], Path] | None:
    """The newest artifact of ``kind`` whose directory still has its manifest.

    With ``with_table``, the newest one that also holds that table. Experiments look their once-only holdout up
    this way: an INSUFFICIENT DATA run (a vendor outage, a short history) publishes no holdout, and reading the
    newest artifact instead would let the next run score the holdout a second time."""
    from ..jobs.paths import jobs_db_path

    db = Path(db_path) if db_path is not None else jobs_db_path()
    if not db.exists():
        return None
    for row in _rows(db, kind, params_hash):
        path = Path(row["path"])
        if with_table is not None and not (path / f"{with_table}.parquet").exists():
            continue
        try:
            return json.loads((path / "manifest.json").read_text()), path
        except FileNotFoundError:
            continue
    return None


def latest_with_path(kind: str, *, now: datetime,
                     db_path: Path | None = None) -> tuple[dict[str, Any], Path, bool] | None:
    """``(manifest, path, stale)``, with stale per schedules.yaml's ``max_age`` (never stale if none)."""
    from ..jobs.db import parse_iso
    from ..jobs.schedules import load_schedules

    got = previous_artifact(kind, db_path=db_path)
    if got is None:
        return None
    manifest, path = got
    max_age = load_schedules().max_age_s.get(kind)
    age = (now - parse_iso(manifest["finished_at"])).total_seconds()
    return manifest, path, max_age is not None and age > max_age


def read_table(path: Path, name: str) -> pd.DataFrame | None:
    f = Path(path) / f"{name}.parquet"
    return pd.read_parquet(f) if f.exists() else None
