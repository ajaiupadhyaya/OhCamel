"""Execution cost: the Almgren-Chriss (2000) schedule and its cost-risk frontier.

Liquidate ``X`` shares over ``T`` in ``n`` intervals of ``tau = T / n``. Price moves with volatility
``sigma`` per unit-time^0.5 (price units); a trade of ``n_j`` in an interval moves the price
permanently by ``gamma n_j`` and costs a temporary ``eta n_j / tau`` per share on that interval's
trades, both linear (the paper's section 2, and the discrete model of section 3).

Formulas, with the numbering of Almgren & Chriss, *Optimal execution of portfolio transactions*,
J. Risk 3(2), 2000 (equation numbers as the paper prints them; this file cites them from the
paper's text and was written offline, so re-check them against the PDF before quoting):

* ``eta~ = eta - gamma tau / 2``                                              (eq. 15)
* ``cosh(kappa tau) = 1 + lambda sigma^2 tau^2 / (2 eta~)``                   (eq. 17-18's kappa)
* holdings ``x_j = X sinh(kappa (T - t_j)) / sinh(kappa T)``                  (eq. 18)
* trades ``n_j = x_{j-1} - x_j``                                              (eq. 19)
* ``E = gamma X^2 / 2 + epsilon X + (eta~ / tau) sum_j n_j^2``                (eq. 20)
* ``V = sigma^2 tau sum_{j=1..n} x_j^2``                                      (eq. 21)

``epsilon`` is the fixed cost per share (half-spread, fees). **It is not included here:** the
signature has no ``epsilon``, so ``expected_cost`` is the impact cost only, ``epsilon = 0``. Add
``epsilon * |X|`` to compare with an all-in figure.

``lambda = 0`` is the risk-neutral limit, where ``kappa -> 0`` and the schedule is the TWAP (equal
slices); it is handled as that limit, not by dividing zero by zero. A sell is the mirror of a buy,
so ``X`` is taken by magnitude and ``trades`` are the (positive) sizes to execute each interval.

``eta`` and ``gamma`` are ASSUMPTIONS the caller states, not estimates; ``sigma`` should be
estimated from real data and its source stated. Costs come out in price-units x shares (dollars when
``sigma``, ``eta`` and ``gamma`` are in dollars).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Below this kappa T the hyperbolic ratios are the TWAP's to machine precision.
_TWAP_KAPPA_T = 1e-7


@dataclass(frozen=True)
class ACSchedule:
    trades: np.ndarray        # n positive trade sizes, in time order
    expected_cost: float      # E, impact only (epsilon = 0)
    variance: float           # V
    kappa: float              # per unit time; 0 at the TWAP limit


def almgren_chriss(X: float, T: float, n: int, *, sigma: float, eta: float, gamma: float,
                   lam: float) -> ACSchedule:
    if n < 1 or int(n) != n:
        raise ValueError("n must be a positive integer number of intervals")
    if T <= 0 or sigma < 0 or eta <= 0 or gamma < 0 or lam < 0:
        raise ValueError("need T > 0, sigma >= 0, eta > 0, gamma >= 0, lambda >= 0")
    n = int(n)
    tau = T / n
    eta_t = eta - gamma * tau / 2.0
    if eta_t <= 0:
        raise ValueError("eta - gamma tau / 2 must be positive: the permanent impact is too large for this interval")
    size = abs(float(X))
    arg = lam * sigma**2 * tau**2 / (2.0 * eta_t)
    kappa = math.acosh(1.0 + arg) / tau if arg > 0 else 0.0
    j = np.arange(n + 1)
    if kappa * T < _TWAP_KAPPA_T:
        kappa = 0.0
        x = size * (1.0 - j / n)
    else:
        x = size * np.sinh(kappa * (T - j * tau)) / math.sinh(kappa * T)
    x[-1] = 0.0
    trades = x[:-1] - x[1:]
    cost = 0.5 * gamma * size**2 + eta_t / tau * float(np.sum(trades**2))
    variance = sigma**2 * tau * float(np.sum(x[1:] ** 2))
    return ACSchedule(trades=trades, expected_cost=cost, variance=variance, kappa=kappa)


def frontier(X: float, T: float, n: int, *, sigma: float, eta: float, gamma: float,
             lams: Sequence[float]) -> pd.DataFrame:
    """Expected cost against variance across risk aversions: one row per lambda, in the order given."""
    rows = []
    for lam in lams:
        s = almgren_chriss(X, T, n, sigma=sigma, eta=eta, gamma=gamma, lam=lam)
        rows.append({"lam": float(lam), "expected_cost": s.expected_cost, "variance": s.variance,
                     "std": math.sqrt(s.variance)})
    return pd.DataFrame(rows, columns=["lam", "expected_cost", "variance", "std"])
