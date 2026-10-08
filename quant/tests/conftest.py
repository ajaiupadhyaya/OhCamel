"""Every test runs OFFLINE against committed real fixtures, unless marked ``live``."""

from __future__ import annotations

import os

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
