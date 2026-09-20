"""The EV bridge: what it refuses to invent, and what it can still prove.

The tests that matter here are the NEGATIVE ones. An EV engine that
produces numbers is easy; one that declines to produce them when the
inputs are absent is the whole requirement, and every regression this
file is meant to catch takes the form of a number appearing where
NOT_IDENTIFIED belongs.
"""

from decimal import Decimal

import pytest

from sportsassets import bettor_ev_actions as acts
from sportsassets import bettor_ev_bridge as evb
from sportsassets import shadow_bettor as sb

BOOK = {"bid": "0.48", "ask": "0.52", "mid": "0.50",
        "availableDepth": "250", "readable": True}


def _row(result, action):
    return {r["action"]: r for r in result["table"]}[action]


# ── the machinery is the tested one, not a copy ──────────────────────

def test_the_engine_is_imported_not_reimplemented():
    """§0: do not create duplicate engines. The bridge must LOAD them."""
    assert evb.available(), "research EV machinery did not load"
    prov = evb.evaluate(BOOK)["engineProvenance"]
    assert prov["loadedFrom"] == "research/beta48/shadow"
    assert "action_ev" in prov["modules"]


def test_missing_machinery_refuses_rather_than_improvises(monkeypatch):
    def boom(*a, **k):
        raise evb.MachineryUnavailable("simulated absence")

    monkeypatch.setattr(evb, "machinery", boom)
    out = evb.evaluate(BOOK)
    assert out["actionEvStatus"] == evb.MACHINERY_UNAVAILABLE
    assert out["bestAction"] == "NO_TRADE"
    assert out["table"] == []
    # The distinction that matters: this NO_TRADE is a refusal to act
    # without the engine, NOT the outcome of a comparison.
    assert "not a comparison" in out["why"]


# ── unknown never becomes zero (§1) ──────────────────────────────────

def test_an_absent_fee_is_not_a_zero_fee():
    """The regression that would silently destroy every break-even.

    With fee defaulted to zero, BREAK_EVEN_P_FILL comes out 0.000000 --
    "any fill rate whatsoever makes this pay" -- which is a confident
    claim manufactured entirely out of a term nobody measured.
    """
    r = _row(evb.evaluate(BOOK), "MAKE_YES")
    assert r["feeStatus"] == evb.NOT_IDENTIFIED
    assert r["BREAK_EVEN_P_FILL_LOWER_BOUND"] == evb.NOT_IDENTIFIED
    assert r["BREAK_EVEN_P_FILL_LOWER_BOUND"] != "0.000000"


def test_passive_ev_is_not_identified_because_p_fill_never_was():
    r = _row(evb.evaluate(BOOK), "MAKE_YES")
    assert r["expectedNetDollarsPerContract"] == evb.NOT_IDENTIFIED
    assert "P_FILL" in r["missingCriticalTerms"]
    assert "never rested an order" in r["whyNot"]


def test_an_unknown_size_does_not_become_one():
    """Order-level dollars go unidentified; per-contract survives."""
    r = _row(evb.evaluate(BOOK, fee="0.001"), "TAKE_YES")
    assert r["expectedNetDollars"] == evb.NOT_IDENTIFIED
    assert r["expectedNetDollarsPerContract"] == "-0.021000"

    sized = _row(evb.evaluate(BOOK, size="100", fee="0.001"), "TAKE_YES")
    assert sized["expectedNetDollars"] == "-2.100000"


def test_per_contract_and_order_level_are_not_the_same_field():
    """A units error here reads a per-contract figure as order dollars."""
    r = _row(evb.evaluate(BOOK, size="100", fee="0.001"), "TAKE_YES")
    assert Decimal(r["expectedNetDollars"]) == (
        Decimal(r["expectedNetDollarsPerContract"]) * 100)


# ── what can be proved with no priors at all ─────────────────────────

def test_crossing_is_refuted_without_any_fee_schedule():
    """The headline result: it needs no prior and no fee.

    The champion fair value is the venue's own price, so crossing pays
    the far side and receives nothing. A fee cannot be negative, so no
    fee schedule could rescue it.
    """
    out = evb.evaluate(BOOK)
    for action in ("TAKE_YES", "TAKE_NO", "DIRECT_EXIT"):
        r = _row(out, action)
        assert r["status"] == evb.REFUTED_BEFORE_COSTS, action
        assert Decimal(r["evUpperBoundPerContract"]) < 0
        assert "cannot be negative" in r["evUpperBoundBasis"]


def test_the_two_refutations_are_not_the_same_word():
    """Passive dies for want of a fill rate; aggressive has none."""
    assert evb.REFUTED != evb.REFUTED_BEFORE_COSTS
    expensive = evb.evaluate(BOOK, fee="0.05")
    assert _row(expensive, "MAKE_YES")["status"] == evb.REFUTED
    assert _row(evb.evaluate(BOOK), "TAKE_YES")["status"] == \
        evb.REFUTED_BEFORE_COSTS


def test_the_break_even_bound_is_a_bound_not_an_estimate():
    r = _row(evb.evaluate(BOOK, fee="0.001"), "MAKE_YES")
    assert r["BREAK_EVEN_P_FILL_LOWER_BOUND"] == "0.050000"
    assert "not a forecast of P_FILL" in r["isNotAnEstimateOfPFill"]


def test_a_bound_above_one_refutes_with_no_prior():
    r = _row(evb.evaluate(BOOK, fee="0.05"), "MAKE_YES")
    assert Decimal(r["BREAK_EVEN_P_FILL_LOWER_BOUND"]) > 1
    assert r["status"] == evb.REFUTED


# ── actions that name their missing precondition ─────────────────────

@pytest.mark.parametrize("action,precondition", [
    ("MERGE", "MERGE_MECHANISM_CONFIRMED"),
    ("HEDGE", "HEDGE_TAX"),
    ("HOLD_TO_SETTLEMENT", "SETTLEMENT_SEMANTICS_RESOLVED"),
    ("MAKE_BOTH", "JOINT_FILL_MODEL"),
])
def test_unmet_preconditions_are_named_not_swallowed(action, precondition):
    r = _row(evb.evaluate(BOOK, fee="0.001"), action)
    assert r["status"] == evb.NOT_IDENTIFIED
    assert precondition in r["unmetPreconditions"]


def test_make_both_is_not_the_sum_of_its_legs():
    """§6, the Ferrari failure: filling one leg leaves a residual.

    MAKE_BOTH must not inherit MAKE_YES's single-leg break-even, which
    would price a joint fill as though the legs were independent.
    """
    out = evb.evaluate(BOOK, fee="0.001")
    assert _row(out, "MAKE_YES")["BREAK_EVEN_P_FILL_LOWER_BOUND"] == "0.050000"
    assert "BREAK_EVEN_P_FILL_LOWER_BOUND" not in _row(out, "MAKE_BOTH")


def test_an_unreadable_book_prices_nothing():
    out = evb.evaluate({"readable": False})
    assert out["actionEvStatus"] == evb.NOT_IDENTIFIED
    assert out["bestAction"] == "NO_TRADE"
    assert all(r.get("expectedNetDollarsPerContract",
                     evb.NOT_IDENTIFIED) == evb.NOT_IDENTIFIED
               for r in out["table"] if r["aggression"] != acts.NON_ORDER)


# ── the canonical action table (§1, §23) ─────────────────────────────

def test_every_canonical_action_appears_exactly_once():
    out = evb.evaluate(BOOK)
    seen = [r["action"] for r in out["table"]]
    assert seen == list(acts.ACTIONS)
    assert len(set(seen)) == len(seen)


def test_the_directive_action_set_is_the_one_implemented():
    for a in ("MAKE_YES", "MAKE_NO", "MAKE_BOTH", "TAKE_YES", "TAKE_NO",
              "POST_COMPLEMENT", "TAKE_COMPLEMENT", "COMPLETE_PAIR",
              "MERGE", "HOLD", "WAIT_REQUOTE", "DIRECT_EXIT", "HEDGE",
              "HOLD_TO_SETTLEMENT", "NO_TRADE"):
        assert a in acts.CANONICAL_ACTIONS, a


def test_every_action_names_the_leg_it_touches():
    """§7: per-leg, never net."""
    legs = {acts.LEG_YES, acts.LEG_NO, acts.LEG_BOTH, acts.LEG_NONE,
            acts.LEG_HELD}
    for a in acts.ACTIONS:
        assert acts.leg_of(a) in legs, a


def test_vocabulary_gaps_are_recorded_not_aliased():
    """An action no research module prices must SAY so."""
    cov = acts.coverage()
    assert "MERGE" in cov["evCore"]["notRepresented"]
    assert acts.research_action("MERGE", "evCore") == acts.NOT_REPRESENTED
    # ... and it is not quietly aliased onto PAIR, which would make an
    # unevaluated action look evaluated-and-rejected in the audit.
    assert acts.research_action("MERGE", "actionEv") != "PAIR"


# ── the lane itself ──────────────────────────────────────────────────

def _opportunity():
    return sb.opportunity_record(
        symbol="TEST-YES", outcome_leg="YES",
        evidence_source="INSTITUTIONAL_INSTRUMENTS")


def test_the_lane_still_refuses_to_trade():
    """The gate does not move. Nothing here produces a BUY or a SELL."""
    d = sb.decide(_opportunity(), BOOK)
    assert d["actionEvComponents"]["bestAction"] == "NO_TRADE"
    assert d["actionEvComponents"]["positiveExposureIncreasingActions"] == []
    assert d.get("action") in (None, "NO_TRADE")


def test_no_trade_is_now_a_conclusion_rather_than_an_assertion():
    d = sb.decide(_opportunity(), BOOK)
    assert len(d["alternatives"]) == len(acts.ACTIONS)
    # The old stub said the same sentence about all three alternatives.
    whys = {a["why"] for a in d["alternatives"]}
    assert len(whys) > 1, "every alternative still carries one stock reason"
    assert d["actionEvStatus"] == "PARTIALLY_IDENTIFIED"


def test_the_lane_does_not_invent_an_order_size():
    """availableDepth is the book's depth, never BETTOR's intended size."""
    d = sb.decide(_opportunity(), BOOK)
    for a in d["alternatives"]:
        assert a["expectedNetDollarsPerContract"] is not None
    # Order-level dollars stay unidentified because no sizing policy
    # exists; a number here means the book's depth was read as a size.
    assert all(r.get("expectedNetDollars", evb.NOT_IDENTIFIED)
               == evb.NOT_IDENTIFIED
               for r in d["actionEvComponents"]["table"]
               if r["aggression"] == "AGGRESSIVE")


def test_the_policy_version_moved_with_the_behaviour():
    """V2's 4,503 rows must stay readable as what V2 believed."""
    assert sb.POLICY_VERSION == "BETTOR_EV_SHADOW_V3"
    assert sb.POLICY_VERSION != "BETTOR_EV_SHADOW_V2"
