"""Performance statistics for daily strategy returns (self-contained).

Conventions
-----------
* Inputs are daily SIMPLE returns as decimals (``pd.Series`` / ``np.ndarray``).
* ``periods`` = sessions per year used to annualize (252 by default: the
  NYSE convention).
* Sharpe ratios are computed on EXCESS returns ``r_t - rf_t`` when a daily
  risk-free series is supplied, otherwise on raw returns (callers must say so).
* "Per-period" Sharpe (``sr = mean / std``, not annualized) is what the
  probabilistic / deflated Sharpe formulas require; annualized values are
  ``sr * sqrt(periods)`` (Lo 2002, "The Statistics of Sharpe Ratios", eq. 7
  -- exact for i.i.d. returns only).

References
----------
* Sharpe, W. F. (1994). The Sharpe Ratio. *Journal of Portfolio Management* 21(1).
* Sortino, F. & Price, L. (1994). Performance Measurement in a Downside Risk
  Framework. *Journal of Investing* 3(3).
* Bailey, D. H. & Lopez de Prado, M. (2012). The Sharpe Ratio Efficient
  Frontier. *Journal of Risk* 15(2) -- PSR and minimum track record length.
* Bailey, D. H. & Lopez de Prado, M. (2014). The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality.
  *Journal of Portfolio Management* 40(5).
* Magdon-Ismail, M. & Atiya, A. (2004). Maximum Drawdown. *Risk* 17(10).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

PERIODS = 252
EULER_GAMMA = 0.5772156649015329


def _arr(x: Any) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    return a[np.isfinite(a)]


def excess(r: pd.Series, rf: pd.Series | None) -> pd.Series:
    """``r_t - rf_t`` aligned on ``r``'s index (rf missing -> raw ``r``)."""
    if rf is None:
        return r
    return r - rf.reindex(r.index).fillna(0.0)


def cagr(r: Any, periods: int = PERIODS) -> float:
    """Compound annual growth rate ``prod(1+r)^(periods/n) - 1``."""
    a = _arr(r)
    if len(a) == 0:
        return float("nan")
    growth = float(np.prod(1.0 + a))
    if growth <= 0:
        return -1.0
    return growth ** (periods / len(a)) - 1.0


def total_return(r: Any) -> float:
    a = _arr(r)
    return float(np.prod(1.0 + a) - 1.0) if len(a) else float("nan")


def ann_vol(r: Any, periods: int = PERIODS) -> float:
    """Annualized volatility ``std(r, ddof=1) * sqrt(periods)``."""
    a = _arr(r)
    return float(np.std(a, ddof=1) * math.sqrt(periods)) if len(a) > 1 else float("nan")


def sharpe_per_period(r: Any) -> float:
    """Non-annualized Sharpe ``mean(r) / std(r, ddof=1)`` of (excess) returns."""
    a = _arr(r)
    if len(a) < 2:
        return float("nan")
    sd = float(np.std(a, ddof=1))
    return float(np.mean(a)) / sd if sd > 0 else float("nan")


def sharpe(r: Any, periods: int = PERIODS) -> float:
    """Annualized Sharpe ratio ``sqrt(periods) * mean / std`` (Sharpe 1994)."""
    s = sharpe_per_period(r)
    return s * math.sqrt(periods) if math.isfinite(s) else float("nan")


def sortino(r: Any, periods: int = PERIODS, mar: float = 0.0) -> float:
    """Sortino ratio: ``sqrt(periods) * mean(r - mar) / DD``, downside deviation
    ``DD = sqrt(mean(min(r - mar, 0)^2))`` over ALL observations
    (Sortino & Price 1994)."""
    a = _arr(r) - mar
    if len(a) < 2:
        return float("nan")
    dd = math.sqrt(float(np.mean(np.minimum(a, 0.0) ** 2)))
    return float(np.mean(a)) / dd * math.sqrt(periods) if dd > 0 else float("nan")


def equity_curve(r: pd.Series, start: float = 1.0) -> pd.Series:
    """Wealth index ``start * cumprod(1 + r)``."""
    return start * (1.0 + r.fillna(0.0)).cumprod()


def drawdown_series(r: pd.Series) -> pd.Series:
    """Drawdown ``D_t = W_t / max_{s<=t} W_s - 1`` (<= 0) of the wealth index."""
    w = equity_curve(r)
    peak = np.maximum.accumulate(np.r_[1.0, w.to_numpy()])[1:]
    return pd.Series(w.to_numpy() / peak - 1.0, index=r.index, name="drawdown")


def max_drawdown(r: Any) -> float:
    """Maximum drawdown ``min_t D_t`` (a negative number)."""
    s = r if isinstance(r, pd.Series) else pd.Series(_arr(r))
    if len(s) == 0:
        return float("nan")
    return float(drawdown_series(s).min())


def drawdown_table(r: pd.Series, top: int = 5) -> list[dict[str, Any]]:
    """The ``top`` deepest drawdown episodes: peak, trough, recovery (None if
    not recovered), depth, and lengths in sessions (peak->trough, peak->recovery)."""
    dd = drawdown_series(r)
    vals = dd.to_numpy()
    idx = dd.index
    episodes: list[dict[str, Any]] = []
    i, n = 0, len(vals)
    while i < n:
        if vals[i] < 0:
            start = i  # first session under water; peak is the session before
            j = i
            while j < n and vals[j] < 0:
                j += 1
            seg = vals[start:j]
            trough = start + int(np.argmin(seg))
            peak_pos = start - 1
            episodes.append({
                "peak": idx[peak_pos] if peak_pos >= 0 else idx[start],
                "trough": idx[trough],
                "recovery": idx[j] if j < n else None,
                "depth": float(seg.min()),
                "sessions_to_trough": int(trough - peak_pos),
                "sessions_to_recovery": int(j - peak_pos) if j < n else None,
            })
            i = j
        else:
            i += 1
    episodes.sort(key=lambda e: e["depth"])
    return episodes[:top]


def calmar(r: Any, periods: int = PERIODS) -> float:
    """Calmar ratio ``CAGR / |MaxDD|`` over the full sample."""
    mdd = max_drawdown(r)
    return cagr(r, periods) / abs(mdd) if mdd and mdd < 0 else float("nan")


def moments(r: Any) -> tuple[float, float]:
    """(skewness, NON-excess kurtosis) -- the gamma_3, gamma_4 of the PSR formula."""
    a = _arr(r)
    if len(a) < 4:
        return float("nan"), float("nan")
    return float(stats.skew(a, bias=False)), float(stats.kurtosis(a, fisher=False, bias=False))


def probabilistic_sharpe(sr: float, n: int, skew: float, kurt: float, sr_benchmark: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012, eq. 11)::

        PSR(SR*) = Phi( (SR - SR*) sqrt(n - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2) )

    with PER-PERIOD Sharpe ratios, g3 = skewness and g4 = (non-excess) kurtosis
    of the returns. The probability that the true Sharpe exceeds ``SR*``.
    """
    if not all(math.isfinite(x) for x in (sr, skew, kurt, sr_benchmark)) or n < 2:
        return float("nan")
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return float("nan")
    return float(stats.norm.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)))


def min_track_record_length(sr: float, skew: float, kurt: float, sr_benchmark: float = 0.0,
                            confidence: float = 0.95) -> float:
    """Minimum track record length (Bailey & Lopez de Prado 2012, eq. 13), in
    periods, for ``PSR(SR*) >= confidence``::

        MinTRL = 1 + (1 - g3 SR + (g4 - 1)/4 SR^2) (z_conf / (SR - SR*))^2

    Infinite when ``SR <= SR*``."""
    if not all(math.isfinite(x) for x in (sr, skew, kurt)):
        return float("nan")
    if sr <= sr_benchmark:
        return float("inf")
    z = stats.norm.ppf(confidence)
    return float(1.0 + (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) * (z / (sr - sr_benchmark)) ** 2)


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """Expected maximum of ``N`` i.i.d. Sharpe estimates with zero mean and
    variance ``V`` (Bailey & Lopez de Prado 2014, eq. 2; false strategy theorem)::

        E[max SR] ~ sqrt(V) [ (1 - gamma) Phi^-1(1 - 1/N) + gamma Phi^-1(1 - 1/(N e)) ]

    gamma = Euler-Mascheroni constant."""
    if n_trials < 2 or not math.isfinite(var_sr) or var_sr <= 0:
        return 0.0
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(var_sr) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def deflated_sharpe(sr: float, n: int, skew: float, kurt: float, n_trials: int, var_sr: float) -> dict[str, float]:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014): the PSR evaluated
    at the benchmark ``SR0 = E[max SR]`` of ``n_trials`` trials whose per-period
    Sharpe estimates have cross-sectional variance ``var_sr``."""
    sr0 = expected_max_sharpe(n_trials, var_sr)
    return {"dsr": probabilistic_sharpe(sr, n, skew, kurt, sr0), "sr0_per_period": sr0,
            "sr0_annualized": sr0 * math.sqrt(PERIODS), "n_trials": n_trials, "var_sr": var_sr}


def rolling_sharpe(r: pd.Series, window: int = 252, periods: int = PERIODS) -> pd.Series:
    """Annualized rolling Sharpe over ``window`` sessions (NaN until full)."""
    m = r.rolling(window, min_periods=window).mean()
    s = r.rolling(window, min_periods=window).std(ddof=1)
    return (m / s.where(s > 0) * math.sqrt(periods)).rename("rolling_sharpe")


def rolling_vol(r: pd.Series, window: int = 63, periods: int = PERIODS) -> pd.Series:
    return (r.rolling(window, min_periods=window).std(ddof=1) * math.sqrt(periods)).rename("rolling_vol")


def relative_stats(r: pd.Series, b: pd.Series, rf: pd.Series | None = None,
                   periods: int = PERIODS) -> dict[str, float]:
    """CAPM regression of strategy on benchmark excess returns and active stats.

    ``r - rf = alpha + beta (b - rf) + e``; alpha annualized ``alpha * periods``;
    tracking error ``std(r - b) sqrt(periods)``; information ratio
    ``mean(r - b) / std(r - b) sqrt(periods)`` (Grinold & Kahn 2000);
    up/down capture = mean strategy return on benchmark up/down days divided by
    the mean benchmark return on those days.
    """
    df = pd.concat([r.rename("r"), b.rename("b")], axis=1).dropna()
    if len(df) < 20:
        return {}
    ex_r = excess(df["r"], rf).to_numpy()
    ex_b = excess(df["b"], rf).to_numpy()
    vb = float(np.var(ex_b, ddof=1))
    beta = float(np.cov(ex_r, ex_b, ddof=1)[0, 1] / vb) if vb > 0 else float("nan")
    alpha = float(np.mean(ex_r) - beta * np.mean(ex_b))
    active = (df["r"] - df["b"]).to_numpy()
    te = float(np.std(active, ddof=1))
    up = df["b"] > 0
    down = df["b"] < 0
    return {
        "beta": beta,
        "alpha_ann": alpha * periods,
        "correlation": float(np.corrcoef(df["r"], df["b"])[0, 1]),
        "tracking_error": te * math.sqrt(periods),
        "information_ratio": float(np.mean(active)) / te * math.sqrt(periods) if te > 0 else float("nan"),
        "up_capture": float(df["r"][up].mean() / df["b"][up].mean()) if up.any() else float("nan"),
        "down_capture": float(df["r"][down].mean() / df["b"][down].mean()) if down.any() else float("nan"),
    }


def summary(r: pd.Series, rf: pd.Series | None = None, periods: int = PERIODS) -> dict[str, float]:
    """Headline statistics of a daily return series (``rf`` for Sharpe/Sortino)."""
    r = r.dropna()
    ex = excess(r, rf)
    sr_pp = sharpe_per_period(ex)
    sk, ku = moments(ex)
    n = len(r)
    mdd = max_drawdown(r)
    return {
        "observations": n,
        "years": n / periods,
        "total_return": total_return(r),
        "cagr": cagr(r, periods),
        "ann_vol": ann_vol(r, periods),
        "sharpe": sr_pp * math.sqrt(periods) if math.isfinite(sr_pp) else float("nan"),
        "sortino": sortino(ex, periods),
        "max_drawdown": mdd,
        "calmar": calmar(r, periods),
        "skew": sk,
        "kurtosis": ku,
        "hit_rate": float((r > 0).mean()) if n else float("nan"),
        "best_day": float(r.max()) if n else float("nan"),
        "worst_day": float(r.min()) if n else float("nan"),
        "var_95_hist": float(-np.quantile(r, 0.05)) if n else float("nan"),
        "cvar_95_hist": float(-r[r <= np.quantile(r, 0.05)].mean()) if n else float("nan"),
        "psr_vs_0": probabilistic_sharpe(sr_pp, n, sk, ku, 0.0),
        "min_track_record_years_95": min_track_record_length(sr_pp, sk, ku) / periods,
    }


def calendar_returns(r: pd.Series) -> pd.Series:
    """Compounded calendar-year returns (partial first/last years included)."""
    g = (1.0 + r.dropna()).groupby(r.dropna().index.year).prod() - 1.0
    g.index.name = "year"
    return g


def monthly_returns(r: pd.Series) -> pd.DataFrame:
    """Year x month table of compounded monthly returns."""
    s = r.dropna()
    m = (1.0 + s).groupby([s.index.year, s.index.month]).prod() - 1.0
    tab = m.unstack()
    tab.index.name = "year"
    tab.columns = [f"{int(c):02d}" for c in tab.columns]
    return tab
