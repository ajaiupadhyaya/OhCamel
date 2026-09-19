"""Dev-mode feed: a replay of committed REAL bars, never synthetic ones.

The charter forbids synthetic market data anywhere a number is reported, and
a replay that could be quietly pointed at generated prices would be the first
place that rule eroded. So this module refuses any bar file that does not
carry a provenance sidecar saying, in so many words, that it is historical and
not synthetic. The sidecar format is fdq's, so a fixture sliced from fdq's
cache is accepted as-is and anything else has to earn its way in.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas as pd

from ohcamel_research import REPO_ROOT

DEFAULT_FIXTURES = REPO_ROOT / "fixtures" / "bars"
FIELDS = ["open", "high", "low", "close", "volume"]


class ProvenanceError(RuntimeError):
    """A bar file without acceptable provenance. Refused, never worked around."""


def read_provenance(parquet: Path) -> dict[str, object]:
    sidecar = parquet.with_name(parquet.name + ".meta.json")
    if not sidecar.exists():
        raise ProvenanceError(f"{parquet.name}: no provenance sidecar ({sidecar.name}); refusing")
    doc = json.loads(sidecar.read_text())
    if doc.get("synthetic", True) is not False:
        raise ProvenanceError(f"{parquet.name}: sidecar does not say synthetic=false; refusing")
    if doc.get("data_kind") != "historical":
        raise ProvenanceError(
            f"{parquet.name}: data_kind is {doc.get('data_kind')!r}, not historical"
        )
    if doc.get("symbol") not in (None, parquet.stem):
        raise ProvenanceError(f"{parquet.name}: sidecar is for {doc.get('symbol')!r}")
    return doc


def load_bars(fixtures: Path = DEFAULT_FIXTURES) -> pd.DataFrame:
    """All bars under ``fixtures`` in long form, sorted by (date, symbol).

    Columns: date (datetime.date), symbol, open, high, low, close, volume.
    Every file is checked for provenance before it is read.
    """
    frames = []
    for path in sorted(fixtures.glob("*.parquet")):
        read_provenance(path)
        df = pd.read_parquet(path)[FIELDS].copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        df["symbol"] = path.stem
        frames.append(df.reset_index())
    if not frames:
        raise ProvenanceError(f"no bar files under {fixtures}")
    out = pd.concat(frames, ignore_index=True)
    out["date"] = out["date"].dt.date
    # mergesort is stable, so equal dates keep the alphabetical symbol order
    # the sorted() glob produced: the replay is deterministic.
    return out.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)


def slice_bars(
    bars: pd.DataFrame,
    start: date | None = None,
    end: date | None = None,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    m = pd.Series(True, index=bars.index)
    if start is not None:
        m &= bars["date"] >= start
    if end is not None:
        m &= bars["date"] <= end
    if symbols:
        m &= bars["symbol"].isin(symbols)
    return bars.loc[m].reset_index(drop=True)


def iter_bars(bars: pd.DataFrame) -> Iterator[dict[str, object]]:
    """The wire format: one dict per bar, ``type: bar``."""
    for r in bars.itertuples(index=False):
        yield {
            "type": "bar",
            "symbol": r.symbol,
            "date": r.date.isoformat(),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
        }


def to_jsonl(bars: pd.DataFrame) -> str:
    return "".join(json.dumps(b, separators=(",", ":")) + "\n" for b in iter_bars(bars))
