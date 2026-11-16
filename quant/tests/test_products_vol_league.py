"""Lane M, M3: vol.forecast_league. Offline, on the committed ETF fixtures.

Two kinds of data, and each test says which it uses:

* the fixture warehouse (``build_fixture_warehouse``: the committed real daily bars) with NO minute bars,
  which is what the league falls back on: the squared daily return, a close-to-close quantity;
* the same warehouse plus CONSTRUCTED minute bars (``_minute_bars``), because no real minute bars are
  committed (they are IEX-only, ~10M rows). The constructed bars are anchored to the real daily open and
  close and calibrated to the real daily range, but they are not market data: tests on them check the
  arithmetic of the scoring target and the league's plumbing, never a claim about markets.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest
from lane_m_harness import is_label

from ohcamel_quant.data import fixtures
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.models import vol_league as V
from ohcamel_quant.models.surface_metrics import vrp
from ohcamel_quant.products import vol_league
from ohcamel_quant.warehouse.db import open_rw
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse
from ohcamel_quant.warehouse.freshness import NY

N_SESSIONS = 340     # >= HAR_WINDOW + 42 eval days + 22 lags
PER_DAY = 390


def ctx(market, con=None):
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=market, _warehouse=con)


def _minute_bars(ticker: str, prints: int = PER_DAY) -> pd.DataFrame:
    """CONSTRUCTED minute bars for the last N_SESSIONS fixture sessions of ``ticker``: log steps of
    ln(C/O)/390 with alternating +-a, a = ln(H/L) / (2 sqrt(390)), so the path starts at the real open's
    neighbourhood, ends on the real close and has a real-range-sized minute variance. ``prints`` bars a day."""
    df, _ = fixtures.history(ticker)
    df = df.iloc[-N_SESSIONS:]
    frames = []
    for d, row in df.iterrows():
        o, h, lo, c = (float(row[x]) for x in ("open", "high", "low", "close"))
        a = np.log(h / lo) / (2 * np.sqrt(PER_DAY))
        steps = np.log(c / o) / PER_DAY + a * np.where(np.arange(PER_DAY) % 2 == 0, 1.0, -1.0)
        px = o * np.exp(np.cumsum(steps))
        t0 = pd.Timestamp(d.date()).tz_localize(NY) + pd.Timedelta(hours=9, minutes=30)
        ts = (pd.date_range(t0, periods=PER_DAY, freq="min").tz_convert("UTC").tz_localize(None))
        frames.append(pd.DataFrame({"ticker": ticker, "ts": ts, "open": px, "high": px, "low": px, "close": px,
                                    "volume": 100.0, "source": "constructed"}).iloc[:prints])
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def wh(tmp_path_factory):
    """Fixture warehouse (real daily bars) + constructed minute bars: SPY dense, TLT with 40 prints a session."""
    path = build_fixture_warehouse(tmp_path_factory.mktemp("wh") / "w.duckdb")
    with open_rw(path) as con:
        for t, prints in (("SPY", PER_DAY), ("TLT", 40)):
            con.register("incoming", _minute_bars(t, prints))
            con.execute("INSERT INTO bars_minute SELECT ticker, ts, open, high, low, close, volume, source "
                        "FROM incoming")
            con.unregister("incoming")
    return path


@pytest.fixture()
def con(wh):
    c = duckdb.connect(str(wh), read_only=True)
    yield c
    c.close()


def test_league_on_two_fixture_etfs(market):
    """Fixture warehouse-free path: real daily bars, no minute bars -> the close-to-close squared return."""
    spec = vol_league.run({"tickers": ["SPY", "TLT", "NOPE"], "eval_days": 42}, ctx(market))
    assert spec.verdict == "DESCRIPTIVE ONLY" and "MCS" in spec.verdict_detail and is_label(spec.verdict_detail)
    lg = spec.tables["league"]
    assert set(lg["ticker"]) == {"SPY", "TLT"}
    assert {"garch", "gjr", "ewma"} <= set(lg["model"]) <= {"garch", "gjr", "egarch", "ewma"}  # no HAR offline
    assert (lg["target"] == "squared_return").all() and lg.groupby("ticker")["in_mcs"].any().all()
    n = lg.groupby("ticker").size()
    assert len(spec.tables["dm"]) == int((n * (n - 1) // 2).sum())  # every pair of scored models, per name
    d = spec.tables["dropped"]  # a degenerate EGARCH refit is listed with its reason, never scored
    assert set(d["model"]) <= {"egarch", "garch", "gjr", "mcs"} and d["reason"].str.len().gt(0).all()
    assert spec.tables["skipped"].set_index("ticker").loc["NOPE", "reason"]
    f = spec.tables["forecasts"].set_index("ticker")
    assert (f.loc[["SPY", "TLT"], "var_1d_gjr"] > 0).all()
    assert spec.data_asof == "2026-06-01"


# --- the target: open-to-close minute RV + the overnight move ---------------------------------------------

class _Daily:
    """Stand-in for the pieces _daily_rv reads, with a tiny CONSTRUCTED two-session frame: arithmetic only."""

    def __init__(self, con):
        self.warehouse = con


def _two_sessions():
    """Session 1 closes at 100; session 2 opens at 101 (a clean +1% overnight gap). Minute closes are
    constructed round numbers so the minute RV is known by hand."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE bars_daily (ticker TEXT, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, "
                "close DOUBLE, adj_close DOUBLE, volume DOUBLE, source TEXT, fetched_at TIMESTAMP)")
    con.execute("CREATE TABLE bars_minute (ticker TEXT, ts TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE, "
                "close DOUBLE, volume DOUBLE, source TEXT)")
    days = pd.DatetimeIndex(["2026-05-26", "2026-05-27"])
    daily = [("2026-05-26", 99.0, 100.5, 98.5, 100.0), ("2026-05-27", 101.0, 102.0, 100.0, 100.0)]
    for d, o, h, lo, c in daily:
        con.execute("INSERT INTO bars_daily VALUES ('AAA', ?, ?, ?, ?, ?, ?, 1e6, 'test', NULL)", [d, o, h, lo, c, c])
    prices = {}
    for d in days:
        t0 = pd.Timestamp(d.date()).tz_localize(NY) + pd.Timedelta(hours=9, minutes=30)
        ts = pd.date_range(t0, periods=150, freq="min").tz_convert("UTC").tz_localize(None)
        px = np.where(np.arange(150) % 2 == 0, 100.0, 100.5)
        prices[d] = (ts, px)
        con.register("incoming", pd.DataFrame({"ticker": "AAA", "ts": ts, "open": px, "high": px, "low": px,
                                               "close": px, "volume": 1.0, "source": "test"}))
        con.execute("INSERT INTO bars_minute SELECT * FROM incoming")
        con.unregister("incoming")
    return con, days, prices


def test_target_is_minute_rv_plus_the_squared_overnight_gap():
    con, days, prices = _two_sessions()
    rv = vol_league._daily_rv(_Daily(con), "AAA", days, min_prints=100)
    px = prices[days[1]][1]
    minute_rv = float(np.sum(np.diff(np.log(px)) ** 2))   # by hand: the same bars' squared log returns
    assert np.isnan(rv[0])                                  # no previous close in the frame
    assert rv[1] - minute_rv == pytest.approx((101.0 / 100.0 - 1.0) ** 2, abs=1e-12)   # 1e-4, a simple return
    assert rv[1] - minute_rv == pytest.approx(1e-4, abs=1e-12)


def test_a_session_with_under_100_prints_is_excluded_and_named(con):
    notes: list[str] = []
    days = pd.DatetimeIndex(fixtures.history("TLT")[0].index[-N_SESSIONS:])
    rv = vol_league._daily_rv(ctx(None, con), "TLT", days, notes=notes)
    assert np.isnan(rv).all()                                 # 40 prints a session: every session excluded
    assert len(notes) == 1 and "TLT" in notes[0] and "40" in notes[0] and "100" in notes[0]
    spy = vol_league._daily_rv(ctx(None, con), "SPY", days[-6:], notes=notes)
    assert np.isnan(spy[0]) and np.isfinite(spy[1:]).all()    # the first day of a window has no previous close
    assert len(notes) == 1                                    # a dense name adds no note


def test_league_scores_the_full_day_target_and_names_the_sparse_name(market, con):
    spec = vol_league.run({"tickers": ["SPY", "TLT"], "eval_days": 42}, ctx(market, con))
    lg = spec.tables["league"]
    spy, tlt = lg[lg["ticker"] == "SPY"], lg[lg["ticker"] == "TLT"]
    assert "har" in set(spy["model"]) and (spy["target"] == "full_day_rv").all()
    assert "har" not in set(tlt["model"]) and (tlt["target"] == "squared_return").all()   # thin name: fallback
    assert any("TLT" in n and "100" in n for n in spec.notes)
    assert any("overnight" in n and "open_t / close_{t-1}" in n for n in spec.notes)       # the target is stated
    f = spec.tables["forecasts"].set_index("ticker")
    assert f.loc["SPY", "har_covers"] == "close_to_close"


def test_har_22d_and_the_vrp_use_the_overnight_inclusive_target(market, con):
    """The published HAR monthly forecast is trained on minute RV + overnight; a model trained on the
    open-to-close part alone forecasts less, so the VRP computed from it is overstated."""
    spec = vol_league.run({"tickers": ["SPY"], "eval_days": 42}, ctx(market, con))
    har_new = float(spec.tables["forecasts"].set_index("ticker").loc["SPY", "har_22d"])
    days = pd.DatetimeIndex(market.returns(["SPY"]).data["SPY"].dropna().index)
    full = vol_league._daily_rv(ctx(market, con), "SPY", days)
    on2 = full - vol_league._minute_only(ctx(market, con), "SPY", days)
    assert np.isclose(V.har_22d(full), har_new)
    har_old = V.har_22d(full - on2)
    mf = 0.05                                                  # an arbitrary implied variance: arithmetic only
    fall = vrp(mf, har_old, covers="close_to_close") - vrp(mf, har_new, covers="close_to_close")
    assert fall == pytest.approx(252.0 * (har_new - har_old)) and fall > 0
    assert fall == pytest.approx(252.0 * np.nanmean(on2[-250:]), rel=1.0)   # of the order of the overnight share


def test_ewma_is_closer_to_the_full_day_target_than_to_the_open_to_close_one(market, con):
    """EWMA forecasts close-to-close variance. QLIKE's level depends on the target's scale (ln F + RV/F grows
    with RV), so the comparison is each target's excess loss over its own optimum, RV/F - ln(RV/F) - 1."""
    days = pd.DatetimeIndex(market.returns(["SPY"]).data["SPY"].dropna().index)
    r = market.returns(["SPY"]).data["SPY"].dropna().to_numpy(float)
    full = vol_league._daily_rv(ctx(market, con), "SPY", days)[-42:]
    o2c = vol_league._minute_only(ctx(market, con), "SPY", days)[-42:]
    f, _ = V.ewma_oos(r, 42)

    def excess(t):
        return float(np.mean(t / f - np.log(t / f) - 1.0))

    assert excess(full) < excess(o2c)
