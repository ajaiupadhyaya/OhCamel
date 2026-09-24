"""Hamilton Markov switching on REAL SPY returns; risk-on/off panel."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.macro import regimes as rg


@pytest.fixture(scope="module")
def spy(market):
    return market.ohlcv("SPY").data["adj_close"]


@pytest.fixture(scope="module")
def ms_weekly(spy):
    return rg.fit_markov_switching(rg.resample_returns(spy, "W"), k=2, freq="W")


def test_weekly_high_vol_regime_contains_march_2020(ms_weekly):
    ms = ms_weekly
    assert ms.vols_ann[1] > 1.8 * ms.vols_ann[0]  # regimes ordered by vol
    march = ms.smoothed.loc["2020-03-01":"2020-03-31", "regime_1"]
    assert len(march) >= 4 and (march > 0.9).all()
    assert ms.filtered.loc["2020-03-13":"2020-03-31", "regime_1"].mean() > 0.8
    # calm regime dominates a quiet stretch
    assert ms.smoothed.loc["2017-03-01":"2017-10-31", "regime_0"].mean() > 0.8
    # proper stochastic matrix, durations 1/(1-p_ii)
    assert np.allclose(ms.transition.sum(axis=1), 1.0)
    assert np.allclose(ms.expected_duration, 1 / (1 - np.diag(ms.transition)))
    assert np.allclose(ms.smoothed.sum(axis=1), 1.0)
    assert ms.current_regime in (0, 1) and 0.5 <= ms.current_prob <= 1.0


def test_deterministic_given_seed(spy, ms_weekly):
    again = rg.fit_markov_switching(rg.resample_returns(spy, "W"), k=2, freq="W")
    assert np.allclose(again.vols_ann, ms_weekly.vols_ann)
    assert again.loglik == pytest.approx(ms_weekly.loglik)


def test_three_regimes_ordered(spy):
    ms = rg.fit_markov_switching(rg.resample_returns(spy, "W"), k=3, freq="W")
    assert np.all(np.diff(ms.vols_ann) > 0)
    assert ms.smoothed.loc["2020-03-09":"2020-03-31"].idxmax(axis=1).eq("regime_2").all()


def test_resample_and_validation(spy):
    w = rg.resample_returns(spy, "W")
    assert (w.index.dayofweek == 4).all()
    m = rg.resample_returns(spy, "M")
    assert 110 < len(m) < 125
    with pytest.raises(ValueError):
        rg.fit_markov_switching(w, k=4)
    with pytest.raises(ValueError):
        rg.fit_markov_switching(w.iloc[:50])


def test_percentile_rank_and_panel(market, spy):
    h = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert rg.percentile_rank(h) == 1.0
    assert rg.percentile_rank(h, 2.5) == 0.5
    macro = market.fred(["VIXCLS", "DGS10", "DGS2"]).data
    slope = (macro["DGS10"] - macro["DGS2"]).dropna()
    p = rg.risk_panel(vix=macro["VIXCLS"], slope=slope, index_prices=spy)
    names = [c["name"] for c in p["components"]]
    assert names == ["vix", "curve_slope", "trend"]
    vix = next(c for c in p["components"] if c["name"] == "vix")
    assert vix["score"] == pytest.approx(1 - rg.percentile_rank(macro["VIXCLS"]))
    assert p["composite"] == pytest.approx(np.mean([c["score"] for c in p["components"]]))
    assert p["state"] in {"risk-on", "risk-off", "neutral"}
    with pytest.raises(ValueError):
        rg.risk_panel()
