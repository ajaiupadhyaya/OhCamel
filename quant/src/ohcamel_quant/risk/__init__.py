"""Market risk: VaR/ES models, out-of-sample backtesting, decomposition and stress tests.

Pure analytics over numpy/pandas (no I/O). Modules:

* :mod:`.core`          -- conventions, portfolio returns, covariance, t helpers
* :mod:`.var`           -- historical, Gaussian, Student-t, Cornish-Fisher, EWMA, EVT-POT
* :mod:`.garch`         -- GARCH(1,1)-t, GJR-GARCH(1,1,1)-t, filtered historical simulation
* :mod:`.backtest`      -- rolling OOS forecasts, Kupiec, Christoffersen, Basel, DQ, Z2
* :mod:`.decomposition` -- Euler VaR/ES, incremental/standalone VaR, beta, tracking error
* :mod:`.stress`        -- historical scenario replay and Kupiec (1998) conditional stress
"""

from .core import RiskEstimate, portfolio_returns

__all__ = ["RiskEstimate", "portfolio_returns"]
