"""SEC EDGAR parsers (company_tickers, companyfacts, submissions, 13F
information tables) and OpenFIGI mapping, offline with vendor-format samples."""

from __future__ import annotations

import json
from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from ohcamel_quant.config import Settings
from ohcamel_quant.data import http, openfigi, sec
from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.data.market import MarketData


def online(tmp_path, **kw) -> Settings:
    base = dict(offline=False, data_dir=tmp_path, http_retries=0, APCA_API_KEY_ID=None,
                APCA_API_SECRET_KEY=None, FRED_API_KEY=None)
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    http.reset_rate_limits()


COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
    "2": {"cik_str": 1067983, "ticker": "BRK-A", "title": "BERKSHIRE HATHAWAY INC"},
    "3": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
    "4": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "5": {"cik_str": 1467858, "ticker": "GM", "title": "General Motors Co"},
    "6": {"cik_str": 1990001, "ticker": "APLE", "title": "Apple Hospitality REIT, Inc."},
}


def test_parse_company_tickers_and_rank_search():
    t = sec.parse_company_tickers(COMPANY_TICKERS)
    assert t.loc[t["ticker"] == "AAPL", "cik"].iloc[0] == "0000320193"
    hits = sec.rank_search(t, "apple")
    assert [h["ticker"] for h in hits[:2]] == ["AAPL", "APLE"]  # name prefix, shorter ticker first
    hits = sec.rank_search(t, "GM")
    assert hits[0]["ticker"] == "GM"  # exact ticker beats everything
    assert sec.rank_search(t, "brk")[0]["ticker"] in ("BRK-A", "BRK-B")
    assert sec.rank_search(t, "  ") == []


@respx.mock
def test_resolve_share_class_and_cik(tmp_path):
    s = online(tmp_path)
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(200, json=COMPANY_TICKERS))
    assert sec.resolve("BRK.B", s)[0] == "0001067983"
    assert sec.resolve("320193", s)[2] == "AAPL"
    with pytest.raises(DataUnavailable, match="not found"):
        sec.resolve("ZZZZ", s)
    ds = MarketData(s).search("amaz")
    assert ds.data[0]["ticker"] == "AMZN" and ds.provenance[0].source == "sec-edgar"


# ----------------------------------------------------------- companyfacts
def f(start, end, val, form, filed, fy=None, fp=None, accn="0000320193-23-000106"):
    d = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


K23, K22, K17 = "2023-11-03", "2022-10-28", "2017-11-03"
FACTS = {
    "cik": 320193, "entityName": "Apple Inc.",
    "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            f(None, "2022-10-14", 15908118000, "10-K", K22, 2022, "FY", "a22"),
            f(None, "2023-10-20", 15552752000, "10-K", K23, 2023, "FY", "a23")]}}},
        "us-gaap": {
            "SalesRevenueNet": {"units": {"USD": [
                f("2016-09-25", "2017-09-30", 229234e6, "10-K", K17, 2017, "FY")]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                # FY2022 as first reported, then again as a comparative (restated) in the FY2023 10-K
                f("2021-09-26", "2022-09-24", 394328e6, "10-K", K22, 2022, "FY"),
                f("2021-09-26", "2022-09-24", 394330e6, "10-K", K23, 2023, "FY"),
                f("2020-09-27", "2021-09-25", 365817e6, "10-K", K22, 2022, "FY"),
                f("2022-09-25", "2023-09-30", 383285e6, "10-K", K23, 2023, "FY"),
                f("2022-09-25", "2022-12-31", 117154e6, "10-Q", "2023-02-03", 2023, "Q1"),
                f("2023-01-01", "2023-04-01", 94836e6, "10-Q", "2023-05-05", 2023, "Q2"),
                f("2022-09-25", "2023-04-01", 211990e6, "10-Q", "2023-05-05", 2023, "Q2"),
                f("2023-04-02", "2023-07-01", 81797e6, "10-Q", "2023-08-04", 2023, "Q3"),
                f("2022-09-25", "2023-07-01", 293787e6, "10-Q", "2023-08-04", 2023, "Q3"),
            ]}},
            "CostOfGoodsAndServicesSold": {"units": {"USD": [
                f("2022-09-25", "2023-09-30", 214137e6, "10-K", K23, 2023, "FY")]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                f("2022-09-25", "2022-12-31", 34005e6, "10-Q", "2023-02-03", 2023, "Q1"),
                f("2022-09-25", "2023-04-01", 62565e6, "10-Q", "2023-05-05", 2023, "Q2"),
                f("2022-09-25", "2023-07-01", 88945e6, "10-Q", "2023-08-04", 2023, "Q3"),
                f("2022-09-25", "2023-09-30", 110543e6, "10-K", K23, 2023, "FY"),
            ]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
                f("2022-09-25", "2023-09-30", 10959e6, "10-K", K23, 2023, "FY")]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                f("2022-09-25", "2023-09-30", 6.13, "10-K", K23, 2023, "FY"),
                f("2022-09-25", "2022-12-31", 1.88, "10-Q", "2023-02-03", 2023, "Q1"),
                f("2022-09-25", "2023-04-01", 3.40, "10-Q", "2023-05-05", 2023, "Q2"),
            ]}},
            "Assets": {"units": {"USD": [
                f(None, "2022-09-24", 352755e6, "10-K", K23, 2023, "FY"),
                f(None, "2023-09-30", 352583e6, "10-K", K23, 2023, "FY"),
                f(None, "2022-12-31", 346747e6, "10-Q", "2023-02-03", 2023, "Q1"),
                f(None, "2022-06-25", 336309e6, "10-Q", "2022-07-29", 2022, "Q3"),
            ]}},
            "LongTermDebtNoncurrent": {"units": {"USD": [f(None, "2023-09-30", 95281e6, "10-K", K23, 2023, "FY")]}},
            "CommercialPaper": {"units": {"USD": [f(None, "2023-09-30", 5985e6, "10-K", K23, 2023, "FY")]}},
            "OperatingIncomeLoss": {"units": {"USD": [
                f("2022-09-25", "2023-09-30", 114301e6, "10-K", K23, 2023, "FY")]}},
            "DepreciationDepletionAndAmortization": {"units": {"USD": [
                f("2022-09-25", "2023-09-30", 11519e6, "10-K", K23, 2023, "FY")]}},
        },
    },
}


def test_standardize_annual_merges_tags_and_prefers_latest_filing():
    res = sec.standardize(FACTS)
    a = res["annual"]
    assert list(a.index) == [pd.Timestamp(d) for d in ("2017-09-30", "2021-09-25", "2022-09-24", "2023-09-30")]
    assert a.loc["2017-09-30", "revenue"] == 229234e6          # older tag fills older years
    assert a.loc["2022-09-24", "revenue"] == 394330e6          # restated comparative wins
    assert a.loc["2023-09-30", "gross_profit"] == 383285e6 - 214137e6   # derived where missing
    assert a.loc["2023-09-30", "fcf"] == 110543e6 - 10959e6
    assert a.loc["2023-09-30", "ebitda"] == 114301e6 + 11519e6
    assert a.loc["2023-09-30", "total_debt"] == 95281e6 + 5985e6
    assert a.loc["2022-09-24", "total_assets"] == 352755e6
    assert a.loc["2023-09-30", "eps_diluted"] == 6.13
    assert res["tags_used"]["revenue"] == ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]
    assert res["first_filed_annual"].loc["2022-09-24"] == pd.Timestamp(K22)


def test_standardize_quarterly_derives_q4_and_ytd_cash_flow():
    q = sec.standardize(FACTS)["quarterly"]
    assert q.loc["2022-12-31", "revenue"] == 117154e6
    assert q.loc["2023-09-30", "revenue"] == pytest.approx(383285e6 - 293787e6)   # Q4 = FY - 9M
    cfo = q["cfo"].dropna()
    assert list(cfo.round(-6) / 1e6) == [34005, 28560, 26380, 21598]               # YTD differences
    assert q.loc["2023-04-01", "eps_diluted"] != pytest.approx(3.40 - 1.88) or np.isnan(
        q.loc["2023-04-01", "eps_diluted"])                                          # EPS never differenced
    assert np.isnan(q.loc["2023-09-30", "eps_diluted"])
    assert q.loc["2022-12-31", "total_assets"] == 346747e6
    assert pd.Timestamp("2022-06-25") not in q.index  # not a reported quarter end of any flow item


def test_standardize_rejects_ifrs_only_filers():
    with pytest.raises(DataUnavailable, match="IFRS"):
        sec.standardize({"entityName": "Foreign", "facts": {"ifrs-full": {}}})


def test_shares_outstanding_latest_dei():
    v, d, tag = sec._shares_outstanding(FACTS["facts"])
    assert v == 15552752000 and d == "2023-10-20" and tag.startswith("dei:")


@respx.mock
def test_fetch_company_facts_end_to_end_and_cache(tmp_path):
    s = online(tmp_path)
    respx.get(sec.TICKERS_URL).mock(return_value=httpx.Response(200, json=COMPANY_TICKERS))
    route = respx.get(sec.FACTS_URL.format(cik="0000320193")).mock(return_value=httpx.Response(200, json=FACTS))
    ds = MarketData(s).company_facts("aapl")
    d = ds.data
    assert d["cik"] == "0000320193" and d["name"] == "Apple Inc." and d["ticker"] == "AAPL"
    assert d["shares_outstanding"] == 15552752000
    assert d["annual"].loc["2023-09-30", "revenue"] == 383285e6
    assert d["annual"].index.name == "period_end"
    d2 = MarketData(s).company_facts("AAPL").data   # decoded from the store
    assert route.call_count == 1
    pd.testing.assert_frame_equal(d["annual"], d2["annual"], check_freq=False)
    pd.testing.assert_frame_equal(d["quarterly"], d2["quarterly"], check_freq=False)


# --------------------------------------------------------------------- 13F
NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def info_table(prefix: str = "ns1") -> str:
    p = f"{prefix}:" if prefix else ""
    xmlns = f'xmlns:{prefix}="{NS}"' if prefix else f'xmlns="{NS}"'

    def row(issuer, cusip, value, shares, put_call=None, manager="4"):
        pc = f"<{p}putCall>{put_call}</{p}putCall>" if put_call else ""
        return (f"<{p}infoTable><{p}nameOfIssuer>{issuer}</{p}nameOfIssuer><{p}titleOfClass>COM</{p}titleOfClass>"
                f"<{p}cusip>{cusip}</{p}cusip><{p}value>{value}</{p}value><{p}shrsOrPrnAmt>"
                f"<{p}sshPrnamt>{shares}</{p}sshPrnamt><{p}sshPrnamtType>SH</{p}sshPrnamtType></{p}shrsOrPrnAmt>"
                f"{pc}<{p}investmentDiscretion>DFND</{p}investmentDiscretion><{p}otherManager>{manager}"
                f"</{p}otherManager><{p}votingAuthority><{p}Sole>{shares}</{p}Sole><{p}Shared>0</{p}Shared>"
                f"<{p}None>0</{p}None></{p}votingAuthority></{p}infoTable>")

    rows = (row("APPLE INC", "037833100", 1000, 10) + row("APPLE INC", "037833100", 500, 5, manager="4,8")
            + row("BANK AMER CORP", "060505104", 500, 20) + row("SPDR S&amp;P 500 ETF TR", "78462F103", 2000, 4, "Put"))
    return (f'<?xml version="1.0" encoding="UTF-8"?><{p}informationTable {xmlns} '
            f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{rows}</{p}informationTable>')


@pytest.mark.parametrize("prefix", ["ns1", ""])
def test_parse_info_table_namespaces(prefix):
    raw = sec.parse_info_table(info_table(prefix).encode())
    assert len(raw) == 4 and set(raw["cusip"]) == {"037833100", "060505104", "78462F103"}
    assert raw["put_call"].isna().tolist() == [True, True, True, False] and raw["put_call"].iloc[3] == "Put"


def test_aggregate_value_units_pre_and_post_2023():
    raw = sec.parse_info_table(info_table())
    pre = sec.aggregate_holdings(raw, date(2022, 11, 14))
    post = sec.aggregate_holdings(raw, date(2023, 2, 14))
    aapl_pre = pre[(pre["cusip"] == "037833100")].iloc[0]
    assert aapl_pre["value_usd"] == 1_500_000 and aapl_pre["shares"] == 15      # thousands -> USD, summed
    assert post[post["cusip"] == "037833100"]["value_usd"].iloc[0] == 1500
    assert aapl_pre["weight"] == pytest.approx(0.75)
    assert pre.loc[pre["cusip"] == "060505104", "weight"].iloc[0] == pytest.approx(0.25)
    assert np.isnan(pre.loc[pre["put_call"] == "Put", "weight"].iloc[0])
    assert pre["weight"].sum() == pytest.approx(1.0)
    assert sec.value_multiplier(date(2023, 1, 2)) == 1000 and sec.value_multiplier(date(2023, 1, 3)) == 1


SUBMISSIONS = {
    "cik": "1067983", "name": "BERKSHIRE HATHAWAY INC",
    "filings": {"recent": {
        "accessionNumber": ["0000950123-24-011775", "0000950123-24-011700", "0000950123-24-005000",
                            "0000950123-23-012000"],
        "filingDate": ["2024-11-15", "2024-11-14", "2024-08-14", "2023-11-14"],
        "reportDate": ["2024-09-30", "2024-09-30", "2024-06-30", "2023-09-30"],
        "form": ["13F-HR/A", "13F-HR", "4", "13F-HR"],
        "primaryDocument": ["primary_doc.xml"] * 4,
    }},
}


def test_list_and_pick_13f():
    fl = sec.list_13f_filings(SUBMISSIONS)
    assert [x["form"] for x in fl] == ["13F-HR/A", "13F-HR", "13F-HR"]
    assert sec.pick_latest_13f(fl)["accession"] == "0000950123-24-011700"
    with pytest.raises(DataUnavailable):
        sec.pick_latest_13f([])


def test_pick_info_table():
    idx = {"directory": {"item": [
        {"name": "0000950123-24-011700-index-headers.html", "size": ""},
        {"name": "primary_doc.xml", "size": "3000"},
        {"name": "46994.xml", "size": "90000"},
        {"name": "0000950123-24-011700.txt", "size": "99999"}]}}
    assert sec.pick_info_table(idx) == "46994.xml"
    idx["directory"]["item"].append({"name": "form13fInfoTable.xml", "size": "10"})
    assert sec.pick_info_table(idx) == "form13fInfoTable.xml"


def test_openfigi_parsing():
    payload = [
        {"data": [{"figi": "BBG000B9XRY4", "ticker": "AAPL", "exchCode": "US", "marketSector": "Equity"}]},
        {"data": [{"ticker": "BRK/B", "exchCode": "UN", "marketSector": "Equity"},
                  {"ticker": "BRK/B", "exchCode": "US", "marketSector": "Equity"}]},
        {"warning": "No identifier found."},
        {"error": "Invalid idValue format"},
    ]
    out = openfigi.parse_mapping_response(["A", "B", "C", "D"], payload)
    assert out == {"A": "AAPL", "B": "BRK.B", "C": None}  # errors are not cached


@respx.mock
def test_fetch_13f_end_to_end(tmp_path):
    s = online(tmp_path)
    base = "https://www.sec.gov/Archives/edgar/data/1067983/000095012324011700/"
    respx.get(sec.SUBMISSIONS_URL.format(cik="0001067983")).mock(return_value=httpx.Response(200, json=SUBMISSIONS))
    respx.get(base + "index.json").mock(return_value=httpx.Response(200, json={"directory": {"item": [
        {"name": "primary_doc.xml", "size": "1"}, {"name": "infotable.xml", "size": "2"}]}}))
    respx.get(base + "infotable.xml").mock(return_value=httpx.Response(200, text=info_table()))
    figi_calls = []

    def figi(request: httpx.Request) -> httpx.Response:
        jobs = json.loads(request.content)
        figi_calls.append(jobs)
        tick = {"037833100": "AAPL", "060505104": "BAC"}
        return httpx.Response(200, json=[{"data": [{"ticker": tick[j["idValue"]], "exchCode": "US"}]}
                                         if j["idValue"] in tick else {"warning": "No identifier found."}
                                         for j in jobs])

    respx.post(openfigi.URL).mock(side_effect=figi)
    ds = MarketData(s).holdings_13f("1067983")
    d = ds.data
    assert d["filer"] == "BERKSHIRE HATHAWAY INC" and d["period"] == "2024-09-30" and d["form"] == "13F-HR"
    h = d["holdings"]
    by_cusip = h.set_index("cusip")["ticker"]
    assert by_cusip["037833100"] == "AAPL" and by_cusip["060505104"] == "BAC"
    assert pd.isna(by_cusip["78462F103"])  # OpenFIGI "No identifier found." stays unmapped
    assert h["value_usd"].iloc[0] == 2000  # post-2023 filing: dollars; sorted by value (the put)
    assert any("amendment" in n for n in d["notes"])
    assert len(figi_calls) == 1 and all(j["idType"] == "ID_CUSIP" for j in figi_calls[0])
    # second call: CUSIP map cached permanently, holdings cached by accession
    MarketData(s).holdings_13f("0001067983")
    assert len(figi_calls) == 1
    assert openfigi.cached_map(s)["78462F103"] is None
