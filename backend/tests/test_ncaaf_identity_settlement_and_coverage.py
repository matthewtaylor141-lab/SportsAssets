"""NCAAF FOLLOW-UP (cand22 P0-A): ONE LEAGUE IDENTITY, NAMES THAT NEVER
CROSS-MAP, CAPTURED SETTLEMENT TERMS, AND A CENSUS THAT FILES NCAAF HONESTLY.

1. LEAGUE IDENTITY. `cfb` <-> `americanfootball_ncaaf` <-> "NCAAF" is read
   from ONE place (ext_pinnacle_loop.provider_key_for_venue_token) by the
   cycle, the coverage census and coverage_integrity, and the census no longer
   files a REQUESTED league (football, soccer) as OUTSIDE_MANDATE.
2. NAMES, on production-shaped venue rows (team_name / side_norm of
   2026-10-03): ranked prefixes, St./State, Miami (FL)/(OH) with the venue's
   double space, UL Monroe / Louisiana-Monroe. Each maps exactly or refuses
   by name; Ohio/Ohio State and Miami FL/Miami OH never cross-map.
3. SETTLEMENT, from the two captured documents (fixtures with run ids and
   hashes): overtime agrees and is ESTABLISHED; abandonment/postponement
   CONFLICTS (book voids, venue pays the last fair market price) and stays
   refused by that name. Football is NOT added to the de-vig set; the book's
   priced set is recorded on the refused valuation so the measurement accrues.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

import pytest

from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_settlement_terms as T
from sportsassets import bettor_venue_native_identity as V
from sportsassets import bettor_venue_settlement as vset
from sportsassets.agents import coverage as COV
from sportsassets.agents import coverage_integrity as CI
from sportsassets.workers import ext_pinnacle_loop as L

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")
FIX = Path(__file__).parent / "fixtures"
NCAAF = "americanfootball_ncaaf"
LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
T0 = _dt.datetime(2026, 10, 3, 19, 30, tzinfo=_dt.timezone.utc)


# ═════════════════════════════════════════════════════════════════════
# 1 · ONE LEAGUE IDENTITY
# ═════════════════════════════════════════════════════════════════════

def test_cfb_is_ncaaf_everywhere():
    assert L.provider_key_for_venue_token("cfb") == NCAAF
    assert L.provider_key_for_venue_token("CFB") == NCAAF
    assert L.venue_league_tokens(NCAAF) == ("cfb",)
    assert L.family_for_provider_key(NCAAF) == "football"
    assert CI.catalogue_token_map()["cfb"] == NCAAF
    assert CI.league_name(NCAAF) == "NCAAF"
    lg = COV.league_of_row({"event_slug": "cfb-ohiost-iowa-2026-10-03",
                            "market_slug": "aec-cfb-ohiost-iowa-2026-10-03"})
    assert lg == {"token": "cfb", "provider_key": NCAAF, "lane_maps": True,
                  "league_name": "NCAAF"}
    # cand24: nfl IS mapped by the lane now, to its OWN key -- never NCAAF's
    assert L.provider_key_for_venue_token("nfl") == "americanfootball_nfl"
    nfl = COV.league_of_row({"event_slug": "nfl-ari-nyg-2026-10-04"})
    assert nfl["lane_maps"] is True and nfl["league_name"] == "NFL"
    # every lane token round-trips through the shared identity
    for tok, key in list(L.VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY.items()) + \
            list(L.VENUE_TOKEN_TO_PROVIDER_KEY.items()):
        assert L.provider_key_for_venue_token(tok) == key
        assert CI.catalogue_token_map()[tok] == key
        assert tok in L.venue_league_tokens(key)


def _row(slug, sports_type, event):
    return {"market_slug": slug, "event_slug": event, "event_title": "A vs. B",
            "question": "A vs. B", "sports_type": sports_type}


CFB_ML = _row("aec-cfb-ohiost-iowa-2026-10-03",
              "football_team_full_game_winner", "cfb-ohiost-iowa-2026-10-03")
NFL_ML = _row("aec-nfl-ari-nyg-2026-10-04", "football_team_full_game_winner",
              "nfl-ari-nyg-2026-10-04")
CFB_1H = _row("atc-cfb-ohiost-iowa-2026-10-03-winner-1h-ohiost",
              "football_team_first_half_winner", "cfb-ohiost-iowa-2026-10-03")
UNL_ML = _row("atc-unl-fra-ita-2026-10-02-fra", "soccer_team_full_time_winner",
              "unl-fra-ita-2026-10-02")
ARG2_ML = _row("atc-arg2-atl-agro-2026-10-02-atl",
               "soccer_team_full_time_winner", "arg2-atl-agro-2026-10-02")
REQUESTED = ["baseball_mlb", NCAAF, "soccer_uefa_nations_league",
             "soccer_brazil_serie_b"]


def test_a_requested_league_is_inside_the_mandate_and_others_are_named():
    m = COV.mandate(REQUESTED)
    assert m["families"] == ["baseball", "football", "soccer"]
    assert m["league_tokens_by_family"] == {"football": ["cfb"],
                                            "soccer": ["brb", "unl"]}
    # NCAAF is no longer OUTSIDE_MANDATE: it is inside and UNSUPPORTED for the
    # one reason that is true (no measured probability source for football)
    assert COV.classify_listing(CFB_ML, mand=m) == (
        COV.S_UNSUPPORTED, "%s:football" % COV.X_NO_PROBABILITY)
    assert COV.classify_listing(UNL_ML, mand=m) == (None, None)
    assert COV.classify_listing(NFL_ML, mand=m) == (
        COV.S_OUTSIDE, "%s:nfl" % COV.X_LEAGUE)
    assert COV.classify_listing(ARG2_ML, mand=m) == (
        COV.S_OUTSIDE, "%s:arg2" % COV.X_LEAGUE)
    assert COV.classify_listing(CFB_1H, mand=m)[0] == COV.S_OUTSIDE
    # with no requested set the mandate is the configured set, as before
    base = COV.mandate()
    assert base["families"] == ["baseball"]
    assert COV.classify_listing(CFB_ML, mand=base) == (
        COV.S_OUTSIDE, "%s:football" % COV.X_SPORT)


async def _census_tables(conn, heartbeat):
    await conn.execute("""
        CREATE TEMP TABLE us_premap (identifier text, event_slug text,
            event_title text, market_slug text, question text,
            sports_type text, game_start timestamptz,
            updated_at timestamptz NOT NULL);
        CREATE TEMP TABLE ingestion_state (key text PRIMARY KEY, value text);
    """)
    for r in (CFB_ML, NFL_ML, UNL_ML, ARG2_ML):
        await conn.execute(
            "INSERT INTO us_premap VALUES ($1,$2,$3,$1,$4,$5,"
            " now() + interval '1 day', now() - interval '60 seconds')",
            r["market_slug"], r["event_slug"], r["event_title"],
            r["question"], r["sports_type"])
    if heartbeat is not None:
        await conn.execute("INSERT INTO ingestion_state VALUES ($1, $2)",
                           COV.REQUESTED_KEY, json.dumps(heartbeat))


@pg
async def test_the_census_files_ncaaf_by_the_collectors_requested_set():
    import asyncpg
    import time
    for heartbeat, cfb_state in (
            ({"at": time.time() - 60,
              "sports_selection": {"requested": REQUESTED}}, "UNSUPPORTED"),
            # STALE: the requested set is not current, so the configured set
            # alone stands and the census says why
            ({"at": time.time() - 3 * 3600,
              "sports_selection": {"requested": REQUESTED}},
             "OUTSIDE_MANDATE"),
            (None, "OUTSIDE_MANDATE")):
        conn = await asyncpg.connect(DSN)
        try:
            await _census_tables(conn, heartbeat)
            got = await COV.census(conn, now=time.time())
            assert got["ok"] is True, got
            cats = got["categories"]
            assert cats["sums_to_listed"] is True
            ncaaf = cats["by_league"]["NCAAF"]
            assert ncaaf["provider_key"] == NCAAF
            assert ncaaf["venue_token"] == "cfb"
            assert ncaaf["states"] == {cfb_state: 1}, ncaaf
            req = got["mandate"]["requested_set"]
            assert req["fresh"] is (cfb_state == "UNSUPPORTED"), req
            if cfb_state == "UNSUPPORTED":
                br = got["blocked_by_reason"]
                assert br["UNSUPPORTED:%s:football" % COV.X_NO_PROBABILITY] == 1
                assert br["OUTSIDE_MANDATE:%s:nfl" % COV.X_LEAGUE] == 1
                assert "OUTSIDE_MANDATE:%s:football" % COV.X_SPORT not in br
        finally:
            await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · NAMES: EXACT OR A NAMED REFUSAL, NEVER A CROSS-MAP
# ═════════════════════════════════════════════════════════════════════

def _event(slug, title, a, b):
    return [{"market_slug": "aec-" + slug, "intent": intent,
             "event_slug": slug, "event_title": title, "question": title,
             "kind": "side", "sports_type": "football_team_full_game_winner",
             "team_abbr": abbr, "team_name": team, "side_norm": nick,
             "line": "00", "game_start": T0}
            for (team, nick, abbr), intent in ((a, LONG), (b, SHORT))]


# production rows of 2026-10-03 (team_name / side_norm / team_abbr), all
# moved into ONE window so every lookalike school is a live candidate
ROWS = (
    _event("cfb-ohio-kentst-2026-10-03", "Ohio vs. Kent State",
           ("ohio", "bobcats", "ohio"),
           ("kent state", "golden flashes", "kentst"))
    + _event("cfb-ohiost-iowa-2026-10-03", "Ohio State vs. Iowa",
             ("ohio state", "buckeyes", "ohiost"),
             ("iowa", "hawkeyes", "iowa"))
    + _event("cfb-mia-clmsn-2026-10-03", "Miami (FL) vs. Clemson",
             ("miami  fl", "hurricanes", "mia"),
             ("clemson", "tigers", "clmsn"))
    + _event("cfb-bowlgr-miaoh-2026-10-03", "Bowling Green vs. Miami (OH)",
             ("bowling green", "falcons", "bowlgr"),
             ("miami  oh", "redhawks", "miaoh"))
    + _event("cfb-lamon-sala-2026-10-03",
             "Louisiana-Monroe vs. South Alabama",
             ("louisiana monroe", "warhawks", "lamon"),
             ("south alabama", "jaguars", "sala"))
    + _event("cfb-ala-mspst-2026-10-03", "Alabama vs. Mississippi State",
             ("alabama", "crimson tide", "ala"),
             ("mississippi state", "bulldogs", "mspst"))
    + _event("cfb-mich-minnst-2026-10-03", "Michigan vs. Minnesota",
             ("michigan", "wolverines", "mich"),
             ("minnesota", "golden gophers", "minnst"))
    + _event("cfb-mst-wisc-2026-10-03", "Michigan State vs. Wisconsin",
             ("michigan state", "spartans", "mst"),
             ("wisconsin", "badgers", "wisc")))


def _m(home, away):
    return V.match_event(home=home, away=away,
                         commence_epoch=T0.timestamp() + 300,
                         family="football", rows=ROWS, competition=NCAAF,
                         league_tokens=("cfb",))


EXACT = {
    ("Ohio State Buckeyes", "Iowa Hawkeyes"): "aec-cfb-ohiost-iowa-2026-10-03",
    ("Ohio Bobcats", "Kent State Golden Flashes"):
        "aec-cfb-ohio-kentst-2026-10-03",
    ("Miami Hurricanes", "Clemson Tigers"): "aec-cfb-mia-clmsn-2026-10-03",
    ("Miami (FL) Hurricanes", "Clemson Tigers"):
        "aec-cfb-mia-clmsn-2026-10-03",
    ("Miami FL Hurricanes", "Clemson Tigers"): "aec-cfb-mia-clmsn-2026-10-03",
    ("Miami (OH) RedHawks", "Bowling Green Falcons"):
        "aec-cfb-bowlgr-miaoh-2026-10-03",
    ("UL Monroe Warhawks", "South Alabama Jaguars"):
        "aec-cfb-lamon-sala-2026-10-03",
    ("Louisiana-Monroe Warhawks", "South Alabama Jaguars"):
        "aec-cfb-lamon-sala-2026-10-03",
    ("#5 Alabama Crimson Tide", "Mississippi St. Bulldogs"):
        "aec-cfb-ala-mspst-2026-10-03",
    ("(5) Alabama Crimson Tide", "Mississippi St Bulldogs"):
        "aec-cfb-ala-mspst-2026-10-03",
    ("No. 5 Alabama Crimson Tide", "Mississippi State Bulldogs"):
        "aec-cfb-ala-mspst-2026-10-03",
    ("Michigan State Spartans", "Wisconsin Badgers"):
        "aec-cfb-mst-wisc-2026-10-03",
    ("Michigan Wolverines", "Minnesota Golden Gophers"):
        "aec-cfb-mich-minnst-2026-10-03",
}


def test_each_rendering_maps_exactly_to_its_own_contract():
    for (home, away), slug in EXACT.items():
        m = _m(home, away)
        assert m["ok"], (home, away, m.get("refusal"), m.get("why"))
        assert m["us_market_slug"] == slug, (home, away, m["us_market_slug"])
        assert m["candidates"] == 1


REFUSED = {
    # a bare school is contained in its State namesake: refused by name
    ("Ohio", "Iowa Hawkeyes"): V.R_NICKNAME,
    ("Miami", "Clemson Tigers"): V.R_NICKNAME,
    # an abbreviation with no nickname to confirm it
    ("UL Monroe", "South Alabama Jaguars"): V.R_NICKNAME,
    # the right school, the wrong opponent: two different fixtures
    ("Miami (OH) RedHawks", "Clemson Tigers"): V.R_ONE_TEAM_ONLY,
    ("Ohio Bobcats", "Iowa Hawkeyes"): V.R_ONE_TEAM_ONLY,
    # a rendering this resolver does not bridge (no alias is invented)
    ("ULM Warhawks", "South Alabama Jaguars"): V.R_ONE_TEAM_ONLY,
}


def test_everything_else_refuses_by_name():
    for (home, away), code in REFUSED.items():
        m = _m(home, away)
        assert not m["ok"] and m["refusal"] == code, (home, away, m)
        assert m.get("us_market_slug") is None


def test_ohio_and_ohio_state_and_the_two_miamis_never_cross_map():
    pairs = (("Ohio Bobcats", "ohio state", "buckeyes"),
             ("Ohio State Buckeyes", "ohio", "bobcats"),
             ("Miami Hurricanes", "miami  oh", "redhawks"),
             ("Miami (FL) Hurricanes", "miami  oh", "redhawks"),
             ("Miami (OH) RedHawks", "miami  fl", "hurricanes"),
             ("Michigan Wolverines", "michigan state", "spartans"),
             ("Michigan State Spartans", "michigan", "wolverines"),
             ("Ohio", "ohio state", "buckeyes"),
             ("Miami", "miami  oh", "redhawks"))
    for prov, team, nick in pairs:
        p = V.team_profile(prov, "football")
        v = V.team_profile("%s %s" % (team, nick), "football", nickname=nick)
        assert V.same_college_team(p, v)["same"] is False, (prov, team)
    # and through the resolver, every orientation of every lookalike pair
    for home, away in (("Ohio Bobcats", "Iowa Hawkeyes"),
                       ("Ohio State Buckeyes", "Kent State Golden Flashes"),
                       ("Miami Hurricanes", "Bowling Green Falcons"),
                       ("Miami (OH) RedHawks", "Clemson Tigers"),
                       ("Michigan Wolverines", "Wisconsin Badgers"),
                       ("Michigan State Spartans", "Minnesota Golden Gophers")):
        for h, a in ((home, away), (away, home)):
            m = _m(h, a)
            assert not m["ok"], (h, a, m.get("us_market_slug"))


def test_the_ranked_venue_title_still_confirms_the_competition():
    conf = L.confirm_mapping_by_fixtures(
        provider_events=[{"home_team": "Alabama Crimson Tide",
                          "away_team": "Mississippi State Bulldogs",
                          "commence_time": "2026-10-03T16:00:00Z"}],
        venue_event_titles=["#5 Alabama vs. Mississippi St."],
        venue_event_days={"#5 Alabama vs. Mississippi St.": "2026-10-03"},
        family="football")
    assert conf["ok"], conf
    # soccer's confirmation is unchanged by the college rewrites
    assert L._team_tokens("St. Pauli")[0] == frozenset({"st", "pauli"})


# ═════════════════════════════════════════════════════════════════════
# 3 · SETTLEMENT FROM THE CAPTURED DOCUMENTS
# ═════════════════════════════════════════════════════════════════════

BOOK_FX = json.loads((FIX / "pinnacle_american_football_rules_2026_10_03"
                      ".json").read_text())
VENUE_FX = json.loads((FIX / "pmus_cfb_listing_2026_10_03.json").read_text())


def test_the_book_terms_are_the_captured_words_with_their_hash():
    cap = T.CAPTURE_RUN_FOOTBALL
    assert cap["sha256"] == BOOK_FX["page_sha256"]
    assert cap["retrieved_at"] == BOOK_FX["retrieved_at"]
    assert cap["url"] == BOOK_FX["source_url"]
    assert "37161033936" in cap["job"]
    section = BOOK_FX["american_football_section"]
    for q in (T._Q_AF_SUSPENDED, T._Q_AF_NOT_STARTED, T._Q_AF_OVERTIME,
              T._Q_AF_LEAGUES):
        assert q in section, q
    pre = T.BOOK_TERMS[("football", "h2h", T.CTX_PRE_GAME)]
    live = T.BOOK_TERMS[("football", "h2h", T.CTX_LIVE)]
    assert pre == live, "the section states one rule set for both contexts"
    adm = T.admit_book_terms(pre)
    assert adm.get("ok") is True and not adm.get("rejected"), adm
    for rec in pre.values():
        assert rec["cite"]["page_sha256"] == BOOK_FX["page_sha256"]
    assert vset.BOOK_SETTLEMENT["football"] == \
        L.PINNACLE_SETTLEMENT["football"] == "FULL_GAME_INCLUDING_OVERTIME"


def test_overtime_is_established_and_abandonment_conflicts_by_name():
    assert VENUE_FX["_evidence"]["response_sha256"].startswith("1df8dc71")
    for mk in VENUE_FX["markets"]:
        assert mk["sportsMarketType"] == "football_team_full_game_winner"
        a = vset.attest(sport_family="football", market="h2h",
                        venue_evidence={"rules_text": mk["description"],
                                        "rules_field": "description",
                                        "rules_source": VENUE_FX["_source"]},
                        book_evidence={"outcome_names": ["H", "A"]},
                        observed_at=1.0)
        ot, void = a["rules"]["overtime"], a["rules"]["void"]
        assert ot["established"] is True, ot
        assert ot["evidence_class"] == vset.EV_VENUE_RULES_TEXT
        assert void["established"] is False
        assert void["refusal"] == vset.R_VOID_CONFLICTS
        assert void["mismatched_conditions"] == [T.C_NOT_PLAYED]
        per = void["terms_comparison"]["per_condition"][T.C_NOT_PLAYED]
        assert (per["book_payout"], per["venue_payout"]) == (
            T.PAY_STAKE_BACK, T.PAY_LAST_FAIR_MARKET_PRICE)
        assert a["unmet"] == [vset.R_VOID_CONFLICTS]
        assert a["overall_established"] is False
        # the quote context selects nothing for football, so it is not a
        # blocker; the conflict is
        blockers = vset.settlement_blockers(a)["blockers"]
        assert not any(b.startswith(vset.B_CONTEXT) for b in blockers)
        assert any(b.startswith(vset.B_INCOMPATIBLE) for b in blockers)


def test_prose_that_excluded_overtime_would_conflict():
    a = vset.attest(sport_family="football", market="h2h",
                    venue_evidence={"rules_text": (
                        "This market settles on regulation time only.")},
                    book_evidence={"outcome_names": ["H", "A"]},
                    observed_at=1.0)
    assert a["rules"]["overtime"]["refusal"] == vset.R_OVERTIME_CONFLICTS


def test_baseball_and_soccer_settlement_are_unchanged():
    assert T.admit_scope(sport_family="baseball", phase=None,
                         game_format=None)["ok"] is False
    assert T.book_terms(sport_family="baseball", context=None,
                        phase=T.PHASE_REGULAR, game_format=T.FMT_NINE) == {}
    assert T.admit_scope(sport_family="soccer")["ok"] is False


def test_football_stays_out_of_the_measured_de_vig_set_but_is_recorded():
    assert ("football", "h2h") not in devig.SUPPORTED
    val = devig.valuation(
        contract={"sport_family": "football", "market": "h2h",
                  "selection": "Ohio State Buckeyes"},
        quote={"book": "pinnacle",
               "outcomes": {"Ohio State Buckeyes": 1.25,
                            "Iowa Hawkeyes": 4.2}},
        now=0.0)
    assert val["probability"] is None
    assert devig.R_UNSUPPORTED_MARKET in val["refusals"]
    assert val["raw_odds"] == {"Ohio State Buckeyes": 1.25,
                               "Iowa Hawkeyes": 4.2}
    assert val["outcomes_priced"] == 2
    assert val["overround"] == pytest.approx(1 / 1.25 + 1 / 4.2 - 1)
    # another book's prices are never recorded under Pinnacle's name
    other = devig.valuation(
        contract={"sport_family": "football", "market": "h2h"},
        quote={"book": "draftkings", "outcomes": {"A": 1.5, "B": 2.5}},
        now=0.0)
    assert "raw_odds" not in other
