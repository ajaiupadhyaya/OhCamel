"""Lane M, M3: vol.forecast_league on fixture data (no warehouse -> daily-return target, no HAR)."""

from __future__ import annotations

from lane_m_harness import is_label

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import vol_league


def ctx(market):
    return JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                      _market=market)


def test_league_on_two_fixture_etfs(market):
    spec = vol_league.run({"tickers": ["SPY", "TLT", "NOPE"], "eval_days": 42}, ctx(market))
    assert spec.verdict == "DESCRIPTIVE ONLY" and "MCS" in spec.verdict_detail and is_label(spec.verdict_detail)
    lg = spec.tables["league"]
    assert set(lg["ticker"]) == {"SPY", "TLT"}
    assert {"garch", "gjr", "ewma"} <= set(lg["model"]) <= {"garch", "gjr", "egarch", "ewma"}  # no HAR offline
    assert (lg["target"] == "squared_return").all() and lg.groupby("ticker")["in_mcs"].any().all()
    n = lg.groupby("ticker").size()
    assert len(spec.tables["dm"]) == int((n * (n - 1) // 2).sum())  # every pair of scored models, per name
    d = spec.tables["dropped"]  # a degenerate EGARCH refit is listed with its reason, never scored
    assert set(d["model"]) <= {"egarch", "garch", "gjr", "mcs"} and d["reason"].str.len().gt(0).all()
    assert spec.tables["skipped"].set_index("ticker").loc["NOPE", "reason"]
    f = spec.tables["forecasts"].set_index("ticker")
    assert (f.loc[["SPY", "TLT"], "var_1d_gjr"] > 0).all()
    assert spec.data_asof == "2026-06-01"
