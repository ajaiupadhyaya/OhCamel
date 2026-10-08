"""Shared helpers for Lane M tests: publish an artifact exactly the way the worker does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ohcamel_quant.jobs.artifacts import build_manifest, publish, write_tables
from ohcamel_quant.jobs.db import connect, iso
from ohcamel_quant.jobs.queue import claim_next, enqueue, finish

T0 = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)


def is_label(detail: str | None) -> bool:
    """A verdict detail is public copy: a terse upper-case label, never a sentence or an operator instruction."""
    return bool(detail) and detail == detail.upper() and not any(c in detail for c in ";:`") \
        and "PYTHON -M" not in detail


def publish_artifact(db, root, kind, tables, *, verdict=None, finished=T0, params=None, mem="S",
                     data_asof="2026-06-01"):
    """Queue, claim, write and finish one job; returns (artifact id, artifact directory)."""
    conn = connect(db)
    enqueue(conn, kind=kind, params=params or {"t": iso(finished)}, priority=2, mem_class=mem, heavy=False,
            submitted_by="schedule:test", now=finished - timedelta(seconds=5))
    job = claim_next(conn, allowed_classes=frozenset({"S", "M", "L"}), allow_heavy=True,
                     now=finished - timedelta(seconds=4))
    staging = root / ".staging" / job.id
    names = write_tables(staging, tables)
    m = build_manifest(job, {"data_asof": data_asof, "provenance": [{"source": "test"}], "notes": [],
                             "survivorship": None, "engine": "python", "tables": names, "verdict": verdict,
                             "verdict_detail": None if verdict is None else "test"},
                       code_sha="abc", finished_at=iso(finished), cpu_seconds=1.0, peak_rss_bytes=1)
    final = publish(staging, root, job, m)
    finish(conn, job.id, artifact={"id": job.id, "kind": kind, "params_hash": job.params_hash,
                                   "finished_at": iso(finished), "path": str(final), "data_asof": data_asof},
           cpu_seconds=1.0, peak_rss_bytes=1, now=finished)
    conn.close()
    return job.id, final
