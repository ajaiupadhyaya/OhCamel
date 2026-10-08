"""P3 job: ``cov.league`` (weekly, heavy, class M).

Universes: universes.json ``sectors``; every universes.json ETF with full
history over the span; ``ndx100`` members from the warehouse (survivorship
noted). Tickers without full history over the evaluation span are dropped
and listed. ``params["universes"]`` (name -> tickers, or None for the
warehouse's ndx100) replaces the default set in tests.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..data.base import DataUnavailable
from ..jobs.artifacts import ArtifactSpec
from ..models import cov_league as C
from ..models.cov_league import ESTIMATORS

SURVIVORSHIP = "current constituents; delisted names absent"


def _default_universes() -> dict[str, list[str] | None]:
    from ..data.market import load_universes

    u = load_universes()["universes"]
    every = sorted({m["ticker"] for v in u.values() for m in v["members"]})
    return {"sectors": [m["ticker"] for m in u["sectors"]["members"]], "site_etfs": every, "ndx100": None}


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    universes = params.get("universes") or _default_universes()
    eval_sessions = int(params.get("eval_sessions", C.EVAL_SESSIONS))
    league, tests, skipped, prov, notes, last = [], [], [], [], [], []
    surv = None
    for k, (name, tickers) in enumerate(universes.items()):
        ctx.check_cancelled()
        try:
            if tickers is None:
                rows = ctx.warehouse.execute("SELECT ticker FROM universe_members WHERE universe = ? ORDER BY ticker",
                                             [name]).fetchall()
                tickers, surv = [r[0] for r in rows], SURVIVORSHIP
            got = {}
            for t in tickers:
                try:
                    ds = ctx.market.returns([t])
                    got[t] = ds.data[t]
                except DataUnavailable as e:
                    notes.append(f"{name}: {t} dropped ({e})")
            panel = pd.DataFrame(got).dropna(how="all")
            span = panel.iloc[-(eval_sessions + C.EST_WINDOW):]
            full = span.columns[span.notna().all()]
            if len(full) < 3:
                raise ValueError(f"{len(full)} tickers with full history over the span (< 3)")
            dropped = sorted(set(span.columns) - set(full))
            if dropped:
                notes.append(f"{name}: no full history over the span for {', '.join(dropped)}")
            daily, gross = C.league_returns(span[full], est_window=C.EST_WINDOW, hold=C.HOLD,
                                            eval_sessions=eval_sessions)
        except (DataUnavailable, ValueError) as e:
            skipped.append({"universe": name, "reason": str(e)})
            continue
        vols = daily.std(ddof=1) * np.sqrt(252)
        ranks = vols.rank(method="first").astype(int)
        for e in ESTIMATORS:
            vt = None if e == "sample" else C.variance_test(daily[e].to_numpy(), daily["sample"].to_numpy())
            league.append({"universe": name, "estimator": e, "realized_vol": float(vols[e]), "rank": int(ranks[e]),
                           "lw_p": None if vt is None else vt["pvalue"], "n_days": int(len(daily)),
                           "n_rebalances": int(len(gross)), "tickers": int(len(full)),
                           "mean_gross": float(gross[e].mean())})
            if vt is not None:
                tests.append({"universe": name, "estimator": e, "vs": "sample", **vt})
        last.append(daily.index[-1])
        prov.append({"source": "ohcamel-cov-league", "universe": name, "tickers": list(full)})
        ctx.progress((k + 1) / len(universes), name)
    if not league:
        return ArtifactSpec(tables={"skipped": pd.DataFrame(skipped)}, data_asof=None, provenance=prov, notes=notes,
                            verdict="INSUFFICIENT DATA", verdict_detail="NO UNIVERSE WITH COMMON HISTORY")
    lg = pd.DataFrame(league)
    win = lg[lg["rank"] == 1].groupby("estimator").size().sort_values(ascending=False)
    return ArtifactSpec(
        tables={"league": lg, "tests": pd.DataFrame(tests), "skipped": pd.DataFrame(skipped, columns=["universe", "reason"])},
        data_asof=str(min(last).date()), provenance=prov,
        notes=[*notes, f"GMV portfolios refitted every {C.HOLD} sessions on {C.EST_WINDOW}; realized volatility "
                       "annualized; equal-variance test vs sample covariance (HAC delta method)"],
        survivorship=surv, verdict="DESCRIPTIVE ONLY",
        verdict_detail="ESTIMATOR LEAGUE · LOWEST GMV VOL " + ", ".join(f"{str(e).upper()} ({n})"
                                                                       for e, n in win.items()))
