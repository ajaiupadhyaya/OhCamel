def test_fixture_returns_are_real_and_aligned(etf_returns):
    assert etf_returns.shape[1] == 9
    assert len(etf_returns) > 2400
    assert etf_returns.index.is_monotonic_increasing
    assert not etf_returns.isna().any().any()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["offline"] is True


def test_head_health_is_not_stolen_by_the_shell(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("SHELL", encoding="utf-8")
    monkeypatch.setattr("ohcamel_quant.api.app.WEB_DIST", tmp_path)
    from fastapi.testclient import TestClient

    from ohcamel_quant.api.app import create_app

    c = TestClient(create_app())
    assert c.get("/api/health").status_code == 200
    assert c.head("/api/health").status_code == 200
    assert c.head("/api/no-such").status_code == 404
    assert c.head("/").status_code == 200


def test_macro_offline(market):
    df = market.fred(["DGS10", "DGS2"]).data
    assert {"DGS10", "DGS2"} <= set(df.columns)
