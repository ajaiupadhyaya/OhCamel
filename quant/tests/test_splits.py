"""An unadjusted split is repaired on the way out, and a raw bar still fails integrity."""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from ohcamel_quant.products.data_integrity import check_bars
from ohcamel_quant.warehouse import readers


def _bars(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "adj_close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def mtum() -> pd.DataFrame:
    return _bars([
        ("2026-11-04", 272.86, 272.86, 272.86, 272.86, 272.86, 1_000_000),
        ("2026-11-05", 66.76, 68.0, 62.0, 62.60, 62.60, 8_000_000),
    ])


def crash() -> pd.DataFrame:
    return _bars([
        ("2026-11-04", 100.0, 101.0, 99.0, 100.0, 100.0, 1_000_000),
        ("2026-11-05", 98.0, 98.0, 60.0, 60.0, 60.0, 2_000_000),
    ])


def adjusted() -> pd.DataFrame:
    return _bars([
        ("2026-11-04", 68.215, 68.215, 68.215, 68.215, 68.215, 4_000_000),
        ("2026-11-05", 66.76, 68.0, 62.0, 62.60, 62.60, 8_000_000),
    ])


def test_mtum_shape_is_a_four_for_one_and_the_prior_close_is_divided():
    from ohcamel_quant.data.splits import present

    raw = mtum()
    out, notes = present(raw)
    assert notes == ["split-adjusted 4:1 before 2026-11-05 (vendor adj_close did not absorb it)"]
    assert out.iloc[0]["close"] == pytest.approx(272.86 / 4)
    assert out.iloc[0]["volume"] == pytest.approx(1_000_000 * 4)
    assert out.iloc[1]["close"] == pytest.approx(62.60)
    assert out.iloc[1]["volume"] == pytest.approx(8_000_000)
    ret = out.iloc[1]["adj_close"] / out.iloc[0]["adj_close"] - 1
    assert ret == pytest.approx(62.60 / (272.86 / 4) - 1)
    assert abs(ret) < 0.20
    assert raw.iloc[0]["close"] == pytest.approx(272.86)


def test_a_second_pass_does_not_divide_again():
    from ohcamel_quant.data.splits import present

    once, _ = present(mtum())
    twice, notes = present(once)
    assert notes == []
    pd.testing.assert_frame_equal(twice, once)


def test_a_crash_with_no_split_ratio_is_unchanged():
    from ohcamel_quant.data.splits import present

    raw = crash()
    out, notes = present(raw)
    assert notes == []
    assert out.iloc[1]["close"] == 60
    pd.testing.assert_frame_equal(out, raw)


def test_an_adjusted_history_is_unchanged():
    from ohcamel_quant.data.splits import present

    raw = adjusted()
    out, notes = present(raw)
    assert notes == []
    pd.testing.assert_frame_equal(out, raw)


def test_the_raw_mtum_shape_is_still_a_split_suspect():
    findings = check_bars(mtum())
    assert any(f["check"] == "split_suspect" for f in findings)
    assert "present" not in inspect.getsource(readers.ohlcv)
    assert "present" not in inspect.getsource(check_bars)


# ---------------------------------------------------------------- one read path (Task 3.1)
# A CONSTRUCTED test fixture, not market data: the fixture warehouse (the committed real ETF bars) with an
# MTUM-shaped unadjusted 4:1 split put into SPY -- every bar before EX multiplied by 4 (prices) and divided by 4
# (volume), the vendor's bytes as they would be had it not adjusted the split. 4 is a power of two, so the repair
# (divide by 4) restores the committed fixture bit for bit. SPY 2026-02-27 close 685.99, 2026-03-02 open 678.70:
# overnight ratio 4 x 1.0107 = 4.043, within 3% of 4; close-to-close 686.38 / (4 x 685.99) - 1 = -75%.
SPLIT_TICKER, SPLIT_EX, SPLIT_RATIO = "SPY", "2026-03-02", 4.0
SPLIT_NOTE = "SPY: split-adjusted 4:1 before 2026-03-02 (vendor adj_close did not absorb it)"


def unadjusted_split(path, ticker: str = SPLIT_TICKER, ex: str = SPLIT_EX, ratio: float = SPLIT_RATIO):
    """The fixture warehouse at ``path`` with an unadjusted ``ratio``:1 split put into ``ticker`` before ``ex``."""
    from ohcamel_quant.warehouse.db import open_rw
    from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

    build_fixture_warehouse(path)
    with open_rw(path) as con:
        con.execute("UPDATE bars_daily SET open = open * ?, high = high * ?, low = low * ?, close = close * ?, "
                    "adj_close = adj_close * ?, volume = volume / ? WHERE ticker = ? AND date < CAST(? AS DATE)",
                    [ratio] * 6 + [ticker, ex])
    return path


@pytest.fixture(scope="module")
def split_wh(tmp_path_factory):
    return unadjusted_split(tmp_path_factory.mktemp("split") / "wh.duckdb")


@pytest.fixture(scope="module")
def clean_wh(tmp_path_factory):
    from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

    return build_fixture_warehouse(tmp_path_factory.mktemp("clean") / "wh.duckdb")


def _ro(path):
    from ohcamel_quant.warehouse.db import open_ro

    return open_ro(path)


def test_bars_panel_has_no_return_beyond_30pct_on_the_ex_date_and_one_note(split_wh, clean_wh):
    with _ro(split_wh) as con:
        px, notes = readers.bars_panel(con, ["SPY", "QQQ"], None, None)
        raw = readers.ohlcv(con, "SPY").data["adj_close"]
    assert notes == [SPLIT_NOTE]
    assert list(px.columns) == ["SPY", "QQQ"] and px.index.name == "date"
    ret = px.pct_change()
    assert abs(ret.loc[SPLIT_EX, "SPY"]) < 0.30
    assert (ret.abs().max() < 0.30).all()
    assert abs(raw.pct_change().loc[SPLIT_EX]) > 0.30            # the vendor's bytes are left as they were
    with _ro(clean_wh) as con:
        clean, none = readers.bars_panel(con, ["SPY", "QQQ"], None, None)
    assert none == []
    pd.testing.assert_frame_equal(px, clean, check_exact=True)   # x * 4 / 4 == x: the committed bars, bit for bit


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "adj_close", "volume"])
def test_every_field_is_served_on_the_repaired_basis(split_wh, clean_wh, field):
    with _ro(split_wh) as con:
        got, notes = readers.bars_panel(con, ["SPY"], None, None, field=field)
    with _ro(clean_wh) as con:
        want, _ = readers.bars_panel(con, ["SPY"], None, None, field=field)
    assert notes == [SPLIT_NOTE]
    pd.testing.assert_frame_equal(got, want, check_exact=True)


def test_close_and_volume_move_opposite_ways_so_dollar_volume_is_unchanged(split_wh):
    with _ro(split_wh) as con:
        close, _ = readers.bars_panel(con, ["SPY"], None, None, field="close")
        volume, _ = readers.bars_panel(con, ["SPY"], None, None, field="volume")
        raw = readers.ohlcv(con, "SPY").data
    before = close.index < pd.Timestamp(SPLIT_EX)
    assert (close.loc[before, "SPY"] == raw.loc[before, "close"] / 4).all()
    assert (volume.loc[before, "SPY"] == raw.loc[before, "volume"] * 4).all()
    assert (close.loc[~before, "SPY"] == raw.loc[~before, "close"]).all()       # the ex-date onward is untouched
    pd.testing.assert_series_equal(close["SPY"] * volume["SPY"], raw["close"] * raw["volume"],
                                   check_names=False, check_freq=False)
    with _ro(split_wh) as con, pytest.raises(ValueError, match="field"):
        readers.bars_panel(con, ["SPY"], None, None, field="vwap")


def test_the_repair_is_deterministic_and_sees_nothing_after_end(split_wh):
    with _ro(split_wh) as con:
        a, na = readers.bars_panel(con, ["SPY", "QQQ"], None, None)
        b, nb = readers.bars_panel(con, ["SPY", "QQQ"], None, None)
        early, n_early = readers.bars_panel(con, ["SPY"], None, pd.Timestamp("2026-02-27").date())
        late, n_late = readers.bars_panel(con, ["SPY"], pd.Timestamp(SPLIT_EX).date(), None)
        raw = readers.ohlcv(con, "SPY", end=pd.Timestamp("2026-02-27").date()).data
    assert na == nb and pd.util.hash_pandas_object(a).equals(pd.util.hash_pandas_object(b))
    # As of 2026-02-27 the split has not happened: nothing is divided and nothing is noted.
    assert n_early == [] and (early["SPY"] == raw["adj_close"]).all()
    # From the ex-date on there is no previous close inside the window: nothing to repair.
    assert n_late == [] and late.index[0] == pd.Timestamp(SPLIT_EX)


def test_a_ticker_absent_from_the_warehouse_has_no_column(split_wh):
    with _ro(split_wh) as con:
        px, _ = readers.bars_panel(con, ["spy", "NOPE"], None, None)
    assert list(px.columns) == ["SPY"]


def test_integrity_still_reports_split_suspect_on_the_same_warehouse(split_wh, tmp_path):
    import duckdb

    from ohcamel_quant.config import Settings
    from ohcamel_quant.data.market import MarketData
    from ohcamel_quant.jobs.context import JobContext
    from ohcamel_quant.products import data_integrity

    ctx = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                     _market=MarketData(Settings(offline=True, data_dir=tmp_path)),
                     _warehouse=duckdb.connect(str(split_wh), read_only=True))
    try:
        spec = data_integrity.run({"tickers": ["SPY"]}, ctx)
    finally:
        ctx.close()
    f = spec.tables["findings"]
    hit = f[(f["source"] == "warehouse") & (f["check"] == "split_suspect")]
    assert list(zip(hit["ticker"], hit["date"], strict=True)) == [("SPY", SPLIT_EX)]
