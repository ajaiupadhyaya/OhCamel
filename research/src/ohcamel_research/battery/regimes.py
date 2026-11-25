"""The one regime table, ``research/config/regimes.yaml`` (completion task 2.5,
spec ruling R3, awaiting the owner): the stress windows the charter's regime gate reads, written
once and read by both batteries -- this module for the research battery
(``gates.regime_gate``), ``ohcamel_quant.models.regimes`` for the quant
battery. The two loaders apply the same rules and each package's tests load
the other's and require the same tuple.

Standalone on purpose (the standard library and PyYAML only, no package
import): nothing under ``battery/`` may import ``ohcamel_research.*`` from
outside it (``tests/test_battery_boundary.py``), and the quant suite loads
this file by its path to compare tables. The path is resolved from this file's
own location (battery -> ohcamel_research -> src -> research -> the
repository), as ``gates.COSTS_PATH`` is.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import yaml

REGIMES_PATH = Path(__file__).resolve().parents[4] / "research" / "config" / "regimes.yaml"
#: The path as manifests and pages name it (repo-relative).
REGIMES_REL = "research/config/regimes.yaml"

Regime = tuple[str, str, str]  # (name, first day, last day), ISO dates, both inclusive


def _iso(v: Any, where: str) -> str:
    try:
        return (v if isinstance(v, date) else date.fromisoformat(str(v))).isoformat()
    except ValueError as e:
        raise ValueError(f"{where}: {v!r} is not an ISO date") from e


def parse(doc: Mapping[str, Any], where: str) -> tuple[Regime, ...]:
    """The table in ``doc`` as ``((name, start, end), ...)`` in the file's order.
    Refused: a missing or empty list, a name used twice, a window that ends
    before it starts, and windows out of date order or overlapping -- regimes
    are distinct by construction, so one day can never count twice."""
    try:
        rows = doc["regimes"]
        out = tuple((str(r["name"]), _iso(r["start"], where), _iso(r["end"], where)) for r in rows)
    except (KeyError, TypeError) as e:
        raise ValueError(f"{where}: not a regime table ({type(e).__name__}: {e})") from e
    if not out:
        raise ValueError(f"{where}: the regime table is empty")
    names = [n for n, _, _ in out]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"{where}: regime {dup[0]!r} is named twice")
    for n, a, b in out:
        if b < a:
            raise ValueError(f"{where}: regime {n!r} ends ({b}) before it starts ({a})")
    for (n0, _, b0), (n1, a1, _) in zip(out, out[1:], strict=False):
        if a1 <= b0:
            raise ValueError(
                f"{where}: regime {n1!r} starts ({a1}) on or before {n0!r} ends ({b0})"
            )
    return out


def load(path: Path | None = None) -> tuple[Regime, ...]:
    """The regime table in ``path`` (default: the one regime file)."""
    p = Path(path or REGIMES_PATH)
    return parse(yaml.safe_load(p.read_text(encoding="utf-8")), str(p))
