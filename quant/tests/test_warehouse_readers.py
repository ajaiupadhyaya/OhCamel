"""Warehouse readers return MarketData's own shapes; MarketData.returns reads
the warehouse first when every ticker is present, fresh and covers the window,
and otherwise falls back to the providers with a provenance note (compute plan C1)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from test_warehouse_db import hold_writer

from ohcamel_quant.config import Settings
from ohcamel_quant.data import fixtures
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.warehouse import freshness, readers
from ohcamel_quant.warehouse.db import open_ro, open_rw
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
from ohcamel_quant.warehouse.ingest.base import log_row

DUE = freshness.VENDOR_DUE  # 16:20 New York
# First session stored for both SPY and QQQ. The fixture warehouse records no
# requested_start, so it covers windows from here on and nothing earlier.
FIRST = max(fixtures.history(t)[0].index[0].date() for t in ("SPY", "QQQ"))


@pytest.fixture
def wh(tmp_path):
    return build_fixture_warehouse(tmp_path / "wh.duckdb")


def market(tmp_path, path) -> MarketData:
    return MarketData(Settings(offline=True, data_dir=tmp_path, warehouse_path=path))


def pin(monkeypatch, utc: datetime) -> None:
    monkeypatch.setattr(freshness, "_now", lambda: utc)


# ---------------------------------------------------------------- session clock
@pytest.mark.parametrize("utc, expected", [
    # Mon 2026-10-05 10:00 EDT (UTC-4): today's 16:20 not reached -> Fri 2026-10-02
    (datetime(2026, 10, 5, 14, 0, tzinfo=UTC), date(2026, 10, 2)),
    # Mon 2026-10-05 16:25 EDT -> today
    (datetime(2026, 10, 5, 20, 25, tzinfo=UTC), date(2026, 10, 5)),
    # Sun 2026-10-04 08:00 EDT -> Fri 2026-10-02
    (datetime(2026, 10, 4, 12, 0, tzinfo=UTC), date(2026, 10, 2)),
    # Sat 2026-10-03 03:00 UTC = Fri 23:00 EDT -> Fri 2026-10-02
    (datetime(2026, 10, 3, 3, 0, tzinfo=UTC), date(2026, 10, 2)),
    # DST ends 2026-11-01: Mon 2026-11-02 21:25 UTC = 16:25 EST (UTC-5) -> today
    (datetime(2026, 11, 2, 21, 25, tzinfo=UTC), date(2026, 11, 2)),
    # Mon 2026-11-02 20:25 UTC = 15:25 EST -> Fri 2026-10-30
    (datetime(2026, 11, 2, 20, 25, tzinfo=UTC), date(2026, 10, 30)),
])
def test_expected_session(utc, expected):
    assert freshness.expected_session(utc, DUE) == expected


def test_is_fresh_rules():
    tue = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)  # Tue 10:00 EDT: expected session Mon 2026-10-05
    assert freshness.is_fresh(date(2026, 10, 5), None, tue, DUE)
    assert not freshness.is_fresh(date(2026, 10, 2), None, tue, DUE)
    # One session behind, but the last good ingest ran after Mon's 16:20 and found nothing
    # newer (an exchange holiday): Mon 17:00 EDT = 21:00 UTC >= 20:20 UTC.
    assert freshness.is_fresh(date(2026, 10, 2), datetime(2026, 10, 5, 21, 0, tzinfo=UTC), tue, DUE)
    # ... but not when that ingest ran before the session was due (15:00 EDT < 16:20).
    assert not freshness.is_fresh(date(2026, 10, 2), datetime(2026, 10, 5, 19, 0, tzinfo=UTC), tue, DUE)
    # Two sessions behind is stale whatever the log says.
    assert not freshness.is_fresh(date(2026, 10, 1), datetime(2026, 10, 5, 21, 0, tzinfo=UTC), tue, DUE)
    assert not freshness.is_fresh(None, None, tue, DUE)


# ---------------------------------------------------------------- readers
def test_ohlcv_matches_the_fixture_shape_exactly(wh):
    want, prov = fixtures.history("SPY")
    with open_ro(wh) as con:
        ds = readers.ohlcv(con, "spy", path=str(wh))
    assert list(ds.data.columns) == readers.BAR_COLUMNS and ds.data.index.name == "date"
    assert ds.data.index.dtype == "datetime64[ns]"
    np.testing.assert_array_equal(ds.data.index.values, want.index.values.astype("datetime64[ns]"))
    np.testing.assert_array_equal(ds.data.to_numpy(), want.to_numpy())  # float64 round-trips exactly
    assert ds.provenance[0].source == "warehouse:" + prov.source  # "warehouse:fixture:alpaca"


def test_returns_read_warehouse_when_fresh(tmp_path, wh, monkeypatch):
    # Fixture bars end Mon 2026-06-01; Tue 2026-06-02 10:00 EDT expects session 06-01 -> fresh.
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    got = market(tmp_path, wh).returns(["SPY", "QQQ"], start=FIRST)
    ref = MarketData(Settings(offline=True, data_dir=tmp_path)).returns(["SPY", "QQQ"], start=FIRST)
    assert [p.source for p in got.provenance] == ["warehouse:fixture:alpaca"] * 2
    np.testing.assert_array_equal(got.data.index.values.astype("datetime64[ns]"),
                                  ref.data.index.values.astype("datetime64[ns]"))
    np.testing.assert_allclose(got.data.to_numpy(), ref.data.to_numpy(), rtol=1e-12, atol=0)


def test_returns_fall_back_when_stale(tmp_path, wh, monkeypatch):
    # Wed 2026-06-10 10:00 EDT expects Tue 06-09; data ends 06-01, more than one session behind.
    pin(monkeypatch, datetime(2026, 6, 10, 14, 0, tzinfo=UTC))
    ds = market(tmp_path, wh).returns(["SPY"], start=FIRST)
    assert ds.provenance[0].source == "fixture:alpaca"
    note = ds.provenance[-1]
    assert note.source == "derived" and "stale" in note.detail["note"] and "SPY" in note.detail["note"]


def test_historical_window_is_served_even_when_stale(tmp_path, wh, monkeypatch):
    pin(monkeypatch, datetime(2026, 6, 10, 14, 0, tzinfo=UTC))
    ds = market(tmp_path, wh).returns(["SPY"], start=FIRST, end=date(2026, 5, 1))  # data reach 06-01 >= 05-01
    assert ds.provenance[0].source == "warehouse:fixture:alpaca"


def test_returns_fall_back_when_a_ticker_is_missing(tmp_path, wh, monkeypatch):
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    with open_rw(wh) as con:
        con.execute("DELETE FROM bars_daily WHERE ticker = 'QQQ'")
    ds = market(tmp_path, wh).returns(["SPY", "QQQ"], start=FIRST)
    assert ds.provenance[0].source == "fixture:alpaca"
    assert "not in warehouse: QQQ" in ds.provenance[-1].detail["note"]


def test_returns_fall_back_when_warehouse_locked(tmp_path, wh, monkeypatch):
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    monkeypatch.setattr(readers, "RO_TIMEOUT_S", 0.3)
    with hold_writer(wh, tmp_path):
        ds = market(tmp_path, wh).returns(["SPY"], start=FIRST)
    assert ds.provenance[0].source == "fixture:alpaca"
    assert "locked" in ds.provenance[-1].detail["note"]


def test_returns_fall_back_when_start_precedes_stored_history(tmp_path, wh, monkeypatch):
    # An explicit start before the first stored session must not be silently truncated.
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    early = FIRST - timedelta(days=365)
    ds = market(tmp_path, wh).returns(["SPY"], start=early)
    ref = MarketData(Settings(offline=True, data_dir=tmp_path)).returns(["SPY"], start=early)
    assert ds.provenance[0].source == "fixture:alpaca"
    note = ds.provenance[-1].detail["note"]
    assert "window" in note and "SPY" in note and str(early) in note
    np.testing.assert_array_equal(ds.data.index.values, ref.data.index.values)


def test_default_start_falls_back_the_same_way(tmp_path, wh, monkeypatch):
    # start=None means provider max history (api/models.py), i.e. prices.DEFAULT_START.
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    ds = market(tmp_path, wh).returns(["SPY"])
    assert ds.provenance[0].source == "fixture:alpaca"
    note = ds.provenance[-1].detail["note"]
    assert "window" in note and "1990-01-01" in note and "SPY" in note


def _record_full_fetch(wh, ticker, requested_start):
    """What C3's full fetch logs: the start the serving vendor was asked for."""
    with open_rw(wh) as con:
        log_row(con, "bars_daily", ticker, "ok", 0,
                json.dumps({"mode": "full", "requested_start": requested_start}),
                date(2026, 6, 1), ran_at=datetime(2026, 6, 1, 21, 0))


def test_full_history_fetch_serves_the_default_window(tmp_path, wh, monkeypatch):
    # The vendor was asked from 1990 and its history starts at FIRST: nothing earlier exists.
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    _record_full_fetch(wh, "SPY", "1990-01-01")
    assert market(tmp_path, wh).returns(["SPY"]).provenance[0].source == "warehouse:fixture:alpaca"
    assert market(tmp_path, wh).returns(["SPY"], start=date(2000, 1, 3)).provenance[0].source \
        == "warehouse:fixture:alpaca"


def test_short_requested_start_covers_only_its_own_window(tmp_path, wh, monkeypatch):
    # Alpaca is asked from 2016-01-01 at the earliest; that does not prove 1990..2016 is empty.
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    _record_full_fetch(wh, "SPY", "2016-01-01")
    assert "window" in market(tmp_path, wh).returns(["SPY"]).provenance[-1].detail["note"]
    assert market(tmp_path, wh).returns(["SPY"], start=date(2016, 1, 4)).provenance[0].source \
        == "warehouse:fixture:alpaca"


def test_no_warehouse_configured_changes_nothing(tmp_path):
    ds = MarketData(Settings(offline=True, data_dir=tmp_path)).returns(["SPY", "QQQ"])
    assert [p.source for p in ds.provenance] == ["fixture:alpaca", "fixture:alpaca"]


# ---------------------------------------------------------------- prices, warehouse first (opt-in)
def test_prices_warehouse_first_serves_the_warehouse(tmp_path, wh, monkeypatch):
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    got = market(tmp_path, wh).prices(["SPY", "QQQ"], start=FIRST, warehouse_first=True)
    ref = MarketData(Settings(offline=True, data_dir=tmp_path)).prices(["SPY", "QQQ"], start=FIRST)
    assert [p.source for p in got.provenance] == ["warehouse:fixture:alpaca"] * 2
    assert list(got.data.columns) == ["SPY", "QQQ"] and got.data.index.name == "date"
    np.testing.assert_allclose(got.data.to_numpy(), ref.data.to_numpy(), rtol=1e-12, atol=0)


def test_prices_warehouse_first_falls_back_with_a_note(tmp_path, wh, monkeypatch):
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    with open_rw(wh) as con:
        con.execute("DELETE FROM bars_daily WHERE ticker = 'QQQ'")
    ds = market(tmp_path, wh).prices(["SPY", "QQQ"], start=FIRST, warehouse_first=True)
    assert ds.provenance[0].source == "fixture:alpaca"
    assert "not in warehouse: QQQ" in ds.provenance[-1].detail["note"]


def test_prices_default_still_reads_the_providers(tmp_path, wh, monkeypatch):
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    ds = market(tmp_path, wh).prices(["SPY"], start=FIRST)
    assert [p.source for p in ds.provenance] == ["fixture:alpaca"]


# ---------------------------------------------------------------- one read path repairs splits (Task 3.1)
def test_every_warehouse_read_is_the_one_repair(tmp_path, monkeypatch):
    """The pages' warehouse-first read, the one-name read and ``bars_panel`` give the same repaired bars on a
    warehouse holding a constructed unadjusted 4:1 split (test_splits.unadjusted_split), each with its note."""
    from test_splits import SPLIT_EX, SPLIT_NOTE, unadjusted_split

    wh = unadjusted_split(tmp_path / "split.duckdb")
    pin(monkeypatch, datetime(2026, 6, 2, 14, 0, tzinfo=UTC))
    for t in ("SPY", "QQQ"):
        _record_full_fetch(wh, t, "1990-01-01")
    got = market(tmp_path, wh).prices(["SPY", "QQQ"], warehouse_first=True)
    assert [p.source for p in got.provenance] == ["warehouse:fixture:alpaca"] * 2
    with open_ro(wh) as con:
        panel, notes = readers.bars_panel(con, ["SPY", "QQQ"], None, None)
        one = readers.repaired_ohlcv(con, "SPY", path=str(wh))
        raw = readers.ohlcv(con, "SPY").data
    assert notes == [SPLIT_NOTE]
    pd.testing.assert_frame_equal(got.data, panel.dropna().rename_axis(columns=None), check_exact=True,
                                  check_freq=False)
    pd.testing.assert_series_equal(one.data["adj_close"], panel["SPY"].dropna(), check_names=False, check_exact=True)
    said = SPLIT_NOTE.removeprefix("SPY: ")
    assert got.provenance[0].detail["notes"] == [said] and one.provenance[0].detail["notes"] == [said]
    assert "notes" not in got.provenance[1].detail
    assert raw.loc[: pd.Timestamp(SPLIT_EX)].iloc[-2]["close"] > 4 * 600     # ohlcv is still the vendor's bytes


def test_the_shadow_and_the_factor_screen_read_the_repair(tmp_path):
    """``kronos_shadow.bars_of`` (which ``factors.screen`` also reads) took the vendor's bytes from the warehouse."""
    import duckdb
    from test_splits import SPLIT_EX, unadjusted_split

    from ohcamel_quant.jobs.context import JobContext
    from ohcamel_quant.products.kronos_shadow import bars_of

    wh = unadjusted_split(tmp_path / "split.duckdb")
    ctx = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                     _market=MarketData(Settings(offline=True, data_dir=tmp_path)),
                     _warehouse=duckdb.connect(str(wh), read_only=True))
    try:
        ds = bars_of(ctx, "SPY")
    finally:
        ctx.close()
    ret = ds.data["adj_close"].pct_change()
    assert abs(ret.loc[SPLIT_EX]) < 0.30
    want, _ = fixtures.history("SPY")
    np.testing.assert_array_equal(ds.data.to_numpy(), want.to_numpy())
    assert ds.provenance[0].detail["notes"] == ["split-adjusted 4:1 before 2026-03-02 (vendor adj_close did not "
                                                "absorb it)"]


def test_the_factor_screen_carries_its_repairs_and_reads_nothing_after_its_window(tmp_path):
    """``factors.screen`` reads to the exploration window's end (2023-12-31): a split before it is repaired and
    named in the artifact's provenance; the constructed 2026 split is after it and is never read."""
    import duckdb
    from test_splits import unadjusted_split

    from ohcamel_quant.jobs.context import JobContext
    from ohcamel_quant.products import factor_screen as FS

    wh = unadjusted_split(tmp_path / "split.duckdb")                      # SPY, 2026-03-02: after the window
    with open_rw(wh) as con:                                               # and QQQ 4:1 on 2019-06-03, within it
        con.execute("UPDATE bars_daily SET open = open * 4, high = high * 4, low = low * 4, close = close * 4, "
                    "adj_close = adj_close * 4, volume = volume / 4 WHERE ticker = 'QQQ' AND date < DATE '2019-06-03'")
    ctx = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                     _market=MarketData(Settings(offline=True, data_dir=tmp_path)),
                     _warehouse=duckdb.connect(str(wh), read_only=True))
    repairs: list[str] = []
    try:
        fields, missing = FS.load_fields(ctx, ["SPY", "QQQ"], FS.W.EXPLORATION_END, repairs=repairs)
    finally:
        ctx.close()
    assert missing == []
    assert repairs == ["QQQ: split-adjusted 4:1 before 2019-06-03 (vendor adj_close did not absorb it)"]
    got = fields["close"]["QQQ"].dropna()
    assert (got.pct_change().dropna().abs() < 0.30).all() and got.index[-1] <= FS.W.EXPLORATION_END
