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
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from ohcamel_research.battery import gates
from ohcamel_research.battery.gates import (
    REGIMES,
    THRESHOLDS,
    Thresholds,
    capacity,
    cost_sweep,
    psr_gate,
    regime_gate,
    turnover,
)

EXP_A01_CONFIG = Path(__file__).resolve().parents[1] / "experiments" / "EXP-A01" / "config.yaml"


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


def test_psr_gate_passes_at_exactly_the_threshold(monkeypatch):
    # Pins strictness: the gate is `psr >= threshold`, so a PSR of exactly
    # 0.70 must pass. Monkeypatching the `probabilistic_sharpe` name
    # `gates.py` calls (rather than constructing a series that happens to
    # land on 0.70) isolates the comparison itself -- a `>=` -> `>` flip
    # fails this test regardless of what the real PSR formula does.
    monkeypatch.setattr(gates, "probabilistic_sharpe", lambda *a, **k: 0.70)
    g = psr_gate(daily([0.01, -0.01, 0.02, -0.02, 0.03]))
    assert g.value == pytest.approx(0.70, abs=1e-12)
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


def test_block_bootstrap_sharpes_scripts_a_restart_and_a_continuation():
    # block=2's restart probability is 1/2, the same as its complement
    # (1 - 1/2), so it cannot tell a geometric block from a fixed one or from
    # p=1-1/block. block=4 can: p = 1/4 = 0.25, distinct from 1-1/4=0.75.
    # returns = [0.01, -0.02, 0.03] (n=3), so the loop only needs draws at
    # t=1 and t=2 (t=0 only draws the initial position).
    #
    # Scripted rng, one resample (n_samples=1):
    #   pos = rng.integers(3) -> 1                        (idx[0]=1, pos->2)
    #   t=1: rng.random() -> 0.2 (< 0.25, restart)
    #        pos = rng.integers(3) -> 0                    (idx[1]=0, pos->1)
    #   t=2: rng.random() -> 0.3 (>= 0.25, no restart)      (idx[2]=1, pos->2)
    # idx = [1, 0, 1] -> resample = returns[idx] = [-0.02, 0.01, -0.02]
    #
    # This distinguishes the geometric bootstrap from two mutations:
    # - Fixed-length blocks of length 4 (>= n=3) would never restart inside
    #   a 3-long sample, giving idx=[1,2,0] instead and consuming only the
    #   initial integers() draw -- no random() draws at all.
    # - p=1-1/block=0.75 would also restart at t=2 (0.3 < 0.75), consuming a
    #   third integers() draw this script does not provide.
    # Both were tried by hand against this exact script in a scratch copy of
    # gates.py: the fixed-block mutant leaves random_seq entirely unconsumed,
    # and the p=1-1/block mutant raises IndexError pulling a third integers()
    # value. Both fail this test.
    #
    # mean = (-0.02+0.01-0.02)/3 = -0.03/3 = -0.01 exactly
    # deviations: -0.01, 0.02, -0.01; squared: 0.0001, 0.0004, 0.0001 -> 0.0006
    # variance (ddof=1) = 0.0006/2 = 0.0003; std = sqrt(0.0003) = sqrt(3)/100
    # sharpe = mean/std * sqrt(252) = (-1/100)/(sqrt(3)/100) * 6*sqrt(7)
    #        = -6*sqrt(7)/sqrt(3) = -6*sqrt(7/3) = -2*sqrt(21)
    #        = -9.16515138991168
    returns = np.array([0.01, -0.02, 0.03])
    rng = ScriptedRng(integers_seq=[1, 0], random_seq=[0.2, 0.3])
    out = gates._block_bootstrap_sharpes(returns, n_samples=1, block=4, rng=rng)
    assert out.shape == (1,)
    assert out[0] == pytest.approx(-9.16515138991168, abs=1e-9)
    # Exactly two integers() draws (the initial position, plus one restart)
    # and two random() draws (checked at t=1 and t=2) -- no more, no fewer.
    assert rng._i == 2
    assert rng._r == 2


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
    g = gates.bootstrap_gate(daily([0.0] * 30), n_samples=5, seed=0)
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
    g = gates.bootstrap_gate(daily([0.0] * 30), n_samples=5, seed=0)
    assert g.value == pytest.approx(0.28, abs=1e-9)
    assert g.passed is True
    assert g.detail["n_samples"] == 5
    assert g.detail["block"] == THRESHOLDS.bootstrap_block


def test_bootstrap_gate_fails_when_the_lower_5th_percentile_is_exactly_zero(monkeypatch):
    # Pins strictness the other way: the gate is `lo > 0.0`, so a lower 5th
    # percentile of exactly 0.0 must fail. Scripting the sampler to return a
    # percentile of exactly 0.0 isolates the comparison -- a `>` -> `>=` flip
    # is the only way this test can pass.
    fixed = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(gates, "_block_bootstrap_sharpes", lambda *a, **k: fixed)
    g = gates.bootstrap_gate(daily([0.0] * 30), n_samples=5, seed=0)
    assert g.value == pytest.approx(0.0, abs=1e-12)
    assert g.passed is False  # 0.0 is not > 0.0


def test_bootstrap_gate_is_seeded_and_reproducible():
    rng_input = np.random.default_rng(2)
    r = daily(list(rng_input.normal(0.0005, 0.005, 60)))
    assert gates.bootstrap_gate(r, seed=7).value == gates.bootstrap_gate(r, seed=7).value
    assert gates.bootstrap_gate(r, seed=7).value != gates.bootstrap_gate(r, seed=8).value


def test_bootstrap_gate_defaults_to_the_charters_resample_count_and_block():
    # The defaults are the table's, never a literal of their own.
    r = daily(list(np.random.default_rng(3).normal(0.0005, 0.005, 60)))
    d = gates.bootstrap_gate(r, seed=7).detail
    assert d["n_samples"] == THRESHOLDS.bootstrap_resamples
    assert d["block"] == THRESHOLDS.bootstrap_block


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


def test_regime_gate_reports_bar_counts_including_zero_for_skipped_regimes():
    # M5 is disclosure only -- no coverage threshold -- but the verdict must
    # show coverage: every regime in the input dict gets a bar count, 0 for
    # one the series never reaches.
    r = daily([0.0001] * 250, start="2023-01-02")  # inside 2023_recovery only
    g = regime_gate(r)
    assert set(g.detail["bar_counts"]) == set(REGIMES)
    assert g.detail["bar_counts"]["2023_recovery"] == 250
    for name in REGIMES:
        if name != "2023_recovery":
            assert g.detail["bar_counts"][name] == 0


def test_regime_gate_excludes_regimes_that_are_not_truly_positive():
    # Three regimes built so that counting "positive" by cum >= 0, by a
    # majority of up days, or by the arithmetic sum of daily returns would
    # each wrongly call one of them positive, while genuine compounding
    # (strictly > 0) correctly calls none of them positive.
    regimes = {
        "cash": (date(2021, 3, 1), date(2021, 3, 3)),
        "mostly_up_days_net_down": (date(2021, 4, 1), date(2021, 4, 5)),
        "sum_positive_compounds_negative": (date(2021, 5, 3), date(2021, 5, 4)),
    }
    idx = pd.to_datetime(
        [
            "2021-03-01",
            "2021-03-02",
            "2021-03-03",  # cash: flat all regime
            "2021-04-01",
            "2021-04-02",
            "2021-04-05",  # 2 of 3 days up, net down
            "2021-05-03",
            "2021-05-04",  # daily sum positive, compounds negative
        ]
    )
    r = pd.Series(
        [0.0, 0.0, 0.0, 0.01, 0.01, -0.03, 0.10, -0.095],
        index=idx,
    )
    g = regime_gate(r, regimes=regimes, min_positive=1)

    # cash: cum = 0.0 exactly. Cash through a whole regime is not a positive
    # one -- "cum >= 0" would wrongly count it, "cum > 0" correctly does not.
    assert g.detail["by_regime"]["cash"] == pytest.approx(0.0, abs=1e-12)

    # mostly_up_days_net_down: (1.01*1.01*0.97) - 1 = -0.010503. Two of the
    # three days are up, so "majority of day signs" would wrongly count this
    # as positive; compounding correctly does not.
    assert g.detail["by_regime"]["mostly_up_days_net_down"] == pytest.approx(-0.010503, abs=1e-9)

    # sum_positive_compounds_negative: 1.10*0.905 - 1 = -0.0045, while the
    # arithmetic sum 0.10 + (-0.095) = +0.005 is positive. "Arithmetic sum"
    # would wrongly count this as positive; compounding correctly does not.
    assert g.detail["by_regime"]["sum_positive_compounds_negative"] == pytest.approx(
        -0.0045, abs=1e-12
    )

    assert g.value == 0.0  # none of the three genuinely compounds positive
    assert g.passed is False  # 0 < min_positive=1


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


def test_cost_sweep_normalises_integer_levels_to_the_same_keys_as_floats():
    # EXP-A01's config.yaml stores its levels as YAML integers ([0, 5, 15,
    # 30]); THRESHOLDS' default is floats. Both must key the sweep the same
    # way, or a report reading one against the other misses every level.
    def run(level: float) -> pd.Series:
        return daily([0.02, -0.01])

    g = cost_sweep(run, bps_levels=(0, 5, 15, 30))
    assert list(g.detail["sharpe_by_bps"]) == ["0.0", "5.0", "15.0", "30.0"]


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
    # annualised = 0.55 * 252 / (4-1): N-1=3 is the number of Delta-weight
    # terms actually summed (day0 is dropped, leaving days 1-3) -- not N=4.
    #            = 0.55 * 252/3 = 0.55 * 84 = 46.2
    dates = pd.bdate_range("2021-01-04", periods=4)
    weights = pd.DataFrame({"SPY": [0.0, 0.5, 0.5, 0.2], "TLT": [0.0, 0.0, 0.3, 0.3]}, index=dates)
    assert turnover(weights) == pytest.approx(46.2, abs=1e-9)


def test_turnover_is_zero_when_weights_never_change():
    dates = pd.bdate_range("2021-01-04", periods=5)
    weights = pd.DataFrame({"SPY": [0.3] * 5}, index=dates)
    assert turnover(weights) == 0.0


def test_turnover_counts_a_new_position_entering_from_nan_as_a_change():
    # A second symbol only starts trading on day2: its earlier weight is NaN
    # (no position held yet), not 0.0. `fillna(0)` must run before `.diff()`
    # so that entry is counted as a change from 0 -- not dropped as "no
    # change" the way `skipna=True` would drop a genuine NaN-vs-NaN diff.
    #        SPY   NEW
    # day0:  0.5   NaN   (no prior day -> excluded from the diff sum)
    # day1:  0.5   NaN   |0.5-0.5| + |0-0| = 0.0            (NEW: NaN->NaN filled to 0->0)
    # day2:  0.5   0.4   |0.5-0.5| + |0.4-0| = 0.4           (NEW enters at 0.4, from 0)
    # sum of daily absolute changes = 0.0 + 0.4 = 0.4; one-way = 0.4/2 = 0.2
    # annualised = 0.2 * 252/(3-1) = 0.2 * 126 = 25.2
    #
    # Without fillna(0) before diff, NEW's day1 and day2 diffs are both
    # NaN-vs-something-that-was-NaN, which `.sum(axis=1, skipna=True)` drops
    # entirely -- the entry vanishes and turnover reads 0.0 instead of 25.2.
    dates = pd.bdate_range("2021-01-04", periods=3)
    weights = pd.DataFrame({"SPY": [0.5, 0.5, 0.5], "NEW": [np.nan, np.nan, 0.4]}, index=dates)
    assert turnover(weights) == pytest.approx(25.2, abs=1e-9)


def test_turnover_is_none_for_fewer_than_two_rows():
    # One row gives no Delta-weight term, so the rate is undefined; 0.0 would
    # read as "never traded". Empty is the same.
    dates = pd.bdate_range("2021-01-04", periods=1)
    assert turnover(pd.DataFrame({"SPY": [0.5]}, index=dates)) is None
    assert turnover(pd.DataFrame({"SPY": []})) is None


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


def test_capacity_returns_none_when_nothing_traded():
    # 0.0 would read as "zero capacity" (nothing could be traded); the true
    # answer for an untraded frame is "no trades to measure capacity from".
    dates = pd.bdate_range("2021-01-04", periods=3)
    trades = pd.Series([0.0, 0.0, 0.0], index=dates)
    adv = pd.Series([10_000_000.0, 4_000_000.0, 1_000_000.0], index=dates)
    assert capacity(trades, adv) is None


def test_capacity_raises_on_a_nan_adv_for_a_traded_day():
    dates = pd.bdate_range("2021-01-04", periods=3)
    trades = pd.Series([0.10, 0.05, 0.20], index=dates)
    adv = pd.Series([10_000_000.0, np.nan, 1_000_000.0], index=dates)
    with pytest.raises(ValueError, match=str(dates[1].date())):
        capacity(trades, adv)


def test_capacity_names_the_first_traded_date_with_unknown_adv():
    dates = pd.bdate_range("2021-01-04", periods=4)
    # day0 is untraded, so its NaN ADV must not matter; day1 is the first
    # traded day with an unknown ADV, and must be the one named.
    trades = pd.Series([0.0, 0.10, 0.05, 0.20], index=dates)
    adv = pd.Series([np.nan, np.nan, 10_000_000.0, np.nan], index=dates)
    with pytest.raises(ValueError, match=str(dates[1].date())):
        capacity(trades, adv)


def test_capacity_raises_on_a_traded_day_absent_from_the_adv_series():
    # An ADV built with rolling(20).mean().dropna() omits its warm-up days;
    # a trade on an omitted day is an unknown ADV, not a row to skip. Trades
    # on day0 and day1, ADV only for day0: without the reindex the median
    # would quietly read 1,000,000 from day0 alone.
    dates = pd.bdate_range("2021-01-04", periods=2)
    trades = pd.Series([0.10, 0.05], index=dates)
    adv = pd.Series([10_000_000.0], index=dates[:1])
    with pytest.raises(ValueError, match=str(dates[1].date())):
        capacity(trades, adv)


def test_capacity_raises_on_an_unknown_trade():
    # NaN > 0 is False, so without the check a NaN Delta-weight would read as
    # "no trade" -- the same skip turnover's fillna(0) exists to prevent.
    dates = pd.bdate_range("2021-01-04", periods=3)
    trades = pd.Series([np.nan, 0.10, 0.20], index=dates)
    adv = pd.Series([10_000_000.0] * 3, index=dates)
    with pytest.raises(ValueError, match=str(dates[0].date())):
        capacity(trades, adv)


# ---------------------------------------------------------------------------
# THRESHOLDS: one table, pinned to the charter, read by EXP-A01's config too
# ---------------------------------------------------------------------------


def test_thresholds_matches_the_charter_exactly():
    # docs/CHARTER.md's gate table, literally. Changing any of these values
    # is the owner's decision, argued before a result -- not a passing test's
    # to loosen quietly by drifting one field.
    assert THRESHOLDS == Thresholds(
        psr_min=0.70,
        dsr_min=0.30,
        bootstrap_resamples=1000,
        bootstrap_block=21,
        bootstrap_lower_percentile=5.0,
        regime_min_positive=3,
        cost_sweep_bps_round_trip=(0.0, 5.0, 15.0, 30.0),
        pbo_fragile_above=0.5,
    )


def test_exp_a01_config_agrees_with_thresholds():
    # The pre-registration is not this module's to edit (it is EXP-A01's own
    # frozen decision), but it must agree with the same table this module
    # gates by -- otherwise the report and the run it describes could read
    # different cost sweeps or a different bootstrap resample count.
    config = yaml.safe_load(EXP_A01_CONFIG.read_text())
    assert tuple(float(b) for b in config["cost_sweep_bps_round_trip"]) == (
        THRESHOLDS.cost_sweep_bps_round_trip
    )
    assert config["bootstrap"]["resamples"] == THRESHOLDS.bootstrap_resamples
