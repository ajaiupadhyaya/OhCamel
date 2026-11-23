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

(inside the worker image the keys are already in the environment and the warehouse path is the service's own.)
It writes the CSV (``ticker, tier, median_half_spread_bps, sessions, quotes, first_session, last_session``) and prints
the costs.yaml rows to paste, each ``source: measured``; commit the CSV beside costs.yaml. Nothing is written to the
warehouse. A name with no quotes in any sampled window is reported and keeps its provisional row.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
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


def quotes(symbol: str, start: datetime, end: datetime, key: str, secret: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    token = None
    while True:
        q = {"feed": "iex", "start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": "10000"}
        if token:
            q["page_token"] = token
        req = urllib.request.Request(QUOTES.format(symbol=urllib.parse.quote(symbol)) + "?" + urllib.parse.urlencode(q),
                                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
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
    ap.add_argument("--out", default="research/config/costs_measured.csv")
    args = ap.parse_args()
    key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        print("APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set", file=sys.stderr)
        return 2
    days = sessions(args.warehouse, args.sessions)
    if not days:
        print("the warehouse's bars_minute holds no sessions", file=sys.stderr)
        return 1
    model = costs.load()
    rows, yaml_rows = [], []
    for symbol in sorted(model.tickers):
        per_session, n_quotes = [], 0
        for d in days:
            hs: list[float] = []
            for w in WINDOWS:
                t0 = datetime.combine(d, w, NY)
                hs.extend(half_spreads_bps(quotes(symbol, t0, t0 + timedelta(minutes=1), key, secret)))
            n_quotes += len(hs)
            if hs:
                per_session.append(statistics.median(hs))
        if not per_session:
            print(f"{symbol}: no two-sided IEX quote in any sampled window; its provisional row stands")
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
    print(f"wrote {args.out}: {len(rows)} names over {len(days)} sessions {days[0]} .. {days[-1]}")
    print("costs.yaml rows:")
    print("\n".join(yaml_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
