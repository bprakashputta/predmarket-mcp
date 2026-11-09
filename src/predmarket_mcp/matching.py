"""Pair markets that look like the same question on Kalshi and Polymarket."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .models import Market, MarketMatch
from .text import InvertedIndex, tokens

MAX_CLOSE_GAP_DAYS = 45

_MONTH_DAY = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(st|nd|rd|th)?\b"
)
_NUMBER = re.compile(r"(?<![\w.])\$?(\d+(?:\.\d+)?)\s*(bps|k|m|bn|b|%)?(?![\w])")
# how a threshold is compared: closes above, closes below, or touches it at any point
_COMPARATORS = {
    "above": re.compile(
        r"(>|\b(above|over|exceeds?|greater than|higher than|more than|at least|or more)\b)"
    ),
    "below": re.compile(r"(<|\b(below|under|less than|lower than|at most|or less|fewer than)\b)"),
    "touch": re.compile(r"\b(reach(es)?|hits?|dips? to|falls? to|touch(es)?)\b"),
}
# which way the thing itself moves
_RISE = re.compile(r"\b(hikes?|increases?|raises?|rises?)\b")
_FALL = re.compile(r"\b(cuts?|decreases?|reduces?|lowers?)\b")
_NEGATION = re.compile(r"\b(no|not|never|without|none)\b")
_SCALE = {"k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9, "bps": 1e-4}
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_MONTHS = frozenset(
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec".split()
)


@dataclass(frozen=True)
class _Terms:
    years: frozenset[int]
    numbers: frozenset[float]
    comparator: frozenset[str]
    movement: str | None
    negated: bool
    # how each number was written in the title, for human-readable reasons
    spelled: tuple[tuple[float, str], ...] = ()

    def show_numbers(self) -> str:
        names = dict(self.spelled)
        return ", ".join(names.get(n, f"{n:g}") for n in sorted(self.numbers)) or "none"


def _terms(title: str) -> _Terms:
    """The parts of a question that change its meaning even when the words look alike:
    thresholds, years, direction (above/below) and negation."""
    text = _MONTH_DAY.sub(" ", title.lower().replace(",", ""))
    years: set[int] = set()
    numbers: set[float] = set()
    spelled: dict[float, str] = {}
    for match in _NUMBER.finditer(text):
        value, unit = match.groups()
        num = float(value)
        if not unit and num.is_integer() and 1990 <= num <= 2100:
            years.add(int(num))
        else:
            scaled = round(num * _SCALE.get(unit or "", 1), 6)
            numbers.add(scaled)
            spelled.setdefault(scaled, match.group(0).strip())
    comparator = frozenset(name for name, rx in _COMPARATORS.items() if rx.search(text))
    rise, fall = bool(_RISE.search(text)), bool(_FALL.search(text))
    movement = "rise" if rise and not fall else "fall" if fall and not rise else None
    return _Terms(
        frozenset(years),
        frozenset(numbers),
        comparator,
        movement,
        bool(_NEGATION.search(text)),
        tuple(spelled.items()),
    )


def _action_words(title: str) -> set[str]:
    """Lowercase words in the original title: the verbs and nouns that say what the question
    is about ("dissent", "recession", "hike"), as opposed to names and dates."""
    words = {w.lower() for w in _WORD.findall(title) if w[0].islower()}
    return tokens(" ".join(words)) - _MONTHS


def shares_action(a: str, b: str) -> bool:
    """Titles that only share names and dates ask different questions: 'Who will dissent at
    the FOMC? (Jerome Powell)' vs 'Jerome Powell out from the Fed Board'."""
    return bool(_action_words(a) & tokens(b)) or bool(_action_words(b) & tokens(a))


_COMPARATOR_WORDS = {"above": "above", "below": "below", "touch": "reaches"}


def _show_comparator(c: frozenset[str]) -> str:
    return " or ".join(_COMPARATOR_WORDS[n] for n in sorted(c)) if c else "exactly"


def _show_years(years: frozenset[int]) -> str:
    return ", ".join(str(y) for y in sorted(years))


def _movement_word(title: str) -> str:
    """The word that set a title's movement, e.g. 'cut' or 'hikes'."""
    text = title.lower()
    found = _RISE.search(text) if _terms(title).movement == "rise" else _FALL.search(text)
    return found.group(0) if found else "?"


def incompatibility_reason(a: str, b: str) -> str | None:
    """Why two titles that share words still ask different questions, or None if they agree.

    Catches e.g. 'above 3.75%' vs 'exactly 3.75%', a 3.5% vs a 3% threshold, 2026 vs 2027,
    a cut vs a hike, or 'cuts' vs 'no cuts'. Checks run in a fixed order and the first
    failing one is reported."""
    if not shares_action(a, b):
        return "only names or dates in common, no shared action word"
    x, y = _terms(a), _terms(b)
    if x.negated != y.negated:
        return f"negated on one side only ({'first' if x.negated else 'second'} says no/not)"
    if x.years and y.years and not x.years & y.years:
        return f"different year: {_show_years(x.years)} vs {_show_years(y.years)}"
    if x.movement and y.movement and x.movement != y.movement:
        return f"opposite direction: {_movement_word(a)} vs {_movement_word(b)}"
    if x.numbers != y.numbers:
        if not x.numbers or not y.numbers:
            side = x if x.numbers else y
            return f"threshold on one side only: {side.show_numbers()}"
        return f"different threshold: {x.show_numbers()} vs {y.show_numbers()}"
    # with a threshold, "above 3.75%", "exactly 3.75%" and "reaches 3.75%" are different bets
    if x.numbers and x.comparator != y.comparator:
        return (
            f"different comparison: {_show_comparator(x.comparator)} vs "
            f"{_show_comparator(y.comparator)} {x.show_numbers()}"
        )
    return None


def compatible(a: str, b: str) -> bool:
    """True when two titles agree on action, negation, years, direction, thresholds and
    comparison. See incompatibility_reason for the checks."""
    return incompatibility_reason(a, b) is None


def _text(m: Market) -> str:
    return f"{m.event_title or ''} {m.title}"


def _idf(docs: list[set[str]]) -> dict[str, float]:
    """Words that appear in many titles of this result set (e.g. "fed", "2026") count less
    than distinctive ones (e.g. "dissent", "charged")."""
    df = Counter(t for doc in docs for t in doc)
    n = max(len(docs), 1)
    return {t: math.log(1 + n / c) for t, c in df.items()}


def _weighted_jaccard(a: set[str], b: set[str], weight: dict[str, float]) -> float:
    union = sum(weight.get(t, 1.0) for t in a | b)
    return sum(weight.get(t, 1.0) for t in a & b) / union if union else 0.0


@dataclass(frozen=True)
class NearMiss:
    """A pair that scored as similar on words but that the compatibility checks refused."""

    kalshi: Market
    polymarket: Market
    similarity: float
    reason: str


def _similar_pairs(
    kalshi: list[Market], polymarket: list[Market], min_similarity: float
) -> list[tuple[float, int, int]]:
    """(similarity, kalshi index, polymarket index) for every pair at or above the threshold.

    Indexes the Polymarket side so each Kalshi market is only compared with candidates
    that share at least one token."""
    index: InvertedIndex[int] = InvertedIndex()
    index.extend((_text(m), i) for i, m in enumerate(polymarket))
    k_tokens = [tokens(_text(m)) for m in kalshi]
    pm_tokens = [tokens(_text(m)) for m in polymarket]
    weight = _idf(k_tokens + pm_tokens)
    out: list[tuple[float, int, int]] = []
    for ki, k in enumerate(kalshi):
        for _, pi in index.search(_text(k), limit=20):
            sim = _weighted_jaccard(k_tokens[ki], pm_tokens[pi], weight)
            if sim >= min_similarity:
                out.append((sim, ki, pi))
    return out


def match_markets(
    kalshi: list[Market], polymarket: list[Market], min_similarity: float = 0.2
) -> list[MarketMatch]:
    """Greedy one-to-one matching on rarity-weighted title similarity, after checking that
    thresholds, years, direction and negation agree and close dates are near each other."""
    candidates: list[tuple[float, int, int]] = []
    for sim, ki, pi in _similar_pairs(kalshi, polymarket, min_similarity):
        k, p = kalshi[ki], polymarket[pi]
        if not compatible(k.title, p.title):
            continue
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


def near_misses(
    kalshi: list[Market], polymarket: list[Market], min_similarity: float = 0.35
) -> list[NearMiss]:
    """Pairs whose titles overlap heavily but that incompatibility_reason refuses, most
    similar first. Useful to show what the matcher protects against."""
    out: list[NearMiss] = []
    for sim, ki, pi in _similar_pairs(kalshi, polymarket, min_similarity):
        k, p = kalshi[ki], polymarket[pi]
        reason = incompatibility_reason(k.title, p.title)
        if reason:
            out.append(NearMiss(k, p, round(sim, 3), reason))
    out.sort(key=lambda n: n.similarity, reverse=True)
    return out
