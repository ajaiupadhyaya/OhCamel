"""Accounting-based quality, distress and manipulation scores.

Every function takes standardized annual statements (rows = fiscal years,
oldest first) and returns a dict with the score, its itemized components,
the fiscal years used and ``missing``: the inputs that were unavailable. A
score whose inputs are missing is ``None`` -- inputs are never imputed.

The coefficients below are the *estimated model parameters published in each
paper* (they define the model); they are not market data.

References
----------
* Piotroski, J. D. (2000), "Value Investing: The Use of Historical Financial
  Statement Information to Separate Winners from Losers", *Journal of
  Accounting Research* 38 (Supplement), 1-41.
* Altman, E. I. (1968), "Financial Ratios, Discriminant Analysis and the
  Prediction of Corporate Bankruptcy", *Journal of Finance* 23(4), 589-609.
* Altman, E. I., Hartzell, J. & Peck, M. (1995), "Emerging Markets Corporate
  Bonds: A Scoring System", Salomon Brothers; Altman & Hotchkiss (2006),
  *Corporate Financial Distress and Bankruptcy*, 3rd ed., ch. 12 (Z'').
* Beneish, M. D. (1999), "The Detection of Earnings Manipulation",
  *Financial Analysts Journal* 55(5), 24-36.
* Sloan, R. G. (1996), "Do Stock Prices Fully Reflect Information in Accruals
  and Cash Flows about Future Earnings?", *The Accounting Review* 71(3),
  289-315; cash-flow-statement accruals per Hribar, P. & Collins, D. W.
  (2002), "Errors in Estimating Accruals", *Journal of Accounting Research*
  40(1), 105-134.
* Ohlson, J. A. (1980), "Financial Ratios and the Probabilistic Prediction
  of Bankruptcy", *Journal of Accounting Research* 18(1), 109-131.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .ratios import _f, safe_div
from .statements import ensure_columns, prior_year_label


def _years(annual: pd.DataFrame, n: int) -> list[pd.Timestamp] | None:
    """The last ``n`` fiscal years as consecutive (one year apart) labels,
    newest first, or None."""
    d = ensure_columns(annual)
    if d.empty:
        return None
    out = [d.index[-1]]
    for _ in range(n - 1):
        p = prior_year_label(d.index, out[-1])
        if p is None:
            return None
        out.append(p)
    return out


def _need(values: dict[str, Any]) -> list[str]:
    return [k for k, v in values.items() if _f(v) is None]


def _iso(ts: pd.Timestamp) -> str:
    return ts.date().isoformat()


# ----------------------------------------------------------------- Piotroski
PIOTROSKI_SIGNALS: dict[str, str] = {
    "F_ROA": "ROA > 0 (net income / beginning total assets)",
    "F_CFO": "CFO > 0 (cash from operations / beginning total assets)",
    "F_dROA": "ROA improved vs prior year",
    "F_ACCRUAL": "CFO/assets > ROA (earnings backed by cash)",
    "F_dLEVER": "long-term debt / average total assets fell",
    "F_dLIQUID": "current ratio rose",
    "F_EQ_OFFER": "no equity issuance (diluted share count did not rise; proxy)",
    "F_dMARGIN": "gross margin rose",
    "F_dTURN": "asset turnover (sales / beginning total assets) rose",
}


def piotroski(annual: pd.DataFrame) -> dict[str, Any]:
    """Piotroski (2000) F-score: sum of nine binary signals (0-9).

    With t the latest fiscal year and ``TA_{t-1}`` beginning-of-year assets:

    * profitability: ``ROA_t = NI_t / TA_{t-1} > 0``; ``CFO_t / TA_{t-1} > 0``;
      ``ROA_t > ROA_{t-1}``; ``CFO_t / TA_{t-1} > ROA_t`` (accrual);
    * leverage/liquidity: ``LTD_t / avg(TA_t, TA_{t-1}) < LTD_{t-1} / avg(TA_{t-1}, TA_{t-2})``;
      ``CR_t > CR_{t-1}``; no common equity issued (proxy: diluted weighted
      shares ``S_t <= S_{t-1}``, since XBRL company facts lack a uniform
      issuance tag);
    * efficiency: ``GM_t > GM_{t-1}``; ``Sales_t / TA_{t-1} > Sales_{t-1} / TA_{t-2}``.

    Needs three consecutive fiscal years. Each signal is 1/0 or None when its
    inputs are missing; ``score`` is None unless all nine are available
    (``partial_score`` sums the available ones).
    """
    ys = _years(annual, 3)
    if ys is None:
        return {"score": None, "signals": {}, "missing": ["three consecutive fiscal years"],
                "reference": "Piotroski (2000)"}
    d = ensure_columns(annual)
    t, t1, t2 = (d.loc[y] for y in ys)
    sig: dict[str, int | None] = {}
    detail: dict[str, dict[str, float | None]] = {}
    missing: list[str] = []

    def put(name: str, inputs: dict[str, Any], cond: Any) -> None:
        miss = _need(inputs)
        detail[name] = {k: _f(v) for k, v in inputs.items()}
        if miss:
            sig[name] = None
            missing.extend(f"{name}:{m}" for m in miss)
        else:
            sig[name] = int(bool(cond()))

    roa_t = safe_div(t["net_income"], t1["total_assets"])
    roa_t1 = safe_div(t1["net_income"], t2["total_assets"])
    cfo_t = safe_div(t["cfo"], t1["total_assets"])
    put("F_ROA", {"roa_t": roa_t}, lambda: roa_t > 0)
    put("F_CFO", {"cfo_to_assets_t": cfo_t}, lambda: cfo_t > 0)
    put("F_dROA", {"roa_t": roa_t, "roa_t-1": roa_t1}, lambda: roa_t > roa_t1)
    put("F_ACCRUAL", {"cfo_to_assets_t": cfo_t, "roa_t": roa_t}, lambda: cfo_t > roa_t)
    at_avg_t = None if _f(t["total_assets"]) is None or _f(t1["total_assets"]) is None else \
        (t["total_assets"] + t1["total_assets"]) / 2
    at_avg_t1 = None if _f(t1["total_assets"]) is None or _f(t2["total_assets"]) is None else \
        (t1["total_assets"] + t2["total_assets"]) / 2
    lev_t = safe_div(t["long_term_debt"], at_avg_t)
    lev_t1 = safe_div(t1["long_term_debt"], at_avg_t1)
    put("F_dLEVER", {"lever_t": lev_t, "lever_t-1": lev_t1}, lambda: lev_t < lev_t1)
    cr_t = safe_div(t["current_assets"], t["current_liabilities"])
    cr_t1 = safe_div(t1["current_assets"], t1["current_liabilities"])
    put("F_dLIQUID", {"current_ratio_t": cr_t, "current_ratio_t-1": cr_t1}, lambda: cr_t > cr_t1)
    put("F_EQ_OFFER", {"shares_t": t["shares_diluted"], "shares_t-1": t1["shares_diluted"]},
        lambda: t["shares_diluted"] <= t1["shares_diluted"])
    gm_t = safe_div(t["gross_profit"], t["revenue"])
    gm_t1 = safe_div(t1["gross_profit"], t1["revenue"])
    put("F_dMARGIN", {"gross_margin_t": gm_t, "gross_margin_t-1": gm_t1}, lambda: gm_t > gm_t1)
    turn_t = safe_div(t["revenue"], t1["total_assets"])
    turn_t1 = safe_div(t1["revenue"], t2["total_assets"])
    put("F_dTURN", {"turnover_t": turn_t, "turnover_t-1": turn_t1}, lambda: turn_t > turn_t1)

    avail = [v for v in sig.values() if v is not None]
    score = sum(avail) if len(avail) == 9 else None
    return {
        "score": score,
        "partial_score": sum(avail),
        "n_available": len(avail),
        "signals": [{"name": k, "description": PIOTROSKI_SIGNALS[k], "value": sig[k], "inputs": detail[k]}
                    for k in PIOTROSKI_SIGNALS],
        "interpretation": None if score is None else ("strong (8-9)" if score >= 8 else
                                                      "weak (0-2)" if score <= 2 else "neutral (3-7)"),
        "fiscal_years": [_iso(y) for y in ys],
        "missing": missing,
        "reference": "Piotroski (2000), J. Accounting Research 38 Suppl., 1-41",
    }


# -------------------------------------------------------------------- Altman
ALTMAN_Z = {"X1": 1.2, "X2": 1.4, "X3": 3.3, "X4": 0.6, "X5": 1.0}
ALTMAN_Z_ZONES = (1.81, 2.99)
ALTMAN_Z2 = {"X1": 6.56, "X2": 3.26, "X3": 6.72, "X4": 1.05}
ALTMAN_Z2_ZONES = (1.10, 2.60)


def _zone(z: float, lo: float, hi: float) -> str:
    return "distress" if z < lo else ("safe" if z > hi else "grey")


def altman_z(annual: pd.DataFrame, market_value_equity: float | None) -> dict[str, Any]:
    """Altman (1968) Z-score (public manufacturers)::

        Z = 1.2 X1 + 1.4 X2 + 3.3 X3 + 0.6 X4 + 1.0 X5
        X1 = working capital / TA,  X2 = retained earnings / TA,
        X3 = EBIT / TA,  X4 = market value of equity / total liabilities,
        X5 = sales / TA

    zones: Z < 1.81 distress, 1.81-2.99 grey, > 2.99 safe. EBIT is operating
    income; balances and flows are the latest fiscal year; the market value of
    equity is live price x shares outstanding (supplied by the caller).
    """
    d = ensure_columns(annual)
    if d.empty:
        return {"z": None, "missing": ["annual statements"], "reference": "Altman (1968)"}
    r = d.iloc[-1]
    ta = r["total_assets"]
    ca, cl = _f(r["current_assets"]), _f(r["current_liabilities"])
    x = {
        "X1": safe_div(None if ca is None or cl is None else ca - cl, ta),
        "X2": safe_div(r["retained_earnings"], ta),
        "X3": safe_div(r["operating_income"], ta),
        "X4": safe_div(market_value_equity, r["total_liabilities"]),
        "X5": safe_div(r["revenue"], ta),
    }
    missing = [k for k, v in x.items() if v is None]
    z = None if missing else float(sum(ALTMAN_Z[k] * x[k] for k in ALTMAN_Z))
    return {
        "z": z, "zone": None if z is None else _zone(z, *ALTMAN_Z_ZONES),
        "components": [{"name": k, "value": x[k], "coefficient": ALTMAN_Z[k],
                        "contribution": None if x[k] is None else ALTMAN_Z[k] * x[k]} for k in ALTMAN_Z],
        "zones": {"distress_below": ALTMAN_Z_ZONES[0], "safe_above": ALTMAN_Z_ZONES[1]},
        "fiscal_year": _iso(d.index[-1]), "missing": missing,
        "reference": "Altman (1968), J. Finance 23(4), 589-609",
    }


def altman_z2(annual: pd.DataFrame) -> dict[str, Any]:
    """Altman Z'' (Altman, Hartzell & Peck 1995; non-manufacturers and
    emerging markets)::

        Z'' = 6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4
        X4 = book value of equity / total liabilities

    zones: Z'' < 1.10 distress, 1.10-2.60 grey, > 2.60 safe (the emerging-
    market variant adds a constant 3.25, not applied here).
    """
    d = ensure_columns(annual)
    if d.empty:
        return {"z": None, "missing": ["annual statements"], "reference": "Altman et al. (1995)"}
    r = d.iloc[-1]
    ta = r["total_assets"]
    ca, cl = _f(r["current_assets"]), _f(r["current_liabilities"])
    x = {
        "X1": safe_div(None if ca is None or cl is None else ca - cl, ta),
        "X2": safe_div(r["retained_earnings"], ta),
        "X3": safe_div(r["operating_income"], ta),
        "X4": safe_div(r["equity"], r["total_liabilities"]),
    }
    missing = [k for k, v in x.items() if v is None]
    z = None if missing else float(sum(ALTMAN_Z2[k] * x[k] for k in ALTMAN_Z2))
    return {
        "z": z, "zone": None if z is None else _zone(z, *ALTMAN_Z2_ZONES),
        "components": [{"name": k, "value": x[k], "coefficient": ALTMAN_Z2[k],
                        "contribution": None if x[k] is None else ALTMAN_Z2[k] * x[k]} for k in ALTMAN_Z2],
        "zones": {"distress_below": ALTMAN_Z2_ZONES[0], "safe_above": ALTMAN_Z2_ZONES[1]},
        "fiscal_year": _iso(d.index[-1]), "missing": missing,
        "reference": "Altman, Hartzell & Peck (1995); Altman & Hotchkiss (2006) ch. 12",
    }


# ------------------------------------------------------------------- Beneish
BENEISH_INTERCEPT = -4.84
BENEISH_COEF = {"DSRI": 0.920, "GMI": 0.528, "AQI": 0.404, "SGI": 0.892, "DEPI": 0.115,
                "SGAI": -0.172, "TATA": 4.679, "LVGI": -0.327}
BENEISH_THRESHOLD = -1.78
BENEISH_INDEX_DESCRIPTIONS = {
    "DSRI": "days sales in receivables index",
    "GMI": "gross margin index (prior / current)",
    "AQI": "asset quality index (non-current, non-PP&E asset share)",
    "SGI": "sales growth index",
    "DEPI": "depreciation index (prior rate / current rate)",
    "SGAI": "SG&A expense index",
    "LVGI": "leverage index ((CL + LTD) / TA)",
    "TATA": "total accruals to total assets ((NI - CFO) / TA)",
}


def beneish_indices(cur: pd.Series, prev: pd.Series) -> dict[str, float | None]:
    """The eight Beneish (1999) indices for year t (``cur``) vs t-1 (``prev``)::

        DSRI = (REC_t / S_t) / (REC_{t-1} / S_{t-1})
        GMI  = GM_{t-1} / GM_t,               GM = (S - COGS) / S
        AQI  = (1 - (CA_t + PPE_t)/TA_t) / (1 - (CA_{t-1} + PPE_{t-1})/TA_{t-1})
        SGI  = S_t / S_{t-1}
        DEPI = [D_{t-1}/(D_{t-1} + PPE_{t-1})] / [D_t/(D_t + PPE_t)]
        SGAI = (SGA_t / S_t) / (SGA_{t-1} / S_{t-1})
        LVGI = [(CL_t + LTD_t)/TA_t] / [(CL_{t-1} + LTD_{t-1})/TA_{t-1}]
        TATA = (NI_t - CFO_t) / TA_t

    D is depreciation & amortization (XBRL rarely separates depreciation);
    GM uses reported gross profit (revenue - cost of revenue where not reported).
    """
    def num(s: pd.Series, k: str) -> float | None:
        return _f(s.get(k))

    def ratio(a: float | None, b: float | None) -> float | None:
        return safe_div(a, b, positive_den=False)

    out: dict[str, float | None] = {}
    out["DSRI"] = ratio(safe_div(num(cur, "receivables"), num(cur, "revenue")),
                        safe_div(num(prev, "receivables"), num(prev, "revenue")))
    out["GMI"] = ratio(safe_div(num(prev, "gross_profit"), num(prev, "revenue")),
                       safe_div(num(cur, "gross_profit"), num(cur, "revenue")))

    def nonhard(s: pd.Series) -> float | None:
        ca, ppe, ta = num(s, "current_assets"), num(s, "ppe_net"), num(s, "total_assets")
        return None if ca is None or ppe is None or safe_div(ca + ppe, ta) is None else 1 - (ca + ppe) / ta

    out["AQI"] = ratio(nonhard(cur), nonhard(prev))
    out["SGI"] = safe_div(num(cur, "revenue"), num(prev, "revenue"))

    def deprate(s: pd.Series) -> float | None:
        dep, ppe = num(s, "d_and_a"), num(s, "ppe_net")
        return None if dep is None or ppe is None else safe_div(dep, dep + ppe)

    out["DEPI"] = ratio(deprate(prev), deprate(cur))
    out["SGAI"] = ratio(safe_div(num(cur, "sga"), num(cur, "revenue")),
                        safe_div(num(prev, "sga"), num(prev, "revenue")))

    def lev(s: pd.Series) -> float | None:
        cl, ltd = num(s, "current_liabilities"), num(s, "long_term_debt")
        return None if cl is None or ltd is None else safe_div(cl + ltd, num(s, "total_assets"))

    out["LVGI"] = ratio(lev(cur), lev(prev))
    ni, cfo = num(cur, "net_income"), num(cur, "cfo")
    out["TATA"] = None if ni is None or cfo is None else safe_div(ni - cfo, num(cur, "total_assets"))
    return out


def beneish(annual: pd.DataFrame) -> dict[str, Any]:
    """Beneish (1999) eight-variable M-score (unweighted probit, Table 3)::

        M = -4.84 + 0.920 DSRI + 0.528 GMI + 0.404 AQI + 0.892 SGI + 0.115 DEPI
            - 0.172 SGAI + 4.679 TATA - 0.327 LVGI

    ``M > -1.78`` flags a likely manipulator (Beneish's cut-off); the implied
    probability is ``Phi(M)`` (probit). Beneish, Lee & Nichols (2013) use
    -2.22 as a more sensitive screen; both are reported.
    """
    ys = _years(annual, 2)
    if ys is None:
        return {"m": None, "missing": ["two consecutive fiscal years"], "reference": "Beneish (1999)"}
    d = ensure_columns(annual)
    idx = beneish_indices(d.loc[ys[0]], d.loc[ys[1]])
    missing = [k for k, v in idx.items() if v is None]
    m = None if missing else float(BENEISH_INTERCEPT + sum(BENEISH_COEF[k] * idx[k] for k in BENEISH_COEF))
    from scipy.stats import norm

    return {
        "m": m,
        "probability": None if m is None else float(norm.cdf(m)),
        "flag": None if m is None else bool(m > BENEISH_THRESHOLD),
        "flag_sensitive": None if m is None else bool(m > -2.22),
        "threshold": BENEISH_THRESHOLD,
        "intercept": BENEISH_INTERCEPT,
        "indices": [{"name": k, "description": BENEISH_INDEX_DESCRIPTIONS[k], "value": idx[k],
                     "coefficient": BENEISH_COEF[k],
                     "contribution": None if idx[k] is None else BENEISH_COEF[k] * idx[k]} for k in BENEISH_COEF],
        "fiscal_years": [_iso(y) for y in ys],
        "missing": missing,
        "reference": "Beneish (1999), Financial Analysts Journal 55(5), 24-36 (coefficients: Table 3)",
    }


# --------------------------------------------------------------------- Sloan
def sloan_accruals(annual: pd.DataFrame) -> dict[str, Any]:
    """Sloan (1996) accruals ratio, cash-flow-statement form (Hribar & Collins 2002)::

        accruals ratio = (NI_t - CFO_t) / avg(TA_t, TA_{t-1})

    High (positive) accruals signal lower earnings persistence; Sloan's
    extreme deciles are roughly beyond +/-10% of assets.
    """
    ys = _years(annual, 2)
    if ys is None:
        return {"ratio": None, "missing": ["two consecutive fiscal years"], "reference": "Sloan (1996)"}
    d = ensure_columns(annual)
    t, t1 = d.loc[ys[0]], d.loc[ys[1]]
    inputs = {"net_income": t["net_income"], "cfo": t["cfo"], "total_assets_t": t["total_assets"],
              "total_assets_t-1": t1["total_assets"]}
    missing = _need(inputs)
    ratio = None
    if not missing:
        ratio = safe_div(t["net_income"] - t["cfo"], (t["total_assets"] + t1["total_assets"]) / 2)
    return {
        "ratio": ratio,
        "interpretation": None if ratio is None else (
            "high accruals (low earnings quality)" if ratio > 0.10 else
            "low accruals (high earnings quality)" if ratio < -0.10 else "moderate"),
        "inputs": {k: _f(v) for k, v in inputs.items()},
        "fiscal_years": [_iso(y) for y in ys], "missing": missing,
        "reference": "Sloan (1996), Accounting Review 71(3); Hribar & Collins (2002)",
    }


# -------------------------------------------------------------------- Ohlson
def price_level_index_1968(deflator: pd.Series, balance_sheet_date: pd.Timestamp) -> float | None:
    """Ohlson's (1980) price-level index for SIZE: the annual mean of a price
    deflator (e.g. FRED GDPDEF, any base) in the calendar year *before* the
    balance-sheet year, rebased so that the 1968 mean = 100. None when either
    year is missing from ``deflator``."""
    s = deflator.dropna()
    if s.empty:
        return None
    idx = pd.DatetimeIndex(s.index)
    base = s[idx.year == 1968]
    prior = s[idx.year == pd.Timestamp(balance_sheet_date).year - 1]
    if base.empty or prior.empty or float(base.mean()) <= 0:
        return None
    return float(prior.mean() / base.mean() * 100.0)


OHLSON_COEF = {"const": -1.32, "SIZE": -0.407, "TLTA": 6.03, "WCTA": -1.43, "CLCA": 0.0757, "OENEG": -1.72,
               "NITA": -2.37, "FUTL": -1.83, "INTWO": 0.285, "CHIN": -0.521}


def ohlson_o(annual: pd.DataFrame, price_level_index: float | None) -> dict[str, Any]:
    """Ohlson (1980) O-score (model 1, one-year horizon, Table 4)::

        O = -1.32 - 0.407 SIZE + 6.03 TLTA - 1.43 WCTA + 0.0757 CLCA
            - 1.72 OENEG - 2.37 NITA - 1.83 FUTL + 0.285 INTWO - 0.521 CHIN

    SIZE = log(total assets [USD, as reported in dollars] / GNP price-level
    index, 1968 = 100, taken for the year *prior* to the balance-sheet year)
    -- Ohlson (1980), p. 118: "Total assets are as reported in dollars. The
    index year is as of the year prior to the year of the balance sheet date"
    (see :func:`price_level_index_1968`). Scaling assets to millions (or the
    index to 1968 = 1) shifts O by 3.7-5.6 and turns a healthy firm's ~1%
    probability into ~30% or more;
    TLTA = TL/TA; WCTA = (CA - CL)/TA; CLCA = CL/CA; OENEG = 1 if TL > TA;
    NITA = NI/TA; FUTL = funds from operations (CFO proxy) / TL;
    INTWO = 1 if net income < 0 in both of the last two years;
    CHIN = (NI_t - NI_{t-1}) / (|NI_t| + |NI_{t-1}|).
    P(bankruptcy) = 1 / (1 + exp(-O)). ``price_level_index`` is the GDP
    deflator rebased to 1968 = 100 (FRED GDPDEF, supplied by the caller).
    """
    ys = _years(annual, 2)
    if ys is None:
        return {"o": None, "missing": ["two consecutive fiscal years"], "reference": "Ohlson (1980)"}
    d = ensure_columns(annual)
    t, t1 = d.loc[ys[0]], d.loc[ys[1]]
    ta, tl, ca, cl = _f(t["total_assets"]), _f(t["total_liabilities"]), _f(t["current_assets"]), \
        _f(t["current_liabilities"])
    ni, ni1, cfo = _f(t["net_income"]), _f(t1["net_income"]), _f(t["cfo"])
    x: dict[str, float | None] = {
        "SIZE": None if ta is None or ta <= 0 or _f(price_level_index) is None or price_level_index <= 0
        else math.log(ta / price_level_index),
        "TLTA": safe_div(tl, ta),
        "WCTA": None if ca is None or cl is None else safe_div(ca - cl, ta),
        "CLCA": safe_div(cl, ca),
        "OENEG": None if tl is None or ta is None else float(tl > ta),
        "NITA": safe_div(ni, ta),
        "FUTL": safe_div(cfo, tl),
        "INTWO": None if ni is None or ni1 is None else float(ni < 0 and ni1 < 0),
        "CHIN": None if ni is None or ni1 is None or (abs(ni) + abs(ni1)) == 0
        else (ni - ni1) / (abs(ni) + abs(ni1)),
    }
    missing = [k for k, v in x.items() if v is None]
    if "SIZE" in missing and _f(price_level_index) is None:
        missing[missing.index("SIZE")] = "SIZE (GDP deflator unavailable)"
    o = None if missing else float(OHLSON_COEF["const"] + sum(OHLSON_COEF[k] * x[k] for k in x))
    return {
        "o": o,
        "probability": None if o is None else float(1.0 / (1.0 + np.exp(-o))),
        "components": [{"name": k, "value": x[k], "coefficient": OHLSON_COEF[k]} for k in x],
        "intercept": OHLSON_COEF["const"],
        "fiscal_years": [_iso(y) for y in ys], "missing": missing,
        "reference": "Ohlson (1980), J. Accounting Research 18(1), 109-131 (model 1)",
    }
