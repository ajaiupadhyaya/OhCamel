def test_fixture_returns_are_real_and_aligned(etf_returns):
    assert etf_returns.shape[1] == 9
    assert len(etf_returns) > 2400
    assert etf_returns.index.is_monotonic_increasing
    assert not etf_returns.isna().any().any()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["offline"] is True


def test_macro_offline(market):
    df = market.fred(["DGS10", "DGS2"]).data
    assert {"DGS10", "DGS2"} <= set(df.columns)
