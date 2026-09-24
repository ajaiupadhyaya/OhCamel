"""/api/portfolio endpoints against the committed real ETF fixtures (offline).

Offline the FRED 3-month bill (DGS3MO) is not in the fixtures, so endpoints that
need a risk-free rate are exercised with an explicit caller-supplied
``risk_free_rate`` and are also checked to fail loudly (503) without one.
"""

from __future__ import annotations

import numpy as np
import pytest

U = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]
RF = 0.02


def _ok(r):
    assert r.status_code == 200, r.text
    j = r.json()
    assert "provenance" in j and "method" in j
    assert all(not p.get("synthetic") for p in j["provenance"])
    return j


def test_methods_catalog(client):
    j = client.get("/api/portfolio/methods").json()
    names = {m["name"] for m in j["methods"]}
    assert {"min_variance", "max_sharpe", "mean_variance", "risk_parity", "hrp", "herc",
            "max_diversification", "min_cvar", "inverse_volatility", "equal_weight"} <= names
    for m in j["methods"]:
        assert m["reference"] and m["description"]
    assert {e["name"] for e in j["covariance_estimators"]} >= {"lw_constant_corr", "mp_denoise", "oas"}


def test_covariance(client):
    j = _ok(client.post("/api/portfolio/covariance", json={"tickers": U, "estimator": "mp_denoise"}))
    c = j["correlation"]
    assert c["columns"] == U
    assert all(abs(c["data"][t][i] - 1) < 1e-12 for i, t in enumerate(U))
    assert sorted(j["hrp_order"]) == sorted(U)
    assert j["correlation_ordered"]["columns"] == j["hrp_order"]
    sp = j["spectrum"]
    assert sp["lambda_plus"] > sp["lambda_minus"] >= 0
    assert abs(sum(sp["eigenvalues"]) - len(U)) < 1e-9
    assert {r["estimator"] for r in j["comparison"]} == {"sample", "ewma", "lw_constant_corr",
                                                         "lw_identity", "oas", "mp_denoise"}
    assert j["universe"]["observations"] > 2400
    assert any("Alpaca" in n for n in j["notes"])


@pytest.mark.parametrize("method", ["min_variance", "risk_parity", "hrp", "herc", "max_diversification",
                                    "min_cvar", "inverse_volatility", "equal_weight"])
def test_optimize_methods_without_rf(client, method):
    j = _ok(client.post("/api/portfolio/optimize", json={"tickers": U, "method": method}))
    w = j["result"]["weights"]
    assert abs(sum(w.values()) - 1) < 1e-6
    assert abs(sum(j["result"]["risk_contributions"].values()) - j["result"]["volatility"]) < 1e-9
    assert j["result"]["sharpe"] is None  # rf unavailable offline and not supplied
    assert any("Risk-free rate unavailable" in n for n in j["notes"])
    if method in ("hrp", "herc"):
        assert len(j["result"]["extra"]["dendrogram"]["order"]) == len(U)


def test_optimize_max_sharpe_needs_rf(client):
    r = client.post("/api/portfolio/optimize", json={"tickers": U, "method": "max_sharpe"})
    assert r.status_code == 503
    assert "risk_free_rate" in r.json()["detail"]


def test_optimize_max_sharpe_with_constraints(client):
    body = {"tickers": U, "method": "max_sharpe", "risk_free_rate": RF, "cov_method": "oas",
            "constraints": {"max_weight": 0.3, "bounds": {"GLD": [0.05, 0.2]}}}
    j = _ok(client.post("/api/portfolio/optimize", json=body))
    w = j["result"]["weights"]
    assert all(v <= 0.3 + 1e-6 for v in w.values())
    assert 0.05 - 1e-6 <= w["GLD"] <= 0.2 + 1e-6
    assert j["result"]["sharpe"] is not None
    assert j["risk_free"]["source"] == "user"
    assert len(j["in_sample_growth"]["values"]) == j["universe"]["observations"]
    assert any("look-ahead" in n for n in j["notes"])
    assert "cvar_alpha" not in j["method"]["params"]


def test_evaluate_given_weights_matches_optimize(client):
    """/evaluate on the optimizer's own weights reproduces its ex-ante diagnostics."""
    base = {"tickers": U, "risk_free_rate": RF, "cov_method": "oas"}
    opt = _ok(client.post("/api/portfolio/optimize", json={**base, "method": "min_variance"}))
    w = opt["result"]["weights"]
    j = _ok(client.post("/api/portfolio/evaluate", json={**base, "weights": w}))
    r, o = j["result"], opt["result"]
    for k in ("expected_return", "volatility", "sharpe", "diversification_ratio", "effective_bets"):
        assert r[k] == pytest.approx(o[k], rel=1e-8), k
    assert sum(r["risk_contributions"].values()) == pytest.approx(r["volatility"], rel=1e-10)
    assert sum(r["risk_contributions_pct"].values()) == pytest.approx(1.0, rel=1e-10)
    assert j["constraints_satisfied"] is True


def test_evaluate_equal_weight_closed_form_and_validation(client):
    body = {"tickers": ["SPY", "TLT"], "weights": {"SPY": 0.6, "tlt": 0.4}, "cov_method": "sample"}
    j = _ok(client.post("/api/portfolio/evaluate", json=body))
    cov = j["covariance"]
    assert cov["estimator"] == "sample"
    r = j["result"]
    assert r["weights"] == {"SPY": 0.6, "TLT": 0.4}
    assert r["sharpe"] is None and any("Sharpe ratio not reported" in n for n in j["notes"])
    assert 1.0 <= r["effective_bets"] <= 2.0 and r["diversification_ratio"] >= 1.0
    # missing tickers are held at 0 and said so
    j = _ok(client.post("/api/portfolio/evaluate", json={"tickers": ["SPY", "TLT", "GLD"],
                                                          "weights": {"SPY": 0.5, "TLT": 0.5}}))
    assert j["result"]["weights"]["GLD"] == 0 and any("held at 0" in n for n in j["notes"])
    bad = client.post("/api/portfolio/evaluate", json={**body, "weights": {"SPY": 0.6, "TLT": 0.3}})
    assert bad.status_code == 422
    bad = client.post("/api/portfolio/evaluate", json={**body, "weights": {"SPY": 0.6, "XLE": 0.4}})
    assert bad.status_code == 422


def test_optimize_black_litterman(client):
    body = {"tickers": U, "method": "max_sharpe", "risk_free_rate": RF, "returns_model": "black_litterman",
            "views": [{"long": "XLE", "short": "XLK", "value": 0.03, "confidence": 0.6},
                      {"long": "GLD", "value": 0.06, "confidence": 0.3}]}
    j = _ok(client.post("/api/portfolio/optimize", json=body))
    e = j["expected_returns"]
    assert e["model"] == "black_litterman" and len(e["views"]) == 2
    assert e["params"]["delta"] > 0
    assert "equal weights" in e["prior_source"]
    assert any("uninformative" in n for n in j["notes"])
    # with no views the posterior equals the prior
    j0 = _ok(client.post("/api/portfolio/optimize", json={**body, "views": []}))
    e0 = j0["expected_returns"]
    assert np.allclose([e0["posterior_excess"][t] for t in U], [e0["prior_excess"][t] for t in U])
    # caller-supplied prior weights are used when market caps are unavailable
    pw = {t: (i + 1) for i, t in enumerate(U)}
    j1 = _ok(client.post("/api/portfolio/optimize", json={**body, "prior_weights": pw}))
    assert "caller-supplied" in j1["expected_returns"]["prior_source"]
    bad = client.post("/api/portfolio/optimize",
                      json={**body, "views": [{"long": "AAPL", "value": 0.1, "confidence": 0.5}]})
    assert bad.status_code == 422


def test_optimize_mean_variance_and_validation(client):
    j = _ok(client.post("/api/portfolio/optimize", json={"tickers": U, "method": "mean_variance",
                                                         "target_vol": 0.08}))
    assert abs(j["result"]["volatility"] - 0.08) < 1e-4
    assert client.post("/api/portfolio/optimize",
                       json={"tickers": U, "method": "mean_variance"}).status_code == 422
    assert client.post("/api/portfolio/optimize",
                       json={"tickers": U, "method": "min_variance",
                             "constraints": {"max_weight": 0.05}}).status_code == 422
    assert client.post("/api/portfolio/optimize",
                       json={"tickers": ["SPY", "NOTATICKER"], "method": "hrp"}).status_code == 503
    assert client.post("/api/portfolio/optimize",
                       json={"tickers": U, "method": "min_variance",
                             "constraints": {"max_turnover": 0.1}}).status_code == 422


def test_frontier(client):
    j = _ok(client.post("/api/portfolio/frontier", json={"tickers": U, "risk_free_rate": RF, "points": 20}))
    pts = j["frontier"]
    assert len(pts) == 20
    assert all(b["ret"] > a["ret"] for a, b in zip(pts, pts[1:], strict=False))
    assert all(b["vol"] >= a["vol"] - 1e-7 for a, b in zip(pts, pts[1:], strict=False))
    t = j["tangency"]
    assert t["sharpe"] >= max(p["sharpe"] for p in pts) - 1e-7
    assert j["cml"][0]["ret"] == pytest.approx(RF)
    assert {o["method"] for o in j["overlay"]} >= {"hrp", "equal_weight"}
    for o in j["overlay"]:
        assert o.get("error") is None
        assert o["vol"] >= pts[0]["vol"] - 1e-7  # nothing beats the GMV on volatility
    assert len(j["assets"]) == len(U)


def test_compare(client):
    body = {"tickers": ["SPY", "TLT", "GLD", "XLE", "XLK"], "start": "2018-01-01",
            "methods": ["min_variance", "hrp", "risk_parity"], "window": 252, "rebalance": "Q",
            "cost_bps": 10, "risk_free_rate": RF}
    j = _ok(client.post("/api/portfolio/compare", json=body))
    names = {s["method"] for s in j["stats"]}
    assert names == {"min_variance", "hrp", "risk_parity", "equal_weight"}
    for s in j["stats"]:
        assert s["max_drawdown"] <= 0 and s["annual_vol"] > 0 and s["sharpe"] is not None
    assert set(j["tests"]) == {"min_variance", "hrp", "risk_parity"}
    eq = j["equity"]
    assert set(eq["columns"]) == names
    assert eq["index"][0] == j["rebalance_dates"][0]
    # weekly: at most one point per ISO week after the first, same frame shape for drawdown
    import pandas as pd

    idx = pd.DatetimeIndex(eq["index"])
    assert not idx[1:].to_period("W-FRI").duplicated().any()
    assert j["drawdown"]["index"] == eq["index"] and set(j["drawdown"]["columns"]) == names
    assert j["stats"][0]["observations"] > 3 * len(idx)
    assert any("downsampled to weekly" in n for n in j["notes"])
    assert "cvar_alpha" not in j["method"]
    j2 = _ok(client.post("/api/portfolio/compare", json={**body, "methods": ["min_cvar"], "cvar_alpha": 0.9}))
    assert j2["method"]["cvar_alpha"] == 0.9
    assert client.post("/api/portfolio/compare",
                       json={**body, "risk_free_rate": None}).status_code == 503
    assert client.post("/api/portfolio/compare",
                       json={**body, "methods": ["mean_variance"]}).status_code == 422


@pytest.mark.live
def test_live_market_rf_and_market_cap_prior(client):
    """Online: rf from FRED DGS3MO and a Black-Litterman prior from SEC shares x price."""
    body = {"tickers": ["AAPL", "MSFT", "JPM", "XOM"], "method": "max_sharpe", "start": "2019-01-01",
            "returns_model": "black_litterman",
            "views": [{"long": "XOM", "short": "MSFT", "value": 0.02, "confidence": 0.5}]}
    j = _ok(client.post("/api/portfolio/optimize", json=body))
    assert j["risk_free"]["source"] == "market"
    assert 0.0 <= j["risk_free"]["annual"] < 0.2
    assert "market capitalisation" in j["expected_returns"]["prior_source"]
    mw = j["expected_returns"]["market_weights"]
    assert abs(sum(mw.values()) - 1) < 1e-9 and mw["MSFT"] > mw["XOM"] * 0.5


def _post_raw(client, text):
    return client.post("/api/portfolio/optimize", content=text, headers={"content-type": "application/json"})


def test_non_finite_and_unknown_inputs_rejected(client):
    """JSON parsers accept NaN: a NaN risk budget used to return 200 with null weights,
    and budgets for tickers outside the universe were silently ignored."""
    u = '"tickers": ["SPY", "TLT", "GLD", "XLE"]'
    r = _post_raw(client, "{" + u + ', "method": "risk_parity", '
                  '"risk_budgets": {"SPY": NaN, "TLT": 1, "GLD": 1, "XLE": 1}}')
    assert r.status_code == 422
    r = _post_raw(client, "{" + u + ', "method": "min_variance", "constraints": {"bounds": {"SPY": [NaN, 0.5]}}}')
    assert r.status_code == 422
    r = _post_raw(client, "{" + u + ', "method": "min_variance", "returns_model": "black_litterman", '
                  '"risk_free_rate": 0.02, "prior_weights": {"SPY": NaN, "TLT": 1, "GLD": 1, "XLE": 1}}')
    assert r.status_code == 422
    r = client.post("/api/portfolio/optimize", json={
        "tickers": ["SPY", "TLT", "GLD", "XLE"], "method": "risk_parity",
        "risk_budgets": {"SPY": 1, "TLT": 1, "GLD": 1, "XLE": 1, "QQQ": 5}})
    assert r.status_code in (400, 422) and "QQQ" in r.text


def test_user_risk_free_rate_is_one_rate(client):
    """With a caller-supplied rf the CAPM excess returns (daily rf series) and the Sharpe
    ratios (scalar rf) must use the same annual rate."""
    j = _ok(client.post("/api/portfolio/optimize", json={
        "tickers": ["SPY", "TLT", "GLD", "XLE"], "method": "min_variance", "returns_model": "capm",
        "benchmark": "SPY", "risk_free_rate": 0.04}))
    rf_capm = j["expected_returns"]["params"]["risk_free"]
    spy = next(a for a in j["assets"] if a["ticker"] == "SPY")
    rf_sharpe = spy["expected_return"] - spy["sharpe"] * spy["volatility"]
    assert rf_sharpe == pytest.approx(rf_capm, abs=1e-12)
    assert j["risk_free"]["annual"] == 0.04
    assert j["risk_free"]["annual_arithmetic"] == pytest.approx(rf_capm, abs=1e-12)


def test_risk_free_not_extrapolated_past_last_print(etf_returns):
    """The FRED series stopped months before the window end: the router used to
    forward-fill the last print indefinitely (a made-up rate)."""
    import pandas as pd

    from ohcamel_quant.api.routers import portfolio as pr
    from ohcamel_quant.data.base import DataUnavailable

    idx = etf_returns.dropna().index[-300:]

    class _Stub:
        def __init__(self, end):
            self.end = end

        def risk_free_daily(self, start, end):
            days = pd.bdate_range(pd.Timestamp(start), self.end)
            return type("DS", (), {"data": pd.Series(1e-4, index=days),
                                   "provenance_dicts": lambda self: []})()

    # a short publication lag is bridged
    rf, ann, *_ = pr._risk_free(_Stub(idx[-3]), idx, None, True)
    assert rf is not None and ann == pytest.approx(1e-4 * 252)
    # a stale series is missing data
    with pytest.raises(DataUnavailable, match="does not cover"):
        pr._risk_free(_Stub(idx[-60]), idx, None, True)
    rf, ann, *_ = pr._risk_free(_Stub(idx[-60]), idx, None, False)
    assert rf is None and ann is None
