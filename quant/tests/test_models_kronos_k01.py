"""EXP-K01's scorer. Kronos itself is never run here (no torch in quant): its forecasts are stood in for by
transformed EWMA forecasts, whose correct scores are known in sign, on the committed real fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from ohcamel_quant.models import kronos_k01 as K
from ohcamel_quant.models import vol_league as V

ROOT = K.repo_root()
CONFIG = ROOT / "research/experiments/EXP-K01/config.yaml"
N = 160


@pytest.fixture(scope="module")
def cfg():
    return K.load_config(CONFIG)


@pytest.fixture(scope="module")
def spy():
    return K.fixture_returns(ROOT / "fixtures/history", "SPY")


@pytest.fixture
def light_incumbents(monkeypatch):
    """EWMA and a 1.2x EWMA standing in for the GARCH family, for tests about plumbing rather than GARCH
    (the NumPy-reference GARCH fits take ~10 s a name). One real-GARCH test below covers the real path."""
    def inc(r, n, cfg):
        f, _ = V.ewma_oos(r, n, cfg["incumbents"]["ewma_lambda"])
        return {"garch": 1.2 * f, "ewma": f}, {}
    monkeypatch.setattr(K, "incumbent_forecasts", inc)


@pytest.fixture(scope="module")
def tripled(spy, cfg):
    return K.score_name("SPY", spy, fake_kronos(spy, 3.0), cfg)


def fake_kronos(ret: pd.Series, scale: float, n: int = N, symbol: str = "SPY") -> pd.DataFrame:
    """Rows shaped like research/kronos's forecasts.csv: Kronos := scale x EWMA's out-of-sample forecast."""
    f, _ = V.ewma_oos(ret.to_numpy(float), n)
    tail = ret.iloc[-n:]
    sd = np.sqrt(f)
    return pd.DataFrame({"symbol": symbol, "date": tail.index, "var_kronos": scale * f,
                         "var_kronos_actual_anchor": scale * f * 1.1, "q05_kronos": -1.645 * sd,
                         "distinct_frac": 0.7, "prev_close_actual": 100.0, "prev_close_rec": 99.9, "seed": 1})


def test_the_committed_config_is_approved_and_a_draft_would_say_so(cfg):
    assert cfg["approved"] == "2026-10-19"
    assert "DRAFT" not in K.verdict(pooled_p=0.001, mcs_names=9, n_names=9, cfg=cfg).detail
    v = K.verdict(pooled_p=0.001, mcs_names=9, n_names=9, cfg={**cfg, "approved": None})
    assert v.label == K.PASS and "DRAFT" in v.detail
    short = K.verdict(pooled_p=0.0, mcs_names=0, n_names=9, cfg={**cfg, "approved": None}, short=["XLE"])
    assert "draft-pre-registration result" in K.report(K.Result(short, {}, {}, *[pd.DataFrame()] * 5, ("", "")))


@pytest.mark.parametrize("p, mcs, label", [
    (0.01, 5, K.PASS), (0.01, 4, K.FAIL), (0.05, 9, K.FAIL), (0.2, 0, K.FAIL), (float("nan"), 9, K.FAIL)])
def test_verdict_is_the_preregistered_rule_applied_literally(cfg, p, mcs, label):
    assert K.verdict(pooled_p=p, mcs_names=mcs, n_names=9, cfg=cfg).label == label


def test_short_names_make_the_verdict_insufficient_and_name_them(cfg):
    v = K.verdict(pooled_p=0.0, mcs_names=9, n_names=9, cfg=cfg, short=["XLE (12 sessions)"])
    assert v.label == K.INSUFFICIENT and "XLE" in v.detail


def test_one_sided_dm_points_the_right_way():
    rng = np.random.default_rng(0)
    base = rng.normal(1.0, 0.1, 400)
    better = K.one_sided_dm(base - 0.05, base)
    worse = K.one_sided_dm(base + 0.05, base)
    assert better["stat"] < 0 and better["p_one_sided"] < 0.01
    assert worse["stat"] > 0 and worse["p_one_sided"] > 0.99


def test_mincer_zarnowitz_recovers_a_known_slope():
    rng = np.random.default_rng(1)
    f = rng.uniform(1, 2, 2000)
    y = 0.5 + 2.0 * f + rng.normal(0, 0.1, f.size)
    mz = K.mincer_zarnowitz(y, f)
    assert mz["a"] == pytest.approx(0.5, abs=0.05) and mz["b"] == pytest.approx(2.0, abs=0.05)
    assert mz["t_b_eq_1"] > 10


def test_a_tripled_ewma_loses_to_ewma_and_is_called_over_dispersed(tripled):
    s = tripled
    lg = pd.DataFrame(s.rows).set_index("model")
    assert set(lg.index) - set(s.dropped) == {"kronos", "garch", "gjr", "egarch", "ewma"} - set(s.dropped)
    assert {"kronos", "ewma"} <= set(lg.index) and lg.loc["kronos", "qlike"] > lg.loc["ewma", "qlike"]
    assert next(d for d in s.dm if d["vs"] == "ewma")["stat"] > 0
    assert s.calibration["mean_forecast_over_mean_r2"] == pytest.approx(
        3.0 * s.calibration["ewma_mean_over_mean_r2"], rel=1e-9)
    assert s.var95["expected"] == pytest.approx(0.05 * N)
    assert list(s.losses.columns)[-1] == "kronos_actual_anchor"


def test_ewma_itself_ties_ewma(spy, cfg, light_incumbents):
    s = K.score_name("SPY", spy, fake_kronos(spy, 1.0), cfg)
    np.testing.assert_allclose(s.losses["kronos"], s.losses["ewma"])


def test_forecasts_must_be_the_fixtures_last_sessions(spy, cfg):
    fk = fake_kronos(spy, 1.0)
    fk["date"] = fk["date"] - pd.Timedelta(days=1)
    with pytest.raises(K.Refused, match="last 160 sessions"):
        K.score_name("SPY", spy, fk, cfg)


def write_run(tmp: Path, df: pd.DataFrame, cfg_text: str, complete: bool = True) -> tuple[Path, Path]:
    c = tmp / "config.yaml"
    c.write_text(cfg_text)
    f = tmp / "forecasts.csv"
    df.to_csv(f, index=False)
    Path(f"{f}.meta.json").write_text(json.dumps(
        {"complete": complete, "config_sha256": hashlib.sha256(c.read_bytes()).hexdigest()}))
    return c, f


def test_partial_mismatched_or_unsidecarred_runs_are_refused(tmp_path, spy):
    df = fake_kronos(spy, 1.0)
    c, f = write_run(tmp_path, df, CONFIG.read_text(), complete=False)
    with pytest.raises(K.Refused, match="partial"):
        K.load_forecasts(f, c)
    c, f = write_run(tmp_path, df, CONFIG.read_text())
    c.write_text(CONFIG.read_text() + "\n# edited after the run\n")
    with pytest.raises(K.Refused, match="different config"):
        K.load_forecasts(f, c)
    Path(f"{f}.meta.json").unlink()
    with pytest.raises(K.Refused, match="sidecar"):
        K.load_forecasts(f, c)
    c, f = write_run(tmp_path, df.assign(var_kronos=0.0), CONFIG.read_text())
    with pytest.raises(K.Refused, match="positive"):
        K.load_forecasts(f, c)


def test_end_to_end_on_two_names_fails_and_reports_verdict_first(tmp_path, light_incumbents):
    raw = yaml.safe_load(CONFIG.read_text())
    raw["names"] = ["SPY", "TLT"]
    fx = ROOT / raw["fixtures"]
    df = pd.concat([fake_kronos(K.fixture_returns(fx, s), 2.0, symbol=s) for s in raw["names"]])
    c, f = write_run(tmp_path, df, yaml.safe_dump(raw))
    res = K.run(c, f)
    assert res.verdict.label == K.FAIL                       # 2x EWMA cannot beat EWMA
    assert res.pooled["stat"] > 0 and res.pooled["days"] == N
    text = K.report(res)
    assert text.splitlines()[2].startswith(f"**Verdict: {K.FAIL}**")
    assert "Diagnostic, not eligible" in text


def test_too_few_sessions_is_insufficient(tmp_path, spy):
    raw = yaml.safe_load(CONFIG.read_text())
    raw["names"] = ["SPY"]
    c, f = write_run(tmp_path, fake_kronos(spy, 1.0, n=40), yaml.safe_dump(raw))
    res = K.run(c, f)
    assert res.verdict.label == K.INSUFFICIENT and "SPY (40 sessions)" in res.verdict.detail
    assert K.report(res).startswith("# EXP-K01")


def test_an_incumbent_whose_refit_fails_is_dropped_and_reported_not_guessed(spy, cfg, monkeypatch):
    real = V.garch_oos

    def flaky(r, kind, **kw):
        if kind == "egarch":
            raise ValueError("the GARCH fit ended at a non-finite likelihood")
        return real(r, kind, **kw)
    monkeypatch.setattr(V, "garch_oos", flaky)
    s = K.score_name("SPY", spy, fake_kronos(spy, 1.0), cfg)
    assert "egarch" in s.dropped and "non-finite" in s.dropped["egarch"]
    assert "egarch" not in {r["model"] for r in s.rows} and "egarch" not in s.losses.columns


def test_the_reference_egarch_fit_raises_garch_rs_error_instead_of_crashing(monkeypatch):
    from ohcamel_quant.kernels import reference as R
    y = 100 * np.random.default_rng(3).normal(0, 0.01, 300)
    monkeypatch.setattr(R, "_variance_path", lambda *a, **k: None)   # the infeasible path garch.rs maps to Err
    with pytest.raises(ValueError, match="non-finite likelihood"):
        R.garch_fit(y, "egarch", None)


def test_garch_oos_can_skip_the_next_day_fit_without_changing_the_past(spy):
    r = spy.to_numpy(float)
    a, nx = V.garch_oos(r, "garch", eval_days=42)
    b, nb = V.garch_oos(r, "garch", eval_days=42, next_day=False)
    np.testing.assert_array_equal(a, b)
    assert np.isfinite(nx) and np.isnan(nb)


def test_an_unadjusted_split_is_detected_not_repaired():
    idx = pd.bdate_range("2025-11-01", periods=40)
    r = pd.Series(0.001, index=idx)
    r.iloc[25] = -0.5
    assert K.split_suspects(r, pd.Timestamp("2025-07-01")) == [str(idx[25].date())]
    assert K.split_suspects(r, idx[26]) == []
    fx = K.repo_root() / "fixtures/history"
    for s in ("XLE", "XLK"):          # the committed fixtures' known defect, pinned so a fix shows up here
        assert K.split_suspects(K.fixture_returns(fx, s), pd.Timestamp("2025-07-01")) == ["2025-12-05"]
    assert K.split_suspects(K.fixture_returns(fx, "SPY"), pd.Timestamp("2016-07-01")) == []
