"""``signals.daily`` (design §4.2; INC-01): every farm cell of the roster as one
contract-v1 document a session, for the desk to judge.

For each roster cell whose producer is ``signals.daily`` (the research
service's rows are not this producer's and are skipped silently):

* a cell naming a symbol outside ``book.example.sexp``'s positions is refused --
  never projected onto the book's names;
* its parameters are the month's frozen ones (:mod:`.freeze`);
* its target weights are the LAST row of the backtest's own strategy function,
  ``fn(StrategyContext, **params)``, built exactly as ``products/farm.py`` builds
  it (warehouse-first adjusted closes on common sessions, the T-bill rate, SPY
  as the market), with the engine's look-ahead audit run on it; ``as_of`` is
  that row's date. NaN and zero weights are dropped -- an all-flat row is an
  empty, valid document -- and a book over 1x gross is scaled to 1x, the
  document's params then carrying ``gross_scaled: true`` (INC-01, *Gross
  scaling*);
* ``validation`` is the cell's NEWEST farm verdict, never the frozen one;
* ``sequence`` is the SPY session ordinal in ``bars_daily`` at ``as_of``.

The document goes to ``ctx.signals_dir`` as ``<slug>-<as_of>.json`` through
``write_atomic``; one already there is never rewritten. Refusals are rows of
the artifact's ``refused`` table, never exceptions that stop the run.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..config import REPO_ROOT
from ..jobs.artifacts import ArtifactSpec
from .contract import GATES_VERSION, signal_v1, write_atomic
from .freeze import frozen_params, newest_farm
from .roster import ROSTER_PATH, Cell, load_roster, targets_gross_scaled

__all__ = ["BOOK_PATH", "book_symbols", "run", "targets_for", "validation_for"]

BOOK_PATH = REPO_ROOT / "book.example.sexp"
PRODUCER = "signals.daily"
NY = ZoneInfo("America/New_York")
_SYMBOL = re.compile(r"\(symbol ([A-Z][A-Z0-9.]*)\)")
_STATUS = {"PASS": "pass", "FAIL": "fail"}
EMITTED = ["slug", "as_of", "sequence", "gross_scaled", "n_targets", "status", "path"]
REFUSED = ["slug", "reason"]


class CellRefused(ValueError):
    """One cell cannot be emitted this session; the message is the artifact's reason."""


def _today() -> date:
    """The run's New York date: the month whose freeze applies."""
    return datetime.now(NY).date()


def book_symbols(path: Path = BOOK_PATH) -> frozenset[str]:
    """Every ``(symbol X)`` in the book's ``(positions …)`` block, comments stripped."""
    text = "\n".join(ln.split(";", 1)[0] for ln in Path(path).read_text().splitlines())
    start = text.index("(positions")
    end = text.find("(limits", start)
    return frozenset(_SYMBOL.findall(text[start : end if end > 0 else len(text)]))


def _weights(cell: Cell, params: dict[str, Any], prices: pd.DataFrame, rf: pd.Series | None,
             market: pd.Series | None) -> tuple[dict[str, float], date]:
    """The strategy's raw target weights at the last close, NaN and zero dropped."""
    from ..backtest.engine import EngineConfig, LookAheadError, StrategyContext, audit_causality
    from ..backtest.strategies import get_strategy, warmup_sessions

    if prices.empty:
        raise CellRefused("no common sessions for the cell's symbols")
    spec = get_strategy(str(cell.strategy))
    warm = warmup_sessions(spec, params)
    if len(prices) < warm:
        raise CellRefused(f"{len(prices)} sessions of history < the {warm}-session warm-up")
    ctx = StrategyContext(prices=prices, rf=rf, market=market)
    full = spec.fn(ctx, **params)
    try:
        audit_causality(spec.fn, ctx, params, full, EngineConfig().audit_points)
    except LookAheadError as e:
        raise CellRefused(f"look-ahead audit: {e}") from e
    last = full.reindex(index=prices.index, columns=prices.columns).iloc[-1]
    w = {str(s): float(v) for s, v in last.items() if np.isfinite(v) and float(v) != 0.0}
    return w, prices.index[-1].date()


def targets_for(cell: Cell, params: dict[str, Any], prices: pd.DataFrame, rf: pd.Series | None,
                market: pd.Series | None = None) -> tuple[dict[str, float], date]:
    """``fn(StrategyContext, **params)``'s last row (NaN and 0 dropped), gross-scaled to
    at most 1x, and that row's date."""
    w, as_of = _weights(cell, params, prices, rf, market)
    return targets_gross_scaled(w)[0], as_of


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def validation_for(cell: Cell, farm_cells: pd.DataFrame | None, *, manifest: str | None = None) -> dict:
    """The cell's newest farm verdict as a validation block: ``PASS`` -> pass, ``FAIL``
    -> fail, anything else or no row -> unvalidated; ``dsr``/``psr``/``pbo`` from the
    row when present, else null; ``manifest`` the farm artifact's id when it has a row."""
    from ..models.farm import cell_id

    row = None
    if farm_cells is not None and len(farm_cells) and "cell_id" in farm_cells.columns:
        hit = farm_cells[farm_cells["cell_id"] == cell_id(str(cell.strategy), str(cell.universe))]
        row = None if hit.empty else hit.iloc[-1].to_dict()
    if row is None:
        return {"status": "unvalidated", "gates_version": GATES_VERSION, "dsr": None, "psr": None,
                "pbo": None, "manifest": None}
    verdict = row.get("verdict")
    return {"status": _STATUS.get(verdict if isinstance(verdict, str) else "", "unvalidated"),
            "gates_version": GATES_VERSION,
            "dsr": _num(row.get("dsr", row.get("dsr_farm"))), "psr": _num(row.get("psr")),
            "pbo": _num(row.get("pbo")), "manifest": manifest}


def _inputs(cell: Cell, ctx: Any) -> tuple[pd.DataFrame, pd.Series | None, pd.Series | None]:
    """Prices, rf and market exactly as products/farm.py's ``run_cell`` builds them."""
    from ..backtest.strategies import get_strategy
    from ..data.base import DataUnavailable

    spec = get_strategy(str(cell.strategy))
    try:
        px = ctx.market.prices(list(cell.symbols), warehouse_first=True).data.dropna()
    except DataUnavailable as e:
        raise CellRefused(f"prices: {e}") from e
    if px.empty:
        raise CellRefused("no common sessions for the cell's symbols")
    try:
        rf = ctx.market.risk_free_daily(px.index[0].date(), px.index[-1].date()).data.reindex(px.index).fillna(0.0)
    except DataUnavailable:
        rf = None
    mkt = None
    if spec.needs_market:
        try:
            mkt = ctx.market.prices(["SPY"], warehouse_first=True).data["SPY"].reindex(px.index)
        except DataUnavailable as e:
            raise CellRefused(f"market (SPY) prices: {e}") from e
    return px, rf, mkt


def _sequence(ctx: Any, as_of: date, cache: dict[date, int]) -> int:
    """SPY's sessions in ``bars_daily`` up to and including ``as_of``: one per session."""
    if as_of not in cache:
        from ..data.base import DataUnavailable

        try:
            n = ctx.warehouse.execute("SELECT COUNT(*) FROM bars_daily WHERE ticker = 'SPY' AND date <= ?",
                                      [as_of]).fetchone()[0]
        except DataUnavailable as e:
            raise CellRefused(f"no sequence: {e}") from e
        cache[as_of] = int(n)
    if cache[as_of] < 1:
        raise CellRefused(f"no SPY session in bars_daily at or before {as_of}: no sequence")
    return cache[as_of]


def _bars(ctx: Any, symbols: list[str], sessions: pd.DatetimeIndex) -> pd.DataFrame:
    """The bars the strategy read: the warehouse's OHLCV rows on the sessions it used."""
    from ..data.base import DataUnavailable

    try:
        df = ctx.warehouse.execute(
            "SELECT ticker AS symbol, date, open, high, low, close, volume FROM bars_daily "
            "WHERE ticker IN (SELECT UNNEST(?)) AND date BETWEEN ? AND ?",
            [symbols, sessions[0].date(), sessions[-1].date()]).df()
    except DataUnavailable as e:
        raise CellRefused(f"no bars for the data hash: {e}") from e
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].isin(sessions)].copy()
    df["date"] = df["date"].dt.date
    return df


def _emit(cell: Cell, params: dict[str, Any], ctx: Any, out_dir: Path, farm_id: str | None,
          farm: pd.DataFrame | None, seq_cache: dict[date, int]) -> dict[str, Any]:
    px, rf, mkt = _inputs(cell, ctx)
    raw, as_of = _weights(cell, params, px, rf, mkt)
    targets, scaled = targets_gross_scaled(raw)
    doc_params = {**params, "gross_scaled": True} if scaled else dict(params)
    sequence = _sequence(ctx, as_of, seq_cache)
    path = out_dir / f"{cell.slug}-{as_of.isoformat()}.json"
    row = {"slug": cell.slug, "as_of": as_of.isoformat(), "sequence": sequence, "gross_scaled": scaled,
           "n_targets": len(targets), "path": path.name}
    if path.exists():
        return {**row, "status": "already_present"}
    doc = signal_v1(strategy=cell.slug, params=doc_params, as_of=as_of, sequence=sequence,
                    validation=validation_for(cell, farm, manifest=farm_id), targets=targets,
                    bars=_bars(ctx, list(px.columns), px.index))
    write_atomic(doc, path)
    return {**row, "status": "written"}


def _spec(emitted: list[dict], refused: list[dict], notes: list[str], *,
          verdict: str | None = None, detail: str | None = None,
          provenance: list[dict] | None = None) -> ArtifactSpec:
    em = pd.DataFrame(emitted, columns=EMITTED)
    return ArtifactSpec(tables={"emitted": em, "refused": pd.DataFrame(refused, columns=REFUSED)},
                        data_asof=str(em["as_of"].max()) if len(em) else None,
                        provenance=provenance or [], notes=notes, verdict=verdict, verdict_detail=detail)


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    cells = [c for c in load_roster(ROSTER_PATH).cells if c.producer == PRODUCER]
    out_dir = ctx.signals_dir
    if out_dir is None:
        why = "OHCAMEL_QUANT_SIGNALS_DIR is not set: signals.daily writes only to the signals directory"
        return _spec([], [{"slug": c.slug, "reason": why} for c in cells], [why],
                     verdict="INSUFFICIENT DATA", detail="NO SIGNALS DIRECTORY")
    book = book_symbols()
    refused: list[dict] = []
    eligible: list[Cell] = []
    for c in cells:
        outside = [s for s in c.symbols if s not in book]
        if outside:
            refused.append({"slug": c.slug, "reason": f"symbol {outside[0]} is not in the book"})
        elif not c.strategy or not c.universe or not c.symbols:
            refused.append({"slug": c.slug, "reason": "the roster row names no strategy, universe or symbols"})
        else:
            eligible.append(c)
    frozen, unfrozen = frozen_params(ctx, month=_today(), cells=eligible)
    refused.extend(unfrozen)
    farm_id, farm = newest_farm()
    emitted: list[dict] = []
    seq_cache: dict[date, int] = {}
    todo = [c for c in eligible if c.slug in frozen]
    for i, c in enumerate(todo):
        ctx.check_cancelled()
        ctx.progress(i / max(1, len(todo)), c.slug)
        try:
            emitted.append(_emit(c, frozen[c.slug], ctx, Path(out_dir), farm_id, farm, seq_cache))
        except CellRefused as e:
            refused.append({"slug": c.slug, "reason": str(e)})
        except Exception as e:  # noqa: BLE001 - one cell's failure is its refusal, never the run's (h)
            refused.append({"slug": c.slug, "reason": f"{type(e).__name__}: {e}"[:500]})
    written = sum(r["status"] == "written" for r in emitted)
    return _spec(emitted, refused,
                 [f"{written} written, {len(emitted) - written} already present, {len(refused)} refused; "
                  "parameters frozen for the month (INC-01); validation from the newest farm verdict"],
                 provenance=[{"source": "farm.sweep", "artifact": farm_id},
                             {"source": "roster", "path": str(ROSTER_PATH)}])
