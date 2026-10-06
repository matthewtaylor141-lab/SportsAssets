"""P0 COVERAGE (2026-10-06): THE NBA AND NHL FULL-GAME MONEY LINES.

Production's first-loss census filed every PinnAPI-native basketball (and
hockey) seed at MAPPED, VENUE_NATIVE_FAMILY_NOT_SUPPORTED (SOFTWARE /
CAPABILITY): the venue-native resolver read no winner type for either family,
although the venue's catalogue lists exactly one full-game winner per game
(basketball_team_full_game_winner, hockey_team_full_game_winner).

Every claim below is read from a captured document:

  venue  tests/fixtures/pmus_nba_nhl_winner_listings_2026_10_06.json -- the
         venue's own listings (63 nba + 26 nhl open winner listings, ONE
         wording per league, each one contract with one LONG and one SHORT
         side, one per team)
  book   tests/fixtures/pinnacle_line_rules_2026_10_04.json -- Pinnacle's
         Basketball / Hockey sections (fetch-docs run 37234815185)

1. IDENTITY. Each rendering maps to exactly its own contract, for BOTH
   outcomes, with the two-way LONG/SHORT semantics (the intent selects the
   ladder; the payout event is the priced team; no complement); everything
   else refuses by name -- segment types, the 3-way hockey regulation winner,
   a draw, a one-team match, the other sport's same-named team.
2. PROBABILITY. The de-vig admits the money line for the NBA and the NHL
   ONLY (SUPPORTED_BY_LEAGUE), two outcomes, the same event as the payout;
   every other league -- and a draw-priced regulation set -- refuses.
3. COMPLETED-GAME GRADING. Both sides grade the game through every overtime
   (hockey: and the shootout), a period with no tie state.
4. STRICT SETTLEMENT. Overtime is ESTABLISHED; the postponement rule
   CONFLICTS (the book voids, the venue pays the last fair market price), so
   the strict comparison is INCOMPATIBLE by name -- never COMPATIBLE.
5. CENSUS / HELD READS admit the two types for the same two leagues.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_settlement_terms as T
from sportsassets import bettor_venue_mapping as vmap
from sportsassets import bettor_venue_native_identity as V
from sportsassets import bettor_venue_settlement as vset
from sportsassets import pinnapi_census as CEN
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as FR
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.workers import ext_pinnacle_loop as L

FIX = Path(__file__).parent / "fixtures"
VENUE = json.loads((FIX / "pmus_nba_nhl_winner_listings_2026_10_06.json")
                   .read_text())
BOOK = json.loads((FIX / "pinnacle_line_rules_2026_10_04.json").read_text())
LONG, SHORT = V.LONG, V.SHORT
TYPES = {"basketball": "basketball_team_full_game_winner",
         "hockey": "hockey_team_full_game_winner"}


def _epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _title(question):
    m = re.search(r"event (.+?) vs (.+?) scheduled", question)
    return "%s vs. %s" % (m.group(1), m.group(2))


def _rows(market, **over):
    """The us_premap rows production persists for one listing
    (workers/premap: team_name = the side's team.name folded, side_norm = its
    description, intent from `long`), and the census columns."""
    out = []
    for s in market["marketSides"]:
        out.append(dict({
            "market_slug": market["slug"], "identifier": market["slug"],
            "event_slug": market["slug"][len("aec-"):],
            "event_title": _title(market["question"]),
            "question": market["question"], "kind": "side",
            "sports_type": market["sportsMarketType"],
            "team_abbr": s["team"]["abbreviation"],
            "team_name": s["team"]["name"].lower(),
            "team_league": s["team"]["league"],
            "side_norm": s["description"].lower(),
            "intent": LONG if s["long"] else SHORT,
            "line": re.search(r"\d{1,2}:(\d{2})", market["question"]).group(1),
            "signed": None,
            "game_start": market["gameStartTime"]}, **over))
    return out


MARKETS = {m["slug"]: m for m in VENUE["markets"]}
ROWS = [r for m in VENUE["markets"] for r in _rows(m)]

#: (slug) -> (provider home, provider away), Pinnacle's renderings: the
#: venue's `ordering` home team is the provider's home team
PROVIDER = {
    "aec-nba-gs-lac-2026-10-04": ("Los Angeles Clippers",
                                  "Golden State Warriors"),
    "aec-nba-bkn-cha-2026-10-06": ("Charlotte Hornets", "Brooklyn Nets"),
    "aec-nba-lal-gs-2026-10-06": ("Golden State Warriors",
                                  "Los Angeles Lakers"),
    "aec-nba-lal-sac-2026-10-05": ("Sacramento Kings", "Los Angeles Lakers"),
    "aec-nba-den-uta-2026-10-06": ("Utah Jazz", "Denver Nuggets"),
    "aec-nhl-uta-nyr-2026-10-04": ("New York Rangers", "Utah Mammoth"),
    "aec-nhl-car-mon-2026-10-06": ("Montreal Canadiens",
                                   "Carolina Hurricanes"),
    "aec-nhl-nyi-nyr-2026-10-06": ("New York Rangers", "New York Islanders"),
    "aec-nhl-fla-la-2026-10-06": ("Los Angeles Kings", "Florida Panthers"),
    "aec-nhl-uta-nj-2026-10-06": ("New Jersey Devils", "Utah Mammoth"),
}


def _family(slug):
    return "basketball" if slug.split("-")[1] == "nba" else "hockey"


def _m(home, away, slug, *, family=None, priced="home", rows=None,
       league_tokens=None, offset=240.0):
    fam = family or _family(slug)
    return V.match_event(
        home=home, away=away,
        commence_epoch=_epoch(MARKETS[slug]["gameStartTime"]) + offset,
        family=fam, rows=ROWS if rows is None else rows,
        competition="pinnapi_%s" % fam,
        league_tokens=(league_tokens if league_tokens is not None
                       else (slug.split("-")[1],)),
        priced=priced)


# ═════════════════════════════════════════════════════════════════════
# 0 · THE CAPTURE IS WHAT THE TESTS SAY IT IS
# ═════════════════════════════════════════════════════════════════════

def test_the_venue_capture_is_one_wording_per_league_and_two_way():
    w = VENUE["wording_census"]["wordings"]
    (nba,) = w["nba/basketball_team_full_game_winner"]
    (nhl,) = w["nhl/hockey_team_full_game_winner"]
    assert nba["listings"] == 63 and nhl["listings"] == 26
    assert "Overtime is included if played." in nba["masked_description"]
    assert ("Overtime and any shootout are included if played."
            in nhl["masked_description"])
    assert VENUE["wording_census"]["hockey_team_regulation_winner_listed"] \
        == 0
    for m in VENUE["markets"]:
        assert m["sportsMarketType"] == TYPES[_family(m["slug"])]
        assert m["sportsMarketTypeV2"] == "SPORTS_MARKET_TYPE_MONEYLINE"
        assert sorted(s["long"] for s in m["marketSides"]) == [False, True]
        assert {s["team"]["ordering"] for s in m["marketSides"]} == \
            {"home", "away"}
        assert m["slug"] in PROVIDER


def test_the_family_winner_types_are_exact_values_with_full_game_scope():
    assert V.FAMILY_WINNER_TYPES["basketball"] == (TYPES["basketball"],)
    assert V.FAMILY_WINNER_TYPES["hockey"] == (TYPES["hockey"],)
    for t in TYPES.values():
        assert vmap.scope_from_v1(t)["scope"] == "FULL"
    for t in ("hockey_team_regulation_winner",
              "hockey_team_first_period_winner",
              "basketball_team_first_half_winner",
              "basketball_team_first_quarter_winner"):
        assert all(t not in ts for ts in V.FAMILY_WINNER_TYPES.values()), t


# ═════════════════════════════════════════════════════════════════════
# 1 · IDENTITY
# ═════════════════════════════════════════════════════════════════════

def test_each_rendering_maps_exactly_to_its_own_contract_both_outcomes():
    for slug, (home, away) in PROVIDER.items():
        m = MARKETS[slug]
        by_team = {s["team"]["ordering"]: s for s in m["marketSides"]}
        for priced, name in (("home", home), ("away", away)):
            got = _m(home, away, slug, priced=priced)
            assert got["ok"], (slug, priced, got.get("refusal"),
                               got.get("why"))
            assert got["us_market_slug"] == slug
            assert got["candidates"] == 1
            side = by_team[priced]
            # the row bought is the priced team's OWN row, with ITS intent
            assert got["priced_participant"] == side["team"]["name"].lower()
            assert got["intent"] == (LONG if side["long"] else SHORT)
            assert got["period_evidence"]["period"] == vmap.FULL_MATCH
            ident = V.identity_from_match(got, priced_outcome=name)
            assert ident["ok"] is True
            # LONG/SHORT selects the ladder, never the payout event
            assert ident["ladder_side"] == (
                "ASK" if got["intent"] == LONG else "BID")
            assert ident["payout_event"] == name
            assert ident["probability_event"] == name
            assert ident["payout_is_complement"] is False
            assert ident["period"] == "FULL_GAME"
        # both outcomes are the SAME contract, on its two opposite sides
        h = _m(home, away, slug, priced="home")
        a = _m(home, away, slug, priced="away")
        assert h["us_market_slug"] == a["us_market_slug"] == slug
        assert {h["intent"], a["intent"]} == {LONG, SHORT}


def test_the_provider_orientation_is_read_never_assumed():
    """The provider's home team is assigned by NAME. Swapping the provider's
    home and away swaps the orientation and the row each outcome buys."""
    slug = "aec-nba-gs-lac-2026-10-04"
    home, away = PROVIDER[slug]
    a = _m(home, away, slug)
    b = _m(away, home, slug)
    assert a["ok"] and b["ok"]
    assert a["assignment"] == {"home": "clippers", "away": "warriors"}
    assert b["assignment"] == {"home": "warriors", "away": "clippers"}
    assert a["intent"] == SHORT and b["intent"] == LONG
    assert a["orientation"] != b["orientation"]


REFUSED = (
    # one New York team in each of two events in the window: neither fixture
    ("New York Rangers", "Utah Mammoth", "aec-nhl-uta-nj-2026-10-06",
     V.R_ONE_TEAM_ONLY),
    ("Los Angeles Lakers", "Los Angeles Clippers",
     "aec-nba-lal-gs-2026-10-06", V.R_ONE_TEAM_ONLY),
    # a rendering this resolver does not bridge (no alias is invented: the
    # Mammoth's former name)
    ("New Jersey Devils", "Utah Hockey Club", "aec-nhl-uta-nj-2026-10-06",
     V.R_ONE_TEAM_ONLY),
    # nobody in the window
    ("Boston Bruins", "Buffalo Sabres", "aec-nhl-car-mon-2026-10-06",
     V.R_NO_EVENT),
)


def test_everything_else_refuses_by_name():
    for home, away, slug, code in REFUSED:
        got = _m(home, away, slug)
        assert not got["ok"] and got["refusal"] == code, (home, away, got)
        assert got.get("us_market_slug") is None


def test_the_other_sports_same_named_team_is_never_read():
    """Sacramento Kings (NBA) and Los Angeles Kings (NHL) share 'kings', and
    LA's NBA and NHL games tip off at the same instant on 2026-10-07. The
    family reads its own winner type only, and the league its own token."""
    lal = _m("Golden State Warriors", "Los Angeles Lakers",
             "aec-nba-lal-gs-2026-10-06")
    la = _m("Los Angeles Kings", "Florida Panthers",
            "aec-nhl-fla-la-2026-10-06")
    assert lal["us_market_slug"] == "aec-nba-lal-gs-2026-10-06"
    assert la["us_market_slug"] == "aec-nhl-fla-la-2026-10-06"
    # the NHL fixture asked as basketball, the NBA one as hockey: nothing
    x = _m("Los Angeles Kings", "Florida Panthers",
           "aec-nhl-fla-la-2026-10-06", family="basketball",
           league_tokens=("nba",))
    assert x["refusal"] == V.R_NO_EVENT
    y = _m("Sacramento Kings", "Los Angeles Lakers",
           "aec-nba-lal-sac-2026-10-05", family="hockey",
           league_tokens=("nhl",))
    assert y["refusal"] == V.R_NO_EVENT
    # and a hockey event of another league in the window is set aside
    khl = [dict(r, event_slug=r["event_slug"].replace("nhl-", "khl-"),
                market_slug=r["market_slug"].replace("-nhl-", "-khl-"))
           for r in _rows(MARKETS["aec-nhl-car-mon-2026-10-06"])]
    z = _m("Montreal Canadiens", "Carolina Hurricanes",
           "aec-nhl-car-mon-2026-10-06", rows=khl)
    assert z["refusal"] == V.R_NO_EVENT
    assert z["set_aside"]["other_competition"] == \
        ["khl-car-mon-2026-10-06"]


@pytest.mark.parametrize("seg", [
    "hockey_team_regulation_winner", "hockey_team_first_period_winner",
    "hockey_team_full_game_spread"])
def test_segment_regulation_and_line_types_refuse(seg):
    slug = "aec-nhl-uta-nyr-2026-10-04"
    rows = _rows(MARKETS[slug], sports_type=seg)
    got = _m(*PROVIDER[slug], slug, rows=rows)
    assert got["ok"] is False and got["refusal"] == V.R_NO_EVENT


@pytest.mark.parametrize("seg", [
    "basketball_team_first_half_winner", "basketball_team_first_quarter_winner",
    "basketball_team_full_game_total"])
def test_basketball_segment_types_refuse(seg):
    slug = "aec-nba-gs-lac-2026-10-04"
    rows = _rows(MARKETS[slug], sports_type=seg)
    got = _m(*PROVIDER[slug], slug, rows=rows)
    assert got["ok"] is False and got["refusal"] == V.R_NO_EVENT


def test_a_draw_and_a_malformed_contract_refuse():
    slug = "aec-nhl-uta-nyr-2026-10-04"
    got = _m(*PROVIDER[slug], slug, priced="draw")
    assert got["refusal"] == V.R_NO_DRAW_IN_FAMILY
    # both sides LONG: which side a row buys is not shown
    rows = [dict(r, intent=LONG) for r in _rows(MARKETS[slug])]
    got = _m(*PROVIDER[slug], slug, rows=rows)
    assert got["refusal"] == V.R_CONTRACT_SIDES
    # a full-game type whose identifier names a segment conflicts
    rows = [dict(r, market_slug=r["market_slug"] + "-1p")
            for r in _rows(MARKETS[slug])]
    got = _m(*PROVIDER[slug], slug, rows=rows)
    assert got["ok"] is False


def test_the_cycle_maps_the_family_a_native_seed_carries():
    """A PinnAPI-native seed reaches the resolver with its sport family's
    name (pinnapi_census.sport_family_of) and the de-vig's spelling."""
    assert CEN.sport_family_of(3) == "basketball"
    assert CEN.sport_family_of(4) == "hockey"
    assert set(TYPES) <= set(V.FAMILY_WINNER_TYPES)


# ═════════════════════════════════════════════════════════════════════
# 2 · PROBABILITY: THE NBA / NHL ONLY, TWO OUTCOMES, THE PAYOUT'S EVENT
# ═════════════════════════════════════════════════════════════════════

NOW = 1_790_000_000.0


def _val(slug, outcomes, selection, *, league=None, family=None):
    fam = family or _family(slug)
    contract = {"venue": "PMUS", "sport_family": fam, "market": "h2h",
                "selection": selection, "us_market_slug": slug,
                "event_key": "pinnapi:1", "period": "FULL_GAME",
                "line": None, "settlement_rule": vset.BOOK_SETTLEMENT.get(fam)}
    if league is not None:
        contract["league"] = league
    quote = {"book": devig.BOOK, "outcomes": outcomes,
             "observed_at": NOW - 3.0, "received_at": NOW - 2.0,
             "event_key": "pinnapi:1", "period": "FULL_GAME", "line": None,
             "settlement_rule": vset.BOOK_SETTLEMENT.get(fam)}
    return devig.valuation(contract=contract, quote=quote, now=NOW)


def test_the_nba_and_nhl_money_lines_are_admitted_by_league_only():
    assert devig.expected_outcomes("basketball", "h2h", league="nba") == 2
    assert devig.expected_outcomes("hockey", "h2h", league="nhl") == 2
    for fam, lg in (("basketball", "wnba"), ("basketball", "xbl"),
                    ("basketball", None), ("hockey", "xhl"),
                    ("hockey", "wnba"), ("hockey", None)):
        assert devig.expected_outcomes(fam, "h2h", league=lg) is None
    # the family-wide set is untouched
    assert ("basketball", "h2h") not in devig.SUPPORTED
    assert ("hockey", "h2h") not in devig.SUPPORTED


def test_the_probability_is_the_payout_events_two_way_price():
    slug = "aec-nba-gs-lac-2026-10-04"
    home, away = PROVIDER[slug]
    odds = {home: 1.62, away: 2.45}
    for sel in (home, away):
        v = _val(slug, odds, sel)
        assert v["refusals"] == [], v
        assert v["expected_outcomes"] == 2
        assert v["admitted_for_league"] == "basketball/h2h@nba"
        assert v["probability"] == pytest.approx(v["devigged"][sel])
        assert v.get("conditional_on") is None   # no tie state to condition
    assert sum(_val(slug, odds, home)["devigged"].values()) == \
        pytest.approx(1.0)
    v = _val("aec-nhl-uta-nyr-2026-10-04",
             {"New York Rangers": 1.70, "Utah Mammoth": 2.25},
             "Utah Mammoth")
    assert v["refusals"] == [] and v["admitted_for_league"] == \
        "hockey/h2h@nhl"


def test_another_league_a_disagreeing_league_or_a_draw_priced_set_refuses():
    odds = {"A": 1.8, "B": 2.1}
    assert devig.R_UNSUPPORTED_MARKET in _val(
        "aec-xhl-cska-ska-2026-10-06", odds, "A", family="hockey")["refusals"]
    assert devig.R_UNSUPPORTED_MARKET in _val(
        "aec-wnba-ny-atl-2026-10-06", odds, "A",
        family="basketball")["refusals"]
    # the structured league and the slug disagree: no league at all
    assert devig.R_UNSUPPORTED_MARKET in _val(
        "aec-nba-gs-lac-2026-10-04", odds, "A", league="wnba")["refusals"]
    # the 3-way REGULATION set is not the game line: three of two
    v = _val("aec-nhl-uta-nyr-2026-10-04",
             {"New York Rangers": 2.2, "Utah Mammoth": 3.0, "Draw": 4.1},
             "Utah Mammoth")
    assert v["probability"] is None
    assert devig.R_INCOMPLETE_OUTCOMES in v["refusals"]


# ═════════════════════════════════════════════════════════════════════
# 3 · COMPLETED-GAME GRADING: THROUGH EVERY OVERTIME (AND THE SHOOTOUT)
# ═════════════════════════════════════════════════════════════════════

def test_the_book_sentences_are_the_captured_words():
    sec = BOOK["sections"]
    assert T.CAPTURE_RUN_LINE_RULES["sha256"] == BOOK["page_sha256"]
    assert T.CAPTURE_RUN_LINE_RULES["retrieved_at"] == BOOK["retrieved_at"]
    assert str(BOOK["captured_in"]["run_id"]) in \
        T.CAPTURE_RUN_LINE_RULES["job"]
    assert T._Q_BK_OVERTIME in sec["basketball"]
    assert any(T._Q_BK_MINIMUM in line for line in sec["basketball"])
    assert T._Q_HK_OVERTIME in sec["hockey"]
    assert any(T._Q_HK_MINIMUM in line for line in sec["hockey"])
    assert T._Q_GENERAL_NOT_STARTED in sec["general_rules"]
    for fam in ("basketball", "hockey"):
        pre = T.BOOK_TERMS[(fam, "h2h", T.CTX_PRE_GAME)]
        assert pre == T.BOOK_TERMS[(fam, "h2h", T.CTX_LIVE)]
        adm = T.admit_book_terms(pre)
        assert adm.get("ok") is True and not adm.get("rejected"), adm
        for rec in pre.values():
            assert rec["cite"]["page_sha256"] == BOOK["page_sha256"]
            assert rec["cite"]["quote"] in " ".join(
                sec[fam] + sec["general_rules"])
        # only what a sentence states is declared
        assert set(pre) == {T.C_FULL, T.C_OVERTIME, T.C_STOPPED_EARLY,
                            T.C_NOT_PLAYED}
        assert vset.BOOK_SETTLEMENT[fam] == L.PINNACLE_SETTLEMENT[fam]


def test_both_sides_grade_the_completed_game_identically():
    for m in VENUE["markets"]:
        fam, lg = _family(m["slug"]), m["slug"].split("-")[1]
        book = PB.book_grading_period(fam, lg)
        venue = PB.venue_grading_period(fam, m["description"], lg)
        assert venue["refusal"] is None, (m["slug"], venue)
        assert venue["period"] == book["period"]
        assert book["page_sha256"] == BOOK["page_sha256"]
    assert PB.book_grading_period("basketball", "nba")["period"] == \
        PB.GP_BASKETBALL_NBA
    assert PB.book_grading_period("hockey", "nhl")["period"] == \
        PB.GP_HOCKEY_NHL
    assert PB.GP_BASKETBALL_NBA != PB.GP_HOCKEY_NHL
    # a completed NBA / NHL game cannot end level through overtime (and the
    # shootout): no tie state to price, unlike the NFL
    for fam in ("basketball", "hockey"):
        assert vset.tie_is_reachable(sport_family=fam,
                                     overtime="OT_INCLUDED")["permits_tie"] \
            is False


NBA_TEXT = MARKETS["aec-nba-gs-lac-2026-10-04"]["description"]
NHL_TEXT = MARKETS["aec-nhl-uta-nyr-2026-10-04"]["description"]


def test_any_other_wording_league_or_family_establishes_nothing():
    # another league of the family has no book grading here
    for fam, lg in (("basketball", "wnba"), ("basketball", "xbl"),
                    ("hockey", "xhl"), ("basketball", None)):
        assert PB.book_grading_period(fam, lg) is None
    # an NHL text silent on the shootout is not the NHL wording
    no_so = NHL_TEXT.replace("Overtime and any shootout are included if "
                             "played.", "Overtime is included if played.")
    assert PB.venue_grading_period("hockey", no_so, "nhl")["refusal"] == \
        PB.R_GP_UNKNOWN
    # a regulation-only text is a different period
    reg = NHL_TEXT + " This market is graded on regulation time only."
    assert PB.venue_grading_period("hockey", reg, "nhl")["refusal"] == \
        PB.R_GP_MISMATCH
    reg = NBA_TEXT.replace("Overtime is included if played.",
                           "Overtime is not included.")
    assert PB.venue_grading_period("basketball", reg, "nba")["refusal"] == \
        PB.R_GP_MISMATCH
    # each family's text under the other family's template
    assert PB.venue_grading_period("hockey", NBA_TEXT, "nhl")["refusal"] == \
        PB.R_GP_UNKNOWN
    assert PB.venue_grading_period("basketball", NHL_TEXT,
                                   "nba")["refusal"] == PB.R_GP_UNKNOWN
    assert PB.venue_grading_period("hockey", "", "nhl")["refusal"] == \
        PB.R_GP_TEXT_ABSENT


# ═════════════════════════════════════════════════════════════════════
# 4 · STRICT SETTLEMENT: OVERTIME AGREES, POSTPONEMENT CONFLICTS
# ═════════════════════════════════════════════════════════════════════

def _attest(fam, text):
    return vset.attest(sport_family=fam, market="h2h",
                       venue_evidence={"rules_text": text,
                                       "rules_field": "description",
                                       "rules_source": VENUE["_source"]},
                       book_evidence={"outcome_names": ["H", "A"]},
                       observed_at=1.0)


def test_overtime_is_established_and_postponement_conflicts_by_name():
    for m in VENUE["markets"]:
        fam = _family(m["slug"])
        a = _attest(fam, m["description"])
        ot, void, draw = (a["rules"]["overtime"], a["rules"]["void"],
                          a["rules"]["draw"])
        assert draw["applicable"] is False                 # no tie state
        assert ot["established"] is True, (m["slug"], ot)
        assert ot["evidence_class"] == vset.EV_VENUE_RULES_TEXT
        assert void["established"] is False
        assert void["refusal"] == vset.R_VOID_CONFLICTS
        assert void["mismatched_conditions"] == [T.C_NOT_PLAYED]
        per = void["terms_comparison"]["per_condition"][T.C_NOT_PLAYED]
        assert (per["book_payout"], per["venue_payout"]) == (
            T.PAY_STAKE_BACK, T.PAY_LAST_FAIR_MARKET_PRICE)
        assert a["unmet"] == [vset.R_VOID_CONFLICTS]
        assert a["overall_established"] is False
        b = vset.settlement_blockers(a)
        assert b["established"] is False and b["verdict"] == T.INCOMPATIBLE
        assert any(x.startswith(vset.B_INCOMPATIBLE) for x in b["blockers"])
        assert not any(x.startswith(vset.B_CONTEXT) for x in b["blockers"])
        # the book conditions it does not state are NAMED, never assumed
        assert "%s:%s" % (vset.B_BOOK_SILENT, T.C_CALLED_FINAL) in \
            b["blockers"]


def test_prose_that_excludes_overtime_or_the_shootout_would_conflict():
    a = _attest("basketball", "This market settles on regulation time only.")
    assert a["rules"]["overtime"]["refusal"] == vset.R_OVERTIME_CONFLICTS
    a = _attest("hockey", "Overtime is included. The shootout will not count.")
    assert a["rules"]["overtime"]["refusal"] == vset.R_OVERTIME_CONFLICTS
    # overtime without the shootout says nothing about a level game
    a = _attest("hockey", "Overtime is included if played.")
    assert a["rules"]["overtime"]["established"] is False
    assert a["rules"]["overtime"]["refusal"] == vset.R_OVERTIME_UNKNOWN


# ═════════════════════════════════════════════════════════════════════
# 5 · CENSUS AND HELD READS: THE SAME TWO LEAGUES
# ═════════════════════════════════════════════════════════════════════

def test_the_census_admits_nba_and_nhl_rows_and_no_other_league():
    for r in ROWS:
        assert r["line"] not in (None, "")
        got = CEN.family_of(r["kind"], r["line"], r["sports_type"], r)
        assert got == ("MONEYLINE", True, "PROVED_CLOCK_ARTIFACT"), (r, got)
    r = _rows(MARKETS["aec-nhl-uta-nyr-2026-10-04"])[0]
    for over in ({"team_league": "xhl",
                  "identifier": "aec-xhl-uta-nyr-2026-10-04"},
                 {"team_league": "wnba"},
                 {"team_league": None, "identifier": None}):
        got = CEN.family_of(r["kind"], r["line"], r["sports_type"],
                            dict(r, **over))
        assert got == ("MONEYLINE", False, CEN.R_WINNER_LEAGUE_NOT_ADMITTED)
    # a real line the side states is never erased
    assert CEN.family_of(r["kind"], "1.5", r["sports_type"], r) == \
        ("UNKNOWN", False, "WINNER_WITH_UNEXPECTED_LINE")
    # the regulation winner and the segments stay unproved
    assert CEN.family_of("side", None, "hockey_team_regulation_winner")[1] \
        is False
    assert CEN.family_of("side", None, "hockey_team_first_period_winner") \
        == ("PERIOD", False, "PERIOD_GRADING_NOT_PROVED")
    assert CEN.family_of("side", None, "basketball_team_first_half_winner") \
        == ("PERIOD", False, "PERIOD_GRADING_NOT_PROVED")
    # the refusal is classified
    assert RT.classify(CEN.R_WINNER_LEAGUE_NOT_ADMITTED)["classified"]
    for t in TYPES.values():
        assert t in FR.HELD_FULL_GAME_TYPES
    assert "hockey_team_regulation_winner" not in FR.HELD_FULL_GAME_TYPES


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _held_cache(start, prices):
    market = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
              "period": 0, "status": "open", "prices": prices}
    at = start - 3600
    ev = {"id": 91, "startTime": _iso(start), "isLive": False,
          "participants": [{"name": "Los Angeles Clippers",
                            "alignment": "home"},
                           {"name": "Golden State Warriors",
                            "alignment": "away"}],
          "markets": [market]}
    c = F.FeedCache()
    e = c.new_connection([("prematch", 3)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 3,
             "ts": (at - 60) * 1000, "events": [ev]}, epoch=e,
            received_ms=(at - 60) * 1000 + 5)
    moved = json.loads(json.dumps(market))
    moved["prices"][0]["price"] += 5
    c.apply({"type": "prematch_markets", "matchup_id": 91, "data": [moved],
             "sport_id": 3, "ts": (at - 2) * 1000}, epoch=e,
            received_ms=(at - 2) * 1000 + 5)
    return c, at


FULL_NAME = {"warriors": "golden state warriors",
             "clippers": "los angeles clippers"}


def test_a_held_nba_contract_is_read_from_the_feed_and_another_league_not(
        monkeypatch):
    """The held read admits the NBA type for the NBA only: with the
    structured name equal to the feed's, the type and league admission are
    what decide. The production-shaped row (nickname only) resolves too, by
    the split-name rule (section 6)."""
    import types
    slug = "aec-nba-gs-lac-2026-10-04"
    prod = [dict(r, game_start=_epoch(r["game_start"]))
            for r in _rows(MARKETS[slug])]
    for league, admitted in (("nba", True), ("wnba", False)):
        rows = [dict(r, team_league=league,
                     team_name=FULL_NAME[r["team_name"]],
                     identifier=slug.replace("-nba-", "-%s-" % league),
                     event_slug=r["event_slug"].replace("nba-", league + "-"))
                for r in prod]
        gsw = next(r for r in rows if r["side_norm"] == "warriors")
        cache, at = _held_cache(gsw["game_start"],
                                [{"designation": "home", "price": -160},
                                 {"designation": "away", "price": 140}])
        monkeypatch.setitem(FR._STATE, "owner", types.SimpleNamespace(
            cache=cache, sport_ids=[3]))
        view = CEN.feed_event_view(cache)
        got = FR.held_quote(gsw, event_rows=rows,
                            payout_event="Golden State Warriors",
                            payout_is_complement=False, at=at,
                            max_age_s=30.0, sport_ids=[3], synced=True,
                            view=view)
        if not admitted:
            assert got["ok"] is False and got["reason"] == CEN.S_UNSUPPORTED
            continue
        assert got["ok"] is True, got
        assert got["designation"] == "away" and got["sport_id"] == 3
        dv = got["devig"]
        assert dv["outcomes"] == 2 and set(dv["raw_odds"]) == {"home", "away"}
        assert got["p"] == pytest.approx(dv["devigged"]["away"])
        assert "conditional_on" not in got
        nc = FR.held_quote(gsw, event_rows=rows,
                           payout_event="NOT(Golden State Warriors)",
                           payout_is_complement=True, at=at, max_age_s=30.0,
                           sport_ids=[3], synced=True, view=view)
        assert nc["ok"] and nc["p"] == pytest.approx(1.0 - got["p"])
        # the 30 s rule is unchanged
        stale = FR.held_quote(gsw, event_rows=rows,
                              payout_event="Golden State Warriors",
                              payout_is_complement=False, at=at + 31.0,
                              max_age_s=30.0, sport_ids=[3], synced=True,
                              view=view)
        assert stale["ok"] is False
        # the production-shaped row (nickname only) reads the same number
        pg = next(r for r in prod if r["team_name"] == "warriors")
        same = FR.held_quote(pg, event_rows=prod,
                             payout_event="Golden State Warriors",
                             payout_is_complement=False, at=at,
                             max_age_s=30.0, sport_ids=[3], synced=True,
                             view=view)
        assert same["ok"] is True and same["p"] == pytest.approx(got["p"])


# ═════════════════════════════════════════════════════════════════════
# 6 · HELD POSITIONS: THE SPLIT-NAME TEAM RECORD RESOLVES, BY THE
#     DISCOVERY MATCHER'S RULES, FOR THE NBA / NHL ONLY
# ═════════════════════════════════════════════════════════════════════

SID = {"basketball": 3, "hockey": 4}
FEED_ID = {slug: 500 + i for i, slug in enumerate(sorted(PROVIDER))}
AT = 1_791_000_000.0


def _feed(at, fixtures):
    """A PinnAPI cache holding `fixtures` [(id, sport id, start, home, away)],
    each with a fresh period-0 two-way money line."""
    c = F.FeedCache()
    e = c.new_connection([("prematch", 3), ("prematch", 4)])
    for sid in (3, 4):
        evs = [{"id": fid, "startTime": _iso(start), "isLive": False,
                "participants": [{"name": h, "alignment": "home"},
                                 {"name": w, "alignment": "away"}],
                "markets": [{"key": F.FULL_GAME_MONEYLINE_KEY,
                             "type": "moneyline", "period": 0,
                             "status": "open",
                             "prices": [{"designation": "home",
                                         "price": -150},
                                        {"designation": "away",
                                         "price": 130}]}]}
               for fid, s, start, h, w in fixtures if s == sid]
        c.apply({"type": "snapshot", "stream": "prematch", "sport_id": sid,
                 "ts": (at - 60) * 1000, "events": evs}, epoch=e,
                received_ms=(at - 60) * 1000 + 5)
        # an OBSERVED price change, so the quote's age is known
        for ev in evs:
            moved = json.loads(json.dumps(ev["markets"][0]))
            moved["prices"][0]["price"] += 5
            c.apply({"type": "prematch_markets", "matchup_id": ev["id"],
                     "data": [moved], "sport_id": sid,
                     "ts": (at - 2) * 1000}, epoch=e,
                    received_ms=(at - 2) * 1000 + 5)
    return c


def _all_fixtures():
    return [(FEED_ID[slug], SID[_family(slug)],
             _epoch(MARKETS[slug]["gameStartTime"]), h, w)
            for slug, (h, w) in PROVIDER.items()]


def _prod(slug, **over):
    return [dict(r, **dict({"game_start": _epoch(r["game_start"])}, **over))
            for r in _rows(MARKETS[slug])]


def _held(row, rows, payout, view, *, sport_ids=(3, 4)):
    return FR.held_quote(row, event_rows=rows, payout_event=payout,
                         payout_is_complement=False, at=AT, max_age_s=30.0,
                         sport_ids=list(sport_ids), synced=True, view=view)


def test_every_held_nba_nhl_position_resolves_to_its_own_event_and_side(
        monkeypatch):
    """All ten captured listings, both sides, on the production-shaped
    catalogue rows (team_name = the nickname): each held position finds
    EXACTLY its own feed fixture -- among NBA and NHL games starting at the
    same instants, both New York NHL teams, the NBA's and the NHL's Kings --
    and its own designation."""
    import types
    cache = _feed(AT, _all_fixtures())
    monkeypatch.setitem(FR._STATE, "owner", types.SimpleNamespace(
        cache=cache, sport_ids=[3, 4]))
    view = CEN.feed_event_view(cache)
    for slug, (home, away) in PROVIDER.items():
        rows = _prod(slug)
        for side in MARKETS[slug]["marketSides"]:
            row = next(r for r in rows
                       if r["team_name"] == side["team"]["name"].lower())
            payout = home if side["team"]["ordering"] == "home" else away
            got = _held(row, rows, payout, view)
            assert got["ok"] is True, (slug, side, got)
            assert got["feed_event_id"] == FEED_ID[slug], (slug, got)
            assert got["sport_id"] == SID[_family(slug)]
            assert got["designation"] == side["team"]["ordering"]
        state, eid, sid = CEN.contract_match(
            rows[0], rows, view, subscribed_sports={3, 4}, synced=True)
        assert (state, eid, sid) == (CEN.S_SUPPORTED, FEED_ID[slug],
                                     SID[_family(slug)])


def test_the_held_event_id_path_resolves_the_split_name_record(monkeypatch):
    import types
    cache = _feed(AT, _all_fixtures())
    monkeypatch.setitem(FR._STATE, "owner", types.SimpleNamespace(
        cache=cache, sport_ids=[3, 4]))

    class _Conn:
        def __init__(self, rows):
            self.rows = rows

        async def fetchrow(self, sql, slug):
            return dict(self.rows[0])

        async def fetch(self, sql, *args):
            return [dict(r) for r in self.rows]

    for slug in ("aec-nhl-nyi-nyr-2026-10-06", "aec-nba-lal-gs-2026-10-06",
                 "aec-nhl-fla-la-2026-10-06", "aec-nba-lal-sac-2026-10-05"):
        eid, why = asyncio.run(FR.held_event_id(_Conn(_prod(slug)), slug))
        assert (eid, why) == (FEED_ID[slug], None), slug


def test_ambiguous_and_unknown_names_refuse_by_name():
    slug = "aec-nba-gs-lac-2026-10-04"
    start = _epoch(MARKETS[slug]["gameStartTime"])
    rows = _prod(slug)
    gsw = next(r for r in rows if r["team_name"] == "warriors")
    # the same fixture listed twice by the feed: two candidates, no choice
    twice = CEN.feed_event_view(_feed(AT, [
        (1, 3, start, "Los Angeles Clippers", "Golden State Warriors"),
        (2, 3, start + 600, "Los Angeles Clippers", "Golden State Warriors")]))
    got = _held(gsw, rows, "Golden State Warriors", twice)
    assert got["ok"] is False and got["reason"] == CEN.S_AMBIGUOUS
    # a feed fixture whose names fit the two records BOTH ways round
    nyr = [dict(r, team_name=n) for r, n in zip(
        _prod("aec-nhl-nyi-nyr-2026-10-06"), ("rangers", "new york rangers"))]
    both = CEN.feed_event_view(_feed(AT, [
        (3, 4, nyr[0]["game_start"], "New York Rangers", "Rangers")]))
    assert CEN.split_name_identity(nyr, 4, nyr[0]["game_start"],
                                   both[4]) == (CEN.S_AMBIGUOUS, None)
    # a name the record does not carry: no alias is invented
    uta = _prod("aec-nhl-uta-nj-2026-10-06")
    old = CEN.feed_event_view(_feed(AT, [
        (4, 4, uta[0]["game_start"], "New Jersey Devils",
         "Utah Hockey Club")]))
    got = _held(uta[0], uta, "Utah Hockey Club", old)
    assert got["ok"] is False and got["reason"] == CEN.S_NO_FEED_EVENT
    # the LA teams: a Lakers record never fits the Clippers, even with the
    # venue's truncated place name on the record
    lal = [dict(r, team_safe_name=("los angeles l" if r["team_name"] ==
                                   "lakers" else "golden state"))
           for r in _prod("aec-nba-lal-gs-2026-10-06")]
    clip = CEN.feed_event_view(_feed(AT, [
        (5, 3, lal[0]["game_start"], "Golden State Warriors",
         "Los Angeles Clippers")]))
    assert CEN.split_name_identity(lal, 3, lal[0]["game_start"],
                                   clip[3]) == (CEN.S_NO_FEED_EVENT, None)
    # the NBA's Kings are not in the NHL's sport id, and the reverse
    la = _prod("aec-nhl-fla-la-2026-10-06")
    nba_kings = CEN.feed_event_view(_feed(AT, [
        (6, 3, la[0]["game_start"], "Sacramento Kings", "Florida Panthers")]))
    got = _held(la[0], la, "Los Angeles Kings", nba_kings)
    assert got["ok"] is False and got["reason"] == CEN.S_NO_FEED_EVENT
    sac = _prod("aec-nba-lal-sac-2026-10-05")
    nhl_kings = CEN.feed_event_view(_feed(AT, [
        (7, 4, sac[0]["game_start"], "Los Angeles Kings",
         "Los Angeles Lakers")]))
    got = _held(sac[0], sac, "Sacramento Kings", nhl_kings)
    assert got["ok"] is False and got["reason"] == CEN.S_NO_FEED_EVENT


def test_other_leagues_and_sports_are_unchanged():
    """Outside the NBA / NHL the exact structured comparison alone answers,
    as before: a nickname-only record of another league (the same rows
    relabelled WNBA / KHL) stays NO_FEED_EVENT -- not a containment match,
    not a different refusal."""
    view = CEN.feed_event_view(_feed(AT, _all_fixtures()))
    for slug, lg in (("aec-nba-gs-lac-2026-10-04", "wnba"),
                     ("aec-nhl-uta-nyr-2026-10-04", "khl")):
        tok = slug.split("-")[1]
        rows = _prod(slug, team_league=lg,
                     identifier=slug.replace("-%s-" % tok, "-%s-" % lg),
                     event_slug=slug[4:].replace(tok + "-", lg + "-", 1))
        got = _held(rows[0], rows, PROVIDER[slug][0], view)
        assert got["ok"] is False and got["reason"] == CEN.S_NO_FEED_EVENT
        sid = SID[_family(slug)]
        assert CEN.split_name_identity(rows, sid, rows[0]["game_start"],
                                       view[sid]) == \
            (CEN.S_NO_FEED_EVENT, None)
    assert set(CEN.SPLIT_NAME_LEAGUES) == {3, 4}
    assert CEN.split_name_identity(_prod("aec-nba-gs-lac-2026-10-04"), 5,
                                   0.0, []) == (CEN.S_NO_FEED_EVENT, None)


def test_the_census_counts_the_split_name_events_like_the_held_read():
    view = CEN.feed_event_view(_feed(AT, _all_fixtures()))
    rows = [dict(r, game_start=_epoch(r["game_start"])) for r in ROWS]
    got = CEN.census(rows, view, subscribed_sports={3, 4}, synced=True,
                     now=AT)
    states = got["by_sport_family_phase_state"]
    assert sum(n for k, n in states.items()
               if k.endswith("|" + CEN.S_SUPPORTED)) == len(rows), states
