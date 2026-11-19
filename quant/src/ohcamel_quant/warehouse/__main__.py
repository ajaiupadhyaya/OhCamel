"""``python -m ohcamel_quant.warehouse``: run an ingest handler by hand (the
controller's backfill, local checks) or build the offline fixture warehouse.

  run <kind> [--params JSON] [--threads N]   needs OHCAMEL_QUANT_WAREHOUSE_PATH
  build-fixture <path> [--requested-start YYYY-MM-DD]
  freshness                                  needs OHCAMEL_QUANT_WAREHOUSE_PATH
  export <warehouse> <root> [--date D]       the nightly export (warehouse/export.py; deploy/backup.sh)
  verify <export dir>                        print an export's record, or fail
  restore <export dir> <target> [--replace]  a new warehouse file from an export
  migrate [<warehouse>]                      apply pending migrations and finish a table move (after a deploy)
  backfill-vintages [--series S] [--since D] fill fred_vintages once, resumable (docs/runbooks/compute.md)
  calendar                                   refresh exchange_calendar from Alpaca now (needs APCA_* keys)
  calendar-slice <start> <end> <dir>         save Alpaca's /v2/calendar for a range, with a .meta.json sidecar
                                             (the test fixture of completion task 3.6; needs APCA_* keys)
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
    x.add_argument("--wait-s", type=float, default=None, help="how long to wait for a running ingest (default 600)")
    v = sub.add_parser("verify", help="print an export's verified record")
    v.add_argument("export")
    rs = sub.add_parser("restore", help="build a NEW warehouse file from an export")
    rs.add_argument("export")
    rs.add_argument("target")
    rs.add_argument("--replace", action="store_true",
                    help="replace an existing TARGET (moved aside, never deleted); the caller has stopped its users")
    m = sub.add_parser("migrate", help="apply pending migrations and finish any table move; print what was done")
    m.add_argument("warehouse", nargs="?", default=None, help="the file (default: OHCAMEL_QUANT_WAREHOUSE_PATH)")
    bv = sub.add_parser("backfill-vintages", help="fill fred_vintages from ALFRED once (resumable); needs "
                                                  "OHCAMEL_QUANT_WAREHOUSE_PATH")
    bv.add_argument("--series", action="append", default=None, help="repeatable (default: every VINTAGE_SERIES)")
    bv.add_argument("--since", default=None, help="keyless only: start the Friday grid here instead of resuming")
    sub.add_parser("calendar", help="refresh exchange_calendar from Alpaca's /v2/calendar now (the nightly bars run "
                                    "does it too); needs OHCAMEL_QUANT_WAREHOUSE_PATH and APCA_* keys")
    cs = sub.add_parser("calendar-slice", help="save Alpaca's /v2/calendar for a range, unmodified, with a "
                                               ".meta.json sidecar (a test fixture); needs APCA_* keys")
    cs.add_argument("start")
    cs.add_argument("end")
    cs.add_argument("dir")
    a = p.parse_args(argv)
    if a.cmd == "calendar-slice":
        return _calendar_slice(a)
    if a.cmd == "calendar":
        from .ingest.base import IngestFailed
        from .ingest.calendar import run_calendar

        try:
            spec = run_calendar(LocalContext(market=MarketData()))
        except IngestFailed as e:
            print(f"calendar: FAILED -- {e}", file=sys.stderr)
            return 1
        print(json.dumps({k: v for k, v in spec.items() if k != "tables"}, default=str, indent=2))
        return 0
    if a.cmd == "backfill-vintages":
        from datetime import date as _date

        from ..config import get_settings
        from .ingest.macro import VINTAGE_SERIES, backfill_vintages

        for sid in a.series or list(VINTAGE_SERIES):
            out = backfill_vintages(sid, get_settings(), since=_date.fromisoformat(a.since) if a.since else None,
                                    progress=lambda m: print(m, file=sys.stderr))
            print(json.dumps(out))
        return 0
    if a.cmd == "migrate":
        from .db import WarehouseUnavailable, apply_migrations

        try:
            print(json.dumps(apply_migrations(Path(a.warehouse) if a.warehouse else None)))
        except WarehouseUnavailable as e:
            print(f"migrate: FAILED -- {e}", file=sys.stderr)
            return 1
        return 0
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


def _calendar_slice(a: argparse.Namespace) -> int:
    """The response bytes exactly as Alpaca sent them, and the sidecar the fixture convention asks for (synthetic
    false, source, the instant the response arrived in UTC, sha256, what was kept). The keys go in headers only:
    neither file names them."""
    import hashlib
    from datetime import UTC, date, datetime

    from ..config import get_settings
    from ..data import http
    from ..data.prices import _alpaca_headers
    from .calendar import ALPACA_CALENDAR, parse_alpaca_calendar

    s = get_settings()
    if not (s.alpaca_key_id and s.alpaca_secret_key):
        print("calendar-slice: FAILED -- no Alpaca keys (APCA_API_KEY_ID / APCA_API_SECRET_KEY)", file=sys.stderr)
        return 1
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    params = {"start": start.isoformat(), "end": end.isoformat()}
    resp = http.get(ALPACA_CALENDAR, s, params=params, headers=_alpaca_headers(s), source="alpaca")
    fetched = datetime.now(UTC).replace(microsecond=0)
    body = resp.content
    frame = parse_alpaca_calendar(json.loads(body))          # refuse to save anything that is not a calendar
    out = Path(a.dir) / f"alpaca_calendar_{start}_{end}.json"
    out.write_bytes(body)
    meta = {"source": "alpaca", "synthetic": False, "url": ALPACA_CALENDAR, "params": params,
            "fetched_utc": fetched.isoformat().replace("+00:00", "Z"),
            "fetched_note": "the instant the response finished arriving (UTC, this machine's clock)",
            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "n_sessions": len(frame),
            "kept": "the whole response body, unmodified",
            "used_by": "completion task 3.6: the exchange calendar's freshness and integrity tests "
                       "(tests/test_warehouse_freshness.py)"}
    out.with_name(out.name + ".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"calendar-slice: {out} -- {len(frame)} sessions, sha256 {meta['sha256']}")
    return 0


def _export_cmd(a: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from . import export as E
    from .db import WarehouseUnavailable

    try:
        if a.cmd == "export":
            os.umask(0o007)   # group-writable: deploy/backup.sh, as the host user in the group, prunes old exports
            day = a.date or datetime.now(UTC).date().isoformat()
            out = E.export_warehouse(Path(a.warehouse), Path(a.root), day=day,
                                     jobs_db=Path(a.jobs_db) if a.jobs_db else None,
                                     wait_s=E.WRITER_WAIT_S if a.wait_s is None else a.wait_s)
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
