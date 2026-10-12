"""Signal (v1, v2) and overlay documents for the desk's intake.

A port of ``research/src/ohcamel_research/contract.py``'s two hash recipes --
not an import: the Quant image does not contain ``ohcamel_research``. The
hashes are byte-identical to the research package's (the tests pin both
packages' vectors). Every document is validated against ``interface/``'s
schemas before it is written, and written atomically, so the desk -- which
reads only regular ``*.json`` files -- never sees a half-written one.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jsonschema import Draft202012Validator

from ohcamel_quant.config import REPO_ROOT

__all__ = [
    "GATES_VERSION",
    "OVERLAY_SCHEMA_PATH",
    "V1_SCHEMA_PATH",
    "V2_SCHEMA_PATH",
    "canonical_json",
    "data_hash",
    "overlay_doc",
    "params_hash",
    "schema_path_for",
    "signal_v1",
    "signal_v2",
    "validate",
    "write_atomic",
]

INTERFACE = REPO_ROOT / "interface"
V1_SCHEMA_PATH = INTERFACE / "signal.schema.json"
V2_SCHEMA_PATH = INTERFACE / "signal.v2.schema.json"
OVERLAY_SCHEMA_PATH = INTERFACE / "overlay.schema.json"

# The date of docs/CHARTER.md's gates, as ohcamel_research.battery.GATES_VERSION.
GATES_VERSION = "2026-09-02"


def canonical_json(obj: Any) -> str:
    """Sorted keys, no whitespace: the same object always hashes the same."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def params_hash(params: Mapping[str, Any]) -> str:
    return _sha256(canonical_json(dict(params)))


def data_hash(bars: pd.DataFrame) -> str:
    """SHA-256 over the canonical bar text: ``symbol,date,open,high,low,close,volume``
    per line, sorted by (date, symbol), floats as ``repr``, each line
    newline-terminated. Row order in the input does not matter."""
    df = bars.sort_values(["date", "symbol"], kind="mergesort")
    lines = (
        f"{r.symbol},{r.date.isoformat()},{r.open!r},{r.high!r},{r.low!r},{r.close!r},{r.volume!r}"
        for r in df.itertuples(index=False)
    )
    return _sha256("\n".join(lines) + "\n")


def _rfc3339(t: datetime) -> str:
    """UTC, second precision, ``Z``. A naive datetime is refused: an instant
    without a zone is not an instant."""
    if t.tzinfo is None:
        raise ValueError(f"naive datetime {t!r}: give it a timezone")
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unvalidated() -> dict[str, Any]:
    return {
        "status": "unvalidated",
        "gates_version": GATES_VERSION,
        "dsr": None,
        "psr": None,
        "pbo": None,
        "manifest": None,
    }


def _targets(targets: Mapping[str, float]) -> list[dict[str, Any]]:
    """Zero weights dropped, sorted by symbol."""
    return [
        {"symbol": s, "weight": float(w)} for s, w in sorted(targets.items()) if float(w) != 0.0
    ]


def _signal(
    version: int,
    *,
    strategy: str,
    params: Mapping[str, Any],
    as_of: str,
    sequence: int,
    validation: dict[str, Any] | None,
    targets: Mapping[str, float],
    bars: pd.DataFrame,
    extra: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": version,
        "strategy": strategy,
        "params_hash": params_hash(params),
        "as_of": as_of,
        **extra,
        "computed_at": _rfc3339(datetime.now(UTC)),
        "data_hash": data_hash(bars),
        "sequence": sequence,
        "validation": _unvalidated() if validation is None else validation,
        "targets": _targets(targets),
    }


def signal_v1(
    *,
    strategy: str,
    params: Mapping[str, Any],
    as_of: date,
    sequence: int,
    validation: dict[str, Any] | None,
    targets: Mapping[str, float],
    bars: pd.DataFrame,
) -> dict[str, Any]:
    """A v1 (daily) signal: ``as_of`` is the bar date at whose close it was computed."""
    return _signal(
        1,
        strategy=strategy,
        params=params,
        as_of=as_of.isoformat(),
        sequence=sequence,
        validation=validation,
        targets=targets,
        bars=bars,
        extra={},
    )


def signal_v2(
    *,
    strategy: str,
    params: Mapping[str, Any],
    as_of: datetime,
    bar: str,
    horizon: str,
    sequence: int,
    validation: dict[str, Any] | None,
    targets: Mapping[str, float],
    bars: pd.DataFrame,
) -> dict[str, Any]:
    """A v2 (intraday) signal: ``as_of`` is the instant the bar it read closed."""
    return _signal(
        2,
        strategy=strategy,
        params=params,
        as_of=_rfc3339(as_of),
        sequence=sequence,
        validation=validation,
        targets=targets,
        bars=bars,
        extra={"bar": bar, "horizon": horizon},
    )


def overlay_doc(
    *,
    as_of: datetime,
    sequence: int,
    exposure_scale: float,
    inputs: dict[str, Any],
    reported: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "overlay",
        "as_of": _rfc3339(as_of),
        "computed_at": _rfc3339(datetime.now(UTC)),
        "sequence": sequence,
        "exposure_scale": float(exposure_scale),
        "inputs": inputs,
        "reported": reported,
    }


def schema_path_for(doc: Mapping[str, Any]) -> Path:
    """An overlay by ``kind``, a v2 signal by ``schema_version``, else v1."""
    if doc.get("kind") == "overlay":
        return OVERLAY_SCHEMA_PATH
    if doc.get("schema_version") == 2:
        return V2_SCHEMA_PATH
    return V1_SCHEMA_PATH


def validate(doc: dict[str, Any], schema: Path) -> None:
    """Raise ``jsonschema.ValidationError`` unless ``doc`` is valid against
    ``schema``; for a signal, also ``ValueError`` when the sum of |weight|
    exceeds 1 (R7's sum, which the schema cannot express)."""
    Draft202012Validator(json.loads(Path(schema).read_text())).validate(doc)
    if doc.get("kind") == "overlay":
        return
    total = sum(abs(float(t["weight"])) for t in doc.get("targets", []))
    if total > 1.0 + 1e-9:
        raise ValueError(f"R7: sum |weight| = {total:g} > 1")


def write_atomic(doc: dict[str, Any], path: Path) -> Path:
    """Validate ``doc`` against the schema its content names, then write it to
    ``path`` atomically: a temp file in the same directory (a dotfile ending in
    ``.tmp``, never ``*.json``, the shape research's ``sweep_orphaned_tmp``
    clears), fsync, ``os.replace``. No temp file survives, success or failure.
    NaN is refused (``allow_nan=False``)."""
    validate(doc, schema_path_for(doc))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        text = json.dumps(doc, indent=2, allow_nan=False) + "\n"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path
