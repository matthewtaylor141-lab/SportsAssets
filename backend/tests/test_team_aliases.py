"""Desk search must speak fan (owner report 2026-08-07: 'Braves' found
nothing because Kalshi titles games by city)."""

from sportsassets.team_aliases import matches, terms_of


def test_nickname_finds_the_city_titled_game():
    assert matches("braves", ["Atlanta at Philadelphia Winner?", "ATL"])
    assert matches("yankees", ["New York at Boston", ""])
    assert matches("aces", ["Las Vegas at Seattle", None])


def test_city_still_matches_directly():
    assert matches("atlanta", ["Atlanta at Philadelphia Winner?"])


def test_multiword_nicknames_are_one_term():
    assert matches("red sox", ["Boston at Baltimore"])
    assert matches("blue jays", ["Toronto at Tampa Bay"])
    ts = terms_of("red sox win")
    assert any("boston" in s for s in ts)


def test_bet_type_words_are_noise():
    assert matches("braves ml", ["Atlanta at Philadelphia Winner?"])
    assert matches("yankees moneyline", ["New York at Boston"])


def test_ambiguous_nicknames_match_every_owner():
    assert matches("cardinals", ["St. Louis at Chicago"])
    assert matches("cardinals", ["Arizona at Seattle"])
    assert matches("rangers", ["Texas at Houston"])
    assert matches("rangers", ["New York at Ottawa"])


def test_unrelated_query_still_fails():
    assert not matches("braves", ["Boston at Baltimore"])
    assert not matches("yankees dodgers", ["New York at Boston"])
