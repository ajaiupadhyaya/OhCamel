"""Daily bars ingest (compute plan C3). Vendors are replaced by functions that
serve the committed real fixture bars (fixtures/history), so no network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from ohcamel_quant.config import Settings
from ohcamel_quant.data import fixtures, prices
from ohcamel_quant.data.base import DataUnavailable, Provenance
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.warehouse.constituents import SURVIVORSHIP
from ohcamel_quant.warehouse.db import open_ro, open_rw
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
from ohcamel_quant.warehouse.ingest import HANDLERS, bars
from ohcamel_quant.warehouse.ingest.base import IngestFailed, LocalContext

SPY, _ = fixtures.history("SPY")  # Alpaca daily bars 2016-06-01..2026-06-01
SPY.index = SPY.index.astype("datetime64[ns]")


def vendor(name, frame, calls=None):
    def fetch(ticker, start, end, settings):
        if calls is not None:
            calls.append((name, ticker, start))
        out = frame[frame.index >= pd.Timestamp(start)]
        if out.empty:
            raise DataUnavailable(f"{name}: no bars for {ticker}")
        return out.copy(), Provenance.now(name, symbol=ticker, notes=[f"{name} test vendor"])
    return fetch


def failing(name, msg):
    def fetch(ticker, start, end, settings):
        raise DataUnavailable(f"{name}: {msg}")
    return fetch


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(bars, "_now", lambda: datetime(2026, 6, 2, 14, 0, tzinfo=UTC))  # Tue 10:00 EDT
    s = Settings(offline=False, data_dir=tmp_path, warehouse_path=tmp_path / "w.duckdb",
                 price_providers=["alpaca", "yahoo"], APCA_API_KEY_ID=None, APCA_API_SECRET_KEY=None)
    return LocalContext(market=MarketData(s)), tmp_path / "w.duckdb"


def stored(path, ticker="SPY"):
    with open_ro(path) as con:
        return con.execute("SELECT count(*), max(date), min(source), max(source) FROM bars_daily WHERE ticker = ?",
                           [ticker]).fetchone()


def last_log(path, key="SPY"):
    with open_ro(path) as con:
        return con.execute("SELECT status, data_asof, detail FROM ingest_log WHERE dataset = 'bars_daily' "
                           "AND key = ? ORDER BY ran_at DESC LIMIT 1", [key]).fetchone()


def test_backfill_uses_yahoo_first_for_full_history(setup, monkeypatch):
    # provider_order: a start before 2016 moves alpaca after yahoo (data/prices.py).
    ctx, path = setup
    calls = []
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY, calls))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", vendor("alpaca", SPY, calls))
    spec = bars.run_bars_daily({"tickers": ["spy"]}, ctx)
    assert calls[0] == ("yahoo", "SPY", prices.DEFAULT_START)  # the provider path's own default, 1990-01-01
    assert stored(path) == (len(SPY), date(2026, 6, 1), "yahoo", "yahoo")
    assert spec["data_asof"] == "2026-06-01" and spec["provenance"][0]["source"] == "yahoo"
    assert json.loads(last_log(path)[2])["requested_start"] == "1990-01-01"


def test_years_or_start_narrow_the_window(setup, monkeypatch):
    ctx, path = setup
    calls = []
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY, calls))
    bars.run_bars_daily({"tickers": ["SPY"], "years": 20, "full": True}, ctx)
    bars.run_bars_daily({"tickers": ["SPY"], "start": "2010-01-04", "full": True}, ctx)
    assert [c[2] for c in calls] == [date(2006, 6, 2), date(2010, 1, 4)]  # 20y before Tue 2026-06-02
    assert json.loads(last_log(path)[2])["requested_start"] == "2010-01-04"


def test_fallback_records_the_vendor_that_served(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", failing("yahoo", "HTTP 429"))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", vendor("alpaca", SPY))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert stored(path)[2] == "alpaca"
    status, asof, detail = last_log(path)
    assert status == "ok" and "yahoo: HTTP 429" in json.loads(detail)["fallback_from"]
    # Alpaca is asked from 2016 at the earliest, so the log must not claim 1990 coverage.
    assert json.loads(detail)["requested_start"] == prices.ALPACA_HISTORY_START.isoformat()


def test_incremental_appends_after_the_overlap(setup, monkeypatch):
    ctx, path = setup
    early = SPY[SPY.index <= "2026-05-15"]
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", early))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert stored(path)[1] == date(2026, 5, 15)
    calls = []
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY, calls))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert calls == [("yahoo", "SPY", date(2026, 5, 5))]  # 10 calendar days before 2026-05-15
    assert stored(path)[:2] == (len(SPY), date(2026, 6, 1))
    detail = json.loads(last_log(path)[2])
    assert detail["mode"] == "incremental" and "requested_start" not in detail  # only full fetches prove coverage


def test_readjusted_history_triggers_full_replace(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    readj = SPY.copy()
    readj["adj_close"] *= 0.99  # a new dividend rescales every adjusted close
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", readj))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert json.loads(last_log(path)[2])["mode"] == "full"
    with open_ro(path) as con:
        first = con.execute("SELECT adj_close FROM bars_daily WHERE ticker = 'SPY' ORDER BY date LIMIT 1").fetchone()[0]
    assert first == pytest.approx(SPY["adj_close"].iloc[0] * 0.99, rel=1e-12)  # whole history replaced
    assert stored(path)[0] == len(SPY)


def test_vendor_failure_keeps_last_good_data_asof(tmp_path, monkeypatch):
    # Compute plan Review Focus 4: a vendor fails at 02:00 -> data_asof stays at the last good date.
    path = build_fixture_warehouse(tmp_path / "w.duckdb")  # SPY through 2026-06-01
    monkeypatch.setattr(bars, "_now", lambda: datetime(2026, 6, 3, 6, 0, tzinfo=UTC))
    ctx = LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path, warehouse_path=path,
                                                  price_providers=["alpaca"])))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", failing("alpaca", "HTTP 500"))
    before = stored(path)
    with pytest.raises(IngestFailed, match="alpaca: HTTP 500"):
        bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    status, asof, detail = last_log(path)
    assert (status, asof) == ("failed", date(2026, 6, 1)) and "alpaca: HTTP 500" in detail
    assert stored(path) == before  # bars untouched
    assert last_log(path, "*")[:2] == ("failed", date(2026, 6, 1))


def test_rerun_is_idempotent(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    first = stored(path)
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert stored(path) == first


def test_default_tickers_come_from_the_universe(tmp_path, monkeypatch):
    path = build_fixture_warehouse(tmp_path / "w.duckdb")
    with open_ro(path) as con:
        n = con.execute("SELECT count(DISTINCT ticker) FROM universe_members").fetchone()[0]
    tickers = bars.universe_tickers(path)
    assert len(tickers) == n and tickers == sorted(tickers)
    ctx = LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path,
                                                  warehouse_path=tmp_path / "empty.duckdb")))
    from ohcamel_quant.warehouse.ingest import universes

    calls = []
    monkeypatch.setattr(universes, "run_universes", lambda params, c: calls.append(params) or {})
    with pytest.raises(IngestFailed, match="no universe"):
        bars.run_bars_daily({}, ctx)
    assert calls == [{}], "an empty universe is built on demand before giving up"


def test_an_empty_universe_is_built_before_the_bars(tmp_path, monkeypatch):
    # A fresh warehouse must not fail the daily bars every weekday until
    # Sunday's ingest.universes: the bars job builds the universe itself
    # (it already holds the writer) and then ingests it. Found 2026-10-08,
    # when the production backfill raced the universe job.
    path = tmp_path / "w.duckdb"
    from ohcamel_quant.warehouse.ingest import universes

    def build(params, c):
        with open_rw(path) as con:
            con.execute("INSERT INTO universe_members VALUES ('sp500', 'SPY', 'x', NULL, 't', ?)", [SURVIVORSHIP])
        return {}

    monkeypatch.setattr(universes, "run_universes", build)
    monkeypatch.setattr(bars, "_now", lambda: datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    ctx = LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path, warehouse_path=path)))
    out = bars.run_bars_daily({}, ctx)
    assert stored(path), "the bars were ingested after the universe was built"
    assert any("universe built on demand" in n for n in out.get("notes", [])), out.get("notes")


def test_universe_runs_carry_the_survivorship_note(tmp_path, monkeypatch):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        con.execute("INSERT INTO universe_members VALUES ('sp500', 'SPY', 'x', NULL, 't', ?)", [SURVIVORSHIP])
    monkeypatch.setattr(bars, "_now", lambda: datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    ctx = LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path, warehouse_path=path,
                                                  price_providers=["yahoo"])))
    assert bars.run_bars_daily({}, ctx)["survivorship"] == SURVIVORSHIP
    # completion task 3.6: the handler is the bars run with the exchange calendar's refresh after it
    assert HANDLERS["ingest.bars_daily"].__wrapped__ is bars.run_bars_daily


def test_the_nightly_bars_run_refreshes_the_calendar_and_a_failed_refresh_is_only_a_note(tmp_path, monkeypatch):
    """Completion task 3.6: a host without Alpaca keys refreshes no calendar and logs no failure for it (fix round
    1, M5: an announced weekday fallback is a note, not a fault lamp): the bars run says so in a note. A run for
    named tickers does not try."""
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        con.execute("INSERT INTO universe_members VALUES ('sp500', 'SPY', 'x', NULL, 't', ?)", [SURVIVORSHIP])
    monkeypatch.setattr(bars, "_now", lambda: datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    for var in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):        # a keyless host, whatever this machine has
        monkeypatch.delenv(var, raising=False)
    ctx = LocalContext(market=MarketData(Settings(offline=False, data_dir=tmp_path, warehouse_path=path,
                                                  price_providers=["yahoo"])))
    spec = HANDLERS["ingest.bars_daily"]({}, ctx)
    assert spec["data_asof"] is not None
    assert any("exchange_calendar not refreshed" in n and "no Alpaca keys" in n for n in spec["notes"]), spec["notes"]
    with open_ro(path) as con:
        assert con.execute("SELECT count(*) FROM ingest_log WHERE dataset = 'exchange_calendar'").fetchone()[0] == 0
    spec = HANDLERS["ingest.bars_daily"]({"tickers": ["SPY"]}, ctx)
    assert not any("exchange_calendar" in n for n in spec["notes"])


def test_a_bars_pass_killed_at_its_wall_limit_does_not_starve_the_calendar(tmp_path, monkeypatch):
    """Fix round 1, I3: the calendar is refreshed BEFORE the bars pass, so a pass killed part way (here: raising out
    of the loop, as a kill would end it) has already refreshed it."""
    from ohcamel_quant.warehouse.ingest import calendar as cal_ingest

    calls = []
    monkeypatch.setattr(cal_ingest, "run_calendar", lambda ctx: calls.append("calendar") or {
        "notes": [], "provenance": [], "tables": {}})

    class Killed(BaseException):
        pass

    def bars_killed(params, ctx):
        calls.append("bars")
        raise Killed

    with pytest.raises(Killed):
        cal_ingest.with_calendar(bars_killed)({}, LocalContext(market=MarketData(Settings(offline=True,
                                                                                            data_dir=tmp_path))))
    assert calls == ["calendar", "bars"]


# ------------------------------------------------------------------ raw bars (completion task 3.2)
def raw_rows(path, ticker="SPY"):
    with open_ro(path) as con:
        return con.execute("SELECT date, open, high, low, close, volume, source FROM bars_raw WHERE ticker = ? "
                           "ORDER BY date", [ticker]).df()


def with_splits(name, frame, splits):
    """A vendor whose provenance lists the splits its bars are adjusted for (what data/prices.py's Yahoo does)."""
    def fetch(ticker, start, end, settings):
        out = frame[frame.index >= pd.Timestamp(start)]
        return out.copy(), Provenance.now(name, symbol=ticker, splits=splits)
    return fetch


def test_yahoo_bars_are_stored_raw_too_by_multiplying_the_splits_back(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", with_splits("yahoo", SPY, [["2020-01-02", 2.0]]))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    raw = raw_rows(path)
    assert len(raw) == len(SPY) and set(raw["source"]) == {"yahoo"}
    before = pd.to_datetime(raw["date"]) < "2020-01-02"
    assert raw.loc[before, "close"].to_numpy() == pytest.approx(SPY.loc[SPY.index < "2020-01-02", "close"] * 2,
                                                                 rel=1e-15)
    assert raw.loc[before, "volume"].to_numpy() == pytest.approx(SPY.loc[SPY.index < "2020-01-02", "volume"] / 2,
                                                                  rel=1e-15)
    assert raw.loc[~before, "close"].to_numpy() == pytest.approx(SPY.loc[SPY.index >= "2020-01-02", "close"],
                                                                  rel=1e-15)
    assert "raw" in json.loads(last_log(path)[2])


def test_a_readjustment_replaces_bars_daily_but_keeps_the_raw_rows(setup, monkeypatch):
    ctx, path = setup
    early = SPY[SPY.index <= "2026-05-15"]
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", with_splits("yahoo", early, []))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    first = raw_rows(path)
    readj = SPY.copy()
    readj["adj_close"] *= 0.99                                   # a new dividend: bars_daily is replaced whole
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", with_splits("yahoo", readj, []))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    after = raw_rows(path)
    assert len(after) == len(SPY)                                # the new sessions added
    assert after.iloc[: len(first)].equals(first)                # every earlier raw row as it was


def test_alpaca_bars_store_alpacas_own_raw_pass(setup, monkeypatch):
    # CONSTRUCTED stand-in for Alpaca's adjustment=raw pass: the committed SPY bars with every price before
    # 2020-01-02 tripled, as if a 3:1 split fell there (SPY never split; only the plumbing is under test)
    ctx, path = setup
    raw_spy = SPY[["open", "high", "low", "close", "volume"]] * 1.0
    raw_spy.loc[raw_spy.index < "2020-01-02", ["open", "high", "low", "close"]] *= 3.0
    asked = []
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", failing("yahoo", "HTTP 429"))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", vendor("alpaca", SPY))
    monkeypatch.setitem(bars.RAW_FETCH, "alpaca",
                        lambda t, start, end, s: asked.append((t, start)) or raw_spy[raw_spy.index >= pd.Timestamp(start)])
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    raw = raw_rows(path)
    assert asked == [("SPY", prices.ALPACA_HISTORY_START)] and set(raw["source"]) == {"alpaca"}
    assert raw["close"].to_numpy() == pytest.approx(raw_spy["close"].to_numpy(), rel=1e-15)


def test_a_vendor_with_no_raw_prices_stores_none_and_says_why(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))      # no splits in its provenance
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    assert raw_rows(path).empty and stored(path)[0] == len(SPY)               # bars_daily unaffected
    assert json.loads(last_log(path)[2])["raw"].startswith("none")


def test_raw_missing_backfills_only_the_names_without_raw_rows(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", vendor("yahoo", SPY))
    bars.run_bars_daily({"tickers": ["SPY", "QQQ"]}, ctx)                     # bars, no raw
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", with_splits("yahoo", SPY, []))
    bars.run_bars_daily({"tickers": ["SPY"], "full": True}, ctx)               # SPY now has raw
    spy_raw = raw_rows(path)
    seen = []
    real = bars.fetch_bars
    monkeypatch.setattr(bars, "fetch_bars", lambda t, st, s, **kw: seen.append((t, kw["full"])) or real(t, st, s, **kw))
    bars.run_bars_daily({"tickers": ["SPY", "QQQ"], "raw_missing": True}, ctx)
    assert seen == [("QQQ", True)]                                             # resumable: only what is missing
    assert len(raw_rows(path, "QQQ")) == len(SPY) and raw_rows(path).equals(spy_raw)


# ------------------------------------------------------------------ fix round 1, I3: a failed raw pass never changes bars_daily
def broken_raw(exc):
    def fetch(t, start, end, s):
        raise exc
    return fetch


def test_a_raw_pass_that_breaks_keeps_the_full_fetchs_bars(setup, monkeypatch):
    ctx, path = setup
    monkeypatch.setattr(ctx.market, "settings", ctx.market.settings.model_copy(
        update={"price_providers": ["yahoo", "alpaca", "stooq"]}))
    stooq_calls = []
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", failing("yahoo", "HTTP 429"))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", vendor("alpaca", SPY))
    monkeypatch.setitem(prices.PROVIDERS, "stooq", vendor("stooq", SPY, stooq_calls))
    monkeypatch.setitem(bars.RAW_FETCH, "alpaca", broken_raw(KeyError("c")))      # not a DataUnavailable
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    status, _, detail = last_log(path)
    assert status == "ok" and stored(path)[2:] == ("alpaca", "alpaca") and stooq_calls == []
    assert raw_rows(path).empty and json.loads(detail)["raw"].startswith("none: alpaca raw pass failed (KeyError")


def test_a_raw_pass_that_breaks_keeps_the_incremental_bars(setup, monkeypatch):
    ctx, path = setup
    early = SPY[SPY.index <= "2026-05-15"]
    monkeypatch.setitem(prices.PROVIDERS, "yahoo", failing("yahoo", "HTTP 429"))
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", vendor("alpaca", early))
    monkeypatch.setitem(bars.RAW_FETCH, "alpaca", lambda t, start, end, s: early[["open", "high", "low", "close",
                                                                                    "volume"]])
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    full_calls = []
    monkeypatch.setitem(prices.PROVIDERS, "alpaca", vendor("alpaca", SPY, full_calls))
    monkeypatch.setitem(bars.RAW_FETCH, "alpaca", broken_raw(RuntimeError("socket closed")))
    bars.run_bars_daily({"tickers": ["SPY"]}, ctx)
    detail = json.loads(last_log(path)[2])
    assert detail["mode"] == "incremental" and len(full_calls) == 1          # one incremental call, no refetch
    assert stored(path)[:2] == (len(SPY), date(2026, 6, 1))
    assert detail["raw"].startswith("none: alpaca raw pass failed (RuntimeError")
    assert len(raw_rows(path)) == len(early)                                 # the earlier raw rows stay


def test_fetch_alpaca_raw_asks_for_unadjusted_bars_and_refuses_a_malformed_page(tmp_path, monkeypatch):
    """CONSTRUCTED pages in the shape Alpaca documents for GET /v2/stocks/{sym}/bars (bars of t, o, h, l, c, v and
    next_page_token); no real raw page is committed (no keys on the machine that wrote this)."""
    import httpx
    import respx

    from ohcamel_quant.data import http

    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()
    monkeypatch.setattr(prices, "_now", lambda: datetime(2026, 6, 2, 23, 0, tzinfo=UTC))
    s = Settings(offline=False, data_dir=tmp_path, http_retries=0, APCA_API_KEY_ID="k", APCA_API_SECRET_KEY="s")
    page1 = {"bars": [{"t": "2020-08-28T04:00:00Z", "o": 504.05, "h": 505.77, "l": 498.31, "c": 499.23, "v": 46907479}],
             "next_page_token": "p2"}
    page2 = {"bars": [{"t": "2020-08-31T04:00:00Z", "o": 127.58, "h": 131.0, "l": 126.0, "c": 129.04, "v": 225702700}],
             "next_page_token": None}
    seen = []

    def answer(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=page2 if request.url.params.get("page_token") == "p2" else page1)

    with respx.mock() as router:
        router.get(f"{prices.ALPACA_BASE}/stocks/AAPL/bars").mock(side_effect=answer)
        raw = prices.fetch_alpaca_raw("AAPL", date(2020, 8, 1), None, s)
    assert {p["adjustment"] for p in seen} == {"raw"} and seen[0]["start"] == "2020-08-01"
    assert raw["close"].tolist() == [499.23, 129.04] and raw["volume"].tolist() == [46907479.0, 225702700.0]
    assert list(raw.index) == [pd.Timestamp("2020-08-28"), pd.Timestamp("2020-08-31")]
    bad = {"bars": [{"t": "2020-08-28T04:00:00Z", "o": 504.05}], "next_page_token": None}     # no h, l, c, v
    with respx.mock() as router:
        router.get(f"{prices.ALPACA_BASE}/stocks/AAPL/bars").mock(return_value=httpx.Response(200, json=bad))
        with pytest.raises(KeyError):
            prices.fetch_alpaca_raw("AAPL", date(2020, 8, 1), None, s)
