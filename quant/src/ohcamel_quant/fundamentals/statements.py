"""Presentable financial statements from standardized SEC XBRL line items.

Input contract (``MarketData.company_facts(...).data``): ``annual`` and
``quarterly`` DataFrames indexed by fiscal period end, one column per
standardized line item (USD; ``eps_diluted`` in USD/share; ``shares_diluted``
in shares). Any item may be missing or NaN.

Conventions
-----------
* **Flows** (income statement, cash-flow statement) cover a period;
  **stocks** (balance sheet) are measured at the period end.
* **TTM** (trailing twelve months): flows are the sum of the last four
  *consecutive* fiscal quarters; stocks are the latest quarter-end balance;
  diluted EPS is the sum of the four quarterly EPS figures (the convention
  used by data vendors); diluted shares are the four-quarter average.
  A TTM value is ``None`` when any of the four quarters is missing -- nothing
  is ever imputed.
* **YoY growth** compares a period with the period ending one year earlier
  (matched by date, +/- 20 days): ``g = x_t / x_{t-1y} - 1``. Growth is
  ``None`` when the base is missing, zero or negative (a percentage change
  from a non-positive base has no economic meaning).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: Flow items that add up across quarters.
FLOW_ITEMS: tuple[str, ...] = (
    "revenue", "cost_of_revenue", "gross_profit", "sga", "rnd", "operating_income", "d_and_a", "ebitda",
    "interest_expense", "pretax_income", "income_tax", "net_income",
    "cfo", "capex", "fcf", "dividends_paid", "buybacks",
)
#: Balance-sheet (instant) items.
STOCK_ITEMS: tuple[str, ...] = (
    "total_assets", "current_assets", "cash", "receivables", "inventory", "ppe_net", "total_liabilities",
    "current_liabilities", "long_term_debt", "short_term_debt", "total_debt", "equity", "retained_earnings",
)
#: Per-share / share-count items (not additive).
SPECIAL_ITEMS: tuple[str, ...] = ("eps_diluted", "shares_diluted")
ALL_ITEMS: tuple[str, ...] = FLOW_ITEMS + STOCK_ITEMS + SPECIAL_ITEMS

LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "cost_of_revenue": "Cost of revenue",
    "gross_profit": "Gross profit",
    "sga": "Selling, general & administrative",
    "rnd": "Research & development",
    "operating_income": "Operating income (EBIT)",
    "d_and_a": "Depreciation & amortization",
    "ebitda": "EBITDA",
    "interest_expense": "Interest expense",
    "pretax_income": "Pre-tax income",
    "income_tax": "Income tax",
    "net_income": "Net income",
    "eps_diluted": "Diluted EPS",
    "shares_diluted": "Diluted shares (weighted avg.)",
    "total_assets": "Total assets",
    "current_assets": "Current assets",
    "cash": "Cash & equivalents",
    "receivables": "Receivables",
    "inventory": "Inventory",
    "ppe_net": "PP&E (net)",
    "total_liabilities": "Total liabilities",
    "current_liabilities": "Current liabilities",
    "long_term_debt": "Long-term debt",
    "short_term_debt": "Short-term debt",
    "total_debt": "Total debt",
    "equity": "Shareholders' equity",
    "retained_earnings": "Retained earnings",
    "cfo": "Cash from operations",
    "capex": "Capital expenditures",
    "fcf": "Free cash flow (CFO - capex)",
    "dividends_paid": "Dividends paid",
    "buybacks": "Share repurchases",
}

SECTIONS: dict[str, tuple[str, ...]] = {
    "income_statement": (
        "revenue", "cost_of_revenue", "gross_profit", "sga", "rnd", "operating_income", "d_and_a", "ebitda",
        "interest_expense", "pretax_income", "income_tax", "net_income", "eps_diluted", "shares_diluted",
    ),
    "balance_sheet": (
        "cash", "receivables", "inventory", "current_assets", "ppe_net", "total_assets",
        "current_liabilities", "short_term_debt", "long_term_debt", "total_debt", "total_liabilities",
        "retained_earnings", "equity",
    ),
    "cash_flow": ("cfo", "capex", "fcf", "d_and_a", "dividends_paid", "buybacks"),
}

#: Tolerance (days) when matching a period with the one a year earlier.
YEAR_TOLERANCE_DAYS = 20
QUARTER_DAYS = (80, 100)


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` sorted by date with every standard item present (missing -> NaN)."""
    out = df.copy()
    for c in ALL_ITEMS:
        if c not in out.columns:
            out[c] = np.nan
    out = out.sort_index()
    out.index = pd.DatetimeIndex(out.index)
    return out.astype(float)


def prior_year_label(index: pd.DatetimeIndex, when: pd.Timestamp,
                     tol_days: int = YEAR_TOLERANCE_DAYS) -> pd.Timestamp | None:
    """The index label closest to ``when - 1 year`` within ``tol_days`` (else None)."""
    return prior_label(index, when, 365, tol_days)


def prior_label(index: pd.DatetimeIndex, when: pd.Timestamp, days_back: float,
                tol_days: int = YEAR_TOLERANCE_DAYS) -> pd.Timestamp | None:
    """The index label closest to ``when - days_back`` within ``tol_days`` (else None)."""
    if len(index) == 0:
        return None
    target = when - pd.Timedelta(days=days_back)
    diffs = np.abs((index - target).days)
    k = int(np.argmin(diffs))
    return index[k] if diffs[k] <= tol_days else None


def yoy_growth(df: pd.DataFrame) -> pd.DataFrame:
    """Year-over-year growth for every column: ``x_t / x_{t-1y} - 1``.

    Periods are matched by date (one year back, +/- 20 days), so this works for
    annual rows and for quarterly rows (same fiscal quarter a year earlier).
    ``NaN`` where the base is missing or non-positive.
    """
    out = pd.DataFrame(np.nan, index=df.index, columns=df.columns)
    for t in df.index:
        p = prior_year_label(df.index, t)
        if p is None:
            continue
        cur, base = df.loc[t], df.loc[p]
        with np.errstate(divide="ignore", invalid="ignore"):
            g = cur / base - 1.0
        out.loc[t] = g.where(base > 0)
    return out


def _consecutive_quarters(idx: pd.DatetimeIndex) -> bool:
    gaps = np.diff(idx.to_numpy()).astype("timedelta64[D]").astype(int)
    return bool(len(gaps) and np.all((gaps >= QUARTER_DAYS[0]) & (gaps <= QUARTER_DAYS[1])))


def ttm_at(quarterly: pd.DataFrame, end: pd.Timestamp) -> pd.Series | None:
    """TTM row ending at quarter ``end`` (see module conventions), or None if
    four consecutive quarters ending there are not available."""
    q = ensure_columns(quarterly)
    if end not in q.index:
        return None
    pos = q.index.get_loc(end)
    if not isinstance(pos, int) or pos < 3:
        return None
    win = q.iloc[pos - 3: pos + 1]
    if not _consecutive_quarters(win.index):
        return None
    row = pd.Series(np.nan, index=list(ALL_ITEMS), dtype=float)
    flows = list(FLOW_ITEMS) + ["eps_diluted"]
    sums = win[flows].sum(axis=0, min_count=4)          # NaN unless all four quarters present
    row[flows] = sums
    # SEC reports no Q4 per-share facts (Q4 is derived as FY - 9M for flows
    # only), so a four-quarter window almost always lacks one EPS/share value.
    # Diluted shares: the mean of the quarters reported (>= 3 of 4). EPS: the
    # sum of four quarterly EPS when all exist, else TTM net income divided by
    # those average diluted shares -- the same definition, computed.
    sh = win["shares_diluted"].dropna()
    row["shares_diluted"] = sh.mean() if len(sh) >= 3 else np.nan
    if np.isnan(row["eps_diluted"]) and np.isfinite(row["net_income"]) and row["shares_diluted"] > 0:
        row["eps_diluted"] = row["net_income"] / row["shares_diluted"]
    row[list(STOCK_ITEMS)] = win.iloc[-1][list(STOCK_ITEMS)]
    return row


def rolling_ttm(quarterly: pd.DataFrame) -> pd.DataFrame:
    """A TTM row for every quarter end that closes four consecutive quarters."""
    q = ensure_columns(quarterly)
    rows = {t: r for t in q.index if (r := ttm_at(q, t)) is not None}
    if not rows:
        return pd.DataFrame(columns=list(ALL_ITEMS), dtype=float)
    out = pd.DataFrame(rows).T.astype(float)
    out.index = pd.DatetimeIndex(out.index, name="period_end")
    return out


def latest_ttm(quarterly: pd.DataFrame) -> tuple[pd.Timestamp, pd.Series] | None:
    """(period end, row) of the most recent TTM window, or None."""
    r = rolling_ttm(quarterly)
    if r.empty:
        return None
    return r.index[-1], r.iloc[-1]


def _table(df: pd.DataFrame, items: tuple[str, ...], growth: pd.DataFrame | None) -> dict[str, Any]:
    periods = [t.date().isoformat() for t in df.index]
    rows = []
    for it in items:
        vals = [None if not np.isfinite(v) else float(v) for v in df[it].to_numpy()]
        yoy = None
        if growth is not None:
            yoy = [None if not np.isfinite(v) else float(v) for v in growth[it].to_numpy()]
        rows.append({"item": it, "label": LABELS[it], "values": vals, "yoy": yoy,
                     "available": any(v is not None for v in vals)})
    return {"periods": periods, "rows": rows}


def build_statements(annual: pd.DataFrame, quarterly: pd.DataFrame, period: str = "annual",
                     max_periods: int = 10) -> dict[str, Any]:
    """Income statement, balance sheet and cash-flow statement tables.

    ``period``: ``annual`` (fiscal years), ``quarterly`` (fiscal quarters) or
    ``ttm`` (rolling trailing-twelve-month windows, one per quarter end). The
    latest ``max_periods`` columns are returned, oldest first, each row with
    its YoY growth. Missing items are ``None`` -- never imputed.
    """
    if period == "annual":
        df = ensure_columns(annual)
    elif period == "quarterly":
        df = ensure_columns(quarterly)
    elif period == "ttm":
        df = rolling_ttm(quarterly)
    else:
        raise ValueError(f"period must be 'annual', 'quarterly' or 'ttm', not {period!r}")
    df = df.dropna(how="all")
    growth = yoy_growth(df)
    df, growth = df.iloc[-max_periods:], growth.iloc[-max_periods:]
    return {
        "period": period,
        **{name: _table(df, items, growth) for name, items in SECTIONS.items()},
    }
