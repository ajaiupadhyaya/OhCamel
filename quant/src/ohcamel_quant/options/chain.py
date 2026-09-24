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
   (:func:`.parity.fit_all`), hence ``r`` and ``q`` -- no external rate/dividend;
5. Black-76 implied vol from the mid (and from bid / ask), NaN where the mid
   violates the no-arbitrage bounds (flag ``arb_bound``);
6. OTM selection for smile construction: puts for ``K < F``, calls for ``K >= F``
   (standard practice, e.g. Cboe VIX white paper; Ait-Sahalia & Lo 1998) because
   OTM quotes are the liquid ones and ITM quotes carry early-exercise premium
   for American options;
7. greeks (BSM with the slice's implied r, q; :mod:`.bsm`) and comparison with
   the vendor's IV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import bsm, parity

CONTRACT_MULTIPLIER = 100.0


@dataclass
class ChainAnalytics:
    underlying: str
    spot: float
    as_of: pd.Timestamp
    quotes: pd.DataFrame           # one row per contract with flags, iv, greeks
    slices: pd.DataFrame           # one row per expiry slice (index = slice id)
    european: bool
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

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
    max_rate_se: float = 0.005, rate_curve: Any = None,
) -> ChainAnalytics:
    """Clean an OptionChain (``underlying, spot, as_of, quotes``) and compute analytics.

    Parameters are quote-filter *choices*, not market inputs: ``max_rel_spread``
    (bid-ask spread as a fraction of mid above which a quote is flagged ``wide``),
    ``n_parity_strikes`` (near-the-money strikes in the parity regression),
    ``max_rate_se`` (rate standard error above which a slice borrows ``r``).
    ``rate_curve`` (``T -> r``, continuously compounded, from market data) fixes the
    discount factor per expiry instead of inferring it from parity -- used for
    American-style underlyings (see :mod:`.parity`).
    """
    notes: list[str] = []
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
    fits, errors = parity.fit_all(groups, spot, n_parity_strikes, max_rate_se, rate_curve=rate_curve)
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
    intrinsic = D * np.maximum(bsm.option_sign(typ) * (F - K), 0.0)
    mid = q["mid"].to_numpy(float)
    q["arb_bound"] = q["valid"] & ((mid <= intrinsic) | (mid >= np.where(typ == "C", D * F, D * K)))
    good_mid = q["valid"].to_numpy()
    q["iv"] = np.where(good_mid, bsm.implied_vol_black(np.where(good_mid, mid, np.nan), F, K, T, D, typ), np.nan)
    bid_ok = (bid > 0).to_numpy()
    q["iv_bid"] = np.where(bid_ok, bsm.implied_vol_black(np.where(bid_ok, bid, np.nan), F, K, T, D, typ), np.nan)
    ask_ok = (ask > 0).to_numpy()
    q["iv_ask"] = np.where(ask_ok, bsm.implied_vol_black(np.where(ask_ok, ask, np.nan), F, K, T, D, typ), np.nan)
    q["use_smile"] = q["valid"] & q["otm"] & np.isfinite(q["iv"])
    g = bsm.bsm_greeks(spot, K, T, q["iv"].to_numpy(float), q["r"].to_numpy(float), q["q"].to_numpy(float), typ)
    for name in ("delta", "gamma", "vega", "theta_day", "rho", "vanna", "volga", "charm_day"):
        q[name] = g[name]
    q["model_price"] = g["price"]
    q["iv_minus_vendor"] = q["iv"] - q["vendor_iv"]

    rows = []
    for sid, g_ in q.groupby("slice", sort=False):
        f = fmap[sid]
        ivd = g_.loc[g_["use_smile"], "iv_minus_vendor"].abs()
        rows.append({
            "slice": sid, "expiry": g_["expiry"].iloc[0], "expiry_ts": g_["expiry_ts"].iloc[0],
            "settlement": g_["settlement"].iloc[0], "roots": ",".join(sorted(g_["root"].unique())),
            "T": f.T, "dte": f.T * 365.0, "forward": f.forward, "discount": f.discount,
            "rate": f.rate, "div_yield": f.div_yield, "rate_source": f.rate_source, "rate_se": f.rate_se,
            "forward_se": f.forward_se, "parity_rmse": f.residual_rmse, "parity_strikes": f.n_strikes,
            "n_contracts": len(g_), "n_valid": int(g_["valid"].sum()), "n_smile": int(g_["use_smile"].sum()),
            "n_arb_bound": int(g_["arb_bound"].sum()),
            "vendor_iv_mad": float(ivd.median()) if ivd.notna().any() else float("nan"),
        })
    slices = pd.DataFrame(rows).set_index("slice").sort_values("T")
    q = q.sort_values(["T", "slice", "type", "strike"]).reset_index(drop=True)

    european = str(chain.underlying).upper().lstrip("_^") in parity.EUROPEAN_UNDERLYINGS
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
    if not european:
        notes.append("American-style options: European BSM/Black-76 applied to OTM quotes; the implied q "
                     "absorbs any early-exercise premium near the money")
    notes.append("Cboe delayed quotes (15 minutes); T is ACT/365 from the quote time to 16:00 ET "
                 "(09:30 ET for AM-settled index roots)")
    return ChainAnalytics(
        underlying=str(chain.underlying), spot=spot, as_of=as_of, quotes=q, slices=slices,
        european=european, notes=notes, errors={str(k): v for k, v in errors.items()},
        params={"max_rel_spread": max_rel_spread, "n_parity_strikes": n_parity_strikes,
                "max_rate_se": max_rate_se,
                "rate_source": "treasury" if rate_curve is not None else "parity"},
    )
