"""Lightweight text matching used for Kalshi keyword search and cross-venue pairing.

Uses an inverted index, so a query only touches documents that share a token with it,
instead of scanning every market title."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOPWORDS = frozenset(
    "a an and are be before by for from in is it of on or the to will with what who which "
    "when than this that at as yes no end".split()
)


# fold common long forms into one canonical token so "US" and "United States" score the same
_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{src}\b"), dst)
    for src, dst in [
        (r"new york city", "nyc"),
        (r"united states( of america)?", "us"),
        (r"u\.s\.a?\.?", "us"),
        (r"usa", "us"),
        (r"united kingdom", "uk"),
        (r"federal reserve", "fed"),
        (r"bitcoin", "btc"),
        (r"ethereum", "eth"),
        (r"republicans?", "gop"),
        (r"pro football|nfl league", "nfl"),
    ]
]
_KEEP_SHORT = frozenset({"us", "uk", "eu"})


def tokens(text: str) -> set[str]:
    text = text.lower()
    for pattern, canonical in _PHRASES:
        text = pattern.sub(canonical, text)
    return {
        t for t in _TOKEN.findall(text) if t not in STOPWORDS and (len(t) > 1 or t in _KEEP_SHORT)
    }


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class InvertedIndex[T]:
    def __init__(self) -> None:
        self._docs: list[tuple[set[str], T]] = []
        self._postings: dict[str, list[int]] = defaultdict(list)

    def add(self, text: str, item: T) -> None:
        toks = tokens(text)
        idx = len(self._docs)
        self._docs.append((toks, item))
        for t in toks:
            self._postings[t].append(idx)

    def extend(self, pairs: Iterable[tuple[str, T]]) -> None:
        for text, item in pairs:
            self.add(text, item)

    def __len__(self) -> int:
        return len(self._docs)

    def search(self, query: str, limit: int = 10, min_score: float = 0.0) -> list[tuple[float, T]]:
        """Rank documents by query-token coverage, then by Jaccard to break ties."""
        q = tokens(query)
        if not q:
            return []
        hits: dict[int, int] = defaultdict(int)
        for t in q:
            for i in self._postings.get(t, ()):
                hits[i] += 1
        scored = []
        for i, overlap in hits.items():
            doc_toks, item = self._docs[i]
            coverage = overlap / len(q)
            score = 0.8 * coverage + 0.2 * jaccard(q, doc_toks)
            if score >= min_score:
                scored.append((round(score, 4), item))
        scored.sort(key=lambda s: s[0], reverse=True)
        return scored[:limit]
