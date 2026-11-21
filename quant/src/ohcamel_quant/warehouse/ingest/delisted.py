"""``ingest.delisted`` (completion task 3.8, spec R5): prices to their exit for every company that left a dated
universe, delisted names included, from the paid source behind the switch (data/eodhd.py).

**Off by default.** With ``OHCAMEL_DELISTED_SOURCE`` unset the job is not scheduled (jobs/schedules.py drops a kind
whose switch is off) and, run by hand, refuses before it opens anything; with the switch on and no
``EODHD_API_KEY`` it refuses too.

**What it fetches** (:func:`plan`). Every exit of a universe with dated membership (sp500) on or after its changes
table's coverage start (2011-01-01) whose series lacks bars across the stint to the exit -- the exits
``readers.survivorship_free`` counts as uncovered. One key per series (a company's last ticker across renames,
``membership_series``), asked from 1990 to 100 days after its last such exit (AFTER_EXIT_DAYS); a vendor series that
begins after the company joined is refused (:func:`check_continuity`). Left out, each with a note: the earlier holder of a reused ticker (``TICKER@DATE``: the vendor's code for it is not known, and the
bare ticker could be the later company), and a series another vendor already serves (a listed name is never half
one vendor and half another).

**What it writes** (:func:`write_delisted`), per key in one transaction (``ingest.base``): ``bars_raw`` (the prints
as traded), ``corp_actions`` (splits and cash dividends as paid, through ingest/corp_actions.py's write, so a
changed statement is a revision), and ``bars_daily`` derived from the two by ``warehouse/adjust.py`` -- the read path
every backtest uses -- all with ``source = 'eodhd'`` and the whole series replaced. A series wholly from one vendor
never trips ``ops.data_integrity``'s ``vendor_change``, and its end is not ``stale`` (an ended series ends).

**Resumable.** A key written is covered, so the next plan leaves it out: a run killed at the M wall limit (45 min) is
retried by the worker (``run_ingest``'s stalest-first order) and carries on with the keys still missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from ...data import eodhd, prices
from ...data.base import DataUnavailable, Provenance
from .. import adjust
from ..constituents import CHANGES_SOURCES
from ..db import open_rw, warehouse_path
from ..freshness import NY
from .base import IngestFailed, KeyState, Written, insert_frame, run_ingest, utcnow
from .corp_actions import ActionsPayload, write_actions

DATASET = "delisted"
#: Calendar days fetched after a series' last uncovered exit (fix round 1, I4). A name removed from the index but
#: still trading is held until the next decision zeroes it: up to about 92 days under quarterly rebalancing, so 100
#: days keep its price live until it is sold. Short enough that a later company re-using the freed ticker is
#: unlikely to be reached; that case is guarded by :func:`check_continuity` (a series that begins after the company
#: joined is another company's) and by never asking for a ``TICKER@DATE`` series.
AFTER_EXIT_DAYS = 100
#: Weekdays of slack between a company's first membership day and the vendor series' first bar (check_continuity).
START_SLACK = 5
RAW_INSERT = ("INSERT INTO bars_raw (ticker, date, open, high, low, close, volume, source) SELECT ticker, "
              "CAST(date AS DATE), open, high, low, close, volume, source FROM incoming")
DAILY_INSERT = ("INSERT INTO bars_daily (ticker, date, open, high, low, close, adj_close, volume, source, fetched_at) "
                "SELECT ticker, CAST(date AS DATE), open, high, low, close, adj_close, volume, source, fetched_at "
                "FROM incoming")
NOTES_LISTED = 20


@dataclass(frozen=True)
class Want:
    series: str
    code: str
    exit: date              # the series' last exit without prices to it
    universes: tuple[str, ...]
    first: date | None = None   # the series' first day as a member (None: before the changes table's coverage)


@dataclass
class Plan:
    keys: list[Want]
    skipped: list[str]


@dataclass
class DelistedPayload:
    raw: pd.DataFrame        # open..volume as traded, on a date index
    actions: pd.DataFrame    # ex_date, kind, ratio, cash
    prov: Provenance
    window_start: date
    reached: date
    full: bool = True
    fetched_to: date | None = None   # the last date asked for (a series cut here was not delisted)


def plan(con: Any, universes: list[str] | None = None) -> Plan:
    """The series to fetch (module docstring), and why any uncovered exit is left out."""
    from .. import readers
    from ..constituents import COVERAGE_START

    wants: dict[str, tuple[date, set[str]]] = {}
    firsts: dict[str, date] = {}
    for u in universes or list(CHANGES_SOURCES):
        cov = readers.exit_coverage(con, u)
        for row in cov[~cov["covered"]].itertuples(index=False):
            end = pd.Timestamp(row.end).date()
            ex, us = wants.get(row.series, (end, set()))
            wants[row.series] = (max(ex, end), us | {u})
            start = None if pd.isna(row.start) else pd.Timestamp(row.start).date()
            first = start or COVERAGE_START.get(u)
            if first is not None:
                firsts[row.series] = min(firsts.get(row.series, first), first)
    other = dict(con.execute(
        "SELECT ticker, string_agg(DISTINCT source, ', ') FROM bars_daily WHERE list_contains(?, ticker) "
        "AND source <> 'eodhd' GROUP BY ticker", [sorted(wants) or [""]]).fetchall())
    keys: list[Want] = []
    skipped: list[str] = []
    for series in sorted(wants):
        exit_, us = wants[series]
        if series in other:
            skipped.append(f"{series}: served by {other[series]}, whose bars do not reach its exit on {exit_}; not "
                           "fetched from EODHD (a listed name is never half one vendor and half another)")
            continue
        try:
            code = eodhd.code_for(series)
        except DataUnavailable as e:
            skipped.append(f"{e}; not fetched (exit {exit_})")
            continue
        keys.append(Want(series, code, exit_, tuple(sorted(us)), firsts.get(series)))
    return Plan(keys, skipped)


def check_continuity(want: Want, raw: pd.DataFrame) -> None:
    """Refuse a vendor series that begins after the company was already a member (``want.first``, START_SLACK
    weekdays of slack): under that code the vendor holds another company, a later holder of the ticker."""
    if want.first is None or raw.empty:
        return
    begins = raw.index[0].date()
    if begins > want.first and int(np.busday_count(want.first, begins)) > START_SLACK:
        raise DataUnavailable(f"{want.series}: the vendor's series under {want.code} begins {begins}, after the company "
                              f"was already a member on {want.first}: another company under the code? Not written")


def fetch_delisted(want: Want, settings: Any, today: date) -> DelistedPayload:
    start, end = prices.DEFAULT_START, min(today, want.exit + timedelta(days=AFTER_EXIT_DAYS))
    table = eodhd.splits(want.series, settings)
    raw = eodhd.bars(want.series, start, end, delisted=True, settings=settings, split_table=table)
    check_continuity(want, raw)
    acts = eodhd.actions(want.series, settings, split_table=table)
    prov = Provenance.now(eodhd.SOURCE, code=want.code, url=eodhd.ENDPOINTS["eod"].format(code=want.code),
                          params={"from": start.isoformat(), "to": end.isoformat()}, delisted=True,
                          exit=want.exit.isoformat(), universes=list(want.universes),
                          convention="raw OHLC as traded; volume as traded (the vendor's split-adjusted volume "
                                     "divided back by later splits); dividends as paid on the ex-date")
    return DelistedPayload(raw, acts, prov, start, raw.index[-1].date(), True, end)


def write_delisted(con: Any, key: str, state: KeyState, p: DelistedPayload) -> Written:
    """Replace ``key``'s whole series with the vendor's (module docstring); refuse a series another vendor serves."""
    others = sorted({f"{t} ({s})" for t, s in con.execute(
        "SELECT 'bars_daily', source FROM bars_daily WHERE ticker = ? AND source <> 'eodhd' UNION "
        "SELECT 'bars_raw', source FROM bars_raw WHERE ticker = ? AND source <> 'eodhd' UNION "
        "SELECT 'corp_actions', source FROM corp_actions WHERE ticker = ? AND source <> 'eodhd'",
        [key, key, key]).fetchall()})
    if others:
        raise DataUnavailable(f"{key}: already served by another vendor in {', '.join(others)}; a listed name is never "
                              "half one vendor and half another, so nothing was written")
    if p.raw.empty:
        raise DataUnavailable(f"{key}: the vendor returned no bars")
    con.execute("DELETE FROM bars_daily WHERE ticker = ?", [key])
    con.execute("DELETE FROM bars_raw WHERE ticker = ?", [key])
    raw = p.raw[adjust.RAW_COLUMNS].reset_index()
    raw.columns = ["date", *adjust.RAW_COLUMNS]
    raw.insert(0, "ticker", key)
    raw["source"] = eodhd.SOURCE
    insert_frame(con, RAW_INSERT, raw)
    acts = write_actions(con, key, state, ActionsPayload(p.actions, p.window_start, p.reached, p.prov, p.full))
    stored = adjust.read_actions(con, key)
    daily = adjust.bars(p.raw[adjust.RAW_COLUMNS], stored).reset_index()
    daily.columns = ["date", *adjust.BAR_COLUMNS]
    daily.insert(0, "ticker", key)
    daily["source"] = eodhd.SOURCE
    daily["fetched_at"] = utcnow()
    insert_frame(con, DAILY_INSERT, daily)
    detail = {"source": eodhd.SOURCE, "first": str(raw["date"].min().date()), "last": str(p.reached),
              "exit": p.prov.detail.get("exit"), "bars": len(raw), "actions": acts.detail,
              "fetched_to": p.fetched_to.isoformat() if p.fetched_to else None}
    return Written(len(raw), p.reached, detail)


def _refuse_unless_on(settings: Any) -> None:
    if settings.delisted_source != "eodhd":
        raise IngestFailed("ingest.delisted: the delisted-names source is off (OHCAMEL_DELISTED_SOURCE is not "
                           "'eodhd'); nothing was fetched or written (docs/runbooks/compute.md, Delisted names)")
    if not settings.eodhd_api_key:
        raise IngestFailed("ingest.delisted: EODHD_API_KEY is not set; nothing was fetched or written")


def run_delisted(params: dict, ctx: Any) -> dict:
    settings = ctx.market.settings
    _refuse_unless_on(settings)
    path = warehouse_path(settings)
    with open_rw(path) as con:
        p = plan(con)
    notes = [f"delisted names (spec R5): {len(p.keys)} series without prices to their exit, fetched from EODHD"]
    if p.skipped:
        notes.append(f"{len(p.skipped)} uncovered exit(s) left out: " + "; ".join(p.skipped[:NOTES_LISTED])
                     + (f"; and {len(p.skipped) - NOTES_LISTED} more" if len(p.skipped) > NOTES_LISTED else ""))
    if not p.keys:
        return {"data_asof": None, "provenance": [], "notes": [*notes, "every exit already has prices to it"],
                "survivorship": None, "tables": {}}
    wants = {w.series: w for w in p.keys}
    today = datetime.now(UTC).astimezone(NY).date()
    return run_ingest(dataset=DATASET, keys=list(wants), ctx=ctx, read_state=lambda con, keys: {},
                      fetch=lambda key, state, s: [fetch_delisted(wants[key], s, today)],
                      write=write_delisted, notes=notes)
