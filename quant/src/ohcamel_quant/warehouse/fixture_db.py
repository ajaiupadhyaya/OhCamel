"""A small warehouse built from the committed real fixtures, with no network
(compute plan gate GC: offline tests and CI need no vendor). Bars: the nine
fixture ETFs (fixtures/history). FRED: VIXCLS, DGS10, DGS2 (fixtures/macro).
``ingest_log`` rows carry each fixture's own fetched_at as ran_at."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ..data import fixtures
from .db import open_rw
from .ingest.base import SUMMARY_KEY, insert_frame, log_row


def _ts(iso: str) -> datetime | None:
    return datetime.fromisoformat(iso).astimezone(UTC).replace(tzinfo=None) if iso else None


def build_fixture_warehouse(path: Path) -> Path:
    with open_rw(path) as con:
        con.execute("BEGIN TRANSACTION")
        last_bars, ran_bars = None, None
        for sym in fixtures.fixture_symbols():
            df, prov = fixtures.history(sym)
            ran = _ts(prov.fetched_at)
            frame = df.reset_index()
            frame.insert(0, "ticker", sym)
            frame["source"] = prov.source
            frame["fetched_at"] = ran
            insert_frame(con, "INSERT OR REPLACE INTO bars_daily SELECT ticker, CAST(date AS DATE), open, high, "
                              "low, close, adj_close, volume, source, fetched_at FROM incoming", frame)
            asof = df.index[-1].date()
            log_row(con, "bars_daily", sym, "ok", len(frame), json.dumps({"source": prov.source, "fixture": True}),
                    asof, ran_at=ran)
            last_bars, ran_bars = max(filter(None, [last_bars, asof])), ran
        log_row(con, "bars_daily", SUMMARY_KEY, "ok", 0, json.dumps({"fixture": True}), last_bars, ran_at=ran_bars)
        hit = fixtures.macro(list(fixtures.FRED_FIXTURE_COLUMNS))
        if hit is not None:
            macro, prov = hit
            ran = _ts(prov.fetched_at)
            for sid in macro.columns:
                s = macro[sid].dropna()
                frame = pd.DataFrame({"series": sid, "date": s.index, "value": s.to_numpy(float), "fetched_at": ran})
                insert_frame(con, "INSERT OR REPLACE INTO fred SELECT series, CAST(date AS DATE), value, fetched_at "
                                  "FROM incoming", frame)
                log_row(con, "fred", sid, "ok", len(frame), json.dumps({"source": prov.source, "fixture": True}),
                        s.index[-1].date(), ran_at=ran)
            log_row(con, "fred", SUMMARY_KEY, "ok", 0, json.dumps({"fixture": True}),
                    macro.dropna(how="all").index[-1].date(), ran_at=ran)
        con.execute("COMMIT")
    return path
