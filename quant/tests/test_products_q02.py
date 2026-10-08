"""Lane M, M7: regime.hmm. Offline fixtures carry no ICE BofA series, so a real run is INSUFFICIENT DATA;
the machinery test swaps in VIXCLS for the OAS input in memory (nothing is published) to exercise the
holdout-once path with a shortened TEST config."""

from __future__ import annotations

import copy
from datetime import timedelta

import pandas as pd
import pytest
from lane_m_harness import T0, is_label, publish_artifact

from ohcamel_quant.data import fixtures
from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import experiment, q02
from ohcamel_quant.products.io import read_table


def ctx(market):
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=market)


def test_q02_short_oas_history_is_insufficient_data(market, tmp_path, monkeypatch):
    monkeypatch.setattr(q02, "DB_PATH", tmp_path / "jobs.sqlite")
    spec = q02.run({}, ctx(market))  # offline: BAMLH0A0HYM2 is not in the fixtures
    assert spec.verdict == "INSUFFICIENT DATA" and "BAMLH0A0HYM2" in spec.verdict_detail
    assert is_label(spec.verdict_detail) and any("BAMLH0A0HYM2" in n for n in spec.notes)


@pytest.fixture
def test_cfg():
    c = copy.deepcopy(experiment.load_config("EXP-Q02"))
    c["model"] = {**c["model"], "ks": [2, 3], "restarts": 3, "min_train_weeks": 80}
    c.update(refit={"first": "2019-07-01", "every_weeks": 26},
             windows={"selection_end": "2022-12-31", "holdout_start": "2023-01-01"},
             sufficiency={"min_selection_weeks": 100, "min_holdout_weeks": 26})
    return c


def _inputs(market):
    m = fixtures.macro(["DGS10", "DGS2", "VIXCLS"])[0]
    return fixtures.history("SPY")[0]["adj_close"], m["DGS10"], m["DGS2"], m["VIXCLS"], []


def test_q02_holdout_is_evaluated_once(market, tmp_path, monkeypatch, test_cfg):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(q02, "DB_PATH", db)
    monkeypatch.setattr(q02, "load_config", lambda exp: test_cfg)
    monkeypatch.setattr(q02, "load_inputs", lambda ctx, cfg: _inputs(market))
    first = q02.run({}, ctx(market))
    assert first.verdict in ("PASS", "DESCRIPTIVE ONLY")
    assert {"probs", "fits", "regression", "holdout", "holdout_forecasts", "gates"} <= set(first.tables)
    assert first.tables["probs"]["p_high"].dropna().between(0, 1).all()
    _, path = publish_artifact(db, root, "regime.hmm", first.tables, verdict=first.verdict)
    second = q02.run({}, ctx(market))
    for t in ("holdout", "holdout_forecasts", "regression", "gates"):
        pd.testing.assert_frame_equal(second.tables[t], read_table(path, t))  # the stored score, unchanged


def test_q02_outage_artifact_does_not_reopen_the_holdout(market, tmp_path, monkeypatch, test_cfg):
    """Evaluate, then an outage run publishes INSUFFICIENT DATA with no holdout table; the next run must carry
    the stored holdout unchanged and never call ``score`` again (holdouts are evaluated once)."""
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    monkeypatch.setattr(q02, "DB_PATH", db)
    monkeypatch.setattr(q02, "load_config", lambda exp: test_cfg)
    monkeypatch.setattr(q02, "load_inputs", lambda ctx, cfg: _inputs(market))
    first = q02.run({}, ctx(market))
    _, path = publish_artifact(db, root, "regime.hmm", first.tables, verdict=first.verdict, finished=T0)

    def outage(ctx, cfg):
        raise DataUnavailable("FRED unreachable")
    monkeypatch.setattr(q02, "load_inputs", outage)
    down = q02.run({}, ctx(market))
    assert down.verdict == "INSUFFICIENT DATA"
    publish_artifact(db, root, "regime.hmm", down.tables, verdict=down.verdict, finished=T0 + timedelta(days=7))

    calls = []
    real = q02.Q.score
    monkeypatch.setattr(q02.Q, "score", lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setattr(q02, "load_inputs", lambda ctx, cfg: _inputs(market))
    third = q02.run({}, ctx(market))
    assert calls == []
    assert third.verdict == first.verdict
    for t in q02.HOLDOUT_TABLES:
        pd.testing.assert_frame_equal(third.tables[t], read_table(path, t))
