"""Admission control (compute plan B3). B2 ships only the permissive rule used by tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Admission:
    allowed_classes: frozenset[str]
    allow_heavy: bool
    threads: int
    reason: str


def admit_all(now: datetime) -> Admission:
    return Admission(frozenset({"S", "M", "L"}), True, 2, "admit_all (tests only)")
