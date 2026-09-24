"""SEC EDGAR: ticker search, standardized XBRL financials, 13F holdings.

Endpoints (https://www.sec.gov/edgar/sec-api-documentation):

* ``https://www.sec.gov/files/company_tickers.json`` -- ``{"0": {"cik_str",
  "ticker", "title"}, ...}``; cached daily.
* ``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`` -- every
  non-dimensional XBRL fact a company has filed, by taxonomy/tag/unit, each
  with ``start`` (duration facts), ``end``, ``val``, ``form``, ``fy``, ``fp``,
  ``filed``, ``accn``, ``frame``.
* ``https://data.sec.gov/submissions/CIK##########.json`` -- filing history.
* ``https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json`` --
  a filing's documents (13F information table XML).

Fair access: SEC asks for a descriptive ``User-Agent`` with contact details
and at most 10 requests/second; :mod:`.http` sends ``Settings.user_agent``
and spaces requests to these hosts by 0.12 s.

Standardization (company facts -> statements)
---------------------------------------------
Each standard line item has a *prioritized* list of us-gaap tags
(:data:`LINE_ITEMS`); companies switch tags over time (e.g. ``Revenues`` ->
``RevenueFromContractWithCustomerExcludingAssessedTax`` after ASC 606), so
series are merged period-by-period with the higher-priority tag winning.

* **Annual** rows: facts from 10-K family forms. Duration items require a
  fiscal-year duration of 330-400 days (``end - start``); 52/53-week years
  fit. A 10-K also carries prior-year comparatives, so facts are keyed by
  period ``end`` (not by the filing's ``fy``), and when a period was reported
  more than once the most recently *filed* value wins (restatements).
  Instant items (balance sheet) take the value at each fiscal-year end.
* **Quarterly** rows: fiscal-quarter (80-120 day, covering 12- to 17-week
  quarters of 52/53-week filers) facts from 10-Q/10-K. Many items
  (all cash-flow items, and every Q4) are only reported year-to-date, so for
  additive flow items a quarter is derived as the difference of consecutive
  cumulative facts sharing a fiscal-year start: ``Q2 = H1 - Q1``,
  ``Q3 = 9M - H1``, ``Q4 = FY - 9M``. Per-share and share-count items are not
  additive and are never derived.
* Derived: ``gross_profit = revenue - cost_of_revenue`` (only where not
  reported), ``ebitda = operating_income + d_and_a``, ``total_debt =
  long_term_debt + short_term_debt`` (sum of reported components),
  ``fcf = cfo - capex``.

Caveat (stated in the payload notes): values are the latest restated
figures, not point-in-time as first reported; ``first_filed`` gives the
earliest filing date that reported each period, for look-ahead-free use.

13F
---
Form 13F-HR information tables (Securities Exchange Act s.13(f); SEC Form
13F instructions): ``value`` is reported in *thousands* of USD for filings
made before 2023-01-03 and in whole dollars on/after (SEC Release 34-95148,
2022). Rows are aggregated by (CUSIP, put/call); portfolio weights use only
non-option rows (option rows report the underlying's value, not premium).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from io import StringIO
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ..config import Settings
from . import http
from .base import DataUnavailable, Provenance
from .store import get_store

if TYPE_CHECKING:
    from .market import Dataset

log = logging.getLogger("ohcamel_quant.data.sec")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"
FAMILY = "sec"
VALUE_IN_DOLLARS_FROM = date(2023, 1, 3)

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-KT/A"})
QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A"}) | ANNUAL_FORMS
ANNUAL_DAYS = (330, 400)
# 13-week quarters are 91 days; 52/53-week filers with 12/12/12/16(17)-week
# quarters (e.g. Costco: Q4 = 16 or 17 weeks, 112-119 days) need the upper bound.
QUARTER_DAYS = (80, 120)


@dataclass(frozen=True)
class LineItem:
    tags: tuple[str, ...]
    kind: str = "duration"   # 'duration' (flow over a period) or 'instant' (balance at a date)
    unit: str = "USD"
    additive: bool = True    # can quarters be derived by differencing year-to-date values?


#: Standard line items -> prioritized us-gaap tags (first = preferred).
LINE_ITEMS: dict[str, LineItem] = {
    # --- income statement (duration)
    "revenue": LineItem((
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
        "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense",
    )),
    "cost_of_revenue": LineItem((
        "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
    )),
    "gross_profit": LineItem(("GrossProfit",)),
    "sga": LineItem(("SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense")),
    "rnd": LineItem((
        "ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    )),
    "operating_income": LineItem(("OperatingIncomeLoss",)),
    "interest_expense": LineItem((
        "InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestAndDebtExpense",
    )),
    "pretax_income": LineItem((
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    )),
    "income_tax": LineItem(("IncomeTaxExpenseBenefit",)),
    "net_income": LineItem((
        "NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
    )),
    "eps_diluted": LineItem(("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
                            unit="USD/shares", additive=False),
    "shares_diluted": LineItem(("WeightedAverageNumberOfDilutedSharesOutstanding",),
                               unit="shares", additive=False),
    # --- cash flow (duration; reported year-to-date in 10-Qs)
    "d_and_a": LineItem((
        "DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet", "Depreciation",
    )),
    "cfo": LineItem((
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    )),
    "capex": LineItem((
        "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
    )),
    "dividends_paid": LineItem(("PaymentsOfDividends", "PaymentsOfDividendsCommonStock")),
    "buybacks": LineItem(("PaymentsForRepurchaseOfCommonStock",)),
    # --- balance sheet (instant)
    "total_assets": LineItem(("Assets",), kind="instant"),
    "current_assets": LineItem(("AssetsCurrent",), kind="instant"),
    "cash": LineItem((
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash",
    ), kind="instant"),
    "receivables": LineItem(("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"), kind="instant"),
    "inventory": LineItem(("InventoryNet",), kind="instant"),
    "ppe_net": LineItem(("PropertyPlantAndEquipmentNet",), kind="instant"),
    "total_liabilities": LineItem(("Liabilities",), kind="instant"),
    "current_liabilities": LineItem(("LiabilitiesCurrent",), kind="instant"),
    # LongTermDebt (last resort) includes the current portion; see notes.
    "long_term_debt": LineItem((
        "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt",
    ), kind="instant"),
    "short_term_debt": LineItem((
        "DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper",
    ), kind="instant"),
    "equity": LineItem((
        "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ), kind="instant"),
    "retained_earnings": LineItem(("RetainedEarningsAccumulatedDeficit",), kind="instant"),
}
DERIVED_ITEMS = ("ebitda", "total_debt", "fcf")
ITEM_ORDER = [
    "revenue", "cost_of_revenue", "gross_profit", "sga", "rnd", "operating_income", "d_and_a", "ebitda",
    "interest_expense", "pretax_income", "income_tax", "net_income", "eps_diluted", "shares_diluted",
    "total_assets", "current_assets", "cash", "receivables", "inventory", "ppe_net", "total_liabilities",
    "current_liabilities", "long_term_debt", "short_term_debt", "total_debt", "equity", "retained_earnings",
    "cfo", "capex", "fcf", "dividends_paid", "buybacks",
]


# ------------------------------------------------------------------ tickers
def cik10(cik: Any) -> str:
    """Zero-padded 10-digit CIK string."""
    s = re.sub(r"\D", "", str(cik))
    if not s:
        raise ValueError(f"invalid CIK {cik!r}")
    return s.zfill(10)


def parse_company_tickers(payload: dict[str, Any]) -> pd.DataFrame:
    """``company_tickers.json`` -> frame (ticker, name, cik [10-digit])."""
    rows = [
        {"ticker": str(v["ticker"]).upper(), "name": str(v["title"]), "cik": cik10(v["cik_str"])}
        for v in (payload or {}).values() if isinstance(v, dict) and "ticker" in v
    ]
    if not rows:
        raise DataUnavailable("sec: company_tickers.json is empty")
    return pd.DataFrame(rows)


def ticker_table(settings: Settings) -> tuple[pd.DataFrame, Provenance]:
    def fetch() -> tuple[dict[str, Any], Provenance]:
        payload = http.get_json(TICKERS_URL, settings, source="sec-edgar")
        parse_company_tickers(payload)  # validate before caching
        return payload, Provenance.now("sec-edgar", url=TICKERS_URL)

    payload, prov = get_store(settings).fetch_or_stale(
        FAMILY, "company_tickers", 24 * 3600, fetch, offline=settings.offline)
    return parse_company_tickers(payload), prov


def rank_search(table: pd.DataFrame, query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Rank: exact ticker, ticker prefix, name prefix, name word-prefix,
    ticker substring, name substring; ties by shorter ticker, then shorter
    (closer-matching) name."""
    q = query.strip().upper()
    if not q:
        return []
    t = table["ticker"].str.upper()
    n = table["name"].str.upper()
    rank = pd.Series(np.inf, index=table.index)
    word = n.str.contains(r"\b" + re.escape(q), regex=True)
    for r, mask in enumerate([
        t == q, t.str.startswith(q), n.str.startswith(q), word,
        t.str.contains(q, regex=False), n.str.contains(q, regex=False),
    ]):
        rank = rank.where(~(mask & (rank == np.inf)), r)
    hits = table.assign(_r=rank, _l=t.str.len(), _nl=n.str.len()).loc[rank < np.inf]
    hits = hits.sort_values(["_r", "_l", "_nl", "name"]).drop_duplicates("ticker").head(limit)
    return [{"ticker": r.ticker, "name": r.name, "cik": r.cik} for r in hits.itertuples(index=False)]


def search(query: str, limit: int, settings: Settings) -> Dataset:
    from .market import Dataset

    table, prov = ticker_table(settings)
    return Dataset(rank_search(table, query, limit), [prov])


def resolve(ticker_or_cik: str, settings: Settings) -> tuple[str, str, str | None]:
    """``(cik10, name, ticker)`` for a ticker (``BRK.B``/``BRK-B``) or CIK."""
    s = ticker_or_cik.strip().upper()
    table, _ = ticker_table(settings)
    if s.isdigit():
        c = cik10(s)
        m = table[table["cik"] == c]
        return c, (m["name"].iloc[0] if len(m) else ""), (m["ticker"].iloc[0] if len(m) else None)
    for cand in (s, s.replace(".", "-"), s.replace("-", ".")):
        m = table[table["ticker"] == cand]
        if len(m):
            return m["cik"].iloc[0], m["name"].iloc[0], cand
    raise DataUnavailable(f"sec: ticker {s} not found in SEC company_tickers.json")


# ------------------------------------------------------------ company facts
def _tag_facts(facts: dict[str, Any], tag: str, unit: str, taxonomy: str = "us-gaap") -> pd.DataFrame:
    rows = (((facts.get(taxonomy) or {}).get(tag) or {}).get("units") or {}).get(unit) or []
    if not rows:
        return pd.DataFrame(columns=["start", "end", "val", "form", "filed"])
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "start": pd.to_datetime(df["start"]) if "start" in df else pd.NaT,
        "end": pd.to_datetime(df["end"]),
        "val": pd.to_numeric(df["val"], errors="coerce").astype(float),
        "form": df.get("form", pd.Series([""] * len(df))).astype(str),
        "filed": pd.to_datetime(df.get("filed", pd.Series([None] * len(df)))),
    })
    return out.dropna(subset=["end", "val"])


def _latest(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """One fact per key: the most recently filed (restated) value."""
    return df.sort_values("filed", kind="stable").drop_duplicates(keys, keep="last")


def _annual_duration(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["form"].isin(ANNUAL_FORMS) & df["start"].notna()]
    days = (d["end"] - d["start"]).dt.days
    d = d[(days >= ANNUAL_DAYS[0]) & (days <= ANNUAL_DAYS[1])]
    return _latest(d, ["end"])


def _quarterly_duration(df: pd.DataFrame, additive: bool) -> pd.Series:
    """3-month values by period end: direct 80-100-day facts first, then
    (additive items only) differences of consecutive year-to-date facts."""
    d = df[df["form"].isin(QUARTERLY_FORMS) & df["start"].notna()].copy()
    if d.empty:
        return pd.Series(dtype=float)
    d = _latest(d, ["start", "end"])
    days = (d["end"] - d["start"]).dt.days
    direct = _latest(d[(days >= QUARTER_DAYS[0]) & (days <= QUARTER_DAYS[1])], ["end"])
    out = pd.Series(direct["val"].to_numpy(), index=pd.DatetimeIndex(direct["end"]), dtype=float)
    if not additive:
        return out.sort_index()
    derived: dict[pd.Timestamp, float] = {}
    for _, g in d.groupby("start"):
        g = g.sort_values("end")
        ends, vals = list(g["end"]), list(g["val"])
        for k in range(1, len(ends)):
            gap = (ends[k] - ends[k - 1]).days
            if QUARTER_DAYS[0] <= gap <= QUARTER_DAYS[1] and ends[k] not in out.index:
                derived.setdefault(ends[k], vals[k] - vals[k - 1])
    if derived:
        out = pd.concat([out, pd.Series(derived, dtype=float)])
    return out[~out.index.duplicated(keep="first")].sort_index()


def _instant_at(df: pd.DataFrame, forms: frozenset[str], dates: pd.DatetimeIndex, tol_days: int) -> pd.Series:
    """Instant values snapped to the nearest target date within ``tol_days``."""
    d = df[df["form"].isin(forms) & df["start"].isna()]
    if d.empty or len(dates) == 0:
        return pd.Series(dtype=float)
    d = _latest(d, ["end"]).sort_values("end")
    targets = pd.Series(dates.sort_values(), name="target")
    m = pd.merge_asof(d, targets.to_frame(), left_on="end", right_on="target",
                      direction="nearest", tolerance=pd.Timedelta(days=tol_days)).dropna(subset=["target"])
    m = m.assign(_gap=(m["end"] - m["target"]).abs()).sort_values(["_gap", "filed"])
    m = m.drop_duplicates("target", keep="first")
    return pd.Series(m["val"].to_numpy(), index=pd.DatetimeIndex(m["target"]), dtype=float).sort_index()


def standardize(facts_json: dict[str, Any]) -> dict[str, Any]:
    """companyfacts JSON -> {'annual', 'quarterly', 'first_filed_annual',
    'first_filed_quarterly', 'tags_used', 'notes'} (see module docstring)."""
    facts = facts_json.get("facts") or {}
    if not facts.get("us-gaap"):
        raise DataUnavailable(
            f"sec: {facts_json.get('entityName', 'company')} files no us-gaap facts "
            "(foreign private issuers report IFRS on 20-F/40-F)")
    raw: dict[str, list[tuple[str, pd.DataFrame]]] = {}
    for item, spec in LINE_ITEMS.items():
        raw[item] = [(tag, f) for tag in spec.tags if len(f := _tag_facts(facts, tag, spec.unit))]

    # Fiscal-year periods from every annual duration fact.
    fy_frames = [_annual_duration(f) for item, spec in LINE_ITEMS.items() if spec.kind == "duration"
                 for _, f in raw[item]]
    fy_all = pd.concat([f for f in fy_frames if len(f)]) if any(len(f) for f in fy_frames) else None
    if fy_all is None:
        raise DataUnavailable(f"sec: no annual (10-K) duration facts for {facts_json.get('entityName')}")
    fy_ends = pd.DatetimeIndex(sorted(fy_all["end"].unique()))
    # earliest 10-K that reported each fiscal year (before restatement de-duplication)
    first_parts = []
    for item in LINE_ITEMS:
        for _, f in raw[item]:
            d = f[f["form"].isin(ANNUAL_FORMS) & f["end"].isin(fy_ends)]
            if len(d):
                first_parts.append(d[["end", "filed"]])
    annual_first = pd.concat(first_parts).groupby("end")["filed"].min()

    annual: dict[str, pd.Series] = {}
    quarterly: dict[str, pd.Series] = {}
    tags_used: dict[str, list[str]] = {}
    for item, spec in LINE_ITEMS.items():
        if spec.kind != "duration":
            continue
        a_parts, q_parts, used = [], [], []
        for tag, f in raw[item]:
            a = _annual_duration(f)
            a_s = pd.Series(a["val"].to_numpy(), index=pd.DatetimeIndex(a["end"]), dtype=float)
            q_s = _quarterly_duration(f, spec.additive)
            if len(a_s) or len(q_s):
                used.append(tag)
            a_parts.append(a_s)
            q_parts.append(q_s)
        annual[item] = _merge_priority(a_parts)
        quarterly[item] = _merge_priority(q_parts)
        tags_used[item] = used

    q_ends = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in quarterly.values()]) | set(fy_ends)))
    quarter_first = pd.Series(dtype="datetime64[ns]")
    gross_parts: tuple = ((), ())
    st_parts: tuple = ([], [])
    for item, spec in LINE_ITEMS.items():
        if spec.kind != "instant":
            continue
        a_parts, q_parts, used = [], [], []
        for tag, f in raw[item]:
            a_s = _instant_at(f, ANNUAL_FORMS, fy_ends, 7)
            q_s = _instant_at(f, QUARTERLY_FORMS, q_ends, 3)
            if len(a_s) or len(q_s):
                used.append(tag)
            a_parts.append(a_s)
            q_parts.append(q_s)
        annual[item] = _merge_priority(a_parts)
        quarterly[item] = _merge_priority(q_parts)
        tags_used[item] = used
        if item == "long_term_debt":
            # us-gaap:LongTermDebt (the last resort) INCLUDES the current portion.
            ltd_tags = [t for t, _ in raw[item]]
            gross_parts = tuple(
                ([x for t, x in zip(ltd_tags, parts, strict=True) if t == "LongTermDebt"],
                 [x for t, x in zip(ltd_tags, parts, strict=True) if t != "LongTermDebt"])
                for parts in (a_parts, q_parts))
        if item == "short_term_debt":
            # Short-term borrowings that are not current maturities of LTD.
            st_tags = [t for t, _ in raw[item]]
            st_parts = tuple(
                [x for t, x in zip(st_tags, parts, strict=True) if t in ("ShortTermBorrowings", "CommercialPaper")]
                for parts in (a_parts, q_parts))

    # Periods whose long_term_debt came from the gross tag: adding DebtCurrent /
    # LongTermDebtCurrent would count current maturities twice, so total debt
    # there is LongTermDebt + short-term borrowings + commercial paper.
    gross_debt: dict[str, pd.Series] = {}
    for key, idx in (("annual", 0), ("quarterly", 1)):
        if not gross_parts[idx]:
            continue
        gross, others = gross_parts[idx]
        gross = [x for x in gross if len(x)]
        if not gross:
            continue
        g = gross[0].dropna()
        net = _merge_priority([x for x in others if len(x)])
        g = g[~g.index.isin(net.dropna().index)]
        if len(g):
            st = [x for x in st_parts[idx] if len(x)] if st_parts[idx] else []
            st_sum = pd.concat(st, axis=1, sort=True).sum(axis=1, min_count=1) if st else pd.Series(dtype=float)
            gross_debt[key] = g.add(st_sum.reindex(g.index).fillna(0.0))

    # earliest filing that reported each quarter end (any quarterly-form fact)
    qf = [f[f["form"].isin(QUARTERLY_FORMS)][["end", "filed"]] for item in raw for _, f in raw[item]]
    qf = [x for x in qf if len(x)]
    if qf:
        quarter_first = pd.concat(qf).groupby("end")["filed"].min()

    notes: list[str] = [
        "values are latest restated figures (most recently filed), not as first reported; "
        "use first_filed_* to avoid look-ahead",
        "quarterly flow values for cash-flow items and Q4 are derived by differencing year-to-date facts",
    ]
    out = {}
    for name, cols, idx in (("annual", annual, fy_ends), ("quarterly", quarterly, q_ends)):
        df = pd.DataFrame({k: v for k, v in cols.items()}).reindex(idx)
        df = _derive(df, notes if name == "annual" else [])
        if name in gross_debt:
            g = gross_debt[name].reindex(df.index).dropna()
            df.loc[g.index, "total_debt"] = g
        df = df[[c for c in ITEM_ORDER if c in df.columns]].dropna(how="all")
        df.index.name = "period_end"
        out[name] = df.astype(float)
    if tags_used.get("long_term_debt") and tags_used["long_term_debt"][0] == "LongTermDebt":
        notes.append("long_term_debt from us-gaap:LongTermDebt includes the current portion; "
                     "total_debt for those periods adds only short-term borrowings and commercial paper")
    out["first_filed_annual"] = annual_first.reindex(out["annual"].index)
    out["first_filed_quarterly"] = quarter_first.reindex(out["quarterly"].index) if len(quarter_first) else \
        pd.Series(pd.NaT, index=out["quarterly"].index)
    out["tags_used"] = tags_used
    out["notes"] = notes
    return out


def _merge_priority(parts: list[pd.Series]) -> pd.Series:
    """Period-by-period merge: earlier (higher-priority) series win."""
    res = pd.Series(dtype=float)
    for s in parts:
        if len(s):
            s = s[~s.index.duplicated(keep="last")]
            res = s if res.empty else res.combine_first(s)
    return res.sort_index()


def _derive(df: pd.DataFrame, notes: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in ITEM_ORDER:
        if c not in df.columns:
            df[c] = np.nan
    gp = df["revenue"] - df["cost_of_revenue"]
    df["gross_profit"] = df["gross_profit"].fillna(gp)
    df["ebitda"] = df["operating_income"] + df["d_and_a"]
    df["total_debt"] = df[["long_term_debt", "short_term_debt"]].sum(axis=1, min_count=1)
    df["fcf"] = df["cfo"] - df["capex"]
    if notes is not None and len(df):
        notes.append("total_debt sums the reported debt components; a component the company does not "
                     "report (e.g. no short-term borrowings) contributes nothing")
    return df


def _shares_outstanding(facts: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    """Latest dei:EntityCommonStockSharesOutstanding (summed across share
    classes reported at the same date in the same filing), falling back to
    us-gaap:CommonStockSharesOutstanding, then to the latest weighted-average
    diluted share count (a period average, not a point count -- the returned tag
    says which was used so callers can state it)."""
    for taxonomy, tag in (("dei", "EntityCommonStockSharesOutstanding"),
                          ("us-gaap", "CommonStockSharesOutstanding"),
                          ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding")):
        rows = (((facts.get(taxonomy) or {}).get(tag) or {}).get("units") or {}).get("shares") or []
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df = df[pd.to_numeric(df["val"], errors="coerce") > 0]
        if df.empty:
            continue
        df["end"] = pd.to_datetime(df["end"])
        df["filed"] = pd.to_datetime(df.get("filed"))
        last = df.sort_values(["filed", "end"]).iloc[-1]
        same = df[(df["end"] == last["end"]) & (df.get("accn", pd.Series(index=df.index)) == last.get("accn"))]
        val = float(same["val"].astype(float).sum()) if len(same) > 1 else float(last["val"])
        return val, str(last["end"].date()), f"{taxonomy}:{tag}" + (f" (summed {len(same)} classes)"
                                                                    if len(same) > 1 else "")
    return None, None, None



def _encode(result: dict[str, Any]) -> dict[str, Any]:
    enc = dict(result)
    for k in ("annual", "quarterly"):
        enc[k] = result[k].to_json(orient="split", date_format="iso")
    for k in ("first_filed_annual", "first_filed_quarterly"):
        s = pd.to_datetime(result[k])
        enc[k] = {"index": [d.isoformat() for d in s.index],
                  "values": [None if pd.isna(v) else pd.Timestamp(v).isoformat() for v in s]}
    return enc


def _decode(enc: dict[str, Any]) -> dict[str, Any]:
    out = dict(enc)
    for k in ("annual", "quarterly"):
        df = pd.read_json(StringIO(enc[k]), orient="split", convert_dates=False)
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index)).tz_localize(None).normalize()
        df.index.name = "period_end"
        out[k] = df.astype(float)
    for k in ("first_filed_annual", "first_filed_quarterly"):
        v = enc[k]
        out[k] = pd.Series(pd.to_datetime(v["values"]), index=pd.DatetimeIndex(pd.to_datetime(v["index"])),
                           name="first_filed")
    return out


def fetch_company_facts(ticker: str, settings: Settings) -> Dataset:
    """Standardized financials for ``ticker`` (or a CIK); see module docstring.

    ``.data`` keys: cik, name, ticker, annual, quarterly, shares_outstanding,
    shares_outstanding_as_of, first_filed_annual, first_filed_quarterly,
    tags_used, notes.
    """
    from .market import Dataset

    cik, name, tick = resolve(ticker, settings)
    url = FACTS_URL.format(cik=cik)

    def fetch() -> tuple[dict[str, Any], Provenance]:
        payload = http.get_json(url, settings, source="sec-edgar")
        res = standardize(payload)
        so, so_date, so_tag = _shares_outstanding(payload.get("facts") or {})
        res.update(cik=cik, name=payload.get("entityName") or name, ticker=tick,
                   shares_outstanding=so, shares_outstanding_as_of=so_date, shares_outstanding_tag=so_tag)
        if so_tag and "WeightedAverage" in so_tag:
            res["notes"] = [*res.get("notes", []),
                            "shares_outstanding is the latest weighted-average DILUTED count (no cover-page "
                            "shares-outstanding fact was filed); market cap uses it as an approximation"]
        return _encode(res), Provenance.now("sec-edgar", url=url, cik=cik, dataset="companyfacts")

    enc, prov = get_store(settings).fetch_or_stale(
        "sec_facts", cik, settings.ttl_filings_s, fetch, offline=settings.offline)
    return Dataset(_decode(enc), [prov])


# -------------------------------------------------------------------- 13F
def list_13f_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    """13F-HR / 13F-HR/A filings from a submissions JSON, newest first."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    out = []
    for i, form in enumerate(forms):
        if form in ("13F-HR", "13F-HR/A"):
            out.append({
                "accession": recent["accessionNumber"][i], "form": form,
                "filed": recent["filingDate"][i],
                "period": (recent.get("reportDate") or [None] * len(forms))[i],
            })
    out.sort(key=lambda r: (r["filed"], r["accession"]), reverse=True)
    return out


def pick_latest_13f(filings: list[dict[str, Any]]) -> dict[str, Any]:
    """Original 13F-HR for the latest *report period* (amendments may only
    *add* holdings); an amendment only when no original exists.

    Ranked by (period, filed): a delinquent filer catching up files originals
    for old quarters *after* newer ones, so the newest filing date alone can
    point at a stale quarter."""
    if not filings:
        raise DataUnavailable("sec: filer has no 13F-HR filings")
    originals = [f for f in filings if f["form"] == "13F-HR"]
    pool = originals or filings
    return max(pool, key=lambda f: (f.get("period") or "", f["filed"], f["accession"]))


def pick_info_table(index_json: dict[str, Any]) -> str:
    """Name of the information-table XML in a filing's ``index.json``."""
    items = ((index_json or {}).get("directory") or {}).get("item") or []
    xmls = [it for it in items if str(it.get("name", "")).lower().endswith(".xml")
            and str(it.get("name", "")).lower() != "primary_doc.xml"]
    if not xmls:
        raise DataUnavailable("sec: 13F filing has no information-table XML")
    named = [it for it in xmls if re.search(r"info|table", str(it["name"]), re.I)]
    if named:
        return str(named[0]["name"])

    def size(it: dict[str, Any]) -> int:
        try:
            return int(it.get("size") or 0)
        except ValueError:
            return 0

    return str(max(xmls, key=size)["name"])


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_info_table(xml: bytes | str) -> pd.DataFrame:
    """13F information table XML (any namespace prefix) -> raw rows: issuer,
    title, cusip, value (as reported), shares, shares_type, put_call."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise DataUnavailable(f"sec: malformed 13F XML ({e})") from e
    rows = []
    for el in root.iter():
        if _local(el.tag) != "infoTable":
            continue
        rec: dict[str, Any] = {}
        for ch in el.iter():
            name = _local(ch.tag)
            text = (ch.text or "").strip()
            if name in ("nameOfIssuer", "titleOfClass", "cusip", "value", "sshPrnamt",
                        "sshPrnamtType", "putCall"):
                rec[name] = text
        rows.append({
            "issuer": rec.get("nameOfIssuer", ""), "title": rec.get("titleOfClass", ""),
            "cusip": rec.get("cusip", "").upper(),
            "value": float(rec.get("value") or "nan"), "shares": float(rec.get("sshPrnamt") or "nan"),
            "shares_type": rec.get("sshPrnamtType") or None,
            "put_call": (rec.get("putCall") or "").capitalize() or None,
        })
    if not rows:
        raise DataUnavailable("sec: 13F information table has no infoTable rows")
    return pd.DataFrame(rows)


def value_multiplier(filed: date) -> float:
    """USD per reported unit of 13F ``value``: 1000 before 2023-01-03, else 1."""
    return 1000.0 if filed < VALUE_IN_DOLLARS_FROM else 1.0


def value_unit_warning(holdings: pd.DataFrame, filed: date) -> str | None:
    """Sanity check of the value-unit rule on the filing itself.

    Median implied price ``value_usd / shares`` over share (``SH``), non-option
    rows: below $1 after the 2023 switch suggests the filer still reported
    thousands; above $100,000 before it suggests whole dollars. Nothing is
    rescaled (that would be a guess); the payload says so instead.
    """
    h = holdings
    m = (h["shares_type"].fillna("SH").str.upper() == "SH") & h["put_call"].isna() & (h["shares"] > 0)
    px = (h.loc[m, "value_usd"] / h.loc[m, "shares"]).replace([np.inf, -np.inf], np.nan).dropna()
    if len(px) < 3:
        return None
    med = float(px.median())
    if filed >= VALUE_IN_DOLLARS_FROM and med < 1.0:
        return (f"median implied price value/shares is ${med:.4f}: this filer appears to report value in "
                "thousands despite the 2023 whole-dollar rule; value_usd is shown as filed (not rescaled)")
    if filed < VALUE_IN_DOLLARS_FROM and med > 100_000.0:
        return (f"median implied price value/shares is ${med:,.0f}: this filer appears to report whole dollars "
                "although pre-2023 filings use thousands; value_usd (x1000) may be overstated")
    return None


def aggregate_holdings(raw: pd.DataFrame, filed: date) -> pd.DataFrame:
    """Aggregate raw rows by (CUSIP, put/call) (managers split positions
    across sub-managers / discretion types), convert value to USD, and
    compute weights ``w_i = V_i / sum_{j non-option} V_j`` for non-option rows."""
    df = raw.copy()
    df["value_usd"] = df["value"] * value_multiplier(filed)
    df["pc"] = df["put_call"].fillna("")
    agg = df.groupby(["cusip", "pc"], sort=False).agg(
        issuer=("issuer", "first"), title=("title", "first"), value_usd=("value_usd", "sum"),
        shares=("shares", "sum"), shares_type=("shares_type", "first"),
    ).reset_index()
    agg["put_call"] = agg["pc"].replace("", None)
    agg = agg.drop(columns="pc")
    is_opt = agg["put_call"].notna()
    total = agg.loc[~is_opt, "value_usd"].sum()
    agg["weight"] = np.where(~is_opt & (total > 0), agg["value_usd"] / total if total > 0 else np.nan, np.nan)
    agg = agg.sort_values("value_usd", ascending=False).reset_index(drop=True)
    return agg[["issuer", "cusip", "title", "value_usd", "shares", "shares_type", "put_call", "weight"]]


def fetch_13f(cik: str, settings: Settings, map_tickers: bool = True, figi_budget_s: float = 20.0) -> Dataset:
    """Latest 13F-HR holdings for a filer (see module docstring).

    ``.data``: filer, cik, period, filed, form, accession, url, holdings
    (DataFrame issuer, cusip, title, value_usd, shares, put_call, ticker,
    weight), amendments (later 13F-HR/A for the same period), notes.
    """
    from .market import Dataset
    from .openfigi import map_cusips

    c = cik10(cik)
    store = get_store(settings)
    sub_url = SUBMISSIONS_URL.format(cik=c)

    def fetch_sub() -> tuple[dict[str, Any], Provenance]:
        payload = http.get_json(sub_url, settings, source="sec-edgar")
        return ({"name": payload.get("name", ""), "filings": list_13f_filings(payload)},
                Provenance.now("sec-edgar", url=sub_url, cik=c, dataset="submissions"))

    sub, sub_prov = store.fetch_or_stale("sec_13f_index", c, settings.ttl_filings_s, fetch_sub,
                                         offline=settings.offline)
    latest = pick_latest_13f(sub["filings"])
    acc = latest["accession"]
    acc_nodash = acc.replace("-", "")
    base = ARCHIVE_URL.format(cik=int(c), acc=acc_nodash)
    filed = date.fromisoformat(latest["filed"])

    def fetch_table() -> tuple[pd.DataFrame, Provenance]:
        idx = http.get_json(base + "index.json", settings, source="sec-edgar")
        name = pick_info_table(idx)
        xml = http.get_bytes(base + name, settings, source="sec-edgar")
        h = aggregate_holdings(parse_info_table(xml), filed)
        return h, Provenance.now("sec-edgar", url=base + name, accession=acc, form=latest["form"],
                                 value_units="thousands USD (pre-2023-01-03), scaled to USD"
                                 if filed < VALUE_IN_DOLLARS_FROM else "USD")

    # A filed accession never changes: cache its table permanently.
    holdings, tab_prov = store.fetch_or_stale("sec_13f", acc_nodash, None, fetch_table, offline=settings.offline)
    holdings = holdings.copy()
    mapping = map_cusips(list(holdings["cusip"]), settings, budget_s=figi_budget_s) if map_tickers else {}
    holdings["ticker"] = [mapping.get(cu) for cu in holdings["cusip"]]
    holdings = holdings[["issuer", "cusip", "title", "ticker", "value_usd", "shares", "shares_type",
                         "put_call", "weight"]]
    amendments = [f for f in sub["filings"] if f["form"] == "13F-HR/A" and f["period"] == latest["period"]
                  and f["filed"] >= latest["filed"]]
    notes = [
        "13F covers long US-listed 13(f) securities only: no shorts, cash, non-US listings or most derivatives",
        "reported ~45 days after quarter end; positions may have changed since",
        "option rows report the underlying's market value; weights use non-option rows only",
    ]
    unmapped = int(holdings["ticker"].isna().sum())
    if unmapped:
        notes.append(f"{unmapped} CUSIPs not (yet) mapped to tickers via OpenFIGI")
    if amendments:
        notes.append(f"{len(amendments)} 13F-HR/A amendment(s) filed for this period are not merged")
    unit_warn = value_unit_warning(holdings, filed)
    if unit_warn:
        notes.append(unit_warn)
    provs = [sub_prov, tab_prov]
    if map_tickers:
        provs.append(Provenance.now("openfigi", url="https://api.openfigi.com/v3/mapping",
                                    mapped=len(holdings) - unmapped))
    return Dataset({
        "filer": sub["name"], "cik": c, "period": latest["period"], "filed": latest["filed"],
        "form": latest["form"], "accession": acc, "url": base, "holdings": holdings,
        "amendments": amendments, "notes": notes,
    }, provs)
