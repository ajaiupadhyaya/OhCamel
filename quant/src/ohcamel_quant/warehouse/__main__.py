"""``python -m ohcamel_quant.warehouse``: run an ingest handler by hand (the
controller's backfill, local checks) or build the offline fixture warehouse.

  run <kind> [--params JSON] [--threads N]   needs OHCAMEL_QUANT_WAREHOUSE_PATH
  build-fixture <path> [--requested-start YYYY-MM-DD]
  freshness                                  needs OHCAMEL_QUANT_WAREHOUSE_PATH
  export <warehouse> <root> [--date D]       the nightly export (warehouse/export.py; deploy/backup.sh)
  verify <export dir>                        print an export's record, or fail
  restore <export dir> <target> [--replace]  a new warehouse file from an export
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..data.market import MarketData
from .fixture_db import build_fixture_warehouse
from .ingest.base import LocalContext


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ohcamel_quant.warehouse")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one ingest handler")
    r.add_argument("kind")
    r.add_argument("--params", default="{}")
    r.add_argument("--threads", type=int, default=1)
    b = sub.add_parser("build-fixture", help="build the offline fixture warehouse")
    b.add_argument("path")
    b.add_argument("--requested-start", default=None, help="log the bars as fetched from this date (fixture_db)")
    sub.add_parser("freshness", help="print data_asof and staleness per dataset")
    x = sub.add_parser("export", help="export the warehouse, read-only, to ROOT/<date>/ and verify it")
    x.add_argument("warehouse")
    x.add_argument("root")
    x.add_argument("--date", default=None, help="the directory's date (default: today, UTC)")
    x.add_argument("--jobs-db", default=None, help="the job queue (jobs.sqlite): wait while an ingest writes")
    x.add_argument("--wait-s", type=float, default=1800.0, help="how long to wait for a running ingest")
    v = sub.add_parser("verify", help="print an export's verified record")
    v.add_argument("export")
    rs = sub.add_parser("restore", help="build a NEW warehouse file from an export")
    rs.add_argument("export")
    rs.add_argument("target")
    rs.add_argument("--replace", action="store_true",
                    help="replace an existing TARGET (moved aside, never deleted); the caller has stopped its users")
    a = p.parse_args(argv)
    if a.cmd == "run":
        from .ingest import HANDLERS

        handler = HANDLERS.get(a.kind)
        if handler is None:
            print(f"unknown kind {a.kind!r}; known: {', '.join(sorted(HANDLERS)) or '(none)'}", file=sys.stderr)
            return 2
        spec = handler(json.loads(a.params), LocalContext(market=MarketData(), threads=a.threads))
        print(json.dumps({k: v for k, v in spec.items() if k != "tables"}, default=str, indent=2))
        return 0
    if a.cmd == "build-fixture":
        print(build_fixture_warehouse(Path(a.path), requested_start=a.requested_start))
        return 0
    if a.cmd in ("export", "verify", "restore"):
        return _export_cmd(a)
    if a.cmd == "freshness":
        from . import freshness
        from .db import open_ro

        with open_ro() as con:
            print(json.dumps(freshness.dataset_freshness(con, freshness._now()), indent=2))
        return 0
    return 2  # pragma: no cover - argparse enforces the choices


def _export_cmd(a: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from . import export as E
    from .db import WarehouseUnavailable

    try:
        if a.cmd == "export":
            os.umask(0o007)   # group-writable: deploy/backup.sh, as the host user in the group, prunes old exports
            day = a.date or datetime.now(UTC).date().isoformat()
            out = E.export_warehouse(Path(a.warehouse), Path(a.root), day=day,
                                     jobs_db=Path(a.jobs_db) if a.jobs_db else None, wait_s=a.wait_s)
            rec = E.read_record(out)
            print(f"export: {out} -- {len(rec['tables'])} tables, {sum(rec['tables'].values())} rows, "
                  f"{rec['bytes']} bytes, schema version {rec['schema_version']}, verified")
        elif a.cmd == "verify":
            print(json.dumps(E.read_record(Path(a.export)), indent=1, sort_keys=True))
        else:
            out = E.restore_export(Path(a.export), Path(a.target), replace=a.replace)
            rec = E.read_record(Path(a.export))
            print(f"restore: {out} -- {len(rec['tables'])} tables, {sum(rec['tables'].values())} rows, every count "
                  f"equal to the export's record, schema version {rec['schema_version']}")
    except (E.ExportFailed, E.duckdb.Error, WarehouseUnavailable, OSError, ValueError) as e:
        print(f"{a.cmd}: FAILED -- {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
