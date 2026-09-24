"""Committed real-data fixtures: the offline data source.

These files are real market data already committed to the repository (see
``fixtures/history/README.md`` and ``docs/crisis/*.csv`` for provenance):

* ``fixtures/history/<SYM>.parquet`` -- Alpaca daily bars, 2016-06-01..2026-06-01,
  for SPY QQQ IWM TLT IEF GLD XLE XLF XLK (columns open/high/low/close/volume/close_adj).
* ``fixtures/macro/macro.parquet`` -- FRED ``vix`` (VIXCLS), ``dgs10``, ``dgs2``.
* ``docs/crisis/{gfc,covid,rates-2022}.csv`` -- Yahoo adjusted closes of
  AAPL CVX JPM MSFT NVDA XOM over three crisis windows.

They are used when ``Settings.offline`` is true (tests, CI) and as the last
resort of the price chain online. Nothing here is generated.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..config import get_settings
from .base import Provenance

# FRED ids -> the column names used in macro.parquet.
FRED_FIXTURE_COLUMNS = {"VIXCLS": "vix", "DGS10": "dgs10", "DGS2": "dgs2"}


def _root() -> Path:
    return get_settings().fixtures_dir


def fixture_symbols() -> list[str]:
    return sorted(p.stem for p in (_root() / "history").glob("*.parquet"))


# Split ratios a one-session close-to-close jump is tested against.
_SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 20, 1.5, 2 / 3)


def _repair(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Repair two vendor defects found in the committed files, and say so.

    1. Unadjusted splits. The committed Alpaca ``close_adj`` equals ``close``
       and misses at least one split (XLK 2-for-1, 2025-12-05), which reads as
       a -50% session. A split is recognised only when the overnight ratio
       prev_close/open is within 3% of a standard split ratio AND the
       close-to-close move exceeds 30% -- a price series' own evidence, applied
       as the vendor's adjusted series would: earlier prices divided by the
       ratio, earlier volumes multiplied by it.
    2. Decimal-shift prints. A high/low more than 25% outside the session's
       open/close (e.g. SPY 2026-02-02 low 69.005 on a 695 close) is replaced
       by the nearer of open/close.
    """
    notes: list[str] = []
    df = df.copy()
    prev_close = df["close"].shift(1)
    jump = df["close"] / prev_close - 1.0
    overnight = prev_close / df["open"]
    for day in df.index[(jump.abs() > 0.30).fillna(False)]:
        ratio = float(overnight.loc[day])
        match = min(_SPLIT_RATIOS, key=lambda r: abs(ratio / r - 1.0))
        if abs(ratio / match - 1.0) <= 0.03:
            before = df.index < day
            for col in ("open", "high", "low", "close", "adj_close"):
                df.loc[before, col] = df.loc[before, col] / match
            df.loc[before, "volume"] = df.loc[before, "volume"] * match
            notes.append(f"split-adjusted {match:g}:1 before {day.date()} (unadjusted in vendor file)")
    body_lo = df[["open", "close"]].min(axis=1)
    body_hi = df[["open", "close"]].max(axis=1)
    bad_lo = df["low"] < 0.75 * body_lo
    bad_hi = df["high"] > 1.25 * body_hi
    if bad_lo.any() or bad_hi.any():
        df.loc[bad_lo, "low"] = body_lo[bad_lo]
        df.loc[bad_hi, "high"] = body_hi[bad_hi]
        days = sorted({d.date().isoformat() for d in df.index[bad_lo | bad_hi]})
        notes.append(f"bad high/low print(s) replaced by the session body on {', '.join(days)}")
    return df, notes


@lru_cache(maxsize=64)
def _load_history(symbol: str) -> tuple[pd.DataFrame, tuple[str, ...]]:
    path = _root() / "history" / f"{symbol}.parquet"
    df = pd.read_parquet(path)
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    df = df.rename(columns={"close_adj": "adj_close"})
    df = df[["open", "high", "low", "close", "adj_close", "volume"]].astype(float)
    df, notes = _repair(df)
    return df, tuple(notes)


def history(symbol: str) -> tuple[pd.DataFrame, Provenance] | None:
    """OHLCV for one committed symbol, or None when it is not a fixture."""
    symbol = symbol.upper()
    path = _root() / "history" / f"{symbol}.parquet"
    if not path.exists():
        return None
    meta_path = path.with_name(path.name + ".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    df, notes = _load_history(symbol)
    detail = {"path": str(path.relative_to(_root().parent)), "symbol": symbol,
              "adjustment": "vendor close_adj is split-only; dividends are NOT reinvested"}
    if notes:
        detail["repairs"] = list(notes)
    prov = Provenance(
        source=f"fixture:{meta.get('source', 'unknown')}",
        fetched_at=meta.get("fetched_at", ""),
        detail=detail,
    )
    return df.copy(), prov


@lru_cache(maxsize=4)
def _load_crisis(name: str) -> pd.DataFrame:
    path = _root().parent / "docs" / "crisis" / f"{name}.csv"
    df = pd.read_csv(path, comment="#", parse_dates=["date"], index_col="date")
    return df.astype(float)


def crisis_closes() -> dict[str, pd.DataFrame]:
    """Adjusted closes for the three committed crisis windows."""
    return {n: _load_crisis(n).copy() for n in ("gfc", "covid", "rates-2022")}


def macro(series_ids: list[str]) -> tuple[pd.DataFrame, Provenance] | None:
    """FRED series available in the committed macro fixture, by FRED id."""
    path = _root() / "macro" / "macro.parquet"
    wanted = [s for s in series_ids if s in FRED_FIXTURE_COLUMNS]
    if not wanted or not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    out = df[[FRED_FIXTURE_COLUMNS[s] for s in wanted]].copy()
    out.columns = wanted
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    return out.astype(float), Provenance(
        source="fixture:fred", fetched_at=meta.get("fetched_at", ""), detail={"series": wanted}
    )
