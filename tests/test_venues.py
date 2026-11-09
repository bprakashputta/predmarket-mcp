import json

import httpx
import respx

from predmarket_mcp.http import HttpClient, TTLCache
from predmarket_mcp.kalshi import BASE_URL as KALSHI_URL
from predmarket_mcp.kalshi import KalshiClient
from predmarket_mcp.kalshi import to_market as kalshi_market
from predmarket_mcp.polymarket import CLOB_URL, DATA_URL, GAMMA_URL, PolymarketClient, yes_token
from predmarket_mcp.polymarket import to_market as pm_market

KALSHI_MARKET = {
    "ticker": "KXRECESSION-27",
    "event_ticker": "KXRECESSION-27",
    "title": "Will there be a recession in 2027?",
    "yes_bid_dollars": "0.3100",
    "yes_ask_dollars": "0.3300",
    "last_price_dollars": "0.3200",
    "volume_fp": "1520.00",
    "close_time": "2028-01-31T13:25:00Z",
}
KALSHI_EVENT = {
    "event_ticker": "KXRECESSION-27",
    "series_ticker": "KXRECESSION",
    "title": "Recession in 2027?",
    "category": "Economics",
}
PM_MARKET = {
    "slug": "us-recession-by-end-of-2027",
    "question": "US recession by end of 2027?",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.36", "0.64"]',
    "clobTokenIds": '["111", "222"]',
    "conditionId": "0xabc",
    "bestBid": 0.35,
    "bestAsk": 0.37,
    "lastTradePrice": 0.36,
    "volumeNum": 125000.5,
    "endDate": "2027-12-31T23:59:00Z",
    "active": True,
    "closed": False,
    "events": [{"slug": "us-recession-by-end-of-2027", "title": "US recession by end of 2027?"}],
}


def fast_http(url: str) -> HttpClient:
    return HttpClient(url, max_rps=1000)


def test_kalshi_market_parsing():
    m = kalshi_market(KALSHI_MARKET, KALSHI_EVENT)
    assert (m.yes_bid, m.yes_ask, m.mid) == (0.31, 0.33, 0.32)
    assert m.volume_unit == "contracts"
    assert m.url == "https://kalshi.com/markets/kxrecession"
    assert m.category == "Economics"


def test_kalshi_zero_quotes_are_treated_as_missing():
    m = kalshi_market({**KALSHI_MARKET, "yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0000"})
    assert m.yes_bid is None and m.yes_ask is None
    assert m.mid == 0.32  # falls back to last price


@respx.mock
async def test_kalshi_orderbook_converts_no_bids_to_yes_asks():
    respx.get(f"{KALSHI_URL}/markets/T/orderbook").respond(
        json={
            "orderbook_fp": {
                "yes_dollars": [["0.30", "10"], ["0.31", "5"]],
                "no_dollars": [["0.66", "7"], ["0.67", "2"]],
            }
        }
    )
    book = await KalshiClient(fast_http(KALSHI_URL)).orderbook("T", depth=5)
    assert [lv.price for lv in book.bids] == [0.31, 0.30]  # best bid first
    assert [lv.price for lv in book.asks] == [0.33, 0.34]  # 1 - 0.67, 1 - 0.66
    assert book.spread == 0.02


@respx.mock
async def test_kalshi_search_uses_catalog_and_skips_combo_markets():
    respx.get(f"{KALSHI_URL}/events").respond(json={"events": [KALSHI_EVENT], "cursor": ""})
    combo = {**KALSHI_MARKET, "ticker": "KXMVE-X", "mve_collection_ticker": "KXMVE"}
    respx.get(f"{KALSHI_URL}/markets").respond(json={"markets": [KALSHI_MARKET, combo]})
    results = await KalshiClient(fast_http(KALSHI_URL)).search("recession 2027")
    assert [m.id for m in results] == ["KXRECESSION-27"]


def test_polymarket_parsing_handles_json_string_fields():
    m = pm_market(PM_MARKET)
    assert (m.yes_bid, m.yes_ask, m.mid) == (0.35, 0.37, 0.36)
    assert m.volume == 125000.5 and m.volume_unit == "usd"
    assert m.url == "https://polymarket.com/event/us-recession-by-end-of-2027"
    assert yes_token(PM_MARKET) == "111"


@respx.mock
async def test_polymarket_orderbook_sorted_best_first():
    respx.get(f"{GAMMA_URL}/markets").respond(json=[PM_MARKET])
    respx.get(f"{CLOB_URL}/book").respond(
        json={
            "bids": [{"price": "0.30", "size": "1"}, {"price": "0.35", "size": "2"}],
            "asks": [{"price": "0.40", "size": "1"}, {"price": "0.37", "size": "3"}],
        }
    )
    client = PolymarketClient(gamma=fast_http(GAMMA_URL), clob=fast_http(CLOB_URL))
    book = await client.orderbook(PM_MARKET["slug"])
    assert book.bids[0].price == 0.35 and book.asks[0].price == 0.37
    assert book.spread == 0.02


@respx.mock
async def test_polymarket_trades_express_no_fills_as_yes_price():
    respx.get(f"{GAMMA_URL}/markets").respond(json=[PM_MARKET])
    route = respx.get(f"{DATA_URL}/v2/trades").respond(
        json={
            "data": [
                {
                    "token_id": "111",
                    "price": 0.36,
                    "size": 10,
                    "timestamp": 1794190000,
                    "side": "BUY",
                },
                {
                    "token_id": "222",
                    "price": 0.60,
                    "size": 4,
                    "timestamp": 1794190001,
                    "side": "SELL",
                },
            ]
        }
    )
    client = PolymarketClient(gamma=fast_http(GAMMA_URL), data=fast_http(DATA_URL))
    trades = await client.trades(PM_MARKET["slug"])
    assert [t.yes_price for t in trades] == [0.36, 0.4]
    assert route.calls.last.request.url.params["condition"] == "0xabc"


@respx.mock
async def test_http_retries_on_429_then_succeeds():
    route = respx.get("https://x.test/a").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    assert await fast_http("https://x.test").get_json("/a") == {"ok": True}
    assert route.call_count == 2


async def test_cache_collapses_concurrent_loads():
    import asyncio

    cache = TTLCache()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return json.dumps({"n": calls})

    results = await asyncio.gather(*(cache.get_or_set("k", 60, loader) for _ in range(5)))
    assert calls == 1 and len(set(results)) == 1
