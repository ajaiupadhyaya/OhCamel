"""/api/backtest endpoints, offline against the committed real ETF fixtures."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

NINE = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]


@pytest.fixture(scope="module")
def bt_client():
    """The full app when it builds; otherwise (e.g. an unrelated SPA-route
    problem in app.py) a minimal app with the same /api mount and error
    handlers, so these tests still exercise the backtest router."""
    from fastapi.testclient import TestClient

    try:
        from ohcamel_quant.api.app import create_app

        return TestClient(create_app())
    except Exception:  # noqa: BLE001
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        from ohcamel_quant.api.routers.backtest import router
        from ohcamel_quant.data.base import DataUnavailable

        app = FastAPI()

        @app.exception_handler(DataUnavailable)
        async def _u(_, exc):  # noqa: ANN001
            return JSONResponse(status_code=503, content={"error": "data_unavailable", "detail": str(exc)})

        @app.exception_handler(ValueError)
        async def _v(_, exc):  # noqa: ANN001
            return JSONResponse(status_code=422, content={"error": "invalid_input", "detail": str(exc)})

        app.include_router(router, prefix="/api")
        return TestClient(app)


def test_catalog(bt_client):
    r = bt_client.get("/api/backtest/strategies")
    assert r.status_code == 200
    j = r.json()
    keys = {s["key"] for s in j["strategies"]}
    assert {"tsmom", "xsmom", "dual_momentum", "sma_trend", "vol_managed", "risk_parity", "low_beta",
            "st_reversal", "pairs_distance", "pairs_coint", "buy_and_hold", "static_mix"} <= keys
    assert "sectors" in {u["key"] for u in j["universes"]}
    assert j["caps"]["grid_combinations"] == 64 and j["caps"]["bootstrap_reps"] == 1000


def test_run_tsmom(bt_client):
    r = bt_client.post("/api/backtest/run", json={"strategy": "tsmom", "tickers": NINE, "bootstrap_reps": 200})
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j["equity"]["columns"]) == {"net", "gross", "benchmark"}
    assert j["equity"]["data"]["net"][0] == pytest.approx(1.0, abs=0.05)
    m = j["metrics"]
    assert m["net"]["cagr"] <= m["gross"]["cagr"]
    assert j["bootstrap_sharpe"]["ci_low"] < j["bootstrap_sharpe"]["ci_high"]
    assert 0 <= j["psr"]["psr_vs_0"] <= 1
    assert j["look_ahead_audit"]["passed"]
    assert j["weights_monthly"]["columns"] == NINE
    assert j["provenance"] and j["method"]["reference"].startswith("Moskowitz")
    assert any("Risk-free" in n for n in j["notes"])          # offline: no T-bill series
    assert any("dividends" in n for n in j["notes"])
    assert len(j["drawdowns"]) >= 1 and j["trades"]["executions"] > 50


def test_run_with_start_warmup_and_vol_target(bt_client):
    r = bt_client.post("/api/backtest/run", json={
        "strategy": "tsmom", "tickers": NINE, "start": "2019-01-01", "vol_target": 0.10,
        "bootstrap_reps": 100})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["window"]["live_start"] >= "2019-01-01"
    assert j["window"]["live_start"] < "2019-03-01"            # history before start filled the look-back
    assert j["vol_scale"] is not None


def test_run_pairs_and_mix(bt_client):
    r = bt_client.post("/api/backtest/run", json={"strategy": "pairs_distance", "tickers": ["XLK", "QQQ"],
                                                   "bootstrap_reps": 100})
    assert r.status_code == 200, r.text
    r = bt_client.post("/api/backtest/run", json={
        "strategy": "static_mix", "tickers": ["SPY", "IEF"], "params": {"weights": {"SPY": 0.6, "IEF": 0.4}},
        "bootstrap_reps": 100})
    assert r.status_code == 200, r.text
    w = r.json()["weights_monthly"]["data"]
    assert w["SPY"][-1] == pytest.approx(0.6, abs=0.05)


def test_run_errors(bt_client):
    assert bt_client.post("/api/backtest/run", json={"strategy": "nope", "tickers": NINE}).status_code == 422
    r = bt_client.post("/api/backtest/run", json={"strategy": "tsmom", "tickers": NINE, "params": {"lookback": 9999}})
    assert r.status_code == 422
    r = bt_client.post("/api/backtest/run", json={"strategy": "tsmom", "tickers": ["NOTATICKER"]})
    assert r.status_code == 503
    r = bt_client.post("/api/backtest/run", json={"strategy": "tsmom", "tickers": NINE, "execution_lag": 0})
    assert r.status_code == 422


def test_sweep(bt_client):
    body = {"strategy": "tsmom", "tickers": NINE, "grid": {"lookback": [126, 252, 378], "com": [30, 60]},
            "spa_reps": 200, "n_partitions": 8}
    r = bt_client.post("/api/backtest/sweep", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["heatmap"]["z"]) == 2 and len(j["heatmap"]["z"][0]) == 3
    assert 0 <= j["pbo"]["pbo"] <= 1
    assert j["deflated_sharpe"]["n_trials"] == 6
    assert 0 <= j["spa"]["pvalue_consistent"] <= 1
    assert j["caps"]["grid_combinations"] == 64
    too_big = {**body, "grid": {"lookback": list(range(21, 504, 5)), "com": [30, 60]}}
    assert bt_client.post("/api/backtest/sweep", json=too_big).status_code == 422


def test_walkforward(bt_client):
    r = bt_client.post("/api/backtest/walkforward", json={
        "strategy": "xsmom", "tickers": NINE, "grid": {"lookback": [126, 252], "top_k": [2, 3]},
        "is_days": 504, "oos_days": 126})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["folds"]) >= 5
    assert set(j["equity"]["columns"]) >= {"walk_forward", "benchmark"}
    assert j["oos_summary"]["observations"] > 500


def test_costs(bt_client):
    r = bt_client.post("/api/backtest/costs", json={"strategy": "st_reversal", "tickers": NINE,
                                                     "bps": [0, 2, 5, 10, 25]})
    assert r.status_code == 200, r.text
    j = r.json()
    sharpes = [row["sharpe"] for row in j["curve"]]
    assert sharpes == sorted(sharpes, reverse=True)
    assert j["annual_turnover"] > 10                        # weekly reversal trades a lot
    assert j["break_even_case"] in {"always_above", "always_below", "crosses"}
    assert (j["breakeven_bps_sharpe_zero"] is not None) == (j["break_even_case"] == "crosses")
    assert j["break_even_case_vs_benchmark"] in {"always_above", "always_below", "crosses", None}


# ---------------------------------------------------------- risk-free handling
def _yield_rf(market, start=None, end=None):
    """A real yield-derived daily cash return (FRED DGS2 fixture), shaped like
    ``MarketData.risk_free_daily`` output (offline has no DGS3MO)."""
    from ohcamel_quant.data.base import Provenance
    from ohcamel_quant.data.market import Dataset

    y = market.fred(["DGS2"]).data["DGS2"].dropna()
    rf = ((1.0 + y / 100.0) ** (1.0 / 252.0) - 1.0).rename("RF")
    if start is not None:
        rf = rf[rf.index >= pd.Timestamp(start)]
    if end is not None:
        rf = rf[rf.index <= pd.Timestamp(end)]
    return Dataset(rf, [Provenance.now("derived", series="DGS2", formula="(1 + y/100)**(1/252) - 1")])


def test_cash_accrual_uses_the_rate_known_at_the_previous_close(market):
    """A yield printed at the close of session s is not known during (s-1, s]:
    the cash return credited on s must come from the print of s - 1."""
    from ohcamel_quant.api.routers import backtest as B

    idx = market.prices(["SPY"]).data.index[300:600]
    ds = _yield_rf(market)
    rf, _, notes = B._risk_free(type("M", (), {"risk_free_daily": lambda self, a, b: _yield_rf(market, a, b)})(),
                                idx)
    known = ds.data.reindex(ds.data.index.union(idx)).ffill(limit=B.RF_FFILL_SESSIONS)
    np.testing.assert_allclose(rf.iloc[1:].to_numpy(), known.reindex(idx).iloc[:-1].to_numpy(), rtol=1e-14)
    assert rf.iloc[0] == pytest.approx(float(known.loc[known.index < idx[0]].dropna().iloc[-1]), rel=1e-14)
    assert any("previous session" in n for n in notes)


def test_sweep_pbo_ranks_on_excess_returns(bt_client, market, monkeypatch):
    """The sweep's 'best' and the DSR select on EXCESS Sharpe; CSCV must rank
    trials with the same criterion. Levering a portfolio leaves its excess
    Sharpe unchanged but raises its raw-return Sharpe, so on a leverage grid
    raw-return CSCV mechanically crowns the most levered trial."""
    from ohcamel_quant.api.routers import backtest as B
    from ohcamel_quant.backtest import validation as V

    monkeypatch.setattr(market, "risk_free_daily", lambda a=None, b=None: _yield_rf(market, a, b))
    body = {"strategy": "risk_parity", "tickers": NINE,
            "grid": {"target_vol": [0.02, 0.05, 0.10, 0.15, 0.20, 0.30]},
            "spa_reps": 100, "n_partitions": 8, "seed": 424242}
    r = bt_client.post("/api/backtest/sweep", json=body)
    assert r.status_code == 200, r.text
    p = B._prepare(B.SweepIn(**body), market)
    assert p.rf is not None
    sw = V.sweep(p.ctx, p.spec, p.params, body["grid"], p.config, p.rf)
    ex = sw.returns.sub(p.rf.reindex(sw.returns.index), axis=0)
    assert V.cscv_pbo(ex, 8)["pbo"] != V.cscv_pbo(sw.returns, 8)["pbo"]      # the criterion matters here
    assert r.json()["pbo"]["pbo"] == pytest.approx(V.cscv_pbo(ex, 8)["pbo"], abs=1e-12)


@pytest.mark.parametrize("params", [
    {"lookback": None}, {"lookback": "inf"}, {"vol_target": None}, {"vol_target": [1]}, {"long_only": "yes"},
])
def test_bad_param_values_are_422(bt_client, params):
    r = bt_client.post("/api/backtest/run", json={"strategy": "tsmom", "tickers": NINE, "params": params})
    assert r.status_code == 422, r.text


def test_bad_weights_param_is_422(bt_client):
    r = bt_client.post("/api/backtest/run", json={"strategy": "static_mix", "tickers": ["SPY", "IEF"],
                                                   "params": {"weights": [0.6, 0.4]}})
    assert r.status_code == 422, r.text
    r = bt_client.post("/api/backtest/run", json={"strategy": "static_mix", "tickers": ["SPY", "IEF"],
                                                   "params": {"weights": {"SPY": "x"}}})
    assert r.status_code == 422, r.text


def test_french_rf_detected_from_structured_provenance(market):
    """The router reads ``detail.rf_series`` (not note text): Ken French RF is the
    realized return of the day, so it is credited without the one-session lag."""
    from ohcamel_quant.api.routers import backtest as B
    from ohcamel_quant.data.base import Provenance
    from ohcamel_quant.data.market import Dataset

    idx = market.prices(["SPY"]).data.index[300:400]
    raw = _yield_rf(market).data
    fake = type("M", (), {"risk_free_daily": lambda self, a, b: Dataset(
        raw, [Provenance.now("derived", rf_series="FF_RF", note="some wording")])})()
    rf, _, notes = B._risk_free(fake, idx)
    np.testing.assert_allclose(rf.to_numpy(), raw.reindex(raw.index.union(idx)).ffill(
        limit=B.RF_FFILL_SESSIONS).reindex(idx).to_numpy())
    assert notes == []


def test_sweep_of_inert_parameters_reports_no_pbo(bt_client):
    """xsmom holding top_k >= n_assets is the same portfolio for every lookback/skip:
    identical trials tie at logit 0 in CSCV, which would read as PBO = 100%. The router
    must instead say that there was no selection to overfit."""
    body = {"strategy": "xsmom", "tickers": ["XLK", "XLF", "XLE"], "params": {"top_k": 3},
            "grid": {"lookback": [126, 252], "skip": [11, 21]}, "spa_reps": 100, "n_partitions": 8}
    r = bt_client.post("/api/backtest/sweep", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "pbo" not in j["pbo"] or j["pbo"]["pbo"] is None
    assert "identical returns" in j["pbo"]["error"]
    assert any("distinct return streams" in n for n in j["notes"])


def test_xsmom_holding_every_asset_says_it_is_equal_weight(bt_client):
    r = bt_client.post("/api/backtest/run", json={"strategy": "xsmom", "tickers": ["XLK", "XLF", "XLE"],
                                                  "params": {"top_k": 3}})
    assert r.status_code == 200, r.text
    assert any("equal-weight portfolio" in n for n in r.json()["notes"])
