"""The graph: spec §5's rings, assembled from inputs already loaded.

The Machine draws OhCamel as rings that narrow to one book: feeds, the warehouse's series, the
clock's jobs, the products they publish, the results inside those, the desk's strategies, the
book's limits and kill switch, and the book. Every node is a real entity and every edge has
exactly one rule in ``RULES``. "An edge with no rule is a bug" (spec §5), and a test holds every
edge to it. Nothing here is invented. A source that is absent leaves its node present with
``status: "missing"`` and a ``why``, and it never fabricates a value.

``build_graph`` is pure. Task 6's loader does the I/O and hands it an ``Inputs``. It sends
semantics, not coordinates: each node's ``ring`` and its ``at`` (section and index, an hour, or
the node it sits beside). The client lays the rings out (spec §6.3). The sections follow the
approved prototype's order and ids: the world, the clock, the feeds, the products, the results,
then the desk.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from ..signals.roster import Cell, Roster
from .clock import PRETTY, JobClock
from .world import World

# kind -> (code, name): every artifact kind the clock publishes (the prototype's list)
PROD: dict[str, tuple[str, str]] = {
    "risk.mc_atlas": ("P1", "Risk atlas"), "vol.forecast_league": ("P2", "Vol forecast league"),
    "cov.league": ("P3", "Covariance league"), "farm.sweep": ("P4", "Strategy farm"),
    "models.xs_lgbm": ("P5", "Cross-section model"), "regime.hmm": ("P6", "Regime model"),
    "vol.surface_history": ("P7", "Vol surface history"), "kronos.shadow": ("K", "Kronos shadow"),
    "ops.data_integrity": ("DI", "Data integrity"), "risk.mc_intraday": ("P1i", "Intraday risk"),
    "factors.screen": ("FS", "Factor screen"), "signals.daily": ("SD", "Daily signals"),
    "signals.overlay": ("SO", "Exposure overlay"), "desk.report": ("DR", "Desk report"),
}

# (key, label, who, the jobs it feeds, warehouse dataset, the track its first job writes). Vendor
# names from data/prices.py's vendor chain and each ingest module.
FEEDS: tuple[tuple[str, str, str, tuple[str, ...], str, str], ...] = (
    ("prices", "Prices", "Yahoo, Alpaca, Stooq", ("ingest.bars_daily",), "bars_daily", "daily"),
    ("minute", "Minute bars", "Alpaca (IEX)", ("ingest.bars_minute",), "bars_minute", "minute"),
    ("cboe", "Option chains", "Cboe, 15-minute delay", ("ingest.option_snapshots",), "option_snapshots", "options"),
    ("fred", "Rates & macro", "FRED", ("ingest.fred_warehouse", "ingest.fred"), "fred", "macro"),
    ("french", "Factors", "Kenneth French", ("ingest.factors",), "factors", "factors"),
    ("sec", "Filings", "SEC EDGAR", ("ingest.sec_facts",), "sec_facts", "filings"),
    ("sec13f", "13F holdings", "SEC EDGAR", ("ingest.holdings_13f",), "holdings_13f", "13f"),
)

# spec §5: edge kind -> the (from-kind, to-kind) pairs its one rule allows
RULES: dict[str, frozenset[tuple[str, str]]] = {
    "pull": frozenset({("feed", "job")}),
    "write": frozenset({("job", "series")}),
    "publish": frozenset({("job", "product")}),
    "result": frozenset({("product", "result")}),
    "read": frozenset({("series", "job"), ("product", "job"), ("series", "limit"), ("book", "job")}),
    "roster": frozenset({("result", "strategy")}),
    "signal": frozenset({("product", "strategy")}),
    "trade": frozenset({("strategy", "book")}),
    "scale": frozenset({("product", "book")}),
    "limit": frozenset({("limit", "book")}),
    "trip": frozenset({("limit", "kill")}),
    "halt": frozenset({("kill", "book")}),
}

DAILY, RESEARCH = "signals.daily", "research-service"
RUN_TIMES_ONLY = frozenset({"desk.report"})   # owner, 2026-10-29: its node shows only its run times (spec §7 Q1)
KILL_STATES = frozenset({"disabled", "armed", "halted"})
HALTS = frozenset({"armed", "halted"})        # a disabled switch halts nothing
ATLAS_ALPHA = 0.99
ATLAS_METHODS = {"fhs": "FHS", "copula_t": "t-copula"}
LEADERBOARD = ("cell_id", "verdict", "detail", "dsr_farm", "psr", "oos_sharpe_ann", "pbo", "n_combos")
# a product node's manifest fields: wire name -> manifest key
MANIFEST = {"verdict": "verdict", "detail": "verdict_detail", "code": "code_sha", "asof": "data_asof",
            "finished": "finished_at", "cpu": "cpu_seconds", "engine": "engine", "notes": "notes", "art": "id"}


@dataclass(frozen=True)
class Product:
    """The latest artifact of one kind: its manifest (None when there is none), whether it is past its
    max age, the tables the graph reads, every listed table's row count, and why anything is missing."""

    kind: str
    manifest: dict | None
    stale: bool = False
    tables: Mapping[str, pd.DataFrame] = field(default_factory=dict)
    rows: Mapping[str, int] = field(default_factory=dict)
    why: str | None = None


@dataclass(frozen=True)
class Inputs:
    """Everything the graph is built from. ``freshness`` is the warehouse's ``datasets`` (None when the
    warehouse could not be read); ``order`` is the equities' cluster order; ``why`` names what is missing."""

    world: World
    clock: Mapping[str, JobClock]
    roster: Roster
    products: Mapping[str, Product]
    freshness: Mapping[str, dict] | None
    readouts: Mapping[str, dict]
    order: Sequence[str]
    why: Mapping[str, str]


def _provenance(manifest: Any) -> list[dict]:
    """A manifest's provenance entries that are dicts; none when the manifest or its list is malformed,
    so one bad artifact degrades only its own node, never the graph."""
    prov = manifest.get("provenance") if isinstance(manifest, dict) else None
    return [e for e in prov if isinstance(e, dict)] if isinstance(prov, list) else []


def provenance_symbols(manifest: dict | None) -> list[tuple[str, str]]:
    """``(dataset, SYMBOL)`` for every symbol a manifest's provenance names, in ``detail.symbol``,
    ``detail.tickers``, ``symbol`` or ``tickers``, once each, in order. ``dataset`` is the entry's
    ``detail.dataset``, else ``bars_daily``. A count where a list belongs names nothing."""
    out: list[tuple[str, str]] = []
    for e in _provenance(manifest):
        detail = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        dataset = detail.get("dataset") if isinstance(detail.get("dataset"), str) else "bars_daily"
        for src in (detail, e):
            names = [src.get("symbol")] + (src["tickers"] if isinstance(src.get("tickers"), list) else [])
            out += [(dataset, s.upper()) for s in names if isinstance(s, str) and s]
    return list(dict.fromkeys(out))


def _plain(v: Any) -> Any:
    """JSON-safe: numpy scalars as Python, NaN, inf and pandas' missing values as None, tuples as lists."""
    if isinstance(v, Mapping):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, list | tuple | np.ndarray):
        return [_plain(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str | bool | int):
        return v
    if isinstance(v, datetime | date):
        return v.isoformat()
    return str(v)


class _Graph:
    """Nodes in insertion order and edges as index triples. A repeated id is ignored. An edge whose
    ends are absent or equal, or that is already there, is dropped."""

    def __init__(self) -> None:
        self.nodes: list[dict] = []
        self.index: dict[str, int] = {}
        self.edges: list[list] = []
        self._seen: set[tuple[int, int, str]] = set()

    def node(self, id_: str, k: str, label: str, name: str, ring: str, at: dict, m: dict, *,
             status: str | None = None, why: str | None = None) -> None:
        if id_ in self.index:
            return
        n = {"id": id_, "k": k, "l": label, "n": name, "ring": ring, "at": at, "m": _plain(m)}
        if status:
            n["status"] = status
        if why:
            n["why"] = why
        self.index[id_] = len(self.nodes)
        self.nodes.append(n)

    def edge(self, a: str, b: str, kind: str) -> None:
        ia, ib = self.index.get(a), self.index.get(b)
        if ia is None or ib is None or ia == ib or (ia, ib, kind) in self._seen:
            return
        self._seen.add((ia, ib, kind))
        self.edges.append([ia, ib, kind])

    def has(self, id_: str) -> bool:
        return id_ in self.index


def build_graph(inp: Inputs) -> dict:
    """``{"nodes": [{id, k, l, n, ring, at, m, status?, why?}], "edges": [[a, b, kind]]}``, spec §5."""
    g = _Graph()
    tracks = _world(g, inp)
    _clock(g, inp)
    _feeds(g, inp, tracks)
    _products(g, inp, tracks)
    _results(g, inp)
    _desk(g, inp)
    return {"nodes": g.nodes, "edges": g.edges}


# ------------------------------------------------------------------ the world: tickers, macro, factors, filers
def _equities(inp: Inputs) -> list[str]:
    """The equities in cluster order, then any the order leaves out, alphabetically."""
    eq = inp.world.equities
    first = [t for t in dict.fromkeys(inp.order) if t in eq]
    placed = set(first)
    return first + sorted(t for t in eq if t not in placed)


def _world(g: _Graph, inp: Inputs) -> dict[str, list[str]]:
    """Every series the warehouse holds. The daily ring runs in sections (ETFs by universe, equities by
    cluster, FRED, factors, 13F filers); the filings, minute and options tracks sit beside their
    ticker's daily series. Returns each track's series ids, which the feeds write."""
    w = inp.world
    tracks: dict[str, list[str]] = {t: [] for t in ("daily", "filings", "minute", "options", "macro", "factors", "13f")}

    def series(id_: str, label: str, name: str, ring: str, at: dict, track: str, **m: Any) -> None:
        g.node(id_, "series", label, name, ring, at, {"track": track, **m})
        tracks[track].append(id_)

    for i, (t, name, unis) in enumerate(w.etfs):
        series(f"d:{t}", t, name, "daily", {"sec": "etf", "i": i}, "daily", unis=list(unis), **inp.readouts.get(t, {}))
    for i, t in enumerate(_equities(inp)):
        name = w.equities[t]
        series(f"d:{t}", t, name, "daily", {"sec": "eq", "i": i}, "daily", **inp.readouts.get(t, {}))
        series(f"s:{t}", t, f"{name}: SEC filings", "filings", {"of": f"d:{t}"}, "filings")
    for i, (sid, name) in enumerate(w.fred):
        series(f"f:{sid}", sid, name, "daily", {"sec": "fred", "i": i}, "macro")
    for i, (key, name) in enumerate(w.factors):
        series(f"ff:{key}", key, name, "daily", {"sec": "ff", "i": i}, "factors")
    for i, f in enumerate(w.filers):
        label = f["manager"].split()[-1].upper() if f["manager"].strip() else f["cik"]
        series(f"13f:{f['cik']}", label, f"{f['name']} (13F)", "daily", {"sec": "13f", "i": i}, "13f",
               manager=f["manager"])
    for t in w.minute:
        if g.has(f"d:{t}"):
            series(f"m:{t}", t, f"{t}: one-minute bars", "minute", {"of": f"d:{t}"}, "minute")
    for t in w.options:
        if g.has(f"d:{t}"):
            series(f"o:{t}", t, f"{t}: option chain", "options", {"of": f"d:{t}"}, "options")
    return tracks


# ------------------------------------------------------------------ the clock: every scheduled job at its New York hour
def _clock(g: _Graph, inp: Inputs) -> None:
    for kind, c in inp.clock.items():
        name = PRETTY.get(kind, kind)
        g.node(f"j:{kind}", "job", name, name, "clock", {"h": round(c.hour, 4)},
               {"kind": kind, "fires": [round(h, 4) for h in c.fires], "cron": list(c.cron), "heavy": c.heavy,
                "mem": c.mem, "when": list(c.when)})


# ------------------------------------------------------------------ the feeds, outside everything
def _feeds(g: _Graph, inp: Inputs, tracks: dict[str, list[str]]) -> None:
    """Each feed pulls its ingest jobs (the wiring is config, so it stands without freshness), and its
    first job writes every series on its track. Its figures come from warehouse freshness alone."""
    for key, label, who, jobs, dataset, track in FEEDS:
        fr = None if inp.freshness is None else inp.freshness.get(dataset)
        m: dict[str, Any] = {"who": who, "dataset": dataset}
        why = None
        if inp.freshness is None:
            why = inp.why.get("warehouse") or "the warehouse's freshness was not read"
        elif fr is None:
            why = f"the warehouse's freshness lists no {dataset} dataset"
        else:
            m |= {"keys": fr.get("keys"), "asof": fr.get("data_asof"), "ok": fr.get("last_ok_at"),
                  "behind": fr.get("keys_behind")}
        g.node(f"feed:{key}", "feed", label, f"{label} from {who}", "feeds", {}, m,
               status="missing" if why else None, why=why)
        for job in jobs:
            g.edge(f"feed:{key}", f"j:{job}", "pull")
        for s in tracks[track]:
            g.edge(f"j:{jobs[0]}", s, "write")


# ------------------------------------------------------------------ products: what each job publishes, and what it read
def _present(p: Product | None) -> bool:
    return p is not None and isinstance(p.manifest, dict)   # a manifest that is not a mapping is no manifest


def _table(inp: Inputs, kind: str, name: str, needs: Sequence[str]) -> pd.DataFrame | None:
    """An artifact's table, when the artifact, the table and the columns ``needs`` names are all there."""
    p = inp.products.get(kind)
    df = p.tables.get(name) if _present(p) else None
    return df if df is not None and set(needs) <= set(df.columns) else None


def _product_m(kind: str, p: Product | None) -> dict[str, Any]:
    """A product node's ``m``: its manifest fields and stale flag; ``desk.report`` only its run times."""
    if not _present(p):
        return {"kind": kind}
    mf = p.manifest
    if kind in RUN_TIMES_ONLY:
        return {"kind": kind, "finished": mf.get("finished_at"), "stale": p.stale}
    return {"kind": kind, **{k: mf.get(src) for k, src in MANIFEST.items()}, "stale": p.stale}


def _series_reads(kind: str, p: Product | None, inp: Inputs, tracks: dict[str, list[str]]) -> list[str]:
    """The series a job reads: its artifact's provenance symbols (``bars_minute`` names the minute
    track), except for spec §5's named cases. Those are config, so they stand without the artifact."""
    if kind == "risk.mc_atlas":
        members = _table(inp, kind, "members", ("ticker",))
        return [] if members is None else [f"d:{str(t).upper()}" for t in members["ticker"].dropna().unique()]
    if kind == "farm.sweep":
        return [f"d:{s}" for s in sorted({s for c in inp.roster.cells if c.producer == DAILY for s in c.symbols})]
    if kind == "regime.hmm":
        return ["d:SPY", "f:VIXCLS"]
    if kind == "vol.surface_history":
        return tracks["options"]
    if kind == "ops.data_integrity":
        return tracks["daily"]
    if kind == "factors.screen":
        return ["ff:FF5", "ff:MOM"]
    ids = [f"{'m' if ds == 'bars_minute' else 'd'}:{t}" for ds, t in provenance_symbols(p.manifest if p else None)]
    if kind == "vol.forecast_league":   # its target is minute RV where the minute track holds it
        ids += tracks["minute"]
    return ids


def _products(g: _Graph, inp: Inputs, tracks: dict[str, list[str]]) -> None:
    for kind, (code, name) in PROD.items():
        p = inp.products.get(kind)
        at = {"of": f"j:{kind}"} if g.has(f"j:{kind}") else {}
        why = p.why if p else None   # a present artifact may still miss a table; it says which
        if not _present(p):
            why = why or inp.why.get(kind) or f"no {kind} artifact has been published"
        g.node(f"p:{kind}", "product", code, name, "products", at, _product_m(kind, p),
               status=None if _present(p) else "missing", why=why)
        g.edge(f"j:{kind}", f"p:{kind}", "publish")
    for kind in PROD:
        p = inp.products.get(kind)
        for s in _series_reads(kind, p, inp, tracks):
            g.edge(s, f"j:{kind}", "read")
        for e in _provenance(p.manifest) if _present(p) else []:
            src = e.get("source")
            if isinstance(src, str) and src in PROD:   # "reported only" and the like are not reads
                g.edge(f"p:{src}", f"j:{kind}", "read")


# ------------------------------------------------------------------ results inside the products
def _records(df: pd.DataFrame, cols: Mapping[str, str]) -> list[dict]:
    """Rows as dicts of the columns present, renamed ``column -> field``."""
    have = {c: f for c, f in cols.items() if c in df.columns}
    return [{have[c]: v for c, v in r.items()} for r in df[list(have)].to_dict("records")]


Result = tuple[str, str, str, dict]   # id, label, name, m


def _farm_cells(inp: Inputs) -> list[Result]:
    """One node per farm cell, with its leaderboard row when it has one (a skipped cell has none)."""
    cells = _table(inp, "farm.sweep", "cells", ("strategy", "universe"))
    if cells is None:
        return []
    board = _table(inp, "farm.sweep", "leaderboard", ("cell_id",))
    lead: dict[Any, dict] = {}
    for row in [] if board is None else _records(board, {c: c for c in LEADERBOARD}):
        lead[row.pop("cell_id")] = row
    out = []
    for row in _records(cells, {"cell_id": "cell_id", "strategy": "s", "universe": "u", "status": "status",
                                "reason": "reason", "n_combos": "combos"}):
        s, u, cid = row.pop("s"), row.pop("u"), row.pop("cell_id", None)
        if isinstance(s, str) and isinstance(u, str):
            name = f"{s.replace('_', ' ')} on {u.replace('_', ' ')}"
            out.append((f"c:{s}@{u}", f"{s}@{u}", name, {**row, **lead.get(cid or f"{s}@{u}", {})}))
    return out


def _vol_models(inp: Inputs) -> list[Result]:
    summary = _table(inp, "vol.forecast_league", "summary", ("model",))
    out = []
    for row in [] if summary is None else _records(summary, {c: c for c in ("model", "mcs_rate", "mean_rank", "names")}):
        model = str(row.pop("model"))
        out.append((f"lm:{model}", model.upper(), f"{model.upper()} volatility model", row))
    return out


def _cov_estimators(inp: Inputs) -> list[Result]:
    """One node per estimator, in the league's own order, with each universe's realised vol and rank."""
    league = _table(inp, "cov.league", "league", ("estimator", "universe"))
    est: dict[str, dict[str, dict]] = {}
    for row in [] if league is None else _records(league, {"estimator": "e", "universe": "u",
                                                           "realized_vol": "vol", "rank": "rank"}):
        est.setdefault(str(row.pop("e")), {})[str(row.pop("u"))] = row
    return [(f"ce:{e}", e.replace("_", " "), f"{e.replace('_', ' ')} covariance estimator", v) for e, v in est.items()]


def _atlas_measures(inp: Inputs) -> list[Result]:
    """The atlas's book measures at alpha 0.99, one per method and horizon."""
    atlas = _table(inp, "risk.mc_atlas", "summary", ("method", "horizon", "alpha"))
    cols = {"method": "method", "horizon": "horizon", "alpha": "alpha", "var": "var", "es": "es",
            "var_usd": "var_usd", "es_usd": "es_usd", "n_paths": "paths"}
    out = []
    for row in [] if atlas is None else _records(atlas, cols):
        alpha, method, h = _plain(row.pop("alpha")), str(row.pop("method")), _plain(row.pop("horizon"))
        if not (isinstance(alpha, float | int) and abs(alpha - ATLAS_ALPHA) < 1e-9 and isinstance(h, float | int)):
            continue
        h = int(h) if float(h).is_integer() else h
        label = f"{ATLAS_METHODS.get(method, method)} {h}-day"
        out.append((f"am:{method}:{h}", label, f"{label} VaR and ES at 99%", row))
    return out


def _results(g: _Graph, inp: Inputs) -> None:
    """The tables named in spec §5's Results row, each row a node beside its product (``at.i`` of ``n``)."""
    for kind, items in (("farm.sweep", _farm_cells(inp)), ("vol.forecast_league", _vol_models(inp)),
                        ("cov.league", _cov_estimators(inp)), ("risk.mc_atlas", _atlas_measures(inp))):
        for i, (id_, label, name, m) in enumerate(items):
            g.node(id_, "result", label, name, "results", {"of": f"p:{kind}", "i": i, "n": len(items)}, m)
            g.edge(f"p:{kind}", id_, "result")


# ------------------------------------------------------------------ the desk: the roster, the overlay, the book, its limits
def _json_list(v: Any) -> list | None:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, list) else None


def _book_summary(p: Product | None) -> tuple[dict | None, str | None]:
    """``desk.report``'s ``book_summary``, parsed, when it is usable: present, fresh, a row, and limits
    read. Otherwise None and which case it is, in plain words (spec §7 Q1: the book then shows the
    roster and the overlay only, and the reason)."""
    if not _present(p):
        return None, "desk.report has published no artifact, so the live book's limits and kill switch are not shown"
    if p.stale:
        return None, (f"the desk.report artifact of {p.manifest.get('finished_at')} is stale, so its book_summary "
                      "is not shown as the live book")
    t = p.tables.get("book_summary")
    if t is None:
        return None, "the desk.report artifact has no book_summary table"
    if t.empty:
        return None, "book_summary has no row: the engine was unreachable when desk.report last ran"
    row = {k: _plain(v) for k, v in t.iloc[0].items()}
    limits = _json_list(row.get("limits"))
    if limits is None:
        return None, "book_summary did not read the book's limits"
    trips = _json_list(row.get("trips_on"))
    pos, kill = row.get("positions"), row.get("kill_switch")
    return {
        "as_of": row.get("as_of"),
        "positions": int(pos) if isinstance(pos, int | float) and not isinstance(pos, bool) else None,
        "kill": kill if kill in KILL_STATES else None,
        "trips_on": [x for x in trips if isinstance(x, str)] if trips is not None else None,
        # name, kind and scope only, copied field by field: a row copied whole could carry a threshold
        "limits": [{k: x[k] for k in ("name", "kind", "scope")} for x in limits
                   if isinstance(x, dict) and all(isinstance(x.get(k), str) for k in ("name", "kind", "scope"))],
    }, None


def _signal_source(c: Cell) -> str:
    return {DAILY: "p:signals.daily", RESEARCH: "p:research"}.get(c.producer, f"p:{c.producer}")


def _desk(g: _Graph, inp: Inputs) -> None:
    r = inp.roster
    research = [c for c in r.cells if c.producer == RESEARCH]
    if research:
        g.node("p:research", "product", "RS", "Research service", "products", {"beside": "p:signals.daily"},
               {"producer": RESEARCH, "strategies": len(research)})
    summary, why = _book_summary(inp.products.get("desk.report"))
    book = {"strategies": len(r.cells), "capital": round(sum(c.capital_fraction for c in r.cells), 6),
            "incubation_cap": r.incubation_cap, "canary_cap": r.canary_cap, "vol_target": r.vol_target}
    if summary is None:
        book["summary_why"] = why
    else:
        book |= {"positions": summary["positions"], "kill": summary["kill"], "limits": len(summary["limits"]),
                 "summary_asof": summary["as_of"]}
    g.node("book", "book", "THE BOOK", "The paper book at Alpaca", "book", {}, book)

    for i, c in enumerate(r.cells):
        g.node(f"st:{c.slug}", "strategy", c.slug.replace("farm_", "").replace("_", " "), c.slug, "strategies",
               {"i": i, "n": len(r.cells)}, {"producer": c.producer, "strategy": c.strategy, "universe": c.universe,
                                             "symbols": list(c.symbols), "capital": c.capital_fraction})
        if c.producer == DAILY:
            g.edge(f"c:{c.strategy}@{c.universe}", f"st:{c.slug}", "roster")
        g.edge(_signal_source(c), f"st:{c.slug}", "signal")
        g.edge(f"st:{c.slug}", "book", "trade")
    g.edge("p:signals.overlay", "book", "scale")
    g.edge("book", "j:desk.report", "read")
    if summary is not None:
        _limits(g, summary)


def _limits(g: _Graph, s: dict) -> None:
    """The book's limits and its kill switch, one ring: the limits, then the switch in the last slot.
    Only an instrument-scoped limit reads a series (its ticker's daily closes); sector and portfolio
    limits bound the book as a whole and read nothing."""
    lims, trips, state = s["limits"], s["trips_on"], s["kill"]
    slots = len(lims) + 1
    for i, lim in enumerate(lims):
        name, kind, scope = lim["name"], lim["kind"], lim["scope"]
        g.node(f"lim:{name}", "limit", name, f"{name}: {kind}, {scope}", "limits", {"i": i, "n": slots},
               {"kind": kind, "scope": scope, "trips": None if trips is None else name in trips})
        g.edge(f"lim:{name}", "book", "limit")
        if scope.startswith("instrument:"):
            g.edge(f"d:{scope.split(':', 1)[1].upper()}", f"lim:{name}", "read")
    g.node("kill", "kill", "KILL SWITCH", f"Kill switch: {state or 'not read'}", "limits",
           {"i": len(lims), "n": slots}, {"state": state, "trips_on": trips})
    for name in trips or []:
        g.edge(f"lim:{name}", "kill", "trip")
    if state in HALTS:
        g.edge("kill", "book", "halt")
