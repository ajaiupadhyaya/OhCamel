"""``signals.daily`` (design §4.2; INC-01): every farm cell of the roster as a
contract-v1 document on the strategy's own decision sessions, for the desk to judge.

For each roster cell whose producer is ``signals.daily`` (the research
service's rows are not this producer's and are skipped silently):

* a cell naming a symbol outside ``book.example.sexp``'s positions is refused --
  never projected onto the book's names -- and so is one whose roster symbols
  are not ``universes.json``'s members for its universe (the farm's cell);
* its parameters are the month's frozen ones (:mod:`.freeze`), checked and
  merged over the defaults by ``validate_params`` as the farm's ``sweep`` does;
* its target weights are the LAST row of the backtest's own strategy function,
  ``fn(StrategyContext, **params)``, built exactly as ``products/farm.py`` builds
  it (warehouse-first adjusted closes on common sessions, the T-bill rate, SPY
  as the market), with the engine's look-ahead audit run on it; ``as_of`` is
  that row's date;
* the engine's decision rule decides whether there is a document at all:
  an all-NaN row is no decision (``held``; the engine holds its positions);
  ``weekly``/``monthly``/``quarterly`` decide only on a period's last session
  (``as_of``'s next NYSE session falls in a new period -- the engine's own
  ``rebalance_mask`` definition, decided against the calendar because the
  panel's final session is never a period end there); ``signal`` decides only
  when the targets move more than ``EngineConfig().signal_tolerance`` from the
  last EMITTED document's; ``daily`` always. A cell's first document ever is
  always emitted. Every other session is a ``held`` row, naming the last
  rebalance;
* NaN and zero weights are dropped -- an all-zero row is a flat decision, an
  empty valid document -- and a book over 1x gross is scaled to 1x, the
  document's params then carrying ``gross_scaled: true`` (INC-01);
* ``validation`` is the cell's NEWEST farm verdict, and only when that row
  selected the parameters that ran (same ``params_hash``); otherwise unvalidated;
* ``sequence`` is ``max(SPY sessions in bars_daily through as_of, the slug's last
  document's sequence + 1)``: it only rises, whatever happens to the table;
  a session SPY has no bar for is refused.

The document goes to ``ctx.signals_dir`` as ``<slug>-<as_of>.json`` through
``write_atomic``; one already there is never rewritten. Refusals are rows of
the artifact's ``refused`` table, never exceptions that stop the run.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..config import REPO_ROOT
from ..jobs.artifacts import ArtifactSpec
from .contract import GATES_VERSION, params_hash, signal_v1, write_atomic
from .freeze import frozen_params, newest_farm
from .roster import ROSTER_PATH, Cell, load_roster, targets_gross_scaled

__all__ = ["BOOK_PATH", "book_symbols", "next_session", "run", "targets_for", "validation_for"]

BOOK_PATH = REPO_ROOT / "book.example.sexp"
PRODUCER = "signals.daily"
NY = ZoneInfo("America/New_York")
_SYMBOL = re.compile(r"\(symbol ([A-Z][A-Z0-9.]*)\)")
_STATUS = {"PASS": "pass", "FAIL": "fail"}
_PERIOD = {"weekly": "W", "monthly": "M", "quarterly": "Q"}   # engine.rebalance_mask's frequencies
EMITTED = ["slug", "as_of", "sequence", "gross_scaled", "n_targets", "status", "note", "last_rebalance", "path"]
REFUSED = ["slug", "reason"]


class CellRefused(ValueError):
    """One cell cannot be emitted this session; the message is the artifact's reason."""


def _today() -> date:
    """The run's New York date: the month whose freeze applies."""
    return datetime.now(NY).date()


def _universe(name: str) -> list[str]:
    from ..products.farm import _universe as farm_universe

    return farm_universe(name)


def book_symbols(path: Path = BOOK_PATH) -> frozenset[str]:
    """Every ``(symbol X)`` in the book's ``(positions …)`` block, comments stripped."""
    text = "\n".join(ln.split(";", 1)[0] for ln in Path(path).read_text().splitlines())
    start = text.index("(positions")
    end = text.find("(limits", start)
    return frozenset(_SYMBOL.findall(text[start : end if end > 0 else len(text)]))


# --------------------------------------------------------------- the calendar
@lru_cache(maxsize=1)
def _nyse_calendar() -> Any:
    """NYSE's full-day holidays by rule, from pandas' own holiday rules (no new
    dependency; the package has no exchange calendar -- warehouse/freshness.py and
    deck/clock.py both count weekdays and say they cannot know holidays). Unscheduled
    closures (a national day of mourning, a storm) are not known."""
    from pandas.tseries.holiday import (
        AbstractHolidayCalendar,
        GoodFriday,
        Holiday,
        USLaborDay,
        USMartinLutherKingJr,
        USMemorialDay,
        USPresidentsDay,
        USThanksgivingDay,
        nearest_workday,
        sunday_to_monday,
    )

    class NYSE(AbstractHolidayCalendar):
        rules = [
            Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),  # Saturday: no Friday close
            USMartinLutherKingJr, USPresidentsDay, GoodFriday, USMemorialDay,
            Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
            Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
            USLaborDay, USThanksgivingDay,
            Holiday("Christmas", month=12, day=25, observance=nearest_workday),
        ]

    return NYSE()


def _holidays(year: int) -> frozenset[date]:
    days = _nyse_calendar().holidays(start=f"{year}-01-01", end=f"{year}-12-31")
    return frozenset(d.date() for d in days)


def next_session(d: date) -> date:
    """The next weekday after ``d`` that is not an NYSE holiday."""
    n = d + timedelta(days=1)
    while n.weekday() >= 5 or n in _holidays(n.year):
        n += timedelta(days=1)
    return n


def _period_end(as_of: date, cadence: str) -> bool:
    """``rebalance_mask``'s rule: a period end is a session whose next session is in a new period."""
    freq = _PERIOD[cadence]
    return pd.Timestamp(as_of).to_period(freq) != pd.Timestamp(next_session(as_of)).to_period(freq)


# ------------------------------------------------------------------ the weights
def _weights(cell: Cell, params: dict[str, Any], prices: pd.DataFrame, rf: pd.Series | None,
             market: pd.Series | None) -> tuple[dict[str, float] | None, date, dict[str, Any]]:
    """The strategy's raw target weights at the last close, NaN and zero dropped (None:
    an all-NaN row, no decision), that row's date, and the validated params that ran."""
    from ..backtest.engine import EngineConfig, LookAheadError, StrategyContext, audit_causality
    from ..backtest.strategies import get_strategy, validate_params, warmup_sessions

    if prices.empty:
        raise CellRefused("no common sessions for the cell's symbols")
    spec = get_strategy(str(cell.strategy))
    try:
        ran = validate_params(spec, params, list(prices.columns))
    except ValueError as e:
        raise CellRefused(f"parameters: {e}") from e
    warm = warmup_sessions(spec, ran)
    if len(prices) < warm:
        raise CellRefused(f"{len(prices)} sessions of history < the {warm}-session warm-up")
    ctx = StrategyContext(prices=prices, rf=rf, market=market)
    full = spec.fn(ctx, **ran)
    try:
        audit_causality(spec.fn, ctx, ran, full, EngineConfig().audit_points)
    except LookAheadError as e:
        raise CellRefused(f"look-ahead audit: {e}") from e
    last = full.reindex(index=prices.index, columns=prices.columns).iloc[-1].astype(float)
    as_of = prices.index[-1].date()
    if not np.isfinite(last.to_numpy()).any():
        return None, as_of, ran
    w = {str(s): float(v) for s, v in last.items() if np.isfinite(v) and float(v) != 0.0}
    return w, as_of, ran


def targets_for(cell: Cell, params: dict[str, Any], prices: pd.DataFrame, rf: pd.Series | None,
                market: pd.Series | None = None) -> tuple[dict[str, float], date]:
    """``fn(StrategyContext, **params)``'s last row (NaN and 0 dropped), gross-scaled to
    at most 1x, and that row's date. An all-NaN row (no decision) is ``{}`` here; the
    job holds instead of emitting it."""
    w, as_of, _ = _weights(cell, params, prices, rf, market)
    return targets_gross_scaled(w or {})[0], as_of


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _selected_hash(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return params_hash(json.loads(raw))
    except ValueError:
        return None


def validation_for(cell: Cell, farm_cells: pd.DataFrame | None, *, manifest: str | None = None,
                   params: dict[str, Any] | None = None) -> dict:
    """The cell's newest farm verdict as a validation block: ``PASS`` -> pass, ``FAIL``
    -> fail, anything else or no row -> unvalidated; ``dsr``/``psr``/``pbo`` from the
    row when present, else null; ``manifest`` the farm artifact's id when it has a row.
    With ``params`` (the frozen parameters that ran), the verdict is used only when the
    newest row selected those same parameters (same ``params_hash``): a farm that
    re-selected mid-month never lends its verdict to parameters it did not test."""
    from ..models.farm import cell_id

    unvalidated = {"status": "unvalidated", "gates_version": GATES_VERSION, "dsr": None, "psr": None,
                   "pbo": None, "manifest": None}
    row = None
    if farm_cells is not None and len(farm_cells) and "cell_id" in farm_cells.columns:
        hit = farm_cells[farm_cells["cell_id"] == cell_id(str(cell.strategy), str(cell.universe))]
        row = None if hit.empty else hit.iloc[-1].to_dict()
    if row is None:
        return unvalidated
    if params is not None:
        frozen, newest = params_hash(params), _selected_hash(row.get("selected_params"))
        if frozen != newest:
            return {**unvalidated, "manifest": f"{manifest}: farm re-selected mid-month "
                                               f"(frozen {frozen}, newest {newest or 'none'})"}
    verdict = row.get("verdict")
    return {"status": _STATUS.get(verdict if isinstance(verdict, str) else "", "unvalidated"),
            "gates_version": GATES_VERSION,
            "dsr": _num(row.get("dsr", row.get("dsr_farm"))), "psr": _num(row.get("psr")),
            "pbo": _num(row.get("pbo")), "manifest": manifest}


# ------------------------------------------------------------------- the inputs
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


def _last_doc(out_dir: Path, slug: str) -> dict[str, Any] | None:
    """The slug's newest document in the signals directory (names sort by as_of)."""
    paths = sorted(out_dir.glob(f"{slug}-*.json")) if out_dir.is_dir() else []
    if not paths:
        return None
    try:
        doc = json.loads(paths[-1].read_text())
        int(doc["sequence"]), str(doc["as_of"]), list(doc["targets"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CellRefused(f"the last document {paths[-1].name} is unreadable ({e}): no sequence") from e
    return doc


def _sequence(ctx: Any, as_of: date, last: dict[str, Any] | None) -> int:
    """``max(SPY sessions in bars_daily through as_of, last document's sequence + 1)``;
    refused when SPY has no bar on ``as_of`` itself."""
    from ..data.base import DataUnavailable

    try:
        count, on_day = ctx.warehouse.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE date = ?) FROM bars_daily "
            "WHERE ticker = 'SPY' AND date <= ?", [as_of, as_of]).fetchone()
    except DataUnavailable as e:
        raise CellRefused(f"no sequence: {e}") from e
    if not on_day:
        raise CellRefused(f"SPY has no bar on {as_of}")
    return max(int(count), (int(last["sequence"]) + 1) if last else 1)


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


def _hold_reason(cadence: str, as_of: date, targets: dict[str, float],
                 last: dict[str, Any] | None) -> str | None:
    """None when ``as_of`` is a decision session for this cadence, else why it holds."""
    from ..backtest.engine import EngineConfig

    if last is None or cadence == "daily":
        return None  # the first document ever is always emitted
    if cadence in _PERIOD:
        if _period_end(as_of, cadence):
            return None
        return f"not a {cadence} rebalance date (next session {next_session(as_of)} is in the same period)"
    if cadence == "signal":
        before = {t["symbol"]: float(t["weight"]) for t in last["targets"]}
        names = set(before) | set(targets)
        moved = max((abs(targets.get(s, 0.0) - before.get(s, 0.0)) for s in names), default=0.0)
        if moved > EngineConfig().signal_tolerance:
            return None
        return "signal unchanged from the last document"
    return f"{cadence}: decided once, at the first document"


def _emit(cell: Cell, params: dict[str, Any], ctx: Any, out_dir: Path, farm_id: str | None,
          farm: pd.DataFrame | None) -> dict[str, Any]:
    from ..backtest.strategies import get_strategy

    cadence = get_strategy(str(cell.strategy)).default_rebalance
    px, rf, mkt = _inputs(cell, ctx)
    raw, as_of, ran = _weights(cell, params, px, rf, mkt)
    path = out_dir / f"{cell.slug}-{as_of.isoformat()}.json"
    row: dict[str, Any] = {"slug": cell.slug, "as_of": as_of.isoformat(), "sequence": None,
                           "gross_scaled": False, "n_targets": None, "note": None, "path": path.name}
    if path.exists():
        return {**row, "status": "already_present", "note": "a document for this session is already there"}
    last = _last_doc(out_dir, cell.slug)
    row["last_rebalance"] = last["as_of"] if last else None
    if last is not None and last["as_of"] > as_of.isoformat():
        raise CellRefused(f"as_of {as_of} is before the last document's {last['as_of']}")
    if raw is None:
        return {**row, "status": "held", "note": "no decision"}
    targets, scaled = targets_gross_scaled(raw)
    hold = _hold_reason(cadence, as_of, targets, last)
    if hold is not None:
        return {**row, "status": "held", "note": hold}
    sequence = _sequence(ctx, as_of, last)
    doc = signal_v1(strategy=cell.slug, params={**ran, "gross_scaled": True} if scaled else dict(ran),
                    as_of=as_of, sequence=sequence,
                    validation=validation_for(cell, farm, manifest=farm_id, params=params), targets=targets,
                    bars=_bars(ctx, list(px.columns), px.index))
    write_atomic(doc, path)
    return {**row, "status": "written", "sequence": sequence, "gross_scaled": scaled, "n_targets": len(targets),
            "last_rebalance": as_of.isoformat(), "note": f"{cadence} decision"}


def _spec(emitted: list[dict], refused: list[dict], notes: list[str], *,
          verdict: str | None = None, detail: str | None = None,
          provenance: list[dict] | None = None) -> ArtifactSpec:
    em = pd.DataFrame(emitted, columns=EMITTED)
    em["sequence"] = em["sequence"].astype("Int64")
    em["n_targets"] = em["n_targets"].astype("Int64")
    em["gross_scaled"] = em["gross_scaled"].astype(bool)
    return ArtifactSpec(tables={"emitted": em, "refused": pd.DataFrame(refused, columns=REFUSED)},
                        data_asof=str(em["as_of"].max()) if len(em) else None,
                        provenance=provenance or [], notes=notes, verdict=verdict, verdict_detail=detail)


def _roster_refusal(c: Cell, book: frozenset[str]) -> str | None:
    outside = [s for s in c.symbols if s not in book]
    if outside:
        return f"symbol {outside[0]} is not in the book"
    if not c.strategy or not c.universe or not c.symbols:
        return "the roster row names no strategy, universe or symbols"
    try:
        members = _universe(c.universe)
    except (KeyError, ValueError) as e:
        return f"universe {c.universe!r} is not in universes.json ({e})"
    if list(c.symbols) != list(members):
        return "roster symbols differ from the farm's universe"
    return None


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
        why = _roster_refusal(c, book)
        if why is None:
            eligible.append(c)
        else:
            refused.append({"slug": c.slug, "reason": why})
    frozen, unfrozen = frozen_params(ctx, month=_today(), cells=eligible)
    refused.extend(unfrozen)
    farm_id, farm = newest_farm()
    emitted: list[dict] = []
    todo = [c for c in eligible if c.slug in frozen]
    for i, c in enumerate(todo):
        ctx.check_cancelled()
        ctx.progress(i / max(1, len(todo)), c.slug)
        try:
            emitted.append(_emit(c, frozen[c.slug], ctx, Path(out_dir), farm_id, farm))
        except CellRefused as e:
            refused.append({"slug": c.slug, "reason": str(e)})
        except Exception as e:  # noqa: BLE001 - one cell's failure is its refusal, never the run's (h)
            refused.append({"slug": c.slug, "reason": f"{type(e).__name__}: {e}"[:500]})
    n = {s: sum(r["status"] == s for r in emitted) for s in ("written", "held", "already_present")}
    return _spec(emitted, refused,
                 [f"{n['written']} written, {n['held']} held, {n['already_present']} already present, "
                  f"{len(refused)} refused; parameters frozen for the month (INC-01); documents only on each "
                  "strategy's own decision sessions; validation from the newest farm verdict when it tested "
                  "the frozen parameters"],
                 provenance=[{"source": "farm.sweep", "artifact": farm_id},
                             {"source": "roster", "path": str(ROSTER_PATH)},
                             {"source": "calendar", "note": "NYSE full-day holidays by rule (pandas); "
                                                           "unscheduled closures not known"}])
