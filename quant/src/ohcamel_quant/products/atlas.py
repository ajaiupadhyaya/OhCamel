"""P1 jobs: ``risk.mc_atlas`` (nightly) and ``risk.mc_intraday`` (every 15 min in session).

Returns come from ``ctx.market.returns``, which reads the warehouse first and
says so in provenance. The last ``LOOKBACK`` common sessions are used. Members
are the warehouse's universe members (S&P 500, Nasdaq-100, site ETFs), each
measured alone; a member with too little history gets a reason, not a number.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data.base import DataUnavailable, Provenance
from ..jobs.artifacts import ArtifactSpec
from ..models import mc_risk

LOOKBACK = 1000
MIN_OBS = 500
MEMBER_ALPHAS = (0.975, 0.99)
MEMBER_METHODS = ("fhs",)  # members store FHS only, so only FHS is computed for them (no busy-work)
DEFAULTS: dict[str, Any] = {"n_paths": 1_000_000, "member_paths": 250_000, "members": True, "seed": 20_261_006}
INTRADAY: dict[str, Any] = {"n_paths": 250_000, "seed": 20_261_006}
SURVIVORSHIP = "current constituents; delisted names absent"
DEFAULT_DB: Path | None = None  # tests point the deck's lookup at a temp jobs.sqlite


def _books() -> dict[str, dict[str, Any]]:
    from ..api.routers.deck import BOOKS

    return BOOKS


def _returns(ctx: Any, tickers: list[str]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    ds = ctx.market.returns(tickers)  # full history (warehouse first), then the last LOOKBACK common sessions
    r = ds.data[tickers].dropna().tail(LOOKBACK)
    if len(r) < MIN_OBS:
        raise DataUnavailable(f"{', '.join(tickers)}: {len(r)} common sessions (< {MIN_OBS})")
    return r, ds.provenance_dicts()


def _members(ctx: Any, spec: Any) -> list[tuple[str, str]]:
    if isinstance(spec, list):
        return [(t.upper(), "requested") for t in spec]
    if not spec:
        return []
    rows = ctx.warehouse.execute("SELECT ticker, min(universe) FROM universe_members GROUP BY ticker "
                                 "ORDER BY ticker").fetchall()
    return [(str(t), str(u)) for t, u in rows]


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    p = {**DEFAULTS, **params}
    books = _books()
    summary, euler, prov, notes, last = [], [], [], [], []
    for i, (key, b) in enumerate(books.items()):
        ctx.check_cancelled()
        tickers = [h["ticker"] for h in b["holdings"]]
        w = np.array([h["weight"] for h in b["holdings"]], dtype=float)
        r, pv = _returns(ctx, tickers)
        prov += pv
        last.append(r.index[-1])
        br = mc_risk.book_risk(r, w, n_paths=int(p["n_paths"]), seed=int(p["seed"]), threads=ctx.threads)
        for row in br.rows:
            summary.append({"book": key, **row, "notional": b["notional"], "var_usd": row["var"] * b["notional"],
                            "es_usd": row["es"] * b["notional"], "nu": br.nu, "observations": len(r),
                            "start": str(r.index[0].date()), "end": str(r.index[-1].date()), "engine": br.engine})
        for a, c in br.euler.items():
            es = sum(c)
            for t, wt, ci in zip(tickers, w, c, strict=True):
                euler.append({"book": key, "method": "fhs", "horizon": 1, "alpha": a, "ticker": t, "weight": wt,
                              "es_contribution": float(ci), "pct_es": float(ci / es) if es else None})
        if not br.converged:
            notes.append(f"{key}: a GJR fit did not report convergence; its numbers are shown with this note")
        ctx.progress(0.3 * (i + 1) / len(books), f"book {key}")
    members, surv = [], None
    try:
        todo = _members(ctx, p["members"])
    except DataUnavailable as e:  # no warehouse yet: the books still publish
        todo = []
        notes.append(f"members skipped: {e}")
    for j, (t, u) in enumerate(todo):
        ctx.check_cancelled()
        if u in ("sp500", "ndx100"):
            surv = SURVIVORSHIP
        try:
            r, _ = _returns(ctx, [t])
            br = mc_risk.book_risk(r, np.array([1.0]), n_paths=int(p["member_paths"]), seed=int(p["seed"]),
                                   threads=ctx.threads, alphas=MEMBER_ALPHAS, horizons=(1, 10),
                                   methods=MEMBER_METHODS)
            members += [{"ticker": t, "universe": u, **row, "observations": len(r), "reason": None}
                        for row in br.rows]
        except (DataUnavailable, ValueError, KeyError) as e:
            members.append({"ticker": t, "universe": u, "method": "fhs", "horizon": None, "alpha": None,
                            "var": None, "es": None, "n_paths": None, "observations": None, "reason": str(e)})
        if j % 10 == 0:
            ctx.progress(0.3 + 0.7 * (j + 1) / max(len(todo), 1), f"member {t}")
    member_cols = ["ticker", "universe", "method", "horizon", "alpha", "var", "es", "n_paths", "observations",
                   "reason"]
    return ArtifactSpec(
        tables={"summary": pd.DataFrame(summary), "euler": pd.DataFrame(euler),
                "members": pd.DataFrame(members, columns=member_cols)},
        data_asof=str(min(last).date()), provenance=[*prov, Provenance.now("ohcamel-mc", engine=br.engine).to_dict()],
        notes=[*notes, f"last {LOOKBACK} common sessions; 1-day FHS exact over residual rows; "
                        f"{int(p['n_paths']):,} paths per book for 10-day FHS and both copula horizons; "
                        f"members: FHS only, {int(p['member_paths']):,} paths for 10-day"],
        survivorship=surv, engine=br.engine,
        verdict="DESCRIPTIVE ONLY", verdict_detail="RISK MEASUREMENT · NO GATE")


def run_intraday(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    from ..deck.live import live_weights

    p = {**INTRADAY, **params}
    rows, prov, notes, last = [], [], [], []
    for key, b in _books().items():
        tickers = [h["ticker"] for h in b["holdings"]]
        weights = {h["ticker"]: h["weight"] for h in b["holdings"]}
        r, pv = _returns(ctx, tickers)
        prov += pv
        last.append(r.index[-1])
        q = ctx.market.quotes(tickers)
        moves = {t: (float(q.data.loc[t, "change_pct"]) if t in q.data.index else None) for t in tickers}
        lw, day, missing = live_weights(weights, moves)
        mark = max((pd.Timestamp(x) for x in q.data["as_of"].dropna()), default=None)
        prov += q.provenance_dicts()
        if missing:
            notes.append(f"{key}: no live mark for {', '.join(missing)} (held at the previous close)")
        br = mc_risk.book_risk(r, np.array([lw[t] for t in tickers]), n_paths=int(p["n_paths"]), seed=int(p["seed"]),
                               threads=ctx.threads, alphas=(0.99,), horizons=(1,))
        for row in br.rows:
            rows.append({"book": key, **row, "var_usd": row["var"] * b["notional"], "es_usd": row["es"] * b["notional"],
                         "day_pnl": None if missing else day, "complete": not missing,
                         "mark_as_of": None if mark is None else mark.isoformat()})
    return ArtifactSpec(tables={"summary": pd.DataFrame(rows)}, data_asof=str(min(last).date()), provenance=prov,
                        notes=[*notes, "weights drifted to the live marks; GARCH filters as of the last close"],
                        engine=br.engine, verdict="DESCRIPTIVE ONLY",
                        verdict_detail="RISK MEASUREMENT · NO GATE")


def intraday_for(weights: dict[str, float], *, now: datetime | None = None,
                 db_path: Path | None = None) -> dict[str, Any] | None:
    """The latest intraday FHS 1-day 99% reading for the reference book with exactly these weights, in the
    deck's ``mc_var`` wire shape (ship/lane-f quant/web/src/pages/deck/live.ts)."""
    from .io import latest_with_path, read_table

    key = next((k for k, b in _books().items()
                if {h["ticker"]: h["weight"] for h in b["holdings"]}.keys() == weights.keys()
                and all(abs(h["weight"] - weights[h["ticker"]]) < 1e-9 for h in b["holdings"])), None)
    if key is None:
        return None
    got = latest_with_path("risk.mc_intraday", now=now or datetime.now(UTC), db_path=db_path or DEFAULT_DB)
    if got is None:
        return None
    m, path, stale = got
    s = read_table(path, "summary")
    hit = s[(s["book"] == key) & (s["method"] == "fhs")] if s is not None else pd.DataFrame()
    if hit.empty:
        return None
    row = hit.iloc[0]
    as_of = row["mark_as_of"] if isinstance(row["mark_as_of"], str) and row["mark_as_of"] else m["finished_at"]
    return {"var_usd": float(row["var_usd"]), "es_usd": float(row["es_usd"]), "alpha": float(row["alpha"]),
            "horizon_days": 1, "method": "fhs", "paths": None, "as_of": as_of, "finished_at": m["finished_at"],
            "stale": stale, "book": key, "artifact_id": m["id"]}
