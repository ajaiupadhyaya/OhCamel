"""Every test runs OFFLINE against committed real fixtures, unless marked ``live``."""

from __future__ import annotations

import os

import pytest

if not os.environ.get("OHCAMEL_QUANT_LIVE_TESTS"):
    os.environ["OHCAMEL_QUANT_OFFLINE"] = "1"

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
