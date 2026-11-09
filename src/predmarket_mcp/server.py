"""MCP server exposing Kalshi and Polymarket market data as tools for AI agents."""

from __future__ import annotations

import asyncio
import logging
from itertools import zip_longest
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .kalshi import KalshiClient
from .matching import match_markets
from .models import Market, Venue
from .polymarket import PolymarketClient

VenueName = Literal["kalshi", "polymarket"]

logging.getLogger("httpx").setLevel(logging.WARNING)

_kalshi = KalshiClient()
_polymarket = PolymarketClient()


mcp = MCPServer(
    "predmarket",
    version="0.1.0",
    instructions=(
        "Live prediction-market data from Kalshi and Polymarket. Prices are YES probabilities "
        "between 0 and 1. Start with search_markets to find market ids, then use the other tools "
        "with the venue and id. Before treating a cross-venue price gap as an opportunity, check "
        "that both markets resolve on the same rules and date."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def _client(venue: VenueName) -> KalshiClient | PolymarketClient:
    return _kalshi if Venue(venue) is Venue.KALSHI else _polymarket


def _market_dict(m: Market) -> dict[str, Any]:
    return {**m.model_dump(mode="json", exclude_none=True), "mid": m.mid}


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


@mcp.tool(annotations=READ_ONLY)
async def search_markets(
    query: str, venue: VenueName | Literal["both"] = "both", limit: int = 10
) -> list[dict[str, Any]]:
    """Find open markets by keywords, e.g. "fed rate cut december" or "bitcoin 150k".

    Returns market id, title, YES bid/ask/mid, volume and close time for each match."""
    limit = _clamp(limit, 1, 50)
    venues = [Venue.KALSHI, Venue.POLYMARKET] if venue == "both" else [Venue(venue)]
    results = await asyncio.gather(
        *(_client(v.value).search(query, limit) for v in venues), return_exceptions=True
    )
    errors: list[dict[str, Any]] = []
    lists: list[list[Market]] = []
    for v, res in zip(venues, results, strict=True):
        if isinstance(res, BaseException):
            errors.append({"venue": v.value, "error": f"{type(res).__name__}: {res}"})
        else:
            lists.append(res)
    # alternate venues so one side can't crowd out the other
    merged = [m for group in zip_longest(*lists) for m in group if m is not None]
    return [_market_dict(m) for m in merged[:limit]] + errors


@mcp.tool(annotations=READ_ONLY)
async def get_market(venue: VenueName, market_id: str) -> dict[str, Any]:
    """Current quote and details for one market (Kalshi ticker or Polymarket slug)."""
    return _market_dict(await _client(venue).get_market(market_id))


@mcp.tool(annotations=READ_ONLY)
async def get_orderbook(venue: VenueName, market_id: str, depth: int = 10) -> dict[str, Any]:
    """YES-side order book: bids best-first, asks best-first, plus the spread.

    Kalshi NO bids are converted to YES asks (a NO bid at p equals a YES ask at 1 - p)."""
    book = await _client(venue).orderbook(market_id, _clamp(depth, 1, 50))
    return {**book.model_dump(mode="json"), "spread": book.spread}


@mcp.tool(annotations=READ_ONLY)
async def get_recent_trades(
    venue: VenueName, market_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Most recent fills, newest first, each expressed as a YES price."""
    trades = await _client(venue).trades(market_id, _clamp(limit, 1, 200))
    return [t.model_dump(mode="json") for t in trades]


@mcp.tool(annotations=READ_ONLY)
async def get_price_history(venue: VenueName, market_id: str, hours: int = 72) -> dict[str, Any]:
    """YES price history: hourly points up to 7 days, daily points beyond that (max 90 days)."""
    points = await _client(venue).price_history(market_id, _clamp(hours, 1, 24 * 90))
    prices = [p.price for p in points]
    summary = (
        {"first": prices[0], "last": prices[-1], "min": min(prices), "max": max(prices)}
        if prices
        else {}
    )
    return {
        "venue": venue,
        "market_id": market_id,
        "summary": summary,
        "points": [p.model_dump(mode="json") for p in points],
    }


@mcp.tool(annotations=READ_ONLY)
async def compare_venues(
    query: str, min_similarity: float = 0.2, limit: int = 10
) -> list[dict[str, Any]]:
    """Search both venues and pair markets that appear to ask the same question.

    price_gap = Polymarket mid minus Kalshi mid. Pairs must share the question's key words and
    agree on thresholds, years, direction and negation, but they are still candidates: always
    confirm the resolution rules match before acting on a gap."""
    kalshi, polymarket = await asyncio.gather(
        _kalshi.search(query, limit=30), _polymarket.search(query, limit=30)
    )
    matches = match_markets(kalshi, polymarket, min_similarity=min_similarity)
    matches.sort(key=lambda m: abs(m.price_gap or 0), reverse=True)
    return [
        {
            "similarity": m.similarity,
            "price_gap": m.price_gap,
            "kalshi": _market_dict(m.kalshi),
            "polymarket": _market_dict(m.polymarket),
        }
        for m in matches[: _clamp(limit, 1, 50)]
    ]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
