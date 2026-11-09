"""Build the cross-venue board snapshot: search both venues for a fixed set of topics, pair the
markets, and write one JSON file the static dashboard reads.

    uv run python scripts/build_dashboard.py --out site/data.json

Only live public data is written. A venue error on one topic is recorded and the sweep carries
on; the script exits non-zero if a venue returned nothing at all or no pairs were found, so CI
never publishes an empty board."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from predmarket_mcp.kalshi import KalshiClient
from predmarket_mcp.matching import match_markets, near_misses
from predmarket_mcp.models import Market, MarketMatch, PricePoint
from predmarket_mcp.polymarket import PolymarketClient

TOPICS = [
    "fed rate",
    "fed hike",
    "rate cut",
    "recession",
    "inflation",
    "bitcoin",
    "ethereum",
    "government shutdown",
    "tariffs",
    "unemployment",
    "gdp",
    "s&p 500",
    "oil",
    "gold",
    "election",
    "supreme court",
]
SEARCH_LIMIT = 30
MIN_SIMILARITY = 0.2
NEAR_MISS_SIMILARITY = 0.35
MAX_NEAR_MISSES = 40
HISTORY_HOURS = 24 * 7
HISTORY_STEP_S = 3 * 3600  # keep one point per 3 hours: 56 points a leg over 7 days

log = logging.getLogger("build_dashboard")


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def market_json(m: Market) -> dict[str, Any]:
    return {
        "id": m.id,
        "title": m.title,
        "event": m.event_title,
        "bid": _r(m.yes_bid),
        "ask": _r(m.yes_ask),
        "mid": _r(m.mid),
        "last": _r(m.last_price),
        "volume": None if m.volume is None else round(m.volume),
        "volume_unit": m.volume_unit,
        "close_time": _iso(m.close_time),
        "url": m.url,
    }


def downsample(points: list[PricePoint], step_s: int = HISTORY_STEP_S) -> list[list[float]]:
    """Last point in each step-sized bucket, as [unix seconds, price]; the newest point is
    always kept so the line ends at the latest print."""
    buckets: dict[int, PricePoint] = {}
    for p in sorted(points, key=lambda p: p.time):
        buckets[int(p.time.timestamp()) // step_s] = p
    kept = list(buckets.values())
    return [[int(p.time.timestamp()), round(p.price, 4)] for p in kept]


async def search_topic(
    topic: str, kalshi: KalshiClient, pm: PolymarketClient, errors: list[dict[str, str]]
) -> tuple[list[Market], list[Market]]:
    k_res, p_res = await asyncio.gather(
        kalshi.search(topic, limit=SEARCH_LIMIT),
        pm.search(topic, limit=SEARCH_LIMIT),
        return_exceptions=True,
    )
    out: list[list[Market]] = []
    now = datetime.now(UTC)
    for venue, res in (("kalshi", k_res), ("polymarket", p_res)):
        if isinstance(res, BaseException):
            log.warning("%s search %r failed: %s", venue, topic, res)
            errors.append({"stage": "search", "venue": venue, "topic": topic, "error": _err(res)})
            out.append([])
        else:
            # a market past its close time can't be traded; it is only waiting to resolve
            out.append([m for m in res if not (m.close_time and m.close_time <= now)])
    return out[0], out[1]


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:240]


async def history(
    client: KalshiClient | PolymarketClient,
    venue: str,
    market_id: str,
    errors: list[dict[str, str]],
) -> list[list[float]]:
    try:
        return downsample(await client.price_history(market_id, HISTORY_HOURS))
    except Exception as exc:  # noqa: BLE001 - one missing chart must not sink the board
        log.warning("%s history %s failed: %s", venue, market_id, exc)
        errors.append({"stage": "history", "venue": venue, "market": market_id, "error": _err(exc)})
        return []


async def build() -> dict[str, Any]:
    started = time.monotonic()
    kalshi, pm = KalshiClient(), PolymarketClient()
    errors: list[dict[str, str]] = []
    seen_k: dict[str, Market] = {}
    seen_p: dict[str, Market] = {}
    pairs: dict[tuple[str, str], tuple[str, MarketMatch]] = {}
    misses: dict[tuple[str, str], dict[str, Any]] = {}
    topic_stats: list[dict[str, Any]] = []
    try:
        for topic in TOPICS:  # sequential: the clients' throttles pace each venue
            k_list, p_list = await search_topic(topic, kalshi, pm, errors)
            seen_k.update((m.id, m) for m in k_list)
            seen_p.update((m.id, m) for m in p_list)
            found = match_markets(k_list, p_list, min_similarity=MIN_SIMILARITY)
            new = 0
            for match in found:
                key = (match.kalshi.id, match.polymarket.id)
                if key not in pairs:  # first topic that surfaced the pair keeps it
                    pairs[key] = (topic, match)
                    new += 1
            for nm in near_misses(k_list, p_list, min_similarity=NEAR_MISS_SIMILARITY):
                key = (nm.kalshi.id, nm.polymarket.id)
                if key in misses:
                    continue
                misses[key] = {
                    "topic": topic,
                    "similarity": nm.similarity,
                    "reason": nm.reason,
                    "kalshi": {"id": nm.kalshi.id, "title": nm.kalshi.title, "url": nm.kalshi.url},
                    "polymarket": {
                        "id": nm.polymarket.id,
                        "title": nm.polymarket.title,
                        "url": nm.polymarket.url,
                    },
                }
            topic_stats.append(
                {"topic": topic, "kalshi": len(k_list), "polymarket": len(p_list), "pairs": new}
            )
            log.info("%-20s kalshi=%2d pm=%2d pairs=%d", topic, len(k_list), len(p_list), new)

        ordered = sorted(pairs.values(), key=lambda tm: abs(tm[1].price_gap or 0), reverse=True)
        # histories: Kalshi and Polymarket legs run side by side, each paced by its own client
        k_hist, p_hist = await asyncio.gather(
            asyncio.gather(*(history(kalshi, "kalshi", m.kalshi.id, errors) for _, m in ordered)),
            asyncio.gather(
                *(history(pm, "polymarket", m.polymarket.id, errors) for _, m in ordered)
            ),
        )
    finally:
        await asyncio.gather(
            kalshi.http.aclose(), pm.gamma.aclose(), pm.clob.aclose(), pm.data.aclose()
        )

    rows = []
    for (topic, m), kh, ph in zip(ordered, k_hist, p_hist, strict=True):
        rows.append(
            {
                "topic": topic,
                "similarity": m.similarity,
                "gap": m.price_gap,
                "kalshi": {**market_json(m.kalshi), "history": kh},
                "polymarket": {**market_json(m.polymarket), "history": ph},
            }
        )
    gaps = [abs(r["gap"]) for r in rows if r["gap"] is not None]
    rejected = sorted(misses.values(), key=lambda n: n["similarity"], reverse=True)
    return {
        "generated_at": _iso(datetime.now(UTC)),
        "build_seconds": round(time.monotonic() - started, 1),
        "source": "https://github.com/bprakashputta/predmarket-mcp",
        "settings": {
            "min_similarity": MIN_SIMILARITY,
            "near_miss_similarity": NEAR_MISS_SIMILARITY,
            "history_hours": HISTORY_HOURS,
            "history_step_hours": HISTORY_STEP_S // 3600,
        },
        "topics": topic_stats,
        "markets_scanned": {"kalshi": len(seen_k), "polymarket": len(seen_p)},
        "summary": {
            "pairs": len(rows),
            "median_abs_gap": round(statistics.median(gaps), 4) if gaps else None,
            "max_abs_gap": round(max(gaps), 4) if gaps else None,
            "rejected": len(rejected),
        },
        "pairs": rows,
        "rejected": rejected[:MAX_NEAR_MISSES],
        "errors": errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("site/data.json"))
    ap.add_argument(
        "--allow-empty", action="store_true", help="write the file even if no pairs were found"
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    data = asyncio.run(build())
    scanned = data["markets_scanned"]
    problems = [f"{v} returned no markets" for v, n in scanned.items() if n == 0]
    if not data["pairs"] and not args.allow_empty:
        problems.append("no cross-venue pairs found")
    if problems:
        log.error("refusing to write an empty board: %s", "; ".join(problems))
        for e in data["errors"][:10]:
            log.error("  %s", e)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    args.out.write_text(text, encoding="utf-8")
    s = data["summary"]
    log.info(
        "wrote %s (%.0f KB): %d pairs, %d rejected, kalshi=%d pm=%d markets, %d errors, %.1fs",
        args.out,
        len(text.encode()) / 1024,
        s["pairs"],
        s["rejected"],
        scanned["kalshi"],
        scanned["polymarket"],
        len(data["errors"]),
        data["build_seconds"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
