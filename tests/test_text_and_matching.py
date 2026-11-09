from datetime import UTC, datetime, timedelta

import pytest

from predmarket_mcp.matching import (
    close_time_reason,
    compatible,
    incompatibility_reason,
    match_markets,
    near_misses,
)
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


@pytest.mark.parametrize(
    ("a", "b", "reason"),
    [
        (
            "Will the upper bound of the federal funds rate be above 3.50% following the "
            "Fed's Dec 9, 2026 meeting?",
            "Will the upper bound of the target federal funds rate be 3.0% at the end of 2026?",
            "different threshold: 3.50% vs 3.0%",
        ),
        (
            "Will the upper bound of the federal funds rate be above 3.75% following the "
            "Fed's Dec 9, 2026 meeting?",
            "Will the upper bound of the target federal funds rate be 3.75% at the end of 2026?",
            "different comparison: above vs exactly 3.75%",
        ),
        (
            "When will the next U.S. recession start? (Before 2027)",
            "US recession by end of 2026?",
            "different year: 2027 vs 2026",
        ),
        (
            "Will the Federal Reserve Cut rates by 25bps at their December meeting?",
            "Will the Fed increase interest rates by 25 bps after the December meeting?",
            "opposite direction: cut vs increase",
        ),
        (
            "Who will dissent at the December 2026 FOMC meeting? (Jerome Powell)",
            "Jerome Powell out from Fed Board by December 31?",
            "only names or dates in common, no shared action word",
        ),
        (
            "Will the Federal Reserve Hike rates by >25bps at their next meeting?",
            "Another Fed rate hike in 2026?",
            "threshold on one side only: 25bps",
        ),
        (
            "Will a government shutdown happen in 2026?",
            "Will no government shutdown happen in 2026?",
            "negated on one side only (second says no/not)",
        ),
        ("Will there be a recession in 2027?", "US recession by end of 2027?", None),
    ],
)
def test_incompatibility_reason_explains_the_refusal(a, b, reason):
    assert incompatibility_reason(a, b) == reason
    assert compatible(a, b) is (reason is None)


def test_near_misses_lists_refused_lookalikes_with_reasons():
    k = [
        mk(Venue.KALSHI, "K1", "Will the Fed cut rates by 25bps in December 2026?"),
        mk(Venue.KALSHI, "K2", "Will there be a recession in 2027?"),
    ]
    p = [
        mk(Venue.POLYMARKET, "P1", "Will the Fed cut rates by 50bps in December 2026?"),
        mk(Venue.POLYMARKET, "P2", "US recession by end of 2027?"),
    ]
    misses = near_misses(k, p, min_similarity=0.3)
    assert [(n.kalshi.id, n.polymarket.id) for n in misses] == [("K1", "P1")]
    assert misses[0].reason == "different threshold: 25bps vs 50bps"
    assert [m.kalshi.id for m in match_markets(k, p)] == ["K2"]


# real title pairs from the live board (Nov 2026) that share most words but differ in substance
@pytest.mark.parametrize(
    ("a", "b", "reason"),
    [
        (
            "Ethereum price at Nov 13, 2026 at 5pm EST? ($2,200 or above)",
            "Will the price of Ethereum be above $2,200 on November 9?",
            "different dates: Nov 13 vs Nov 9",
        ),
        (
            "Will the Federal Reserve cut rates by June 30, 2027? (Before July 2027)",
            "Fed rate cut by July 2027 meeting?",
            "different dates: Jun 30 vs July",
        ),
        (
            "Will the Federal Reserve cut rates before 2027? (Cuts)",
            "Fed rate cut by January 2027 meeting?",
            "different dates: before 2027 vs January",
        ),
        (
            "Will the US government be shut down on Dec 12, 2026? (Yes)",
            "Government shutdown by December 12?",
            "different timing: on a date vs by a date",
        ),
        (
            "When will the next U.S. recession start? (Q1 2026)",
            "US recession by end of 2026?",
            "different period: Q1 vs no quarter",
        ),
        (
            "GDP growth in 2026? (0.0% or Below)",
            "Will UK annual GDP growth in 2026 be below 0%?",
            "different place: US (implied) vs UK",
        ),
        (
            "Will South Africa GDP growth rate QoQ for Q3 2026 be above 1.0%?",
            "Will UK GDP growth in Q3 2026 be negative?",
            "different place: South Africa vs UK",
        ),
        (
            "Will Aileen Cannon become the next Justice on the Supreme Court?",
            "Will Aileen Cannon be the next Supreme Court nominee?",
            "different stage: become vs nominee",
        ),
        (
            "Will the Prime Minister of Canada announce lower tariffs on U.S. goods before "
            "Jan 1, 2027?",
            "US-Canada diplomatic agreement to lower tariffs by December 31?",
            "different stage: announce vs agreement",  # before Jan 1 == by Dec 31, so dates agree
        ),
        (
            "Will the Federal Reserve cut rates before 2027? (Cuts)",
            "Fed emergency rate cut before 2027?",
            "narrower on one side: emergency",
        ),
    ],
)
def test_incompatibility_reason_dates_place_stage(a, b, reason):
    assert incompatibility_reason(a, b) == reason


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # same deadline written two ways, same place, same stage: still a pair
        (
            "Will the US government be shut down by Dec 12, 2026?",
            "Government shutdown by December 12?",
        ),
        (
            "Will the Federal Reserve cut rates by December 31, 2026? (Before 2027)",
            "Fed rate cut by December 2026 meeting?",
        ),
        ("Will there be a recession in 2027? (Yes)", "US recession by end of 2027?"),
        (
            "Will the Fed hike rates in 2026?",
            "Will the Federal Reserve raise interest rates in 2026?",
        ),
        ("Will the next Fed chair be confirmed?", "Fed chair confirmed by the Senate?"),
    ],
)
def test_new_rules_keep_genuine_pairs(a, b):
    assert incompatibility_reason(a, b) is None


def test_close_time_rule_is_strict_only_for_short_dated_markets():
    now = datetime(2026, 11, 9, tzinfo=UTC)

    def leg(venue, days):
        return Market(
            venue=venue, id=venue.value, title="t", volume_unit="usd",
            close_time=now + timedelta(days=days),
        )  # fmt: skip

    k, p = Venue.KALSHI, Venue.POLYMARKET
    # closes in 4 days vs 1 day: most of the remaining life apart
    assert close_time_reason(leg(k, 4), leg(p, 1), now) == ("closes 3 days apart with 1 day left")
    # a year-end question where one venue closes three weeks after the other is normal
    assert close_time_reason(leg(k, 53), leg(p, 31), now) is None
    # half a day apart on a short-dated market is fine
    assert close_time_reason(leg(k, 5), leg(p, 5.5), now) is None
    # the long-standing 45-day cap still applies
    assert close_time_reason(leg(k, 300), leg(p, 200), now) == "closes 100 days apart (limit 45)"
