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
