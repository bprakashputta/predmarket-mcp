# predmarket-mcp

[![CI](https://github.com/bprakashputta/predmarket-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bprakashputta/predmarket-mcp/actions/workflows/ci.yml)

**An MCP server that gives AI agents live prediction-market data from Kalshi and Polymarket.**

Ask Claude (or any MCP client) things like *"What are the odds of a US recession in 2027 on Kalshi vs Polymarket?"* or *"Show me the order book and last day of trading for the December Fed cut market"*. The agent calls these tools, gets clean venue-neutral data and answers with real numbers.

- **6 read-only tools:** search, quote, order book, recent trades, price history and cross-venue comparison
- **One data model for both venues:** every price is a YES probability from 0 to 1, Kalshi NO bids are converted to YES asks, and Polymarket NO-token fills are converted to YES prices
- **Built for agent workloads:** pooled HTTP clients, per-venue rate limiting, retries that honor `Retry-After`, a TTL cache that collapses duplicate concurrent requests, and an inverted-index search
- **No API keys:** uses only the venues' public market-data endpoints

```
 MCP client (Claude Desktop, Claude Code, LangGraph agent, ...)
        │  stdio (JSON-RPC)
        ▼
 ┌──────────────────────────── predmarket-mcp ────────────────────────────┐
 │  server.py     6 tools, input clamping, venue interleaving             │
 │  matching.py   cross-venue pairing (title similarity + close date)     │
 │  text.py       tokenizer + inverted index (Kalshi has no text search)  │
 │  kalshi.py ─┐                                   ┌─ polymarket.py       │
 │             └── http.py: rate limit · retry · TTL cache · dedupe ──────┘│
 └───────────────┬────────────────────────────────────────┬───────────────┘
                 ▼                                        ▼
     Kalshi Trade API v2 (public)          Polymarket Gamma · CLOB · Data API
```

## Tools

| Tool | What it returns |
|---|---|
| `search_markets(query, venue="both", limit=10)` | Open markets matching keywords, with bid/ask/mid, volume and close time. Results alternate between venues |
| `get_market(venue, market_id)` | Current quote and details for one market (Kalshi ticker or Polymarket slug) |
| `get_orderbook(venue, market_id, depth=10)` | YES bids and asks, best first, plus the spread |
| `get_recent_trades(venue, market_id, limit=20)` | Latest fills, newest first, as YES prices |
| `get_price_history(venue, market_id, hours=72)` | Hourly (≤7 days) or daily points, with first/last/min/max summary |
| `compare_venues(query, min_similarity=0.35, limit=10)` | Markets that appear to ask the same question on both venues, ranked by price gap |

Example, `compare_venues("recession")` (live output, trimmed, Nov 2026):

```json
[{
  "similarity": 0.5,
  "price_gap": 0.06,
  "kalshi":     {"id": "KXRECSSNBER-27", "title": "Will there be a recession in 2027?", "mid": 0.215},
  "polymarket": {"id": "us-recession-by-end-of-2027", "title": "US recession by end of 2027?", "mid": 0.275}
}]
```

> A price gap is a starting point, not an arbitrage. This pair shows why: the Kalshi ticker resolves on an NBER recession call, and Polymarket's market has its own rules and cut-off. Venues often define the "same" event differently, so the server tells the agent to check resolution rules before treating a gap as an opportunity.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/bprakashputta/predmarket-mcp
cd predmarket-mcp
uv sync
uv run predmarket-mcp        # speaks MCP over stdio
```

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings → Developer → Edit Config), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "predmarket": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/predmarket-mcp", "run", "predmarket-mcp"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add predmarket -- uv --directory /absolute/path/to/predmarket-mcp run predmarket-mcp
```

### From your own code

[`examples/client.py`](examples/client.py) starts the server over stdio, lists the tools and calls two of them, which is what an agent framework does under the hood:

```bash
uv run python examples/client.py "fed rate"
```

## Design notes

- **Kalshi search.** Kalshi's API has no keyword search, so the server pages through all open events (about 30 pages of 200), builds an inverted index of titles, and refreshes it every 10 minutes. Indexing starts in the background when the server boots, so searches are usually instant. A query only touches events that share a token with it, never the whole catalog.
- **Rate limits.** Each venue client spaces its requests evenly (8 req/s for Kalshi) instead of bursting and then backing off. On 429 or 5xx it retries with exponential backoff and honors `Retry-After`.
- **Caching.** Quotes are cached for 5 seconds and searches for 30 seconds. If an agent fires the same request several times in parallel, only one upstream call is made.
- **Cross-venue matching.** Titles are tokenized with stop words removed, and common long forms are folded into one token ("United States" → `us`, "Federal Reserve" → `fed`, "New York City" → `nyc`). Candidates come from an inverted index, are paired one-to-one by Jaccard similarity, and pairs whose close dates are more than 45 days apart are rejected.
- **Units.** Prices are probabilities. Volume stays in each venue's own unit (`contracts` on Kalshi, `usd` on Polymarket) and is labeled, never mixed.

## Development

```bash
uv sync
uv run pytest          # no network: HTTP is mocked with respx
uv run ruff check .
```

## Limitations

- Read-only market data. It doesn't place orders and never touches an account.
- Matching is a title heuristic. It finds candidates; a human or the agent must confirm the contracts are equivalent.
- Kalshi multivariate "combo" markets are excluded because they aren't comparable across venues.
- Public endpoints change. The venue adapters are small and isolated so they're easy to update.

Not financial advice. Not affiliated with Kalshi or Polymarket.

## License

MIT
