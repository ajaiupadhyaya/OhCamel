"""Daily OHLCV bars and latest quotes from real vendors.

Providers, tried in ``Settings.price_providers`` order (first success wins):

``alpaca``
    ``GET https://data.alpaca.markets/v2/stocks/{sym}/bars`` (``timeframe=1Day``,
    ``feed=sip``, ``limit=10000``, paginated with ``next_page_token``; needs
    ``APCA_API_KEY_ID``/``APCA_API_SECRET_KEY``). Two passes: OHLCV with
    ``adjustment=split`` and ``adj_close`` from ``adjustment=all`` (splits,
    cash dividends, spin-offs). Free plans may not query SIP data newer than 15
    minutes, so ``end`` is clamped to now-16min, and a 403 on ``feed=sip``
    falls back to ``feed=iex``. Alpaca history starts in 2016, so for longer
    windows Yahoo is tried first.
``yahoo``
    ``GET https://query2.finance.yahoo.com/v8/finance/chart/{sym}`` with
    ``interval=1d``, ``events=div|split``, ``includeAdjustedClose=true``.
    Handles indices (``^GSPC``, ``^VIX``), crypto (``BTC-USD``) and FX
    (``EURUSD=X``). Epoch timestamps are converted to *exchange-local* session
    dates using ``meta.exchangeTimezoneName``. Yahoo's ``close`` is
    split-adjusted, ``adjclose`` is split- and dividend-adjusted (CRSP-style
    total-return factor).
``stooq``
    ``https://stooq.com/q/d/l/?s={sym}.us&i=d`` CSV. No adjusted close:
    ``adj_close = close`` and the provenance says so.

Rules: rows with a missing close are dropped; nothing is forward-filled; an
in-progress (intraday, partial) daily bar is dropped from *history* (it is a
quote, not a session close). The full available history (from 1990-01-01 by
default) is cached per ticker; later calls refresh incrementally, refetching
only the last five cached sessions onward and verifying that the overlap
matches (a new dividend or split rescales history, which triggers a full
refetch rather than a silently inconsistent splice).
"""

from __future__ import annotations

import io
import logging
import re
import threading
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote as urlquote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..config import Settings
from . import http
from .base import DataUnavailable, Provenance
from .store import get_store, stale_provenance

log = logging.getLogger("ohcamel_quant.data.prices")

FAMILY = "prices"
QUOTE_FAMILY = "quotes"
COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
DEFAULT_START = date(1990, 1, 1)
ALPACA_HISTORY_START = date(2016, 1, 1)
ALPACA_BASE = "https://data.alpaca.markets/v2"
YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/"
STOOQ_CSV = "https://stooq.com/q/d/l/"
NY = ZoneInfo("America/New_York")
# Yahoo rejects many non-browser agents with 429; identify ourselves inside a
# browser-compatible token.
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OhCamel-Quant/1.0; +research)"}
SIP_DELAY = timedelta(minutes=16)
OVERLAP_SESSIONS = 5
OVERLAP_RTOL = 5e-5

_EQUITY_RE = re.compile(r"^[A-Z]{1,6}([.-][A-Z]{1,2})?$")
#: US regular-session close (New York wall clock); a daily bar dated today is
#: only a completed session once the (delayed) data reach this time.
NY_CLOSE = time(16, 0)

# Per-ticker locks: concurrent requests for the same ticker (FastAPI runs sync
# endpoints in a threadpool) wait for one fetch instead of each downloading
# the full history (cache stampede / vendor rate limits).
_ticker_locks: dict[str, threading.Lock] = {}
_ticker_locks_guard = threading.Lock()


def _now() -> datetime:
    """Current UTC time (indirection so tests can pin the clock)."""
    return datetime.now(UTC)


def _ticker_lock(ticker: str) -> threading.Lock:
    with _ticker_locks_guard:
        lk = _ticker_locks.get(ticker)
        if lk is None:
            lk = _ticker_locks[ticker] = threading.Lock()
        return lk


# ------------------------------------------------------------------ symbols
def is_equity_symbol(ticker: str) -> bool:
    """US listed stock/ETF-style symbol (``SPY``, ``BRK.B``) -- as opposed to an
    index (``^VIX``), crypto pair (``BTC-USD``) or FX (``EURUSD=X``)."""
    t = ticker.upper()
    return bool(_EQUITY_RE.match(t)) and not t.endswith(("-USD", "-EUR", "-GBP"))


def yahoo_symbol(ticker: str) -> str:
    """Yahoo spells share classes with a dash: ``BRK.B -> BRK-B``."""
    t = ticker.upper()
    if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]{1,2}", t):
        return t.replace(".", "-")
    return t


def alpaca_symbol(ticker: str) -> str:
    """Alpaca spells share classes with a dot: ``BRK-B -> BRK.B``."""
    t = ticker.upper()
    if re.fullmatch(r"[A-Z]{1,5}-[A-Z]{1,2}", t) and not t.endswith("-USD"):
        return t.replace("-", ".")
    return t


def stooq_symbol(ticker: str) -> str:
    return yahoo_symbol(ticker).lower() + ".us"


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    """Contract shape: float columns, tz-naive normalized ascending unique
    ``date`` index, rows with missing close dropped (no fill)."""
    df = df[COLUMNS].astype(float)
    df = df[df["close"].notna() & np.isfinite(df["close"])]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index = pd.DatetimeIndex(df.index).normalize()
    df.index.name = "date"
    return df


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


# ------------------------------------------------------------------- yahoo
def parse_yahoo_chart(
    payload: dict[str, Any], now: datetime | None = None, drop_in_progress: bool = True
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse a Yahoo v8 chart response into contract OHLCV.

    Returns ``(frame, info)``; ``info`` holds the chart ``meta`` and notes.
    Raises :class:`DataUnavailable` on a Yahoo error object or empty result.
    """
    chart = (payload or {}).get("chart") or {}
    results = chart.get("result")
    if not results:
        err = chart.get("error") or {}
        raise DataUnavailable(
            f"yahoo: {err.get('code', 'error')}: {err.get('description', 'empty chart result')}"
        )
    r = results[0]
    meta = r.get("meta") or {}
    ts = r.get("timestamp") or []
    if not ts:
        raise DataUnavailable(f"yahoo: no daily rows for {meta.get('symbol', '?')}")
    tzname = meta.get("exchangeTimezoneName") or "UTC"
    idx = pd.to_datetime(np.asarray(ts, dtype="int64"), unit="s", utc=True).tz_convert(tzname)
    local_dates = idx.tz_localize(None).normalize()
    ind = r.get("indicators") or {}
    q = (ind.get("quote") or [{}])[0]
    n = len(ts)

    def col(values: list[Any] | None) -> np.ndarray:
        if values is None:
            return np.full(n, np.nan)
        return np.array([np.nan if v is None else v for v in values], dtype=float)

    close = col(q.get("close"))
    adj_list = (ind.get("adjclose") or [{}])[0].get("adjclose")
    notes: list[str] = []
    if adj_list is None:
        adj = close.copy()
        notes.append("yahoo returned no adjclose for this symbol; adj_close = close")
    else:
        adj = col(adj_list)
    df = pd.DataFrame(
        {"open": col(q.get("open")), "high": col(q.get("high")), "low": col(q.get("low")),
         "close": close, "adj_close": adj, "volume": col(q.get("volume"))},
        index=local_dates,
    )
    if drop_in_progress and len(df):
        reg = ((meta.get("currentTradingPeriod") or {}).get("regular") or {})
        now_ts = (now or _now()).timestamp()
        start, end = reg.get("start"), reg.get("end")
        if start is not None and end is not None and start <= now_ts < end:
            session = pd.Timestamp(start, unit="s", tz="UTC").tz_convert(tzname).tz_localize(None).normalize()
            if df.index[-1] == session:
                df = df.iloc[:-1]
                notes.append(f"dropped in-progress session {session.date()} (not yet closed)")
    events = r.get("events") or {}
    info = {
        "meta": meta, "notes": notes, "timezone": tzname,
        "n_dividends": len(events.get("dividends") or {}), "n_splits": len(events.get("splits") or {}),
    }
    return _finish(df), info


def _fetch_yahoo(ticker: str, start: date, end: date | None, settings: Settings) -> tuple[pd.DataFrame, Provenance]:
    sym = yahoo_symbol(ticker)
    url = YAHOO_CHART + urlquote(sym, safe="")
    period2 = _epoch((end or datetime.now(UTC).date()) + timedelta(days=1))
    params = {
        "period1": _epoch(start), "period2": period2, "interval": "1d",
        "events": "div|split", "includeAdjustedClose": "true",
    }
    payload = http.get_json(url, settings, params=params, headers=YAHOO_HEADERS, source="yahoo")
    df, info = parse_yahoo_chart(payload)
    if df.empty:
        raise DataUnavailable(f"yahoo: no rows for {sym}")
    detail = {
        "url": url, "symbol": sym, "first": str(df.index[0].date()), "last": str(df.index[-1].date()),
        "adjustment": "close split-adjusted; adj_close split+dividend adjusted (Yahoo adjclose)",
        "timezone": info["timezone"], "currency": info["meta"].get("currency"),
    }
    if info["notes"]:
        detail["notes"] = info["notes"]
    return df, Provenance.now("yahoo", **detail)


# ------------------------------------------------------------------ alpaca
def parse_alpaca_bars(bars: list[dict[str, Any]]) -> pd.DataFrame:
    """Alpaca v2 bars (``t`` RFC-3339 UTC, ``o h l c v``) -> frame indexed by
    New York session date (columns open, high, low, close, volume)."""
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], dtype=float)
    raw = pd.DataFrame(bars)
    idx = pd.to_datetime(raw["t"], utc=True).dt.tz_convert(NY).dt.tz_localize(None).dt.normalize()
    df = pd.DataFrame(
        {"open": raw["o"].astype(float).to_numpy(), "high": raw["h"].astype(float).to_numpy(),
         "low": raw["l"].astype(float).to_numpy(), "close": raw["c"].astype(float).to_numpy(),
         "volume": raw["v"].astype(float).to_numpy()},
        index=pd.DatetimeIndex(idx.to_numpy()),
    )
    return df[~df.index.duplicated(keep="last")].sort_index()


def _alpaca_headers(settings: Settings) -> dict[str, str]:
    return {"APCA-API-KEY-ID": settings.alpaca_key_id or "",
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key or ""}


def _alpaca_pages(sym: str, start: date, end_utc: datetime, adjustment: str, feed: str,
                  settings: Settings) -> list[dict[str, Any]]:
    url = f"{ALPACA_BASE}/stocks/{urlquote(sym, safe='.')}/bars"
    params: dict[str, Any] = {
        "timeframe": "1Day", "adjustment": adjustment, "feed": feed, "limit": 10000,
        "start": start.isoformat(), "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    out: list[dict[str, Any]] = []
    for _ in range(100):
        payload = http.get_json(url, settings, params=params, headers=_alpaca_headers(settings), source="alpaca")
        out.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            return out
        params["page_token"] = token
    raise DataUnavailable(f"alpaca: pagination did not terminate for {sym}")


def _fetch_alpaca(ticker: str, start: date, end: date | None, settings: Settings) -> tuple[pd.DataFrame, Provenance]:
    if not (settings.alpaca_key_id and settings.alpaca_secret_key):
        raise DataUnavailable("alpaca: API keys not configured")
    if not is_equity_symbol(ticker):
        raise DataUnavailable(f"alpaca: {ticker} is not a US equity symbol")
    sym = alpaca_symbol(ticker)
    s = max(start, ALPACA_HISTORY_START)
    end_utc = _now() - SIP_DELAY
    if end is not None:
        end_utc = min(end_utc, datetime(end.year, end.month, end.day, 23, 59, tzinfo=NY).astimezone(UTC))
    last_err: http.FetchError | None = None
    for feed in ("sip", "iex"):
        try:
            split_bars = _alpaca_pages(sym, s, end_utc, "split", feed, settings)
            all_bars = _alpaca_pages(sym, s, end_utc, "all", feed, settings)
            break
        except http.FetchError as e:
            last_err = e
            if e.status == 403 and feed == "sip":
                continue
            raise
    else:  # pragma: no cover - loop always breaks or raises
        raise last_err or DataUnavailable("alpaca: no feed available")
    df = parse_alpaca_bars(split_bars)
    if df.empty:
        raise DataUnavailable(f"alpaca: no bars for {sym}")
    adj = parse_alpaca_bars(all_bars)["close"]
    df["adj_close"] = adj.reindex(df.index)
    df = _finish(df)
    notes = ["Alpaca daily history begins 2016"] + (["IEX feed (single venue) volume"] if feed == "iex" else [])
    # Alpaca returns the *forming* daily bar during the session; it is a quote,
    # not a session close, so it never enters history (same rule as Yahoo).
    end_ny = end_utc.astimezone(NY)
    if len(df) and df.index[-1] == pd.Timestamp(end_ny.date()) and end_ny.time() < NY_CLOSE:
        notes.append(f"dropped in-progress session {end_ny.date()} (not yet closed)")
        df = df.iloc[:-1]
    if df.empty:
        raise DataUnavailable(f"alpaca: no completed sessions for {sym}")
    return df, Provenance.now(
        "alpaca", symbol=sym, feed=feed, url=f"{ALPACA_BASE}/stocks/{sym}/bars",
        first=str(df.index[0].date()), last=str(df.index[-1].date()),
        adjustment="OHLCV adjustment=split; adj_close adjustment=all (splits, dividends, spin-offs)",
        notes=notes,
    )


# ------------------------------------------------------------------- stooq
def parse_stooq_csv(text: str) -> pd.DataFrame:
    """Stooq daily CSV (``Date,Open,High,Low,Close,Volume``) -> contract frame
    with ``adj_close = close`` (Stooq publishes no adjusted close)."""
    head = text.lstrip()[:200]
    if not head.lower().startswith("date"):
        raise DataUnavailable(f"stooq: {' '.join(head.split())[:80] or 'empty response'}")
    raw = pd.read_csv(io.StringIO(text))
    raw.columns = [c.strip().lower() for c in raw.columns]
    if raw.empty:
        raise DataUnavailable("stooq: no rows")
    df = pd.DataFrame(
        {c: pd.to_numeric(raw[c], errors="coerce") for c in ("open", "high", "low", "close")}
    )
    df["volume"] = pd.to_numeric(raw["volume"], errors="coerce") if "volume" in raw else np.nan
    df["adj_close"] = df["close"]
    df.index = pd.DatetimeIndex(pd.to_datetime(raw["date"]))
    return _finish(df)


def _fetch_stooq(ticker: str, start: date, end: date | None, settings: Settings) -> tuple[pd.DataFrame, Provenance]:
    if not is_equity_symbol(ticker):
        raise DataUnavailable(f"stooq: {ticker} is not a US equity symbol")
    sym = stooq_symbol(ticker)
    params = {"s": sym, "i": "d", "d1": start.strftime("%Y%m%d")}
    if end is not None:
        params["d2"] = end.strftime("%Y%m%d")
    df = parse_stooq_csv(http.get_text(STOOQ_CSV, settings, params=params, source="stooq"))
    return df, Provenance.now(
        "stooq", symbol=sym, url=STOOQ_CSV, first=str(df.index[0].date()), last=str(df.index[-1].date()),
        adjustment="none: Stooq publishes no adjusted close; adj_close = close (total return understated by dividends)",
    )


PROVIDERS = {"alpaca": _fetch_alpaca, "yahoo": _fetch_yahoo, "stooq": _fetch_stooq}


def provider_order(ticker: str, start: date, settings: Settings) -> list[str]:
    """Configured order, except Alpaca (history from 2016) yields to Yahoo for
    windows that begin earlier, and non-equity symbols skip equity-only vendors."""
    order = [p for p in settings.price_providers if p in PROVIDERS]
    if start < ALPACA_HISTORY_START and "alpaca" in order and "yahoo" in order:
        order.remove("alpaca")
        order.insert(order.index("yahoo") + 1, "alpaca")
    if not is_equity_symbol(ticker):
        order = [p for p in order if p == "yahoo"]
    return order


def _window(df: pd.DataFrame, start: date | None, end: date | None) -> pd.DataFrame:
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def merge_incremental(old: pd.DataFrame, new: pd.DataFrame, rtol: float = OVERLAP_RTOL) -> pd.DataFrame:
    """Splice ``new`` bars onto ``old`` after checking the overlap agrees.

    The overlap must be non-empty and ``close``/``adj_close`` must agree to
    ``rtol``; otherwise history was rescaled (new dividend/split) or the
    vendor revised it, and :class:`DataUnavailable` asks for a full refetch.
    """
    common = old.index.intersection(new.index)
    if len(common) == 0:
        raise DataUnavailable("incremental refresh: no overlap with cached history")
    for c in ("close", "adj_close"):
        a, b = old.loc[common, c].to_numpy(), new.loc[common, c].to_numpy()
        if not np.allclose(a, b, rtol=rtol, atol=0.0, equal_nan=True):
            raise DataUnavailable(f"incremental refresh: {c} changed on overlap (split/dividend re-adjustment)")
    merged = pd.concat([old[~old.index.isin(new.index)], new]).sort_index()
    merged.index.name = "date"
    return merged


def _fixture_fallback(ticker: str) -> tuple[pd.DataFrame, Provenance] | None:
    from . import fixtures

    hit = fixtures.history(ticker)
    if hit is None:
        return None
    df, prov = hit
    detail = dict(prov.detail)
    detail["note"] = "all online providers failed; serving the committed real fixture (may be older)"
    return df, Provenance(source=prov.source, fetched_at=prov.fetched_at, detail=detail)


def fetch_ohlcv(ticker: str, start: date | None, end: date | None, settings: Settings):
    """Daily OHLCV for ``ticker`` as a :class:`~.market.Dataset` (see module doc)."""
    ticker = ticker.upper().strip()
    if not ticker:
        raise DataUnavailable("empty ticker")
    with _ticker_lock(ticker):
        return _fetch_ohlcv_locked(ticker, start, end, settings)


def _fetch_ohlcv_locked(ticker: str, start: date | None, end: date | None, settings: Settings):
    from .market import Dataset

    store = get_store(settings)
    want = start or DEFAULT_START
    entry = store.read(FAMILY, ticker)
    reasons: list[str] = []
    if entry is not None:
        cached, cprov = entry.obj, entry.provenance
        covered_from = date.fromisoformat(cprov.detail.get("requested_start", DEFAULT_START.isoformat()))
        covers = want >= covered_from and len(cached) > 0
        # ``attempted_start``: the start the whole provider chain was last asked
        # for. When it reached further back than what we got (e.g. Yahoo
        # failed and Alpaca only has 2016+), the copy is still the best
        # available until its TTL expires -- without this, every call for the
        # default full history re-ran the chain (Yahoo retries + two full
        # Alpaca downloads per ticker per request).
        attempted_from = date.fromisoformat(cprov.detail.get("attempted_start", covered_from.isoformat()))
        best_known = want >= min(covered_from, attempted_from) and len(cached) > 0
        historical = end is not None and len(cached) and pd.Timestamp(end) <= cached.index[-1]
        if (best_known and entry.age_s <= settings.ttl_prices_s) or (covers and historical):
            return Dataset(_window(cached, start, end), [cprov])
        if covers and cprov.source in PROVIDERS and not settings.offline:
            try:
                frm = cached.index[max(0, len(cached) - 1 - OVERLAP_SESSIONS)].date()
                new, nprov = PROVIDERS[cprov.source](ticker, frm, None, settings)
                merged = merge_incremental(cached, new)
                detail = dict(nprov.detail)
                detail.update(requested_start=covered_from.isoformat(), first=str(merged.index[0].date()),
                              incremental_from=frm.isoformat(),
                              attempted_start=min(covered_from, attempted_from).isoformat())
                prov = Provenance(source=nprov.source, fetched_at=nprov.fetched_at, detail=detail)
                store.put(FAMILY, ticker, merged, prov)
                return Dataset(_window(merged, start, end), [prov])
            except DataUnavailable as e:
                reasons.append(str(e))
                log.info("%s: incremental refresh failed (%s); full refetch", ticker, e)
    if not settings.offline:
        for name in provider_order(ticker, want, settings):
            try:
                df, prov = PROVIDERS[name](ticker, want, None, settings)
            except DataUnavailable as e:
                reasons.append(str(e))
                continue
            except Exception as e:  # noqa: BLE001 - malformed vendor payloads must not crash the chain
                reasons.append(f"{name}: unexpected response ({type(e).__name__}: {e})")
                log.exception("%s: provider %s failed", ticker, name)
                continue
            req_start = max(want, ALPACA_HISTORY_START) if name == "alpaca" else want
            detail = dict(prov.detail)
            detail["requested_start"] = req_start.isoformat()
            detail["attempted_start"] = want.isoformat()
            prov = Provenance(source=prov.source, fetched_at=prov.fetched_at, detail=detail)
            store.put(FAMILY, ticker, df, prov)
            out = _window(df, start, end)
            if out.empty:
                raise DataUnavailable(f"{ticker}: {name} has no bars in the requested window")
            return Dataset(out, [prov])
    else:
        reasons.append("offline mode")
    reason = "; ".join(reasons) or "no provider configured"
    if entry is not None:
        out = _window(entry.obj, start, end)
        if not out.empty:
            return Dataset(out, [stale_provenance(entry.provenance, reason)])
    fb = _fixture_fallback(ticker)
    if fb is not None:
        out = _window(fb[0], start, end)
        if not out.empty:
            return Dataset(out, [fb[1]])
    raise DataUnavailable(f"{ticker}: {reason}")


# ------------------------------------------------------------------ quotes
def _ny_date(ts: pd.Timestamp) -> date:
    return ts.tz_convert(NY).date()


def parse_alpaca_snapshots(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Alpaca ``/v2/stocks/snapshots`` (``{SYM: {latestTrade, dailyBar,
    prevDailyBar, ...}}``) -> {SYM: quote row}. The previous close is the
    prior session's close: ``prevDailyBar.c`` when ``dailyBar`` is the latest
    trade's session, else ``dailyBar.c`` (pre-market of the next session)."""
    out: dict[str, dict[str, Any]] = {}
    snaps = payload.get("snapshots", payload) if isinstance(payload, dict) else {}
    for sym, snap in (snaps or {}).items():
        if not isinstance(snap, dict):
            continue
        trade, daily, prev = snap.get("latestTrade") or {}, snap.get("dailyBar") or {}, snap.get("prevDailyBar") or {}
        if "p" not in trade:
            continue
        t = pd.Timestamp(trade["t"])
        prev_close = prev.get("c")
        if daily.get("t") and _ny_date(pd.Timestamp(daily["t"])) != _ny_date(t):
            prev_close = daily.get("c", prev_close)
        out[sym.upper()] = _quote_row(float(trade["p"]), prev_close, t.tz_convert(UTC).tz_localize(None), "alpaca")
    return out


def _quote_row(price: float, prev_close: Any, as_of: pd.Timestamp, source: str) -> dict[str, Any]:
    prev = float(prev_close) if prev_close is not None else float("nan")
    return {
        "price": price, "prev_close": prev, "change": price - prev,
        "change_pct": price / prev - 1.0 if prev else float("nan"), "as_of": as_of, "source": source,
    }


def parse_yahoo_quote(payload: dict[str, Any]) -> dict[str, Any]:
    """Quote from a Yahoo chart response (``range=5d&interval=1d``):
    ``meta.regularMarketPrice`` at ``meta.regularMarketTime``; previous close =
    the last daily close dated before the quote's session (falling back to
    ``meta.previousClose`` / ``meta.chartPreviousClose``)."""
    chart = (payload or {}).get("chart") or {}
    results = chart.get("result")
    if not results:
        err = chart.get("error") or {}
        raise DataUnavailable(f"yahoo: {err.get('description', 'empty quote result')}")
    r = results[0]
    meta = r.get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        raise DataUnavailable(f"yahoo: no regularMarketPrice for {meta.get('symbol')}")
    tzname = meta.get("exchangeTimezoneName") or "UTC"
    t = pd.Timestamp(int(meta.get("regularMarketTime") or 0), unit="s", tz="UTC")
    session = t.tz_convert(tzname).tz_localize(None).normalize()
    prev = meta.get("previousClose")
    ts = r.get("timestamp") or []
    closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    if ts and closes:
        days = pd.to_datetime(np.asarray(ts, dtype="int64"), unit="s", utc=True).tz_convert(tzname)
        days = days.tz_localize(None).normalize()
        before = [(d, c) for d, c in zip(days, closes, strict=False) if d < session and c is not None]
        if before:
            prev = before[-1][1]
    if prev is None:
        prev = meta.get("chartPreviousClose")
    return _quote_row(float(price), prev, t.tz_localize(None), "yahoo")


def _yahoo_quote(ticker: str, settings: Settings) -> dict[str, Any]:
    url = YAHOO_CHART + urlquote(yahoo_symbol(ticker), safe="")
    payload = http.get_json(url, settings, params={"range": "5d", "interval": "1d"},
                            headers=YAHOO_HEADERS, source="yahoo")
    return parse_yahoo_quote(payload)


def fetch_quotes(tickers: list[str], settings: Settings):
    """Latest quote per ticker (DataFrame indexed by ticker: price, prev_close,
    change, change_pct, as_of [UTC, tz-naive], source, error).

    Alpaca snapshots for equities when keys exist, Yahoo chart meta otherwise.
    A ticker that no source can quote gets a row with NaN numbers and an
    ``error``; if none can be quoted, :class:`DataUnavailable` is raised.
    """
    from .market import Dataset

    store = get_store(settings)
    syms = list(dict.fromkeys(t.upper().strip() for t in tickers if t.strip()))
    if not syms:
        raise DataUnavailable("no tickers requested")
    rows: dict[str, dict[str, Any]] = {}
    provs: list[Provenance] = []
    for s in syms:
        hit = store.get(QUOTE_FAMILY, s, settings.ttl_intraday_quote_s)
        if hit is not None:
            row = dict(hit[0])
            row["as_of"] = pd.Timestamp(row["as_of"])
            rows[s] = row
            provs.append(hit[1])
    need = [s for s in syms if s not in rows]
    errors: dict[str, str] = {}
    if need and not settings.offline and settings.alpaca_key_id and settings.alpaca_secret_key:
        eq = [s for s in need if is_equity_symbol(s)]
        for i in range(0, len(eq), 100):
            batch = eq[i:i + 100]
            got: dict[str, dict[str, Any]] = {}
            for feed in ("sip", "iex"):
                try:
                    payload = http.get_json(
                        f"{ALPACA_BASE}/stocks/snapshots", settings,
                        params={"symbols": ",".join(alpaca_symbol(s) for s in batch), "feed": feed},
                        headers=_alpaca_headers(settings), source="alpaca",
                    )
                    got = parse_alpaca_snapshots(payload)
                    break
                except http.FetchError as e:
                    if e.status == 403 and feed == "sip":
                        continue
                    log.info("alpaca snapshots failed: %s", e)
                    break
            for s in batch:
                row = got.get(alpaca_symbol(s))
                if row is not None:
                    prov = Provenance.now("alpaca", endpoint="/v2/stocks/snapshots", symbol=s, feed=feed)
                    rows[s] = row
                    provs.append(prov)
                    store.put(QUOTE_FAMILY, s, {**row, "as_of": row["as_of"].isoformat()}, prov)
    for s in [s for s in need if s not in rows]:
        if settings.offline:
            errors[s] = "offline mode"
            continue
        try:
            row = _yahoo_quote(s, settings)
        except DataUnavailable as e:
            stale = store.get_stale(QUOTE_FAMILY, s, str(e))
            if stale is not None:
                row = dict(stale[0])
                row["as_of"] = pd.Timestamp(row["as_of"])
                rows[s] = row
                provs.append(stale[1])
            else:
                errors[s] = str(e)
            continue
        prov = Provenance.now("yahoo", endpoint="v8/finance/chart meta", symbol=yahoo_symbol(s))
        rows[s] = row
        provs.append(prov)
        store.put(QUOTE_FAMILY, s, {**row, "as_of": row["as_of"].isoformat()}, prov)
    if not rows:
        raise DataUnavailable("quotes: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
    table = []
    for s in syms:
        if s in rows:
            table.append({"ticker": s, **rows[s], "error": None})
        else:
            table.append({"ticker": s, "price": np.nan, "prev_close": np.nan, "change": np.nan,
                          "change_pct": np.nan, "as_of": pd.NaT, "source": None, "error": errors.get(s)})
    return Dataset(pd.DataFrame(table).set_index("ticker"), provs)
