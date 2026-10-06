"""P0 COVERAGE (2026-10-06): THE REMAINING MAPPING FIRST LOSSES, CLOSED BY
THEIR OWN EVIDENCE OR REFUSED BY AN EXACT NAME.

The production first-loss census (24 h, read 2026-10-06) named, at MAPPED /
NORMALIZED, these SOFTWARE codes; research-sql runs 37411602665,
37411612077 and 37411963692 (research/p0_*.sql, branch
claude/p0-research-mapping) read the venue's own rows behind each:

1. VENUE_NATIVE_FAMILY_NOT_SUPPORTED (31 in the census, 44 events in 48 h):
   PinnAPI-native basketball / hockey seeds of NBA, NHL and 17 other venue
   leagues (aba, bbl, bcl, bsl, eurocup, kbl, lnbp, khl, cehl ... and wnba).
   Every league's winner wording is captured
   (tests/fixtures/pmus_basketball_hockey_winner_listings_2026_10_06.json)
   and grades the full game, overtime (hockey: and the shootout) included --
   the book's grading for every competition. Each is admitted; the WNBA is
   NOT: its venue team record is the city alone, which the NBA shares.
2. VENUE_NATIVE_NICKNAME_DOES_NOT_CONFIRM_THE_TEAM (Arkansas State / South
   Alabama, UTSA / South Florida): written by builds 7cb1979b and fd2bff3a,
   which lack a73a7d15 (the exact-school confirmation on the discovered
   event). The current tree maps both; pinned here on the venue's own rows.
3. VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM (Goias / Athletic Club): the
   venue's Serie B "athletic club" is all generic tokens, so containment
   could never confirm it. Identical names are now an identity; "Athletic
   Club (MG)" still refuses.
4. PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS (Deportivo Riestra / Central Cordoba,
   a native seed): two feed fixtures of the same names; the seed's OWN
   fixture id decides, a metered event stays ambiguous.
5. PINNAPI_PRIMARY_NO_EXACT_FIXTURE (Bosnia & Herzegovina / Poland): the
   conjunction "&" / "and" / nothing is one spelling.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_settlement_terms as T
from sportsassets import bettor_venue_native_identity as V
from sportsassets import bettor_venue_settlement as vset
from sportsassets import pinnapi_census as CEN
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_primary as P
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB

FIX = Path(__file__).parent / "fixtures"
ALL = json.loads((FIX / "pmus_basketball_hockey_winner_listings_2026_10_06"
                  ".json").read_text())
LONG, SHORT = V.LONG, V.SHORT
TYPE = {"basketball": "basketball_team_full_game_winner",
        "hockey": "hockey_team_full_game_winner"}
T0 = _dt.datetime(2026, 10, 6, 16, 0, tzinfo=_dt.timezone.utc).timestamp()


def _fam(slug):
    return "hockey" if slug.startswith("hockey") else "basketball"


# ═════════════════════════════════════════════════════════════════════
# 1 · EVERY LEAGUE THE VENUE LISTS: ADMITTED BY ITS OWN WORDING, OR NAMED
# ═════════════════════════════════════════════════════════════════════

def _leagues_in_capture():
    out = {"basketball": set(), "hockey": set()}
    for k in ALL["wording_census"]:
        lg, st = k.split("/")
        out["hockey" if st.startswith("hockey") else "basketball"].add(lg)
    return out


def test_the_admitted_leagues_are_every_captured_league_but_the_wnba():
    cap = _leagues_in_capture()
    assert V.ADMITTED_WINNER_LEAGUES["basketball"] == cap["basketball"] - {
        "wnba"}
    assert V.ADMITTED_WINNER_LEAGUES["hockey"] == cap["hockey"]
    assert set(V.LEAGUES_NOT_READ) == {"wnba"}
    # one list, read three ways: identity, de-vig, census admission
    for fam, leagues in V.ADMITTED_WINNER_LEAGUES.items():
        dv = {k[2] for k in devig.SUPPORTED_BY_LEAGUE if k[:2] == (fam, "h2h")}
        assert dv == set(leagues), fam
    assert devig.expected_outcomes("basketball", "h2h", league="wnba") is None


def test_every_captured_league_states_one_full_game_overtime_wording():
    for key, wordings in ALL["wording_census"].items():
        lg, st = key.split("/")
        assert len(wordings) == 1, key                  # ONE wording
        text = wordings[0]["after_the_first_sentence"]
        if st.startswith("hockey"):
            assert text.startswith(
                "Overtime and any shootout are included if played."), key
        else:
            assert text.startswith("Overtime is included if played."), key
        assert "last fair market price" in text, key


def test_every_listing_grades_like_the_book_and_strict_terms_conflict():
    seen = set()
    for m in ALL["markets"]:
        lg = m["slug"].split("-")[1]
        fam = "hockey" if m["sportsMarketType"].startswith("hockey") \
            else "basketball"
        seen.add(lg)
        book = PB.book_grading_period(fam, lg)
        venue = PB.venue_grading_period(fam, m["description"], lg)
        if lg in V.LEAGUES_NOT_READ:
            assert book is None                        # no grading claimed
            continue
        assert venue["refusal"] is None, (m["slug"], venue)
        assert venue["period"] == book["period"], m["slug"]
        a = vset.attest(sport_family=fam, market="h2h",
                        venue_evidence={"rules_text": m["description"]},
                        book_evidence={"outcome_names": ["H", "A"]},
                        observed_at=1.0)
        assert a["rules"]["overtime"]["established"] is True, m["slug"]
        assert a["unmet"] == [vset.R_VOID_CONFLICTS], (m["slug"], a["unmet"])
        assert a["rules"]["void"]["mismatched_conditions"] == [
            T.C_NOT_PLAYED]
    assert seen == set().union(*_leagues_in_capture().values())


def test_the_book_minimum_is_cited_for_every_competition():
    q = T.BOOK_TERMS[("basketball", "h2h", T.CTX_PRE_GAME)][
        T.C_STOPPED_EARLY]["cite"]["quote"]
    assert "43 minutes" in q and "35 minutes" in q
    sec = json.loads((FIX / "pinnacle_line_rules_2026_10_04.json")
                     .read_text())["sections"]["basketball"]
    assert any(q in line for line in sec)


def _event(slug, a, b, start):
    """The venue's two-way rows of one event, exactly as us_premap carries
    them (team_name / side_norm), from research-sql run 37411602665 (F2)."""
    fam = _fam_of_slug(slug)
    out = []
    for (team, it) in ((a, LONG), (b, SHORT)):
        out.append({"market_slug": "aec-" + slug, "event_slug": slug,
                    "event_title": "%s vs. %s" % (a.title(), b.title()),
                    "question": "Who will win?", "kind": "side",
                    "sports_type": TYPE[fam], "team_abbr": team[:3],
                    "team_name": team, "side_norm": team.split()[-1],
                    "intent": it,
                    "game_start": _dt.datetime.fromtimestamp(
                        start, _dt.timezone.utc).isoformat()})
    return out


HOCKEY_LEAGUES = {"nhl", "ahl", "cehl", "khl", "liiga", "snhl"}


def _fam_of_slug(slug):
    return "hockey" if slug.split("-")[0] in HOCKEY_LEAGUES else "basketball"


#: (venue event, venue team names, provider home, provider away) -- the
#: production refusals of 2026-10-05/06 and the venue's own rows behind them
PROD = (
    ("aba-par-meg-2026-10-05", ("kk partizan belgrade",
                                "kk mega basket belgrade"),
     "KK Partizan Belgrade", "KK Mega Basket Belgrade"),
    ("aba-zad-scd-2026-10-05", ("kk zadar", "kk studentski centar podgorica"),
     "KK Zadar", "KK Studentski centar Podgorica"),
    ("bbl-ras-blb-2026-10-05", ("rasta vechta",
                                "basketball lowen braunschweig"),
     "Rasta Vechta", "Braunschweig"),
    ("bcl-cib-gsn-2026-10-06", ("kk cibona", "galatasaray sk"),
     "KK Cibona", "Galatasaray"),
    ("bcl-iga-bil-2026-10-06", ("igokea aleksandrovac", "bilbao basket"),
     "Igokea", "Bilbao Basket"),
    ("bcl-sig-war-2026-10-06", ("sig strasbourg", "legia warszawa"),
     "SIG Strasbourg", "Legia Warszawa"),
    ("bcl-bad-bhe-2026-10-06", ("club joventut badalona",
                                "bnei herzliya basket"),
     "Club Joventut Badalona", "Bnei Herzliya"),
    ("bsl-bjk-tra-2026-10-05", ("besiktas", "trabzonspor"),
     "Besiktas JK", "Trabzonspor"),
    ("eurocup-ceo-hap-2026-10-06", ("kk cedevita olimpija ljubljana",
                                    "hapoel jerusalem"),
     "KK Cedevita Olimpija Ljubljana", "Hapoel Jerusalem"),
    ("eurocup-tts-max-2026-10-06", ("turk telekom", "maxima roma"),
     "Turk Telekom", "Maxima Roma"),
    ("eurocup-bos-pao-2026-10-06", ("kk bosna royal sarajevo", "paok bc"),
     "KK Bosna Royal Sarajevo", "PAOK BC"),
    ("eurocup-sky-sia-2026-10-06", ("skyliners frankfurt", "bc siauliai"),
     "Skyliners Frankfurt", "BC Siauliai"),
    ("eurocup-atr-wro-2026-10-06", ("aquila basket trento", "slask wroclaw"),
     "Aquila Trento", "Slask Wroclaw"),
    ("eurocup-ros-rig-2026-10-06", ("rostock seawolves", "rigas zelli"),
     "Rostock Seawolves", "Rigas Zelli"),
    ("kbl-dae-any-2026-10-05", ("daegu pegasus",
                                "anyang jungkwanjang red boosters"),
     "Daegu Kogas Pegasus", "Anyang Jungkwanjang Red Boosters"),
    ("lnbp-cor-abe-2026-10-06", ("correcaminos uat victoria",
                                 "abejas de leon"),
     "Correcaminos UAT Victoria", "Abejas de Leon"),
    ("lnbp-min-ssl-2026-10-06", ("mineros de zacatecas", "santos del potosi"),
     "Mineros Zacatecas", "Santos del Potosi"),
    ("lnbp-pan-dia-2026-10-06", ("panteras de aguascalientes",
                                 "diablos rojos del mexico"),
     "Panteras de Aguascalientes", "Diablos Rojos"),
    ("lnbp-sol-fue-2026-10-06", ("soles de mexicali", "fuerza regia"),
     "Soles de Mexicali", "Fuerza Regia de Monterrey"),
    ("cehl-ceb-kom-2026-10-05", ("motor ceske budejovice", "kometa"),
     "Ceske Budejovice", "HC Kometa Brno"),
    ("khl-lad-shd-2026-10-05", ("lada togliatti", "shanghai dragons"),
     "HC Lada Togliatti", "Shanghai Dragons"),
    ("khl-akb-lok-2026-10-05", ("ak bars kazan", "lokomotiv yaroslavl"),
     "AK Bars Kazan", "Lokomotiv Yaroslavl"),
    ("khl-dmn-tor-2026-10-05", ("dinamo minsk", "torpedo"),
     "HC Dinamo Minsk", "Torpedo N. Novgorod"),
    ("khl-cska-sal-2026-10-05", ("cska moscow", "salavat yulaev ufa"),
     "CSKA Moscow", "Salavat Yulaev Ufa"),
    ("khl-trk-amr-2026-10-06", ("traktor", "amur khabarovsk"),
     "HC Traktor Chelyabinsk", "Amur Khabarovsk"),
    ("khl-dmo-shd-2026-10-07", ("hc dynamo moscow", "shanghai dragons"),
     "HC Dynamo Moscow", "Shanghai Dragons"),
)


def _match(slug, rows, home, away, *, priced="home", event_slug=None):
    return V.match_event(home=home, away=away, commence_epoch=T0 + 120,
                         family=_fam_of_slug(slug), rows=rows,
                         competition="pinnapi_" + _fam_of_slug(slug),
                         league_tokens=(slug.split("-")[0],),
                         priced=priced,
                         event_slug=event_slug or slug)


def test_every_production_refusal_now_maps_to_its_own_contract():
    for slug, (a, b), home, away in PROD:
        rows = _event(slug, a, b, T0)
        for priced in ("home", "away"):
            m = _match(slug, rows, home, away, priced=priced)
            assert m["ok"], (slug, priced, m.get("refusal"), m.get("why"))
            assert m["us_market_slug"] == "aec-" + slug
            assert m["venue_league"] == slug.split("-")[0]
        h, w = (_match(slug, rows, home, away, priced=p) for p in
                ("home", "away"))
        assert {h["intent"], w["intent"]} == {LONG, SHORT}
        assert h["priced_participant"] != w["priced_participant"]


def test_the_wnba_city_only_record_refuses_by_name():
    """Golden State Valkyries / Las Vegas Aces against the venue's WNBA
    'golden state' / 'las vegas' -- and the NBA's Golden State Warriors
    would contain the same city: no identity, by name."""
    rows = _event("wnba-lv-gsv-2026-10-07", "golden state", "las vegas", T0)
    for home, away in (("Golden State Valkyries", "Las Vegas Aces"),
                       ("Golden State Warriors", "Las Vegas Aces")):
        m = _match("wnba-lv-gsv-2026-10-07", rows, home, away)
        assert m["ok"] is False and m["refusal"] == V.R_LEAGUE_NOT_ADMITTED
        assert "city" in m["why"]
    # an uncaptured league of the family refuses by the same name
    rows = _event("xbl-aaa-bbb-2026-10-07", "alpha club", "beta club", T0)
    m = _match("xbl-aaa-bbb-2026-10-07", rows, "Alpha Club", "Beta Club")
    assert m["refusal"] == V.R_LEAGUE_NOT_ADMITTED
    assert RT.classify(V.R_LEAGUE_NOT_ADMITTED)["classified"]
    assert V.R_LEAGUE_NOT_ADMITTED in V.EVENT_LEVEL_REFUSALS


def test_lookalikes_in_one_admitted_league_still_refuse():
    """Two Belgrade clubs, two Shanghai Dragons games, a Moscow derby
    rendering: one-team matches and wrong pairs never map."""
    par = _event("aba-par-meg-2026-10-05", "kk partizan belgrade",
                 "kk mega basket belgrade", T0)
    m = _match("aba-par-meg-2026-10-05", par, "KK Partizan Belgrade",
               "KK Crvena Zvezda Belgrade")
    assert m["ok"] is False and m["refusal"] == V.R_ONE_TEAM_ONLY
    dmo = _event("khl-dmo-shd-2026-10-07", "hc dynamo moscow",
                 "shanghai dragons", T0)
    m = _match("khl-dmo-shd-2026-10-07", dmo, "CSKA Moscow",
               "Shanghai Dragons")
    assert m["ok"] is False and m["refusal"] == V.R_ONE_TEAM_ONLY


def test_the_census_and_de_vig_admit_each_captured_league():
    for fam, leagues in V.ADMITTED_WINNER_LEAGUES.items():
        for lg in leagues:
            row = {"identifier": "aec-%s-aaa-bbb-2026-10-06" % lg,
                   "team_league": lg, "side_norm": "x",
                   "question": "Who will win?"}
            assert CEN.family_of("side", None, TYPE[fam], row) == \
                ("MONEYLINE", True, None), (fam, lg)
    row = {"identifier": "aec-wnba-ny-atl-2026-10-06", "team_league": "wnba"}
    assert CEN.family_of("side", None, TYPE["basketball"], row) == \
        ("MONEYLINE", False, CEN.R_WINNER_LEAGUE_NOT_ADMITTED)


# ═════════════════════════════════════════════════════════════════════
# 2 · NCAAF: THE TWO PRODUCTION NICKNAME REFUSALS MAP ON THE CURRENT TREE
# ═════════════════════════════════════════════════════════════════════

def _cfb(slug, a, b, start):
    out = []
    for (team, nick, abbr), it in ((a, LONG), (b, SHORT)):
        out.append({"market_slug": "aec-" + slug, "event_slug": slug,
                    "event_title": "%s vs. %s" % (a[0].title(), b[0].title()),
                    "question": "Who will win?", "kind": "side",
                    "sports_type": "football_team_full_game_winner",
                    "team_abbr": abbr, "team_name": team, "side_norm": nick,
                    "intent": it,
                    "game_start": _dt.datetime.fromtimestamp(
                        start, _dt.timezone.utc).isoformat()})
    return out


def test_the_ncaaf_school_only_seeds_map_on_the_discovered_event():
    """research-sql run 37411612077 (M1/M2) and 37411963692 (T1): the
    venue's own rows of both fixtures; the refusals were written by builds
    7cb1979b / fd2bff3a, which predate a73a7d15."""
    t = _dt.datetime(2026, 10, 8, 23, 30, tzinfo=_dt.timezone.utc).timestamp()
    rows = (_cfb("cfb-sala-arkst-2026-10-08",
                 ("south alabama", "jaguars", "sala"),
                 ("arkansas state", "red wolves", "arkst"), t)
            + _cfb("cfb-sfl-utsa-2026-10-08",
                   ("south florida", "bulls", "sfl"),
                   ("utsa", "roadrunners", "utsa"), t))
    for home, away, ev in (("Arkansas State", "South Alabama",
                            "cfb-sala-arkst-2026-10-08"),
                           ("UTSA", "South Florida",
                            "cfb-sfl-utsa-2026-10-08")):
        m = V.match_event(home=home, away=away, commence_epoch=t,
                          family="football", rows=rows,
                          competition="americanfootball_ncaaf",
                          league_tokens=("cfb",), event_slug=ev)
        assert m["ok"] and m["us_market_slug"] == "aec-" + ev, m
        # without the discovered event a school alone still refuses
        m = V.match_event(home=home, away=away, commence_epoch=t,
                          family="football", rows=rows,
                          competition="americanfootball_ncaaf",
                          league_tokens=("cfb",))
        assert m["refusal"] == V.R_NICKNAME


# ═════════════════════════════════════════════════════════════════════
# 3 · GOIAS / ATHLETIC CLUB: IDENTICAL NAMES ARE AN IDENTITY
# ═════════════════════════════════════════════════════════════════════

def _soccer(slug, sides, start):
    """The venue's per-side rows (research-sql run 37411612077, M3)."""
    out = []
    for code, team in sides + (("draw", ""),):
        for it, sn in ((LONG, "yes"), (SHORT, "no")):
            out.append({"market_slug": "atc-%s-%s" % (slug, code),
                        "event_slug": slug, "event_title": "Goias EC vs. "
                        "Athletic Club", "question": "Will %s win?" % (
                            team or "it be a draw"),
                        "kind": "side",
                        "sports_type": "soccer_team_full_time_winner",
                        "team_abbr": code if team else None,
                        "team_name": team, "side_norm": sn, "intent": it,
                        "game_start": _dt.datetime.fromtimestamp(
                            start, _dt.timezone.utc).isoformat()})
    return out


def test_goias_and_athletic_club_map_and_a_qualified_name_does_not():
    t = _dt.datetime(2026, 10, 6, 23, 30, tzinfo=_dt.timezone.utc).timestamp()
    rows = _soccer("brb-goi-ath-2026-10-06",
                   (("goi", "goias ec"), ("ath", "athletic club")), t)
    for priced, slug in (("home", "atc-brb-goi-ath-2026-10-06-goi"),
                         ("away", "atc-brb-goi-ath-2026-10-06-ath")):
        m = V.match_event(home="Goias", away="Athletic Club",
                          commence_epoch=t, family="soccer", rows=rows,
                          competition="soccer_brazil_serie_b",
                          league_tokens=("brb",), priced=priced)
        assert m["ok"], (priced, m.get("refusal"), m.get("why"))
        assert m["us_market_slug"] == slug and m["intent"] == LONG
    ev = V.same_team(V.team_profile("Athletic Club"),
                     V.team_profile("athletic club"))
    assert ev["same"] is True and ev["identical"] is True
    # never by generic tokens alone: a qualified or different name refuses
    for prov in ("Athletic Club (MG)", "Athletic FC", "Athletic"):
        assert V.same_team(V.team_profile(prov),
                           V.team_profile("athletic club"))["same"] is False
    m = V.match_event(home="Goias", away="Athletic Club (MG)",
                      commence_epoch=t, family="soccer", rows=rows,
                      competition="soccer_brazil_serie_b",
                      league_tokens=("brb",))
    assert m["ok"] is False and m["refusal"] == V.R_ONE_TEAM_ONLY


# ═════════════════════════════════════════════════════════════════════
# 4 / 5 · THE PRIMARY FIXTURE: THE SEED'S OWN ID; ONE CONJUNCTION SPELLING
# ═════════════════════════════════════════════════════════════════════

def _feed(fixtures, at):
    c = F.FeedCache()
    e = c.new_connection([("prematch", 1)])
    evs = [{"id": fid, "startTime": _dt.datetime.fromtimestamp(
                start, _dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "isLive": False,
            "participants": [{"name": h, "alignment": "home"},
                             {"name": w, "alignment": "away"}],
            "markets": []} for fid, start, h, w in fixtures]
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
             "ts": (at - 60) * 1000, "events": evs}, epoch=e,
            received_ms=(at - 60) * 1000 + 5)
    return c


def _ev(home, away, start, fixture_id=None):
    e = {"id": "x", "home_team": home, "away_team": away,
         "commence_time": _dt.datetime.fromtimestamp(
             start, _dt.timezone.utc).isoformat().replace("+00:00", "Z")}
    if fixture_id is not None:
        e["id"] = "pinnapi:%s" % fixture_id
        e["pinnapi_native"] = {"fixture_id": fixture_id}
    return e


def test_a_native_seed_takes_its_own_fixture_and_a_metered_event_does_not():
    t = _dt.datetime(2026, 10, 5, 19, 45, tzinfo=_dt.timezone.utc).timestamp()
    cache = _feed([(1637743785, t, "Deportivo Riestra", "Central Cordoba"),
                   (1637743999, t + 1800, "Deportivo Riestra",
                    "Central Cordoba")], t - 3600)
    hit, why = P.match_event(cache, _ev("Deportivo Riestra",
                                        "Central Cordoba", t, 1637743785),
                             "soccer")
    assert why is None and hit[0] == 1637743785
    # the same answer through the pass-wide index
    hit, why = P.match_event(cache, _ev("Deportivo Riestra",
                                        "Central Cordoba", t, 1637743785),
                             "soccer", index=P.fixture_index(cache))
    assert why is None and hit[0] == 1637743785
    # a metered event, or a seed whose own id is not among them: ambiguous
    for ev in (_ev("Deportivo Riestra", "Central Cordoba", t),
               _ev("Deportivo Riestra", "Central Cordoba", t, 42)):
        hit, why = P.match_event(cache, ev, "soccer")
        assert hit is None and why == "PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS"


def test_the_conjunction_is_one_spelling_and_nothing_else_moves():
    a = P.name("Bosnia & Herzegovina")
    assert a == P.name("Bosnia and Herzegovina") == \
        P.name("Bosnia-Herzegovina") == "bosnia herzegovina"
    assert P.name("Bosnia") != a
    assert P.name("Trinidad and Tobago") == P.name("Trinidad & Tobago")
    assert P.name("Brighton and Hove Albion") != P.name("Brighton")
    t = _dt.datetime(2026, 10, 5, 18, 45, tzinfo=_dt.timezone.utc).timestamp()
    cache = _feed([(77, t, "Bosnia and Herzegovina", "Poland")], t - 3600)
    hit, why = P.match_event(cache, _ev("Bosnia & Herzegovina", "Poland", t),
                             "soccer")
    assert why is None and hit[0] == 77
    assert hit[1] == {"home": "Bosnia & Herzegovina", "away": "Poland"}
    # a different second team is still no fixture
    hit, why = P.match_event(cache, _ev("Bosnia & Herzegovina", "Sweden", t),
                             "soccer")
    assert why == "PINNAPI_PRIMARY_NO_EXACT_FIXTURE"
