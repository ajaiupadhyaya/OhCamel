"""P2 job: ``vol.forecast_league`` (nightly, heavy, class M; one name in memory at a time).

Names: the warehouse's universe members, or universes.json's ETFs when no
warehouse is configured (said in notes), or ``params["tickers"]``. Daily
returns from ``ctx.market.returns``; minute realized variance from the
warehouse's ``bars_minute`` (09:30-16:00 New York sessions, kernel
``realized_vol_minute``) where a name has it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..data.base import DataUnavailable
from ..jobs.artifacts import ArtifactSpec
from ..models import vol_league as V
from ..warehouse.freshness import NY

SURVIVORSHIP = "current constituents; delisted names absent"


def _names(ctx: Any, params: dict[str, Any], notes: list[str]) -> tuple[list[str], bool]:
    if params.get("tickers"):
        return [str(t).upper() for t in params["tickers"]], False
    try:
        rows = ctx.warehouse.execute("SELECT DISTINCT ticker, universe FROM universe_members").fetchall()
        return sorted({t for t, _ in rows}), any(u in ("sp500", "ndx100") for _, u in rows)
    except DataUnavailable as e:
        from ..data.market import load_universes

        notes.append(f"no warehouse ({e}): universes.json ETFs only")
        u = load_universes()["universes"]
        return sorted({m["ticker"] for v in u.values() for m in v["members"]}), False


def _minute_rv(ctx: Any, ticker: str, days: pd.DatetimeIndex) -> np.ndarray | None:
    try:
        df = ctx.warehouse.execute("SELECT ts, close FROM bars_minute WHERE ticker = ? ORDER BY ts", [ticker]).df()
    except DataUnavailable:
        return None
    if df.empty:
        return None
    ts = pd.DatetimeIndex(df["ts"]).tz_localize("UTC")  # bars_minute.ts is naive UTC (warehouse/ingest/minute.py)
    opens = pd.DatetimeIndex([pd.Timestamp(d.date()).tz_localize(NY) + pd.Timedelta(hours=9, minutes=30) for d in days])
    bounds = np.column_stack([opens.asi8, (opens + pd.Timedelta(hours=6, minutes=30)).asi8])
    rv = V.kernels.realized_vol_minute(ts.asi8, df["close"].to_numpy(float), bounds)
    return rv if np.isfinite(rv).sum() >= V.HAR_WINDOW + 60 else None


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    eval_days = int(params.get("eval_days", V.EVAL_DAYS))
    refit = int(params.get("refit_every", V.REFIT_EVERY))
    notes: list[str] = []
    names, surv = _names(ctx, params, notes)
    league, dm, fcs, skipped, dropped, prov, last = [], [], [], [], [], [], []
    for i, t in enumerate(names):
        ctx.check_cancelled()
        try:
            ds = ctx.market.returns([t])
            s = ds.data[t].dropna()
            if len(s) < V.EST_WINDOW + eval_days:
                raise ValueError(f"{len(s)} sessions (< {V.EST_WINDOW + eval_days})")
            r = s.to_numpy(float)
            mrv = _minute_rv(ctx, t, s.index)
            ok = mrv is not None and np.isfinite(mrv[-(V.HAR_WINDOW + eval_days + 22):]).all()
            target = mrv if ok else r * r
            nl = V.league_for(r, target, has_minute=ok, eval_days=eval_days, refit_every=refit)
        except (DataUnavailable, ValueError, KeyError, np.linalg.LinAlgError) as e:
            skipped.append({"ticker": t, "reason": str(e)})
            continue
        league += [{"ticker": t, **row} for row in nl.rows]
        dropped += [{"ticker": t, "model": m, "reason": why} for m, why in nl.dropped.items()]
        dm += [{"ticker": t, **row} for row in nl.dm]
        fcs.append({"ticker": t, "asof": str(s.index[-1].date()), **nl.forecast})
        prov += ds.provenance_dicts()[:1]
        last.append(s.index[-1])
        if i % 10 == 0:
            ctx.progress((i + 1) / len(names), t)
    lg = pd.DataFrame(league)
    if lg.empty:
        return ArtifactSpec(tables={"skipped": pd.DataFrame(skipped, columns=["ticker", "reason"])}, data_asof=None,
                            provenance=prov, notes=notes, verdict="INSUFFICIENT DATA",
                            verdict_detail="no name had enough history for the league")
    summary = (lg.assign(in_mcs_f=lg["in_mcs"].astype(float))  # None (MCS not computable) -> NaN, not False
               .groupby("model").agg(names=("ticker", "nunique"), mean_rank=("rank_qlike", "mean"),
                                     mcs_rate=("in_mcs_f", "mean"), median_qlike=("qlike", "median"))
               .reset_index())
    best = summary.sort_values("mcs_rate", ascending=False).iloc[0]
    return ArtifactSpec(
        tables={"league": lg, "dm": pd.DataFrame(dm), "summary": summary, "forecasts": pd.DataFrame(fcs),
                "skipped": pd.DataFrame(skipped, columns=["ticker", "reason"]),
                "dropped": pd.DataFrame(dropped, columns=["ticker", "model", "reason"])},
        data_asof=str(min(last).date()), provenance=prov,
        notes=[*notes, f"out of sample: last {eval_days} sessions, refit every {refit}, window {V.EST_WINDOW}; "
                       "target is minute RV where stored, else the squared daily return (QLIKE is robust to it)"],
        survivorship=SURVIVORSHIP if surv else None, engine=V.kernels.engine_of("garch_fit"),
        verdict="DESCRIPTIVE ONLY",
        verdict_detail=f"forecast league, not a strategy; {best['model'].upper()} in the 10% MCS for "
                       f"{best['mcs_rate']:.0%} of {int(best['names'])} names")
