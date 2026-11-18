"""The nightly warehouse export and its restore (Task 3.3).

``export_warehouse(src, root, day)`` writes ``root/<day>/``: DuckDB's ``EXPORT DATABASE ... (FORMAT PARQUET)``
-- ``schema.sql`` (every table with its primary key), ``load.sql``, one zstd-compressed Parquet per table --
plus ``export.json``, the record that makes it a good export: every table's row count read back from the
Parquet files and equal to the source's, the source's migration version, the DuckDB that wrote it.

* The source is opened READ-ONLY (DuckDB 1.5: EXPORT works on a read-only connection). A read-only process holds
  the shared lock, so the single writer -- an ingest job -- waits for the export (it retries for 30 s, then the
  job's own two retries apply); the export retries the open for 60 s when an ingest holds the write lock.
* It is written to ``<day>.part``, verified, and only then renamed to ``<day>``; a failure removes the
  ``.part`` and leaves every earlier export as it was. A second export on one day replaces the first only
  after it has verified. ``deploy/pull-backups.sh`` skips ``*.part``.
* Retention (the 14 newest and the newest of each month) is deploy/backup.sh's, beside its other copies.

``restore_export(export_dir, target)`` builds a NEW DuckDB file from an export (``IMPORT DATABASE``) into
``<target>.restoring``, checks every count against the record, and renames it into place. It refuses a target
that exists unless ``replace=True``, and then moves the old file aside as ``<target>.pre-restore-<stamp>``,
never deleting it. ``schema_version`` is a table like any other, so the migration runner sees the restored
version. Whether the services are stopped is deploy/restore.sh's question, asked before this runs.

``python -m ohcamel_quant.warehouse export SRC ROOT [--date D]`` and ``restore EXPORT TARGET [--replace]``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .db import _connect

RECORD = "export.json"
SOURCE_TIMEOUT_S = 60.0
SOURCE_CONFIG = {"threads": 1, "memory_limit": "256MB"}
_COPY = re.compile(r"""^COPY\s+("?)(?P<table>[^"\s]+)\1\s+FROM\s+'(?P<path>[^']+)'""", re.IGNORECASE)


class ExportFailed(RuntimeError):
    """An export or a restore that cannot be trusted; nothing it touched was left looking good."""


def _q(path: Path) -> str:
    return str(path).replace("'", "''")


def _tables(con: Any) -> list[str]:
    return [t for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' "
                                      "AND table_type = 'BASE TABLE' ORDER BY 1").fetchall()]


def _counts(con: Any, tables: list[str]) -> dict[str, int]:
    return {t: int(con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]) for t in tables}


def _load_files(directory: Path) -> dict[str, Path]:
    """``{table: its Parquet file in directory}`` from load.sql (DuckDB renames some: holdings_13f ->
    holdings___f.parquet), by file name, so a renamed export directory still resolves."""
    out = {}
    for line in (directory / "load.sql").read_text().splitlines():
        m = _COPY.match(line.strip())
        if m:
            out[m.group("table")] = directory / Path(m.group("path")).name
    return out


def _count_export(directory: Path, tables: list[str]) -> dict[str, int]:
    files = _load_files(directory)
    missing = [t for t in tables if t not in files or not files[t].is_file()]
    if missing:
        raise ExportFailed(f"the export at {directory} has no Parquet file for: {', '.join(missing)}")
    con = duckdb.connect()
    try:
        return {t: int(con.execute(f"SELECT count(*) FROM read_parquet('{_q(files[t])}')").fetchone()[0])
                for t in tables}
    finally:
        con.close()


def _size(directory: Path) -> int:
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def export_warehouse(src: Path, root: Path, *, day: str) -> Path:
    """Export ``src`` to ``root/<day>`` (module docstring); return the directory."""
    src, root = Path(src), Path(root)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError(f"day must be YYYY-MM-DD, got {day!r}")
    if not src.is_file():
        raise ExportFailed(f"{src} does not exist: nothing to export")
    root.mkdir(parents=True, exist_ok=True)
    final, part, old = root / day, root / f"{day}.part", root / f"{day}.old.part"
    shutil.rmtree(part, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    try:
        con = _connect(src, read_only=True, config=dict(SOURCE_CONFIG), timeout_s=SOURCE_TIMEOUT_S)
        try:
            tables = _tables(con)
            counts = _counts(con, tables)
            version = int(con.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0]) \
                if "schema_version" in tables else 0
            con.execute(f"EXPORT DATABASE '{_q(part)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        finally:
            con.close()
        got = _count_export(part, tables)
        bad = {t: (counts[t], got.get(t)) for t in tables if got.get(t) != counts[t]}
        if bad:
            raise ExportFailed("the export's row count differs from the warehouse's for "
                               + ", ".join(f"{t} ({w} in the warehouse, {g} exported)" for t, (w, g) in bad.items()))
        record = {"day": day, "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                  "source": src.name, "duckdb": duckdb.__version__, "schema_version": version, "tables": counts,
                  "bytes": _size(part)}
        tmp = part / (RECORD + ".tmp")
        tmp.write_text(json.dumps(record, indent=1, sort_keys=True))
        os.replace(tmp, part / RECORD)
        if final.exists():
            os.replace(final, old)          # the earlier export of this day goes only once this one has verified
        os.replace(part, final)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        if old.exists() and not final.exists():
            os.replace(old, final)
        raise
    return final


def read_record(export_dir: Path) -> dict[str, Any]:
    f = Path(export_dir) / RECORD
    if not f.is_file():
        raise ExportFailed(f"{export_dir} is not a verified export (no {RECORD}): a partial or unverified export is "
                           "never restored")
    return json.loads(f.read_text())


def restore_export(export_dir: Path, target: Path, *, replace: bool = False, stamp: str | None = None) -> Path:
    """Build ``target`` from ``export_dir`` (module docstring); return it."""
    export_dir, target = Path(export_dir), Path(target)
    record = read_record(export_dir)
    if target.exists() and not replace:
        raise ExportFailed(f"{target} exists; a restore writes a new file. To replace a warehouse, stop every service "
                           "that opens it and pass replace (deploy/restore.sh --warehouse ... --replace-live)")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".restoring")
    for f in (tmp, tmp.with_name(tmp.name + ".wal")):
        f.unlink(missing_ok=True)
    try:
        con = duckdb.connect(str(tmp))
        try:
            con.execute(f"IMPORT DATABASE '{_q(export_dir)}'")
            got = _counts(con, list(record["tables"]))
            con.execute("CHECKPOINT")
        finally:
            con.close()
        bad = {t: (n, got.get(t)) for t, n in record["tables"].items() if got.get(t) != n}
        if bad:
            raise ExportFailed("the restored row counts differ from the export's record for "
                               + ", ".join(f"{t} ({w} recorded, {g} restored)" for t, (w, g) in bad.items()))
        if target.exists():
            at = stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            for f in (target, target.with_name(target.name + ".wal")):
                if f.exists():
                    os.replace(f, f.with_name(f"{f.name}.pre-restore-{at}"))
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target
