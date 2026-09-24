"""Market regimes: Hamilton Markov switching and a transparent risk-on/off panel.

Markov-switching model (Hamilton 1989)
--------------------------------------
``r_t = mu_{S_t} + sigma_{S_t} e_t``, ``e_t ~ N(0, 1)``,
``S_t in {0..k-1}`` a first-order Markov chain with
``p_ij = P(S_t = j | S_{t-1} = i)``. Estimated by maximum likelihood with
the Hamilton filter (statsmodels ``MarkovRegression``, switching mean and
variance) after a random search over starting values (``search_reps``) and
EM iterations (Hamilton 1990), with a fixed RNG seed for reproducibility.
Returns are in percent for numerical conditioning; regimes are relabelled
in order of increasing volatility (0 = calmest). Expected duration of regime
``i`` is ``1 / (1 - p_ii)`` periods. Filtered probabilities
``P(S_t | r_1..r_t)`` are real-time; smoothed ``P(S_t | r_1..r_T)``
(Kim 1994) use the full sample.

Risk-on/off panel
-----------------
Each component is a transparent score in ``[0, 1]`` (1 = risk-on), computed
from the series' OWN history rather than fixed thresholds:

* VIX: ``1 - percentile rank`` of today's level in its history;
* HY OAS (ICE BofA, FRED ``BAMLH0A0HYM2``): ``1 - percentile rank``;
* curve slope (10y - 2y, or 10y - 3m): 1 if positive, 0 if inverted;
* trend: 1 if the index is above its 200-session moving average.

The composite is the equal-weighted mean of the components available.

References
----------
* Hamilton, J. D. (1989), "A New Approach to the Economic Analysis of
  Nonstationary Time Series and the Business Cycle", Econometrica 57(2).
* Hamilton, J. D. (1990), "Analysis of Time Series Subject to Changes in
  Regime", J. Econometrics 45.
* Kim, C.-J. (1994), "Dynamic Linear Models with Markov-Switching",
  J. Econometrics 60.
* Ang, A. & Bekaert, G. (2002), "Regime Switches in Interest Rates",
  JBES 20(2) -- on the use of regime models for asset allocation.
* Faber, M. (2007), "A Quantitative Approach to Tactical Asset Allocation",
  J. Wealth Management -- the 10-month / 200-day trend rule.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = {"D": 252, "W": 52, "M": 12}

__all__ = ["resample_returns", "MSResult", "fit_markov_switching", "percentile_rank", "risk_panel",
           "PERIODS_PER_YEAR", "resample_prices"]


def resample_prices(prices: pd.Series, freq: Literal["D", "W", "M"]) -> pd.Series:
    """Prices sampled at daily, weekly (Friday close) or month-end frequency
    (last observation of each period, stamped with the period end)."""
    p = prices.dropna()
    if freq == "W":
        p = p.resample("W-FRI").last().dropna()
    elif freq == "M":
        p = p.resample("ME").last().dropna()
    return p


def resample_returns(prices: pd.Series, freq: Literal["D", "W", "M"]) -> pd.Series:
    """Simple returns at daily, weekly (Friday close) or month-end frequency."""
    return resample_prices(prices, freq).pct_change().dropna()


@dataclass
class MSResult:
    k: int
    freq: str
    means_ann: np.ndarray        # decimal / year
    vols_ann: np.ndarray         # decimal / year
    means: np.ndarray            # per period, percent
    sigmas: np.ndarray           # per period, percent
    transition: np.ndarray       # P[i, j] = P(S_t=j | S_{t-1}=i)
    expected_duration: np.ndarray  # periods
    smoothed: pd.DataFrame
    filtered: pd.DataFrame
    current_regime: int
    current_prob: float
    loglik: float
    aic: float
    bic: float
    nobs: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"k": self.k, "freq": self.freq, "means_ann": self.means_ann.tolist(),
                "vols_ann": self.vols_ann.tolist(), "transition": self.transition.tolist(),
                "expected_duration_periods": self.expected_duration.tolist(),
                "current_regime": self.current_regime, "current_prob": self.current_prob,
                "loglik": self.loglik, "aic": self.aic, "bic": self.bic, "nobs": self.nobs}


def fit_markov_switching(returns: pd.Series, k: int = 2, freq: Literal["D", "W", "M"] = "W",
                         search_reps: int = 20, seed: int = 12345) -> MSResult:
    """Fit a ``k``-regime switching mean/variance model to ``returns``
    (decimal, one period per observation) and relabel regimes by volatility."""
    from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

    if k not in (2, 3):
        raise ValueError("k must be 2 or 3")
    r = returns.dropna().astype(float)
    if len(r) < 100:
        raise ValueError("Markov switching needs at least 100 return observations")
    y = 100.0 * r
    model = MarkovRegression(y.to_numpy(), k_regimes=k, trend="c", switching_variance=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = model.fit(search_reps=search_reps, rng=seed, em_iter=10, maxiter=500, disp=False)
    names = list(model.param_names)
    params = np.asarray(res.params)
    const = np.array([params[names.index(f"const[{i}]")] for i in range(k)])
    sig2 = np.array([params[names.index(f"sigma2[{i}]")] for i in range(k)])
    # statsmodels: regime_transition[j, i] = P(S_t = j | S_{t-1} = i)
    P = np.asarray(res.regime_transition)[:, :, 0].T
    order = np.argsort(sig2)
    const, sig2 = const[order], sig2[order]
    P = P[np.ix_(order, order)]
    sm_probs = np.asarray(res.smoothed_marginal_probabilities)[:, order]
    fl_probs = np.asarray(res.filtered_marginal_probabilities)[:, order]
    ppy = PERIODS_PER_YEAR[freq]
    cols = [f"regime_{i}" for i in range(k)]
    smoothed = pd.DataFrame(sm_probs, index=r.index, columns=cols)
    filtered = pd.DataFrame(fl_probs, index=r.index, columns=cols)
    cur = int(np.argmax(fl_probs[-1]))
    with np.errstate(divide="ignore"):
        dur = 1.0 / (1.0 - np.diag(P))
    notes = [f"{len(r)} {'daily' if freq == 'D' else 'weekly' if freq == 'W' else 'monthly'} returns; "
             f"regimes ordered by volatility (0 = calmest)",
             "filtered probabilities are real-time; smoothed use the full sample (look-ahead)"]
    return MSResult(
        k=k, freq=freq, means_ann=const / 100.0 * ppy, vols_ann=np.sqrt(sig2 * ppy) / 100.0,
        means=const, sigmas=np.sqrt(sig2), transition=P, expected_duration=dur,
        smoothed=smoothed, filtered=filtered, current_regime=cur, current_prob=float(fl_probs[-1, cur]),
        loglik=float(res.llf), aic=float(res.aic), bic=float(res.bic), nobs=int(res.nobs), notes=notes)


def percentile_rank(history: pd.Series, value: float | None = None) -> float:
    """Fraction of ``history`` observations ``<= value`` (default: last value)."""
    h = history.dropna()
    if h.empty:
        return float("nan")
    v = float(h.iloc[-1]) if value is None else float(value)
    return float((h.to_numpy() <= v).mean())


def risk_panel(vix: pd.Series | None = None, hy_oas: pd.Series | None = None,
               slope: pd.Series | None = None, index_prices: pd.Series | None = None,
               slope_label: str = "10y-2y", trend_window: int = 200,
               lookback_years: float | None = None) -> dict[str, Any]:
    """Transparent risk-on/off panel; each component documents its value,
    percentile and score, and the composite is their mean."""
    comps: list[dict[str, Any]] = []

    def window(s: pd.Series) -> pd.Series:
        s = s.dropna()
        if lookback_years is not None and not s.empty:
            s = s[s.index >= s.index[-1] - pd.DateOffset(days=int(365.25 * lookback_years))]
        return s

    for name, s, label in (("vix", vix, "VIX (Cboe, FRED VIXCLS)"),
                           ("hy_oas", hy_oas, "HY OAS (ICE BofA, percent)")):
        if s is None or s.dropna().empty:
            continue
        h = window(s)
        pct = percentile_rank(h)
        comps.append({"name": name, "label": label, "value": float(h.iloc[-1]),
                      "as_of": h.index[-1], "percentile": pct, "score": 1.0 - pct,
                      "history_start": h.index[0], "rule": "score = 1 - percentile rank in own history"})
    if slope is not None and not slope.dropna().empty:
        s = slope.dropna()
        v = float(s.iloc[-1])
        comps.append({"name": "curve_slope", "label": f"Treasury slope {slope_label} (pp)", "value": v,
                      "as_of": s.index[-1], "percentile": percentile_rank(window(s)),
                      "score": 1.0 if v > 0 else 0.0, "rule": "1 if slope > 0 else 0 (inversion)"})
    if index_prices is not None and len(index_prices.dropna()) > trend_window:
        p = index_prices.dropna()
        ma = p.rolling(trend_window).mean()
        v = float(p.iloc[-1] / ma.iloc[-1] - 1.0)
        comps.append({"name": "trend", "label": f"price vs {trend_window}d moving average", "value": v,
                      "as_of": p.index[-1], "percentile": None, "score": 1.0 if v > 0 else 0.0,
                      "rule": f"1 if price > {trend_window}-session SMA"})
    if not comps:
        raise ValueError("no inputs for the risk panel")
    composite = float(np.mean([c["score"] for c in comps]))
    return {"components": comps, "composite": composite,
            "state": "risk-on" if composite > 0.5 else "risk-off" if composite < 0.5 else "neutral"}
