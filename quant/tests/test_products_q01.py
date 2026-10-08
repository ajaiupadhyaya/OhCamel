"""Lane M, M6: models.xs_lgbm end to end on the fixture warehouse, with a shortened TEST config.

The windows are shrunk to fit the fixtures (2016-06..2026-06). This tests the machinery, not the experiment;
the frozen design lives in research/experiments/EXP-Q01/config.yaml and is never edited by tests."""

from __future__ import annotations

import copy
from datetime import timedelta

import duckdb
import numpy as np
import pandas as pd
import pytest
from lane_m_harness import T0, is_label, publish_artifact

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import experiment, q01
from ohcamel_quant.products.io import read_table
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

FIXTURE_ETFS = ["GLD", "IEF", "IWM", "QQQ", "SPY", "TLT", "XLE", "XLF", "XLK"]


@pytest.fixture(scope="module")
def frozen():
    return experiment.load_config("EXP-Q01")


@pytest.fixture
def test_cfg(frozen):
    c = copy.deepcopy(frozen)
    c.update(universe=FIXTURE_ETFS, universe_frozen_on="2026-10-06",
             cv={"train_months": 24, "test_months": 6, "step_months": 3, "embargo_sessions": 5},
             windows={"selection": ["2017-07-01", "2022-12-31"], "holdout_start": "2023-01-01"})
    c["model"] = {**c["model"], "grid": {"num_leaves": [7], "min_data_in_leaf": [20], "learning_rate": [0.1, 0.03]},
                  "rounds": 40, "early_stopping_rounds": 10, "validation_months": 6}
    return c


@pytest.fixture(scope="module")
def wh(tmp_path_factory):
    return build_fixture_warehouse(tmp_path_factory.mktemp("wh") / "w.duckdb")


def ctx(market, wh):
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=market, _warehouse=duckdb.connect(str(wh), read_only=True))


def test_the_frozen_config_is_the_preregistration(frozen):
    assert frozen["cv"] == {"train_months": 60, "test_months": 12, "step_months": 6, "embargo_sessions": 5}
    assert frozen["model"]["grid"] == {"num_leaves": [7, 15], "min_data_in_leaf": [50, 200], "learning_rate": [0.03, 0.1]}
    assert frozen["model"]["rounds"] == 300 and frozen["portfolio"]["quantile"] == 0.2
    assert frozen["windows"] == {"selection": ["2008-01-01", "2021-12-31"], "holdout_start": "2022-01-01"}
    assert len(frozen["interpretations"]) == 9


def test_i_q01_7_dollar_volume_sign_is_recorded(frozen):
    assert frozen["composite_signs"]["dollar_vol_20d"] == -1
    i7 = next(i for i in frozen["interpretations"] if i.startswith("I-Q01-7"))
    assert "owner to confirm" not in i7 and "Amihud 2002" in i7 and "2026-10-07" in i7


def test_q01_unfrozen_universe_is_insufficient_data(market, wh, frozen, monkeypatch, tmp_path):
    monkeypatch.setattr(q01, "load_config", lambda exp: {**frozen, "universe": None})
    monkeypatch.setattr(q01, "DB_PATH", tmp_path / "jobs.sqlite")
    spec = q01.run({}, ctx(market, wh))
    assert spec.verdict == "INSUFFICIENT DATA" and spec.verdict_detail == "UNIVERSE NOT FROZEN"
    assert set(spec.tables) == {"gates"}  # no numbers at all


def _run(market, wh, cfg, monkeypatch, db):
    monkeypatch.setattr(q01, "load_config", lambda exp: cfg)
    monkeypatch.setattr(q01, "DB_PATH", db)
    return q01.run({}, ctx(market, wh))


def test_q01_runs_and_leads_with_the_charter_verdict(market, wh, test_cfg, monkeypatch, tmp_path):
    spec = _run(market, wh, test_cfg, monkeypatch, tmp_path / "jobs.sqlite")
    assert spec.verdict in ("PASS", "FAIL")
    g = spec.tables["gates"]
    assert {"holdout_positive", "dsr", "psr", "bootstrap_lower_5pct", "regimes_positive", "pbo", "cost_sweep",
            "beats_linear_composite"} == set(g["gate"])
    h = spec.tables["holdout"].iloc[0]
    assert h["methodology_version"] == 1 and h["n_trials"] == 2 * 13
    con = duckdb.connect(str(wh), read_only=True)
    first_2023 = str(con.execute("SELECT min(date) FROM bars_daily WHERE ticker = 'SPY' AND date >= DATE '2023-01-01'")
                     .fetchone()[0])
    assert h["holdout_start"] == first_2023  # the 2022-12-30 decision is the first holdout sample (I-Q01-1)
    assert list(spec.tables["holdout_costs"]["cost_bps"]) == [0.0, 5.0, 15.0, 30.0]
    assert set(spec.tables["scores"]["side"]) <= {"long", "short", "none"}
    assert "ADVISORY" in spec.verdict_detail or spec.verdict == "FAIL"
    assert is_label(spec.verdict_detail)


def test_q01_no_selection_return_reaches_the_holdout(market, wh, test_cfg, monkeypatch, tmp_path):
    """Review Focus 2 / I-Q01-1: windows split by when the label is realised. No selection daily return (the
    stitched series that picks the configuration and feeds PBO) is dated on or after holdout_start; the first
    holdout return is the first session of the holdout month; the last selection return is the last session
    before it (no month is dropped between the windows)."""
    spec = _run(market, wh, test_cfg, monkeypatch, tmp_path / "jobs.sqlite")
    hold_start = pd.Timestamp(test_cfg["windows"]["holdout_start"])
    sessions = pd.DatetimeIndex(pd.to_datetime(duckdb.connect(str(wh), read_only=True).execute(
        "SELECT date FROM bars_daily WHERE ticker = 'SPY' ORDER BY date").df()["date"]))
    stitched = pd.to_datetime(spec.tables["stitched"]["date"])
    held = pd.to_datetime(spec.tables["holdout_returns"]["date"])
    assert stitched.max() < hold_start
    assert stitched.max() == sessions[sessions < hold_start].max()
    assert held.min() == sessions[sessions >= hold_start].min()
    assert spec.tables["holdout"].iloc[0]["holdout_start"] == str(held.min().date())


def test_coverage_from_warehouse_judges_adjustment_by_source(tmp_path):
    """A GLD-like fund (no distributions: adj_close == close) from Yahoo then Alpaca is kept; a fund with an
    early Stooq segment stitched to Alpaca is excluded although its later adj_close differs from close."""
    from ohcamel_quant.models.xsec import freeze_universe
    from ohcamel_quant.warehouse.db import open_rw

    days = pd.bdate_range("2006-12-01", "2026-09-30")
    frames = []
    for t in ("SPY", "GLDX", "MIXD"):
        early = days.year < (2012 if t == "MIXD" else 2016)
        src = np.where(early, "stooq" if t == "MIXD" else "yahoo", "alpaca")
        # close is 100 every day; GLDX pays nothing, so adj_close == close from every vendor; MIXD's Alpaca
        # segment is adjusted (99) and its Stooq segment is not (100), which the old adj != close rule passed.
        adj = np.where((t == "GLDX") | (src == "stooq"), 100.0, 99.0)
        frames.append(pd.DataFrame({"ticker": t, "date": days.date, "open": 100.0, "high": 100.0, "low": 100.0,
                                    "close": 100.0, "adj_close": adj, "volume": 1e6, "source": src,
                                    "fetched_at": pd.NaT}))
    incoming = pd.concat(frames, ignore_index=True)
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        con.register("incoming", incoming)
        con.execute("INSERT INTO bars_daily SELECT ticker, CAST(date AS DATE), open, high, low, close, adj_close, "
                    "volume, source, CAST(fetched_at AS TIMESTAMP) FROM incoming")
    with duckdb.connect(str(path), read_only=True) as con:
        cov, spy = q01.coverage_from_warehouse(con, ["GLDX", "MIXD"])
    assert set(cov["GLDX"]["source"]) == {"yahoo", "alpaca"}
    members, excluded = freeze_universe(cov, spy, pd.Timestamp("2008-01-02"), pd.Timestamp("2026-09-30"),
                                        adjusted_from=pd.Timestamp(q01.PANEL_START))
    assert members == ["GLDX"]
    assert "stooq" in excluded["MIXD"]


def test_q01_holdout_is_evaluated_once_and_carried_forward(market, wh, test_cfg, monkeypatch, tmp_path):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    first = _run(market, wh, test_cfg, monkeypatch, db)
    _, path = publish_artifact(db, root, "models.xs_lgbm", first.tables, verdict=first.verdict)
    second = _run(market, wh, test_cfg, monkeypatch, db)
    for t in ("holdout", "holdout_returns", "holdout_costs", "holdout_regimes", "holdout_ic", "gates"):
        pd.testing.assert_frame_equal(second.tables[t], read_table(path, t))  # the stored evaluation, unchanged
    assert second.verdict == first.verdict


def test_q01_holdout_goes_stale_on_methodology_change(market, wh, test_cfg, monkeypatch, tmp_path):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    first = _run(market, wh, test_cfg, monkeypatch, db)
    publish_artifact(db, root, "models.xs_lgbm", first.tables, verdict=first.verdict)
    bumped = {**test_cfg, "methodology_version": 2}
    stale = _run(market, wh, bumped, monkeypatch, db)
    assert stale.verdict == "INSUFFICIENT DATA" and "OWNER" in stale.verdict_detail and is_label(stale.verdict_detail)
    approved = {**bumped, "holdout_reevaluation_approved": 2}
    again = _run(market, wh, approved, monkeypatch, db)
    assert again.tables["holdout"].iloc[0]["methodology_version"] == 2


def test_q01_insufficient_artifact_does_not_reopen_the_holdout(market, wh, test_cfg, frozen, monkeypatch, tmp_path):
    """An INSUFFICIENT DATA artifact (no holdout table) published after the evaluation must not make the next
    run evaluate the holdout again: the lookup reads the newest artifact that holds a holdout."""
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    first = _run(market, wh, test_cfg, monkeypatch, db)
    _, path = publish_artifact(db, root, "models.xs_lgbm", first.tables, verdict=first.verdict, finished=T0)
    down = _run(market, wh, {**frozen, "universe": None}, monkeypatch, db)
    assert set(down.tables) == {"gates"}
    publish_artifact(db, root, "models.xs_lgbm", down.tables, verdict=down.verdict, finished=T0 + timedelta(days=1))

    def boom(*a, **k):
        raise AssertionError("holdout evaluated a second time")
    monkeypatch.setattr(q01.X, "run_holdout", boom)
    third = _run(market, wh, test_cfg, monkeypatch, db)
    assert third.verdict == first.verdict
    for t in q01.HOLDOUT_TABLES:
        pd.testing.assert_frame_equal(third.tables[t], read_table(path, t))
