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


def test_the_plan_records_that_it_predates_the_data():
    assert pre.PLAN_REGISTERED_BEFORE_ANY_ROW_MATURED is True
    assert pre.describe_plan()["ruleSha"] == sc.RULE_SHA


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
