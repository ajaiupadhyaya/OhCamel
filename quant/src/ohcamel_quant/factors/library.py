"""Factor dashboard analytics over a table of daily factor returns (pure).

Input: Kenneth French daily factors (decimals) -- ``Mkt-RF, SMB, HML [, RMW,
CMA] [, Mom], RF``. Every factor is a zero-investment (excess) return, so its
Sharpe ratio is ``mean / std * sqrt(252)`` with no further rf subtraction
(Sharpe 1994, "The Sharpe Ratio", *JPM* 21(1)).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .hac import newey_west_lrv, nw_lag_rule
from .performance import TRADING_DAYS, drawdown_series

# Look-back windows as calendar offsets from the last observation.
WINDOWS: dict[str, pd.DateOffset | None] = {
    "1M": pd.DateOffset(months=1),
    "3M": pd.DateOffset(months=3),
    "6M": pd.DateOffset(months=6),
    "YTD": None,  # handled specially
    "1Y": pd.DateOffset(years=1),
    "3Y": pd.DateOffset(years=3),
    "5Y": pd.DateOffset(years=5),
    "10Y": pd.DateOffset(years=10),
    "20Y": pd.DateOffset(years=20),
    "MAX": None,
}
TRAILING = ("1M", "3M", "YTD", "1Y", "3Y", "5Y", "10Y", "MAX")
SUB_ANNUAL = frozenset({"1M", "3M", "6M", "YTD"})  # not annualised (compounding a month x12 misleads)


def window_slice(df: pd.DataFrame, window: str) -> pd.DataFrame:
    """Rows of ``df`` inside the look-back ``window`` ending at its last date.

    A window of ``k`` months keeps returns strictly after ``last - k months``
    so the compounded return spans exactly that calendar interval.
    """
    key = window.upper()
    if key not in WINDOWS:
        raise ValueError(f"unknown window {window!r}; choose one of {', '.join(WINDOWS)}")
    if df.empty or key == "MAX":
        return df
    last = df.index[-1]
    if key == "YTD":
        return df[df.index > pd.Timestamp(year=last.year - 1, month=12, day=31)]
    off = WINDOWS[key]
    assert off is not None
    return df[df.index > last - off]


def _thin(obj: pd.Series | pd.DataFrame, max_points: int) -> pd.Series | pd.DataFrame:
    """Keep every k-th row (always the last) so a chart gets <= ``max_points`` points.
    Cumulative/level series stay exact at the kept dates."""
    n = len(obj)
    if n <= max_points:
        return obj
    step = math.ceil(n / max_points)
    pos = np.unique(np.r_[np.arange(0, n, step), n - 1])
    return obj.iloc[pos]


def factor_stats(f: pd.Series, periods_per_year: int = TRADING_DAYS) -> dict[str, float]:
    """Annualised mean, vol, Sharpe, compounded/annualised return, max drawdown,
    and the Newey-West t-statistic of the mean premium."""
    x = f.dropna().to_numpy()
    n = len(x)
    if n < 2:
        return {"n_obs": float(n)}
    mu, sd = float(x.mean()), float(x.std(ddof=1))
    lrv = float(newey_west_lrv((x - mu)[:, None], nw_lag_rule(n))[0, 0])
    growth = float(np.prod(1.0 + x))
    return {
        "n_obs": float(n),
        "cumulative": growth - 1.0,
        "annualized": growth ** (periods_per_year / n) - 1.0 if growth > 0 else -1.0,
        "mean_ann": mu * periods_per_year,
        "vol_ann": sd * math.sqrt(periods_per_year),
        "sharpe": mu / sd * math.sqrt(periods_per_year) if sd > 0 else float("nan"),
        "t_stat_nw": mu / math.sqrt(lrv / n) if lrv > 0 else float("nan"),
        "max_drawdown": float(drawdown_series(pd.Series(x)).min()),
    }


def factor_library(
    factors: pd.DataFrame,
    window: str = "5Y",
    corr_window: int = TRADING_DAYS,
    max_points: int = 1500,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    """Dashboard payload (pandas objects) for the factor library.

    * ``cumulative``  -- ``prod(1 + f) - 1`` of each factor over ``window``.
    * ``drawdowns``   -- drawdown path of each factor over ``window``.
    * ``trailing``    -- per factor x horizon (1M..MAX): compounded return,
      annualised return (horizons >= 1Y only; NaN below), vol, Sharpe, NW t-stat.
    * ``rolling_corr`` -- rolling ``corr_window``-day correlations of every
      factor pair over ``window`` (thinned to ``max_points``).
    * ``corr_window_matrix`` / ``corr_full_matrix`` -- correlation matrices.
    * ``full_sample`` -- since-inception statistics.
    """
    cols = [c for c in factors.columns if c != "RF"]
    f = factors[cols].dropna(how="any").astype(float)
    if len(f) < 30:
        raise ValueError(f"only {len(f)} factor observations")
    w = window_slice(f, window)
    cum = (1.0 + w).cumprod() - 1.0
    dd = pd.DataFrame({c: drawdown_series(w[c]).to_numpy() for c in cols}, index=w.index)

    rows = []
    for h in TRAILING:
        s = window_slice(f, h)
        for c in cols:
            st = factor_stats(s[c], periods_per_year)
            if h in SUB_ANNUAL:
                st["annualized"] = float("nan")
            rows.append({"factor": c, "horizon": h, "start": s.index[0], "end": s.index[-1], **st})
    trailing = pd.DataFrame(rows)

    # Rolling pairwise correlations: computed on data starting corr_window-1 rows
    # before the window so the first plotted value is a full window.
    start_pos = max(0, f.index.get_indexer([w.index[0]])[0] - corr_window + 1)
    ext = f.iloc[start_pos:]
    pairs: dict[str, pd.Series] = {}
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            pairs[f"{a} / {b}"] = ext[a].rolling(corr_window).corr(ext[b])
    rc = pd.DataFrame(pairs).loc[w.index[0]:].dropna(how="all")

    return {
        "factors": cols,
        "window": window.upper(),
        "start": w.index[0], "end": w.index[-1],
        "cumulative": _thin(cum, max_points),
        "drawdowns": _thin(dd, max_points),
        "max_drawdown_window": dd.min(),
        "trailing": trailing,
        "rolling_corr": _thin(rc, max_points),
        "corr_window_matrix": w.corr(),
        "corr_full_matrix": f.corr(),
        "full_sample": {c: factor_stats(f[c], periods_per_year) for c in cols},
        "first_date": f.index[0], "last_date": f.index[-1],
    }


__all__ = ["TRAILING", "WINDOWS", "factor_library", "factor_stats", "window_slice"]
