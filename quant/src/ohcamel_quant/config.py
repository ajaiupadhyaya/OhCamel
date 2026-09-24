"""Runtime settings, read once from the environment (prefix ``OHCAMEL_QUANT_``).

Nothing here is a market parameter. Market inputs (rates, prices, factors,
option quotes, filings) always come from a data source; settings only say
*where* to fetch, *where* to cache, and *how long* a cached copy stays fresh.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OHCAMEL_QUANT_", env_file=None, extra="ignore")

    # Where fetched datasets are cached (Parquet + JSON provenance sidecars).
    data_dir: Path = Field(default=REPO_ROOT / "quant" / ".data")
    # Offline mode: never touch the network; serve only committed real fixtures
    # and whatever is already cached. Used by tests and CI.
    offline: bool = False
    # SEC EDGAR requires a descriptive User-Agent with contact information.
    user_agent: str = "OhCamel-Quant/1.0 (research; contact: ohcamel@ajaiupadhyaya.com)"
    http_timeout_s: float = 20.0
    http_retries: int = 3
    # Alpaca market data (optional; used first for equity bars when present).
    # The image entrypoint maps the droplet's ALPACA_* names in live.env onto APCA_*.
    alpaca_key_id: str | None = Field(default=None, validation_alias="APCA_API_KEY_ID")
    alpaca_secret_key: str | None = Field(default=None, validation_alias="APCA_API_SECRET_KEY")
    # FRED API key is optional: the public fredgraph.csv endpoint needs none.
    fred_api_key: str | None = Field(default=None, validation_alias="FRED_API_KEY")
    # Cache freshness, seconds, per dataset family.
    ttl_prices_s: int = 6 * 3600
    ttl_intraday_quote_s: int = 60
    ttl_macro_s: int = 12 * 3600
    ttl_factors_s: int = 7 * 24 * 3600
    ttl_options_s: int = 15 * 60
    ttl_filings_s: int = 24 * 3600
    # Price provider order; the first that returns data wins.
    price_providers: list[str] = Field(default_factory=lambda: ["alpaca", "yahoo", "stooq"])
    # Optional URL of the OCaml real-time engine (read-only JSON), for the Live page.
    engine_url: str | None = None
    # Directory of committed real-data fixtures (used offline and in tests).
    fixtures_dir: Path = Field(default=REPO_ROOT / "fixtures")

    @field_validator("alpaca_key_id", "alpaca_secret_key", "fred_api_key", "engine_url", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        # An env line like `FRED_API_KEY=` must mean "not configured", not "".
        return None if isinstance(v, str) and not v.strip() else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
