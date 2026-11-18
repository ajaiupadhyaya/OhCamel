"""``ingest.bars_daily`` (compute plan C3): daily bars for the universe, full
history from ``prices.DEFAULT_START`` (the provider path's own default),
from data/prices.py's vendor chain, one vendor per ticker, incremental by
``max(date)`` with an overlap check, full replace on re-adjustment.

``close`` is split-adjusted and ``adj_close`` fully adjusted (splits and
dividends) where the vendor provides it; Stooq has no adjusted close
(``adj_close = close``) and its provenance says so.

**Raw bars** (completion task 3.2). Every payload is also written to ``bars_raw``, the unadjusted prints,
from the SAME vendor and window, INSERT OR REPLACE by (ticker, date) and never deleted by a re-adjustment:

* Yahoo serves only split-adjusted prices, and lists in the same response every split from the window's
  start to today (``prov.detail['splits']``). Raw = split-adjusted price x the ratios of the splits dated
  after the bar, volume / the same (:func:`raw_from_split_adjusted`); the round trip through
  ``adjust.series(..., dividends=False)`` gives the vendor's bars back to 1e-12 on the committed responses.
* Alpaca serves raw prints itself (``adjustment=raw``, one more request: :data:`RAW_FETCH`).
* Stooq (split-adjusted, no events) and any vendor that lists no splits: no raw rows, and the key's
  ``ingest_log`` detail says why (``raw: none: ...``). A failed raw fetch never fails the key: bars_daily
  is still every reader's source until the owner's cut-over.

``{"raw_missing": true}`` is the backfill of ``bars_raw`` on a warehouse that predates it: only the
tickers with no raw row are fetched, in full, so a killed run is resumed by running it again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from ...data import prices
from ...data.base import DataUnavailable, Provenance
from .. import adjust
from ..constituents import SURVIVORSHIP
from ..db import open_rw, warehouse_path
from ..freshness import NY
from .base import IngestFailed, KeyState, Written, insert_frame, run_ingest, utcnow

OVERLAP_DAYS = 10  # calendar days, >= prices.OVERLAP_SESSIONS (5) sessions
INSERT = ("INSERT OR REPLACE INTO bars_daily SELECT ticker, CAST(date AS DATE), open, high, low, close, adj_close, "
          "volume, source, fetched_at FROM incoming")


def _now() -> datetime:
    return datetime.now(UTC)


#: Vendors that serve raw prints on a request of their own: name -> (ticker, start, end, settings) -> frame.
RAW_FETCH: dict[str, Any] = {"alpaca": prices.fetch_alpaca_raw}
RAW_INSERT = ("INSERT OR REPLACE INTO bars_raw SELECT ticker, CAST(date AS DATE), open, high, low, close, volume, "
              "source FROM incoming")


@dataclass
class BarsPayload:
    frame: pd.DataFrame
    prov: Provenance
    mode: str                 # 'full' | 'incremental'
    fallback_from: list[str]
    requested_start: date | None = None  # full fetches only: what the serving vendor was asked for
    raw: pd.DataFrame | None = None      # the same window unadjusted (open..volume), or None
    raw_note: str = ""                   # how raw was obtained, or why there is none


def _years_before(d: date, years: int) -> date:
    return date(d.year - years, d.month, 28 if (d.month, d.day) == (2, 29) else d.day)


def raw_from_split_adjusted(frame: pd.DataFrame, splits: Any) -> pd.DataFrame:
    """Raw OHLCV from a vendor's split-adjusted bars and the splits it adjusted for: every price before
    a split's ex-date multiplied by its ratio (new shares per old), volume divided by it."""
    table = pd.DataFrame([(pd.Timestamp(d), float(r)) for d, r in splits], columns=["ex_date", "ratio"])
    s = adjust.split_factor(pd.DatetimeIndex(frame.index), table)
    out = frame[["open", "high", "low", "close"]].astype(float).mul(s, axis=0)
    out["volume"] = frame["volume"].astype(float) / s
    return out


def universe_tickers(path: Any) -> list[str]:
    with open_rw(path) as con:
        return [r[0] for r in con.execute("SELECT DISTINCT ticker FROM universe_members ORDER BY ticker").fetchall()]


def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
    heads = con.execute("SELECT ticker, max(date), arg_max(source, date) FROM bars_daily "
                        "WHERE list_contains(?, ticker) GROUP BY ticker", [keys]).fetchall()
    tails = con.execute(
        "SELECT ticker, date, close, adj_close FROM (SELECT ticker, date, close, adj_close, "
        "row_number() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn FROM bars_daily "
        "WHERE list_contains(?, ticker)) WHERE rn <= 15 ORDER BY ticker, date", [keys]).df()
    out = {}
    for ticker, last, source in heads:
        t = tails[tails["ticker"] == ticker]
        tail = pd.DataFrame({"close": t["close"].to_numpy(), "adj_close": t["adj_close"].to_numpy()},
                            index=pd.DatetimeIndex(pd.to_datetime(t["date"])).astype("datetime64[ns]"))
        out[ticker] = KeyState(last, {"source": source, "tail": tail})
    return out


def _ns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.DatetimeIndex(df.index).astype("datetime64[ns]")
    return df


def raw_for(ticker: str, frame: pd.DataFrame, prov: Provenance, start: date,
            settings: Any) -> tuple[pd.DataFrame | None, str]:
    """The unadjusted bars for ``frame``'s sessions from the vendor that served it, and how (or why not)."""
    splits = prov.detail.get("splits")
    if splits is not None:
        n = len(splits)
        return raw_from_split_adjusted(frame, splits), f"{prov.source} split-adjusted bars x its {n} listed split(s)"
    fetch = RAW_FETCH.get(prov.source)
    if fetch is None:
        return None, f"none: {prov.source} serves split-adjusted prices and lists no splits"
    try:
        raw = fetch(ticker, start, None, settings)
    except DataUnavailable as e:
        return None, f"none: {prov.source} raw pass failed ({e})"
    raw = raw[["open", "high", "low", "close", "volume"]].astype(float)
    raw.index = pd.DatetimeIndex(raw.index).astype("datetime64[ns]")
    raw = raw[raw.index.isin(frame.index)]
    return raw, f"{prov.source} adjustment=raw"


def _with_raw(p: BarsPayload, ticker: str, start: date, settings: Any) -> BarsPayload:
    p.raw, p.raw_note = raw_for(ticker, p.frame, p.prov, start, settings)
    return p


def fetch_bars(ticker: str, state: KeyState, settings: Any, *, start: date, full: bool) -> BarsPayload:
    reasons: list[str] = []
    src = state.extra.get("source")
    if state.data_asof is not None and src in prices.PROVIDERS and not full:
        frm = state.data_asof - timedelta(days=OVERLAP_DAYS)
        try:
            new, prov = prices.PROVIDERS[src](ticker, frm, None, settings)
            new = _ns(new)
            prices.merge_incremental(state.extra["tail"], new)  # raises on re-adjusted history
            return _with_raw(BarsPayload(new, prov, "incremental", []), ticker, frm, settings)
        except DataUnavailable as e:
            reasons.append(f"incremental {src}: {e}")
    for name in prices.provider_order(ticker, start, settings):
        try:
            df, prov = prices.PROVIDERS[name](ticker, start, None, settings)
            asked = max(start, prices.ALPACA_HISTORY_START) if name == "alpaca" else start
            return _with_raw(BarsPayload(_ns(df), prov, "full", reasons, asked), ticker, asked, settings)
        except DataUnavailable as e:
            reasons.append(str(e))
        except Exception as e:  # noqa: BLE001 - a malformed vendor payload must not stop the chain
            reasons.append(f"{name}: unexpected response ({type(e).__name__}: {e})")
    raise DataUnavailable("; ".join(reasons) or f"{ticker}: no price provider configured")


def write_bars(con: Any, ticker: str, state: KeyState, p: BarsPayload) -> Written:
    frame = p.frame[prices.COLUMNS].reset_index()
    frame.columns = ["date", *prices.COLUMNS]
    frame.insert(0, "ticker", ticker)
    frame["source"] = p.prov.source
    frame["fetched_at"] = utcnow()
    if p.mode == "full":
        con.execute("DELETE FROM bars_daily WHERE ticker = ?", [ticker])
    insert_frame(con, INSERT, frame)
    raw_rows = 0
    if p.raw is not None and len(p.raw):
        raw = p.raw[["open", "high", "low", "close", "volume"]].reset_index()
        raw.columns = ["date", "open", "high", "low", "close", "volume"]
        raw.insert(0, "ticker", ticker)
        raw["source"] = p.prov.source
        insert_frame(con, RAW_INSERT, raw)          # never deleted: a re-adjustment does not change a raw price
        raw_rows = len(raw)
    last = con.execute("SELECT max(date) FROM bars_daily WHERE ticker = ?", [ticker]).fetchone()[0]
    detail = {
        "source": p.prov.source, "mode": p.mode, "first": str(frame["date"].min().date()),
        "last": str(last), "fallback_from": "; ".join(p.fallback_from),
        "vendor_notes": p.prov.detail.get("notes"),
        "raw": f"{raw_rows} rows ({p.raw_note})" if raw_rows else (p.raw_note or "none"),
    }
    if p.requested_start is not None:
        detail["requested_start"] = p.requested_start.isoformat()  # C1 bars_coverage
    return Written(len(frame), last, detail)


def run_bars_daily(params: dict, ctx: Any) -> dict:
    settings = ctx.market.settings
    raw_missing = bool(params.get("raw_missing", False))
    full = bool(params.get("full", False)) or raw_missing
    given = [t.upper().strip() for t in params.get("tickers") or [] if str(t).strip()]
    from_universe = not given
    tickers = given or universe_tickers(warehouse_path(settings))
    built_universe = False
    if not tickers and from_universe:
        # A fresh warehouse: build the universe here (this job already holds
        # the writer) rather than fail every weekday until Sunday's
        # ingest.universes. Found 2026-10-08 when the backfill raced it.
        from . import universes

        universes.run_universes({}, ctx)
        tickers = universe_tickers(warehouse_path(settings))
        built_universe = True
    if not tickers:
        raise IngestFailed("bars_daily: no universe -- run ingest.universes first")
    notes_raw: list[str] = []
    if raw_missing:
        with open_rw(warehouse_path(settings)) as con:
            have = {t for (t,) in con.execute("SELECT DISTINCT ticker FROM bars_raw WHERE list_contains(?, ticker)",
                                              [tickers]).fetchall()}
        tickers = [t for t in tickers if t not in have]
        notes_raw.append(f"raw backfill: {len(tickers)} ticker(s) without raw bars, fetched in full; "
                         f"{len(have)} already had them")
        if not tickers:
            return {"data_asof": None, "provenance": [], "notes": notes_raw, "survivorship": None, "tables": {}}
    if params.get("start"):
        start = date.fromisoformat(str(params["start"]))
    elif params.get("years"):
        start = _years_before(_now().astimezone(NY).date(), int(params["years"]))
    else:
        start = prices.DEFAULT_START
    notes = [f"window from {start.isoformat()}; one vendor per ticker; Alpaca history begins 2016, "
             "so windows before 2016 are served by Yahoo first (data/prices.py provider order)"]
    if built_universe:
        notes.append("universe built on demand: the warehouse had none (ingest.universes ran inside this job)")
    notes += notes_raw
    return run_ingest(
        dataset="bars_daily", keys=list(dict.fromkeys(tickers)), ctx=ctx, read_state=read_state,
        fetch=lambda key, state, s: [fetch_bars(key, state, s, start=start, full=full)],
        write=write_bars, notes=notes, survivorship=SURVIVORSHIP if from_universe else None)
