"""The Quant package's signal and overlay writer: hashed byte-for-byte as the
research package hashes (the two vectors below are the research package's own
output, recomputed by hand with hashlib), valid against interface/'s schemas,
written atomically."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

import jsonschema
import pandas as pd
import pytest

from ohcamel_quant.signals.contract import (
    OVERLAY_SCHEMA_PATH,
    V1_SCHEMA_PATH,
    V2_SCHEMA_PATH,
    data_hash,
    overlay_doc,
    params_hash,
    signal_v1,
    signal_v2,
    validate,
    write_atomic,
)
from ohcamel_quant.signals.roster import targets_gross_scaled

# research.contract.params_hash({"fast": 1, "slow": 150}); equally
# sha256(b'{"fast":1,"slow":150}').
PARAMS_VECTOR = "sha256:b0c28dc35eeaa8593259274059a19c9428f1c509ee4555decf4631af2e0f8965"
# research.contract.data_hash over research/tests/test_contract.py's three-row
# frame; equally sha256 of the three canonical lines, each newline-terminated.
DATA_VECTOR = "sha256:999ed054784e79c101c5520da51d74086b507b02f9936fe5124d120eac3cffbb"


def _bars() -> pd.DataFrame:
    def row(symbol: str, day: str, close: float) -> dict:
        return {
            "symbol": symbol,
            "date": pd.Timestamp(day).date(),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": close,
            "volume": 10.0,
        }

    return pd.DataFrame(
        [
            row("SPY", "2020-12-30", 1.5),
            row("TLT", "2020-12-30", 1.5),
            row("SPY", "2020-12-31", 1.6),
        ]
    )


def test_params_hash_matches_the_research_packages_vector():
    assert params_hash({"fast": 1, "slow": 150}) == PARAMS_VECTOR
    assert params_hash({"slow": 150, "fast": 1}) == PARAMS_VECTOR


def test_data_hash_matches_the_research_packages_vector_in_any_row_order():
    bars = _bars()
    assert data_hash(bars) == DATA_VECTOR
    assert data_hash(bars.iloc[::-1].reset_index(drop=True)) == DATA_VECTOR


def test_signal_v1_validates_against_the_v1_schema():
    doc = signal_v1(
        strategy="farm_tsmom_us_equity_indices",
        params={"fast": 1, "slow": 150},
        as_of=date(2020, 12, 31),
        sequence=3,
        validation=None,
        targets={"TLT": -0.25, "SPY": 0.5, "QQQ": 0.0},
        bars=_bars(),
    )
    validate(doc, V1_SCHEMA_PATH)
    assert doc["params_hash"] == PARAMS_VECTOR
    assert doc["data_hash"] == DATA_VECTOR
    assert doc["as_of"] == "2020-12-31"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", doc["computed_at"])
    # zero weights dropped, sorted by symbol
    assert doc["targets"] == [{"symbol": "SPY", "weight": 0.5}, {"symbol": "TLT", "weight": -0.25}]
    # no validation given: unvalidated, never invented
    assert doc["validation"]["status"] == "unvalidated"
    assert doc["validation"]["gates_version"] == "2026-09-02"


def test_signal_v1_passes_validation_through_unchanged():
    block = {
        "status": "pass",
        "gates_version": "2026-09-02",
        "dsr": 0.3,
        "psr": 0.7,
        "pbo": 0.2,
        "manifest": None,
    }
    doc = signal_v1(
        strategy="exp_a01_spy",
        params={},
        as_of=date(2020, 12, 31),
        sequence=1,
        validation=block,
        targets={"SPY": 1.0},
        bars=_bars(),
    )
    assert doc["validation"] == block


def test_signal_v2_validates_against_the_v2_schema():
    doc = signal_v2(
        strategy="exp_i01_spy",
        params={"lookback": 12},
        as_of=datetime(2026, 10, 13, 15, 30, tzinfo=UTC),
        bar="30Min",
        horizon="close",
        sequence=41,
        validation=None,
        targets={"SPY": 0.9},
        bars=_bars(),
    )
    validate(doc, V2_SCHEMA_PATH)
    assert doc["schema_version"] == 2
    assert doc["as_of"] == "2026-10-13T15:30:00Z"


def test_overlay_doc_validates_against_the_overlay_schema():
    doc = overlay_doc(
        as_of=datetime(2026, 10, 13, 15, 30, tzinfo=UTC),
        sequence=7,
        exposure_scale=0.7071,
        inputs={
            "vol_target": 0.10,
            "vol_forecast": 0.1414,
            "vol_model": "har",
            "cov_estimator": "lw",
            "vol_artifact": "01M" + "0" * 23,
            "cov_artifact": "01M" + "1" * 23,
        },
        reported={"regime_p_high": 0.12},
    )
    validate(doc, OVERLAY_SCHEMA_PATH)
    assert doc["kind"] == "overlay"
    with pytest.raises(jsonschema.ValidationError):
        validate({**doc, "exposure_scale": 1.5}, OVERLAY_SCHEMA_PATH)


def test_validate_refuses_a_sum_over_one():
    doc = signal_v1(
        strategy="exp_a01_spy",
        params={},
        as_of=date(2020, 12, 31),
        sequence=1,
        validation=None,
        targets={"SPY": 0.6, "TLT": 0.6},
        bars=_bars(),
    )
    with pytest.raises(ValueError, match="R7"):
        validate(doc, V1_SCHEMA_PATH)


def test_write_atomic_leaves_no_tmp_on_success(tmp_path):
    doc = signal_v1(
        strategy="exp_a01_spy",
        params={},
        as_of=date(2020, 12, 31),
        sequence=1,
        validation=None,
        targets={"SPY": 1.0},
        bars=_bars(),
    )
    out = write_atomic(doc, tmp_path / "signals" / "exp_a01_spy-2020-12-31.json")
    assert json.loads(out.read_text()) == doc
    assert [p.name for p in out.parent.iterdir()] == [out.name]


def test_write_atomic_leaves_nothing_when_the_document_is_invalid(tmp_path):
    doc = overlay_doc(
        as_of=datetime(2026, 10, 13, 15, 30, tzinfo=UTC),
        sequence=7,
        exposure_scale=0.5,
        inputs={},  # missing every required input
        reported={},
    )
    with pytest.raises(jsonschema.ValidationError):
        write_atomic(doc, tmp_path / "overlay.json")
    assert list(tmp_path.iterdir()) == []


def test_write_atomic_leaves_no_tmp_when_serialisation_fails(tmp_path):
    doc = overlay_doc(
        as_of=datetime(2026, 10, 13, 15, 30, tzinfo=UTC),
        sequence=7,
        exposure_scale=0.5,
        inputs={
            "vol_target": 0.10,
            "vol_forecast": 0.1,
            "vol_model": "har",
            "cov_estimator": "lw",
            "vol_artifact": "a",
            "cov_artifact": "b",
        },
        reported={"x": float("nan")},  # schema leaves reported open; JSON refuses NaN
    )
    with pytest.raises(ValueError):
        write_atomic(doc, tmp_path / "overlay.json")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("weights", "expected", "scaled"),
    [
        ({"SPY": 0.8, "TLT": 0.8}, {"SPY": 0.5, "TLT": 0.5}, True),
        ({"SPY": 0.3, "TLT": 0.3}, {"SPY": 0.3, "TLT": 0.3}, False),
    ],
)
def test_targets_gross_scaled(weights, expected, scaled):
    out, flag = targets_gross_scaled(weights)
    assert flag is scaled
    assert out == pytest.approx(expected)
