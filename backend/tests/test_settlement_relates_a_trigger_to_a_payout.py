"""CO-OCCURRENCE IS NOT A RELATIONSHIP, AND A RESOLUTION IS NOT A PAYOUT.

── THE FOUR DEFECTS CODEX REPRODUCED AGAINST THE COMMITTED V1 READER ──

Each was executed against `bettor_settlement_clauses` at 4ec0af0 before this
file existed. The recorded v1 behaviour is in the test that pins it.

    1. "If cancelled, contracts are not refunded."
           v1 -> established REFUNDS_THE_PURCHASE_BASIS.
       The sentence says the opposite of what was recorded.

    2. "A tie resolves 50-50, but cancellation is decided separately."
           v1 -> established a 50-cent CANCELLATION payout.
       The 50-50 belongs to the tie. The cancellation clause states no payout.

    3. "If cancelled, contracts pay $0.50."
           v1 -> established nothing.
       The sentence splitter broke "$0." from "50." and destroyed the amount.

    4. "If cancelled, this market resolves NO."
           v1 -> 0 cents for BOTH the long and the short side.
       The sentence describes THE MARKET'S RESOLUTION. Which side the account
       holds is a different fact, and a NO resolution pays the NO holder $1.00.

── WHAT V1 ACTUALLY CHECKED ──────────────────────────────────────────

Whether a sentence contained an outcome word and a payout word. That is
co-occurrence. It does not establish that the payout applies to that outcome,
and every defect above is one way for the two to come apart.

── WHAT V2 REQUIRES INSTEAD ──────────────────────────────────────────

A clause -- not a sentence -- must match a LISTED construction in which this
trigger and this payout are parts of one predication, with no negation, no
exception, no discretion and no second outcome. The list is
`SUPPORTED_CONSTRUCTIONS` and it IS the grammar: a venue sentence outside it
leaves the outcome unestablished. That is a bounded parser, and this file
asserts both halves of the bound -- what it reads AND what it refuses.

No claim of general prose interpretation is made anywhere.
"""

import pytest

from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_settlement_clauses as SC

VOID = IS.Region("fixture cancelled or abandoned", state=IS.STATE_VOID)
TIE_R = IS.Region("regulation tie", state=IS.STATE_TIE, lo=0, hi=0)


def leg(side, *, backs="A", cost=55, rules=None, kind=IS.KIND_MONEYLINE):
    cid = ("slug#%s" % side) if side else "slug"
    return IS.Leg(condition_id=cid, fixture_id="fx", kind=kind,
                  period=IS.PERIOD_FULL, overtime=IS.OT_INCLUDED, backs=backs,
                  quantity=10, cost_cents_per_unit=cost,
                  settlement_text_captured=True, settlement_rules=rules)


def rules_for(prose):
    return SC.interpret(prose, source="test_vocabulary")["rules"]


# ═════════════════════════════════════════════════════════════════════
# 1 · THE FOUR COUNTEREXAMPLES, EXACTLY AS REPORTED
# ═════════════════════════════════════════════════════════════════════

NEGATED = "If cancelled, contracts are not refunded."
MIXED = "A tie resolves 50-50, but cancellation is decided separately."
DECIMAL = "If cancelled, contracts pay $0.50."
RESOLVES_NO = "If cancelled, this market resolves NO."


def test_a_negated_payout_establishes_nothing():
    """v1 read this as a refund. It says the opposite."""
    got = SC.read_outcome(NEGATED, SC.CANCELLED)
    assert got["established"] is False, got
    assert got["refusal"] == SC.R_NEGATED
    assert got["resolution"] is None
    assert got["payout_cents"] is None
    assert any("not" in n for n in got["negations"]), got
    # And nothing downstream can price the cell.
    assert IS._leg_payout_cents(
        leg(SC.SIDE_LONG, rules=rules_for(NEGATED)), VOID) is None


@pytest.mark.parametrize("prose", [
    "If cancelled, contracts are not refunded.",
    "If the game is cancelled, stakes will not be returned.",
    "Cancelled markets do not pay out at 50-50.",
    "If cancelled, the market does not resolve NO.",
    "If cancelled, there is no refund of the purchase price.",
    "If cancelled, contracts never pay $0.50.",
])
def test_every_negation_form_leaves_the_outcome_unestablished(prose):
    got = SC.read_outcome(prose, SC.CANCELLED)
    assert got["established"] is False, (prose, got)
    assert got["refusal"] in (SC.R_NEGATED, SC.R_UNSUPPORTED_CONSTRUCTION,
                              SC.R_TRIGGER_WITHOUT_PAYOUT), got


def test_a_payout_in_one_clause_does_not_answer_a_trigger_in_another():
    """v1 gave cancellation the tie's 50-50. The sentence has two subjects."""
    doc = SC.interpret(MIXED)
    cancel = doc["rules"][SC.CANCELLED]
    assert cancel["established"] is False, cancel
    assert cancel["payout_cents"] is None
    # The cancellation clause names the outcome and states no resolution.
    assert cancel["refusal"] in (SC.R_TRIGGER_WITHOUT_PAYOUT,
                                 SC.R_DISCRETIONARY, SC.R_MIXED_TRIGGERS), cancel
    # AND THE TIE IS STILL READ. The repair is not "refuse everything".
    tie = doc["rules"][SC.TIE]
    assert tie["established"] is True, tie
    assert tie["resolution"] == SC.RES_HALF
    assert doc["established"] == [SC.TIE]


def test_the_clause_splitter_separates_the_two_subjects():
    parts = list(SC.clauses(MIXED))
    assert len(parts) == 2, parts
    assert "tie resolves 50-50" in parts[0]
    assert "cancellation" in parts[1]


def test_one_clause_naming_two_outcomes_relates_its_payout_to_neither():
    """The stronger case: both triggers inside ONE predication."""
    prose = "If the game is cancelled or tied the market resolves 50-50."
    for outcome in (SC.CANCELLED, SC.TIE):
        got = SC.read_clause(prose, outcome)
        assert got["established"] is False, (outcome, got)
        assert got["refusal"] == SC.R_MIXED_TRIGGERS, got
        assert got["also_names"], got


def test_a_decimal_amount_survives_the_sentence_splitter():
    """v1 split "$0." from "50." and lost the amount it was looking for."""
    assert list(SC.sentences(DECIMAL)) == [DECIMAL], (
        "a period followed by a digit is a decimal point, not a sentence end")
    got = SC.read_outcome(DECIMAL, SC.CANCELLED)
    assert got["established"] is True, got
    assert got["stated_cents"] == 50
    # FIFTY CENTS IS FIFTY CENTS however it is written, so a document saying
    # "$0.50" and one saying "50-50" do not look like a conflict.
    assert got["resolution"] == SC.RES_HALF
    assert IS._leg_payout_cents(
        leg(SC.SIDE_LONG, rules=rules_for(DECIMAL)), VOID) == 50


@pytest.mark.parametrize("prose,cents", [
    ("If cancelled, contracts pay $0.50.", 50),
    ("If cancelled, contracts pay 50 cents.", 50),
    ("If cancelled, contracts pay $0.25.", 25),
    ("If cancelled, contracts pay $1.00.", 100),
    ("If cancelled, contracts pay $0.00.", 0),
])
def test_amounts_are_read_at_their_stated_value(prose, cents):
    got = SC.read_outcome(prose, SC.CANCELLED)
    assert got["established"] is True, (prose, got)
    assert got["stated_cents"] == cents, got


def test_a_sentence_boundary_after_a_decimal_still_splits():
    prose = "If cancelled, contracts pay $0.50. If postponed the market stays open."
    parts = list(SC.sentences(prose))
    assert len(parts) == 2, parts
    doc = SC.interpret(prose)
    assert doc["rules"][SC.CANCELLED]["stated_cents"] == 50
    assert doc["rules"][SC.POSTPONED]["resolution"] == SC.RES_STAYS_OPEN


def test_a_line_of_three_point_five_is_not_two_sentences():
    """The same repair, on the other kind of decimal a venue writes."""
    prose = "The line is 3.5 goals. If cancelled, all stakes are refunded."
    assert len(list(SC.sentences(prose))) == 2
    assert SC.read_outcome(prose, SC.CANCELLED)["resolution"] == SC.RES_REFUND


# ═════════════════════════════════════════════════════════════════════
# 2 · THE AFFIRMATIVE CONTROLS -- THE BOUND HAS TWO SIDES
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("prose,outcome,resolution,construction", [
    ("If the game is cancelled all stakes are refunded.",
     SC.CANCELLED, SC.RES_REFUND, "CONDITION_THEN_RESOLUTION"),
    ("If the game is cancelled the market resolves 50-50.",
     SC.CANCELLED, SC.RES_HALF, "CONDITION_THEN_RESOLUTION"),
    ("If cancelled, contracts pay $0.50.",
     SC.CANCELLED, SC.RES_HALF, "CONDITION_THEN_RESOLUTION"),
    ("Contracts are refunded if the game is cancelled.",
     SC.CANCELLED, SC.RES_REFUND, "RESOLUTION_THEN_CONDITION"),
    ("A tie resolves 50-50.", SC.TIE, SC.RES_HALF, "TRIGGER_IS_THE_SUBJECT"),
    ("Cancelled markets are refunded.",
     SC.CANCELLED, SC.RES_REFUND, "TRIGGER_MODIFIES_THE_SUBJECT"),
    ("If the game is postponed the market remains open until replayed.",
     SC.POSTPONED, SC.RES_STAYS_OPEN, "CONDITION_THEN_RESOLUTION"),
    ("If the total lands exactly on the line the stakes are returned.",
     SC.PUSH, SC.RES_REFUND, "CONDITION_THEN_RESOLUTION"),
    ("If the game is shortened it is graded on the result at the time of the "
     "stoppage.", SC.SHORTENED, SC.RES_ON_PARTIAL, "CONDITION_THEN_RESOLUTION"),
])
def test_a_supported_construction_is_read_and_names_itself(prose, outcome,
                                                          resolution,
                                                          construction):
    got = SC.read_outcome(prose, outcome)
    assert got["established"] is True, (prose, got)
    assert got["resolution"] == resolution, got
    assert got["construction"] == construction, got
    # The exact source clause travels with it.
    assert got["clause"].rstrip(".") in prose


def test_the_five_outcomes_still_read_from_one_document():
    prose = ("The market resolves on the final score. A tie resolves 50-50. "
             "If the total lands exactly on the line the stakes are returned. "
             "If the game is cancelled all stakes are refunded. "
             "If the game is postponed the market remains open until it is "
             "replayed. If the game is shortened it is graded on the result at "
             "the time of the stoppage.")
    doc = SC.interpret(prose)
    assert doc["established"] == sorted(SC.OUTCOMES), doc["unestablished"]
    got = {o: doc["rules"][o]["resolution"] for o in SC.OUTCOMES}
    assert got == {SC.TIE: SC.RES_HALF, SC.PUSH: SC.RES_REFUND,
                   SC.CANCELLED: SC.RES_REFUND,
                   SC.POSTPONED: SC.RES_STAYS_OPEN,
                   SC.SHORTENED: SC.RES_ON_PARTIAL}, got
    # Each cites a DIFFERENT clause. Sharing one would make the separation
    # nominal.
    clauses = [doc["rules"][o]["clause"] for o in SC.OUTCOMES]
    assert len(set(clauses)) == len(clauses), clauses


def test_a_condition_on_the_trigger_is_retained():
    prose = "If the game is cancelled after the first half all stakes are refunded."
    got = SC.read_outcome(prose, SC.CANCELLED)
    assert got["established"] is True, got
    assert got["condition"], got
    assert "after the first half" in got["condition"], got["condition"]


# ═════════════════════════════════════════════════════════════════════
# 3 · WHAT THE BOUNDED PARSER REFUSES, AND SAYS IT IS REFUSING
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("prose,refusal", [
    ("Unless the game is cancelled, stakes are refunded.", SC.R_EXCEPTION),
    ("If cancelled, stakes are refunded except in the first half.",
     SC.R_EXCEPTION),
    ("If cancelled, we may refund all stakes.", SC.R_DISCRETIONARY),
    ("If cancelled, stakes are generally refunded.", SC.R_DISCRETIONARY),
    ("If cancelled, settlement is at our discretion.", SC.R_DISCRETIONARY),
    ("A cancelled game will be handled under our general rules.",
     SC.R_DISCRETIONARY),
    ("Cancellation: see our rules page.", SC.R_DISCRETIONARY),
])
def test_an_exception_or_a_discretion_is_not_a_stated_rule(prose, refusal):
    got = SC.read_outcome(prose, SC.CANCELLED)
    assert got["established"] is False, (prose, got)
    assert got["refusal"] == refusal, got


def test_an_unsupported_construction_says_so_and_names_what_it_would_have_read():
    """The honest refusal: this is about the parser, not about the venue."""
    prose = ("Refunds, in the case of cancellation, being the treatment "
             "customarily applied.")
    got = SC.read_outcome(prose, SC.CANCELLED)
    assert got["established"] is False, got
    assert got["refusal"] in (SC.R_UNSUPPORTED_CONSTRUCTION,
                              SC.R_DISCRETIONARY), got
    if got["refusal"] == SC.R_UNSUPPORTED_CONSTRUCTION:
        assert got["would_have_read"] == SC.RES_REFUND, got
        assert "this parser's grammar" in got["why"]


def test_the_module_declares_its_grammar_rather_than_claiming_general_reading():
    d = SC.describe()
    # SPECIFIC BEFORE GENERAL, and the order is asserted because it decides
    # which construction a clause is REPORTED as. Template 4's optional noun
    # would otherwise swallow every case template 3 exists to name.
    assert d["supported_constructions"] == [
        "CONDITION_THEN_RESOLUTION", "RESOLUTION_THEN_CONDITION",
        "TRIGGER_MODIFIES_THE_SUBJECT", "TRIGGER_IS_THE_SUBJECT"]
    assert "refuses everything else" in d["this_is_a_bounded_parser"]
    assert "no claim of general prose interpretation" in \
        d["this_is_a_bounded_parser"]
    # And the four defects are on the record in the module itself.
    assert set(d["what_v1_got_wrong"]) == {"negation", "mixed_triggers",
                                           "decimals", "the_held_side"}


def test_two_clauses_stating_different_resolutions_are_still_a_conflict():
    prose = ("If the game is cancelled the market resolves 50-50. "
             "If the game is cancelled all stakes are refunded.")
    got = SC.read_outcome(prose, SC.CANCELLED)
    assert got["established"] is False
    assert got["refusal"] == SC.R_CONFLICTING_CLAUSES
    assert sorted(got["states"]) == sorted([SC.RES_HALF, SC.RES_REFUND])
    assert "cannot be repaired by preferring the first" in got["why"]


def test_silence_and_absent_prose_are_still_distinct_refusals():
    assert SC.read_outcome("The market resolves on the final score.",
                           SC.CANCELLED)["refusal"] == SC.R_NOT_STATED
    for empty in ("", "   ", None):
        assert SC.read_outcome(empty, SC.CANCELLED)["refusal"] == SC.R_NO_PROSE


# ═════════════════════════════════════════════════════════════════════
# 4 · THE MARKET'S RESOLUTION IS NOT THE HELD SIDE'S PAYOUT
# ═════════════════════════════════════════════════════════════════════

def test_a_no_resolution_pays_the_no_holder_and_not_the_yes_holder():
    """v1 returned 0 for BOTH sides. A NO resolution pays the NO holder $1.00."""
    rules = rules_for(RESOLVES_NO)
    assert rules[SC.CANCELLED]["resolution"] == SC.RES_NO
    assert rules[SC.CANCELLED]["side_dependent"] is True
    # The resolution alone has no per-contract value.
    assert rules[SC.CANCELLED]["payout_cents"] is None
    assert SC.RES_NO not in SC.PAYOUT_CENTS
    assert IS._leg_payout_cents(leg(SC.SIDE_LONG, rules=rules), VOID) == 0
    assert IS._leg_payout_cents(leg(SC.SIDE_SHORT, rules=rules), VOID) == 100


def test_a_yes_resolution_pays_the_yes_holder_and_not_the_no_holder():
    rules = rules_for("If cancelled, this market resolves YES.")
    assert rules[SC.CANCELLED]["resolution"] == SC.RES_YES
    assert IS._leg_payout_cents(leg(SC.SIDE_LONG, rules=rules), VOID) == 100
    assert IS._leg_payout_cents(leg(SC.SIDE_SHORT, rules=rules), VOID) == 0


def test_a_fifty_cent_resolution_pays_both_sides_the_same():
    rules = rules_for("If cancelled, the market resolves 50-50.")
    assert rules[SC.CANCELLED]["side_dependent"] is False
    for side in SC.SIDES:
        assert IS._leg_payout_cents(leg(side, rules=rules), VOID) == 50


def test_a_basis_refund_pays_each_side_its_own_cost():
    rules = rules_for("If cancelled, all stakes are refunded.")
    assert rules[SC.CANCELLED]["basis_dependent"] is True
    assert IS._leg_payout_cents(leg(SC.SIDE_LONG, rules=rules, cost=55),
                                VOID) == 55
    assert IS._leg_payout_cents(leg(SC.SIDE_SHORT, rules=rules, cost=30),
                                VOID) == 30
    # Unstated basis is undetermined, not fifty.
    unstated = IS.Leg(condition_id="slug#%s" % SC.SIDE_LONG, fixture_id="fx",
                      kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                      overtime=IS.OT_INCLUDED, backs="A", quantity=10,
                      cost_cents_per_unit=None,
                      settlement_text_captured=True, settlement_rules=rules)
    assert IS._leg_payout_cents(unstated, VOID) is None


@pytest.mark.parametrize("backs", ["A", "B"])
@pytest.mark.parametrize("resolution,side,cents", [
    (SC.RES_YES, SC.SIDE_LONG, 100), (SC.RES_YES, SC.SIDE_SHORT, 0),
    (SC.RES_NO, SC.SIDE_LONG, 0), (SC.RES_NO, SC.SIDE_SHORT, 100),
])
def test_polarity_never_comes_from_team_orientation(backs, resolution, side,
                                                    cents):
    """WHICH PARTICIPANT A LEG BACKS AND WHICH TOKEN IT HOLDS ARE INDEPENDENT.

    A leg backing team B can hold either outcome token of a market about team B.
    Deriving YES/NO from A/B is the conflation this must not make, so the same
    side gives the same payout under both orientations.
    """
    rules = rules_for("If cancelled, this market resolves %s."
                      % ("YES" if resolution == SC.RES_YES else "NO"))
    assert IS._leg_payout_cents(leg(side, backs=backs, rules=rules),
                                VOID) == cents


def test_a_leg_with_no_side_cannot_be_paid_a_yes_or_no_resolution():
    """Unsupported semantics refuse. A bare-slug identity has no order intent."""
    rules = rules_for(RESOLVES_NO)
    assert IS._leg_payout_cents(leg(None, rules=rules), VOID) is None
    got = SC.side_payout_cents(SC.RES_NO, None)
    assert got["refusal"] == SC.R_SIDE_NOT_STATED
    assert "V1 returned 0 for both sides" in got["why"]
    # And a side outside the vocabulary is the same refusal, not a guess.
    assert SC.side_payout_cents(SC.RES_NO, "ORDER_INTENT_SELL")["cents"] is None


def test_the_side_is_read_from_the_identity_not_from_backs():
    rules = rules_for(RESOLVES_NO)
    a = leg(SC.SIDE_SHORT, backs="A", rules=rules)
    assert IS.held_side_of_leg(a) == SC.SIDE_SHORT
    assert IS.held_side_of_leg(leg(None, rules=rules)) is None
    # The identity is the slug plus the order intent.
    assert HS.split_identity(a.condition_id) == ("slug", SC.SIDE_SHORT)


def test_the_exact_clause_and_the_side_travel_together_into_valuation():
    rules = rules_for(RESOLVES_NO)
    for side, cents in ((SC.SIDE_LONG, 0), (SC.SIDE_SHORT, 100)):
        held = leg(side, rules=rules)
        rec = held.rule_for(SC.CANCELLED)
        assert rec["clause"] == RESOLVES_NO
        assert rec["construction"] == "CONDITION_THEN_RESOLUTION"
        assert IS.held_side_of_leg(held) == side
        assert IS._leg_payout_cents(held, VOID) == cents


def test_a_stays_open_resolution_is_established_and_is_still_not_a_payout():
    rules = rules_for("If postponed the market remains open until replayed.")
    assert rules[SC.POSTPONED]["established"] is True
    for side in SC.SIDES:
        got = SC.side_payout_cents(SC.RES_STAYS_OPEN, side)
        assert got["cents"] is None
        assert got["refusal"] is None, "not unknown -- there is no money here"
        assert got["stays_open"] is True


def test_the_two_sides_of_one_instrument_disagree_only_where_they_should():
    """Both sides, all four resolutions, in one table."""
    table = {}
    for label, prose in (
            ("YES", "If cancelled, this market resolves YES."),
            ("NO", "If cancelled, this market resolves NO."),
            ("HALF", "If cancelled, the market resolves 50-50."),
            ("REFUND", "If cancelled, all stakes are refunded.")):
        rules = rules_for(prose)
        table[label] = tuple(
            IS._leg_payout_cents(leg(s, rules=rules, cost=55), VOID)
            for s in SC.SIDES)
    assert table == {"YES": (100, 0), "NO": (0, 100),
                     "HALF": (50, 50), "REFUND": (55, 55)}, table


# ═════════════════════════════════════════════════════════════════════
# 5 · AND THE FIELD GUARD STILL HOLDS AT THE POINT OF USE
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text", [
    "This market resolves on the final score and includes any extra innings "
    "played. A tie resolves 50-50.",
    "A tie resolves 50-50.",
    "This market includes any extra innings played.",
])
def test_text_that_does_not_name_a_cancellation_cannot_price_the_void_cell(text):
    held = IS.Leg(condition_id="slug#%s" % SC.SIDE_LONG, fixture_id="fx",
                  kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                  overtime=IS.OT_INCLUDED, backs="A", quantity=10,
                  cost_cents_per_unit=55, void_rule=text,
                  settlement_text_captured=True)
    assert IS._leg_payout_cents(held, VOID) is None, text


def test_a_legacy_void_field_that_does_name_a_cancellation_still_prices_it():
    held = IS.Leg(condition_id="slug#%s" % SC.SIDE_LONG, fixture_id="fx",
                  kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                  overtime=IS.OT_INCLUDED, backs="A", quantity=10,
                  cost_cents_per_unit=55,
                  void_rule="If the game is cancelled the market resolves 50-50.",
                  settlement_text_captured=True)
    assert IS._leg_payout_cents(held, VOID) == 50


def test_a_legacy_void_field_carrying_a_negation_prices_nothing():
    """The guard and the parser agree: the legacy path is read by the same
    grammar, so a negated legacy string cannot price a cell either."""
    held = IS.Leg(condition_id="slug#%s" % SC.SIDE_LONG, fixture_id="fx",
                  kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                  overtime=IS.OT_INCLUDED, backs="A", quantity=10,
                  cost_cents_per_unit=55, void_rule=NEGATED,
                  settlement_text_captured=True)
    assert IS._leg_payout_cents(held, VOID) is None
