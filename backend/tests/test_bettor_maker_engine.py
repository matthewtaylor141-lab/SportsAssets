"""§6. Maker-first is not maker-always, and MAKE_BOTH is not two MAKEs."""

import pytest

from sportsassets import bettor_applicability as applic
from sportsassets import bettor_maker_engine as me


ALL_IDENTIFIED = dict(
    gross_spread_capture="0.010", partial_fill_distribution="0.900",
    value_if_fill="0.020", value_if_no_fill="-0.001",
    adverse_selection="-0.004", fee="-0.002", toxicity="-0.001",
    residual_inventory_cost="-0.003", capital_hours="-0.002")


def _flat(**over):
    return me.evaluate_entry(inventory_state="FLAT", **over)


def _row(result, action):
    return next(r for r in result["actions"] if r["action"] == action)


# ── §6: the six actions, evaluated from FLAT ─────────────────────────

def test_the_six_entry_actions_are_the_directives_six():
    assert me.ENTRY_ACTIONS == ("MAKE_YES", "MAKE_NO", "MAKE_BOTH",
                                "TAKE_YES", "TAKE_NO", "NO_TRADE")


def test_every_entry_action_is_evaluated_from_flat():
    r = _flat()
    assert [a["action"] for a in r["actions"]] == list(me.ENTRY_ACTIONS)
    for a in r["actions"]:
        assert a["APPLICABILITY_STATUS"] == applic.APPLICABLE


def test_entry_actions_are_not_applicable_when_inventory_exists():
    """Applicability runs first: an entry action from a non-flat state
    carries no number at all, not a zero."""
    r = me.evaluate_entry(inventory_state="LONG_YES_UNPAIRED")
    for a in r["actions"]:
        if a["action"] == "NO_TRADE":
            continue
        assert a["APPLICABILITY_STATUS"] != applic.APPLICABLE
        assert a["expectedNetDollarsPerContract"] == me.NOT_IDENTIFIED
        assert a["ECONOMIC_STATUS"] == applic.NOT_EVALUATED


# ── §6: the thirteen components, computed separately ─────────────────

def test_all_thirteen_components_are_named():
    assert len(me.COMPONENTS) == 13
    for c in ("GROSS_SPREAD_CAPTURE", "P_FILL", "PARTIAL_FILL_DISTRIBUTION",
              "VALUE_IF_FILL", "VALUE_IF_NO_FILL", "FILL_SELECTION_EFFECT",
              "ADVERSE_SELECTION", "REBATE", "INCENTIVE", "FEE",
              "TOXICITY", "RESIDUAL_INVENTORY_COST", "CAPITAL_HOURS"):
        assert c in me.COMPONENTS


@pytest.mark.parametrize("action", ["MAKE_YES", "MAKE_BOTH", "TAKE_YES"])
def test_every_component_appears_whether_or_not_it_is_identified(action):
    """A component that vanishes when missing is one nobody notices."""
    t = me.components(action)
    assert [c["component"] for c in t["components"]] == list(me.COMPONENTS)


def test_a_missing_component_says_why_and_names_its_source():
    t = me.components("MAKE_YES")
    by = {c["component"]: c for c in t["components"]}
    assert by["P_FILL"]["status"] == me.NOT_IDENTIFIED
    assert by["P_FILL"]["source"] == "BETTOR_NATIVE_FILLS"
    assert "BETTOR-native admitted executions" in by["P_FILL"]["why"]
    assert by["RESIDUAL_INVENTORY_COST"]["source"] == "EXIT_ENGINE"


def test_the_frozen_prior_is_the_one_component_identified_today():
    by = {c["component"]: c for c in me.components("MAKE_YES")["components"]}
    assert by["FILL_SELECTION_EFFECT"]["status"] == "IDENTIFIED"
    assert by["FILL_SELECTION_EFFECT"]["source"] == "FROZEN_PRIOR_REGISTRY"


def test_the_prior_band_travels_with_its_width_and_no_direction():
    b = me.components("MAKE_YES")["fillSelectionEffectBand"]
    assert b["P10"] is not None and b["P90"] is not None
    assert b["P10"] != b["P90"]
    assert b["directionAssumed"] == "NO"


# ── unidentified never becomes zero ──────────────────────────────────

def test_an_unidentified_component_blocks_the_total():
    r = _row(_flat(), "MAKE_YES")
    assert r["status"] == me.NOT_IDENTIFIED
    assert r["expectedNetDollarsPerContract"] == me.NOT_IDENTIFIED
    assert "P_FILL" in r["componentsMissing"]
    assert "not 0.004" in r["whyNotZero"]


def test_no_missing_component_is_written_as_zero():
    for c in me.components("MAKE_YES")["components"]:
        if c["status"] not in ("IDENTIFIED", me.ABSENT):
            assert c["value"] == me.NOT_IDENTIFIED
            assert c["value"] != "0"


def test_an_absent_optional_component_is_absent_not_zero():
    by = {c["component"]: c for c in me.components("MAKE_YES")["components"]}
    for name in me.OPTIONAL_COMPONENTS:
        assert by[name]["status"] == me.ABSENT
        assert by[name]["value"] == me.ABSENT
        assert by[name]["value"] != "0"


def test_an_absent_optional_component_does_not_block_a_total():
    r = _row(_flat(**ALL_IDENTIFIED), "TAKE_YES")
    assert r["status"] == "IDENTIFIED"
    for name in me.OPTIONAL_COMPONENTS:
        assert name not in r["componentsMissing"]


def test_a_complete_table_produces_a_total_from_its_own_rows():
    r = _row(_flat(**ALL_IDENTIFIED), "TAKE_YES")
    assert r["status"] == "IDENTIFIED"
    # 0.010 + 0.020 + 0.0 - 0.004 - 0.002 - 0.001 - 0.003 - 0.002
    assert r["expectedNetDollarsPerContract"] == "0.018"
    assert "FILL_SELECTION_EFFECT" in r["componentsUsed"]
    assert "REBATE" not in r["componentsUsed"]


# ── a probability is not a dollar ────────────────────────────────────

def test_probabilities_are_never_summed_into_a_dollar_total():
    """THE CORRECTED UNITS BUG. An earlier version summed every
    identified row and reported 0.917 per contract for a table whose
    largest entry was a 0.900 PROBABILITY."""
    r = _row(_flat(**ALL_IDENTIFIED), "TAKE_YES")
    assert set(r["componentsNotSummed"]) == {"P_FILL",
                                             "PARTIAL_FILL_DISTRIBUTION"}
    assert r["expectedNetDollarsPerContract"] != "0.917"
    assert "0.900 PROBABILITY" in r["aProbabilityIsNotADollar"]


def test_every_component_declares_its_kind():
    assert set(me.COMPONENT_KIND) == set(me.COMPONENTS)
    assert me.COMPONENT_KIND["P_FILL"] == me.PROBABILITY
    assert me.COMPONENT_KIND["FEE"] == me.DOLLARS_IF_FILL
    assert me.COMPONENT_KIND["CAPITAL_HOURS"] == me.DOLLARS_UNCONDITIONAL
    for c in me.components("MAKE_YES")["components"]:
        assert c["kind"] == me.COMPONENT_KIND[c["component"]]


def test_the_passive_combination_weights_by_p_fill():
    """The passive path is unreachable through the engine today because
    P_FILL is NOT_IDENTIFIED and cannot be supplied by a caller. The
    arithmetic is still pinned, on a table built by hand."""
    table = {
        "aggression": "PASSIVE",
        "componentsMissing": [],
        "components": [
            {"component": "P_FILL", "value": "0.25",
             "kind": me.PROBABILITY, "status": "IDENTIFIED"},
            {"component": "PARTIAL_FILL_DISTRIBUTION", "value": "0.9",
             "kind": me.DISTRIBUTION, "status": "IDENTIFIED"},
            {"component": "VALUE_IF_FILL", "value": "0.040",
             "kind": me.DOLLARS_IF_FILL, "status": "IDENTIFIED"},
            {"component": "FEE", "value": "-0.010",
             "kind": me.DOLLARS_IF_FILL, "status": "IDENTIFIED"},
            {"component": "VALUE_IF_NO_FILL", "value": "-0.002",
             "kind": me.DOLLARS_IF_NO_FILL, "status": "IDENTIFIED"},
            {"component": "CAPITAL_HOURS", "value": "-0.001",
             "kind": me.DOLLARS_UNCONDITIONAL, "status": "IDENTIFIED"},
        ],
    }
    from decimal import Decimal
    c = me.combine(table)
    # 0.25 * (0.040 - 0.010) + 0.75 * (-0.002) - 0.001
    assert Decimal(c["expectedNetDollarsPerContract"]) == Decimal("0.005")
    assert "1 - P_FILL" in c["combinationRule"]


def test_a_crossing_order_has_no_no_fill_branch_to_weight():
    assert "fills by construction" in me.COMBINATION_RULE["AGGRESSIVE"]
    assert "P_FILL" not in me.REQUIRED_FOR_AGGRESSIVE


def test_the_combination_is_declared_as_a_modelling_choice():
    r = _row(_flat(**ALL_IDENTIFIED), "TAKE_YES")
    assert "declared, not measured" in r["isAModellingChoice"]


def test_partial_fill_distribution_enters_at_sizing_not_per_contract():
    r = _row(_flat(**ALL_IDENTIFIED), "TAKE_YES")
    assert "expected filled QUANTITY" in \
        r["partialFillDistributionEntersAtSizing"]
    assert "PARTIAL_FILL_DISTRIBUTION" in r["componentsNotSummed"]


def test_a_taker_needs_fewer_components_than_a_maker():
    """A taker crosses, so it does not wait on P_FILL and does not
    create a residual on a single leg."""
    assert "P_FILL" not in me.REQUIRED_FOR_AGGRESSIVE
    assert "P_FILL" in me.REQUIRED_FOR_PASSIVE
    assert "RESIDUAL_INVENTORY_COST" not in me.REQUIRED_FOR_AGGRESSIVE


# ── MAKE_BOTH is not two MAKEs ───────────────────────────────────────

def test_make_both_needs_a_joint_fill_model():
    t = me.components("MAKE_BOTH")
    assert t["JOINT_FILL_MODEL"] == me.NOT_IDENTIFIED
    assert "JOINT_FILL_MODEL" in t["componentsMissing"]
    assert "the same flow that fills one leg" in t["jointFillModelRequired"]


def test_make_both_is_not_priced_even_with_every_component_identified():
    """THE FERRARI GUARD. A complete single-leg table does not unlock
    MAKE_BOTH: the joint distribution is a separate object."""
    r = _row(_flat(**ALL_IDENTIFIED), "MAKE_BOTH")
    assert r["status"] == me.NOT_IDENTIFIED
    assert "JOINT_FILL_MODEL" in r["componentsMissing"]


def test_the_additive_composition_is_refused_by_name():
    a = me.additive_composition_refused()
    assert a["refused"] == "EV(MAKE_BOTH) = EV(MAKE_YES) + EV(MAKE_NO)"
    assert "RESIDUAL" in a["outcomes"]["ONE_FILLS"]
    assert "Ferrari mistake" in a["why"]


def test_make_both_carries_one_blocker_the_single_legs_do_not():
    r = _flat(**ALL_IDENTIFIED)
    yes = _row(r, "MAKE_YES")["componentsMissing"]
    both = _row(r, "MAKE_BOTH")["componentsMissing"]
    assert "JOINT_FILL_MODEL" not in yes
    assert "JOINT_FILL_MODEL" in both
    # Satisfying every single-leg component would still leave MAKE_BOTH
    # short by exactly the joint model.
    assert set(both) - set(yes) == {"JOINT_FILL_MODEL"}


# ── maker-first is not maker-always ──────────────────────────────────

def test_no_maker_action_receives_a_bonus():
    assert "Nothing gives a maker action a bonus" in \
        me.MAKER_FIRST_IS_NOT_MAKER_ALWAYS
    r = _flat(**ALL_IDENTIFIED)
    maker = _row(r, "MAKE_YES")
    taker = _row(r, "TAKE_YES")
    # Same footing: each priced from its own table. The maker is the one
    # that fails to price today, which is the opposite of a bonus.
    assert taker["status"] == "IDENTIFIED"
    assert maker["status"] == me.NOT_IDENTIFIED
    assert set(me.REQUIRED_FOR_AGGRESSIVE) < set(me.REQUIRED_FOR_PASSIVE)


# ── NO_TRADE is a real outcome and is not HOLD ───────────────────────

def test_no_trade_is_priced_at_exactly_zero_and_is_not_hold():
    r = _row(_flat(), "NO_TRADE")
    assert r["status"] == "IDENTIFIED"
    assert r["expectedNetDollarsPerContract"] == "0"
    assert "never the same row" in r["isNotHold"]


def test_no_trade_is_the_only_thing_priced_today():
    r = _flat()
    assert r["PRICED"] == 1
    assert _row(r, "NO_TRADE")["status"] == "IDENTIFIED"


def test_p_fill_is_the_binding_component_today():
    d = me.describe()
    assert d["bindingComponentToday"] == "P_FILL"
    assert "correct output" in d["whyNothingPricesToday"]


def test_an_evaluation_creates_no_inventory():
    assert "not an order and not a position" in _flat()["createsNoInventory"]


def test_an_unknown_action_is_refused_rather_than_priced():
    t = me.components("MAKE_MAYBE")
    assert t["status"] == "UNKNOWN_ACTION"
