# predmarket-mcp

[![CI](https://github.com/bprakashputta/predmarket-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bprakashputta/predmarket-mcp/actions/workflows/ci.yml)

**An MCP server that gives AI agents live prediction-market data from Kalshi and Polymarket.**

**[Live board →](https://bprakashputta.github.io/predmarket-mcp/)** Markets that ask the same question on both venues, the price gap between them, and the look-alikes the matcher refused. Rebuilt from the live APIs every 6 hours by [`scripts/build_dashboard.py`](scripts/build_dashboard.py).

Ask Claude (or any MCP client) things like *"What are the odds of a US recession in 2027 on Kalshi vs Polymarket?"* or *"Show me the order book and last day of trading for the December Fed cut market"*. The agent calls these tools, gets clean venue-neutral data and answers with real numbers.

- **6 read-only tools:** search, quote, order book, recent trades, price history and cross-venue comparison
- **One data model for both venues:** every price is a YES probability from 0 to 1, Kalshi NO bids are converted to YES asks, and Polymarket NO-token fills are converted to YES prices
- **Built for agent workloads:** pooled HTTP clients, per-venue rate limiting, retries that honor `Retry-After`, and a TTL cache that collapses duplicate concurrent requests
- **Cross-venue matching that avoids look-alikes:** 12 rules make pairs agree on thresholds, dates, place, stage (nominee ≠ confirmed), direction and more, not just share words. Each refusal comes with a readable reason
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
- **Cross-venue matching.** Titles are tokenized, stop words removed, and long forms folded into one token ("United States" → `us`, "Federal Reserve" → `fed`). Similarity is Jaccard weighted by word rarity in the result set, so "Fed" and "2026" count less than "dissent" or "shutdown". Before scoring, a pair must pass 12 title rules, checked in order. `incompatibility_reason(a, b)` returns the first one that fails as plain text ("different dates: Nov 13 vs Nov 9"):
  1. **Action word.** Share at least one, not just a name or a date ("Who will dissent at the FOMC? (Powell)" ≠ "Powell out from the Fed Board").
  2. **Negation.** "cuts" ≠ "no cuts".
  3. **Year.** 2026 ≠ 2027.
  4. **Dates.** Nov 13 ≠ Nov 9, and "by June 30 (Before July)" ≠ "by the July meeting". "Before Jan 1" counts as the same deadline as "by Dec 31".
  5. **Timing.** A state *on* a date ≠ something happening *by* that date ("shut down on Dec 12" ≠ "shutdown by December 12").
  6. **Period.** A quarter ≠ the whole year ("recession starts in Q1 2026" ≠ "recession by end of 2026").
  7. **Place.** UK GDP ≠ US GDP. Unplaced macro titles (GDP, inflation, recession, the Fed) count as US, the way both venues word them.
  8. **Stage.** Nominated ≠ confirmed, announced ≠ agreed ("become the next Justice" ≠ "be the next nominee").
  9. **Qualifier.** An emergency cut ≠ any cut.
  10. **Direction.** A rate cut ≠ a rate increase.
  11. **Threshold.** 3.5% ≠ 3.0%, while 25bps = 25 bps.
  12. **Comparison.** Above, below and touch differ ("above $150k on Dec 31" ≠ "reach $150k in 2026", "more than 25bps" ≠ "25bps").

  Close times must be within 45 days. When the earlier market closes within two weeks, they must also be within a quarter of its remaining life, because three days is noise for a year-end question but a different bet for a weekly crypto price. Pairs are one-to-one.

  These rules came from live data. Without them, a 3.5% Fed-funds threshold was paired with a 3.0% one and showed a fake 98-point gap, and the board's widest "gaps" were a Nov 13 vs Nov 9 crypto price and a nominee vs a confirmed justice. Each rule has a regression test built from real title pairs.
- **Units.** Prices are probabilities. Volume stays in each venue's own unit (`contracts` on Kalshi, `usd` on Polymarket) and is labeled, never mixed.

## Development

```bash
uv sync
uv run pytest          # no network: HTTP is mocked with respx
uv run ruff check .

# the live board: build a snapshot from the real APIs, then serve site/
uv run python scripts/build_dashboard.py --out site/data.json
python3 -m http.server -d site 8000
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
