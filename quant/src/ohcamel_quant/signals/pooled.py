"""INC-02's one test, as the desk's public record states it (INC-02 pre-registration, *The test*).

The pooled incubation book's daily return over 500 sessions from the first fill; null: Sharpe <= 0;
rejected at one-sided 5% when the Probabilistic Sharpe Ratio against zero is at least 0.95. The power is
computed here, not quoted: this is ``research/tests/test_inc02_power.py``'s closed form (the Quant image does
not contain ``ohcamel_research``, so it is ported, and ``tests/test_signals_pooled.py`` pins both values to
what the pre-registration prints). The PSR is ``backtest.metrics.probabilistic_sharpe``, Bailey and Lopez de
Prado's formula, the one the research battery's ``psr_gate`` evaluates, with the sample skewness and
kurtosis.

Both ``psr`` and ``sharpe_se`` are published, and together they determine the Sharpe estimate exactly:
PSR = Phi(SR_hat sqrt(n - 1) / sqrt(d)) and se = sqrt(252 d / (n - 1)) give z * se = SR_hat * sqrt(252), with
z = Phi^-1(PSR). So the annualised Sharpe estimate is public by construction; that is the test's own
statistic, the one thing the page exists to state, and it is not a return, equity, position or size.

Nothing returned here is a return, an equity, a position or a size: counts, a probability, a standard error
(of the annualised Sharpe estimate), the power and a verdict word.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import stats

from ..backtest.metrics import probabilistic_sharpe

__all__ = ["ALPHA", "PERIODS", "SESSIONS", "analytic_power", "verdict_at", "psr_and_se"]

SESSIONS = 500
PERIODS = 252
ALPHA = 0.05
PSR_MIN = 1.0 - ALPHA   # the one-sided 5% test


def analytic_power(sharpe_annual: float, n: int = SESSIONS, alpha: float = ALPHA) -> float:
    """P(PSR(0) >= 1 - alpha) for iid normal returns of true annualised Sharpe ``sharpe_annual``:
    the research test's closed form (Lo 2002; Bailey and Lopez de Prado 2012)."""
    sr = sharpe_annual / math.sqrt(PERIODS)
    z = float(stats.norm.ppf(1.0 - alpha))
    se = math.sqrt((1.0 + sr * sr / 2.0) / (n - 1))
    critical = z * se
    return float(1.0 - stats.norm.cdf((critical - sr) / se))


def psr_and_se(returns: Sequence[float]) -> tuple[float | None, float | None]:
    """``(psr, sharpe_se)`` of daily returns: PSR against zero with the sample skewness and (raw) kurtosis, and
    the standard error of the annualised Sharpe estimate from the same denominator,
    ``sqrt(252 (1 - g3 SR + (g4 - 1)/4 SR^2) / (n - 1))``. None for each when undefined (under four returns,
    no spread, a non-positive denominator)."""
    x = np.asarray(list(returns), dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 4:
        return None, None
    sd = float(np.std(x, ddof=1))
    if sd <= 0.0:
        return None, None
    sr = float(np.mean(x)) / sd
    g3 = float(stats.skew(x, bias=True))
    g4 = float(stats.kurtosis(x, fisher=False, bias=True))
    d = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr
    psr = probabilistic_sharpe(sr, n, g3, g4, 0.0)
    if d <= 0.0 or not math.isfinite(psr):
        return None, None
    return round(psr, 6), round(math.sqrt(PERIODS * d / (n - 1)), 6)


def verdict_at(sessions_done: int, psr: float | None) -> str:
    """``IN PROGRESS`` until the 500th session, then the one evaluation: ``REJECTED NULL`` at PSR >= 0.95."""
    if sessions_done < SESSIONS:
        return "IN PROGRESS"
    return "REJECTED NULL" if psr is not None and psr >= PSR_MIN else "NOT REJECTED"
