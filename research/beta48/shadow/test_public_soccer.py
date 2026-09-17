"""Tests for public soccer ingestion and fail-closed event identity.

No network. Every fetch is injected.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import public_soccer_identity as ident  # noqa: E402
import public_soccer_ingest as ing  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def spread_row(slug, labels):
    return {"market_slug": slug, "resolved": True,
            "tokens": [{"outcome_index": i, "outcome": n}
                       for i, n in enumerate(labels)]}


def fixture(league, season, home, away, date, ft=None):
    return {
        "SEASON": season, "VENUE_LEAGUE": league, "DATE": date,
        "HOME": home, "AWAY": away,
        "HOME_KEY": ing.normalise_club(home),
        "AWAY_KEY": ing.normalise_club(away),
        "PLAYED": ft is not None,
        "FT_HOME": ft[0] if ft else None,
        "FT_AWAY": ft[1] if ft else None,
    }


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def test_the_score_is_read_from_every_shape_openfootball_uses():
    assert ing._score_pair({"score": {"ft": [3, 0], "ht": [2, 0]}}) == \
        ([3, 0], [2, 0])
    assert ing._score_pair({"score": [1, 1]}) == ([1, 1], None)
    assert ing._score_pair({"score1": 2, "score2": 4}) == ([2, 4], None)


def test_an_unreadable_score_is_an_unplayed_fixture_not_a_nil_nil():
    assert ing._score_pair({"score": "abandoned"}) == (None, None)
    assert ing._score_pair({}) == (None, None)
    rows = ing.parse_season_file(
        {"name": "X", "matches": [{"date": "2026-08-01", "team1": "A FC",
                                   "team2": "B FC", "score": "TBD"}]},
        "2026-27", "en.1", "epl")
    assert rows[0]["PLAYED"] is False
    assert rows[0]["FT_HOME"] is None and rows[0]["FT_AWAY"] is None


def test_diacritics_survive_normalisation():
    # Stripping accents is the first step of fuzzy matching. We fold case and
    # whitespace only, so a club with an accent stays distinct from one without.
    assert ing.normalise_club("1. FC Köln") != ing.normalise_club("1. FC Koln")
    assert ing.normalise_club("  Chelsea   FC ") == ing.normalise_club("chelsea fc")


def test_ingest_records_provenance_and_survives_a_refusal():
    calls = []

    def fake(url, timeout_s=0):
        calls.append(url)
        if "es.1" in url:
            return {"URL": url, "HTTP_STATUS": "404", "OK": False, "BYTES": 0,
                    "SHA256": None, "FETCHED_AT": "t", "STDERR": "", "_body": b""}
        body = json.dumps({"name": "L", "matches": [
            {"date": "2026-08-01", "team1": "A FC", "team2": "B FC",
             "score": {"ft": [1, 0]}}]}).encode()
        return {"URL": url, "HTTP_STATUS": "200", "OK": True, "BYTES": len(body),
                "SHA256": "deadbeef", "FETCHED_AT": "t", "STDERR": "",
                "_body": body}

    with tempfile.TemporaryDirectory() as d:
        s = ing.ingest(out_dir=d, seasons=("2026-27",),
                       league_files={"en.1": "epl", "es.1": "lal"}, fetcher=fake)
        assert s["FILES_OK"] == 1 and s["FILES_MISSING"] == 1
        assert s["FIXTURES"] == 1 and s["FIXTURES_PLAYED"] == 1
        assert all(m.get("SHA256") or m["STATUS"] != "OK" for m in s["MANIFEST"])
        assert os.path.exists(os.path.join(d, "manifest_v1.json"))
        assert len(ing.load_fixtures(d)) == 1
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# club names from the venue's own labels
# ---------------------------------------------------------------------------


def test_a_spread_row_names_both_clubs_with_the_slug_side_first():
    rows = [spread_row("epl-che-bri-2026-08-30-spread-home-2pt5",
                       ["Chelsea FC", "Brighton & Hove Albion FC"]),
            spread_row("epl-ips-liv-2026-09-04-spread-away-1pt5",
                       ["Liverpool FC", "Ipswich Town FC"])]
    names, rep = ident.derive_club_names(rows)
    assert names[("epl", "che")] == "Chelsea FC"
    assert names[("epl", "bri")] == "Brighton & Hove Albion FC"
    assert names[("epl", "liv")] == "Liverpool FC"   # away side, named first
    assert names[("epl", "ips")] == "Ipswich Town FC"
    assert rep["CODES_IN_CONFLICT"] == 0


def test_a_code_that_resolves_two_ways_is_dropped_not_voted_on():
    rows = [spread_row("nfl-sf-lv-2026-09-01-spread-home-2pt5", ["Rams", "LV"]),
            spread_row("nfl-sf-lv-2026-09-08-spread-home-2pt5",
                       ["49ers", "Raiders"]),
            spread_row("nfl-sf-lv-2026-09-15-spread-home-2pt5",
                       ["Rams", "Raiders"])]
    names, rep = ident.derive_club_names(rows)
    assert ("nfl", "sf") not in names       # 2 votes for Rams, 1 for 49ers
    assert rep["CODES_IN_CONFLICT"] >= 1


def test_the_same_code_in_two_leagues_is_two_different_clubs():
    rows = [spread_row("sea-mil-tor-2026-08-30-spread-home-1pt5",
                       ["AC Milan", "Torino FC"]),
            spread_row("elc-mil-wre-2026-09-02-spread-home-1pt5",
                       ["Millwall FC", "Wrexham AFC"])]
    names, _ = ident.derive_club_names(rows)
    assert names[("sea", "mil")] == "AC Milan"
    assert names[("elc", "mil")] == "Millwall FC"


def test_a_row_with_a_missing_label_is_not_used():
    rows = [spread_row("epl-che-bri-2026-08-30-spread-home-2pt5",
                       ["Chelsea FC", None])]
    names, rep = ident.derive_club_names(rows)
    assert names == {} and rep["SPREAD_ROWS_USED"] == 0


# ---------------------------------------------------------------------------
# the exact join
# ---------------------------------------------------------------------------


def test_the_join_is_exact_equality_and_a_near_miss_is_refused():
    fx = [fixture("bun", "2026-27", "Borussia Dortmund", "FC Bayern München",
                  "2026-08-22", (1, 2))]
    names = {("bun", "dor"): "BV Borussia 09 Dortmund",
             ("bun", "bay"): "FC Bayern München"}
    bound, rep = ident.join_clubs(names, fx)
    assert ("bun", "bay") in bound
    assert ("bun", "dor") not in bound        # near miss, not a match
    assert rep["CODES_UNMATCHED"] == 1


def test_a_league_with_no_public_data_is_named_not_silently_dropped():
    fx = [fixture("epl", "2026-27", "A FC", "B FC", "2026-08-01", (0, 0))]
    names = {("wnba", "lv"): "Las Vegas Aces"}
    bound, rep = ident.join_clubs(names, fx)
    assert bound == {}
    assert "wnba" in rep["LEAGUES_WITHOUT_PUBLIC_DATA"]


def test_a_club_is_not_borrowed_from_another_league():
    # 'X FC' exists publicly, but in epl, while the venue code is elc.
    fx = [fixture("epl", "2026-27", "X FC", "Y FC", "2026-08-01", (0, 0))]
    names = {("elc", "x"): "X FC"}
    bound, _ = ident.join_clubs(names, fx)
    assert bound == {}


# ---------------------------------------------------------------------------
# residual closure by schedule
# ---------------------------------------------------------------------------


def _residual_world():
    """Three bound clubs and one unbound one, playing a real little schedule."""
    fx = [
        fixture("bun", "2026-27", "Borussia Dortmund", "A FC", "2026-08-22", (2, 0)),
        fixture("bun", "2026-27", "Borussia Dortmund", "B FC", "2026-08-29", (1, 1)),
        fixture("bun", "2026-27", "C FC", "Borussia Dortmund", "2026-09-05", (0, 3)),
        fixture("bun", "2026-27", "A FC", "B FC", "2026-09-12", (1, 0)),
    ]
    bound = {("bun", "a"): ing.normalise_club("A FC"),
             ("bun", "b"): ing.normalise_club("B FC"),
             ("bun", "c"): ing.normalise_club("C FC")}
    slugs = ["bun-dor-a-2026-08-22", "bun-dor-b-2026-08-29",
             "bun-c-dor-2026-09-05", "bun-a-b-2026-09-12"]
    return fx, bound, slugs


def test_a_club_is_identified_by_who_it_played_not_by_its_name():
    fx, bound, slugs = _residual_world()
    extra, rep = ident.close_residual_by_schedule(slugs, bound, fx)
    assert extra == {("bun", "dor"): ing.normalise_club("Borussia Dortmund")}
    assert rep["NAMES_COMPARED"] is False
    assert rep["REFUSED_AMBIGUOUS"] == 0 and rep["REFUSED_CONTESTED"] == 0


def test_one_shared_fixture_is_not_enough_to_identify_a_club():
    fx, bound, slugs = _residual_world()
    extra, rep = ident.close_residual_by_schedule(
        slugs[:1] + slugs[3:], bound, fx, min_witnesses=2)
    assert extra == {}
    assert rep["REFUSED_TOO_FEW_WITNESSES"] >= 1


def test_two_unbound_clubs_with_the_same_schedule_are_both_refused():
    # A pathological world: two public clubs whose fixtures against the bound
    # clubs coincide exactly. Neither can be identified, so neither is.
    fx = [
        fixture("bun", "2026-27", "Twin One", "A FC", "2026-08-22", (1, 0)),
        fixture("bun", "2026-27", "Twin One", "B FC", "2026-08-29", (1, 0)),
        fixture("bun", "2026-27", "Twin Two", "A FC", "2026-08-22", (2, 0)),
        fixture("bun", "2026-27", "Twin Two", "B FC", "2026-08-29", (2, 0)),
    ]
    bound = {("bun", "a"): ing.normalise_club("A FC"),
             ("bun", "b"): ing.normalise_club("B FC")}
    slugs = ["bun-t1-a-2026-08-22", "bun-t1-b-2026-08-29"]
    extra, rep = ident.close_residual_by_schedule(slugs, bound, fx)
    assert extra == {}
    assert rep["REFUSED_AMBIGUOUS"] == 1


def test_two_venue_codes_claiming_one_public_club_are_both_refused():
    fx = [
        fixture("bun", "2026-27", "Solo FC", "A FC", "2026-08-22", (1, 0)),
        fixture("bun", "2026-27", "Solo FC", "B FC", "2026-08-29", (1, 0)),
    ]
    bound = {("bun", "a"): ing.normalise_club("A FC"),
             ("bun", "b"): ing.normalise_club("B FC")}
    slugs = ["bun-x-a-2026-08-22", "bun-x-b-2026-08-29",
             "bun-y-a-2026-08-22", "bun-y-b-2026-08-29"]
    extra, rep = ident.close_residual_by_schedule(slugs, bound, fx)
    assert extra == {}
    assert rep["REFUSED_CONTESTED"] == 1


def test_two_unbound_codes_cannot_identify_each_other():
    # Every witness must involve an already-bound opponent.
    fx = [fixture("bun", "2026-27", "P FC", "Q FC", "2026-08-22", (1, 0)),
          fixture("bun", "2026-27", "Q FC", "P FC", "2026-08-29", (0, 1))]
    extra, rep = ident.close_residual_by_schedule(
        ["bun-p-q-2026-08-22", "bun-q-p-2026-08-29"], {}, fx)
    assert extra == {}
    assert rep["UNBOUND_CODES_WITH_SCHEDULES"] == 0


# ---------------------------------------------------------------------------
# the event bind
# ---------------------------------------------------------------------------


def test_the_season_rule_follows_the_european_calendar():
    assert ident.season_of("2026-08-21") == "2026-27"
    assert ident.season_of("2026-12-31") == "2026-27"
    assert ident.season_of("2027-01-01") == "2026-27"
    assert ident.season_of("2027-07-31") == "2026-27"
    assert ident.season_of("2027-08-01") == "2027-28"


def test_a_bound_event_carries_the_public_result():
    fx = [fixture("epl", "2026-27", "Chelsea FC", "Brighton & Hove Albion FC",
                  "2026-08-30", (2, 1))]
    bound = {("epl", "che"): ing.normalise_club("Chelsea FC"),
             ("epl", "bri"): ing.normalise_club("Brighton & Hove Albion FC")}
    idx = ident.fixture_index(fx)
    got, reason = ident.bind_event("epl-che-bri-2026-08-30-total-2pt5", bound, idx)
    assert reason == "BOUND" and (got["FT_HOME"], got["FT_AWAY"]) == (2, 1)


def test_the_home_away_order_is_part_of_the_identifier():
    fx = [fixture("epl", "2026-27", "Chelsea FC", "Brighton & Hove Albion FC",
                  "2026-08-30", (2, 1))]
    bound = {("epl", "che"): ing.normalise_club("Chelsea FC"),
             ("epl", "bri"): ing.normalise_club("Brighton & Hove Albion FC")}
    idx = ident.fixture_index(fx)
    got, reason = ident.bind_event("epl-bri-che-2026-08-30", bound, idx)
    assert got is None
    assert reason == "NO_PUBLIC_FIXTURE_FOR_THIS_ORDERED_PAIR_AND_SEASON"


def test_each_refusal_is_named_separately():
    fx = [fixture("epl", "2026-27", "Chelsea FC", "Brighton & Hove Albion FC",
                  "2026-08-30", (2, 1))]
    bound = {("epl", "che"): ing.normalise_club("Chelsea FC")}
    idx = ident.fixture_index(fx)
    assert ident.bind_event("epl-che-bri-2026-08-30", bound, idx)[1] == \
        "AWAY_CLUB_CODE_UNBOUND"
    assert ident.bind_event("epl-xxx-che-2026-08-30", bound, idx)[1] == \
        "HOME_CLUB_CODE_UNBOUND"
    assert ident.bind_event("epl-xxx-yyy-2026-08-30", bound, idx)[1] == \
        "BOTH_CLUB_CODES_UNBOUND"
    assert ident.bind_event("not-a-slug", bound, idx)[1] == \
        "SLUG_NOT_AN_EVENT_SHAPE"


def test_a_date_beyond_tolerance_refuses_rather_than_taking_the_nearest():
    fx = [fixture("epl", "2026-27", "Chelsea FC", "Brighton & Hove Albion FC",
                  "2026-08-30", (2, 1))]
    bound = {("epl", "che"): ing.normalise_club("Chelsea FC"),
             ("epl", "bri"): ing.normalise_club("Brighton & Hove Albion FC")}
    idx = ident.fixture_index(fx)
    assert ident.bind_event("epl-che-bri-2026-08-31", bound, idx)[1] == "BOUND"
    assert ident.bind_event("epl-che-bri-2026-09-06", bound, idx)[1] == \
        "PUBLIC_FIXTURE_DATE_OUTSIDE_TOLERANCE"


def test_two_candidate_fixtures_are_refused_not_disambiguated_by_distance():
    fx = [fixture("epl", "2026-27", "Chelsea FC", "Brighton & Hove Albion FC",
                  "2026-08-30", (2, 1)),
          fixture("epl", "2026-27", "Chelsea FC", "Brighton & Hove Albion FC",
                  "2026-08-31", (0, 0))]
    bound = {("epl", "che"): ing.normalise_club("Chelsea FC"),
             ("epl", "bri"): ing.normalise_club("Brighton & Hove Albion FC")}
    idx = ident.fixture_index(fx)
    assert ident.bind_event("epl-che-bri-2026-08-30", bound, idx)[1] == \
        "AMBIGUOUS_MULTIPLE_PUBLIC_FIXTURES"


# ---------------------------------------------------------------------------
# agreement: the test of the bind
# ---------------------------------------------------------------------------


def test_agreement_counts_a_wrong_score_as_a_disagreement():
    fx = fixture("epl", "2026-27", "Chelsea FC", "Brighton & Hove Albion FC",
                 "2026-08-30", (2, 1))
    rep = ident.check_agreement({"e1": fx, "e2": fx},
                                {"e1": (2, 1), "e2": (1, 1)})
    assert rep["AGREE"] == 1 and rep["DISAGREE"] == 1
    assert rep["AGREEMENT_RATE"] == 0.5
    assert rep["DISAGREEMENTS"][0]["SLUG"] == "e2"


def test_a_missing_side_is_not_counted_as_agreement():
    played = fixture("epl", "2026-27", "A FC", "B FC", "2026-08-30", (1, 0))
    unplayed = fixture("epl", "2026-27", "C FC", "D FC", "2027-05-30")
    rep = ident.check_agreement({"e1": played, "e2": unplayed}, {"e2": (9, 9)})
    assert rep["COMPARABLE"] == 0
    assert rep["NO_CORPUS_SCORE"] == 1
    assert rep["PUBLIC_RESULT_NOT_YET_IN"] == 1
    assert rep["AGREEMENT_RATE"] is None


# ---------------------------------------------------------------------------
# the standing constraints
# ---------------------------------------------------------------------------


def test_no_forbidden_matching_technique_is_enabled():
    assert ident.MATCH_POLICY == "EXACT_NORMALISED_EQUALITY_OR_REFUSE"
    assert ident.FUZZY_MATCHING_USED is False
    assert ident.TITLE_PARSING_USED is False
    assert ident.PARTIAL_ABBREVIATION_MATCHING_USED is False
    assert ident.NEAREST_START_TIME_USED is False
    assert ident.SINGLE_TEAM_INFERENCE_USED is False
    assert ident.HANDWRITTEN_EXCEPTIONS == ()


def test_the_ingest_touches_nothing_it_must_not():
    assert ing.NO_ORDERS and ing.NO_CAPITAL and ing.NO_CREDENTIALS
    assert ing.MIRROR_LIVE is False
    assert ing.READS_PRODUCTION_DATABASE is False
    assert ing.TOUCHES_RUN85 is False


def test_the_egress_refusal_is_recorded_rather_than_hidden():
    assert "www.football-data.co.uk" in ing.EGRESS_DENIED
    assert "raw.githubusercontent.com" in ing.EGRESS_PERMITTED
    assert ing.EGRESS_BLOCK_IS_BLANKET is False
    assert "403" in ing.EGRESS_DENIAL_TEXT


def test_statsbomb_is_recorded_as_not_overlapping_the_corpus_window():
    assert ing.STATSBOMB_USE == "METADATA_ONLY"
    assert "2023/24" in ing.STATSBOMB_WHY_NOT_USED_FOR_RATINGS
