"""INC-02's parameter-freeze rule (INC-02 is a draft awaiting the owner's approval; the rule is INC-01's, verbatim
from its pre-registration, with one addition, below):

    On the first session of each month the newest ``farm.sweep`` artifact's
    ``selected_params`` for the cell are read and held for the month. A cell the
    farm reports SKIPPED or INSUFFICIENT DATA keeps last month's parameters. A
    cell with none is not emitted, and the reason is recorded in the producer's
    artifact. The farm's nightly re-selection never reaches the live book inside
    a month.

THE EX-ANTE VOLATILITY (completion task 2.2). Each emitted cell also has a row in the ``volatility`` table (never served publicly: a stop's
threshold is a limit threshold) with ``ex_ante_vol``: the annualised
(``sqrt(252)``) standard deviation (``ddof=1``) of the cell's NET daily returns over the last
``VOL_SESSIONS`` = 252 live sessions of its backtest AT THE FROZEN (traded) PARAMETERS in the traded
configuration -- gross 1 (``max_gross_leverage=1``), decided at a close and filled at the next open (``fill="open"``,
lag 1), costed by the one cost file -- exactly the configuration the farm scores a cell in (``products.farm``).
It is what the desk's stop is a multiple of (``Stop.threshold``: twice it, floored at 0.05, capped at 0.25), and
the owner copies it into the book as ``(ex_ante_vol F)``. A cell whose backtest has FEWER than 252 live
sessions, or whose backtest cannot be run (no cost tier, no open prints, parameters the strategy refuses), has no
volatility and is NOT EMITTED, the reason recorded: no minimum is invented and no number is estimated from less.
With no ``ctx`` (tests of the parameter rule alone) no volatility is measured and the rows carry none.

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

__all__ = ["DB_PATH", "FREEZE_KIND", "VOL_SESSIONS", "ex_ante_vol", "freeze_id", "frozen_ex_ante_vol",
           "frozen_params", "month_key", "newest_farm", "realised_vol"]

FREEZE_KIND = "signals.freeze"
DB_PATH: Path | None = None   # tests point every artifact read and the freeze's publish at a temp jobs.sqlite
KEEPS_LAST_MONTH = frozenset({"SKIPPED", "INSUFFICIENT DATA"})
COLUMNS = ["slug", "strategy", "universe", "selected_params", "source_artifact", "verdict", "reason"]
#: The volatility table: a stop's threshold is a limit threshold and never public, so it lives in a table of its
#: own, which ``api/routers/artifacts.PUBLIC_TABLES`` does not serve (the ``params`` table is).
VOL_TABLE = "volatility"
VOL_COLUMNS = ["slug", "ex_ante_vol", "vol_sessions", "vol_asof"]
ERROR_PREFIX = "error: "   # a ``why`` that begins so is a failure to run the backtest (transient), not a short history
VOL_SESSIONS = 252   # INC-02: the realised volatility is measured over a trading year of live sessions


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


def realised_vol(returns: pd.Series, sessions: int = VOL_SESSIONS) -> float | None:
    """``std(r, ddof=1) * sqrt(252)`` of the last ``sessions`` returns; None when there are fewer than
    ``sessions`` finite ones (never a number from less) or the result is not finite."""
    import math

    r = pd.Series(returns).dropna()
    if len(r) < sessions:
        return None
    vol = float(r.iloc[-sessions:].std(ddof=1)) * math.sqrt(252)
    return vol if math.isfinite(vol) else None


def ex_ante_vol(cell: Cell, params: dict[str, Any], ctx: Any) -> tuple[float | None, int, str | None, str | None]:
    """``(vol, sessions, asof, why)``: the cell's ex-ante annualised volatility at ``params`` -- the last
    ``VOL_SESSIONS`` live sessions of its backtest in the traded configuration (module docstring) -- the number of
    live sessions the backtest had, the last session's date, and, when ``vol`` is None, why."""
    from ..backtest import costs as cost_file
    from ..backtest.engine import EngineConfig, StrategyContext, run_backtest
    from ..backtest.strategies import get_strategy, validate_params
    from ..data.base import DataUnavailable
    from ..products.farm import EXECUTION_LAG, FILL, MAX_GROSS, _prices_and_opens

    try:
        spec = get_strategy(str(cell.strategy))
        px, opens = _prices_and_opens(ctx, list(cell.symbols))
        model = cost_file.load()
        model.half_spreads(px.columns)   # NoCostTier (a ValueError): no tier, no costed backtest
        ran = validate_params(spec, params, list(px.columns))
        try:
            rf = ctx.market.risk_free_daily(px.index[0].date(), px.index[-1].date()).data.reindex(px.index).fillna(0.0)
        except DataUnavailable:
            rf = None
        mkt = ctx.market.prices(["SPY"], warehouse_first=True).data["SPY"].reindex(px.index) if spec.needs_market else None
        sctx = StrategyContext(prices=px, rf=rf, market=mkt, opens=opens)
        cfg = EngineConfig(rebalance=spec.default_rebalance, max_gross_leverage=MAX_GROSS, costs=model, fill=FILL,
                           execution_lag=EXECUTION_LAG)
        res = run_backtest(sctx, spec.fn, ran, cfg)
    except Exception as e:  # noqa: BLE001 - a cell that cannot be backtested has no volatility; its reason is recorded
        return None, 0, None, f"{ERROR_PREFIX}{type(e).__name__}: {e}"[:300]
    live = res.live(res.returns_net).dropna()
    vol = realised_vol(live)
    if vol is None:
        return None, len(live), None, (f"{len(live)} live sessions of the backtest at the traded parameters, fewer "
                                       f"than the {VOL_SESSIONS} its volatility is measured over")
    return vol, len(live), str(live.index[-1].date()), None


def newest_farm(db_path: Path | None = None) -> tuple[str | None, pd.DataFrame | None]:
    """The newest ``farm.sweep`` artifact's id and its cells, one row per ``cell_id``:
    the ``cells`` table's columns plus the leaderboard's ``verdict`` and ``dsr`` (its
    ``dsr_farm``). A skipped cell's verdict is ``SKIPPED``; an ok cell the leaderboard
    does not list has none. ``(None, None)`` when no farm artifact exists."""
    from ..products.io import previous_artifact, read_table

    got = previous_artifact("farm.sweep", db_path=db_path if db_path is not None else _db(), worker_only=True)
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


def _take(key: str, cells: list[Cell], db: Path,
          ctx: Any = None) -> tuple[pd.DataFrame, str | None, pd.DataFrame]:
    """The month's ``params`` table, from the newest farm artifact and last month's freeze; with a ``ctx``, each
    emitted row's ``ex_ante_vol`` too, in the second table (a cell with none is not emitted)."""
    from ..products.io import previous_artifact, read_table

    farm_id, farm = newest_farm(db)
    last_key = month_key(_previous_month(date.fromisoformat(key + "-01")))
    last = previous_artifact(FREEZE_KIND, _phash(last_key), db_path=db, worker_only=True)
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
    vols = []
    if ctx is not None:
        for row, c in zip(rows, cells, strict=True):
            if row["selected_params"] is None:
                continue
            vol, n, asof, why = ex_ante_vol(c, json.loads(row["selected_params"]), ctx)
            if vol is None and not str(why).startswith(ERROR_PREFIX):
                # persisted: the history is short, and stays short for the month
                row.update(selected_params=None, reason=f"no ex-ante volatility, so not emitted: {why}")
            elif vol is not None:
                vols.append({"slug": c.slug, "ex_ante_vol": vol, "vol_sessions": n, "vol_asof": asof})
    return pd.DataFrame(rows, columns=COLUMNS), farm_id, pd.DataFrame(vols, columns=VOL_COLUMNS)


def _publish(db: Path, key: str, table: pd.DataFrame, farm_id: str | None, vols: pd.DataFrame) -> str:
    """Publish one ``signals.freeze`` artifact exactly as the worker publishes a job's."""
    from ..jobs.artifacts import build_manifest, publish, write_tables
    from ..jobs.db import connect, iso, tx, utcnow
    from ..jobs.ulid import new_ulid

    root = db.parent / "artifacts"  # jobs/paths.py: jobs.sqlite and artifacts/ share Settings.data_dir
    now = iso(utcnow())
    job = SimpleNamespace(id=new_ulid(), kind=FREEZE_KIND, params={"month": key}, params_hash=_phash(key),
                          started_at=now)
    staging = root / ".staging" / job.id
    names = write_tables(staging, {"params": table, VOL_TABLE: vols})
    manifest = build_manifest(job, {  # type: ignore[arg-type]
        "data_asof": None, "tables": names,
        "provenance": [{"source": "farm.sweep", "artifact": farm_id}],
        "notes": [f"INC-02's parameter freeze for {key}: the newest farm.sweep artifact's selected_params, "
                  "held for the month; SKIPPED or INSUFFICIENT DATA keeps last month's; none is not emitted; "
                  f"ex_ante_vol is the {VOL_SESSIONS}-session annualised volatility of the cell's backtest at "
                  "those parameters, gross 1, filled at the next open"]},
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


def vol_table_slugs(got: Any) -> set[str]:
    """The slugs with a row in a freeze artifact's ``volatility`` table (``got`` as ``previous_artifact`` returns it;
    None, or an artifact frozen before the table existed, has none)."""
    from ..products.io import read_table

    t = read_table(got[1], VOL_TABLE) if got else None
    return set(t["slug"]) if t is not None and len(t) else set()


def frozen_params(ctx: Any, *, month: date, cells: list[Cell]) -> tuple[dict[str, dict], list[dict]]:
    """``({slug: selected_params}, [{slug, reason}])`` for ``cells`` in ``month``: the
    month's ``signals.freeze`` artifact, taken now (from the newest farm artifact) when
    the month has none yet. A cell with no usable parameters, or one that joined the
    roster after the month's freeze was taken, is in the second list, with the reason."""
    from ..products.io import previous_artifact, read_table

    db = _db()
    key = month_key(month)
    got = previous_artifact(FREEZE_KIND, _phash(key), db_path=db, worker_only=True)
    if got is None:
        if ctx is not None:
            ctx.progress(0.0, f"taking the {key} parameter freeze")
        table, farm_id, vols = _take(key, cells, db, ctx)
        _publish(db, key, table, farm_id, vols)
        got = previous_artifact(FREEZE_KIND, _phash(key), db_path=db, worker_only=True)
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
    if ctx is not None:
        # A cell whose volatility row is missing -- its backtest failed when the month was frozen (a transient
        # error is not persisted, so it can retry within the month), or the month was frozen before task 2.2 --
        # is measured now; one that still cannot be is refused this run only.
        have = vol_table_slugs(got)
        for c in cells:
            if c.slug in params and c.slug not in have:
                vol, _, _, why = ex_ante_vol(c, params[c.slug], ctx)
                if vol is None:
                    del params[c.slug]
                    refused.append({"slug": c.slug, "reason": f"no ex-ante volatility, so not emitted this run: {why}"})
    return params, refused


def frozen_ex_ante_vol(*, month: date, cells: list[Cell]) -> dict[str, float]:
    """``{slug: ex_ante_vol}`` from ``month``'s ``signals.freeze`` artifact, for the cells whose row carries one
    (a month frozen without a ``ctx``, or before this column existed, carries none). Read only: it never takes a
    freeze."""
    from ..products.io import previous_artifact, read_table

    got = previous_artifact(FREEZE_KIND, _phash(month_key(month)), db_path=_db(), worker_only=True)
    table = read_table(got[1], VOL_TABLE) if got else None
    out: dict[str, float] = {}
    for c in cells:
        r = _row(table, "slug", c.slug)
        v = r.get("ex_ante_vol") if r else None
        if isinstance(v, int | float) and v == v:
            out[c.slug] = float(v)
    return out


def freeze_id(month: date) -> str | None:
    """The id of ``month``'s ``signals.freeze`` artifact, or None when it has none:
    ``signals.daily`` names it in its provenance, so a document's parameters can be
    traced to the freeze that held them."""
    from ..products.io import previous_artifact

    got = previous_artifact(FREEZE_KIND, _phash(month_key(month)), db_path=_db(), worker_only=True)
    return None if got is None else str(got[0]["id"])
