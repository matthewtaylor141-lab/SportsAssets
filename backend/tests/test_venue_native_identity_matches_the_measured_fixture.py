"""THE VENUE-NATIVE IDENTITY PATH, PINNED ON THE REAL 2026-09-29 CAPTURE.

`tests/fixtures/venue_native_2026-09-29.json` is the verbatim production
capture (research-sql run 36638975693) of the 49 provider events the entry lane
saw in its latest cycle and the US venue's winner contracts in the window,
trimmed deterministically -- the rule is stated in the file's own `trim` field:
venue rows within one day of some provider event (which, on this capture,
removed nothing).

WHAT IS PINNED, AND HOW IT WAS CHECKED. `match_event` is run over EVERY
provider event and the full outcome table is pinned below: the mapped contract
and its intent, or the named refusal. Each mapping was inspected by eye against
the venue rows before it was pinned -- the fixture, the date, the two teams, and
that the row chosen is the HOME team's own row. The two events production
already mapped through the global path (HOU-CWS, ATL-PHI) are cross-checked
against the venue slug production resolved for them, and agree.

WHAT IS SUBSTITUTED IN THE CYCLE TESTS, NAMED: the odds provider and the venue
at their transport boundaries, the league schedule (empty), and -- in the one
test that says so -- a book-currency reading the venue does not document
(production refuses without it: VENUE_BOOK_CURRENCY_NOT_ESTABLISHED). Every
provider PRICE in those tests is SYNTHETIC TEST EVIDENCE; the team names, the
commence time and the venue rows of the trace test are the real capture. No
submission switch is touched anywhere in this file.
"""

from __future__ import annotations

import ast
import datetime as _dt
import inspect
import json
import os
import pathlib
import textwrap
import time

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_venue_mapping as vmap
from sportsassets import bettor_venue_native_identity as V
from sportsassets import bettor_venue_realism as vreal
from sportsassets.workers import ext_pinnacle_loop as loop

FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "venue_native_2026-09-29.json")
DATA = json.loads(FIXTURE.read_text())
ROWS = DATA["venue_winner_rows"]
EVENTS = DATA["provider_events"]

LONG, SHORT = V.LONG, V.SHORT


def _ep(iso):
    return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _match(e, rows=ROWS, **kw):
    args = dict(home=e["home"], away=e["away"],
                commence_epoch=_ep(e["commence_time"]), family=e["family"],
                rows=rows, competition=e["sport_key"])
    args.update(kw)
    return V.match_event(**args)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE FULL OUTCOME TABLE, EVERY PROVIDER EVENT IN THE CAPTURE
# ═════════════════════════════════════════════════════════════════════

#: provider_event_id -> (fixture, expected). MAPS carries the venue contract
#: and the intent that buys the HOME team; REFUSED carries the named refusal.
#: Inspected by eye row by row before pinning (see the module docstring). The
#: soccer contracts all end in the HOME team's own venue code and are bought
#: LONG; the four MLB contracts are the single two-way contract whose SHORT row
#: is the provider's home team (the venue lists the visitor as LONG).
EXPECTED = {
    '3a08d35ecf12b54dc25f5912ccecf222': ('Houston Astros v Chicago White Sox', ("MAPS", 'aec-mlb-cws-hou-2026-09-29', 'SHORT')),
    '955583952ce8b24ce50e44881e5f1023': ('Botafogo-SP v Ponte Preta', ("MAPS", 'atc-brb-bot-pop-2026-09-29-bot', 'LONG')),
    '4cb76cceb16eb603c683038d132f4642': ('New York Yankees v Boston Red Sox', ("MAPS", 'aec-mlb-bos-nyy-2026-09-29', 'SHORT')),
    '98adc841e3aababc93878e3cf743a2d4': ('San Diego Padres v Chicago Cubs', ("MAPS", 'aec-mlb-chc-sd-2026-09-29', 'SHORT')),
    '2a39377453f4d72b2f9a8851e2fa75ff': ('BK Hacken v Juventus', ("MAPS", 'atc-uwcl-bhf-juv-2026-09-30-bhf', 'LONG')),
    '6eb5303534d0b64ca7f11d09bd3d5e36': ('Paris FC v Arsenal', ("MAPS", 'atc-uwcl-pfc-ars-2026-09-30-pfc', 'LONG')),
    'd15a54cd0e08b8b028fc493fd76fcf26': ('AS Roma v Barcelona', ("MAPS", 'atc-uwcl-asr-fcb-2026-09-30-asr', 'LONG')),
    '9e23239e0075a017225697fa3bcd88b3': ('Atlanta Braves v Philadelphia Phillies', ("MAPS", 'aec-mlb-phi-atl-2026-09-30', 'SHORT')),
    '6380fc2b450afd4d9c580f1ef483bca1': ('Benfica v Bayern Munich', ("REFUSED", 'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM')),
    'c72252d0582902aebb8ac6cda65a9978': ('Lyon v Chelsea', ("REFUSED", 'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM')),
    '0fc933670b2cf487a5ac2878f500492f': ('Azerbaijan v Liechtenstein', ("MAPS", 'atc-unl-aze-lie-2026-10-01-aze', 'LONG')),
    '569ba3f14347105c50c8164a0ecfe28e': ('HB Køge v Servette FC', ("MAPS", 'atc-uwcl-hkg-ser-2026-10-01-hkg', 'LONG')),
    '695a3929e6554772a974fa98fecb48f0': ('Austria Wien v Inter Milan', ("REFUSED", 'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM')),
    '0db832b7346f766521d9e67cd5ab1687': ('Republic of Ireland v Austria', ("MAPS", 'atc-unl-irl-aut-2026-10-01-irl', 'LONG')),
    '16d310e835713f3457023deb98fb9f74': ('Wales v Norway', ("MAPS", 'atc-unl-wal-nor-2026-10-01-wal', 'LONG')),
    '546150d4747a3a5108c7e9860426bf36': ('Germany v Serbia', ("MAPS", 'atc-unl-ger-srb-2026-10-01-ger', 'LONG')),
    '6321b7d465a21274f0da7a5e50dbf5ef': ('Greece v Netherlands', ("MAPS", 'atc-unl-gre-ned-2026-10-01-gre', 'LONG')),
    'a2b54d223ce491ac52af34a462de0140': ('Malta v Gibraltar', ("MAPS", 'atc-unl-mlt-gib-2026-10-01-mlt', 'LONG')),
    'adc5517ae91bb43f3406d577b489abc0': ('Israel v Kosovo', ("MAPS", 'atc-unl-isr-kos-2026-10-01-isr', 'LONG')),
    'd4cee9aa708870095e23599afb35bce0': ('Denmark v Portugal', ("MAPS", 'atc-unl-den-por-2026-10-01-den', 'LONG')),
    '1ec1e91715390c359da942323953f11a': ('Paris Saint Germain v Leuven', ("MAPS", 'atc-uwcl-psg-ohl-2026-10-01-psg', 'LONG')),
    '7230f1ccede51bbb72f0c6cada82dec8': ('Manchester City v Real Madrid', ("MAPS", 'atc-uwcl-mci-rma-2026-10-01-mci', 'LONG')),
    'e213f5e85eeab702dea9daa829698b6d': ('Athletic Club (MG) v Sport Recife', ("REFUSED", 'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM')),
    '2a42fc2dbde835ff11aa67b6f47c7fe8': ('Grêmio Novorizontino v Goiás', ("MAPS", 'atc-brb-nov-goi-2026-10-01-nov', 'LONG')),
    '7db202b104fa0b2c3efc16c9ab45dc1e': ('Kazakhstan v Moldova', ("MAPS", 'atc-unl-kaz-mda-2026-10-02-kaz', 'LONG')),
    '2752b34190caefe2813fa470ff2c01d4': ('Cyprus v Armenia', ("MAPS", 'atc-unl-cyp-arm-2026-10-02-cyp', 'LONG')),
    '32c438139763280b8efb86423e5b911e': ('Latvia v Montenegro', ("MAPS", 'atc-unl-lat-mne-2026-10-02-lat', 'LONG')),
    '429e47b7c49fba99d5cbcba70b9c48c5': ('Belgium v Turkey', ("REFUSED", 'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM')),
    '4985fdd6c7e6801fcca1f9f1e78aed66': ('Ukraine v Northern Ireland', ("MAPS", 'atc-unl-ukr-nir-2026-10-02-ukr', 'LONG')),
    'a7769e85ee3c914abe40a277de800869': ('Faroe Islands v Slovakia', ("MAPS", 'atc-unl-fro-svk-2026-10-02-fro', 'LONG')),
    'aa65ea35f0aa1c0bb87cbcb9e5341b5b': ('Hungary v Georgia', ("MAPS", 'atc-unl-hun-geo-2026-10-02-hun', 'LONG')),
    'b575f66a4bee31eb4f46cfbb2f3d7182': ('Bosnia & Herzegovina v Sweden', ("MAPS", 'atc-unl-bih-swe-2026-10-02-bih', 'LONG')),
    'e5ef3c5aba98aed80abcb9eb0d592bff': ('France v Italy', ("MAPS", 'atc-unl-fra-ita-2026-10-02-fra', 'LONG')),
    'fdb1dbf27fe19575c7ce64fdfd4bcaa9': ('Poland v Romania', ("MAPS", 'atc-unl-pol-rou-2026-10-02-pol', 'LONG')),
    '93704173a25de24b2fbe254ba32f3a3a': ('São Bernardo v Clube de Regatas Brasil', ("MAPS", 'atc-brb-ber-crb-2026-10-02-ber', 'LONG')),
    '479fa03024830eea649bf348b9a66f2e': ('Londrina v Criciuma', ("MAPS", 'atc-brb-lon-cri-2026-10-02-lon', 'LONG')),
    '8ad2d54a071a210a01483dceac45b888': ('Juventude v Operario PR', ("REFUSED", 'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM')),
    '8bf10103e8eea18dc1511825ec1240ab': ('Fortaleza v Nautico PE', ("REFUSED", 'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM')),
    '927cffe5b9e767db9ea09629ead4f944': ('Avai v Ceará', ("MAPS", 'atc-brb-ava-csc-2026-10-03-ava', 'LONG')),
    'ead81aac8131ba7e2f42e6989f4ba3c2': ('Croatia v England', ("MAPS", 'atc-unl-cro-eng-2026-10-03-cro', 'LONG')),
    'a1b75fa94c18a201e6feec4cd04a2d56': ('Spain v Czech Republic', ("REFUSED", 'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM')),
    'ac6c8a4fa9f7c3f6caf15b1e3b5c77d4': ('Atletico Goianiense v América Mineiro', ("MAPS", 'atc-brb-acg-afc-2026-10-03-acg', 'LONG')),
    '650866b10c18149fc1b528c4f2e263a5': ('Cuiabá v Ponte Preta', ("MAPS", 'atc-brb-cui-pop-2026-10-03-cui', 'LONG')),
    'b4d5817327ab9a1e854c93056604843f': ('Botafogo-SP v Vila Nova', ("MAPS", 'atc-brb-bot-vln-2026-10-03-bot', 'LONG')),
    '18c29d24c37e4fa9c5c2a12218c87944': ('Wales v Denmark', ("REFUSED", 'NO_VENUE_NATIVE_EVENT_FOR_FIXTURE')),
    '1dc55d09f236eff7e5bc8e754d23e144': ('Republic of Ireland v Israel', ("REFUSED", 'NO_VENUE_NATIVE_EVENT_FOR_FIXTURE')),
    '4a0c0e7aa93708ae03af18db691f750f': ('Greece v Germany', ("REFUSED", 'NO_VENUE_NATIVE_EVENT_FOR_FIXTURE')),
    '8de611cbeea26af7bae8f076d9237fd4': ('Netherlands v Serbia', ("REFUSED", 'NO_VENUE_NATIVE_EVENT_FOR_FIXTURE')),
    'a902c361d1a8c4fd596ffb93d71ab378': ('Portugal v Norway', ("REFUSED", 'NO_VENUE_NATIVE_EVENT_FOR_FIXTURE')),
}


def test_the_fixture_is_the_capture_the_spec_describes():
    assert len(EVENTS) == 49
    assert len(ROWS) == 1792
    firsts = [e["first_refusal"] for e in EVENTS]
    assert firsts.count("NO_PINNACLE_ON_EVENT") == 21
    assert firsts.count("NO_VENUE_CONTRACT_FOR_EVENT") == 20
    assert "removed 0 of 1792 rows" in DATA["trim"]
    assert set(EXPECTED) == {e["provider_event_id"] for e in EVENTS}


def test_every_provider_event_in_the_capture_has_its_pinned_outcome():
    got = {}
    for e in EVENTS:
        m = _match(e)
        got[e["provider_event_id"]] = (
            "%s v %s" % (e["home"], e["away"]),
            ("MAPS", m["us_market_slug"],
             "SHORT" if m["intent"] == SHORT else "LONG")
            if m["ok"] else ("REFUSED", m["refusal"]))
    assert got == EXPECTED


def test_every_mapped_contract_is_the_home_teams_own_row():
    """THE SIDE, CHECKED AGAIN FROM THE ROW ITSELF. A wrong side is the
    costliest error this path can make, so beyond the pinned slug: the chosen
    row's own team is the participant assigned to the provider's HOME team,
    it is never the draw, and for soccer it is the LONG row of the per-side
    contract named by that team's code."""
    for e in EVENTS:
        m = _match(e)
        if not m["ok"]:
            continue
        row = m["row"]
        assert row["team_name"] == m["assignment"]["home"], e
        assert row["team_name"] != m["assignment"]["away"], e
        assert not row["market_slug"].endswith("-draw"), e
        assert V.same_team(V.team_profile(e["home"]),
                           V.team_profile(row["team_name"]),
                           womens_competition=m["womens_competition"])["same"]
        if e["family"] == "soccer":
            assert row["intent"] == LONG
            assert row["market_slug"] == "atc-%s-%s" % (row["event_slug"],
                                                        row["team_abbr"])
        else:
            assert row["market_slug"] == "aec-%s" % row["event_slug"]
        assert m["period_evidence"]["period"] == vmap.FULL_MATCH


def test_the_scheduled_paths_league_restriction_changes_no_pinned_outcome():
    """The cycle passes the provider competition's own venue league(s). On the
    capture that restriction changes nothing -- every pinned mapping is in its
    competition's own league -- and it is what keeps a women's price off a
    men's fixture and a Serie B price off Serie A."""
    for e in EVENTS:
        tokens = loop.venue_league_tokens(e["sport_key"])
        assert tokens, e["sport_key"]
        m = _match(e, league_tokens=tokens)
        want = EXPECTED[e["provider_event_id"]][1]
        got = (("MAPS", m["us_market_slug"],
                "SHORT" if m["intent"] == SHORT else "LONG")
               if m["ok"] else ("REFUSED", m["refusal"]))
        assert got == want, e
    assert loop.venue_league_tokens("baseball_mlb") == ("mlb",)
    assert loop.venue_league_tokens("soccer_uefa_champs_league_women") == \
        ("uwcl",)
    assert loop.venue_league_tokens("soccer_epl") == ()


def test_a_fixture_in_another_venue_league_is_not_a_candidate():
    # the capture's own MLB event, searched as though it were NPB
    m = _match(HOU, league_tokens=("npb",))
    assert m["refusal"] == V.R_NO_EVENT
    assert "mlb-cws-hou-2026-09-29" in m["set_aside"]["other_competition"]
    # and a competition nobody named maps nothing
    assert _match(HOU, league_tokens=())["refusal"] == V.R_COMPETITION


def test_it_agrees_with_the_venue_slug_production_resolved_globally():
    """Where production DID cross through `premap.resolve` (HOU-CWS reached the
    book read, ATL-PHI too), the venue-native matcher names the same contract."""
    checked = 0
    for e in EVENTS:
        if not e.get("us_market_slug"):
            continue
        m = _match(e)
        assert m["ok"] and m["us_market_slug"] == e["us_market_slug"], e
        checked += 1
    assert checked == 2


def test_the_measured_coverage_on_the_priced_events():
    """Of the 28 events that HAVE a Pinnacle price, 21 map and 7 refuse, each
    by name. The spec's crude last-word count said "at least 11 of 20"; exact
    names map 13 of the 20 NO_VENUE_CONTRACT events, and refuse two of the
    fixtures it listed (Lyon v "OL Lyonnes", Austria Wien v "FC Internazionale
    Milano") plus Benfica v "FC Bayern Munchen" and Belgium v "Turkiye",
    because no alias is invented."""
    priced = [e for e in EVENTS if e["first_refusal"] != "NO_PINNACLE_ON_EVENT"]
    outcomes = [EXPECTED[e["provider_event_id"]][1][0] for e in priced]
    assert len(priced) == 28
    assert outcomes.count("MAPS") == 21
    assert outcomes.count("REFUSED") == 7
    replaced = [e for e in priced
                if e["first_refusal"] in ("NO_VENUE_CONTRACT_FOR_EVENT",
                                          "VENUE_MAPPING_AMBIGUOUS",
                                          "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT"
                                          "_A_MONEYLINE",
                                          "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP")
                and EXPECTED[e["provider_event_id"]][1][0] == "MAPS"]
    # the events whose production refusal the venue-native path would
    # replace: 13 of the 20 NO_VENUE_CONTRACT, both MLB AMBIGUOUS series
    # games, all 3 LINE and the 1 NO_PREMAP (Wales v Norway, whose global
    # row was the DRAW market)
    assert len(replaced) == 19
    by = {}
    for e in replaced:
        by[e["first_refusal"]] = by.get(e["first_refusal"], 0) + 1
    assert by == {"NO_VENUE_CONTRACT_FOR_EVENT": 13,
                  "VENUE_MAPPING_AMBIGUOUS": 2,
                  "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE": 3,
                  "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP": 1}


# ═════════════════════════════════════════════════════════════════════
# 2 · THE MATCHER'S RULES, ONE AT A TIME
# ═════════════════════════════════════════════════════════════════════

def _event(pid):
    return next(e for e in EVENTS if e["provider_event_id"] == pid)


NYY = _event('4cb76cceb16eb603c683038d132f4642')
HOU = _event('3a08d35ecf12b54dc25f5912ccecf222')
WAL = _event('16d310e835713f3457023deb98fb9f74')


def test_the_tolerance_is_ninety_minutes_both_ways():
    assert V.START_TOLERANCE_S == 5400.0
    venue_start = _ep("2026-09-30T00:00:00+00:00")
    for shift, ok in ((0, True), (5400, True), (-5400, True),
                      (5401, False), (-5401, False)):
        m = _match(NYY, commence_epoch=venue_start + shift)
        assert m["ok"] is ok, (shift, m["refusal"])
        if not ok:
            assert m["refusal"] == V.R_NO_EVENT


def test_series_games_a_day_apart_are_told_apart_by_the_start():
    """THE MLB SERIES CASE the global match could not separate (map4 D6)."""
    g1 = _match(NYY)
    g2 = _match(NYY, commence_epoch=_ep(NYY["commence_time"]) + 86400)
    assert g1["us_market_slug"] == "aec-mlb-bos-nyy-2026-09-29"
    assert g2["us_market_slug"] == "aec-mlb-bos-nyy-2026-09-30"
    midway = _match(NYY, commence_epoch=_ep(NYY["commence_time"]) + 43200)
    assert midway["refusal"] == V.R_NO_EVENT


def test_two_candidates_are_refused_as_ambiguous():
    dup = [dict(r, event_slug=r["event_slug"] + "-dup",
                market_slug=r["market_slug"] + "-dup")
           for r in ROWS if r["event_slug"] == "mlb-bos-nyy-2026-09-29"]
    m = _match(NYY, rows=ROWS + dup)
    assert m["refusal"] == V.R_AMBIGUOUS
    assert m["candidates"] == 2


def test_a_partial_match_never_maps():
    """One team named is a different fixture."""
    m = _match(_event('429e47b7c49fba99d5cbcba70b9c48c5'))  # Belgium v Turkey
    assert m["ok"] is False
    assert m["refusal"] == V.R_ONE_TEAM_ONLY
    assert m["partial_matches"][0]["event_slug"] == "unl-bel-tur-2026-10-02"
    assert m["partial_matches"][0]["home_matched"] is True
    assert m["partial_matches"][0]["away_matched"] is False
    assert "us_market_slug" not in m


def test_generic_tokens_never_count_alone():
    p, v = V.team_profile("Athletic Club (MG)"), V.team_profile("Athletic Club")
    assert V.same_team(p, v)["same"] is False
    assert "generic" in V.same_team(p, v)["why"]
    # and a distinctive token that is shared DOES carry it
    assert V.same_team(V.team_profile("São Bernardo"),
                       V.team_profile("sao bernardo fc"))["same"] is True


def test_a_discriminating_word_is_never_dropped():
    """The Manchester collision `bettor_venue_mapping` was written against."""
    assert V.same_team(V.team_profile("Manchester City"),
                       V.team_profile("Manchester United"))["same"] is False
    assert V.same_team(V.team_profile("Chicago Cubs"),
                       V.team_profile("chicago white sox"))["same"] is False


def test_a_squad_qualifier_makes_it_a_different_team_outside_a_womens_competition():
    arsenal = V.team_profile("Arsenal")
    wfc = V.team_profile("Arsenal WFC")
    assert V.same_team(arsenal, wfc, womens_competition=False)["same"] is False
    assert V.same_team(arsenal, wfc, womens_competition=True)["same"] is True
    # age and reserve qualifiers have no exemption at all
    assert V.same_team(V.team_profile("Real Madrid"),
                       V.team_profile("Real Madrid Castilla"),
                       womens_competition=True)["same"] is False
    # the capture's own UWCL fixture refuses under a men's competition key
    e = _event('6eb5303534d0b64ca7f11d09bd3d5e36')          # Paris FC v Arsenal
    assert _match(e)["ok"] is True
    men = _match(e, competition="soccer_uefa_champs_league")
    assert men["ok"] is False and men["refusal"] == V.R_ONE_TEAM_ONLY


def test_a_swappable_assignment_is_refused():
    """"Paris" is contained in "Paris Saint-Germain FC" as much as in "Paris
    FC", so for a Paris derby named that way which participant is home is not
    established -- each provider team matches BOTH participants."""
    rows = []
    for team, abbr in (("paris fc", "pfc"), ("paris saint germain fc", "psg")):
        rows.append({"market_slug": "atc-d1f-pfc-psg-2026-10-05-%s" % abbr,
                     "intent": LONG, "event_slug": "d1f-pfc-psg-2026-10-05",
                     "event_title": "Paris FC vs. Paris Saint-Germain FC",
                     "question": "Will %s win?" % team, "kind": "side",
                     "sports_type": "soccer_team_full_time_winner",
                     "team_abbr": abbr, "team_name": team, "side_norm": "yes",
                     "game_start": "2026-10-05T19:00:00+00:00"})
    m = V.match_event(home="Paris", away="Paris Saint Germain",
                      family="soccer",
                      commence_epoch=_ep("2026-10-05T19:00:00Z"), rows=rows)
    assert m["refusal"] == V.R_ASSIGNMENT_AMBIGUOUS


def test_the_draw_contract_is_never_the_priced_outcome():
    """Remove the LONG row of Wales's own contract. The event still names
    both participants (Wales through its SHORT row, Norway through its own
    contract) and still matches -- and neither the draw's LONG row nor the
    Wales SHORT row (which pays on NOT Wales) is picked in its place."""
    wal_ev = "unl-wal-nor-2026-10-01"
    rows = [r for r in ROWS if not (r["event_slug"] == wal_ev
                                    and r["team_abbr"] == "wal"
                                    and r["intent"] == LONG)]
    m = _match(WAL, rows=rows)
    assert m["ok"] is False
    assert m["refusal"] == V.R_NO_PRICED_CONTRACT


def test_the_baseball_row_is_chosen_by_its_team_and_keeps_its_own_intent():
    m = _match(HOU)
    assert m["ok"]
    assert m["row"]["team_name"] == "houston astros"
    assert m["intent"] == SHORT
    ident = V.identity_from_match(m, priced_outcome=HOU["home"])
    # THE INTENT PICKS THE LADDER, NOT THE PAYOUT EVENT.
    assert ident["ladder_side"] == "BID"
    assert ident["payout_event"] == "Houston Astros"
    assert ident["probability_event"] == "Houston Astros"
    assert ident["payout_is_complement"] is False
    assert ident["venue_event_key"] == "mlb-cws-hou-2026-09-29"
    assert ident["condition_id"] is None and ident["global_slug"] is None
    assert ident["contract_identity_basis"] == "VENUE_NATIVE_US_SLUG"
    assert ident["resolver"] == "VENUE_NATIVE"
    # and the away team, priced instead, is the LONG row of the same contract
    away = V.match_event(home=HOU["away"], away=HOU["home"], family="baseball",
                         commence_epoch=_ep(HOU["commence_time"]), rows=ROWS)
    assert away["us_market_slug"] == m["us_market_slug"]
    assert away["intent"] == LONG
    assert V.identity_from_match(
        away, priced_outcome=HOU["away"])["ladder_side"] == "ASK"


def test_a_two_way_contract_whose_sides_are_not_shown_refuses():
    ev = "mlb-cws-hou-2026-09-29"
    rows = [dict(r, intent=LONG) if r["event_slug"] == ev else r
            for r in ROWS]
    m = _match(HOU, rows=rows)
    assert m["refusal"] == V.R_CONTRACT_SIDES


def test_a_simulated_or_unrecognised_row_on_the_event_refuses():
    ev = "unl-wal-nor-2026-10-01"
    sim = [dict(r, question="eBattles: " + r["question"])
           if r["event_slug"] == ev else r for r in ROWS]
    m = _match(WAL, rows=sim)
    assert m["ok"] is False
    assert m["refusal"] == vreal.R_REALISM_EVIDENCE_CONFLICTS


def test_a_segment_slug_refuses_by_the_period_rule():
    ev = "unl-wal-nor-2026-10-01"
    seg = [dict(r, market_slug=r["market_slug"].replace(
        "2026-10-01-wal", "2026-10-01-h1-wal"))
        if r["event_slug"] == ev else r for r in ROWS]
    m = _match(WAL, rows=seg)
    assert m["ok"] is False
    assert m["refusal"] == vmap.R_SCOPE_CONFLICT


def test_only_the_familys_exact_winner_types_are_read():
    assert V.FAMILY_WINNER_TYPES == {
        "soccer": ("soccer_team_full_time_winner",),
        "baseball": ("baseball_team_full_game_winner",),
        # cand22: the college-football full-game winner (segment winners
        # such as football_team_first_half_winner stay absent)
        "football": ("football_team_full_game_winner",)}
    half = [dict(r, sports_type="soccer_team_first_half_winner")
            for r in ROWS]
    assert _match(WAL, rows=half)["refusal"] == V.R_NO_EVENT
    m = V.match_event(home="A", away="B", commence_epoch=0, family="hockey",
                      rows=ROWS)
    assert m["refusal"] == V.R_FAMILY


def test_the_latin_fold_is_the_venue_writers_own_table():
    from sportsassets import pmus
    assert V.LATIN_FOLD == pmus._LATIN_FOLD
    assert V.fold("HB Køge") == "hb koge"


def test_the_identity_carries_every_key_the_global_resolver_returns():
    """EXACTLY THE SHAPE `resolve_venue_identity` RETURNS, read from its own
    source so a key added there cannot be silently missing here."""
    src = inspect.getsource(loop.resolve_venue_identity)
    keys = set()
    for node in ast.walk(ast.parse(textwrap.dedent(src))):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == "out"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.ctx, ast.Store)):
            keys.add(node.slice.value)
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "out"
                and isinstance(node.value, ast.Dict)):
            keys |= {k.value for k in node.value.keys
                     if isinstance(k, ast.Constant)}
    # set only when a read RAISES, or only on a refusal
    keys -= {"why", "venue_event_key_error", "venue_catalogue_read_error"}
    assert len(keys) > 25
    ident = V.identity_from_match(_match(HOU), priced_outcome=HOU["home"])
    assert ident["ok"] is True
    assert keys - set(ident) == set()


def test_every_refusal_is_staged_and_classified():
    for code in V.REFUSALS:
        assert ext.STAGE_OF[code] == "3_IDENTITY", code
        assert code in ext.EVALUABILITY_OF, code


# ═════════════════════════════════════════════════════════════════════
# 3 · THE LOOP'S PURE PIECES
# ═════════════════════════════════════════════════════════════════════

def test_only_the_global_catalogues_own_misses_are_offered_to_it():
    may = loop.venue_native_may_replace
    assert may(["NO_VENUE_CONTRACT_FOR_EVENT"])
    assert may(["VENUE_MAPPING_AMBIGUOUS"])
    assert may(["VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE"])
    # UPDATED, R30A P0 incident (normalization/identity root cause RC6, merged
    # from the inc-discovery stream): this test pinned that a CLOSED or
    # SEGMENT finding in the GLOBAL catalogue means "the fixture WAS found".
    # The production receipt measured that it does not: the global catalogue
    # is matched on names alone and is date-blind, so its segment or closed
    # row was an inning market, a half, or YESTERDAY's series game -- 5 events
    # in 72 h (MLB, Serie B, UNL) and ~21 h of one MLB playoff game's coverage
    # were lost to it. The venue-native resolver re-applies its own FULL_MATCH
    # period rule, realism and re-seen window, so those refusals are now
    # offered to the venue's own catalogue (ext_pinnacle_loop.
    # VENUE_NATIVE_MAY_REPLACE, whose comment carries the evidence).
    assert may(["VENUE_MARKET_CLOSED_OR_RESOLVED",
                "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE"])
    assert may(["VENUE_CONTRACT_IS_A_SEGMENT_NOT_FULL_GAME"])
    assert not may(["TEAM_NAMES_COLLIDE_AFTER_NORMALISATION"])
    assert not may([])
    # NO PROVIDER PRICE CAN NEVER BE REPAIRED BY A CATALOGUE
    assert not may(["NO_PINNACLE_ON_EVENT"])
    assert loop.VENUE_NATIVE_MAY_REPLACE_IDENTITY == (
        "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP",)


def test_a_venue_native_mapping_never_borrows_the_global_condition():
    vn = V.identity_from_match(_match(WAL), priced_outcome="Wales")
    glob = {"mapped": True, "condition_id": "0xglobal-draw-market",
            "market_row": {"slug": "unl-wal-nor-2026-10-01-draw",
                           "title": "Wales vs. Norway: draw"}}
    m = loop.venue_native_mapping(vn, replaced=["NO_VENUE_NATIVE_CONTRACT_IN"
                                                "_PREMAP"], global_mapped=glob)
    assert m["condition_id"] is None and m["market_row"] is None
    assert m["global_slug"] is None
    assert m["global_mapping_replaced"]["condition_id"] == "0xglobal-draw-market"
    assert m["global_refusal_replaced"] == "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"


def test_the_arrival_split_is_theirs_plus_ours_and_none_when_unmeasured():
    got = loop.arrival_split(100.0, 110.0, 145.0)
    assert got == {"provider_lag_s": 10.0, "our_processing_s": 35.0,
                   "quote_age_s": 45.0}
    assert loop.arrival_split(None, 110.0, 145.0) == {
        "provider_lag_s": None, "our_processing_s": 35.0, "quote_age_s": None}
    assert loop.arrival_split(100.0, None, 145.0) == {
        "provider_lag_s": None, "our_processing_s": None, "quote_age_s": 45.0}


def test_the_dead_code_after_the_return_is_gone():
    """map4 D7: unreachable lines named `_participants` and `venue_sets`,
    neither of which exists."""
    src = inspect.getsource(loop.confirm_mapping_by_fixtures)
    assert "_participants" not in src and "venue_sets" not in src
    tree = ast.parse(src)
    fn = tree.body[0]
    for i, stmt in enumerate(fn.body):
        if isinstance(stmt, ast.Return):
            assert i == len(fn.body) - 1, "statements after the final return"


def test_the_production_refusals_are_staged_and_classified():
    """map4 D9: a caller classifying these got UNCLASSIFIED or the default."""
    assert ext.STAGE_OF["NO_PINNACLE_ON_EVENT"] == "1_PROBABILITY"
    assert ext.STAGE_OF["QUOTE_STALE_ON_ARRIVAL"] == "2_FRESHNESS"
    assert ext.STAGE_OF["VENUE_MAPPING_AMBIGUOUS"] == "3_IDENTITY"
    assert ext.STAGE_OF[
        "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE"] == "3_IDENTITY"
    assert ext.evaluability(["NO_PINNACLE_ON_EVENT"]) == ext.EXTERNAL_DEPENDENCY
    assert ext.evaluability(["QUOTE_STALE_ON_ARRIVAL"]) == ext.DECIDED
    assert ext.evaluability(["VENUE_MAPPING_AMBIGUOUS"]) == \
        ext.COULD_NOT_EVALUATE
    assert ext.evaluability(
        ["VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE"]) == \
        ext.EXTERNAL_DEPENDENCY


class _NoCalls:
    """A connection that must not be used: every call is a test failure."""

    def __getattr__(self, name):
        raise AssertionError("the connection was used: %s" % name)


@pytest.mark.asyncio
async def test_a_missing_condition_neither_reads_nor_writes_the_fixture_row():
    """`str(None)` is "None": without the guard a venue-native candidate would
    read -- and on a match write -- a fixture row under that shared key, and
    the next one would be handed its game state."""
    got = await loop.acquire_fixture_scope(
        _NoCalls(), condition_id=None, home=HOME, away=AWAY,
        commence_iso="2026-10-01T00:00:00Z", now=time.time(), cache={})
    assert got["read"] is False
    assert got["refusal"] == loop.R_FIXTURE_KEY_ABSENT
    assert got["acquisition"]["attempted"] is False


@pytest.mark.asyncio
async def test_a_venue_native_payout_is_never_bound_to_a_guessed_index():
    got = await loop.bind_payout_outcome(
        _NoCalls(), condition_id=None, payout_event=HOME, intent=SHORT)
    assert got["ok"] is False
    assert got["refusal"] == loop.R_OUTCOME_NOT_BOUND
    assert got["basis"] == "NO_GLOBAL_CONDITION_TO_BIND_AGAINST"


def test_the_market_rail_counts_an_open_row_on_the_same_venue_contract():
    """A venue-native candidate has no global condition. An open row entered
    through the global path on the SAME venue contract must still count toward
    MAX_MARKET_EXPOSURE -- the slug only ever ADDS rows to the market rail."""
    from sportsassets import bettor_entry_execution as EX

    now = time.time()
    rows = [{"condition_id": "0xglobal", "venue_market_slug": "aec-x",
             "event_key": "ev-x", "cost_usd": 100.0, "qty": 10.0,
             "opened_at": now - 60},
            {"condition_id": "0xother", "venue_market_slug": "aec-y",
             "event_key": "ev-y", "cost_usd": 50.0, "qty": 5.0,
             "opened_at": now - 60}]
    kw = dict(event_key="ev-x", proposed_cost_usd=0.0, proposed_qty=0.0,
              now=now)
    old = EX.exposure_from_rows(rows, condition_id=None, **kw)["observed"]
    new = EX.exposure_from_rows(rows, condition_id=None,
                                venue_market_slug="aec-x", **kw)["observed"]
    assert old["MAX_MARKET_EXPOSURE"] == 0.0
    assert new["MAX_MARKET_EXPOSURE"] == 100.0
    # the global path is unchanged, and never loses a row it counted
    glob = EX.exposure_from_rows(rows, condition_id="0xglobal", **kw)
    both = EX.exposure_from_rows(rows, condition_id="0xglobal",
                                 venue_market_slug="aec-x", **kw)
    assert glob["observed"] == both["observed"]
    assert glob["observed"]["MAX_MARKET_EXPOSURE"] == 100.0
    head = EX.headroom_from_rows(rows, condition_id=None, event_key="ev-x",
                                 now=now, venue_market_slug="aec-x")
    assert head["used"]["MAX_MARKET_EXPOSURE"] == 100.0


# ═════════════════════════════════════════════════════════════════════
# 4 · THE DATABASE READ AND THE REAL CYCLE
# ═════════════════════════════════════════════════════════════════════

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

PREFIX = "odds-d3vn-"
HOME, AWAY = "Miami Marlins", "Colorado Rockies"
SPORTS_TYPE = "baseball_team_full_game_winner"
VENUE_PROSE = (
    "This market settles on the final result of the game, including any "
    "extra innings.")


def _iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class Game:
    """One synthetic MLB fixture, dated from NOW so it is always current.
    The venue lists the VISITOR (Rockies) as LONG and the provider's HOME team
    (Marlins) as SHORT, exactly the shape of every MLB contract in the
    capture."""

    def __init__(self, *, hours_ahead=6.0):
        start = int((time.time() + hours_ahead * 3600) // 3600) * 3600
        self.start = float(start)
        self.day = _dt.datetime.fromtimestamp(
            start, _dt.timezone.utc).date().isoformat()
        self.event_slug = "mlb-col-mia-%s" % self.day
        self.us_slug = "aec-%s" % self.event_slug
        # the provider states the first pitch ten minutes after the venue's
        # hour, as MLB did in the capture (21:10:26 against 21:00)
        self.commence = _iso(self.start + 600)

    def venue_event(self):
        side = {"identifier": self.us_slug}
        return {"slug": self.event_slug,
                "title": "COL Rockies vs. MIA Marlins",
                "markets": [{
                    "slug": self.us_slug,
                    "question": "Colorado Rockies vs. Miami Marlins",
                    "sportsMarketType": SPORTS_TYPE,
                    "gameStartTime": _iso(self.start), "closed": False,
                    "marketSides": [
                        dict(side, description="Rockies", long=True,
                             team={"abbreviation": "col",
                                   "name": "Colorado Rockies",
                                   "safeName": "Colorado", "league": "mlb",
                                   "id": 115}),
                        dict(side, description="Marlins", long=False,
                             team={"abbreviation": "mia",
                                   "name": "Miami Marlins",
                                   "safeName": "Miami", "league": "mlb",
                                   "id": 146})]}]}


def odds_event(game, eid, *, stamp_age_s=2.0, pinnacle=True, at=None,
               home=HOME, away=AWAY):
    """A provider event. THE PRICES ARE SYNTHETIC TEST EVIDENCE."""
    now = time.time() if at is None else at
    stamp = _iso(now - stamp_age_s)
    prices = [{"name": home, "price": 2.20}, {"name": away, "price": 1.72}]
    books = [{"key": "smarkets", "last_update": stamp,
              "markets": [{"key": "h2h", "outcomes": prices}]}]
    if pinnacle:
        books.insert(0, {"key": "pinnacle", "last_update": stamp,
                         "markets": [{"key": "h2h", "last_update": stamp,
                                      "outcomes": prices}]})
    return {"id": PREFIX + eid, "home_team": home, "away_team": away,
            "commence_time": game.commence, "bookmakers": books}


class _Markets:
    def __init__(self, slugs):
        self.slugs = set(slugs)
        self.books = 0

    def list(self, params=None):
        want = list((params or {}).get("slug") or [])
        # orderPriceMinTickSize: the market's own tick, on every listing row
        # as the venue publishes it (SYNTHETIC 0.01). Every funded ladder is
        # restricted to the executable grid, which refuses an unread tick.
        return {"markets": [{"slug": s, "description": VENUE_PROSE,
                             "sportsMarketType": SPORTS_TYPE,
                             "orderPriceMinTickSize": "0.01"}
                            for s in want if s in self.slugs]}

    def book(self, slug):
        self.books += 1
        if slug not in self.slugs:
            return {}
        lvl = lambda p, q: {"px": {"value": "%.2f" % p, "currency": "USD"},
                            "qty": str(q)}
        return {"marketData": {
            "offers": [lvl(0.55, 400), lvl(0.57, 300)],
            "bids": [lvl(0.52, 400), lvl(0.50, 300)],
            "transactTime": _dt.datetime.fromtimestamp(
                time.time() - 2.0, _dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"}}

    def retrieve_by_slug(self, slug):
        return {"market": {"slug": slug, "marketSides": []}}


class _Client:
    def __init__(self, slugs):
        self.markets = _Markets(slugs)


def substitute(monkeypatch, *, slugs, odds_by_sport, received_at=None,
               catalogue=("baseball_mlb",), currency=False):
    """Stand up the provider and the venue. NO SUBMISSION SWITCH IS TOUCHED."""
    from sportsassets import pmus

    monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)   # a placeholder
    client = _Client(slugs)
    monkeypatch.setattr(pmus, "_get_client", lambda: client)

    async def fake_catalogue(*, api_key, timeout=20.0):
        return {"ok": True, "status": 200, "sports": [
            {"key": k, "group": "x", "active": True} for k in catalogue]}

    async def fake_odds(sport_key, *, api_key, timeout=20.0):
        now = time.time()
        got = odds_by_sport.get(sport_key)
        events = got(now) if callable(got) else list(got or [])
        return {"ok": True, "events": events,
                "received_at": (now if received_at is None
                                else received_at(now)),
                "credits_used": "1", "credits_remaining": "9"}

    async def fake_scores(sport_key, *, api_key, timeout=20.0):
        return {"ok": True, "events": [], "received_at": time.time()}

    monkeypatch.setattr(loop, "fetch_sport_catalogue", fake_catalogue)
    monkeypatch.setattr(loop, "fetch_odds", fake_odds)
    monkeypatch.setattr(loop, "fetch_scores", fake_scores)

    # R30A · THE COVERAGE SCHEDULER SKIPS A COMPETITION WITH NO VENUE EVENT IN
    # THE NEXT 24 H, and the captures these tests replay are dated 2026-09-29
    # (MLB `aec-mlb-cws-hou-2026-09-29`) while the cycle runs on the real
    # clock -- so the venue's own catalogue correctly shows them as past, and
    # MLB would no longer be fetched. Before R30A MLB was fetched every cycle
    # whatever its board said (240 cycles in 7 days with no event in 24 h).
    # The subject here is identity, not scheduling, so the horizon is stated
    # UNREAD, exactly as on a failed read: the competition stays in demand.
    async def _horizon_unread(conn):
        return {"read": False, "by_token": {}, "source": "substituted",
                "error": "SUBSTITUTED_BY_THE_TEST"}
    monkeypatch.setattr(loop, "venue_horizon", _horizon_unread)
    monkeypatch.setattr(loop, "_fetch_schedule_blocking",
                        lambda d: {"ok": True, "url": "substituted",
                                   "payload": {"dates": []}})
    if currency:
        real = loop.book_currency_evidence

        def supplied(slug=None):
            got = dict(real(slug))
            now = time.time()
            got["subscription"] = {"alive_at": now - 0.5,
                                   "last_update_at": now - 1.0}
            got["SUPPLIED_BY_A_TEST"] = (
                "the venue documents no book-timing contract (P5); production "
                "refuses VENUE_BOOK_CURRENCY_NOT_ESTABLISHED without this")
            return got
        monkeypatch.setattr(loop, "book_currency_evidence", supplied)
    loop.rules_cache_reset()
    return client


async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


async def seed_venue(conn, game):
    from sportsassets.workers import premap as pm

    await pm._ensure_table(conn)
    await conn.execute("DELETE FROM us_premap WHERE event_slug=$1",
                       game.event_slug)
    ev = game.venue_event()
    keys = pm.event_keys_for(ev["title"], ev["slug"])
    rows = [r for m in ev["markets"] for r in pm._market_rows(ev, m)]
    keys = sorted(set(keys) | pm.venue_kick_keys(rows))
    for r in rows:
        await pm._upsert(conn, r, pm.keys_for_row(keys, r))
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
        "ON CONFLICT (key) DO UPDATE SET value='true'", loop.CONTROL_KEY)


async def clean(conn, *games, conditions=()):
    stmts = [("DELETE FROM ext_candidate_outcomes "
              " WHERE provider_event_id LIKE $1", (PREFIX + "%",))]
    for g in games:
        stmts += [("DELETE FROM external_valuations WHERE us_market_slug=$1",
                   (g.us_slug,)),
                  ("DELETE FROM us_premap WHERE event_slug=$1",
                   (g.event_slug,)),
                  ("DELETE FROM bettor_pair_observation_attempts "
                   " WHERE us_market_slug=$1", (g.us_slug,))]
    for c in conditions:
        stmts += [("DELETE FROM markets WHERE condition_id=$1", (c,))]
    for sql, args in stmts:
        try:
            await conn.execute(sql, *args)
        except Exception:                                      # noqa: BLE001
            # A TABLE THIS DATABASE DOES NOT HAVE is not a failure of the
            # test; the assertions below never read it.
            pass


async def _rows(conn):
    return [dict(r) for r in await conn.fetch(
        "SELECT provider_event_id, outcome, first_refusal, codes, stage, "
        "       us_market_slug, global_slug, mapped_by, "
        "       global_refusal_replaced, provider_lag_s, our_processing_s, "
        "       quote_age_s "
        "  FROM ext_candidate_outcomes WHERE provider_event_id LIKE $1 "
        " ORDER BY id", PREFIX + "%")]


def _ev(out, eid):
    return next(r for r in out["event_ledger"]
                if r["provider_event_id"] == PREFIX + eid)


@pg
@pytest.mark.asyncio
async def test_resolve_venue_native_reads_the_catalogue_writers_rows():
    conn = await _connect()
    g = Game()
    try:
        await seed_venue(conn, g)
        got = await V.resolve_venue_native(
            conn, home=HOME, away=AWAY, commence_time=g.commence,
            family="baseball", now=time.time(), competition="baseball_mlb")
        assert got["ok"] is True, got
        assert got["us_market_slug"] == g.us_slug
        assert got["intent"] == SHORT
        assert got["ladder_side"] == "BID"
        assert got["payout_event"] == HOME
        assert got["payout_is_complement"] is False
        assert got["venue_event_key"] == g.event_slug
        assert got["realism"]["verdict"] == vreal.REAL
        assert got["period"] == "FULL_GAME"
        # A ROW NOT RE-SEEN RECENTLY IS NOT A CURRENT CONTRACT.
        stale = await V.resolve_venue_native(
            conn, home=HOME, away=AWAY, commence_time=g.commence,
            family="baseball", now=time.time() + V.RESEEN_WITHIN_S + 60)
        assert stale["refusal"] == V.R_NO_EVENT
    finally:
        await clean(conn, g)
        await conn.close()


@pytest.mark.asyncio
async def test_a_matcher_that_raises_maps_nothing_and_says_so(monkeypatch):
    class _Rows:
        async def fetch(self, *a, **k):
            return [dict(r) for r in ROWS[:4]]

    def boom(**k):
        raise KeyError("defect")

    monkeypatch.setattr(V, "match_event", boom)
    got = await V.resolve_venue_native(
        _Rows(), home=HOME, away=AWAY, commence_time="2026-10-01T00:00:00Z",
        family="baseball", now=time.time())
    assert got["ok"] is False
    assert got["refusal"] == V.R_MATCH_RAISED
    assert got["match_error"] == "KeyError"
    assert got["us_market_slug"] is None


@pytest.mark.asyncio
async def test_a_failed_catalogue_read_refuses_by_name():
    class _Broken:
        async def fetch(self, *a, **k):
            raise RuntimeError("down")

    got = await V.resolve_venue_native(
        _Broken(), home=HOME, away=AWAY, commence_time="2026-10-01T00:00:00Z",
        family="baseball", now=time.time())
    assert got["ok"] is False
    assert got["refusal"] == V.R_READ_FAILED
    assert got["read_error"] == "RuntimeError"


@pg
@pytest.mark.asyncio
async def test_a_fixture_the_global_catalogue_lacks_is_mapped_and_stops_where_production_does(
        monkeypatch):
    """NO_VENUE_CONTRACT_FOR_EVENT is replaced by the venue's own contract, the
    candidate continues down the SAME admission path, and with no book-currency
    mechanism (the production state) it stops at the book read, by name."""
    conn = await _connect()
    g = Game()
    try:
        await clean(conn, g)
        await seed_venue(conn, g)
        substitute(monkeypatch, slugs=[g.us_slug],
                   odds_by_sport={"baseball_mlb":
                                  lambda now: [odds_event(g, "a", at=now)]})
        out = await loop.cycle(conn)
        assert out["ran"] is True, out.get("why")
        row = _ev(out, "a")
        assert row["mapped_by"] == "VENUE_NATIVE"
        assert row["global_refusal_replaced"] == "NO_VENUE_CONTRACT_FOR_EVENT"
        assert row["us_market_slug"] == g.us_slug
        assert row["first_refusal"] == "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"
        assert "NO_VENUE_CONTRACT_FOR_EVENT" not in row["codes"]
        fn = out["funnel_by_provider_sport"]["baseball_mlb"]
        assert fn["mapped_to_a_venue_contract"] == 1
        assert fn["mapped_by_venue_native"] == 1
        assert fn["venue_native_replaced"] == {"NO_VENUE_CONTRACT_FOR_EVENT": 1}
        assert fn["identity_resolved"] == 1
        assert "NO_VENUE_CONTRACT_FOR_EVENT" not in out["refusals"]
        # the durable row carries the path and the arrival split
        db = await _rows(conn)
        assert len(db) == 1
        assert db[0]["mapped_by"] == "VENUE_NATIVE"
        assert db[0]["global_refusal_replaced"] == \
            "NO_VENUE_CONTRACT_FOR_EVENT"
        assert db[0]["provider_lag_s"] is not None
        assert db[0]["our_processing_s"] is not None
        assert db[0]["quote_age_s"] is not None
        assert out["candidate_outcomes"]["persisted"]["columns_143"] is True
        assert out["candidate_outcomes"]["reconciles"] is True
    finally:
        await clean(conn, g)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_venue_native_candidate_is_valued_on_the_right_side_and_holds_nothing(
        monkeypatch):
    """WITH A BOOK-CURRENCY READING SUPPLIED (a test assumption; production has
    none) the candidate reaches the valuation. The row records the venue-native
    identity, the SHORT row's bid ladder, the home team as the payout event and
    NO complement -- and no inventory or order can follow, because a
    venue-native identity has no global condition to bind a payout index to."""
    conn = await _connect()
    g = Game()
    try:
        await clean(conn, g)
        await seed_venue(conn, g)
        substitute(monkeypatch, slugs=[g.us_slug], currency=True,
                   odds_by_sport={"baseball_mlb":
                                  lambda now: [odds_event(g, "b", at=now)]})
        out = await loop.cycle(conn)
        assert out["ran"] is True, out.get("why")
        assert out["evaluated"] == 1, out["refusals"]
        v = dict(await conn.fetchrow(
            "SELECT condition_id, us_market_slug, contract_identity_basis, "
            "       contract_selection, payout_event, probability_event, "
            "       payout_is_complement, buy_intent, ladder_side, "
            "       matched_side_norm, resolver_asked_for, admissible, "
            "       order_submitted "
            "  FROM external_valuations WHERE us_market_slug=$1", g.us_slug))
        assert v["condition_id"] is None
        assert v["contract_identity_basis"] == "VENUE_NATIVE_US_SLUG"
        assert v["contract_selection"] == HOME
        assert v["payout_event"] == HOME
        assert v["probability_event"] == HOME
        assert v["payout_is_complement"] is False
        assert v["buy_intent"] == SHORT
        assert v["ladder_side"] == "BID"
        assert v["matched_side_norm"] == "marlins"
        assert v["order_submitted"] is False
        assert "FUNDED:SUBMITTED" not in out["refusals"]
        assert "ENTRY_INVENTORY_WRITTEN" not in out["refusals"]
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE venue_market_slug=$1",
            g.us_slug) == 0
    finally:
        await clean(conn, g)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_no_pinnacle_is_never_repaired_by_the_venue_catalogue(monkeypatch):
    """THE VENUE LISTS THE FIXTURE AND THE PROVIDER HAS NO PINNACLE PRICE: the
    event refuses NO_PINNACLE_ON_EVENT alone and the venue catalogue is never
    asked. The capture's 21 such events cannot be touched by this change."""
    conn = await _connect()
    g = Game()
    calls = []
    real = V.resolve_venue_native

    async def spy(*a, **k):
        calls.append(k)
        return await real(*a, **k)

    try:
        await clean(conn, g)
        await seed_venue(conn, g)
        substitute(monkeypatch, slugs=[g.us_slug], odds_by_sport={
            "baseball_mlb": lambda now: [odds_event(g, "np", at=now,
                                                    pinnacle=False)]})
        monkeypatch.setattr(V, "resolve_venue_native", spy)
        out = await loop.cycle(conn)
        row = _ev(out, "np")
        # P0 INCIDENT (attribution): the generic NO_PINNACLE_ON_EVENT is now
        # recorded BY CAUSE -- the PinnAPI refusal (no feed owner here), then
        # the discovery payload's absence -- and still nothing else: the
        # venue catalogue is never asked (below).
        assert row["codes"] == ["FEED_OWNERSHIP_NOT_HELD",
                                loop.R_PAYLOAD_HAS_NO_PINNACLE]
        assert row["first_refusal"] == "FEED_OWNERSHIP_NOT_HELD"
        assert row["mapped_by"] is None and row["us_market_slug"] is None
        assert row["provider_lag_s"] is None
        assert calls == []
        fn = out["funnel_by_provider_sport"]["baseball_mlb"]
        assert fn["mapped_by_venue_native"] == 0
        assert fn["with_pinnacle_h2h"] == 0
        assert out["latency"]["on_arrival_every_priced_event"]["events"] == 0
    finally:
        await clean(conn, g)
        await conn.close()


async def _insert_fixture_rows(conn, event_slugs):
    """The capture's own venue rows for these events, written verbatim (the
    identifier is the market slug, as the writer keys a per-side row)."""
    n = 0
    for r in ROWS:
        if r["event_slug"] not in event_slugs:
            continue
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title, "
            " market_slug, question, kind, line, side_norm, event_keys, "
            " intent, signed, team_abbr, team_name, game_start, sports_type, "
            " updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,"
            " $14::timestamptz,$15, now()) "
            "ON CONFLICT (identifier, side_norm) DO UPDATE SET updated_at=now()",
            r["market_slug"], r["event_slug"], r["event_title"],
            r["market_slug"], r["question"], r["kind"], r["line"],
            r["side_norm"], [], r["intent"], r["signed"], r["team_abbr"],
            r["team_name"], _dt.datetime.fromisoformat(r["game_start"]),
            r["sports_type"])
        n += 1
    return n


@pg
@pytest.mark.asyncio
async def test_none_of_the_captures_21_no_pinnacle_events_is_repaired(
        monkeypatch):
    """ALL 21 NO_PINNACLE_ON_EVENT EVENTS OF THE CAPTURE, THROUGH THE REAL
    CYCLE, WITH THE VENUE ROWS THAT WOULD MAP 15 OF THEM PRESENT. The provider
    response carries no Pinnacle h2h for any of them -- exactly production --
    so every one refuses NO_PINNACLE_ON_EVENT and nothing else, and the venue
    catalogue is never asked. (The competition confirmation runs for real, on
    the venue's own titles and dates for these fixtures.)"""
    conn = await _connect()
    nopin = [e for e in EVENTS if e["first_refusal"] == "NO_PINNACLE_ON_EVENT"]
    assert len(nopin) == 21
    mappable = {_match(e)["event_slug"] for e in nopin if _match(e)["ok"]}
    assert len(mappable) == 15
    keys = sorted({e["sport_key"] for e in nopin})
    token = {"soccer_uefa_nations_league": "unl",
             "soccer_brazil_serie_b": "brb"}
    calls = []
    real = V.resolve_venue_native

    async def spy(*a, **k):
        calls.append(k)
        return await real(*a, **k)

    titles, days = {}, {}
    for r in ROWS:
        if r["event_slug"] in mappable:
            t = token[next(e["sport_key"] for e in nopin
                           if _match(e).get("event_slug") == r["event_slug"])]
            titles.setdefault(t, set()).add(r["event_title"])
            days.setdefault(t, {})[r["event_title"]] = r["game_start"][:10]

    async def board(conn, *, now=None):
        return {"read": True, "source": "us_premap", "evidence": "LIVE_READ",
                "evidence_age_s": 0.0,
                "board": [(t, len(v)) for t, v in sorted(titles.items())],
                "titles": {t: sorted(v) for t, v in titles.items()},
                "title_days": days}

    def provider(key):
        def events(now):
            stamp = _iso(now - 2)
            return [{"id": PREFIX + "nopin-" + e["provider_event_id"],
                     "home_team": e["home"], "away_team": e["away"],
                     "commence_time": e["commence_time"],
                     # another book prices it; PINNACLE DOES NOT
                     "bookmakers": [{"key": "smarkets", "last_update": stamp,
                                     "markets": [{"key": "h2h", "outcomes": [
                                         {"name": e["home"], "price": 2.0},
                                         {"name": e["away"], "price": 3.6},
                                         {"name": "Draw", "price": 3.3}]}]}]}
                    for e in nopin if e["sport_key"] == key]
        return events

    try:
        await clean(conn)
        await conn.execute(
            "DELETE FROM us_premap WHERE event_slug = ANY($1::text[])",
            sorted(mappable))
        assert await _insert_fixture_rows(conn, mappable) > 0
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value='true'", loop.CONTROL_KEY)
        substitute(monkeypatch, slugs=[],
                   catalogue=["baseball_mlb"] + keys,
                   odds_by_sport=dict({k: provider(k) for k in keys},
                                      baseball_mlb=[]))
        monkeypatch.setattr(loop, "venue_soccer_competitions", board)
        monkeypatch.setattr(V, "resolve_venue_native", spy)
        out = await loop.cycle(conn)
        rows = [r for r in out["event_ledger"]
                if str(r["provider_event_id"]).startswith(PREFIX + "nopin-")]
        assert len(rows) == 21
        for r in rows:
            # (P0 incident) by cause, not the generic NO_PINNACLE_ON_EVENT
            assert r["codes"] == ["FEED_OWNERSHIP_NOT_HELD",
                                  loop.R_PAYLOAD_HAS_NO_PINNACLE], r
            assert r["mapped_by"] is None and r["us_market_slug"] is None
        assert calls == []
        assert out["refusals"][loop.R_PAYLOAD_HAS_NO_PINNACLE] == 21
        assert "NO_PINNACLE_ON_EVENT" not in out["refusals"]
        for k in keys:
            fn = out["funnel_by_provider_sport"][k]
            assert fn["mapping_confirmation"]["ok"] is True, fn
            assert fn["mapped_by_venue_native"] == 0
            assert fn["with_pinnacle_h2h"] == 0
    finally:
        await conn.execute(
            "DELETE FROM us_premap WHERE event_slug = ANY($1::text[])",
            sorted(mappable))
        await clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_venue_native_refusal_is_counted_beside_the_global_one(
        monkeypatch):
    """Neither catalogue lists these teams: the global refusal stays FIRST, the
    venue-native refusal is added to the event's codes, and both are counted --
    and the price's age is still measured, because it has a Pinnacle quote."""
    conn = await _connect()
    g = Game()
    try:
        await clean(conn, g)
        await seed_venue(conn, g)
        substitute(monkeypatch, slugs=[g.us_slug], odds_by_sport={
            "baseball_mlb": lambda now: [odds_event(
                g, "none", at=now, home="Toronto Blue Jays",
                away="Kansas City Royals")]})
        out = await loop.cycle(conn)
        row = _ev(out, "none")
        assert row["codes"] == ["NO_VENUE_CONTRACT_FOR_EVENT",
                                "NO_VENUE_NATIVE_EVENT_FOR_FIXTURE"]
        assert row["first_refusal"] == "NO_VENUE_CONTRACT_FOR_EVENT"
        assert row["mapped_by"] is None
        assert out["refusals"].get("NO_VENUE_NATIVE_EVENT_FOR_FIXTURE") == 1
        fn = out["funnel_by_provider_sport"]["baseball_mlb"]
        assert fn["refusals"]["NO_VENUE_CONTRACT_FOR_EVENT"] == 1
        assert fn["refusals"]["NO_VENUE_NATIVE_EVENT_FOR_FIXTURE"] == 1
        db = await _rows(conn)
        assert db[0]["provider_lag_s"] is not None
        assert db[0]["quote_age_s"] is not None
        assert db[0]["mapped_by"] is None
        every = out["latency"]["on_arrival_every_priced_event"]
        assert every["events"] == 1
        assert every["provider_lag_s"] is not None
        # ...while the evaluated-candidate figures, rightly, saw nothing
        assert out["latency"]["samples"] == 0
    finally:
        await clean(conn, g)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_no_premap_is_offered_to_the_venue_catalogue_and_both_count_when_it_refuses(
        monkeypatch):
    """The GLOBAL row is found but the US crossing is not (a global slug with
    no date, which `premap.resolve` cannot key). With the venue's own row
    present the venue-native identity replaces NO_VENUE_NATIVE_CONTRACT_IN_PREMAP
    and borrows NOTHING from the global row; with it absent both refusals are
    counted, the crossing's first, in this sport's own refusals too (D4)."""
    conn = await _connect()
    g = Game()
    cond = "c-d3vn-col-mia"
    try:
        await clean(conn, g, conditions=[cond])
        await seed_venue(conn, g)
        await conn.execute(
            "INSERT INTO markets (condition_id, title, event_title, slug, "
            " sport, closed, resolved) VALUES ($1,$2,$3,$4,'MLB',false,false) "
            "ON CONFLICT (condition_id) DO UPDATE SET updated_at=now(), "
            " title=EXCLUDED.title, event_title=EXCLUDED.event_title, "
            " slug=EXCLUDED.slug, sport='MLB', closed=false, resolved=false",
            cond, "Will %s beat %s?" % (AWAY, HOME),
            "%s vs. %s" % (AWAY, HOME), "mlb-col-mia-no-date")
        substitute(monkeypatch, slugs=[g.us_slug], odds_by_sport={
            "baseball_mlb": lambda now: [odds_event(g, "np1", at=now)]})
        out = await loop.cycle(conn)
        row = _ev(out, "np1")
        assert row["mapped_by"] == "VENUE_NATIVE"
        assert row["global_refusal_replaced"] == \
            "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"
        assert row["us_market_slug"] == g.us_slug
        assert "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP" not in row["codes"]
        assert row["global_slug"] is None           # nothing borrowed

        # and now the venue's own row is gone: both refusals counted
        await conn.execute("DELETE FROM us_premap WHERE event_slug=$1",
                           g.event_slug)
        substitute(monkeypatch, slugs=[g.us_slug], odds_by_sport={
            "baseball_mlb": lambda now: [odds_event(g, "np2", at=now)]})
        out = await loop.cycle(conn)
        row = _ev(out, "np2")
        assert row["codes"] == ["NO_VENUE_NATIVE_CONTRACT_IN_PREMAP",
                                "NO_VENUE_NATIVE_EVENT_FOR_FIXTURE"]
        assert row["first_refusal"] == "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"
        assert row["mapped_by"] == "GLOBAL_CATALOGUE"
        fn = out["funnel_by_provider_sport"]["baseball_mlb"]
        assert fn["refusals"]["NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"] == 1
        assert fn["refusals"]["NO_VENUE_NATIVE_EVENT_FOR_FIXTURE"] == 1
        assert fn["identity_resolved"] == 0
        assert fn["mapped_to_a_venue_contract"] == 1
    finally:
        await clean(conn, g, conditions=[cond])
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_stale_on_arrival_is_split_into_theirs_and_ours(monkeypatch):
    """TWO EVENTS SKIPPED AT LEVER A. The first arrived already 45 s old: the
    provider's lateness. The second was handed over 2 s old and our own
    receipt-to-arrival time (40 s here) took it past the rule: ours, now
    counted by name. Both join the cycle's latency samples, and both rows
    carry the split durably."""
    conn = await _connect()
    g = Game()
    try:
        await clean(conn, g)
        await seed_venue(conn, g)
        # (1) the provider's lateness
        substitute(monkeypatch, slugs=[g.us_slug], odds_by_sport={
            "baseball_mlb": lambda now: [odds_event(g, "late", at=now,
                                                    stamp_age_s=45.0)]})
        out = await loop.cycle(conn)
        lat = out["latency"]
        assert out["refusals"].get("QUOTE_STALE_ON_ARRIVAL") == 1
        assert lat["provider_stale_on_arrival"] == 1
        assert lat["stale_on_arrival_due_to_our_processing"] == 0
        assert lat["samples_from_arrival_skips"] == 1
        assert lat["provider_lag_s"] >= 44.0
        assert out["funnel_by_provider_sport"]["baseball_mlb"]["refusals"][
            "QUOTE_STALE_ON_ARRIVAL"] == 1

        # (2) ours: received 40 s ago, stamped 2 s before that
        substitute(monkeypatch, slugs=[g.us_slug],
                   received_at=lambda now: now - 40.0,
                   odds_by_sport={"baseball_mlb": lambda now: [odds_event(
                       g, "ours", at=now - 40.0, stamp_age_s=2.0)]})
        out = await loop.cycle(conn)
        lat = out["latency"]
        assert lat["skipped_stale_on_arrival"] == 1
        assert lat["provider_stale_on_arrival"] == 0
        assert lat["stale_on_arrival_due_to_our_processing"] == 1
        assert lat["samples_from_arrival_skips"] == 1
        assert 1.5 <= lat["provider_lag_s"] <= 3.0
        assert lat["our_processing_s"] >= 40.0
        fd = loop._freshness_digest(out)
        assert fd["stale_on_arrival_due_to_our_processing"] == 1
        db = {r["provider_event_id"]: r for r in await _rows(conn)}
        late, ours = db[PREFIX + "late"], db[PREFIX + "ours"]
        assert late["first_refusal"] == ours["first_refusal"] == \
            "QUOTE_STALE_ON_ARRIVAL"
        assert late["provider_lag_s"] >= 44.0
        assert 1.5 <= ours["provider_lag_s"] <= 3.0
        assert ours["our_processing_s"] >= 40.0
        assert ours["quote_age_s"] >= 42.0
    finally:
        await clean(conn, g)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_fixture_dates_reach_the_scheduled_confirmation_and_refused_events_are_rows(
        monkeypatch):
    """D1 AND D3, TOGETHER. The venue lists France vs. Italy a WEEK after the
    provider's fixture. The names match; only the date separates them -- and
    the date reaches the confirmation only if `title_days` is passed (D1).
    Refused, the paid fetch's two events are still counted and each gets a
    REFUSED row, so the ledger reconciles (D3)."""
    conn = await _connect()
    key = "soccer_uefa_nations_league"

    async def board(conn, *, now=None):
        return {"read": True, "board": [("unl", 1)], "source": "us_premap",
                "evidence": "LIVE_READ", "evidence_age_s": 0.0,
                "titles": {"unl": ["France vs. Italy"]},
                "title_days": {"unl": {"France vs. Italy": "2026-10-09"}}}

    def soccer(now):
        stamp = _iso(now - 2)
        mk = lambda eid, h, a: {
            "id": PREFIX + eid, "home_team": h, "away_team": a,
            "commence_time": "2026-10-02T18:45:00Z",
            "bookmakers": [{"key": "pinnacle", "last_update": stamp,
                            "markets": [{"key": "h2h", "outcomes": [
                                {"name": h, "price": 2.5},
                                {"name": a, "price": 3.0},
                                {"name": "Draw", "price": 3.1}]}]}]}
        return [mk("fra", "France", "Italy"),
                mk("esp", "Spain", "Czech Republic")]

    try:
        await clean(conn)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value='true'", loop.CONTROL_KEY)
        substitute(monkeypatch, slugs=[], catalogue=("baseball_mlb", key),
                   odds_by_sport={key: soccer, "baseball_mlb": []})
        monkeypatch.setattr(loop, "venue_soccer_competitions", board)
        out = await loop.cycle(conn)
        cand = next(c for c in out["sports_selection"]["confirmed_by_provider"]
                    if c["key"] == key)
        assert cand["venue_title_days"] == {"France vs. Italy": "2026-10-09"}
        fn = out["funnel_by_provider_sport"][key]
        assert fn["mapping_confirmation"]["ok"] is False
        assert fn["mapping_confirmation"]["refusal"] == \
            loop.R_MAPPING_FIXTURES_DO_NOT_MATCH
        assert fn["provider_events"] == 2
        rows = [r for r in out["event_ledger"] if r["sport_key"] == key]
        assert len(rows) == 2
        assert {r["outcome"] for r in rows} == {"REFUSED"}
        assert {r["first_refusal"] for r in rows} == {
            loop.R_MAPPING_FIXTURES_DO_NOT_MATCH}
        assert out["refusals"][loop.R_MAPPING_FIXTURES_DO_NOT_MATCH] == 1
        per = out["candidate_outcomes"]["per_sport"][key]
        assert per == {"provider_events": 2, "rows": 2, "reconciles": True}
        assert len([r for r in await _rows(conn)
                    if r["provider_event_id"] in (PREFIX + "fra",
                                                  PREFIX + "esp")]) == 2
    finally:
        await clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_captures_mlb_event_traces_to_the_book_currency_refusal(
        monkeypatch):
    """THE REAL CANDIDATE, WITH THE REAL VENUE ROWS. Houston Astros v Chicago
    White Sox as the provider listed it (home, away, commence 21:10:26Z) and
    the venue's own two rows for `aec-mlb-cws-hou-2026-09-29`, written
    verbatim. The global catalogue row is absent here, so the venue-native path
    carries it: identity -> the SHORT row (Houston is the venue's short side)
    -> bid ladder, payout event Houston -- and the first check it cannot pass
    is the one production stops it at, VENUE_BOOK_CURRENCY_NOT_ESTABLISHED.
    The Pinnacle price is SYNTHETIC TEST EVIDENCE; nothing else is."""
    conn = await _connect()
    ev_slug = "mlb-cws-hou-2026-09-29"
    real_rows = [r for r in ROWS if r["event_slug"] == ev_slug]
    assert len(real_rows) == 2
    e = HOU
    try:
        await clean(conn)
        await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", ev_slug)
        for r in real_rows:
            await conn.execute(
                "INSERT INTO us_premap (identifier, event_slug, event_title, "
                " market_slug, question, kind, line, side_norm, event_keys, "
                " intent, signed, team_abbr, team_name, game_start, "
                " sports_type, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,"
                " $9,$10,$11,$12,$13,$14::timestamptz,$15, now())",
                r["market_slug"], r["event_slug"], r["event_title"],
                r["market_slug"], r["question"], r["kind"], r["line"],
                r["side_norm"], [], r["intent"], r["signed"], r["team_abbr"],
                r["team_name"],
                _dt.datetime.fromisoformat(r["game_start"]),
                r["sports_type"])
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value='true'", loop.CONTROL_KEY)

        def provider(now):
            stamp = _iso(now - 2)
            prices = [{"name": e["home"], "price": 1.80},
                      {"name": e["away"], "price": 2.10}]
            return [{"id": PREFIX + "trace-" + e["provider_event_id"],
                     "home_team": e["home"], "away_team": e["away"],
                     "commence_time": e["commence_time"],
                     "bookmakers": [{"key": "pinnacle", "last_update": stamp,
                                     "markets": [{"key": "h2h",
                                                  "last_update": stamp,
                                                  "outcomes": prices}]}]}]

        substitute(monkeypatch, slugs=["aec-mlb-cws-hou-2026-09-29"],
                   odds_by_sport={"baseball_mlb": provider})
        out = await loop.cycle(conn)
        row = next(r for r in out["event_ledger"]
                   if r["provider_event_id"].startswith(PREFIX + "trace-"))
        assert row["mapped_by"] == "VENUE_NATIVE"
        assert row["us_market_slug"] == "aec-mlb-cws-hou-2026-09-29"
        assert row["first_refusal"] == "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"
        led = next(x for x in out["mapped_candidate_ledger"]
                   if x.get("us_market_slug") == "aec-mlb-cws-hou-2026-09-29")
        assert led["first_refusal"] == "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"
        assert led["priced_outcome"] == "Houston Astros"
    finally:
        await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", ev_slug)
        await clean(conn)
        await conn.close()
