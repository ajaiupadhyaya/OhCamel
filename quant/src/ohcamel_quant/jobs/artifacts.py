"""Artifacts (contract II.3): ``/data/artifacts/<kind>/<id>/manifest.json`` + one Parquet per table.

Immutable once published. "Latest" is the newest ``finished_at`` in the
``artifacts`` table; the read returns ``{"manifest", "stale"}`` (ship delta),
where ``stale`` is ``now - finished_at > max_age_s`` and never true when the
kind declares no max age.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import parse_iso

if TYPE_CHECKING:  # the worker's parent never imports pandas
    import pandas as pd

    from .queue import Job

TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENGINES = ("rust", "python")


@dataclass
class ArtifactSpec:
    """What a handler returns (``def run(params, ctx) -> ArtifactSpec``)."""

    tables: dict[str, pd.DataFrame]
    data_asof: str | None
    provenance: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    survivorship: str | None = None
    engine: str = "python"


def write_tables(staging: Path, tables: dict[str, Any]) -> list[str]:
    """Write each frame as ``<name>.parquet`` (no index: handlers return plain columns)."""
    staging.mkdir(parents=True, exist_ok=True)
    names = []
    for name, df in tables.items():
        if not TABLE_NAME.match(name):
            raise ValueError(f"table name {name!r} must match {TABLE_NAME.pattern}")
        df.to_parquet(staging / f"{name}.parquet", index=False)
        names.append(name)
    return names


def build_manifest(job: Job, result: dict[str, Any], *, code_sha: str, finished_at: str, cpu_seconds: float,
                   peak_rss_bytes: int | None) -> dict[str, Any]:
    if result.get("engine", "python") not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}")
    return {
        "id": job.id,
        "kind": job.kind,
        "params": job.params,
        "params_hash": job.params_hash,
        "code_sha": code_sha,
        "data_asof": result.get("data_asof"),
        "started_at": job.started_at,
        "finished_at": finished_at,
        "cpu_seconds": cpu_seconds,
        "peak_rss_bytes": peak_rss_bytes,
        "engine": result.get("engine", "python"),
        "provenance": list(result.get("provenance") or []),
        "notes": list(result.get("notes") or []),
        "survivorship": result.get("survivorship"),
        "tables": list(result.get("tables") or []),
    }


def publish(staging: Path, root: Path, job: Job, manifest: dict[str, Any]) -> Path:
    """Write manifest.json into the staged directory, then rename it into place (one filesystem: atomic)."""
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=False))
    final = root / job.kind / job.id
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)
    return final


def latest_artifact(conn: sqlite3.Connection, kind: str, params_hash: str | None = None, *, now: datetime,
                    max_age_s: float | None) -> dict[str, Any] | None:
    sql, args = "SELECT * FROM artifacts WHERE kind = ?", [kind]
    if params_hash is not None:
        sql += " AND params_hash = ?"
        args.append(params_hash)
    sql += " ORDER BY finished_at DESC, id DESC"
    for row in conn.execute(sql, args).fetchall():
        try:
            manifest = json.loads((Path(row["path"]) / "manifest.json").read_text())
        except FileNotFoundError:
            continue
        age = (now - parse_iso(row["finished_at"])).total_seconds()
        return {"manifest": manifest, "stale": max_age_s is not None and age > max_age_s}
    return None
