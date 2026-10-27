#!/usr/bin/env python3
"""How long each book name goes without an IEX print during a session.

The engine calls a name stale when its last trade is older than a threshold
(Runtime's staleness_threshold, 90 s, or the book's own `stale_after_s` for
the name). On IEX -- the feed a free Alpaca plan streams -- a thin ETF prints
minutes apart in the regular session, so a threshold sized for AAPL reads it
stale most of the day. This script measures the silences, so the book's
thresholds rest on a number and not a guess: for every `(symbol X)` in a
book's positions it pulls the session's IEX trades and prints the median,
99th-percentile and longest gap between consecutive prints.

    ALPACA_API_KEY=... ALPACA_SECRET_KEY=... \
      python3 tools/iex_gaps.py --book book.example.sexp --date 2026-10-27

Alpaca's data API only; nothing is written. The window is 09:30-16:00 New
York unless --start/--end (UTC, ISO 8601) say otherwise. One request per
name, paginated; a name with fewer than two prints is reported as such.
The thresholds in book.example.sexp are tiers over the longest gap this
printed on 2026-10-27 (see the comment there).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SYMBOL = re.compile(r"\(symbol ([A-Z][A-Z0-9.]*)\)")
DATA = "https://data.alpaca.markets/v2/stocks/{symbol}/trades"


def book_symbols(path: str) -> list[str]:
    text = "\n".join(ln.split(";", 1)[0] for ln in open(path).read().splitlines())
    start = text.index("(positions")
    end = text.find("(limits", start)
    return SYMBOL.findall(text[start : end if end > 0 else len(text)])


def trades(symbol: str, start: str, end: str, key: str, secret: str) -> list[datetime]:
    out: list[datetime] = []
    token = None
    while True:
        q = {"feed": "iex", "start": start, "end": end, "limit": "10000"}
        if token:
            q["page_token"] = token
        req = urllib.request.Request(
            DATA.format(symbol=symbol) + "?" + urllib.parse.urlencode(q),
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
        for t in body.get("trades") or []:
            out.append(datetime.fromisoformat(t["t"].replace("Z", "+00:00")))
        token = body.get("next_page_token")
        if not token:
            return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", default="book.example.sexp")
    ap.add_argument("--date", default=date.today().isoformat(), help="the session, New York")
    ap.add_argument("--start", help="UTC ISO 8601; default the session's 09:30 New York")
    ap.add_argument("--end", help="UTC ISO 8601; default the session's 16:00 New York")
    args = ap.parse_args()
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        print("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set", file=sys.stderr)
        return 2
    day = date.fromisoformat(args.date)
    start = args.start or datetime.combine(day, time(9, 30), NY).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = args.end or datetime.combine(day, time(16, 0), NY).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for symbol in book_symbols(args.book):
        ts = sorted(trades(symbol, start, end, key, secret))
        if len(ts) < 2:
            rows.append((symbol, len(ts), None, None, None))
            continue
        gaps = sorted((b - a).total_seconds() for a, b in zip(ts[:-1], ts[1:], strict=True))

        def pick(q: float, gaps: list[float] = gaps) -> float:
            return gaps[min(len(gaps) - 1, int(q * len(gaps)))]

        rows.append((symbol, len(ts), pick(0.5), pick(0.99), gaps[-1]))
    rows.sort(key=lambda r: -(r[4] if r[4] is not None else float("inf")))
    print(f"IEX trades {start} .. {end}; gaps between consecutive prints, seconds")
    print(f"{'symbol':6} {'trades':>7} {'p50':>6} {'p99':>6} {'max':>6}")
    for symbol, n, p50, p99, mx in rows:
        if p50 is None:
            print(f"{symbol:6} {n:7d} {'-':>6} {'-':>6} {'-':>6}   fewer than two prints")
        else:
            print(f"{symbol:6} {n:7d} {p50:6.0f} {p99:6.0f} {mx:6.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
