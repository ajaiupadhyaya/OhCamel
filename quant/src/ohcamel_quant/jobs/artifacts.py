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
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import iso, parse_iso

if TYPE_CHECKING:  # the worker's parent never imports pandas
    import pandas as pd

    from .queue import Job

TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENGINES = ("rust", "python")
VERDICTS = ("PASS", "FAIL", "ADVISORY", "INSUFFICIENT DATA", "DESCRIPTIVE ONLY")  # II.3 as amended (Lane M)


@dataclass
class ArtifactSpec:
    """What a handler returns (``def run(params, ctx) -> ArtifactSpec``)."""

    tables: dict[str, pd.DataFrame]
    data_asof: str | None
    provenance: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    survivorship: str | None = None
    engine: str = "python"
    verdict: str | None = None          # II.3 as amended: products lead with a verdict
    verdict_detail: str | None = None


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
    verdict = result.get("verdict")
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS} or None; got {verdict!r}")
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
        "verdict": verdict,
        "verdict_detail": result.get("verdict_detail"),
    }


def publish(staging: Path, root: Path, job: Job, manifest: dict[str, Any]) -> Path:
    """Write manifest.json into the staged directory, then rename it into place (one filesystem: atomic)."""
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=False))
    final = root / job.kind / job.id
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)
    return final


def inside(root: Path, p: Path) -> bool:
    """True when ``p``, every symlink and ``..`` resolved, lies under ``root`` (Harden H4: artifact reads
    never leave <data>/artifacts, whatever a row's path or a link on disk says)."""
    try:
        p.resolve().relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        return False
    return True


def latest_artifact(conn: sqlite3.Connection, kind: str, params_hash: str | None = None, *, now: datetime,
                    max_age_s: float | None, root: Path | None = None) -> dict[str, Any] | None:
    sql, args = "SELECT * FROM artifacts WHERE kind = ?", [kind]
    if params_hash is not None:
        sql += " AND params_hash = ?"
        args.append(params_hash)
    sql += " ORDER BY finished_at DESC, id DESC"
    for row in conn.execute(sql, args).fetchall():
        mpath = Path(row["path"]) / "manifest.json"
        if root is not None and not inside(root, mpath):
            continue  # a row or link that leaves the root is treated as absent, never followed
        try:
            manifest = json.loads(mpath.read_text())
        except FileNotFoundError:
            continue
        age = (now - parse_iso(row["finished_at"])).total_seconds()
        return {"manifest": manifest, "stale": max_age_s is not None and age > max_age_s}
    return None


KEEP_NEWEST = 30
EVENTS_KEEP = timedelta(hours=48)
STAGING_MAX_AGE_S = 24 * 3600


def _keeps_each_series(kind: str) -> bool:
    """Scheduled kinds (every kind not marked public, and any kind no longer registered) keep the
    newest artifact of each params_hash: their params come from schedules.yaml or the owner, a finite
    set. Public kinds (api.*) do not: there every request is its own params_hash."""
    from .kinds import REGISTRY

    spec = REGISTRY.get(kind)
    return spec is None or not spec.public


def prune(conn: sqlite3.Connection, *, kind: str | None = None, keep: int = KEEP_NEWEST) -> list[str]:
    """II.3 retention, per kind: the newest ``keep`` plus the newest of each calendar month (UTC).
    Scheduled kinds also keep the newest artifact of each params_hash (``_keeps_each_series``)."""
    sql, args = "SELECT id, kind, params_hash, finished_at, path FROM artifacts", []
    if kind is not None:
        sql += " WHERE kind = ?"
        args.append(kind)
    by_kind: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(sql + " ORDER BY finished_at DESC, id DESC", args).fetchall():
        by_kind.setdefault(r["kind"], []).append(r)
    pruned: list[str] = []
    for k, rows in by_kind.items():
        kept = {r["id"] for r in rows[:keep]}
        series = _keeps_each_series(k)
        months: set[str] = set()
        hashes: set[str] = set()
        for r in rows:  # newest first: the first row seen in a month (or of a series) is its newest
            if r["finished_at"][:7] not in months:
                months.add(r["finished_at"][:7])
                kept.add(r["id"])
            if series and r["params_hash"] not in hashes:
                hashes.add(r["params_hash"])
                kept.add(r["id"])
        for r in rows:
            if r["id"] not in kept:
                conn.execute("DELETE FROM artifacts WHERE id = ?", (r["id"],))
                shutil.rmtree(r["path"], ignore_errors=True)
                pruned.append(r["id"])
    return pruned


def prune_housekeeping(conn: sqlite3.Connection, root: Path, *, now: datetime) -> dict[str, int]:
    """SSE events older than 48 h; staging directories abandoned for a day (a worker killed mid-publish)."""
    cur = conn.execute("DELETE FROM job_events WHERE at < ?", (iso(now - EVENTS_KEEP),))
    removed = 0
    staging = root / ".staging"
    if staging.is_dir():
        cutoff = time.time() - STAGING_MAX_AGE_S
        for d in staging.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return {"events_deleted": cur.rowcount, "staging_removed": removed}
