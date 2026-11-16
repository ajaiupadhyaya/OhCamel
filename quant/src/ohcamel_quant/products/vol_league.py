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


def _minute_parts(ctx: Any, ticker: str, days: pd.DatetimeIndex, *, min_prints: int = MIN_PRINTS,
                  notes: list[str] | None = None) -> dict[str, np.ndarray]:
    """Per session on ``days``: ``rv`` (open-to-close realized variance: sum of squared 1-minute log returns
    between consecutive bar closes, 09:30-16:00 New York, kernel ``realized_vol_minute``), and ``first`` /
    ``last`` (the closes of the session's first and last stored bar). All NaN where the warehouse has no bars or
    fewer than ``min_prints`` bars (every stored bar is a minute with a print) in the session; those sessions
    are named in ``notes``. Minute closes are split-adjusted, not dividend-adjusted (``adjustment=split``)."""
    nan = np.full(len(days), np.nan)
    out = {"rv": nan, "first": nan, "last": nan}
    try:
        df = ctx.warehouse.execute("SELECT ts, close FROM bars_minute WHERE ticker = ? ORDER BY ts", [ticker]).df()
    except DataUnavailable:
        return out
    if df.empty:
        return out
    ts = pd.DatetimeIndex(df["ts"]).tz_localize("UTC")  # bars_minute.ts is naive UTC (warehouse/ingest/minute.py)
    px = df["close"].to_numpy(float)
    opens = pd.DatetimeIndex([pd.Timestamp(d.date()).tz_localize(NY) + pd.Timedelta(hours=9, minutes=30) for d in days])
    closes = opens + pd.Timedelta(hours=6, minutes=30)
    bounds = np.column_stack([opens.asi8, closes.asi8])
    rv = V.kernels.realized_vol_minute(ts.asi8, px, bounds)
    lo = np.searchsorted(ts.asi8, opens.asi8, side="left")
    hi = np.searchsorted(ts.asi8, closes.asi8, side="left")
    prints = hi - lo
    stored = (closes.asi8 > ts.asi8[0]) & (opens.asi8 <= ts.asi8[-1])   # sessions inside the stored minute history
    thin = stored & (prints < min_prints)                                  # includes a session with no print at all
    if thin.any() and notes is not None:
        notes.append(f"{ticker}: {int(thin.sum())} session(s) with fewer than {min_prints} IEX prints "
                     f"(median {int(np.median(prints[thin]))}) excluded from the minute target; a name left without a "
                     "complete target is scored on the squared daily return")
    good = prints >= min_prints
    first = np.where(good, px[np.minimum(lo, px.size - 1)], np.nan)
    last = np.where(good, px[np.maximum(hi - 1, 0)], np.nan)
    return {"rv": np.where(good, rv, np.nan), "first": first, "last": last}


def _minute_only(ctx: Any, ticker: str, days: pd.DatetimeIndex, *, min_prints: int = MIN_PRINTS,
                 notes: list[str] | None = None) -> np.ndarray:
    """The minute part alone: realized variance from the first to the last stored bar's close."""
    return _minute_parts(ctx, ticker, days, min_prints=min_prints, notes=notes)["rv"]


def _daily_bars(ctx: Any, ticker: str) -> pd.DataFrame:
    """The warehouse's daily bars (the official open and close), split-repaired as ``MarketData.ohlcv`` does
    on its way out (``data.splits.present``): ``close`` is split-adjusted, ``adj_close`` total-return adjusted."""
    from ..data.splits import present
    from ..warehouse.readers import ohlcv

    return present(ohlcv(ctx.warehouse, ticker).data)[0]


def _overnight_return(ctx: Any, ticker: str, days: pd.DatetimeIndex) -> np.ndarray:
    """``open_t / close_{t-1} - 1`` on ``days`` from the daily bars table, total-return adjusted (O and C scaled
    by adj_close / close, the basis of ``realized.adjusted_ohlc``), so a split or an ex-dividend date is not an
    overnight move. The previous close is the previous session of the FULL daily-bar index, never of ``days``
    (a session missing from ``days`` must not make it two sessions old); NaN where either bar is invalid."""
    from ..options.realized import adjusted_ohlc

    bars = _daily_bars(ctx, ticker)
    f = bars["adj_close"] / bars["close"]
    ok = pd.Series(bars.index.isin(adjusted_ohlc(bars).index), index=bars.index)
    on = (bars["open"] * f) / (bars["close"] * f).shift(1) - 1.0
    on = on.where(ok & ok.shift(1, fill_value=False))
    return on.reindex(days).to_numpy(float)


def _intraday_legs(ctx: Any, ticker: str, days: pd.DatetimeIndex, first: np.ndarray,
                   last: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The two legs the minute RV does not cover: official open -> first bar's close, and last bar's close ->
    official close, as simple returns. Both prices of each leg are on one basis: the daily bars' UNADJUSTED
    (split-adjusted only) ``open`` and ``close``, like the minute closes; the overnight term uses the
    total-return adjusted prices, as the previous close and the open are on different dates."""
    bars = _daily_bars(ctx, ticker).reindex(days)
    return first / bars["open"].to_numpy(float) - 1.0, bars["close"].to_numpy(float) / last - 1.0


def _target_legs(ctx: Any, ticker: str, days: pd.DatetimeIndex, *, min_prints: int = MIN_PRINTS,
                 notes: list[str] | None = None) -> dict[str, np.ndarray]:
    """The squared pieces of one session's close-to-close path, each a daily variance:
    ``overnight`` (close_{t-1} -> official open), ``open_leg`` (official open -> first bar's close), ``minute``
    (first -> last bar's close) and ``close_leg`` (last bar's close -> official close)."""
    m = _minute_parts(ctx, ticker, days, min_prints=min_prints, notes=notes)
    open_leg, close_leg = _intraday_legs(ctx, ticker, days, m["first"], m["last"])
    return {"overnight": _overnight_return(ctx, ticker, days) ** 2, "open_leg": open_leg ** 2,
            "minute": m["rv"], "close_leg": close_leg ** 2}


def _daily_rv(ctx: Any, ticker: str, days: pd.DatetimeIndex, *, min_prints: int = MIN_PRINTS,
              notes: list[str] | None = None) -> np.ndarray:
    """The league's one scoring target: close-to-close daily variance of session ``t``,
    ``(open_t / close_{t-1} - 1)^2 + (first_bar / open_t - 1)^2 + RV_minute + (close_t / last_bar - 1)^2``.
    GARCH, GJR, EGARCH and EWMA forecast the close-to-close variance (it includes the overnight move and the
    first and last minutes the stored bars do not span), so the target must too; HAR is trained on it.
    Known at the close of ``t`` and nothing later. NaN where any piece is missing."""
    return np.sum(list(_target_legs(ctx, ticker, days, min_prints=min_prints, notes=notes).values()), axis=0)


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
                       "target: close-to-close daily variance of the session = (open_t / close_{t-1} - 1)^2 "
                       "+ (first bar close / open_t - 1)^2 + minute RV from the first to the last bar close "
                       "(09:30-16:00, IEX, sessions with fewer than "
                       f"{MIN_PRINTS} prints excluded) + (close_t / last bar close - 1)^2; open and close are the "
                       "daily bars table's official prices (split-repaired; the overnight term total-return "
                       "adjusted, the two intraday legs split-adjusted only, like the minute closes); names "
                       "without it are scored on the squared daily return, also close-to-close"],
        survivorship=SURVIVORSHIP if surv else None, engine=V.kernels.engine_of("garch_fit"),
        verdict="DESCRIPTIVE ONLY",
        verdict_detail=f"FORECAST LEAGUE · {best['model'].upper()} IN 10% MCS · {best['mcs_rate']:.0%} OF "
                       f"{int(best['names'])} NAMES")
