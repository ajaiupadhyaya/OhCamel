"""The delisted-names acceptance test (completion task 3.8, spec R5): ``tools/accept_delisted.py``, or on the droplet
``python -m ohcamel_quant.warehouse accept-delisted``.

The spec takes EODHD's delisted coverage as unconfirmed until a committed list of known failures and acquisitions
comes back with prices up to the delisting. ``delisted_acceptance.csv`` is that list: 26 S&P 500 ever-members --
Lehman, Bear Stearns, Washington Mutual, SVB, First Republic, Bed Bath & Beyond and twenty more -- each with its last
trading date, the reason it stopped, and the notice the date was read from (``date_basis`` says whether the notice
states the date or the date is the session before a stated suspension). Eleven failed outright and Bear Stearns was
sold for scraps: names that went to zero or near it are what survivorship bias hides, so they are what the vendor is
tested on.

**The test.** For each name, ask the vendor for its bars from 30 calendar days before its last trading date to 30
after, under its ticker (:func:`judge`): it passes when there is a bar within ``TOLERANCE`` (5) weekdays of the last
trading date and none later than that. A series that runs on past the date is another company under the same
code, which is exactly the splice the test exists to catch, so it fails. The run passes when at least ``REQUIRED``
(24) of the 26 pass. On a pass it appends one ``delisted_acceptance`` row (``accepted_at``, the vendor, the counts,
the sha256 of the list, every name's result) and exits 0; otherwise it writes nothing and exits 1. That row is what
``readers.survivorship_free`` reads, and nothing else in the package writes it.

**If it fails:** cancel the subscription; the switch stays off; nothing else changes (docs/runbooks/compute.md).
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

ACCEPTANCE_LIST = Path(__file__).with_name("delisted_acceptance.csv")
TOLERANCE = 5
REQUIRED = 24
WINDOW_DAYS = 30


@dataclass(frozen=True)
class Known:
    ticker: str
    name: str
    last_trading_date: date
    reason: str
    kind: str
    date_basis: str
    sp500_evidence: str
    source_url: str


def load_acceptance_list(path: Path = ACCEPTANCE_LIST) -> list[Known]:
    body = [line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    out = []
    for r in csv.DictReader(body):
        out.append(Known(r["ticker"].strip().upper(), r["name"], date.fromisoformat(r["last_trading_date"]),
                         r["reason"], r["kind"], r["date_basis"], r["sp500_evidence"], r["source_url"]))
    return out


def acceptance_list_sha256(path: Path = ACCEPTANCE_LIST) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def judge(last_trading_date: date, dates: list[date]) -> tuple[bool, str]:
    """Pass when the vendor's last bar in the window is within ``TOLERANCE`` weekdays of the last trading date,
    on either side (module docstring). Returns the verdict and why."""
    if not dates:
        return False, "no bars around the last trading date"
    last = max(dates)
    gap = int(np.busday_count(min(last, last_trading_date), max(last, last_trading_date)))
    if last > last_trading_date and gap > TOLERANCE:
        return False, f"bars run on to {last}, {gap} weekdays past the last trading date: another company's series?"
    if gap > TOLERANCE:
        return False, f"last bar {last}, {gap} weekdays before the last trading date"
    return True, f"last bar {last} ({gap} weekday(s) from {last_trading_date})"


def vendor_dates(settings: Any = None) -> Callable[[Known], list[date]]:
    """The production fetch: the session dates EODHD returns for a known name around its last trading date."""
    from ..data import eodhd
    from ..data.base import DataUnavailable

    def fetch(k: Known) -> list[date]:
        try:
            bars = eodhd.bars(k.ticker, k.last_trading_date - timedelta(days=WINDOW_DAYS),
                              k.last_trading_date + timedelta(days=WINDOW_DAYS), delisted=True, settings=settings,
                              split_table=eodhd.parse_splits([]))   # the dates are judged, not the volume
        except DataUnavailable:
            return []
        return [d.date() for d in bars.index]

    return fetch


def run_acceptance(path: Path, fetch_dates: Callable[[Known], list[date]], *, source: str,
                   progress: Callable[[str], None] = lambda m: None) -> tuple[int, dict[str, Any]]:
    """Judge every known name and, on a pass, append the acceptance row. Returns ``(exit code, summary)``."""
    from .db import open_rw

    rows = load_acceptance_list()
    results = []
    for k in rows:
        ok, why = judge(k.last_trading_date, fetch_dates(k))
        results.append({"ticker": k.ticker, "last_trading_date": k.last_trading_date.isoformat(), "ok": ok,
                        "why": why})
        progress(f"{'PASS' if ok else 'FAIL'} {k.ticker:5s} {k.name}: {why}")
    passed = sum(r["ok"] for r in results)
    summary = {"passed": passed, "total": len(rows), "required": REQUIRED,
               "failed": [r for r in results if not r["ok"]], "list_sha256": acceptance_list_sha256()}
    if passed < REQUIRED:
        return 1, summary
    accepted_at = datetime.now(UTC).replace(tzinfo=None)
    with open_rw(path) as con:
        con.execute("INSERT INTO delisted_acceptance VALUES (?, ?, ?, ?, ?, ?)",
                    [accepted_at, source, passed, len(rows), summary["list_sha256"], json.dumps(results)])
    summary["accepted_at"] = accepted_at.isoformat()
    return 0, summary


def main(argv: list[str] | None = None) -> int:
    """Run against the configured vendor (EODHD_API_KEY) and the configured warehouse (OHCAMEL_QUANT_WAREHOUSE_PATH).
    Exit 0 accepted (recorded), 1 not accepted (nothing recorded), 2 not configured."""
    from ..config import get_settings
    from ..data.base import DataUnavailable
    from .db import warehouse_path

    s = get_settings()
    # The switch is NOT required: the test runs with only the key in place, so that a failure leaves the switch off
    # and nothing else changed (docs/runbooks/compute.md, Delisted names).
    if not s.eodhd_api_key:
        print("accept-delisted: FAILED -- EODHD_API_KEY is not set", file=sys.stderr)
        return 2
    path = warehouse_path(s)
    if path is None or not Path(path).is_file():
        print(f"accept-delisted: FAILED -- no warehouse at {path} (OHCAMEL_QUANT_WAREHOUSE_PATH)", file=sys.stderr)
        return 2
    try:
        code, summary = run_acceptance(Path(path), vendor_dates(s), source="eodhd",
                                       progress=lambda m: print(m, file=sys.stderr))
    except DataUnavailable as e:
        print(f"accept-delisted: FAILED -- {e}", file=sys.stderr)
        return 1
    verdict = "ACCEPTED" if code == 0 else "NOT ACCEPTED"
    print(f"accept-delisted: {verdict} -- {summary['passed']} of {summary['total']} known names have prices up to "
          f"their last trading date (needs {REQUIRED})")
    print(json.dumps({k: v for k, v in summary.items() if k != "failed"} | {"failed": [
        f"{r['ticker']}: {r['why']}" for r in summary["failed"]]}, indent=2))
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
