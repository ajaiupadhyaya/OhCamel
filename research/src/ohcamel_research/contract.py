"""The Python side of the signal contract.

The schema in interface/signal.schema.json is the shape. This module adds the
two hashes and the one rule the schema cannot express (R7's sum), and it is
the only place a signal document is validated before it is written. The OCaml
core re-validates everything on its side; the point of doing it here too is
that a malformed signal fails in the process that produced it, with a message,
rather than silently being rejected downstream.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd
from jsonschema import Draft202012Validator

from ohcamel_research import REPO_ROOT

SCHEMA_PATH = REPO_ROOT / "interface" / "signal.schema.json"
GATES_VERSION = "2026-09-02"  # the date of docs/CHARTER.md whose gates apply


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def canonical_json(obj: Any) -> str:
    """Sorted keys, no whitespace: the same object always hashes the same."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def params_hash(params: dict[str, Any]) -> str:
    return sha256(canonical_json(params))


def data_hash(bars: pd.DataFrame) -> str:
    """SHA-256 over the canonical bar text: ``symbol,date,open,high,low,close,volume``
    per line, sorted by (date, symbol), floats as ``repr``. Row order in the
    input does not matter; the sort is part of the recipe."""
    df = bars.sort_values(["date", "symbol"], kind="mergesort")
    lines = (
        f"{r.symbol},{r.date.isoformat()},{r.open!r},{r.high!r},{r.low!r},{r.close!r},{r.volume!r}"
        for r in df.itertuples(index=False)
    )
    return sha256("\n".join(lines) + "\n")


def check(doc: dict[str, Any]) -> list[str]:
    """Every problem with a signal document, as messages. Empty means valid."""
    validator = Draft202012Validator(load_schema())
    errors = [f"schema: {e.json_path}: {e.message}" for e in validator.iter_errors(doc)]
    targets = doc.get("targets")
    if isinstance(targets, list):
        total = 0.0
        for t in targets:
            if isinstance(t, dict) and isinstance(t.get("weight"), int | float):
                total += abs(float(t["weight"]))
        if total > 1.0 + 1e-9:
            errors.append(f"R7: sum |weight| = {total:g} > 1")
    return errors
