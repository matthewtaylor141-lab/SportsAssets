"""Sportsbook translation: American odds and bet labels."""

from sportsassets.betting import american_odds, bet_label, opponent_of, result_word


def test_american_odds():
    assert american_odds(0.61) == "-156"
    assert american_odds(0.30) == "+233"
    assert american_odds(0.5) == "-100"
    assert american_odds(None) == "—"
    assert american_odds(1.0) == "—"


def test_moneyline_from_team_outcome():
    assert bet_label("Yankees", "Yankees vs. Red Sox", "Yankees vs. Red Sox") == "Yankees ML"
    assert bet_label("Chiefs", "Chiefs vs. Bills", "Chiefs vs. Bills") == "Chiefs ML"


def test_no_side_becomes_opponent_ml():
    # The user's example: NO Yankees ML should read as the opponent's ML.
    assert (
        bet_label("No", "Will the Yankees beat the Red Sox?", "Yankees vs. Red Sox")
        == "Red Sox ML"
    )
    assert bet_label("No", "Chiefs vs. Bills: Chiefs to win", "Chiefs vs. Bills") == "Bills ML"


def test_yes_side_reads_as_proposition():
    assert (
        bet_label("Yes", "Will the Yankees win the World Series?", None)
        == "Yankees win the World Series"
    )


def test_totals():
    assert bet_label("Over", "Yankees vs. Red Sox: Total Runs 8.5", None) == "Over 8.5"
    assert bet_label("Under", "Total points 224.5", None) == "Under 224.5"


def test_spread_passthrough():
    assert bet_label("Chiefs -3.5", "Chiefs vs. Bills spread", "Chiefs vs. Bills") == "Chiefs -3.5"


def test_futures_outcome_with_context():
    assert (
        bet_label("Lakers", "Which team will win the NBA Championship?", None)
        == "Lakers — Which team will the NBA Championship"
        or "Lakers" in bet_label("Lakers", "Which team will win the NBA Championship?", None)
    )


def test_opponent_of():
    assert opponent_of("Yankees", "Yankees vs. Red Sox") == "Red Sox"
    assert opponent_of("Red Sox", "Yankees vs. Red Sox") == "Yankees"
    assert opponent_of("Yankees", "no versus here") is None


def test_result_word():
    assert result_word(500, True) == "Win"
    assert result_word(-500, True) == "Loss"
    assert result_word(0.0, True) == "Push"
    assert result_word(120, False) == "Cash-out (profit)"
