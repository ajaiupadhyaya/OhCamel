"""Markets: universes, cross-asset overview, price history, quotes, search, 13F.

All numbers are computed from real OHLCV served by :class:`MarketData`;
nothing here is a market input. Definitions (``method`` in the payloads):

* returns use ``adj_close`` (split- and dividend-adjusted where the vendor
  provides it) over *calendar* look-backs: ``r_h = P_t / P_{t-h} - 1`` with
  ``P_{t-h}`` the last close on or before ``t - h``; YTD uses the last close
  of the prior calendar year; the 3-year figure is annualized geometrically,
  ``(P_t / P_{t-3y})^{365.25/days} - 1``;
* 1-month realized volatility: sample standard deviation of daily log
  returns over the last calendar month, annualized by ``sqrt(N)`` with ``N``
  the number of sessions per year actually observed (252 for US equities,
  365 for crypto) -- the close-to-close estimator (e.g. Hull, *Options,
  Futures and Other Derivatives*, ch. 15);
* 52-week range from daily high/low over the last 365 days, after a
  bad-tick filter: a high (low) more than 25% above (below) the session's
  open/close envelope is treated as an erroneous print and replaced by the
  envelope (reported in ``notes``);
* data-quality flag: a daily ``adj_close`` move beyond +/-35% is reported as a
  possible unadjusted corporate action (the data are shown unchanged);
* trend: ``P_t / SMA_n - 1`` for n = 20, 50, 200 sessions;
* 1-year correlation: Pearson correlation of daily simple returns over the
  last 365 calendar days, with prices aligned on common sessions *before*
  differencing (so every asset's return spans the same interval);
* 13F concentration: Herfindahl-Hirschman index ``HHI = sum w_i^2``
  (Hirschman 1945; Herfindahl 1950) and effective number of positions
  ``1/HHI``.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Annotated, Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ...data.base import DataUnavailable
from ...data.market import MarketData, get_market, load_universes
from ..serialize import clean, frame, records

router = APIRouter(prefix="/market", tags=["market"])

Market = Annotated[MarketData, Depends(get_market)]
OVERVIEW_TTL_S = 300.0
F13_TTL_S = 600.0
#: A payload with failed rows (e.g. a vendor outage) is kept only briefly, so
#: a recovered source shows up without waiting out the full TTL.
ERROR_TTL_S = 30.0
MAX_TICKERS = 60
CORR_WINDOW_DAYS = 365
#: Symbols: stocks/ETFs (BRK.B), indices (^VIX), crypto (BTC-USD), FX (EURUSD=X).
TICKER_PATTERN = r"^\^?[A-Za-z0-9][A-Za-z0-9.=_-]{0,19}$"
_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_build_locks: dict[tuple[Any, ...], threading.Lock] = {}


def _has_errors(payload: dict[str, Any]) -> bool:
    return any(r.get("error") for r in payload.get("rows") or [] if isinstance(r, dict))


def _cached(key: tuple[Any, ...], ttl: float, build: Any) -> dict[str, Any]:
    """TTL cache with single flight: concurrent requests for the same key wait
    for one ``build()`` instead of each recomputing (cache stampede)."""
    def fresh() -> dict[str, Any] | None:
        hit = _cache.get(key)
        if hit is None:
            return None
        limit = min(ttl, ERROR_TTL_S) if _has_errors(hit[1]) else ttl
        return hit[1] if time.monotonic() - hit[0] <= limit else None

    with _cache_lock:
        got = fresh()
        if got is not None:
            return got
        lk = _build_locks.setdefault(key, threading.Lock())
    with lk:
        with _cache_lock:
            got = fresh()  # built by the request we waited for
            if got is not None:
                return got
        try:
            payload = build()
            with _cache_lock:
                _cache[key] = (time.monotonic(), payload)
                if len(_cache) > 256:  # bound memory on the small droplet
                    for k in sorted(_cache, key=lambda k: _cache[k][0])[:64]:
                        _cache.pop(k, None)
        finally:
            with _cache_lock:  # don't let one lock per distinct custom list accumulate
                if _build_locks.get(key) is lk:
                    del _build_locks[key]
    return payload


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _build_locks.clear()


def _tickers_param(tickers: str) -> list[str]:
    out = list(dict.fromkeys(t.strip().upper() for t in tickers.split(",") if t.strip()))
    if not out:
        raise HTTPException(422, "no tickers given")
    bad = [t for t in out if not re.match(TICKER_PATTERN, t)]
    if bad:
        raise HTTPException(422, f"invalid ticker symbol(s): {', '.join(bad[:5])}")
    if len(out) > MAX_TICKERS:
        raise HTTPException(422, f"at most {MAX_TICKERS} tickers")
    return out


# ---------------------------------------------------------------- metrics
def _ret_since(px: pd.Series, ts: pd.Timestamp) -> float | None:
    """P_t / P(last close on or before ts) - 1; None without history back to ts."""
    if px.index[0] > ts:
        return None
    base = px.loc[:ts]
    return float(px.iloc[-1] / base.iloc[-1] - 1.0)


BAD_TICK = 0.25
JUMP_FLAG = 0.35


def clean_range(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, int]:
    """High/low with erroneous prints clipped to the open/close envelope:
    ``high > (1+k)*max(open, close)`` or ``low < (1-k)*min(open, close)``, k=0.25."""
    top = df[["open", "close"]].max(axis=1)
    bot = df[["open", "close"]].min(axis=1)
    bad_hi = df["high"] > (1 + BAD_TICK) * top
    bad_lo = df["low"] < (1 - BAD_TICK) * bot
    hi = df["high"].where(~bad_hi, top).fillna(df["close"])
    lo = df["low"].where(~bad_lo, bot).fillna(df["close"])
    return hi, lo, int(bad_hi.sum() + bad_lo.sum())


def quality_flags(df: pd.DataFrame) -> list[str]:
    """Dates where adj_close moves more than +/-35% in one session."""
    r = df["adj_close"].pct_change()
    return [f"adj_close moves {v:+.1%} on {d.date()} -- possible unadjusted split/corporate action"
            for d, v in r[r.abs() > JUMP_FLAG].items()]


def ticker_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """Overview statistics of one instrument from its daily OHLCV (module doc)."""
    px = df["adj_close"].dropna()
    if len(px) < 2:
        raise DataUnavailable("fewer than two sessions of prices")
    t = px.index[-1]
    last_year = px.loc[t - pd.Timedelta(days=365):]
    ann = float(len(last_year) - 1) if px.index[0] <= t - pd.Timedelta(days=365) else 252.0
    ann = ann if ann >= 200 else 252.0
    out: dict[str, Any] = {
        "last": float(df["close"].iloc[-1]), "as_of": t,
        "ret_1d": float(px.iloc[-1] / px.iloc[-2] - 1.0),
        "ret_1w": _ret_since(px, t - pd.Timedelta(days=7)),
        "ret_1m": _ret_since(px, t - pd.DateOffset(months=1)),
        "ret_3m": _ret_since(px, t - pd.DateOffset(months=3)),
        "ret_ytd": _ret_since(px, pd.Timestamp(t.year - 1, 12, 31)),
        "ret_1y": _ret_since(px, t - pd.DateOffset(years=1)),
        "ret_3y_ann": None,
    }
    t3 = t - pd.DateOffset(years=3)
    if px.index[0] <= t3:
        base = px.loc[:t3]
        days = (t - base.index[-1]).days
        out["ret_3y_ann"] = float((px.iloc[-1] / base.iloc[-1]) ** (365.25 / days) - 1.0)
    lr = np.log(px).diff().loc[t - pd.DateOffset(months=1):].iloc[1:].dropna()
    out["vol_1m_ann"] = float(lr.std(ddof=1) * np.sqrt(ann)) if len(lr) >= 10 else None
    w = df.loc[t - pd.Timedelta(days=365):]
    hi_s, lo_s, n_bad = clean_range(w)
    hi, lo = float(hi_s.max()), float(lo_s.min())
    out["bad_ticks_52w"] = n_bad
    last_close = float(df["close"].iloc[-1])
    out["high_52w"], out["low_52w"] = float(hi), float(lo)
    out["dist_52w_high"] = last_close / hi - 1.0 if hi > 0 else None
    out["dist_52w_low"] = last_close / lo - 1.0 if lo > 0 else None
    for n in (20, 50, 200):
        out[f"sma{n}_pos"] = float(px.iloc[-1] / px.tail(n).mean() - 1.0) if len(px) >= n else None
    spark = px.loc[t - pd.DateOffset(months=6):]
    out["sparkline"] = {"index": clean(list(spark.index)), "values": clean(spark.round(4).to_numpy())}
    out["periods_per_year"] = ann
    return out


def correlation_matrix(prices: dict[str, pd.Series], days: int = CORR_WINDOW_DAYS,
                       min_obs: int = 20) -> dict[str, Any]:
    """Pearson correlation of daily simple returns over the last ``days``
    calendar days of *common* sessions.

    Prices are aligned first (inner join on dates) and returns computed on the
    aligned panel, so every return spans the same interval for every asset
    (``r_t = P_t / P_{t'} - 1`` with ``t'`` the previous *common* session).
    Differencing each series on its own calendar and then aligning would pair,
    e.g., a Friday-to-Monday equity return with a Sunday-to-Monday crypto
    return, or a two-day return across a missing print with a one-day return,
    biasing correlations toward zero.
    """
    empty = {"tickers": [], "matrix": [], "n_obs": 0}
    if len(prices) < 2:
        return empty
    px = pd.DataFrame(prices).dropna(how="any").sort_index()
    if len(px) < 2:
        return empty
    px = px.loc[px.index[-1] - pd.Timedelta(days=days):]
    r = px.pct_change().iloc[1:].dropna(how="any")
    if len(r) < min_obs:
        return {**empty, "n_obs": len(r)}
    c = r.corr()
    return {"tickers": list(c.columns), "matrix": clean(c.to_numpy()), "n_obs": len(r),
            "start": clean(r.index[0]), "end": clean(r.index[-1])}


def _overview(market: MarketData, key: str, label: str, members: list[dict[str, str]]) -> dict[str, Any]:
    def load(t: str) -> tuple[str, Any]:
        # Full cached history; metrics are anchored at each series' last session.
        try:
            return t, market.ohlcv(t)
        except DataUnavailable as e:
            return t, e

    with ThreadPoolExecutor(max_workers=6) as ex:
        loaded = dict(ex.map(load, [m["ticker"] for m in members]))
    rows, provs, closes, notes = [], [], {}, []
    for m in members:
        t = m["ticker"]
        res = loaded[t]
        row: dict[str, Any] = {"ticker": t, "name": m.get("name", t), "error": None}
        if isinstance(res, Exception):
            row["error"] = str(res)
            rows.append(row)
            continue
        df = res.data
        df = df.loc[df.index[-1] - pd.DateOffset(years=3, days=15):] if len(df) else df
        try:
            row.update(ticker_metrics(df))
        except DataUnavailable as e:
            row["error"] = str(e)
            rows.append(row)
            continue
        provs.extend(res.provenance_dicts())
        notes.extend(f"{t}: {q}" for q in quality_flags(df.loc[df.index[-1] - pd.Timedelta(days=365):]))
        if row.get("bad_ticks_52w"):
            notes.append(f"{t}: {row['bad_ticks_52w']} erroneous high/low print(s) clipped in the 52-week range")
        closes[t] = df["adj_close"].dropna()
        if (pd.Timestamp(date.today()) - row["as_of"]).days > 5:
            notes.append(f"{t}: latest available session is {row['as_of'].date()}")
        for p in res.provenance:
            if "note" in p.detail:
                notes.append(f"{t}: {p.detail['note']}")
        rows.append(row)
    corr = correlation_matrix(closes)
    as_ofs = [r["as_of"] for r in rows if r.get("as_of") is not None]
    ok = [r for r in rows if not r["error"]]
    if any(r["error"] for r in rows):
        notes.append(f"{len(rows) - len(ok)} of {len(rows)} instruments could not be loaded (see row errors)")
    return clean({
        "universe": key, "label": label, "as_of": max(as_ofs) if as_ofs else None,
        "rows": rows, "correlation_1y": corr,
        "method": {
            "returns": "adj_close, calendar look-backs; P_t/P_{t-h}-1; YTD from prior year-end close; "
                       "3Y annualized (P_t/P_{t-3y})^(365.25/days)-1",
            "volatility": "close-to-close: stdev of daily log returns over 1 calendar month x sqrt(sessions/yr)",
            "range_52w": "max(high), min(low) over 365 days vs last close",
            "trend": "P_t/SMA_n - 1, n in {20, 50, 200} sessions",
            "correlation": "Pearson, daily simple returns computed on common sessions (prices aligned "
                           "first), last 365 calendar days to the latest common session",
        },
        "notes": notes, "provenance": provs,
    })


# ------------------------------------------------------------- endpoints
@router.get("/universes")
def universes() -> dict[str, Any]:
    """Named universe definitions (tickers + names) and notable 13F filers."""
    u = load_universes()
    return {"universes": u["universes"], "notable_13f_filers": u["notable_13f_filers"], "provenance": [],
            "notes": ["definitions only; every figure shown for them is fetched from real sources"]}


@router.get("/overview")
def overview(
    market: Market,
    universe: Annotated[str, Query(description="universe key from /market/universes")] = "us_equity_indices",
    tickers: Annotated[str | None, Query(description="comma-separated custom list (overrides universe)")] = None,
) -> dict[str, Any]:
    """Per-instrument returns, volatility, 52-week range, trend and a 6-month
    sparkline, plus the 1-year correlation matrix, for one universe."""
    if tickers:
        members = [{"ticker": t, "name": t} for t in _tickers_param(tickers)]
        key, label = "custom", "Custom"
    else:
        u = load_universes()["universes"]
        if universe not in u:
            raise HTTPException(404, f"unknown universe {universe!r}; one of {sorted(u)}")
        members, label, key = u[universe]["members"], u[universe]["label"], universe
    ck = ("overview", key, tuple(m["ticker"] for m in members), id(market))
    return _cached(ck, OVERVIEW_TTL_S, lambda: _overview(market, key, label, members))


@router.get("/history/{ticker}")
def history(
    ticker: Annotated[str, Path(pattern=TICKER_PATTERN, description="e.g. SPY, BRK.B, ^VIX, BTC-USD")],
    market: Market, start: date | None = None, end: date | None = None,
) -> dict[str, Any]:
    """Daily OHLCV (open, high, low, close, adj_close, volume) with provenance."""
    if start and end and start > end:
        raise HTTPException(422, "start must be on or before end")
    ds = market.ohlcv(ticker, start, end)
    df = ds.data
    notes = [p.detail["note"] for p in ds.provenance if "note" in p.detail]
    for p in ds.provenance:
        adj = p.detail.get("adjustment")
        if adj:
            notes.append(f"{p.source}: {adj}")
    if len(df) < 250:
        notes.append(f"only {len(df)} sessions in the window")
    return clean({
        "ticker": ticker.upper(), "n": len(df),
        "first": df.index[0] if len(df) else None, "last": df.index[-1] if len(df) else None,
        "ohlcv": frame(df), "notes": notes, "provenance": ds.provenance_dicts(),
    })


@router.get("/quotes")
def quotes(market: Market, tickers: Annotated[str, Query(min_length=1)]) -> dict[str, Any]:
    """Latest quote per ticker (price, previous close, change, as-of, source)."""
    ds = market.quotes(_tickers_param(tickers))
    df = ds.data.copy()
    notes = []
    if market.settings.offline:
        notes.append("offline mode: 'quotes' are the last committed daily closes, not live prices")
    return clean({"quotes": records(df, "ticker"), "notes": notes, "provenance": ds.provenance_dicts()})


@router.get("/search")
def search(
    market: Market, q: Annotated[str, Query(min_length=1, max_length=64)],
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
) -> dict[str, Any]:
    """Ticker / company-name search over SEC's company list."""
    ds = market.search(q, limit)
    return clean({"query": q, "results": ds.data, "provenance": ds.provenance_dicts()})


@router.get("/13f/filers")
def f13_filers() -> dict[str, Any]:
    """Notable institutional managers with verified SEC CIKs."""
    return {"filers": load_universes()["notable_13f_filers"], "provenance": []}


def _f13(market: MarketData, cik: str, top: int) -> dict[str, Any]:
    ds = market.holdings_13f(cik)
    d = ds.data
    h: pd.DataFrame = d["holdings"]
    eq = h[h["put_call"].isna()]
    w = eq["weight"].dropna().to_numpy()
    hhi = float(np.sum(w**2)) if len(w) else None
    summary = {
        "n_positions": int(len(eq)), "n_option_rows": int(h["put_call"].notna().sum()),
        "total_value_usd": float(eq["value_usd"].sum()),
        "option_underlying_value_usd": float(h.loc[h["put_call"].notna(), "value_usd"].sum()),
        "top10_weight": float(np.sort(w)[::-1][:10].sum()) if len(w) else None,
        "hhi": hhi, "effective_n": 1.0 / hhi if hhi else None,
    }
    return clean({
        "filer": d["filer"], "cik": d["cik"], "period": d["period"], "filed": d["filed"], "form": d["form"],
        "accession": d["accession"], "url": d["url"], "summary": summary,
        "holdings": records(h.head(top)), "n_total_rows": int(len(h)), "amendments": d["amendments"],
        "method": {"weights": "value / sum of non-option values", "hhi": "sum w_i^2 (Herfindahl 1950; "
                   "Hirschman 1945)", "effective_n": "1/HHI"},
        "notes": d["notes"], "provenance": ds.provenance_dicts(),
    })


@router.get("/13f/{cik}")
def f13_holdings(
    market: Market, cik: Annotated[str, Path(pattern=r"^\d{1,10}$")],
    top: Annotated[int, Query(ge=1, le=5000)] = 50,
) -> dict[str, Any]:
    """Latest 13F-HR holdings of a filer with weights and concentration."""
    return _cached(("13f", cik.zfill(10), top, id(market)), F13_TTL_S, lambda: _f13(market, cik, top))
