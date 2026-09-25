"""The book marked to live quotes (deck/live.py): drifted weights, drawdown
with today appended, and VaR at the live weights with its Euler split."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.deck.live import drawdown, live_weights, risk_at


def test_weights_drift_with_the_day_and_cash_takes_the_rest():
    # SPY +1 %, TLT -2 %, 10 % cash: day = 0.5(0.01) + 0.4(-0.02) = -0.003
    lw, day, missing = live_weights({"SPY": 0.5, "TLT": 0.4}, {"SPY": 0.01, "TLT": -0.02})
    assert day == pytest.approx(-0.003)
    assert lw["SPY"] == pytest.approx(0.5 * 1.01 / 0.997)   # 0.506519...
    assert lw["TLT"] == pytest.approx(0.4 * 0.98 / 0.997)   # 0.393180...
    assert 1 - sum(lw.values()) == pytest.approx(0.1 / 0.997)  # cash is unchanged in dollars
    assert missing == []


def test_a_missing_move_is_held_at_zero_and_named():
    lw, day, missing = live_weights({"SPY": 0.5, "TLT": 0.5}, {"SPY": 0.02, "TLT": float("nan")})
    assert missing == ["TLT"] and day == pytest.approx(0.01)
    assert lw["TLT"] == pytest.approx(0.5 / 1.01)


def test_a_book_wiped_out_by_the_day_is_refused():
    with pytest.raises(ValueError, match="not positive"):
        live_weights({"X": 2.0}, {"X": -0.6})                # 1 + 2(-0.6) = -0.2


def test_drawdown_appends_today_only_when_given():
    r = pd.Series([0.10, -0.20, 0.05])                        # wealth 1.1, 0.88, 0.924; peak 1.1
    assert drawdown(r, None) == pytest.approx(1 - 0.924 / 1.1)          # 0.16
    assert drawdown(r, 0.10) == pytest.approx(1 - 0.924 * 1.1 / 1.1)    # 1.0164 / 1.1 -> 0.076
    assert drawdown(pd.Series([0.05, 0.05]), None) == 0.0              # at the peak


def test_drawdown_counts_a_loss_from_the_starting_equity():
    assert drawdown(pd.Series([-0.1]), None) == pytest.approx(0.1)     # peak is the initial 1.0


def test_var_at_live_weights_and_its_euler_split_sum_to_it():
    # Two uncorrelated assets, daily vols 1 % and 2 %, weights 0.5 / 0.5:
    # sigma_p = sqrt(0.25e-4 + 1e-4) = 0.0111803; VaR95 = 1.644854 * that.
    cov = np.diag([1e-4, 4e-4])
    out = risk_at({"A": 0.5, "B": 0.5}, ["A", "B"], cov, 0.95)
    sp = math.sqrt(0.25 * 1e-4 + 0.25 * 4e-4)
    assert out["var"] == pytest.approx(1.6448536269514722 * sp)
    assert out["es"] == pytest.approx(math.exp(-1.6448536269514722 ** 2 / 2) / math.sqrt(2 * math.pi) / 0.05 * sp)
    assert sum(out["component"].values()) == pytest.approx(out["var"])
    # B carries 4x A's variance at equal weights: shares 0.2 and 0.8.
    assert out["pct"] == {"A": pytest.approx(0.2), "B": pytest.approx(0.8)}
