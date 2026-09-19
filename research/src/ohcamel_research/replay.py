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
from pathlib import Path

import pandas as pd

from ohcamel_research import REPO_ROOT

# The loader, the slicer and the provenance check live under battery/, where
# the manifest's hash can see them (the battery's verdict reads bars through
# exactly these), and are imported back here so replay's behaviour is
# unchanged. The names are re-exported for every existing caller.
from ohcamel_research.battery.data import (
    FIELDS,
    ProvenanceError,
    read_provenance,
    slice_bars,
)
from ohcamel_research.battery.data import load_bars as _load_bars

__all__ = [
    "DEFAULT_FIXTURES",
    "FIELDS",
    "ProvenanceError",
    "iter_bars",
    "load_bars",
    "read_provenance",
    "slice_bars",
    "to_jsonl",
]

DEFAULT_FIXTURES = REPO_ROOT / "fixtures" / "bars"


def load_bars(fixtures: Path = DEFAULT_FIXTURES) -> pd.DataFrame:
    """All bars under ``fixtures`` in long form, sorted by (date, symbol).

    Columns: date (datetime.date), symbol, open, high, low, close, volume.
    Every file is checked for provenance before it is read. The work is
    ``battery.data.load_bars``'s; this keeps replay's default directory.
    """
    return _load_bars(fixtures)


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
