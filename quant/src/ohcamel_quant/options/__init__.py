"""Options & volatility analytics on real listed chains (pure functions, no I/O).

Modules: :mod:`.bsm` (BSM / Black-76 prices, greeks, implied vol), :mod:`.parity`
(implied forward, discount, r, q; expiry timing), :mod:`.chain` (cleaning and
per-quote analytics), :mod:`.svi` (raw SVI calibration and arbitrage checks),
:mod:`.surface` (per-expiry fits and term structure), :mod:`.metrics` (ATM,
RR/BF, skew, Cboe model-free variance, implied moves), :mod:`.density`
(Breeden-Litzenberger), :mod:`.realized` (OHLC estimators, cone, VRP) and
:mod:`.strategy` (multi-leg positions).
"""
