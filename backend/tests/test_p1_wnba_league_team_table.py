"""P1 FIRST-LOSS CENSUS: THE WNBA, ADMITTED THROUGH ITS OWN TEAM TABLE.

The venue's WNBA team record is the CITY alone ("Las Vegas", "Golden State",
"New York", "Atlanta": tests/fixtures/
pmus_basketball_hockey_winner_listings_2026_10_06.json, aec-wnba-lv-gsv /
aec-wnba-ny-atl, `marketSides[].team.name`) -- places the NBA's own
fixtures carry in the same sport -- so a provider "Atlanta Hawks" contained
the WNBA's "atlanta" exactly as "Atlanta Dream" does, and the league was
refused VENUE_NATIVE_LEAGUE_NOT_ADMITTED. Its wording (the full game,
overtime included, the last-fair-market-price postponement clause) was
already captured.

Pinned here: inside the league the city is ONE team, and the venue states
its nickname on the same side ("Aces", "Valkyries", "Liberty", "Dream"), so
a WNBA venue participant is the league's own (city, nickname) pair, read
only for an event of the league's own token. A WNBA event can then match
only the WNBA fixture: the NBA's "Atlanta Hawks" / "Golden State Warriors"
contain nothing of "atlanta dream" / "golden state valkyries", on the
venue-native resolver AND the PinnAPI-native discovery (whose controlled
fallback is never asked for a city-only record). A city the table does not
name, or a winner row whose own nickname disagrees, refuses
VENUE_NATIVE_LEAGUE_TEAM_NOT_IN_THE_LEAGUE_TABLE.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_venue_native_identity as V
from sportsassets import bettor_venue_settlement as vset
from sportsassets import pinnapi_census as CEN
from sportsassets import pinnapi_discovery as PD
from sportsassets import pinnapi_feed as F
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB

FIX = Path(__file__).parent / "fixtures"
ALL = json.loads((FIX / "pmus_basketball_hockey_winner_listings_2026_10_06"
                  ".json").read_text())
WNBA = [m for m in ALL["markets"] if m["slug"].split("-")[1] == "wnba"]
TYPE = "basketball_team_full_game_winner"
LONG, SHORT = V.LONG, V.SHORT


def _start(m):
    return _dt.datetime.fromisoformat(
        m["gameStartTime"].replace("Z", "+00:00")).timestamp()


def _rows(m):
    """The listing's two sides as us_premap rows (team_name = the venue's
    city record, side_norm = its nickname, as for every aec side)."""
    slug = m["slug"][len("aec-"):]
    out = []
    for s in m["marketSides"]:
        out.append({"market_slug": m["slug"], "event_slug": slug,
                    "event_title": m["question"], "question": m["question"],
                    "kind": "side", "sports_type": TYPE,
                    "team_abbr": s["team"]["abbreviation"],
                    "team_name": s["team"]["name"].lower(),
                    "side_norm": s["description"].lower(),
                    "intent": LONG if s["long"] else SHORT,
                    "game_start": m["gameStartTime"]})
    return out


def _match(rows, home, away, start, *, priced="home", league_tokens=("wnba",),
           event_slug=None):
    return V.match_event(home=home, away=away, commence_epoch=start + 60,
                         family="basketball", rows=rows,
                         competition="pinnapi_basketball",
                         league_tokens=league_tokens, priced=priced,
                         event_slug=event_slug)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE TABLE, AGAINST THE VENUE'S OWN LISTINGS
# ═════════════════════════════════════════════════════════════════════

def test_the_table_is_one_team_per_city_and_agrees_with_every_listing():
    t = V.WNBA_TEAMS
    assert len(t) == 15
    assert len(set(t.values())) == len(t)            # one nickname per city
    assert V.LEAGUE_TEAM_TABLES == {"wnba": t}
    assert len(WNBA) == 2
    for m in WNBA:
        assert "WNBA" in m["description"]
        for s in m["marketSides"]:
            assert s["team"]["league"] == "wnba"
            assert t[s["team"]["name"].lower()] == s["description"].lower()
    # the four 2026 playoff teams seen in production (run 37478502753)
    for city, nick in (("atlanta", "dream"), ("golden state", "valkyries"),
                       ("las vegas", "aces"), ("new york", "liberty")):
        assert V.league_participant(city, "wnba") == ("%s %s" % (city, nick),
                                                       None)
    # never another league's table, never a bare city
    assert V.league_participant("atlanta", "nba")[0] is None
    assert V.league_participant("springfield", "wnba")[0] is None
    assert V.league_participant("atlanta", "wnba", "hawks")[0] is None
    assert V.league_participant("atlanta", "wnba", "yes")[0] == \
        "atlanta dream"


def test_the_league_is_admitted_on_its_captured_wording():
    assert "wnba" in V.ADMITTED_WINNER_LEAGUES["basketball"]
    # PIN MOVED (2026-10-08): the only leagues listed and not read are the
    # BSKT Cup boards of the second capture, by their different wording
    # (test_ident_league_capture_2026_10_08); never the WNBA
    assert set(V.LEAGUES_NOT_READ) == {"bsktcin", "bsktcua"}
    assert "wnba" not in V.LEAGUES_NOT_READ
    assert devig.expected_outcomes("basketball", "h2h", league="wnba") == 2
    for m in WNBA:
        book = PB.book_grading_period("basketball", "wnba")
        venue = PB.venue_grading_period("basketball", m["description"],
                                        "wnba")
        assert venue["refusal"] is None, (m["slug"], venue)
        assert venue["period"] == book["period"] == PB.GP_BASKETBALL_NBA
        a = vset.attest(sport_family="basketball", market="h2h",
                        venue_evidence={"rules_text": m["description"]},
                        book_evidence={"outcome_names": ["H", "A"]},
                        observed_at=1.0)
        assert a["rules"]["overtime"]["established"] is True
        assert a["unmet"] == [vset.R_VOID_CONFLICTS]
    row = {"identifier": "aec-wnba-ny-atl-2026-10-07", "team_league": "wnba",
           "side_norm": "liberty", "question": "Who will win?"}
    assert CEN.family_of("side", None, TYPE, row) == ("MONEYLINE", True,
                                                      None)


# ═════════════════════════════════════════════════════════════════════
# 2 · THE VENUE-NATIVE RESOLVER: A WNBA EVENT MATCHES ONLY WNBA TEAMS
# ═════════════════════════════════════════════════════════════════════

def test_each_listed_wnba_game_maps_both_outcomes_to_its_own_contract():
    for m, (home, away) in zip(
            sorted(WNBA, key=lambda x: x["slug"]),
            (("Golden State Valkyries", "Las Vegas Aces"),
             ("Atlanta Dream", "New York Liberty"))):
        rows = _rows(m)
        got = {}
        for priced in ("home", "away"):
            r = _match(rows, home, away, _start(m), priced=priced)
            assert r["ok"], (m["slug"], priced, r.get("refusal"), r.get("why"))
            assert r["venue_league"] == "wnba"
            assert r["us_market_slug"] == m["slug"]
            got[priced] = r["intent"]
        assert set(got.values()) == {LONG, SHORT}


def test_the_nba_never_lands_on_a_wnba_event_and_the_wnba_never_on_the_nba():
    m = next(x for x in WNBA if x["slug"].startswith("aec-wnba-lv-gsv"))
    rows = _rows(m)
    # the NBA's Golden State Warriors contain the WNBA city, not its team
    r = _match(rows, "Golden State Warriors", "Las Vegas Aces", _start(m),
               league_tokens=None)
    assert r["ok"] is False and r["refusal"] == V.R_ONE_TEAM_ONLY
    r = _match(rows, "Golden State Warriors", "Los Angeles Lakers",
               _start(m), league_tokens=None)
    assert r["ok"] is False and r["refusal"] == V.R_NO_EVENT
    # an NBA event (team record = the nickname, safe name = the place) is
    # never the WNBA fixture of the same city
    nba = [{"market_slug": "aec-nba-atl-ind-2026-10-10",
            "event_slug": "nba-atl-ind-2026-10-10", "kind": "side",
            "event_title": "Hawks vs. Pacers", "question": "Who will win?",
            "sports_type": TYPE, "team_abbr": a, "team_name": n,
            "side_norm": n, "intent": it, "game_start": m["gameStartTime"]}
           for a, n, it in (("atl", "hawks", LONG), ("ind", "pacers",
                                                      SHORT))]
    r = _match(nba, "Atlanta Dream", "Indiana Fever", _start(m),
               league_tokens=None)
    assert r["ok"] is False


def test_a_city_or_nickname_the_table_does_not_establish_refuses_by_name():
    m = WNBA[0]
    start = _start(m)
    rows = _rows(m)
    unknown = [dict(r, team_name="springfield") if i == 0 else r
               for i, r in enumerate(rows)]
    r = _match(unknown, "Springfield Stars", "Las Vegas Aces", start)
    assert r["ok"] is False and r["refusal"] == V.R_LEAGUE_TEAM
    # the venue's own nickname disagreeing with the table: no participant
    clash = [dict(r, side_norm="warriors") if i == 0 else r
             for i, r in enumerate(rows)]
    slug = m["slug"][len("aec-"):]
    r = _match(clash, "Golden State Valkyries", "Las Vegas Aces", start,
               event_slug=slug)
    assert r["ok"] is False and r["refusal"] == V.R_LEAGUE_TEAM
    assert V.R_LEAGUE_TEAM in V.REFUSALS
    assert V.R_LEAGUE_TEAM in V.EVENT_LEVEL_REFUSALS
    assert RT.classify(V.R_LEAGUE_TEAM)["classified"]
    assert ext.STAGE_OF[V.R_LEAGUE_TEAM] == "3_IDENTITY"
    assert ext.EVALUABILITY_OF[V.R_LEAGUE_TEAM] == ext.COULD_NOT_EVALUATE


# ═════════════════════════════════════════════════════════════════════
# 3 · THE PINNAPI-NATIVE DISCOVERY: THE TABLE RENDERING, NO FALLBACK
# ═════════════════════════════════════════════════════════════════════

def _feed(*recs):
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 3)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 3,
             "ts": 1_000, "events": list(recs)}, epoch=ep, received_ms=1_000)
    return c


def _rec(eid, home, away, start, league):
    return {"id": eid, "type": "matchup", "startTime": start,
            "isLive": False, "units": "Regular",
            "league": {"id": 1, "name": league},
            "participants": [{"name": home, "alignment": "home"},
                             {"name": away, "alignment": "away"}],
            "markets": [{"key": "s;0;m", "type": "moneyline", "period": 0,
                         "status": "open",
                         "prices": [{"designation": "home", "price": -150},
                                    {"designation": "away", "price": 130}]}]}


def _vrows(m):
    slug = m["slug"][len("aec-"):]
    return [{"event_slug": slug, "team_name": s["team"]["name"].lower(),
             "team_safe_name": s["team"]["safeName"].lower(),
             "team_abbr": s["team"]["abbreviation"], "team_id": None,
             "team_league": "wnba", "sports_type": TYPE,
             "game_start": _start(m), "rows": 1} for s in m["marketSides"]]


def test_discovery_matches_the_wnba_fixture_exactly_and_never_the_nba_one():
    m = next(x for x in WNBA if x["slug"].startswith("aec-wnba-lv-gsv"))
    start = m["gameStartTime"]
    # the WNBA game and an NBA game of the same city at the same instant
    c = _feed(_rec(1, "Golden State Valkyries", "Las Vegas Aces", start,
                   "WNBA"),
              _rec(2, "Golden State Warriors", "Los Angeles Lakers", start,
                   "NBA"))
    out = PD.discover(c.events, _vrows(m), sport_ids=[3])
    by = {r["fixture_id"]: r for r in out["receipts"]}
    assert by[1]["state"] == PD.MATCHED
    assert by[1]["matched_by"] == PD.MATCHED_BY_EXACT
    assert by[1]["venue_event_slug"] == m["slug"][len("aec-"):]
    assert by[2]["state"] != PD.MATCHED
    (seed,) = out["seeds"]
    assert seed["id"] == "pinnapi:1"
    # the city alone is no longer a rendering of the WNBA record
    ev = PD.venue_events(_vrows(m))[3][0]
    for rec in ev["participants"]:
        assert rec["no_fallback"] is True
        assert len(rec["exact"]) == 1 and rec["exact"][0].split()[-1] in \
            set(V.WNBA_TEAMS.values())
    assert not ev["problems"]


def test_discovery_names_a_city_the_table_does_not_hold():
    m = next(x for x in WNBA if x["slug"].startswith("aec-wnba-lv-gsv"))
    rows = _vrows(m)
    rows[0] = dict(rows[0], team_name="springfield")
    ev = PD.venue_events(rows)[3][0]
    assert any(p.startswith(V.R_LEAGUE_TEAM) for p in ev["problems"])
