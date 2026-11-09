from datetime import UTC, datetime, timedelta

from predmarket_mcp.matching import match_markets
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
