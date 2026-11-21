"""A fixture warehouse for the survivorship gate (completion task 3.8), built from committed real data only.

``build_fixture_warehouse`` gives the nine committed ETF series (fixtures/history, 2016-06-01..2026-06-01) and the
real S&P 500 membership from the committed changes table. The gate's coverage is read from ``universe_membership``
and ``bars_daily``; to put a known share of exits inside and outside the bars, this harness adds a TEST universe,
``fixture-xs``, whose stints are spans of those nine real series: each covered exit falls inside its series' bars,
each uncovered exit after 2026-06-01, where the committed bars stop. The universe is a test construction (it is not
an index), the prices are the committed ones, and no number anywhere is reported from it.

Acceptance is written with plain SQL, the way only ``tools/accept_delisted.py`` writes it in production: there is
no helper for it in the package.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from ohcamel_quant.warehouse.db import open_rw
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
from ohcamel_quant.warehouse.ingest.base import insert_frame

UNIVERSE = "fixture-xs"
NINE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]
FIRST = date(2017, 1, 3)


def stints(covered: int, uncovered: int) -> pd.DataFrame:
    """``covered`` exits inside the committed bars, ``uncovered`` exits after them. Covered stints run 760 days with
    a 60-day gap, round-robin over the nine series, so several names are members on most dates."""
    rows = []
    for i in range(covered):
        t = NINE[i % len(NINE)]
        k = i // len(NINE)
        start = FIRST + timedelta(days=820 * k + 13 * (i % len(NINE)))
        rows.append((t, start, start + timedelta(days=760)))
    for j in range(uncovered):
        rows.append((NINE[j % len(NINE)], date(2026, 3, 2), date(2026, 9, 1)))   # exit after the bars end
    assert all(e <= date(2026, 6, 1) for _, _, e in rows[:covered])
    return pd.DataFrame([{"universe": UNIVERSE, "ticker": t, "start": s, "end": e, "source": "test universe",
                          "series": t} for t, s, e in rows])


def warehouse(tmp_path: Path, covered: int, uncovered: int, *, name: str = "w.duckdb") -> Path:
    path = build_fixture_warehouse(tmp_path / name)
    with open_rw(path) as con:
        insert_frame(con, 'INSERT INTO universe_membership (universe, ticker, "start", "end", source, series) '
                          'SELECT universe, ticker, CAST("start" AS DATE), CAST("end" AS DATE), source, series '
                          "FROM incoming", stints(covered, uncovered))
    return path


def record_acceptance(path: Path, list_sha256: str, passed: int = 25, total: int = 26) -> None:
    with open_rw(path) as con:
        con.execute("INSERT INTO delisted_acceptance VALUES (?, ?, ?, ?, ?, ?)",
                    [datetime.now(UTC).replace(tzinfo=None), "eodhd", passed, total, list_sha256, "{}"])
