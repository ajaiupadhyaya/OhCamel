"""``ingest.sec_facts`` and ``ingest.holdings_13f`` (compute plan C5).

sec_facts: SEC XBRL companyfacts for the universe's companies, only the tags
data/sec.py standardizes plus dei:EntityCommonStockSharesOutstanding, one
company at a time (a companyfacts JSON is 5-50 MB; it is flattened and dropped
before the next). ``filed`` is the availability date (II.5 point-in-time rule);
``period_start = period_end`` for instant facts (II.5 amendment 2026-10-06).

holdings_13f: the latest 13F-HR of each notable filer (universes.json), via
data/sec.fetch_13f, keyed by accession -- re-running is idempotent and history
accrues a quarter at a time.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from ...data import http, sec
from ...data.base import DataUnavailable
from ...data.market import load_universes
from ..constituents import SURVIVORSHIP
from ..db import warehouse_path
from .bars import universe_tickers
from .base import KeyState, Written, insert_frame, run_ingest

SEC_TAGS = tuple(sorted({t for item in sec.LINE_ITEMS.values() for t in item.tags}))
DEI_TAGS = ("EntityCommonStockSharesOutstanding",)
FACT_KEY = ["cik", "tag", "unit", "period_start", "period_end", "filed"]
FACT_COLUMNS = ["cik", "ticker", "tag", "unit", "period_start", "period_end", "filed", "form", "value"]


def flatten_company_facts(payload: dict[str, Any], cik: str, ticker: str | None) -> tuple[pd.DataFrame, int]:
    facts = (payload or {}).get("facts") or {}
    rows = []
    for taxonomy, tags in (("us-gaap", SEC_TAGS), ("dei", DEI_TAGS)):
        tx = facts.get(taxonomy) or {}
        for tag in tags:
            for unit, obs in (((tx.get(tag) or {}).get("units")) or {}).items():
                for o in obs:
                    end, filed, val = o.get("end"), o.get("filed"), o.get("val")
                    if not end or not filed or val is None:
                        continue
                    rows.append((cik, ticker, tag, unit, o.get("start") or end, end, filed, o.get("form") or "",
                                 float(val)))
    df = pd.DataFrame(rows, columns=FACT_COLUMNS)
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c])
    dup = df.duplicated(FACT_KEY, keep="first")
    first = df[~dup].set_index(FACT_KEY)["value"]
    later = df[dup].set_index(FACT_KEY)["value"]
    conflicts = int((later != first.reindex(later.index)).sum())
    return df[~dup].reset_index(drop=True), conflicts


def run_sec_facts(params: dict, ctx: Any) -> dict:
    settings = ctx.market.settings
    given = [t.upper().strip() for t in params.get("tickers") or [] if str(t).strip()]
    tickers = given or universe_tickers(warehouse_path(settings))
    table, _ = sec.ticker_table(settings)
    by_ticker = dict(zip(table["ticker"], table["cik"], strict=True))
    keys, names, missing = [], {}, []
    for t in dict.fromkeys(tickers):
        cik = by_ticker.get(t) or by_ticker.get(t.replace(".", "-"))
        if cik is None:
            missing.append(t)
        elif cik not in names:
            keys.append(cik)
            names[cik] = t

    def read_state(con: Any, ks: list[str]) -> dict[str, KeyState]:
        return {c: KeyState(d) for c, d in con.execute(
            "SELECT cik, max(filed) FROM sec_facts WHERE list_contains(?, cik) GROUP BY cik", [ks]).fetchall()}

    def fetch(cik: str, state: KeyState, s: Any):
        payload = http.get_json(sec.FACTS_URL.format(cik=cik), s, source="sec-edgar")
        df, conflicts = flatten_company_facts(payload, cik, names[cik])
        del payload
        if df.empty:
            raise DataUnavailable(f"sec: CIK {cik} ({names[cik]}) has none of the standardized tags")
        yield df, conflicts

    def write(con: Any, cik: str, state: KeyState, payload: Any) -> Written:
        df, conflicts = payload
        insert_frame(con, "INSERT OR REPLACE INTO sec_facts SELECT cik, ticker, tag, unit, CAST(period_start AS DATE), "
                          "CAST(period_end AS DATE), CAST(filed AS DATE), form, value FROM incoming", df)
        last = con.execute("SELECT max(filed) FROM sec_facts WHERE cik = ?", [cik]).fetchone()[0]
        return Written(len(df), last, {"source": "sec-edgar", "ticker": names[cik], "conflicting_duplicates": conflicts})

    notes = ["a fact is usable from its filed date (point-in-time rule)"]
    if missing:
        notes.append(f"{len(missing)} tickers have no SEC CIK (ETFs, funds): " + ", ".join(missing[:30]))
    return run_ingest(dataset="sec_facts", keys=keys, ctx=ctx, read_state=read_state, fetch=fetch, write=write,
                      notes=notes, survivorship=None if given else SURVIVORSHIP)


def _nulls(s: pd.Series) -> pd.Series:
    """Text column with missing values as None (stored as NULL, never the string 'nan')."""
    return s.astype(object).where(s.notna(), None)


def run_holdings_13f(params: dict, ctx: Any) -> dict:
    ciks = [sec.cik10(c) for c in params.get("ciks") or [f["cik"] for f in load_universes()["notable_13f_filers"]]]

    def read_state(con: Any, ks: list[str]) -> dict[str, KeyState]:
        return {c: KeyState(d) for c, d in con.execute(
            "SELECT cik, max(filed) FROM holdings_13f WHERE list_contains(?, cik) GROUP BY cik", [ks]).fetchall()}

    def fetch(cik: str, state: KeyState, s: Any):
        yield sec.fetch_13f(cik, s).data

    def write(con: Any, cik: str, state: KeyState, d: dict[str, Any]) -> Written:
        h = d["holdings"].copy()
        frame = pd.DataFrame({
            "cik": cik, "filer": d["filer"], "accession": d["accession"], "form": d["form"],
            "period": pd.to_datetime(d["period"]), "filed": pd.to_datetime(d["filed"]),
            "cusip": h["cusip"], "put_call": h["put_call"].fillna(""), "issuer": h["issuer"], "title": h["title"],
            "ticker": _nulls(h["ticker"]), "shares": h["shares"].astype(float), "shares_type": _nulls(h["shares_type"]),
            "value_usd": h["value_usd"].astype(float), "weight": h["weight"].astype(float)})
        insert_frame(con, "INSERT OR REPLACE INTO holdings_13f SELECT cik, filer, accession, form, CAST(period AS DATE), "
                          "CAST(filed AS DATE), cusip, put_call, issuer, title, ticker, shares, shares_type, "
                          "value_usd, weight FROM incoming", frame)
        return Written(len(frame), date.fromisoformat(d["filed"]),
                       {"source": "sec-edgar", "accession": d["accession"], "period": d["period"],
                        "notes": d.get("notes")})

    return run_ingest(dataset="holdings_13f", keys=ciks, ctx=ctx, read_state=read_state, fetch=fetch, write=write,
                      notes=["13F covers long US-listed 13(f) securities only, reported ~45 days after quarter end"])
