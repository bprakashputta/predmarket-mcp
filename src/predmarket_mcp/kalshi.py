"""Kalshi adapter (public market-data endpoints, no API key needed)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from .http import HttpClient, TTLCache
from .models import Level, Market, OrderBook, PricePoint, Trade, Venue
from .text import InvertedIndex

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
CATALOG_TTL = 600  # seconds; open events change slowly
QUOTE_TTL = 5
MAX_CATALOG_PAGES = 50  # 200 events per page


def _f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_combo(m: dict[str, Any]) -> bool:
    # multivariate "parlay" markets clutter every listing and aren't comparable across venues
    return bool(m.get("mve_collection_ticker")) or m.get("ticker", "").startswith("KXMVE")


def to_market(m: dict[str, Any], event: dict[str, Any] | None = None) -> Market:
    bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    series = (event or {}).get("series_ticker")
    return Market(
        venue=Venue.KALSHI,
        id=m["ticker"],
        title=m.get("title") or m["ticker"],
        event_title=(event or {}).get("title"),
        category=(event or {}).get("category"),
        yes_bid=bid if bid else None,
        yes_ask=ask if ask else None,
        last_price=_f(m.get("last_price_dollars")),
        volume=_f(m.get("volume_fp")),
        volume_unit="contracts",
        close_time=_ts(m.get("close_time")),
        url=f"https://kalshi.com/markets/{series.lower()}" if series else None,
    )


class KalshiClient:
    def __init__(self, http: HttpClient | None = None, cache: TTLCache | None = None) -> None:
        self.http = http or HttpClient(BASE_URL, max_rps=8)
        self.cache = cache or TTLCache()

    async def _catalog(self) -> InvertedIndex[dict[str, Any]]:
        """Index of open events by title, subtitle and category (Kalshi has no text search)."""

        async def load() -> InvertedIndex[dict[str, Any]]:
            index: InvertedIndex[dict[str, Any]] = InvertedIndex()
            cursor = None
            for _ in range(MAX_CATALOG_PAGES):
                page = await self.http.get_json(
                    "/events", {"status": "open", "limit": 200, "cursor": cursor}
                )
                for ev in page.get("events", []):
                    if ev.get("event_ticker", "").startswith("KXMVE"):
                        continue
                    text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title")]))
                    index.add(f"{text} {ev.get('category') or ''}", ev)
                cursor = page.get("cursor")
                if not cursor:
                    break
            return index

        return await self.cache.get_or_set("kalshi:catalog", CATALOG_TTL, load)

    async def warm(self) -> None:
        """Build the search index ahead of the first query."""
        await self._catalog()

    async def event_markets(self, event: dict[str, Any]) -> list[Market]:
        ticker = event["event_ticker"]

        async def load() -> list[Market]:
            data = await self.http.get_json(
                "/markets", {"event_ticker": ticker, "status": "open", "limit": 200}
            )
            return [to_market(m, event) for m in data.get("markets", []) if not _is_combo(m)]

        return await self.cache.get_or_set(f"kalshi:event:{ticker}", QUOTE_TTL * 6, load)

    async def search(self, query: str, limit: int = 10) -> list[Market]:
        index = await self._catalog()
        events = [ev for _, ev in index.search(query, limit=limit, min_score=0.3)]
        per_event = await asyncio.gather(*(self.event_markets(ev) for ev in events))
        out: list[Market] = []
        for markets in per_event:
            # within an event, show the most traded outcomes first
            out.extend(sorted(markets, key=lambda m: m.volume or 0, reverse=True)[:5])
        return out[:limit]

    async def get_market(self, ticker: str) -> Market:
        async def load() -> Market:
            m = (await self.http.get_json(f"/markets/{ticker}"))["market"]
            event = None
            with contextlib.suppress(httpx.HTTPError, KeyError):
                event = (await self.http.get_json(f"/events/{m['event_ticker']}"))["event"]
            return to_market(m, event)

        return await self.cache.get_or_set(f"kalshi:market:{ticker}", QUOTE_TTL, load)

    async def orderbook(self, ticker: str, depth: int = 10) -> OrderBook:
        data = await self.http.get_json(f"/markets/{ticker}/orderbook", {"depth": depth})
        book = data.get("orderbook_fp") or {}
        yes = [Level(price=float(p), size=float(s)) for p, s in book.get("yes_dollars") or []]
        # a NO bid at p is a YES ask at 1 - p
        no = [
            Level(price=round(1 - float(p), 4), size=float(s))
            for p, s in book.get("no_dollars") or []
        ]
        return OrderBook(
            venue=Venue.KALSHI,
            market_id=ticker,
            bids=sorted(yes, key=lambda lv: lv.price, reverse=True)[:depth],
            asks=sorted(no, key=lambda lv: lv.price)[:depth],
        )

    async def trades(self, ticker: str, limit: int = 20) -> list[Trade]:
        data = await self.http.get_json("/markets/trades", {"ticker": ticker, "limit": limit})
        return [
            Trade(
                venue=Venue.KALSHI,
                market_id=ticker,
                time=_ts(t["created_time"]),
                yes_price=float(t["yes_price_dollars"]),
                size=float(t["count_fp"]),
                taker_side=t.get("taker_side"),
            )
            for t in data.get("trades", [])
        ]

    async def price_history(self, ticker: str, hours: int = 72) -> list[PricePoint]:
        m = (await self.http.get_json(f"/markets/{ticker}"))["market"]
        series = (await self.http.get_json(f"/events/{m['event_ticker']}"))["event"][
            "series_ticker"
        ]
        end = int(time.time())
        interval = 60 if hours <= 24 * 7 else 1440  # minutes per candle
        data = await self.http.get_json(
            f"/series/{series}/markets/{ticker}/candlesticks",
            {"start_ts": end - hours * 3600, "end_ts": end, "period_interval": interval},
        )
        points = []
        for c in data.get("candlesticks", []):
            close = _f((c.get("price") or {}).get("close_dollars"))
            if close is not None:
                points.append(
                    PricePoint(time=datetime.fromtimestamp(c["end_period_ts"], UTC), price=close)
                )
        return points
