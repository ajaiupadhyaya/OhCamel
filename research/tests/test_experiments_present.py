"""The pre-registrations of the autonomous-paper-trading plan exist in the form
the charter needs before anything trades or any battery runs: each directory
holds a ``preregistration.md`` whose last section is the owner's ``## Approval``,
and INC-01's roster -- the one list every producer reads -- adds up to the
book's ``incubation_cap`` and names exactly the strategies the example book
registers, with the same fractions and symbols."""

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


def test_roster_fractions_sum_to_the_incubation_cap() -> None:
    roster = yaml.safe_load(ROSTER.read_text())
    fractions = [s["capital_fraction"] for s in roster["strategies"]]
    assert all(f > 0 for f in fractions)
    assert abs(sum(fractions) - roster["incubation_cap"]) < 1e-9


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


def test_roster_agrees_with_the_example_book() -> None:
    roster = yaml.safe_load(ROSTER.read_text())
    book = _book_strategies()
    assert len(book) == 23
    ours = {s["slug"]: s for s in roster["strategies"]}
    assert set(ours) == set(book)
    for slug, (fraction, symbols) in book.items():
        assert ours[slug]["capital_fraction"] == pytest.approx(fraction, abs=1e-12), slug
        if "symbols" in ours[slug]:
            assert ours[slug]["symbols"] == symbols, slug
