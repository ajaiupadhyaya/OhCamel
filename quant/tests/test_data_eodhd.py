"""The EODHD adapter (completion task 3.8, spec R5) against committed real responses.

The listed fixture (AAPL, across its 2020-08-31 4-for-1 split and the 2020-08-07 dividend) was recorded with
EODHD's public ``demo`` token by ``python -m ohcamel_quant.warehouse eodhd-slice``. The delisted fixture needs the
owner's paid key: until it is committed, the delisted test skips with a BLOCKED reason and the command to record it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import eodhd, http
from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.warehouse import adjust

DATA = Path(__file__).parent / "data"
LISTED = ("AAPL.US", "2020-07-31", "2020-09-10")


def _fixture(endpoint: str, code: str, start: str, end: str) -> tuple[object, dict]:
    path = DATA / f"eodhd_{endpoint}_{code}_{start}_{end}.json"
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    return json.loads(path.read_bytes()), meta


def _delisted_fixtures() -> list[Path]:
    out = []
    for meta in sorted(DATA.glob("eodhd_eod_*.json.meta.json")):
        if json.loads(meta.read_text()).get("delisted") is True:
            out.append(meta.with_name(meta.name.removesuffix(".meta.json")))
    return out


@pytest.mark.parametrize("endpoint", ["eod", "div", "splits"])
def test_listed_fixtures_are_real_and_carry_their_sidecars(endpoint):
    path = DATA / f"eodhd_{endpoint}_{LISTED[0]}_{LISTED[1]}_{LISTED[2]}.json"
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    assert meta["synthetic"] is False and meta["source"] == "eodhd" and meta["delisted"] is False
    assert meta["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()   # kept whole, unmodified
    assert meta["fetched_utc"].endswith("Z") and meta["url"].startswith("https://eodhd.com/api/")
    assert "api_token" not in json.dumps(meta) and "demo" not in meta["url"]   # no token, in any form


def test_the_adapter_parses_the_listed_response():
    eod, _ = _fixture("eod", *LISTED)
    splits = eodhd.parse_splits(_fixture("splits", *LISTED)[0])
    divs = eodhd.parse_dividends(_fixture("div", *LISTED)[0])
    served = eodhd.parse_eod(eod)
    raw = eodhd.raw_bars(served, splits)
    assert list(raw.columns) == ["open", "high", "low", "close", "volume"]
    assert raw.index[0] == pd.Timestamp("2020-07-31") and raw.index[-1] == pd.Timestamp("2020-09-10")
    # OHLC as traded: 499.30 the session before the split, 129.04 on its ex-date
    assert raw.loc["2020-08-28", "close"] == pytest.approx(499.23)
    assert raw.loc["2020-08-31", "close"] == pytest.approx(129.04)
    # EODHD's volume is split-adjusted (its documentation); raw is the shares that traded: / 4 before the split
    assert raw.loc["2020-08-20", "volume"] == pytest.approx(served.loc["2020-08-20", "volume"] / 4.0)
    assert raw.loc["2020-08-31", "volume"] == served.loc["2020-08-31", "volume"]
    s = splits.set_index("ex_date")["ratio"]
    assert s.loc[pd.Timestamp("2020-08-31")] == 4.0
    d = divs.set_index("ex_date")["cash"]
    assert d.loc[pd.Timestamp("2020-08-07")] == pytest.approx(0.82)   # as paid, not the split-adjusted 0.205
    acts = eodhd.as_actions(splits, divs)
    assert set(acts["kind"]) == {"split", "dividend"} and list(acts.columns) == ["ex_date", "kind", "ratio", "cash"]


def test_raw_bars_and_actions_reproduce_the_vendors_adjusted_returns():
    """The raw prints and the actions, through warehouse/adjust.py, give back EODHD's own adjusted_close
    returns across the split and the dividend (its adjusted_close is rounded to 4 decimals)."""
    eod, _ = _fixture("eod", *LISTED)
    served = eodhd.parse_eod(eod)
    splits = eodhd.parse_splits(_fixture("splits", *LISTED)[0])
    divs = eodhd.parse_dividends(_fixture("div", *LISTED)[0])
    derived = adjust.series(eodhd.raw_bars(served, splits), eodhd.as_actions(splits, divs), dividends=True)
    ours = derived["close"].pct_change().dropna()
    theirs = served["adjusted_close"].pct_change().dropna()
    assert np.max(np.abs(ours.to_numpy() - theirs.to_numpy())) < 2e-5


@pytest.mark.skipif(not _delisted_fixtures(), reason=(
    "BLOCKED on the owner's EODHD key: no recorded delisted-ticker response is committed. Record one with "
    "`cd quant && EODHD_API_KEY=... uv run --frozen python -m ohcamel_quant.warehouse eodhd-slice LEH.US "
    "2008-08-01 2008-10-31 tests/data --delisted --last-trading-date 2008-09-17` and commit the six files"))
def test_the_adapter_parses_a_delisted_response():
    path = _delisted_fixtures()[0]
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    assert meta["synthetic"] is False and meta["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    code, start, end = meta["code"], meta["params"]["from"], meta["params"]["to"]
    splits = eodhd.parse_splits(_fixture("splits", code, start, end)[0])
    raw = eodhd.raw_bars(eodhd.parse_eod(json.loads(path.read_bytes())), splits)
    last = date.fromisoformat(meta["last_trading_date"])
    assert len(raw) and (raw[["open", "high", "low", "close"]] > 0).all().all()
    # the series stops at the name's last trading date (within the acceptance test's 5 sessions)
    assert int(np.busday_count(raw.index[-1].date(), last)) <= 5 and raw.index[-1].date() <= last + pd.Timedelta(days=7)


def test_refuses_without_a_key():
    with pytest.raises(DataUnavailable, match="EODHD_API_KEY"):
        eodhd.bars("AAPL", date(2020, 1, 2), date(2020, 1, 31), delisted=False,
                   settings=Settings(offline=False, EODHD_API_KEY=None))


def test_no_vendor_code_for_a_reused_tickers_earlier_holder():
    assert eodhd.code_for("BRK.B") == "BRK-B.US" and eodhd.code_for("aapl") == "AAPL.US"
    with pytest.raises(DataUnavailable, match="reused"):
        eodhd.code_for("AGN@2015-03-23")


@respx.mock
def test_the_key_never_reaches_an_error_or_the_provenance(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()
    secret = "k3y-for-this-test-only"
    respx.get(url__regex=r"https://eodhd\.com/api/eod/.*").mock(
        return_value=httpx.Response(403, text=f"Forbidden for api_token={secret}"))
    s = Settings(offline=False, http_retries=0, EODHD_API_KEY=secret)
    with pytest.raises(DataUnavailable) as e:
        eodhd.bars("LEH", date(2008, 9, 1), date(2008, 9, 30), delisted=True, settings=s)
    assert secret not in str(e.value) and "403" in str(e.value)
    prov = eodhd.provenance("eod", "LEH.US", {"from": "2008-09-01", "to": "2008-09-30"}, delisted=True)
    assert secret not in json.dumps(prov) and "api_token" not in json.dumps(prov)


def test_http_log_lines_never_carry_a_key():
    import logging

    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s"',
                            ("GET", "https://eodhd.com/api/eod/LEH.US?api_token=abc123&fmt=json", "HTTP/1.1 200 OK"),
                            None)
    for f in logging.getLogger("httpx").filters:
        f.filter(rec)
    assert "abc123" not in rec.getMessage() and "api_token=***" in rec.getMessage()


@respx.mock
@pytest.mark.parametrize("which", ["fred", "eodhd"])
def test_no_key_reaches_a_log_record_or_an_exception(monkeypatch, caplog, which):
    """M2: the shared HTTP layer's own retry log and its errors carry the vendor's words, scrubbed of every key:
    by parameter name and by the key's literal value, wherever the vendor echoes it."""
    import logging

    from ohcamel_quant.data import fred

    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()
    secret = "s3cr3t-value-0123456789"
    body = f"bad request api_key={secret} api_token={secret} echoed {secret}"
    respx.get(url__regex=r"https://(api\.stlouisfed\.org|eodhd\.com)/.*").mock(
        side_effect=[httpx.Response(503, text=body), httpx.Response(400, text=body)])
    caplog.set_level(logging.DEBUG)
    s = Settings(offline=False, http_retries=1, FRED_API_KEY=secret, EODHD_API_KEY=secret)
    with pytest.raises(DataUnavailable) as e:
        if which == "fred":
            http.get_json("https://api.stlouisfed.org/fred/series/observations", s,
                          params={"series_id": "UNRATE", "api_key": secret, "file_type": "json"}, source="fred-api")
        else:
            eodhd.bars("LEH", date(2008, 9, 1), date(2008, 9, 30), delisted=True, settings=s)
    assert secret not in str(e.value)
    assert caplog.records and not [r for r in caplog.records if secret in r.getMessage()]
    assert fred  # the FRED client passes its key the same way (data/fred.py)
