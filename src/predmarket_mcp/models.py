"""Venue-neutral data models. Every price is a probability between 0 and 1."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Venue(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class Market(BaseModel):
    venue: Venue
    id: str = Field(description="Kalshi ticker or Polymarket market slug")
    title: str
    event_title: str | None = None
    category: str | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    last_price: float | None = None
    volume: float | None = None
    volume_unit: str = Field(description="'contracts' on Kalshi, 'usd' on Polymarket")
    close_time: datetime | None = None
    url: str | None = None

    @property
    def mid(self) -> float | None:
        if self.yes_bid is not None and self.yes_ask is not None and self.yes_ask > 0:
            return round((self.yes_bid + self.yes_ask) / 2, 4)
        return self.last_price


class Level(BaseModel):
    price: float
    size: float


class OrderBook(BaseModel):
    venue: Venue
    market_id: str
    bids: list[Level] = Field(description="YES bids, best (highest) first")
    asks: list[Level] = Field(description="YES asks, best (lowest) first")

    @property
    def spread(self) -> float | None:
        if self.bids and self.asks:
            return round(self.asks[0].price - self.bids[0].price, 4)
        return None


class Trade(BaseModel):
    venue: Venue
    market_id: str
    time: datetime
    yes_price: float
    size: float
    taker_side: str | None = None


class PricePoint(BaseModel):
    time: datetime
    price: float


class MarketMatch(BaseModel):
    kalshi: Market
    polymarket: Market
    similarity: float = Field(description="Title similarity, 0 to 1")
    price_gap: float | None = Field(
        default=None, description="Polymarket mid minus Kalshi mid, in probability points"
    )
