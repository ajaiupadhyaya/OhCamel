"""A ruling of the completion spec is the owner's only once the owner has given it.

The completion spec (``docs/superpowers/specs/2026-11-15-the-completion.md``, *Rulings the owner
must give*) lists R1-R6, each with a default the plan proceeds on. A row whose answer reads
``**Answered ...`` is one the owner gave in chat; every other row is the spec's default and
awaits the owner. Task 2.5 shipped prose calling R3 "the owner's ruling" while R3 was the
spec's default (completion task 2.1, review C1). This test fails when any file outside the
plans and the agent's ledgers calls a ruling the owner's that the spec does not record as
answered, when the charter's gates paragraph stops saying R3 awaits the owner while it does,
and when the research battery's judged-by note, which every manifest carries, names the owner.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ohcamel_research import REPO_ROOT
from ohcamel_research.battery.run import judged_by_note

SPEC = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-11-15-the-completion.md"
CHARTER = REPO_ROOT / "docs" / "CHARTER.md"

#: Where prose and code live; the plans, specs and the agent's ledgers quote rulings by design.
ROOTS = ("README.md", "book.example.sexp", "docs", "research", "quant/src", "quant/tests",
         "quant/web/src", "lib", "desk", "bin", "test", "tools", "deploy", "interface")
SKIP_DIRS = {"superpowers", ".superpowers", "node_modules", ".venv", "_build", "__pycache__",
             "dist", ".data", "results"}
TEXT = {".md", ".py", ".ml", ".mli", ".ts", ".tsx", ".yaml", ".yml", ".sexp", ".sh", ".json",
        ".txt"}
#: The owner's (or owner) + "ruling" + R and a digit, wrapped across a comment's lines or not.
OWNERS = re.compile(r"owner(?:'s)?(?:\s|#|;|//|\*|>)+ruling(?:\s|#|;|//|\*)+R([1-6])\b")


def answered() -> set[str]:
    """The rulings the spec records the owner as having answered."""
    rows = re.findall(r"^\| (R[1-6]) \|[^|]*\| (.*?) \|$", SPEC.read_text(), re.M)
    assert len(rows) == 6, "the spec's rulings table has moved"
    return {r for r, answer in rows if answer.startswith("**Answered")}


def _files():
    for root in ROOTS:
        base = REPO_ROOT / root
        if base.is_file():
            yield base
            continue
        for d, dirs, names in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for n in names:
                if Path(n).suffix in TEXT:
                    yield Path(d) / n


def test_r3_is_not_recorded_as_answered() -> None:
    """The premise of the checks below; when the owner answers R3 in chat, the spec row says so."""
    assert "R3" not in answered()


def test_no_file_calls_an_unanswered_ruling_the_owners() -> None:
    ok = answered()
    bad = []
    for f in _files():
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in OWNERS.finditer(text):
            if f"R{m.group(1)}" not in ok:
                line = text.count("\n", 0, m.start()) + 1
                bad.append(f"{f.relative_to(REPO_ROOT)}:{line}: {m.group(0)!r}")
    assert not bad, "a ruling the owner has not given is called the owner's:\n" + "\n".join(bad)


def test_the_charters_gates_say_r3_awaits_the_owner() -> None:
    text = " ".join(CHARTER.read_text().split())
    para = text[text.index("The lines are ruling R3") :]
    para = para[: para.index("this table.") + len("this table.")]
    assert "awaits the owner" in para
    assert "owner's ruling" not in para


def test_the_judged_by_note_names_no_owner() -> None:
    """Every research manifest carries this note; it states the gates, not whose they are."""
    note = judged_by_note({})
    assert "owner" not in note.lower()
