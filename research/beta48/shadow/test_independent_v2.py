"""Tests for the V2 zoo, the source ingest, the linkage and the three-expert
comparison. No network: every fetch is injected."""

import datetime
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import club_football_ingest as CF  # noqa: E402
import ev_core as EVC  # noqa: E402
import ev_core_three_expert as T3  # noqa: E402
import p_bettor_independent as V1  # noqa: E402
import p_bettor_independent_v2 as V2  # noqa: E402
import p_external_consensus as XC  # noqa: E402
import public_source_linkage as PL  # noqa: E402

D0 = datetime.date(2024, 1, 6)


def match(day, div, h, a, fh, fa, **kw):
    r = {"Division": div,
         "MatchDate": (D0 + datetime.timedelta(days=day)).isoformat(),
         "MatchTime": "15:00:00", "HomeTeam": h, "AwayTeam": a,
         "FTHome": str(fh), "FTAway": str(fa),
         "HTHome": "0", "HTAway": "0",
         "HomeElo": "1500", "AwayElo": "1500",
         "Form3Home": "3", "Form5Home": "5", "Form3Away": "3", "Form5Away": "5",
         "HomeShots": "12", "AwayShots": "9", "HomeTarget": "5",
         "AwayTarget": "3", "HomeCorners": "6", "AwayCorners": "4",
         "OddHome": "2.00", "OddDraw": "3.40", "OddAway": "4.00",
         "Over25": "1.90", "Under25": "1.95"}
    r.update(kw)
    return r


# ---------------------------------------------------------------------------
# Section 1: the V1 verdict must not widen
# ---------------------------------------------------------------------------


def test_the_v1_result_is_recorded_at_the_width_it_was_measured():
    assert V1.INDEPENDENT_V1_RESULT == \
        "NO_INCREMENTAL_SIGNAL_ON_TESTED_RN1_TRIGGERED_SAMPLE"
    assert V1.GENERAL_INDEPENDENT_ALPHA_STATUS == "NOT_YET_IDENTIFIED"
    assert V1.V1_RESULT_MUST_NOT_BE_RESTATED_AS == \
        "INDEPENDENT_SPORTS_DATA_CANNOT_ADD_VALUE"
    assert V1.V1_TESTED_SAMPLE["INDEPENDENT_EVENTS"] == 157
    assert V1.V1_TESTED_SAMPLE["OBSERVATION_CLOCK"] == "RN1_TRADE_TRIGGERED"
    assert V1.V1_TESTED_SAMPLE["SHARE_IN_PLAY_PCT"] == 20.1


def test_the_v2_zoo_carries_the_same_reclassification():
    d = V2.describe()
    assert d["INDEPENDENT_V1_RESULT"] == V1.INDEPENDENT_V1_RESULT
    assert d["GENERAL_INDEPENDENT_ALPHA_STATUS"] == "NOT_YET_IDENTIFIED"


# ---------------------------------------------------------------------------
# Section 6: the feature split is physical
# ---------------------------------------------------------------------------


def test_the_column_groups_are_disjoint_and_cover_the_header():
    header = (list(CF.IDENTITY_COLUMNS) + list(CF.FUNDAMENTAL_COLUMNS)
              + list(CF.SAME_MATCH_OUTCOME_COLUMNS)
              + list(CF.EXTERNAL_MARKET_COLUMNS)
              + list(CF.DERIVED_OPAQUE_COLUMNS))
    rep = CF.check_groups(header)
    assert rep["GROUPS_ARE_DISJOINT"] is True
    assert rep["EVERY_COLUMN_IS_GROUPED"] is True
    assert rep["IN_MORE_THAN_ONE_GROUP"] == []


def test_the_fundamental_accessor_cannot_return_an_odd():
    r = match(0, "E0", "A", "B", 1, 0)
    f = CF.fundamental_row(r)
    for k in CF.EXTERNAL_MARKET_COLUMNS:
        assert k not in f
    for k in CF.SAME_MATCH_OUTCOME_COLUMNS:
        assert k not in f


def test_the_market_accessor_is_the_only_one_that_carries_odds():
    r = match(0, "E0", "A", "B", 1, 0)
    m = CF.market_row(r)
    assert m["OddHome"] == 2.0
    assert "FTHome" not in m


def test_no_glm_feature_is_an_odds_column():
    assert not (set(V2.GLM_FEATURES) & set(CF.EXTERNAL_MARKET_COLUMNS))
    assert V2.EXTERNAL_MARKET_FEATURES_USED == "NO"
    assert V2.SAME_MATCH_STATISTICS_USED_AS_FEATURES == "NO"


def test_the_elo_split_keeps_the_provisional_era_apart():
    body = (b"date,club,country,elo\n"
            b"2024-01-01,X,ENG,1500\n"
            b"2026-01-01,X,ENG,1600\n")
    rows, rep = CF.parse_elo(body)
    assert rep["BY_SOURCE"][CF.ELO_SOURCE_HISTORICAL] == 1
    assert rep["BY_SOURCE"][CF.ELO_SOURCE_PROVISIONAL] == 1
    assert "not ClubElo" in rep["PROVISIONAL_WARNING"]


def test_the_source_is_recorded_as_a_mirror_of_a_blocked_upstream():
    p = CF.SOURCE_PROVENANCE
    assert p["IS_PRIMARY_SOURCE"] is False
    assert p["UPSTREAM_REACHABLE_FROM_THIS_ENVIRONMENT"] is False
    assert p["LICENSE_STATUS"] == "NOT_IDENTIFIED"
    assert "Football-Data.co.uk" in p["ORIGINAL_UPSTREAM_SOURCES"][
        "MATCH_RESULTS_AND_STATISTICS"]


def test_the_kickoff_timezone_claim_is_recorded_as_unverified():
    assert CF.MATCHTIME_TIMEZONE_VERIFIED == "NO"
    assert CF.MATCHTIME_TIMEZONE_STATUS == "NOT_IDENTIFIED"
    assert CF.CROSS_SOURCE_OFFSET_IS_SEASONAL is False
    assert CF.NO_HORIZON_IS_BOTH_ANCHORABLE_AND_POPULATED is True
    # continental leagues differ by an hour, UK and Portugal by nothing
    assert CF.CROSS_SOURCE_KICKOFF_OFFSET_HOURS["lal"] == -1.0
    assert CF.CROSS_SOURCE_KICKOFF_OFFSET_HOURS["epl"] == 0.0


def test_the_division_map_records_the_mistake_it_already_made():
    assert CF.DIVISION_TO_VENUE_LEAGUE["E1"] == "elc"
    assert "EC" not in CF.DIVISION_TO_VENUE_LEAGUE
    assert "National League" in CF.DIVISION_MAP_CAVEAT


# ---------------------------------------------------------------------------
# Section 9: strict temporal features
# ---------------------------------------------------------------------------


def _season(n_clubs=10, rounds=12, seed=5):
    import random
    rnd = random.Random(seed)
    clubs = ["c%d" % i for i in range(n_clubs)]
    out, day = [], 0
    for _r in range(rounds):
        order = clubs[:]
        rnd.shuffle(order)
        for i in range(0, n_clubs - 1, 2):
            day += 3
            out.append(match(day, "E0", order[i], order[i + 1],
                             rnd.randint(0, 4), rnd.randint(0, 3)))
    return out


def test_a_rolling_feature_never_contains_its_own_match():
    rows = _season()
    frame = V2.build_frame(rows)
    # the first appearance of any club has no rolling history at all
    seen = set()
    for f in frame:
        for side, team in (("HOME", f["HOME"]), ("AWAY", f["AWAY"])):
            if team in seen:
                continue
            assert f["GF5_%s" % side] is None
            assert f["MATCHES_SEEN_%s" % side] == 0
            seen.add(team)


def test_a_future_result_cannot_reach_an_earlier_feature():
    rows = _season(rounds=20)
    rep = V2.leakage_test(rows)
    assert rep["STATUS"] == "MEASURED"
    assert rep["NO_LOOKAHEAD"] is True
    assert rep["EARLIER_ROWS_THAT_MOVED"] == 0


def test_the_leakage_test_is_not_vacuous():
    # the same corruption MUST move something later, or the test proves nothing
    rows = _season(rounds=20)
    rep = V2.leakage_test(rows)
    assert rep["THE_CORRUPTION_DID_PROPAGATE_FORWARD"] is True
    assert rep["LATER_ROWS_THAT_MOVED"] > 0


def test_rest_days_count_from_the_previous_match_only():
    rows = [match(0, "E0", "a", "b", 1, 0), match(7, "E0", "a", "c", 2, 2),
            match(14, "E0", "a", "d", 0, 0)]
    frame = V2.build_frame(rows)
    assert frame[0]["REST_DAYS_HOME"] is None
    assert frame[1]["REST_DAYS_HOME"] == 7
    assert frame[2]["REST_DAYS_HOME"] == 7


# ---------------------------------------------------------------------------
# Section 7: the zoo
# ---------------------------------------------------------------------------


def test_every_member_either_fits_or_says_why_not():
    # B1 needs 200 rows with an Elo and B6 needs 500; the fixture is
    # sized so a refusal means a real refusal, not a small world
    frame = V2.build_frame(_season(rounds=130))
    zoo = V2.fit_zoo(frame, models=("B1_ELO", "B2_POISSON", "B3_DIXON_COLES",
                                    "B5_DYNAMIC_ATTACK_DEFENCE",
                                    "B6_REGULARIZED_GLM"))
    for m in ("B1_ELO", "B2_POISSON", "B3_DIXON_COLES",
              "B5_DYNAMIC_ATTACK_DEFENCE", "B6_REGULARIZED_GLM"):
        f = zoo[m]
        assert f.get("FITTED") is True or f.get("REASON")


def test_every_qualified_member_emits_a_probability_distribution():
    # B1 needs 200 rows with an Elo and B6 needs 500; the fixture is
    # sized so a refusal means a real refusal, not a small world
    frame = V2.build_frame(_season(rounds=130))
    zoo = V2.fit_zoo(frame, models=("B1_ELO", "B2_POISSON", "B3_DIXON_COLES",
                                    "B5_DYNAMIC_ATTACK_DEFENCE",
                                    "B6_REGULARIZED_GLM"))
    for m in ("B1_ELO", "B2_POISSON", "B3_DIXON_COLES",
              "B5_DYNAMIC_ATTACK_DEFENCE", "B6_REGULARIZED_GLM"):
        g, why = V2.grid(zoo, m, frame[-1])
        assert g is not None, (m, why)
        assert abs(sum(g.values()) - 1.0) < 1e-9


def test_a_club_the_zoo_never_saw_is_refused_not_averaged():
    # B1 needs 200 rows with an Elo and B6 needs 500; the fixture is
    # sized so a refusal means a real refusal, not a small world
    frame = V2.build_frame(_season(rounds=130))
    zoo = V2.fit_zoo(frame, models=("B2_POISSON",))
    row = dict(frame[-1], HOME="a club that never played")
    g, why = V2.grid(zoo, "B2_POISSON", row)
    assert g is None and why == "CLUB_NOT_IN_FIT"


def test_b4_is_disqualified_by_the_directives_own_criterion():
    ok, why = V2.qualified("B4_BIVARIATE_POISSON")
    assert ok is False
    assert why == "DISQUALIFIED_NOT_RECONCILED_INTO_A_COHERENT_DISTRIBUTION"
    d = V2.DISQUALIFIED_MODELS["B4_BIVARIATE_POISSON"]
    assert d["THIS_IS_NOT_EVIDENCE_ABOUT_BIVARIATE_POISSON_MODELS"] is True
    assert "B4_BIVARIATE_POISSON" not in V2.QUALIFIED_MODEL_IDS
    assert V2.qualified("B3_DIXON_COLES")[0] is True


# ---------------------------------------------------------------------------
# Linkage without names
# ---------------------------------------------------------------------------


NAMES_A = ["alpha fc", "beta fc", "gamma fc", "delta fc"]
NAMES_B = {"alpha fc": "Alpha", "beta fc": "Beta",
           "gamma fc": "Gamma", "delta fc": "Delta"}


def _two_sources():
    """A real double round-robin: every ordered pair meets exactly once.

    That matters. An earlier version of this fixture had two clubs meeting
    twelve times, and `verify_scores` correctly refused every one of them as
    ambiguous -- which looked like a bug in the linkage and was a bug in the
    fixture.
    """
    a, day = [], 0
    for i, h in enumerate(NAMES_A):
        for j, aw in enumerate(NAMES_A):
            if i == j:
                continue
            day += 3
            a.append({"DATE": (D0 + datetime.timedelta(days=day)).isoformat(),
                      "HOME": h, "AWAY": aw,
                      "FT_HOME": (i + day) % 4, "FT_AWAY": (j + day) % 3})
    b = [dict(x, HOME=NAMES_B[x["HOME"]], AWAY=NAMES_B[x["AWAY"]]) for x in a]
    return a, b


def test_two_sources_are_linked_without_comparing_their_names():
    a, b = _two_sources()
    mapped, rep = PL.club_map(a, b, min_votes=3)
    assert mapped == NAMES_B
    assert rep["NAMES_COMPARED"] is False
    assert PL.HANDWRITTEN_EXCEPTIONS == ()


def test_the_verification_is_run_on_every_fixture_not_just_the_seeds():
    a, b = _two_sources()
    mapped, _ = PL.club_map(a, b, min_votes=3)
    links, rep = PL.verify_scores(a, b, mapped)
    assert rep["COMPARED"] == len(a)
    assert rep["DISAGREE"] == 0
    assert rep["AGREEMENT_RATE"] == 1.0


def test_a_wrong_club_map_is_caught_by_the_score_check():
    a, b = _two_sources()
    bad = {"alpha fc": "Beta", "beta fc": "Alpha",
           "gamma fc": "Delta", "delta fc": "Gamma"}
    _links, rep = PL.verify_scores(a, b, bad)
    assert rep["DISAGREE"] > 0 or rep["UNMATCHED"] == len(a)


def test_a_club_with_too_few_votes_is_refused():
    a, b = _two_sources()
    mapped, rep = PL.club_map(a[:2], b[:2], min_votes=5)
    assert mapped == {}
    assert rep["REFUSED_TOO_FEW_VOTES"] >= 1


# ---------------------------------------------------------------------------
# Sections 10-12: the external consensus
# ---------------------------------------------------------------------------


def test_every_devig_method_returns_probabilities_summing_to_one():
    odds = [2.00, 3.40, 4.00]
    for m in XC.DEVIG_METHODS:
        p, why = XC.devig(odds, m)
        assert p is not None, (m, why)
        assert abs(sum(p) - 1.0) < 1e-6
        assert all(0 < x < 1 for x in p)


def test_the_methods_disagree_most_on_the_longshot():
    odds = [1.20, 7.00, 15.00]
    got = {m: XC.devig(odds, m)[0] for m in XC.DEVIG_METHODS}
    longshot = [got[m][2] for m in XC.DEVIG_METHODS]
    favourite = [got[m][0] for m in XC.DEVIG_METHODS]
    # relative, not absolute: the favourite's probability is an order of
    # magnitude larger, so it wins any absolute comparison for free
    rel = lambda v: (max(v) - min(v)) / (sum(v) / len(v))   # noqa: E731
    assert rel(longshot) > rel(favourite)


def test_shin_takes_more_off_the_longshot_than_multiplicative_does():
    odds = [1.20, 7.00, 15.00]
    mult = XC.devig(odds, XC.METHOD_MULTIPLICATIVE)[0]
    shin = XC.devig(odds, XC.METHOD_SHIN)[0]
    assert shin[2] < mult[2]


def test_unreadable_odds_refuse():
    assert XC.devig(["x", "y"])[0] is None
    assert XC.devig([0.5, 2.0])[0] is None


def test_an_untimestamped_odd_may_be_compared_only_against_settlement():
    ok, _ = XC.may_compare_at("SETTLEMENT")
    assert ok is True
    ok, why = XC.may_compare_at("T-6H")
    assert ok is False and "REFUSED" in why
    assert XC.EXTERNAL_ODDS_TIME_PRECISION == "COARSE_PREMATCH_UNTIMESTAMPED"


def test_the_rejected_odds_source_is_recorded_with_its_reason():
    r = XC.REJECTED_SOURCE
    assert r["WHY_NOT_USED"] == "NO_OVERLAP_WITH_EVALUATION_WINDOW"
    assert "2024/25" in r["ITS_COVERAGE"]


def test_the_consensus_grid_refuses_on_too_few_prices():
    g, rep = XC.consensus_grid({"OddHome": 2.0})
    assert g is None and rep["STATUS"] == XC.FIT_STATUS_REFUSED


def test_the_consensus_grid_is_a_distribution_when_it_fits():
    g, rep = XC.consensus_grid(
        {"OddHome": 2.0, "OddDraw": 3.4, "OddAway": 4.0,
         "Over25": 1.9, "Under25": 1.95}, XC.METHOD_SHIN)
    assert rep["STATUS"] == XC.FIT_STATUS_OK
    assert abs(sum(g.values()) - 1.0) < 1e-9
    assert rep["OVERROUND_1X2"] > 0


def test_the_consensus_is_independent_of_the_venue_but_not_of_markets():
    d = XC.describe()
    assert d["IS_INDEPENDENT_OF_THE_VENUE"] is True
    assert d["IS_INDEPENDENT_OF_MARKETS_IN_GENERAL"] is False


# ---------------------------------------------------------------------------
# Section 3: event-equal weighting
# ---------------------------------------------------------------------------


def _weighted_rows():
    # one loud fixture the forecaster gets right, one quiet fixture it gets
    # badly wrong: row weighting hides the second, event weighting does not
    rows = [{"EVENT_KEY": "loud", "P": 0.9, "Y": 1} for _ in range(100)]
    rows += [{"EVENT_KEY": "quiet", "P": 0.9, "Y": 0}]
    return rows


def test_event_equal_weighting_does_not_let_one_fixture_dominate():
    rows = _weighted_rows()
    ee = T3.event_equal_weighted(rows, "P")
    rw, _ = T3.row_weighted([(r["P"], r["Y"]) for r in rows])
    assert ee["EVENTS"] == 2
    assert ee["LOG_LOSS"] > rw
    assert ee["ROWS_PER_EVENT_MAX"] == 100


def test_both_weightings_are_reported_and_the_event_one_is_principal():
    rows = [{"EVENT_KEY": "e%d" % (i // 3), "P_MARKET": 0.5, "Y": i % 2}
            for i in range(60)]
    s = T3.score_expert(rows, "P_MARKET", draws=20)
    assert "ROW_WEIGHTED_LOG_LOSS" in s
    assert "EVENT_EQUAL_WEIGHTED_LOG_LOSS" in s
    assert s["PRINCIPAL_WEIGHTING"] == "EVENT_EQUAL_WEIGHTED"
    assert T3.PRINCIPAL_WEIGHTING == "EVENT_EQUAL_WEIGHTED"


# ---------------------------------------------------------------------------
# Section 14: chronological orthogonality
# ---------------------------------------------------------------------------


def _ortho_rows(share, n_events=160, per=6, seed=4):
    import random
    rnd = random.Random(seed)
    rows = []
    for e in range(n_events):
        extra = rnd.gauss(0, 1.0)
        ev = "epl-a-b-2026-%02d-%02d" % (1 + e // 28, 1 + e % 28)
        for _c in range(per):
            base = rnd.gauss(0, 1.0)
            z = base + extra
            y = 1 if rnd.random() < 1 / (1 + math.exp(-z)) else 0
            rows.append({"EVENT_KEY": ev, "Y": y,
                         "P_MARKET": 1 / (1 + math.exp(-base)),
                         "P_C": 1 / (1 + math.exp(-(base + share * extra)))})
    return rows


def test_the_split_is_chronological_and_no_event_spans_it():
    rows = _ortho_rows(0.0)
    dev, test, split = T3.chronological_split(rows)
    assert split["SPLIT_IS_CHRONOLOGICAL"] is True
    assert not ({r["EVENT_KEY"] for r in dev}
                & {r["EVENT_KEY"] for r in test})
    assert max(r["EVENT_KEY"] for r in dev) < max(r["EVENT_KEY"]
                                                  for r in test)


def test_a_challenger_with_nothing_extra_is_not_detected():
    rep = T3.orthogonality_chronological(_ortho_rows(0.0), challenger="P_C",
                                         draws=120)
    assert rep["STATUS"] == "MEASURED"
    assert rep["INCREMENTAL_SIGNAL_STATUS"] == \
        "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"


def test_a_challenger_that_does_know_something_extra_is_detected():
    rep = T3.orthogonality_chronological(_ortho_rows(1.0, seed=12),
                                         challenger="P_C", draws=200)
    assert rep["INCREMENTAL_SIGNAL_STATUS"] == "DETECTED"
    assert rep["CHALLENGER_COEFFICIENT"] > 0.1
    assert rep["DELTA_LOG_LOSS_CI"]["CI95_LOW"] > 0


def test_not_detected_is_never_written_as_absent():
    rep = T3.orthogonality_chronological(_ortho_rows(0.0), challenger="P_C",
                                         draws=120)
    assert "not detected" in rep["NOT_DETECTED_IS_NOT_ABSENT"]
    assert "not that it is zero" in rep["NOT_DETECTED_IS_NOT_ABSENT"]


def test_error_correlation_is_reported_and_is_bounded():
    rows = _ortho_rows(1.0)
    c = T3.error_correlation(rows, "P_MARKET", "P_C")
    assert -1.0 <= c <= 1.0


# ---------------------------------------------------------------------------
# Section 20: the Gen2 holdout is prospective and frozen
# ---------------------------------------------------------------------------


def test_the_gen2_holdout_is_in_the_future_and_cannot_be_peeked_at():
    g = EVC.gen2_holdout()
    assert g["GEN2_PROSPECTIVE_HOLDOUT_STATUS"] == "FROZEN_PROSPECTIVE"
    assert g["GEN2_HOLDOUT_KIND"] == \
        "PROSPECTIVE_NOT_A_SLICE_OF_RETAINED_DATA"
    assert g["GEN2_FREEZE_STAMP"].endswith("Z")
    assert "strictly after" in g["GEN2_HOLDOUT_DEFINITION"]


def test_the_gen2_challengers_are_named_before_the_data_exists():
    g = EVC.gen2_holdout()
    assert set(g["GEN2_PREREGISTERED_CHALLENGERS"]) == {
        "FULL_TIME_MONEYLINE", "FULL_TIME_TOTAL", "DRAW", "EXACT_SCORE"}
    assert "not validated alpha" in g["GEN2_PREREGISTRATION_NOTE"]


def test_gen1_stays_burned_and_gen2_forbids_the_same_eight_uses():
    g = EVC.gen2_holdout()
    assert g["GENERATION_1_FINAL_HOLDOUT"] == "BURNED_FOR_MODEL_SELECTION"
    forb = " ".join(g["GEN2_FORBIDDEN_USES_BEFORE_THE_TRIGGER"])
    for phrase in ("model family", "features", "calibration method",
                   "hyper-parameters", "thresholds", "ensemble weights"):
        assert phrase in forb


# ---------------------------------------------------------------------------
# Section 16
# ---------------------------------------------------------------------------


def test_the_surface_evidence_is_labelled_rn1_triggered_only():
    import ev_core_surface_asof as A
    assert A.MARKET_SURFACE_CURRENT_EVIDENCE_STATUS == \
        "RN1_TRIGGERED_SAMPLE_ONLY"
    assert A.SURFACE_RESIDUALS_MAY_NOT_BE_PROMOTED_INTO_EXECUTION_POLICY is True
