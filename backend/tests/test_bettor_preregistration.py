"""The pre-registration, pinned.

Owner directive 2026-09-20 §9: "Do NOT trade it. Do NOT tune bands from
those historical results. Pre-register only the hypothesis ... Do not
call this validation of the historical RN1 result unless the population
and measurement are genuinely comparable."

A pre-registration that can be edited after the data matures is not one.
These tests hold the parts that would be tempting to move: the bands,
the family size, the gate, and the sentence forbidding the conclusion
that the capture validates the RN1 result.
"""

import pytest

from sportsassets import bettor_preregistration as pre
from sportsassets import bettor_state_capture as sc


# ── the bands were not fitted to anything ────────────────────────────

def test_the_bands_are_symmetric_about_a_half():
    """A scheme fitted to a prior result is the same mistake as a
    threshold fitted to a holdout. Symmetry is checkable; intent is
    not."""
    from decimal import Decimal
    edges = [Decimal(lo) for _n, lo, _h in pre.PRICE_BANDS] + \
            [Decimal(pre.PRICE_BANDS[-1][2])]
    mirrored = sorted(Decimal("1") - e for e in edges)
    assert sorted(edges) == mirrored


def test_the_bands_tile_the_whole_unit_interval_without_gaps():
    from decimal import Decimal
    lo = Decimal("0")
    for _n, a, b in pre.PRICE_BANDS:
        assert Decimal(a) == lo, (a, lo)
        lo = Decimal(b)
    assert lo == Decimal("1.00")


def test_every_price_lands_in_exactly_one_band():
    for p in ("0.00", "0.049", "0.05", "0.5", "0.95", "0.999", "1.00"):
        assert pre.band_of(p) != pre.NOT_IDENTIFIED, p
    assert pre.band_of("nonsense") == pre.NOT_IDENTIFIED
    assert pre.band_of("1.5") == pre.NOT_IDENTIFIED


def test_the_bands_are_declared_untuned_in_words_too():
    t = pre.BANDS_ARE_NOT_TUNED.lower()
    assert "not derived from the rn1 result" in t
    assert "fixed before any row matured" in t


# ── the plan is frozen, and its drift is detectable ──────────────────

def test_the_plan_sha_moves_when_the_plan_moves():
    before = pre.plan_sha()
    pre.MULTIPLICITY["alpha"] = "0.10"
    try:
        assert pre.plan_sha() != before
    finally:
        pre.MULTIPLICITY["alpha"] = "0.05"
    assert pre.plan_sha() == before


def test_the_plan_records_the_sampling_version_it_was_registered_against():
    """Pinned as history, not read live. The earlier form asserted the
    plan tracked the CURRENT rule sha, which is precisely the coupling
    that broke the freeze when the sampling rule was amended."""
    assert pre.PLAN_REGISTERED_BEFORE_ANY_ROW_MATURED is True
    p = pre.describe_plan()
    assert p["registeredAgainstRuleSha"] == pre.REGISTERED_AGAINST_RULE_SHA
    assert p["registeredAgainstUniverseVersion"] == \
        "BETTOR_UNSELECTED_STATE_V1"
    assert sc.UNIVERSE_VERSION != p["registeredAgainstUniverseVersion"]


def test_the_multiplicity_family_is_the_declared_test_list():
    """A correction applied to however many tests happened to get run
    is not a correction."""
    assert pre.MULTIPLICITY["familySize"] == len(pre.TESTS)
    assert set(pre.MULTIPLICITY["family"]) == {t["id"] for t in pre.TESTS}


def test_every_test_declares_its_estimand_object_and_scope():
    for t in pre.TESTS:
        assert t["object"] == sc.OBJECT_A, t["id"]
        assert t["estimand"].strip(), t["id"]
        assert t["scope"].strip(), t["id"]
        assert t["clustering"] == "by event_id", t["id"]


# ── the gate fails closed ────────────────────────────────────────────

def test_the_gate_is_shut_on_an_empty_dataset():
    g = pre.gate()
    assert g["TESTS_MAY_RUN"] is False
    assert g["STATUS"] == pre.NOT_YET_EVALUABLE
    assert g["BLOCKERS"]


def test_the_gate_counts_events_not_rows():
    """Rows within one event are not independent -- a game's markets
    move together, so 10,000 rows from 40 events carry about 40 events'
    worth of information."""
    assert "INDEPENDENT_EVENTS" in pre.gate(
        independent_events=10, matured_settlements=10_000_000)["BLOCKERS"][0]
    assert "not independent" in pre.WHY_EVENTS_NOT_ROWS


def test_a_thin_band_blocks_the_gate_by_name():
    full = {n: 10_000 for n, _l, _h in pre.PRICE_BANDS}
    full["DEEP_LOW"] = 1
    g = pre.gate(independent_events=100_000,
                 matured_settlements=100_000, events_per_band=full)
    assert g["TESTS_MAY_RUN"] is False
    assert any("DEEP_LOW" in b for b in g["BLOCKERS"])


def test_the_gate_opens_only_when_every_minimum_is_met():
    g = pre.gate(independent_events=pre.MIN_INDEPENDENT_EVENTS,
                 matured_settlements=pre.MIN_MATURED_SETTLEMENTS,
                 events_per_band={n: pre.MIN_EVENTS_PER_BAND
                                  for n, _l, _h in pre.PRICE_BANDS})
    assert g["TESTS_MAY_RUN"] is True
    assert g["STATUS"] == "GATE_OPEN"


def test_an_open_gate_still_authorises_no_trading():
    g = pre.gate(independent_events=10**6, matured_settlements=10**6,
                 events_per_band={n: 10**6
                                  for n, _l, _h in pre.PRICE_BANDS})
    assert g["TESTS_MAY_RUN"] is True
    assert "authorises an order" in g["doNotTradeThis"]


# ── §8. the comparison, and what it may not compare ──────────────────

def test_pnl_and_net_ev_are_refused_as_comparison_features():
    for forbidden in ("PNL", "NET_EV", "ADVERSE_SELECTION"):
        assert forbidden in pre.COMPARISON_FORBIDDEN
        assert forbidden not in pre.COMPARISON_FEATURES


def test_the_comparison_features_are_structural():
    for expected in ("PRICE_BAND", "SPREAD", "BOOK_IMBALANCE",
                     "RECENT_MOVE", "TIME_TO_EVENT", "MARKET_TYPE",
                     "SETTLEMENT_DIRECTION"):
        assert expected in pre.COMPARISON_FEATURES, expected


def test_the_comparison_does_not_transfer_economics_between_samples():
    assert "does not transfer either sample's economics" in \
        pre.COMPARISON_SCOPE
    assert "a fact about selection, not an edge" in pre.COMPARISON_SCOPE


# ── §9. the conclusion that may not be drawn ─────────────────────────

def test_agreement_with_rn1_is_explicitly_not_confirmation():
    t = pre.NOT_A_VALIDATION_OF_RN1
    assert "is NOT validation" in t
    assert "fill-conditional" in t
    assert "different venue" in t
    assert "is interesting and is not confirmation" in t


def test_the_plan_identifies_a_and_disclaims_b_c_and_d():
    p = pre.describe_plan()
    assert p["identifiesObject"] == sc.OBJECT_A
    assert set(p["doesNotIdentify"]) == {sc.OBJECT_B, sc.OBJECT_C,
                                         sc.OBJECT_D}


def test_the_plan_cannot_be_given_a_forbidden_name():
    with pytest.raises(sc.ForbiddenName):
        sc.forbidden_name("UNCONDITIONAL_MAKER_ADVERSE_SELECTION")


# ── the chronology, the amendments, and the sha disclosure ───────────

def test_the_plan_predates_the_current_sampling_versions_first_row():
    """The load-bearing ordering. A zero maturation count is an
    assertion about a table; this is an assertion about the record."""
    ev = {c["event"]: c["at"] for c in pre.CHRONOLOGY}
    assert ev["PLAN_FROZEN"] < ev["CAPTURE_V2_DEPLOY_LIVE"]
    assert ev["FIRST_OUTCOME_ACCESS"] == "NOT_YET_OCCURRED"


def test_the_chronology_is_ordered_and_every_entry_has_a_reference():
    stamped = [c for c in pre.CHRONOLOGY if c["at"] != "NOT_YET_OCCURRED"]
    assert [c["at"] for c in stamped] == sorted(c["at"] for c in stamped)
    for c in stamped:
        assert c.get("ref"), c["event"]
        assert c["at"].endswith("Z"), c["event"]


def test_no_amendment_touched_a_hypothesis_or_a_gate():
    """Amendments may change how data is collected. They may not change
    what is being tested or when the test is allowed to run."""
    for a in pre.AMENDMENTS:
        assert a["hypothesesChanged"] is False, a["id"]
        assert a["gatesChanged"] is False, a["id"]
        assert a["outcomeDataSeenFirst"] is False, a["id"]
        assert a["appliesToCohort"], a["id"]
        assert a["why"], a["id"]


def test_the_frozen_fields_are_named_and_still_hold_their_values():
    """Asserted against the VALUES, independently of any hash -- which
    is what makes the sha disclosure checkable rather than a promise."""
    assert "H0" in pre.FROZEN_ACROSS_ALL_AMENDMENTS
    assert "PRICE_BANDS" in pre.FROZEN_ACROSS_ALL_AMENDMENTS
    assert len(pre.PRICE_BANDS) == 7
    assert len(pre.TESTS) == 5
    assert pre.MULTIPLICITY["alpha"] == "0.05"
    assert pre.MIN_INDEPENDENT_EVENTS == 400
    assert pre.MIN_MATURED_SETTLEMENTS == 1_000
    assert pre.MIN_EVENTS_PER_BAND == 30


def test_the_lost_original_sha_is_disclosed_not_quietly_replaced():
    assert pre.ORIGINAL_PLAN_SHA == "5c81ca7b0accf747"
    assert pre.PLAN_SHA[:16] != pre.ORIGINAL_PLAN_SHA
    d = pre.PLAN_SHA_DISCLOSURE
    assert "5c81ca7b0accf747" in d
    assert "no hypothesis, band, test, correction or gate changed" in d


def test_the_plan_no_longer_tracks_the_live_sampling_rule():
    """The coupling that broke the freeze: an analysis plan must not
    take its identity from a sampling version it is meant to span."""
    import inspect
    src = inspect.getsource(pre.describe_plan)
    assert "sc.RULE_SHA" not in src
    assert "sc.UNIVERSE_VERSION" not in src
    assert pre.REGISTERED_AGAINST_RULE_SHA == "552cc26d247732f2"
    before = pre.plan_sha()
    saved = sc.RULE_SHA
    try:
        sc.RULE_SHA = "a-completely-different-rule"
        assert pre.plan_sha() == before
    finally:
        sc.RULE_SHA = saved


# ── the deviations, disclosed rather than smoothed over ──────────────

def test_the_late_freeze_is_recorded_as_a_deviation():
    """"Registered before any row matured" answered a weaker question
    than the directive asked. The directive said before ROW 1."""
    d = pre.DEVIATION_PLAN_FROZEN_AFTER_ROW_ONE
    assert "DEVIATED" in d["analysisPlan"]
    assert "121 seconds late" in d["analysisPlan"]
    assert "That is an argument about what was" in d["assessment"]
    # What was accessible must name the outcome tables explicitly.
    joined = " ".join(d["INFORMATION_ACCESSIBLE_AT_FREEZE_TIME"])
    assert "NO settlement rows" in joined
    assert "NO mid/outcome rows" in joined


def test_the_original_hash_reproduces_from_its_own_commit():
    d = pre.PLAN_SHA_DISCLOSURE
    assert "REPRODUCES EXACTLY from commit 157969c" in d
    assert "no missing input" in d
    assert pre.ORIGINAL_PLAN_SHA == "5c81ca7b0accf747"


def test_the_endpoint_meaning_change_is_disclosed():
    """A diff of the hypotheses would not show this one."""
    d = pre.DEVIATION_ENDPOINT_MEANING_CHANGED
    assert d["hypothesesRewritten"] is False
    assert "existence no longer implies on time" in d["after"]
    assert "ON_TIME" in d["mitigation"]
    assert "T2_PRICE_BAND_SHORT_HORIZON_DRIFT" in d["affectsWhichTests"]


def test_the_cohort_exclusion_predates_any_outcome():
    d = pre.DEVIATION_COHORT_EXCLUSION
    assert d["decidedBeforeAnyOutcomeExisted"] is True
    assert "retained and never deleted" in d["rowsAffected"]


def test_only_on_time_reads_are_admissible_to_a_horizon_gate():
    """The mitigation, asserted against the capture module rather than
    trusted to the prose."""
    assert sc.ADMISSIBLE_TO_HORIZON_GATE == (sc.TIMING_ON_TIME,)
    assert sc.TIMING_LATE_RECOVERY not in sc.ADMISSIBLE_TO_HORIZON_GATE
    assert "NEVER" in sc.LATE_IS_NOT_ON_TIME
