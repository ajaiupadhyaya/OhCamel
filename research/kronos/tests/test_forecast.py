from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ohcamel_kronos import forecast as F

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "research/experiments/EXP-K01/config.yaml"


def test_the_committed_config_is_the_preregistered_design():
    cfg = F.load_config(CONFIG)
    assert cfg["approved"] == "2026-10-19"              # owner approved before any scored result existed
    assert cfg["eval_start"] == "2025-07-01" and cfg["kronos"]["weights_committed"] == "2025-06-30"
    s = cfg["sampling"]
    assert (s["lookback"], s["n_samples"], s["top_p"], s["top_k"], s["temperature"]) == (400, 128, 1.0, 0, 1.0)
    assert s["lookback"] <= cfg["kronos"]["max_context"]


def test_every_name_has_real_bars_reaching_back_a_full_context():
    cfg = F.load_config(CONFIG)
    for sym in cfg["names"]:
        b = F.load_bars(ROOT / cfg["fixtures"], sym)
        days = F.eval_days(b, cfg["eval_start"], cfg["sampling"]["lookback"])
        assert days[0] >= pd.Timestamp("2025-07-01") and len(days) >= cfg["sufficiency"]["min_eval_sessions"]


def test_bars_without_provenance_or_marked_synthetic_are_refused(tmp_path):
    pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2024-01-02"])).to_parquet(tmp_path / "X.parquet")
    with pytest.raises(ValueError, match="sidecar"):
        F.load_bars(tmp_path, "X")
    (tmp_path / "X.parquet.meta.json").write_text(json.dumps({"synthetic": True}))
    with pytest.raises(ValueError, match="synthetic"):
        F.load_bars(tmp_path, "X")


def test_eval_days_refuse_a_short_history():
    idx = pd.bdate_range("2025-01-01", "2025-08-01")
    b = pd.DataFrame({"close": np.ones(len(idx))}, index=idx)
    with pytest.raises(ValueError, match="need 400"):
        F.eval_days(b, "2025-07-01", 400)


def test_day_seeds_are_stable_and_distinct():
    d = pd.Timestamp("2025-07-01")
    assert F.day_seed(1, "SPY", d) == F.day_seed(1, "SPY", d)
    assert F.day_seed(1, "SPY", d) != F.day_seed(1, "QQQ", d)
    assert F.day_seed(1, "SPY", d) != F.day_seed(1, "SPY", d + pd.Timedelta(days=1))


def _fake_run(tmp_path, monkeypatch, cfg_text):
    """forecast.run with the model and sampler stubbed: the bookkeeping is what is under test."""
    import yaml

    cfg = yaml.safe_load(cfg_text)
    cfg["names"] = ["SPY", "QQQ"]
    c = tmp_path / "config.yaml"
    c.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(F, "load_models", lambda cfg: None)
    calls, n = [], [0]

    def row(models, bars, sym, day, s, mc, seed):
        calls.append(sym)
        n[0] += 1                                       # never reset: a re-forecast gets new values
        return {"symbol": sym, "date": day.date().isoformat(), "var_kronos": float(n[0]),
                "var_kronos_actual_anchor": 1.0, "q05_kronos": -0.01, "distinct_frac": 0.5,
                "prev_close_actual": 1.0, "prev_close_rec": 1.0, "seed": 1}
    monkeypatch.setattr(F, "forecast_row", row)
    return c, calls


def test_an_update_reforecasts_only_its_symbols_and_records_why(tmp_path, monkeypatch):
    c, calls = _fake_run(tmp_path, monkeypatch, CONFIG.read_text())
    out = tmp_path / "forecasts.csv"
    F.run(c, ROOT, out, limit_days=None, progress=lambda m: None)
    before = pd.read_csv(out)
    calls.clear()
    with pytest.raises(ValueError, match="must say why"):
        F.run(c, ROOT, out, update=["SPY"], progress=lambda m: None)
    F.run(c, ROOT, out, update=["SPY"], reason="fixture repaired", progress=lambda m: None)
    after = pd.read_csv(out)
    assert set(calls) == {"SPY"}
    pd.testing.assert_frame_equal(after[after.symbol == "QQQ"].reset_index(drop=True),
                                  before[before.symbol == "QQQ"].reset_index(drop=True))
    assert not after[after.symbol == "SPY"]["var_kronos"].equals(before[before.symbol == "SPY"]["var_kronos"])
    assert list(after["symbol"].unique()) == ["SPY", "QQQ"] and len(after) == len(before)
    meta = json.loads((tmp_path / "forecasts.csv.meta.json").read_text())
    assert meta["complete"] and meta["amendments"][-1]["symbols"] == ["SPY"]
    assert meta["amendments"][-1]["reason"] == "fixture repaired"


def test_an_update_needs_a_complete_run_to_update(tmp_path, monkeypatch):
    c, _ = _fake_run(tmp_path, monkeypatch, CONFIG.read_text())
    out = tmp_path / "forecasts.csv"
    F.run(c, ROOT, out, limit_days=3, progress=lambda m: None)
    with pytest.raises(ValueError, match="not a complete run"):
        F.run(c, ROOT, out, update=["SPY"], reason="x", progress=lambda m: None)


def test_the_v2_amendment_changes_nothing_in_the_design():
    """v2 re-forecasts three names and keeps six; that is only legitimate if the design is v1's exactly."""
    cfg = F.load_config(CONFIG)
    assert cfg["methodology_version"] == 2 and "v2 2026-10-19" in cfg["amendments"][0]
    v1 = {"names": ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"], "eval_start": "2025-07-01",
          "sampling": {"lookback": 400, "n_samples": 128, "temperature": 1.0, "top_k": 0, "top_p": 1.0, "clip": 5.0,
                       "device": "cpu", "seed": 20261019, "torch_threads": 4},
          "model_revision": "901c26c1332695a2a8f243eb2f37243a37bea320"}
    assert cfg["names"] == v1["names"] and cfg["eval_start"] == v1["eval_start"] and cfg["sampling"] == v1["sampling"]
    assert cfg["kronos"]["model_revision"] == v1["model_revision"]
