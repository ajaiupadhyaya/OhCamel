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


MIN_PRINTS = 100  # IEX is ~2-3% of consolidated volume: a thin name can print only a handful of minutes in a session


def _minute_only(ctx: Any, ticker: str, days: pd.DatetimeIndex, *, min_prints: int = MIN_PRINTS,
                 notes: list[str] | None = None) -> np.ndarray:
    """Open-to-close realized variance per session (sum of squared 1-minute log returns, 09:30-16:00 New
    York, kernel ``realized_vol_minute``); NaN where the warehouse has no bars or fewer than ``min_prints``
    bars (every stored bar is a minute with a print) in the session. Excluded sessions are named in ``notes``."""
    nan = np.full(len(days), np.nan)
    try:
        df = ctx.warehouse.execute("SELECT ts, close FROM bars_minute WHERE ticker = ? ORDER BY ts", [ticker]).df()
    except DataUnavailable:
        return nan
    if df.empty:
        return nan
    ts = pd.DatetimeIndex(df["ts"]).tz_localize("UTC")  # bars_minute.ts is naive UTC (warehouse/ingest/minute.py)
    opens = pd.DatetimeIndex([pd.Timestamp(d.date()).tz_localize(NY) + pd.Timedelta(hours=9, minutes=30) for d in days])
    closes = opens + pd.Timedelta(hours=6, minutes=30)
    bounds = np.column_stack([opens.asi8, closes.asi8])
    rv = V.kernels.realized_vol_minute(ts.asi8, df["close"].to_numpy(float), bounds)
    prints = np.searchsorted(ts.asi8, closes.asi8, side="left") - np.searchsorted(ts.asi8, opens.asi8, side="left")
    stored = (closes.asi8 > ts.asi8[0]) & (opens.asi8 <= ts.asi8[-1])   # sessions inside the stored minute history
    thin = stored & (prints < min_prints)                                  # includes a session with no print at all
    if thin.any() and notes is not None:
        notes.append(f"{ticker}: {int(thin.sum())} session(s) with fewer than {min_prints} IEX prints "
                     f"(median {int(np.median(prints[thin]))}) excluded from the minute target; a name left without a "
                     "complete target is scored on the squared daily return")
    return np.where(thin, np.nan, rv)


def _overnight_return(ctx: Any, ticker: str, days: pd.DatetimeIndex) -> np.ndarray:
    """``open_t / close_{t-1} - 1`` on ``days`` from the daily bars table (the official open and close, not the
    first and last IEX print), split-repaired (``data.splits.present``, as MarketData.ohlcv does on its way
    out) and total-return adjusted (``realized.adjusted_ohlc``: O/H/L/C scaled by adj_close / close), so a
    split or an ex-dividend date is not an overnight move. NaN where either price is missing."""
    from ..data.splits import present
    from ..options.realized import adjusted_ohlc
    from ..warehouse.readers import ohlcv

    adj = adjusted_ohlc(present(ohlcv(ctx.warehouse, ticker).data)[0])
    o, c = adj["open"].reindex(days), adj["close"].reindex(days)
    return (o / c.shift(1) - 1.0).to_numpy(float)   # shift on ``days``: a missing session leaves NaN, not a stale close


def _daily_rv(ctx: Any, ticker: str, days: pd.DatetimeIndex, *, min_prints: int = MIN_PRINTS,
              notes: list[str] | None = None) -> np.ndarray:
    """The league's one scoring target: close-to-close daily variance of session ``t``,
    ``RV_open_to_close(t) + (open_t / close_{t-1} - 1)^2``. GARCH, GJR, EGARCH and EWMA forecast the
    close-to-close variance (it includes the overnight move), so the target must too; HAR is trained on it.
    Known at the close of ``t`` and nothing later. NaN where the minute part or the overnight return is missing."""
    return _minute_only(ctx, ticker, days, min_prints=min_prints, notes=notes) \
        + _overnight_return(ctx, ticker, days) ** 2


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
            try:
                mrv = _daily_rv(ctx, t, s.index, notes=notes)
            except DataUnavailable:  # no warehouse, or the name is not in its daily bars
                mrv = np.full(len(s), np.nan)
            ok = np.isfinite(mrv).sum() >= V.HAR_WINDOW + 60 and np.isfinite(mrv[-(V.HAR_WINDOW + eval_days + 22):]).all()
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
                            verdict_detail="NO NAME WITH ENOUGH HISTORY")
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
                       "target: close-to-close daily variance of the session = open-to-close minute RV (09:30-16:00, "
                       f"IEX, sessions with fewer than {MIN_PRINTS} prints excluded) + (open_t / close_{{t-1}} - 1)^2, "
                       "the overnight move from the daily bars table's official open and close (split-repaired, "
                       "total-return adjusted); names without it are scored on the squared daily return, "
                       "also close-to-close"],
        survivorship=SURVIVORSHIP if surv else None, engine=V.kernels.engine_of("garch_fit"),
        verdict="DESCRIPTIVE ONLY",
        verdict_detail=f"FORECAST LEAGUE · {best['model'].upper()} IN 10% MCS · {best['mcs_rate']:.0%} OF "
                       f"{int(best['names'])} NAMES")
