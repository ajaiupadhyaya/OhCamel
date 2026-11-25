"""Completion task 4.7: beta.league on the committed real ETF returns (sectors in the fixture: XLE, XLF, XLK)."""

from __future__ import annotations

from lane_m_harness import is_label

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.models import beta_league as B
from ohcamel_quant.products import beta_league


def _ctx(market):
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=market)


def test_beta_league_on_fixtures(market):
    spec = beta_league.run({"eval_sessions": 252, "kalman_window": 500, "refit_every": 63}, _ctx(market))
    assert spec.verdict == "DESCRIPTIVE ONLY" and is_label(spec.verdict_detail)
    assert spec.verdict_detail.startswith("BETA LEAGUE · LOWEST MSE ")
    lg = spec.tables["league"]
    assert set(lg["ticker"]) == {"XLE", "XLF", "XLK"}                    # the sectors the fixture holds
    assert set(lg["model"]) == set(B.MODELS) and (lg["n"] == 252).all() and (lg["mse"] > 0).all()
    for _, g in lg.groupby("ticker"):
        assert sorted(g["rank"]) == [1, 2, 3, 4]
    t = spec.tables["tests"]
    pooled = t[t["ticker"] == "POOLED"]
    assert set(pooled["vs"]) == {"ols_60", "ols_120", "ols_250"} and (t["lag"] == B.HAC_LAG).all()
    assert (pooled["pvalue_holm"] >= pooled["pvalue"]).all()
    sk = spec.tables["skipped"].set_index("ticker")
    assert {"XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"} <= set(sk.index)
    assert spec.data_asof is not None and all(p["market"] == "SPY" for p in spec.provenance)
    assert set(spec.tables["latest"].columns) == {"ticker", "date", "kalman", "ols_60", "ols_120", "ols_250", "q"}


def test_beta_league_without_the_market_is_insufficient_data(market):
    spec = beta_league.run({"market": "NOPE", "tickers": ["XLK"]}, _ctx(market))
    assert spec.verdict == "INSUFFICIENT DATA" and is_label(spec.verdict_detail)
