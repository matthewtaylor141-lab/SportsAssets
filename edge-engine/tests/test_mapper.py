from edge.venues.mapper import (VenueMarket, match_event, parse_matchup,
                                norm_team, team_score)


def test_norm_strips_noise_and_accents():
    assert norm_team("Atlético de Madrid CF") == norm_team("atletico madrid")
    assert team_score("Bayern München", "Bayern Munchen") == 1.0


def test_containment_scores_high():
    assert team_score("Arsenal", "Arsenal London") >= 0.9


def test_match_event_straight_and_flipped():
    mkt = VenueMarket(
        market_id="c1", title="Palmeiras vs. Corinthians", league_code="bra",
        outcome_tokens={"Palmeiras": "t1", "Corinthians": "t2"},
    )
    m = match_event("Corinthians", "Palmeiras", "bra", [mkt])
    assert m is not None and m.score >= 0.85
    assert m.home_outcome == "Corinthians" and m.away_outcome == "Palmeiras"


def test_league_mismatch_rejected():
    mkt = VenueMarket("c1", "Arsenal vs. Chelsea", "epl", {"Arsenal": "t1", "Chelsea": "t2"})
    assert match_event("Arsenal", "Chelsea", "lal", [mkt]) is None


def test_low_similarity_rejected():
    mkt = VenueMarket("c1", "Lakers vs. Celtics", "nba", {"Lakers": "t1", "Celtics": "t2"})
    assert match_event("Arsenal", "Chelsea", "nba", [mkt]) is None


# ── series-word titles: "Eagles vs Cowboys Spread" must still map ────────
#
# The spread/total series name their EVENTS with the series word appended to
# the matchup. Left in the away team's tokens it dilutes similarity against
# the feed's full names below the candidate floor — the market discovers but
# never maps, and the new surface stays zero silently (audit 2026-08-05).

def test_parse_matchup_strips_trailing_series_words():
    assert parse_matchup("Eagles vs Cowboys Spread") == ("Eagles", "Cowboys")
    assert parse_matchup("Eagles vs Cowboys Total") == ("Eagles", "Cowboys")
    assert parse_matchup("Lakers @ Celtics Total Points") == ("Lakers", "Celtics")
    assert parse_matchup("Arsenal vs. Chelsea Winner") == ("Arsenal", "Chelsea")
    # ...but only TRAILING qualifiers: mid-name words survive.
    assert parse_matchup("Palmeiras vs Corinthians") == ("Palmeiras", "Corinthians")


def test_a_total_series_title_maps_against_full_feed_names():
    mkt = VenueMarket("c1", "Eagles vs Cowboys Total", "nfl",
                      {"Over 45.5": "t1", "Under 45.5": "t2"})
    m = match_event("Philadelphia Eagles", "Dallas Cowboys", "nfl", [mkt])
    assert m is not None and m.tradeable, \
        "the series word must not dilute the away team below the floor"


def test_a_spread_series_title_maps_against_full_feed_names():
    mkt = VenueMarket("c1", "Lakers vs Celtics Spread", "nba",
                      {"Lakers -3.5": "t1", "Celtics +3.5": "t2"})
    m = match_event("Los Angeles Lakers", "Boston Celtics", "nba", [mkt])
    assert m is not None and m.tradeable
