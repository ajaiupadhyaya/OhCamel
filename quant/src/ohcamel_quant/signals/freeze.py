"""INC-01's parameter-freeze rule, verbatim from its pre-registration:

    On the first session of each month the newest ``farm.sweep`` artifact's
    ``selected_params`` for the cell are read and held for the month. A cell the
    farm reports SKIPPED or INSUFFICIENT DATA keeps last month's parameters. A
    cell with none is not emitted, and the reason is recorded in the producer's
    artifact. The farm's nightly re-selection never reaches the live book inside
    a month.

``signals.daily`` calls :func:`frozen_params` on every run. The first run in a
month that has no ``signals.freeze`` artifact reads the newest farm artifact and
publishes one (table ``params``, one row per cell); every later run that month
reads that artifact back, so the month is read from the farm exactly once.
"Last month's" is the previous month's freeze row. A cell the newest farm
artifact does not carry at all, or no farm artifact at all, is treated as the
farm not reporting it: last month's row is kept when there is one.

The freeze artifact is published from inside the ``signals.daily`` child exactly
as the worker publishes a job's: tables staged, ``manifest.json`` written, the
directory renamed into ``<data>/artifacts/signals.freeze/<id>``, then one
``artifacts`` row. Its ``params`` are ``{"month": "YYYY-MM"}``, so its
``params_hash`` names the month and retention keeps every month's.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from .roster import Cell

__all__ = ["DB_PATH", "FREEZE_KIND", "freeze_id", "frozen_params", "month_key", "newest_farm"]

FREEZE_KIND = "signals.freeze"
DB_PATH: Path | None = None   # tests point every artifact read and the freeze's publish at a temp jobs.sqlite
KEEPS_LAST_MONTH = frozenset({"SKIPPED", "INSUFFICIENT DATA"})
COLUMNS = ["slug", "strategy", "universe", "selected_params", "source_artifact", "verdict", "reason"]


def _db() -> Path:
    from ..jobs.paths import jobs_db_path

    return Path(DB_PATH) if DB_PATH is not None else jobs_db_path()


def month_key(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def _previous_month(month: date) -> date:
    return date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)


def _phash(key: str) -> str:
    from ..jobs.queue import params_hash

    return params_hash({"month": key})


def _cell_id(cell: Cell) -> str:
    from ..models.farm import cell_id

    return cell_id(str(cell.strategy), str(cell.universe))


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def newest_farm(db_path: Path | None = None) -> tuple[str | None, pd.DataFrame | None]:
    """The newest ``farm.sweep`` artifact's id and its cells, one row per ``cell_id``:
    the ``cells`` table's columns plus the leaderboard's ``verdict`` and ``dsr`` (its
    ``dsr_farm``). A skipped cell's verdict is ``SKIPPED``; an ok cell the leaderboard
    does not list has none. ``(None, None)`` when no farm artifact exists."""
    from ..products.io import previous_artifact, read_table

    got = previous_artifact("farm.sweep", db_path=db_path if db_path is not None else _db())
    if got is None:
        return None, None
    manifest, path = got
    cells = read_table(path, "cells")
    if cells is None:
        return manifest["id"], None
    board = read_table(path, "leaderboard")
    out = cells.drop(columns=[c for c in ("verdict", "dsr") if c in cells.columns])
    if board is not None and len(board) and "verdict" in board.columns:
        cols = board[["cell_id", "verdict"] + (["dsr_farm"] if "dsr_farm" in board.columns else [])]
        out = out.merge(cols.rename(columns={"dsr_farm": "dsr"}), on="cell_id", how="left")
    else:
        out = out.assign(verdict=None)
    if "status" in out.columns:
        out["verdict"] = out["verdict"].astype(object).where(out["status"] != "skipped", "SKIPPED")
    out["verdict"] = out["verdict"].astype(object).where(out["verdict"].notna(), None)
    return manifest["id"], out


def _row(df: pd.DataFrame | None, col: str, key: str) -> dict[str, Any] | None:
    if df is None or not len(df) or col not in df.columns:
        return None
    hit = df[df[col] == key]
    return None if hit.empty else hit.iloc[-1].to_dict()


def _take(key: str, cells: list[Cell], db: Path) -> tuple[pd.DataFrame, str | None]:
    """The month's ``params`` table, from the newest farm artifact and last month's freeze."""
    from ..products.io import previous_artifact, read_table

    farm_id, farm = newest_farm(db)
    last_key = month_key(_previous_month(date.fromisoformat(key + "-01")))
    last = previous_artifact(FREEZE_KIND, _phash(last_key), db_path=db)
    last_rows = read_table(last[1], "params") if last else None
    rows = []
    for c in cells:
        base = {"slug": c.slug, "strategy": c.strategy, "universe": c.universe}
        r = _row(farm, "cell_id", _cell_id(c))
        verdict = _str(r.get("verdict")) if r else None
        status = _str(r.get("status")) if r else None
        sel = _str(r.get("selected_params")) if r else None
        if r is not None and status in (None, "ok") and verdict not in KEEPS_LAST_MONTH and sel is not None:
            rows.append({**base, "selected_params": sel, "source_artifact": farm_id, "verdict": verdict,
                         "reason": None})
            continue
        if farm is None:
            why = "no farm.sweep artifact"
        elif r is None:
            why = "absent from the newest farm.sweep artifact"
        elif verdict in KEEPS_LAST_MONTH:
            why = f"the farm reports {verdict}"
        else:
            why = "the farm selected no parameters"
        prev = _row(last_rows, "slug", c.slug)
        prev_sel = _str(prev.get("selected_params")) if prev else None
        if prev_sel is not None:
            rows.append({**base, "selected_params": prev_sel, "source_artifact": prev.get("source_artifact"),
                         "verdict": verdict, "reason": f"{why}: {last_key}'s parameters kept"})
        else:
            rows.append({**base, "selected_params": None, "source_artifact": farm_id, "verdict": verdict,
                         "reason": f"{why}, and no {last_key} parameters to keep: not emitted"})
    return pd.DataFrame(rows, columns=COLUMNS), farm_id


def _publish(db: Path, key: str, table: pd.DataFrame, farm_id: str | None) -> str:
    """Publish one ``signals.freeze`` artifact exactly as the worker publishes a job's."""
    from ..jobs.artifacts import build_manifest, publish, write_tables
    from ..jobs.db import connect, iso, tx, utcnow
    from ..jobs.ulid import new_ulid

    root = db.parent / "artifacts"  # jobs/paths.py: jobs.sqlite and artifacts/ share Settings.data_dir
    now = iso(utcnow())
    job = SimpleNamespace(id=new_ulid(), kind=FREEZE_KIND, params={"month": key}, params_hash=_phash(key),
                          started_at=now)
    staging = root / ".staging" / job.id
    names = write_tables(staging, {"params": table})
    manifest = build_manifest(job, {  # type: ignore[arg-type]
        "data_asof": None, "tables": names,
        "provenance": [{"source": "farm.sweep", "artifact": farm_id}],
        "notes": [f"INC-01's parameter freeze for {key}: the newest farm.sweep artifact's selected_params, "
                  "held for the month; SKIPPED or INSUFFICIENT DATA keeps last month's; none is not emitted"]},
        code_sha=os.environ.get("OHCAMEL_GIT_SHA") or "unknown", finished_at=now, cpu_seconds=0.0,
        peak_rss_bytes=None)
    final = publish(staging, root, job, manifest)  # type: ignore[arg-type]
    conn = connect(db)
    try:
        with tx(conn):
            conn.execute("INSERT INTO artifacts (id, kind, params_hash, finished_at, path, data_asof) "
                         "VALUES (?,?,?,?,?,?)", (job.id, FREEZE_KIND, job.params_hash, now, str(final), None))
    finally:
        conn.close()
    return job.id


def frozen_params(ctx: Any, *, month: date, cells: list[Cell]) -> tuple[dict[str, dict], list[dict]]:
    """``({slug: selected_params}, [{slug, reason}])`` for ``cells`` in ``month``: the
    month's ``signals.freeze`` artifact, taken now (from the newest farm artifact) when
    the month has none yet. A cell with no usable parameters, or one that joined the
    roster after the month's freeze was taken, is in the second list, with the reason."""
    from ..products.io import previous_artifact, read_table

    db = _db()
    key = month_key(month)
    got = previous_artifact(FREEZE_KIND, _phash(key), db_path=db)
    if got is None:
        if ctx is not None:
            ctx.progress(0.0, f"taking the {key} parameter freeze")
        table, farm_id = _take(key, cells, db)
        _publish(db, key, table, farm_id)
    else:
        table = read_table(got[1], "params")
        table = table if table is not None else pd.DataFrame(columns=COLUMNS)
    params: dict[str, dict] = {}
    refused: list[dict] = []
    for c in cells:
        r = _row(table, "slug", c.slug)
        sel = _str(r.get("selected_params")) if r else None
        if r is None:
            refused.append({"slug": c.slug, "reason": f"not in the {key} parameter freeze (joined the roster "
                                                      "after it was taken): not emitted until next month"})
        elif sel is None:
            refused.append({"slug": c.slug, "reason": _str(r.get("reason")) or "no frozen parameters"})
        else:
            params[c.slug] = json.loads(sel)
    return params, refused


def freeze_id(month: date) -> str | None:
    """The id of ``month``'s ``signals.freeze`` artifact, or None when it has none:
    ``signals.daily`` names it in its provenance, so a document's parameters can be
    traced to the freeze that held them."""
    from ..products.io import previous_artifact

    got = previous_artifact(FREEZE_KIND, _phash(month_key(month)), db_path=_db())
    return None if got is None else str(got[0]["id"])
