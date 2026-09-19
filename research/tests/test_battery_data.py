"""The battery's loaders (``battery/data.py``): only what was named is read,
the macro series is refused rather than degraded, and the dollar ADV is what
was known at each day's open."""

from __future__ import annotations

import json
import shutil
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ohcamel_research import REPO_ROOT, replay
from ohcamel_research.battery import data
from ohcamel_research.battery.data import (
    ProvenanceError,
    bar_files,
    dollar_adv,
    load_bars,
    load_macro,
)

BARS = REPO_ROOT / "fixtures" / "bars"
MACRO = REPO_ROOT / "fixtures" / "macro" / "macro.parquet"


def test_replay_reads_through_the_battery_loader():
    # One loader, not two copies: replay's names are the battery's.
    assert replay.ProvenanceError is data.ProvenanceError
    assert replay.read_provenance is data.read_provenance
    assert replay.slice_bars is data.slice_bars
    assert replay.load_bars().equals(load_bars(BARS))


def test_naming_symbols_reads_only_their_files():
    assert bar_files(BARS, ["TLT", "SPY", "SPY"]) == [BARS / "SPY.parquet", BARS / "TLT.parquet"]
    bars = load_bars(BARS, ["TLT", "SPY"])
    assert sorted(bars["symbol"].unique()) == ["SPY", "TLT"]
    assert len(bars) == 2 * 756


def test_a_named_symbol_with_no_file_is_refused():
    with pytest.raises(ProvenanceError, match="ZZZ: no bar file"):
        load_bars(BARS, ["SPY", "ZZZ"])


def test_the_macro_series_loads_whole_and_sorted():
    macro = load_macro(MACRO)
    assert "vix" in macro.columns
    assert isinstance(macro.index, pd.DatetimeIndex)
    assert macro.index.is_monotonic_increasing
    assert macro.index[0] == pd.Timestamp("2016-06-01")


def _macro_copy(tmp_path, frame=None, meta_update=None):
    path = tmp_path / "macro.parquet"
    if frame is None:
        shutil.copy(MACRO, path)
    else:
        frame.to_parquet(path)
    meta = json.loads((MACRO.parent / "macro.parquet.meta.json").read_text())
    meta.update(meta_update or {})
    (tmp_path / "macro.parquet.meta.json").write_text(json.dumps(meta))
    return path


def test_a_synthetic_macro_series_is_refused(tmp_path):
    with pytest.raises(ProvenanceError, match="synthetic"):
        load_macro(_macro_copy(tmp_path, meta_update={"synthetic": True}))


def test_a_macro_series_without_vix_is_refused(tmp_path):
    frame = pd.read_parquet(MACRO).drop(columns=["vix"])
    with pytest.raises(ProvenanceError, match="no 'vix' column"):
        load_macro(_macro_copy(tmp_path, frame))


def test_a_macro_series_with_an_unknown_vix_is_refused(tmp_path):
    frame = pd.read_parquet(MACRO)
    frame.iloc[10, frame.columns.get_loc("vix")] = np.nan
    with pytest.raises(ProvenanceError, match="vix is unknown"):
        load_macro(_macro_copy(tmp_path, frame))


def test_dollar_adv_is_the_trailing_twenty_sessions_known_at_the_open():
    days = [d.date() for d in pd.bdate_range("2019-01-01", periods=22)]
    long = pd.DataFrame(
        {
            "date": days,
            "symbol": "SPY",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": [float(i + 1) for i in range(22)],  # dollars: 10, 20, ..., 220
        }
    )
    adv = dollar_adv(long, "SPY")
    assert adv.iloc[:20].isna().all()  # no twenty sessions before, no ADV: unknown, not absent
    # Day 20's ADV is sessions 0..19 (10 x mean(1..20) = 105), not its own.
    assert adv.iloc[20] == pytest.approx(105.0)
    assert adv.iloc[21] == pytest.approx(115.0)
    assert adv.index[20] == pd.Timestamp(date(2019, 1, 29))
