"""Deterministic sport classification."""

from sportsassets.sports import classify, is_sport


def test_tag_classification():
    assert classify(["NBA"], "", "") == "NBA"
    assert classify(["Sports", "NFL"], "", "") == "NFL"
    assert classify(["Champions League"], "", "") == "Soccer"
    assert classify(["UFC"], "", "") == "MMA"
    assert classify(["PGA Tour", "Golf"], "", "") == "Golf"


def test_slug_and_title_fallback():
    assert classify([], "nfl-chiefs-bills-2026-01-25", "") == "NFL"
    assert classify([], "", "Will the Chiefs win Super Bowl LXI?") == "NFL"
    assert classify([], "epl-arsenal-chelsea", "") == "Soccer"
    assert classify([], "", "Wimbledon men's final winner") == "Tennis"


def test_generic_sports_bucket():
    assert classify(["Sports", "Cricket"], "", "") == "Other-Sports"
    assert classify(["Sports"], "", "") == "Other-Sports"


def test_non_sports():
    assert classify(["Politics"], "us-president-2028", "Who wins the 2028 election?") == "Non-Sports"
    assert not is_sport("Non-Sports")
    assert not is_sport("unclassified")
    assert is_sport("NBA")


def test_deterministic_and_rerunnable():
    args = (["Sports", "NBA", "Playoffs"], "nba-finals-2026", "NBA Finals winner")
    assert classify(*args) == classify(*args) == "NBA"


def test_priority_league_over_generic():
    # A market tagged both Sports and MLB must land in MLB, not Other-Sports.
    assert classify(["Sports", "MLB"], "", "") == "MLB"


def test_slug_prefix_classification():
    # No tags, opaque titles — the league lives in the slug prefix.
    assert classify([], "", "Hamburg European Open: Korpatsch vs Bondar",
                    event_slug="atp-hamburg-2026") == "Tennis"
    assert classify([], "wta-prague-open-r1", "Aksu vs Snigur") == "Tennis"
    assert classify([], "mlb-nyy-bos-2026-07-22", "Yankees vs. Red Sox") == "MLB"
    assert classify([], "", "Will CF Cruz Azul win on 2026-07-21?",
                    event_slug="ligamx-cruz-azul-2026") == "Soccer"
    assert classify([], "cfb-osu-mich", "Ohio State vs Michigan") == "NFL"


def test_recovered_soccer_slug_codes():
    assert classify([], "bra-cor-pal-2026-07-20", "Corinthians vs Palmeiras") == "Soccer"
    assert classify([], "lal-rma-bar-2026-03-01", "Real Madrid vs Barcelona") == "Soccer"
    assert classify([], "fifwc-arg-fra-final", "Argentina vs France") == "Soccer"
    assert classify([], "kbo-doosan-lg", "Doosan vs LG") == "MLB"
    assert classify([], "", "Will the match end in a draw?", event_slug="") == "Soccer"
