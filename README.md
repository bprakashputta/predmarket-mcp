# predmarket-mcp

[![CI](https://github.com/bprakashputta/predmarket-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bprakashputta/predmarket-mcp/actions/workflows/ci.yml)

**An MCP server that gives AI agents live prediction-market data from Kalshi and Polymarket.**

Ask Claude (or any MCP client) things like *"What are the odds of a US recession in 2027 on Kalshi vs Polymarket?"* or *"Show me the order book and last day of trading for the December Fed cut market"*. The agent calls these tools, gets clean venue-neutral data and answers with real numbers.

- **6 read-only tools:** search, quote, order book, recent trades, price history and cross-venue comparison
- **One data model for both venues:** every price is a YES probability from 0 to 1, Kalshi NO bids are converted to YES asks, and Polymarket NO-token fills are converted to YES prices
- **Built for agent workloads:** pooled HTTP clients, per-venue rate limiting, retries that honor `Retry-After`, and a TTL cache that collapses duplicate concurrent requests
- **Cross-venue matching that avoids look-alikes:** pairs must agree on thresholds, years, above/below/touch, rise/fall and negation, not just share words
- **No API keys:** uses only the venues' public market-data endpoints

```
 MCP client (Claude Desktop, Claude Code, LangGraph agent, ...)
        │  stdio (JSON-RPC)
        ▼
 ┌──────────────────────────── predmarket-mcp ────────────────────────────┐
 │  server.py     6 tools, input clamping, venue interleaving             │
 │  matching.py   cross-venue pairing (title similarity + close date)     │
 │  text.py       tokenizer + inverted index (fallback Kalshi search)     │
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
| `compare_venues(query, min_similarity=0.2, limit=10)` | Markets that appear to ask the same question on both venues, ranked by price gap |

Example, `compare_venues("recession")` (live output, trimmed, Nov 2026):

```json
[{
  "similarity": 0.297,
  "price_gap": 0.06,
  "kalshi":     {"id": "KXRECSSNBER-27", "title": "Will there be a recession in 2027? (Yes)", "mid": 0.215},
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

- **Kalshi search.** The documented Kalshi API has no keyword search. The server uses the public search endpoint behind kalshi.com's search box: one request, about 0.3 s, quotes included. That endpoint isn't part of the documented API, so if it fails the server falls back to its own inverted index of every open event, built from the documented `/events` endpoint and cached for 10 minutes. An earlier version always built that index: a cold search took about 10 s and 50+ requests and could trip Kalshi's rate limit.
- **Rate limits.** Each venue client spaces its requests evenly (5 req/s for Kalshi) instead of bursting and then backing off. On 429 or 5xx it retries with exponential backoff and honors `Retry-After`. A 15-topic `compare_venues` sweep makes 30 requests with zero 429s.
- **Caching.** Quotes are cached for 5 seconds and searches for 30 seconds. If an agent fires the same request several times in parallel, only one upstream call is made.
- **Cross-venue matching.** Titles are tokenized, stop words removed, and long forms folded into one token ("United States" → `us`, "Federal Reserve" → `fed`). Similarity is Jaccard weighted by word rarity in the result set, so "Fed" and "2026" count less than "dissent" or "shutdown". Before scoring, a pair must:
  - share at least one action word, not just a name or a date ("Who will dissent at the FOMC? (Powell)" ≠ "Powell out from the Fed Board");
  - use the same thresholds (3.5% ≠ 3.0%, 25bps = 25 bps) and overlapping years;
  - compare the threshold the same way: above, below or touch ("above $150k on Dec 31" ≠ "reach $150k in 2026", "more than 25bps" ≠ "25bps");
  - move the same way (a rate cut ≠ a rate increase) and agree on negation ("cuts" ≠ "no cuts").
  
  These rules came from live data. Without them, a 3.5% Fed-funds threshold was paired with a 3.0% one, showing a fake 98-point gap. Each rule has a regression test built from real title pairs. Close dates must also be within 45 days, and pairs are one-to-one.
- **Units.** Prices are probabilities. Volume stays in each venue's own unit (`contracts` on Kalshi, `usd` on Polymarket) and is labeled, never mixed.

## Development

```bash
uv sync
uv run pytest          # no network: HTTP is mocked with respx
uv run ruff check .
```

## Limitations

- Read-only market data. It doesn't place orders and never touches an account.
- Matching works on titles. It finds candidates; a human or the agent must still confirm the contracts resolve the same way.
- The primary Kalshi search uses an undocumented public endpoint. If it changes, search falls back to the slower index automatically.
- Kalshi multivariate "combo" markets are excluded because they aren't comparable across venues.
- Public endpoints change. The venue adapters are small and isolated so they're easy to update.

Not financial advice. Not affiliated with Kalshi or Polymarket.

## License

MIT
