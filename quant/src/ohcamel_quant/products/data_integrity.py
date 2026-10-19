"""``ops.data_integrity``: every daily series, checked nightly for the defects that have actually happened here.

EXP-K01 found an unadjusted 2-for-1 split (XLE and XLK, 2025-12-05) and a decimal-shift low (SPY, 2026-02-02)
sitting in committed data for months. ``data/fixtures.py`` repaired both at load time for the API, but anything
that read the files directly scored against a fictitious crash. This job looks for that class of defect in
the warehouse and in the committed fixtures every night, and names each finding (ticker, session, check).

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
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..data import fixtures as F
from ..data.base import DataUnavailable
from ..jobs.artifacts import ArtifactSpec

KIND = "ops.data_integrity"
SPLIT_RATIOS = F._SPLIT_RATIOS
JUMP = 0.40
STALE_BDAYS = 5
COLUMNS = ["source", "ticker", "date", "check", "detail"]


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


def stale(last: dict[str, pd.Timestamp], bdays: int = STALE_BDAYS) -> list[dict[str, Any]]:
    if not last:
        return []
    newest = max(last.values())
    cut = newest - pd.offsets.BDay(bdays)
    return [{"ticker": t, "date": str(d.date()), "check": "stale",
             "detail": f"last bar {d.date()}, newest across series {newest.date()}"} for t, d in last.items() if d < cut]


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
    if tickers:
        from ..warehouse import readers

        for i, t in enumerate(tickers):
            ctx.check_cancelled()
            try:
                df = readers.ohlcv(ctx.warehouse, t).data
            except DataUnavailable:
                continue
            checked["warehouse"] += 1
            last[t] = pd.Timestamp(df.index[-1])
            rows += [{"source": "warehouse", "ticker": t, **f} for f in check_bars(df)]
            if i % 50 == 0:
                ctx.progress((i + 1) / len(tickers), t)
        rows += [{"source": "warehouse", **f} for f in stale(last)]
    root = F._root() / "history"
    for p in sorted(root.glob("*.parquet")):
        df = pd.read_parquet(p).rename(columns={"close_adj": "adj_close"})
        df.index = pd.DatetimeIndex(df.index)
        checked["fixtures"] += 1
        rows += [{"source": "fixtures", "ticker": p.stem, **f} for f in check_bars(df)]
    findings = pd.DataFrame(rows, columns=COLUMNS)
    serious = findings[findings["check"].isin(["split_suspect", "bad_print", "nonpositive", "calendar"])]
    by = findings.groupby("check").size().to_dict() if len(findings) else {}
    summary = pd.DataFrame([{"warehouse_series": checked["warehouse"], "fixture_series": checked["fixtures"],
                             **{f"n_{k}": int(by.get(k, 0)) for k in ("split_suspect", "jump", "bad_print",
                                                                      "nonpositive", "calendar", "stale")}}])
    n = checked["warehouse"] + checked["fixtures"]
    if n == 0:
        return ArtifactSpec(tables={"findings": findings, "summary": summary}, data_asof=None, provenance=[],
                            notes=notes, verdict="INSUFFICIENT DATA", verdict_detail="NO SERIES TO CHECK")
    if len(serious):
        names = sorted({f"{r.ticker}" for r in serious.itertuples()})[:6]
        verdict, detail = "FAIL", (f"{len(serious)} DEFECT(S) ON {serious['ticker'].nunique()} SERIES · "
                                   f"{', '.join(names)}{' …' if serious['ticker'].nunique() > 6 else ''}")
    else:
        verdict, detail = "PASS", (f"{n} SERIES CLEAN · {int(by.get('jump', 0))} LARGE MOVES SHOWN · "
                                   f"{int(by.get('stale', 0))} STALE")
    asof = max(last.values()).date().isoformat() if last else None
    return ArtifactSpec(tables={"findings": findings, "summary": summary}, data_asof=asof,
                        provenance=[{"source": "warehouse:bars_daily+fixtures/history", "fetched_at": "",
                                     "detail": {"series": n}}],
                        notes=[*notes, "detects, never repairs: a repair to a published input is the owner's, and "
                                       "is archived with provenance (fixtures/archive/)"],
                        verdict=verdict, verdict_detail=detail)
