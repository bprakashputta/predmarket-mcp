from datetime import UTC, datetime

from predmarket_mcp import server
from predmarket_mcp.models import Market, Venue

EXPECTED_TOOLS = {
    "search_markets",
    "get_market",
    "get_orderbook",
    "get_recent_trades",
    "get_price_history",
    "compare_venues",
}


def mk(venue: Venue, n: int) -> Market:
    return Market(
        venue=venue,
        id=f"{venue.value}-{n}",
        title=f"market {n}",
        volume_unit="usd",
        close_time=datetime(2027, 1, 1, tzinfo=UTC),
    )


async def test_tools_are_registered_and_read_only():
    tools = await server.mcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.annotations and t.annotations.read_only_hint is True
        assert t.description


async def test_search_interleaves_venues_and_reports_errors(monkeypatch):
    async def kalshi_search(query, limit):
        return [mk(Venue.KALSHI, i) for i in range(3)]

    async def pm_search(query, limit):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(server._kalshi, "search", kalshi_search)
    monkeypatch.setattr(server._polymarket, "search", pm_search)
    out = await server.search_markets("anything", limit=5)
    assert [r.get("id") for r in out[:3]] == ["kalshi-0", "kalshi-1", "kalshi-2"]
    assert out[-1] == {"venue": "polymarket", "error": "RuntimeError: upstream down"}

    async def pm_ok(query, limit):
        return [mk(Venue.POLYMARKET, i) for i in range(3)]

    monkeypatch.setattr(server._polymarket, "search", pm_ok)
    out = await server.search_markets("anything", limit=4)
    assert [r["venue"] for r in out] == ["kalshi", "polymarket", "kalshi", "polymarket"]
