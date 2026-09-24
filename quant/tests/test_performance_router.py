"""/api/performance/analyze against the committed real ETF fixtures (offline)."""

from __future__ import annotations

import numpy as np
import pytest

PF = {"holdings": [{"ticker": "SPY", "weight": 0.6}, {"ticker": "TLT", "weight": 0.3}],
      "benchmark": "SPY"}


def _ok(r):
    assert r.status_code == 200, r.text
    j = r.json()
    assert "provenance" in j and "notes" in j and "method" in j
    assert j["provenance"] and all(not p["synthetic"] for p in j["provenance"])
    return j


def test_analyze_full(client):
    j = _ok(client.post("/api/performance/analyze", json={**PF, "n_trials": 50, "sr_variance": 0.25}))
    s = j["summary"]
    assert j["portfolio"]["observations"] > 2400
    assert j["portfolio"]["cash_weight"] == pytest.approx(0.1)
    assert j["portfolio"]["rebalance"] == "monthly"
    assert s["max_drawdown"] < 0 and s["vol_ann"] > 0
    assert s["cvar"] >= s["var"] > 0
    # rf is unavailable offline -> 0 and the payload says so.
    assert s["rf_ann"] == 0
    assert any("Risk-free rate unavailable" in n for n in j["notes"])
    # the provider's own "risk-free rate unavailable" phrase is not repeated in the note
    assert not any(n.lower().count("risk-free rate unavailable") > 1 for n in j["notes"])
    si = j["sharpe_inference"]
    assert si["deflated"]["dsr"] <= si["psr"]
    assert si["se_hac"] > 0
    rel = j["relative"]
    assert 0 < rel["beta"] < 1 and rel["r2"] > 0.5
    eq = j["equity"]
    assert set(eq["columns"]) == {"portfolio", "benchmark"}
    assert eq["data"]["portfolio"][-1] == pytest.approx(1 + s["total_return"])
    assert min(j["drawdown"]["data"]["portfolio"]) == pytest.approx(s["max_drawdown"])
    assert len(j["drawdowns"]) == 5
    assert j["drawdowns"][0]["depth"] == pytest.approx(s["max_drawdown"])
    rows = j["monthly_table"]["rows"]
    assert rows[0]["year"] == 2016 and "Annual" in rows[0]
    assert set(j["rolling"]["columns"]) == {"return", "vol", "sharpe", "beta"}
    assert j["benchmark_summary"]["n_obs"] == s["n_obs"]
    cw = j["portfolio"]["current_weights"]
    assert sum(cw.values()) == pytest.approx(1.0)
    assert j["portfolio"]["turnover"]["rebalances"] > 100
    assert sum(j["returns_histogram"]["counts"]) == s["n_obs"]


def test_analyze_buy_and_hold_vs_daily(client):
    base = {**PF, "start": "2020-01-01", "end": "2021-12-31"}
    bh = _ok(client.post("/api/performance/analyze", json={**base, "rebalance": "none"}))
    dl = _ok(client.post("/api/performance/analyze", json={**base, "rebalance": "daily"}))
    assert bh["summary"]["total_return"] != pytest.approx(dl["summary"]["total_return"], rel=1e-6)
    assert bh["portfolio"]["turnover"] is None
    assert any("Buy-and-hold" in n for n in bh["notes"])


def test_analyze_single_asset_matches_benchmark(client):
    j = _ok(client.post("/api/performance/analyze",
                        json={"holdings": [{"ticker": "SPY", "weight": 1.0}], "rebalance": "daily"}))
    rel = j["relative"]
    assert rel["beta"] == pytest.approx(1.0) and rel["tracking_error"] == pytest.approx(0.0, abs=1e-12)
    assert rel["up_capture"] == pytest.approx(1.0)
    p, b = j["equity"]["data"]["portfolio"], j["equity"]["data"]["benchmark"]
    np.testing.assert_allclose(p, b)


def test_analyze_no_benchmark(client):
    j = _ok(client.post("/api/performance/analyze", json={**PF, "benchmark": ""}))
    assert j["relative"] is None and j["equity"]["columns"] == ["portfolio"]


def test_analyze_errors(client):
    r = client.post("/api/performance/analyze", json={"holdings": [{"ticker": "ZZZZ", "weight": 1}]})
    assert r.status_code == 503
    r = client.post("/api/performance/analyze", json={**PF, "rebalance": "hourly"})
    assert r.status_code == 422
    r = client.post("/api/performance/analyze", json={**PF, "start": "2026-05-20"})
    assert r.status_code == 503  # too few observations


def test_analyze_zero_exposure_is_422_not_500(client):
    for h in ([{"ticker": "SPY", "weight": 0.0}],
              [{"ticker": "SPY", "weight": 0.5}, {"ticker": "SPY", "weight": -0.5}]):
        r = client.post("/api/performance/analyze", json={"holdings": h})
        assert r.status_code == 422 and "zero gross exposure" in r.json()["detail"]


def test_turnover_counts_calendar_rebalances(client):
    # A single fully-invested asset never drifts; it still rebalances once per month.
    j = _ok(client.post("/api/performance/analyze", json={
        "holdings": [{"ticker": "SPY", "weight": 1.0}], "rebalance": "monthly",
        "start": "2020-01-01", "end": "2020-12-31"}))
    to = j["portfolio"]["turnover"]
    assert to["rebalances"] == 11 and to["annual"] == pytest.approx(0.0)
