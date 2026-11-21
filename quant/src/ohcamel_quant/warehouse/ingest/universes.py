"""``ingest.universes`` (compute plan C2): ``universe_members`` from the committed
constituent lists (sp500, ndx100) and the site's universes.json ('site', ~45),
names from SEC company_tickers where it lists the ticker. Each universe is
replaced whole in one transaction. Completion task 3.5: in the same transaction, a
universe whose changes table is committed (sp500) has its dated stints replaced in
``universe_membership`` (``constituents.membership_intervals``). Completion task 3.8: the walk applies the committed
rename map (``constituents/sp500_renames.csv``), and each row's company's price series goes to ``membership_series``."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ...data import sec
from ...data.base import DataUnavailable
from ...data.market import load_universes
from ..constituents import (
    CHANGES_SOURCES,
    COVERAGE_START,
    MEMBERSHIP_SURVIVORSHIP,
    SURVIVORSHIP,
    load_changes,
    load_constituents,
    load_renames,
    membership_intervals,
)
from .base import KeyState, Written, insert_frame, log_row, run_ingest

UNIVERSES = ("sp500", "ndx100", "site")
COLUMNS = ["universe", "ticker", "name", "added", "source", "survivorship"]
LIST_SOURCE = {"sp500": "wikipedia:List_of_S&P_500_companies", "ndx100": "wikipedia:List_of_NASDAQ-100_companies"}


def _site_members() -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for u in load_universes()["universes"].values():
        for m in u["members"]:
            seen.setdefault(m["ticker"].upper(), m["name"])
    return list(seen.items())


def universe_rows(universe: str, sec_names: dict[str, str] | None) -> pd.DataFrame:
    if universe == "site":
        members, added, base = _site_members(), None, "ohcamel:universes.json"
    else:
        c = load_constituents(universe)
        members, added, base = c.members, c.as_of, f"{LIST_SOURCE[universe]}@{c.as_of.isoformat()}"
    rows = []
    for ticker, name in members:
        sec_name = (sec_names or {}).get(ticker) or (sec_names or {}).get(ticker.replace(".", "-"))
        rows.append({"universe": universe, "ticker": ticker, "name": sec_name or name, "added": added,
                     "source": base + ("+sec:company_tickers" if sec_name else ""), "survivorship": SURVIVORSHIP})
    return pd.DataFrame(rows, columns=COLUMNS)


MEMBERSHIP_COLUMNS = ["universe", "ticker", "start", "end", "source", "series"]


def membership_rows(universe: str) -> tuple[pd.DataFrame, list[str]]:
    """``universe_membership`` rows from the committed list and changes table (empty for a universe without one),
    and the walk's notes."""
    if universe not in CHANGES_SOURCES:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS), []
    c, ch, rn = load_constituents(universe), load_changes(universe), load_renames(universe)
    stints, notes = membership_intervals([t for t, _ in c.members], c.as_of, ch.changes, rn.renames)
    source = (f"{ch.source}@{ch.as_of.isoformat()}+{c.source}"
              + (f"+renames:{len(rn.renames)}@{rn.as_of.isoformat()}" if rn.renames else "")
              + f"; coverage from {COVERAGE_START[universe].isoformat()}; {MEMBERSHIP_SURVIVORSHIP}")
    frame = pd.DataFrame([{"universe": universe, "ticker": s.ticker, "start": s.start, "end": s.end,
                           "source": source, "series": s.key} for s in stints], columns=MEMBERSHIP_COLUMNS)
    return frame, notes


def write_membership(con: Any, universe: str) -> tuple[int, list[str]]:
    frame, notes = membership_rows(universe)
    if frame.empty:
        return 0, notes
    con.execute("DELETE FROM universe_membership WHERE universe = ?", [universe])
    con.execute("DELETE FROM membership_series WHERE universe = ?", [universe])
    insert_frame(con, 'INSERT INTO universe_membership (universe, ticker, "start", "end", source) SELECT '
                      'universe, ticker, CAST("start" AS DATE), CAST("end" AS DATE), source FROM incoming', frame)
    insert_frame(con, 'INSERT INTO membership_series (universe, ticker, "start", "end", series) SELECT '
                      'universe, ticker, CAST("start" AS DATE), CAST("end" AS DATE), series FROM incoming', frame)
    as_of = load_constituents(universe).as_of
    log_row(con, "universe_membership", universe, "ok", len(frame),
            f'{{"stints": {len(frame)}, "unplaced_rows": {len(notes)}}}', as_of)
    return len(frame), notes


def _sec_names(settings: Any) -> tuple[dict[str, str] | None, str | None]:
    try:
        table, _ = sec.ticker_table(settings)
    except DataUnavailable as e:
        return None, f"SEC company_tickers unavailable ({e}): names from the constituent lists"
    return dict(zip(table["ticker"], table["name"], strict=True)), None


def run_universes(params: dict, ctx: Any) -> dict:
    names, warn = _sec_names(ctx.market.settings)

    def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
        return {}

    def fetch(key: str, state: KeyState, settings: Any):
        yield universe_rows(key, names)

    def write(con: Any, key: str, state: KeyState, frame: pd.DataFrame) -> Written:
        con.execute("DELETE FROM universe_members WHERE universe = ?", [key])
        insert_frame(con, "INSERT INTO universe_members SELECT universe, ticker, name, CAST(added AS DATE), source, "
                          "survivorship FROM incoming", frame)
        added = frame["added"].iloc[0] if len(frame) else None
        base = str(frame["source"].iloc[0]).split("+")[0] if len(frame) else key
        named = int(frame["source"].str.contains("sec:company_tickers").sum())
        stints, unplaced = write_membership(con, key)
        detail = {"source": base, "sec_named": named}
        if stints:
            detail |= {"membership_stints": stints, "membership_unplaced": len(unplaced)}
        return Written(len(frame), added, detail)

    notes = [f"universe members are {SURVIVORSHIP}"] + ([warn] if warn else [])
    notes += [f"{u}: universe_membership has dated stints from its changes table (coverage from "
              f"{COVERAGE_START[u].isoformat()}); {len(membership_rows(u)[1])} change row(s) could not be placed "
              "(constituents module docstring; the rename map places the rest)" for u in CHANGES_SOURCES]
    return run_ingest(dataset="universe_members", keys=list(UNIVERSES), ctx=ctx, read_state=read_state,
                      fetch=fetch, write=write, notes=notes, survivorship=SURVIVORSHIP)
