"""Lane M, M4: cov.league on the fixture ETFs (no warehouse: the ndx100 universe is skipped with a reason)."""

from __future__ import annotations

from lane_m_harness import is_label

from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import cov_league


def test_cov_league_on_fixtures(market):
    c = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                   _market=market)
    spec = cov_league.run({"universes": {"fixture": ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"],
                                         "ndx100": None}, "eval_sessions": 504}, c)
    assert spec.verdict == "DESCRIPTIVE ONLY" and is_label(spec.verdict_detail)
    lg = spec.tables["league"]
    assert set(lg["estimator"]) == set(cov_league.ESTIMATORS) and (lg["realized_vol"] > 0).all()
    assert sorted(lg["rank"]) == list(range(1, len(cov_league.ESTIMATORS) + 1))
    t = spec.tables["tests"]
    assert set(t["estimator"]) == set(cov_league.ESTIMATORS) - {"sample"} and (t["vs"] == "sample").all()
    assert spec.tables["skipped"].set_index("universe").loc["ndx100", "reason"]


def test_an_entrant_that_raises_is_skipped_and_the_seven_still_rank(market, monkeypatch):
    """Final review: dcc raising drops dcc alone; site_etfs still ranks and the overlay's choice is unchanged."""
    import pandas as pd

    from ohcamel_quant.models import cov_league as C
    from ohcamel_quant.signals import overlay

    tickers = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]

    def league():
        c = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: False,
                       _market=market)
        return cov_league.run({"universes": {"site_etfs": tickers}, "eval_sessions": 504}, c)

    def choice(lg: pd.DataFrame) -> str:
        rows = lg[(lg["universe"] == overlay.COV_UNIVERSE) & lg["estimator"].isin(overlay.OVERLAY_ESTIMATORS)]
        return str(rows.sort_values("rank", kind="mergesort").iloc[0]["estimator"])

    before = league().tables["league"]
    real = C.estimator_cov

    def broken(name, window, hold=C.HOLD):
        if name == "dcc":
            raise RuntimeError("dcc did not converge")
        return real(name, window, hold)

    monkeypatch.setattr(C, "estimator_cov", broken)
    spec = league()
    lg = spec.tables["league"]
    assert set(lg["estimator"]) == set(cov_league.ESTIMATORS) - {"dcc"}
    assert sorted(lg["rank"]) == list(range(1, len(cov_league.ESTIMATORS)))
    sk = spec.tables["skipped"]
    assert list(sk["estimator"]) == ["dcc"] and "dcc did not converge" in sk.iloc[0]["reason"]
    assert choice(lg) == choice(before)
    assert spec.verdict == "DESCRIPTIVE ONLY"
