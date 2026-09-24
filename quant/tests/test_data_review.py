"""Regression tests for defects found in the data-layer review (each failed
before its fix): Alpaca forming bar, per-ticker fetch single flight, store
read/write consistency, 52/53-week fiscal quarters, 13F period ranking and
unit sanity, French non-return tables, FRED CSV with a BOM."""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import fred, french, http, prices, sec
from ohcamel_quant.data.base import Provenance
from ohcamel_quant.data.store import Store, get_store


def online(tmp_path, **kw) -> Settings:
    base = dict(offline=False, data_dir=tmp_path, http_retries=0, APCA_API_KEY_ID=None,
                APCA_API_SECRET_KEY=None, FRED_API_KEY=None)
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    http.reset_rate_limits()


# ------------------------------------------------------------ alpaca bars
def _alpaca_bars(days_closes):
    return [{"t": f"{d}T05:00:00Z", "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 10} for d, c in days_closes]


@respx.mock
def test_alpaca_forming_daily_bar_is_not_history(tmp_path, monkeypatch):
    """During the session Alpaca returns today's *forming* bar; it must not be
    stored as a session close (Yahoo's parser already drops it)."""
    s = online(tmp_path, APCA_API_KEY_ID="k", APCA_API_SECRET_KEY="sec")
    bars = _alpaca_bars([("2024-01-02", 100.0), ("2024-01-03", 101.0)])
    respx.get(url__startswith=prices.ALPACA_BASE + "/stocks/SPY/bars").mock(
        return_value=httpx.Response(200, json={"bars": bars, "next_page_token": None}))
    # 14:00 New York on 2024-01-03: the 01-03 bar is still forming
    monkeypatch.setattr(prices, "_now", lambda: datetime(2024, 1, 3, 19, 0, tzinfo=UTC))
    df, prov = prices._fetch_alpaca("SPY", date(2024, 1, 1), None, s)
    assert list(df.index) == [pd.Timestamp("2024-01-02")]
    assert any("in-progress" in n for n in prov.detail["notes"])
    # 16:30 New York (data delayed 16 min -> 16:14): the session has closed
    monkeypatch.setattr(prices, "_now", lambda: datetime(2024, 1, 3, 21, 30, tzinfo=UTC))
    df, prov = prices._fetch_alpaca("SPY", date(2024, 1, 1), None, s)
    assert list(df.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert not any("in-progress" in n for n in prov.detail["notes"])


@respx.mock
def test_concurrent_requests_for_one_ticker_fetch_once(tmp_path):
    """FastAPI runs sync endpoints in a threadpool: N simultaneous requests for
    an uncached ticker must trigger one full-history download, not N."""
    s = online(tmp_path)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=5)]
    ts = [int(pd.Timestamp(f"{d} 09:30", tz="America/New_York").timestamp()) for d in days]
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    payload = {"chart": {"result": [{
        "meta": {"symbol": "SPY", "exchangeTimezoneName": "America/New_York", "currency": "USD"},
        "timestamp": ts,
        "indicators": {"quote": [{"open": closes, "high": closes, "low": closes, "close": closes,
                                  "volume": [1] * 5}], "adjclose": [{"adjclose": closes}]}}], "error": None}}

    def slow(request):
        time.sleep(0.2)
        return httpx.Response(200, json=payload)

    route = respx.get(url__startswith=prices.YAHOO_CHART).mock(side_effect=slow)
    out: list = []
    threads = [threading.Thread(target=lambda: out.append(prices.fetch_ohlcv("SPY", None, None, s)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(out) == 4 and all(len(d.data) == 5 for d in out)
    assert route.call_count == 1


# ------------------------------------------------------------------ store
def test_store_read_never_pairs_payload_with_another_writes_provenance(tmp_path, monkeypatch):
    """put() replaces the payload, then the sidecar. A reader in between used
    to get the NEW payload with the OLD provenance (e.g. an Alpaca-from-2016
    frame labelled as a Yahoo-from-1990 copy)."""
    st = Store(tmp_path)
    idx = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03"]), name="date")
    st.put("prices", "SPY", pd.DataFrame({"close": [1.0, 2.0]}, index=idx), Provenance.now("old"))
    real_read = pd.read_parquet
    writers: list[threading.Thread] = []

    def racing_read(path, *a, **kw):
        df = real_read(path, *a, **kw)
        if not writers:
            w = threading.Thread(target=st.put, args=(
                "prices", "SPY", pd.DataFrame({"close": [7.0, 8.0]}, index=idx), Provenance.now("new")))
            writers.append(w)
            w.start()
            w.join(0.3)  # the writer must not be able to interleave with this read
        return df

    monkeypatch.setattr("ohcamel_quant.data.store.pd.read_parquet", racing_read)
    e = st.read("prices", "SPY")
    assert (e.provenance.source, e.obj["close"].iloc[0]) in {("old", 1.0), ("new", 7.0)}
    writers[0].join()
    e2 = st.read("prices", "SPY")
    assert (e2.provenance.source, e2.obj["close"].iloc[0]) == ("new", 7.0)


# -------------------------------------------------------- SEC fiscal quarters
def _f(start, end, val, form, filed):
    d = {"end": end, "val": val, "form": form, "filed": filed, "accn": "x"}
    if start:
        d["start"] = start
    return d


def test_q4_derived_for_52_53_week_filer_with_16_week_fourth_quarter():
    """Costco-style fiscal year: quarters of 12/12/12/17 weeks (FY2023 ended
    2023-09-03; Q3 ended 2023-05-07, so Q4 spans 119 days). Q4 = FY - 36 weeks
    must still be derived; the old 80-100-day window silently dropped it."""
    S = "2022-08-29"
    facts = {"entityName": "Costco-like", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _f(S, "2022-11-20", 54.44e9, "10-Q", "2022-12-14"),
        _f(S, "2023-02-12", 109.7e9, "10-Q", "2023-03-15"),
        _f(S, "2023-05-07", 163.7e9, "10-Q", "2023-06-01"),
        _f(S, "2023-09-03", 242.3e9, "10-K", "2023-10-11"),
        _f("2022-11-21", "2023-02-12", 55.26e9, "10-Q", "2023-03-15"),
        _f("2023-02-13", "2023-05-07", 54.0e9, "10-Q", "2023-06-01"),
    ]}}}}}
    q = sec.standardize(facts)["quarterly"]["revenue"]
    assert q.loc["2023-09-03"] == pytest.approx(242.3e9 - 163.7e9)
    assert q.loc["2022-11-20"] == pytest.approx(54.44e9)
    assert q.loc["2023-02-12"] == pytest.approx(55.26e9)


# -------------------------------------------------------------------- 13F
def test_pick_latest_13f_ranks_by_report_period_not_filing_date():
    filings = [  # newest filing first, as list_13f_filings returns them
        {"accession": "a3", "form": "13F-HR", "filed": "2024-09-20", "period": "2023-12-31"},  # late catch-up
        {"accession": "a2", "form": "13F-HR", "filed": "2024-08-14", "period": "2024-06-30"},
        {"accession": "a1", "form": "13F-HR", "filed": "2024-05-15", "period": "2024-03-31"},
    ]
    assert sec.pick_latest_13f(filings)["accession"] == "a2"


def test_13f_value_unit_sanity_note():
    raw = pd.DataFrame({
        "issuer": ["A", "B", "C"], "title": ["COM"] * 3, "cusip": ["1", "2", "3"],
        "value": [19_000.0, 5_000.0, 800.0], "shares": [100_000.0, 20_000.0, 4_000.0],
        "shares_type": ["SH"] * 3, "put_call": [None] * 3,
    })
    # post-2023 filing that still reports thousands: implied prices $0.19-0.25
    h = sec.aggregate_holdings(raw, date(2024, 2, 14))
    assert "thousands" in (sec.value_unit_warning(h, date(2024, 2, 14)) or "")
    # the same numbers in a 2022 filing are thousands -> $190, $250, $200: fine
    h = sec.aggregate_holdings(raw, date(2022, 2, 14))
    assert sec.value_unit_warning(h, date(2022, 2, 14)) is None


# ---------------------------------------------------------------- French
PORTFOLIO_FILE = """This file was created by CMPT_IND_RETS using the 202401 CRSP database.
It contains value- and equal-weighted returns for 5 industry portfolios.

  Average Value Weighted Returns -- Monthly
,Cnsmr,Manuf,HiTec,Hlth ,Other
192607,    5.43,    2.73,    1.83,    1.77,    2.13
192608,    2.76,    2.33,    2.41,    4.25,    4.35

  Number of Firms in Portfolios
,Cnsmr,Manuf,HiTec,Hlth ,Other
192607,     166,     263,      60,      15,     176
192608,     167,     263,      60,      15,     176

  Average Firm Size
,Cnsmr,Manuf,HiTec,Hlth ,Other
192607,   20.93,   30.52,   51.51,    8.42,   25.44
192608,   22.01,   31.35,   52.47,    8.57,   25.97

Copyright 2024 Kenneth R. French
"""


def test_french_non_return_tables_keep_their_units():
    tables = french.parse_french_tables(PORTFOLIO_FILE)
    titles = [t for t, _ in tables]
    assert titles == ["Average Value Weighted Returns -- Monthly", "Number of Firms in Portfolios",
                      "Average Firm Size"]
    assert tables[0][1].iloc[0, 0] == pytest.approx(0.0543)        # percent -> decimal
    assert tables[1][1].iloc[0, 0] == 166.0                        # a firm count, not 1.66
    assert tables[2][1].iloc[0, 2] == pytest.approx(51.51)         # $ millions
    assert list(tables[1][1].columns) == ["Cnsmr", "Manuf", "HiTec", "Hlth", "Other"]


@respx.mock
def test_french_dataset_provenance_units_for_firm_counts(tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("5_Industry_Portfolios.CSV", PORTFOLIO_FILE)
    s = online(tmp_path)
    respx.get(french.BASE + "5_Industry_Portfolios_CSV.zip").mock(return_value=httpx.Response(200, content=buf.getvalue()))
    ds = french.fetch_dataset("5_Industry_Portfolios", s, table=1)
    assert ds.data.iloc[0, 0] == 166.0
    assert ds.provenance[0].detail["units"].startswith("as published")


# ------------------------------------------------------------------ FRED
def test_fredgraph_csv_with_utf8_bom():
    text = "﻿observation_date,DGS10\n2024-01-02,3.95\n2024-01-03,.\n"
    df = fred.parse_fredgraph_csv(text)
    assert df.loc["2024-01-02", "DGS10"] == 3.95 and np.isnan(df.loc["2024-01-03", "DGS10"])


def test_store_get_store_is_shared(tmp_path):
    s = online(tmp_path)
    assert get_store(s) is get_store(online(tmp_path))


@respx.mock
def test_fred_batch_with_one_unknown_id_still_serves_the_others(tmp_path):
    """fredgraph.csv fails the whole request when one id is unknown; the error
    used to be attributed to every id (DGS10 'unavailable')."""
    s = online(tmp_path)

    def handler(request):
        ids = request.url.params["id"].split(",")
        if "BOGUS" in ids:
            return httpx.Response(404, text="Series not found")
        return httpx.Response(200, text="observation_date," + ",".join(ids) + "\n2024-01-02,"
                              + ",".join("3.95" for _ in ids) + "\n")

    route = respx.get(fred.FREDGRAPH).mock(side_effect=handler)
    from ohcamel_quant.data.base import DataUnavailable

    with pytest.raises(DataUnavailable) as ei:
        fred.fetch_series(["DGS10", "BOGUS"], None, None, s)
    msg = str(ei.value)
    assert "BOGUS" in msg and "DGS10" not in msg
    assert route.call_count == 3  # batch, then one per id
    ds = fred.fetch_series(["DGS10"], None, None, s)  # the good one was cached
    assert ds.data["DGS10"].iloc[0] == 3.95 and route.call_count == 3


@respx.mock
def test_alpaca_only_history_is_reused_within_ttl_when_yahoo_is_down(tmp_path, monkeypatch):
    """Yahoo rate-limited, Alpaca has 2016+. The default full-history call
    (start=None -> 1990) used to find the Alpaca copy 'not covering' 1990 and
    re-run the whole chain on EVERY request."""
    s = online(tmp_path, APCA_API_KEY_ID="k", APCA_API_SECRET_KEY="sec")
    monkeypatch.setattr(prices, "_now", lambda: datetime(2024, 1, 4, 22, 0, tzinfo=UTC))
    y = respx.get(url__startswith=prices.YAHOO_CHART).mock(return_value=httpx.Response(429))
    bars = _alpaca_bars([("2024-01-02", 100.0), ("2024-01-03", 101.0)])
    a = respx.get(url__startswith=prices.ALPACA_BASE + "/stocks/SPY/bars").mock(
        return_value=httpx.Response(200, json={"bars": bars, "next_page_token": None}))
    ds1 = prices.fetch_ohlcv("SPY", None, None, s)
    assert ds1.provenance[0].source == "alpaca" and (y.call_count, a.call_count) == (1, 2)
    ds2 = prices.fetch_ohlcv("SPY", None, None, s)
    assert (y.call_count, a.call_count) == (1, 2)  # fresh best-available copy, no refetch
    assert ds2.data.equals(ds1.data)
