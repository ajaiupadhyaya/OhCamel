"""signals.overlay (Task 15): one exposure scale from volatility targeting on the
roster's combined targets (INC-01, *The overlay*; design §4.2, D5).

The arithmetic is hand-derived beside each case. Units: portfolio/covariance.py's
frames are ANNUALISED (daily x 252, its header); P2's ``var_1d_<model>`` is a
next-day variance in DECIMAL^2 daily units (models/vol_league.py), so x 252 is
an annual variance. Run-level cases are offline, on the fixture warehouse
(warehouse/fixture_db.py: the nine fixture ETFs to 2026-06-01) and on league
artifacts published the way the worker publishes them (lane_m_harness)."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from lane_m_harness import publish_artifact

from ohcamel_quant.config import Settings
from ohcamel_quant.data.market import MarketData
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.models.cov_league import pca_factor_cov
from ohcamel_quant.portfolio.covariance import estimate_covariance
from ohcamel_quant.signals import overlay
from ohcamel_quant.signals.contract import OVERLAY_SCHEMA_PATH, validate
from ohcamel_quant.signals.roster import load_roster
from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

SIGMA_D = 0.2 / math.sqrt(252)  # daily sigma whose annualised variance is 0.2^2 = 0.04
NOW = datetime(2026, 6, 2, 14, 7, 31, tzinfo=UTC)

ROSTER = """\
version: 1
incubation_cap: 0.60
canary_cap: 0.10
vol_target: 0.10
strategies:
  - slug: exp_a01_spy
    producer: research-service
    capital_fraction: 0.025
  - slug: farm_mix_fixture
    producer: signals.daily
    strategy: static_mix
    universe: eq2
    symbols: [SPY, TLT]
    capital_fraction: 0.025
  - slug: farm_absent
    producer: signals.daily
    strategy: tsmom
    universe: bonds
    symbols: [IEF]
    capital_fraction: 0.025
"""


def _two_asset_returns(rho_zero: bool = True) -> pd.DataFrame:
    """A = (d, -d, d, -d) and B = (d, d, -d, -d), repeated: both mean 0, orthogonal (rho 0),
    sample variance 4d^2/3 per 4-row block -> with 4k rows, sum d^2 / (4k - 1) = 4k d^2 / (4k - 1).
    Choosing d^2 = SIGMA_D^2 (4k - 1) / (4k) makes the sample variance exactly SIGMA_D^2."""
    k = 63
    n = 4 * k
    d = SIGMA_D * math.sqrt((n - 1) / n)
    a = np.tile([d, -d, d, -d], k)
    b = np.tile([d, d, -d, -d], k)
    return pd.DataFrame({"A": a, "B": b}, index=pd.bdate_range("2025-01-01", periods=n))


def _cov(rho: float, sa: float = 0.2, sb: float = 0.2) -> pd.DataFrame:
    return pd.DataFrame([[sa * sa, rho * sa * sb], [rho * sa * sb, sb * sb]], index=["A", "B"], columns=["A", "B"])


# ------------------------------------------------------------- the arithmetic
def test_two_assets_hand_derived_through_the_sample_estimator():
    # Daily sigma 0.2/sqrt(252), rho 0: covariance() annualises, Sigma = diag(0.2^2 x ... ) = diag(0.04).
    # sigma_hat = sqrt(0.25 * 0.04 + 0.25 * 0.04) = sqrt(0.02) = 0.141421.
    cov = overlay.covariance(_two_asset_returns(), "sample")
    assert cov.loc["A", "A"] == pytest.approx(0.04, rel=1e-12)
    assert cov.loc["B", "B"] == pytest.approx(0.04, rel=1e-12)
    assert cov.loc["A", "B"] == pytest.approx(0.0, abs=1e-15)
    vol = overlay.forecast_vol({"A": 0.5, "B": 0.5}, cov, None)
    assert vol == pytest.approx(0.141421, abs=1e-6)


def test_exposure_scale_hand_derived():
    # min(1, 0.10 / 0.141421) = 0.707107, with sigma_hat = sqrt(0.02) = 0.141421 unrounded
    # (0.10 / the rounded 0.141421 is 0.7071086, 1.6e-6 off: the rounding, not the rule).
    assert overlay.exposure_scale(0.10, math.sqrt(0.02)) == pytest.approx(0.707107, abs=1e-6)


def test_forecast_replaces_the_diagonal():
    # A's annualised vol 0.4 replaces its 0.2: sqrt(0.25 * 0.16 + 0.25 * 0.04) = sqrt(0.05) = 0.223607.
    assert overlay.forecast_vol({"A": 0.5, "B": 0.5}, _cov(0.0), {"A": 0.4}) == pytest.approx(0.223607, abs=1e-6)


def test_diagonal_replacement_keeps_the_correlation():
    # rho 0.5: the off-diagonal becomes 0.5 * 0.4 * 0.2 = 0.04, so Sigma' = [[0.16, 0.04], [0.04, 0.04]];
    # sigma_hat = sqrt(0.25 * (0.16 + 0.04 + 2 * 0.04)) = sqrt(0.07) = 0.264575.
    replaced = overlay.replace_diagonal(_cov(0.5), {"A": 0.4})
    assert replaced.loc["A", "A"] == pytest.approx(0.16, rel=1e-12)
    assert replaced.loc["A", "B"] == pytest.approx(0.04, rel=1e-12)
    implied = replaced.loc["A", "B"] / math.sqrt(replaced.loc["A", "A"] * replaced.loc["B", "B"])
    assert implied == pytest.approx(0.5, rel=1e-12)
    assert overlay.forecast_vol({"A": 0.5, "B": 0.5}, _cov(0.5), {"A": 0.4}) == pytest.approx(0.264575, abs=1e-6)


def test_scale_capped_at_one():
    # 0.10 / 0.05 = 2 -> min(1, 2) = 1
    assert overlay.exposure_scale(0.10, 0.05) == 1.0


def test_zero_forecast_is_full_scale():
    assert overlay.exposure_scale(0.10, 0.0) == 1.0
    assert overlay.forecast_vol({}, _cov(0.0), None) == 0.0


def test_nan_forecast_raises():
    with pytest.raises(ValueError):
        overlay.exposure_scale(0.10, float("nan"))
    with pytest.raises(ValueError):
        overlay.exposure_scale(0.10, float("inf"))
    bad = _cov(0.0)
    bad.loc["A", "A"] = float("nan")
    with pytest.raises(ValueError):
        overlay.forecast_vol({"A": 0.5, "B": 0.5}, bad, None)
    with pytest.raises(ValueError):
        overlay.forecast_vol({"A": 0.5, "B": 0.5}, _cov(0.0), {"A": float("nan")})


def test_covariance_dispatches_by_the_league_names():
    r = _two_asset_returns()
    r["B"] = r["B"] * 0.5 + r["A"] * 0.3  # a non-trivial correlation, so the estimators differ
    for name in ("sample", "ewma", "lw_constant_corr", "lw_identity", "oas", "mp_denoise"):
        pd.testing.assert_frame_equal(overlay.covariance(r, name), estimate_covariance(r, name).cov)
    # pca_factor lives in models/cov_league.py and is DAILY there: x 252 annualises it.
    np.testing.assert_allclose(overlay.covariance(r, "pca_factor").to_numpy(), pca_factor_cov(r.to_numpy()) * 252)
    pd.testing.assert_frame_equal(overlay.covariance(r, "no_such"), estimate_covariance(r, "lw_constant_corr").cov)


def test_covariance_of_one_name_is_its_annualised_variance():
    r = _two_asset_returns()[["A"]]
    assert overlay.covariance(r, "oas").loc["A", "A"] == pytest.approx(0.04, rel=1e-12)


def test_ewma_diagonal_hand_value():
    # SYNTHETIC, test-only: three daily returns 0.01, -0.02, 0.015 for one name X, no P2 forecast.
    # RiskMetrics EWMA(0.94), seeded with the mean square of the first min(30, 3) returns:
    #   s0 = (0.01^2 + 0.02^2 + 0.015^2) / 3 = (1e-4 + 4e-4 + 2.25e-4) / 3 = 2.4166667e-4
    #   s1 = 0.94 s0 + 0.06 (0.01)^2  = 2.2716667e-4 + 6e-6    = 2.3316667e-4
    #   s2 = 0.94 s1 + 0.06 (0.02)^2  = 2.1917667e-4 + 2.4e-5  = 2.4317667e-4
    #   s3 = 0.94 s2 + 0.06 (0.015)^2 = 2.2858607e-4 + 1.35e-5 = 2.4208607e-4  (next-day variance, daily)
    # annualised: 2.4208607e-4 x 252 = 0.0610057; sigma_hat for w = 1 is sqrt(0.0610057) = 0.246993.
    r = pd.DataFrame({"X": [0.01, -0.02, 0.015]}, index=pd.bdate_range("2026-01-05", periods=3))
    vol, source, asof = overlay.diagonal_vols(r, {})["X"]
    assert source == "ewma" and asof is None
    replaced = overlay.replace_diagonal(overlay.covariance(r, "lw_constant_corr"), {"X": vol})
    assert replaced.loc["X", "X"] == pytest.approx(2.42086067e-4 * 252, rel=1e-8)  # 0.0610057
    sigma = overlay.forecast_vol({"X": 1.0}, overlay.covariance(r, "lw_constant_corr"), {"X": vol})
    assert sigma == pytest.approx(0.246993, abs=1e-6)


# ------------------------------------------------------------ combined targets
def _doc(path: Path, sequence: int, targets: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "sequence": sequence,
                                "targets": [{"symbol": s, "weight": w} for s, w in targets.items()]}))


def test_combined_targets_uses_the_newest_sequence(tmp_path):
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(ROSTER)
    roster = load_roster(roster_path)
    sig = tmp_path / "signals"
    # farm_mix_fixture: sequence 7 (named by the older date) beats sequence 6 -- by sequence, not name.
    _doc(sig / "farm_mix_fixture-2026-06-01.json", 6, {"SPY": 1.0})
    _doc(sig / "farm_mix_fixture-2026-05-29.json", 7, {"SPY": 0.6, "TLT": 0.4})
    _doc(sig / "exp_a01_spy-2026-06-01.json", 3, {"SPY": 1.0})
    (sig / "exp_a01_spy-2026-06-02.json").write_text("{not json")  # malformed: skipped and named
    notes: list[str] = []
    got = overlay.combined_targets(sig, roster, notes=notes)
    # SPY = 0.025 x 0.6 + 0.025 x 1.0 = 0.04; TLT = 0.025 x 0.4 = 0.01; farm_absent has no document.
    assert got == pytest.approx({"SPY": 0.04, "TLT": 0.01}, abs=1e-15)
    assert any("exp_a01_spy-2026-06-02.json" in n for n in notes)


def test_combined_targets_empty_without_documents(tmp_path):
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(ROSTER)
    assert overlay.combined_targets(tmp_path / "signals", load_roster(roster_path)) == {}


# ------------------------------------------------------------------- the run
def _ctx(world: dict, signals: Path | None) -> JobContext:
    md = MarketData(Settings(offline=True, data_dir=world["tmp"], warehouse_path=world["wh"], signals_dir=signals))
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None,
                      cancelled_fn=lambda: False, _market=md)


def _run(world: dict, signals: Path | None):
    ctx = _ctx(world, signals)
    try:
        return overlay.run({}, ctx)
    finally:
        ctx.close()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("signals_overlay")
    roster = tmp / "roster.yaml"
    roster.write_text(ROSTER)
    mp = pytest.MonkeyPatch()
    mp.setattr(overlay, "ROSTER_PATH", roster)
    mp.setattr(overlay, "_now", lambda: NOW)
    w = {"tmp": tmp, "wh": build_fixture_warehouse(tmp / "wh.duckdb")}
    yield w
    mp.undo()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.sqlite"
    monkeypatch.setattr(overlay, "DB_PATH", path)
    return path


def _signals(tmp_path: Path) -> Path:
    sig = tmp_path / "signals"
    _doc(sig / "farm_mix_fixture-2026-06-01.json", 2514, {"SPY": 0.6, "TLT": 0.4})
    _doc(sig / "exp_a01_spy-2026-06-01.json", 3, {"SPY": 1.0})
    return sig


def _overlay_doc(sig: Path, sequence: int) -> dict:
    return json.loads((sig / f"overlay-{sequence}.json").read_text())


def test_document_validates_and_records_its_inputs(world, db, tmp_path):
    sig = _signals(tmp_path)
    spec = _run(world, sig)
    doc = _overlay_doc(sig, 1)
    validate(doc, OVERLAY_SCHEMA_PATH)
    assert doc["kind"] == "overlay" and doc["sequence"] == 1
    # as_of is the run's own instant, whole minute, UTC.
    assert doc["as_of"] == "2026-06-02T14:07:00Z"
    inp = doc["inputs"]
    assert set(inp) == {"vol_target", "vol_forecast", "vol_model", "cov_estimator", "vol_artifact", "cov_artifact"}
    assert inp["vol_target"] == 0.10
    # No league artifacts: Ledoit-Wolf (constant correlation) and EWMA(0.94), said in the notes.
    assert inp["cov_estimator"] == "lw_constant_corr"
    assert inp["vol_model"] == "ewma"
    assert inp["vol_artifact"] == "none" and inp["cov_artifact"] == "none"
    assert doc["exposure_scale"] == pytest.approx(min(1.0, 0.10 / inp["vol_forecast"]))
    assert inp["vol_forecast"] > 0
    assert doc["reported"]["bars_asof"] == "2026-06-01"
    row = spec.tables["overlay"].iloc[0]
    assert int(row["sequence"]) == 1 and row["bars_asof"] == "2026-06-01"
    names = spec.tables["names"].set_index("symbol")
    assert names.loc["SPY", "weight"] == pytest.approx(0.04) and names.loc["TLT", "weight"] == pytest.approx(0.01)
    assert set(names["diag_source"]) == {"ewma"}
    assert any("lw_constant_corr" in n for n in spec.notes)
    assert any("on 252 sessions" in n for n in spec.notes)  # the fixture has > 252: the last 252 are used
    assert spec.data_asof == "2026-06-01"
    assert not list(sig.glob(".*"))  # no temp file survives


def test_reported_fields_none_when_the_artifacts_are_absent(world, db, tmp_path):
    sig = _signals(tmp_path)
    _run(world, sig)
    rep = _overlay_doc(sig, 1)["reported"]
    assert rep["regime_p_high"] is None and rep["mc_var_99_1d_core"] is None


def test_no_targets_is_full_scale_with_a_note(world, db, tmp_path):
    sig = tmp_path / "signals"
    sig.mkdir()
    spec = _run(world, sig)
    doc = _overlay_doc(sig, 1)
    validate(doc, OVERLAY_SCHEMA_PATH)
    assert doc["exposure_scale"] == 1.0 and doc["inputs"]["vol_forecast"] == 0.0
    assert any("no targets yet" in n for n in spec.notes)


def test_no_signals_directory_raises(world, db):
    with pytest.raises(RuntimeError, match="signals directory"):
        _run(world, None)


def _publish_spec(db: Path, root: Path, kind: str, spec, finished: datetime) -> str:
    aid, _ = publish_artifact(db, root, kind, spec.tables, finished=finished)
    return aid


def test_sequence_increments_across_runs(world, db, tmp_path):
    sig = _signals(tmp_path)
    first = _run(world, sig)
    _publish_spec(db, tmp_path / "artifacts", "signals.overlay", first, NOW)
    # The ledger, not the directory, carries it: a wiped signals directory still gets sequence 2.
    (sig / "overlay-1.json").unlink()
    second = _run(world, sig)
    assert int(second.tables["overlay"]["sequence"].iloc[0]) == 2
    assert _overlay_doc(sig, 2)["sequence"] == 2


def test_sequence_never_below_the_signals_directory(world, db, tmp_path):
    sig = _signals(tmp_path)
    _doc(sig / "overlay-7.json", 7, {})  # a wiped artifact ledger: the desk has already judged 7
    spec = _run(world, sig)
    assert int(spec.tables["overlay"]["sequence"].iloc[0]) == 8
    assert _overlay_doc(sig, 8)["sequence"] == 8


def test_p3_winner_is_the_estimator(world, db, tmp_path):
    sig = _signals(tmp_path)
    league = pd.DataFrame([
        {"universe": "site_etfs", "estimator": "sample", "realized_vol": 0.12, "rank": 2},
        {"universe": "site_etfs", "estimator": "oas", "realized_vol": 0.11, "rank": 1},
        {"universe": "sectors", "estimator": "mp_denoise", "realized_vol": 0.10, "rank": 1},
    ])
    aid, _ = publish_artifact(db, tmp_path / "artifacts", "cov.league", {"league": league})
    _run(world, sig)
    inp = _overlay_doc(sig, 1)["inputs"]
    assert inp["cov_estimator"] == "oas" and inp["cov_artifact"] == aid


def test_p2_best_model_replaces_the_diagonal(world, db, tmp_path):
    sig = _signals(tmp_path)
    league = pd.DataFrame([
        {"ticker": "SPY", "model": "ewma", "qlike": -8.0, "rank_qlike": 2},
        {"ticker": "SPY", "model": "gjr", "qlike": -8.2, "rank_qlike": 1},
    ])
    # var_1d_gjr = 1e-4 (decimal^2, daily) -> annual variance 1e-4 x 252 = 0.0252 -> vol sqrt(0.0252) = 0.158745.
    fc = pd.DataFrame([{"ticker": "SPY", "asof": "2026-06-01", "var_1d_ewma": 2e-4, "var_1d_gjr": 1e-4}])
    aid, _ = publish_artifact(db, tmp_path / "artifacts", "vol.forecast_league", {"league": league, "forecasts": fc})
    spec = _run(world, sig)
    doc = _overlay_doc(sig, 1)
    # SPY is the largest combined weight (0.04 vs TLT 0.01): its model names vol_model.
    assert doc["inputs"]["vol_model"] == "gjr" and doc["inputs"]["vol_artifact"] == aid
    names = spec.tables["names"].set_index("symbol")
    assert names.loc["SPY", "diag_source"] == "p2:gjr"
    assert names.loc["SPY", "vol_forecast"] == pytest.approx(math.sqrt(0.0252), abs=1e-12)  # 0.158745
    assert names.loc["TLT", "diag_source"] == "ewma"


def test_reported_regime_and_var_never_change_the_number(world, db, tmp_path):
    sig = _signals(tmp_path)
    base = _run(world, sig)
    root = tmp_path / "artifacts"
    probs = pd.DataFrame({"date": pd.to_datetime(["2026-05-29", "2026-06-01"]), "p_high": [0.2, 0.83]})
    publish_artifact(db, root, "regime.hmm", {"probs": probs})
    summary = pd.DataFrame([
        {"book": "core", "method": "fhs", "alpha": 0.99, "horizon": 1, "var": 0.021, "var_usd": 2100.0},
        {"book": "core", "method": "fhs", "alpha": 0.95, "horizon": 1, "var": 0.014, "var_usd": 1400.0},
        {"book": "tech", "method": "fhs", "alpha": 0.99, "horizon": 1, "var": 0.03, "var_usd": 3000.0},
    ])
    publish_artifact(db, root, "risk.mc_atlas", {"summary": summary}, finished=NOW - timedelta(hours=10))
    intraday = summary.assign(var=lambda d: d["var"] * 1.5)  # newer: 0.021 x 1.5 = 0.0315
    publish_artifact(db, root, "risk.mc_intraday", {"summary": intraday}, finished=NOW - timedelta(minutes=5))
    again = _run(world, sig)  # overlay-1.json is still there: this one is sequence 2
    doc = _overlay_doc(sig, 2)
    assert doc["reported"]["regime_p_high"] == pytest.approx(0.83)
    assert doc["reported"]["mc_var_99_1d_core"] == pytest.approx(0.0315)
    for col in ("exposure_scale", "vol_forecast"):
        assert again.tables["overlay"][col].iloc[0] == base.tables["overlay"][col].iloc[0]


def test_a_reshaped_regime_artifact_never_stops_the_number(world, db, tmp_path):
    sig = _signals(tmp_path)
    probs = pd.DataFrame({"date": pd.to_datetime(["2026-06-01"]), "p_low": [0.4]})  # no p_high
    aid, _ = publish_artifact(db, tmp_path / "artifacts", "regime.hmm", {"probs": probs})
    spec = _run(world, sig)
    doc = _overlay_doc(sig, 1)
    validate(doc, OVERLAY_SCHEMA_PATH)
    assert doc["reported"]["regime_p_high"] is None
    assert any("regime.hmm" in n and aid in n and "p_high" in n for n in spec.notes)


def test_a_corrupt_mc_artifact_never_stops_the_number(world, db, tmp_path):
    sig = _signals(tmp_path)
    summary = pd.DataFrame([{"book": "core", "method": "fhs", "alpha": 0.99, "horizon": 1, "var": 0.02}])
    _, path = publish_artifact(db, tmp_path / "artifacts", "risk.mc_atlas", {"summary": summary})
    (path / "summary.parquet").write_bytes(b"not a parquet file")
    spec = _run(world, sig)
    doc = _overlay_doc(sig, 1)
    assert doc["reported"]["mc_var_99_1d_core"] is None
    assert any("risk.mc_atlas" in n and "unreadable" in n for n in spec.notes)


def test_a_non_finite_p2_variance_falls_back_to_ewma_with_a_note(world, db, tmp_path):
    sig = _signals(tmp_path)
    league = pd.DataFrame([{"ticker": "SPY", "model": "gjr", "qlike": -8.2, "rank_qlike": 1}])
    fc = pd.DataFrame([{"ticker": "SPY", "asof": "2026-06-01", "var_1d_gjr": float("nan")}])
    publish_artifact(db, tmp_path / "artifacts", "vol.forecast_league", {"league": league, "forecasts": fc})
    spec = _run(world, sig)
    assert spec.tables["names"].set_index("symbol").loc["SPY", "diag_source"] == "ewma"
    assert any("SPY" in n and "gjr" in n and "EWMA(0.94)" in n for n in spec.notes)


def test_fewer_than_thirty_sessions_is_refused(world, db, tmp_path, monkeypatch):
    sig = _signals(tmp_path)
    monkeypatch.setattr(overlay, "LOOKBACK", 29)  # 29 sessions of returns < the 30 floor
    with pytest.raises(ValueError, match="29 sessions"):
        _run(world, sig)
    assert not list(sig.glob("overlay-*.json"))


# ------------------------------------------------------- the stage review's fix wave
def _max_age_minutes() -> int:
    """book.example.sexp's desk block: ``(overlay_max_age_minutes N)``."""
    from ohcamel_quant.config import REPO_ROOT

    text = (REPO_ROOT / "book.example.sexp").read_text()
    m = re.search(r"\(overlay_max_age_minutes (\d+)\)", text)
    assert m is not None
    return int(m.group(1))


def test_an_overlay_run_precedes_every_sizing_moment_within_the_desks_max_age():
    # B-C1. The desk sizes at 18:45 (signals-daily's documents), 19:15 (the research service,
    # a constant here) and 09:30 (the open). Each needs a scheduled signals.overlay run at most
    # overlay_max_age_minutes (60, the book's) before it, or it sizes at the fallback.
    from ohcamel_quant.deck.clock import NY
    from ohcamel_quant.jobs.cron import fire_times, parse_cron
    from ohcamel_quant.jobs.schedules import DEFAULT_PATH, load_schedules, parse_duration

    sched = load_schedules(DEFAULT_PATH)
    daily = [e for e in sched.entries if e.kind == "signals.daily"]
    assert [e.cron for e in daily] == ["45 18 * * 1-5"]
    runs = sorted(t for e in sched.entries if e.kind == "signals.overlay"
                  for t in fire_times(parse_cron(e.cron), date(2026, 6, 2)))
    max_age = _max_age_minutes()
    assert max_age == 60
    for hh, mm, who in ((18, 45, "signals-daily"), (19, 15, "the research service"), (9, 30, "the open")):
        moment = datetime(2026, 6, 2, hh, mm, tzinfo=NY).astimezone(UTC)
        before = [t for t in runs if t <= moment]
        assert before, f"no overlay run before {who}"
        age = (moment - before[-1]).total_seconds() / 60
        assert 0 <= age <= max_age, f"{who}: the newest overlay is {age:.0f} min old"
    assert parse_duration("35m") == sched.max_age_s["signals.overlay"]  # the kind's own max age is kept


def test_the_sources_table_names_every_document_that_fed_the_targets(world, db, tmp_path):
    # B-I4: one row per cell whose newest document fed combined_targets.
    sig = _signals(tmp_path)
    spec = _run(world, sig)
    src = spec.tables["sources"].sort_values("slug").reset_index(drop=True)
    assert list(src.columns) == ["slug", "sequence", "as_of", "capital_fraction", "path"]
    assert src.to_dict("records") == [
        {"slug": "exp_a01_spy", "sequence": 3, "as_of": None, "capital_fraction": 0.025,
         "path": "exp_a01_spy-2026-06-01.json"},
        {"slug": "farm_mix_fixture", "sequence": 2514, "as_of": None, "capital_fraction": 0.025,
         "path": "farm_mix_fixture-2026-06-01.json"},
    ]


def test_bars_two_sessions_old_are_refused_and_nothing_is_written(world, db, tmp_path, monkeypatch):
    # B-I5: the fixture's bars end Mon 2026-06-01. Run on Thu 06-04: the previous session is Wed
    # 06-03, one behind it is Tue 06-02, and 06-01 is older: refused, naming both dates.
    sig = _signals(tmp_path)
    monkeypatch.setattr(overlay, "_now", lambda: datetime(2026, 6, 4, 14, 7, tzinfo=UTC))
    with pytest.raises(ValueError, match=r"the bars end 2026-06-01, more than one session behind 2026-06-03"):
        _run(world, sig)
    assert not list(sig.glob("overlay-*.json"))
    # One session behind the previous session is still fresh: Wed 06-03's run, previous 06-02.
    assert overlay.bars_refusal(date(2026, 6, 1), date(2026, 6, 3)) is None


def test_a_materially_negative_variance_raises():
    # B minor: [[1, 2], [2, 1]] is not PSD; w = (1, -1): w'Sw = 1 - 4 + 1 = -2, far below
    # -1e-12 x w' diag(S) w = -2e-12. A tiny negative from rounding is still 0.
    cov = pd.DataFrame([[1.0, 2.0], [2.0, 1.0]], index=["A", "B"], columns=["A", "B"])
    with pytest.raises(ValueError, match="materially negative"):
        overlay.forecast_vol({"A": 1.0, "B": -1.0}, cov, None)
    near = pd.DataFrame([[1.0, 1.0 + 1e-15], [1.0 + 1e-15, 1.0]], index=["A", "B"], columns=["A", "B"])
    assert overlay.forecast_vol({"A": 1.0, "B": -1.0}, near, None) == 0.0
