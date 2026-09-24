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
