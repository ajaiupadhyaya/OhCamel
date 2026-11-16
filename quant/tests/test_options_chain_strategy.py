"""Chain cleaning, parity, surface, density and strategy analytics on a numerical
BSM/SSVI-generated chain (a test fixture: the analytics must recover the inputs)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.data.market import OptionChain
from ohcamel_quant.options import bsm, parity, strategy
from ohcamel_quant.options.chain import analyze_chain
from ohcamel_quant.options.surface import build_surface
from ohcamel_quant.options.svi import SVIParams

S0, R, Q = 500.0, 0.045, 0.013
AS_OF = pd.Timestamp("2026-09-24 11:00:00")
EXPIRIES = ["2026-10-02", "2026-10-16", "2026-10-30", "2026-11-20", "2026-12-18", "2027-03-19", "2027-06-17"]
RHO, ETA, GAMMA, SIG0 = -0.6, 1.0, 0.5, 0.20


def ssvi(T: float) -> SVIParams:
    """SSVI slice (Gatheral & Jacquier 2014, power-law phi) as raw SVI parameters."""
    theta = SIG0**2 * T
    phi = ETA / (theta**GAMMA * (1 + theta) ** (1 - GAMMA))
    return SVIParams(a=theta / 2 * (1 - RHO**2), b=theta * phi / 2, rho=RHO, m=-RHO / phi,
                     sigma=np.sqrt(1 - RHO**2) / phi)


def make_chain(spot: float = S0, root: str = "TESTX", half_spread: float = 0.01) -> OptionChain:
    rows = []
    for e in EXPIRIES:
        ts = parity.expiry_timestamp(e, root)
        T = float(parity.year_fraction(AS_OF, ts)[0])
        F = spot * np.exp((R - Q) * T)
        p = ssvi(T)
        for K in np.arange(300.0, 705.0, 5.0):
            iv = float(p.implied_vol(np.log(K / F), T))
            for cp in ("C", "P"):
                mid = float(bsm.bsm_price(spot, K, T, iv, R, Q, cp))
                hs = max(half_spread, half_spread * mid)
                bid, ask = (mid - hs, mid + hs) if mid > 0.05 else (0.0, 0.05)
                occ = f"{root}{pd.Timestamp(e):%y%m%d}{cp}{int(K * 1000):08d}"
                rows.append({"contract": occ, "root": root, "expiry": pd.Timestamp(e), "strike": K, "type": cp,
                             "bid": bid, "ask": ask, "last": mid, "volume": 10.0, "open_interest": 100.0,
                             "vendor_iv": iv})
    # priced with Black-Scholes, so the chain states European exercise whatever its root
    return OptionChain(underlying=root, spot=spot, as_of=AS_OF, quotes=pd.DataFrame(rows), exercise="european")


@pytest.fixture(scope="module")
def ca():
    return analyze_chain(make_chain())


@pytest.fixture(scope="module")
def vs(ca):
    return build_surface(ca)


def test_chain_recovers_forward_rate_yield_and_iv(ca):
    s = ca.slices
    assert len(s) == len(EXPIRIES)
    # with 1% quoted spreads only long expiries pin down the parity slope (r); the
    # others borrow it -- either way every expiry ends up with the right rate
    reg = s[s["rate_source"] == "regression"]
    assert len(reg) >= 1 and (reg["rate_se"] < 0.01).all()
    assert (s.loc[s["rate_source"] == "borrowed", "T"] < reg["T"].max()).all()
    np.testing.assert_allclose(s["rate"], R, atol=1e-4)
    np.testing.assert_allclose(s["div_yield"], Q, atol=5e-4)
    np.testing.assert_allclose(s["forward"], S0 * np.exp((R - Q) * s["T"]), rtol=2e-6)
    sm = ca.quotes[ca.quotes["use_smile"]]
    np.testing.assert_allclose(sm["iv"], sm["vendor_iv"], atol=2e-4)
    assert (sm["otm"]).all()
    assert ((sm["type"] == "P") == (sm["k"] < 0)).all()
    assert ca.quotes["zero_bid"].any() and not ca.quotes.loc[ca.quotes["zero_bid"], "valid"].any()
    assert ca.european and ca.exercise == "european" and not any("American" in n for n in ca.notes)


def test_chain_flags_bad_quotes_and_drops_adjusted_roots():
    ch = make_chain()
    q = ch.quotes
    q.loc[0, ["bid", "ask"]] = [5.0, 4.0]                     # crossed
    q.loc[1, ["bid", "ask"]] = [1.0, 3.0]                     # 100% of mid: wide
    extra = q.iloc[[2]].assign(root="TESTX1", contract="TESTX1261002C00300000")
    ch.quotes = pd.concat([q, extra], ignore_index=True)
    out = analyze_chain(ch, max_rel_spread=0.5)
    assert out.quotes.loc[out.quotes["contract"] == q.loc[0, "contract"], "crossed"].item()
    assert out.quotes.loc[out.quotes["contract"] == q.loc[1, "contract"], "wide"].item()
    assert "TESTX1" not in set(out.quotes["root"])
    assert any("non-standard" in n for n in out.notes)


def test_resolve_expiry(ca):
    assert ca.resolve("2026-10-30") == "2026-10-30"
    assert ca.resolve(None) == "2026-10-02"                   # first expiry >= 7 days out
    with pytest.raises(ValueError):
        ca.resolve("2031-01-01")


def test_surface_recovers_ssvi_and_is_arbitrage_free(vs):
    assert len(vs.fits) == len(EXPIRIES) and not vs.errors
    for f in vs.fits.values():
        true = ssvi(f.T)
        k = np.linspace(f.k_min, f.k_max, 50)
        np.testing.assert_allclose(f.params.implied_vol(k, f.T), true.implied_vol(k, f.T), atol=5e-5)
        assert f.butterfly["arbitrage_free"]
    assert not any(c["violation"] for c in vs.calendar)
    # SSVI with theta = sig0^2 T has ATM vol exactly sig0 at every expiry
    np.testing.assert_allclose(vs.term["atm_iv"], SIG0, atol=1e-4)
    assert vs.atm_30d["iv"] == pytest.approx(SIG0, abs=1e-4)
    assert (vs.term["rr_25d"] < 0).all() and (vs.term["bf_25d"] > 0).all()   # equity-style skew
    assert (vs.term["skew_slope"] < 0).all()
    assert np.isfinite(vs.grid_iv).all() and vs.grid_iv.shape == (len(vs.grid_T), len(vs.grid_k))


def test_model_free_vol_reflects_the_smile(vs):
    # with negative skew, the model-free (fair variance) vol exceeds the ATM vol
    t = vs.term
    assert (t["mf_vol"] > t["atm_iv"]).all()
    assert vs.vix_style is not None and not vs.vix_style["extrapolated"]
    assert 20.0 < vs.vix_style["index"] < 30.0


def _position(ca, vs, specs):
    fits = {s: f.params for s, f in vs.fits.items()}
    return strategy.analyze_position(ca, strategy.resolve_legs(ca, specs, fits), fits)


def test_bull_call_spread(ca, vs):
    e = "2026-10-30"
    out = _position(ca, vs, [{"qty": 1, "expiry": e, "strike": 500, "type": "C"},
                             {"qty": -1, "expiry": e, "strike": 520, "type": "C"}])
    cost = out["cost"]["mid"]
    assert out["max_profit"] == pytest.approx(20 * 100 - cost, abs=1e-6)
    assert out["max_loss"] == pytest.approx(-cost, abs=1e-6)
    assert out["max_profit_unbounded"] is False and out["max_loss_unbounded"] is False
    assert out["breakevens"] == [pytest.approx(500 + cost / 100, abs=1e-3)]
    assert out["cost"]["natural"] > cost
    # risk-neutral expectation of the P&L is only the financing carry: E_Q[payoff] = cost / D
    T = out["horizon"]["T_years"]
    assert out["expected_pnl_q"] == pytest.approx(cost * (np.exp(R * T) - 1), abs=0.05)
    assert 0 < out["prob_profit"] < 1


def test_straddle_unbounded_and_pop_matches_density(ca, vs):
    e = "2026-10-30"
    out = _position(ca, vs, [{"qty": 1, "expiry": e, "strike": 500, "type": "C"},
                             {"qty": 1, "expiry": e, "strike": 500, "type": "P"}])
    assert out["max_profit"] == float("inf") and out["asymptotic_slope"] == pytest.approx(100.0)
    assert out["max_profit_unbounded"] is True and out["max_loss_unbounded"] is False
    lo, hi = out["breakevens"]
    from ohcamel_quant.options.density import breeden_litzenberger
    f = vs.fits[e]
    s = ca.slices.loc[e]
    d = breeden_litzenberger(s["forward"], s["discount"], s["T"], f.params.w)
    expected = float(d.prob_below(lo) + 1 - d.prob_below(hi))
    assert out["prob_profit"] == pytest.approx(expected, abs=2e-3)
    assert abs(out["greeks"]["delta"]) < 20                    # near delta-neutral (per 100-share contracts)
    assert out["greeks"]["gamma"] > 0 and out["greeks"]["theta_day"] < 0


def test_covered_call_and_short_put_unbounded_loss(ca, vs):
    e = "2026-11-20"
    cc = _position(ca, vs, [{"kind": "stock", "qty": 100}, {"qty": -1, "expiry": e, "strike": 520, "type": "C"}])
    assert np.isfinite(cc["max_profit"]) and cc["max_loss"] == pytest.approx(-cc["cost"]["mid"], abs=1e-3)
    nc = _position(ca, vs, [{"qty": -1, "expiry": e, "strike": 520, "type": "C"}])
    assert nc["max_loss"] == float("-inf") and nc["cost"]["direction"] == "credit"
    assert nc["max_loss_unbounded"] is True and nc["max_profit_unbounded"] is False
    by_symbol = _position(ca, vs, [{"qty": -1, "contract": "TESTX261120C00520000"}])
    assert by_symbol["cost"]["mid"] == pytest.approx(nc["cost"]["mid"])


def test_strategy_rejects_unknown_legs(ca, vs):
    with pytest.raises(ValueError):
        strategy.resolve_legs(ca, [{"qty": 1, "expiry": "2026-10-30", "strike": 501, "type": "C"}])
    with pytest.raises(ValueError):
        strategy.resolve_legs(ca, [{"qty": 0, "expiry": "2026-10-30", "strike": 500, "type": "C"}])
    with pytest.raises(ValueError):
        strategy.analyze_position(ca, strategy.resolve_legs(ca, [{"kind": "stock", "qty": 100}]))


def test_value_today_equals_mid_cost(ca, vs):
    e = "2026-12-18"
    out = _position(ca, vs, [{"qty": 2, "expiry": e, "strike": 480, "type": "P"}])
    S = out["grid"]["spot"]
    today = out["grid"]["curves"]["today"]["pnl"]
    i = int(np.argmin(np.abs(S - S0)))
    assert S[i] == pytest.approx(S0) and today[i] == pytest.approx(0.0, abs=0.02)


# ------------------------------------------------------------------ American exercise
AM_SIG = 0.25
AM_EXPIRIES = ["2026-12-18", "2027-09-17"]


def make_american_chain(half_spread: float = 0.005, expiries=tuple(AM_EXPIRIES), q: float = Q,
                        vendor_spot: float = S0) -> OptionChain:
    """Every quote is the American model price (options.american) at one flat vol on the underlying
    S0, with the yield ``q`` and the rate R: the chain analytics must give back AM_SIG at every
    strike, call or put. ``vendor_spot`` is the spot the chain reports (a stale print if != S0)."""
    from ohcamel_quant.options import american

    rows = []
    for e in expiries:
        T = float(parity.year_fraction(AS_OF, parity.expiry_timestamp(e, "AMX"))[0])
        K = np.arange(420.0, 581.0, 20.0)
        for cp in ("C", "P"):
            mid = american.prices(S0, K, T, R, q, AM_SIG, cp == "C")
            for k_, m in zip(K, mid, strict=True):
                rows.append({"contract": f"AMX{pd.Timestamp(e):%y%m%d}{cp}{int(k_ * 1000):08d}", "root": "AMX",
                             "expiry": pd.Timestamp(e), "strike": k_, "type": cp, "bid": m - half_spread,
                             "ask": m + half_spread, "last": m, "volume": 1.0, "open_interest": 1.0,
                             "vendor_iv": np.nan})
    return OptionChain(underlying="AMX", spot=vendor_spot, as_of=AS_OF, quotes=pd.DataFrame(rows))


@pytest.fixture(scope="module")
def am_pair():
    ch = make_american_chain()
    return (analyze_chain(ch, rate_curve=lambda T: R),
            analyze_chain(ch, rate_curve=lambda T: R, exercise="european"))


def test_exercise_style_is_classified_in_one_place():
    for t in ("SPX", "_SPX", "^spx", "XSP", "NDX", "RUT", "VIX"):
        assert parity.exercise_style(t) == "european", t
    for t in ("SPY", "QQQ", "AAPL", "TESTX", "AMX"):
        assert parity.exercise_style(t) == "american", t
    assert analyze_chain(make_chain()).exercise == "european"                # the chain states it
    with pytest.raises(ValueError, match="exercise"):
        analyze_chain(make_chain(), exercise="bermudan")


def test_american_chain_gives_back_one_vol_for_every_put_and_call(am_pair):
    ca, _ = am_pair
    assert ca.exercise == "american" and not ca.european
    q = ca.quotes[ca.quotes["valid"]]
    assert len(q) == 2 * 9 * 2 and q["iv"].notna().all()
    np.testing.assert_allclose(q["iv"], AM_SIG, atol=1e-6)
    np.testing.assert_allclose(ca.slices["div_yield"], Q, atol=1e-7)
    np.testing.assert_allclose(ca.slices["forward"], S0 * np.exp((R - Q) * ca.slices["T"]), rtol=1e-9)
    np.testing.assert_allclose(ca.slices["pair_vol"], AM_SIG, atol=1e-7)
    assert (ca.slices["parity_rmse"] < 1e-4).all()
    assert (ca.slices["n_parity_bound_violations"] == 0).all()
    assert any("CRR binomial" in n for n in ca.notes) and any("no dividend schedule" in n for n in ca.notes)


def test_european_inversion_of_the_same_chain_shows_the_early_exercise_as_vol(am_pair):
    """What the review found, on a chain where the answer is known: Black-76 on American prices
    puts in-the-money put IVs above the call IVs, and bends parity."""
    _, ce = am_pair
    q = ce.quotes[ce.quotes["valid"]]
    itm_puts = q[(q["type"] == "P") & (q["k"] > 0.1)]
    assert (itm_puts["iv"] - AM_SIG > 0.01).all()
    assert (ce.slices["parity_rmse"] > 0.05).all()


def test_parity_bound_violations_count_tradeable_crossings():
    pq = pd.DataFrame({"c_mid": [3.0, 1.5, 0.2], "p_mid": [1.0, 1.0, 1.0], "c_spread": [0.2, 0.2, 0.02],
                       "p_spread": [0.2, 0.2, 0.02]}, index=pd.Index([100.0, 100.5, 100.0001], name="strike"))
    # S - K D = 1.0 at K = 100 (D = 0.99): 2.9 - 1.1 = 1.8 crosses the upper bound; 1.4 - 1.1 does not;
    # the lower bound S - K = ~0 (q = 0): 0.21 - 0.99 = -0.78 crosses it
    assert parity.bound_violations(pq, 100.0, 1.0, 0.99, 0.0) == 2
    assert parity.bound_violations(pq.iloc[[1]], 100.0, 1.0, 0.99, 0.0) == 0


def test_american_pair_forward_reprices_both_legs():
    from ohcamel_quant.options import american

    T, D = 0.75, np.exp(-R * 0.75)
    c, p = (float(v) for v in american.prices(S0, 540.0, T, R, 0.02, 0.3, np.array([True, False])))
    F, q, sig = parity.american_pair_forward(c, p, 540.0, S0, T, D)
    assert q == pytest.approx(0.02, abs=1e-9) and sig == pytest.approx(0.3, abs=1e-9)
    assert F == pytest.approx(S0 * np.exp((R - 0.02) * T), rel=1e-12)
    with pytest.raises(parity.InsufficientQuotes):
        parity.american_pair_forward(c, c + 600.0, 540.0, S0, T, D)       # implies a negative forward


FRONT = "2026-09-25"         # 1.2 days after AS_OF: q T and early exercise are fractions of a cent


def test_implied_spot_replaces_a_stale_vendor_spot():
    """A chain priced on S0 = 500 that reports a stale spot of 501.50: the front expiry's pair
    nearest the money gives the underlying back, the vendor spot stays as reported, and the later
    expiries' American IVs come back to AM_SIG. Without a front expiry the stale spot is used, and
    the same IVs miss by more than a point."""
    exp = (FRONT, *AM_EXPIRIES)
    ca = analyze_chain(make_american_chain(expiries=exp, q=0.0, vendor_spot=S0 + 1.5), rate_curve=lambda T: R)
    assert ca.spot == S0 + 1.5 and ca.iv_spot_source == "implied" and ca.iv_spot == ca.implied_spot
    assert ca.implied_spot == pytest.approx(S0, abs=0.01)
    assert any("option-implied underlying 500.00, not the vendor spot 501.50" in n for n in ca.notes)
    later = ca.quotes[ca.quotes["valid"] & ca.quotes["expiry"].isin(pd.to_datetime(AM_EXPIRIES))]
    assert later["iv"].notna().all() and np.abs(later["iv"] - AM_SIG).max() < 2e-3
    stale = analyze_chain(make_american_chain(q=0.0, vendor_spot=S0 + 1.5), rate_curve=lambda T: R)
    assert stale.iv_spot_source == "vendor" and stale.iv_spot == S0 + 1.5
    sv = stale.quotes[stale.quotes["valid"]]
    assert np.abs(sv["iv"] - AM_SIG).max() > 1e-2


def test_implied_spot_falls_back_to_the_vendor_spot_and_says_why():
    # no expiry within a week: nothing to imply the level from
    ca = analyze_chain(make_american_chain(q=0.0), rate_curve=lambda T: R)
    assert ca.iv_spot_source == "vendor" and math.isnan(ca.implied_spot) and ca.iv_spot == S0
    assert any("American IVs use the vendor spot 500.00: no expiry within 7 days" in n for n in ca.notes)
    # the implied level is within the front pair's half-spread (0.01) of the vendor spot
    ch = make_american_chain(expiries=(FRONT, *AM_EXPIRIES), q=0.0, vendor_spot=S0 + 0.003)
    ca = analyze_chain(ch, rate_curve=lambda T: R)
    assert ca.iv_spot_source == "vendor" and ca.iv_spot == S0 + 0.003
    assert ca.implied_spot == pytest.approx(S0, abs=0.01)
    assert any("within their half-spread 0.010" in n for n in ca.notes)
    # the function itself, on the same slices
    groups = {sid: (g, float(g["T"].iloc[0])) for sid, g in ca.quotes.groupby("slice")}
    isp = parity.implied_spot(groups, S0 + 0.003, rate_curve=lambda T: R)
    assert (isp.source, isp.used, isp.slice) == ("vendor", S0 + 0.003, FRONT) and isp.half_spread == pytest.approx(0.01)
    assert parity.implied_spot(groups, S0 + 0.5, rate_curve=lambda T: R).source == "implied"
    # an index (European) chain never implies one
    ce = analyze_chain(make_chain())
    assert math.isnan(ce.implied_spot) and ce.iv_spot == ce.spot and ce.iv_spot_source == "vendor"


def _set(ch, expiry, strike, cp, bid, ask):
    q = ch.quotes
    i = q.index[(q["expiry"] == pd.Timestamp(expiry)) & (q["strike"] == strike) & (q["type"] == cp)][0]
    q.loc[i, ["bid", "ask"]] = [bid, ask]
    return q.loc[i, "contract"]


def test_each_iv_status_on_a_constructed_quote():
    """One quote per status on the American test chain (S0 = 500, no front expiry: the vendor spot
    is used, tolerance 0)."""
    ch = make_american_chain()
    long, short = AM_EXPIRIES[1], AM_EXPIRIES[0]
    T = float(parity.year_fraction(AS_OF, parity.expiry_timestamp(long, "AMX"))[0])
    fwd_floor = S0 * np.exp(-Q * T) - 420.0 * np.exp(-R * T)          # D (F - K) for the 420 call
    want = {
        _set(ch, short, 580.0, "P", 78.0, 79.0): "arb_bound",        # ask 79 < intrinsic 80: buy, exercise
        _set(ch, short, 440.0, "C", 500.5, 501.0): "arb_bound",      # bid above S: sell a call worth < S
        _set(ch, short, 560.0, "P", 59.5, 60.6): "at_intrinsic",     # mid 60.05: 0.05 over 60, half-spread 0.55
        _set(ch, long, 420.0, "C", fwd_floor - 1.1, fwd_floor - 0.9): "below_model_forward",  # 80 + ~30 < floor
        _set(ch, long, 440.0, "C", 499.0, 502.0): "no_vol",           # mid 500.5 >= S, bid not above it
        _set(ch, long, 460.0, "C", 0.0, 0.05): "zero_bid",
        _set(ch, long, 480.0, "P", 5.0, 4.0): "crossed",
        _set(ch, long, 500.0, "P", 10.0, 30.0): "wide",
        _set(ch, long, 520.0, "P", np.nan, 40.0): "no_quote",
    }
    ca = analyze_chain(ch, rate_curve=lambda T: R)
    q = ca.quotes.set_index("contract")
    assert {c: q.loc[c, "iv_status"] for c in want} == want
    assert q.loc[list(want), "iv"].isna().all()
    assert (q["iv_status"] == "ok").sum() >= 20
    assert q["arb_bound"].sum() == 2                       # model-free crossings only
    assert any(n.startswith("below_model_forward: 1 calls, 0 puts") for n in ca.notes)
    assert any(n.startswith("at_intrinsic: 0 calls, 1 puts") for n in ca.notes)


def test_european_chain_keeps_its_arb_bound_and_gets_statuses():
    ch = make_chain()
    ca = analyze_chain(ch)
    st = ca.quotes["iv_status"]
    assert set(st) <= {"ok", "zero_bid", "arb_bound", "no_vol", "wide", "crossed", "no_quote"}
    assert not (st == "at_intrinsic").any() and not (st == "below_model_forward").any()


def test_a_price_flat_in_vol_identifies_no_vol():
    """chain._drop_flat on real tree prices: a deep in-the-money put whose price moves by d per vol
    point keeps its IV when d is at least 1% of the half-spread, loses it (no_vol) when it is not."""
    from ohcamel_quant.options import american
    from ohcamel_quant.options.chain import _drop_flat

    K, T, sig = np.array([600.0]), np.array([0.25]), 0.30
    args = (K, T, np.array([R]), np.array([Q]), np.array([False]))
    mid = american.prices(S0, K, T, R, Q, sig, False)
    d = min(float((american.prices(S0, K, T, R, Q, sig + 0.01, False) - mid)[0]),
            float((mid - american.prices(S0, K, T, R, Q, sig - 0.01, False))[0]))
    assert d > 0
    iv = np.array([sig])
    assert np.isfinite(_drop_flat(iv, mid, np.array([50.0 * d]), S0, *args)).all()      # 1 point = 2% of it
    assert np.isnan(_drop_flat(iv, mid, np.array([200.0 * d]), S0, *args)).all()        # 1 point = 0.5%
