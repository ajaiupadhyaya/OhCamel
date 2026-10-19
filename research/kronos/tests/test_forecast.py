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
