"""Polymarket adapter: Gamma API for discovery, CLOB for books and history, Data API for trades.
All public, no API key needed."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from .http import HttpClient, TTLCache
from .models import Level, Market, OrderBook, PricePoint, Trade, Venue

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"
QUOTE_TTL = 5


def _f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list(value: Any) -> list[Any]:
    """Gamma returns some arrays as JSON-encoded strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value or []


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def yes_token(m: dict[str, Any]) -> str | None:
    tokens, outcomes = _list(m.get("clobTokenIds")), _list(m.get("outcomes"))
    if not tokens:
        return None
    for token, outcome in zip(tokens, outcomes, strict=False):
        if str(outcome).lower() == "yes":
            return token
    return tokens[0]


def to_market(m: dict[str, Any], event: dict[str, Any] | None = None) -> Market:
    prices = [_f(p) for p in _list(m.get("outcomePrices"))]
    title = m.get("question") or m.get("groupItemTitle") or m["slug"]
    event_slug = (event or {}).get("slug") or (_list(m.get("events")) or [{}])[0].get("slug")
    return Market(
        venue=Venue.POLYMARKET,
        id=m["slug"],
        title=title,
        event_title=(event or {}).get("title"),
        category=(event or {}).get("category") or m.get("category"),
        yes_bid=_f(m.get("bestBid")),
        yes_ask=_f(m.get("bestAsk")),
        last_price=_f(m.get("lastTradePrice")) or (prices[0] if prices else None),
        volume=_f(m.get("volumeNum") or m.get("volume")),
        volume_unit="usd",
        close_time=_ts(m.get("endDate")),
        url=f"https://polymarket.com/event/{event_slug}" if event_slug else None,
    )


def _is_live(m: dict[str, Any]) -> bool:
    return bool(m.get("active")) and not m.get("closed") and not m.get("archived")


class PolymarketClient:
    def __init__(
        self,
        gamma: HttpClient | None = None,
        clob: HttpClient | None = None,
        data: HttpClient | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self.gamma = gamma or HttpClient(GAMMA_URL)
        self.clob = clob or HttpClient(CLOB_URL)
        self.data = data or HttpClient(DATA_URL)
        self.cache = cache or TTLCache()

    async def search(self, query: str, limit: int = 10) -> list[Market]:
        async def load() -> list[Market]:
            data = await self.gamma.get_json(
                "/public-search",
                {"q": query, "limit_per_type": 10, "events_status": "active"},
            )
            out: list[Market] = []
            for ev in data.get("events") or []:
                live = [m for m in ev.get("markets") or [] if _is_live(m)]
                live.sort(
                    key=lambda m: _f(m.get("volumeNum") or m.get("volume")) or 0, reverse=True
                )
                out.extend(to_market(m, ev) for m in live[:5])
            return out

        markets = await self.cache.get_or_set(f"pm:search:{query.lower()}", 30, load)
        return markets[:limit]

    async def _raw_market(self, slug: str) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            rows = await self.gamma.get_json("/markets", {"slug": slug})
            if not rows:
                raise LookupError(f"Polymarket market not found: {slug}")
            return rows[0]

        return await self.cache.get_or_set(f"pm:raw:{slug}", QUOTE_TTL, load)

    async def get_market(self, slug: str) -> Market:
        raw = await self._raw_market(slug)
        events = _list(raw.get("events"))
        return to_market(raw, events[0] if events else None)

    async def orderbook(self, slug: str, depth: int = 10) -> OrderBook:
        token = yes_token(await self._raw_market(slug))
        if not token:
            raise LookupError(f"No order book for {slug}")
        book = await self.clob.get_json("/book", {"token_id": token})

        def levels(rows: list[dict[str, str]], best_high: bool) -> list[Level]:
            lv = [Level(price=float(r["price"]), size=float(r["size"])) for r in rows or []]
            return sorted(lv, key=lambda x: x.price, reverse=best_high)[:depth]

        return OrderBook(
            venue=Venue.POLYMARKET,
            market_id=slug,
            bids=levels(book.get("bids"), best_high=True),
            asks=levels(book.get("asks"), best_high=False),
        )

    async def trades(self, slug: str, limit: int = 20) -> list[Trade]:
        raw = await self._raw_market(slug)
        token = yes_token(raw)
        data = await self.data.get_json(
            "/v2/trades", {"condition": raw["conditionId"], "limit": limit}
        )
        rows = data.get("data", data) if isinstance(data, dict) else data
        out = []
        for t in rows or []:
            price = float(t["price"])
            # express every fill as a YES price so both venues read the same way
            if token and t.get("token_id") and t["token_id"] != token:
                price = round(1 - price, 4)
            out.append(
                Trade(
                    venue=Venue.POLYMARKET,
                    market_id=slug,
                    time=datetime.fromtimestamp(int(t["timestamp"]), UTC),
                    yes_price=price,
                    size=float(t["size"]),
                    taker_side=(t.get("side") or "").lower() or None,
                )
            )
        return out

    async def price_history(self, slug: str, hours: int = 72) -> list[PricePoint]:
        token = yes_token(await self._raw_market(slug))
        end = int(time.time())
        fidelity = 60 if hours <= 24 * 7 else 1440  # minutes per point
        data = await self.clob.get_json(
            "/prices-history",
            {"market": token, "startTs": end - hours * 3600, "endTs": end, "fidelity": fidelity},
        )
        return [
            PricePoint(time=datetime.fromtimestamp(p["t"], UTC), price=float(p["p"]))
            for p in data.get("history", [])
        ]
