"""/api/factors endpoints. Offline: custom ETF factors work; Ken French data is
online-only (503 offline, exercised by the ``live`` tests)."""

from __future__ import annotations

import numpy as np
import pytest

PF = {"holdings": [{"ticker": "XLK", "weight": 0.5}, {"ticker": "XLE", "weight": 0.5}]}
DEFS = [{"name": "MKT", "long": "SPY"}, {"name": "SIZE", "long": "IWM", "short": "SPY"},
        {"name": "TERM", "long": "TLT", "short": "IEF"}]


def _ok(r):
    assert r.status_code == 200, r.text
    j = r.json()
    assert "provenance" in j and "notes" in j and "method" in j
    return j


def test_models(client):
    j = client.get("/api/factors/models").json()
    assert {m["key"] for m in j["models"]} == {"capm", "ff3", "carhart4", "ff5", "ff5mom"}
    assert any(p["name"] == "MKT" for p in j["etf_preset"])


def test_custom_explicit(client):
    j = _ok(client.post("/api/factors/custom", json={**PF, "factors": DEFS, "rolling_window": 126}))
    terms = [row["term"] for row in j["loadings"]]
    assert terms == ["alpha", "MKT", "SIZE", "TERM"]
    mkt = next(r for r in j["loadings"] if r["term"] == "MKT")
    assert 0.7 < mkt["estimate"] < 1.4 and mkt["t_stat"] > 10
    st = j["stats"]
    assert 0.3 < st["r2"] < 1 and st["n_obs"] > 2400 and st["nw_lags"] == 8
    rd = j["risk_decomposition"]
    assert sum(r["variance_contribution"] for r in rd["table"]) == pytest.approx(rd["systematic_variance"])
    assert rd["total_variance"] == pytest.approx(rd["sample_variance"], rel=1e-9)
    tot = j["attribution"]["totals_linked"]
    assert sum(v for k, v in tot.items() if k != "total") == pytest.approx(tot["total"], rel=1e-9)
    assert tot["total"] == pytest.approx(j["fitted_vs_actual"]["data"]["actual"][-1], rel=1e-9)
    assert set(j["rolling"]["columns"]) == {"alpha_ann", "MKT", "SIZE", "TERM", "r2"}
    assert any("Risk-free rate unavailable" in n for n in j["notes"])


def test_custom_preset_drops_unavailable(client):
    j = _ok(client.post("/api/factors/custom", json={"ticker": "QQQ"}))
    names = [f["name"] for f in j["model"]["factors"]]
    assert names[0] == "MKT" and "TERM" in names and "SIZE" in names
    assert any("Preset factors dropped" in n for n in j["notes"])
    assert j["target"]["ticker"] == "QQQ"


def test_custom_single_ticker_exact_beta(client):
    # A holding identical to the only factor: beta 1, alpha 0, R^2 1.
    j = _ok(client.post("/api/factors/custom", json={"ticker": "SPY", "factors": [DEFS[0]]}))
    mkt = next(r for r in j["loadings"] if r["term"] == "MKT")
    assert mkt["estimate"] == pytest.approx(1.0)
    assert j["stats"]["r2"] == pytest.approx(1.0)
    assert any("mechanically inflated" in n for n in j["notes"])
    a = j["attribution"]["cumulative_linked"]["data"]
    np.testing.assert_allclose(a["MKT"], a["total"], atol=1e-9)


def test_custom_validation(client):
    assert client.post("/api/factors/custom", json={"ticker": "SPY", **PF}).status_code == 422
    assert client.post("/api/factors/custom", json={}).status_code == 422
    r = client.post("/api/factors/custom", json={"ticker": "SPY", "factors": [{"name": "X", "long": "ZZZZ"}]})
    assert r.status_code == 503


def test_french_endpoints_offline(client):
    r = client.post("/api/factors/regression", json={"ticker": "SPY", "model": "ff3"})
    assert r.status_code == 503 and "offline" in r.json()["detail"]
    assert client.get("/api/factors/library").status_code == 503
    assert client.get("/api/factors/library", params={"window": "7Y"}).status_code == 422
    assert client.post("/api/factors/regression", json={"ticker": "SPY", "model": "ff9"}).status_code == 422


@pytest.mark.live
def test_live_french_regression_and_library(client):
    j = _ok(client.post("/api/factors/regression", json={**PF, "model": "ff5mom", "start": "2018-01-01"}))
    assert [r["term"] for r in j["loadings"]] == ["alpha", "Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
    assert j["stats"]["r2"] > 0.5
    lib = _ok(client.get("/api/factors/library", params={"window": "1Y"}))
    assert {f["name"] for f in lib["factors"]} >= {"Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"}
    assert lib["trailing"]


def test_custom_rejects_singular_and_empty(client):
    dup = [{"name": "A", "long": "SPY"}, {"name": "B", "long": "SPY"}]
    r = client.post("/api/factors/custom", json={"ticker": "XLK", "factors": dup})
    assert r.status_code == 422 and "collinear" in r.json()["detail"]
    r = client.post("/api/factors/custom", json={"holdings": [{"ticker": "SPY", "weight": 0.0}], "factors": DEFS})
    assert r.status_code == 422


def test_custom_explicit_drops_unservable_factor(client):
    defs = [*DEFS, {"name": "BOGUS", "long": "ZZZZ", "short": "SPY"}]
    j = _ok(client.post("/api/factors/custom", json={**PF, "factors": defs}))
    assert [f["name"] for f in j["model"]["factors"]] == ["MKT", "SIZE", "TERM"]
    assert [d["name"] for d in j["dropped"]] == ["BOGUS"]
    assert j["dropped"][0]["long"] == "ZZZZ" and j["dropped"][0]["reason"]
    assert any("Requested factors dropped" in n and "BOGUS" in n for n in j["notes"])
    assert [row["term"] for row in j["loadings"]] == ["alpha", "MKT", "SIZE", "TERM"]
    ok = _ok(client.post("/api/factors/custom", json={**PF, "factors": DEFS}))
    assert ok["dropped"] == []
