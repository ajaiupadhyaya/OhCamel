"""Completion task 4.7 job: ``beta.league`` (weekly, Saturday 08:30, heavy, class M).

Universe: universes.json ``sectors`` (the eleven Select Sector SPDR ETFs) against the market ``SPY``
(universes.json ``us_equity_indices``), daily returns from ``ctx.market.returns`` -- real data only. A sector
without enough common history with SPY is skipped with its reason (XLC and XLRE are younger than the rest).
``params``: ``tickers`` (replaces the sectors), ``market``, ``eval_sessions``, ``kalman_window``, ``refit_every``
(tests shorten the run; the schedule passes none).

Mechanics and the test are models/beta_league.py's: rolling OLS (60, 120, 250) against the filtered Kalman beta,
each scored on the realised beta over the NEXT 21 sessions; Diebold-Mariano with a Newey-West lag of 40 per
sector, and pooled (the cross-sectional mean loss differential on the dates every scored sector shares) with
Holm's adjustment over the three comparisons. The verdict is DESCRIPTIVE ONLY: a forecast comparison is not a
strategy, and the detail names a pooled difference only when its Holm-adjusted p-value is below 5%, with it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..data.base import DataUnavailable
from ..jobs.artifacts import ArtifactSpec
from ..models import beta_league as B

MARKET = "SPY"
ALPHA = 0.05


def _sectors() -> list[str]:
    from ..data.market import load_universes

    return [m["ticker"] for m in load_universes()["universes"]["sectors"]["members"]]


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    tickers = [str(t).upper() for t in (params.get("tickers") or _sectors())]
    market = str(params.get("market", MARKET)).upper()
    kw = {"eval_sessions": int(params.get("eval_sessions", B.EVAL_SESSIONS)),
          "kalman_window": int(params.get("kalman_window", B.KALMAN_WINDOW)),
          "refit_every": int(params.get("refit_every", B.REFIT_EVERY))}
    notes: list[str] = []
    try:
        mkt = ctx.market.returns([market]).data[market].dropna()
    except DataUnavailable as e:
        return ArtifactSpec(tables={"skipped": pd.DataFrame([{"ticker": market, "reason": str(e)}])},
                            data_asof=None, provenance=[], notes=[f"market {market} unavailable"],
                            verdict="INSUFFICIENT DATA", verdict_detail="NO MARKET SERIES")
    league, tests, latest, skipped, prov, losses = [], [], [], [], [], {}
    for k, t in enumerate(tickers):
        ctx.check_cancelled()
        try:
            y = ctx.market.returns([t]).data[t].dropna()
            common = y.index.intersection(mkt.index)
            s = B.score_pair(y.loc[common], mkt.loc[common], **kw)
        except (DataUnavailable, ValueError) as e:
            skipped.append({"ticker": t, "reason": str(e)})
            ctx.progress((k + 1) / len(tickers), t)
            continue
        f = s["forecasts"]
        for row in s["league"].to_dict("records"):
            league.append({"ticker": t, **row, "first": str(f.index[0].date()), "last": str(f.index[-1].date())})
        for row in s["tests"].to_dict("records"):
            tests.append({"ticker": t, **row})
        end = f.iloc[-1]
        latest.append({"ticker": t, "date": str(f.index[-1].date()), "kalman": float(end["kalman"]),
                       **{f"ols_{w}": float(end[f"ols_{w}"]) for w in B.WINDOWS}, "q": float(end["q"])})
        losses[t] = s["losses"]
        prov.append({"source": "ohcamel-beta-league", "ticker": t, "market": market, "tickers": [t, market],
                     "first_forecast": str(f.index[0].date()), "last_forecast": str(f.index[-1].date()),
                     "data_through": str(common[-1].date())})
        ctx.progress((k + 1) / len(tickers), t)
    skipped_df = pd.DataFrame(skipped, columns=["ticker", "reason"])
    if not league:
        return ArtifactSpec(tables={"skipped": skipped_df}, data_asof=None, provenance=prov, notes=notes,
                            verdict="INSUFFICIENT DATA", verdict_detail="NO SECTOR WITH ENOUGH HISTORY")
    # pooled: the cross-sectional mean loss differential on the dates every scored sector shares
    dates = sorted(set.intersection(*(set(v.index) for v in losses.values())))
    pooled = []
    for w in B.WINDOWS:
        la = pd.concat([v.loc[dates, "kalman"] for v in losses.values()], axis=1).mean(axis=1).to_numpy()
        lb = pd.concat([v.loc[dates, f"ols_{w}"] for v in losses.values()], axis=1).mean(axis=1).to_numpy()
        pooled.append({"ticker": "POOLED", "model": "kalman", "vs": f"ols_{w}", **B.dm_test(la, lb)})
    for row, adj in zip(pooled, B.holm([p["pvalue"] for p in pooled]), strict=True):
        row["pvalue_holm"] = adj
    tests.extend(pooled)
    lg = pd.DataFrame(league)
    wins = lg[lg["rank"] == 1].groupby("model").size().sort_values(ascending=False)
    sig = [p for p in pooled if p["pvalue_holm"] < ALPHA]
    if sig:
        best = min(sig, key=lambda p: p["pvalue_holm"])
        side = "LOWER" if best["mean_diff"] < 0 else "HIGHER"
        claim = (f"KALMAN {side} POOLED LOSS THAN {best['vs'].upper().replace('_', '-')} "
                 f"HOLM P {best['pvalue_holm']:.3f}")
    else:
        claim = "NO POOLED DIFFERENCE SIGNIFICANT AT 5% AFTER HOLM"
    notes.append(f"Next-{B.HORIZON}-session realised beta (OLS, starting the session after the forecast); "
                 f"Kalman q by ML every {kw['refit_every']} sessions on the trailing {kw['kalman_window']}; "
                 f"Diebold-Mariano with Newey-West lag {B.HAC_LAG} (overlapping {B.HORIZON}-session targets); "
                 f"pooled over {len(losses)} sectors on {len(dates)} common dates, Holm over three comparisons.")
    asof = min(pd.Timestamp(r["data_through"]) for r in prov)     # the last forecast's target ends here
    return ArtifactSpec(
        tables={"league": lg, "tests": pd.DataFrame(tests), "latest": pd.DataFrame(latest), "skipped": skipped_df},
        data_asof=str(asof.date()), provenance=prov, notes=notes, verdict="DESCRIPTIVE ONLY",
        verdict_detail="BETA LEAGUE · LOWEST MSE " + ", ".join(f"{str(m).upper().replace('_', '-')} ({n})"
                                                               for m, n in wins.items()) + " · " + claim)
