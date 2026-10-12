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
  always emitted. A calendar cadence whose last period end passed with no
  document (a worker down, an ingest failed, a refusal that evening) is caught
  up on the next run: today's targets, emitted ``late``, naming the missed
  period end. Every other session is a ``held`` row, naming the last
  rebalance -- unless the DESK did not size the last document (the stage
  review's B-I3): when the live engine answers (``OHCAMEL_QUANT_LIVE_ENGINE_URL``,
  one GET of ``/api/research`` with ``desk.report``'s 10 s timeout), the last
  document counts as done only when the desk's latest judgement for the slug is
  of that sequence, accepted or accepted_incubation, with a rebalance sentence
  that sized it (not one beginning "refused", "not sized", "pending", "no
  targets" or "the rebalance raised"). Otherwise the cell is due again: today's
  targets, emitted ``retry``, the note naming the desk's verdict. When the
  engine does not answer, the hold is keyed on the last written document, as
  before, and the run's notes say so;
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
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..config import REPO_ROOT
from ..jobs.artifacts import ArtifactSpec
from .calendar import holidays, next_session, previous_session
from .contract import GATES_VERSION, params_hash, signal_v1, write_atomic
from .freeze import FREEZE_KIND, freeze_id, frozen_params, newest_farm
from .roster import ROSTER_PATH, Cell, load_roster, targets_gross_scaled

__all__ = ["BOOK_PATH", "book_symbols", "next_session", "run", "targets_for", "validation_for"]

BOOK_PATH = REPO_ROOT / "book.example.sexp"
PRODUCER = "signals.daily"
NY = ZoneInfo("America/New_York")
_SYMBOL = re.compile(r"\(symbol ([A-Z][A-Z0-9.]*)\)")
_STATUS = {"PASS": "pass", "FAIL": "fail"}
_PERIOD = {"weekly": "W", "monthly": "M", "quarterly": "Q"}   # engine.rebalance_mask's frequencies
# A rebalance sentence that did not size the strategy (desk/intake.ml's outcomes).
UNSIZED = ("refused", "not sized", "pending", "no targets", "the rebalance raised")
# INC-01, *Gross scaling* / *Execution* (B-I2): what a farm verdict was earned under, noted on every run.
EXECUTION_NOTES = [
    "a farm verdict was earned at the strategy's default_max_leverage (2-3x gross for tsmom, risk_parity and "
    "low_beta), while the desk trades these de-levered 1x targets: a cell's verdict is not evidence at the "
    "leverage traded",
    "the backtest engine fills at the close of d+1 (execution_lag=1); the desk fills at the open of d+1 by "
    "market-on-open: the farm's fill price is not the desk's",
]
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
# NYSE's sessions (weekdays less the rule holidays) live in .calendar, shared
# with signals.overlay's bars-freshness refusal.
_holidays = holidays
_previous_session = previous_session


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
        doc["sequence"] = int(doc["sequence"])
        doc["as_of"] = date.fromisoformat(str(doc["as_of"])).isoformat()
        if not isinstance(doc["targets"], list):
            raise TypeError("targets is not a list")
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


def _last_period_end(as_of: date, cadence: str) -> date:
    """The most recent period end strictly before ``as_of`` (at most a quarter's sessions back)."""
    d = _previous_session(as_of)
    while not _period_end(d, cadence):
        d = _previous_session(d)
    return d


def desk_judgements(ctx: Any, notes: list[str]) -> dict[str, dict[str, Any]] | None:
    """``{slug: latest judgement}`` from the live engine's ``/api/research`` (the
    ``latest`` object desk/intake.ml's ``research_json`` serves per strategy), or None
    -- with the note -- when no engine answers: the URL unset, no answer within
    :data:`report.TIMEOUT_S`, an error status, or a body that is not the route's."""
    import httpx

    from .report import TIMEOUT_S

    unreachable = "desk unreachable: keyed on the last written document"
    base = (getattr(ctx.market.settings, "live_engine_url", None) or "").strip().rstrip("/")
    if not base:
        notes.append(f"{unreachable} (OHCAMEL_QUANT_LIVE_ENGINE_URL is unset)")
        return None
    try:
        r = httpx.get(base + "/api/research", timeout=TIMEOUT_S)
        body = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as e:
        notes.append(f"{unreachable} ({type(e).__name__}: {e})")
        return None
    if not isinstance(body, dict) or not isinstance(body.get("strategies"), list):
        notes.append(f"{unreachable} (GET /api/research answered {r.status_code}, not the route's body)")
        return None
    return {str(s.get("name")): s.get("latest") for s in body["strategies"] if isinstance(s, dict)}


def desk_verdict(latest: Any, last: dict[str, Any]) -> str | None:
    """None when the desk sized ``last`` (its latest judgement is of ``last``'s sequence,
    accepted or accepted_incubation, and its rebalance sentence sized it); else what the
    desk did, in words for the retry's note."""
    if not isinstance(latest, dict):
        return "the desk holds no judgement of it"
    seq = latest.get("sequence")
    if seq != last["sequence"]:
        return f"the desk's latest judgement is of sequence {seq}, not the last document's {last['sequence']}"
    verdict, rule = latest.get("verdict"), latest.get("rule")
    if verdict not in ("accepted", "accepted_incubation"):
        return f"the desk judged sequence {seq} {verdict}" + (f" at {rule}" if rule else "")
    rb = latest.get("rebalance")
    if not isinstance(rb, str) or rb.startswith(UNSIZED):
        return f"the desk accepted sequence {seq} and did not size it: {str(rb)[:160]}"
    return None


def _hold_reason(cadence: str, as_of: date, targets: dict[str, float],
                 last: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """``(None, None)`` when ``as_of`` is a decision session for this cadence; ``(None, missed)``
    when a period end passed with no document since (the catch-up: emit late, naming it);
    else ``(why it holds, None)``."""
    from ..backtest.engine import EngineConfig

    if last is None or cadence == "daily":
        return None, None  # the first document ever is always emitted
    if cadence in _PERIOD:
        if _period_end(as_of, cadence):
            return None, None
        missed = _last_period_end(as_of, cadence)
        if last["as_of"] < missed.isoformat():
            return None, missed.isoformat()
        return (f"not a {cadence} rebalance date (next session {next_session(as_of)} is in the same period)",
                None)
    if cadence == "signal":
        before = {t["symbol"]: float(t["weight"]) for t in last["targets"]}
        names = set(before) | set(targets)
        moved = max((abs(targets.get(s, 0.0) - before.get(s, 0.0)) for s in names), default=0.0)
        if moved > EngineConfig().signal_tolerance:
            return None, None
        return "signal unchanged from the last document", None
    return f"{cadence}: decided once, at the first document", None


def _emit(cell: Cell, params: dict[str, Any], ctx: Any, out_dir: Path, farm_id: str | None,
          farm: pd.DataFrame | None, desk: dict[str, Any] | None = None) -> dict[str, Any]:
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
    hold, missed = _hold_reason(cadence, as_of, targets, last)
    retry = None
    if hold is not None and last is not None and desk is not None:
        retry = desk_verdict(desk.get(cell.slug), last)
    if hold is not None and retry is None:
        return {**row, "status": "held", "note": hold}
    sequence = _sequence(ctx, as_of, last)
    doc = signal_v1(strategy=cell.slug, params={**ran, "gross_scaled": True} if scaled else dict(ran),
                    as_of=as_of, sequence=sequence,
                    validation=validation_for(cell, farm, manifest=farm_id, params=params), targets=targets,
                    bars=_bars(ctx, list(px.columns), px.index))
    write_atomic(doc, path)
    done = {**row, "sequence": sequence, "gross_scaled": scaled, "n_targets": len(targets),
            "last_rebalance": as_of.isoformat()}
    if retry is not None:
        return {**done, "status": "retry", "note": f"retry: {retry} (the last document is {last['as_of']})"}
    if missed is not None:
        return {**done, "status": "late", "note": f"late: the {cadence} period end {missed} passed with no "
                                                  f"document (the last is {last['as_of']})"}
    return {**done, "status": "written", "note": f"{cadence} decision"}


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


def _todo_any(eligible: list[Cell], frozen: dict[str, dict]) -> bool:
    return any(c.slug in frozen for c in eligible)


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
    run_notes: list[str] = []
    desk = desk_judgements(ctx, run_notes) if _todo_any(eligible, frozen) else None
    emitted: list[dict] = []
    todo = [c for c in eligible if c.slug in frozen]
    for i, c in enumerate(todo):
        ctx.check_cancelled()
        ctx.progress(i / max(1, len(todo)), c.slug)
        try:
            emitted.append(_emit(c, frozen[c.slug], ctx, Path(out_dir), farm_id, farm, desk))
        except CellRefused as e:
            refused.append({"slug": c.slug, "reason": str(e)})
        except Exception as e:  # noqa: BLE001 - one cell's failure is its refusal, never the run's (h)
            refused.append({"slug": c.slug, "reason": f"{type(e).__name__}: {e}"[:500]})
    n = {s: sum(r["status"] == s for r in emitted)
         for s in ("written", "late", "retry", "held", "already_present")}
    return _spec(emitted, refused,
                 [f"{n['written']} written, {n['late']} written late, {n['retry']} written again (the desk did not "
                  f"size the last), {n['held']} held, {n['already_present']} already present, "
                  f"{len(refused)} refused; parameters frozen for the month (INC-01); documents only on each "
                  "strategy's own decision sessions; validation from the newest farm verdict when it tested "
                  "the frozen parameters", *run_notes, *EXECUTION_NOTES],
                 provenance=[{"source": "farm.sweep", "artifact": farm_id},
                             {"source": FREEZE_KIND, "artifact": freeze_id(_today())},
                             {"source": "roster", "path": str(ROSTER_PATH)},
                             {"source": "calendar", "note": "NYSE full-day holidays by rule (pandas); "
                                                           "unscheduled closures not known"}])
