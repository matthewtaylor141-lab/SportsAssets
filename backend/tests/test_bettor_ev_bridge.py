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
    assert prov["loadedFrom"].endswith("research/beta48/shadow")
    assert "action_ev" in prov["modules"]
    # §2: every named module must import, not just the first one.
    pre = evb.preflight()
    assert pre["ok"], pre["why"]
    assert set(pre["modules"].values()) == {"IMPORTED"}


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

    With fee defaulted to zero, the break-even comes out 0.000000 --
    "any fill rate whatsoever makes this pay" -- which is a confident
    claim manufactured entirely out of a term nobody measured.
    """
    r = _row(evb.evaluate(BOOK), "MAKE_YES")
    assert r["feeStatus"] == evb.NOT_IDENTIFIED
    assert r["BREAK_EVEN_P_FILL_BAND"] == evb.NOT_IDENTIFIED


def test_passive_ev_is_not_identified_because_p_fill_never_was():
    r = _row(evb.evaluate(BOOK), "MAKE_YES")
    assert r["expectedNetDollarsPerContract"] == evb.NOT_IDENTIFIED
    assert "P_FILL" in r["missingCriticalTerms"]
    assert "never rested an order" in r["whyNot"]


# ── §0: fill selection has no established sign ───────────────────────

def test_fill_selection_is_carried_not_zeroed():
    """The correction against this bridge's own first version.

    prior_registry declares DIRECTION_ASSUMED = NO and warns that a
    consumer reading only the mean "sees 0.0 and silently treats the
    term as absent". That consumer was this bridge.
    """
    band = evb.fill_selection_band()
    assert band["directionAssumed"] == "NO"
    assert band["priorCenter"] == "ZERO"
    assert band["source"] == "STRUCTURAL_NONDIRECTIONAL_PRIOR"
    assert band["bettorNativeObservations"] == 0
    # The band straddles zero: ADVERSE at P10, FAVOURABLE at P90.
    assert band["P10"] < 0 < band["P90"]


def test_no_sign_constraint_is_imposed_on_fill_selection():
    """A bound needs a sign. This term has none, so there is no bound."""
    r = _row(evb.evaluate(BOOK, fee="0.001"), "MAKE_YES")
    assert "BREAK_EVEN_P_FILL_LOWER_BOUND" not in r
    assert "noSignIsAssumed" in r
    assert r["fillSelectionConvention"] == "SEPARATE_TERM"


def test_the_break_even_is_reported_across_the_whole_band():
    """The width must travel with the mean, or the prior is lost."""
    r = _row(evb.evaluate(BOOK, fee="0.001"), "MAKE_YES")
    band = r["BREAK_EVEN_P_FILL_BAND"]
    assert set(band) == {"P10", "P50", "P90"}
    # An ADVERSE fill-selection draw demands a HIGHER fill rate; a
    # FAVOURABLE one demands less. Both directions are represented.
    assert Decimal(band["P10"]) > Decimal(band["P50"]) > Decimal(band["P90"])


def test_a_cross_cannot_be_selected_against_so_the_term_is_excluded():
    """EXCLUDED, never 0.0 -- the distinction prior_registry insists on."""
    r = _row(evb.evaluate(BOOK, fee="0.001"), "TAKE_YES")
    assert r["fillSelectionConvention"] == "EXCLUDED"
    assert "not zero" in r["whyFillSelectionExcluded"]


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

# ── §1: a venue-relative cost is not a settlement EV ─────────────────

def test_crossing_costs_money_against_the_venue_benchmark():
    """What the number IS: an execution cost, measured and real."""
    out = evb.evaluate(BOOK)
    for action in ("TAKE_YES", "TAKE_NO", "DIRECT_EXIT"):
        r = _row(out, action)
        assert r["status"] == "EXECUTION_COST_IDENTIFIED", action
        assert Decimal(r[evb.SNAPSHOT_EXECUTION_COST]) < 0
        assert r["executionCostBenchmark"] == evb.FV_VENUE_IMPLIED


def test_that_cost_is_not_a_settlement_ev_and_not_alpha():
    """What the number IS NOT. B0 is the venue measured against itself."""
    out = evb.evaluate(BOOK)
    r = _row(out, "TAKE_YES")
    assert r["settlementEv"] == evb.SETTLEMENT_EV_NOT_IDENTIFIED
    assert r[evb.FV_BETTOR_INDEPENDENT] == evb.NOT_IDENTIFIED
    assert "mispriced" in r["whatThisDoesNotEstablish"]
    assert out["actionEvStatus"] == evb.NOT_IDENTIFIED


def test_the_two_fair_values_never_merge():
    out = evb.evaluate(BOOK)
    assert out[evb.FV_BETTOR_INDEPENDENT] == evb.NOT_IDENTIFIED
    assert evb.fair_value(BOOK)["kind"] == evb.FV_VENUE_IMPLIED
    assert "cannot be evidence against itself" in evb.fair_value(BOOK)["why"]
    # Every row carries the separation, so no reader can lose it.
    assert all(r.get(evb.FV_BETTOR_INDEPENDENT) == evb.NOT_IDENTIFIED
               for r in out["table"])


def test_no_action_ever_claims_an_identified_settlement_ev():
    """The gate that matters while independent fair value is absent."""
    for fee in (None, "0.001", "0.05"):
        out = evb.evaluate(BOOK, fee=fee)
        assert out["actionEvStatus"] == evb.NOT_IDENTIFIED
        assert out["bestAction"] == "NO_TRADE"


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
    assert _row(out, "MAKE_YES")["BREAK_EVEN_P_FILL_BAND"]["P50"] == "0.050000"
    assert "BREAK_EVEN_P_FILL_BAND" not in _row(out, "MAKE_BOTH")


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
    comp = d["actionEvComponents"]
    assert comp["bestAction"] == "NO_TRADE"
    assert comp["settlementEvStatus"] == evb.SETTLEMENT_EV_NOT_IDENTIFIED
    assert comp[evb.FV_BETTOR_INDEPENDENT] == evb.NOT_IDENTIFIED
    assert d.get("action") in (None, "NO_TRADE")


def test_no_trade_is_now_a_conclusion_rather_than_an_assertion():
    d = sb.decide(_opportunity(), BOOK)
    assert len(d["alternatives"]) == len(acts.ACTIONS)
    # The old stub said the same sentence about all three alternatives.
    whys = {a["why"] for a in d["alternatives"]}
    assert len(whys) > 1, "every alternative still carries one stock reason"
    # NOT_IDENTIFIED is the CORRECT status: no settlement EV exists for
    # any action while independent fair value is absent. What changed
    # is that the row now carries the comparison that reached it.
    assert d["actionEvStatus"] == evb.NOT_IDENTIFIED
    costs = [a for a in d["alternatives"]
             if a.get("snapshotExecutionCostVsVenuePrice")
             not in (None, evb.NOT_IDENTIFIED)]
    assert len(costs) == 5, "aggressive execution costs were not recorded"


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
