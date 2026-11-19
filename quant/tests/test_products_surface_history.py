"""Lane M, M8: vol.surface_history over a temp warehouse of BSM-priced quotes (hand-built to check the
pipeline against a known smile, as tests/test_options_svi_parity.py does; nothing is published)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from lane_m_harness import T0, is_label, publish_artifact

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.options import bsm
from ohcamel_quant.products import surface_history
from ohcamel_quant.products.io import read_table
from ohcamel_quant.warehouse.db import open_rw
from ohcamel_quant.warehouse.ingest.base import insert_frame, log_row
from ohcamel_quant.warehouse.ingest.options import INSERT

SPOT, R, Q = 100.0, 0.04, 0.01
ASOF = pd.Timestamp("2026-06-01 15:30:00")       # the quotes' time, New York (a 15:45 pull, 15 minutes delayed)


def _utc(ny):
    return pd.Timestamp(ny).tz_localize("America/New_York").tz_convert("UTC").tz_localize(None)


def _quotes(asof=ASOF, atm=0.20, pull="1545", spot=SPOT, und="SPY", stored=True):
    """BSM quotes of a known smile (ATM ``atm`` at every expiry), T counted from ``asof``. ``stored=False`` leaves
    the 3.4 columns NULL, as on a row stored before them (an after-close pull)."""
    rows = []
    for dte in (29, 60, 120):          # 29: the 30-day target stays bracketed from any quote time
        expiry = (asof + pd.Timedelta(days=dte)).normalize()
        T = (expiry + pd.Timedelta(hours=16) - asof).total_seconds() / (365.0 * 86400)
        F = spot * np.exp((R - Q) * T)
        for K in np.arange(70.0, 131.0, 2.5) * spot / SPOT:
            k = np.log(K / F)
            iv = atm - 0.10 * k + 0.30 * k * k
            for cp in ("C", "P"):
                m = float(bsm.bsm_price(spot, K, T, iv, R, Q, cp))
                rows.append({"underlying": und, "asof": asof.normalize(), "expiry": expiry, "strike": K, "cp": cp,
                             "bid": max(m - 0.02, 0.01), "ask": m + 0.02, "last": m, "volume": 10.0,
                             "open_interest": 100.0, "source": "test",
                             "spot": spot if stored else None, "quote_ts": _utc(asof) if stored else None,
                             "vendor_iv": iv if stored else None, "pull": pull if stored else "1620",
                             "quote_ts_source": "cboe:last_trade_time" if stored else None})
    return pd.DataFrame(rows)


@pytest.fixture
def wh(tmp_path):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        insert_frame(con, INSERT, _quotes())
    return path


def curves(*dated):
    """CONSTRUCTED Treasury par curves (percent, FRED's shape: one row per date, tenors in years): flat at
    ``level`` so the bootstrapped zero is about level - 0.04 percentage points, near the quotes' R."""
    tenors = [1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0]
    return pd.DataFrame([[lvl] * len(tenors) for _, lvl in dated], index=pd.DatetimeIndex([d for d, _ in dated]),
                        columns=tenors)


class CurveMarket:
    """The offline market, with Treasury curves it serves (the fixtures hold no full curve); records each window
    asked for, so a test can see that nothing after a snapshot's date was read."""

    def __init__(self, market, frame):
        self._market, self._frame, self.asked = market, frame, []

    def __getattr__(self, name):
        return getattr(self._market, name)

    def treasury_curve(self, start=None, end=None):
        from ohcamel_quant.data.base import Provenance
        from ohcamel_quant.data.market import Dataset

        self.asked.append((start, end))
        f = self._frame
        f = f[f.index >= pd.Timestamp(start)] if start is not None else f
        f = f[f.index <= pd.Timestamp(end)] if end is not None else f
        return Dataset(f, [Provenance.now("fixture", note="CONSTRUCTED treasury curve")])


JUNE_CURVE = curves(("2026-05-29", 4.04), ("2026-06-01", 4.04))


def ctx(market, wh, frame=JUNE_CURVE):
    m = market if frame is None else CurveMarket(market, frame)
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=m, _warehouse=duckdb.connect(str(wh), read_only=True))


def test_one_day_of_history_from_a_known_smile(market, wh, tmp_path, monkeypatch):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(surface_history, "DB_PATH", db)
    publish_artifact(db, root, "vol.forecast_league",
                     {"forecasts": pd.DataFrame({"ticker": ["SPY"], "asof": ["2026-05-29"], "har_22d": [1e-4],
                                                         "har_covers": ["close_to_close"]})})
    spec = surface_history.run({}, ctx(market, wh))
    assert spec.verdict == "DESCRIPTIVE ONLY" and "1 DAY" in spec.verdict_detail and is_label(spec.verdict_detail)
    h = spec.tables["history"].iloc[0]
    assert h["underlying"] == "SPY" and h["asof"] == "2026-06-01"
    assert h["atm_iv_30d"] == pytest.approx(0.20, abs=0.005)   # the constructed smile's ATM level
    assert abs(h["term_slope"]) < 0.01 and h["rr25_30d"] < 0         # flat ATM term; put skew from -0.10 k
    assert h["mf_var_30d"] > 0 and h["vrp"] == pytest.approx(h["mf_var_30d"] - 252e-4)
    assert spec.tables["summary"].set_index("underlying").loc["SPY", "days"] == 1


def test_a_second_run_only_adds_new_days(market, wh, tmp_path, monkeypatch):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(surface_history, "DB_PATH", db)
    first = surface_history.run({}, ctx(market, wh))
    _, path = publish_artifact(db, root, "vol.surface_history", first.tables, verdict=first.verdict)
    second = surface_history.run({}, ctx(market, wh))
    pd.testing.assert_frame_equal(second.tables["history"], read_table(path, "history"))  # nothing new: carried


def test_surface_history_with_no_snapshots_is_insufficient_data(market, tmp_path, monkeypatch):
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    path = tmp_path / "empty.duckdb"
    with open_rw(path):
        pass
    spec = surface_history.run({}, ctx(market, path))
    assert spec.verdict == "INSUFFICIENT DATA" and spec.verdict_detail == "NO OPTION SNAPSHOTS"


def test_a_snapshot_without_its_spot_is_listed_not_fatal(market, wh, tmp_path, monkeypatch):
    """A QQQ snapshot stored before task 3.4 (no spot column) with no ingest_log row goes to `errors`; SPY's day
    is still recorded."""
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    with open_rw(wh) as con:
        insert_frame(con, INSERT, _quotes(und="QQQ", stored=False))
    spec = surface_history.run({}, ctx(market, wh))
    assert spec.tables["history"]["underlying"].tolist() == ["SPY"]
    err = spec.tables["errors"]
    assert err["underlying"].tolist() == ["QQQ"] and "spot" in err["error"].iloc[0]


def test_empty_history_errors_table_keeps_its_columns(market, tmp_path, monkeypatch):
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    path = tmp_path / "empty.duckdb"
    with open_rw(path):
        pass
    spec = surface_history.run({}, ctx(market, path))
    assert list(spec.tables["errors"].columns) == ["underlying", "asof", "error"]


def test_a_forecast_of_the_open_to_close_part_alone_gives_no_vrp(market, wh, tmp_path, monkeypatch):
    """A league artifact written before the target included the overnight move (no ``har_covers``) would
    leave the overnight share in the premium: the row is blank and says why."""
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(surface_history, "DB_PATH", db)
    publish_artifact(db, root, "vol.forecast_league",
                     {"forecasts": pd.DataFrame({"ticker": ["SPY"], "asof": ["2026-05-29"], "har_22d": [1e-4]})})
    h = surface_history.run({}, ctx(market, wh)).tables["history"].iloc[0]
    assert pd.isna(h["vrp"]) and "open-to-close" in h["vrp_note"] and h["mf_var_30d"] > 0


def test_a_carried_row_published_on_the_old_basis_is_blanked_with_its_reason(market, wh, tmp_path, monkeypatch):
    """History rows are carried forward and never recomputed, so a VRP published before the league's target
    included the overnight move (no ``vrp_covers``) would keep its overstated value for ever: it is blanked.
    A row marked close_to_close keeps its number."""
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(surface_history, "DB_PATH", db)
    cols = ["underlying", "asof", "atm_iv_30d", "atm_iv_90d", "term_slope", "rr25_30d", "bf25_30d", "mf_var_30d",
            "vrp", "vrp_note", "n_slices", "spot"]
    old = pd.DataFrame([["SPY", "2026-05-20", 0.2, 0.22, 0.02, -0.04, 0.01, 0.04, 0.0148, None, 8, 600.0]], columns=cols)
    publish_artifact(db, root, "vol.surface_history", {"history": old})
    spec = surface_history.run({}, ctx(market, wh))
    h = spec.tables["history"].set_index("asof")
    assert pd.isna(h.loc["2026-05-20", "vrp"]) and "open-to-close" in h.loc["2026-05-20", "vrp_note"]
    assert h.loc["2026-05-20", "mf_var_30d"] == 0.04                       # the rest of the row is untouched
    # a row already on the new basis is kept as it is
    new = old.assign(vrp_covers="close_to_close")
    publish_artifact(db, root, "vol.surface_history", {"history": new}, finished=T0 + timedelta(hours=1))
    kept = surface_history.run({}, ctx(market, wh)).tables["history"].set_index("asof")
    assert kept.loc["2026-05-20", "vrp"] == 0.0148 and pd.isna(kept.loc["2026-05-20", "vrp_note"])


# --- Task 3.4: the pull, the quote time, the curve of the snapshot's date -------------------------------------------
def test_the_pre_close_pull_is_preferred_and_an_after_close_only_day_says_so(market, tmp_path, monkeypatch):
    """Both pulls on 06-01 (the 16:20 one priced with a different smile): the 15:45 pull is the one measured. On 06-02
    only the 16:20 pull exists: it is used, and the row and the notes say so."""
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    path = tmp_path / "w.duckdb"
    day2 = pd.Timestamp("2026-06-02 16:00:00")
    with open_rw(path) as con:
        insert_frame(con, INSERT, _quotes())
        insert_frame(con, INSERT, _quotes(pd.Timestamp("2026-06-01 16:00:00"), atm=0.30, pull="1620"))
        insert_frame(con, INSERT, _quotes(day2, atm=0.25, pull="1620"))
    spec = surface_history.run({}, ctx(market, path, curves(("2026-06-01", 4.04), ("2026-06-02", 4.04))))
    h = spec.tables["history"].set_index("asof")
    assert h.loc["2026-06-01", "pull"] == "1545" and h.loc["2026-06-01", "atm_iv_30d"] == pytest.approx(0.20, abs=0.005)
    assert h.loc["2026-06-01", "quote_ts"] == "2026-06-01T15:30:00"
    assert h.loc["2026-06-02", "pull"] == "1620" and h.loc["2026-06-02", "atm_iv_30d"] == pytest.approx(0.25, abs=0.005)
    assert any("after-close pull" in n and "2026-06-02" in n for n in spec.notes)


def test_time_to_expiry_counts_from_the_stored_quote_time(market, tmp_path, monkeypatch):
    """The committed Sunday SPY payload, stored by the ingest: the chain surface_history builds is stamped with the
    quotes' time (Friday 16:00 New York) and the vendor's spot, so the Tuesday expiry has at least the two sessions
    left from Friday's close in business time -- not the 1.7 days counted from Sunday's payload stamp."""
    import gzip

    import httpx
    import respx

    from ohcamel_quant.config import Settings
    from ohcamel_quant.data import cboe, http
    from ohcamel_quant.data.market import MarketData
    from ohcamel_quant.options.chain import analyze_chain
    from ohcamel_quant.warehouse.ingest import options
    from ohcamel_quant.warehouse.ingest.base import LocalContext

    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()
    payload = json.loads(gzip.decompress((Path(__file__).parent / "data" / "cboe_spy_2026-11-15.json.gz").read_bytes()))
    wh = tmp_path / "w.duckdb"
    with respx.mock:
        respx.get(cboe.URL.format(sym="SPY")).mock(return_value=httpx.Response(200, json=payload))
        options.run_option_snapshots({"underlyings": ["SPY"], "pull": "1620"}, LocalContext(market=MarketData(
            Settings(offline=False, data_dir=tmp_path, http_retries=0, warehouse_path=wh))))
    con = duckdb.connect(str(wh), read_only=True)
    try:
        chain, meta = surface_history._chain(con, "SPY", "2026-11-13")
    finally:
        con.close()
    assert chain.as_of == pd.Timestamp("2026-11-13 16:00:00") and chain.spot == 754.37
    assert meta["pull"] == "1620" and meta["quote_ts_source"] == "cboe:last_trade_time"
    m = json.loads(Path(__file__).parent.joinpath("data", "cboe_spy_2026-11-15.json.gz.meta.json").read_text())
    tc = m["treasury_curve"]
    rate = curves((tc["date"], 0.0))
    rate.iloc[0] = tc["yield_pct"]
    from ohcamel_quant.macro.curve import zero_curve_on

    ca = analyze_chain(chain, rate_curve=zero_curve_on(rate, chain.as_of).rate)
    s = ca.slices.loc["2026-11-17"]
    assert s["T"] >= 2 / 252 and abs(s["div_yield"]) < 0.05


def test_a_back_processed_snapshot_discounts_with_the_curve_of_its_own_date(market, tmp_path, monkeypatch):
    """A 2026-10-20 snapshot processed later, with curves dated 10-19, 10-20 and 10-21 on file: the zero curve
    bootstrapped from 10-20's par yields is the one used, and nothing dated after 10-20 is asked for."""
    from ohcamel_quant.macro.curve import zero_curve_on

    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        insert_frame(con, INSERT, _quotes(pd.Timestamp("2026-10-20 15:30:00")))
    frame = curves(("2026-10-19", 3.0), ("2026-10-20", 4.04), ("2026-10-21", 6.0))
    used = []
    real = surface_history.analyze_chain
    monkeypatch.setattr(surface_history, "analyze_chain",
                        lambda chain, rate_curve=None, **k: used.append(rate_curve) or real(chain, rate_curve=rate_curve, **k))
    c = ctx(market, path, frame)
    spec = surface_history.run({}, c)
    h = spec.tables["history"].iloc[0]
    assert h["asof"] == "2026-10-20" and h["curve_date"] == "2026-10-20"
    want = zero_curve_on(frame, "2026-10-20")
    assert [used[0](T) for T in (0.1, 0.5)] == pytest.approx([want.rate(0.1), want.rate(0.5)], abs=1e-14)
    assert all(end is not None and pd.Timestamp(end) <= pd.Timestamp("2026-10-20") for _, end in c._market.asked)


def test_a_missing_curve_takes_the_nearest_earlier_one_within_the_limit_else_refuses(market, tmp_path, monkeypatch):
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        insert_frame(con, INSERT, _quotes(pd.Timestamp("2026-10-20 15:30:00")))
    spec = surface_history.run({}, ctx(market, path, curves(("2026-10-16", 4.04))))      # 4 days before: used
    h = spec.tables["history"].iloc[0]
    assert h["curve_date"] == "2026-10-16" and any("2026-10-16" in n and "4 days before" in n for n in spec.notes)
    spec = surface_history.run({}, ctx(market, path, curves(("2026-10-09", 4.04))))      # 11 days before: refused
    assert spec.tables["history"].empty or "2026-10-20" not in set(spec.tables["history"]["asof"])
    assert "no Treasury curve within 5 days" in spec.tables["errors"]["error"].iloc[0]


def test_a_row_stored_before_the_new_columns_uses_its_ingest_log_and_says_so(market, tmp_path, monkeypatch):
    """Rows from before task 3.4 have no spot or quote time: the spot comes from the ingest_log row, and the time
    from its as_of -- the payload's own timestamp, which is UTC (data/cboe.py) -- converted to New York."""
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        insert_frame(con, INSERT, _quotes(pd.Timestamp("2026-06-01 16:20:00"), stored=False))
        log_row(con, "option_snapshots", "SPY", "ok", 1, json.dumps({"spot": SPOT, "as_of": "2026-06-01T20:20:00"}),
                date(2026, 6, 1), ran_at=datetime(2026, 6, 1, 20, 21))
    con = duckdb.connect(str(path), read_only=True)
    try:
        chain, meta = surface_history._chain(con, "SPY", "2026-06-01")
    finally:
        con.close()
    assert chain.as_of == pd.Timestamp("2026-06-01 16:20:00") and chain.spot == SPOT
    assert meta["pull"] == "1620" and "before task 3.4" in meta["quote_ts_source"]
