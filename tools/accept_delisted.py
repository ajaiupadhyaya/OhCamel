#!/usr/bin/env python3
"""The delisted-names acceptance test (completion task 3.8, spec R5): ask the delisted-names source for each of the
26 known failures and acquisitions in quant/src/ohcamel_quant/warehouse/delisted_acceptance.csv and, when at least 24
come back with prices up to their last trading date, record ``accepted_at`` in the warehouse and exit 0. Otherwise
exit 1 and record nothing. Backtests over dated index membership stay refused until this has passed on the owner's
key.

    cd quant && EODHD_API_KEY=... OHCAMEL_QUANT_WAREHOUSE_PATH=... uv run --frozen python ../tools/accept_delisted.py

It needs the key only, not OHCAMEL_DELISTED_SOURCE: run it before the switch goes on, so a failure leaves the
switch off. Exit 0 accepted and recorded, 1 not accepted, 2 not configured, 3 passed but not recorded (warehouse busy: run again).

On the droplet the image has no tools/ directory; the same code runs as
``dc exec ohcamel-worker python -m ohcamel_quant.warehouse accept-delisted`` (docs/runbooks/compute.md, Delisted
names). What it checks, and why, is ohcamel_quant/warehouse/accept.py's docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "quant" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if __name__ == "__main__":
    from ohcamel_quant.warehouse.accept import main

    raise SystemExit(main(sys.argv[1:]))
