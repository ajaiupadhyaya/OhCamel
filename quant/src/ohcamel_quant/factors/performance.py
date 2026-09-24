"""Performance analytics for a daily return series (pure functions).

Conventions
-----------
* Inputs are simple (arithmetic) DECIMAL returns per period, indexed by date.
* ``P`` = periods per year (252 for daily data). Arithmetic annualisation
  (``mean * P``, ``std * sqrt(P)``) is used for means/vols/Sharpe; geometric
  compounding for total return / CAGR.
* ``rf`` is the per-period risk-free return aligned on the same dates. When it
  is ``None`` it is treated as 0 (callers must say so in their notes).
* Losses (VaR, CVaR, drawdowns) are reported with their natural sign stated
  per function.

References are given per function (author, year, title).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy import stats

from .hac import newey_west_lrv, nw_lag_rule

TRADING_DAYS = 252
EULER_MASCHERONI = 0.5772156649015329
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

Rebalance = Literal["daily", "weekly", "monthly", "quarterly", "annual", "none"]
_REBAL_FREQ = {"weekly": "W", "monthly": "M", "quarterly": "Q", "annual": "Y"}


# =============================================================== helpers
def _as_series(r: pd.Series | np.ndarray, name: str = "r") -> pd.Series:
    if isinstance(r, pd.Series):
        return r.astype(float)
    return pd.Series(np.asarray(r, dtype=float), name=name)


def _rf_aligned(r: pd.Series, rf: pd.Series | float | None) -> pd.Series:
    if rf is None:
        return pd.Series(0.0, index=r.index)
    if isinstance(rf, (int, float)):
        return pd.Series(float(rf), index=r.index)
    out = rf.reindex(r.index)
    if out.isna().any():
        raise ValueError(f"risk-free series is missing {int(out.isna().sum())} of the return dates")
    return out.astype(float)


def _safe_div(a: float, b: float) -> float:
    return a / b if b not in (0.0, -0.0) and np.isfinite(b) else float("nan")


# ====================================================== portfolio returns
def _holding_groups(index: pd.Index, rebalance: str) -> np.ndarray:
    """Integer label of the holding period each row belongs to (a new label
    starts at the first session after each reset)."""
    if rebalance == "daily":
        return np.arange(len(index))
    if rebalance == "none":
        return np.zeros(len(index), dtype=int)
    if rebalance in _REBAL_FREQ:
        return pd.factorize(pd.DatetimeIndex(index).to_period(_REBAL_FREQ[rebalance]))[0]
    raise ValueError(f"unknown rebalance {rebalance!r}")


def portfolio_returns(
    returns: pd.DataFrame,
    weights: dict[str, float] | pd.Series,
    rebalance: Rebalance = "daily",
    rf: pd.Series | None = None,
    return_weights: bool = False,
) -> pd.Series | tuple[pd.Series, pd.DataFrame]:
    """Portfolio simple returns with correct weight drift.

    Let ``w`` be target weights (decimals of equity; negative = short) and
    ``c = 1 - sum(w)`` the cash weight, which earns ``rf`` (0 when ``rf`` is
    None). Within a holding period that starts at a rebalance with value 1,
    position values evolve as ``V_{i,t} = w_i prod_{s<=t}(1 + r_{i,s})`` and
    ``V_{c,t} = c prod(1 + rf_s)``; the portfolio return is
    ``R_t = V_t / V_{t-1} - 1`` with ``V_t = sum_i V_{i,t} + V_{c,t}``.

    * ``'daily'``  -- weights reset every period: ``R_t = w' r_t + c rf_t``.
    * ``'weekly'|'monthly'|'quarterly'|'annual'`` -- reset to ``w`` at the
      close of the last session of each calendar period, drift in between.
    * ``'none'``   -- buy-and-hold: one holding period over the whole sample,
      identical to holding ``w_i / P_{i,0}`` shares of each asset.

    With ``return_weights=True`` also returns the weights held over each
    period, ``V_{i,t-1} / V_{t-1}`` (post-rebalance, i.e. equal to the target on
    reset dates), for every asset and ``CASH``.
    """
    w = pd.Series(weights, dtype=float)
    missing = [t for t in w.index if t not in returns.columns]
    if missing:
        raise ValueError(f"no returns for {', '.join(missing)}")
    r = returns[list(w.index)].astype(float)
    if r.isna().any().any():
        raise ValueError("returns contain NaN; align/clean them before building the portfolio")
    rfs = _rf_aligned(r.iloc[:, 0], rf).to_numpy()
    cash = 1.0 - float(w.sum())
    rv = r.to_numpy()
    wv = w.to_numpy()

    if rebalance == "daily":
        rp = rv @ wv + cash * rfs
        out = pd.Series(rp, index=r.index, name="portfolio")
        if not return_weights:
            return out
        wt = pd.DataFrame(np.tile(np.append(wv, cash), (len(r), 1)), index=r.index,
                          columns=[*w.index, "CASH"])
        return out, wt

    group = _holding_groups(r.index, rebalance)

    # Growth of each leg since the start of its holding period (cumprod within group).
    growth = np.empty((len(r), len(wv) + 1))
    legs = np.column_stack([rv, rfs])
    starts = np.flatnonzero(np.r_[True, group[1:] != group[:-1]])
    ends = np.r_[starts[1:], len(r)]
    for s, e in zip(starts, ends, strict=True):
        growth[s:e] = np.cumprod(1.0 + legs[s:e], axis=0)
    wa = np.append(wv, cash)
    pos_end = growth * wa                      # position values at t (period starts at 1)
    v_end = pos_end.sum(axis=1)
    pos_start = np.empty_like(pos_end)
    pos_start[1:] = pos_end[:-1]
    pos_start[starts] = wa                     # rebalanced back to target at period start
    v_start = pos_start.sum(axis=1)
    rp = v_end / v_start - 1.0
    out = pd.Series(rp, index=r.index, name="portfolio")
    if not return_weights:
        return out
    wt = pd.DataFrame(pos_start / v_start[:, None], index=r.index, columns=[*w.index, "CASH"])
    return out, wt


def turnover(weights_path: pd.DataFrame, target: dict[str, float] | pd.Series,
             returns: pd.DataFrame, rf: pd.Series | None = None,
             rebalance: Rebalance | None = None) -> pd.Series:
    """One-way turnover at each rebalance: ``0.5 * sum_i |w_i - w_{i,t}^{drift}|``.

    ``weights_path`` is from :func:`portfolio_returns` (weights held over each
    period); the previous period's weights are drifted by that period's
    returns and compared with the target on the dates where a reset occurred.
    Pass the ``rebalance`` schedule used to build ``weights_path`` so reset
    dates come from the calendar; without it they are inferred as the dates
    whose held weights equal the target, which over-counts resets whenever
    weights cannot drift (e.g. a single fully-invested asset).
    """
    w = pd.Series(target, dtype=float)
    tgt = pd.Series({**w.to_dict(), "CASH": 1.0 - float(w.sum())})
    r = returns[list(w.index)].astype(float).copy()
    r["CASH"] = _rf_aligned(r.iloc[:, 0], rf)
    grown = weights_path[tgt.index] * (1.0 + r[tgt.index])
    drifted = grown.div(grown.sum(axis=1), axis=0).shift(1)
    if rebalance is None:
        reset = (weights_path[tgt.index] - tgt).abs().sum(axis=1) < 1e-12
    else:
        g = _holding_groups(weights_path.index, rebalance)
        reset = pd.Series(np.r_[True, g[1:] != g[:-1]], index=weights_path.index)
    to = 0.5 * (drifted.sub(tgt, axis=1)).abs().sum(axis=1)
    return to[reset].iloc[1:].rename("turnover")


# ======================================================== basic statistics
def total_return(r: pd.Series) -> float:
    """``prod(1 + r_t) - 1``."""
    return float(np.prod(1.0 + _as_series(r).to_numpy()) - 1.0)


def cagr(r: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Compound annual growth rate ``(prod(1 + r_t))^(P/T) - 1``."""
    x = _as_series(r).to_numpy()
    if len(x) == 0:
        return float("nan")
    g = float(np.prod(1.0 + x))
    return g ** (periods_per_year / len(x)) - 1.0 if g > 0 else -1.0


def annualized_vol(r: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """``std(r, ddof=1) * sqrt(P)``."""
    return float(_as_series(r).std(ddof=1) * math.sqrt(periods_per_year))


def sharpe_ratio(r: pd.Series, rf: pd.Series | float | None = None,
                 periods_per_year: int = TRADING_DAYS) -> float:
    """Annualised Sharpe (1966, 1994) ratio ``mean(r - rf) / std(r - rf) * sqrt(P)``."""
    r = _as_series(r)
    ex = r - _rf_aligned(r, rf)
    return _safe_div(float(ex.mean()), float(ex.std(ddof=1))) * math.sqrt(periods_per_year)


def sortino_ratio(r: pd.Series, mar: pd.Series | float | None = None,
                  periods_per_year: int = TRADING_DAYS) -> float:
    """Sortino & Price (1994), "Performance Measurement in a Downside Risk Framework".

    ``mean(r - MAR) * P / (DD * sqrt(P))`` with downside deviation
    ``DD = sqrt(mean(min(r_t - MAR_t, 0)^2))`` over ALL periods.
    ``mar`` defaults to 0; pass the risk-free series to use rf as MAR.
    """
    r = _as_series(r)
    ex = (r - _rf_aligned(r, mar)).to_numpy()
    dd = math.sqrt(float(np.mean(np.minimum(ex, 0.0) ** 2)))
    return _safe_div(float(ex.mean()) * periods_per_year, dd * math.sqrt(periods_per_year))


def omega_ratio(r: pd.Series, threshold_annual: float = 0.0,
                periods_per_year: int = TRADING_DAYS) -> float:
    """Keating & Shadwick (2002), "A Universal Performance Measure".

    ``Omega(L) = sum max(r_t - L, 0) / sum max(L - r_t, 0)`` with per-period
    threshold ``L = (1 + L_annual)^(1/P) - 1``.
    """
    x = _as_series(r).to_numpy()
    lvl = (1.0 + threshold_annual) ** (1.0 / periods_per_year) - 1.0
    up = float(np.maximum(x - lvl, 0.0).sum())
    dn = float(np.maximum(lvl - x, 0.0).sum())
    return _safe_div(up, dn)


# ================================================================ drawdowns
def equity_curve(r: pd.Series) -> pd.Series:
    """Wealth index ``W_t = prod_{s<=t}(1 + r_s)`` (starts from 1 before the first return)."""
    return (1.0 + _as_series(r)).cumprod().rename("equity")


def drawdown_series(r: pd.Series) -> pd.Series:
    """``DD_t = W_t / max(1, max_{s<=t} W_s) - 1`` (<= 0)."""
    w = equity_curve(r)
    peak = np.maximum.accumulate(np.maximum(w.to_numpy(), 1.0))
    return pd.Series(w.to_numpy() / peak - 1.0, index=w.index, name="drawdown")


def drawdown_episodes(r: pd.Series, top: int | None = 5) -> pd.DataFrame:
    """Drawdown episodes, deepest first.

    An episode starts at a high-water mark (``peak``: the last date with
    ``DD = 0`` before the decline, or the first date if the series opens in a
    decline), reaches ``trough`` (min DD), and ends at ``recovery`` -- the
    first date with ``DD = 0`` again (``NaT`` if still under water).
    ``length`` counts periods peak->recovery (or peak->last date),
    ``decline`` peak->trough, ``recovery_periods`` trough->recovery.
    """
    dd = drawdown_series(r)
    v = dd.to_numpy()
    idx = dd.index
    under = v < 0
    rows: list[dict[str, Any]] = []
    i, n = 0, len(v)
    while i < n:
        if not under[i]:
            i += 1
            continue
        j = i
        while j < n and under[j]:
            j += 1
        seg = v[i:j]
        t_rel = int(np.argmin(seg))
        peak_pos = i - 1 if i > 0 else 0
        trough_pos = i + t_rel
        rec_pos = j if j < n else None
        end_pos = rec_pos if rec_pos is not None else n - 1
        rows.append({
            "peak": idx[peak_pos], "trough": idx[trough_pos],
            "recovery": idx[rec_pos] if rec_pos is not None else pd.NaT,
            "depth": float(seg[t_rel]),
            "length": end_pos - peak_pos,
            "decline": trough_pos - peak_pos,
            "recovery_periods": (rec_pos - trough_pos) if rec_pos is not None else None,
            "recovered": rec_pos is not None,
        })
        i = j
    df = pd.DataFrame(rows, columns=["peak", "trough", "recovery", "depth", "length", "decline",
                                     "recovery_periods", "recovered"])
    df = df.sort_values("depth", kind="stable").reset_index(drop=True)
    return df.head(top) if top else df


def max_drawdown(r: pd.Series) -> float:
    """Maximum drawdown ``min_t DD_t`` (a negative decimal)."""
    dd = drawdown_series(r)
    return float(dd.min()) if len(dd) else float("nan")


def ulcer_index(r: pd.Series) -> float:
    """Martin & McCann (1989), *The Investor's Guide to Fidelity Funds*: ``sqrt(mean(DD_t^2))`` (decimal)."""
    dd = drawdown_series(r).to_numpy()
    return float(np.sqrt(np.mean(dd**2)))


def calmar_ratio(r: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Young (1991), "Calmar Ratio: A Smoother Tool": ``CAGR / |MDD|`` over the full sample."""
    return _safe_div(cagr(r, periods_per_year), abs(max_drawdown(r)))


# ==================================================== distribution & tails
def historical_var_cvar(r: pd.Series, level: float = 0.95) -> tuple[float, float]:
    """Historical VaR and CVaR (expected shortfall) as POSITIVE loss fractions.

    ``VaR = -Q_{1-level}(r)`` (linear-interpolated empirical quantile),
    ``CVaR = -mean(r_t | r_t <= -VaR)`` (Acerbi & Tasche 2002, "On the
    coherence of expected shortfall").
    """
    x = _as_series(r).dropna().to_numpy()
    q = float(np.quantile(x, 1.0 - level))
    tail = x[x <= q]
    return -q, -float(tail.mean())


def tail_ratio(r: pd.Series) -> float:
    """``|Q_0.95(r)| / |Q_0.05(r)|`` -- right-tail size relative to left tail."""
    x = _as_series(r).dropna().to_numpy()
    return _safe_div(abs(float(np.quantile(x, 0.95))), abs(float(np.quantile(x, 0.05))))


def distribution_stats(r: pd.Series) -> dict[str, float]:
    """Sample skewness and excess kurtosis (bias-corrected, Joanes & Gill 1998
    type G1/G2) and the Jarque & Bera (1980) normality test
    ``JB = T/6 (S^2 + K_ex^2 / 4)`` ~ chi2(2) (population moments)."""
    x = _as_series(r).dropna().to_numpy()
    jb = stats.jarque_bera(x)
    return {
        "skew": float(stats.skew(x, bias=False)),
        "excess_kurtosis": float(stats.kurtosis(x, fisher=True, bias=False)),
        "jarque_bera": float(jb.statistic),
        "jarque_bera_p": float(jb.pvalue),
    }


def monthly_returns(r: pd.Series) -> pd.Series:
    """Calendar-month compounded returns ``prod(1 + r_t) - 1`` (month-end index)."""
    s = _as_series(r)
    m = (1.0 + s).groupby(s.index.to_period("M")).prod() - 1.0
    m.index = m.index.to_timestamp(how="end").normalize()
    return m.rename("monthly")


def monthly_table(r: pd.Series) -> pd.DataFrame:
    """Year x month table of compounded returns plus an ``Annual`` column
    (calendar-year compounded; partial first/last years are partial-year returns)."""
    s = _as_series(r)
    per = s.index.to_period("M")
    m = (1.0 + s).groupby(per).prod() - 1.0
    df = pd.DataFrame({"year": m.index.year, "month": m.index.month, "ret": m.to_numpy()})
    tab = df.pivot(index="year", columns="month", values="ret").reindex(columns=range(1, 13))
    tab.columns = list(MONTH_NAMES)
    annual = (1.0 + s).groupby(s.index.year).prod() - 1.0
    tab["Annual"] = annual.reindex(tab.index).to_numpy()
    tab.index.name = "year"
    return tab


def win_loss_stats(r: pd.Series) -> dict[str, float]:
    """Hit rate ``#{r_t > 0} / #{r_t != 0}``, average win/loss and payoff ratio."""
    x = _as_series(r).dropna().to_numpy()
    wins, losses = x[x > 0], x[x < 0]
    nz = len(wins) + len(losses)
    avg_w = float(wins.mean()) if len(wins) else float("nan")
    avg_l = float(losses.mean()) if len(losses) else float("nan")
    return {
        "hit_rate": len(wins) / nz if nz else float("nan"),
        "avg_win": avg_w, "avg_loss": avg_l,
        "payoff_ratio": _safe_div(avg_w, abs(avg_l)),
        "profit_factor": _safe_div(float(wins.sum()), abs(float(losses.sum()))),
    }


# ===================================================== Sharpe inference (Lo)
def sharpe_inference(
    r: pd.Series,
    rf: pd.Series | float | None = None,
    periods_per_year: int = TRADING_DAYS,
    lags: int | None = None,
) -> dict[str, float]:
    """Standard errors of the Sharpe ratio. Lo (2002), "The Statistics of
    Sharpe Ratios", *Financial Analysts Journal* 58(4), 36-52.

    With per-period ``SR = mu / sigma`` of excess returns over ``T`` periods:

    * IID normal (Lo eq. 5): ``Var(SR_hat) = (1 + SR^2 / 2) / T``.
    * IID non-normal (Mertens 2002; Opdyke 2007):
      ``(1 + SR^2/2 - g3 SR + (g4 - 3)/4 SR^2) / T``.
    * GMM / HAC (Lo eq. 9-10): moments ``psi_t = (x_t - mu, (x_t - mu)^2 - sigma^2)``,
      ``Var(SR_hat) = grad' S grad / T`` with ``grad = (1/sigma, -mu / (2 sigma^3))``
      and ``S`` the Newey-West long-run covariance -- robust to serial
      correlation, heteroskedasticity and fat tails.

    Standard errors are annualised by ``sqrt(P)`` alongside ``SR``.

    Autocorrelation-adjusted annualisation (Lo eq. 11): with ``rho_k`` the
    return autocorrelations,
    ``SR(q) = eta(q) SR,  eta(q) = q / sqrt(q + 2 sum_{k=1}^{q-1} (q - k) rho_k)``
    where ``q = P``; ``rho_k`` is estimated for ``k <= lags`` (NW rule by
    default) and set to 0 beyond, since high-order sample autocorrelations
    are noise. ``IID`` scaling is ``eta = sqrt(q)``.
    """
    r = _as_series(r)
    x = (r - _rf_aligned(r, rf)).dropna().to_numpy()
    t = len(x)
    q = periods_per_year
    mu = float(x.mean())
    sig = float(x.std(ddof=1))
    sr = _safe_div(mu, sig)
    g3 = float(stats.skew(x))
    g4 = float(stats.kurtosis(x, fisher=False))
    lag = nw_lag_rule(t) if lags is None else int(lags)
    if t < 3 or not np.isfinite(sig) or sig <= max(1e-10 * abs(mu), 1e-15):
        # Zero-variance (e.g. an all-cash portfolio) or too-short sample: the
        # Sharpe ratio and its standard errors are undefined.
        nan = float("nan")
        return {"sharpe": nan, "sharpe_per_period": nan, "se_iid": nan, "se_nonnormal": nan,
                "se_hac": nan, "ci95_lower": nan, "ci95_upper": nan, "t_stat_hac": nan,
                "sharpe_autocorr_adjusted": nan, "eta": nan, "autocorr_lags": 0.0,
                "rho_1": nan, "n_obs": float(t)}

    v_iid = (1.0 + 0.5 * sr**2) / t
    v_nn = (1.0 + 0.5 * sr**2 - g3 * sr + (g4 - 3.0) / 4.0 * sr**2) / t
    xc = x - mu
    psi = np.column_stack([xc, xc**2 - float(np.mean(xc**2))])
    s = newey_west_lrv(psi, lag)
    grad = np.array([1.0 / sig, -mu / (2.0 * sig**3)])
    v_hac = float(grad @ s @ grad) / t

    rho = np.array([float(np.corrcoef(x[k:], x[:-k])[0, 1]) for k in range(1, min(lag, q - 1, t - 2) + 1)])
    ks = np.arange(1, len(rho) + 1)
    denom = q + 2.0 * float(np.sum((q - ks) * rho))
    eta = q / math.sqrt(denom) if denom > 0 else float("nan")
    ann = math.sqrt(q)
    se_hac_ann = math.sqrt(max(v_hac, 0.0)) * ann
    sr_ann = sr * ann
    return {
        "sharpe": sr_ann,
        "sharpe_per_period": sr,
        "se_iid": math.sqrt(v_iid) * ann,
        "se_nonnormal": math.sqrt(max(v_nn, 0.0)) * ann,
        "se_hac": se_hac_ann,
        "ci95_lower": sr_ann - 1.959963984540054 * se_hac_ann,
        "ci95_upper": sr_ann + 1.959963984540054 * se_hac_ann,
        "t_stat_hac": _safe_div(sr_ann, se_hac_ann),
        "sharpe_autocorr_adjusted": sr * eta,
        "eta": eta,
        "autocorr_lags": float(len(rho)),
        "rho_1": float(rho[0]) if len(rho) else float("nan"),
        "n_obs": float(t),
    }


# ======================================== PSR / MinTRL / DSR (Bailey & LdP)
def _sr_denominator(sr: float, skew: float, kurtosis: float) -> float:
    return 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr**2


def probabilistic_sharpe(sr: float, n_obs: int, sr_benchmark: float = 0.0,
                         skew: float = 0.0, kurtosis: float = 3.0) -> float:
    """Probabilistic Sharpe Ratio. Bailey & Lopez de Prado (2012), "The Sharpe
    Ratio Efficient Frontier", *Journal of Risk* 15(2), eq. (6):

        PSR(SR*) = Phi( (SR_hat - SR*) sqrt(T - 1) / sqrt(1 - g3 SR_hat + (g4 - 1)/4 SR_hat^2) )

    All Sharpe ratios are PER PERIOD (not annualised); ``kurtosis`` is the
    raw (non-excess) kurtosis ``g4`` (3 for a normal).
    """
    d = _sr_denominator(sr, skew, kurtosis)
    if n_obs < 2 or d <= 0:
        return float("nan")
    return float(stats.norm.cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(d)))


def min_track_record_length(sr: float, sr_benchmark: float = 0.0, skew: float = 0.0,
                            kurtosis: float = 3.0, confidence: float = 0.95) -> float:
    """Minimum Track Record Length (Bailey & Lopez de Prado 2012, eq. 11):

        MinTRL = 1 + (1 - g3 SR + (g4 - 1)/4 SR^2) (z_{conf} / (SR - SR*))^2

    in periods; infinite when ``SR <= SR*``. Per-period Sharpe ratios.
    """
    if sr <= sr_benchmark:
        return float("inf")
    z = float(stats.norm.ppf(confidence))
    return 1.0 + _sr_denominator(sr, skew, kurtosis) * (z / (sr - sr_benchmark)) ** 2


def expected_max_sharpe(n_trials: int, sr_variance: float, sr_mean: float = 0.0) -> float:
    """Expected maximum of ``N`` independent trial Sharpe ratios (Bailey &
    Lopez de Prado 2014, eq. 2; the False Strategy Theorem):

        SR_0 = E[SR_n] + sqrt(V[SR_n]) ((1 - gamma) Phi^-1(1 - 1/N) + gamma Phi^-1(1 - 1/(N e)))

    with ``gamma`` the Euler-Mascheroni constant.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if sr_variance < 0:
        raise ValueError("sr_variance must be >= 0")
    if n_trials == 1:
        return sr_mean
    g = EULER_MASCHERONI
    z = (1.0 - g) * stats.norm.ppf(1.0 - 1.0 / n_trials) + g * stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return sr_mean + math.sqrt(sr_variance) * float(z)


@dataclass(frozen=True)
class DeflatedSharpe:
    dsr: float            # probability the true SR exceeds the expected max of N null trials
    sr: float             # per-period Sharpe used
    sr0: float            # per-period deflation benchmark (expected max SR)
    n_obs: int
    n_trials: int
    skew: float
    kurtosis: float

    def to_dict(self, periods_per_year: int | None = None) -> dict[str, float]:
        a = math.sqrt(periods_per_year) if periods_per_year else 1.0
        return {"dsr": self.dsr, "sharpe": self.sr * a, "sr0": self.sr0 * a, "n_obs": self.n_obs,
                "n_trials": self.n_trials, "skew": self.skew, "kurtosis": self.kurtosis}


def deflated_sharpe(
    returns: pd.Series | np.ndarray | None = None,
    *,
    n_trials: int,
    sr_variance: float,
    sr: float | None = None,
    n_obs: int | None = None,
    skew: float | None = None,
    kurtosis: float | None = None,
    periods_per_year: int | None = None,
    detail: bool = False,
) -> float | DeflatedSharpe:
    """Deflated Sharpe Ratio. Bailey & Lopez de Prado (2014), "The Deflated
    Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and
    Non-Normality", *Journal of Portfolio Management* 40(5), eq. (2)-(3):

        DSR = PSR(SR_0),   SR_0 = sqrt(V[SR_n]) ((1-gamma) Phi^-1(1 - 1/N) + gamma Phi^-1(1 - 1/(N e)))

    Either pass the selected strategy's ``returns`` (EXCESS returns per
    period; ``sr``, ``n_obs``, ``skew`` and raw ``kurtosis`` are estimated from
    them with population moments) or pass ``sr`` and ``n_obs`` (and
    optionally ``skew``/``kurtosis``, default normal 0/3) directly.

    Units: by default ``sr`` and ``sr_variance`` (variance across the N
    trials' Sharpe ratios) are PER PERIOD. If ``periods_per_year`` is given,
    both are taken as ANNUALISED and converted (``SR / sqrt(P)``,
    ``V / P``); ``returns``-derived SR is always per period.

    Returns the DSR probability (or a :class:`DeflatedSharpe` with
    ``detail=True``). ``n_trials = 1`` reduces to ``PSR(0)``.
    """
    scale = float(periods_per_year) if periods_per_year else 1.0
    if returns is not None:
        x = np.asarray(returns, dtype=float)
        x = x[np.isfinite(x)]
        sd = float(x.std(ddof=1))
        sr_pp = float(x.mean()) / sd if sd > 0 else float("nan")
        t = len(x)
        g3 = float(stats.skew(x)) if skew is None else skew
        g4 = float(stats.kurtosis(x, fisher=False)) if kurtosis is None else kurtosis
    else:
        if sr is None or n_obs is None:
            raise ValueError("pass either returns or both sr and n_obs")
        sr_pp = sr / math.sqrt(scale)
        t = int(n_obs)
        g3 = 0.0 if skew is None else skew
        g4 = 3.0 if kurtosis is None else kurtosis
    sr0 = expected_max_sharpe(n_trials, sr_variance / scale)
    dsr = probabilistic_sharpe(sr_pp, t, sr0, g3, g4)
    if detail:
        return DeflatedSharpe(dsr, sr_pp, sr0, t, n_trials, g3, g4)
    return dsr


# ============================================================ relative stats
def capture_ratios(r: pd.Series, b: pd.Series) -> dict[str, float]:
    """Up/down capture (Morningstar methodology): geometric mean return of the
    portfolio over periods where the benchmark is up (down), divided by the
    benchmark's geometric mean over the same periods.

        UC = [prod(1 + r_t | b_t > 0)^(1/n_up) - 1] / [prod(1 + b_t | b_t > 0)^(1/n_up) - 1]
    """
    out: dict[str, float] = {}
    for name, mask in (("up_capture", b > 0), ("down_capture", b < 0)):
        n = int(mask.sum())
        if n == 0:
            out[name] = float("nan")
            continue
        gp = float(np.prod(1.0 + r[mask].to_numpy())) ** (1.0 / n) - 1.0
        gb = float(np.prod(1.0 + b[mask].to_numpy())) ** (1.0 / n) - 1.0
        out[name] = _safe_div(gp, gb)
    return out


def relative_stats(
    r: pd.Series,
    b: pd.Series,
    rf: pd.Series | None = None,
    periods_per_year: int = TRADING_DAYS,
    lags: int | None = None,
) -> dict[str, Any]:
    """Benchmark-relative statistics.

    * CAPM regression ``r - rf = a + beta (b - rf) + e`` with Newey-West t-stats
      (Jensen 1968, "The Performance of Mutual Funds in the Period 1945-1964").
    * Tracking error ``TE = std(r - b) sqrt(P)``; information ratio
      ``IR = mean(r - b) P / TE`` (Grinold & Kahn 2000).
    * Treynor (1965) ratio ``mean(r - rf) P / beta``.
    * M2 = ``SR_p * sigma_b + mean(rf) P`` (Modigliani & Modigliani 1997,
      "Risk-Adjusted Performance", *JPM* 23(2)); ``m2_excess = M2 - mean(b) P``.
    * Up/down capture on daily data and on calendar-month compounded returns.
    * Correlation of simple returns.
    """
    from .models import factor_regression

    df = pd.concat([r.rename("r"), b.rename("b")], axis=1, join="inner").dropna()
    rr, bb = df["r"], df["b"]
    rfs = _rf_aligned(rr, rf)
    reg = factor_regression((rr - rfs).rename("y"), (bb - rfs).rename("benchmark").to_frame(),
                            lags=lags, periods_per_year=periods_per_year)
    beta = float(reg.params["benchmark"])
    active = rr - bb
    te = float(active.std(ddof=1)) * math.sqrt(periods_per_year)
    ex_mean_ann = float((rr - rfs).mean()) * periods_per_year
    sr_p = sharpe_ratio(rr, rfs, periods_per_year)
    sig_b = annualized_vol(bb, periods_per_year)
    rf_ann = float(rfs.mean()) * periods_per_year
    m2 = sr_p * sig_b + rf_ann
    cap_d = capture_ratios(rr, bb)
    cap_m = capture_ratios(monthly_returns(rr), monthly_returns(bb))
    return {
        "alpha_ann": reg.alpha_annualized,
        "alpha_t": reg.alpha_t,
        "alpha_p": float(reg.p_values["alpha"]),
        "beta": beta,
        "beta_t": float(reg.t_stats["benchmark"]),
        "beta_se": float(reg.std_errors["benchmark"]),
        "r2": reg.r2,
        "correlation": float(rr.corr(bb)),
        "tracking_error": te,
        "active_return_ann": float(active.mean()) * periods_per_year,
        "information_ratio": _safe_div(float(active.mean()) * periods_per_year, te),
        "treynor": _safe_div(ex_mean_ann, beta),
        "m2": m2,
        "m2_excess": m2 - float(bb.mean()) * periods_per_year,
        "up_capture": cap_m["up_capture"],
        "down_capture": cap_m["down_capture"],
        "up_capture_daily": cap_d["up_capture"],
        "down_capture_daily": cap_d["down_capture"],
        "benchmark_vol": sig_b,
        "benchmark_cagr": cagr(bb, periods_per_year),
        "benchmark_sharpe": sharpe_ratio(bb, rfs, periods_per_year),
        "nw_lags": reg.nw_lags,
        "n_obs": reg.n_obs,
    }


# ================================================================== rolling
def rolling_stats(
    r: pd.Series,
    window: int = TRADING_DAYS,
    b: pd.Series | None = None,
    rf: pd.Series | None = None,
    periods_per_year: int = TRADING_DAYS,
) -> pd.DataFrame:
    """Rolling-window statistics (all vectorised with pandas rolling moments).

    * ``return``: annualised compounded return ``exp(P/w * sum log(1 + r)) - 1``
    * ``vol``: ``std * sqrt(P)``
    * ``sharpe``: ``mean(r - rf) / std(r - rf) * sqrt(P)``
    * ``beta``: ``Cov(r, b) / Var(b)`` (when ``b`` is given; raw returns --
      with a near-constant rf the excess-return beta is numerically identical)
    """
    r = _as_series(r)
    rfs = _rf_aligned(r, rf)
    ex = r - rfs
    out = pd.DataFrame(index=r.index)
    out["return"] = np.expm1(np.log1p(r).rolling(window).sum() * periods_per_year / window)
    out["vol"] = r.rolling(window).std(ddof=1) * math.sqrt(periods_per_year)
    out["sharpe"] = ex.rolling(window).mean() / ex.rolling(window).std(ddof=1) * math.sqrt(periods_per_year)
    if b is not None:
        bb = b.reindex(r.index)
        out["beta"] = r.rolling(window).cov(bb) / bb.rolling(window).var()
    return out.iloc[window - 1:]


# ================================================================== report
def performance_report(
    r: pd.Series,
    benchmark: pd.Series | None = None,
    rf: pd.Series | None = None,
    periods_per_year: int = TRADING_DAYS,
    var_level: float = 0.95,
    omega_threshold: float = 0.0,
    top_drawdowns: int = 5,
    rolling_window: int = TRADING_DAYS,
    sr_benchmark: float = 0.0,
    psr_confidence: float = 0.95,
    n_trials: int = 1,
    sr_variance: float | None = None,
    nw_lags: int | None = None,
) -> dict[str, Any]:
    """Everything in this module for one return series.

    ``sr_benchmark`` (annualised) is the PSR / MinTRL hurdle; ``n_trials`` and
    ``sr_variance`` (annualised variance of the trial Sharpes) enable the
    Deflated Sharpe Ratio. ``rf`` defaults to 0 when None.
    """
    r = _as_series(r).dropna()
    if len(r) < 20:
        raise ValueError(f"only {len(r)} return observations; need at least 20")
    rfs = _rf_aligned(r, rf)
    ex = r - rfs
    p = periods_per_year
    mdd_eps = drawdown_episodes(r, top=None)
    longest = int(mdd_eps["length"].max()) if len(mdd_eps) else 0
    worst = mdd_eps.iloc[0] if len(mdd_eps) else None
    var_, cvar_ = historical_var_cvar(r, var_level)
    mret = monthly_returns(r)
    dist = distribution_stats(r)
    wl = win_loss_stats(r)
    sri = sharpe_inference(r, rfs, p, nw_lags)

    x = ex.to_numpy()
    sr_pp = sri["sharpe_per_period"]
    g3 = float(stats.skew(x))
    g4 = float(stats.kurtosis(x, fisher=False))
    srb_pp = sr_benchmark / math.sqrt(p)
    psr = probabilistic_sharpe(sr_pp, len(x), srb_pp, g3, g4)
    mintrl = min_track_record_length(sr_pp, srb_pp, g3, g4, psr_confidence)
    dsr: dict[str, Any] | None = None
    if n_trials > 1 and sr_variance is not None:
        d = deflated_sharpe(x, n_trials=n_trials, sr_variance=sr_variance / p, detail=True)
        assert isinstance(d, DeflatedSharpe)
        dsr = d.to_dict(p)

    summary: dict[str, Any] = {
        "start": r.index[0], "end": r.index[-1], "n_obs": len(r),
        "years": len(r) / p,
        "total_return": total_return(r),
        "cagr": cagr(r, p),
        "mean_ann": float(r.mean()) * p,
        "vol_ann": annualized_vol(r, p),
        "rf_ann": float(rfs.mean()) * p,
        "sharpe": sri["sharpe"],
        "sortino": sortino_ratio(r, rfs, p),
        "calmar": calmar_ratio(r, p),
        "omega": omega_ratio(r, omega_threshold, p),
        "omega_threshold_ann": omega_threshold,
        "max_drawdown": float(worst["depth"]) if worst is not None else 0.0,
        "max_drawdown_peak": worst["peak"] if worst is not None else None,
        "max_drawdown_trough": worst["trough"] if worst is not None else None,
        "max_drawdown_recovery": (worst["recovery"] if worst is not None and worst["recovered"] else None),
        "max_drawdown_length": int(worst["length"]) if worst is not None else 0,
        "longest_drawdown_periods": longest,
        "current_drawdown": float(drawdown_series(r).iloc[-1]),
        "ulcer_index": ulcer_index(r),
        **dist,
        **wl,
        "best_day": float(r.max()), "best_day_date": r.idxmax(),
        "worst_day": float(r.min()), "worst_day_date": r.idxmin(),
        "best_month": float(mret.max()), "best_month_date": mret.idxmax(),
        "worst_month": float(mret.min()), "worst_month_date": mret.idxmin(),
        "positive_months": float((mret > 0).mean()),
        "var_level": var_level,
        "var": var_, "cvar": cvar_,
        "tail_ratio": tail_ratio(r),
    }
    sharpe_stats = {
        **sri,
        "psr": psr,
        "psr_benchmark_sharpe": sr_benchmark,
        "min_track_record_periods": mintrl,
        "min_track_record_years": mintrl / p if np.isfinite(mintrl) else float("inf"),
        "min_track_record_confidence": psr_confidence,
        "track_record_sufficient": bool(len(x) >= mintrl),
        "skew_population": g3,
        "kurtosis_population": g4,
        "deflated": dsr,
    }
    out: dict[str, Any] = {
        "summary": summary,
        "sharpe_inference": sharpe_stats,
        "drawdowns": drawdown_episodes(r, top_drawdowns),
        "monthly_table": monthly_table(r),
        "equity": equity_curve(r),
        "drawdown": drawdown_series(r),
        "rolling": rolling_stats(r, rolling_window, benchmark, rfs, p) if len(r) >= rolling_window else None,
        "relative": None,
    }
    if benchmark is not None:
        out["relative"] = relative_stats(r, benchmark, rfs, p, nw_lags)
    return out


__all__ = [
    "DeflatedSharpe", "TRADING_DAYS", "annualized_vol", "cagr", "calmar_ratio", "capture_ratios",
    "deflated_sharpe", "distribution_stats", "drawdown_episodes", "drawdown_series", "equity_curve",
    "expected_max_sharpe", "historical_var_cvar", "max_drawdown", "min_track_record_length",
    "monthly_returns", "monthly_table", "omega_ratio", "performance_report", "portfolio_returns",
    "probabilistic_sharpe", "relative_stats", "rolling_stats", "sharpe_inference", "sharpe_ratio",
    "sortino_ratio", "tail_ratio", "total_return", "turnover", "ulcer_index", "win_loss_stats",
]
