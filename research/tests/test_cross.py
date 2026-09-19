"""The cross-language check, by shared examples (ruling 6).

Alpha's ``test_cross.py`` invoked the OCaml binary from Python, and was
skipped whenever that binary was not built. Here neither language invokes the
other: both sides read the same ``interface/examples/expected.json``. The
OCaml test (task 5) checks each example's ``core`` verdict -- the first
failing rule, or pass. This one checks ``schema_valid``. Between the two,
every column of the oracle is checked, by the language that owns that column.
"""

from __future__ import annotations

import json

import pytest

from ohcamel_research import REPO_ROOT
from ohcamel_research.contract import check

EXAMPLES = REPO_ROOT / "interface" / "examples"
EXPECTED = json.loads((EXAMPLES / "expected.json").read_text())


def _example_names() -> list[str]:
    """Every example file on disk, minus the oracle itself."""
    return sorted(p.name for p in EXAMPLES.glob("*.json") if p.name != "expected.json")


def test_every_example_on_disk_is_named_in_expected_json():
    # Catches an example added to interface/examples/ without a matching
    # entry in expected.json's "cases" -- or the reverse, a stale entry for a
    # file that no longer exists.
    assert _example_names() == sorted(EXPECTED["cases"])


@pytest.mark.parametrize("name", _example_names())
def test_schema_valid_matches_expected_json(name: str):
    doc = json.loads((EXAMPLES / name).read_text())
    assert (check(doc) == []) == EXPECTED["cases"][name]["schema_valid"]
