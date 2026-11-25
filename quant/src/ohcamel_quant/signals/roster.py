"""INC-02's roster (research/experiments/INC-02/roster.yaml): the one list every
producer reads. INC-01 closed at 2026-11-05 and its roster.yaml stays as it
traded; INC-02 is the agent's draft awaiting the owner's approval, and the
roster here only names what may be sized once the owner approves it and resets
the switch. Loaded strictly -- an unknown key, a fraction outside (0, 1], a
malformed or repeated slug, an unknown producer or fractions summing past
``incubation_cap`` is a ``ValueError``, never a silently skipped row."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ohcamel_quant.config import REPO_ROOT

__all__ = ["PRODUCERS", "ROSTER_PATH", "Cell", "Roster", "load_roster", "preregistration_text", "recorded_approval", "roster_draft",
           "targets_gross_scaled"]

ROSTER_PATH = REPO_ROOT / "research" / "experiments" / "INC-02" / "roster.yaml"
PRODUCERS = frozenset({"research-service", "signals.daily", "signals.intraday", "signals.q01"})
_SLUG = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TOP_KEYS = frozenset({"version", "incubation_cap", "canary_cap", "vol_target", "strategies"})
_ROW_KEYS = frozenset({"slug", "producer", "strategy", "universe", "symbols", "capital_fraction"})
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


@dataclass(frozen=True)
class Cell:
    slug: str
    producer: str
    strategy: str | None
    universe: str | None
    symbols: tuple[str, ...]
    capital_fraction: float


@dataclass(frozen=True)
class Roster:
    version: int
    incubation_cap: float
    canary_cap: float
    vol_target: float
    cells: tuple[Cell, ...]


def _fraction(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 1:
        raise ValueError(f"{what} must be a number in (0, 1], got {value!r}")
    return float(value)


def _positive(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise ValueError(f"{what} must be a positive number, got {value!r}")
    return float(value)


def _cell(raw: Any) -> Cell:
    if not isinstance(raw, dict):
        raise ValueError(f"roster row is not a mapping: {raw!r}")
    unknown = set(raw) - _ROW_KEYS
    if unknown:
        raise ValueError(f"roster row {raw.get('slug')!r}: unknown keys {sorted(unknown)}")
    slug = raw.get("slug")
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise ValueError(f"roster slug {slug!r} does not match {_SLUG.pattern}")
    producer = raw.get("producer")
    if producer not in PRODUCERS:
        raise ValueError(f"roster row {slug}: producer {producer!r} not in {sorted(PRODUCERS)}")
    symbols = raw.get("symbols", [])
    if not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols):
        raise ValueError(f"roster row {slug}: symbols must be a list of strings")
    return Cell(
        slug=slug,
        producer=producer,
        strategy=raw.get("strategy"),
        universe=raw.get("universe"),
        symbols=tuple(symbols),
        capital_fraction=_fraction(
            raw.get("capital_fraction"), f"roster row {slug}: capital_fraction"
        ),
    )


def load_roster(path: Path = ROSTER_PATH) -> Roster:
    doc = yaml.safe_load(Path(path).read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: not a mapping")
    unknown = set(doc) - _TOP_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    cells = tuple(_cell(r) for r in doc.get("strategies") or [])
    seen: set[str] = set()
    for c in cells:
        if c.slug in seen:
            raise ValueError(f"{path}: duplicate slug {c.slug}")
        seen.add(c.slug)
    cap = _fraction(doc.get("incubation_cap"), "incubation_cap")
    total = sum(c.capital_fraction for c in cells)
    if total > cap + 1e-9:
        raise ValueError(f"{path}: capital fractions sum to {total:g} > incubation_cap {cap:g}")
    return Roster(
        version=int(doc.get("version", 1)),
        incubation_cap=cap,
        canary_cap=_fraction(doc.get("canary_cap"), "canary_cap"),
        vol_target=_positive(doc.get("vol_target"), "vol_target"),
        cells=cells,
    )


def recorded_approval(text: str) -> str | None:
    """The approval date a pre-registration gives: its ``Approved-by:`` line, else its ``**Status: APPROVED``
    line, the first date on that line; None when it records no approval (the ledger reads the same)."""
    for line in text.splitlines():
        if re.match(r"^`?Approved-by:`?", line.strip()) and (m := _DATE.search(line)):
            return m.group(1)
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("**Status:") and "APPROVED" in s and (m := _DATE.search(s[s.index("APPROVED"):])):
            return m.group(1)
    return None


def preregistration_text(path: Path = ROSTER_PATH) -> str:
    """The pre-registration beside the roster at ``path``; empty when it cannot be read (records no approval)."""
    try:
        return (Path(path).parent / "preregistration.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def roster_draft(path: Path = ROSTER_PATH) -> str | None:
    """The experiment whose roster ``path`` is, when its pre-registration beside it records no approval (an
    unreadable one records none); None once the owner's approval is recorded there. A page that draws the roster
    says it is a draft while this names one, so the label goes when the ``Approved-by:`` line is written."""
    text = preregistration_text(path)
    if not text:
        return Path(path).parent.name
    return None if recorded_approval(text) else Path(path).parent.name


def targets_gross_scaled(w: dict[str, float]) -> tuple[dict[str, float], bool]:
    """``w / max(1, sum |w|)``: a book over 1x gross is scaled to exactly 1x
    (R7's sum), and the flag says whether it was."""
    gross = sum(abs(v) for v in w.values())
    if gross <= 1.0:
        return dict(w), False
    return {k: v / gross for k, v in w.items()}, True
