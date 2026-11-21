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
trading date, and no bar at all on or before it fails. Prints after the date pass for a failure when they continue
without a gap (the same company carrying on, e.g. an over-the-counter continuation the vendor stitched onto the
code); for an acquisition, whose ticker never continues, prints running more than 5 weekdays past the date fail. Prints that resume after a gap of more than 5
weekdays are another company under the same code, the splice the test exists to catch, and fail. The run passes
when at least ``REQUIRED`` (24) of the 26 pass. On a pass it appends one ``delisted_acceptance`` row (``accepted_at``, the vendor, the counts,
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


def judge(last_trading_date: date, dates: list[date], *, kind: str = "failure") -> tuple[bool, str]:
    """The verdict on one name's bars around its last trading date ``L`` (module docstring), and why.

    * No bar on or before ``L``: fail -- the series does not hold the company's prices up to its delisting.
    * Bars ending on or before ``L``: pass when the last is within ``TOLERANCE`` weekdays of ``L``.
    * Bars after ``L``: the last bar on or before ``L`` must be within ``TOLERANCE`` weekdays of ``L``. A gap of more
      than ``TOLERANCE`` weekdays before the first print after ``L`` is another company under the code (a reused
      ticker): fail. Otherwise the run past ``L`` is continuous. For a ``failure`` that is the same company carrying
      on, as when a vendor stitches the over-the-counter continuation (ENDPQ, LEHMQ, SIVBQ) onto the exchange code,
      and it passes, the verdict saying it ran on. For an ``acquisition`` the ticker never continues, so prints
      running more than ``TOLERANCE`` weekdays past ``L`` fail (fix round 3)."""
    if not dates:
        return False, "no bars around the last trading date"
    ds = sorted(set(dates))
    before = [d for d in ds if d <= last_trading_date]
    if not before:
        return False, f"no prices on or before the last trading date {last_trading_date} (first bar {ds[0]})"
    after = [d for d in ds if d > last_trading_date]
    if after:
        gap = int(np.busday_count(before[-1], after[0]))
        if gap > TOLERANCE:
            return False, (f"prints resume {after[0]} after a gap of {gap} weekdays from {before[-1]}, past the last "
                           "trading date: another company under the code?")
        if int(np.busday_count(before[-1], last_trading_date)) > TOLERANCE:
            return False, f"no bar within {TOLERANCE} weekdays before the last trading date (last {before[-1]})"
        if kind != "failure" and int(np.busday_count(last_trading_date, ds[-1])) > TOLERANCE:
            return False, (f"prints run on to {ds[-1]}, more than {TOLERANCE} weekdays past the last trading date "
                           f"{last_trading_date}, for an {kind}: an acquired company's code does not continue")
        return True, (f"ran on to {ds[-1]} without a gap past the last trading date {last_trading_date}: the same "
                      "company carrying on (an over-the-counter continuation)")
    last = ds[-1]
    gap = int(np.busday_count(last, last_trading_date))
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
    """Judge every known name and, on a pass, append the acceptance row. Returns ``(exit code, summary)``: 0 passed
    and recorded, 1 not passed (nothing recorded), 3 passed but NOT recorded (the warehouse stayed busy past the
    writer's wait: run it again -- this is not a failure, and it is not acceptance either)."""
    from .db import WarehouseUnavailable, open_rw

    rows = load_acceptance_list()
    results = []
    for k in rows:
        ok, why = judge(k.last_trading_date, fetch_dates(k), kind=k.kind)
        results.append({"ticker": k.ticker, "last_trading_date": k.last_trading_date.isoformat(), "ok": ok,
                        "why": why})
        progress(f"{'PASS' if ok else 'FAIL'} {k.ticker:5s} {k.name}: {why}")
    passed = sum(r["ok"] for r in results)
    summary = {"passed": passed, "total": len(rows), "required": REQUIRED,
               "failed": [r for r in results if not r["ok"]], "list_sha256": acceptance_list_sha256()}
    if passed < REQUIRED:
        return 1, summary
    accepted_at = datetime.now(UTC).replace(tzinfo=None)
    try:
        with open_rw(path) as con:
            con.execute("INSERT INTO delisted_acceptance (accepted_at, source, passed, total, list_sha256, detail) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [accepted_at, source, passed, len(rows), summary["list_sha256"], json.dumps(results)])
    except WarehouseUnavailable as e:
        summary |= {"recorded": False, "why": str(e)}
        return 3, summary
    summary |= {"recorded": True, "accepted_at": accepted_at.isoformat()}
    return 0, summary


def main(argv: list[str] | None = None) -> int:
    """Run against the configured vendor (EODHD_API_KEY) and the configured warehouse (OHCAMEL_QUANT_WAREHOUSE_PATH).
    Exit 0 accepted (recorded), 1 not accepted (nothing recorded), 2 not configured, 3 passed but not recorded
    (the warehouse was busy: run it again)."""
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
    if code == 3:
        print(f"accept-delisted: PASSED, NOT RECORDED -- {summary['passed']} of {summary['total']} known names passed, "
              f"but the warehouse was busy ({summary['why']}): run it again. This is not a failure.")
        return code
    verdict = "ACCEPTED" if code == 0 else "NOT ACCEPTED"
    print(f"accept-delisted: {verdict} -- {summary['passed']} of {summary['total']} known names have prices up to "
          f"their last trading date (needs {REQUIRED})")
    print(json.dumps({k: v for k, v in summary.items() if k != "failed"} | {"failed": [
        f"{r['ticker']}: {r['why']}" for r in summary["failed"]]}, indent=2))
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
