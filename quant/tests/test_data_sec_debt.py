"""total_debt must not double-count current maturities when a filer reports
only the gross us-gaap:LongTermDebt (which includes the current portion)."""

from ohcamel_quant.data import sec


def _fact(tag, val, end, form="10-K", fy=2024, fp="FY", filed="2025-02-01"):
    return {tag: {"units": {"USD": [{"end": end, "val": val, "form": form, "fy": fy, "fp": fp,
                                     "filed": filed, "accn": "0000000000-25-000001"}]}}}


def _facts(*tags):
    gaap = {}
    for t in tags:
        gaap.update(t)
    rev = {"Revenues": {"units": {"USD": [{"start": "2024-01-01", "end": "2024-12-31", "val": 1000.0,
                                           "form": "10-K", "fy": 2024, "fp": "FY", "filed": "2025-02-01",
                                           "accn": "0000000000-25-000001"}]}}}
    gaap.update(rev)
    return {"cik": 1, "entityName": "Test Co", "facts": {"us-gaap": gaap}}


def test_gross_long_term_debt_is_not_double_counted():
    f = _facts(_fact("LongTermDebt", 500.0, "2024-12-31"),
               _fact("LongTermDebtCurrent", 50.0, "2024-12-31"),
               _fact("ShortTermBorrowings", 20.0, "2024-12-31"))
    out = sec.standardize(f)
    row = out["annual"].iloc[-1]
    assert row["total_debt"] == 520.0  # 500 (incl. 50 current) + 20 borrowings, not 570


def test_noncurrent_plus_current_is_summed():
    f = _facts(_fact("LongTermDebtNoncurrent", 450.0, "2024-12-31"),
               _fact("LongTermDebtCurrent", 50.0, "2024-12-31"))
    out = sec.standardize(f)
    assert out["annual"].iloc[-1]["total_debt"] == 500.0


def test_shares_outstanding_falls_back_to_diluted_weighted_average():
    facts = {"us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
        {"start": "2024-01-01", "end": "2024-12-31", "val": 4.3e9, "form": "10-K", "fy": 2024,
         "fp": "FY", "filed": "2025-02-01", "accn": "a"}]}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2025-01-31", "val": 0, "form": "10-K", "filed": "2025-02-01", "accn": "a"}]}}}}
    val, _, tag = sec._shares_outstanding(facts)
    assert val == 4.3e9 and tag.endswith("WeightedAverageNumberOfDilutedSharesOutstanding")


def test_fetch_company_facts_skips_a_registrant_without_annual_reports(monkeypatch):
    """XOM is listed under a new holding company (no 10-K yet) and the legacy filer."""
    import pandas as pd

    from ohcamel_quant.config import Settings
    from ohcamel_quant.data.base import DataUnavailable, Provenance

    table = pd.DataFrame({"cik": ["0002115436", "0000034088"], "ticker": ["XOM", "XOM"],
                          "name": ["ExxonMobil Holdings Corp", "EXXON MOBIL CORP"]})
    monkeypatch.setattr(sec, "ticker_table", lambda settings: (table, None))
    tried = []

    def fake(cik, name, tick, settings):
        tried.append(cik)
        if cik == "0002115436":
            raise DataUnavailable("sec: no annual (10-K) duration facts")
        return type("D", (), {"data": {"cik": cik}, "provenance": [Provenance.now("sec-edgar")]})()

    monkeypatch.setattr(sec, "_fetch_company_facts", fake)
    out = sec.fetch_company_facts("XOM", Settings(offline=False))
    assert out.data["cik"] == "0000034088" and tried == ["0002115436", "0000034088"]


def test_registrant_with_only_10q_facts_is_kept_with_empty_annual():
    q = [{"start": "2026-01-01", "end": "2026-03-31", "val": 85e9, "form": "10-Q", "fy": 2026,
          "fp": "Q1", "filed": "2026-05-04", "accn": "a"}]
    f = {"entityName": "NewCo", "facts": {"us-gaap": {"Revenues": {"units": {"USD": q}}},
                                          "dei": {"EntityCommonStockSharesOutstanding": {"units": {
                                              "shares": [{"end": "2026-04-30", "val": 4.2e9, "form": "10-Q",
                                                          "filed": "2026-05-04", "accn": "a"}]}}}}}
    out = sec.standardize(f)
    assert out["annual"].empty and out["quarterly"]["revenue"].iloc[-1] == 85e9
    assert any("no 10-K yet" in n for n in out["notes"])
    assert sec._shares_outstanding(f["facts"])[0] == 4.2e9
