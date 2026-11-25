"""Completion task 4.5: vol.surface_league -- per underlying, per-slice SVI against SSVI on three scores: in-sample
RMSE, the count of butterfly and calendar violations (counted by the same code for both), and the next-day RMSE
(the previous snapshot day's parameters on the latest day's quotes).

The job's plumbing runs over a CONSTRUCTED temp warehouse of BSM-priced quotes (tests/test_products_surface_history's
harness; nothing is published). The next-day scorer is tested on committed real quotes only: the 2026-11-13 SPY chain
(tests/data/cboe_spy_2026-11-15.json.gz) fitted, scored on the 2026-11-19 SPY quotes committed beside it
(tests/data/cboe_spy_header_2026-11-19.json: one strike of the 2026-11-20 expiry) -- never a synthetic tomorrow."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from lane_m_harness import is_label
from test_products_surface_history import ctx, curves

from ohcamel_quant.options import bsm, heston, ssvi, svi
from ohcamel_quant.products import surface_league as L
from ohcamel_quant.warehouse.db import open_rw
from ohcamel_quant.warehouse.ingest.base import insert_frame
from ohcamel_quant.warehouse.ingest.options import INSERT

DATA = Path(__file__).parent / "data"
D1, D2 = pd.Timestamp("2026-06-01 15:30:00"), pd.Timestamp("2026-06-02 15:30:00")
CURVES = curves(*[(d, 4.04) for d in ("2026-05-29", "2026-06-01", "2026-06-02", "2026-06-05", "2026-06-08",
                                      "2026-06-09")])
SCORES = ("rmse_vol_pts", "butterfly_violations", "calendar_violations_quoted", "calendar_violations",
          "next_day_rmse_vol_pts")


SPOT, R, Q = 100.0, 0.04, 0.01
EXPIRIES = [pd.Timestamp("2026-06-30"), pd.Timestamp("2026-07-31"), pd.Timestamp("2026-09-30")]


def _quotes(asof, atm, und):
    """CONSTRUCTED BSM quotes of a known smile on fixed expiries (so one day's fit has the next day's expiries), T
    counted from ``asof`` -- tests/test_products_surface_history._quotes with the expiries held."""
    rows = []
    for expiry in EXPIRIES:
        T = (expiry + pd.Timedelta(hours=16) - asof).total_seconds() / (365.0 * 86400)
        F = SPOT * np.exp((R - Q) * T)
        for K in np.arange(70.0, 131.0, 2.5):
            k = np.log(K / F)
            iv = atm - 0.10 * k + 0.30 * k * k
            for cp in ("C", "P"):
                m = float(bsm.bsm_price(SPOT, K, T, iv, R, Q, cp))
                rows.append({"underlying": und, "asof": asof.normalize(), "expiry": expiry, "strike": K, "cp": cp,
                             "bid": max(m - 0.02, 0.01), "ask": m + 0.02, "last": m, "volume": 10.0,
                             "open_interest": 100.0, "source": "test", "spot": SPOT,
                             "quote_ts": asof.tz_localize("America/New_York").tz_convert("UTC").tz_localize(None),
                             "vendor_iv": iv, "pull": "1545", "quote_ts_source": "cboe:last_trade_time"})
    return pd.DataFrame(rows)


def _wh(tmp_path, days, unds=("SPY",)):
    path = tmp_path / "w.duckdb"
    with open_rw(path) as con:
        for und in unds:
            for i, d in enumerate(days):
                insert_frame(con, INSERT, _quotes(d, atm=0.20 + 0.01 * i, und=und))
    return path


def test_one_row_per_model_per_underlying_with_the_three_scores(market, tmp_path):
    wh = _wh(tmp_path, (D1, D2), unds=("SPY", "QQQ"))
    spec = L.run({}, ctx(market, wh, CURVES))
    t = spec.tables["league"]
    assert sorted(zip(t["underlying"], t["model"], strict=True)) == sorted(
        (u, m.name) for u in ("QQQ", "SPY") for m in L.MODELS)
    assert len(L.MODELS) == 3 and {m.name for m in L.MODELS} == {"SVI (per slice)", "SSVI (power law)", "Heston (COS)"}
    assert set(SCORES) <= set(t.columns)
    assert t[list(SCORES)].notna().all().all() and (t["asof"] == "2026-06-02").all()
    assert (t["next_day_fit_asof"] == "2026-06-01").all() and (t["next_day_n"] > 0).all()
    ss = t[t["model"] == "SSVI (power law)"]
    assert (ss["butterfly_violations"] == 0).all() and (ss["calendar_violations"] == 0).all()
    assert (ss["calendar_violations_quoted"] == 0).all()
    assert spec.verdict == "DESCRIPTIVE ONLY" and is_label(spec.verdict_detail)
    assert spec.data_asof == "2026-06-02" and spec.provenance


def test_one_day_of_snapshots_leaves_the_next_day_cell_blank_with_its_reason(market, tmp_path):
    spec = L.run({}, ctx(market, _wh(tmp_path, (D1,)), CURVES))
    t = spec.tables["league"]
    assert len(t) == len(L.MODELS) and t["rmse_vol_pts"].notna().all()
    assert t["next_day_rmse_vol_pts"].isna().all()
    assert t["next_day_note"].str.contains("no earlier snapshot").all()


def test_an_earlier_day_that_is_not_the_previous_session_is_not_a_next_day_score(market, tmp_path):
    """Monday 06-01 to Tuesday 06-09 is six NYSE sessions: blank, with the count and the reason."""
    spec = L.run({}, ctx(market, _wh(tmp_path, (D1, pd.Timestamp("2026-06-09 15:30:00"))), CURVES))
    t = spec.tables["league"]
    assert t["next_day_rmse_vol_pts"].isna().all()
    assert t["next_day_note"].str.contains("6 sessions back, not the previous session").all()


def test_friday_to_monday_is_the_next_session(market, tmp_path):
    days = (pd.Timestamp("2026-06-05 15:30:00"), pd.Timestamp("2026-06-08 15:30:00"))
    t = L.run({}, ctx(market, _wh(tmp_path, days), CURVES)).tables["league"]
    assert t["next_day_rmse_vol_pts"].notna().all() and (t["next_day_fit_asof"] == "2026-06-05").all()
    assert L.sessions_between("2026-11-25", "2026-11-27") == 1        # over Thanksgiving
    assert L.sessions_between("2026-01-16", "2026-01-20") == 1        # over Martin Luther King Day


def test_any_failure_in_one_name_is_that_names_record_and_cancellation_still_stops(market, tmp_path, monkeypatch):
    """M8: an unexpected exception in one underlying (here a RuntimeError) is recorded as that name's failure with
    its reason; the other name is scored. A cancellation is not caught."""
    from ohcamel_quant.jobs.context import JobCancelled, JobContext

    wh = _wh(tmp_path, (D1, D2), unds=("SPY", "QQQ"))
    real = L._day

    def flaky(c, und, asof):
        if und == "QQQ":
            raise RuntimeError("boom")
        return real(c, und, asof)

    monkeypatch.setattr(L, "_day", flaky)
    spec = L.run({}, ctx(market, wh, CURVES))
    assert set(spec.tables["league"]["underlying"]) == {"SPY"}
    err = spec.tables["errors"]
    assert err["underlying"].tolist() == ["QQQ"] and err["error"].iloc[0] == "RuntimeError: boom"
    c = ctx(market, wh, CURVES)
    stop = JobContext(job_id="t", params={}, threads=1, progress_fn=lambda f, m: None, cancelled_fn=lambda: True,
                      _market=c._market, _warehouse=c._warehouse)
    with pytest.raises(JobCancelled):
        L.run({}, stop)


def test_no_snapshots_is_insufficient_data(market, tmp_path):
    path = tmp_path / "empty.duckdb"
    with open_rw(path):
        pass
    spec = L.run({}, ctx(market, path, CURVES))
    assert spec.verdict == "INSUFFICIENT DATA" and is_label(spec.verdict_detail)
    assert list(spec.tables["league"].columns) == L.COLS


# --- on the committed SPY quotes ------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def spy_next(spy_2026_11_15):
    """The 2026-11-19 SPY quotes (committed), read like the fixture: the fixture's Treasury curve (the latest one
    committed; over a 1.3-day expiry the discount factor is 0.99986 either way)."""
    from conftest import _spy_2026_11_15_chain

    from ohcamel_quant.data.cboe import parse_chain
    from ohcamel_quant.options.chain import analyze_chain

    _, rate_curve = _spy_2026_11_15_chain(as_the_review_read_it=True)
    chain = parse_chain(json.loads((DATA / "cboe_spy_header_2026-11-19.json").read_text()), "SPY")
    return analyze_chain(chain, rate_curve=rate_curve, exercise="european")


def test_next_day_scorer_on_committed_days(spy_2026_11_15, spy_next):
    """Fitted on the 2026-11-13 quotes, scored on the one identified 2026-11-19 quote (the 738 put of the
    2026-11-20 expiry; the call's IV is not on the smile): each model's prediction is that expiry's fitted smile
    at the new quote's log-moneyness."""
    ca, vs, _ = spy_2026_11_15
    fitted = {m.name: m.fit(ca, vs) for m in L.MODELS}
    out = L.next_day(fitted, spy_next)
    q = spy_next.slice_quotes("2026-11-20")
    q = q[q["use_smile"]]
    assert len(q) == 1
    k, iv = float(q["k"].iloc[0]), float(q["iv"].iloc[0])
    f = vs.fits["2026-11-20"]
    want = {"SVI (per slice)": float(f.params.implied_vol(k, f.T)),
            "SSVI (power law)": float(ssvi.implied_vol(fitted["SSVI (power law)"].fit.params, k, f.T)),
            "Heston (COS)": float(heston.implied_vol(fitted["Heston (COS)"].fit.params, np.array([k]), f.T,
                                                     heston.LEAGUE_N, heston.LEAGUE_L)[0])}
    for name, s in out.items():
        assert s["n"] == 1 and s["unscored"] == 0
        assert s["rmse_vol_pts"] == pytest.approx(abs(want[name] - iv) * 100.0, rel=1e-12)
    assert out["SVI (per slice)"]["rmse_vol_pts"] != out["SSVI (power law)"]["rmse_vol_pts"]


def test_a_quote_on_an_expiry_the_fit_did_not_have_is_counted_not_scored(spy_2026_11_15, spy_next):
    ca, vs, _ = spy_2026_11_15
    keep = [s for s in vs.fits if s != "2026-11-20"]
    vs2 = type(vs)(**{**vs.__dict__, "fits": {s: vs.fits[s] for s in keep}})
    out = L.next_day({m.name: m.fit(ca, vs2) for m in L.MODELS}, spy_next)
    assert all(s["n"] == 0 and s["unscored"] == 1 and np.isnan(s["rmse_vol_pts"]) for s in out.values())


def test_violations_are_counted_by_the_same_code_for_both_models(spy_2026_11_15):
    """Butterfly: slices failing svi.butterfly_check (the refined g scan on [-K_MAX, K_MAX], G_TOL); calendar: adjacent
    pairs failing svi.calendar_check on the same 1201-point k grid. SSVI's slices go through the same two calls."""
    ca, vs, _ = spy_2026_11_15
    out = {m.name: m.score(m.fit(ca, vs)) for m in L.MODELS}
    sv = sorted(vs.fits.values(), key=lambda f: f.T)
    want_cal = sum(c["violation"] for c in svi.calendar_check([(f.T, f.params) for f in sv], L.K_GRID))
    want_bf = sum(not svi.butterfly_check(f.params)["arbitrage_free"] for f in sv)
    # the quoted-range count, computed here pair by pair: 201 points of the pair's common quoted k range
    want_q, pairs_q = 0, 0
    for f1, f2 in zip(sv, sv[1:], strict=False):
        lo, hi = max(f1.k_min, f2.k_min), min(f1.k_max, f2.k_max)
        if hi > lo:
            pairs_q += 1
            k = np.linspace(lo, hi, 201)
            want_q += bool(np.min(f2.params.w(k) - f1.params.w(k)) < -1e-8)
    s = out["SVI (per slice)"]
    assert s["calendar_violations"] == want_cal and s["butterfly_violations"] == want_bf
    assert s["calendar_violations_quoted"] == want_q and s["calendar_pairs_quoted"] == pairs_q == len(sv) - 1
    assert s["calendar_violations_quoted"] == 4 and s["calendar_violations"] == 17   # 2026-11-13 SPY, measured
    assert s["calendar_violations_quoted"] == sum(c["violation"] for c in vs.calendar)  # the page's table: one check
    q = out["SSVI (power law)"]
    assert q["calendar_violations"] == q["calendar_violations_quoted"] == q["butterfly_violations"] == 0
    for s in out.values():
        assert s["n_slices"] == len(vs.fits) and s["calendar_pairs"] == len(vs.fits) - 1
    n = np.array([f.n for f in vs.fits.values()])
    pooled = np.sqrt(np.sum(n * np.array([f.rmse_vol_pts for f in vs.fits.values()]) ** 2) / n.sum())
    assert out["SVI (per slice)"]["rmse_vol_pts"] == pytest.approx(pooled)
    assert out["SVI (per slice)"]["n_quotes"] == out["SSVI (power law)"]["n_quotes"] == n.sum()


def test_heston_is_fitted_to_the_same_quotes_and_its_violations_are_counted_on_the_same_grids(spy_2026_11_15):
    """Task 4.6: Heston (COS) on the identified-IV quotes per-slice SVI and SSVI fit (surface.smile_inputs), each
    IV turned into a European Black-76 price on its expiry's forward and discount factor; scored in IV points.
    Its violations come from its implied total variance on the league's grids: butterfly = Durrleman g < -G_TOL on
    svi._K_GRID (g from the COS density and w), calendar = w decreasing on the quoted overlap and on K_GRID."""
    from ohcamel_quant.options.surface import heston_violations, smile_inputs

    ca, vs, _ = spy_2026_11_15
    m = next(m for m in L.MODELS if m.name == "Heston (COS)")
    fitted = m.fit(ca, vs)
    n = sum(smile_inputs(ca, sid)[1].size for sid in vs.fits)
    assert fitted.fit.n == n
    s = m.score(fitted)
    assert s["n_quotes"] == n and np.isfinite(s["rmse_vol_pts"])
    assert s["n_slices"] == len(vs.fits) and s["calendar_pairs"] == len(vs.fits) - 1
    v = heston_violations(fitted.fit.params, [(f.T, f.k_min, f.k_max) for f in vs.fits.values()])
    assert s == {**s, **{k: x for k, x in v.items() if k != "violation_points_excluded"}}
    # Heston is a martingale model: in exact arithmetic w(k, T) cannot fall with T, nor g go negative. Scored on the
    # converged series (LEAGUE_N terms on LEAGUE_L) at points priced at least HESTON_W_FLOOR, the expansion leaves
    # none on this surface (fit sigma ~ 3); at 160 terms on L = 12 with no floor it showed 30/31 butterfly and 13/30
    # calendar, all series-length and rounding artefacts (task 4.6 fix report). Bounds, not platform-exact pins.
    assert s["butterfly_violations"] == 0 and s["calendar_violations_quoted"] == 0
    assert s["calendar_violations"] <= 2
    assert fitted.fit.n_iv_failed == 0 and "5230 of 5230 quotes scored" in s["fit_note"]
    p = m.params(fitted)
    assert set(p) == {"kappa", "theta", "sigma", "rho", "v0", "feller_ratio", "n_iv_failed", "max_abs_err_vol_pts",
                      "status", "nfev", "cost", "start", "n_terms", "L", "violation_points_excluded"}
    assert p["n_terms"] == heston.LEAGUE_N and p["violation_points_excluded"] > 0
    # the league row carries the excluded and scored points beside the Heston row, and the fit_note its COS settings
    assert s["violation_points_excluded"] == p["violation_points_excluded"] and s["violation_points_scored"] > 0
    assert f"COS {heston.LEAGUE_N} terms, L {heston.LEAGUE_L:g}" in s["fit_note"] and "price floor 1e-08" in s["fit_note"]
