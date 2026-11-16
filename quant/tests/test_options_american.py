"""American options (options.american): the CRR tree's pricing identities, its convergence, the
early-exercise boundary under a yield and under a discrete dividend, the implied-vol inversion,
and -- on the real SPY chain of 2026-11-15 -- put and call agreeing on one volatility once early
exercise is priced.

The pricing tests use hand-checkable inputs (no market data is reported from them); the chain test
uses the committed Cboe fixture (tests/data, provenance in its sidecar)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ohcamel_quant.options import american, bsm
from ohcamel_quant.options import chain as chain_mod


def plain_crr(S, K, T, r, sigma, *, is_call, q=0.0, steps):
    """An independent textbook CRR tree (Cox, Ross & Rubinstein 1979; Hull ch. 21): no smoothing,
    no extrapolation, continuous yield only. The cross-check for the package's tree."""
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    disc = math.exp(-r * dt)
    phi = 1.0 if is_call else -1.0
    j = np.arange(steps + 1)
    v = np.maximum(phi * (S * u ** (2.0 * j - steps) - K), 0.0)
    for i in range(steps - 1, -1, -1):
        s = S * u ** (2.0 * np.arange(i + 1) - i)
        v = np.maximum(disc * (p * v[1:] + (1 - p) * v[:-1]), phi * (s - K))
    return float(v[0])


def euro(S, K, T, r, sigma, q, cp):
    return float(bsm.bsm_price(S, K, T, sigma, r, q, cp))


# ------------------------------------------------------------------ pricing identities
def test_deep_itm_put_is_worth_at_least_intrinsic_and_more_than_the_european_put():
    S, T, r, sig = 100.0, 1.0, 0.05, 0.2
    for K in (120.0, 130.0, 150.0):
        a = american.price(S, K, T, r, sig, is_call=False)
        e = euro(S, K, T, r, sig, 0.0, "P")
        assert a >= K - S
        assert a > e + 0.5, (K, a, e)          # the European put trades below intrinsic here


def test_put_matches_an_independent_crr_tree_at_many_steps():
    """The package's tree (BBS + Richardson at 400 steps) against a plain CRR tree at 4,000."""
    for S, K, T, r, sig, q in [(100.0, 100.0, 1.0, 0.05, 0.2, 0.0), (100.0, 110.0, 0.5, 0.06, 0.3, 0.01),
                               (100.0, 90.0, 2.0, 0.04, 0.25, 0.02)]:
        a = american.price(S, K, T, r, sig, is_call=False, q=q)
        ref = plain_crr(S, K, T, r, sig, is_call=False, q=q, steps=4000)
        assert a == pytest.approx(ref, abs=2e-3), (S, K, T)


def test_call_without_dividends_equals_black_scholes():
    """Merton (1973): no early exercise without dividends, so the American call is the European one."""
    for K in (80.0, 100.0, 120.0):
        for T, sig in ((0.25, 0.2), (1.0, 0.2), (1.0, 0.4)):
            a = american.price(100.0, K, T, 0.05, sig, is_call=True)
            assert a == pytest.approx(euro(100.0, K, T, 0.05, sig, 0.0, "C"), abs=1e-3), (K, T, sig)


def test_the_one_thousandth_tolerance_is_one_a_plain_tree_misses():
    # why the tree is smoothed and extrapolated: a plain 400-step CRR tree is 5e-3 off at the money
    e = euro(100.0, 100.0, 1.0, 0.05, 0.2, 0.0, "C")
    assert abs(plain_crr(100.0, 100.0, 1.0, 0.05, 0.2, is_call=True, steps=400) - e) > 1e-3
    assert abs(american.price(100.0, 100.0, 1.0, 0.05, 0.2, is_call=True) - e) < 1e-3


def test_call_with_yield_above_the_rate_is_exercised_early():
    S, K, T, r, q, sig = 100.0, 100.0, 0.5, 0.08, 0.12, 0.2
    a = american.price(S, K, T, r, sig, is_call=True, q=q)
    assert a - euro(S, K, T, r, sig, q, "C") > 0.05


def test_converges_400_steps_within_a_cent_of_1600():
    cases = [
        dict(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2, is_call=False),
        dict(S=100.0, K=110.0, T=2.0, r=0.06, sigma=0.3, is_call=False, q=0.01),
        dict(S=100.0, K=100.0, T=0.5, r=0.08, sigma=0.2, is_call=True, q=0.12),
        dict(S=100.0, K=80.0, T=0.5, r=0.05, sigma=0.2, is_call=True, dividends=[(0.25, 2.0)]),
    ]
    for c in cases:
        S, K, T, r, sig = c.pop("S"), c.pop("K"), c.pop("T"), c.pop("r"), c.pop("sigma")
        a400 = american.price(S, K, T, r, sig, steps=400, **c)
        a1600 = american.price(S, K, T, r, sig, steps=1600, **c)
        assert abs(a400 - a1600) < 0.01, (c, a400, a1600)
    # the cent can be missed: 10 steps are not converged (0.026 off)
    assert abs(american.price(100.0, 100.0, 1.0, 0.05, 0.2, is_call=False, steps=10)
               - american.price(100.0, 100.0, 1.0, 0.05, 0.2, is_call=False, steps=1600)) > 0.01


def test_call_before_a_discrete_dividend_is_worth_at_least_holding_or_exercising_just_before_it():
    """Escrowed dividend model (Hull ch. 21): S* = S - PV(div). Holding to T is a European call on S*;
    exercising just before the dividend at t_d pays S*_{t_d} + div - K, a European call on S* struck
    at K - div maturing at t_d (Black's 1975 pseudo-American bound). The American is worth both."""
    S, T, r, sig, div = 100.0, 0.5, 0.05, 0.2, 2.0
    td = T / 2
    s_star = S - div * math.exp(-r * td)
    for K in (80.0, 100.0):
        a = american.price(S, K, T, r, sig, is_call=True, dividends=[(td, div)])
        hold = euro(s_star, K, T, r, sig, 0.0, "C")
        early = euro(s_star, K - div, td, r, sig, 0.0, "C")
        assert a >= max(hold, early), (K, a, hold, early)
    # deep in the money, exercising before the dividend beats holding: the bound binds through `early`
    a = american.price(S, 80.0, T, r, sig, is_call=True, dividends=[(td, div)])
    assert euro(s_star, 78.0, td, r, sig, 0.0, "C") > euro(s_star, 80.0, T, r, sig, 0.0, "C") + 0.5
    assert a > euro(s_star, 80.0, T, r, sig, 0.0, "C") + 0.5


def test_dividends_at_or_after_expiry_or_already_paid_are_ignored():
    base = american.price(100.0, 95.0, 0.5, 0.05, 0.25, is_call=False)
    for div in ([(0.5, 3.0)], [(0.75, 3.0)], [(0.0, 3.0)], [(-0.1, 3.0)]):
        assert american.price(100.0, 95.0, 0.5, 0.05, 0.25, is_call=False, dividends=div) == base
    assert american.price(100.0, 95.0, 0.5, 0.05, 0.25, is_call=False, dividends=[(0.2, 3.0)]) > base


# ------------------------------------------------------------------ implied volatility
@pytest.mark.parametrize("is_call", [True, False])
@pytest.mark.parametrize("K", [70.0, 95.0, 100.0, 108.0, 140.0])
def test_implied_vol_round_trips(is_call, K):
    for sig, kw in ((0.15, {}), (0.45, {"q": 0.03}), (0.25, {"dividends": [(0.1, 1.5), (0.35, 1.5)]})):
        p = american.price(100.0, K, 0.5, 0.05, sig, is_call=is_call, **kw)
        if p <= max((100.0 - K) if is_call else (K - 100.0), 0.0) + 1e-9:
            continue            # exercised at once: no vol is identified
        assert american.implied_vol(p, 100.0, K, 0.5, 0.05, is_call=is_call, **kw) == pytest.approx(sig, abs=1e-6)


def test_implied_vol_is_nan_outside_the_american_bounds():
    # at or below immediate exercise, at or above the underlying (call) / strike (put): no vol fits
    assert math.isnan(american.implied_vol(19.99, 100.0, 120.0, 1.0, 0.05, is_call=False))
    assert math.isnan(american.implied_vol(20.0, 100.0, 120.0, 1.0, 0.05, is_call=False))
    assert math.isnan(american.implied_vol(120.0, 100.0, 120.0, 1.0, 0.05, is_call=False))
    assert math.isnan(american.implied_vol(100.0, 100.0, 90.0, 1.0, 0.05, is_call=True))
    assert math.isnan(american.implied_vol(0.0, 100.0, 90.0, 1.0, 0.05, is_call=True))


def test_implied_vol_of_an_itm_put_is_below_its_european_implied_vol():
    """The review's defect in one number: invert an American put price with Black-Scholes and the
    early-exercise premium shows up as volatility."""
    p = american.price(100.0, 115.0, 1.0, 0.05, 0.2, is_call=False)
    assert american.implied_vol(p, 100.0, 115.0, 1.0, 0.05, is_call=False) == pytest.approx(0.2, abs=1e-6)
    assert float(bsm.implied_vol(p, 100.0, 115.0, 1.0, 0.05, 0.0, "P")) > 0.2 + 0.02


def test_vectorized_entry_points_agree_with_the_scalar_ones():
    K = np.array([90.0, 100.0, 110.0, 120.0])
    typ = np.array([True, False, True, False])
    px = american.prices(100.0, K, 0.75, 0.04, 0.01, 0.3, typ)
    for i in range(4):
        assert px[i] == american.price(100.0, K[i], 0.75, 0.04, 0.3, is_call=bool(typ[i]), q=0.01)
    iv = american.implied_vols(px, 100.0, K, 0.75, 0.04, 0.01, typ)
    np.testing.assert_allclose(iv, 0.3, atol=1e-6)


def test_bad_inputs_are_refused():
    with pytest.raises(ValueError, match="steps"):
        american.price(100.0, 100.0, 1.0, 0.05, 0.2, is_call=True, steps=401)
    with pytest.raises(ValueError, match="dividend"):
        american.price(100.0, 100.0, 1.0, 0.05, 0.2, is_call=True, dividends=[(0.5, -1.0)])
    assert math.isnan(american.price(100.0, 100.0, 1.0, 0.05, -0.2, is_call=True))
    assert math.isnan(american.price(100.0, 100.0, 0.0, 0.05, 0.2, is_call=True))


# ------------------------------------------------------------------ the real SPY chain
#: A leg's IV is resolved when its own bid/ask pin it within this many vol points either side: two
#: resolved legs can then be told apart at the 0.5-point tolerance (0.25 + 0.25).
RESOLVED_HALF_WIDTH = 0.25


def same_strike_pairs(ca, kmax=0.15):
    """Strikes with a valid call and put (both IVs finite), |k| < kmax: put minus call IV and
    each leg's IV half-spread, in vol points."""
    q = ca.quotes
    cols = ["slice", "strike", "k", "iv", "iv_bid", "iv_ask", "valid"]
    j = q[q["type"] == "C"][cols].merge(q[q["type"] == "P"][cols], on=["slice", "strike"], suffixes=("_c", "_p"))
    j = j[j["valid_c"] & j["valid_p"] & np.isfinite(j["iv_c"]) & np.isfinite(j["iv_p"]) & (j["k_c"].abs() < kmax)]
    return j.assign(d=(j["iv_p"] - j["iv_c"]) * 100, hw_c=(j["iv_ask_c"] - j["iv_bid_c"]) * 50,
                    hw_p=(j["iv_ask_p"] - j["iv_bid_p"]) * 50)


def resolved(pairs):
    return pairs[(pairs["hw_c"] <= RESOLVED_HALF_WIDTH) & (pairs["hw_p"] <= RESOLVED_HALF_WIDTH)]


def test_spy_put_and_call_agree_within_half_a_vol_point_on_every_quote_pinned_pair(spy_2026_11_15_american):
    """The brief's condition, asserted where it is testable: on the review's SPY chain, at every
    strike with |k| < 0.15 whose call and put IVs are each pinned within +-0.25 points by their own
    bid/ask, the American IVs of the put and the call differ by under 0.5 points -- all of them
    (287 pairs on the whole chain; the reference engine's five expiries hold fewer).

    The check can fail: the same pairs inverted with Black-76 (the chain before this work) differ by
    0.5 points or more at some of them (16 on the whole chain, up to 2.0 points at two years), so it
    is the American pricer that makes them pass. Pairs whose quotes do not pin both IVs (mostly
    in-the-money legs $2-5 wide) are reported, not asserted."""
    ca, ce, _, _ = spy_2026_11_15_american
    am = resolved(same_strike_pairs(ca))
    assert len(am) >= 25 and am["slice"].nunique() >= 4
    worst = am.loc[am["d"].abs().idxmax()]
    assert am["d"].abs().max() < 0.5, (worst["slice"], worst["strike"], worst["d"])
    eu = same_strike_pairs(ce).set_index(["slice", "strike"])
    keys = [k for k in zip(am["slice"], am["strike"], strict=True) if k in eu.index]
    assert len(keys) >= 0.9 * len(am)
    assert (eu.loc[keys, "d"].abs() >= 0.5).sum() > 0                  # Black-76 fails on the same pairs


def test_spy_ivs_use_the_option_implied_underlying(spy_2026_11_15_american):
    """The payload's spot, 754.37, is the 16:00 close; the options quoted to 16:15 put the
    underlying at 752.62 (front expiry, 0.7 days: the American pair nearest the money at q = 0). The
    chain keeps the vendor spot, carries the implied level separately, says which the IVs use and
    why -- and the yield the stale spot used to fake (132% at 0.7 days) is gone."""
    ca, ce, chain, _ = spy_2026_11_15_american
    assert ca.spot == chain.spot == 754.37                           # the vendor's spot, untouched
    assert ca.iv_spot_source == "implied" and ca.iv_spot == ca.implied_spot
    assert ca.implied_spot == pytest.approx(752.616, abs=0.001)
    note = next(n for n in ca.notes if "option-implied underlying" in n)
    assert "752.62" in note and "754.37" in note and "16:15" in note
    short = ca.slices["dte"] < 7
    assert short.any() and (ca.slices.loc[short, "div_yield"].abs() < 0.05).all()
    assert (ce.slices.loc[short, "div_yield"] > 0.15).all()          # Black-76's q from the stale spot
    assert math.isnan(ce.implied_spot) and ce.iv_spot == ce.spot     # European: unaffected


def _reference_expiries(c):
    from conftest import SPY_REFERENCE_EXPIRIES

    return c.quotes[c.quotes["expiry"].dt.strftime("%Y-%m-%d").isin(SPY_REFERENCE_EXPIRIES)]


def test_spy_every_blank_iv_has_a_status_and_the_counts_are_exact(spy_2026_11_15_american):
    """Every quote says why it has, or has not, an IV. Exact counts per status and side on the five
    expiries both engines hold. Whole chain (Rust): ok 4515 C / 4023 P, at_intrinsic 750 / 1949,
    below_model_forward 251 / 0, no_vol 8 / 0 (prices flat in vol at the root), wide 306 / 112,
    zero_bid 480 / 226."""
    ca = spy_2026_11_15_american[0]
    assert set(ca.quotes["iv_status"]) <= set(chain_mod.IV_STATUSES)
    q = _reference_expiries(ca)
    got = {k: int(v) for k, v in q.groupby(["iv_status", "type"]).size().items()}
    assert got == {("ok", "C"): 627, ("ok", "P"): 577, ("no_vol", "C"): 1, ("at_intrinsic", "C"): 178, ("at_intrinsic", "P"): 362,
                   ("below_model_forward", "C"): 54, ("wide", "C"): 53, ("wide", "P"): 30,
                   ("zero_bid", "C"): 113, ("zero_bid", "P"): 57}
    note = next(n for n in ca.notes if n.startswith("below_model_forward:"))
    assert "single dividend yield" in note and "discrete dividends are not available" in note


def test_spy_has_no_model_free_arbitrage(spy_2026_11_15_american):
    """arb_bound on the American chain is a tradeable crossing of an exercise bound against the
    implied 752.62 (tolerance: the front pair's half-spread, 0.025): none on the whole chain,
    against 87 forward-bound flags before this work -- which the European twin still shows (20 on
    the five expiries, e.g. the 0.7-day 565 call at 186.28 / 189.05 against D (F - K) = 187.67).

    Hand-check of the two quotes closest to crossing (both 0.7-day puts, intrinsic K - 752.616):
      768 P: ask 15.58 vs intrinsic 768 - 752.616 = 15.384 -> the ask is 0.196 ABOVE it: no
             crossing (at_intrinsic: the bid 15.16 is below intrinsic);
      763 P: ask 10.62 vs intrinsic 10.384 -> 0.236 above: no crossing (at_intrinsic).
    No bid exceeds S (calls) or K (puts)."""
    ca, ce, _, _ = spy_2026_11_15_american
    q = ca.quotes
    assert int(q["arb_bound"].sum()) == 0
    S, phi = ca.iv_spot, np.where(q["type"] == "C", 1.0, -1.0)
    exer = np.maximum(phi * (S - q["strike"]), 0.0)
    v = q["valid"]
    assert (q.loc[v, "ask"] - exer[v]).min() > -0.025                  # the definition, recomputed
    row = q[(q["slice"] == "2026-11-16") & (q["strike"] == 768.0) & (q["type"] == "P")].iloc[0]
    assert row["ask"] == 15.58 and row["ask"] - (768.0 - S) == pytest.approx(0.196, abs=1e-3)
    assert row["iv_status"] == "at_intrinsic"
    assert int(_reference_expiries(ce)["arb_bound"].sum()) == 20
    if len(ce.slices) == 32:                                          # the whole chain (Rust)
        assert int(ce.quotes["arb_bound"].sum()) == 87


def test_spy_parity_residual_after_early_exercise_is_within_half_a_dollar_beyond_six_months(spy_2026_11_15_american):
    """The review's second symptom: the European parity fit's RMSE grew to $4.95 at two years. With
    each leg's early-exercise premium removed, every expiry beyond six months is within $0.50."""
    ca, ce, _, _ = spy_2026_11_15_american
    long = ca.slices["T"] > 0.5
    assert long.sum() >= 2
    assert (ca.slices.loc[long, "parity_rmse"] < 0.5).all(), ca.slices.loc[long, "parity_rmse"]
    assert (ce.slices.loc[long, "parity_rmse"] > 1.0).any()


def test_spy_american_surface_keeps_the_smile_and_density_checks(spy_2026_11_15_american):
    """The surface built from the American IVs passes what the review's surface passes: Lee's
    bound, the vertex inside the quotes, no butterfly arbitrage, a fit within one vol point, and
    every density check that can fail."""
    from test_options_metrics_density import _failed

    from ohcamel_quant.options.surface import build_surface

    ca, _, _, _ = spy_2026_11_15_american
    vs = build_surface(ca)
    assert not vs.errors and len(vs.fits) == int((ca.slices["dte"] >= 1.0).sum())     # build_surface's min_dte
    bad = {}
    for sid, f in vs.fits.items():
        p, s = f.params, ca.slices.loc[sid]
        problems = [n for n, ok in (("lee", p.b * (1 + abs(p.rho)) <= 2.0 + 1e-9),
                                    ("vertex", f.k_min <= p.m <= f.k_max),
                                    ("butterfly", f.butterfly["arbitrage_free"]),
                                    ("rmse", f.rmse_vol_pts < 1.0)) if not ok]
        problems += sorted(_failed(float(s["forward"]), float(s["discount"]), float(s["T"]), p))
        if problems:
            bad[sid] = problems
    assert not bad, bad


def _strategy_on(ca, contract):
    from ohcamel_quant.options import strategy

    legs = strategy.resolve_legs(ca, [{"qty": 1, "contract": contract}])
    out = strategy.analyze_position(ca, legs)
    S = out["grid"]["spot"]
    i = int(np.argmin(np.abs(S - ca.iv_spot)))
    assert S[i] == ca.iv_spot                                      # the grid holds the level itself
    return legs[0], out, float(out["grid"]["curves"]["today"]["pnl"][i])


def test_spy_strategy_today_is_the_mid_cost_at_the_level_the_ivs_use(spy_2026_11_15_american):
    """The strategy values a leg with Black-Scholes at its IV at ca.iv_spot, the level that IV (and
    its expiry's q) was solved at. Its value today at that level then equals the mid paid, up to the
    leg's early-exercise premium (the chain's mid - model_price, ~0 for these calls). At the vendor
    spot (754.37) the same 33-day at-the-money call would be $124 a contract off."""
    ca = spy_2026_11_15_american[0]
    q = ca.quotes
    pick = q[(q["iv_status"] == "ok") & (q["root"] == "SPY") & (q["slice"] == "2026-12-18") & (q["k"].abs() < 0.03)]
    for typ in ("C", "P"):
        row = pick[pick["type"] == typ].iloc[0]
        leg, _, today = _strategy_on(ca, row["contract"])
        assert today == pytest.approx(-100.0 * (row["mid"] - row["model_price"]), abs=1e-6), row["contract"]
        if typ == "C":
            assert abs(today) < 0.05
            stale = 100.0 * (float(bsm.bsm_price(ca.spot, leg.strike, leg.T, leg.iv, leg.r, leg.q, "C")) - leg.mid)
            assert stale > 50.0                                    # what valuing at the vendor spot did


def test_spy_strategy_delta_is_the_chain_tables_delta(spy_2026_11_15_american):
    ca = spy_2026_11_15_american[0]
    q = ca.quotes
    for _, row in q[(q["iv_status"] == "ok") & (q["root"] == "SPY")].sample(6, random_state=3).iterrows():
        _, out, _ = _strategy_on(ca, row["contract"])
        assert out["legs"][0]["delta"] / 100.0 == pytest.approx(row["delta"], rel=1e-12, abs=1e-15), row["contract"]
