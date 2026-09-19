"""Hand-derived tests for the charter's gates and the two reported metrics.

Every expected value is derived in a comment next to its assertion, from a
series small enough to work out with pen and paper (or, where the underlying
formula involves skew/kurtosis on five points, worked out with the same
formula the code runs, shown step by step). No test depends on unseeded
randomness: the one function that samples (`_block_bootstrap_sharpes`) is
driven either by a scripted stand-in for the RNG whose every draw is listed
here, or by `bootstrap_gate`'s own seeded `np.random.default_rng`.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ohcamel_research.battery import gates
from ohcamel_research.battery.gates import (
    THRESHOLDS,
    capacity,
    cost_sweep,
    psr_gate,
    regime_gate,
    turnover,
)


def daily(values: list[float], start: str = "2018-01-01") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx)


class ScriptedRng:
    """A stand-in for `np.random.default_rng` whose draws are a fixed,
    written-down script instead of real randomness, so a block-bootstrap
    resample can be traced by hand. `integers(n)` and `random()` are the only
    two calls `_block_bootstrap_sharpes` makes on its `rng` argument."""

    def __init__(self, integers_seq: list[int], random_seq: list[float]) -> None:
        self._integers = list(integers_seq)
        self._randoms = list(random_seq)
        self._i = 0
        self._r = 0

    def integers(self, n: int) -> int:
        v = self._integers[self._i]
        self._i += 1
        return v

    def random(self) -> float:
        v = self._randoms[self._r]
        self._r += 1
        return v


# ---------------------------------------------------------------------------
# psr_gate
# ---------------------------------------------------------------------------


def test_psr_gate_is_exactly_one_half_on_a_symmetric_series_and_fails():
    # Symmetric series around zero: r = [-0.02, -0.01, 0, 0.01, 0.02] (n=5).
    # mean = 0 exactly.
    # variance (ddof=1) = (0.02^2 + 0.01^2 + 0 + 0.01^2 + 0.02^2) / 4
    #                   = (0.0004 + 0.0001 + 0 + 0.0001 + 0.0004) / 4 = 0.00025
    # std = sqrt(0.00025) = 0.015811388...; sr = mean/std = 0/std = 0.
    # z_i = x_i/std gives z^2 = [1.6, 0.4, 0, 0.4, 1.6] (e.g. 0.02^2/0.00025=1.6),
    # and z is odd-symmetric, so skew = mean(z^3) = 0 exactly (odd powers cancel).
    # kurt = mean(z^4) = 2*(1.6^2 + 0.4^2)/5 = 2*(2.56+0.16)/5 = 5.44/5 = 1.088.
    # denom_sq = 1 - skew*sr + ((kurt-1)/4)*sr^2 = 1 - 0 + (0.088/4)*0 = 1.
    # z_stat = (sr - 0)*sqrt(n-1)/sqrt(denom_sq) = 0*sqrt(4)/1 = 0.
    # PSR = Phi(0) = 0.5 exactly.
    r = daily([-0.02, -0.01, 0.0, 0.01, 0.02])
    g = psr_gate(r)
    assert g.value == pytest.approx(0.5, abs=1e-9)
    assert g.passed is False  # 0.5 < THRESHOLDS.psr_min (0.70)
    assert g.detail["threshold"] == THRESHOLDS.psr_min == 0.70


def test_psr_gate_passes_a_hand_derived_positive_series():
    # r = [0.02, 0.03, 0.01, 0.04, 0.02] (n=5).
    # mean = (0.02+0.03+0.01+0.04+0.02)/5 = 0.12/5 = 0.024 exactly.
    # deviations: -0.004, 0.006, -0.014, 0.016, -0.004
    # squared:     0.000016, 0.000036, 0.000196, 0.000256, 0.000016 -> sum 0.00052
    # variance (ddof=1) = 0.00052/4 = 0.00013; std = sqrt(0.00013) = 0.0114017542...
    # sr = mean/std = 0.024/0.0114017542 = 2.10493925 (per-period Sharpe).
    # The skew/kurtosis correction on 5 points close to symmetric is small:
    # working the same formula through gives skew ~= 0.19430, kurt ~= 1.25160,
    # denom_sq ~= 0.86970, z_stat = sr*sqrt(4)/sqrt(denom_sq) ~= 4.5142,
    # PSR = Phi(4.5142) ~= 0.9999968 -- far past the 0.70 line either way.
    r = daily([0.02, 0.03, 0.01, 0.04, 0.02])
    g = psr_gate(r)
    assert g.value == pytest.approx(0.9999968228683818, abs=1e-9)
    assert g.passed is True


# ---------------------------------------------------------------------------
# bootstrap_gate / _block_bootstrap_sharpes
# ---------------------------------------------------------------------------


def test_block_bootstrap_sharpes_follows_a_scripted_resample_by_hand():
    # returns = [0.01, -0.02, 0.03, 0.00] (n=4), block=2 -> restart
    # probability p = 1/block = 0.5 at each step after the first.
    #
    # Scripted rng, one resample (n_samples=1):
    #   pos = rng.integers(4) -> 1                      (idx[0]=1, pos->2)
    #   t=1: rng.random() -> 0.9 (>=0.5, no restart)     (idx[1]=2, pos->3)
    #   t=2: rng.random() -> 0.1 (<0.5, restart)
    #        pos = rng.integers(4) -> 3                  (idx[2]=3, pos->0)
    #   t=3: rng.random() -> 0.9 (>=0.5, no restart)     (idx[3]=0, pos->1)
    # idx = [1, 2, 3, 0] -> resample = returns[idx] = [-0.02, 0.03, 0.00, 0.01]
    #
    # mean = (-0.02+0.03+0.00+0.01)/4 = 0.02/4 = 0.005
    # deviations: -0.025, 0.025, -0.005, 0.005
    # squared:     0.000625, 0.000625, 0.000025, 0.000025 -> sum 0.0013
    # variance (ddof=1) = 0.0013/3; std = sqrt(0.0013/3) = 0.0208166599946613...
    # sharpe = mean/std * sqrt(252) = 0.005/0.0208166599946613 * 15.87450787
    #        = 3.812933455813454
    returns = np.array([0.01, -0.02, 0.03, 0.00])
    rng = ScriptedRng(integers_seq=[1, 3], random_seq=[0.9, 0.1, 0.9])
    out = gates._block_bootstrap_sharpes(returns, n_samples=1, block=2, rng=rng)
    assert out.shape == (1,)
    assert out[0] == pytest.approx(3.812933455813454, abs=1e-9)


def test_bootstrap_gate_computes_the_lower_5th_percentile_and_passes(monkeypatch):
    # Stand in for the 1000 bootstrapped Sharpes with 5 fixed values, so the
    # percentile arithmetic (numpy's default linear interpolation,
    # rank = q/100 * (n-1)) is hand-checkable:
    # sorted sharpes = [-1.0, 0.5, 1.0, 2.0, 3.0]  (n=5)
    # p5:  rank = 0.05*4 = 0.2, i.e. 20% of the way from arr[0] to arr[1]:
    #        arr[0] + 0.2*(arr[1]-arr[0]) = -1.0 + 0.2*1.5 = -1.0 + 0.3 = -0.7
    # p50: rank = 0.50*4 = 2.0 (exact index 2) -> 1.0
    # p95: rank = 0.95*4 = 3.8 -> arr[3] + 0.8*(arr[4]-arr[3])
    #                           = 2.0 + 0.8*(3.0-2.0) = 2.8
    fixed = np.array([-1.0, 0.5, 1.0, 2.0, 3.0])
    monkeypatch.setattr(gates, "_block_bootstrap_sharpes", lambda *a, **k: fixed)
    g = gates.bootstrap_gate(daily([0.0] * 30), n_samples=5)
    assert g.value == pytest.approx(-0.7, abs=1e-9)
    assert g.detail["p50"] == pytest.approx(1.0, abs=1e-9)
    assert g.detail["p95"] == pytest.approx(2.8, abs=1e-9)
    assert g.passed is False  # -0.7 is not > 0


def test_bootstrap_gate_passes_when_the_lower_5th_percentile_is_positive(monkeypatch):
    # Same 5-point recipe, shifted so p5 comes out positive:
    # sorted sharpes = [0.2, 0.6, 1.0, 2.0, 3.0]
    # p5: rank=0.2 -> 0.2 + 0.2*(0.6-0.2) = 0.2 + 0.08 = 0.28
    fixed = np.array([0.2, 0.6, 1.0, 2.0, 3.0])
    monkeypatch.setattr(gates, "_block_bootstrap_sharpes", lambda *a, **k: fixed)
    g = gates.bootstrap_gate(daily([0.0] * 30), n_samples=5)
    assert g.value == pytest.approx(0.28, abs=1e-9)
    assert g.passed is True
    assert g.detail["n_samples"] == 5
    assert g.detail["block"] == THRESHOLDS.bootstrap_block


def test_bootstrap_gate_is_seeded_and_reproducible():
    rng_input = np.random.default_rng(2)
    r = daily(list(rng_input.normal(0.0005, 0.005, 60)))
    assert gates.bootstrap_gate(r, seed=7).value == gates.bootstrap_gate(r, seed=7).value
    assert gates.bootstrap_gate(r, seed=7).value != gates.bootstrap_gate(r, seed=8).value


# ---------------------------------------------------------------------------
# regime_gate
# ---------------------------------------------------------------------------


def test_regime_gate_compounds_two_days_per_regime_by_hand():
    # Two custom regimes, two trading days each -- small enough that the
    # compounded cumulative return's sign is unambiguous by hand:
    # up:   (1+0.01)*(1+0.02) - 1 = 1.01*1.02 - 1 = 1.0302 - 1   = 0.0302
    # down: (1-0.01)*(1-0.02) - 1 = 0.99*0.98 - 1  = 0.9702 - 1  = -0.0298
    regimes = {
        "up": (date(2021, 1, 4), date(2021, 1, 5)),
        "down": (date(2021, 2, 1), date(2021, 2, 2)),
    }
    idx = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-02-01", "2021-02-02"])
    r = pd.Series([0.01, 0.02, -0.01, -0.02], index=idx)
    g = regime_gate(r, regimes=regimes, min_positive=1)
    assert g.detail["covered"] == ["up", "down"]
    assert g.detail["by_regime"]["up"] == pytest.approx(0.0302, abs=1e-9)
    assert g.detail["by_regime"]["down"] == pytest.approx(-0.0298, abs=1e-9)
    assert g.value == 1.0  # only "up" is positive
    assert g.passed is True  # min_positive=1

    # Raise the bar to 2 positive regimes: same data now fails.
    assert regime_gate(r, regimes=regimes, min_positive=2).passed is False


def test_regime_gate_skips_regimes_the_series_does_not_cover():
    r = daily([0.0001] * 250, start="2023-01-02")  # inside 2023_recovery only
    g = regime_gate(r)
    assert g.detail["covered"] == ["2023_recovery"]
    assert g.passed is False  # one positive regime is not THRESHOLDS.regime_min_positive (3)
    assert g.detail["min_positive"] == THRESHOLDS.regime_min_positive == 3


# ---------------------------------------------------------------------------
# cost_sweep
# ---------------------------------------------------------------------------


def test_cost_sweep_reports_every_bps_level_and_never_gates():
    # run(level) returns a two-point series [0.02, -0.01 - 0.0002*level].
    # For any two points [a, b]: mean=(a+b)/2, std(ddof=1)=|a-b|/sqrt(2), so
    #   sharpe = mean/std*sqrt(252) = (a+b)/|a-b| * sqrt(252/2) = (a+b)/|a-b| * sqrt(126)
    # sqrt(126) = 11.224972160321824
    # level=0:  a+b=0.01,  |a-b|=0.03  -> 0.01/0.03*sqrt(126)  = 3.7416573867739413
    # level=5:  a+b=0.009, |a-b|=0.031 -> 0.009/0.031*sqrt(126)= 3.258862885254724
    # level=15: a+b=0.007, |a-b|=0.033 -> 0.007/0.033*sqrt(126)= 2.381054700674326
    # level=30: a+b=0.004, |a-b|=0.036 -> 0.004/0.036*sqrt(126)= 1.247219128924647
    calls: list[float] = []

    def run(level: float) -> pd.Series:
        calls.append(level)
        return daily([0.02, -0.01 - 0.0002 * level])

    g = cost_sweep(run)
    assert g.passed is None  # reported, never gated
    assert calls == [0.0, 5.0, 15.0, 30.0] == list(THRESHOLDS.cost_sweep_bps_round_trip)
    by = g.detail["sharpe_by_bps"]
    assert list(by) == ["0.0", "5.0", "15.0", "30.0"]
    assert by["0.0"] == pytest.approx(3.7416573867739413, abs=1e-9)
    assert by["5.0"] == pytest.approx(3.258862885254724, abs=1e-9)
    assert by["15.0"] == pytest.approx(2.381054700674326, abs=1e-9)
    assert by["30.0"] == pytest.approx(1.247219128924647, abs=1e-9)
    # Monotonically worse as the round-trip cost rises.
    assert by["30.0"] < by["15.0"] < by["5.0"] < by["0.0"]


# ---------------------------------------------------------------------------
# turnover
# ---------------------------------------------------------------------------


def test_turnover_annualises_the_half_sum_of_absolute_weight_changes():
    # Two symbols, four days:
    #        SPY   TLT
    # day0:  0.0   0.0   (no prior day -> excluded from the diff sum)
    # day1:  0.5   0.0   |0.5-0.0| + |0.0-0.0| = 0.5
    # day2:  0.5   0.3   |0.5-0.5| + |0.3-0.0| = 0.3
    # day3:  0.2   0.3   |0.2-0.5| + |0.3-0.3| = 0.3
    # sum of daily absolute changes = 0.5 + 0.3 + 0.3 = 1.1
    # one-way turnover = 1.1 / 2 = 0.55
    # annualised = 0.55 * 252 / 4 (four days in the sample) = 0.55 * 63 = 34.65
    dates = pd.bdate_range("2021-01-04", periods=4)
    weights = pd.DataFrame(
        {"SPY": [0.0, 0.5, 0.5, 0.2], "TLT": [0.0, 0.0, 0.3, 0.3]}, index=dates
    )
    assert turnover(weights) == pytest.approx(34.65, abs=1e-9)


def test_turnover_is_zero_when_weights_never_change():
    dates = pd.bdate_range("2021-01-04", periods=5)
    weights = pd.DataFrame({"SPY": [0.3] * 5}, index=dates)
    assert turnover(weights) == 0.0


# ---------------------------------------------------------------------------
# capacity
# ---------------------------------------------------------------------------


def test_capacity_is_the_median_of_per_trade_capital():
    # capital_i = 0.01 * adv_i / |trade_i|
    # 0.01*10,000,000/0.10 = 100,000/0.10 = 1,000,000
    # 0.01*4,000,000/0.05  = 40,000/0.05  =   800,000
    # 0.01*1,000,000/0.20  = 10,000/0.20  =    50,000
    # sorted: [50,000, 800,000, 1,000,000] -> median = 800,000
    trades = pd.Series([0.10, -0.05, 0.20])
    adv = pd.Series([10_000_000.0, 4_000_000.0, 1_000_000.0])
    assert capacity(trades, adv) == pytest.approx(800_000.0, abs=1e-6)


def test_capacity_ignores_trades_with_zero_weight_change():
    # Same three real trades as above, with two zero-Delta rows spliced in
    # whose ADV values are nonsense (999) -- they must not enter the median,
    # because a zero weight change is not a trade.
    trades = pd.Series([0.0, 0.10, -0.05, 0.20, 0.0])
    adv = pd.Series([999.0, 10_000_000.0, 4_000_000.0, 1_000_000.0, 999.0])
    assert capacity(trades, adv) == pytest.approx(800_000.0, abs=1e-6)


def test_capacity_averages_the_two_middle_values_for_an_even_count():
    # A fourth trade added to the set above:
    # 0.01*2,000,000/0.25 = 20,000/0.25 = 80,000
    # sorted: [50,000, 80,000, 800,000, 1,000,000]
    # even count -> median = (80,000 + 800,000)/2 = 440,000
    trades = pd.Series([0.10, -0.05, 0.20, 0.25])
    adv = pd.Series([10_000_000.0, 4_000_000.0, 1_000_000.0, 2_000_000.0])
    assert capacity(trades, adv) == pytest.approx(440_000.0, abs=1e-6)
