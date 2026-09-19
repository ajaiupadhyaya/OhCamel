"""Every loader and slicer the battery's verdict depends on, so that every
line of it is hashed into the manifests it produces (``manifest.compute_hashes``
hashes ``battery/`` and nothing else of this package).

``load_bars``, ``slice_bars``, ``read_provenance`` and ``ProvenanceError``
moved here from ``replay.py``, and ``wide`` from ``signal.py``; both modules
import them back from here, so their public behaviour is unchanged.
``load_macro`` and ``dollar_adv`` are the runner's own.

The charter forbids synthetic market data anywhere a number is reported, so
every file read here must carry an fdq provenance sidecar saying, in so many
words, that it is historical and not synthetic. Nothing here reads a path it
was not handed: no module-level default points at a fixture directory, so a
caller always names what it reads.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from ohcamel_research.battery import Refused

FIELDS = ["open", "high", "low", "close", "volume"]

# The "20-day dollar ADV" of ``gates.capacity``'s definition (the desk's own
# participation rule reads a twenty-day ADV too). Kept here, where the ADV is
# built, rather than in ``gates.py``, so that ``replay.py`` -- which imports
# this module -- does not pay for importing fdq and scipy.
ADV_WINDOW_DAYS = 20


class ProvenanceError(Refused, RuntimeError):
    """A bar or macro file without acceptable provenance. Refused, never worked around."""


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


def bar_files(fixtures: Path, symbols: Sequence[str] | None = None) -> list[Path]:
    """The bar files ``load_bars`` reads: every ``*.parquet`` under
    ``fixtures`` when ``symbols`` is None, otherwise exactly
    ``<fixtures>/<SYMBOL>.parquet`` for each symbol named, sorted and
    deduplicated. A named symbol with no file is refused, never skipped.
    The runner hashes exactly this list, so what it hashes is what it read."""
    if symbols is None:
        paths = sorted(fixtures.glob("*.parquet"))
        if not paths:
            raise ProvenanceError(f"no bar files under {fixtures}")
        return paths
    paths = [fixtures / f"{s}.parquet" for s in sorted(set(symbols))]
    for p in paths:
        if not p.is_file():
            raise ProvenanceError(f"{p.stem}: no bar file {p.name} under {fixtures}")
    if not paths:
        raise ProvenanceError(f"no symbols named to read under {fixtures}")
    return paths


def load_bars(fixtures: Path, symbols: Sequence[str] | None = None) -> pd.DataFrame:
    """Bars under ``fixtures`` in long form, sorted by (date, symbol): every
    file when ``symbols`` is None, otherwise only the named symbols' files
    (see ``bar_files``).

    Columns: date (datetime.date), symbol, open, high, low, close, volume.
    Every file is checked for provenance before it is read.
    """
    frames = []
    for path in bar_files(fixtures, symbols):
        read_provenance(path)
        df = pd.read_parquet(path)[FIELDS].copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        df["symbol"] = path.stem
        frames.append(df.reset_index())
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


def wide(bars_long: pd.DataFrame) -> pd.DataFrame:
    """fdq's bar frame: a Timestamp index and (symbol, field) columns."""
    df = bars_long.copy()
    df["date"] = pd.to_datetime(df["date"])
    w = df.pivot(index="date", columns="symbol", values=["open", "high", "low", "close", "volume"])
    w.columns = w.columns.swaplevel(0, 1)
    return w.sort_index(axis=1)


def load_macro(path: Path) -> pd.DataFrame:
    """The macro series fdq's engine reads for its VIX spread widening
    (``fdq.backtest.engine._vix_on`` reads the ``vix`` column), passed to fdq
    exactly as fdq's own ``load_validated_macro`` would: the whole frame, a
    DatetimeIndex, sorted.

    Refused, never degraded: a missing sidecar or a synthetic one, a frame
    with no ``vix`` column, or any NaN in ``vix``. fdq reads a NaN VIX as "no
    VIX" and silently drops the widening on that day; a missing column drops
    it on every day. Either would quietly run a cheaper friction than the one
    registered."""
    read_provenance(path)
    df = pd.read_parquet(path)
    if "vix" not in df.columns:
        raise ProvenanceError(f"{path.name}: no 'vix' column; the VIX widening cannot run")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index))
    df = df.sort_index()
    if df.empty:
        raise ProvenanceError(f"{path.name}: no rows")
    if df["vix"].isna().any():
        first = df.index[df["vix"].isna()][0]
        raise ProvenanceError(f"{path.name}: vix is unknown (NaN) on {first.date()}")
    return df


def dollar_adv(bars_long: pd.DataFrame, symbol: str, window: int = ADV_WINDOW_DAYS) -> pd.Series:
    """``symbol``'s trailing dollar ADV as it was known at each day's open:
    the mean of close x volume over the ``window`` sessions ending the day
    before, indexed by Timestamp. A trade fills at the open, before that
    day's volume exists, so the day's own session is never in its ADV.

    Nothing is dropped or filled: the first ``window`` days have no ADV and
    are NaN, and ``capacity`` refuses a traded day whose ADV is NaN -- an
    unknown ADV is unknown, not absent."""
    rows = bars_long.loc[bars_long["symbol"] == symbol]
    if rows.empty:
        raise ValueError(f"dollar_adv: no bars for {symbol}")
    idx = pd.DatetimeIndex(pd.to_datetime(rows["date"]))
    dollars = pd.Series(
        rows["close"].to_numpy(dtype=float) * rows["volume"].to_numpy(dtype=float), index=idx
    ).sort_index()
    return dollars.rolling(window).mean().shift(1)
