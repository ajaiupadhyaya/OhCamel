"""``ops.data_integrity``: every daily series, checked nightly for the defects that have actually happened here.

EXP-K01 found an unadjusted 2-for-1 split (XLE and XLK, 2025-12-05) and a decimal-shift low (SPY, 2026-02-02)
sitting in committed data for months. ``data/fixtures.py`` repaired both at load time for the API, but anything
that read the files directly scored against a fictitious crash. This job looks for that class of defect in
the warehouse and in the committed fixtures every night, and names each finding (ticker, session, check).

**What fails the lamp.** A defect in the committed fixtures, at any date: they are static, published inputs.
In the warehouse, only a defect in the last ``RECENT_BDAYS`` business days: a new bar that arrived wrong.
Older warehouse findings are listed, marked ``gating = False``. The first run on the droplet showed why:
across ~560 single stocks since 1990, a 25% intraday range or a split-shaped gap is often real (APA fell 54% on
2020-03-09; the 2010 flash crash printed absurd lows), and a lamp that is always red is a lamp nobody reads.

It repairs nothing. A finding is a lamp, not a fix: changing a published input is the owner's decision, and
the archive under ``fixtures/archive/`` is how a repair is recorded. Checks, all on the series' own evidence:

- ``split_suspect``: a session whose close-to-close move is 30% or more AND whose overnight ratio
  (previous close over open) is within 3% of a standard split ratio, the rule ``data/fixtures.py`` uses. The
  adjusted close is checked separately: a split in ``close`` that ``adj_close`` also shows was never adjusted.
- ``jump``: an adjusted move of 40% or more with no split signature. Possibly real, so it is shown for a look.
- ``bad_print``: a high above 1.25x or a low below 0.75x the session body (open/close).
- ``nonpositive``: a price at or below zero, or negative volume.
- ``calendar``: duplicate or unsorted sessions.
- ``stale``: the last bar is more than 5 business days older than the newest bar of any series checked.
- ``adjust_drift`` (completion task 3.2, warehouse only): the adjusted close DERIVED from ``bars_raw`` and
  ``corp_actions`` (``warehouse/adjust.py``) against the vendor's ``bars_daily.adj_close``, flagged when they
  differ by more than ``DRIFT_RTOL`` (1e-6) relative. One finding per series, dated on the LAST session beyond
  the tolerance: a backward adjustment by an action dated E moves every bar before E, so an action missing
  from (or extra in) ``corp_actions`` shows as drift up to the session before E. Series with no raw bars yet,
  or no full corporate-action history yet (the backfill is incomplete), are counted and named in a note,
  never reported as drift.

  *What gates.* Unlike a large move, an adjustment drift is never a real market event, so the recency rule
  alone would be wrong: an old missing split is a defect today. A drift finding gates when it is inside the
  last ``RECENT_BDAYS`` sessions OR its size exceeds ``DRIFT_VENDOR_BAND`` (5e-6) at any date. The band is
  Yahoo's own precision, measured (task-3.2 report; adjust.py): its float32 prices and rounded amounts wander
  from the exact convention by at most 4.2e-7 on 2020-onward slices and 1.0-1.7e-6 on 1990-onward histories
  (SPY 1.1e-6, XOM 1.0e-6, KO 1.1e-6, TLT 1.7e-6 over 290 dividends); 5e-6 is three times the worst seen,
  and a missing dividend of even 0.1% yield is 200 times it. So old drift under the band is listed and does
  not gate; ``adjust_max_drift`` in the summary (the largest non-excepted drift over every compared series,
  flagged or not) shows the band holding, or not, every night.

  *Exceptions.* ``DRIFT_EXCEPTIONS`` names (ticker, session, reason) for bars where the vendor's convention
  is known to differ from adjust.py's: each is REPORTED as its own non-gating finding carrying the reason and
  its size, and is left out of the series' drift and of ``adjust_max_drift``. Nothing is absorbed unnamed.

  *What it can prove.* For Yahoo-served series the raw bars are rebuilt from Yahoo's own split list, so the
  split part of the comparison is an identity: a split Yahoo never lists is invisible here (``split_suspect``
  and ``split_unconfirmed`` are the backstop). What it does test is every dividend, every split that
  ``corp_actions`` and the bars' response disagree on, and Alpaca's raw prints against its adjusted series.
- ``split_unconfirmed``: a ``split_suspect`` session of a warehouse series whose corporate-action history covers
  it but records no split that day -- the page repair (``data/splits.py``) divides that history by a split
  nobody recorded, which may be a real gap. Shown for a look; the ``split_suspect`` finding itself gates.

Completion task 3.6 widened the look (spec, Operations: missing sessions, flat or zero-volume bars, a vendor
change inside a series, and stale minute, option and FRED data):

- ``missing_session`` (warehouse): a session of the exchange calendar (Alpaca's ``/v2/calendar``, cached in
  ``exchange_calendar``) inside the series' own range -- first bar to last, where the calendar covers it -- with
  no bar. A hole, never a market event, except a single name halted all day. Gates when recent, like the
  checks above; older holes are listed (up to ``MISSING_LISTED`` a series, the rest counted in one row). With
  no calendar cached the check is not run and a note says so: weekdays would call every holiday a hole.
- ``flat_bar``: on a tier-1 name (``TIER1``: the broad and sector ETFs, which trade every session), a bar with
  no volume or with open = high = low = close -- a vendor's filler. Tier-1 only: a thin single stock can
  have a real empty day. Gates when recent (warehouse) or at any date (fixtures), like the checks above.
- ``vendor_change`` (warehouse): ``bars_daily.source`` changes from one session to the next. The bars ingest
  serves one vendor per ticker and replaces a series whole when its vendor changes, so a mixed series is two
  vendors' adjustment conventions stitched together. Dated on the first session of the new vendor; gates when
  recent.
- ``stale_minute``, ``stale_options``, ``stale_fred`` (warehouse): ``bars_minute``, ``option_snapshots`` and
  ``fred`` as ``warehouse/freshness.py`` judges them on the same calendar -- the dataset stale (one row, ticker
  ``*``), else a key behind it (a minute ticker or an option underlying more than one session behind the
  expected session, a FRED series whose last fetch failed). Shown, never gating, like ``stale``: the Watch
  page's freshness lamps already carry a stale feed, and this lamp is for defects in the data.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..data import fixtures as F
from ..data.base import DataUnavailable
from ..data.splits import SPLIT_RATIOS
from ..jobs.artifacts import ArtifactSpec
from ..warehouse.ingest.options import BROAD, SECTORS

KIND = "ops.data_integrity"
JUMP = 0.40
STALE_BDAYS = 5
RECENT_BDAYS = 10
DRIFT_RTOL = 1e-6
DRIFT_VENDOR_BAND = 5e-6
DRIFT_LISTED = 25
#: (ticker, session, reason): bars where Yahoo's convention is known to differ from warehouse/adjust.py's.
DRIFT_EXCEPTIONS: tuple[tuple[str, str, str], ...] = (
    ("KO", "2001-09-10", "the 2001-09-12 ex-date fell while the market was shut after 9/11 (Yahoo also lists "
                         "0.09 on 09-17): Yahoo leaves this session unadjusted for it, adjust.py's rule 3 "
                         "adjusts every session before the ex-date; one bar, about 0.7%"),
)
NAMES_LISTED = 12
#: Tier-1 names for ``flat_bar``: the broad and sector ETFs, which print volume every session.
TIER1: frozenset[str] = frozenset(BROAD + SECTORS)
#: Old ``missing_session`` rows listed one by one per series; the rest are counted in one row.
MISSING_LISTED = 5
#: The datasets the stale_* checks read from warehouse/freshness.py, and the check each one names.
STALE_FEEDS = {"bars_minute": "stale_minute", "option_snapshots": "stale_options", "fred": "stale_fred"}
SERIOUS = ("split_suspect", "bad_print", "nonpositive", "calendar", "adjust_drift", "missing_session", "flat_bar",
           "vendor_change")
COLUMNS = ["source", "ticker", "date", "check", "detail", "gating"]


def check_bars(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Findings on one daily series (``open, high, low, close, adj_close, volume`` on a date index)."""
    out: list[dict[str, Any]] = []

    def add(day: Any, check: str, detail: str) -> None:
        out.append({"date": str(pd.Timestamp(day).date()) if day is not None else "", "check": check,
                    "detail": detail})

    idx = pd.DatetimeIndex(df.index)
    if not idx.is_monotonic_increasing:
        add(None, "calendar", "sessions are not sorted")
    for d in idx[idx.duplicated()]:
        add(d, "calendar", "duplicate session")
    px = df[["open", "high", "low", "close", "adj_close"]]
    for d in df.index[(px <= 0).any(axis=1)]:
        add(d, "nonpositive", "a price at or below zero")
    for d in df.index[df["volume"] < 0]:
        add(d, "nonpositive", "negative volume")
    prev = df["close"].shift(1)
    jump = df["close"] / prev - 1.0
    adj_jump = df["adj_close"] / df["adj_close"].shift(1) - 1.0
    overnight = prev / df["open"]
    split_days = set()
    for d in df.index[(jump.abs() >= 0.30).fillna(False)]:
        ratio = float(overnight.loc[d])
        match = min(SPLIT_RATIOS, key=lambda r: abs(ratio / r - 1.0))
        if abs(ratio / match - 1.0) <= 0.03 and abs(float(adj_jump.loc[d])) >= 0.30:
            split_days.add(d)
            add(d, "split_suspect", f"close moved {jump.loc[d]:+.1%} with an overnight ratio of {ratio:.3f} "
                                    f"(~{match:g}:1) and adj_close did not absorb it ({adj_jump.loc[d]:+.1%})")
    for d in df.index[(adj_jump.abs() >= JUMP).fillna(False)]:
        if d not in split_days:
            add(d, "jump", f"adjusted move {adj_jump.loc[d]:+.1%} with no split signature")
    lo = df[["open", "close"]].min(axis=1)
    hi = df[["open", "close"]].max(axis=1)
    for d in df.index[(df["low"] < 0.75 * lo) | (df["high"] > 1.25 * hi)]:
        add(d, "bad_print", f"high {df.loc[d, 'high']:g} / low {df.loc[d, 'low']:g} against a body of "
                            f"{lo.loc[d]:g}-{hi.loc[d]:g}")
    return out


def missing_sessions(index: pd.DatetimeIndex, sessions: Any) -> list[dict[str, Any]]:
    """``missing_session`` findings: each of ``sessions`` (the calendar's, in its coverage) inside the series' range
    -- its first bar to its last, clipped to the sessions' own first and last -- with no bar."""
    have = pd.DatetimeIndex(index).normalize()
    cal = pd.DatetimeIndex(sorted(pd.Timestamp(d) for d in sessions))
    if have.empty or cal.empty:
        return []
    lo, hi = max(have.min(), cal[0]), min(have.max(), cal[-1])
    want = cal[(cal >= lo) & (cal <= hi)]
    gone = want.difference(have)
    return [{"date": str(d.date()), "check": "missing_session",
             "detail": "the exchange calendar has a session here, inside the series' range, and the series no bar"}
            for d in gone]


def flat_bars(df: pd.DataFrame, ticker: str) -> list[dict[str, Any]]:
    """``flat_bar`` findings on a tier-1 name: no volume, or open = high = low = close."""
    if ticker not in TIER1:
        return []
    zero = df["volume"] == 0
    flat = (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])
    out = []
    for d in df.index[zero | flat]:
        what = " and ".join(w for w, hit in (("no volume", bool(zero.loc[d])),
                                             ("open = high = low = close", bool(flat.loc[d]))) if hit)
        out.append({"date": str(pd.Timestamp(d).date()), "check": "flat_bar",
                    "detail": f"{what} on a tier-1 name, which trades every session: a vendor's filler bar"})
    return out


def vendor_changes(con: Any, tickers: list[str]) -> list[dict[str, Any]]:
    """``vendor_change`` findings: ``bars_daily.source`` differs from the session before, per ticker."""
    if not tickers:
        return []
    rows = con.execute(
        "SELECT ticker, date, prev, source FROM (SELECT ticker, date, source, lag(source) OVER "
        "(PARTITION BY ticker ORDER BY date) AS prev FROM bars_daily WHERE list_contains(?, ticker)) "
        "WHERE prev IS NOT NULL AND source IS DISTINCT FROM prev ORDER BY ticker, date", [tickers]).fetchall()
    return [{"ticker": t, "date": str(pd.Timestamp(d).date()), "check": "vendor_change",
             "detail": f"the series changes vendor here, {prev} to {src}: two vendors' adjustments in one series "
                       f"(the bars ingest replaces a series whole when its vendor changes)"}
            for t, d, prev, src in rows]


def stale_feeds(con: Any, now: Any) -> list[dict[str, Any]]:
    """``stale_minute``, ``stale_options``, ``stale_fred`` (module docstring), from warehouse/freshness.py."""
    from ..warehouse import freshness

    fr = freshness.dataset_freshness(con, now)["datasets"]
    cal = freshness.load_calendar(con)
    out: list[dict[str, Any]] = []
    for ds, check in STALE_FEEDS.items():
        f = fr[ds]
        if f["stale"]:
            why = ("its option-table move (migration 003) is unfinished" if f.get("migrating") else
                   f"last good run {f['last_ok_at'] or 'never'}, data through {f['data_asof'] or 'nothing'}")
            out.append({"ticker": "*", "date": f["data_asof"] or "", "check": check, "detail": f"{ds} is stale: {why}"})
            continue
        latest = con.execute(
            "SELECT key, arg_max(status, ran_at), max(data_asof) FILTER (WHERE status IN ('ok', 'partial')) "
            "FROM ingest_log WHERE dataset = ? AND key <> '*' GROUP BY key ORDER BY key", [ds]).fetchall()
        rule = freshness.FRESHNESS[ds]
        if rule["rule"] == "session":
            exp = freshness.expected_session(now, rule["due"], cal)
            floor = freshness._prev_session(exp, cal)
            for key, _, asof in latest:
                if asof is None or asof < floor:
                    out.append({"ticker": key, "date": str(asof) if asof else "", "check": check,
                                "detail": f"{ds}: this key's data runs through {asof or 'nothing'}, more than one "
                                          f"session behind the expected {exp}"})
        else:
            for key, status, asof in latest:
                if status == "failed":
                    out.append({"ticker": key, "date": str(asof) if asof else "", "check": check,
                                "detail": f"{ds}: this series' last fetch failed; its data runs through "
                                          f"{asof or 'nothing'}"})
    return out


def _clip_missing(ticker: str, found: list[dict[str, Any]], recent: pd.Timestamp | None) -> list[dict[str, Any]]:
    """Every recent hole; the old ones up to MISSING_LISTED, the rest in one counted row."""
    new = [f for f in found if recent is not None and pd.Timestamp(f["date"]) >= recent]
    old = [f for f in found if f not in new]
    if len(old) <= MISSING_LISTED:
        return [*old, *new]
    rest = old[MISSING_LISTED:]
    summary = {"date": rest[-1]["date"], "check": "missing_session",
               "detail": f"{len(rest)} more sessions missing from {rest[0]['date']} to {rest[-1]['date']} "
                         f"(the first {MISSING_LISTED} are listed)"}
    return [*old[:MISSING_LISTED], summary, *new]


def stale(last: dict[str, pd.Timestamp], bdays: int = STALE_BDAYS) -> list[dict[str, Any]]:
    if not last:
        return []
    newest = max(last.values())
    cut = newest - pd.offsets.BDay(bdays)
    return [{"ticker": t, "date": str(d.date()), "check": "stale",
             "detail": f"last bar {d.date()}, newest across series {newest.date()}"} for t, d in last.items() if d < cut]


def _coverage(con: Any, tickers: list[str]) -> dict[str, pd.Timestamp]:
    """Per ticker, the earliest window start of a successful ``ingest.corp_actions`` fetch: its corporate-action
    history is known from there on."""
    rows = con.execute("SELECT key, min(CAST(json_extract_string(detail, '$.window_start') AS DATE)) FROM ingest_log "
                       "WHERE dataset = 'corp_actions' AND status = 'ok' AND list_contains(?, key) GROUP BY key",
                       [tickers]).fetchall()
    return {k: pd.Timestamp(d) for k, d in rows if d is not None}


def adjust_drift(con: Any, tickers: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``adjust_drift`` findings (all of them, largest first) and counts (module docstring)."""
    from ..warehouse import adjust

    counts: dict[str, Any] = {"compared": 0, "no_raw": [], "no_actions": [], "not_comparable": [], "raw_gaps": [],
                              "failed": [], "max_drift": float("nan"), "excepted": []}
    out: list[dict[str, Any]] = []
    if not tickers:
        return out, counts
    cover = _coverage(con, tickers)
    except_at = {(t, pd.Timestamp(d)): why for t, d, why in DRIFT_EXCEPTIONS}
    for t in tickers:
        raw = adjust.read_raw(con, t)
        if raw.empty:
            counts["no_raw"].append(t)
            continue
        vendor = con.execute("SELECT date, adj_close, source FROM bars_daily WHERE ticker = ? ORDER BY date", [t]).df()
        if vendor.empty:
            continue
        if str(vendor["source"].iloc[-1]) == "stooq":       # Stooq's adj_close is its close: no dividends to compare
            counts["not_comparable"].append(t)
            continue
        if t not in cover or cover[t] > raw.index[0]:
            counts["no_actions"].append(t)
            continue
        try:
            derived = adjust.series(raw, adjust.read_actions(con, t), dividends=True)["close"]
        except ValueError as e:
            counts["failed"].append(f"{t} ({e})")
            continue
        v = pd.Series(vendor["adj_close"].to_numpy(float), index=pd.DatetimeIndex(pd.to_datetime(vendor["date"])))
        if not v.index.isin(derived.index).all():
            counts["raw_gaps"].append(t)
        common = v.index.intersection(derived.index)
        rel = (derived.loc[common] / v.loc[common] - 1.0).abs()
        counts["compared"] += 1
        excepted = [d for d in rel.index if (t, d) in except_at and rel.loc[d] > DRIFT_RTOL]
        for d in excepted:
            counts["excepted"].append(f"{t} {d.date()}")
            out.append({"ticker": t, "date": str(d.date()), "check": "adjust_drift", "size": float(rel.loc[d]),
                        "excepted": True,
                        "detail": f"excepted bar, drift {rel.loc[d]:.2e} relative: {except_at[(t, d)]}"})
        rel = rel.drop(excepted)
        if len(rel):
            counts["max_drift"] = float(np.nanmax([counts["max_drift"], rel.max()]))
        beyond = rel[rel > DRIFT_RTOL]
        if beyond.empty:
            continue
        first, last, worst = beyond.index[0].date(), beyond.index[-1].date(), rel.idxmax().date()
        out.append({"ticker": t, "date": str(last), "check": "adjust_drift", "size": float(rel.max()),
                    "excepted": False,
                    "detail": f"adj_close derived from raw bars and corp_actions differs from the vendor's by up to "
                              f"{rel.max():.2e} relative (on {worst}); {len(beyond)} of {len(rel)} sessions beyond "
                              f"{DRIFT_RTOL:g}, {first} to {last}: an action missing from or extra in corp_actions "
                              f"on the session after {last} shows this way"})
    out.sort(key=lambda r: (r["excepted"], -r["size"], r["ticker"]))
    return out, counts


def drift_gates(r: dict[str, Any], recent: pd.Timestamp | None) -> bool:
    """An ``adjust_drift`` finding gates when recent or beyond the vendor band; an excepted bar never does."""
    if r.get("excepted"):
        return False
    return r["size"] > DRIFT_VENDOR_BAND or (recent is not None and pd.Timestamp(r["date"]) >= recent)


def split_unconfirmed(con: Any, suspects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``split_unconfirmed`` findings for warehouse ``split_suspect`` rows (module docstring)."""
    if not suspects:
        return []
    tickers = sorted({r["ticker"] for r in suspects})
    cover = _coverage(con, tickers)
    recorded = {(k, str(d)) for k, d in con.execute(
        "SELECT ticker, ex_date FROM corp_actions WHERE kind = 'split' AND list_contains(?, ticker)",
        [tickers]).fetchall()}
    out = []
    for r in suspects:
        t, d = r["ticker"], r["date"]
        if t in cover and cover[t] <= pd.Timestamp(d) and (t, d) not in recorded:
            out.append({"source": "warehouse", "ticker": t, "date": d, "check": "split_unconfirmed",
                        "detail": "corp_actions records no split on this session although its history covers it: "
                                  "the page repair divides the history before it by a split nobody recorded"})
    return out


def warehouse_sessions(con: Any) -> list[pd.Timestamp] | None:
    """The cached exchange calendar's sessions, or None when there is none (or no table: before migration 005)."""
    from ..warehouse.calendar import load_calendar

    try:
        cal = load_calendar(con)
    except DataUnavailable:
        return None
    return None if cal is None else [pd.Timestamp(d) for d in cal.days]


def _names(xs: list[str]) -> str:
    return ", ".join(xs[:NAMES_LISTED]) + (f" and {len(xs) - NAMES_LISTED} more" if len(xs) > NAMES_LISTED else "")


def _warehouse_tickers(ctx: Any) -> list[str]:
    try:
        return [r[0] for r in ctx.warehouse.execute("SELECT DISTINCT ticker FROM bars_daily ORDER BY 1").fetchall()]
    except DataUnavailable:
        return []


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    checked = {"warehouse": 0, "fixtures": 0}
    tickers = params.get("tickers") or _warehouse_tickers(ctx)
    if not tickers:
        notes.append("no warehouse configured or empty: the committed fixtures are checked alone")
    last: dict[str, pd.Timestamp] = {}
    holes: dict[str, list[dict[str, Any]]] = {}
    if tickers:
        from ..warehouse import readers

        sessions = warehouse_sessions(ctx.warehouse)
        if sessions is None:
            notes.append("missing_session not checked: no exchange calendar is cached in the warehouse (the nightly "
                         "bars run fetches Alpaca's /v2/calendar into exchange_calendar); weekdays would call every "
                         "holiday a hole")
        for i, t in enumerate(tickers):
            ctx.check_cancelled()
            try:
                df = readers.ohlcv(ctx.warehouse, t).data
            except DataUnavailable:
                continue
            checked["warehouse"] += 1
            last[t] = pd.Timestamp(df.index[-1])
            rows += [{"source": "warehouse", "ticker": t, **f} for f in check_bars(df)]
            rows += [{"source": "warehouse", "ticker": t, **f} for f in flat_bars(df, t)]
            if sessions is not None:
                found = missing_sessions(pd.DatetimeIndex(df.index), sessions)
                if found:
                    holes[t] = found
            if i % 50 == 0:
                ctx.progress((i + 1) / len(tickers), t)
        rows += [{"source": "warehouse", **f} for f in stale(last)]
    drift: list[dict[str, Any]] = []
    counts: dict[str, Any] = {}
    if last:
        import duckdb

        try:
            suspects = [r for r in rows if r["source"] == "warehouse" and r["check"] == "split_suspect"]
            rows += split_unconfirmed(ctx.warehouse, suspects)
            drift, counts = adjust_drift(ctx.warehouse, list(last))
        except duckdb.CatalogException as e:
            notes.append(f"adjust_drift not run: the warehouse predates migration 002 ({e}); the next ingest migrates it")
        newest_w = max(last.values())
        recent_w = newest_w - pd.offsets.BDay(int(params.get("recent_bdays", RECENT_BDAYS)))
        for t, found in holes.items():
            rows += [{"source": "warehouse", "ticker": t, **f} for f in _clip_missing(t, found, recent_w)]
        try:
            rows += [{"source": "warehouse", **f} for f in vendor_changes(ctx.warehouse, list(last))]
            from ..warehouse import freshness

            rows += [{"source": "warehouse", **f} for f in stale_feeds(ctx.warehouse, freshness._now())]
        except duckdb.CatalogException as e:
            notes.append(f"vendor_change and stale_* not run: the warehouse lacks a table they read ({e})")
        for r in drift:
            r["gating"] = drift_gates(r, recent_w)
        listed = [r for r in drift if r["excepted"]]
        flagged = sorted((r for r in drift if not r["excepted"]), key=lambda r: (not r["gating"], -r["size"]))
        if len(flagged) > DRIFT_LISTED:
            notes.append(f"adjust_drift: {len(flagged) - DRIFT_LISTED} more series beyond {DRIFT_RTOL:g} not listed "
                         f"(the gating ones first, then the {DRIFT_LISTED} largest)")
        rows += [{"source": "warehouse", **{k: v for k, v in r.items() if k not in ("size", "excepted")}}
                 for r in [*flagged[:DRIFT_LISTED], *listed]]
        if counts.get("no_raw"):
            notes.append(f"adjust_drift: {len(counts['no_raw'])} series have no raw bars yet (the bars_raw backfill "
                         f"has not reached them): {_names(counts['no_raw'])}")
        if counts.get("no_actions"):
            notes.append(f"adjust_drift: {len(counts['no_actions'])} series have no corporate-action history from "
                         f"their first raw bar yet (ingest.corp_actions full backfill): {_names(counts['no_actions'])}")
        if counts.get("not_comparable"):
            notes.append(f"adjust_drift: {len(counts['not_comparable'])} series come from Stooq, whose adj_close carries "
                         f"no dividends: {_names(counts['not_comparable'])}")
        if counts.get("raw_gaps"):
            notes.append(f"adjust_drift: {len(counts['raw_gaps'])} series have sessions in bars_daily with no raw bar "
                         f"(compared where both exist): {_names(counts['raw_gaps'])}")
        if counts.get("failed"):
            notes.append(f"adjust_drift: the actions of {len(counts['failed'])} series could not be applied: "
                         f"{_names(counts['failed'])}")
    root = F._root() / "history"
    for p in sorted(root.glob("*.parquet")):
        df = pd.read_parquet(p)
        if hasattr(ctx, "read"):                                   # recorded: the committed fixtures (Task 3.3)
            ctx.read(f"fixture: history/{p.name}", df, source="fixture")
        df = df.rename(columns={"close_adj": "adj_close"})
        df.index = pd.DatetimeIndex(df.index)
        checked["fixtures"] += 1
        rows += [{"source": "fixtures", "ticker": p.stem, **f} for f in check_bars(df)]
        rows += [{"source": "fixtures", "ticker": p.stem, **f} for f in flat_bars(df, p.stem)]
    newest = max(last.values()) if last else None
    recent = (newest - pd.offsets.BDay(int(params.get("recent_bdays", RECENT_BDAYS)))) if newest is not None else None

    def gates(r: dict[str, Any]) -> bool:
        if "gating" in r:                    # adjust_drift decided its own (drift_gates)
            return bool(r["gating"])
        if r["check"] not in SERIOUS:
            return False
        if r["source"] == "fixtures" or not r["date"]:
            return True
        return recent is not None and pd.Timestamp(r["date"]) >= recent

    rows = [{**r, "gating": gates(r)} for r in rows]
    findings = pd.DataFrame(rows, columns=COLUMNS)
    serious = findings[findings["gating"].astype(bool)]
    by = findings.groupby("check").size().to_dict() if len(findings) else {}
    history = int((findings["check"].isin(SERIOUS) & ~findings["gating"].astype(bool)).sum()) if len(findings) else 0
    summary = pd.DataFrame([{"warehouse_series": checked["warehouse"], "fixture_series": checked["fixtures"],
                             **{f"n_{k}": int(by.get(k, 0)) for k in ("split_suspect", "jump", "bad_print",
                                                                      "nonpositive", "calendar", "stale",
                                                                      "split_unconfirmed", "missing_session",
                                                                      "flat_bar", "vendor_change", "stale_minute",
                                                                      "stale_options", "stale_fred")},
                             "n_adjust_drift": sum(not r["excepted"] for r in drift),
                             "n_adjust_drift_gating": sum(bool(r["gating"]) for r in drift),
                             "adjust_compared": int(counts.get("compared", 0)),
                             "adjust_max_drift": float(counts.get("max_drift", float("nan"))),
                             **{f"adjust_{k}": len(counts.get(k, [])) for k in ("no_raw", "no_actions",
                                                                                "not_comparable", "raw_gaps",
                                                                                "failed", "excepted")},
                             "n_gating": len(serious), "n_history": history}])
    n = checked["warehouse"] + checked["fixtures"]
    if n == 0:
        return ArtifactSpec(tables={"findings": findings, "summary": summary}, data_asof=None, provenance=[],
                            notes=notes, verdict="INSUFFICIENT DATA", verdict_detail="NO SERIES TO CHECK")
    if len(serious):
        names = sorted({f"{r.ticker}" for r in serious.itertuples()})[:6]
        verdict, detail = "FAIL", (f"{len(serious)} DEFECT(S) ON {serious['ticker'].nunique()} SERIES · "
                                   f"{', '.join(names)}{' …' if serious['ticker'].nunique() > 6 else ''}")
    else:
        verdict, detail = "PASS", (f"{n} SERIES · NO NEW DEFECTS · {history} HISTORICAL FINDINGS LISTED · "
                                   f"{int(by.get('stale', 0))} STALE")
    asof = newest.date().isoformat() if newest is not None else None
    return ArtifactSpec(tables={"findings": findings, "summary": summary}, data_asof=asof,
                        provenance=[{"source": "warehouse:bars_daily+fixtures/history", "fetched_at": "",
                                     "detail": {"series": n}}],
                        notes=[*notes, "detects, never repairs: a repair to a published input is the owner's, and "
                                       "is archived with provenance (fixtures/archive/)"],
                        verdict=verdict, verdict_detail=detail)
