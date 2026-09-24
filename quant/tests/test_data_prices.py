"""Price providers: Yahoo chart JSON, Alpaca bars/snapshots, Stooq CSV, the
provider chain, incremental refresh and stale fallback -- all offline via
inline samples in each vendor's documented format and respx mocks."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import httpx
import pandas as pd
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import http, prices
from ohcamel_quant.data.base import DataUnavailable, Provenance
from ohcamel_quant.data.store import get_store

NY = "America/New_York"


def online(tmp_path, **kw) -> Settings:
    base = dict(offline=False, data_dir=tmp_path, http_retries=0, APCA_API_KEY_ID=None,
                APCA_API_SECRET_KEY=None, FRED_API_KEY=None)
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()


def _open_ts(day: str, tz: str = NY, hhmm: str = "09:30") -> int:
    return int(pd.Timestamp(f"{day} {hhmm}", tz=tz).timestamp())


def yahoo_chart(days: list[str], closes: list[float | None], adj: list[float | None] | None = None,
                tz: str = NY, symbol: str = "SPY", regular: tuple[int, int] | None = None) -> dict:
    """A v8/finance/chart response as Yahoo documents/returns it."""
    n = len(days)
    meta = {"currency": "USD", "symbol": symbol, "exchangeName": "PCX", "instrumentType": "ETF",
            "exchangeTimezoneName": tz, "timezone": "EST", "gmtoffset": -18000,
            "regularMarketPrice": closes[-1], "chartPreviousClose": 470.0,
            "regularMarketTime": _open_ts(days[-1], tz, "16:00")}
    if regular:
        meta["currentTradingPeriod"] = {"regular": {"start": regular[0], "end": regular[1],
                                                    "timezone": "EST", "gmtoffset": -18000}}
    res = {
        "meta": meta, "timestamp": [_open_ts(d, tz) for d in days],
        "events": {"dividends": {str(_open_ts(days[0], tz)): {"amount": 1.9, "date": _open_ts(days[0], tz)}}},
        "indicators": {"quote": [{
            "open": [c and c - 1 for c in closes], "high": [c and c + 2 for c in closes],
            "low": [c and c - 2 for c in closes], "close": closes, "volume": [1_000_000] * n}]},
    }
    if adj is not None:
        res["indicators"]["adjclose"] = [{"adjclose": adj}]
    return {"chart": {"result": [res], "error": None}}


# ---------------------------------------------------------------- parsers
def test_parse_yahoo_chart_local_dates_adjclose_and_nulls():
    payload = yahoo_chart(["2024-01-02", "2024-01-03", "2024-01-04"], [472.65, None, 467.28],
                          adj=[465.1, None, 459.8])
    df, info = prices.parse_yahoo_chart(payload)
    assert list(df.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-04")]  # null close dropped
    assert list(df.columns) == prices.COLUMNS and df.index.name == "date"
    assert df["adj_close"].iloc[-1] == pytest.approx(459.8)
    assert df["close"].iloc[0] == pytest.approx(472.65)
    assert info["n_dividends"] == 1


def test_parse_yahoo_chart_crypto_utc_and_missing_adjclose():
    days = ["2024-03-01", "2024-03-02", "2024-03-03"]  # includes a weekend
    payload = yahoo_chart(days, [62000.0, 62500.0, 63000.0], adj=None, tz="UTC", symbol="BTC-USD")
    df, info = prices.parse_yahoo_chart(payload)
    assert len(df) == 3 and df.index[1].dayofweek == 5
    assert (df["adj_close"] == df["close"]).all()
    assert "adj_close = close" in info["notes"][0]


def test_parse_yahoo_drops_in_progress_session():
    days = ["2024-01-02", "2024-01-03"]
    start, end = _open_ts("2024-01-03"), _open_ts("2024-01-03", hhmm="16:00")
    payload = yahoo_chart(days, [470.0, 471.0], adj=[470.0, 471.0], regular=(start, end))
    now = datetime.fromtimestamp(start + 3600, UTC)
    df, info = prices.parse_yahoo_chart(payload, now=now)
    assert list(df.index) == [pd.Timestamp("2024-01-02")]
    assert "in-progress" in info["notes"][0]
    df2, _ = prices.parse_yahoo_chart(payload, now=datetime.fromtimestamp(end + 60, UTC))
    assert len(df2) == 2


def test_parse_yahoo_error_object():
    payload = {"chart": {"result": None, "error": {"code": "Not Found",
                                                    "description": "No data found, symbol may be delisted"}}}
    with pytest.raises(DataUnavailable, match="delisted"):
        prices.parse_yahoo_chart(payload)


def test_parse_stooq_csv_and_errors():
    csv = ("Date,Open,High,Low,Close,Volume\n"
           "2024-01-02,472.16,473.67,470.49,472.65,123623700\n"
           "2024-01-03,470.43,471.19,468.17,468.79,103585900\n")
    df = prices.parse_stooq_csv(csv)
    assert len(df) == 2 and (df["adj_close"] == df["close"]).all()
    with pytest.raises(DataUnavailable, match="No data"):
        prices.parse_stooq_csv("No data")


def test_parse_alpaca_bars_new_york_dates():
    bars = [{"t": "2024-01-02T05:00:00Z", "o": 472.16, "h": 473.67, "l": 470.49, "c": 472.65,
             "v": 123623700, "n": 1, "vw": 472.0},
            {"t": "2024-07-01T04:00:00Z", "o": 545.6, "h": 545.9, "l": 542.5, "c": 545.3, "v": 1, "n": 1, "vw": 1}]
    df = prices.parse_alpaca_bars(bars)
    assert list(df.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-07-01")]


def test_symbol_conventions_and_provider_order(tmp_path):
    assert prices.yahoo_symbol("BRK.B") == "BRK-B" and prices.alpaca_symbol("BRK-B") == "BRK.B"
    assert prices.stooq_symbol("SPY") == "spy.us"
    assert not prices.is_equity_symbol("^VIX") and not prices.is_equity_symbol("BTC-USD")
    assert not prices.is_equity_symbol("EURUSD=X") and prices.is_equity_symbol("BRK.B")
    s = online(tmp_path)
    assert prices.provider_order("SPY", date(1990, 1, 1), s) == ["yahoo", "alpaca", "stooq"]
    assert prices.provider_order("SPY", date(2020, 1, 1), s) == ["alpaca", "yahoo", "stooq"]
    assert prices.provider_order("^VIX", date(2020, 1, 1), s) == ["yahoo"]


# ------------------------------------------------------------ fetch chain
@respx.mock
def test_chain_falls_back_to_stooq_and_says_so(tmp_path):
    s = online(tmp_path)
    respx.get(url__startswith=prices.YAHOO_CHART).mock(return_value=httpx.Response(500))
    respx.get(url__startswith=prices.STOOQ_CSV).mock(return_value=httpx.Response(
        200, text="Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,0.5,1.5,10\n2024-01-03,1,2,0.5,1.6,10\n"))
    ds = prices.fetch_ohlcv("SPY", None, None, s)
    assert ds.provenance[0].source == "stooq"
    assert "no adjusted close" in ds.provenance[0].detail["adjustment"].lower()
    assert len(ds.data) == 2


@respx.mock
def test_all_providers_fail_names_every_reason(tmp_path):
    s = online(tmp_path)
    respx.get(url__startswith=prices.YAHOO_CHART).mock(return_value=httpx.Response(404))
    respx.get(url__startswith=prices.STOOQ_CSV).mock(return_value=httpx.Response(200, text="No data"))
    with pytest.raises(DataUnavailable) as ei:
        prices.fetch_ohlcv("ZZZZ", None, None, s)
    msg = str(ei.value)
    assert "yahoo: HTTP 404" in msg and "stooq" in msg and "alpaca: API keys not configured" in msg


@respx.mock
def test_alpaca_pagination_split_and_all_adjustments_and_iex_fallback(tmp_path):
    s = online(tmp_path, APCA_API_KEY_ID="k", APCA_API_SECRET_KEY="sec")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        calls.append(q)
        assert request.headers["APCA-API-KEY-ID"] == "k"
        if q["feed"] == "sip":
            return httpx.Response(403, json={"message": "subscription does not permit querying recent SIP data"})
        mult = 1.0 if q["adjustment"] == "split" else 0.9
        if "page_token" not in q:
            bars = [{"t": "2024-01-02T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 100 * mult, "v": 5}]
            return httpx.Response(200, json={"bars": bars, "symbol": "SPY", "next_page_token": "abc"})
        bars = [{"t": "2024-01-03T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 101 * mult, "v": 5}]
        return httpx.Response(200, json={"bars": bars, "symbol": "SPY", "next_page_token": None})

    respx.get(url__startswith=prices.ALPACA_BASE + "/stocks/SPY/bars").mock(side_effect=handler)
    ds = prices.fetch_ohlcv("SPY", date(2024, 1, 1), None, s)
    df = ds.data
    assert list(df["close"]) == [100.0, 101.0]
    assert list(df["adj_close"]) == pytest.approx([90.0, 90.9])
    assert ds.provenance[0].source == "alpaca" and ds.provenance[0].detail["feed"] == "iex"
    assert {c["adjustment"] for c in calls if c["feed"] == "iex"} == {"split", "all"}
    assert all(c["timeframe"] == "1Day" and c["limit"] == "10000" for c in calls)
    end = pd.Timestamp(calls[0]["end"])
    assert end <= pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=15)  # SIP delay clamp


def _cache(tmp_path, df, source="yahoo", age_s=10 * 24 * 3600, requested_start="1990-01-01"):
    s = online(tmp_path)
    get_store(s).put(prices.FAMILY, "SPY", df, Provenance.now(source, requested_start=requested_start),
                     fetched_at=time.time() - age_s)
    return s


def _frame(days, closes, adj=None):
    idx = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    adj = adj or closes
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                         "adj_close": adj, "volume": [1.0] * len(days)}, index=idx).astype(float)


def test_fresh_cache_hit_needs_no_network(tmp_path):
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=10)]
    s = _cache(tmp_path, _frame(days, list(range(100, 110))), age_s=10)
    with respx.mock(assert_all_called=False) as m:
        ds = prices.fetch_ohlcv("SPY", date(2024, 1, 5), None, s)
        assert m.calls.call_count == 0
    assert ds.data.index[0] == pd.Timestamp("2024-01-05")


@respx.mock
def test_incremental_refresh_fetches_tail_and_merges(tmp_path):
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=10)]
    s = _cache(tmp_path, _frame(days, [float(x) for x in range(100, 110)]))
    new_days = days[-6:] + ["2024-01-16", "2024-01-17"]
    closes = [float(x) for x in range(104, 110)] + [110.0, 111.0]
    route = respx.get(url__startswith=prices.YAHOO_CHART).mock(
        return_value=httpx.Response(200, json=yahoo_chart(new_days, closes, adj=closes)))
    ds = prices.fetch_ohlcv("SPY", None, None, s)
    assert route.call_count == 1
    p1 = int(route.calls[0].request.url.params["period1"])
    assert datetime.fromtimestamp(p1, UTC).date() == date.fromisoformat(days[-6])
    assert len(ds.data) == 12 and ds.data["close"].iloc[-1] == 111.0
    assert ds.provenance[0].detail["requested_start"] == "1990-01-01"


@respx.mock
def test_incremental_mismatch_triggers_full_refetch(tmp_path):
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=10)]
    s = _cache(tmp_path, _frame(days, [float(x) for x in range(100, 110)]))
    # a new dividend rescaled adj_close history -> overlap disagrees
    closes = [float(x) for x in range(100, 110)] + [110.0]
    adj = [c * 0.99 for c in closes]
    route = respx.get(url__startswith=prices.YAHOO_CHART).mock(
        return_value=httpx.Response(200, json=yahoo_chart(days + ["2024-01-16"], closes, adj=adj)))
    ds = prices.fetch_ohlcv("SPY", None, None, s)
    assert route.call_count == 2  # incremental attempt, then full history
    p1 = int(route.calls[1].request.url.params["period1"])
    assert datetime.fromtimestamp(p1, UTC).date() == date(1990, 1, 1)
    assert ds.data["adj_close"].iloc[0] == pytest.approx(99.0)


@respx.mock
def test_stale_copy_served_with_note_when_everything_fails(tmp_path):
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=10)]
    s = _cache(tmp_path, _frame(days, [float(x) for x in range(100, 110)]))
    respx.get(url__startswith=prices.YAHOO_CHART).mock(return_value=httpx.Response(503))
    respx.get(url__startswith=prices.STOOQ_CSV).mock(return_value=httpx.Response(503))
    ds = prices.fetch_ohlcv("SPY", None, None, s)
    assert len(ds.data) == 10
    assert ds.provenance[0].detail["note"].startswith("stale: refresh failed:")


# ------------------------------------------------------------------ quotes
def test_parse_yahoo_quote_prev_close_is_prior_session():
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    payload = yahoo_chart(days, [470.0, 468.0, 467.0], adj=[470.0, 468.0, 467.0])
    payload["chart"]["result"][0]["meta"]["regularMarketPrice"] = 467.5
    q = prices.parse_yahoo_quote(payload)
    assert q["price"] == 467.5 and q["prev_close"] == 468.0
    assert q["change_pct"] == pytest.approx(467.5 / 468.0 - 1)


def test_parse_alpaca_snapshots():
    payload = {
        "SPY": {"latestTrade": {"t": "2024-01-04T15:00:00Z", "p": 468.1},
                "dailyBar": {"t": "2024-01-04T05:00:00Z", "c": 468.0},
                "prevDailyBar": {"t": "2024-01-03T05:00:00Z", "c": 467.0}},
        # pre-market next day: dailyBar still the prior session
        "QQQ": {"latestTrade": {"t": "2024-01-05T12:00:00Z", "p": 400.0},
                "dailyBar": {"t": "2024-01-04T05:00:00Z", "c": 399.0},
                "prevDailyBar": {"t": "2024-01-03T05:00:00Z", "c": 398.0}},
    }
    out = prices.parse_alpaca_snapshots(payload)
    assert out["SPY"]["prev_close"] == 467.0 and out["QQQ"]["prev_close"] == 399.0


@respx.mock
def test_fetch_quotes_partial_failure_keeps_other_rows(tmp_path):
    s = online(tmp_path)
    ok = yahoo_chart(["2024-01-02", "2024-01-03"], [1.0, 2.0], adj=[1.0, 2.0])
    respx.get(prices.YAHOO_CHART + "SPY").mock(return_value=httpx.Response(200, json=ok))
    respx.get(prices.YAHOO_CHART + "NOPE").mock(return_value=httpx.Response(404))
    ds = prices.fetch_quotes(["SPY", "NOPE"], s)
    assert ds.data.loc["SPY", "price"] == 2.0 and ds.data.loc["SPY", "prev_close"] == 1.0
    assert pd.isna(ds.data.loc["NOPE", "price"]) and "404" in ds.data.loc["NOPE", "error"]
