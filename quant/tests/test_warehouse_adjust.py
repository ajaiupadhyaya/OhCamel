"""Task 3.2: the warehouse keeps RAW bars and the corporate actions, and derives the adjusted series.

The unit cases are the brief's, verbatim; the round trip runs on three real Yahoo responses committed under
tests/data (provenance in each sidecar): AAPL from 2020 (a 4:1 split, 28 dividends), NVDA from 2024 (a 10:1
split, 11 dividends of a cent or less) and XLK from mid-2024 (the 2:1 split of 2025-12-05 that the committed
Alpaca fixture never absorbed, 10 dividends). No test touches the network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.warehouse import adjust

DATA = Path(__file__).parent / "data"
YAHOO = {s: DATA / f"yahoo_chart_{s}_2026-11-17.json.gz" for s in ("AAPL", "NVDA", "XLK")}


def raw_frame(closes, volume=1000.0, start="2024-01-02"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": volume},
                        index=pd.DatetimeIndex(idx, name="date"))


def actions(*rows):
    return pd.DataFrame([{"ex_date": pd.Timestamp(d), "kind": k, "ratio": r, "cash": c} for d, k, r, c in rows],
                        columns=["ex_date", "kind", "ratio", "cash"])


NONE = actions()


# ------------------------------------------------------------------ the brief's cases
def test_a_two_for_one_split_halves_prior_raw_closes_and_doubles_prior_volume():
    raw = raw_frame([100.0, 102.0, 51.0, 52.0], volume=[1000.0, 1100.0, 2400.0, 2500.0])
    acts = actions(("2024-01-04", "split", 2.0, None))
    for div in (False, True):
        out = adjust.series(raw, acts, dividends=div)
        assert out["close"].tolist() == [50.0, 51.0, 51.0, 52.0]
        assert out["volume"].tolist() == [2000.0, 2200.0, 2400.0, 2500.0]
        assert out["open"].tolist() == [50.0, 51.0, 51.0, 52.0]
        assert out["high"].iloc[:2].tolist() == pytest.approx([50.5, 51.51], rel=1e-15)


def test_a_fifty_cent_dividend_on_a_hundred_dollar_close_scales_prior_closes_by_0995():
    raw = raw_frame([99.0, 100.0, 99.6, 100.2])
    acts = actions(("2024-01-04", "dividend", None, 0.50))
    out = adjust.series(raw, acts, dividends=True)
    assert out["close"].tolist() == pytest.approx([99.0 * 0.995, 100.0 * 0.995, 99.6, 100.2], rel=1e-15)
    assert out["low"].iloc[0] == pytest.approx(99.0 * 0.99 * 0.995, rel=1e-15)
    assert out["volume"].tolist() == raw["volume"].tolist()      # a dividend never touches volume
    kept = adjust.series(raw, acts, dividends=False)
    assert kept["close"].tolist() == raw["close"].tolist()


def test_the_same_action_twice_is_refused():
    raw = raw_frame([100.0, 102.0, 51.0, 52.0])
    twice = actions(("2024-01-04", "split", 2.0, None), ("2024-01-04", "split", 2.0, None))
    with pytest.raises(ValueError, match="twice"):
        adjust.series(raw, twice, dividends=False)


def _yahoo(symbol):
    from ohcamel_quant.data import prices
    from ohcamel_quant.warehouse.ingest import bars, corp_actions

    payload = json.loads(gzip.decompress(YAHOO[symbol].read_bytes()))
    vendor, info = prices.parse_yahoo_chart(payload, drop_in_progress=False)
    raw = bars.raw_from_split_adjusted(vendor, info["splits"])
    acts = corp_actions.parse_yahoo_events(payload)
    return vendor, raw, acts


@pytest.mark.parametrize("symbol", sorted(YAHOO))
def test_the_derived_series_equals_the_committed_vendor_series_within_1e6(symbol):
    vendor, raw, acts = _yahoo(symbol)
    assert (acts["kind"] == "split").sum() == 1 and (acts["kind"] == "dividend").sum() >= 10
    full = adjust.series(raw, acts, dividends=True)
    rel = (full["close"] / vendor["adj_close"] - 1.0).abs()
    assert rel.max() < 1e-6, (symbol, rel.max(), rel.idxmax())
    split_only = adjust.series(raw, acts, dividends=False)
    for col in ("open", "high", "low", "close", "volume"):
        r = (split_only[col] / vendor[col] - 1.0).abs()
        assert r.max() < 1e-12, (symbol, col, r.max())
    # the raw prices really are raw: the split is visible in them as an overnight jump of its ratio
    (ex,) = acts.loc[acts["kind"] == "split", "ex_date"]
    ratio = float(acts.loc[acts["kind"] == "split", "ratio"].iloc[0])
    before = raw.index[raw.index < ex][-1]
    assert raw.loc[before, "close"] / raw.loc[ex, "open"] == pytest.approx(ratio, rel=0.05)


# ------------------------------------------------------------------ the convention's edges
def test_actions_after_asof_are_not_used():
    raw = raw_frame([100.0, 102.0, 51.0, 52.0, 52.5])
    acts = actions(("2024-01-04", "split", 2.0, None), ("2024-01-08", "dividend", None, 0.26))
    out = adjust.series(raw, acts, dividends=True, asof="2024-01-05")
    assert out.index[-1] == pd.Timestamp("2024-01-05")
    assert out["close"].tolist() == [50.0, 51.0, 51.0, 52.0]            # the dividend after asof is unseen


def test_a_dividend_and_a_later_split_compose_in_either_order():
    # cash is as paid: 1.00 per pre-split share on 01-04; the 2:1 split on 01-08 halves it to 0.50 per share now
    raw = raw_frame([100.0, 100.0, 99.0, 99.0, 49.5])
    acts = actions(("2024-01-04", "dividend", None, 1.0), ("2024-01-08", "split", 2.0, None))
    out = adjust.series(raw, acts, dividends=True)
    assert out["close"].tolist() == pytest.approx([49.5, 49.5, 49.5, 49.5, 49.5], rel=1e-15)
    shuffled = adjust.series(raw, acts.iloc[::-1], dividends=True)
    assert shuffled.equals(out)


def test_a_reverse_split_multiplies_prior_prices():
    raw = raw_frame([1.0, 1.1, 10.5], volume=[1e6, 1e6, 1e5])
    out = adjust.series(raw, actions(("2024-01-04", "split", 0.1, None)), dividends=False)
    assert out["close"].tolist() == pytest.approx([10.0, 11.0, 10.5], rel=1e-15)
    assert out["volume"].tolist() == pytest.approx([1e5, 1e5, 1e5], rel=1e-15)


def test_an_ex_date_on_a_shut_market_uses_the_last_session_before_it():
    raw = raw_frame([100.0, 100.0, 99.0], start="2024-01-04")      # Thu, Fri, Mon
    out = adjust.series(raw, actions(("2024-01-06", "dividend", None, 1.0)), dividends=True)   # a Saturday
    assert out["close"].tolist() == pytest.approx([99.0, 99.0, 99.0], rel=1e-15)


def test_a_dividend_before_the_first_bar_adjusts_nothing_and_an_impossible_one_is_refused():
    raw = raw_frame([100.0, 101.0])
    assert adjust.series(raw, actions(("2024-01-02", "dividend", None, 1.0)), dividends=True).equals(
        adjust.series(raw, NONE, dividends=True))
    with pytest.raises(ValueError, match="no positive factor"):
        adjust.series(raw, actions(("2024-01-03", "dividend", None, 100.0)), dividends=True)


def test_bars_has_the_shape_of_bars_daily():
    raw = raw_frame([99.0, 100.0, 49.8, 50.1])
    acts = actions(("2024-01-04", "split", 2.0, None), ("2024-01-04", "dividend", None, 0.25))
    out = adjust.bars(raw, acts)
    assert list(out.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert out["close"].tolist() == [49.5, 50.0, 49.8, 50.1]
    # 0.25 per post-split share against a pre-split close of 100 (50 per share now): factor 0.995
    assert out["adj_close"].tolist() == pytest.approx([49.5 * 0.995, 50.0 * 0.995, 49.8, 50.1], rel=1e-15)


# ------------------------------------------------------------------ ingest.corp_actions (Yahoo events; respx, no network)
def _payload(symbol):
    return json.loads(gzip.decompress(YAHOO[symbol].read_bytes()))


@pytest.fixture
def wh(tmp_path, monkeypatch):
    import httpx  # noqa: F401  (respx patches it)

    from ohcamel_quant.config import Settings
    from ohcamel_quant.data import http
    from ohcamel_quant.data.market import MarketData
    from ohcamel_quant.warehouse.ingest import corp_actions
    from ohcamel_quant.warehouse.ingest.base import LocalContext

    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()
    from datetime import UTC, datetime
    monkeypatch.setattr(corp_actions, "_now", lambda: datetime(2026, 11, 17, 23, 30, tzinfo=UTC))   # 18:30 EST
    s = Settings(offline=False, data_dir=tmp_path, http_retries=0, warehouse_path=tmp_path / "w.duckdb")
    return LocalContext(market=MarketData(s)), tmp_path / "w.duckdb"


def _serve(router, payloads, seen=None):
    import httpx

    from ohcamel_quant.data import prices

    def answer(request):
        sym = request.url.path.rsplit("/", 1)[-1]
        if seen is not None:
            seen.append((sym, dict(request.url.params)))
        body = payloads.get(sym)
        if body is None:
            return httpx.Response(500, text="upstream error")
        return httpx.Response(200, json=body)

    router.get(url__startswith=prices.YAHOO_CHART).mock(side_effect=answer)


def _stored(path, ticker=None):
    from ohcamel_quant.warehouse.db import open_ro

    with open_ro(path) as con:
        sql = "SELECT ticker, CAST(ex_date AS VARCHAR), kind, ratio, cash, source, fetched_at FROM corp_actions"
        args = []
        if ticker:
            sql += " WHERE ticker = ?"
            args.append(ticker)
        rows = con.execute(sql + " ORDER BY ticker, ex_date, kind", args).fetchall()
        revs = con.execute("SELECT ticker, CAST(ex_date AS VARCHAR), kind, old_ratio, old_cash, new_ratio, new_cash "
                           "FROM corp_action_revisions ORDER BY ticker, ex_date").fetchall()
    return rows, revs


def test_corp_actions_ingest_stores_each_action_once_with_cash_as_paid(wh):
    import respx

    from ohcamel_quant.warehouse.ingest import HANDLERS, corp_actions

    ctx, path = wh
    with respx.mock() as router:
        _serve(router, {"AAPL": _payload("AAPL")})
        spec = corp_actions.run_corp_actions({"tickers": ["aapl"], "full": True}, ctx)
        rows, revs = _stored(path)
        assert len(rows) == 29 and revs == []
        by = {(d, k): (r, c) for _, d, k, r, c, _, _ in rows}
        assert by[("2020-08-31", "split")] == (4.0, None)
        assert by[("2020-08-07", "dividend")][1] == pytest.approx(0.82, rel=1e-12)   # 0.205 a share now, x4
        assert by[("2020-11-06", "dividend")][1] == pytest.approx(0.205, rel=1e-12)
        assert {r[5] for r in rows} == {"yahoo"} and all(r[6] is not None for r in rows)
        assert spec["data_asof"] == "2026-11-17" and spec["provenance"][0]["source"] == "yahoo"
        corp_actions.run_corp_actions({"tickers": ["AAPL"], "full": True}, ctx)          # the same actions again
        again, revs = _stored(path)
        assert again == rows and revs == []                                               # one row each, unchanged
    assert HANDLERS["ingest.corp_actions"] is corp_actions.run_corp_actions


def test_the_nightly_window_is_short_and_full_reaches_1990(wh):
    import respx

    from ohcamel_quant.warehouse.ingest import corp_actions

    ctx, path = wh
    seen = []
    with respx.mock() as router:
        _serve(router, {"XLK": _payload("XLK")}, seen)
        corp_actions.run_corp_actions({"tickers": ["XLK"]}, ctx)
        corp_actions.run_corp_actions({"tickers": ["XLK"], "full": True}, ctx)
    nightly, full = seen
    day = 86400
    assert int(nightly[1]["period1"]) == int(pd.Timestamp("2026-11-17").timestamp()) - corp_actions.LOOKBACK_DAYS * day
    assert int(full[1]["period1"]) == int(pd.Timestamp("1990-01-01").timestamp())
    assert nightly[1]["interval"] == full[1]["interval"] == "1mo" and full[1]["events"] == "div|split"


def test_a_revised_or_withdrawn_action_is_kept_on_record(wh):
    import copy

    import respx

    from ohcamel_quant.warehouse.ingest import corp_actions

    ctx, path = wh
    first = _payload("XLK")
    with respx.mock() as router:
        _serve(router, {"XLK": first})
        corp_actions.run_corp_actions({"tickers": ["XLK"], "full": True}, ctx)
    before, _ = _stored(path)
    revised = copy.deepcopy(first)
    divs = revised["chart"]["result"][0]["events"]["dividends"]
    keys = sorted(divs, key=lambda k: divs[k]["date"])
    divs[keys[-1]]["amount"] = round(divs[keys[-1]]["amount"] + 0.01, 6)       # the latest dividend restated
    del divs[keys[-2]]                                                         # the one before no longer listed
    with respx.mock() as router:
        _serve(router, {"XLK": revised})
        spec = corp_actions.run_corp_actions({"tickers": ["XLK"], "full": True}, ctx)
    after, revs = _stored(path)
    assert len(after) == len(before)                                          # the withdrawn row is kept
    (rev_new, rev_gone) = sorted(revs, key=lambda r: r[1], reverse=True)
    assert rev_new[2] == "dividend" and rev_new[6] == pytest.approx(rev_new[4] + 0.01, rel=1e-9)
    assert rev_gone[5] is None and rev_gone[6] is None and rev_gone[4] is not None
    assert any("revised" in n and "withdrawn" in n for n in spec["notes"]), spec["notes"]


def test_a_failing_vendor_fails_the_job_and_writes_nothing(wh):
    import respx

    from ohcamel_quant.warehouse.ingest import corp_actions
    from ohcamel_quant.warehouse.ingest.base import IngestFailed

    ctx, path = wh
    with respx.mock() as router:
        _serve(router, {})                                                     # HTTP 500 for every ticker
        with pytest.raises(IngestFailed, match="corp_actions: all 2 keys failed"):
            corp_actions.run_corp_actions({"tickers": ["AAPL", "XLK"]}, ctx)
    assert _stored(path) == ([], [])
    empty = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
    with respx.mock() as router:
        _serve(router, {"AAPL": _payload("AAPL"), "XLK": empty})
        spec = corp_actions.run_corp_actions({"tickers": ["AAPL", "XLK"], "full": True}, ctx)
    assert {r[0] for r in _stored(path)[0]} == {"AAPL"}                        # XLK: nothing, and said so
    assert any("XLK" in n and "No data found" in n for n in spec["notes"]), spec["notes"]


# ------------------------------------------------------------------ the backfill on a warehouse, and ops.data_integrity's adjust_drift
@pytest.fixture
def backfilled(tmp_path, monkeypatch):
    """A warehouse filled the way the owner's backfill fills the live one: ingest.bars_daily (Yahoo first for
    the 1990 window) and ingest.corp_actions {"full": true}, both answered by the committed Yahoo responses."""
    from datetime import UTC, datetime

    import respx

    from ohcamel_quant.config import Settings
    from ohcamel_quant.data import http, prices
    from ohcamel_quant.data.market import MarketData
    from ohcamel_quant.warehouse.ingest import bars, corp_actions
    from ohcamel_quant.warehouse.ingest.base import LocalContext

    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()
    when = datetime(2026, 11, 17, 23, 30, tzinfo=UTC)                     # 18:30 EST, after the close
    for mod in (prices, bars, corp_actions):
        monkeypatch.setattr(mod, "_now", lambda: when)
    path = tmp_path / "w.duckdb"
    s = Settings(offline=False, data_dir=tmp_path, http_retries=0, warehouse_path=path, price_providers=["yahoo"])
    lctx = LocalContext(market=MarketData(s))
    with respx.mock() as router:
        _serve(router, {sym: _payload(sym) for sym in YAHOO})
        corp_actions.run_corp_actions({"tickers": sorted(YAHOO), "full": True}, lctx)
        bars.run_bars_daily({"tickers": sorted(YAHOO)}, lctx)
    return path, s


def _integrity(path, settings, params=None):
    from ohcamel_quant.data.market import MarketData
    from ohcamel_quant.jobs.context import JobContext
    from ohcamel_quant.products import data_integrity as DI

    c = JobContext(job_id="t", params=params or {}, threads=1, progress_fn=lambda f, m: None,
                   cancelled_fn=lambda: False, _market=MarketData(settings))
    try:
        return DI.run(params or {}, c)
    finally:
        c.close()


def _rw(path, sql, args=()):
    from ohcamel_quant.warehouse.db import open_rw

    with open_rw(path) as con:
        return con.execute(sql, list(args)).fetchall()


def test_the_backfilled_warehouse_derives_the_vendors_adjusted_close(backfilled):
    from ohcamel_quant.warehouse import adjust
    from ohcamel_quant.warehouse.db import open_ro

    path, settings = backfilled
    with open_ro(path) as con:
        for sym in YAHOO:
            vendor = con.execute("SELECT date, adj_close FROM bars_daily WHERE ticker = ? ORDER BY date", [sym]).df()
            raw = adjust.read_raw(con, sym)
            derived = adjust.series(raw, adjust.read_actions(con, sym), dividends=True)["close"]
            assert len(raw) == len(vendor) and set(raw["source"]) == {"yahoo"}
            rel = np.abs(derived.to_numpy() / vendor["adj_close"].to_numpy() - 1.0)
            assert rel.max() < 1e-6, (sym, rel.max())
    spec = _integrity(path, settings)
    f, summary = spec.tables["findings"], spec.tables["summary"].iloc[0]
    assert "adjust_drift" not in set(f["check"]) and "split_unconfirmed" not in set(f["check"])
    assert (summary["adjust_compared"], summary["n_adjust_drift"], summary["adjust_no_raw"],
            summary["adjust_no_actions"]) == (3, 0, 0, 0)
    assert spec.verdict == "PASS", f


def test_a_missing_dividend_is_reported_where_it_bites(backfilled):
    path, settings = backfilled
    ((ex,),) = _rw(path, "SELECT max(ex_date) FROM corp_actions WHERE ticker = 'XLK' AND kind = 'dividend'")
    _rw(path, "DELETE FROM corp_actions WHERE ticker = 'XLK' AND kind = 'dividend' AND ex_date = ?", [ex])
    spec = _integrity(path, settings)
    f = spec.tables["findings"]
    (row,) = f[f["check"] == "adjust_drift"].itertuples()
    ((before,),) = _rw(path, "SELECT max(date) FROM bars_daily WHERE ticker = 'XLK' AND date < ?", [ex])
    assert (row.ticker, row.date, row.source) == ("XLK", str(before), "warehouse")
    assert "beyond 1e-06" in row.detail and str(before) in row.detail
    assert row.gating == False and spec.verdict == "PASS"                    # noqa: E712  months old: listed only
    recent = _integrity(path, settings, {"recent_bdays": 60})
    assert recent.verdict == "FAIL" and "XLK" in recent.verdict_detail     # inside the window: it fails the lamp


def test_day_one_says_what_is_missing_instead_of_flooding(backfilled):
    path, settings = backfilled
    _rw(path, "DELETE FROM bars_raw WHERE ticker = 'NVDA'")                                   # raw not backfilled
    _rw(path, "DELETE FROM corp_actions WHERE ticker = 'AAPL'")
    _rw(path, "DELETE FROM ingest_log WHERE dataset = 'corp_actions' AND key = 'AAPL'")        # actions not backfilled
    spec = _integrity(path, settings)
    f, summary = spec.tables["findings"], spec.tables["summary"].iloc[0]
    assert "adjust_drift" not in set(f["check"])
    assert (summary["adjust_compared"], summary["adjust_no_raw"], summary["adjust_no_actions"]) == (1, 1, 1)
    assert any("no raw bars yet" in n and "NVDA" in n for n in spec.notes), spec.notes
    assert any("no corporate-action history" in n and "AAPL" in n for n in spec.notes), spec.notes


def test_drift_findings_are_capped_and_counted(backfilled, monkeypatch):
    from ohcamel_quant.products import data_integrity as DI

    path, settings = backfilled
    for sym in ("XLK", "NVDA", "AAPL"):
        _rw(path, "DELETE FROM corp_actions WHERE ticker = ? AND kind = 'dividend' AND ex_date = "
                  "(SELECT max(ex_date) FROM corp_actions WHERE ticker = ? AND kind = 'dividend')", [sym, sym])
    monkeypatch.setattr(DI, "DRIFT_LISTED", 2)
    spec = _integrity(path, settings)
    f, summary = spec.tables["findings"], spec.tables["summary"].iloc[0]
    assert (f["check"] == "adjust_drift").sum() == 2 and summary["n_adjust_drift"] == 3
    assert any("1 more series" in n for n in spec.notes), spec.notes


def test_a_split_suspect_with_no_recorded_split_is_named(backfilled):
    path, settings = backfilled
    # NVDA's bars as a vendor that never absorbed the 10:1 split would serve them
    ((ex,),) = _rw(path, "SELECT ex_date FROM corp_actions WHERE ticker = 'NVDA' AND kind = 'split'")
    _rw(path, "UPDATE bars_daily SET open = open * 10, high = high * 10, low = low * 10, close = close * 10, "
              "adj_close = adj_close * 10 WHERE ticker = 'NVDA' AND date < ?", [ex])
    spec = _integrity(path, settings)
    f = spec.tables["findings"]
    assert set(f.loc[(f["ticker"] == "NVDA") & (f["check"] == "split_suspect"), "date"]) == {str(ex)}
    assert "split_unconfirmed" not in set(f["check"])                     # corp_actions records it: confirmed
    _rw(path, "DELETE FROM corp_actions WHERE ticker = 'NVDA' AND kind = 'split'")
    f = _integrity(path, settings).tables["findings"]
    (row,) = f[f["check"] == "split_unconfirmed"].itertuples()
    assert (row.ticker, row.date) == ("NVDA", str(ex)) and "no split" in row.detail


def test_the_full_backfill_resumes_where_it_stopped(wh):
    import respx

    from ohcamel_quant.warehouse.ingest import corp_actions

    ctx, path = wh
    seen = []
    with respx.mock() as router:
        _serve(router, {"AAPL": _payload("AAPL"), "XLK": _payload("XLK")}, seen)
        corp_actions.run_corp_actions({"tickers": ["AAPL"], "full": True}, ctx)        # killed after AAPL
        corp_actions.run_corp_actions({"tickers": ["AAPL", "XLK"]}, ctx)               # a nightly run: not a backfill
        seen.clear()
        corp_actions.run_corp_actions({"tickers": ["AAPL", "XLK"], "full": True, "missing": True}, ctx)
        assert [s for s, _ in seen] == ["XLK"]
        out = corp_actions.run_corp_actions({"tickers": ["AAPL", "XLK"], "full": True, "missing": True}, ctx)
        assert out["notes"] == ["every ticker already has a full fetch on record"]


def test_two_events_of_a_kind_on_one_session_refuse_the_response():
    from ohcamel_quant.data.base import DataUnavailable
    from ohcamel_quant.warehouse.ingest import corp_actions

    two = {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"}, "events": {"dividends": {
        "a": {"amount": 0.1, "date": 1704205800}, "b": {"amount": 0.2, "date": 1704210000}}}}]}}   # both 2024-01-02
    with pytest.raises(DataUnavailable, match="more than one event"):
        corp_actions.parse_yahoo_events(two)
