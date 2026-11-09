from datetime import UTC, datetime, timedelta

import pytest

from predmarket_mcp.matching import compatible, match_markets
from predmarket_mcp.models import Market, Venue
from predmarket_mcp.text import InvertedIndex, jaccard, tokens


def mk(venue: Venue, id_: str, title: str, bid=None, ask=None, close_days=30) -> Market:
    return Market(
        venue=venue,
        id=id_,
        title=title,
        yes_bid=bid,
        yes_ask=ask,
        volume_unit="contracts" if venue is Venue.KALSHI else "usd",
        close_time=datetime(2027, 1, 1, tzinfo=UTC) + timedelta(days=close_days),
    )


def test_tokens_drop_stopwords_and_expand_abbreviations():
    assert tokens("Will the Federal Reserve cut rates?") == {"fed", "cut", "rates"}
    assert tokens("NYC mayor") == tokens("New York City mayor") == {"nyc", "mayor"}
    assert tokens("US recession by end of 2027") == tokens("United States recession in 2027")


def test_jaccard_edges():
    assert jaccard(set(), {"a"}) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_inverted_index_ranks_by_query_coverage():
    idx: InvertedIndex[str] = InvertedIndex()
    idx.extend(
        [
            ("Bitcoin above 130k in 2027", "btc"),
            ("Ethereum above 5k", "eth"),
            ("Bitcoin ETF approval", "etf"),
        ]
    )
    ranked = [item for _, item in idx.search("bitcoin 130k")]
    assert ranked[0] == "btc"
    assert "eth" not in ranked  # shares no token with the query
    assert idx.search("the and of") == []


def test_match_pairs_same_question_and_computes_gap():
    k = [mk(Venue.KALSHI, "K1", "Will there be a recession in 2027?", 0.30, 0.32)]
    p = [
        mk(Venue.POLYMARKET, "p-rec", "US recession in 2027?", 0.35, 0.37),
        mk(Venue.POLYMARKET, "p-other", "Will Japan host the Olympics?", 0.1, 0.2),
    ]
    matches = match_markets(k, p, min_similarity=0.3)
    assert len(matches) == 1
    assert matches[0].polymarket.id == "p-rec"
    assert matches[0].price_gap == 0.05  # 0.36 - 0.31


def test_match_handles_abbreviations_across_venues():
    k = [mk(Venue.KALSHI, "K1", "Will there be a recession in 2027?", 0.30, 0.32)]
    p = [
        mk(Venue.POLYMARKET, "P1", "US recession by end of 2027?", 0.27, 0.28),
        mk(Venue.POLYMARKET, "P2", "Japan recession in 2026?", 0.1, 0.2),
    ]
    assert [m.polymarket.id for m in match_markets(k, p)] == ["P1"]


def test_match_rejects_far_apart_close_dates():
    k = [mk(Venue.KALSHI, "K1", "Recession in 2027", close_days=0)]
    p = [mk(Venue.POLYMARKET, "P1", "Recession in 2027", close_days=200)]
    assert match_markets(k, p) == []


def test_match_is_one_to_one():
    k = [mk(Venue.KALSHI, "K1", "Bitcoin above 150k"), mk(Venue.KALSHI, "K2", "Bitcoin above 150k")]
    p = [mk(Venue.POLYMARKET, "P1", "Bitcoin above 150k")]
    assert len(match_markets(k, p)) == 1


@pytest.mark.parametrize(
    ("kalshi", "polymarket", "same"),
    [
        # real title pairs seen on the two venues (Nov 2026)
        (
            "Will the upper bound of the federal funds rate be above 3.50% following the "
            "Fed's Dec 9, 2026 meeting?",
            "Will the upper bound of the target federal funds rate be 3.0% at the end of 2026?",
            False,  # different threshold
        ),
        (
            "Will the upper bound of the federal funds rate be above 3.75% following the "
            "Fed's Dec 9, 2026 meeting?",
            "Will the upper bound of the target federal funds rate be 3.75% at the end of 2026?",
            False,  # "above" vs "exactly"
        ),
        ("Will the Fed cut rates 1 time?", "Will no Fed rate cuts happen in 2026?", False),
        (
            "When will the next U.S. recession start? (Before 2027)",
            "US recession by end of 2026?",
            False,
        ),
        ("Will there be a recession in 2027?", "US recession by end of 2027?", True),
        (
            "Will Bitcoin be above $150,000 by Dec 31, 2026?",
            "Will Bitcoin reach $150k in 2026?",
            False,  # closing above on a date vs touching once during the year
        ),
        (
            "Will the Federal Reserve Cut rates by 25bps at their December meeting?",
            "Will the Fed increase interest rates by 25 bps after the December meeting?",
            False,  # cut vs increase
        ),
        (
            "Who will dissent at the December 2026 FOMC meeting? (Jerome Powell)",
            "Jerome Powell out from Fed Board by December 31?",
            False,  # shares only a name and a date
        ),
        (
            "Will the Federal Reserve Hike rates by >25bps at their next meeting?",
            "Another Fed rate hike in 2026?",
            False,  # a 25bps threshold on one side only
        ),
        (
            "Will the Federal Reserve Hike rates by >25bps at their December meeting?",
            "Will the Fed increase interest rates by 25 bps after the December meeting?",
            False,  # more than 25bps vs exactly 25bps
        ),
        (
            "Will there be at least 4 Fed rate hikes in 2026?",
            "Will 4 Fed rate hikes happen in 2026?",
            False,
        ),
        (
            "Will there be exactly 2 Fed rate hikes in 2026?",
            "Will 2 Fed rate hikes happen in 2026?",
            True,
        ),
    ],
)
def test_compatible_rejects_lookalikes_with_different_terms(kalshi, polymarket, same):
    assert compatible(kalshi, polymarket) is same
