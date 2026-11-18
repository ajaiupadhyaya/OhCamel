"""``ingest.corp_actions`` (completion task 3.2): every split and cash dividend of the universe, from Yahoo.

**Source.** Yahoo's chart endpoint (``data/prices.py``'s ``yahoo`` vendor, no key, no credential) with
``events=div|split``: the same vendor, and for most names the same response, that serves ``bars_daily``
(its default window starts in 1990, before Alpaca's history, so Yahoo comes first). The events do not
depend on the bar interval, so the request asks for monthly bars: a 1990-onward response is ~50 KB
instead of ~900 KB. Each event becomes one ``corp_actions`` row:

* a split: ``kind='split'``, ``ratio`` = new shares per old share (numerator over denominator);
* a dividend: ``kind='dividend'``, ``cash`` = per share AS PAID on the ex-date. Yahoo states amounts adjusted
  for every later split, so each is multiplied back by the ratios of the splits after it in the same
  response (the window always runs to today, so every such split is in it).

**Windows.** Nightly (weekdays 18:20, before the 18:30 bars): the last ``LOOKBACK_DAYS`` calendar days,
enough for a week of missed nights. ``{"full": true}``: from 1990-01-01, the backfill. ``{"full": true,
"missing": true}`` fetches only tickers with no successful full fetch on record, so a killed backfill is
resumed by running it again.

**Idempotent.** Rows are keyed on ``(ticker, ex_date, kind)``. The same statement again changes nothing.
A different statement of a stored action (ratio or cash moved by more than ``REVISION_RTOL`` relative) is a
REVISION: the old and new values go to ``corp_action_revisions`` and the row takes the vendor's latest values
(the vendor's adjusted series uses them, so the drift check must too). A stored action the vendor no
longer lists inside a window it was asked for is WITHDRAWN: a revision row with new values NULL, and the
stored row is kept for the owner to judge. Both are counted in the job's notes.

**Failure.** One ticker's response is written in one transaction or not at all (``ingest.base``). A vendor
error or an empty chart fails that key with the vendor's words. Every key failing fails the job (the jobs
system retries ingest kinds twice). ``data_asof`` is the session the response reached (the last trade's
exchange-local date): the actions are known through it, whatever the date of the latest action.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote as urlquote

import numpy as np
import pandas as pd

from ...data import http, prices
from ...data.base import DataUnavailable, Provenance
from ..db import open_rw, warehouse_path
from ..freshness import NY
from .base import IngestFailed, KeyState, Written, run_ingest, utcnow

DATASET = "corp_actions"
COLUMNS = ["ex_date", "kind", "ratio", "cash"]
LOOKBACK_DAYS = 60
FULL_START = date(1990, 1, 1)
REVISION_RTOL = 1e-6


def _now() -> datetime:
    return datetime.now(UTC)


def parse_yahoo_events(payload: dict[str, Any]) -> pd.DataFrame:
    """One chart response's events as ``corp_actions`` rows (``ex_date, kind, ratio, cash``), sorted.

    Raises :class:`DataUnavailable` on a chart error or an empty result (the vendor said nothing)."""
    chart = (payload or {}).get("chart") or {}
    results = chart.get("result") or []
    if not results:
        err = chart.get("error") or {}
        raise DataUnavailable(f"yahoo: {err.get('code', 'error')}: {err.get('description', 'empty chart result')}")
    splits, dividends = prices.yahoo_events(results[0])
    rows = [{"ex_date": pd.Timestamp(d), "kind": "split", "ratio": float(r), "cash": np.nan} for d, r in splits]
    for d, amount in dividends:
        later = float(np.prod([float(r) for sd, r in splits if sd > d])) if splits else 1.0
        rows.append({"ex_date": pd.Timestamp(d), "kind": "dividend", "ratio": np.nan, "cash": float(amount) * later})
    out = pd.DataFrame(rows, columns=COLUMNS)
    out["ex_date"] = pd.to_datetime(out["ex_date"]).astype("datetime64[ns]")
    split_dup = out[out["kind"] == "split"].duplicated(["ex_date"], keep=False)
    if split_dup.any():   # two splits on one session: no sum is right, so refuse the response
        days = sorted({str(d.date()) for d in out.loc[split_dup[split_dup].index, "ex_date"]})
        raise DataUnavailable(f"yahoo: more than one split on one session ({', '.join(days)})")
    # Two dividends on one session (a regular and a special, say) are one ex-date's total cash: the
    # price falls by both, and the table holds one dividend per (ticker, ex_date)
    divs = out[out["kind"] == "dividend"].groupby("ex_date", as_index=False).agg(cash=("cash", "sum"))
    divs["kind"], divs["ratio"] = "dividend", np.nan
    out = pd.concat([out[out["kind"] == "split"], divs[COLUMNS]], ignore_index=True)
    return out.sort_values(["ex_date", "kind"], kind="stable").reset_index(drop=True)


def _reached(payload: dict[str, Any], fallback: date) -> date:
    """The session the response reached: the last trade's exchange-local date (meta.regularMarketTime)."""
    r = payload["chart"]["result"][0]
    meta = r.get("meta") or {}
    tz = meta.get("exchangeTimezoneName") or "America/New_York"
    t = meta.get("regularMarketTime") or (r.get("timestamp") or [None])[-1]
    if t is None:
        return fallback
    return pd.Timestamp(int(t), unit="s", tz="UTC").tz_convert(tz).date()


@dataclass
class ActionsPayload:
    actions: pd.DataFrame
    window_start: date
    reached: date
    prov: Provenance
    full: bool


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def fetch_actions(ticker: str, settings: Any, *, start: date, today: date, full: bool) -> ActionsPayload:
    sym = prices.yahoo_symbol(ticker)
    url = prices.YAHOO_CHART + urlquote(sym, safe="")
    params = {"period1": _epoch(start), "period2": _epoch(today + timedelta(days=1)), "interval": "1mo",
              "events": "div|split"}
    payload = http.get_json(url, settings, params=params, headers=prices.YAHOO_HEADERS, source="yahoo")
    acts = parse_yahoo_events(payload)
    reached = _reached(payload, today)
    prov = Provenance.now("yahoo", url=url, symbol=sym, events="div|split", window_start=start.isoformat(),
                          reached=reached.isoformat(),
                          convention="split ratio = new shares per old; dividend cash = per share as paid "
                                     "on the ex-date (Yahoo's split-adjusted amount times the later splits)")
    return ActionsPayload(acts, start, reached, prov, full)


def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
    rows = con.execute("SELECT key, max(data_asof) FROM ingest_log WHERE dataset = ? AND status = 'ok' "
                       "AND list_contains(?, key) GROUP BY key", [DATASET, keys]).fetchall()
    return {k: KeyState(d) for k, d in rows}


def _same(a: Any, b: Any) -> bool:
    a_nan, b_nan = a is None or (isinstance(a, float) and np.isnan(a)), b is None or (isinstance(b, float) and np.isnan(b))
    if a_nan or b_nan:
        return a_nan and b_nan
    return abs(float(a) - float(b)) <= REVISION_RTOL * max(abs(float(a)), abs(float(b)))


def _val(x: Any) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def write_actions(con: Any, ticker: str, state: KeyState, p: ActionsPayload) -> Written:
    now = utcnow()
    # Every stored row the response could name: a monthly response lists the events of the whole month that
    # holds period1 (tests/data/yahoo_chart_XLK_1mo_2026-11-17.json.gz), so events before the window start
    # arrive too; each is compared with what is stored (a revision is still a revision), never re-inserted.
    since = min([p.window_start, *(pd.Timestamp(d).date() for d in p.actions["ex_date"])])
    stored = {(pd.Timestamp(d), k): (r, c) for d, k, r, c in con.execute(
        "SELECT ex_date, kind, ratio, cash FROM corp_actions WHERE ticker = ? AND ex_date >= ?",
        [ticker, since]).fetchall()}
    seen: set[tuple[pd.Timestamp, str]] = set()
    new = revised = 0
    for a in p.actions.itertuples(index=False):
        key = (pd.Timestamp(a.ex_date), str(a.kind))
        seen.add(key)
        ratio, cash = _val(a.ratio), _val(a.cash)
        if key not in stored:
            con.execute("INSERT INTO corp_actions VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [ticker, key[0].date(), key[1], ratio, cash, p.prov.source, now])
            new += 1
            continue
        old_ratio, old_cash = stored[key]
        if _same(old_ratio, ratio) and _same(old_cash, cash):
            continue
        con.execute("INSERT INTO corp_action_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [ticker, key[0].date(), key[1], old_ratio, old_cash, ratio, cash, p.prov.source, now])
        con.execute("UPDATE corp_actions SET ratio = ?, cash = ?, source = ?, fetched_at = ? "
                    "WHERE ticker = ? AND ex_date = ? AND kind = ?",
                    [ratio, cash, p.prov.source, now, ticker, key[0].date(), key[1]])
        revised += 1
    withdrawn = 0
    for key, (old_ratio, old_cash) in sorted(stored.items()):
        if key in seen or key[0].date() > p.reached or key[0].date() < p.window_start:
            continue                    # withdrawn means: absent from the window the vendor was asked for
        done = con.execute("SELECT count(*) FROM corp_action_revisions WHERE ticker = ? AND ex_date = ? AND kind = ? "
                           "AND new_ratio IS NULL AND new_cash IS NULL", [ticker, key[0].date(), key[1]]).fetchone()[0]
        if not done:   # said once, not every night the vendor keeps leaving it out
            con.execute("INSERT INTO corp_action_revisions VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                        [ticker, key[0].date(), key[1], old_ratio, old_cash, p.prov.source, now])
            withdrawn += 1
    detail = {"source": p.prov.source, "full": p.full, "window_start": p.window_start.isoformat(),
              "listed": len(p.actions), "new": new, "revised": revised, "withdrawn": withdrawn}
    return Written(new + revised + withdrawn, p.reached, detail)


def _universe(path: Any) -> list[str]:
    from .bars import universe_tickers

    return universe_tickers(path)


def _not_backfilled(path: Any, tickers: list[str]) -> list[str]:
    with open_rw(path) as con:
        done = {k for (k,) in con.execute(
            "SELECT DISTINCT key FROM ingest_log WHERE dataset = ? AND status = 'ok' "
            "AND json_extract_string(detail, '$.full') = 'true' AND list_contains(?, key)",
            [DATASET, tickers]).fetchall()}
    return [t for t in tickers if t not in done]


def run_corp_actions(params: dict, ctx: Any) -> dict:
    settings = ctx.market.settings
    path = warehouse_path(settings)
    full = bool(params.get("full", False))
    given = [t.upper().strip() for t in params.get("tickers") or [] if str(t).strip()]
    tickers = list(dict.fromkeys(given or _universe(path)))
    if not tickers:
        raise IngestFailed(f"{DATASET}: no universe -- run ingest.universes first")
    if full and params.get("missing"):
        tickers = _not_backfilled(path, tickers)
        if not tickers:
            return {"data_asof": None, "provenance": [], "notes": ["every ticker already has a full fetch on record"],
                    "survivorship": None, "tables": {}}
    today = _now().astimezone(NY).date()
    start = FULL_START if full else today - timedelta(days=LOOKBACK_DAYS)
    notes = [f"Yahoo chart events (splits, cash dividends) from {start.isoformat()} to today; dividend cash is "
             "per share as paid on the ex-date; a changed or withdrawn action is kept in corp_action_revisions"]
    out = run_ingest(dataset=DATASET, keys=tickers, ctx=ctx, read_state=read_state,
                     fetch=lambda key, state, s: [fetch_actions(key, s, start=start, today=today, full=full)],
                     write=write_actions, notes=notes)
    summary = out["tables"].get("ingest_summary")
    if summary is not None and len(summary):
        details = [json.loads(d) for d in summary["detail"]]
        rev = sum(int(d.get("revised", 0)) for d in details)
        gone = sum(int(d.get("withdrawn", 0)) for d in details)
        if rev or gone:
            names = sorted(str(k) for k, d in zip(summary["key"], details, strict=True)
                           if d.get("revised") or d.get("withdrawn"))
            out["notes"].append(f"{rev} action(s) revised and {gone} withdrawn by the vendor ({', '.join(names[:20])}): "
                                "old and new values are in corp_action_revisions; the table holds the latest statement")
    return out
