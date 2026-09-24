"""/api/macro endpoints.

Offline, only FRED DGS10/DGS2/VIXCLS and the ETF bars exist, so /regimes,
/series, /bond and a partial /dashboard are served from real fixtures; the
curve, recession and Taylor endpoints must answer 503 with a clear reason.
The curve endpoints' code paths are additionally exercised through a
dependency override whose curve is derived from the REAL DGS2/DGS10 fixture
(other tenors linearly inter/extrapolated -- a test-only format sample).
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from ohcamel_quant.api.routers import macro as macro_router
from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.data.market import Dataset, MarketData, get_market


def _fallback_app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(DataUnavailable)
    async def _u(_, exc):  # noqa: ANN001
        return JSONResponse(status_code=503, content={"error": "data_unavailable", "detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _v(_, exc):  # noqa: ANN001
        return JSONResponse(status_code=422, content={"error": "invalid_input", "detail": str(exc)})

    app.include_router(macro_router.router, prefix="/api")
    return app


@pytest.fixture(scope="module")
def app() -> FastAPI:
    try:
        from ohcamel_quant.api.app import create_app

        return create_app()
    except Exception:  # noqa: BLE001 - app assembly issue outside this domain
        warnings.warn("create_app() failed; testing the macro router on a minimal app", stacklevel=1)
        return _fallback_app()


@pytest.fixture(scope="module")
def mclient(app):
    macro_router.clear_cache()
    return TestClient(app)


def _ok(r):
    assert r.status_code == 200, r.text
    j = r.json()
    assert "provenance" in j and "notes" in j and "method" in j
    assert all(not p["synthetic"] for p in j["provenance"])
    return j


def test_regimes_offline(mclient):
    j = _ok(mclient.get("/api/macro/regimes", params={"ticker": "SPY", "k": 2, "freq": "W"}))
    assert j["k"] == 2 and len(j["regimes"]) == 2
    assert j["regimes"][1]["vol_ann"] > j["regimes"][0]["vol_ann"]
    sm = j["smoothed"]
    march = [v for d, v in zip(sm["index"], sm["data"]["regime_1"], strict=True) if d.startswith("2020-03")]
    assert march and min(march) > 0.9
    P = np.array(j["model"]["transition"])
    assert np.allclose(P.sum(axis=1), 1)
    names = {c["name"] for c in j["risk_panel"]["components"]}
    assert {"vix", "curve_slope", "trend"} <= names
    assert any("BAMLH0A0HYM2" in n for n in j["notes"])
    assert mclient.get("/api/macro/regimes", params={"k": 5}).status_code == 422
    assert mclient.get("/api/macro/regimes", params={"ticker": "NOPE"}).status_code == 503


def test_series_offline(mclient):
    j = _ok(mclient.get("/api/macro/series", params={"ids": "DGS10,DGS2", "transform": "level,diff",
                                                      "start": "2024-01-01"}))
    assert j["transforms"] == {"DGS10": "level", "DGS2": "diff"}
    assert j["data"]["index"][0] >= "2024-01-01"
    assert set(j["summary"]) == {"DGS10", "DGS2"}
    yoy = _ok(mclient.get("/api/macro/series", params={"ids": "VIXCLS", "transform": "yoy_pct",
                                                        "start": "2020-01-01"}))
    assert yoy["data"]["index"][0] >= "2020-01-01" and yoy["data"]["data"]["VIXCLS"][0] is not None
    assert mclient.get("/api/macro/series", params={"ids": "DGS10", "transform": "nope"}).status_code == 422
    assert mclient.get("/api/macro/series", params={"ids": "DGS10,DGS2", "transform": "level,diff,diff"}
                       ).status_code == 422
    r = mclient.get("/api/macro/series", params={"ids": "UNRATE"})
    assert r.status_code == 503 and "UNRATE" in r.json()["detail"]


def test_dashboard_partial_offline(mclient):
    j = _ok(mclient.get("/api/macro/dashboard"))
    rows = {r["id"]: r for r in j["series"]}
    for sid in ("DGS10", "DGS2", "VIXCLS"):
        assert rows[sid].get("error") is None
        assert set(rows[sid]["change"]) == {"1M", "3M", "1Y"}
        assert 0 <= rows[sid]["percentile_10y"] <= 1
        assert rows[sid]["sparkline"]["values"]
    assert "error" in rows["CPIAUCSL"] and "CPIAUCSL" in j["missing"]
    assert any(n.startswith("unavailable:") for n in j["notes"])


@pytest.mark.parametrize("path", ["/api/macro/curve", "/api/macro/curve/history", "/api/macro/recession",
                                  "/api/macro/taylor"])
def test_online_only_endpoints_503(mclient, path):
    r = mclient.get(path)
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "data_unavailable" and "offline" in body["detail"]


def test_bond_offline(mclient):
    body = {"coupon": 4.25, "maturity": "2036-05-15", "settlement": "2026-08-17", "price": 98.5}
    j = _ok(mclient.post("/api/macro/bond", json=body))
    assert j["clean_price"] == 98.5 and j["accrued"] > 0
    assert j["dirty_price"] == pytest.approx(98.5 + j["accrued"])
    assert 4.0 < j["ytm_pct"] < 5.0
    assert j["risk"]["dv01_notional"] == pytest.approx(j["risk"]["dv01"] * 10_000)
    assert "curve" not in j and any("curve-based analytics unavailable" in n for n in j["notes"])
    assert len(j["cashflows"]) == 20
    j2 = _ok(mclient.post("/api/macro/bond", json={**body, "price": None, "yield_pct": j["ytm_pct"]}))
    assert j2["clean_price"] == pytest.approx(98.5, abs=1e-9)
    assert mclient.post("/api/macro/bond", json={"coupon": 4, "years": 5, "maturity": "2030-01-01",
                                                 "price": 99}).status_code == 422
    assert mclient.post("/api/macro/bond", json={"coupon": 4, "years": 5}).status_code == 503


class _CurveStub(MarketData):
    """Offline MarketData whose Treasury curve is built from the real DGS2/DGS10 fixture."""

    def treasury_curve(self, start=None, end=None):  # noqa: ANN001
        ds = self.fred(["DGS2", "DGS10"], start, end)
        d2, d10 = ds.data["DGS2"], ds.data["DGS10"]
        cols = {}
        for t in (0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0):
            cols[t] = d2 + (d10 - d2) * (t - 2.0) / 8.0
        df = pd.DataFrame(cols).dropna(how="all")
        return Dataset(df, ds.provenance)


@pytest.fixture()
def stub_client(app):
    app.dependency_overrides[get_market] = lambda: _CurveStub()
    macro_router.clear_cache()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_market, None)
        macro_router.clear_cache()


def test_curve_endpoint_code_path(stub_client):
    j = _ok(stub_client.get("/api/macro/curve", params={"date": "2024-06-14", "compare": "1M,1Y,2020-03-16"}))
    assert j["date"] == "2024-06-14"
    assert j["fits"]["nelson_siegel"]["rmse_bp"] >= 0 and "tau2" in j["fits"]["svensson"]["params"]
    assert [c["label"] for c in j["compare"]] == ["1M", "1Y", "2020-03-16"]
    assert j["compare"][0]["date"] <= "2024-05-14"
    assert set(j["curve"]) == {"t", "discount", "zero", "inst_forward", "forward_1y"}
    b = _ok(stub_client.post("/api/macro/bond", json={"coupon": 4.0, "years": 7, "settlement": "2026-06-02"}))
    assert b["bond"]["price_source"] == "curve"
    assert b["curve"]["z_spread_bp"] == pytest.approx(0.0, abs=1e-6)
    assert b["curve"]["krd_sum"] == pytest.approx(b["curve"]["effective_duration"], rel=1e-6)


def test_curve_history_code_path(stub_client):
    j = _ok(stub_client.get("/api/macro/curve/history", params={"years": 5, "pca_years": 5}))
    assert len(j["pca"]["explained_variance"]) == 3
    assert j["pca"]["explained_variance"][0] > 0.5
    assert set(j["spreads"]["columns"]) >= {"2s10s", "2s5s10s"}
    assert len(j["heatmap"]["dates"]) >= 55


class _MacroStub(MarketData):
    """Constructed monthly/daily inputs (test-only) to exercise the recession
    and Taylor endpoints' assembly logic offline; the econometrics themselves
    are tested in test_macro_recession / test_macro_dashboard."""

    def fred(self, series_ids, start=None, end=None):  # noqa: ANN001
        rng = np.random.default_rng(3)
        m = pd.date_range("1960-01-01", "2026-05-01", freq="MS")
        d = pd.bdate_range("1982-01-04", "2026-06-01")
        w = pd.date_range("1971-01-08", "2026-05-29", freq="W-FRI")
        q = pd.date_range("1960-01-01", "2026-01-01", freq="QS")
        spread_m = pd.Series(1.5 + np.sin(np.arange(len(m)) / 20.0), index=m)
        rec = (spread_m.shift(12) < 0.7).astype(float).fillna(0.0)
        out = {
            "USREC": rec, "GS10": spread_m + 4.0, "TB3MS": pd.Series(4.0, index=m),
            "T10Y3M": spread_m.reindex(d, method="ffill") + rng.normal(0, 0.05, len(d)),
            "NFCI": pd.Series(rng.normal(0, 0.5, len(w)), index=w),
            "UNRATE": 5.0 + rec.rolling(6, min_periods=1).sum() * 0.2,
            "PCEPILFE": pd.Series(100 * 1.025 ** (np.arange(len(m)) / 12), index=m),
            "GDPC1": pd.Series(1e4 * 1.005 ** np.arange(len(q)), index=q),
            "GDPPOT": pd.Series(1e4 * 1.005 ** np.arange(len(q)) * 1.01, index=q),
            "DFF": pd.Series(3.0, index=d),
        }
        ids = [s.upper() for s in series_ids]
        missing = [s for s in ids if s not in out]
        if missing:
            raise DataUnavailable(f"stub: {missing}")
        df = pd.concat([out[s].rename(s) for s in ids], axis=1, sort=True)
        df.index.name = "date"
        return Dataset(df, [])


@pytest.fixture()
def macro_stub_client(app):
    app.dependency_overrides[get_market] = lambda: _MacroStub()
    macro_router.clear_cache()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_market, None)
        macro_router.clear_cache()


def test_recession_and_taylor_code_paths(macro_stub_client):
    j = macro_stub_client.get("/api/macro/recession", params={"h": 12}).json()
    assert set(j["models"]) == {"spread", "spread_nfci"}
    assert j["models"]["spread"]["params"]["spread"] < 0
    # T10Y3M ends 2026-06-01: June is a month-to-date average, target = June 2027
    assert j["current"]["origin"] == "2026-06-01" and j["current"]["target"] == "2027-06-01"
    assert j["probability"]["index"][-1] == "2027-06-01"
    assert any("month-to-date" in n for n in j["notes"])
    assert j["recessions"] and j["sahm"]["threshold"] == 0.5
    assert any("GS10 - TB3MS" in n for n in j["notes"])
    t = macro_stub_client.get("/api/macro/taylor", params={"r_star": 1.0, "pi_star": 2.0}).json()
    last = t["latest"]
    # 1 + 2.5 + 0.5*0.5 + 0.5*(-0.990..) with gap = 100(1/1.01 - 1)
    gap = 100 * (1 / 1.01 - 1)
    assert last["taylor_1993"] == pytest.approx(1 + 2.5 + 0.25 + 0.5 * gap, abs=1e-6)
    assert last["fed_funds"] == pytest.approx(3.0)
    assert t["params"]["r_star"] == 1.0


# ------------------------------------------------------------------ live
@pytest.mark.live
def test_live_dashboard(mclient):
    j = _ok(mclient.get("/api/macro/dashboard"))
    ok = [r for r in j["series"] if r.get("error") is None]
    assert len(ok) >= 20


@pytest.mark.live
def test_live_curve(mclient):
    j = _ok(mclient.get("/api/macro/curve", params={"compare": "1M,1Y"}))
    assert len(j["par"]["tenors"]) >= 8
    assert j["fits"]["svensson"]["rmse_bp"] < 15
    h = _ok(mclient.get("/api/macro/curve/history"))
    assert h["pca"]["explained_variance"][0] > 0.6


@pytest.mark.live
def test_live_recession_and_taylor(mclient):
    j = _ok(mclient.get("/api/macro/recession"))
    assert j["models"]["spread"]["params"]["spread"] < 0  # inversion raises recession odds
    assert 0 <= j["current"]["probability"] <= 1
    assert j["sahm"] is not None
    t = _ok(mclient.get("/api/macro/taylor"))
    assert "taylor_1993" in t["series"]["columns"]


def test_bond_key_tenors_validated(mclient):
    body = {"coupon": 4.0, "maturity": "2036-05-15", "settlement": "2026-08-17", "price": 99.0,
            "key_tenors": [0.0, 2.0, 10.0]}
    r = mclient.post("/api/macro/bond", json=body)
    assert r.status_code == 422 and "key_tenors" in r.text


def test_curve_fits_report_sign_restrictions(stub_client):
    j = _ok(stub_client.get("/api/macro/curve", params={"date": "2024-06-14", "compare": ""}))
    for name in ("nelson_siegel", "svensson"):
        f = j["fits"][name]
        assert isinstance(f["binding_constraints"], list)
        assert f["params"]["b0"] >= -1e-9 and f["params"]["b0"] + f["params"]["b1"] >= -1e-9
    assert any("b0 >= 0" in n for n in j["notes"])


@pytest.mark.parametrize("freq", ["W", "M"])
def test_regimes_price_on_probability_grid(mclient, freq):
    j = _ok(mclient.get("/api/macro/regimes", params={"ticker": "SPY", "k": 2, "freq": freq,
                                                      "search_reps": 0}))
    assert j["price"]["index"] == j["smoothed"]["index"] == j["returns"]["index"]
    px = np.array(j["price"]["values"], dtype=float)
    r = np.array(j["returns"]["values"], dtype=float)
    np.testing.assert_allclose(px[1:] / px[:-1] - 1, r[1:], rtol=1e-10)
    assert any("annualized per-period" in n for n in j["notes"])


def test_series_percentile_window_describes_coverage(mclient):
    j = _ok(mclient.get("/api/macro/series", params={"ids": "DGS10", "start": "2024-01-01"}))
    s = j["summary"]["DGS10"]
    pw = s["percentile_window"]
    assert "percentile_10y" in s and pw["start"] >= "2024-01-01"
    assert pw["complete"] is False and pw["years_requested"] == 10 and pw["years_covered"] < 3
    assert "shorter than the 10-year window" in pw["description"]


def test_dashboard_categories_renamed(mclient):
    j = _ok(mclient.get("/api/macro/dashboard"))
    assert "Growth" in j["categories"] and "Policy & rates" in j["categories"]
    assert "Activity" not in j["categories"] and "Rates" not in j["categories"]
