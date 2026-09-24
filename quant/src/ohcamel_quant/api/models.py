"""Request models shared across routers, so every page speaks one portfolio dialect."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator


class Holding(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    # Signed portfolio weight as a DECIMAL fraction of equity (0.25 = 25%).
    # Negative = short. Weights need not sum to 1 (cash = 1 - sum).
    weight: float

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class PortfolioIn(BaseModel):
    holdings: list[Holding] = Field(min_length=1, max_length=100)
    start: date | None = None          # analysis window; default = provider max history
    end: date | None = None
    benchmark: str = "SPY"             # for beta / active risk / attribution
    notional: float = Field(default=1_000_000.0, gt=0)  # USD, for $-denominated risk

    @property
    def tickers(self) -> list[str]:
        return [h.ticker for h in self.holdings]

    @property
    def weights(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for h in self.holdings:
            out[h.ticker] = out.get(h.ticker, 0.0) + h.weight
        return out


class UniverseIn(BaseModel):
    tickers: list[str] = Field(min_length=2, max_length=60)
    start: date | None = None
    end: date | None = None

    @field_validator("tickers")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(t.strip().upper() for t in v if t.strip()))
