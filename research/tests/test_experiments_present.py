"""The pre-registrations of the autonomous-paper-trading plan exist in the form
the charter needs before anything trades or any battery runs: each directory
holds a ``preregistration.md`` whose last section is the owner's ``## Approval``.

INC-01 closed at 2026-11-05, the day its drawdown cap was changed after it
tripped (completion spec, ruling R1); its roster stays as it traded. INC-02 is
its successor, drafted by the agent for the owner (ruling R4) and approved by
no one yet: its roster -- the one list every producer reads -- keeps its equal
fractions within its own ``incubation_cap`` and agrees with the example book:
the same strategies, fractions and symbols, the same ``incubation_cap`` and
``vol_target``."""

from __future__ import annotations

import re

import pytest
import yaml

from ohcamel_research import REPO_ROOT

EXPERIMENTS = REPO_ROOT / "research" / "experiments"
INC01 = EXPERIMENTS / "INC-01"
INC02 = EXPERIMENTS / "INC-02"
ROSTER = INC02 / "roster.yaml"
BOOK = REPO_ROOT / "book.example.sexp"
CHARTER = REPO_ROOT / "docs" / "CHARTER.md"

#: The two rows INC-02 removes (completion spec, Research, *Roster*).
REMOVED = {"farm_pairs_coint_sectors", "q01_ls_quintile"}


@pytest.mark.parametrize("name", ["INC-01", "INC-02", "EXP-I01", "EXP-I02", "EXP-I03"])
def test_preregistration_ends_with_the_owners_approval(name: str) -> None:
    doc = EXPERIMENTS / name / "preregistration.md"
    assert doc.is_file(), f"{doc} is missing"
    headings = [ln for ln in doc.read_text().splitlines() if ln.startswith("#")]
    assert headings, f"{doc} has no headings"
    assert headings[-1] == "## Approval", f"{doc}'s last heading is {headings[-1]!r}"


# ------------------------------------------------------------------ INC-01 closes
def test_inc01_closes_at_the_day_its_limit_was_changed() -> None:
    text = (INC01 / "preregistration.md").read_text()
    assert "## Amendment A1, 2026-11-15" in text
    assert "## Closed" in text
    assert "Closed: 2026-11-05" in text
    a1 = text[text.index("## Amendment A1, 2026-11-15") : text.index("## Closed")]
    for fact in ("0.02", "0.10", "2026-11-05", "agent-drafted", "2026-11-15", "did not know"):
        assert fact in a1, f"A1 does not say {fact!r}"


def test_inc01_roster_stays_as_it_traded() -> None:
    """The closed record keeps its 23 rows; nothing reads it as a live roster."""
    roster = yaml.safe_load((INC01 / "roster.yaml").read_text())
    assert [s["capital_fraction"] for s in roster["strategies"]] == [0.025] * 23
    assert roster["incubation_cap"] == 0.60


# ------------------------------------------------------------------ INC-02 is approved
def test_inc02_is_approved_by_the_owner_and_waits_for_the_flatten() -> None:
    """2026-11-27: the owner approved INC-02 as drafted, in chat. The ``Approved-by:`` line names the owner, the
    date and their words; the status line says APPROVED with the same date; and the Roster-6(a) flatten line is
    there for the date the pooled test starts after (empty until the flatten is done)."""
    text = (INC02 / "preregistration.md").read_text()
    approval = text[text.rindex("## Approval") :]
    lines = [ln.strip() for ln in approval.splitlines() if ln.strip().startswith("Approved-by:")]
    assert len(lines) == 1, "INC-02's Approval section has not exactly one Approved-by: line"
    assert lines[0].startswith("Approved-by: the owner, 2026-11-27, in chat:") and "approve as drafted" in lines[0]
    status = [ln for ln in text.splitlines() if ln.startswith("**Status:")]
    assert status and "APPROVED by the owner on 2026-11-27" in status[0] and "DRAFT" not in status[0]
    assert any(ln.strip().startswith("Roster-6(a) flatten completed:") for ln in approval.splitlines())


def test_inc02_first_paragraph_states_the_test_and_its_power() -> None:
    """The spec: the power is printed, and the first paragraph says a non-rejection is expected."""
    text = (INC02 / "preregistration.md").read_text()
    body = text.split("\n\n")
    first = " ".join(next(p for p in body if p and not p.startswith(("#", "**Status:"))).split())
    for fact in ("500 sessions", "PSR", "one-sided 5%", "41%", "Sharpe of 1.0", "17%", "0.5",
                 "non-rejection is the expected outcome", "not evidence of anything"):
        assert fact in first, f"INC-02's first paragraph does not say {fact!r}"


def test_inc02_states_the_vol_normalised_stop() -> None:
    text = " ".join((INC02 / "preregistration.md").read_text().split())
    facts = ("2.0 ×", "floored at 0.05", "capped at 0.25", "500 sessions", "reduce_through_trip")
    for fact in facts:
        assert fact in text, f"INC-02 does not state {fact!r}"


def test_every_inc01_clause_awaiting_confirmation_is_an_inc02_line() -> None:
    """INC-01 marks 17 clauses "awaiting the owner's confirmation" and three more "not fixed by
    §5"; INC-02 restates each as its own numbered line, every one awaiting approval."""
    inc01 = (INC01 / "preregistration.md").read_text()
    assert inc01.count("awaiting the owner's confirmation") == 17
    inc02 = (INC02 / "preregistration.md").read_text()
    carried = re.findall(r"^C(\d+)\. .*?\*\(awaiting approval\)\*", inc02, re.M | re.S)
    assert [int(n) for n in carried] == list(range(1, 21))


def test_the_charter_names_inc02s_stop_rule_without_calling_it_approved() -> None:
    text = " ".join(CHARTER.read_text().split())
    assert "INC-02" in text
    i = text.index("INC-02")
    end = text.index(".", text.index("500 sessions", i)) + 1
    sentence = text[max(0, text.rfind(".", 0, i) + 1) : end]
    for fact in ("2.0 ×", "0.05", "0.25", "500 sessions", "not yet approved"):
        assert fact in sentence, f"the charter's INC-02 passage does not say {fact!r}: {sentence}"


# ------------------------------------------------------------------ INC-02's roster
def test_roster_fractions_are_equal_and_within_the_incubation_cap() -> None:
    """22 x 0.025 = 0.55 = incubation_cap: equal fractions, nothing held back."""
    roster = yaml.safe_load(ROSTER.read_text())
    fractions = [s["capital_fraction"] for s in roster["strategies"]]
    assert fractions == [0.025] * 22
    assert sum(fractions) == pytest.approx(0.55, abs=1e-9)
    assert roster["incubation_cap"] == 0.55
    assert sum(fractions) <= roster["incubation_cap"] + 1e-9
    assert not REMOVED & {s["slug"] for s in roster["strategies"]}


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
    assert len(book) == 22
    assert not REMOVED & set(book)
    slugs = [s["slug"] for s in roster["strategies"]]
    assert slugs == list(book), "the roster and the book list the same strategies in the same order"
    ours = {s["slug"]: s for s in roster["strategies"]}
    for slug, (fraction, symbols) in book.items():
        assert ours[slug]["capital_fraction"] == pytest.approx(fraction, abs=1e-12), slug
        if "symbols" in ours[slug]:
            assert ours[slug]["symbols"] == symbols, slug
