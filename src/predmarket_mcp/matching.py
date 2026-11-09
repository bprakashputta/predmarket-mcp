"""Pair markets that look like the same question on Kalshi and Polymarket."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

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


_MONTH_NAMES = (
    "January February March April May June July August September October November December"
).split()
_MONTH = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?(?:\s+(\d{1,2})(?:st|nd|rd|th)?\b)?"
)

# Places. A title that names none is unplaced, except macro questions (GDP, inflation, a
# recession, the Fed), which both venues word without a country when they mean the US.
_PLACES: dict[str, str] = {
    **{w: "US" for w in ("us", "american", "america")},
    **{w: "UK" for w in ("uk", "britain", "british", "england")},
    **{w: "EU" for w in ("eu", "eurozone", "euro area", "ecb")},
    **{w: "World" for w in ("world", "global", "worldwide")},
    "canada": "Canada",
    "canadian": "Canada",
    "china": "China",
    "chinese": "China",
    "japan": "Japan",
    "japanese": "Japan",
    "boj": "Japan",
    "germany": "Germany",
    "german": "Germany",
    "france": "France",
    "french": "France",
    "india": "India",
    "indian": "India",
    "mexico": "Mexico",
    "mexican": "Mexico",
    "brazil": "Brazil",
    "russia": "Russia",
    "russian": "Russia",
    "italy": "Italy",
    "spain": "Spain",
    "australia": "Australia",
    "south korea": "South Korea",
    "korea": "South Korea",
    "argentina": "Argentina",
    "turkey": "Turkey",
    "switzerland": "Switzerland",
    **{
        w: w.title()
        for w in (
            "nigeria egypt israel iran ukraine poland netherlands sweden norway ireland "
            "singapore indonesia taiwan vietnam chile colombia venezuela pakistan"
        ).split()
    },
    **{w: w.title() for w in ("south africa", "new zealand", "saudi arabia", "hong kong")},
}
_PLACE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _PLACES), key=len, reverse=True)) + r")\b"
)
_US_DEFAULT = re.compile(
    r"\b(gdp|inflation|cpi|pce|unemployment|jobless|payrolls?|recession|fed|fomc|"
    r"federal reserve|federal funds|interest rates?)\b"
)
# How far along a process the question is: put forward, approved, announced or agreed.
# A nominee isn't a confirmed justice, and announcing a tariff cut isn't signing a deal.
_STAGES = {
    "nominated": re.compile(r"\b(nominee|nominees|nominated?|nominates|nomination)\b"),
    "confirmed": re.compile(
        r"\b(confirmed|confirms?|confirmation|becomes?|appointed|sworn in|takes? office)\b"
    ),
    "announced": re.compile(r"\b(announces?|announced|announcement)\b"),
    "agreed": re.compile(r"\b(agreements?|agrees?|agreed|signs?|signed|signing|deal)\b"),
}


_BEFORE_YEAR = re.compile(r"\bbefore (\d{4})\b")
_QUARTER = re.compile(r"\bq([1-4])\b")
_MONTH_WORD = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"
# "on Dec 12" is the state on that day; "by Dec 12" is anything that happens before it
_TIMING = {
    "on": re.compile(rf"\b(on|at)\s+{_MONTH_WORD}"),
    "by": re.compile(rf"\b(by|before)\s+{_MONTH_WORD}"),
}
# words that narrow a question to a subset: an emergency cut is a cut, not every cut
_QUALIFIERS = re.compile(r"\b(emergency|unscheduled|inter-?meeting|surprise)\b")


@dataclass(frozen=True)
class _Dates:
    days: frozenset[tuple[int, int]]  # (month, day)
    months: frozenset[int]  # months named without a day
    labels: tuple[str, ...]  # as written, for reasons


def _dates(title: str) -> _Dates:
    """Calendar anchors in a title: 'Nov 13', 'July 2027', and 'before 2027' (= Dec 31)."""
    text = title.lower()
    days: set[tuple[int, int]] = set()
    months: set[int] = set()
    labels: list[str] = []
    for m in _MONTH.finditer(text):
        word, day = m.group(1), m.group(2)
        if word == "may" and not day:
            continue  # "may" is usually the verb
        month = next(i for i, n in enumerate(_MONTH_NAMES, 1) if n.lower().startswith(word[:3]))
        if day and 1 <= int(day) <= 31:
            labels.append(f"{_MONTH_NAMES[month - 1][:3]} {int(day)}")
            if text[: m.start()].rstrip().endswith("before"):
                # "before Jan 1" is the same deadline as "by Dec 31"
                prev = date(2001, month, min(int(day), 28 if month == 2 else int(day)))
                prev -= timedelta(days=1)
                month, day = prev.month, str(prev.day)
            days.add((month, int(day)))
        else:
            months.add(month)
    for year in _BEFORE_YEAR.findall(text):
        days.add((12, 31))
        labels.append(f"before {year}")
    if not days:  # a bare month only anchors the title when there is no day
        labels = [_MONTH_NAMES[m - 1] for m in sorted(months)]
    return _Dates(frozenset(days), frozenset(months), tuple(dict.fromkeys(labels)))


def _date_conflict(a: str, b: str) -> str | None:
    """Both titles name a date and they don't agree. A day beats a bare month: 'by June 30,
    2027 (Before July 2027)' is anchored on June, not July. One side with no date passes."""
    x, y = _dates(a), _dates(b)
    if x.days and y.days:
        clash = not x.days & y.days
    else:
        months_a = {m for m, _ in x.days} or x.months
        months_b = {m for m, _ in y.days} or y.months
        clash = bool(months_a and months_b and not months_a & months_b)
    if clash:
        return f"different dates: {', '.join(x.labels)} vs {', '.join(y.labels)}"
    return None


def _timing_conflict(a: str, b: str) -> str | None:
    ta = {name for name, rx in _TIMING.items() if rx.search(a.lower())}
    tb = {name for name, rx in _TIMING.items() if rx.search(b.lower())}
    if ta and tb and not ta & tb:
        return f"different timing: {'/'.join(sorted(ta))} a date vs {'/'.join(sorted(tb))} a date"
    return None


def _period_conflict(a: str, b: str) -> str | None:
    """A quarter is not the whole year: 'recession starts in Q1 2026' vs 'by end of 2026'."""
    qa, qb = set(_QUARTER.findall(a.lower())), set(_QUARTER.findall(b.lower()))
    if qa != qb:

        def show(q: set[str]) -> str:
            return ", ".join(f"Q{n}" for n in sorted(q)) or "no quarter"

        return f"different period: {show(qa)} vs {show(qb)}"
    return None


def _qualifier_conflict(a: str, b: str) -> str | None:
    qa = {m.replace("-", "") for m in _QUALIFIERS.findall(a.lower())}
    qb = {m.replace("-", "") for m in _QUALIFIERS.findall(b.lower())}
    if qa != qb:
        return f"narrower on one side: {', '.join(sorted(qa ^ qb))}"
    return None


def _places(title: str) -> tuple[frozenset[str], bool]:
    """Places a title names, and whether it was assumed (US for an unplaced macro question)."""
    text = title.lower().replace("u.s.", "us").replace("united states", "us")
    text = text.replace("united kingdom", "uk").replace("european union", "eu")
    found = frozenset(_PLACES[m] for m in _PLACE.findall(text))
    if not found and _US_DEFAULT.search(text):
        return frozenset({"US"}), True
    return found, False


def _place_conflict(a: str, b: str) -> str | None:
    (pa, ia), (pb, ib) = _places(a), _places(b)
    if pa and pb and not pa & pb:

        def show(p: frozenset[str], implied: bool) -> str:
            return "/".join(sorted(p)) + (" (implied)" if implied else "")

        return f"different place: {show(pa, ia)} vs {show(pb, ib)}"
    return None


def _stages(title: str) -> dict[str, str]:
    text = title.lower()
    return {name: m.group(0) for name, rx in _STAGES.items() if (m := rx.search(text))}


def _stage_conflict(a: str, b: str) -> str | None:
    sa, sb = _stages(a), _stages(b)
    if sa and sb and not sa.keys() & sb.keys():
        return f"different stage: {' / '.join(sa.values())} vs {' / '.join(sb.values())}"
    return None


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
    Nov 13 vs Nov 9, UK vs US GDP, a nominee vs a confirmed justice, a cut vs a hike, or
    'cuts' vs 'no cuts'. Checks run in a fixed order and the first
    failing one is reported."""
    if not shares_action(a, b):
        return "only names or dates in common, no shared action word"
    x, y = _terms(a), _terms(b)
    if x.negated != y.negated:
        return f"negated on one side only ({'first' if x.negated else 'second'} says no/not)"
    if x.years and y.years and not x.years & y.years:
        return f"different year: {_show_years(x.years)} vs {_show_years(y.years)}"
    for check in (
        _date_conflict,
        _timing_conflict,
        _period_conflict,
        _place_conflict,
        _stage_conflict,
        _qualifier_conflict,
    ):
        if reason := check(a, b):
            return reason
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
    """True when two titles agree on action, negation, years, dates, place, stage, direction,
    thresholds and comparison. See incompatibility_reason for the checks."""
    return incompatibility_reason(a, b) is None


# Close times. Venues close the "same" question at slightly different moments (Kalshi often
# after the data release, Polymarket before it), so for long-dated markets a few weeks apart
# is normal and MAX_CLOSE_GAP_DAYS is the only cap. For a market that closes soon, the same
# few days are most of its remaining life: a crypto price on Nov 9 and on Nov 13 are
# different bets. So when the earlier leg closes within SHORT_DATED_DAYS, the close times
# must also be within SHORT_DATED_SHARE of the time it has left (and never more than a day
# apart when it closes within a few days).
SHORT_DATED_DAYS = 14
SHORT_DATED_SHARE = 0.25


def _days(n: float) -> str:
    n = round(n)
    return f"{n} day" if n == 1 else f"{n} days"


def close_time_reason(a: Market, b: Market, now: datetime | None = None) -> str | None:
    """Why two markets' close times are too far apart to be the same question, or None."""
    if not (a.close_time and b.close_time):
        return None
    apart = abs((a.close_time - b.close_time).total_seconds()) / 86400
    if apart > MAX_CLOSE_GAP_DAYS:
        return f"closes {_days(apart)} apart (limit {MAX_CLOSE_GAP_DAYS})"
    left = (min(a.close_time, b.close_time) - (now or datetime.now(UTC))).total_seconds() / 86400
    if 0 < left <= SHORT_DATED_DAYS and apart > max(1.0, SHORT_DATED_SHARE * left):
        return f"closes {_days(apart)} apart with {_days(left)} left"
    return None


def pair_reason(k: Market, p: Market, now: datetime | None = None) -> str | None:
    """Every check a cross-venue pair must pass: titles first, then close times."""
    return incompatibility_reason(k.title, p.title) or close_time_reason(k, p, now)


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
    kalshi: list[Market],
    polymarket: list[Market],
    min_similarity: float = 0.2,
    now: datetime | None = None,
) -> list[MarketMatch]:
    """Greedy one-to-one matching on rarity-weighted title similarity, after checking that
    the titles ask the same question (see incompatibility_reason) and close times agree."""
    candidates = [
        (sim, ki, pi)
        for sim, ki, pi in _similar_pairs(kalshi, polymarket, min_similarity)
        if pair_reason(kalshi[ki], polymarket[pi], now) is None
    ]

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
    kalshi: list[Market],
    polymarket: list[Market],
    min_similarity: float = 0.35,
    now: datetime | None = None,
) -> list[NearMiss]:
    """Pairs the checks refused that would otherwise have matched: greedy one-to-one on
    similarity, like match_markets, so each market shows only its closest look-alike."""
    refused: list[tuple[float, int, int, str]] = []
    for sim, ki, pi in _similar_pairs(kalshi, polymarket, min_similarity):
        if reason := pair_reason(kalshi[ki], polymarket[pi], now):
            refused.append((sim, ki, pi, reason))
    refused.sort(key=lambda r: r[0], reverse=True)
    used_k: set[int] = set()
    used_p: set[int] = set()
    out: list[NearMiss] = []
    for sim, ki, pi, reason in refused:
        if ki in used_k or pi in used_p:
            continue
        used_k.add(ki)
        used_p.add(pi)
        out.append(NearMiss(kalshi[ki], polymarket[pi], round(sim, 3), reason))
    return out
