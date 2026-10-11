"""The pre-registrations of the autonomous-paper-trading plan exist in the form
the charter needs before anything trades or any battery runs: each directory
holds a ``preregistration.md`` whose last section is the owner's ``## Approval``,
and INC-01's roster -- the one list every producer reads -- keeps its equal
fractions within its own ``incubation_cap``, and agrees with the example book:
the same strategies, fractions and symbols, the same ``incubation_cap`` and
``vol_target``."""

from __future__ import annotations

import re

import pytest
import yaml

from ohcamel_research import REPO_ROOT

EXPERIMENTS = REPO_ROOT / "research" / "experiments"
ROSTER = EXPERIMENTS / "INC-01" / "roster.yaml"
BOOK = REPO_ROOT / "book.example.sexp"


@pytest.mark.parametrize("name", ["INC-01", "EXP-I01", "EXP-I02", "EXP-I03"])
def test_preregistration_ends_with_the_owners_approval(name: str) -> None:
    doc = EXPERIMENTS / name / "preregistration.md"
    assert doc.is_file(), f"{doc} is missing"
    headings = [ln for ln in doc.read_text().splitlines() if ln.startswith("#")]
    assert headings, f"{doc} has no headings"
    assert headings[-1] == "## Approval", f"{doc}'s last heading is {headings[-1]!r}"


def test_roster_fractions_are_equal_and_within_the_incubation_cap() -> None:
    """23 x 0.025 = 0.575 <= 0.60: equal fractions, 0.025 held for EXP-Q01."""
    roster = yaml.safe_load(ROSTER.read_text())
    fractions = [s["capital_fraction"] for s in roster["strategies"]]
    assert fractions == [0.025] * 23
    assert sum(fractions) == pytest.approx(0.575, abs=1e-9)
    assert sum(fractions) <= roster["incubation_cap"] + 1e-9


def _book_strategies() -> dict[str, tuple[float, list[str]]]:
    """``(name …)`` → (capital_fraction, symbols) for every strategy in the
    example book's signals block, comments stripped. Only the signals block:
    the limits block also has ``(name …)`` entries."""
    text = "\n".join(ln.split(";", 1)[0] for ln in BOOK.read_text().splitlines())
    signals = text[text.index("(signals") :]
    out: dict[str, tuple[float, list[str]]] = {}
    pattern = re.compile(
        r"\(\(name ([a-z][a-z0-9_]+)\)\s+\(symbols \(([A-Z ]+)\)\).*?"
        r"\(capital_fraction ([0-9.]+)\)",
        re.S,
    )
    for m in pattern.finditer(signals):
        out[m.group(1)] = (float(m.group(3)), m.group(2).split())
    return out


def _book_desk_value(field: str) -> float:
    text = "\n".join(ln.split(";", 1)[0] for ln in BOOK.read_text().splitlines())
    m = re.search(rf"\({field} ([0-9.]+)\)", text)
    assert m, f"book.example.sexp has no ({field} ...)"
    return float(m.group(1))


def test_roster_agrees_with_the_example_book() -> None:
    roster = yaml.safe_load(ROSTER.read_text())
    assert roster["incubation_cap"] == _book_desk_value("incubation_cap")
    assert roster["vol_target"] == _book_desk_value("vol_target")
    book = _book_strategies()
    assert len(book) == 23
    ours = {s["slug"]: s for s in roster["strategies"]}
    assert set(ours) == set(book)
    for slug, (fraction, symbols) in book.items():
        assert ours[slug]["capital_fraction"] == pytest.approx(fraction, abs=1e-12), slug
        if "symbols" in ours[slug]:
            assert ours[slug]["symbols"] == symbols, slug
