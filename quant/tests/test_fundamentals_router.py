"""/api/fundamentals router.

Offline, SEC filings are unavailable, so every endpoint must answer 503 with
a clear reason. The end-to-end pipeline is then exercised with a MarketData
whose ``company_facts`` returns the documented contract built from the
hand-computed statements fixture while prices (QQQ, SPY) come from the real
committed fixtures. Live tests hit SEC/FRED/French/Yahoo for AAPL and MSFT.
"""

from __future__ import annotations

import os
import warnings

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from test_fundamentals_analytics import make_annual, make_quarterly

from ohcamel_quant.api.routers import fundamentals as fr
from ohcamel_quant.data.base import DataUnavailable, Provenance
from ohcamel_quant.data.market import Dataset, MarketData, get_market
from ohcamel_quant.fundamentals import dcf as dcfm


def _minimal_app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(DataUnavailable)
    async def _u(_, exc):  # noqa: ANN001
        return JSONResponse(status_code=503, content={"error": "data_unavailable", "detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _v(_, exc):  # noqa: ANN001
        return JSONResponse(status_code=422, content={"error": "invalid_input", "detail": str(exc)})

    app.include_router(fr.router, prefix="/api")
    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """The full app when it assembles; otherwise this router on a minimal app
    with the same exception mapping (keeps these tests independent of other domains)."""
    try:
        from ohcamel_quant.api.app import create_app

        app = create_app()
    except Exception:  # noqa: BLE001 - app assembly issue outside this domain
        warnings.warn("create_app() failed; testing the fundamentals router on a minimal app", stacklevel=1)
        app = _minimal_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_cache():
    fr._CACHE.clear()
    yield
    fr._CACHE.clear()


@pytest.mark.parametrize("path", ["profile", "statements", "ratios", "scores"])
def test_offline_get_is_503(client, path):
    r = client.get(f"/api/fundamentals/AAPL/{path}")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "data_unavailable" and "SEC" in body["detail"]


def test_offline_dcf_is_503(client):
    r = client.post("/api/fundamentals/AAPL/dcf", json={"years": 7})
    assert r.status_code == 503 and "offline" in r.json()["detail"]


def test_bad_inputs_422(client):
    assert client.get("/api/fundamentals/AAPL/statements?period=weekly").status_code == 422
    assert client.post("/api/fundamentals/AAPL/dcf", json={"years": 40}).status_code == 422


class ContractMarket(MarketData):
    """Offline MarketData + a company_facts payload in the documented contract."""

    def company_facts(self, ticker: str) -> Dataset:
        if ticker.upper() != "QQQ":
            raise DataUnavailable(f"sec: {ticker} unknown")
        return Dataset({"cik": "0000000000", "name": "Contract Test Co", "ticker": "QQQ",
                        "annual": make_annual(), "quarterly": make_quarterly(8),
                        "shares_outstanding": 100.0, "shares_outstanding_as_of": "2025-01-20",
                        "notes": ["test contract"]},
                       [Provenance.now("test-contract")])


@pytest.fixture
def contract_client(client):
    client.app.dependency_overrides[get_market] = lambda: ContractMarket()
    yield client
    client.app.dependency_overrides.pop(get_market, None)


def test_pipeline_profile(contract_client):
    r = contract_client.get("/api/fundamentals/qqq/profile")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == "Contract Test Co" and p["provenance"]
    assert p["market_cap"] == pytest.approx(p["price"] * 100)
    assert p["multiples_basis"] == "ttm"
    assert p["beta"]["beta"] > 1.0 and p["beta"]["n_months"] == 60
    assert p["range_52w"]["low"] <= p["price"] <= p["range_52w"]["high"] * 1.0001
    assert any("raw rather than excess" in n for n in p["notes"])


def test_pipeline_statements_and_ratios(contract_client):
    s = contract_client.get("/api/fundamentals/QQQ/statements", params={"period": "quarterly", "limit": 4}).json()
    assert len(s["income_statement"]["periods"]) == 4
    t = contract_client.get("/api/fundamentals/QQQ/statements", params={"period": "ttm"}).json()
    assert t["period"] == "ttm" and len(t["cash_flow"]["periods"]) == 5
    r = contract_client.get("/api/fundamentals/QQQ/ratios").json()
    assert r["annual"]["data"]["gross_margin"][-1] == pytest.approx(0.45)
    assert r["growth_cagr"]["revenue"]["1y"] == pytest.approx(0.2)
    assert r["latest"]["basis"] in ("ttm", "fy") and "margins" in r["groups"]


def test_pipeline_scores(contract_client):
    s = contract_client.get("/api/fundamentals/QQQ/scores").json()
    assert s["piotroski"]["score"] == 8
    assert s["altman_z"]["z"] is not None and s["altman_z2"]["zone"] == "safe"
    assert s["beneish"]["m"] is not None
    assert s["ohlson"]["o"] is None      # GDP deflator is online-only
    assert len(s["history"]) == 2


def test_pipeline_dcf(contract_client):
    body = {"terminal_growth": 0.02, "risk_free": 0.04, "erp": 0.05, "years": 5}
    r = contract_client.post("/api/fundamentals/QQQ/dcf", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    inp = out["inputs"]
    assert inp["terminal_growth"]["source"] == "override"
    assert inp["beta"]["source"] == "derived" and inp["beta"]["value"] > 1
    assert inp["cost_of_equity"]["value"] == pytest.approx(0.04 + inp["beta"]["value"] * 0.05)
    assert inp["base_fcff"]["basis"] == "ttm"
    assert inp["initial_growth"]["value"] == pytest.approx(min(max(1.5 ** 0.5 - 1, 0.02), 0.25))
    v = out["valuation"]
    assert v["enterprise_value"] == pytest.approx(v["pv_explicit"] + v["pv_terminal"])
    assert len(v["table"]) == 5
    assert len(out["sensitivity"]["value_per_share"]) == 5
    # reverse DCF: the implied constant growth reprices the firm at its market cap
    rev = out["reverse_dcf"]
    implied = dcfm.dcf_value(inp["base_fcff"]["value"], [rev["implied_growth"]] * 5, inp["wacc"]["value"],
                             0.02, v["net_debt"])
    assert implied["equity_value"] == pytest.approx(out["market_cap"], rel=1e-8)
    # refusing g >= WACC is a clean 422
    bad = contract_client.post("/api/fundamentals/QQQ/dcf", json={**body, "wacc": 0.02, "terminal_growth": 0.03})
    assert bad.status_code == 422 and "WACC" in bad.json()["detail"]


class StaleTTMMarket(ContractMarket):
    """Quarterly data with a gap: the last complete TTM window (2024-06-30)
    ends before the latest fiscal year (2024-12-31)."""

    def company_facts(self, ticker: str) -> Dataset:
        ds = super().company_facts(ticker)
        d = dict(ds.data)
        d["quarterly"] = d["quarterly"].drop(pd.Timestamp("2024-09-30"))
        return Dataset(d, ds.provenance)


class ShortHistoryMarket(ContractMarket):
    """Only ~10 months of prices for the company: too few for a 24-month beta."""

    def ohlcv(self, ticker, start=None, end=None):  # noqa: ANN001, ANN201
        ds = super().ohlcv(ticker, start, end)
        if ticker.upper() == "QQQ" and start is not None:
            return Dataset(ds.data.iloc[-210:], ds.provenance)
        return ds


def _use(client, market_cls):  # noqa: ANN001, ANN202
    client.app.dependency_overrides[get_market] = lambda: market_cls()


def test_stale_ttm_does_not_beat_newer_fiscal_year(client):
    _use(client, StaleTTMMarket)
    try:
        p = client.get("/api/fundamentals/QQQ/profile").json()
        assert p["multiples_basis"] == "fy" and p["multiples_period_end"] == "2024-12-31"
        r = client.get("/api/fundamentals/QQQ/ratios").json()
        assert r["latest"]["basis"] == "fy"
        body = {"terminal_growth": 0.02, "risk_free": 0.04, "erp": 0.05, "beta": 1.0}
        d = client.post("/api/fundamentals/QQQ/dcf", json=body).json()
        assert d["inputs"]["base_fcff"]["basis"] == "fy"
    finally:
        client.app.dependency_overrides.pop(get_market, None)


def test_dcf_short_beta_history_is_503_and_wacc_override_needs_no_beta(client):
    _use(client, ShortHistoryMarket)
    try:
        body = {"terminal_growth": 0.02, "risk_free": 0.04, "erp": 0.05}
        r = client.post("/api/fundamentals/QQQ/dcf", json=body)
        assert r.status_code == 503 and "pass beta" in r.json()["detail"]
        fr._CACHE.clear()
        ok = client.post("/api/fundamentals/QQQ/dcf", json={"terminal_growth": 0.02, "wacc": 0.09})
        assert ok.status_code == 200, ok.text
        out = ok.json()
        assert out["inputs"]["wacc"]["value"] == 0.09 and out["inputs"]["beta"]["value"] is None
        assert out["valuation"]["value_per_share"] > 0
    finally:
        client.app.dependency_overrides.pop(get_market, None)


# ---------------------------------------------------------------- live
live = pytest.mark.skipif(not os.environ.get("OHCAMEL_QUANT_LIVE_TESTS"), reason="live data tests disabled")


@pytest.mark.live
@live
@pytest.mark.parametrize("ticker", ["AAPL", "MSFT"])
def test_live_fundamentals(client, ticker):
    p = client.get(f"/api/fundamentals/{ticker}/profile")
    assert p.status_code == 200, p.text
    pj = p.json()
    assert pj["market_cap"] > 1e11 and pj["multiples"]["pe"] > 0
    s = client.get(f"/api/fundamentals/{ticker}/statements", params={"period": "annual"})
    assert s.status_code == 200
    rev = next(r for r in s.json()["income_statement"]["rows"] if r["item"] == "revenue")
    assert rev["values"][-1] > 1e10
    sc = client.get(f"/api/fundamentals/{ticker}/scores")
    assert sc.status_code == 200
    assert sc.json()["altman_z"]["z"] is not None
    d = client.post(f"/api/fundamentals/{ticker}/dcf", json={})
    assert d.status_code == 200, d.text
    dj = d.json()
    assert dj["valuation"]["value_per_share"] > 0
    assert 0.0 < dj["inputs"]["wacc"]["value"] < 0.2


def test_stale_price_note_counts_sessions():
    # Mon 2026-09-14 -> Thu 2026-09-24: 8 weekday sessions
    assert fr._stale_price_notes("2026-09-14", today=pd.Timestamp("2026-09-24").date())
    assert fr._stale_price_notes(pd.Timestamp("2026-09-18 20:00", tz="UTC"),
                                 today=pd.Timestamp("2026-09-24").date()) == []
    assert fr._stale_price_notes(None) == []


def test_dcf_falls_back_to_last_dgs10_with_dated_note(client, monkeypatch):
    import datetime as dt

    last = MarketData().fred(["DGS10"]).data["DGS10"].dropna()
    fake_today = (last.index[-1] + pd.Timedelta(days=90)).date()

    class _D(dt.date):
        @classmethod
        def today(cls):  # noqa: ANN206
            return fake_today

    monkeypatch.setattr(fr, "date", _D)
    # the offline DGS10 fixture has no print in the 45 days before the fake 'today'
    _use(client, ContractMarket)
    try:
        body = {"terminal_growth": 0.02, "erp": 0.05, "beta": 1.0}
        r = client.post("/api/fundamentals/QQQ/dcf", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        rf = out["inputs"]["risk_free"]
        assert rf["value"] == pytest.approx(float(last.iloc[-1]) / 100)
        assert rf["as_of"] == last.index[-1].date().isoformat()
        assert any("last available observation" in n and rf["as_of"] in n for n in out["notes"])
        # the committed price fixture is months older than the fake 'today'
        assert any("sessions old" in n for n in out["notes"])
    finally:
        client.app.dependency_overrides.pop(get_market, None)
