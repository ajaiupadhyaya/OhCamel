"""Lane B, B5: heavy synchronous endpoints answer 202 {job} over their synchronous caps.

Under the caps they are unchanged (the existing router tests cover that).
Here: the cap boundaries and the job handlers' plumbing. Hand derivation for
the risk case: the committed SPY/TLT/GLD fixtures give n = 2513 daily
returns (measured 2026-10-06; the risk tests assert > 2400), so with window
500 there are x = 2013 out-of-sample days and ceil(x / 150) = 14.
refit_every 20 >= 14 needs no coarsening. refit_every 5 < 14 is over the cap
but is the Risk page's smallest choice (SYNC_COARSEN_MIN_REFIT), so it is
coarsened synchronously (200) as before B5. refit_every 4 < 14 is over the
cap and finer than the page offers -> job.
"""

from __future__ import annotations

import gc
import json
import tracemalloc

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ohcamel_quant.api.app import create_app
from ohcamel_quant.api.routers import backtest as B
from ohcamel_quant.api.routers.jobs import get_jobs_db_path
from ohcamel_quant.backtest import validation as V
from ohcamel_quant.backtest.engine import EngineConfig, StrategyContext
from ohcamel_quant.backtest.strategies import get_strategy
from ohcamel_quant.jobs.admission import class_budget
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.jobs.db import connect
from ohcamel_quant.jobs.handlers import api_heavy
from ohcamel_quant.jobs.kinds import get_kind
from ohcamel_quant.jobs.queue import get_job

PF = {"holdings": [{"ticker": "SPY", "weight": 0.5}, {"ticker": "TLT", "weight": 0.3},
                   {"ticker": "GLD", "weight": 0.2}], "notional": 2_000_000}
NINE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]


@pytest.fixture
def api(tmp_path):
    app = create_app()
    db = tmp_path / "jobs.sqlite"
    app.dependency_overrides[get_jobs_db_path] = lambda: db
    return TestClient(app), db


def _job(client_db, r, kind):
    client, db = client_db
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job"]["kind"] == kind and body["job"]["state"] == "queued" and body["notes"]
    return get_job(connect(db), body["job"]["id"])


def test_risk_backtest_over_the_refit_cap_becomes_a_job(api):
    """Only refits finer than the Risk page offers (below SYNC_COARSEN_MIN_REFIT) become jobs."""
    from ohcamel_quant.api.routers.risk import SYNC_COARSEN_MIN_REFIT

    client, _ = api
    assert SYNC_COARSEN_MIN_REFIT == 5
    job = _job(api, client.post("/api/risk/backtest", json={**PF, "refit_every": 4}), "api.risk_backtest")
    assert job.params["refit_every"] == 4 and job.submitted_by == "api" and job.priority == 0


@pytest.mark.parametrize("window", [250, 500, 1000])
@pytest.mark.parametrize("refit", [5, 20, 60])
def test_every_request_the_risk_page_can_send_still_answers_200(api, window, refit):
    """RiskTab.tsx (moved to /risk by Lane P) sends window 250/500/1000 and refit
    5/20/60, and reads `q.data?.ranking_by_fz0[0]` from any 2xx, so a 202 {job}
    would be a TypeError during render. Measured 2026-10-06 on the fixtures
    (n = 2513): refit 5 is over the 150-refit cap at every window (coarsened to
    16 / 14 / 11) and must stay a synchronous 200 with the capped note, as
    before B5; refit 20 and 60 are under the cap. About 1 s each, 9 s in all."""
    client, _ = api
    r = client.post("/api/risk/backtest", json={**PF, "window": window, "refit_every": refit})
    assert r.status_code == 200, r.text
    j = r.json()
    assert "job" not in j and j["ranking_by_fz0"]
    if refit == 5:
        assert j["refit_every_effective"] > 5
        assert any("refit" in n and "capped" in n for n in j["notes"])
    else:
        assert j["refit_every_effective"] == refit


def test_the_strategy_lab_never_sends_an_over_cap_grid(api):
    """SweepTab and WalkForwardTab disable Run when the grid has more than
    caps.grid_combinations combinations. While that cap is the synchronous one,
    the sweep/walk-forward 202 path cannot be reached from the page."""
    client, _ = api
    caps = client.get("/api/backtest/strategies").json()["caps"]
    assert caps["grid_combinations"] == V.MAX_GRID == 64


def test_sweep_and_walkforward_between_64_and_512_combinations_become_jobs(api):
    client, _ = api
    grid194 = {"lookback": list(range(21, 504, 5)), "com": [30, 60]}  # 97 x 2 = 194 combinations
    _job(api, client.post("/api/backtest/sweep", json={"strategy": "tsmom", "tickers": NINE, "grid": grid194}),
         "api.backtest_sweep")
    _job(api, client.post("/api/backtest/walkforward", json={"strategy": "tsmom", "tickers": NINE, "grid": grid194}),
         "api.backtest_walkforward")
    grid600 = {"lookback": list(range(1, 301)), "com": [30, 60]}  # 600 > 512
    r = client.post("/api/backtest/sweep", json={"strategy": "tsmom", "tickers": NINE, "grid": grid600})
    assert r.status_code == 422 and "512" in r.json()["detail"]


def test_compare_over_60_tickers_becomes_a_job(api):
    client, _ = api
    t61 = [f"T{i:03d}" for i in range(61)]
    job = _job(api, client.post("/api/portfolio/compare", json={"tickers": t61}), "api.portfolio_compare")
    assert len(job.params["tickers"]) == 61
    r = client.post("/api/portfolio/compare", json={"tickers": [f"T{i:03d}" for i in range(201)]})
    assert r.status_code == 422


def test_sweep_threads_max_combos_into_param_grid(monkeypatch):
    assert len(V.param_grid({"a": list(range(65))}, max_combos=512)) == 65  # before the spy replaces it
    seen = {}

    def spy(space, max_combos=V.MAX_GRID):
        seen["max_combos"] = max_combos
        raise RuntimeError("stop here")

    monkeypatch.setattr(V, "param_grid", spy)
    with pytest.raises(RuntimeError, match="stop here"):
        V.sweep(None, None, {}, {}, None, max_combos=512)  # type: ignore[arg-type]
    assert seen == {"max_combos": 512}


def test_cscv_in_chunks_is_exact_and_bounded(monkeypatch):
    """512 trials x 2520 sessions, S = 16 (12,870 splits). In one pass the
    (splits x trials) temporaries peak at 462 MiB under tracemalloc (891 MiB
    of process RSS, measured 2026-10-06). In 8 MiB chunks they peak at 75 MiB.
    Each split is scored on its own, so the answer does not change. Since the
    integration of Lanes A and B the chunking lives in the NumPy reference
    kernel (the Rust kernel holds O(splits) memory), so this runs on it."""
    from ohcamel_quant import kernels
    from ohcamel_quant.kernels import reference

    m = np.random.default_rng(11).normal(0.0002, 0.01, (2520, 512))  # a numerical test of CSCV itself
    with kernels.forced("python"):
        tracemalloc.start()
        try:
            chunked = V.cscv_pbo(m, 16)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert peak < 96 * 2**20
        monkeypatch.setattr(reference, "CSCV_CHUNK_BYTES", 2**40)  # one chunk: the single pass this replaces
        whole = V.cscv_pbo(m, 16)
    for k in ("pbo", "n_combinations", "selected_counts", "logits"):
        assert chunked[k] == whole[k]
    for k, v in whole["degradation"].items():
        assert np.allclose(chunked["degradation"][k], v, rtol=0, atol=1e-12)


def test_sweep_keeps_no_more_than_its_footprint_estimate(market):
    """Measured 2026-10-06: 32 tsmom combinations on the nine committed ETFs
    (2514 sessions) keep 10.5 MiB, against an estimate of 11.7 MiB. Before B5
    a sweep kept each result's weights, contributions and trades, plus a
    second copy of the weights: 3.4 frames per combination instead of one."""
    px = market.prices(NINE).data
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        sw = V.sweep(StrategyContext(px), get_strategy("tsmom"), {}, {"lookback": list(range(63, 319, 8))},
                     EngineConfig())
        gc.collect()
        kept = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    est = V.sweep_footprint_bytes(len(sw.combos), px.shape[1], len(px))
    assert 0.6 * est <= kept <= est
    assert sw.results[0].trades is None and sw.results[0].contributions.empty
    assert np.shares_memory(sw.weights[0].to_numpy(), sw.results[0].weights.to_numpy())


def test_sweep_refuses_a_footprint_over_max_bytes(market):
    px = market.prices(NINE).data
    with pytest.raises(ValueError, match="MiB"):
        V.sweep(StrategyContext(px), get_strategy("tsmom"), {}, {"lookback": [63, 126, 252]}, EngineConfig(),
                max_bytes=1024)


def test_the_job_sweep_caps_are_pinned_and_fit_class_m():
    """Measured 2026-10-06 in the worktree venv (macOS arm64). Each figure is
    the peak RSS of one process running the router's _do_sweep and the JSON
    encoding the job stores, with JOB_SWEEP_BYTES = 192 MiB:
      - 512 combinations x the nine committed ETFs, 2514 sessions (estimate
        187 MiB): 483 MiB;
      - 142 combinations x 60 tickers x 2520 sessions (estimate 191.7 MiB, the
        largest 60-ticker sweep the cap admits; a synthetic panel, used for the
        measurement only): 485 MiB as a sweep, 388 MiB as a walk-forward.
    Both stay under mem class M (640 MiB), with about 155 MiB to spare. Before
    B5, CSCV alone took 891 MiB on a 512-trial sweep."""
    assert (B.JOB_MAX_GRID, B.JOB_SWEEP_BYTES) == (512, 192 * 2**20)
    assert get_kind("api.backtest_sweep").mem_class == get_kind("api.backtest_walkforward").mem_class == "M"
    assert class_budget("M") == 640 * 2**20
    assert V.sweep_footprint_bytes(512, 9, 2520) <= B.JOB_SWEEP_BYTES  # the committed universe: the full grid
    assert V.sweep_footprint_bytes(142, 60, 2520) <= B.JOB_SWEEP_BYTES < V.sweep_footprint_bytes(143, 60, 2520)
    assert V.sweep_footprint_bytes(512, 60, 2520) > 3 * B.JOB_SWEEP_BYTES  # the review's case is refused


def test_a_sweep_over_the_job_footprint_is_refused_before_queueing(api, monkeypatch):
    client, db = api
    monkeypatch.setattr(B, "JOB_SWEEP_BYTES", 32 * 2**20)  # 194 x 9 tickers x 2514 sessions needs 70.7 MiB
    grid194 = {"lookback": list(range(21, 504, 5)), "com": [30, 60]}
    r = client.post("/api/backtest/sweep", json={"strategy": "tsmom", "tickers": NINE, "grid": grid194})
    assert r.status_code == 422 and "MiB" in r.json()["detail"]
    assert not db.exists()  # nothing was queued


def test_api_handler_stores_the_payload_as_one_result_table():
    ctx = JobContext(job_id="J", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False)
    params = {"strategy": "tsmom", "tickers": NINE, "grid": {"lookback": [126, 252]}, "spa_reps": 200,
              "n_partitions": 8}
    spec = api_heavy.backtest_sweep(params, ctx)
    assert list(spec.tables) == ["result"] and list(spec.tables["result"].columns) == ["payload"]
    payload = json.loads(spec.tables["result"]["payload"].iloc[0])
    assert 0 <= payload["pbo"]["pbo"] <= 1 and payload["deflated_sharpe"]["n_trials"] == 2
    assert spec.provenance and spec.data_asof is None
    assert any("synchronous cap" in n for n in spec.notes) and any("data_asof" in n for n in spec.notes)
