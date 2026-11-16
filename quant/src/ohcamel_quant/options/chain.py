"""Chain cleaning and per-quote analytics: implied forward, our own IV, greeks.

Pipeline (each step is recorded in ``ChainAnalytics.notes`` / per-row flags):

1. drop contracts with non-standard deliverables (OCC roots ending in a digit,
   e.g. ``AAPL1`` after a corporate action) -- their payoff is not ``(S-K)^+``;
2. settlement instant and ACT/365 ``T`` per expiry (:mod:`.parity`); expired
   slices are dropped;
3. quote-quality flags: ``no_quote`` / ``stale`` (missing bid or ask -- Cboe's delayed
   payload carries no per-quote timestamp, so a one-sided quote is the only
   observable staleness), ``zero_bid``, ``crossed``
   (ask <= bid), ``wide`` (spread > ``max_rel_spread`` x mid); ``valid`` = none;
4. per-expiry implied forward ``F`` and discount factor ``D`` from put-call parity
   (:func:`.parity.fit_all`), hence ``r`` and ``q`` -- no external dividend schedule;
   for American-style underlyings ``F`` comes from the strike pair nearest the money
   priced as American (:func:`.parity.american_pair_forward`);
5. implied vol from the mid (and from bid / ask): Black-76 for European exercise,
   the American CRR tree (:mod:`.american`, on spot with the slice's ``r`` and ``q``)
   for American exercise; NaN where no volatility reproduces the mid, with the reason in
   ``iv_status`` (:data:`IV_STATUSES`): ``arb_bound`` is a crossing of the no-arbitrage bounds
   (Black-76's for European exercise; for American exercise only a tradeable crossing of the
   model-free exercise bounds), ``at_intrinsic`` (American: no time value) and
   ``below_model_forward`` (American: under the model forward's ``D (F - K)+``);
6. OTM selection for smile construction: puts for ``K < F``, calls for ``K >= F``
   (standard practice, e.g. Cboe VIX white paper; Ait-Sahalia & Lo 1998) because
   OTM quotes are the liquid ones;
7. greeks (BSM with the slice's implied r, q at our IV; :mod:`.bsm`) and comparison
   with the vendor's IV.

Exercise style is :func:`.parity.exercise_style` of the underlying (index options
European, ETF and single-name options American) unless the caller or the chain
(``OptionChain.exercise``) states it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import american, bsm, parity

CONTRACT_MULTIPLIER = 100.0


@dataclass
class ChainAnalytics:
    underlying: str
    spot: float
    as_of: pd.Timestamp
    quotes: pd.DataFrame           # one row per contract with flags, iv, greeks
    slices: pd.DataFrame           # one row per expiry slice (index = slice id)
    exercise: parity.Exercise
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    implied_spot: float = float("nan")   # option-implied underlying (American; NaN if not formed)
    iv_spot: float = float("nan")        # the level the IVs and greeks use (vendor spot or implied)
    iv_spot_source: str = "vendor"       # 'vendor' | 'implied'

    @property
    def european(self) -> bool:
        return self.exercise == "european"

    def slice_quotes(self, sid: str) -> pd.DataFrame:
        return self.quotes[self.quotes["slice"] == sid]

    def resolve(self, expiry: str | None) -> str:
        """Slice id for an expiry query: exact id, an ISO date (PM slice preferred),
        or None -> the first slice at least 7 calendar days out (else the first)."""
        ids = list(self.slices.index)
        if not ids:
            raise ValueError("chain has no usable expiries")
        if expiry is None:
            later = self.slices.index[self.slices["dte"] >= 7]
            return str(later[0]) if len(later) else str(ids[0])
        e = str(expiry).strip()
        if e in ids:
            return e
        try:
            d = pd.Timestamp(e).date().isoformat()
        except (ValueError, TypeError) as err:
            raise ValueError(f"unrecognized expiry {expiry!r}") from err
        for cand in (d, d + "AM"):
            if cand in ids:
                return cand
        raise ValueError(f"expiry {expiry} not listed; available: {', '.join(ids[:40])}")


def slice_id(expiry: pd.Timestamp, settlement: str) -> str:
    d = pd.Timestamp(expiry).date().isoformat()
    return d + ("AM" if settlement == "AM" else "")


def analyze_chain(
    chain: Any, max_rel_spread: float = 0.5, n_parity_strikes: int = 20,
    max_rate_se: float = 0.005, rate_curve: Any = None, exercise: parity.Exercise | None = None,
) -> ChainAnalytics:
    """Clean an OptionChain (``underlying, spot, as_of, quotes``) and compute analytics.

    Parameters are quote-filter *choices*, not market inputs: ``max_rel_spread``
    (bid-ask spread as a fraction of mid above which a quote is flagged ``wide``),
    ``n_parity_strikes`` (near-the-money strikes in the parity regression),
    ``max_rate_se`` (rate standard error above which a slice borrows ``r``).
    ``rate_curve`` (``T -> r``, continuously compounded, from market data) fixes the
    discount factor per expiry instead of inferring it from parity -- used for
    American-style underlyings (see :mod:`.parity`). ``exercise`` overrides the style the
    chain states (``chain.exercise``) or :func:`.parity.exercise_style` gives.
    """
    notes: list[str] = []
    style = exercise or getattr(chain, "exercise", None) or parity.exercise_style(chain.underlying)
    if style not in ("american", "european"):
        raise ValueError(f"exercise must be 'american' or 'european'; got {style!r}")
    am = style == "american"
    spot = float(chain.spot)
    as_of = pd.Timestamp(chain.as_of)
    q = chain.quotes.copy()
    if "root" not in q.columns:
        q["root"] = chain.underlying
    q = q.reset_index(drop=True)
    for col in ("bid", "ask", "last", "volume", "open_interest", "vendor_iv"):
        if col not in q.columns:
            q[col] = np.nan
    q["root"] = q["root"].astype(str)
    nonstd = q["root"].str[-1].str.isdigit()
    if nonstd.any():
        notes.append(f"dropped {int(nonstd.sum())} contracts with non-standard deliverables "
                     f"(adjusted roots: {', '.join(sorted(q.loc[nonstd, 'root'].unique()))})")
        q = q[~nonstd].reset_index(drop=True)

    q["settlement"] = q["root"].map(parity.settlement_style)
    q["expiry"] = pd.to_datetime(q["expiry"]).dt.normalize()
    q["expiry_ts"] = parity.expiry_timestamps(q["expiry"], q["root"]).to_numpy()
    q["T"] = parity.year_fraction(as_of, q["expiry_ts"]) if len(q) else np.array([], dtype=float)
    expired = q["T"] <= 0
    if expired.any():
        notes.append(f"dropped {int(expired.sum())} contracts at or past settlement")
        q = q[~expired].reset_index(drop=True)
    if q.empty:
        raise parity.InsufficientQuotes(f"{chain.underlying}: no unexpired contracts in the chain")
    q["slice"] = [slice_id(e, s) for e, s in zip(q["expiry"], q["settlement"], strict=True)]

    bid, ask = q["bid"].astype(float), q["ask"].astype(float)
    q["mid"] = (bid + ask) / 2.0
    q["spread"] = ask - bid
    q["rel_spread"] = q["spread"] / q["mid"]
    q["no_quote"] = bid.isna() | ask.isna()
    q["stale"] = q["no_quote"]  # the delayed feed has no per-quote timestamps: a missing side = stale
    q["zero_bid"] = ~q["no_quote"] & (bid <= 0)
    q["crossed"] = ~q["no_quote"] & ~q["zero_bid"] & (ask <= bid)
    q["wide"] = ~q["no_quote"] & ~q["zero_bid"] & ~q["crossed"] & (q["rel_spread"] > max_rel_spread)
    q["valid"] = ~(q["no_quote"] | q["zero_bid"] | q["crossed"] | q["wide"])

    groups = {sid: (g, float(g["T"].iloc[0])) for sid, g in q.groupby("slice", sort=False)}
    # American trees exercise against the underlying, so it must be the level at the quotes' time
    pairs = {sid: parity.pair_quotes(g) for sid, (g, _) in groups.items()}
    isp = parity.implied_spot(groups, spot, n_parity_strikes, rate_curve, pairs=pairs) if am else None
    S = isp.used if am else spot
    fits, errors = parity.fit_all(groups, S, n_parity_strikes, max_rate_se, rate_curve=rate_curve,
                                  exercise=style, pairs=pairs)
    for sid, msg in errors.items():
        notes.append(f"{sid}: forward not identified ({msg}); slice excluded")
    q = q[q["slice"].isin(list(fits))].reset_index(drop=True)
    if q.empty:
        raise parity.InsufficientQuotes(f"{chain.underlying}: no expiry has enough two-sided quotes "
                                        "to infer the forward from put-call parity")
    fmap = {sid: f for sid, f in fits.items()}
    q["F"] = q["slice"].map(lambda s: fmap[s].forward)
    q["D"] = q["slice"].map(lambda s: fmap[s].discount)
    q["r"] = q["slice"].map(lambda s: fmap[s].rate)
    q["q"] = q["slice"].map(lambda s: fmap[s].div_yield)
    K = q["strike"].to_numpy(float)
    F, D, T = q["F"].to_numpy(float), q["D"].to_numpy(float), q["T"].to_numpy(float)
    typ = q["type"].to_numpy()
    q["k"] = np.log(K / F)
    q["otm"] = np.where(typ == "P", K < F, K >= F)
    phi = bsm.option_sign(typ)
    intrinsic = D * np.maximum(phi * (F - K), 0.0)
    mid = q["mid"].to_numpy(float)
    bid_a, ask_a = q["bid"].to_numpy(float), q["ask"].to_numpy(float)
    valid = q["valid"].to_numpy()
    if am:
        # Model-free American bounds (Hull 2018, ch. 11): worth at least immediate exercise against the
        # level the IVs use, at most the underlying (call) / the strike (put). arb_bound is a TRADEABLE
        # crossing of them: the ask below exercise value by more than the level's own uncertainty (the
        # front pair's half-spread), or the bid above the upper bound. D (F - K)+ is not a bound here:
        # F rests on our r and q, so a quote under it is a model misfit, not arbitrage.
        exer = np.maximum(phi * (S - K), 0.0)
        upper = np.where(typ == "C", S, K)
        tol = isp.half_spread if np.isfinite(isp.half_spread) else 0.0
        cross = valid & ((ask_a < exer - tol) | (bid_a > upper))
        at_intr = valid & ~cross & (mid - exer <= (ask_a - bid_a) / 2.0)     # no time value: no vol
        below_fwd = valid & ~cross & ~at_intr & (mid <= intrinsic)          # under the model's D (F - K)+
        q["arb_bound"] = cross
        r_, q_ = q["r"].to_numpy(float), q["q"].to_numpy(float)

        def ivs(px: np.ndarray) -> np.ndarray:
            ok = np.isfinite(px)
            out = np.full(px.shape, np.nan)
            out[ok] = american.implied_vols(px[ok], S, K[ok], T[ok], r_[ok], q_[ok], typ[ok] == "C")
            return out
    else:
        lower, upper = intrinsic, np.where(typ == "C", D * F, D * K)
        q["arb_bound"] = q["valid"] & ((mid <= lower) | (mid >= upper))
        at_intr = below_fwd = np.zeros(len(q), dtype=bool)

        def ivs(px: np.ndarray) -> np.ndarray:
            return bsm.implied_vol_black(px, F, K, T, D, typ)
    good_mid = valid & ~q["arb_bound"].to_numpy() & ~at_intr & ~below_fwd
    q["iv"] = ivs(np.where(good_mid, mid, np.nan))
    q["iv_bid"] = ivs(np.where(bid_a > 0, bid_a, np.nan))
    q["iv_ask"] = ivs(np.where(ask_a > 0, ask_a, np.nan))
    q["iv_status"] = iv_status(q, at_intr, below_fwd)
    q["use_smile"] = q["valid"] & q["otm"] & np.isfinite(q["iv"])
    g = bsm.bsm_greeks(S, K, T, q["iv"].to_numpy(float), q["r"].to_numpy(float), q["q"].to_numpy(float), typ)
    for name in ("delta", "gamma", "vega", "theta_day", "rho", "vanna", "volga", "charm_day"):
        q[name] = g[name]
    q["model_price"] = g["price"]
    q["iv_minus_vendor"] = q["iv"] - q["vendor_iv"]

    rows = []
    for sid, g_ in q.groupby("slice", sort=False):
        f = fmap[sid]
        ivd = g_.loc[g_["use_smile"], "iv_minus_vendor"].abs()
        parity_rmse, n_bound = f.residual_rmse, float("nan")
        if am:
            parity_rmse = _premium_free_parity_rmse(g_, f, S)
            n_bound = parity.bound_violations(pairs[sid], S, f.T, f.discount, f.div_yield)
        rows.append({
            "slice": sid, "expiry": g_["expiry"].iloc[0], "expiry_ts": g_["expiry_ts"].iloc[0],
            "settlement": g_["settlement"].iloc[0], "roots": ",".join(sorted(g_["root"].unique())),
            "T": f.T, "dte": f.T * 365.0, "forward": f.forward, "discount": f.discount,
            "rate": f.rate, "div_yield": f.div_yield, "rate_source": f.rate_source, "rate_se": f.rate_se,
            "forward_se": f.forward_se, "parity_rmse": parity_rmse, "parity_strikes": f.n_strikes,
            "n_parity_bound_violations": n_bound, "pair_vol": f.pair_vol,
            "n_contracts": len(g_), "n_valid": int(g_["valid"].sum()), "n_smile": int(g_["use_smile"].sum()),
            "n_arb_bound": int(g_["arb_bound"].sum()),
            "vendor_iv_mad": float(ivd.median()) if ivd.notna().any() else float("nan"),
        })
    slices = pd.DataFrame(rows).set_index("slice").sort_values("T")
    q = q.sort_values(["T", "slice", "type", "strike"]).reset_index(drop=True)

    notes.append(f"quotes flagged 'wide' when bid-ask spread > {max_rel_spread:.0%} of mid; IVs use mids")
    if rate_curve is not None:
        notes.append("discount D per expiry from the Treasury curve (FRED constant-maturity yields, "
                     "continuously compounded, interpolated in T); forward F implied from put-call parity "
                     "with D fixed; q = r - ln(F/S)/T")
    else:
        notes.append("forward F and discount D per expiry are implied from put-call parity (no external "
                     "rate or dividend assumption); r = -ln D / T and q = r - ln(F/S)/T are continuously "
                     "compounded")
    borrowed = slices.index[slices["rate_source"] == "borrowed"].tolist()
    if borrowed:
        notes.append(f"{len(borrowed)} short/illiquid expiries borrow r from better-identified expiries "
                     "(parity slope not identified by the quotes)")
    if am and isp.source == "implied":
        notes.append(f"American IVs, greeks and q use the option-implied underlying {isp.level:.2f}, not the "
                     f"vendor spot {spot:.2f}: the vendor spot is the stock's last trade (the 16:00 ET close "
                     "after the bell) while equity option quotes run to 16:15 ET, and an American tree "
                     "exercises against spot, so the gap would read as early exercise and yield. The level is "
                     f"European parity on the {isp.slice} call and put nearest the money (S = K D + C - P; "
                     "q and early exercise taken as nil over that front expiry); each expiry's q then comes "
                     "from its own pair with that level fixed")
    elif am:
        notes.append(f"American IVs use the vendor spot {spot:.2f}: {isp.reason}")
    if am:
        st = q["iv_status"]
        side = lambda s_: ", ".join(f"{int(((st == s_) & (q['type'] == t)).sum())} {n}"  # noqa: E731
                                    for t, n in (("C", "calls"), ("P", "puts")))
        notes.append(f"arb_bound on an American chain is model-free: a tradeable crossing of an exercise bound "
                     f"(ask below intrinsic against {S:.2f} by more than {tol:.3f}, or a call bid above it / a put "
                     f"bid above its strike): {side('arb_bound')}. The European bound D(F - K)+ is not used -- "
                     "F rests on our r and q")
        notes.append(f"at_intrinsic: {side('at_intrinsic')} have a mid within their own half-spread of intrinsic "
                     "-- no time value, so no volatility is identified (IV blank), not arbitrage")
        if (st == "below_model_forward").any():
            notes.append(f"below_model_forward: {side('below_model_forward')} have a mid below D(F - K)+ for the "
                         "expiry's forward, so no volatility fits (IV blank). A single dividend yield per expiry, "
                         "taken from the pair nearest the money, cannot fit deep in-the-money long-dated calls; "
                         "discrete dividends are not available to this chain yet")
        if (st == "no_vol").any():
            notes.append(f"no_vol: {side('no_vol')} valid quotes have no volatility in the search interval that "
                         "reproduces their mid (IV blank)")
        notes.append(f"American-style options: every IV inverts a {american.STEPS}-step CRR binomial price "
                     "(Black-Scholes at the last step, Richardson-extrapolated; Broadie & Detemple 1996) on spot "
                     "with the expiry's r and a continuous yield q, by Brent's method")
        notes.append("q per expiry is implied by the strike pair nearest the money alone: the (vol, q) at which "
                     "the American call and put there both reprice their mids, F = S e^{(r-q)T}; no dividend "
                     "schedule is used (none is available offline), so SPY-style discrete dividends are carried "
                     "as that yield. Elsewhere parity is only a bound, S e^{-qT} - K <= C - P <= S - K D: "
                     "n_parity_bound_violations counts strikes whose bid/ask cross it; parity_rmse is C - P "
                     "after removing each leg's early-exercise premium (its mid less Black-Scholes at its "
                     "American IV), over the parity strikes")
        notes.append("greeks are Black-Scholes at the American IV, not tree greeks (an in-the-money put near "
                     "its exercise boundary has |delta| closer to 1 than shown); model_price is the European "
                     "value at that IV, so mid - model_price is the early-exercise premium")
    notes.append("Cboe delayed quotes (15 minutes); T is ACT/365 from the quote time to 16:00 ET "
                 "(09:30 ET for AM-settled index roots)")
    return ChainAnalytics(
        underlying=str(chain.underlying), spot=spot, as_of=as_of, quotes=q, slices=slices,
        exercise=style, notes=notes, errors={str(k): v for k, v in errors.items()},
        implied_spot=isp.level if am else float("nan"), iv_spot=S, iv_spot_source=isp.source if am else "vendor",
        params={"max_rel_spread": max_rel_spread, "n_parity_strikes": n_parity_strikes,
                "max_rate_se": max_rate_se,
                "rate_source": "treasury" if rate_curve is not None else "parity", "exercise": style},
    )


#: Why a quote has, or has not, an IV (``quotes['iv_status']``): 'ok' (IV from the mid); the quote
#: filters 'no_quote', 'zero_bid', 'crossed', 'wide'; 'arb_bound' (crosses a no-arbitrage bound --
#: model-free on American chains, Black-76's bounds on European ones); American only: 'at_intrinsic'
#: (mid within its own half-spread of exercise value: no time value), 'below_model_forward' (mid
#: under the model forward's D (F - K)+); 'no_vol' (no volatility in the search interval fits).
IV_STATUSES = ("ok", "no_quote", "zero_bid", "crossed", "wide", "arb_bound", "at_intrinsic",
               "below_model_forward", "no_vol")


def iv_status(q: pd.DataFrame, at_intrinsic: Any, below_model_forward: Any) -> np.ndarray:
    """One status per quote, the first that applies in IV_STATUSES' order after 'ok'; a quote has
    status 'ok' exactly when its IV is finite."""
    iv_ok = np.isfinite(q["iv"].to_numpy(float))
    conds = [q["no_quote"].to_numpy(), q["zero_bid"].to_numpy(), q["crossed"].to_numpy(), q["wide"].to_numpy(),
             q["arb_bound"].to_numpy(), np.asarray(at_intrinsic), np.asarray(below_model_forward)]
    out = np.where(iv_ok, "ok", "no_vol").astype(object)
    for name, c in zip(reversed(IV_STATUSES[1:8]), reversed(conds), strict=True):
        out = np.where(c & ~iv_ok, name, out)
    return out


def _premium_free_parity_rmse(g: pd.DataFrame, f: parity.ParityFit, spot: float) -> float:
    """RMSE (USD) over the slice's parity strikes of ``C - P`` with each leg's early-exercise
    premium removed -- ``BS_C(iv_C) - BS_P(iv_P)`` -- against ``D (F - K)``; zero exactly when the
    call and the put at a strike share one American IV. Strikes without both IVs are skipped."""
    q = g[g["strike"].isin(f.strikes) & g["valid"] & np.isfinite(g["iv"])]
    iv = q.groupby(["strike", "type"])["iv"].mean().unstack()
    if not {"C", "P"} <= set(iv.columns):
        return float("nan")
    iv = iv.dropna()
    if iv.empty:
        return float("nan")
    K = iv.index.to_numpy(float)
    c = bsm.bsm_price(spot, K, f.T, iv["C"].to_numpy(), f.rate, f.div_yield, "C")
    p = bsm.bsm_price(spot, K, f.T, iv["P"].to_numpy(), f.rate, f.div_yield, "P")
    res = c - p - f.discount * (f.forward - K)
    return float(np.sqrt(np.mean(res * res)))
