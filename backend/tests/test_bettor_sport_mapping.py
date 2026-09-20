"""§1. Sport from the venue's own payload, and never from a guess."""

import pytest

from sportsassets import bettor_sport_mapping as sm


# ── source 1: the sport named inside sportsMarketType ────────────────

@pytest.mark.parametrize("sports_type,sport", [
    ("football_team_full_game_spread", "football"),
    ("baseball_player_hits_runs_rbis", "baseball"),
    ("soccer_team_full_time_winner", "soccer"),
    ("tennis_match_winner", "tennis"),
    ("basketball_team_full_game_spread", "basketball"),
    ("hockey_team_full_game_moneyline", "hockey"),
    ("esports_map_winner", "esports"),
    ("ufc_fight_winner", "ufc"),
    ("darts_match_winner", "darts"),
    ("cricket_match_winner", "cricket"),
])
def test_the_market_type_names_the_sport(sports_type, sport):
    r = sm.classify(sports_type=sports_type)
    assert r["SPORT"] == sport
    assert r["SPORT_SOURCE"] == sm.SOURCE_MARKET_TYPE
    assert r["UNRESOLVED_MAPPING_REASON"] is None


def test_table_tennis_is_not_read_as_table():
    """THE LONGEST-MATCH CASE. split_part(x,'_',1) gives 'table', which
    is not a sport, and production carries 5,504 such rows."""
    r = sm.classify(sports_type="table_tennis_match_winner")
    assert r["SPORT"] == "table_tennis"
    assert r["SPORT"] != "table"
    assert "not a sport" in sm.WHY_LONGEST_MATCH


def test_the_prefix_set_is_ordered_longest_first():
    prefixes = [p for p, _s, _n in sm.MARKET_TYPE_PREFIXES]
    assert prefixes[0] == "table_tennis_"
    # No prefix may be preceded by a shorter prefix of itself.
    for i, p in enumerate(prefixes):
        for q in prefixes[:i]:
            assert not p.startswith(q), (q, p)


def test_a_futures_market_names_no_sport():
    r = sm.classify(sports_type="futures")
    assert r["SPORT"] == sm.NOT_IDENTIFIED
    assert r["UNRESOLVED_MAPPING_REASON"] in (
        sm.R_LEAGUE_ABSENT, sm.R_TYPE_NAMES_NO_SPORT)
    assert r["marketTypeReason"] == sm.R_TYPE_NAMES_NO_SPORT


def test_a_bare_moneyline_type_names_no_sport():
    assert "moneyline" in sm.TYPE_PREFIXES_NAMING_NO_SPORT
    r = sm.classify(sports_type="moneyline")
    assert r["SPORT"] == sm.NOT_IDENTIFIED


def test_an_unrecognised_prefix_is_named_not_guessed():
    r = sm.classify(sports_type="pickleball_match_winner")
    assert r["SPORT"] == sm.NOT_IDENTIFIED
    assert r["marketTypeReason"] == sm.R_TYPE_PREFIX_UNKNOWN


# ── source 2: the league, only where the venue attests it ────────────

def test_the_league_fallback_applies_only_to_typeless_markets():
    r = sm.classify(sports_type="futures", team_league="nfl")
    assert r["SPORT"] == "football"
    assert r["SPORT_SOURCE"] == sm.SOURCE_LEAGUE
    assert r["leagueAttestingRows"] == 20170
    assert r["whyFallback"] == sm.R_TYPE_NAMES_NO_SPORT


def test_the_market_type_wins_over_the_league():
    """Precedence, checked where the two could disagree."""
    r = sm.classify(sports_type="soccer_team_full_time_winner",
                    team_league="nfl")
    assert r["SPORT"] == "soccer"
    assert r["SPORT_SOURCE"] == sm.SOURCE_MARKET_TYPE


def test_a_league_below_the_evidence_threshold_is_not_admitted():
    """'pdc' is the darts tour and production carries four rows of it
    on soccer market types. Four rows is not an attestation."""
    assert "pdc" in sm.LOW_EVIDENCE_LEAGUES
    assert "pdc" not in sm.LEAGUE_TO_SPORT
    r = sm.classify(sports_type="futures", team_league="pdc")
    assert r["SPORT"] == sm.NOT_IDENTIFIED
    assert r["UNRESOLVED_MAPPING_REASON"] == sm.R_LEAGUE_LOW_EVIDENCE
    assert "not an attestation" in sm.WHY_A_THRESHOLD


def test_every_admitted_league_clears_the_threshold():
    for lg in sm.LEAGUE_TO_SPORT:
        assert sm.LEAGUE_ATTESTATION[lg] >= sm.ATTESTATION_MIN_ROWS, lg


def test_an_obvious_league_the_venue_never_stated_is_not_inferred():
    """THE DISCIPLINE. 'nba' is basketball and it is not in the table,
    because every nba row production carries is a futures market."""
    assert "nba" not in sm.LEAGUE_TO_SPORT
    assert "cbb" not in sm.LEAGUE_TO_SPORT
    assert "nba" in sm.LEAGUES_WITH_NO_VENUE_STATED_SPORT
    r = sm.classify(sports_type="futures", team_league="nba")
    assert r["SPORT"] == sm.NOT_IDENTIFIED
    assert r["UNRESOLVED_MAPPING_REASON"] == sm.R_LEAGUE_NOT_ATTESTED
    assert "happens to be true" in r["whyNotInferred"]


def test_no_league_defaults_to_soccer():
    """copy_sports.sport_of sends every unmapped league to soccer. This
    module must not, and the difference is the whole point."""
    from sportsassets import copy_sports
    assert copy_sports.sport_of("aec-zzz-a-b-2026-01-01") == "soccer"
    r = sm.classify(sports_type="futures", team_league="zzz")
    assert r["SPORT"] == sm.NOT_IDENTIFIED
    assert r["SPORT"] != "soccer"


# ── the raw source is preserved ──────────────────────────────────────

def test_the_raw_venue_strings_travel_with_the_mapped_value():
    r = sm.classify(sports_type="football_team_full_game_spread",
                    team_league="nfl")
    assert r["SPORT_SOURCE_RAW"] == "football_team_full_game_spread"
    assert r["LEAGUE_SOURCE_RAW"] == "nfl"
    assert r["SPORT"] == "football"
    assert r["LEAGUE"] == "nfl"


def test_absent_raw_sources_are_named_not_dropped():
    r = sm.classify()
    assert r["SPORT_SOURCE_RAW"] == sm.NOT_IDENTIFIED
    assert r["LEAGUE_SOURCE_RAW"] == sm.NOT_IDENTIFIED


def test_a_market_with_no_premap_row_is_its_own_reason():
    r = sm.classify(has_premap_row=False)
    assert r["UNRESOLVED_MAPPING_REASON"] == sm.R_NO_PREMAP_ROW


def test_the_league_is_the_venue_string_lowered_and_nothing_else():
    assert sm.classify(team_league="  NFL  ")["LEAGUE"] == "nfl"


def test_no_fuzzy_matching_is_declared_and_no_title_is_read():
    assert "No title parsing" in sm.NO_FUZZY_MATCHING
    # The classifier takes no title, slug or price argument at all.
    import inspect
    params = set(inspect.signature(sm.classify).parameters)
    assert params == {"sports_type", "team_league", "has_premap_row"}


# ── §1's report ──────────────────────────────────────────────────────

def test_the_report_returns_every_requested_field():
    rows = [sm.classify(sports_type="football_team_full_game_spread",
                        team_league="nfl"),
            sm.classify(sports_type="tennis_match_winner",
                        team_league="atp"),
            sm.classify(sports_type="futures", team_league="nba"),
            sm.classify(sports_type="futures")]
    r = sm.report(rows)
    for field in ("SPORT_MAPPING_STATUS",
                  "TOTAL_ROWS_ELIGIBLE_FOR_MAPPING",
                  "SPORT_IDENTIFIED_ROWS", "SPORT_UNKNOWN_ROWS",
                  "SPORT_IDENTIFICATION_RATE", "SPORTS_OBSERVED",
                  "LEAGUES_OBSERVED", "UNRESOLVED_MAPPING_REASONS"):
        assert field in r, field
    assert r["TOTAL_ROWS_ELIGIBLE_FOR_MAPPING"] == 4
    assert r["SPORT_IDENTIFIED_ROWS"] == 2
    assert r["SPORT_UNKNOWN_ROWS"] == 2
    assert r["SPORT_IDENTIFICATION_RATE"] == 0.5
    assert r["SPORTS_OBSERVED"] == {"football": 1, "tennis": 1}
    assert r["UNRESOLVED_MAPPING_REASONS"][sm.R_LEAGUE_NOT_ATTESTED] == 1


def test_an_empty_set_has_no_identification_rate():
    r = sm.report([])
    assert r["SPORT_IDENTIFICATION_RATE"] == sm.NOT_IDENTIFIED


def test_the_mapping_records_when_and_from_what_it_was_attested():
    a = sm.describe()["attestedFrom"]
    assert a["query"] == "research/bettor_sport_vocabulary.sql"
    assert a["at"].startswith("2026-09-20")
    assert a["premapRows"] == 166625


def test_the_preferred_future_source_is_named_not_silently_absent():
    p = sm.describe()["preferredFutureSource"]
    assert p["field"] == "event.tags[].league.sportId"
    assert p["status"] == "NOT_PERSISTED"


# ── the wiring the defect was actually in ────────────────────────────

def test_the_universe_query_now_selects_the_columns_it_needs():
    from sportsassets import shadow_bettor as sb
    for col in ("p.sports_type", "p.team_league", "p.game_start"):
        assert col in sb.UNIVERSE_SQL, col


def test_wiring_the_mapping_did_not_move_the_policy_code_sha():
    """universe() is outside the decision boundary on purpose."""
    from sportsassets import shadow_bettor_codesha as cs
    assert cs.semantic_code_sha() == (
        "84c80e7c08153db58266fe56780330fbe5d927ba9f6b76ca8e839c7fef7f8110")


# ── a venue-stated non-sport is not an unknown ───────────────────────

def test_an_election_market_is_not_a_sport_and_not_an_unknown():
    """Collapsing the two would put political markets into whichever
    sport cohort absorbed the unreadable ones."""
    r = sm.classify(sports_type="election_winner", team_league=None)
    assert r["SPORT"] == sm.NOT_IDENTIFIED
    assert r["UNRESOLVED_MAPPING_REASON"] == sm.R_NOT_A_SPORT
    assert r["MARKET_CATEGORY"] == "election"
    assert "same bucket" in r["whyNotUnknown"]


def test_a_non_sport_does_not_fall_through_to_the_league():
    """The league would answer a question the row does not pose."""
    r = sm.classify(sports_type="election_winner", team_league="nfl")
    assert r["SPORT"] == sm.NOT_IDENTIFIED
    assert r["UNRESOLVED_MAPPING_REASON"] == sm.R_NOT_A_SPORT
    assert r["SPORT"] != "football"


def test_not_a_sport_is_a_declared_reason():
    assert sm.R_NOT_A_SPORT in sm.UNRESOLVED_REASONS
    assert "election" in sm.describe()["nonSportCategories"].values()


def test_the_residual_query_filter_matches_the_declared_prefix_set():
    """research/bettor_sport_residual.sql duplicates the prefix list as
    a FILTER. It assigns no sport, but a drifted filter would select
    the wrong rows to reason about, so the two are pinned together."""
    import pathlib
    import re
    sql = pathlib.Path(__file__).resolve().parents[2].joinpath(
        "research", "bettor_sport_residual.sql").read_text()
    # The NOT(...) block is the sport-prefix filter. The election and
    # moneyline counts elsewhere in the file are their own categories
    # and are deliberately not part of that set.
    block = sql[sql.index("NOT (p.sports_type"):]
    block = block[:block.index(")\n")]
    in_sql = {m.replace("\\", "") + "_"
              for m in re.findall(r"LIKE '([a-z\\_]+)_%'", block)}
    declared = {p for p, _s, _n in sm.MARKET_TYPE_PREFIXES}
    assert in_sql == declared, (declared - in_sql, in_sql - declared)
