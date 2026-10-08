"""Lane M, M8: vol.surface_history over a temp warehouse of BSM-priced quotes (hand-built to check the
pipeline against a known smile, as tests/test_options_svi_parity.py does; nothing is published)."""

from __future__ import annotations

import json
from datetime import date, datetime

import duckdb
import numpy as np
import pandas as pd
import pytest
from lane_m_harness import publish_artifact

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.options import bsm
from ohcamel_quant.products import surface_history
from ohcamel_quant.products.io import read_table
from ohcamel_quant.warehouse.db import open_rw
from ohcamel_quant.warehouse.ingest.base import insert_frame, log_row
from ohcamel_quant.warehouse.ingest.options import INSERT

SPOT, R, Q = 100.0, 0.04, 0.01
ASOF = pd.Timestamp("2026-06-01 16:15:00")


def _quotes():
    rows = []
    for dte in (30, 60, 120):
        expiry = (ASOF + pd.Timedelta(days=dte)).normalize()
        T = (expiry + pd.Timedelta(hours=16) - ASOF).total_seconds() / (365.0 * 86400)
        F = SPOT * np.exp((R - Q) * T)
        for K in np.arange(70.0, 131.0, 2.5):
            k = np.log(K / F)
            iv = 0.20 - 0.10 * k + 0.30 * k * k            # ATM 0.20 at every expiry
            for cp in ("C", "P"):
                m = float(bsm.bsm_price(SPOT, K, T, iv, R, Q, cp))
                rows.append({"underlying": "SPY", "asof": ASOF.normalize(), "expiry": expiry, "strike": K, "cp": cp,
                             "bid": max(m - 0.02, 0.01), "ask": m + 0.02, "last": m, "volume": 10.0,
                             "open_interest": 100.0, "source": "test"})
    return pd.DataFrame(rows)


@pytest.fixture
def wh(tmp_path):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        insert_frame(con, INSERT, _quotes())
        log_row(con, "option_snapshots", "SPY", "ok", 1, json.dumps({"spot": SPOT, "as_of": ASOF.isoformat()}),
                date(2026, 6, 1), ran_at=datetime(2026, 6, 1, 20, 20))
    return path


def ctx(market, wh):
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=market, _warehouse=duckdb.connect(str(wh), read_only=True))


def test_one_day_of_history_from_a_known_smile(market, wh, tmp_path, monkeypatch):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(surface_history, "DB_PATH", db)
    publish_artifact(db, root, "vol.forecast_league",
                     {"forecasts": pd.DataFrame({"ticker": ["SPY"], "asof": ["2026-05-29"], "har_22d": [1e-4]})})
    spec = surface_history.run({}, ctx(market, wh))
    assert spec.verdict == "DESCRIPTIVE ONLY" and "1 day" in spec.verdict_detail
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
    assert spec.verdict == "INSUFFICIENT DATA" and "0 days of history" in spec.verdict_detail


def test_a_snapshot_without_its_spot_is_listed_not_fatal(market, wh, tmp_path, monkeypatch):
    """A QQQ snapshot with no ingest_log row (no spot) goes to `errors`; SPY's day is still recorded."""
    monkeypatch.setattr(surface_history, "DB_PATH", tmp_path / "jobs.sqlite")
    with open_rw(wh) as con:
        insert_frame(con, INSERT, _quotes().assign(underlying="QQQ"))
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
