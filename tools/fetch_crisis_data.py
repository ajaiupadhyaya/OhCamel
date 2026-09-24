#!/usr/bin/env python3
"""Populate docs/crisis/*.csv with real daily closes for the crisis backtest.

WHY THIS IS A SCRIPT AND NOT PART OF THE ENGINE

Everything in lib/ runs; this runs once. Its whole output is checked into
docs/crisis/, so `make backtest-crisis` needs no network, no credentials and no
Python -- it reads three small CSVs that are in the repository. Putting an HTTP
client and a JSON parser into the OCaml library for an operation the engine
never performs would add a hundred lines nothing calls, which is a worse trade
than a documented provenance tool sitting outside the build.

WHERE THE DATA COMES FROM, AND WHAT THAT COSTS

Yahoo Finance's chart endpoint, which is keyless and public. It is also
unofficial: there is no contract, the shape can change, and it is not a source
anyone should build a live path on. That is precisely why the output is cached
and committed rather than fetched on demand -- the reproducibility of the
backtest does not depend on this endpoint still existing.

The obvious alternative was ruled out rather than skipped. Alpaca, which this
project already speaks to and which the roadmap originally specified, begins its
historical stock bars in 2016 -- so the 2008 window is unreachable through it at
any subscription tier. A crisis backtest that omitted the crisis everyone means
by the word would have been a weaker claim wearing a stronger name.

ADJUSTED CLOSES, NOT RAW

`adjclose` throughout. lib/feed/alpaca_rest.ml already argues this at length and
the argument is the same here: an unadjusted 2-for-1 split reads as a -50%
single-day return, which for a 60-day window at 95% confidence IS the entire
tail. VaR would report a 50% loss and hold it there for three months. AAPL split
7:1 in 2014 and 4:1 in 2020; NVDA split 4:1 in 2021 and 10:1 in 2024. Every
window below would be wrecked by raw closes, and wrecked in a way that looks
like a market event rather than a data bug.

USAGE

    python3 tools/fetch_crisis_data.py

Rewrites docs/crisis/*.csv in place. Review the diff before committing: a
changed number in a checked-in cache is a change to a published result.
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# The synthetic book's six names, in sorted order so the CSV column order is
# stable across runs and a diff shows data changes rather than dict ordering.
SYMBOLS = ["AAPL", "CVX", "JPM", "MSFT", "NVDA", "XOM"]

# Each window is the crisis PLUS a long enough run-up either side. That is not
# padding for its own sake: the estimator uses a rolling 60-day window, so the
# first 60 observations produce no forecast at all, and a coverage test on the
# handful that remain has no power to reject anything. Roughly 400-600 trading
# days per window leaves 350-550 scored forecasts, which is where Kupiec starts
# being able to tell a calibrated model from an uncalibrated one.
WINDOWS = [
    (
        "gfc",
        "2007-07-01",
        "2009-12-31",
        "Global financial crisis: the quant quake, Bear Stearns, Lehman, "
        "and the March 2009 bottom.",
    ),
    (
        "covid",
        "2019-06-01",
        "2020-12-31",
        "COVID crash: the fastest 30% drawdown on record, and the recovery.",
    ),
    (
        "rates-2022",
        "2021-06-01",
        "2022-12-31",
        "2022 rate shock: a slow grind rather than a spike -- the useful "
        "contrast to the other two.",
    ),
]

CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1={start}&period2={end}&interval=1d&events=div%2Csplit"
)


def epoch(date_string):
    return int(
        datetime.strptime(date_string, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def fetch(symbol, start, end):
    """Adjusted daily closes for one symbol, as {date_string: close}."""
    url = CHART_URL.format(symbol=symbol, start=epoch(start), end=epoch(end))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)

    result = payload["chart"]["result"]
    if not result:
        raise RuntimeError(f"{symbol}: no result in response")
    result = result[0]

    timestamps = result["timestamp"]
    adjclose = result["indicators"]["adjclose"][0]["adjclose"]

    series = {}
    for stamp, close in zip(timestamps, adjclose):
        # Yahoo emits nulls for halted or untraded sessions. Dropped rather than
        # forward-filled: a fabricated price produces a fabricated zero return,
        # which reads to a coverage test as a genuinely calm day.
        if close is None:
            continue
        day = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")
        series[day] = close
    return series


def write_window(name, start, end, description):
    print(f"  {name}: {start} to {end}")
    per_symbol = {}
    for symbol in SYMBOLS:
        series = fetch(symbol, start, end)
        print(f"    {symbol:<5} {len(series):>5} sessions")
        per_symbol[symbol] = series
        # Deliberately unhurried. This is a one-time populate against a free
        # public endpoint, and there is no reason to be the reason it starts
        # rate-limiting.
        time.sleep(1.0)

    # Only sessions every name traded. An inner join rather than a union,
    # because a covariance matrix needs its inputs aligned to the same days --
    # pairing one name's Monday with another's Wednesday produces a matrix of
    # pure fiction, which is the same argument graph.ml's aligned_returns makes.
    common = sorted(set.intersection(*(set(s) for s in per_symbol.values())))
    dropped = sorted(set.union(*(set(s) for s in per_symbol.values())) - set(common))
    if dropped:
        print(f"    dropped {len(dropped)} sessions not common to all six names")

    path = os.path.join("docs", "crisis", f"{name}.csv")
    with open(path, "w", newline="") as handle:
        handle.write(f"# {description}\n")
        handle.write(
            f"# Adjusted daily closes, {start} to {end}. "
            f"{len(common)} sessions common to all six names.\n"
        )
        handle.write(
            "# Source: Yahoo Finance chart endpoint, fetched by "
            "tools/fetch_crisis_data.py. Committed so this backtest reproduces "
            "with no network and no credentials.\n"
        )
        writer = csv.writer(handle)
        writer.writerow(["date"] + SYMBOLS)
        for day in common:
            writer.writerow(
                [day] + [f"{per_symbol[symbol][day]:.6f}" for symbol in SYMBOLS]
            )
    print(f"    wrote {path} ({len(common)} rows)")


def main():
    if not os.path.isdir("lib") or not os.path.isfile("dune-project"):
        sys.exit("run this from the repository root")
    os.makedirs(os.path.join("docs", "crisis"), exist_ok=True)
    print("fetching adjusted daily closes")
    for name, start, end, description in WINDOWS:
        try:
            write_window(name, start, end, description)
        except (urllib.error.URLError, KeyError, RuntimeError) as error:
            sys.exit(f"{name}: fetch failed ({error}). Nothing was written for it.")
    print("done. Review the diff before committing.")


if __name__ == "__main__":
    main()
