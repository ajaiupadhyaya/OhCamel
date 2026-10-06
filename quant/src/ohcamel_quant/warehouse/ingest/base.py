"""The shared ingest loop (compute plan II.2 handler protocol, II.3 result shape).

``run_ingest`` drives one dataset over a list of keys (tickers, series, CIKs):

1. one short read-write connection reads every key's current state;
2. per key, ``fetch`` runs the vendor calls OUTSIDE any connection (the write
   lock is never held across the network) and yields one or more payloads
   (minute bars yield one per month, to bound memory);
3. each payload is written in its own short transaction (rolled back on any
   error); ``write`` returns the key's post-write ``data_asof``;
4. one ``ingest_log`` row per key -- on failure ``status='failed'`` with the
   LAST GOOD ``data_asof`` -- and a summary row ``key='*'`` with status
   ``ok`` | ``partial`` | ``failed`` | ``cancelled``.

Every key failing raises :class:`IngestFailed` (a ``DataUnavailable``: the job
fails and Lane B's ingest retry rule applies). ``ctx.cancelled()`` is checked
between keys and raises :class:`IngestCancelled`.

The result is the II.3 manifest's handler part: ``data_asof``, ``provenance``,
``notes``, ``survivorship`` and ``tables`` (``{name: DataFrame}``, written by the
worker as Parquet). The worker adds id, kind, params, code_sha, timings, etc.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

import pandas as pd

from ...data.base import DataUnavailable, Provenance
from ..db import WarehouseUnavailable, open_rw, warehouse_path

SUMMARY_KEY = "*"
MAX_FAILURES_LISTED = 20


def utcnow() -> datetime:
    """Naive UTC, the warehouse's TIMESTAMP convention."""
    return datetime.now(UTC).replace(tzinfo=None)


def log_row(con: Any, dataset: str, key: str, status: str, rows: int, detail: str,
            data_asof: date | None, ran_at: datetime | None = None) -> None:
    con.execute("INSERT INTO ingest_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                [dataset, key, ran_at or utcnow(), int(rows), status, detail, data_asof])


def insert_frame(con: Any, sql: str, frame: pd.DataFrame) -> None:
    """Run ``sql``, which reads ``frame`` as the relation ``incoming``."""
    con.register("incoming", frame)
    try:
        con.execute(sql)
    finally:
        con.unregister("incoming")


class IngestContext(Protocol):
    """The part of II.2's JobContext that ingest uses. Ingest never reads
    ``warehouse`` (a read-only connection): it writes through ``open_rw``."""

    market: Any
    threads: int

    def progress(self, fraction: float, message: str) -> None: ...

    def cancelled(self) -> bool: ...


class IngestFailed(DataUnavailable):
    """Every key of an ingest run failed."""


class IngestCancelled(RuntimeError):
    """The job was cancelled between keys; rows already written stay."""


@dataclass
class KeyState:
    data_asof: date | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Written:
    rows: int
    data_asof: date | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class LocalContext:
    """A JobContext stand-in for the CLI and tests (no worker, no jobs.sqlite)."""

    market: Any
    threads: int = 1

    def progress(self, fraction: float, message: str) -> None:
        return None

    def cancelled(self) -> bool:
        return False

    @property
    def warehouse(self) -> Any:
        raise AttributeError("ingest handlers must not read ctx.warehouse (II.2's read-only connection); "
                             "they write through open_rw")


def _write_log(path: Any, threads: int, dataset: str, key: str, status: str, rows: int,
               detail: dict[str, Any], data_asof: date | None) -> None:
    with open_rw(path, threads=threads) as con:
        log_row(con, dataset, key, status, rows, json.dumps(detail, default=str), data_asof)


def run_ingest(*, dataset: str, keys: list[str], ctx: IngestContext,
               read_state: Callable[[Any, list[str]], dict[str, KeyState]],
               fetch: Callable[[str, KeyState, Any], Iterable[Any]],
               write: Callable[[Any, str, KeyState, Any], Written],
               notes: list[str] | None = None, survivorship: str | None = None) -> dict[str, Any]:
    settings = ctx.market.settings
    path = warehouse_path(settings)
    if path is None:
        raise WarehouseUnavailable("warehouse not configured (OHCAMEL_QUANT_WAREHOUSE_PATH unset)")
    if not keys:
        raise IngestFailed(f"{dataset}: nothing to ingest (no keys)")
    threads = max(1, int(getattr(ctx, "threads", 1) or 1))
    with open_rw(path, threads=threads) as con:
        states = read_state(con, keys)
    n = len(keys)
    results: list[dict[str, Any]] = []
    sources: dict[str, int] = {}
    for i, key in enumerate(keys):
        if ctx.cancelled():
            _summary(path, threads, dataset, results, "cancelled")
            raise IngestCancelled(f"{dataset}: cancelled after {i} of {n} keys")
        ctx.progress(i / n, f"{dataset} {key} ({i + 1}/{n})")
        state = states.get(key) or KeyState()
        rows, asof, detail = 0, state.data_asof, {}
        try:
            for payload in fetch(key, state, settings):
                with open_rw(path, threads=threads) as con:
                    con.execute("BEGIN TRANSACTION")
                    try:
                        w = write(con, key, state, payload)
                        con.execute("COMMIT")
                    except Exception:
                        con.execute("ROLLBACK")
                        raise
                rows += w.rows
                asof = w.data_asof
                detail.update(w.detail)
            status, why = "ok", None
            if detail.get("source"):
                sources[str(detail["source"])] = sources.get(str(detail["source"]), 0) + 1
        except DataUnavailable as e:
            status, why = "failed", str(e)
        except Exception as e:  # noqa: BLE001 - one malformed payload must not stop the run
            status, why = "failed", f"{type(e).__name__}: {e}"
        if why:
            detail["error"] = why
        _write_log(path, threads, dataset, key, status, rows, detail, asof)
        results.append({"key": key, "status": status, "rows": rows, "data_asof": asof,
                        "detail": json.dumps(detail, default=str)})
    status = _summary(path, threads, dataset, results, None)
    ctx.progress(1.0, f"{dataset} done: {status}")
    failed = [r for r in results if r["status"] == "failed"]
    if len(failed) == n:
        raise IngestFailed(f"{dataset}: all {n} keys failed: "
                           + "; ".join(f"{r['key']}: {json.loads(r['detail'])['error']}"
                                       for r in failed[:MAX_FAILURES_LISTED]))
    out_notes = list(notes or [])
    if failed:
        out_notes.append(f"{len(failed)} of {n} keys failed: " + "; ".join(
            f"{r['key']}: {json.loads(r['detail'])['error']}" for r in failed[:MAX_FAILURES_LISTED]))
    dates = [r["data_asof"] for r in results if r["data_asof"] is not None]
    provenance = [Provenance.now(src, dataset=dataset, keys=count).to_dict() for src, count in sorted(sources.items())]
    return {
        "data_asof": max(dates).isoformat() if dates else None,
        "provenance": provenance,
        "notes": out_notes,
        "survivorship": survivorship,
        "tables": {"ingest_summary": pd.DataFrame(results, columns=["key", "status", "rows", "data_asof", "detail"])},
    }


def _summary(path: Any, threads: int, dataset: str, results: list[dict[str, Any]], forced: str | None) -> str:
    ok = sum(r["status"] == "ok" for r in results)
    failed = [r for r in results if r["status"] == "failed"]
    status = forced or ("ok" if not failed else "failed" if ok == 0 else "partial")
    dates = [r["data_asof"] for r in results if r["data_asof"] is not None]
    detail = {"keys": len(results), "ok": ok, "failed": len(failed),
              "failures": [f"{r['key']}: {json.loads(r['detail']).get('error')}" for r in failed[:MAX_FAILURES_LISTED]]}
    _write_log(path, threads, dataset, SUMMARY_KEY, status, sum(r["rows"] for r in results), detail,
               max(dates) if dates else None)
    return status
