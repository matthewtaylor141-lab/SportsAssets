"""P1 FIRST-LOSS CENSUS: THE PROVIDER'S NAMES AGAINST THE FEED'S.

Production (2026-10-06, research-sql runs 37477575060 / 37478502753,
research/p1_first_loss_evidence*.sql): the metered provider renders a
college team SCHOOL + MASCOT ("Troy Trojans") where the PinnAPI feed renders
the SCHOOL ("Troy"), an MLS club with its "FC" ("Vancouver Whitecaps FC" /
"Vancouver Whitecaps"), a country by another official name ("Turkey" /
"Turkiye") and a Serie B club by another of its names ("Operario PR" /
"Operario Ferroviario") -- so the exact folded match found no WS fixture,
the event fell back to the metered quote (15-27 s old on arrival) and was
refused QUOTE_STALE_ON_ARRIVAL, or, with no metered Pinnacle book either,
PINNAPI_PRIMARY_NO_EXACT_FIXTURE.

Pinned here: the canonical tier (pinnapi_names) maps every production pair,
never merges two schools or two clubs, is used only when the exact tier
finds nothing, keeps every other condition of the match (both teams, the
sport, the tolerance, ONE fixture), records its basis on the quote and is
re-applied by validate(); and a fixture the feed does not hold at all is
named PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM only when NO
feed record near the start shares a token with either team.
"""
from __future__ import annotations

import copy
import datetime as _dt

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_names as N
from sportsassets import pinnapi_primary as P
from sportsassets import refusal_taxonomy as RT

T = _dt.datetime(2026, 10, 10, 16, 0, tzinfo=_dt.timezone.utc).timestamp()
SID = {"soccer": 1, "football": 5, "basketball": 3}


def iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat() \
        .replace("+00:00", "Z")


def feed(fixtures, *, sport="football", at=None, markets=False):
    """A FeedCache holding `fixtures` [(id, start, home, away)] of one
    sport, delivered in one prematch snapshot (and, with `markets`, a
    moved money line so the price is change-dated)."""
    sid = SID[sport]
    at = T - 600 if at is None else at
    c = F.FeedCache()
    e = c.new_connection([("prematch", sid)])
    mk = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
          "period": 0, "status": "open",
          "prices": [{"designation": "home", "price": -125},
                     {"designation": "away", "price": 110}]}
    evs = [{"id": fid, "startTime": iso(start), "isLive": False,
            "participants": [{"name": h, "alignment": "home"},
                             {"name": w, "alignment": "away"}],
            "markets": [copy.deepcopy(mk)] if markets else []}
           for fid, start, h, w in fixtures]
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": sid,
             "ts": (at - 60) * 1000, "events": evs}, epoch=e,
            received_ms=(at - 60) * 1000 + 5)
    if markets:
        for fid, *_ in fixtures:
            moved = copy.deepcopy(mk)
            moved["prices"][0]["price"] = -120
            c.apply({"type": "prematch_markets", "matchup_id": fid,
                     "data": [moved], "sport_id": sid, "ts": (at - 2) * 1000},
                    epoch=e, received_ms=(at - 2) * 1000 + 5)
    return c


def ev(home, away, start=T, sport_key=None, **kw):
    out = {"id": "metered-1", "home_team": home, "away_team": away,
           "commence_time": iso(start)}
    if sport_key:
        out["sport_key"] = sport_key
    out.update(kw)
    return out


# ═════════════════════════════════════════════════════════════════════
# 1 · EVERY PRODUCTION PAIR IS ONE CANONICAL NAME
# ═════════════════════════════════════════════════════════════════════

#: (family, sport key, metered rendering, feed rendering): run 37478502753
#: section 1c, both sources' own names of one fixture at one start
PRODUCTION_PAIRS = (
    ("football", "americanfootball_ncaaf", "Troy Trojans", "Troy"),
    ("football", "americanfootball_ncaaf",
     "Southern Mississippi Golden Eagles", "Southern Miss"),
    ("football", "americanfootball_ncaaf", "Arkansas State Red Wolves",
     "Arkansas State"),
    ("football", "americanfootball_ncaaf", "South Alabama Jaguars",
     "South Alabama"),
    ("football", "americanfootball_ncaaf", "UTSA Roadrunners", "UTSA"),
    ("football", "americanfootball_ncaaf", "South Florida Bulls",
     "South Florida"),
    ("football", "americanfootball_ncaaf", "San Jose State Spartans",
     "San Jose State"),
    ("football", "americanfootball_ncaaf", "Wyoming Cowboys", "Wyoming"),
    ("football", "americanfootball_ncaaf", "Utah State Aggies",
     "Utah State"),
    ("football", "americanfootball_ncaaf", "Washington State Cougars",
     "Washington State"),
    ("football", "americanfootball_ncaaf", "Washington Huskies",
     "Washington"),
    ("football", "americanfootball_ncaaf", "Iowa Hawkeyes", "Iowa"),
    ("football", "americanfootball_ncaaf", "BYU Cougars", "BYU"),
    ("football", "americanfootball_ncaaf", "Iowa State Cyclones",
     "Iowa State"),
    ("soccer", "soccer_usa_mls", "Vancouver Whitecaps FC",
     "Vancouver Whitecaps"),
    ("soccer", "soccer_usa_mls", "Chicago Fire", "Chicago Fire"),
    ("soccer", "soccer_uefa_nations_league", "Turkey", "Turkiye"),
    ("soccer", "soccer_uefa_nations_league", "Czech Republic", "Czechia"),
    ("soccer", "soccer_uefa_nations_league", "Bosnia & Herzegovina",
     "Bosnia and Herzegovina"),
    ("soccer", "soccer_brazil_serie_b", "Operario PR",
     "Operario Ferroviario"),
    ("soccer", "soccer_brazil_serie_b", "Athletic Club (MG)",
     "Athletic Club"),
    ("soccer", "soccer_brazil_serie_b", "Botafogo-SP", "Botafogo SP"),
    ("soccer", "soccer_brazil_serie_b", "Cuiabá", "Cuiaba"),
    ("soccer", "soccer_brazil_serie_b", "Goiás", "Goias"),
)


@pytest.mark.parametrize("fam,key,metered,feed_name", PRODUCTION_PAIRS)
def test_every_production_pair_is_one_canonical_name(fam, key, metered,
                                                      feed_name):
    a = N.canonical(metered, fam, key)
    b = N.canonical(feed_name, fam, side="feed")
    assert a["name"] == b["name"], (metered, feed_name, a, b)


def test_two_schools_or_two_clubs_never_become_one():
    c = lambda n, f="football", k=None: N.canonical(n, f, k)["name"]  # noqa
    # school + mascot is the SCHOOL, never a contained shorter school
    assert c("Ohio State Buckeyes") == "ohio state" != c("Ohio Bobcats") \
        == "ohio" == c("Ohio")
    assert c("Miami (OH) RedHawks") == "miami oh" != c("Miami Hurricanes")
    assert c("Miami Ohio") == "miami oh" and c("Miami Florida") == "miami"
    assert c("Washington Huskies") != c("Washington State Cougars")
    assert c("Mississippi State Bulldogs") != c("Ole Miss Rebels") == \
        c("Mississippi")
    assert c("Michigan State Spartans") != c("Michigan Wolverines")
    assert c("Louisiana Ragin Cajuns") != c("Louisiana Tech Bulldogs") != \
        c("UL Monroe Warhawks")
    assert c("Texas A&M Aggies") == "texas am" == c("Texas A&M")
    assert c("Ohio St") == "ohio state"
    # an NFL team is not a college table entry, and keeps its full name
    assert c("Arizona Cardinals") == "arizona cardinals"
    assert c("Louisville Cardinals") == "louisville"
    # soccer: the affiliation token only, never a squad qualifier
    s = lambda n, k=None: N.canonical(n, "soccer", k)["name"]  # noqa
    assert s("Arsenal Women") != s("Arsenal FC")
    assert s("Manchester City") != s("Manchester United")
    assert s("D.C. United") == "dc united" == s("DC United")
    # a competition-scoped rendering is THAT competition's only
    assert s("Athletic Club (MG)") == "athletic club mg"
    assert s("Athletic Club (MG)", "soccer_brazil_serie_b") == \
        "athletic club"
    assert N.canonical("Athletic Club (MG)", "soccer",
                       "soccer_brazil_serie_b", side="feed")["name"] == \
        "athletic club mg"


#: every NCAAF name the metered provider sent in 14 days (run 37478502753
#: section 1a): each is one school, and no two are the same school
METERED_NCAAF_14D = (
    "Air Force Falcons", "Akron Zips", "Alabama Crimson Tide",
    "Appalachian State Mountaineers", "Arizona State Sun Devils",
    "Arizona Wildcats", "Arkansas Razorbacks", "Arkansas State Red Wolves",
    "Army Black Knights", "Ball State Cardinals", "Baylor Bears",
    "Boise State Broncos", "Bowling Green Falcons", "Buffalo Bulls",
    "BYU Cougars", "California Golden Bears", "Central Michigan Chippewas",
    "Charlotte 49ers", "Cincinnati Bearcats", "Clemson Tigers",
    "Coastal Carolina Chanticleers", "Colorado Buffaloes",
    "Colorado State Rams", "Duke Blue Devils", "East Carolina Pirates",
    "Eastern Michigan Eagles", "Florida Atlantic Owls", "Florida Gators",
    "Florida International Panthers", "Florida State Seminoles",
    "Fresno State Bulldogs", "Georgia Bulldogs", "Georgia Southern Eagles",
    "Georgia Tech Yellow Jackets", "Hawaii Rainbow Warriors",
    "Houston Cougars", "Illinois Fighting Illini", "Indiana Hoosiers",
    "Iowa Hawkeyes", "Iowa State Cyclones", "Jacksonville State Gamecocks",
    "James Madison Dukes", "Kansas Jayhawks", "Kansas State Wildcats",
    "Kennesaw State Owls", "Kent State Golden Flashes", "Kentucky Wildcats",
    "Liberty Flames", "Louisiana Ragin Cajuns", "Louisiana Tech Bulldogs",
    "Louisville Cardinals", "LSU Tigers", "Marshall Thundering Herd",
    "Maryland Terrapins", "McNeese State Cowboys", "Memphis Tigers",
    "Miami Hurricanes", "Miami (OH) RedHawks", "Michigan State Spartans",
    "Minnesota Golden Gophers", "Missouri State Bears", "Missouri Tigers",
    "Navy Midshipmen", "NC State Wolfpack", "Nebraska Cornhuskers",
    "Nevada Wolf Pack", "New Mexico State Aggies", "North Carolina Tar Heels",
    "North Dakota State Bison", "Northern Illinois Huskies",
    "North Texas Mean Green", "Northwestern Wildcats",
    "Notre Dame Fighting Irish", "Ohio Bobcats", "Ohio State Buckeyes",
    "Oklahoma Sooners", "Oklahoma State Cowboys", "Old Dominion Monarchs",
    "Ole Miss Rebels", "Oregon Ducks", "Oregon State Beavers",
    "Penn State Nittany Lions", "Pittsburgh Panthers",
    "Purdue Boilermakers", "Rice Owls", "Rutgers Scarlet Knights",
    "Sacramento State Hornets", "Sam Houston State Bearkats",
    "San Diego State Aztecs", "San Jose State Spartans",
    "South Alabama Jaguars", "South Carolina Gamecocks",
    "Southern Mississippi Golden Eagles", "South Florida Bulls",
    "Stanford Cardinal", "Syracuse Orange", "TCU Horned Frogs",
    "Temple Owls", "Tennessee Volunteers", "Texas A&M Aggies",
    "Texas Longhorns", "Texas Southern Tigers", "Texas State Bobcats",
    "Texas Tech Red Raiders", "Toledo Rockets", "Troy Trojans",
    "Tulane Green Wave", "Tulsa Golden Hurricane", "UAB Blazers",
    "UCF Knights", "UCLA Bruins", "UConn Huskies", "UL Monroe Warhawks",
    "UMass Minutemen", "UNLV Rebels", "USC Trojans", "Utah State Aggies",
    "Utah Utes", "UTEP Miners", "UTSA Roadrunners", "Vanderbilt Commodores",
    "Virginia Cavaliers", "Virginia Tech Hokies",
    "Wake Forest Demon Deacons", "Washington Huskies",
    "Washington State Cougars", "Western Kentucky Hilltoppers",
    "Western Michigan Broncos", "West Virginia Mountaineers",
    "Wyoming Cowboys",
)


def test_every_metered_ncaaf_name_is_one_distinct_school():
    assert len(METERED_NCAAF_14D) == 130
    schools = {}
    for n in METERED_NCAAF_14D:
        got = N.canonical(n, "football", "americanfootball_ncaaf")
        assert any(r.startswith("NCAAF_SCHOOL_MASCOT:")
                   for r in got["rules"]), n
        assert got["name"] not in schools, (n, schools.get(got["name"]))
        schools[got["name"]] = n
    # the table holds no two entries for one school
    table = [s for s, _m in N.NCAAF_SCHOOL_MASCOT]
    assert len(table) == len(set(table))
    # a school rendering never lands on a name the table does not hold
    for alias, school in N.NCAAF_SCHOOL_RENDERINGS.items():
        assert school in set(table), (alias, school)


# ═════════════════════════════════════════════════════════════════════
# 2 · THROUGH THE REAL FEED CACHE: THE SAME IDENTITY, ONE MORE SPELLING
# ═════════════════════════════════════════════════════════════════════

def test_a_metered_ncaaf_event_finds_its_feed_fixture_canonically():
    c = feed([(1637330292, T, "Troy", "Southern Miss"),
              (1637330311, T, "Utah State", "Washington State")])
    e = ev("Troy Trojans", "Southern Mississippi Golden Eagles",
           sport_key="americanfootball_ncaaf")
    why = {}
    hit, reason = P.match_event(c, e, "football", explain=why)
    assert reason is None and hit[0] == 1637330292
    # the labels are the EVENT's own names, oriented by the fixture
    assert hit[1] == {"home": "Troy Trojans",
                      "away": "Southern Mississippi Golden Eagles"}
    assert why["name_match"] == P.MATCH_CANONICAL
    assert "NCAAF_SCHOOL_MASCOT:troy trojans" in why["rules"]
    assert why["canonical"] == {"home": "troy",
                                "away": "southern mississippi"}
    # the pass-wide index answers exactly what the scan answers
    idx = P.fixture_index(c)
    assert P.match_event(c, e, "football", index=idx) == (hit, reason)
    # swapped home / away: the same fixture, labels re-oriented
    hit2, _ = P.match_event(c, ev("Southern Mississippi Golden Eagles",
                                  "Troy Trojans"), "football")
    assert hit2[0] == 1637330292
    assert hit2[1] == {"home": "Troy Trojans",
                       "away": "Southern Mississippi Golden Eagles"}


def test_the_exact_tier_wins_and_the_canonical_tier_keeps_every_condition():
    # an exact match is never re-read canonically
    c = feed([(70, T, "Carolina Panthers", "Detroit Lions")])
    why = {}
    hit, reason = P.match_event(c, ev("Carolina Panthers", "Detroit Lions"),
                                "football", explain=why)
    assert reason is None and why["name_match"] == P.MATCH_EXACT
    # outside the start tolerance: no fixture, canonically either
    c = feed([(71, T + P.START_TOLERANCE_S + 60, "Troy", "Southern Miss")])
    hit, reason = P.match_event(c, ev("Troy Trojans",
                                      "Southern Mississippi Golden Eagles"),
                                "football")
    assert hit is None and reason == P.R_NO_EXACT
    # one team only: no fixture
    c = feed([(72, T, "Troy", "Arkansas State")])
    hit, reason = P.match_event(c, ev("Troy Trojans",
                                      "Southern Mississippi Golden Eagles"),
                                "football")
    assert hit is None and reason == P.R_NO_EXACT
    # two feed fixtures carrying the two canonical names: AMBIGUOUS by name
    c = feed([(73, T, "Troy", "Southern Miss"),
              (74, T + 1800, "Troy", "Southern Mississippi")])
    hit, reason = P.match_event(c, ev("Troy Trojans",
                                      "Southern Mississippi Golden Eagles"),
                                "football")
    assert hit is None and reason == "PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS"
    # another sport's fixture of the same names is not this sport's
    c = feed([(75, T, "Troy", "Southern Miss")], sport="basketball")
    hit, reason = P.match_event(c, ev("Troy Trojans",
                                      "Southern Mississippi Golden Eagles"),
                                "football")
    assert hit is None


def test_soccer_affiliation_countries_and_the_scoped_serie_b_renderings():
    c = feed([(81, T, "Chicago Fire", "Vancouver Whitecaps"),
              (82, T, "Italy", "Turkiye"),
              (83, T, "Goias", "Athletic Club"),
              (84, T, "Operario Ferroviario", "Botafogo SP")],
             sport="soccer")
    for e, fid in ((ev("Chicago Fire", "Vancouver Whitecaps FC"), 81),
                   (ev("Italy", "Turkey"), 82),
                   (ev("Goiás", "Athletic Club (MG)",
                       sport_key="soccer_brazil_serie_b"), 83),
                   (ev("Operario PR", "Botafogo-SP",
                       sport_key="soccer_brazil_serie_b"), 84)):
        hit, reason = P.match_event(c, e, "soccer")
        assert reason is None and hit[0] == fid, (e, reason)
    # "Athletic Club (MG)" outside the provider's Serie B key is not the
    # feed's "Athletic Club"
    hit, reason = P.match_event(c, ev("Goiás", "Athletic Club (MG)"),
                                "soccer")
    assert hit is None


# ═════════════════════════════════════════════════════════════════════
# 3 · THE QUOTE CARRIES ITS BASIS; VALIDATE RE-APPLIES IT
# ═════════════════════════════════════════════════════════════════════

def test_select_records_the_basis_and_validate_reapplies_it():
    at = T - 600
    c = feed([(83, T, "Goias", "Athletic Club")], sport="soccer", at=at,
             markets=True)
    # the soccer money line is three-way: add the draw
    q = c.quotes[(83, F.FULL_GAME_MONEYLINE_KEY)]
    assert q is not None
    e = ev("Goiás", "Athletic Club (MG)", sport_key="soccer_brazil_serie_b")
    hit, why = P.match_event(c, e, "soccer", explain=(basis := {}))
    assert why is None and basis["name_match"] == P.MATCH_CANONICAL
    # a reference shaped like select's: validate re-matches with the
    # recorded sport key, so the scoped rendering is re-applied
    ref = {"provider": P.PROVIDER, "runtime_id": "r1",
           "discovery_home": e["home_team"], "discovery_away": e["away_team"],
           "discovery_start": e["commence_time"],
           "discovery_sport_key": "soccer_brazil_serie_b",
           "family": "soccer", "feed_event_id": 83,
           "outcome_labels": dict(hit[1]), "quote_event_id": 83}
    hit2, why2 = P.match_event(c, {
        "home_team": ref["discovery_home"],
        "away_team": ref["discovery_away"],
        "commence_time": ref["discovery_start"],
        "sport_key": ref["discovery_sport_key"]}, "soccer")
    assert why2 is None and hit2[0] == 83
    # without the recorded key the scoped rendering does not apply
    hit3, why3 = P.match_event(c, {
        "home_team": ref["discovery_home"],
        "away_team": ref["discovery_away"],
        "commence_time": ref["discovery_start"]}, "soccer")
    assert hit3 is None


def test_select_stamps_the_fixture_match_on_the_quote():
    import types
    at = T - 600
    c = feed([(1637330292, T, "Troy", "Southern Miss")], at=at, markets=True)
    c.authority = types.SimpleNamespace(granted=True, synced=True,
                                        epoch=c.authority.epoch,
                                        reason=None)
    e = ev("Troy Trojans", "Southern Mississippi Golden Eagles",
           sport_key="americanfootball_ncaaf")
    got = P.select(c, e, None, family="football", sharp_books=set(), at=at,
                   runtime_id="r1")
    assert got is not None, "the WS quote is selected"
    ref = got["reference_input"]
    assert ref["fixture_match"]["name_match"] == P.MATCH_CANONICAL
    assert ref["discovery_sport_key"] == "americanfootball_ncaaf"
    assert ref["feed_event_id"] == 1637330292
    chk = P.validate(c, got, at=at + 1, runtime_id="r1")
    assert chk["ok"] is True, chk


# ═════════════════════════════════════════════════════════════════════
# 4 · A FIXTURE THE FEED DOES NOT HOLD, NAMED ONLY ON THE EVIDENCE
# ═════════════════════════════════════════════════════════════════════

def test_a_fixture_the_feed_does_not_hold_is_named_and_any_doubt_stays_ours():
    # production: Green Bay Packers v Chicago Bears five days out, the feed
    # holding other NFL games only
    c = feed([(90, T - 5 * 86400, "Dallas Cowboys", "Tampa Bay Buccaneers")])
    why = {}
    hit, reason = P.match_event(c, ev("Green Bay Packers", "Chicago Bears"),
                                "football", explain=why)
    assert hit is None and reason == N.R_NOT_IN_FEED
    assert why["absence"]["absent"] is True
    assert why["absence"]["sport_records"] == 1
    # the index path answers the same
    assert P.match_event(c, ev("Green Bay Packers", "Chicago Bears"),
                         "football", index=P.fixture_index(c)) == \
        (None, N.R_NOT_IN_FEED)
    # a record within the wide window naming ONE team: a naming question,
    # ours -- even though it is a different fixture
    c = feed([(91, T + 3600 * 20, "Chicago Bears", "Detroit Lions")])
    assert P.match_event(c, ev("Green Bay Packers", "Chicago Bears"),
                         "football")[1] == P.R_NO_EXACT
    # an acronym counts as a shared name ("CRB" / "Clube de Regatas Brasil")
    c = feed([(92, T, "CRB", "Atletico GO")], sport="soccer")
    assert P.match_event(c, ev("Clube de Regatas Brasil", "Londrina"),
                         "soccer")[1] == P.R_NO_EXACT
    # no record of the sport at all: the scope is in question, ours
    c = feed([(93, T, "Brazil", "Japan")], sport="soccer")
    assert P.match_event(c, ev("Green Bay Packers", "Chicago Bears"),
                         "football")[1] == P.R_NO_EXACT
    # an eviction: the fixture may have been ours to keep
    c = feed([(94, T - 5 * 86400, "Dallas Cowboys", "Tampa Bay Buccaneers")])
    c.counts["events_evicted"] = 1
    assert P.match_event(c, ev("Green Bay Packers", "Chicago Bears"),
                         "football")[1] == P.R_NO_EXACT


def test_the_absence_code_is_external_and_classified_and_staged():
    c = N.R_NOT_IN_FEED
    assert c in ext.PINNAPI_SELECT_REFUSALS
    assert ext.EVALUABILITY_OF[c] == ext.EXTERNAL_DEPENDENCY
    assert ext.EVALUABILITY_OF[P.R_NO_EXACT] == ext.COULD_NOT_EVALUATE
    assert RT.classify(c)["classified"]
    assert ext.STAGE_OF[c] == "1_PROBABILITY"
    from sportsassets import coverage_first_loss as CFL
    assert CFL.classify(c)["class"] == CFL.EXTERNAL
    assert CFL.classify(P.R_NO_EXACT)["class"] == RT.SOFTWARE
