"""``python -m ohcamel_quant.warehouse``: run an ingest handler by hand (the
controller's backfill, local checks) or build the offline fixture warehouse.

  run <kind> [--params JSON] [--threads N]   needs OHCAMEL_QUANT_WAREHOUSE_PATH
  build-fixture <path>
  freshness                                  needs OHCAMEL_QUANT_WAREHOUSE_PATH
"""

from __future__ import annotations

import argparse
import json
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
    sub.add_parser("freshness", help="print data_asof and staleness per dataset")
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
        print(build_fixture_warehouse(Path(a.path)))
        return 0
    if a.cmd == "freshness":
        from . import freshness
        from .db import open_ro

        with open_ro() as con:
            print(json.dumps(freshness.dataset_freshness(con, freshness._now()), indent=2))
        return 0
    return 2  # pragma: no cover - argparse enforces the choices


if __name__ == "__main__":
    raise SystemExit(main())
