"""Library of published trading strategies as CAUSAL target-weight functions.

Every strategy is ``fn(ctx: StrategyContext, **params) -> DataFrame`` of
target weights (rows = decision closes, columns = ``ctx.prices`` columns,
all-NaN row = "no signal yet"). Row ``t`` may only use ``ctx`` rows ``<= t``;
the engine audits this by re-running on truncated data and executes decisions
with a lag of at least one session.

Each strategy is registered with a :class:`StrategySpec` holding its parameter
schema (name, type, default, bounds, description), the paper it implements
and a plain-English explanation, surfaced by ``GET /api/backtest/strategies``.
Defaults are the papers' own choices wherever the paper specifies one.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .engine import StrategyContext
from .estimators import ewma_vol, rolling_cov_path

PERIODS = 252


# ======================================================================= schema
@dataclass(frozen=True)
class Param:
    name: str
    type: str                          # int | float | bool | choice | ticker | weights
    default: Any
    description: str
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["choices"] is not None:
            d["choices"] = list(d["choices"])
        return d

    @property
    def sweepable(self) -> bool:
        return self.type in ("int", "float")


@dataclass(frozen=True)
class StrategySpec:
    key: str
    name: str
    category: str
    fn: Callable[..., pd.DataFrame]
    params: tuple[Param, ...]
    citation: str
    explanation: str
    default_rebalance: str
    default_tickers: tuple[str, ...]
    suggested_universes: tuple[str, ...] = ()
    min_assets: int = 1
    max_assets: int | None = None
    needs_market: bool = False
    default_max_leverage: float = 2.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "category": self.category,
            "params": [p.to_dict() for p in self.params],
            "citation": self.citation, "explanation": self.explanation,
            "default_rebalance": self.default_rebalance,
            "default_tickers": list(self.default_tickers),
            "suggested_universes": list(self.suggested_universes),
            "min_assets": self.min_assets, "max_assets": self.max_assets,
            "needs_market": self.needs_market,
            "default_max_leverage": self.default_max_leverage,
            "notes": list(self.notes),
        }

    def param(self, name: str) -> Param:
        for p in self.params:
            if p.name == name:
                return p
        raise ValueError(f"strategy {self.key!r} has no parameter {name!r}")


def validate_params(spec: StrategySpec, params: dict[str, Any] | None, tickers: list[str]) -> dict[str, Any]:
    """Merge ``params`` over defaults, coerce types, and check bounds, choices
    and tickers; raises ``ValueError`` (HTTP 422) naming the offending field."""
    params = dict(params or {})
    unknown = set(params) - {p.name for p in spec.params}
    if unknown:
        raise ValueError(f"unknown parameter(s) for {spec.key}: {sorted(unknown)}")
    out: dict[str, Any] = {}
    for p in spec.params:
        v = params.get(p.name, p.default)
        if p.type in ("int", "float"):
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{p.name} must be a number, got {v!r}") from None
            if isinstance(v, bool) or not math.isfinite(x):
                raise ValueError(f"{p.name} must be a finite number, got {v!r}")
            if p.type == "int":
                if x != int(x):
                    raise ValueError(f"{p.name} must be an integer")
                v = int(x)
            else:
                v = x
        elif p.type == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{p.name} must be true/false")
        elif p.type == "choice":
            if v not in (p.choices or ()):
                raise ValueError(f"{p.name} must be one of {p.choices}")
        elif p.type == "ticker":
            v = str(v or "").strip().upper()
            if v and v not in tickers:
                raise ValueError(f"{p.name}={v} must be one of the backtest tickers {tickers}")
        elif p.type == "weights":
            if not isinstance(v or {}, dict):
                raise ValueError(f"{p.name} must be an object mapping ticker -> weight")
            try:
                v = {str(k).strip().upper(): float(x) for k, x in dict(v or {}).items()}
            except (TypeError, ValueError):
                raise ValueError(f"{p.name}: every weight must be a number") from None
            if not all(math.isfinite(x) for x in v.values()):
                raise ValueError(f"{p.name}: weights must be finite")
            bad = [k for k in v if k not in tickers]
            if bad:
                raise ValueError(f"{p.name}: tickers {bad} are not in the backtest universe")
        if p.type in ("int", "float"):
            if p.min is not None and v < p.min:
                raise ValueError(f"{p.name}={v} is below the minimum {p.min}")
            if p.max is not None and v > p.max:
                raise ValueError(f"{p.name}={v} is above the maximum {p.max}")
        out[p.name] = v
    n = len(tickers)
    if n < spec.min_assets:
        raise ValueError(f"{spec.name} needs at least {spec.min_assets} tickers")
    if spec.max_assets is not None and n > spec.max_assets:
        raise ValueError(f"{spec.name} takes at most {spec.max_assets} tickers")
    return out


# ===================================================================== helpers
def _rf_growth(ctx: StrategyContext, lookback: int) -> pd.Series:
    """Compounded risk-free return over the trailing ``lookback`` sessions
    (zeros when no rf series is supplied)."""
    if ctx.rf is None:
        return pd.Series(0.0, index=ctx.prices.index)
    lr = np.log1p(ctx.rf.reindex(ctx.prices.index).fillna(0.0))
    return np.expm1(lr.rolling(lookback, min_periods=lookback).sum())


def _empty(ctx: StrategyContext) -> pd.DataFrame:
    return pd.DataFrame(np.nan, index=ctx.prices.index, columns=ctx.prices.columns)


def _rows_valid(w: pd.DataFrame, valid: pd.Series) -> pd.DataFrame:
    """``w`` with the rows where ``valid`` is False set to NaN ("no signal")."""
    ok = valid.reindex(w.index).fillna(False).to_numpy(dtype=bool)
    return pd.DataFrame(np.where(ok[:, None], w.to_numpy(dtype=float), np.nan), index=w.index, columns=w.columns)


# ================================================================= strategies
def tsmom(ctx: StrategyContext, lookback: int = 252, vol_target: float = 0.40, com: float = 60.0,
          long_only: bool = False) -> pd.DataFrame:
    """Time-series momentum (Moskowitz, Ooi & Pedersen 2012, eq. 5)::

        w_{i,t} = (1/S_t) sign(r^{ex}_{i,t-L..t}) * sigma_target / sigma_{i,t}

    ``r^ex`` = trailing ``L``-session return minus the compounded T-bill return
    (raw return when no rf is available); ``sigma_{i,t}`` = ex-ante annualized
    EWMA volatility with centre of mass 60 days (eq. 1); ``S_t`` = number of
    assets with a signal. The paper uses 40% per-asset volatility, 12-month
    lookback, monthly rebalancing.
    """
    px = ctx.prices
    past = px / px.shift(lookback) - 1.0
    ex = past.sub(_rf_growth(ctx, lookback), axis=0)
    sig = np.sign(ex)
    if long_only:
        sig = sig.clip(lower=0.0)
    vol = ewma_vol(ctx.returns, com=com, periods=261.0)
    raw = sig * (vol_target / vol)
    n = raw.notna().sum(axis=1).replace(0, np.nan)
    return raw.div(n, axis=0)


def xsmom(ctx: StrategyContext, lookback: int = 252, skip: int = 21, top_k: int = 3,
          long_short: bool = False) -> pd.DataFrame:
    """Cross-sectional momentum (Jegadeesh & Titman 1993): rank assets on the
    ``lookback``-to-``skip`` return ``P_{t-skip} / P_{t-lookback} - 1`` (12-1
    months by default; skipping the last month avoids the short-term reversal
    of Jegadeesh 1990), hold the top ``k`` equally (``1/k`` each) and, if
    ``long_short``, short the bottom ``k`` (``-1/k`` each)."""
    if skip >= lookback:
        raise ValueError("skip must be shorter than lookback")
    px = ctx.prices
    n = px.shape[1]
    if top_k > n or (long_short and 2 * top_k > n):
        raise ValueError(f"top_k={top_k} too large for {n} assets")
    score = px.shift(skip) / px.shift(lookback) - 1.0
    valid = score.notna().all(axis=1)
    rk = score.rank(axis=1, ascending=False, method="first")
    w = (rk <= top_k).astype(float) / top_k
    if long_short:
        w = w - (rk > n - top_k).astype(float) / top_k
    return _rows_valid(w, valid)


def dual_momentum(ctx: StrategyContext, lookback: int = 252, top_n: int = 1,
                  safe_asset: str = "") -> pd.DataFrame:
    """Dual momentum (Antonacci 2014, *Dual Momentum Investing*; Antonacci 2012
    "Risk Premia Harvesting Through Dual Momentum"). Relative momentum picks
    the ``top_n`` risky assets by trailing ``lookback`` return; absolute
    momentum keeps each only if its return beats the compounded T-bill return
    over the same window, otherwise its ``1/top_n`` slot goes to ``safe_asset``
    (e.g. an aggregate-bond ETF, as in Global Equities Momentum) or to cash."""
    px = ctx.prices
    risky = [c for c in px.columns if c != safe_asset]
    if top_n > len(risky):
        raise ValueError(f"top_n={top_n} exceeds the {len(risky)} risky assets")
    mom = px[risky] / px[risky].shift(lookback) - 1.0
    hurdle = _rf_growth(ctx, lookback)
    valid = mom.notna().all(axis=1) & hurdle.notna()
    rk = mom.rank(axis=1, ascending=False, method="first")
    chosen = rk <= top_n
    passes = mom.gt(hurdle, axis=0)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    w[risky] = (chosen & passes).astype(float) / top_n
    if safe_asset:
        w[safe_asset] = (chosen & ~passes).sum(axis=1) / top_n
    return _rows_valid(w, valid)


def sma_trend(ctx: StrategyContext, months: int = 10) -> pd.DataFrame:
    """Tactical asset allocation (Faber 2007, "A Quantitative Approach to
    Tactical Asset Allocation", *Journal of Wealth Management* 9(4)): hold each
    of the ``N`` assets with weight ``1/N`` when its price is above its
    ``months``-month simple moving average of month-end closes, else keep that
    slot in cash. The SMA at session ``t`` averages the ``months - 1`` previous
    completed month-end closes and today's close (the current month's close
    once the month ends), so it is causal on every session."""
    px = ctx.prices
    per = px.index.to_period("M")
    me = px.groupby(per).last()
    prev = me.rolling(months - 1, min_periods=months - 1).sum()
    prev.index = prev.index + 1
    prev_d = prev.reindex(per)
    prev_d.index = px.index
    sma = (prev_d + px) / months
    valid = sma.notna().all(axis=1)
    w = (px > sma).astype(float) / px.shape[1]
    return _rows_valid(w, valid)


def vol_managed(ctx: StrategyContext, window: int = 21, min_history: int = 252,
                max_leverage: float = 2.0) -> pd.DataFrame:
    """Volatility-managed portfolio (Moreira & Muir 2017, "Volatility-Managed
    Portfolios", *Journal of Finance* 72(4), eq. 1)::

        f^sigma_{t+1} = (c / sigma^2_t) f_{t+1}

    ``f`` = the equal-weight (daily-rebalanced) portfolio of the assets;
    ``sigma^2_t`` = realized variance of ``f`` over the trailing ``window``
    sessions (one month in the paper), annualized. The paper picks ``c`` so the
    managed and unmanaged series have the same FULL-SAMPLE volatility -- that
    uses future data. This implementation is the real-time version: ``c_t`` is
    re-estimated on an EXPANDING window up to ``t``::

        c_t = std_{s<=t}(f_s) / std_{s<=t}(f_s / sigma^2_{s-1})

    with both standard deviations taken over the SAME sessions (those where
    ``sigma^2_{s-1}`` exists), so ``c_t`` equalizes volatility on one sample.

    Exposure ``c_t / sigma^2_t`` is capped at ``max_leverage`` (the paper's
    Table 4 discusses 1.5x-2x caps)."""
    f = ctx.returns.mean(axis=1)
    var = f.rolling(window, min_periods=window).var(ddof=1) * PERIODS
    scaled = f / var.shift(1)
    f_common = f.where(scaled.notna())    # same sessions in numerator and denominator
    c = f_common.expanding(min_periods=min_history).std() / scaled.expanding(min_periods=min_history).std()
    expo = (c / var).clip(upper=max_leverage)
    n = ctx.prices.shape[1]
    w = pd.DataFrame(np.repeat(expo.to_numpy()[:, None] / n, n, axis=1),
                     index=ctx.prices.index, columns=ctx.prices.columns)
    return w


def risk_parity(ctx: StrategyContext, vol_window: int = 756, target_vol: float = 0.10,
                cov_window: int = 252, max_leverage: float = 3.0) -> pd.DataFrame:
    """Inverse-volatility risk parity with leverage to a volatility target
    (Asness, Frazzini & Pedersen 2012, "Leverage Aversion and Risk Parity",
    *Financial Analysts Journal* 68(1), eq. 1)::

        w_{i,t} = k_t / sigma_{i,t},   k_t = sigma* / sqrt(252 v_t' S_t v_t)

    ``sigma_i`` = rolling daily-return volatility over ``vol_window`` sessions
    (three years in the paper; estimation starts after one year), ``v`` = the
    ``1/sigma`` vector, ``S_t`` = trailing ``cov_window`` sample covariance.
    AFP set ``k`` so the portfolio's realized volatility matches the market's
    over the full sample (look-ahead); here ``k_t`` targets ``sigma*`` ex ante.
    ``target_vol = 0`` gives the unlevered version (weights sum to one)."""
    r = ctx.returns
    sig = r.rolling(vol_window, min_periods=min(252, vol_window)).std(ddof=1)
    inv = 1.0 / sig
    if target_vol <= 0:
        w = inv.div(inv.sum(axis=1), axis=0)
        return _rows_valid(w, inv.notna().all(axis=1))
    cov = rolling_cov_path(r, cov_window)
    v = inv.to_numpy()
    with np.errstate(invalid="ignore"):
        pv = np.einsum("ti,tij,tj->t", v, cov, v)
        k = target_vol / np.sqrt(pv * PERIODS)
    w = inv.mul(k, axis=0)
    gross = w.abs().sum(axis=1)
    w = w.div(np.maximum(gross / max_leverage, 1.0), axis=0)
    valid = inv.notna().all(axis=1) & np.isfinite(k)
    return _rows_valid(w, pd.Series(valid, index=w.index))


def low_beta(ctx: StrategyContext, vol_window: int = 252, corr_window: int = 1260,
             shrink: float = 0.6, mode: str = "long_short") -> pd.DataFrame:
    """Betting against beta (Frazzini & Pedersen 2014, "Betting Against Beta",
    *Journal of Financial Economics* 111(1), eqs. 14-16)::

        beta^TS_i = rho_{i,m} sigma_i / sigma_m,   beta_i = w beta^TS_i + (1 - w) * 1

    ``sigma`` = rolling std of daily log returns (1 year, >= 120 obs);
    ``rho`` = rolling correlation of 3-day overlapping log returns (5 years,
    >= 750 obs); shrinkage ``w = 0.6`` toward the cross-sectional prior of 1.
    Rank weights ``w_H = k (z - zbar)^+``, ``w_L = k (z - zbar)^-`` with
    ``z = rank(beta)`` and ``k = 2 / sum|z - zbar|``. ``long_short`` (BAB):
    ``w = w_L / beta_L - w_H / beta_H`` -- long low-beta levered to beta 1,
    short high-beta de-levered to beta 1 (ex-ante beta-neutral, financed at rf).
    ``long_only``: the rank-weighted low-beta leg ``w_L`` (fully invested)."""
    if ctx.market is None:
        raise ValueError("low_beta needs a market (benchmark) series")
    lr = np.log(ctx.prices).diff()
    lm = np.log(ctx.market.reindex(ctx.prices.index)).diff()
    vol_i = lr.rolling(vol_window, min_periods=min(120, vol_window)).std()
    vol_m = lm.rolling(vol_window, min_periods=min(120, vol_window)).std()
    lr3 = lr.rolling(3).sum()
    lm3 = lm.rolling(3).sum()
    rho = lr3.rolling(corr_window, min_periods=min(750, corr_window)).corr(lm3)
    beta_ts = rho.mul(vol_i).div(vol_m, axis=0)
    beta = shrink * beta_ts + (1.0 - shrink)
    valid = beta.notna().all(axis=1)
    z = beta.rank(axis=1)
    dev = z.sub(z.mean(axis=1), axis=0)
    k = 2.0 / dev.abs().sum(axis=1)
    wh = dev.clip(lower=0).mul(k, axis=0)
    wl = (-dev).clip(lower=0).mul(k, axis=0)
    if mode == "long_only":
        return _rows_valid(wl, valid)
    bl = (wl * beta).sum(axis=1)
    bh = (wh * beta).sum(axis=1)
    w = wl.div(bl, axis=0) - wh.div(bh, axis=0)
    ok = valid & (bl > 0) & (bh > 0)
    return _rows_valid(w, ok)


def st_reversal(ctx: StrategyContext, lookback: int = 5, long_only: bool = False) -> pd.DataFrame:
    """Short-term reversal, losers-minus-winners (Lehmann 1990, "Fads,
    Martingales, and Market Efficiency", *QJE* 105(1))::

        w_{i,t} = -(r_{i,t-L..t} - rbar_t) / (0.5 sum_j |r_{j,t-L..t} - rbar_t|)

    i.e. weights proportional to minus the market-adjusted past-week return,
    scaled so the long (losers) leg is +1 and the short (winners) leg -1.
    ``long_only`` keeps only the losers leg (sums to one). Weekly rebalance."""
    px = ctx.prices
    past = px / px.shift(lookback) - 1.0
    dev = past.sub(past.mean(axis=1), axis=0)
    scale = 0.5 * dev.abs().sum(axis=1)
    w = (-dev).div(scale.where(scale > 0), axis=0)
    if long_only:
        w = w.clip(lower=0.0)
    return _rows_valid(w, past.notna().all(axis=1) & (scale > 0))


def pairs_distance(ctx: StrategyContext, formation: int = 252, trading: int = 126, n_pairs: int = 1,
                   entry_sd: float = 2.0) -> pd.DataFrame:
    """Distance-method pairs trading (Gatev, Goetzmann & Rouwenhorst 2006,
    "Pairs Trading: Performance of a Relative-Value Arbitrage Rule", *RFS* 19(3)).

    Consecutive, non-overlapping cycles anchored at the first session: in each
    ``formation`` window prices are normalized to 1 at its start, and the
    ``n_pairs`` pairs with the smallest sum of squared differences of the
    normalized prices are selected. During the following ``trading`` window the
    normalized spread ``s = p~_i - p~_j`` is monitored; a position opens when
    ``|s| > entry_sd * sd_formation(s)`` (short the rich leg, long the cheap
    leg, ``1/n_pairs`` of NAV per leg) and closes when ``s`` crosses zero or the
    trading window ends. The paper uses 12-month formation, 6-month trading and
    a 2-sd trigger; it also staggers six overlapping portfolios, which we do not.
    """
    px = ctx.prices.to_numpy(dtype=float)
    t_len, n = px.shape
    if n < 2:
        raise ValueError("pairs trading needs at least 2 assets")
    iu, ju = np.triu_indices(n, 1)
    if n_pairs > len(iu):
        raise ValueError(f"n_pairs={n_pairs} exceeds the {len(iu)} available pairs")
    out = np.full((t_len, n), np.nan)
    a = 0
    while a + formation < t_len:
        f_end = a + formation
        norm = px[a:] / px[a]
        form = norm[:formation]
        d = form[:, iu] - form[:, ju]
        ssd = np.sum(d * d, axis=0)
        sel = np.argsort(ssd, kind="stable")[:n_pairs]
        sd = d[:, sel].std(axis=0, ddof=1)
        t_stop = min(f_end + trading, t_len)
        pos = np.zeros(len(sel))
        for t in range(f_end, t_stop):
            s = norm[t - a, iu[sel]] - norm[t - a, ju[sel]]
            for k in range(len(sel)):
                if pos[k] == 0.0:
                    if sd[k] > 0 and abs(s[k]) > entry_sd * sd[k]:
                        pos[k] = -np.sign(s[k])      # +1 = long i / short j
                elif np.sign(s[k]) == pos[k] or s[k] == 0.0:
                    pos[k] = 0.0                     # spread crossed zero: converge
            if t == f_end + trading - 1:
                pos[:] = 0.0                         # end of trading period
            row = np.zeros(n)
            for k, p in enumerate(pos):
                row[iu[sel[k]]] += p / n_pairs
                row[ju[sel[k]]] -= p / n_pairs
            out[t] = row
        a += trading
    return pd.DataFrame(out, index=ctx.prices.index, columns=ctx.prices.columns)


def engle_granger_pvalue(y: np.ndarray, x: np.ndarray, lags: int = 1) -> tuple[float, float]:
    """Engle & Granger (1987) two-step cointegration test with a fixed number of
    augmentation lags: OLS ``y = a + b x + e``; ADF regression without
    deterministic terms ``de_t = g e_{t-1} + sum_k phi_k de_{t-k} + u_t``;
    p-value of the t-statistic of ``g`` from MacKinnon (1994, 2010) response
    surfaces for 2 variables with a constant (as ``statsmodels.tsa.coint``).
    Returns (t-statistic, p-value)."""
    from statsmodels.tsa.adfvalues import mackinnonp

    X = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ coef
    de = np.diff(e)
    cols = [e[lags:-1]]
    for k in range(1, lags + 1):
        cols.append(de[lags - k: len(de) - k])
    Z = np.column_stack(cols)
    yy = de[lags:]
    g, *_ = np.linalg.lstsq(Z, yy, rcond=None)
    resid = yy - Z @ g
    dof = len(yy) - Z.shape[1]
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.inv(Z.T @ Z)
    tstat = float(g[0] / math.sqrt(cov[0, 0]))
    return tstat, float(mackinnonp(tstat, regression="c", N=2))


def pairs_coint(ctx: StrategyContext, window: int = 252, entry_z: float = 2.0, exit_z: float = 0.5,
                stop_z: float = 4.0, max_pvalue: float = 0.10, retest: int = 21) -> pd.DataFrame:
    """Cointegration pairs trading (Engle & Granger 1987; Vidyamurthy 2004,
    *Pairs Trading*): with ``y`` = first ticker and ``x`` = second, a rolling
    OLS on log prices over ``window`` sessions gives ``log y = a_t + b_t log x +
    e``; ``z_t = e_t / sd_t(e)``. Enter short the spread (short ``y``, long
    ``b x``) when ``z > entry_z``, long when ``z < -entry_z``; exit when
    ``|z| < exit_z``; stop out when ``|z| > stop_z`` (re-arm only after ``|z|``
    falls back below ``entry_z``). The hedge ratio is fixed at entry; legs are
    ``+-1/(1+|b|)`` and ``-+b/(1+|b|)`` of NAV (gross 1). New entries require
    the Engle-Granger p-value on the trailing window (re-tested every
    ``retest`` sessions) to be below ``max_pvalue``; a failed test closes open
    positions."""
    if ctx.prices.shape[1] != 2:
        raise ValueError("cointegration pairs trading takes exactly 2 tickers (y, x)")
    ly = np.log(ctx.prices.iloc[:, 0])
    lx = np.log(ctx.prices.iloc[:, 1])
    b = ly.rolling(window, min_periods=window).cov(lx) / lx.rolling(window, min_periods=window).var()
    a = ly.rolling(window, min_periods=window).mean() - b * lx.rolling(window, min_periods=window).mean()
    e = ly - a - b * lx
    # residual sd of the in-window OLS fit: var(y) (1 - rho^2) * (w-1)/(w-2)
    rho = ly.rolling(window, min_periods=window).corr(lx)
    sd = np.sqrt(ly.rolling(window, min_periods=window).var() * (1 - rho ** 2) * (window - 1) / (window - 2))
    z = (e / sd).to_numpy()
    bv = b.to_numpy()
    lyv, lxv = ly.to_numpy(), lx.to_numpy()
    t_len = len(z)
    out = np.full((t_len, 2), np.nan)
    pos, beta_fix, armed, ok = 0.0, 0.0, True, False
    for t in range(window - 1, t_len):
        if (t - (window - 1)) % retest == 0:
            _, p = engle_granger_pvalue(lyv[t + 1 - window: t + 1], lxv[t + 1 - window: t + 1])
            ok = p < max_pvalue
            if not ok:
                pos = 0.0
        zt = z[t]
        if not np.isfinite(zt):
            continue
        if pos != 0.0:
            if abs(zt) < exit_z:
                pos = 0.0
            elif abs(zt) > stop_z:
                pos, armed = 0.0, False
        if not armed and abs(zt) < entry_z:
            armed = True
        if pos == 0.0 and ok and armed and abs(zt) > entry_z and abs(zt) <= stop_z:
            pos = -np.sign(zt)
            beta_fix = bv[t]
        scale = 1.0 + abs(beta_fix)
        out[t] = [pos / scale, -pos * beta_fix / scale] if pos != 0.0 else [0.0, 0.0]
    return pd.DataFrame(out, index=ctx.prices.index, columns=ctx.prices.columns)


def buy_and_hold(ctx: StrategyContext) -> pd.DataFrame:
    """Equal-weight buy-and-hold: ``w = 1/N`` bought at the first execution and
    never rebalanced (weights drift with prices)."""
    n = ctx.prices.shape[1]
    return pd.DataFrame(1.0 / n, index=ctx.prices.index, columns=ctx.prices.columns)


def static_mix(ctx: StrategyContext, weights: dict[str, float] | None = None) -> pd.DataFrame:
    """Constant-mix benchmark (e.g. 60/40), rebalanced on the engine schedule
    (Perold & Sharpe 1988, "Dynamic Strategies for Asset Allocation", *FAJ*
    44(1)). Missing tickers get 0; no weights = equal weight; ``1 - sum w``
    is cash."""
    cols = list(ctx.prices.columns)
    wd = weights or {c: 1.0 / len(cols) for c in cols}
    row = np.array([float(wd.get(c, 0.0)) for c in cols])
    return pd.DataFrame(np.tile(row, (len(ctx.prices), 1)), index=ctx.prices.index, columns=cols)


# ==================================================================== registry
_MULTI = ("SPY", "EFA", "EEM", "IEF", "TLT", "LQD", "GLD", "DBC")
_SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB")

STRATEGIES: dict[str, StrategySpec] = {}


def _reg(spec: StrategySpec) -> None:
    STRATEGIES[spec.key] = spec


_reg(StrategySpec(
    key="tsmom", name="Time-series momentum", category="trend", fn=tsmom,
    params=(
        Param("lookback", "int", 252, "Trailing window for the excess-return sign (sessions; 252 = 12 months).", 21, 504),
        Param("vol_target", "float", 0.40, "Per-asset annualized volatility target (MOP use 40%).", 0.05, 1.0),
        Param("com", "float", 60.0, "Centre of mass (days) of the EWMA volatility estimate.", 10.0, 250.0),
        Param("long_only", "bool", False, "Replace short signals with cash."),
    ),
    citation="Moskowitz, T. J., Ooi, Y. H. & Pedersen, L. H. (2012). Time Series Momentum. "
             "Journal of Financial Economics 104(2), 228-250.",
    explanation="Each asset is bought if it has beaten T-bills over the past year and shorted if it has "
                "lagged them. Positions are sized so every asset contributes the same volatility, so calm "
                "bond futures get larger weights than volatile commodities. Across 58 futures markets the "
                "authors find this trend-following rule earned strong returns, especially in extreme markets.",
    default_rebalance="monthly", default_tickers=_MULTI,
    suggested_universes=("us_equity_indices", "rates", "commodities", "international", "credit"),
    min_assets=1, default_max_leverage=3.0,
    notes=("Per-asset leverage (40%/sigma) is scaled by 1/N and then capped by max_gross_leverage.",),
))
_reg(StrategySpec(
    key="xsmom", name="Cross-sectional momentum", category="momentum", fn=xsmom,
    params=(
        Param("lookback", "int", 252, "Formation window in sessions (252 = 12 months).", 42, 504),
        Param("skip", "int", 21, "Most recent sessions skipped (21 = 1 month).", 0, 63),
        Param("top_k", "int", 3, "Number of winners held (and losers shorted).", 1, 30),
        Param("long_short", "bool", False, "Also short the bottom k."),
    ),
    citation="Jegadeesh, N. & Titman, S. (1993). Returns to Buying Winners and Selling Losers: "
             "Implications for Stock Market Efficiency. Journal of Finance 48(1), 65-91.",
    explanation="Every month the assets are ranked on their return over the past year excluding the last "
                "month. The best performers are bought in equal amounts and, optionally, the worst are sold "
                "short. Winners have tended to keep winning for 3-12 months.",
    default_rebalance="monthly", default_tickers=_SECTORS, suggested_universes=("sectors", "international"),
    min_assets=2, default_max_leverage=2.0,
))
_reg(StrategySpec(
    key="dual_momentum", name="Dual momentum", category="momentum", fn=dual_momentum,
    params=(
        Param("lookback", "int", 252, "Momentum window in sessions.", 42, 504),
        Param("top_n", "int", 1, "Number of risky assets held.", 1, 10),
        Param("safe_asset", "ticker", "", "Ticker held when absolute momentum fails (blank = cash)."),
    ),
    citation="Antonacci, G. (2014). Dual Momentum Investing: An Innovative Strategy for Higher Returns "
             "with Lower Risk. McGraw-Hill. Also Antonacci (2012), Risk Premia Harvesting Through Dual Momentum.",
    explanation="Pick the risky asset(s) with the best 12-month return (relative momentum), but only hold "
                "them if that return beats T-bills (absolute momentum); otherwise move to a safe asset such "
                "as bonds or cash. It aims to keep equity-like returns while side-stepping long bear markets.",
    default_rebalance="monthly", default_tickers=("SPY", "EFA", "IEF"),
    suggested_universes=("us_equity_indices", "international"),
    min_assets=2, default_max_leverage=1.0,
))
_reg(StrategySpec(
    key="sma_trend", name="10-month moving-average timing", category="trend", fn=sma_trend,
    params=(Param("months", "int", 10, "Length of the month-end simple moving average.", 2, 24),),
    citation="Faber, M. T. (2007). A Quantitative Approach to Tactical Asset Allocation. "
             "Journal of Wealth Management 9(4), 69-79.",
    explanation="Split the portfolio equally across assets. At each month end, keep an asset only if its "
                "price is above its 10-month average; otherwise hold that slice in cash. A simple rule that "
                "historically cut drawdowns sharply at a small cost in return.",
    default_rebalance="monthly", default_tickers=("SPY", "EFA", "IEF", "DBC", "VNQ"),
    suggested_universes=("us_equity_indices", "commodities", "international"),
    min_assets=1, default_max_leverage=1.0,
))
_reg(StrategySpec(
    key="vol_managed", name="Volatility-managed portfolio", category="risk", fn=vol_managed,
    params=(
        Param("window", "int", 21, "Realized-variance window in sessions (21 = one month).", 5, 126),
        Param("min_history", "int", 252, "Sessions before the expanding-window constant c is estimated.", 63, 1260),
        Param("max_leverage", "float", 2.0, "Cap on total exposure c/sigma^2.", 0.5, 4.0),
    ),
    citation="Moreira, A. & Muir, T. (2017). Volatility-Managed Portfolios. Journal of Finance 72(4), 1611-1644.",
    explanation="Hold more of an equal-weight portfolio when its recent volatility is low and less when it "
                "is high, scaling exposure by one over last month's variance. Because volatility is "
                "persistent but returns do not rise with it, this raised Sharpe ratios for many factors.",
    default_rebalance="monthly", default_tickers=("SPY",), suggested_universes=("us_equity_indices",),
    min_assets=1, default_max_leverage=2.0,
    notes=("Real-time version: the scaling constant c uses an expanding window, not the paper's in-sample choice.",),
))
_reg(StrategySpec(
    key="risk_parity", name="Inverse-volatility risk parity", category="risk", fn=risk_parity,
    params=(
        Param("vol_window", "int", 756, "Volatility window in sessions (756 = 3 years).", 63, 1260),
        Param("target_vol", "float", 0.10, "Ex-ante annualized portfolio volatility target (0 = unlevered).", 0.0, 0.30),
        Param("cov_window", "int", 252, "Covariance window for the leverage estimate.", 63, 756),
        Param("max_leverage", "float", 3.0, "Cap on gross leverage.", 1.0, 5.0),
    ),
    citation="Asness, C. S., Frazzini, A. & Pedersen, L. H. (2012). Leverage Aversion and Risk Parity. "
             "Financial Analysts Journal 68(1), 47-59.",
    explanation="Give each asset a weight proportional to one over its volatility so each contributes a "
                "similar amount of risk, then lever the whole portfolio to a target volatility. Because "
                "leverage-averse investors overpay for risky assets, low-risk assets levered up have "
                "historically delivered better risk-adjusted returns.",
    default_rebalance="monthly", default_tickers=("SPY", "EFA", "IEF", "TLT", "LQD", "GLD", "DBC"),
    suggested_universes=("rates", "credit", "commodities", "us_equity_indices"),
    min_assets=2, default_max_leverage=3.0,
))
_reg(StrategySpec(
    key="low_beta", name="Betting against beta", category="factor", fn=low_beta,
    params=(
        Param("vol_window", "int", 252, "Volatility window (sessions).", 63, 756),
        Param("corr_window", "int", 1260, "Correlation window of 3-day returns (sessions; FP use 5 years).", 252, 1260),
        Param("shrink", "float", 0.6, "Weight on the time-series beta (1 - shrink toward beta = 1).", 0.0, 1.0),
        Param("mode", "choice", "long_short", "BAB long/short, or long-only low-beta tilt.",
              choices=("long_short", "long_only")),
    ),
    citation="Frazzini, A. & Pedersen, L. H. (2014). Betting Against Beta. Journal of Financial Economics 111(1), 1-25.",
    explanation="Estimate every asset's beta to the market, then buy the low-beta assets (levered up to a "
                "beta of one) and short the high-beta assets (scaled down to a beta of one). Leverage-"
                "constrained investors bid up high-beta assets, so low-beta assets have earned higher "
                "risk-adjusted returns.",
    default_rebalance="monthly", default_tickers=_SECTORS, suggested_universes=("sectors",),
    min_assets=3, needs_market=True, default_max_leverage=3.0,
))
_reg(StrategySpec(
    key="st_reversal", name="Short-term reversal", category="reversal", fn=st_reversal,
    params=(
        Param("lookback", "int", 5, "Formation window in sessions (5 = one week).", 1, 21),
        Param("long_only", "bool", False, "Hold only the losers leg."),
    ),
    citation="Lehmann, B. N. (1990). Fads, Martingales, and Market Efficiency. "
             "Quarterly Journal of Economics 105(1), 1-28.",
    explanation="Each week, buy last week's losers and short last week's winners in proportion to how far "
                "they moved from the average. Short-term overreaction and liquidity provision make recent "
                "losers bounce back. Turnover is very high, so costs matter.",
    default_rebalance="weekly", default_tickers=_SECTORS, suggested_universes=("sectors",),
    min_assets=2, default_max_leverage=2.0,
))
_reg(StrategySpec(
    key="pairs_distance", name="Pairs trading (distance)", category="stat-arb", fn=pairs_distance,
    params=(
        Param("formation", "int", 252, "Formation window in sessions (12 months).", 63, 756),
        Param("trading", "int", 126, "Trading window in sessions (6 months).", 21, 252),
        Param("n_pairs", "int", 1, "Number of closest pairs traded.", 1, 20),
        Param("entry_sd", "float", 2.0, "Open when the spread exceeds this many formation sds.", 0.5, 4.0),
    ),
    citation="Gatev, E., Goetzmann, W. N. & Rouwenhorst, K. G. (2006). Pairs Trading: Performance of a "
             "Relative-Value Arbitrage Rule. Review of Financial Studies 19(3), 797-827.",
    explanation="Find the assets whose normalized prices moved most closely together over the past year. "
                "When the gap between a pair widens beyond two historical standard deviations, sell the "
                "expensive one and buy the cheap one; close when they meet again or after six months.",
    default_rebalance="signal", default_tickers=("XLK", "QQQ"), suggested_universes=("sectors",),
    min_assets=2, default_max_leverage=2.0,
    notes=("One sequence of back-to-back formation/trading cycles anchored at the first loaded session; GGR "
           "average six portfolios started in consecutive months, which smooths the results.",),
))
_reg(StrategySpec(
    key="pairs_coint", name="Pairs trading (cointegration)", category="stat-arb", fn=pairs_coint,
    params=(
        Param("window", "int", 252, "Rolling hedge-ratio / z-score window in sessions.", 63, 756),
        Param("entry_z", "float", 2.0, "Entry |z|.", 0.5, 4.0),
        Param("exit_z", "float", 0.5, "Exit |z|.", 0.0, 2.0),
        Param("stop_z", "float", 4.0, "Stop-loss |z|.", 2.0, 8.0),
        Param("max_pvalue", "float", 0.10, "Max Engle-Granger p-value to allow entries.", 0.01, 1.0),
        Param("retest", "int", 21, "Sessions between cointegration re-tests.", 5, 126),
    ),
    citation="Engle, R. F. & Granger, C. W. J. (1987). Co-integration and Error Correction. Econometrica "
             "55(2), 251-276; Vidyamurthy, G. (2004). Pairs Trading: Quantitative Methods and Analysis. Wiley.",
    explanation="Regress one price on the other to get a hedge ratio and a spread. When the spread is more "
                "than two standard deviations from its mean—and a cointegration test says the pair really "
                "does mean-revert—bet on it closing. Exit near the mean, cut losses if it keeps widening.",
    default_rebalance="signal", default_tickers=("XLK", "QQQ"), suggested_universes=("sectors",),
    min_assets=2, max_assets=2, default_max_leverage=2.0,
))
_reg(StrategySpec(
    key="buy_and_hold", name="Buy and hold (equal weight)", category="benchmark", fn=buy_and_hold,
    params=(),
    citation="Benchmark: equal initial weights, never rebalanced.",
    explanation="Invest equally in every asset once and never trade again; weights drift with prices. The "
                "passive yardstick every active rule must beat after costs.",
    default_rebalance="never", default_tickers=("SPY",), suggested_universes=("us_equity_indices",),
    min_assets=1, default_max_leverage=1.0,
))
_reg(StrategySpec(
    key="static_mix", name="Static mix (e.g. 60/40)", category="benchmark", fn=static_mix,
    params=(Param("weights", "weights", {}, "Target weights by ticker (blank = equal weight; 1 - sum = cash)."),),
    citation="Perold, A. F. & Sharpe, W. F. (1988). Dynamic Strategies for Asset Allocation. "
             "Financial Analysts Journal 44(1), 16-27.",
    explanation="Keep fixed weights—for example 60% stocks and 40% bonds—and trade back to them on "
                "the rebalance schedule. Rebalancing sells what has risen and buys what has fallen.",
    default_rebalance="monthly", default_tickers=("SPY", "IEF"), suggested_universes=("us_equity_indices", "rates"),
    min_assets=1, default_max_leverage=2.0,
))


def get_strategy(key: str) -> StrategySpec:
    try:
        return STRATEGIES[key]
    except KeyError:
        raise ValueError(f"unknown strategy {key!r}; choose from {sorted(STRATEGIES)}") from None


def catalog() -> list[dict[str, Any]]:
    return [s.to_dict() for s in STRATEGIES.values()]


# Parameters that set a look-back length, and how many sessions one unit is.
_WINDOW_PARAMS: dict[str, int] = {
    "lookback": 1, "com": 1, "months": 21, "min_history": 1, "vol_window": 1, "cov_window": 1,
    "corr_window": 1, "formation": 1, "window": 1,
}


def warmup_sessions(spec: StrategySpec, params: dict[str, Any]) -> int:
    """Longest look-back implied by ``params`` (sessions): how much history
    must precede the first decision for the strategy to have a signal."""
    n = 0
    for p in spec.params:
        unit = _WINDOW_PARAMS.get(p.name)
        if unit and p.type in ("int", "float"):
            n = max(n, int(math.ceil(float(params.get(p.name, p.default)) * unit)))
    names = {p.name for p in spec.params}
    if {"window", "min_history"} <= names:
        # vol_managed: min_history observations of f / sigma^2_{s-1}, which
        # itself needs ``window`` sessions first -- the windows add up.
        n = max(n, int(params.get("window", spec.param("window").default))
                + int(params.get("min_history", spec.param("min_history").default)))
    return n + 5
