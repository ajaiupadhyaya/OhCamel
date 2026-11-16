"""Every test runs OFFLINE against committed real fixtures, unless marked ``live``."""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import numpy as np
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


@pytest.fixture(scope="session")
def spy_2026_11_15():
    """The real SPY chain of the 2026-11-15 review (tests/data, provenance in its sidecar), read
    the way the options router reads it: Cboe payload -> analyze_chain with the Treasury curve
    FRED served that day (router's _treasury_rate_curve: r = 2 ln(1 + y/200), linear in tenor)
    -> build_surface. Returns (ChainAnalytics, VolSurface, {slice: (k, iv, T, weights)})."""
    from ohcamel_quant.data.cboe import parse_chain
    from ohcamel_quant.options.chain import analyze_chain
    from ohcamel_quant.options.surface import build_surface, svi_weights

    meta = json.loads(SPY_CHAIN_2026_11_15.with_name(SPY_CHAIN_2026_11_15.name + ".meta.json").read_text())
    curve = meta["treasury_curve"]
    tenors = np.asarray(curve["tenor_years"], float)
    rates = 2.0 * np.log1p(np.asarray(curve["yield_pct"], float) / 200.0)
    chain = parse_chain(json.loads(gzip.decompress(SPY_CHAIN_2026_11_15.read_bytes())), "SPY")
    ca = analyze_chain(chain, rate_curve=lambda T: float(np.interp(T, tenors, rates)))
    vs = build_surface(ca)
    inputs = {}
    for sid in vs.fits:
        sm = ca.slice_quotes(sid)
        sm = sm[sm["use_smile"]]
        T = float(ca.slices.loc[sid, "T"])
        inputs[sid] = (sm["k"].to_numpy(), sm["iv"].to_numpy(), T,
                       svi_weights(T, sm["iv_bid"].to_numpy(), sm["iv_ask"].to_numpy()))
    return ca, vs, inputs


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
