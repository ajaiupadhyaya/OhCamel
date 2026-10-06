"""``ingest.fred_warehouse`` and ``ingest.factors`` (compute plan C5).

FRED: the 26 dashboard series (macro/series.json) plus the 11 Treasury
constant-maturity tenors MarketData's curve and risk-free rate read -- one
request per series, each replaced in full (FRED revises history), holiday
blanks not stored. Factors: Kenneth French FF5 daily and momentum daily as
DECIMALS, one ``factors.dataset`` per file, replaced in full.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ...data import fred as fred_src
from ...data import french, http
from ...data.base import DataUnavailable
from ...data.market import TREASURY_SERIES
from ...macro.dashboard import load_series_config
from .base import KeyState, Written, insert_frame, run_ingest, utcnow

DEFAULT_SERIES = list(dict.fromkeys([s["id"] for s in load_series_config()["series"]] + list(TREASURY_SERIES)))
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

    return run_ingest(dataset="fred", keys=list(dict.fromkeys(series)), ctx=ctx, read_state=_last_logged("fred"),
                      fetch=fetch, write=write, notes=["levels as published by FRED (yields in percent); not forward-filled"])


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
