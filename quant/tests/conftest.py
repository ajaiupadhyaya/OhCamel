"""Every test runs OFFLINE against committed real fixtures, unless marked ``live``."""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

if not os.environ.get("OHCAMEL_QUANT_LIVE_TESTS"):
    os.environ["OHCAMEL_QUANT_OFFLINE"] = "1"
# The shared session `client` posts far more than any visitor; tests/test_api_ratelimit.py tests the limit.
os.environ.setdefault("OHCAMEL_QUANT_RATE_LIMIT_PER_MIN", "0")

from ohcamel_quant.config import get_settings  # noqa: E402
from ohcamel_quant.data.market import MarketData, get_market  # noqa: E402

get_settings.cache_clear()
get_market.cache_clear()


@pytest.fixture(scope="session")
def market() -> MarketData:
    return get_market()


@pytest.fixture(scope="session")
def etf_returns(market):
    """Real daily returns of the nine committed ETFs, 2016-06..2026-06."""
    return market.returns(["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "XLE", "XLF", "XLK"]).data


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from ohcamel_quant.api.app import create_app

    return TestClient(create_app())


SPY_CHAIN_2026_11_15 = Path(__file__).parent / "data" / "cboe_spy_2026-11-15.json.gz"


#: How the 2026-11-15 review stamped the payload: its generation time (22:22:02 UTC on Sunday) read as New York
#: time. Task 3.4 stamps a chain with its quotes' own time (Friday 2026-11-13 16:00 New York, data/cboe.py).
REVIEW_STAMP = pd.Timestamp("2026-11-15 22:22:02")


def _spy_2026_11_15_chain(as_the_review_read_it: bool = False):
    """(OptionChain, rate_curve): the committed Cboe payload and the Treasury curve FRED served that
    day (dated 2026-11-12, in the sidecar).

    As the router reads it now (task 3.4): stamped with the quotes' time and discounted with the zero curve
    bootstrapped from that curve (macro.curve.zero_curve_on). ``as_the_review_read_it``: stamped
    ``REVIEW_STAMP`` and discounted at the par yields (r = 2 ln(1 + y/200), linear in tenor), the reading
    whose smiles the review fitted."""
    from ohcamel_quant.data.cboe import parse_chain
    from ohcamel_quant.macro.curve import zero_curve_on

    meta = json.loads(SPY_CHAIN_2026_11_15.with_name(SPY_CHAIN_2026_11_15.name + ".meta.json").read_text())
    curve = meta["treasury_curve"]
    tenors = np.asarray(curve["tenor_years"], float)
    chain = parse_chain(json.loads(gzip.decompress(SPY_CHAIN_2026_11_15.read_bytes())), "SPY")
    if as_the_review_read_it:
        chain.as_of = REVIEW_STAMP
        rates = 2.0 * np.log1p(np.asarray(curve["yield_pct"], float) / 200.0)
        return chain, (lambda T: float(np.interp(T, tenors, rates)))
    frame = pd.DataFrame([curve["yield_pct"]], index=pd.DatetimeIndex([curve["date"]]), columns=list(tenors))
    return chain, zero_curve_on(frame, chain.as_of).rate


@pytest.fixture(scope="session")
def spy_2026_11_15():
    """The real SPY chain of the 2026-11-15 review (tests/data, provenance in its sidecar), read
    the way the options router read it for that review: Cboe payload -> analyze_chain with the
    Treasury curve and Black-76 inversion (exercise='european', the smiles the review fitted) ->
    build_surface. Returns (ChainAnalytics, VolSurface, {slice: (k, iv, T, weights)}). It keeps the review's
    stamp and par-yield discounting (``_spy_2026_11_15_chain(as_the_review_read_it=True)``): these are the smiles
    whose fits and densities the review examined, and the fitter's tests hold to them.
    spy_2026_11_15_american is the chain as the router reads it now (American exercise, the quotes' own time,
    the zero curve)."""
    from ohcamel_quant.options.chain import analyze_chain
    from ohcamel_quant.options.surface import build_surface, svi_weights

    chain, rate_curve = _spy_2026_11_15_chain(as_the_review_read_it=True)
    ca = analyze_chain(chain, rate_curve=rate_curve, exercise="european")
    vs = build_surface(ca)
    inputs = {}
    for sid in vs.fits:
        sm = ca.slice_quotes(sid)
        sm = sm[sm["use_smile"]]
        T = float(ca.slices.loc[sid, "T"])
        inputs[sid] = (sm["k"].to_numpy(), sm["iv"].to_numpy(), T,
                       svi_weights(T, sm["iv_bid"].to_numpy(), sm["iv_ask"].to_numpy()))
    return ca, vs, inputs


#: The expiries spy_2026_11_15_american keeps on the NumPy reference (see there).
SPY_REFERENCE_EXPIRIES = ("2026-11-16", "2026-11-18", "2026-12-18", "2027-06-17", "2028-12-15")


@pytest.fixture(scope="session")
def spy_2026_11_15_american():
    """The same chain as the router reads it now: SPY options are American, so every IV inverts the
    CRR tree (options.american) and each forward comes from the pair nearest the money; T runs from the
    quotes' own time (Friday 2026-11-13 16:00 New York, task 3.4) and discounting is the zero curve
    bootstrapped from the 2026-11-12 par curve.

    On the Rust kernel that is the whole chain (~36,000 inversions: 2.3 s at one thread, 0.85 s at
    four, macOS arm64). The NumPy reference takes 14 s for the chain, so on it -- the default offline
    suite, where `uv sync` drops the wheel -- the chain is cut to the five expiries in
    SPY_REFERENCE_EXPIRIES (3, 5, 35, 216 and 763 days from the quotes; every strike; 2.3 s). CI's native job
    runs the suite on Rust, over the whole chain. Returns (ChainAnalytics, its European twin over the same
    quotes, OptionChain analyzed, rate_curve)."""
    from ohcamel_quant import kernels
    from ohcamel_quant.options.chain import analyze_chain

    chain, rate_curve = _spy_2026_11_15_chain()
    if kernels.engine_of("american_iv") != "rust":
        keep = chain.quotes["expiry"].dt.strftime("%Y-%m-%d").isin(SPY_REFERENCE_EXPIRIES)
        chain.quotes = chain.quotes[keep].reset_index(drop=True)
    ca = analyze_chain(chain, rate_curve=rate_curve)
    ce = analyze_chain(chain, rate_curve=rate_curve, exercise="european")
    return ca, ce, chain, rate_curve


@pytest.fixture(scope="session", autouse=True)
def _jobs_queue_in_a_temp_dir(tmp_path_factory):
    """In tests the API's default jobs.sqlite ({data_dir}/jobs.sqlite) is a
    session temp file. The shared `client` fixture has no dependency
    override, and an over-cap request through it queues a job (Lane B, B5).
    Tests that need their own queue still override get_jobs_db_path on their app."""
    from ohcamel_quant.api.routers import jobs as jobs_router

    path = tmp_path_factory.mktemp("jobs") / "jobs.sqlite"
    mp = pytest.MonkeyPatch()
    mp.setattr(jobs_router, "jobs_db_path", lambda: path)  # get_jobs_db_path() looks the name up per call
    yield path
    mp.undo()
