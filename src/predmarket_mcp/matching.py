"""Pair markets that look like the same question on Kalshi and Polymarket."""

from __future__ import annotations

from .models import Market, MarketMatch
from .text import InvertedIndex, jaccard, tokens

MAX_CLOSE_GAP_DAYS = 45


def _text(m: Market) -> str:
    return f"{m.event_title or ''} {m.title}"


def match_markets(
    kalshi: list[Market], polymarket: list[Market], min_similarity: float = 0.35
) -> list[MarketMatch]:
    """Greedy one-to-one matching on title similarity, with a close-date sanity check.

    Indexes the Polymarket side so each Kalshi market is only compared with candidates
    that share at least one token."""
    index: InvertedIndex[int] = InvertedIndex()
    index.extend((_text(m), i) for i, m in enumerate(polymarket))
    pm_tokens = [tokens(_text(m)) for m in polymarket]

    candidates: list[tuple[float, int, int]] = []
    for ki, k in enumerate(kalshi):
        k_tokens = tokens(_text(k))
        for _, pi in index.search(_text(k), limit=20):
            sim = jaccard(k_tokens, pm_tokens[pi])
            if sim < min_similarity:
                continue
            p = polymarket[pi]
            if (
                k.close_time
                and p.close_time
                and abs((k.close_time - p.close_time).days) > MAX_CLOSE_GAP_DAYS
            ):
                continue
            candidates.append((sim, ki, pi))

    candidates.sort(reverse=True)
    used_k: set[int] = set()
    used_p: set[int] = set()
    matches: list[MarketMatch] = []
    for sim, ki, pi in candidates:
        if ki in used_k or pi in used_p:
            continue
        used_k.add(ki)
        used_p.add(pi)
        k, p = kalshi[ki], polymarket[pi]
        gap = round(p.mid - k.mid, 4) if k.mid is not None and p.mid is not None else None
        matches.append(MarketMatch(kalshi=k, polymarket=p, similarity=round(sim, 3), price_gap=gap))
    return matches
