"""Realized-volatility estimators from daily OHLC, the volatility cone and the
volatility risk premium.

Per-day variance estimators (``O, H, L, C`` today, ``C_{-1}`` yesterday; all logs):

* close-to-close: ``r = ln(C/C_{-1})``, sample variance with ``n - 1``;
* Parkinson (1980), "The extreme value method for estimating the variance of
  the rate of return", J. Business 53: ``(1/(4 ln 2)) E[ln(H/L)^2]``;
* Garman & Klass (1980), "On the estimation of security price volatilities from
  historical data", J. Business 53: ``E[0.5 ln(H/L)^2 - (2 ln 2 - 1) ln(C/O)^2]``;
* Rogers & Satchell (1991), "Estimating variance from high, low and closing
  prices", Ann. Appl. Prob. 1: ``E[ln(H/C) ln(H/O) + ln(L/C) ln(L/O)]`` (drift-robust);
* Yang & Zhang (2000), "Drift-independent volatility estimation based on high,
  low, open and close prices", J. Business 73:
  ``V_O + k V_C + (1 - k) V_RS`` with overnight ``ln(O/C_{-1})`` and open-to-close
  ``ln(C/O)`` sample variances and ``k = 0.34 / (1.34 + (n + 1)/(n - 1))``.

Annualized vol ``= sqrt(252 x variance)``. Rolling windows are trailing.

Volatility cone (Burghardt & Lane 1990, "How to tell if options are cheap",
J. Portfolio Management 16): for each horizon ``h`` the distribution (min,
percentiles, max) of rolling ``h``-day realized vol over the history, against
which today's value and the implied term structure are compared. Windows overlap
(as in the original), so percentiles are not from independent samples.

Volatility risk premium (Carr & Wu 2009, "Variance risk premiums", RFS 22;
Bollerslev, Tauchen & Zhou 2009): implied minus realized, in vol points and in
variance units.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252
CONE_HORIZONS = (10, 21, 42, 63, 126, 252)
ESTIMATORS = ("close_to_close", "parkinson", "garman_klass", "rogers_satchell", "yang_zhang")


def adjusted_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Scale O/H/L/C by ``adj_close / close`` so ratios across days are free of
    split (and, where the source adjusts, dividend) jumps; intraday ratios are
    unchanged. Rows with non-positive or inconsistent prices are dropped."""
    d = df[["open", "high", "low", "close"]].astype(float)
    if "adj_close" in df.columns:
        f = (df["adj_close"].astype(float) / d["close"]).where(lambda s: np.isfinite(s) & (s > 0))
        d = d.mul(f, axis=0)
    d = d.dropna()
    ok = (d > 0).all(axis=1) & (d["high"] >= d[["open", "close", "low"]].max(axis=1) * (1 - 1e-9)) \
        & (d["low"] <= d[["open", "close", "high"]].min(axis=1) * (1 + 1e-9))
    return d[ok]


def daily_terms(ohlc: pd.DataFrame) -> pd.DataFrame:
    """Per-day log terms used by every estimator (first row dropped)."""
    o, h, l_, c = (np.log(ohlc[x]) for x in ("open", "high", "low", "close"))
    cp = c.shift(1)
    out = pd.DataFrame({
        "ret": c - cp,
        "overnight": o - cp,
        "open_close": c - o,
        "hl2": (h - l_) ** 2,
        "rs": (h - c) * (h - o) + (l_ - c) * (l_ - o),
    }, index=ohlc.index)
    out["gk"] = 0.5 * out["hl2"] - (2.0 * np.log(2.0) - 1.0) * out["open_close"] ** 2
    return out.iloc[1:]


def rolling_vol(ohlc: pd.DataFrame, window: int, estimator: str = "close_to_close") -> pd.Series:
    """Trailing ``window``-day annualized volatility for one estimator."""
    if window < 2:
        raise ValueError("window must be >= 2")
    t = daily_terms(ohlc)
    if estimator == "close_to_close":
        var = t["ret"].rolling(window).var(ddof=1)
    elif estimator == "parkinson":
        var = t["hl2"].rolling(window).mean() / (4.0 * np.log(2.0))
    elif estimator == "garman_klass":
        var = t["gk"].rolling(window).mean()
    elif estimator == "rogers_satchell":
        var = t["rs"].rolling(window).mean()
    elif estimator == "yang_zhang":
        kk = 0.34 / (1.34 + (window + 1) / (window - 1))
        var = (t["overnight"].rolling(window).var(ddof=1) + kk * t["open_close"].rolling(window).var(ddof=1)
               + (1 - kk) * t["rs"].rolling(window).mean())
    else:
        raise ValueError(f"unknown estimator {estimator!r}; choose from {ESTIMATORS}")
    return np.sqrt(var.clip(lower=0.0) * TRADING_DAYS).rename(estimator)


def all_estimators(ohlc: pd.DataFrame, window: int) -> pd.DataFrame:
    return pd.concat([rolling_vol(ohlc, window, e) for e in ESTIMATORS], axis=1)


def vol_cone(
    ohlc: pd.DataFrame, horizons: tuple[int, ...] = CONE_HORIZONS, estimator: str = "close_to_close",
    percentiles: tuple[float, ...] = (0.10, 0.25, 0.50, 0.75, 0.90),
) -> pd.DataFrame:
    """Rows = horizon (days); columns min, p10..p90, max, current, n_windows, current_pctile."""
    rows = []
    for h in horizons:
        s = rolling_vol(ohlc, h, estimator).dropna()
        if s.empty:
            continue
        row: dict[str, Any] = {"horizon": h, "min": s.min(), "max": s.max(), "mean": s.mean()}
        for p in percentiles:
            row[f"p{round(p * 100):d}"] = s.quantile(p)
        row["current"] = s.iloc[-1]
        row["current_pctile"] = float((s <= s.iloc[-1]).mean())
        row["n_windows"] = len(s)
        rows.append(row)
    return pd.DataFrame(rows).set_index("horizon") if rows else pd.DataFrame()


def vrp(implied_vol: float, realized_vol: float) -> dict[str, float]:
    """Implied minus realized in vol and variance units (decimals)."""
    return {"implied": implied_vol, "realized": realized_vol, "vol_premium": implied_vol - realized_vol,
            "variance_premium": implied_vol**2 - realized_vol**2,
            "ratio": implied_vol / realized_vol if realized_vol > 0 else float("nan")}


def vrp_history(implied: pd.Series, ohlc: pd.DataFrame, window: int = 21) -> pd.DataFrame:
    """Daily history of an implied-vol index (decimal) against realized vol.

    ``trailing`` = RV over the past ``window`` days (known at t); ``forward`` = RV
    over the next ``window`` days (ex-post, what the implied vol forecast) -- the
    ex-post premium ``implied - forward`` is the one Carr & Wu (2009) study.
    """
    rv = rolling_vol(ohlc, window, "close_to_close")
    df = pd.concat({"implied": implied.astype(float), "trailing_rv": rv}, axis=1, join="inner").dropna(subset=["implied"])
    df["forward_rv"] = rv.shift(-window).reindex(df.index)
    df["premium_trailing"] = df["implied"] - df["trailing_rv"]
    df["premium_forward"] = df["implied"] - df["forward_rv"]
    return df
