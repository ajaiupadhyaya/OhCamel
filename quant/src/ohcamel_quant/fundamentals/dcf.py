"""Two-stage free-cash-flow-to-the-firm (FCFF) DCF, WACC inputs and reverse DCF.

Model
-----
With base-year FCFF ``F_0``, explicit growth rates ``g_1..g_N``, discount
rate ``WACC`` and perpetual growth ``g_T < WACC``::

    F_i   = F_0 * prod_{j<=i} (1 + g_j)
    PV_i  = F_i / (1 + WACC)^i                      (end-of-year convention)
    TV_N  = F_N (1 + g_T) / (WACC - g_T)            (Gordon 1962 growth model)
    EV    = sum_i PV_i + TV_N / (1 + WACC)^N
    equity value = EV - (total debt - cash);  value / share = equity / shares

(Koller, Goedhart & Wessels (2020), *Valuation: Measuring and Managing the
Value of Companies*, 7th ed., ch. 10-14; Damodaran (2012), *Investment
Valuation*, 3rd ed., ch. 15.) With ``g_i == g_T`` for every i the model
collapses to the closed form ``EV = F_0 (1 + g) / (WACC - g)``.

Inputs (all derived from data by the router, all overridable)
------------------------------------------------------------
* ``FCFF = CFO + interest x (1 - t) - capex`` (Damodaran 2012, ch. 15).
* growth path: the 5-year revenue CAGR clipped to ``[g_T, 25%]`` in year 1,
  fading linearly to ``g_T`` in year N+1 (:func:`fade_path`).
* ``g_T``: 10-year breakeven inflation (FRED T10YIE) -- a data-derived
  nominal floor (zero real growth).
* WACC (Modigliani & Miller 1963 after-tax form)::

      WACC = E/(D+E) k_e + D/(D+E) k_d (1 - t)
      k_e  = r_f + beta x ERP                         (CAPM; Sharpe 1964, Lintner 1965)

  ``r_f`` = 10-year Treasury yield (FRED DGS10); ``beta`` = OLS slope of 60
  monthly excess returns on the market's (Blume (1971) adjusted
  ``0.67 beta + 0.33`` reported alongside); ``ERP`` = full-sample arithmetic
  mean of Ken French's Mkt-RF, annualized; ``k_d`` = interest expense /
  average total debt, floored at ``r_f``; ``t`` = 3-year mean effective tax
  rate clipped to [0, 0.5]; ``E`` at market value, ``D`` at book value.
* Reverse DCF: the constant explicit-period growth that makes DCF equity
  value equal today's market capitalization -- Mauboussin, M. J. &
  Rappaport, A. (2001), *Expectations Investing*, Harvard Business School Press.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .ratios import _f, cagr, effective_tax_rate
from .statements import ensure_columns, prior_label, prior_year_label

MAX_INITIAL_GROWTH = 0.25
BLUME_WEIGHTS = (0.67, 0.33)
TAX_CLIP = (0.0, 0.5)


# ------------------------------------------------------------------ inputs
def fcff(cfo: float, interest: float | None, capex: float, tax_rate: float) -> float:
    """``FCFF = CFO + interest x (1 - t) - capex``; ``interest=None`` omits the
    add-back (caller must disclose it)."""
    if _f(cfo) is None or _f(capex) is None:
        raise ValueError("FCFF needs cash from operations and capital expenditures")
    add = 0.0 if _f(interest) is None else float(interest) * (1.0 - tax_rate)
    return float(cfo) + add - float(capex)


def effective_tax_3y(annual: pd.DataFrame, years: int = 3, clip: tuple[float, float] = TAX_CLIP) -> float | None:
    """Mean of the last ``years`` fiscal-year effective tax rates
    (``income_tax / pretax_income``, years with positive pre-tax income only),
    clipped to ``clip``. None when no year qualifies."""
    d = ensure_columns(annual).iloc[-years:]
    rates = [effective_tax_rate(r["income_tax"], r["pretax_income"], clip=(-np.inf, np.inf))
             for _, r in d.iterrows()]
    rates = [x for x in rates if x is not None]
    if not rates:
        return None
    return float(np.clip(np.mean(rates), *clip))


def revenue_cagr(annual: pd.DataFrame, years: int = 5) -> tuple[float | None, int | None]:
    """(CAGR, horizon used) of revenue over ``years`` fiscal years, falling
    back to the longest shorter horizon available (>= 1 year)."""
    d = ensure_columns(annual)
    if d.empty:
        return None, None
    t = d.index[-1]
    for n in range(years, 0, -1):
        pl = prior_label(d.index, t, 365.25 * n, tol_days=20 + 2 * n)
        if pl is not None:
            g = cagr(d.loc[pl, "revenue"], d.loc[t, "revenue"], n)
            if g is not None:
                return g, n
    return None, None


def fade_path(g0: float, g_terminal: float, years: int) -> np.ndarray:
    """Linear fade: ``g_i = g0 + (g_T - g0) (i - 1) / N`` for i = 1..N, so year 1
    grows at ``g0`` and year N+1 (the first terminal year) at ``g_T``."""
    if years < 1:
        raise ValueError("explicit period must be at least one year")
    i = np.arange(1, years + 1, dtype=float)
    return g0 + (g_terminal - g0) * (i - 1.0) / years


def cost_of_debt(interest: float | None, debt_close: float | None, debt_open: float | None,
                 rf: float) -> tuple[float, str]:
    """``k_d = interest / avg(total debt)`` floored at ``rf``; returns (k_d, basis)."""
    i, dc, do = _f(interest), _f(debt_close), _f(debt_open)
    if i is None or dc is None:
        return rf, "risk-free rate (interest expense or debt not reported)"
    avg_d = (dc + do) / 2.0 if do is not None else dc
    if avg_d <= 0:
        return rf, "risk-free rate (no debt outstanding)"
    kd = i / avg_d
    if kd < rf:
        return rf, f"floored at the risk-free rate (reported {kd:.4f})"
    return kd, "interest expense / average total debt"


def wacc(equity_value: float, debt_value: float, ke: float, kd: float, tax_rate: float) -> float:
    """``E/(D+E) k_e + D/(D+E) k_d (1 - t)`` (market E, book D)."""
    e, d = float(equity_value), max(float(debt_value), 0.0)
    if e <= 0:
        raise ValueError("WACC needs a positive market value of equity")
    return (e * ke + d * kd * (1.0 - tax_rate)) / (e + d)


# -------------------------------------------------------------------- beta
def monthly_returns(prices: pd.Series) -> pd.Series:
    """Month-end-to-month-end simple returns from daily prices; the current
    month is dropped if its last observation is not the month's last business day."""
    p = prices.dropna().sort_index()
    if p.empty:
        return pd.Series(dtype=float)
    last = p.index[-1]
    m = p.resample("ME").last()
    if last != (last + pd.offsets.BMonthEnd(0)):
        m = m.iloc[:-1]
    return m.pct_change().dropna()


def monthly_from_daily(daily: pd.Series) -> pd.Series:
    """Compound daily decimal returns into calendar-month returns (label: month end)."""
    return (1.0 + daily.dropna()).resample("ME").prod() - 1.0


def estimate_beta(asset: pd.Series, market: pd.Series, rf: pd.Series | None = None,
                  window: int = 60) -> dict[str, Any]:
    """CAPM beta by OLS on the last ``window`` common monthly excess returns::

        r_i - r_f = alpha + beta (r_m - r_f) + e

    Reports the slope, its OLS standard error and t-stat, R^2, the number of
    months, and the Blume (1971) adjusted beta ``0.67 beta + 0.33`` (Blume,
    M. E., "On the Assessment of Risk", *Journal of Finance* 26(1), 1-10).
    """
    df = pd.concat({"a": asset, "m": market}, axis=1, join="inner").dropna()
    if rf is not None:
        df = df.join(rf.rename("rf"), how="inner").dropna()
        df["a"] -= df["rf"]
        df["m"] -= df["rf"]
    df = df.iloc[-window:]
    n = len(df)
    if n < 24:
        raise ValueError(f"beta needs at least 24 monthly observations; have {n}")
    y, x = df["a"].to_numpy(), df["m"].to_numpy()
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    if not sxx > 1e-12 * max(float((x ** 2).sum()), 1e-300):
        raise ValueError("beta undefined: the market's monthly excess returns have zero variance")
    b = float(((x - xm) * (y - ym)).sum() / sxx)
    a = float(ym - b * xm)
    resid = y - a - b * x
    s2 = float((resid ** 2).sum() / (n - 2))
    se = math.sqrt(s2 / sxx)
    sst = float(((y - ym) ** 2).sum())
    return {
        "beta": b, "alpha_monthly": a, "se": se, "t_stat": b / se if se > 0 else None,
        "r2": 1.0 - float((resid ** 2).sum()) / sst if sst > 0 else None,
        "n_months": n, "start": df.index[0], "end": df.index[-1],
        "blume_adjusted": BLUME_WEIGHTS[0] * b + BLUME_WEIGHTS[1],
        "excess_returns": rf is not None,
    }


def equity_risk_premium(mkt_excess_monthly: pd.Series) -> dict[str, Any]:
    """Historical ERP: ``12 x mean(monthly market excess return)`` (arithmetic,
    full sample), with its standard error ``12 sd / sqrt(n)``."""
    x = mkt_excess_monthly.dropna()
    if len(x) < 120:
        raise ValueError(f"ERP needs at least 10 years of monthly data; have {len(x)} months")
    return {"erp": 12.0 * float(x.mean()), "se": 12.0 * float(x.std(ddof=1)) / math.sqrt(len(x)),
            "n_months": len(x), "start": x.index[0], "end": x.index[-1]}


# --------------------------------------------------------------------- DCF
def _check(base_fcff: float, w: float, g_terminal: float) -> None:
    if _f(base_fcff) is None:
        raise ValueError("base FCFF is missing")
    if base_fcff <= 0:
        raise ValueError(f"base FCFF is {base_fcff:,.0f} (<= 0): a growing perpetuity of a negative cash "
                         "flow is meaningless; override base_fcff with a normalized positive value")
    if not (w > -1):
        raise ValueError("WACC must exceed -100%")
    if g_terminal >= w:
        raise ValueError(f"terminal growth {g_terminal:.4f} >= WACC {w:.4f}: the Gordon growth value is "
                         "undefined (infinite or negative); lower g or raise WACC")


def dcf_value(base_fcff: float, growth: Sequence[float], w: float, g_terminal: float,
              net_debt: float = 0.0, shares: float | None = None, price: float | None = None) -> dict[str, Any]:
    """Two-stage FCFF DCF (formulas in the module docstring)."""
    _check(base_fcff, w, g_terminal)
    g = np.asarray(growth, dtype=float)
    n = len(g)
    if n < 1:
        raise ValueError("explicit period must be at least one year")
    f = base_fcff * np.cumprod(1.0 + g)
    disc = (1.0 + w) ** np.arange(1, n + 1)
    pv = f / disc
    tv = f[-1] * (1.0 + g_terminal) / (w - g_terminal)
    pv_tv = tv / disc[-1]
    ev = float(pv.sum() + pv_tv)
    equity = ev - float(net_debt)
    per_share = equity / shares if shares else None
    return {
        "table": [{"year": i + 1, "growth": float(g[i]), "fcff": float(f[i]), "discount_factor": float(1 / disc[i]),
                   "pv": float(pv[i])} for i in range(n)],
        "pv_explicit": float(pv.sum()),
        "terminal_value": float(tv),
        "pv_terminal": float(pv_tv),
        "terminal_share": float(pv_tv / ev) if ev != 0 else None,
        "enterprise_value": ev,
        "net_debt": float(net_debt),
        "equity_value": equity,
        "value_per_share": per_share,
        "price": price,
        "upside": (per_share / price - 1.0) if per_share is not None and price else None,
        "implied_exit_ev_fcff": float(tv / f[-1]),
    }


def gordon_value(base_fcff: float, w: float, g: float) -> float:
    """Closed-form constant-growth value ``F_0 (1 + g) / (WACC - g)`` (Gordon 1962)."""
    _check(base_fcff, w, g)
    return base_fcff * (1.0 + g) / (w - g)


def sensitivity(base_fcff: float, g0: float, years: int, w: float, g_terminal: float, net_debt: float,
                shares: float, wacc_step: float = 0.005, g_step: float = 0.0025, size: int = 5
                ) -> dict[str, Any]:
    """Per-share value on a ``size x size`` grid of WACC x terminal g centred
    on the base case. The explicit path is re-faded to each g_T (year-1 growth
    fixed at ``g0``). Cells with ``g_T >= WACC`` are None."""
    half = size // 2
    ws = [w + (k - half) * wacc_step for k in range(size)]
    gs = [g_terminal + (k - half) * g_step for k in range(size)]
    grid: list[list[float | None]] = []
    for wi in ws:
        row: list[float | None] = []
        for gi in gs:
            if gi >= wi or wi <= -1:
                row.append(None)
                continue
            v = dcf_value(base_fcff, fade_path(g0, gi, years), wi, gi, net_debt, shares)
            row.append(v["value_per_share"])
        grid.append(row)
    return {"wacc": ws, "terminal_growth": gs, "value_per_share": grid}


def reverse_dcf(base_fcff: float, years: int, w: float, g_terminal: float, net_debt: float,
                market_cap: float, lo: float = -0.95, hi: float = 2.0) -> dict[str, Any]:
    """Market-implied constant explicit-period growth (Mauboussin & Rappaport 2001).

    Solves ``equity(g) = market cap`` for ``g`` with ``g_1 = ... = g_N = g`` and
    terminal growth ``g_T`` fixed, by Brent's method. Equity value is strictly
    increasing in ``g`` for positive FCFF, so the root is unique when bracketed.
    """
    _check(base_fcff, w, g_terminal)

    def f(g: float) -> float:
        return dcf_value(base_fcff, np.full(years, g), w, g_terminal, net_debt)["equity_value"] - market_cap

    flo, fhi = f(lo), f(hi)
    if flo > 0:
        return {"implied_growth": None, "reason": f"market cap is below the value implied by {lo:.0%} annual "
                                                  "FCFF growth; the market prices a collapse beyond the range"}
    if fhi < 0:
        return {"implied_growth": None, "reason": f"market cap exceeds the value implied by {hi:.0%} annual "
                                                  f"FCFF growth for {years} years"}
    g = float(brentq(f, lo, hi, xtol=1e-12, maxiter=200))
    return {"implied_growth": g, "years": years, "terminal_growth": g_terminal, "wacc": w,
            "market_cap": market_cap, "reason": None}


def base_fcff_from_statements(annual: pd.DataFrame, ttm: pd.Series | None, tax_rate: float,
                              basis: str = "auto") -> dict[str, Any]:
    """Base-year FCFF from the TTM row (``basis='ttm'``), the last fiscal year
    (``'fy'``) or TTM when complete else last FY (``'auto'``)."""
    notes: list[str] = []
    candidates: list[tuple[str, pd.Series, str]] = []
    if basis in ("auto", "ttm") and ttm is not None:
        candidates.append(("ttm", ttm, "trailing twelve months"))
    d = ensure_columns(annual)
    if basis in ("auto", "fy") and not d.empty:
        candidates.append(("fy", d.iloc[-1], f"fiscal year ending {d.index[-1].date()}"))
    for key, row, label in candidates:
        if _f(row.get("cfo")) is None or _f(row.get("capex")) is None:
            notes.append(f"{label}: CFO or capex not reported")
            continue
        interest = _f(row.get("interest_expense"))
        if interest is None:
            notes.append(f"{label}: interest expense not reported, so the after-tax interest add-back is "
                         "omitted (FCFF understated by interest x (1 - t))")
        v = fcff(row["cfo"], interest, row["capex"], tax_rate)
        return {"fcff": v, "basis": key, "label": label, "cfo": _f(row["cfo"]), "capex": _f(row["capex"]),
                "interest_expense": interest, "tax_rate": tax_rate, "notes": notes}
    raise ValueError("cannot compute base FCFF: " + ("; ".join(notes) or "no statements"))


def debt_open_close(annual: pd.DataFrame) -> tuple[float | None, float | None]:
    """(opening, closing) total debt of the latest fiscal year."""
    d = ensure_columns(annual)
    if d.empty:
        return None, None
    t = d.index[-1]
    p = prior_year_label(d.index, t)
    return (_f(d.loc[p, "total_debt"]) if p is not None else None), _f(d.loc[t, "total_debt"])
