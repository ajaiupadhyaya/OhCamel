"""Recession probit (alignment, estimator) and the Sahm rule."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from ohcamel_quant.macro import recession as rec

# completion task 3.5: fit_expanding on the committed FRED slices (spread GS10 - TB3MS, h = 12), 545 targets
OOS_BRIER_GS10_TB3MS = 0.07955644359663917
INSAMPLE_BRIER_GS10_TB3MS = 0.07614020498010228
BASELINE_BRIER_GS10_TB3MS = 0.09027487576800113  # fix round 1: the real-time constant forecast


def _months(n: int, start: str = "1970-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="MS")


def test_align_forecast_shift():
    idx = _months(6)
    y = pd.Series([0, 0, 1, 1, 0, 0], index=idx, dtype=float)
    x = pd.DataFrame({"s": [10, 11, 12, 13, 14, 15]}, index=idx, dtype=float)
    yl, xa = rec.align_forecast(y, x, 2)
    # x at Jan pairs with y at Mar
    assert list(xa["s"]) == [10, 11, 12, 13]
    assert list(yl) == [1, 1, 0, 0]
    assert yl.index[0] == idx[0]
    # mid-month stamps are normalized to month starts
    x2 = x.copy()
    x2.index = x2.index + pd.Timedelta(days=14)
    yl2, _ = rec.align_forecast(y, x2, 2)
    assert list(yl2) == list(yl)
    with pytest.raises(ValueError):
        rec.align_forecast(y, x, -1)


def test_probit_matches_statsmodels_and_recovers_params():
    rng = np.random.default_rng(7)
    n, h = 900, 3
    idx = _months(n)
    x = rng.normal(1.0, 1.2, n)
    latent = -0.6 - 0.9 * x + rng.normal(size=n)
    y_target = (latent > 0).astype(float)  # event at t+h driven by x_t
    y = pd.Series(np.r_[np.zeros(h), y_target[:-h]], index=idx)
    res = rec.fit_probit(y, pd.DataFrame({"spread": x}, index=idx), h, hac=False)
    direct = sm.Probit(y_target[: n - h], sm.add_constant(x[: n - h])).fit(disp=0)
    assert np.allclose(res.params.to_numpy(), direct.params, atol=1e-8)
    assert res.llf == pytest.approx(direct.llf, rel=1e-10)
    assert res.mcfadden_r2 == pytest.approx(direct.prsquared, rel=1e-10)
    assert res.params["const"] == pytest.approx(-0.6, abs=0.15)
    assert res.params["spread"] == pytest.approx(-0.9, abs=0.15)
    # fitted probabilities exist for every origin month, including the last h
    assert len(res.fitted) == n and res.fitted.index[-1] == idx[-1]
    hac = rec.fit_probit(y, pd.DataFrame({"spread": x}, index=idx), h, hac=True)
    assert np.allclose(hac.params, res.params)
    assert any("Newey-West" in s for s in hac.notes)


def test_estrella_r2():
    llf, ll0, n = -100.0, -200.0, 500
    assert rec.estrella_r2(llf, ll0, n) == pytest.approx(1 - (0.5) ** (-(2 / n) * ll0))
    assert rec.estrella_r2(ll0, ll0, n) == pytest.approx(0.0)


def test_sahm_rule_hand_sequence():
    u = pd.Series([4.0] * 12 + [4.0, 4.3, 4.6, 4.9], index=_months(16), dtype=float)
    s = rec.sahm_rule(u)
    # first defined value needs 3 (ma) + 12 (prior window) observations
    assert s.index[0] == _months(16)[14]
    # t=14: ma3 = (4.0+4.3+4.6)/3 = 4.3; prior 12 ma3 min = 4.0 -> 0.30
    assert s["sahm"].iloc[0] == pytest.approx(0.30)
    # t=15: ma3 = 4.6 -> 0.60, triggers
    assert s["sahm"].iloc[1] == pytest.approx(0.60)
    assert list(s["triggered"]) == [False, True]
    assert rec.SAHM_THRESHOLD == 0.5


def test_term_spread_splice_and_monthly_mean():
    d = pd.bdate_range("1982-01-01", "1982-03-31")
    daily = pd.Series(np.where(d.month == 1, 1.0, 2.0), index=d)
    idx = _months(4, "1981-10-01")
    gs10 = pd.Series([15.0, 14.0, 13.5, 14.0], index=idx)
    tb3 = pd.Series([14.0, 11.0, 10.5, 12.0], index=idx)
    s, notes = rec.term_spread_monthly(daily, gs10, tb3)
    assert list(s.index) == list(_months(6, "1981-10-01"))
    assert list(s.round(6)) == [1.0, 3.0, 3.0, 1.0, 2.0, 2.0]
    assert notes and "1982-01-01" in notes[0]


def test_recession_episodes():
    u = pd.Series([0, 1, 1, 0, 0, 1], index=_months(6), dtype=float)
    ep = rec.recession_episodes(u)
    assert ep == [{"start": "1970-02-01", "end": "1970-03-01"}, {"start": "1970-06-01", "end": "1970-06-01"}]


def test_sahm_uses_calendar_months_across_a_missing_release():
    """Regression: with a month missing (Oct 2025: no CPS), observation-count
    windows averaged 4 calendar months and looked back 13."""
    idx = _months(18)
    u = pd.Series([4.0] * 12 + [4.0, 4.2, 4.4, 4.6, 4.8, 5.0], index=idx, dtype=float)
    u_gap = u.drop(idx[15])
    s = rec.sahm_rule(u_gap)
    assert idx[15] not in s.index
    # t=16: calendar window {14, 15(missing), 16} -> mean(4.4, 4.8) = 4.6
    assert s.loc[idx[16], "ma3"] == pytest.approx(4.6)
    assert s.loc[idx[16], "sahm"] == pytest.approx(0.6)
    # t=17: window {15(missing), 16, 17} -> mean(4.8, 5.0)
    assert s.loc[idx[17], "ma3"] == pytest.approx(4.9)
    # gapless data unchanged
    full = rec.sahm_rule(u)
    assert full.loc[idx[16], "ma3"] == pytest.approx((4.4 + 4.6 + 4.8) / 3)


# ------------------------------------------ out of sample, on what was known (completion task 3.5)
DATA = Path(__file__).parent / "data"


def _fred(series: str) -> pd.Series:
    from ohcamel_quant.data.fred import parse_fredgraph_csv

    return parse_fredgraph_csv((DATA / f"fred_{series.lower()}_2026-11-19.csv").read_text())[series].dropna()


@pytest.fixture(scope="module")
def real():
    """USREC, GS10 and TB3MS as FRED served them on 2026-11-19 (committed whole, with sidecars)."""
    for s in ("USREC", "GS10", "TB3MS"):
        meta = json.loads((DATA / f"fred_{s.lower()}_2026-11-19.csv.meta.json").read_text())
        assert meta["synthetic"] is False and len(meta["original_sha256"]) == 64
    spread, _ = rec.term_spread_monthly(None, _fred("GS10"), _fred("TB3MS"))
    return _fred("USREC"), spread.to_frame("spread")


def test_nber_announcements_are_the_committed_page():
    meta = json.loads((DATA / "nber_announcements_2026-11-19.txt.meta.json").read_text())
    assert meta["synthetic"] is False and meta["url"].startswith("https://www.nber.org/")
    assert rec.parse_nber_announcements((DATA / "nber_announcements_2026-11-19.txt").read_text()) == \
        rec.NBER_ANNOUNCEMENTS


def test_usrec_known_at_reads_as_fred_did(real):
    usrec, _ = real
    m = lambda s: pd.Timestamp(s)  # noqa: E731
    assert rec.usrec_known_at(usrec, "1980-06-02") is None            # before the committee's first announcement
    pre = rec.usrec_known_at(usrec, "2008-11-30")                     # Dec 2007 peak announced 2008-12-01
    post = rec.usrec_known_at(usrec, "2008-12-31")
    assert usrec[m("2008-06-01")] == 1 and pre[m("2008-06-01")] == 0 and post[m("2008-06-01")] == 1
    assert pre.index[-1] == m("2008-11-01") and post[m("2008-12-01")] == 1
    mid = rec.usrec_known_at(usrec, "2010-08-31")                     # June 2009 trough announced 2010-09-20
    assert usrec[m("2010-01-01")] == 0 and mid[m("2010-01-01")] == 1
    assert rec.usrec_known_at(usrec, "2010-09-30")[m("2010-01-01")] == 0
    # the final series' convention: the peak month reads 0, the trough month 1
    assert usrec[m("2007-12-01")] == 0 and usrec[m("2009-06-01")] == 1


def test_probit_newton_matches_statsmodels_on_real_data(real):
    usrec, x = real
    yl, xa = rec.align_forecast(usrec, x, 12)
    X = sm.add_constant(xa.to_numpy(float))
    ours = rec._probit_mle(X, yl.to_numpy(float))
    ref = sm.Probit(yl.to_numpy(float), X).fit(disp=0).params
    assert np.allclose(ours, ref, atol=1e-8)


def test_2008_probabilities_come_from_fits_that_end_before_each_date(real):
    usrec, x = real
    o = rec.fit_expanding(usrec, x, 12)
    t2008 = o.oos_prob.loc["2008"].index
    assert len(t2008) == 12
    for target in t2008:
        origin = o.origin[target]
        assert origin == target - pd.DateOffset(months=12)
        assert o.fit_end[target] <= origin < target                   # the fit ends before the date it predicts
    # the June 2008 probability, recomputed from nothing but what was known at the end of June 2007: labels
    # final through the last announced turning point (the Nov 2001 trough), expansion after it
    target, origin = pd.Timestamp("2008-06-01"), pd.Timestamp("2007-06-01")
    known = rec.usrec_known_at(usrec, "2007-06-30")
    yl, xa = rec.align_forecast(known, x[x.index <= origin], 12)
    keep = yl.index <= origin - pd.DateOffset(months=12)
    fit = sm.Probit(yl[keep].to_numpy(float), sm.add_constant(xa[keep].to_numpy(float))).fit(disp=0)
    from scipy.stats import norm

    p = float(norm.cdf(fit.params[0] + fit.params[1] * x.loc[origin, "spread"]))
    assert o.oos_prob[target] == pytest.approx(p, abs=1e-9)
    # and nothing after the origin moves it: every later label flipped, every later spread replaced
    u2 = usrec.copy()
    u2[u2.index > origin] = 1 - u2[u2.index > origin]
    x2 = x.copy()
    x2.loc[x2.index > origin, "spread"] = 9.0
    assert rec.fit_expanding(u2, x2, 12).oos_prob[target] == pytest.approx(o.oos_prob[target], abs=1e-12)


def test_out_of_sample_scores_on_the_committed_series(real):
    usrec, x = real
    o = rec.fit_expanding(usrec, x, 12)
    d = o.to_dict()
    assert d["scored"]["start"] == "1981-06-01" and d["n_scored"] > 500
    # pinned on the committed FRED slices (GS10 - TB3MS spread): the figures the report quotes for this basis
    assert o.oos_brier == pytest.approx(OOS_BRIER_GS10_TB3MS, rel=1e-6)
    assert o.insample_brier == pytest.approx(INSAMPLE_BRIER_GS10_TB3MS, rel=1e-6)
    with pytest.raises(ValueError, match="h must be >= 1"):
        rec.fit_expanding(usrec, x, 0)


def test_sahm_rule_as_first_published():
    from ohcamel_quant.data import fred

    dates = [date(2020, 5, 7), date(2020, 5, 8), date(2020, 6, 5), date(2021, 1, 8), date(2026, 11, 19)]
    rows = fred.parse_alfredgraph_csv((DATA / "alfred_unrate_vintages_2026-11-19.csv").read_text(), "UNRATE", dates)
    s = rec.sahm_realtime(rows)
    apr = pd.Timestamp("2020-04-01")
    assert s.loc[apr, "unrate"] == 14.7 and s.loc[apr, "published"] == pd.Timestamp("2020-05-08")
    # equal to the rule on the vintage of April's first release, and not to the rule on today's revised series
    first = rows[rows["realtime_start"] <= "2020-05-08"].groupby("date")["value"].last()
    assert s.loc[apr, "sahm"] == pytest.approx(rec.sahm_rule(first).loc[apr, "sahm"])
    assert bool(s.loc[apr, "triggered"])
    # May 2020 as first published (13.3, on 2020-06-05) reads 7.27; today's revised series reads 7.23
    may = pd.Timestamp("2020-05-01")
    assert s.loc[may, "published"] == pd.Timestamp("2020-06-05") and s.loc[may, "unrate"] == 13.3
    assert s.loc[may, "sahm"] == pytest.approx(10.8 - 3.533333333, abs=1e-6)
    today = rows.groupby("date")["value"].last()
    assert rec.sahm_rule(today).loc[may, "sahm"] == pytest.approx(10.8 - 3.566666667, abs=1e-6)


def test_sahm_at_exactly_the_threshold_triggers_whatever_the_summation_order():
    # UNRATE 2007-01..2008-04 as ALFRED served it on 2008-05-02, April 2008's first release (the keyless backfill
    # of 2026-11-19): ma3 5.0 against a prior-12-month low of 4.5 -- exactly 0.50, so the rule fires. Summed from
    # this window's start the float came out 0.4999999999999991 and the trigger moved to May.
    first_published = [4.6, 4.5, 4.4, 4.5, 4.5, 4.6, 4.7, 4.7, 4.7, 4.8, 4.7, 5.0, 4.9, 4.8, 5.1, 5.0]
    s = rec.sahm_rule(pd.Series(first_published, index=_months(16, "2007-01-01"), dtype=float))
    apr = pd.Timestamp("2008-04-01")
    assert s.loc[apr, "sahm"] == 0.5 and bool(s.loc[apr, "triggered"])


# ------------------------------------------------ fix round 1: the benchmarks the page shows beside the scores
def test_real_time_baseline_brier_and_skill_on_the_committed_basis(real):
    """The constant forecast an analyst could have made at each origin -- the share of recession months among
    the labels known then (the fit's own sample) -- scored on exactly the targets the probit is scored on."""
    usrec, x = real
    o = rec.fit_expanding(usrec, x, 12)
    assert list(o.baseline.index) == list(o.oos_prob.index)
    scored = o.oos_prob.index.intersection(usrec.index)
    y = usrec.loc[scored]
    assert o.baseline_brier == pytest.approx(float(((o.baseline.loc[scored] - y) ** 2).mean()), rel=1e-12)
    assert o.baseline_brier == pytest.approx(BASELINE_BRIER_GS10_TB3MS, rel=1e-6)
    assert o.brier_skill == pytest.approx(1 - o.oos_brier / o.baseline_brier, rel=1e-12)
    d = o.to_dict()
    assert d["baseline_brier"] == o.baseline_brier and d["brier_skill"] == o.brier_skill


def test_the_baseline_at_an_origin_ignores_later_labels(real):
    usrec, x = real
    o = rec.fit_expanding(usrec, x, 12)
    target, origin = pd.Timestamp("2008-06-01"), pd.Timestamp("2007-06-01")
    u2 = usrec.copy()
    u2[u2.index > origin] = 1 - u2[u2.index > origin]
    assert rec.fit_expanding(u2, x, 12).baseline[target] == o.baseline[target]
    # and it is the mean of the labels known at the end of June 2007 over the months the fit paired
    known = rec.usrec_known_at(usrec, "2007-06-30")
    yl, _ = rec.align_forecast(known, x[x.index <= origin], 12)
    assert o.baseline[target] == pytest.approx(float(yl[yl.index <= origin - pd.DateOffset(months=12)].mean()))


def test_hit_rates_come_with_the_never_recession_rate(real):
    usrec, x = real
    o = rec.fit_expanding(usrec, x, 12)
    scored = o.oos_prob.index.intersection(usrec.index)
    assert o.base_hit_rate == pytest.approx(1 - float(usrec.loc[scored].mean()), rel=1e-12)
    assert o.to_dict()["base_hit_rate"] == o.base_hit_rate
    # on this basis the probit's out-of-sample hit rate is below never predicting a recession
    assert o.oos_hit_rate < o.base_hit_rate
