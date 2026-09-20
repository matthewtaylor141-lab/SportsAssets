"""Which actions can exist, before any of them is priced."""

import pytest

from sportsassets import bettor_applicability as ap
from sportsassets import bettor_inventory as binv

CONFIRMED = "EXACT_ONE_TO_COMPLEMENT_BASKET"


def _inv(rows, status=CONFIRMED):
    return binv.inventory(rows, identity_status=status)


FLAT_ROWS = []
YES_ROWS = [{"leg": "YES", "qty": "100", "price": "0.48"}]
NO_ROWS = [{"leg": "NO", "qty": "100", "price": "0.49"}]
PARTIAL_ROWS = YES_ROWS + [{"leg": "NO", "qty": "60", "price": "0.49"}]
FULL_ROWS = YES_ROWS + [{"leg": "NO", "qty": "100", "price": "0.49"}]


# ── §4: the states ───────────────────────────────────────────────────

@pytest.mark.parametrize("rows,expected", [
    (FLAT_ROWS, ap.FLAT),
    (YES_ROWS, ap.YES_RESIDUAL),
    (NO_ROWS, ap.NO_RESIDUAL),
    (PARTIAL_ROWS, ap.PARTIALLY_MATCHED_WITH_YES_RESIDUAL),
    (FULL_ROWS, ap.FULLY_MATCHED_PAIR),
])
def test_the_state_is_derived_from_per_leg_inventory(rows, expected):
    assert ap.inventory_state(_inv(rows))["state"] == expected


def test_an_unknown_state_is_never_forced_into_flat():
    """§4, and it is the difference between owning nothing and not
    knowing what we own."""
    assert ap.inventory_state(None)["state"] == ap.STATE_NOT_IDENTIFIED
    unknown = _inv(FLAT_ROWS)
    unknown["YES_QTY"] = ap.NOT_IDENTIFIED
    assert ap.inventory_state(unknown)["state"] == ap.STATE_NOT_IDENTIFIED
    assert "never forced into FLAT" in ap.UNKNOWN_IS_NOT_FLAT


def test_an_unconfirmed_complement_is_both_legs_unmatched():
    s = ap.inventory_state(_inv(
        PARTIAL_ROWS,
        status="STRUCTURALLY_IDENTIFIED_COMPLEMENT_PENDING_"
               "INSTITUTIONAL_CONFIRMATION"))
    assert s["state"] == ap.BOTH_LEGS_UNMATCHED


def test_open_quotes_without_a_position_are_their_own_state():
    s = ap.inventory_state(_inv(FLAT_ROWS), open_passive_yes_qty="50")
    assert s["state"] == ap.OPEN_MAKER_QUOTES_NO_POSITION


# ── §5/§18: the FLAT proof ───────────────────────────────────────────

def test_flat_applicable_and_not_applicable_sets():
    s = ap.applicable_set(ap.FLAT)
    assert set(s["APPLICABLE"]) == {
        "MAKE_YES", "MAKE_NO", "MAKE_BOTH", "TAKE_YES", "TAKE_NO",
        "NO_TRADE"}
    assert set(s["NOT_APPLICABLE_CURRENT_STATE"]) == {
        "POST_COMPLEMENT", "TAKE_COMPLEMENT", "COMPLETE_PAIR", "MERGE",
        "HOLD", "DIRECT_EXIT", "HEDGE", "HOLD_TO_SETTLEMENT",
        "WAIT_REQUOTE"}


@pytest.mark.parametrize("action", [
    "HOLD", "DIRECT_EXIT", "COMPLETE_PAIR", "MERGE", "HEDGE",
    "POST_COMPLEMENT", "TAKE_COMPLEMENT", "HOLD_TO_SETTLEMENT"])
def test_inventory_actions_do_not_exist_while_flat(action):
    r = ap.applicability(action, ap.FLAT)
    assert r["APPLICABILITY_STATUS"] == ap.NOT_APPLICABLE
    # §9: not evaluated, which is not the same as not identified.
    assert r["ECONOMIC_STATUS"] == ap.NOT_EVALUATED
    assert r["RISK_STATUS"] == ap.NOT_EVALUATED
    assert r["APPLICABILITY_REASON"]


def test_an_inapplicable_action_carries_no_number():
    """A zero beats every negative, so it must not be offered."""
    r = ap.applicability("HOLD", ap.FLAT)
    assert "EV" not in r
    assert "beats every negative" in r["whyNotZero"]


# ── §6/§7/§8/§12/§13: the state transitions ──────────────────────────

def test_a_residual_leg_makes_inventory_actions_real():
    s = ap.applicable_set(ap.YES_RESIDUAL)
    for action in ("HOLD", "POST_COMPLEMENT", "TAKE_COMPLEMENT",
                   "DIRECT_EXIT", "HEDGE", "HOLD_TO_SETTLEMENT",
                   "COMPLETE_PAIR"):
        assert action in s["APPLICABLE"], action


def test_merge_needs_matched_quantity_not_just_a_mechanism():
    """§13: two independent requirements, and this is the first."""
    assert "MERGE" in ap.applicable_set(ap.YES_RESIDUAL)[
        "NOT_APPLICABLE_CURRENT_STATE"]
    assert "MERGE" in ap.applicable_set(
        ap.PARTIALLY_MATCHED_WITH_YES_RESIDUAL)["APPLICABLE"]
    assert "matched quantity" in ap.REQUIRES_STATE["MERGE"]


def test_a_partially_matched_book_carries_both_books_at_once():
    """§7, the Ferrari lesson: matched must never erase residual."""
    s = ap.applicable_set(ap.PARTIALLY_MATCHED_WITH_YES_RESIDUAL)
    assert "MERGE" in s["APPLICABLE"]               # the matched book
    assert "DIRECT_EXIT" in s["APPLICABLE"]         # the residual book
    assert "TAKE_COMPLEMENT" in s["APPLICABLE"]


def test_a_complete_pair_cannot_be_completed_again():
    s = ap.applicable_set(ap.FULLY_MATCHED_PAIR)
    assert "COMPLETE_PAIR" in s["NOT_APPLICABLE_CURRENT_STATE"]
    # §8: exposure is not created merely to give the engine actions.
    for action in ("POST_COMPLEMENT", "TAKE_COMPLEMENT", "HEDGE"):
        assert action in s["NOT_APPLICABLE_CURRENT_STATE"], action
    assert "MERGE" in s["APPLICABLE"]


# ── §11: WAIT_REQUOTE needs something to wait on ─────────────────────

def test_wait_requote_needs_an_open_quote():
    assert "WAIT_REQUOTE" in ap.applicable_set(ap.FLAT)[
        "NOT_APPLICABLE_CURRENT_STATE"]
    assert "WAIT_REQUOTE" in ap.applicable_set(
        ap.OPEN_MAKER_QUOTES_NO_POSITION)["APPLICABLE"]


# ── §10: NO_TRADE and HOLD are not synonyms ──────────────────────────

def test_no_trade_and_hold_are_scoped_differently():
    assert ap.DECISION_SCOPE["NO_TRADE"] == ap.ENTRY
    assert ap.DECISION_SCOPE["HOLD"] == ap.INVENTORY
    # Flat: NO_TRADE is the real answer, HOLD does not exist.
    flat = ap.applicable_set(ap.FLAT)
    assert "NO_TRADE" in flat["APPLICABLE"]
    assert "HOLD" in flat["NOT_APPLICABLE_CURRENT_STATE"]
    assert "by construction" in ap.SCOPE_RULE


def test_an_unidentified_state_decides_nothing_either_way():
    for action in ("MAKE_YES", "HOLD", "MERGE"):
        r = ap.applicability(action, ap.STATE_NOT_IDENTIFIED)
        assert r["APPLICABILITY_STATUS"] == ap.APPLICABILITY_NOT_IDENTIFIED


def test_one_table_serves_every_engine():
    assert "offered by one engine and refused by another" in \
        ap.describe()["oneTableNotOnePerEngine"]
