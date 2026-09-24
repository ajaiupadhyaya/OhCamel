"""HTTP retry/throttle behaviour (respx mocks) and the file store (tmp_path)."""

from __future__ import annotations

import json
import time

import httpx
import pandas as pd
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import http
from ohcamel_quant.data.base import DataUnavailable, Provenance
from ohcamel_quant.data.store import Store, safe_key


def online(tmp_path, **kw) -> Settings:
    base = dict(offline=False, data_dir=tmp_path, http_retries=3, APCA_API_KEY_ID=None,
                APCA_API_SECRET_KEY=None, FRED_API_KEY=None)
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(http, "_sleep", lambda s: slept.append(s))
    http.reset_rate_limits()
    return slept


# ------------------------------------------------------------------ http
@respx.mock
def test_retries_on_5xx_then_succeeds(tmp_path, no_sleep):
    route = respx.get("https://example.test/x").mock(side_effect=[
        httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": True})])
    assert http.get_json("https://example.test/x", online(tmp_path)) == {"ok": True}
    assert route.call_count == 3
    assert len([s for s in no_sleep if s > 0]) >= 2  # two backoffs


@respx.mock
def test_retry_after_header_is_honoured(tmp_path, no_sleep):
    respx.get("https://example.test/r").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, text="fine")])
    assert http.get_text("https://example.test/r", online(tmp_path)) == "fine"
    assert 7.0 in no_sleep


@respx.mock
def test_404_is_not_retried_and_becomes_data_unavailable(tmp_path):
    route = respx.get("https://example.test/missing").mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(DataUnavailable) as ei:
        http.get("https://example.test/missing", online(tmp_path), source="vendor")
    assert route.call_count == 1
    assert "vendor: HTTP 404" in str(ei.value) and ei.value.status == 404


@respx.mock
def test_connect_errors_exhaust_retries(tmp_path):
    route = respx.get("https://example.test/down").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(http.FetchError, match="after 3 attempts"):
        http.get("https://example.test/down", online(tmp_path, http_retries=2))
    assert route.call_count == 3


def test_offline_refuses_network(tmp_path):
    with pytest.raises(DataUnavailable, match="offline"):
        http.get("https://example.test/", online(tmp_path, offline=True))


def test_backoff_is_bounded_full_jitter():
    for attempt in range(12):
        d = http.backoff_delay(attempt)
        assert 0.0 <= d <= min(http.BACKOFF_CAP_S, http.BACKOFF_BASE_S * 2**attempt)
    assert http.backoff_delay(0, "3") == 3.0
    assert http.backoff_delay(0, "999") == http.RETRY_AFTER_CAP_S


def test_per_host_throttle(monkeypatch):
    clock = [100.0]
    waits: list[float] = []
    monkeypatch.setattr(http, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(http, "_sleep", lambda s: waits.append(s))
    http.reset_rate_limits()
    for _ in range(3):
        http._throttle("data.sec.gov", 0.12)
    assert waits == pytest.approx([0.12, 0.24])  # reservations queue up
    http._throttle("other.host", 0.05)
    assert len(waits) == 2  # independent hosts do not wait on each other
    assert http.HOST_MIN_INTERVAL["www.sec.gov"] <= 0.1 + 0.02  # <= 10 req/s policy


# ----------------------------------------------------------------- store
def _frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03"]), name="date")
    return pd.DataFrame({"close": [1.0, 2.0]}, index=idx)


def test_safe_key_is_collision_free():
    assert safe_key("SPY") == "SPY"
    assert safe_key("^GSPC") != safe_key("_GSPC")
    assert "/" not in safe_key("a/b") and safe_key("a/b") != safe_key("a_b")


def test_store_roundtrip_ttl_and_types(tmp_path):
    st = Store(tmp_path)
    prov = Provenance.now("unit", url="u")
    st.put("prices", "^GSPC", _frame(), prov)
    obj, p = st.get("prices", "^GSPC", ttl=60)
    pd.testing.assert_frame_equal(obj, _frame())
    assert p.source == "unit" and p.detail["url"] == "u"
    s = pd.Series([1.5, None], index=_frame().index, name="DGS10")
    st.put("fred", "DGS10", s, prov)
    got, _ = st.get("fred", "DGS10", None)
    assert isinstance(got, pd.Series) and got.name == "DGS10" and got.isna().iloc[1]
    st.put("sec", "k", {"a": [1, 2]}, prov)
    assert st.get("sec", "k", None)[0] == {"a": [1, 2]}
    # expired
    st.put("prices", "old", _frame(), prov, fetched_at=time.time() - 3600)
    assert st.get("prices", "old", ttl=60) is None
    assert st.get("prices", "old", ttl=None) is not None
    # no temp files left behind
    assert not list(tmp_path.rglob("*.tmp"))
    meta = json.loads((tmp_path / "prices" / f"{safe_key('^GSPC')}.meta.json").read_text())
    assert meta["provenance"]["synthetic"] is False and "fetched_at" in meta


def test_fetch_or_stale_serves_last_good_copy_labelled(tmp_path):
    st = Store(tmp_path)
    st.put("x", "k", {"v": 1}, Provenance.now("vendor"), fetched_at=time.time() - 10_000)

    def boom():
        raise DataUnavailable("vendor: HTTP 503")

    obj, prov = st.fetch_or_stale("x", "k", 60, boom)
    assert obj == {"v": 1}
    assert prov.detail["note"] == "stale: refresh failed: vendor: HTTP 503"
    with pytest.raises(DataUnavailable, match="503"):
        st.fetch_or_stale("x", "missing", 60, boom)


def test_fetch_or_stale_fetches_and_stores(tmp_path):
    st = Store(tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        return {"v": 2}, Provenance.now("vendor")

    assert st.fetch_or_stale("x", "k", 60, fetch)[0] == {"v": 2}
    assert st.fetch_or_stale("x", "k", 60, fetch)[0] == {"v": 2}
    assert len(calls) == 1  # second call was a fresh cache hit
    with pytest.raises(DataUnavailable, match="offline"):
        st.fetch_or_stale("x", "other", 60, fetch, offline=True)


def test_corrupt_entry_is_a_miss(tmp_path):
    st = Store(tmp_path)
    st.put("x", "k", _frame(), Provenance.now("v"))
    (tmp_path / "x" / "k.parquet").write_bytes(b"garbage")
    assert st.read("x", "k") is None
