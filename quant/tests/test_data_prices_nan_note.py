"""MarketData.prices reports sessions dropped because one ticker's value is NaN."""

from __future__ import annotations

import numpy as np

from ohcamel_quant.data.market import Dataset, MarketData


def test_prices_notes_nan_dropped_sessions(market, monkeypatch):
    real = MarketData.ohlcv

    def patched(self, ticker, start=None, end=None):
        ds = real(self, ticker, start, end)
        if ticker.upper() == "TLT":
            df = ds.data.copy()
            df.loc[df.index[[10, 20]], "adj_close"] = np.nan
            return Dataset(df, ds.provenance)
        return ds

    clean = market.prices(["SPY", "TLT"])
    assert not any("dropped from the common-session join" in str(p.detail.get("note", ""))
                   for p in clean.provenance)
    monkeypatch.setattr(MarketData, "ohlcv", patched)
    ds = market.prices(["SPY", "TLT"])
    assert len(ds.data) == len(clean.data) - 2
    p = ds.provenance[-1]
    assert p.source == "derived" and p.detail["nan_tickers"] == {"TLT": 2}
    assert len(p.detail["dropped_sessions"]) == 2
    assert "2 session(s) dropped" in p.detail["note"] and "TLT (2)" in p.detail["note"]
