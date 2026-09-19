import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from ohcamel_research.replay import (
    DEFAULT_FIXTURES,
    ProvenanceError,
    iter_bars,
    load_bars,
    slice_bars,
)


def test_fixture_is_the_nine_etf_universe_2018_2020():
    bars = load_bars()
    assert len(bars) == 9 * 756 == 6804
    assert sorted(bars["symbol"].unique()) == [
        "GLD",
        "IEF",
        "IWM",
        "QQQ",
        "SPY",
        "TLT",
        "XLE",
        "XLF",
        "XLK",
    ]
    assert bars["date"].min() == date(2018, 1, 2)
    assert bars["date"].max() == date(2020, 12, 31)


def test_replay_order_is_date_then_symbol_and_deterministic():
    a = list(iter_bars(load_bars()))
    b = list(iter_bars(load_bars()))
    assert a == b
    assert (a[0]["date"], a[0]["symbol"]) == ("2018-01-02", "GLD")
    keys = [(x["date"], x["symbol"]) for x in a]
    assert keys == sorted(keys)


def test_slice_is_inclusive_on_both_ends():
    bars = load_bars()
    s = slice_bars(bars, date(2020, 12, 28), date(2020, 12, 31))
    assert sorted(s["date"].unique()) == [
        date(2020, 12, 28),
        date(2020, 12, 29),
        date(2020, 12, 30),
        date(2020, 12, 31),
    ]
    assert len(s) == 4 * 9


def test_a_file_without_a_sidecar_is_refused(tmp_path: Path):
    shutil.copy(DEFAULT_FIXTURES / "SPY.parquet", tmp_path / "SPY.parquet")
    with pytest.raises(ProvenanceError, match="no provenance sidecar"):
        load_bars(tmp_path)


def test_a_file_marked_synthetic_is_refused(tmp_path: Path):
    shutil.copy(DEFAULT_FIXTURES / "SPY.parquet", tmp_path / "SPY.parquet")
    meta = json.loads((DEFAULT_FIXTURES / "SPY.parquet.meta.json").read_text())
    meta["synthetic"] = True
    (tmp_path / "SPY.parquet.meta.json").write_text(json.dumps(meta))
    with pytest.raises(ProvenanceError, match="synthetic"):
        load_bars(tmp_path)


def test_a_sidecar_for_another_symbol_is_refused(tmp_path: Path):
    shutil.copy(DEFAULT_FIXTURES / "SPY.parquet", tmp_path / "SPY.parquet")
    shutil.copy(DEFAULT_FIXTURES / "TLT.parquet.meta.json", tmp_path / "SPY.parquet.meta.json")
    with pytest.raises(ProvenanceError, match="is for 'TLT'"):
        load_bars(tmp_path)
