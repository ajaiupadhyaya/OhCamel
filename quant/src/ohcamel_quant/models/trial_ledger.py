"""The trial ledger (completion task 2.4, spec: "Every trial is counted"): every farm trial ever run, one JSON line
each, in ``<artifact root>/trial_ledger.jsonl``.

A trial is ``(cell_id, grid_version, params_hash)``: the cell, the version of its grid (:func:`grid_version`, a
hash of the parameter values the grid sweeps) and the hash of the combination's parameters
(``signals.contract.params_hash``, the hash ``signals.daily`` compares). Running the same grid again adds nothing --
the same trial on a few more sessions of data is not another trial -- and a changed grid adds trials. The farm's
deflated Sharpe takes its ``N`` from :func:`distinct` and its ``V[SR]`` from the Sharpes there.

Append-only, never rewritten. :func:`append_many` takes an exclusive lock, reads the identities already present,
and appends the new rows in one ``write`` (flushed and fsynced), so every earlier state of the file is a prefix of
the next. A row records the per-period excess Sharpe the trial had when it was FIRST run (a later re-run on more data
is the same trial and is not written), its ``n_obs`` sessions, when, and who ran it (``worker`` or ``burst``; a row
copied from an earlier artifact's snapshot keeps its original ``recorded_at`` and ``source``).
A line cut short by a crash mid-write is skipped when read (it was never a whole row) and the next append starts
on a new line after it, so it is never rewritten.

Who appends: the farm (``products/farm.py``) after each cell, on the worker; the burst door
(``jobs/burst_door.record_burst_trials``) when it publishes a farm artifact made on a burst machine: the new
rows of the artifact's ``ledger`` table, each checked to be a trial of this deployment's grid that the upload ran,
with the Sharpe its trials table holds (a row that is not refuses the upload) -- the same function, so a burst's
trials count on the droplet too.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEDGER_NAME = "trial_ledger.jsonl"
IDENTITY = ("cell_id", "grid_version", "params_hash")
COLUMNS = [*IDENTITY, "sharpe", "n_obs", "recorded_at", "source"]


def ledger_path(root: Path | None = None) -> Path:
    """The ledger under the artifact root (``jobs.paths.artifacts_root()`` when ``root`` is None)."""
    if root is None:
        from ..jobs.paths import artifacts_root

        root = artifacts_root()
    return Path(root) / LEDGER_NAME


def grid_version(grid: Mapping[str, Any]) -> str:
    """The version of a cell's grid: the first 16 hex digits of the SHA-256 of its canonical JSON (keys sorted), so
    the same parameter values are the same version whatever their order, and any changed value is a new one."""
    text = json.dumps({str(k): list(v) for k, v in grid.items()}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _finite(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _line(r: Mapping[str, Any], now: str, source: str) -> str:
    for k in IDENTITY:
        if not isinstance(r.get(k), str) or not r.get(k):
            raise ValueError(f"a trial needs its {k} (a non-empty string); got {r.get(k)!r}")
    n = r.get("n_obs")
    rec = {"cell_id": r["cell_id"], "grid_version": r["grid_version"], "params_hash": r["params_hash"],
           "sharpe": _finite(r.get("sharpe")), "n_obs": None if n is None or _finite(n) is None else int(n),
           "recorded_at": r.get("recorded_at") if isinstance(r.get("recorded_at"), str) else now,
           "source": r.get("source") if isinstance(r.get("source"), str) else source}
    return json.dumps(rec, separators=(",", ":"), allow_nan=False)


def _rows(lines: Iterable[str]) -> list[dict[str, Any]]:
    out = []
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue                  # a line a crash cut short: never a whole row
        if isinstance(rec, dict) and all(isinstance(rec.get(k), str) for k in IDENTITY):
            out.append(rec)
    return out


def append_many(path: Path, rows: Any, *, source: str = "worker") -> int:
    """Append each row whose trial is not in the ledger yet; -> how many were appended. ``rows`` is an iterable
    of mappings or a DataFrame with the identity columns, ``sharpe`` and ``n_obs`` (``recorded_at`` and ``source``
    are kept when given). Raises ``ValueError`` for a row without its identity."""
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [(tuple(r.get(k) for k in IDENTITY), _line(r, now, source)) for r in rows]
    if not lines:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            text = f.read()
            seen = {tuple(r[k] for k in IDENTITY) for r in _rows(text.splitlines())}
            new = []
            for key, ln in lines:
                if key not in seen:
                    seen.add(key)
                    new.append(ln)
            if new:
                lead = "\n" if text and not text.endswith("\n") else ""   # after a torn line, start a new one
                f.write(lead + "\n".join(new) + "\n")
                f.flush()
                os.fsync(f.fileno())
            return len(new)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def append(path: Path, cell_id: str, grid_version: str, params_hash: str, sharpe: float, n_obs: int, *,
           source: str = "worker") -> None:
    """Record one trial; nothing is written when the ledger already holds it (module docstring)."""
    append_many(path, [{"cell_id": cell_id, "grid_version": grid_version, "params_hash": params_hash,
                        "sharpe": sharpe, "n_obs": n_obs}], source=source)


def distinct(path: Path) -> Any:
    """One row per distinct trial -- the first time it was recorded -- in file order: ``cell_id, grid_version,
    params_hash, sharpe, n_obs, recorded_at, source``. Empty (with those columns) when there is no ledger."""
    import pandas as pd

    path = Path(path)
    recs = _rows(path.read_text(encoding="utf-8").splitlines()) if path.exists() else []
    df = pd.DataFrame(recs, columns=COLUMNS)
    df = df.drop_duplicates(list(IDENTITY), keep="first").reset_index(drop=True)
    df["sharpe"] = pd.to_numeric(df["sharpe"], errors="coerce").astype(float)
    df["n_obs"] = pd.to_numeric(df["n_obs"], errors="coerce").astype("Int64")
    return df
