"""Lane M, M4: cov.league on the fixture ETFs (no warehouse: the ndx100 universe is skipped with a reason)."""

from __future__ import annotations

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import cov_league


def test_cov_league_on_fixtures(market):
    c = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                   _market=market)
    spec = cov_league.run({"universes": {"fixture": ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"],
                                         "ndx100": None}, "eval_sessions": 504}, c)
    assert spec.verdict == "DESCRIPTIVE ONLY"
    lg = spec.tables["league"]
    assert set(lg["estimator"]) == set(cov_league.ESTIMATORS) and (lg["realized_vol"] > 0).all()
    assert sorted(lg["rank"]) == list(range(1, len(cov_league.ESTIMATORS) + 1))
    t = spec.tables["tests"]
    assert set(t["estimator"]) == set(cov_league.ESTIMATORS) - {"sample"} and (t["vs"] == "sample").all()
    assert spec.tables["skipped"].set_index("universe").loc["ndx100", "reason"]
