"""Financial ratios and market multiples.

Every ratio is computed from one period's row (flows for the period, stocks at
its end) and, where a *balance* enters a return or turnover ratio, the
**average** of the opening and closing balance (opening = the row one year
earlier, matched by date) -- Penman, S. H. (2013), *Financial Statement
Analysis and Security Valuation*, 5th ed., ch. 9-12. If the opening balance is
unavailable the average-based ratio is ``None`` (never approximated with the
closing balance).

Definitions (``avg(X) = (X_open + X_close) / 2``)
-------------------------------------------------
Margins
    gross = gross_profit / revenue; operating = EBIT / revenue;
    EBITDA = ebitda / revenue; net = net_income / revenue; FCF = fcf / revenue.
Returns
    ROE = NI / avg(equity); ROA = NI / avg(total_assets);
    ROIC = NOPAT / avg(IC), IC = total_debt + equity - cash,
    NOPAT = EBIT x (1 - t), t = income_tax / pretax_income clipped to [0, 0.5]
    (Koller, Goedhart & Wessels (2020), *Valuation*, 7th ed., ch. 11).
DuPont (3-step), Soldofsky (1968) / Penman (2013)
    ROE = (NI / revenue) x (revenue / avg(assets)) x (avg(assets) / avg(equity)).
Leverage
    D/E = total_debt / equity; debt/assets; liabilities/assets;
    net debt / EBITDA = (total_debt - cash) / EBITDA;
    interest coverage = EBIT / interest_expense.
Liquidity
    current = current_assets / current_liabilities;
    quick (acid test) = (current_assets - inventory) / current_liabilities;
    cash ratio = cash / current_liabilities.
Efficiency
    asset turnover = revenue / avg(assets);
    DSO = avg(receivables) / revenue x 365; DIO = avg(inventory) / cost_of_revenue x 365.
Growth
    CAGR_n = (x_t / x_{t-n})^(1/n) - 1 (``None`` unless both ends are positive).
Market multiples (live price P, shares outstanding S from the latest SEC cover page)
    market cap = P x S; EV = market cap + total_debt - cash;
    P/E = cap / NI; EV/EBITDA; EV/Sales; EV/EBIT; P/B = cap / equity; P/S;
    FCF yield = FCF / cap; earnings yield = NI / cap;
    dividend yield = dividends_paid / cap; buyback yield = buybacks / cap;
    shareholder yield = dividend + buyback yield (Faber (2013), *Shareholder Yield*).
    Multiples with a non-positive denominator (e.g. P/E with a loss) are ``None``;
    yields keep their sign.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .statements import ensure_columns, prior_label, prior_year_label

TAX_CLIP = (0.0, 0.5)
DAYS_PER_YEAR = 365.0


def _f(x: Any) -> float | None:
    """Finite float or None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def safe_div(num: Any, den: Any, positive_den: bool = True) -> float | None:
    """``num / den`` or None if either is missing, ``den == 0`` or (when
    ``positive_den``) ``den <= 0``."""
    n, d = _f(num), _f(den)
    if n is None or d is None or d == 0 or (positive_den and d < 0):
        return None
    return n / d


def avg(a: Any, b: Any) -> float | None:
    x, y = _f(a), _f(b)
    return None if x is None or y is None else (x + y) / 2.0


def effective_tax_rate(income_tax: Any, pretax_income: Any, clip: tuple[float, float] = TAX_CLIP) -> float | None:
    """``income_tax / pretax_income`` clipped to ``clip``; None if pre-tax income <= 0."""
    r = safe_div(income_tax, pretax_income)
    return None if r is None else float(np.clip(r, *clip))


def row_ratios(cur: pd.Series, prev: pd.Series | None) -> dict[str, float | None]:
    """All accounting ratios for one period (``cur``), with ``prev`` the row one
    year earlier (opening balances) or None."""
    g = cur.get
    p = prev.get if prev is not None else (lambda _k, _d=None: None)
    rev = g("revenue")
    out: dict[str, float | None] = {}
    # margins
    out["gross_margin"] = safe_div(g("gross_profit"), rev)
    out["operating_margin"] = safe_div(g("operating_income"), rev)
    out["ebitda_margin"] = safe_div(g("ebitda"), rev)
    out["net_margin"] = safe_div(g("net_income"), rev)
    out["fcf_margin"] = safe_div(g("fcf"), rev)
    out["rnd_intensity"] = safe_div(g("rnd"), rev)
    out["capex_intensity"] = safe_div(g("capex"), rev)
    # taxes / NOPAT
    t = effective_tax_rate(g("income_tax"), g("pretax_income"))
    out["effective_tax_rate"] = t
    ebit = _f(g("operating_income"))
    nopat = None if ebit is None or t is None else ebit * (1.0 - t)
    out["nopat"] = nopat
    # averages
    a_eq = avg(g("equity"), p("equity"))
    a_ta = avg(g("total_assets"), p("total_assets"))

    def ic(get: Any) -> float | None:
        d, e, c = _f(get("total_debt")), _f(get("equity")), _f(get("cash"))
        return None if d is None or e is None or c is None else d + e - c

    a_ic = avg(ic(g), ic(p)) if prev is not None else None
    out["roe"] = safe_div(g("net_income"), a_eq)
    out["roa"] = safe_div(g("net_income"), a_ta)
    out["roic"] = safe_div(nopat, a_ic)
    # DuPont 3-step
    out["dupont_net_margin"] = out["net_margin"]
    out["dupont_asset_turnover"] = safe_div(rev, a_ta)
    out["dupont_equity_multiplier"] = safe_div(a_ta, a_eq)
    # leverage
    out["debt_to_equity"] = safe_div(g("total_debt"), g("equity"))
    out["debt_to_assets"] = safe_div(g("total_debt"), g("total_assets"))
    out["liabilities_to_assets"] = safe_div(g("total_liabilities"), g("total_assets"))
    nd = None if _f(g("total_debt")) is None or _f(g("cash")) is None else _f(g("total_debt")) - _f(g("cash"))
    out["net_debt"] = nd
    out["net_debt_to_ebitda"] = safe_div(nd, g("ebitda"))
    out["interest_coverage"] = safe_div(g("operating_income"), g("interest_expense"))
    # liquidity
    ca, cl, inv = _f(g("current_assets")), g("current_liabilities"), _f(g("inventory"))
    out["current_ratio"] = safe_div(ca, cl)
    out["quick_ratio"] = safe_div(None if ca is None or inv is None else ca - inv, cl)
    out["cash_ratio"] = safe_div(g("cash"), cl)
    # efficiency
    out["asset_turnover"] = out["dupont_asset_turnover"]
    ar = safe_div(avg(g("receivables"), p("receivables")), rev)
    out["dso_days"] = None if ar is None else ar * DAYS_PER_YEAR
    ii = safe_div(avg(g("inventory"), p("inventory")), g("cost_of_revenue"))
    out["dio_days"] = None if ii is None else ii * DAYS_PER_YEAR
    # per share (weighted-average diluted shares)
    sh = g("shares_diluted")
    out["eps_diluted"] = _f(g("eps_diluted"))
    out["revenue_per_share"] = safe_div(rev, sh)
    out["fcf_per_share"] = safe_div(g("fcf"), sh)
    out["book_value_per_share"] = safe_div(g("equity"), sh)
    out["dividends_per_share"] = safe_div(g("dividends_paid"), sh)
    out["payout_ratio"] = safe_div(g("dividends_paid"), g("net_income"))
    tot_ret = None
    if _f(g("dividends_paid")) is not None and _f(g("buybacks")) is not None:
        tot_ret = _f(g("dividends_paid")) + _f(g("buybacks"))
    out["total_payout_ratio"] = safe_div(tot_ret, g("net_income"))
    # growth vs prior year
    for k, item in (("revenue_growth", "revenue"), ("eps_growth", "eps_diluted"),
                    ("fcf_growth", "fcf"), ("net_income_growth", "net_income")):
        base = _f(p(item))
        cur_v = _f(g(item))
        out[k] = None if base is None or cur_v is None or base <= 0 else cur_v / base - 1.0
    return out


RATIO_KEYS: tuple[str, ...] = tuple(row_ratios(pd.Series(dtype=float), None).keys())


def ratio_history(df: pd.DataFrame) -> pd.DataFrame:
    """One row of :func:`row_ratios` per period of ``df`` (annual rows or a
    rolling-TTM frame); the opening balance is the row one year earlier."""
    d = ensure_columns(df)
    rows = []
    for t in d.index:
        pl = prior_year_label(d.index, t)
        rows.append(row_ratios(d.loc[t], d.loc[pl] if pl is not None else None))
    out = pd.DataFrame(rows, index=d.index, columns=list(RATIO_KEYS)).astype(float)
    out.index.name = "period_end"
    return out


def cagr(start: Any, end: Any, years: float) -> float | None:
    """Compound annual growth rate ``(end/start)^(1/years) - 1``; None unless
    both values are positive and ``years > 0``."""
    s, e = _f(start), _f(end)
    if s is None or e is None or s <= 0 or e <= 0 or years <= 0:
        return None
    return (e / s) ** (1.0 / years) - 1.0


def growth_table(annual: pd.DataFrame, horizons: tuple[int, ...] = (1, 3, 5),
                 items: tuple[str, ...] = ("revenue", "eps_diluted", "fcf", "net_income")) -> dict[str, dict[str, Any]]:
    """CAGRs over ``horizons`` fiscal years ending at the latest fiscal year,
    matched by date (n x 365 days back, +/- 20 days x n)."""
    d = ensure_columns(annual)
    out: dict[str, dict[str, Any]] = {}
    if d.empty:
        return {it: {f"{n}y": None for n in horizons} for it in items}
    t = d.index[-1]
    for it in items:
        row: dict[str, Any] = {}
        for n in horizons:
            pl = prior_label(d.index, t, 365.25 * n, tol_days=20 + 2 * n)
            row[f"{n}y"] = None if pl is None else cagr(d.loc[pl, it], d.loc[t, it], n)
        out[it] = row
    return out


def market_multiples(price: float, shares_outstanding: float, fundamentals: pd.Series) -> dict[str, float | None]:
    """Valuation multiples from a live price and a fundamentals row (normally
    TTM flows and latest balances). See module docstring for the formulas."""
    p, s = _f(price), _f(shares_outstanding)
    if p is None or s is None or p <= 0 or s <= 0:
        raise ValueError("market multiples need a positive price and share count")
    g = fundamentals.get
    cap = p * s
    debt, cash = _f(g("total_debt")), _f(g("cash"))
    ev = None if debt is None or cash is None else cap + debt - cash
    out: dict[str, float | None] = {
        "price": p,
        "shares_outstanding": s,
        "market_cap": cap,
        "enterprise_value": ev,
        "pe": safe_div(cap, g("net_income")),
        "pe_eps": safe_div(p, g("eps_diluted")),
        "ev_ebitda": safe_div(ev, g("ebitda")),
        "ev_ebit": safe_div(ev, g("operating_income")),
        "ev_sales": safe_div(ev, g("revenue")),
        "price_to_sales": safe_div(cap, g("revenue")),
        "price_to_book": safe_div(cap, g("equity")),
        "price_to_fcf": safe_div(cap, g("fcf")),
        "fcf_yield": safe_div(g("fcf"), cap),
        "earnings_yield": safe_div(g("net_income"), cap),
        "dividend_yield": safe_div(g("dividends_paid"), cap),
        "buyback_yield": safe_div(g("buybacks"), cap),
    }
    dy, by = out["dividend_yield"], out["buyback_yield"]
    out["shareholder_yield"] = None if dy is None or by is None else dy + by
    return out


#: Ratio keys grouped for display, with the unit each group is expressed in.
RATIO_GROUPS: dict[str, dict[str, Any]] = {
    "margins": {"unit": "fraction", "keys": ["gross_margin", "operating_margin", "ebitda_margin", "net_margin",
                                             "fcf_margin", "rnd_intensity", "capex_intensity"]},
    "returns": {"unit": "fraction", "keys": ["roe", "roa", "roic", "effective_tax_rate"]},
    "dupont": {"unit": "mixed", "keys": ["dupont_net_margin", "dupont_asset_turnover", "dupont_equity_multiplier"]},
    "leverage": {"unit": "multiple", "keys": ["debt_to_equity", "debt_to_assets", "liabilities_to_assets",
                                              "net_debt_to_ebitda", "interest_coverage"]},
    "liquidity": {"unit": "multiple", "keys": ["current_ratio", "quick_ratio", "cash_ratio"]},
    "efficiency": {"unit": "mixed", "keys": ["asset_turnover", "dso_days", "dio_days"]},
    "per_share": {"unit": "USD", "keys": ["eps_diluted", "revenue_per_share", "fcf_per_share",
                                          "book_value_per_share", "dividends_per_share"]},
    "capital_return": {"unit": "fraction", "keys": ["payout_ratio", "total_payout_ratio"]},
    "growth": {"unit": "fraction", "keys": ["revenue_growth", "eps_growth", "fcf_growth", "net_income_growth"]},
}
