import json

import pandas as pd
import pytest

from ohcamel_research import REPO_ROOT
from ohcamel_research.contract import (
    OVERLAY_SCHEMA_PATH,
    SCHEMA_PATH,
    V2_SCHEMA_PATH,
    canonical_json,
    check,
    data_hash,
    params_hash,
    schema_path_for,
)

EXAMPLES = REPO_ROOT / "interface" / "examples"
EXPECTED = json.loads((EXAMPLES / "expected.json").read_text())


@pytest.mark.parametrize("name", sorted(EXPECTED["cases"]))
def test_examples_validate_as_expected_json_says(name: str):
    doc = json.loads((EXAMPLES / name).read_text())
    problems = check(doc)
    assert (problems == []) == EXPECTED["cases"][name]["schema_valid"], problems


def test_params_hash_ignores_key_order_and_whitespace():
    assert params_hash({"fast": 50, "slow": 200}) == params_hash({"slow": 200, "fast": 50})
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    assert params_hash({"fast": 50}) != params_hash({"fast": 51})


def test_data_hash_is_order_invariant_and_content_sensitive():
    rows = [
        {
            "symbol": "SPY",
            "date": pd.Timestamp("2020-12-30").date(),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
        },
        {
            "symbol": "TLT",
            "date": pd.Timestamp("2020-12-30").date(),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
        },
        {
            "symbol": "SPY",
            "date": pd.Timestamp("2020-12-31").date(),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.6,
            "volume": 10.0,
        },
    ]
    a = pd.DataFrame(rows)
    b = pd.DataFrame(rows[::-1])
    assert data_hash(a) == data_hash(b)
    c = a.copy()
    c.loc[2, "close"] = 1.7
    assert data_hash(c) != data_hash(a)


def test_r7_sum_is_checked_here_too():
    doc = json.loads((EXAMPLES / "pass.json").read_text())
    doc["targets"] = [{"symbol": "SPY", "weight": 0.6}, {"symbol": "TLT", "weight": 0.6}]
    assert any(p.startswith("R7") for p in check(doc))


def test_the_schema_is_chosen_by_the_documents_content():
    def route(name: str):
        return schema_path_for(json.loads((EXAMPLES / name).read_text()))

    assert route("pass.json") == SCHEMA_PATH
    assert route("v2_pass.json") == V2_SCHEMA_PATH
    assert route("overlay_pass.json") == OVERLAY_SCHEMA_PATH


def test_a_v2_signal_needs_bar_and_horizon_and_an_instant():
    doc = json.loads((EXAMPLES / "v2_pass.json").read_text())
    assert check(doc) == []
    for field in ("bar", "horizon"):
        assert check({k: v for k, v in doc.items() if k != field})
    assert check({**doc, "bar": "2Min"})
    assert check({**doc, "as_of": "2026-10-13"})


def test_the_overlay_range_is_the_schemas_o4():
    doc = json.loads((EXAMPLES / "overlay_pass.json").read_text())
    assert check({**doc, "exposure_scale": 1.0}) == []
    assert check({**doc, "exposure_scale": 0.0}) == []
    assert check({**doc, "exposure_scale": 1.2})
    assert check({**doc, "exposure_scale": -0.1})
    assert check({**doc, "sequence": 0})


def test_pending_rows_state_what_the_core_must_say_once_it_learns_them():
    pending = {n: c for n, c in EXPECTED["cases"].items() if c["core"] == "pending-task-22"}
    assert {n: c["verdict"] for n, c in pending.items()} == {
        "v2_pass.json": "ACCEPT",
        "v2_r9_uncorroborated.json": "REJECT R9",
        "v2_r4_stale.json": "REJECT R4",
        "overlay_pass.json": "ACCEPT",
        "overlay_o4_range.json": "REJECT O4",
    }
    assert EXPECTED["clock"]["now"] == "2026-10-13T15:31:00Z"
    assert EXPECTED["clock"]["last_tick"] == {"SPY": "2026-10-13T15:30:58Z"}
