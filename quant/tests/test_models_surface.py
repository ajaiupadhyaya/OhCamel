"""Lane M, M8: surface-history metrics by hand."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from ohcamel_quant.models.surface_metrics import (
    day_metrics,
    interp_linear,
    interp_total_variance,
    vrp,
)

TERM = pd.DataFrame({"T": [0.05, 0.15], "atm_iv": [0.20, 0.30], "rr_25d": [-0.02, -0.04], "bf_25d": [0.01, 0.02],
                     "mf_var": [0.05, 0.10]})


def test_atm_interpolates_in_total_variance():
    # w1 = 0.2^2 x 0.05 = 0.002, w2 = 0.3^2 x 0.15 = 0.0135; T = 30/365 = 0.0821918;
    # w = 0.002 + 0.0115 x (0.0821918 - 0.05)/0.1 = 0.00570205; iv = sqrt(w/T) = 0.2633913.
    assert interp_total_variance(TERM, "atm_iv", 30 / 365) == pytest.approx(0.26339134382131846, rel=1e-12)


def test_no_extrapolation():
    assert interp_total_variance(TERM, "atm_iv", 0.5) is None and interp_linear(TERM, "rr_25d", 0.01) is None


def test_rr_bf_interpolate_linearly_in_T():
    x = (30 / 365 - 0.05) / 0.10
    assert interp_linear(TERM, "rr_25d", 30 / 365) == pytest.approx(-0.02 + x * (-0.02))


def test_day_metrics_and_vrp():
    m = day_metrics(TERM)
    assert m["atm_iv_30d"] == pytest.approx(0.26339134382131846) and m["atm_iv_90d"] is None  # 90d > 0.15: no extrap
    assert m["term_slope"] is None and m["n_slices"] == 2
    assert m["mf_var_30d"] == pytest.approx((0.05 * 0.05 * (0.15 - 30 / 365) + 0.15 * 0.10 * (30 / 365 - 0.05))
                                            / 0.10 / (30 / 365))  # Cboe total-variance interpolation
    assert vrp(0.04, 1e-4) == pytest.approx(0.04 - 252e-4)
    assert math.isnan(vrp(float("nan"), 1e-4))
