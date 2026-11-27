#!/usr/bin/env python3
"""Measure each cost-file ETF's median IEX quoted half-spread, and the tier it rounds up to (completion task 2.3).

research/config/costs.yaml assigns every one of the site's 42 ETFs a half-spread tier (1, 3 or 8 bp). A tier is
MEASURED by this script: for the last ``--sessions`` sessions the warehouse's ``bars_minute`` stores (its distinct
New York dates), it pulls Alpaca's IEX quotes (``feed=iex``) for each name in one-minute windows spread over the
regular session, keeps quotes with ``0 < bid < ask``, takes each quote's half-spread ``(ask - bid) / 2 / mid`` in bp,
the median within each session, then the median over the sessions; the tier is the first one whose half-spread
covers that figure, rounded UP (``costs.tier_for``; above the widest tier the name gets none and is printed so).

Run on the droplet, read-only, with the project's own environment from the repository root:

    APCA_API_KEY_ID=... APCA_API_SECRET_KEY=... \\
      quant/.venv/bin/python tools/measure_spreads.py --warehouse /data/warehouse/warehouse.duckdb \\
        --sessions 20 --out research/config/costs_measured.csv

(inside the worker image the keys are already in the environment and the warehouse path is the service's own; the
image's file system is read-only, so ``--out`` defaults to /tmp there and the file is copied out with ``docker cp``.)
It writes the CSV (``ticker, tier, median_half_spread_bps, sessions, quotes, first_session, last_session``), a
provenance sidecar beside it (``<out>.meta.json``: the feed, the sampled windows, the session list, the code's sha and
the run's time, as the repository's other fetched files carry), and prints the costs.yaml rows to paste, each
``source: measured``; commit the CSV and its sidecar beside costs.yaml. Nothing is written to the warehouse. A name
with no quotes in any sampled window is reported and keeps its provisional row.

The output path is opened and checked BEFORE any request, so a read-only or missing directory fails at once rather
than after 42 names x 20 sessions x 5 windows of requests. Requests are paced (``--pace`` seconds apart, default
0.35: under Alpaca's 200 a minute) and a 429 or a 5xx is retried with exponential backoff (``Retry-After`` when the
server sends one), up to :data:`RETRIES` times, before the run fails.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time as clock
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SRC = Path(__file__).resolve().parents[1] / "quant" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ohcamel_quant.backtest import costs  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
QUOTES = "https://data.alpaca.markets/v2/stocks/{symbol}/quotes"
#: Minute windows sampled in each session, New York time: away from the open and close auctions.
WINDOWS = (time(10, 0), time(11, 30), time(13, 0), time(14, 30), time(15, 30))
RETRIES = 6          # attempts after the first, on a 429 or a 5xx
BACKOFF_S = 1.0      # the first wait; doubled each retry, capped at 60 s
FEED = "iex"
#: 2026-11-27: ``--feed sip`` reads the consolidated (SIP) quotes instead, which Alpaca serves free once they are
#: more than 15 minutes old -- every sampled window here is days old. IEX's own book overstates a name IEX quotes
#: thinly (USO measured 151 bp on it); the feed a run used is in its sidecar.
FEEDS = ("iex", "sip")
REPO = Path(__file__).resolve().parents[1]


def default_out() -> str:
    """research/config/costs_measured.csv in a writable checkout; /tmp/costs_measured.csv where the repository is
    read-only (the worker image)."""
    target = REPO / "research" / "config"
    return str(target / "costs_measured.csv") if os.access(target, os.W_OK) else "/tmp/costs_measured.csv"


def check_out(path: str) -> None:
    """Fail now, before any request, when ``path`` (or its sidecar) cannot be written."""
    out = Path(path)
    if not out.parent.is_dir():
        raise SystemExit(f"--out {path}: the directory {out.parent} does not exist")
    for p in (out, Path(f"{path}.meta.json")):
        if not os.access(p if p.exists() else p.parent, os.W_OK):
            raise SystemExit(f"--out {path}: {p} cannot be written; pass --out under /tmp")


class Paced:
    """At most one request per ``pace`` seconds, and a 429 or a 5xx retried with backoff."""

    def __init__(self, pace: float, sleep=clock.sleep, now=clock.monotonic) -> None:
        self.pace, self.sleep, self.now, self.last = pace, sleep, now, None

    def get(self, req: urllib.request.Request, opener=urllib.request.urlopen) -> dict:
        wait = BACKOFF_S
        for attempt in range(RETRIES + 1):
            if self.last is not None and (gap := self.pace - (self.now() - self.last)) > 0:
                self.sleep(gap)
            self.last = self.now()
            try:
                with opener(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if attempt == RETRIES or not (e.code == 429 or 500 <= e.code < 600):
                    raise
                after = e.headers.get("Retry-After") if e.headers else None
                self.sleep(float(after) if after and after.replace(".", "", 1).isdigit() else min(wait, 60.0))
                wait *= 2
        raise AssertionError("unreachable")


def code_sha() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("OHCAMEL_GIT_SHA") or None


def sessions(warehouse: str, n: int) -> list[date]:
    import duckdb

    con = duckdb.connect(warehouse, read_only=True)
    try:
        # ts is naive UTC and only the 09:30-16:00 New York session is stored (13:30-21:00 UTC at the latest):
        # every bar's UTC date is its New York session date.
        rows = con.execute("SELECT DISTINCT CAST(ts AS DATE) AS d FROM bars_minute ORDER BY d DESC LIMIT ?",
                           [n]).fetchall()
    finally:
        con.close()
    return sorted(d for (d,) in rows)


def quotes(symbol: str, start: datetime, end: datetime, key: str, secret: str,
           client: Paced | None = None, feed: str = FEED) -> list[tuple[float, float]]:
    client = client or Paced(0.0)
    out: list[tuple[float, float]] = []
    token = None
    while True:
        if feed not in FEEDS:
            raise ValueError(f"feed {feed!r} is not one of {FEEDS}")
        q = {"feed": feed, "start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": "10000"}
        if token:
            q["page_token"] = token
        req = urllib.request.Request(QUOTES.format(symbol=urllib.parse.quote(symbol)) + "?" + urllib.parse.urlencode(q),
                                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        body = client.get(req)
        for x in body.get("quotes") or []:
            out.append((float(x.get("bp") or 0.0), float(x.get("ap") or 0.0)))
        token = body.get("next_page_token")
        if not token:
            return out


def half_spreads_bps(pairs: list[tuple[float, float]]) -> list[float]:
    """``(ask - bid) / 2 / mid`` in bp for every two-sided quote (``0 < bid < ask``)."""
    return [(a - b) / (a + b) * 1e4 for b, a in pairs if 0 < b < a]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warehouse", required=True, help="the warehouse DuckDB file (opened read-only)")
    ap.add_argument("--sessions", type=int, default=20, help="the last N bars_minute sessions")
    ap.add_argument("--out", default=None, help="the CSV (default: research/config/costs_measured.csv, or /tmp "
                    "where the repository is read-only); its provenance goes to <out>.meta.json")
    ap.add_argument("--pace", type=float, default=0.35, help="seconds between requests (default 0.35)")
    ap.add_argument("--feed", choices=FEEDS, default=FEED, help="iex (default) or sip, the consolidated quotes")
    ap.add_argument("--tickers", default=None, help="comma-separated names to measure (default: every name in "
                    "costs.yaml); each must be in the cost file")
    args = ap.parse_args()
    args.out = args.out or default_out()
    check_out(args.out)
    key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        print("APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set", file=sys.stderr)
        return 2
    days = sessions(args.warehouse, args.sessions)
    if not days:
        print("the warehouse's bars_minute holds no sessions", file=sys.stderr)
        return 1
    model = costs.load()
    client = Paced(args.pace)
    started = datetime.now(UTC)
    rows, yaml_rows = [], []
    names = sorted(model.tickers)
    if args.tickers:
        names = sorted({t.strip().upper() for t in args.tickers.split(",") if t.strip()})
        unknown = [t for t in names if t not in model.tickers]
        if unknown:
            print(f"not in the cost file: {', '.join(unknown)}", file=sys.stderr)
            return 2
    for symbol in names:
        per_session, n_quotes = [], 0
        for d in days:
            hs: list[float] = []
            for w in WINDOWS:
                t0 = datetime.combine(d, w, NY)
                hs.extend(half_spreads_bps(quotes(symbol, t0, t0 + timedelta(minutes=1), key, secret, client,
                                                    feed=args.feed)))
            n_quotes += len(hs)
            if hs:
                per_session.append(statistics.median(hs))
        if not per_session:
            print(f"{symbol}: no two-sided {args.feed.upper()} quote in any sampled window; its provisional row stands")
            continue
        med = statistics.median(per_session)
        try:
            tier: int | str = costs.tier_for(med, model)
        except costs.NoCostTier:
            tier = "none"
        rows.append({"ticker": symbol, "tier": tier, "median_half_spread_bps": round(med, 4),
                     "sessions": len(per_session), "quotes": n_quotes, "first_session": days[0].isoformat(),
                     "last_session": days[-1].isoformat()})
        if tier != "none":
            yaml_rows.append(f"  {symbol + ':':5} {{tier: {tier}, source: measured}}")
        else:
            print(f"{symbol}: median half-spread {med:.2f} bp is wider than every tier -- the owner's decision")
    with open(args.out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["ticker"])
        wr.writeheader()
        wr.writerows(rows)
    meta = {"source": "alpaca-market-data", "synthetic": False, "feed": args.feed, "endpoint": QUOTES,
            "windows_new_york": [w.strftime("%H:%M") + "-" + (datetime.combine(date.min, w) + timedelta(minutes=1))
                                 .strftime("%H:%M") for w in WINDOWS],
            "sessions": [d.isoformat() for d in days], "names": len(rows), "cost_file_version": model.version,
            "code_sha": code_sha(), "run_started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_finished_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": "median over sessions of the median within a session of (ask - bid) / 2 / mid in bp, two-sided "
                      "quotes only (0 < bid < ask); tier rounded up (costs.tier_for)",
            "produced_by": "tools/measure_spreads.py"}
    Path(f"{args.out}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {args.out} and its .meta.json: {len(rows)} names over {len(days)} sessions {days[0]} .. {days[-1]}")
    print("costs.yaml rows:")
    print("\n".join(yaml_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
