"""/api/risk endpoints against the committed real ETF fixtures."""

from __future__ import annotations

import pytest

PF = {"holdings": [{"ticker": "SPY", "weight": 0.5}, {"ticker": "TLT", "weight": 0.3},
                   {"ticker": "GLD", "weight": 0.2}], "notional": 2_000_000}
MODELS = {"historical", "gaussian", "student_t", "cornish_fisher", "ewma", "garch", "gjr_garch", "fhs", "evt_pot"}


def _ok(r):
    assert r.status_code == 200, r.text
    j = r.json()
    assert "provenance" in j and "notes" in j and "method" in j
    return j


def test_summary(client):
    j = _ok(client.post("/api/risk/summary", json=PF))
    assert j["alphas"] == [0.99, 0.95]
    assert set(j["table"]) == MODELS
    assert j["provenance"] and all(not p["synthetic"] for p in j["provenance"])
    for e in j["estimates"]:
        assert e.get("error") is None, e
        assert e["var"] > 0
        if e["valid"]:
            assert e["es"] >= e["var"] - 1e-12
        else:  # Cornish-Fisher outside its domain: ES withheld, VaR flagged
            assert e["model"] == "cornish_fisher" and e["es"] is None
        assert e["var_usd"] == pytest.approx(e["var"] * 2_000_000)
    t = j["table"]
    for m in MODELS:
        assert t[m]["0.99"]["var"] > t[m]["0.95"]["var"]
    assert j["portfolio"]["observations"] > 2400
    assert len(j["returns"]["values"]) == j["portfolio"]["observations"]
    assert sum(j["distribution"]["counts"]) == j["portfolio"]["observations"]
    assert any("daily rebalanced" in n for n in j["notes"])


def test_summary_horizon_and_validation(client):
    j = _ok(client.post("/api/risk/summary", json={**PF, "horizon": 10, "alphas": [0.99]}))
    g = next(e for e in j["estimates"] if e["model"] == "historical")
    assert any("OVERLAPPING" in n for n in g["notes"])
    assert client.post("/api/risk/summary", json={**PF, "alphas": [1.2]}).status_code == 422
    r = client.post("/api/risk/summary", json={"holdings": [{"ticker": "NOTATICKER", "weight": 1}]})
    assert r.status_code == 503


def test_backtest(client):
    j = _ok(client.post("/api/risk/backtest", json={**PF, "window": 500, "refit_every": 20}))
    assert set(j["scorecards"]) == MODELS
    n = len(j["series"]["dates"])
    assert n == j["portfolio"]["observations"] - 500
    for m, card in j["scorecards"].items():
        assert card["T"] == n
        assert len(j["series"]["var"][m]) == n
        assert len(j["series"]["exceptions"][m]) == card["exceptions"]
        for k in ("kupiec", "christoffersen", "traffic_light", "dq", "acerbi_szekely_z2"):
            assert k in card
        assert card["basel_last_250"]["zone"] in {"green", "yellow", "red"}
    assert sorted(j["ranking_by_fz0"]) == sorted(MODELS)
    assert j["compute_seconds"] < 15


def test_backtest_subset_of_models(client):
    j = _ok(client.post("/api/risk/backtest", json={**PF, "models": ["historical", "ewma"], "alpha": 0.975}))
    assert set(j["scorecards"]) == {"historical", "ewma"}
    assert j["scorecards"]["ewma"].get("basel_last_250") is None
    assert client.post("/api/risk/backtest", json={**PF, "models": ["bogus"]}).status_code == 422


def test_decomposition(client):
    j = _ok(client.post("/api/risk/decomposition", json={**PF, "benchmark": "QQQ", "cov_method": "ewma"}))
    pos = j["positions"]
    assert [p["ticker"] for p in pos] == ["SPY", "TLT", "GLD"]
    tot = j["totals"]
    assert sum(p["component_var"] for p in pos) == pytest.approx(tot["parametric_var"])
    assert sum(p["component_es_usd"] for p in pos) == pytest.approx(tot["parametric_es_usd"])
    assert sum(p["hist_component_es"] for p in pos) == pytest.approx(tot["historical_es"])
    assert j["benchmark"]["benchmark"] == "QQQ"
    assert 0 < j["benchmark"]["portfolio_beta"] < 1
    assert j["correlation"]["columns"] == ["SPY", "TLT", "GLD"]


def test_garch_endpoint(client):
    j = _ok(client.post("/api/risk/garch", json={"holdings": [{"ticker": "SPY", "weight": 1.0}],
                                                   "horizon": 10, "forecast_days": 63}))
    for k in ("garch", "gjr"):
        m = j["models"][k]
        assert m["params"]["alpha"] + m["params"]["beta"] < 1
        assert len(m["term_structure"]["index"]) == 63
        assert m["var_es_horizon"]["var"] > m["var_es_1d"]["var"] > 0
        assert len(m["conditional_vol_annualized"]["values"]) == j["portfolio"]["observations"]
    assert j["models"]["gjr"]["params"]["gamma"] > 0
    assert j["comparison"]["preferred_by_bic"] in {"garch", "gjr"}
    assert j["fhs"]["var"] > 0


def test_scenarios_catalogue(client):
    j = _ok(client.get("/api/risk/scenarios"))
    ids = {s["id"] for s in j["scenarios"]}
    assert {"covid_2020", "gfc_2007", "black_monday_1987", "yen_carry_2024"} <= ids
    cov = next(s for s in j["scenarios"] if s["id"] == "covid_2020")
    assert cov["start"] == "2020-02-19" and cov["end"] == "2020-03-23"


def test_historical_stress(client):
    j = _ok(client.post("/api/risk/stress/historical", json={
        "holdings": [{"ticker": "SPY", "weight": 0.6}, {"ticker": "TLT", "weight": 0.4}],
        "scenarios": ["covid_2020", "bear_2022", "black_monday_1987"]}))
    rows = {r["id"]: r for r in j["summary"]}
    assert rows["covid_2020"]["complete"] and -0.36 < rows["covid_2020"]["benchmark_return"] < -0.32
    # 2022: stocks and long bonds fell together
    b22 = next(s for s in j["scenarios"] if s["id"] == "bear_2022")
    assert all(p["return"] < 0 for p in b22["positions"])
    # offline fixtures start in 2016: 1987 must be incomplete, never invented
    assert rows["black_monday_1987"]["complete"] is False
    assert rows["black_monday_1987"]["portfolio_return"] is None
    assert client.post("/api/risk/stress/historical", json={**PF, "scenarios": ["nope"]}).status_code == 422


def test_conditional_stress(client):
    j = _ok(client.post("/api/risk/stress/conditional", json={**PF, "shocks": {"QQQ": -0.15}}))
    pos = {p["ticker"]: p for p in j["positions"]}
    assert pos["QQQ"]["shocked"] and pos["QQQ"]["move"] == -0.15
    assert pos["SPY"]["move"] < 0          # SPY co-moves with QQQ
    assert j["pnl_usd"] == pytest.approx(j["portfolio_return"] * 2_000_000)
    assert sum(p["pnl"] for p in j["positions"]) == pytest.approx(j["portfolio_return"])
    assert client.post("/api/risk/stress/conditional", json={**PF, "shocks": {"SPY": -3}}).status_code == 422


@pytest.mark.live
def test_live_gfc_replay(client):
    j = _ok(client.post("/api/risk/stress/historical", json={
        "holdings": [{"ticker": "SPY", "weight": 1.0}], "scenarios": ["gfc_2007", "lehman_2008"]}))
    rows = {r["id"]: r for r in j["summary"]}
    assert rows["gfc_2007"]["complete"]
    assert -0.60 < rows["gfc_2007"]["portfolio_return"] < -0.45   # S&P 500 peak-to-trough ~ -55%
