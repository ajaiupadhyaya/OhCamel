"""INC-02's printed power, computed rather than quoted (completion spec, Research, *The test*).

The test: the pooled incubation book's daily net return over 500 sessions; null Sharpe <= 0;
reject at one-sided 5% when the Probabilistic Sharpe Ratio against zero is at least 0.95 -- the
research battery's own ``psr_gate``. The pre-registration prints about 41% power against a true
annualised Sharpe of 1.0 and about 17% against 0.5. Both are checked two ways here, under the
assumptions the pre-registration states (independent, normally distributed daily returns, 252
sessions a year):

* analytically -- under normality PSR's denominator is ``1 + SR^2 / 2`` and the estimated
  per-session Sharpe is asymptotically normal with variance ``(1 + SR^2 / 2) / (n - 1)`` (Lo 2002;
  Bailey & Lopez de Prado 2012), so the rejection region
  ``SR_hat >= z_0.95 sqrt((1 + SR_hat^2/2) / (n - 1))`` has a closed-form probability;
* by simulation -- 10,000 seeded paths of 500 sessions each, judged by ``psr_gate`` itself,
  sample skew and kurtosis included.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from ohcamel_research import REPO_ROOT
from ohcamel_research.battery.gates import psr_gate

SESSIONS = 500
PERIODS = 252
ALPHA = 0.05
PRINTED = {1.0: 0.41, 0.5: 0.17}  # annualised true Sharpe -> the power INC-02 prints


def analytic_power(sharpe_annual: float, n: int = SESSIONS, alpha: float = ALPHA) -> float:
    """P(PSR(0) >= 1 - alpha) for iid normal returns, true annualised Sharpe ``sharpe_annual``."""
    sr = sharpe_annual / math.sqrt(PERIODS)
    z = norm.ppf(1.0 - alpha)
    se = math.sqrt((1.0 + sr * sr / 2.0) / (n - 1))
    # Reject when SR_hat * sqrt(n - 1) / sqrt(1 + SR_hat^2 / 2) >= z; to first order the
    # denominator is evaluated at the true SR (its curvature moves the answer by under 0.1 point
    # at these sizes).
    critical = z * math.sqrt((1.0 + sr * sr / 2.0) / (n - 1))
    return float(1.0 - norm.cdf((critical - sr) / se))


def simulated_power(sharpe_annual: float, *, paths: int = 10_000, seed: int = 20261115) -> float:
    rng = np.random.default_rng(seed)
    sigma = 0.01
    mu = sharpe_annual / math.sqrt(PERIODS) * sigma
    draws = rng.standard_normal((paths, SESSIONS)) * sigma + mu
    return float(np.mean([psr_gate(pd.Series(x)).passed for x in draws]))


@pytest.mark.parametrize("sharpe", sorted(PRINTED))
def test_the_printed_power_is_the_analytic_power(sharpe: float) -> None:
    assert round(analytic_power(sharpe), 2) == PRINTED[sharpe]


@pytest.mark.parametrize("sharpe", sorted(PRINTED))
def test_the_printed_power_is_what_the_battery_rejects_in_simulation(sharpe: float) -> None:
    """10,000 paths: the simulation's standard error is under half a point, so two points is a
    wide band."""
    assert simulated_power(sharpe) == pytest.approx(PRINTED[sharpe], abs=0.02)


def test_the_null_rejects_about_five_percent_of_the_time() -> None:
    """At a true Sharpe of zero the test's size is its stated 5%."""
    assert simulated_power(0.0) == pytest.approx(ALPHA, abs=0.01)


def test_the_preregistration_prints_these_numbers() -> None:
    text = (REPO_ROOT / "research" / "experiments" / "INC-02" / "preregistration.md").read_text()
    assert "research/tests/test_inc02_power.py" in text
    for sharpe, power in PRINTED.items():
        assert f"{power:.0%}" in text, f"INC-02 does not print {power:.0%} at Sharpe {sharpe}"
