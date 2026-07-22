from edge.venues.mapper import VenueMarket, match_event, norm_team, team_score


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
