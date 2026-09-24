import json

import pandas as pd
import pytest

from ohcamel_research import REPO_ROOT
from ohcamel_research.contract import canonical_json, check, data_hash, params_hash

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
