"""``ingest.fred_warehouse`` and ``ingest.factors`` (compute plan C5).

FRED: the 26 dashboard series (macro/series.json) plus the 11 Treasury
constant-maturity tenors MarketData's curve and risk-free rate read -- one
request per series, each replaced in full (FRED revises history), holiday
blanks not stored. Completion task 3.5: the revised series a model reads
(``VINTAGE_SERIES``) also have every published vintage APPENDED to
``fred_vintages`` -- never deleted, never updated -- so a model can read them as
they stood on any date (``readers.fred_asof``). Factors: Kenneth French FF5 daily and momentum daily as
DECIMALS, one ``factors.dataset`` per file, replaced in full.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pandas as pd

from ...data import fred as fred_src
from ...data import french, http
from ...data.base import DataUnavailable
from ...data.market import TREASURY_SERIES
from ...macro.dashboard import load_series_config
from .base import IngestFailed, KeyState, Written, insert_frame, log_row, run_ingest, utcnow

# Model inputs that are not dashboard rows: BAA10Y is EXP-Q02 v2's credit spread (long history, unlike
# FRED's ICE BofA series, which it serves only from 2023-10).
MODEL_SERIES = ("BAA10Y",)
DEFAULT_SERIES = list(dict.fromkeys([s["id"] for s in load_series_config()["series"]] + list(TREASURY_SERIES)
                                    + list(MODEL_SERIES)))
#: Revised series a model reads, kept as every vintage: UNRATE (the Sahm rule, macro/recession.py). NFCI is
#: re-estimated over its whole history every week (each vintage is a new 2,800-row series, and ALFRED has them
#: only from 2011), so the +NFCI probit stays an in-sample comparison instead; USREC's revisions are the NBER
#: announcements themselves (recession.usrec_known_at), and Treasury yields are never revised.
VINTAGE_SERIES = ("UNRATE",)
#: The first ALFRED vintage of each, for a keyless backfill (the series' ALFRED page; checked 2026-11-19).
FIRST_VINTAGE = {"UNRATE": date(1960, 3, 15)}
FACTOR_FILES = {"ff5_daily": french.FILES["ff5"], "mom_daily": french.FILES["mom"]}


def _last_logged(dataset: str):
    """read_state from ingest_log: each key's last recorded data_asof (kept on failure)."""
    def read_state(con: Any, keys: list[str]) -> dict[str, KeyState]:
        rows = con.execute("SELECT key, arg_max(data_asof, ran_at) FROM ingest_log WHERE dataset = ? "
                           "AND list_contains(?, key) GROUP BY key", [dataset, keys]).fetchall()
        return {k: KeyState(d) for k, d in rows}
    return read_state


def run_fred(params: dict, ctx: Any) -> dict:
    raw = params.get("series") or DEFAULT_SERIES
    if isinstance(raw, str):  # Lane B's shape {"series": "DGS10"}: one id, never its characters
        raw = raw.split(",")
    series = [str(s).upper().strip() for s in raw if str(s).strip()]

    def fetch(key: str, state: KeyState, s: Any):
        if s.fred_api_key:
            ser, prov = fred_src._fetch_api(key, s)
        else:
            got, prov = fred_src._fetch_csv([key], s)
            if key not in got:
                raise DataUnavailable(f"fred: series {key} not found or empty")
            ser = got[key]
        yield ser.dropna(), prov

    def write(con: Any, key: str, state: KeyState, payload: Any) -> Written:
        ser, prov = payload
        frame = pd.DataFrame({"series": key, "date": ser.index, "value": ser.to_numpy(float), "fetched_at": utcnow()})
        con.execute("DELETE FROM fred WHERE series = ?", [key])
        insert_frame(con, "INSERT INTO fred SELECT series, CAST(date AS DATE), value, fetched_at FROM incoming", frame)
        last = con.execute("SELECT max(date) FROM fred WHERE series = ?", [key]).fetchone()[0]
        return Written(len(frame), last, {"source": "fred", "endpoint": prov.detail.get("endpoint")})

    spec = run_ingest(dataset="fred", keys=list(dict.fromkeys(series)), ctx=ctx, read_state=_last_logged("fred"),
                      fetch=fetch, write=write, notes=["levels as published by FRED (yields in percent); not forward-filled"])
    vkeys = [s for s in dict.fromkeys(series) if s in VINTAGE_SERIES]
    if not vkeys:
        return spec
    try:
        vspec = run_vintages(vkeys, ctx)
    except IngestFailed as e:
        spec["notes"].append(f"fred_vintages not appended: {e}")
        return spec
    spec["notes"] += vspec["notes"]
    spec["provenance"] += vspec["provenance"]
    spec["tables"]["ingest_summary"] = pd.concat([spec["tables"]["ingest_summary"],
                                                  vspec["tables"]["ingest_summary"]], ignore_index=True)
    return spec


def append_vintages(con: Any, series: str, frame: pd.DataFrame, source: str) -> int:
    """Append to ``fred_vintages`` each row of ``frame`` (``date, realtime_start, value``) that says something
    new: an observation not stored, or a value other than the one in effect at its ``realtime_start``. Nothing
    is deleted or updated. Returns the rows written."""
    if frame.empty:
        return 0
    stored = con.execute("SELECT date, realtime_start, value FROM fred_vintages WHERE series = ?", [series]).df()
    inc = frame[["date", "realtime_start", "value"]].copy()
    inc["new"] = True
    stored["new"] = False
    allr = pd.concat([stored, inc], ignore_index=True)
    allr["date"] = pd.to_datetime(allr["date"])
    allr["realtime_start"] = pd.to_datetime(allr["realtime_start"])
    # a stored row wins its (date, realtime_start); then, in time order, a new row that repeats the value in
    # effect before it says nothing
    allr = allr.sort_values(["date", "realtime_start", "new"]).drop_duplicates(["date", "realtime_start"])
    prev = allr.groupby("date")["value"].shift()
    keep = allr[allr["new"] & (prev.isna() | allr["value"].ne(prev))].copy()
    if keep.empty:
        return 0
    keep.insert(0, "series", series)
    keep["source"] = source
    keep["fetched_at"] = utcnow()
    insert_frame(con, "INSERT INTO fred_vintages SELECT series, CAST(date AS DATE), CAST(realtime_start AS DATE), "
                      "value, source, fetched_at FROM incoming", keep[["series", "date", "realtime_start", "value",
                                                                      "source", "fetched_at"]])
    return len(keep)


def _vintage_source(prov: Any) -> str:
    return "alfred-api" if "api.stlouisfed.org" in str(prov.detail.get("endpoint", "")) else "alfred-graph"


def run_vintages(series: list[str], ctx: Any, vintage_dates: list[date] | None = None) -> dict:
    """``fred_vintages`` for ``series``: with FRED_API_KEY every vintage ALFRED has (exact), else today's (a
    value first seen today is dated today: never earlier than its release). Logged as dataset ``fred_vintages``."""
    def fetch(key: str, state: KeyState, s: Any):
        yield fred_src.fetch_vintages(key, s, vintage_dates)

    def write(con: Any, key: str, state: KeyState, payload: Any) -> Written:
        frame, prov = payload
        n = append_vintages(con, key, frame, _vintage_source(prov))
        last = con.execute("SELECT max(realtime_start) FROM fred_vintages WHERE series = ?", [key]).fetchone()[0]
        return Written(n, last, {"source": _vintage_source(prov), "endpoint": prov.detail.get("endpoint")})

    return run_ingest(dataset="fred_vintages", keys=list(dict.fromkeys(series)), ctx=ctx,
                      read_state=_last_logged("fred_vintages"), fetch=fetch, write=write,
                      notes=["fred_vintages: every published vintage, appended (never deleted); data_asof is the "
                             "newest realtime_start"])


def friday_grid(start: date, end: date) -> list[date]:
    """Every Friday from ``start`` to ``end``: the keyless backfill's vintage dates. The Employment Situation is
    published on a Friday almost always, so a value is dated on its release day or at most six days after."""
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def backfill_vintages(series: str, settings: Any, *, since: date | None = None, until: date | None = None,
                      progress: Any = print) -> dict[str, Any]:
    """The owner's one-off fill of ``fred_vintages`` (docs/runbooks/compute.md). With FRED_API_KEY: one request
    set, exact. Without: ALFRED's graph download on every Friday from the series' first vintage, ``ALFRED_BATCH``
    dates a request, each batch appended in its own short write and its last date logged
    (``ingest_log``: dataset ``fred_vintages``, key ``backfill:<series>``), so a re-run resumes after it. The
    writer is held only while a batch is written, never across a request."""
    from ..db import open_rw, warehouse_path

    sid = series.upper()
    path = warehouse_path(settings)
    key = f"backfill:{sid}"
    if settings.fred_api_key:
        frame, prov = fred_src.fetch_vintages(sid, settings)
        with open_rw(path) as con:
            con.execute("BEGIN TRANSACTION")
            try:
                n = append_vintages(con, sid, frame, _vintage_source(prov))
                log_row(con, "fred_vintages", key, "ok", n, json.dumps({"through": "all", "source": "alfred-api"}),
                        None)
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return {"series": sid, "rows": n, "source": "alfred-api", "batches": 1}
    start = since or FIRST_VINTAGE.get(sid)
    if start is None:
        raise ValueError(f"{sid}: no first vintage known; pass since")
    with open_rw(path) as con:
        row = con.execute("SELECT arg_max(json_extract_string(detail, '$.through'), ran_at) FROM ingest_log "
                          "WHERE dataset = 'fred_vintages' AND key = ? AND status = 'ok' "
                          "AND json_extract_string(detail, '$.source') = 'alfred-graph'", [key]).fetchone()
    if row and row[0] and since is None:
        start = date.fromisoformat(row[0]) + timedelta(days=1)
    grid = friday_grid(start, until or date.today())
    total, batches = 0, 0
    for i in range(0, len(grid), fred_src.ALFRED_BATCH):
        batch = grid[i:i + fred_src.ALFRED_BATCH]
        frame, prov = fred_src.fetch_vintages(sid, settings, batch)
        with open_rw(path) as con:
            con.execute("BEGIN TRANSACTION")
            try:
                n = append_vintages(con, sid, frame, "alfred-graph")
                log_row(con, "fred_vintages", key, "ok", n, json.dumps({"through": batch[-1].isoformat(),
                                                                         "source": "alfred-graph"}), batch[-1])
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        total, batches = total + n, batches + 1
        progress(f"{sid}: vintages {batch[0]}..{batch[-1]}: {n} rows appended")
    return {"series": sid, "rows": total, "source": "alfred-graph", "batches": batches,
            "through": grid[-1].isoformat() if grid else None}


def run_factors(params: dict, ctx: Any) -> dict:
    def fetch(key: str, state: KeyState, s: Any):
        url = f"{french.BASE}{FACTOR_FILES[key]}_CSV.zip"
        tables = french.parse_french_tables(french._read_zip(http.get_bytes(url, s, source="ken-french")))
        if not tables:
            raise DataUnavailable(f"french: {FACTOR_FILES[key]} has no tables")
        yield tables[0][1], url

    def write(con: Any, key: str, state: KeyState, payload: Any) -> Written:
        df, url = payload
        df = df.rename(columns=lambda c: "Mom" if str(c).strip().lower().startswith("mom") else str(c).strip())
        long = (df.rename_axis("date").reset_index()
                .melt(id_vars="date", var_name="factor", value_name="value").dropna(subset=["value"]))
        long.insert(0, "dataset", key)
        con.execute("DELETE FROM factors WHERE dataset = ?", [key])
        insert_frame(con, "INSERT INTO factors SELECT dataset, factor, CAST(date AS DATE), value FROM incoming", long)
        last = con.execute("SELECT max(date) FROM factors WHERE dataset = ?", [key]).fetchone()[0]
        return Written(len(long), last, {"source": "ken-french", "url": url})

    return run_ingest(dataset="factors", keys=list(FACTOR_FILES), ctx=ctx, read_state=_last_logged("factors"),
                      fetch=fetch, write=write, notes=["Kenneth French daily factors, decimals (source: percent)"])
