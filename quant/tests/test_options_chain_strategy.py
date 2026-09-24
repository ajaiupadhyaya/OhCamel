"""Chain cleaning, parity, surface, density and strategy analytics on a numerical
BSM/SSVI-generated chain (a test fixture: the analytics must recover the inputs)."""

from __future__ import annotations

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
    return OptionChain(underlying=root, spot=spot, as_of=AS_OF, quotes=pd.DataFrame(rows))


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
    assert not ca.european and any("American" in n for n in ca.notes)


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
