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
_SEP = r"(?:\s|#|;|//|\*|>)+"
OWNERS = re.compile(
    rf"owner(?:'s)?{_SEP}ruling{_SEP}R([1-6])\b"           # owner's ruling R3, owner ruling R3
    rf"|owner's{_SEP}R([1-6]){_SEP}ruling\b"               # owner's R3 ruling
    rf"|R([1-6]){_SEP}(?:is|was){_SEP}the{_SEP}owner's"    # R3 is the owner's
    rf"|owner{_SEP}(?:has{_SEP})?ruled{_SEP}(?:on{_SEP})?R([1-6])\b"  # the owner has ruled on R3
)
#: The scan must reach at least this many text files, or a wrong root would pass silently.
MIN_FILES = 500


def _ruling(m: re.Match[str]) -> str:
    return "R" + next(g for g in m.groups() if g)


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
        if f.resolve() == Path(__file__).resolve():   # this file's own positive controls
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in OWNERS.finditer(text):
            if _ruling(m) not in ok:
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


# ------------------------------------------------------------------ positive controls
def test_the_table_records_some_ruling_as_answered() -> None:
    """If the table parse broke, nothing would be answered and the checks above would be vacuous."""
    assert {"R1", "R5"} <= answered()


def test_the_scan_reaches_the_repository() -> None:
    files = list(_files())
    assert len(files) >= MIN_FILES, f"the scan read {len(files)} files: is ROOTS right?"
    names = {f.relative_to(REPO_ROOT).as_posix() for f in files}
    for must in ("docs/CHARTER.md", "research/src/ohcamel_research/battery/run.py",
                 "quant/src/ohcamel_quant/models/verdict.py",
                 "quant/web/src/pages/methodology/models.ts", "test/test_intake.ml"):
        assert must in names, f"the scan never reads {must}"


def test_the_pattern_catches_each_phrasing() -> None:
    for text, ruling in [("the owner's ruling R3", "R3"), ("owner ruling R3", "R3"),
                         ("the owner's R3 ruling", "R3"), ("R3 is the owner's", "R3"),
                         ("the owner has ruled on R2", "R2"), ("the owner ruled R6", "R6"),
                         ("the owner's\n# ruling R4", "R4"), ("(owner\n   ruling R3)", "R3")]:
        m = OWNERS.search(text)
        assert m and _ruling(m) == ruling, text
    for text in ("ruling R3, the spec's default, awaiting the owner",
                 "the owner's approval of R3 is pending"):
        assert not OWNERS.search(text), text
