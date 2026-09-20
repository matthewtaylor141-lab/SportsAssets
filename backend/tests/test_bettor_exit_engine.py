"""The exit engine and the hedge tax: priced, and deliberately unranked."""

from decimal import Decimal

import pytest

from sportsassets import bettor_exit_engine as ee
from sportsassets import bettor_hedge_tax as ht
from sportsassets import bettor_inventory as binv

CONFIRMED = "EXACT_ONE_TO_COMPLEMENT_BASKET"


def _holding_yes(qty="100", px="0.48"):
    return binv.inventory([{"leg": "YES", "qty": qty, "price": px}],
                          identity_status=CONFIRMED)


def _row(result, action):
    return {a["action"]: a for a in result["actions"]}[action]


# ── §11: the hedge tax, frozen before use ────────────────────────────

def test_the_definition_is_frozen_and_hashed():
    assert ht.DEFINITION_SHA
    assert ht.UNITS == "USD_PER_CONTRACT"
    assert ht.PAR == Decimal("1.00")
    assert ht.hedge_tax("0.48", "0.49")["definitionSha"] == ht.DEFINITION_SHA


def test_a_pair_above_par_is_a_tax_and_below_par_is_a_credit():
    above = ht.hedge_tax("0.55", "0.50")     # basis 1.05
    below = ht.hedge_tax("0.48", "0.45")     # basis 0.93
    assert Decimal(above["HEDGE_TAX_VS_PAR"]) == Decimal("0.05")
    assert above["isATax"] is True and above["isACredit"] is False
    assert Decimal(below["HEDGE_TAX_VS_PAR"]) == Decimal("-0.07")
    assert below["isACredit"] is True


def test_the_decision_relevant_tax_is_not_identified():
    """vs-par says what the pair costs; vs-hold says whether it is worth it."""
    t = ht.hedge_tax("0.48", "0.49")
    assert Decimal(t["PAIR_BASIS"]) == Decimal("0.97")
    assert Decimal(t["HEDGE_TAX_VS_PAR"]) == Decimal("-0.03")
    assert t["HEDGE_TAX_VS_HOLD"] == ht.NOT_IDENTIFIED
    assert "FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED" in \
        t["whyVsHoldNotIdentified"]


def test_the_tax_is_gross_and_says_so():
    t = ht.hedge_tax("0.48", "0.49")
    assert set(t["grossOf"]) == {"FEES", "INCENTIVES", "REBATES", "SLIPPAGE"}
    assert t["isNot"].startswith("an established net outcome")


def test_neither_input_is_assumed():
    assert ht.hedge_tax(None, "0.49")["HEDGE_TAX_VS_PAR"] == ht.NOT_IDENTIFIED
    assert ht.hedge_tax("0.48", None)["HEDGE_TAX_VS_PAR"] == ht.NOT_IDENTIFIED


def test_a_complement_existing_is_not_a_reason_to_buy_it():
    assert "not a reason to buy it" in ht.DO_NOT_AUTO_HEDGE


# ── §7: every action compared, none ranked ───────────────────────────

def test_every_residual_action_is_evaluated():
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"},
                    complement_book={"ask": "0.49"})
    assert [a["action"] for a in r["actions"]] == list(ee.RESIDUAL_ACTIONS)


def test_nothing_is_ranked_and_the_reason_is_named():
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"},
                    complement_book={"ask": "0.49"})
    assert r["bestAction"] == ee.NOT_IDENTIFIED
    assert all(a["EV_VS_HOLD"] == ee.NOT_IDENTIFIED for a in r["actions"])
    assert "FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED" in r["whyNoBestAction"]


def test_the_cheapest_action_is_not_declared_the_best():
    """HOLD costs zero and must not win by costing zero."""
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"},
                    complement_book={"ask": "0.49"})
    hold = _row(r, "HOLD")
    assert hold["EXECUTION_COST"] == "0"
    assert hold["EV_VS_HOLD"] == ee.NOT_IDENTIFIED
    assert hold["isTheReference"] is True
    assert "systematically prefer inaction" in r["cheapestIsNotBest"]


def test_there_is_no_sixty_second_rule():
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"})
    assert "not a strategy" in r["notAFixedHorizon"]
    assert "60-second" in r["notAFixedHorizon"]


# ── §10: the complement is an exit, and a distinct one ───────────────

def test_direct_exit_and_take_complement_are_different_actions():
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"},
                    complement_book={"ask": "0.49"})
    assert _row(r, "DIRECT_EXIT")["SELL_PRICE"] == "0.45"
    assert _row(r, "TAKE_COMPLEMENT")["BUY_PRICE"] == "0.49"
    assert _row(r, "DIRECT_EXIT")["sellsInto"] != \
        _row(r, "TAKE_COMPLEMENT")["buysFrom"]


def test_the_two_exits_are_shown_side_by_side_but_not_ranked():
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"},
                    complement_book={"ask": "0.49"})
    c = r["exitComparison"]
    assert c["DIRECT_EXIT_SELL_PRICE"] == "0.45"
    assert c["TAKE_COMPLEMENT_BUY_PRICE"] == "0.49"
    assert c["whichIsCheaper"] == ee.NOT_IDENTIFIED
    # They leave different things behind, so price alone cannot decide.
    assert "not the same object" in c["whyNotAnswered"]


def test_owning_yes_does_not_mean_exiting_is_selling_yes():
    assert "Buying NO removes the same exposure" in ee.COMPLEMENT_IS_AN_EXIT


def test_the_realised_loss_against_basis_is_identified():
    r = ee.evaluate(_holding_yes("100", "0.48"), held_book={"bid": "0.45"})
    d = _row(r, "DIRECT_EXIT")
    assert Decimal(d["PROCEEDS"]) == Decimal("45.00")
    assert Decimal(d["REALISED_VS_BASIS"]) == Decimal("-3.00")


def test_completing_carries_the_hedge_tax():
    r = ee.evaluate(_holding_yes("100", "0.48"),
                    complement_book={"ask": "0.49"})
    t = _row(r, "TAKE_COMPLEMENT")
    assert Decimal(t["PAIR_BASIS"]) == Decimal("0.97")
    assert Decimal(t["HEDGE_TAX_VS_PAR"]) == Decimal("-0.03")
    assert t["HEDGE_TAX_VS_HOLD"] == ee.NOT_IDENTIFIED


# ── blocked actions name their blocker ───────────────────────────────

@pytest.mark.parametrize("action,marker", [
    ("MERGE", "MERGE_MECHANISM"),
    ("HOLD_TO_SETTLEMENT", "SETTLEMENT_SEMANTICS"),
    ("POST_COMPLEMENT", "P_FILL"),
])
def test_blocked_actions_name_what_blocks_them(action, marker):
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"},
                    complement_book={"ask": "0.49"})
    assert _row(r, action)[marker] == ee.NOT_IDENTIFIED or \
        _row(r, action)[marker] == "CONFLICTING_VENUE_PROSE"


# ── §9: the distribution fields are declared, not faked ──────────────

def test_the_conservative_fields_are_declared_and_unidentified():
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"})
    for a in r["actions"]:
        for f in ee.CONSERVATIVE_FIELDS:
            assert a[f] == ee.NOT_IDENTIFIED
    assert "intolerable tail" in r["whyADistribution"]


def test_an_exit_comparison_needs_exactly_one_leg_held():
    flat = binv.inventory([], identity_status=CONFIRMED)
    assert ee.evaluate(flat)["status"] == ee.NOT_IDENTIFIED
    both = binv.inventory(
        [{"leg": "YES", "qty": "100", "price": "0.48"},
         {"leg": "NO", "qty": "100", "price": "0.49"}],
        identity_status=CONFIRMED)
    assert ee.evaluate(both)["status"] == ee.NOT_IDENTIFIED


def test_the_actions_come_from_the_canonical_table():
    from sportsassets import bettor_ev_actions as acts
    for a in ee.RESIDUAL_ACTIONS:
        assert a in acts.CANONICAL_ACTIONS, a


# ── §14 in the exit engine ───────────────────────────────────────────

def test_a_resting_complement_that_does_not_fill_leaves_the_leg_open():
    """POST_COMPLEMENT is a passive EXIT: no fill means full exposure."""
    from sportsassets import bettor_ev_bridge as evb
    r = ee.evaluate(_holding_yes(), held_book={"bid": "0.45"},
                    complement_book={"ask": "0.49"})
    p = _row(r, "POST_COMPLEMENT")
    assert p["EV_IF_NO_FILL"] == ee.NOT_IDENTIFIED
    assert p["residualExposure"] == "UNCHANGED"
    assert p["context"] == evb.PASSIVE_EXIT
    assert p["fillOutcomes"] == ["FULL_FILL", "PARTIAL_FILL", "NO_FILL"]
