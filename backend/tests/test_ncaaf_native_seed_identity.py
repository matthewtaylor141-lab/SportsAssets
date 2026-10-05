"""NCAAF: A PINNAPI-NATIVE COLLEGE SEED REACHES ITS VENUE CONTRACT (2026-10-05).

THE LOSS. Production's Command Center: NCAAF 58 provider events -> 0
evaluated. Pinnacle names a college team by its SCHOOL only -- PinnAPI-native
discovery matched "Troy" / "Southern Miss" to the venue's `cfb` records by
exact structured identity (tests/test_r30a_pinnapi_native_discovery.py) and
seeds the fixture with those names (pinnapi_discovery.seed_event). The
collector's venue-native resolver then asked the college NICKNAME guard --
"the venue's own nickname must be in the provider's name", built for the
metered provider's "Troy Trojans" -- and refused EVERY native college seed
with VENUE_NATIVE_NICKNAME_DOES_NOT_CONFIRM_THE_TEAM. The metered path's own
names ("Ohio State Buckeyes") never match a PinnAPI fixture exactly, so its
quote is the 15-minute REST payload and stops QUOTE_STALE_ON_ARRIVAL (a
freshness rule this change does not touch). Both paths ended before a
valuation.

THE FIX, AND WHAT IT DOES NOT LOOSEN. On the venue event discovery named
(event_slug given), a provider name whose tokens EQUAL the venue's school
tokens -- the participant record without its nickname -- confirms the team:
two independent exact identities agree. Containment is still refused
("Ohio" never confirms "ohio state"), the one-to-one assignment of BOTH
teams, the start tolerance and the league token still apply, and a metered
name (no discovered event) still needs its nickname.

The venue rows are production's (tests/fixtures/pmus_cfb_catalogue_rows_
2026_10_04.json, research-sql capture); the provider names are the school
renderings Pinnacle's fixtures carry.
"""
from __future__ import annotations

import json
import pathlib

from sportsassets import bettor_venue_native_identity as V

FIX = pathlib.Path(__file__).parent / "fixtures"
LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
NCAAF = "americanfootball_ncaaf"


def _events():
    rows = json.loads((FIX / "pmus_cfb_catalogue_rows_2026_10_04.json")
                      .read_text())["rows"]
    by: dict = {}
    for r in rows:
        by.setdefault(r["event_slug"], []).append(r)
    return by


def _school(rows, intent):
    """Pinnacle's rendering: the school, no nickname ("Troy")."""
    return next(r for r in rows if r["intent"] == intent)["team_name"].title()


def _match(rows, home, away, *, slug, priced="home"):
    return V.match_event(home=home, away=away,
                         commence_epoch=rows[0]["game_start"],
                         family="football", rows=rows, league_tokens=["cfb"],
                         competition=NCAAF, priced=priced, event_slug=slug)


def test_every_production_cfb_event_maps_a_school_only_seed():
    evs = _events()
    assert len(evs) == 3
    for slug, rows in evs.items():
        # the venue title is "AWAY vs. HOME": the LONG row is the away team
        home, away = _school(rows, SHORT), _school(rows, LONG)
        m = _match(rows, home, away, slug=slug)
        assert m["ok"], (slug, m.get("refusal"), m.get("why"))
        assert m["intent"] == SHORT, (slug, m)
        assert m["us_market_slug"] == rows[0]["market_slug"]
        a = _match(rows, home, away, slug=slug, priced="away")
        assert a["ok"] and a["intent"] == LONG, (slug, a)


def test_before_the_fix_this_was_the_refusal_and_without_discovery_it_still_is():
    for slug, rows in _events().items():
        home, away = _school(rows, SHORT), _school(rows, LONG)
        m = _match(rows, home, away, slug=None)
        assert not m["ok"]
        assert m["refusal"] == V.R_NICKNAME, m


def test_containment_never_confirms_on_the_discovered_event():
    """Ohio (Bobcats) against the venue's Ohio State: refused by name even
    on a discovered event; Ohio against the venue's own Ohio maps."""
    kick = 1791331200.0
    rows = []
    for slug, title, sides in (
            ("cfb-ohiost-kentst-2026-10-10", "Ohio State vs. Kent State",
             (("ohio state", "buckeyes", "ohiost", LONG),
              ("kent state", "golden flashes", "kentst", SHORT))),
            ("cfb-ohio-buff-2026-10-10", "Ohio vs. Buffalo",
             (("ohio", "bobcats", "ohio", LONG),
              ("buffalo", "bulls", "buff", SHORT)))):
        for team, nick, abbr, intent in sides:
            rows.append({"identifier": "aec-" + slug,
                         "market_slug": "aec-" + slug, "event_slug": slug,
                         "event_title": title, "question": title,
                         "kind": "side", "side_norm": nick, "intent": intent,
                         "line": "00", "team_name": team,
                         "team_safe_name": team, "team_abbr": abbr,
                         "team_league": "cfb",
                         "sports_type": "football_team_full_game_winner",
                         "game_start": kick})
    bad = V.match_event(home="Kent State", away="Ohio", commence_epoch=kick,
                        family="football", rows=rows, league_tokens=["cfb"],
                        competition=NCAAF,
                        event_slug="cfb-ohiost-kentst-2026-10-10")
    assert not bad["ok"] and bad["refusal"] == V.R_NICKNAME, bad
    good = V.match_event(home="Buffalo", away="Ohio", commence_epoch=kick,
                         family="football", rows=rows, league_tokens=["cfb"],
                         competition=NCAAF,
                         event_slug="cfb-ohio-buff-2026-10-10")
    assert good["ok"] and good["us_market_slug"] == \
        "aec-cfb-ohio-buff-2026-10-10", good


def test_the_guard_reads_exact_school_tokens_only():
    venue = V.team_profile("troy trojans", "football", nickname="trojans")
    exact = V.same_college_team(V.team_profile("Troy", "football"), venue,
                                exact_school_confirms=True)
    assert exact["same"] and exact["confirmed_by"] == \
        "EXACT_SCHOOL_TOKENS_ON_THE_DISCOVERED_EVENT"
    # the same name without discovery: blocked, as before
    plain = V.same_college_team(V.team_profile("Troy", "football"), venue)
    assert not plain["same"] and plain["nickname_blocked"]
    # the metered rendering still confirms through its nickname
    full = V.same_college_team(V.team_profile("Troy Trojans", "football"),
                               venue)
    assert full["same"] and "confirmed_by" not in full
    # a containment is never an equality
    osu = V.team_profile("ohio state buckeyes", "football",
                         nickname="buckeyes")
    cont = V.same_college_team(V.team_profile("Ohio", "football"), osu,
                               exact_school_confirms=True)
    assert not cont["same"]
