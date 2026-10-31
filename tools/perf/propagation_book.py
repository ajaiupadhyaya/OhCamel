#!/usr/bin/env python3
"""Write docs/perf/propagation_book.csv: the real closes `make propagation` runs.

The recorder is OCaml and the fixtures are parquet, so this flattens the last 62
sessions of the nine committed ETFs, with the daily change in the 10-year Treasury
yield (the engine's factor), into a file the recorder can read. It runs only when
the fixtures change. No network and nothing invented: every value is a fixture's.

    quant/.venv/bin/python tools/perf/propagation_book.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "fixtures" / "history"
SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK")
SESSIONS = 62
OUT = ROOT / "docs" / "perf" / "propagation_book.csv"


def main() -> None:
    closes = pd.DataFrame({s: pd.read_parquet(HISTORY / f"{s}.parquet")["close_adj"] for s in SYMBOLS}).dropna()
    # In decimal: FRED's series is in percentage points.
    change = pd.read_parquet(ROOT / "fixtures" / "macro" / "macro.parquet")["dgs10"].ffill().diff() / 100.0
    frame = closes.tail(SESSIONS).copy()
    frame.insert(0, "dgs10_change", change.reindex(frame.index).fillna(0.0))
    frame.index = frame.index.strftime("%Y-%m-%d")
    frame.index.name = "date"
    frame.to_csv(OUT, float_format="%.8g")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(frame)} sessions to {frame.index[-1]}")


if __name__ == "__main__":
    main()
