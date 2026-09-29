"""EACH EXCEPTIONAL OUTCOME IS ESTABLISHED BY ITS OWN CLAUSE, OR NOT AT ALL.

── THE DEFECT THESE TESTS PIN ────────────────────────────────────────

Owner directive §3, verbatim:

    "build_leg() passes the entire captured prose into both tie_rule and
     void_rule. _leg_payout_cents() interprets '50-50' in void_rule as a
     50-cent cancellation payout... That establishes neither cancellation nor
     abandonment treatment. Nevertheless, the same phrase can currently
     establish the void payout."

Reproduced by execution before any of this existed:

    PROSE = ('This market resolves on the final score and includes any extra '
             'innings played. A tie resolves 50-50.')
    IS._leg_payout_cents(Leg(tie_rule=PROSE, void_rule=PROSE, ...), VOID) == 50

The prose says nothing whatever about cancellation. A tie is a fixture PLAYED
to a drawn result; a cancellation is a fixture that NEVER HAPPENED. They are
different events with different money, and one sentence was answering for both
because both fields held the same blob.

── THE FOUR REGRESSIONS THE DIRECTIVE NAMES ──────────────────────────

    1. Tie-only wording leaves cancellation undetermined.
    2. Explicit cancellation wording establishes only its stated treatment.
    3. Conflicting or ambiguous clauses produce a refusal.
    4. Unknown cancellation treatment is not bypassed by declaring
       cancellation impossible.

Each has its own section below. Everything here is pure: no database, no
network, no fixtures. The prose strings are the inputs and the payouts are the
outputs, so a failure names the sentence that caused it.

A NOTE ON WHAT THESE PROSE STRINGS ARE. They are written for this test to
exercise the reader's vocabulary. They are NOT captured venue text and no claim
about Polymarket's actual published rules rests on them; the production path
reads the venue's own text through `build_leg`, and that reading is what the
integration tests exercise.
"""

from fractions import Fraction

import pytest

from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_settlement_clauses as SC


# ── THE EXACT PROSE FROM THE REPRODUCTION ────────────────────────────
DEFECT_PROSE = ("This market resolves on the final score and includes any "
                "extra innings played. A tie resolves 50-50.")

VOID = IS.Region("fixture cancelled or abandoned", state=IS.STATE_VOID)
TIE = IS.Region("regulation tie", state=IS.STATE_TIE, lo=0, hi=0)
POSTPONED = IS.Region("postponed", state=IS.STATE_POSTPONED)


def leg(**kw):
    base = dict(condition_id="held", fixture_id="fx",
                kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                overtime=IS.OT_INCLUDED, backs="A", quantity=1,
                cost_cents_per_unit=55)
    base.update(kw)
    return IS.Leg(**base)


def read(prose, **kw):
    """A leg carrying the structured five-outcome reading of `prose`."""
    doc = SC.interpret(prose, source="test_vocabulary_exercise",
                       retrieved_at="2026-09-29T00:00:00Z")
    kw.setdefault("settlement_text_captured", True)
    return leg(settlement_rules=doc["rules"],
               settlement_provenance=doc["provenance"], **kw), doc


# ═════════════════════════════════════════════════════════════════════
# REGRESSION 1 · TIE-ONLY WORDING LEAVES CANCELLATION UNDETERMINED
# ═════════════════════════════════════════════════════════════════════

def test_the_reproduced_defect_no_longer_establishes_a_void_payout():
    """The exact prose and the exact call that returned 50 cents."""
    blob = leg(tie_rule=DEFECT_PROSE, void_rule=DEFECT_PROSE,
               settlement_text_captured=True)
    # This is the assertion the bug report is about.
    assert IS._leg_payout_cents(blob, VOID) is None, (
        "the whole-prose blob in void_rule established a cancellation payout "
        "from a sentence about a drawn fixture")
    # And the tie, which the prose DOES state, is still established -- the fix
    # is not "refuse everything".
    assert IS._leg_payout_cents(blob, TIE) == 50


def test_tie_only_prose_leaves_cancellation_unestablished_with_a_reason():
    rules = SC.interpret(DEFECT_PROSE)["rules"]
    assert rules[SC.TIE]["established"] is True
    assert rules[SC.TIE]["payout_cents"] == 50
    assert rules[SC.TIE]["clause"] == "A tie resolves 50-50."
    cancel = rules[SC.CANCELLED]
    assert cancel["established"] is False
    assert cancel["refusal"] == SC.R_NOT_STATED
    # Silence is reported as silence, not as a payout of zero.
    assert cancel["payout_cents"] is None
    assert cancel["candidate_clauses"] == []


def test_tie_only_prose_leaves_all_four_other_outcomes_unestablished():
    doc = SC.interpret(DEFECT_PROSE)
    assert doc["established"] == [SC.TIE]
    for outcome in (SC.PUSH, SC.CANCELLED, SC.POSTPONED, SC.SHORTENED):
        assert doc["unestablished"][outcome] == SC.R_NOT_STATED, outcome


def test_the_structured_reading_of_the_defect_prose_refuses_the_void_cell():
    held, _ = read(DEFECT_PROSE)
    assert IS._leg_payout_cents(held, VOID) is None
    assert IS._leg_payout_cents(held, TIE) == 50


def test_build_leg_puts_one_clause_in_each_field_not_the_document():
    """The field contract, asserted on the shape `build_leg` produces."""
    doc = SC.interpret(DEFECT_PROSE)
    tie_rec = doc["rules"][SC.TIE]
    void_rec = doc["rules"][SC.CANCELLED]
    built = leg(tie_rule=tie_rec["clause"] if tie_rec["established"] else None,
                void_rule=(void_rec["clause"] if void_rec["established"]
                           else None),
                settlement_text_captured=True,
                settlement_rules=doc["rules"])
    assert built.tie_rule == "A tie resolves 50-50."
    assert built.void_rule is None, (
        "void_rule must hold the cancellation clause or nothing; holding the "
        "document is the defect")
    assert "extra innings" not in (built.tie_rule or ""), (
        "the tie field carries the tie clause, not the overtime sentence")


# ═════════════════════════════════════════════════════════════════════
# REGRESSION 2 · EXPLICIT CANCELLATION WORDING ESTABLISHES ONLY ITS OWN
# ═════════════════════════════════════════════════════════════════════

CANCEL_HALF = ("This market resolves on the final score. If the game is "
               "cancelled the market resolves 50-50.")
CANCEL_REFUND = ("This market resolves on the final score. If the game is "
                 "cancelled all stakes are refunded.")


def test_a_cancellation_clause_establishes_the_void_cell_and_nothing_else():
    held, doc = read(CANCEL_HALF)
    assert IS._leg_payout_cents(held, VOID) == 50
    assert doc["rules"][SC.CANCELLED]["clause"] == (
        "If the game is cancelled the market resolves 50-50.")
    # It says nothing about a tie, so the tie stays unknown -- the symmetric
    # half of regression 1, and the half that a looser reader would get wrong
    # in the other direction.
    assert IS._leg_payout_cents(held, TIE) is None
    assert doc["rules"][SC.TIE]["refusal"] == SC.R_NOT_STATED


def test_a_refund_of_basis_is_not_settlement_at_fifty_cents():
    """Directive §3: "A refund of purchase basis is not interchangeable with
    settlement at 50 cents." """
    doc = SC.interpret(CANCEL_REFUND)
    rec = doc["rules"][SC.CANCELLED]
    assert rec["established"] is True
    assert rec["payout_class"] == SC.PAY_REFUND_BASIS
    # The CLASS is established; the CENTS are not a constant of the class.
    assert rec["payout_cents"] is None
    assert SC.PAY_REFUND_BASIS not in SC.PAYOUT_CENTS

    for cost, expected in ((30, 30), (55, 55), (91, 91)):
        held = leg(cost_cents_per_unit=cost, settlement_text_captured=True,
                   settlement_rules=doc["rules"])
        assert IS._leg_payout_cents(held, VOID) == expected, (
            "a refund returns what was PAID; mapping it to 50 would invent a "
            "gain on a contract bought at %dc" % cost)


def test_a_refund_is_undetermined_when_the_basis_is_not_stated():
    doc = SC.interpret(CANCEL_REFUND)
    held = leg(cost_cents_per_unit=None, settlement_text_captured=True,
               settlement_rules=doc["rules"])
    assert IS._leg_payout_cents(held, VOID) is None, (
        "the rule is known and the number is not; that is undetermined, not 50")


def test_each_of_the_five_outcomes_is_read_from_its_own_clause():
    prose = ("The market resolves on the final score. A tie resolves 50-50. "
             "If the total lands exactly on the line the bet is a push and "
             "stakes are refunded. If the game is cancelled all stakes are "
             "refunded. If the game is postponed the market remains open "
             "until it is replayed. If the game is shortened it is graded on "
             "the result at the time of the stoppage.")
    doc = SC.interpret(prose)
    assert doc["established"] == sorted(SC.OUTCOMES)
    got = {o: doc["rules"][o]["payout_class"] for o in SC.OUTCOMES}
    assert got == {
        SC.TIE: SC.PAY_HALF,
        SC.PUSH: SC.PAY_REFUND_BASIS,
        SC.CANCELLED: SC.PAY_REFUND_BASIS,
        SC.POSTPONED: SC.PAY_STAYS_OPEN,
        SC.SHORTENED: SC.PAY_ON_PARTIAL,
    }
    # Each carries a DIFFERENT sentence. If two outcomes cite the same clause
    # the separation is nominal.
    clauses = [doc["rules"][o]["clause"] for o in SC.OUTCOMES]
    assert len(set(clauses)) == len(clauses), clauses


def test_a_postponement_clause_is_recorded_and_is_still_not_a_payout():
    prose = "If the game is postponed the market remains open until replayed."
    held, doc = read(prose)
    assert doc["rules"][SC.POSTPONED]["established"] is True
    assert doc["rules"][SC.POSTPONED]["payout_class"] == SC.PAY_STAYS_OPEN
    # Established, and still no money in the cell: the market stays open.
    assert IS._leg_payout_cents(held, POSTPONED) is None


def test_overtime_is_not_read_here_because_another_module_owns_it():
    """Two readers for one rule is two answers for one rule."""
    assert "OVERTIME" not in SC.OUTCOMES
    from sportsassets import bettor_venue_settlement as VS
    assert hasattr(VS, "OVERTIME_PROSE")
    doc = SC.interpret(DEFECT_PROSE)
    assert "OVERTIME" not in doc["rules"]
    assert "overtime" in SC.describe()["overtime_is_not_here"].lower()


# ═════════════════════════════════════════════════════════════════════
# REGRESSION 3 · CONFLICTING OR AMBIGUOUS CLAUSES PRODUCE A REFUSAL
# ═════════════════════════════════════════════════════════════════════

def test_two_clauses_stating_different_cancellation_payouts_are_a_conflict():
    prose = ("If the game is cancelled the market resolves 50-50. "
             "If the game is cancelled all stakes are refunded.")
    rec = SC.interpret(prose)["rules"][SC.CANCELLED]
    assert rec["established"] is False
    assert rec["refusal"] == SC.R_CONFLICTING_CLAUSES
    assert len(rec["candidate_clauses"]) == 2
    # Directive: "A settlement conflict cannot be repaired by declaring the
    # contracts equivalent." Nor by preferring the first sentence.
    assert "cannot be repaired by preferring the first" in rec["why"]


def test_a_conflict_leaves_the_void_cell_undetermined_not_first_wins():
    prose = ("If the game is cancelled the market resolves 50-50. "
             "If the game is cancelled all stakes are refunded.")
    held, _ = read(prose)
    assert IS._leg_payout_cents(held, VOID) is None


def test_one_clause_stating_two_payouts_is_ambiguous_not_a_choice():
    prose = "If cancelled the market resolves 50-50 and stakes are refunded."
    rec = SC.interpret(prose)["rules"][SC.CANCELLED]
    assert rec["established"] is False
    assert rec["refusal"] == SC.R_AMBIGUOUS_CLAUSE
    assert rec["clause"] == prose
    assert "a reading nobody made" in rec["why"]


def test_a_clause_naming_an_outcome_with_no_payout_is_acknowledged_not_priced():
    prose = "A cancelled game will be handled under our general rules."
    rec = SC.interpret(prose)["rules"][SC.CANCELLED]
    assert rec["established"] is False
    assert rec["refusal"] == SC.R_TRIGGER_WITHOUT_PAYOUT
    assert rec["clause"] == prose
    # The distinction that matters: the venue ACKNOWLEDGED the outcome and did
    # not state its money. That is a different report from silence, and both
    # are refusals.
    assert rec["refusal"] != SC.R_NOT_STATED


def test_absent_prose_refuses_distinctly_from_prose_that_omits_the_outcome():
    for empty in ("", "   ", None):
        rec = SC.interpret(empty)["rules"][SC.CANCELLED]
        assert rec["refusal"] == SC.R_NO_PROSE, empty
    rec = SC.interpret("The market resolves on the final score.")
    assert rec["rules"][SC.CANCELLED]["refusal"] == SC.R_NOT_STATED


def test_a_conflict_in_one_outcome_does_not_poison_a_clean_other_outcome():
    prose = ("A tie resolves 50-50. "
             "If the game is cancelled the market resolves 50-50. "
             "If the game is cancelled all stakes are refunded.")
    doc = SC.interpret(prose)
    assert doc["rules"][SC.CANCELLED]["refusal"] == SC.R_CONFLICTING_CLAUSES
    assert doc["rules"][SC.TIE]["established"] is True
    assert doc["established"] == [SC.TIE]


# ═════════════════════════════════════════════════════════════════════
# REGRESSION 4 · DECLARING CANCELLATION IMPOSSIBLE IS NOT READING THE RULE
# ═════════════════════════════════════════════════════════════════════
#
# `payoff_table(fixture_can_void=False)` REMOVES the VOID cell. A leg whose
# cancellation payout is undetermined stops contributing `None` to the joint
# column, and the minimum becomes a number. The floor then looks proved when
# what happened is that the unknown was deleted.

SPREAD_CANCEL = ("This market resolves on the final score. If the game is "
                 "cancelled all stakes are refunded.")


def _pair(prose_a, prose_b):
    a = read(prose_a, condition_id="a")[0]
    b = read(prose_b, condition_id="b", kind=IS.KIND_SPREAD,
             line=Fraction(-9, 2), backs="B", cost_cents_per_unit=30)[0]
    return a, b


def test_suppressing_void_is_reported_when_it_hides_an_unread_rule():
    a, b = _pair(DEFECT_PROSE, DEFECT_PROSE)   # neither states cancellation
    hidden = IS.void_suppression_hides((a, b), fixture_can_void=False)
    assert len(hidden) == 2
    assert all(SC.R_NOT_STATED in h for h in hidden)
    assert all("artefact of the suppression" in h for h in hidden)


def test_suppressing_void_reports_nothing_when_the_rule_was_read():
    a, b = _pair(SPREAD_CANCEL, SPREAD_CANCEL)
    assert IS.void_suppression_hides((a, b), fixture_can_void=False) == ()


def test_nothing_is_hidden_when_the_void_cell_is_kept():
    a, b = _pair(DEFECT_PROSE, DEFECT_PROSE)
    assert IS.void_suppression_hides((a, b), fixture_can_void=True) == ()


def test_classify_refuses_rather_than_reporting_a_floor_from_a_deleted_cell():
    a, b = _pair(DEFECT_PROSE, DEFECT_PROSE)
    s = IS.classify(a, b, sport_permits_tie=True, fixture_can_void=False,
                    fixture_can_postpone=False)
    assert s.taxonomy == IS.UNESTABLISHABLE
    assert s.min_payout_cents is None, (
        "a minimum computed over a table missing the undetermined cancellation "
        "cell is an artefact of the suppression, not a floor")
    assert any("declared impossible" in g for g in s.missing_facts)


def test_the_same_pair_classifies_once_the_cancellation_rule_is_read():
    """The refusal is about the MISSING RULE, not about suppression itself.

    NOTE ON WHAT IS ASSERTED. `UNESTABLISHABLE` is returned for two different
    reasons -- a REFUSAL (facts needed to build a payout function are missing,
    `missing_facts` non-empty, no table computed) and an UNDETERMINED REGION
    (the legs are fine, some cell has no payout). My first draft of this test
    asserted on the taxonomy and failed, because this pair states no TIE rule
    and so still has an undetermined tie cell. The suppression repair is about
    the refusal, so that is what is asserted.
    """
    a, b = _pair(SPREAD_CANCEL, SPREAD_CANCEL)
    s = IS.classify(a, b, sport_permits_tie=True, fixture_can_void=False,
                    fixture_can_postpone=False)
    assert s.missing_facts == (), s.missing_facts
    assert s.table, "a refused structure computes no table; this one is not"
    # The void cell is gone, by the caller's claim, and nothing was hidden.
    assert not [r for r in s.table if r["state"] == IS.STATE_VOID]


def test_keeping_the_void_cell_leaves_the_floor_undetermined_not_refused():
    """The honest alternative to suppression: the cell stays and says None."""
    a, b = _pair(DEFECT_PROSE, DEFECT_PROSE)
    s = IS.classify(a, b, sport_permits_tie=True, fixture_can_void=True,
                    fixture_can_postpone=False)
    # Not refused -- the legs are fine. The FLOOR is unknown, and the table
    # says which cell made it unknown.
    assert s.missing_facts == (), s.missing_facts
    # AND NO GUARANTEE IS CLAIMED. This is the assertion that matters: the
    # undetermined cell must not be skipped on the way to a locked surplus.
    assert s.guaranteed_gross_result_cents is None
    assert s.locks_gross_surplus is None
    voids = [r for r in s.table if r["state"] == IS.STATE_VOID]
    assert len(voids) == 1
    assert voids[0]["determined"] is False
    assert voids[0]["joint_cents"] is None
    assert any("cancelled" in r.lower() for r in s.undetermined_regions), (
        s.undetermined_regions)


def test_a_reported_minimum_over_determined_cells_is_not_a_guarantee():
    """`min_payout_cents` is a minimum over the cells that HAVE a payout.

    It is reported alongside the undetermined list, and the two must be read
    together: with a cell undetermined, the true minimum is unknown and could
    be anything, including zero. `guaranteed_gross_result_cents` is the field
    that answers "is a surplus locked", and it is None here on purpose.
    """
    a, b = _pair(DEFECT_PROSE, DEFECT_PROSE)
    s = IS.classify(a, b, sport_permits_tie=True, fixture_can_void=True,
                    fixture_can_postpone=False)
    assert s.undetermined_regions, "this fixture is the undetermined case"
    assert s.min_payout_cents is not None, (
        "the minimum over determined cells is still reported -- suppressing "
        "it would hide information rather than add caution")
    assert s.guaranteed_gross_result_cents is None, (
        "and it is NOT promoted to a guarantee while a cell is undetermined")


# ═════════════════════════════════════════════════════════════════════
# PROVENANCE · A DECISION MADE ON THIS READING STAYS AUDITABLE
# ═════════════════════════════════════════════════════════════════════

def test_the_reading_persists_the_raw_text_source_time_hash_and_version():
    doc = SC.interpret(DEFECT_PROSE, source="polymarket:rules:slug-x",
                       retrieved_at="2026-09-29T12:00:00Z")
    p = doc["provenance"]
    assert p["raw_text"] == DEFECT_PROSE
    assert p["chars"] == len(DEFECT_PROSE)
    assert p["source"] == "polymarket:rules:slug-x"
    assert p["retrieved_at"] == "2026-09-29T12:00:00Z"
    assert p["interpretation_version"] == SC.VERSION
    assert len(p["content_sha256"]) == 64


def test_the_content_hash_changes_when_the_venue_changes_one_word():
    a = SC.interpret(DEFECT_PROSE)["provenance"]["content_sha256"]
    b = SC.interpret(DEFECT_PROSE.replace("50-50", "50/50"))
    assert a != b["provenance"]["content_sha256"], (
        "the hash is what proves the text is the same text")


def test_every_established_rule_carries_the_clause_it_was_read_from():
    doc = SC.interpret("A tie resolves 50-50. If cancelled stakes are "
                       "refunded.")
    for outcome in doc["established"]:
        rec = doc["rules"][outcome]
        assert rec["clause"], outcome
        # The clause must be a substring of the captured text -- a summary or
        # a paraphrase is not the source.
        assert rec["clause"].rstrip(".;") in doc["provenance"]["raw_text"]
        assert rec["matched_triggers"], outcome


def test_a_leg_built_from_prose_carries_the_provenance_onto_the_leg():
    held, doc = read(CANCEL_HALF)
    assert held.settlement_provenance["content_sha256"] == (
        doc["provenance"]["content_sha256"])
    assert held.rule_for(SC.CANCELLED)["clause"]
    assert held.rule_for("NOT_AN_OUTCOME") is None


def test_requantifying_a_leg_preserves_its_settlement_reading():
    """The copy helper enumerated ten fields and silently dropped the rest."""
    held, _ = read(CANCEL_HALF)
    bigger = IS._with_quantity(held, 40)
    assert bigger.quantity == 40
    assert bigger.settlement_rules is not None
    assert IS._leg_payout_cents(bigger, VOID) == 50, (
        "requantification must not turn an established rule back into unknown")


# ═════════════════════════════════════════════════════════════════════
# THE GUARD AT THE POINT OF USE, INDEPENDENT OF WHO FILLED THE FIELD
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text", [
    DEFECT_PROSE,
    "A tie resolves 50-50.",
    "The market resolves 50-50 in the event of a dead heat.",
    "This market includes any extra innings played.",
])
def test_text_that_does_not_name_a_cancellation_cannot_price_the_void_cell(text):
    """Whoever put the string in the field, it must be about the outcome."""
    held = leg(void_rule=text, settlement_text_captured=True)
    assert IS._leg_payout_cents(held, VOID) is None, text


@pytest.mark.parametrize("text", [
    "If the game is cancelled the market resolves 50-50.",
    "A voided market resolves 50-50.",
    "If the game is abandoned the market resolves 50-50.",
])
def test_text_that_does_name_a_cancellation_still_prices_the_void_cell(text):
    held = leg(void_rule=text, settlement_text_captured=True)
    assert IS._leg_payout_cents(held, VOID) == 50, text


def test_a_cancellation_clause_in_the_tie_field_does_not_price_the_tie_cell():
    """The guard runs in both directions, not just the one that was reported."""
    held = leg(tie_rule="If the game is cancelled the market resolves 50-50.",
               settlement_text_captured=True)
    assert IS._leg_payout_cents(held, TIE) is None


def test_the_structured_reading_wins_over_a_legacy_string_that_disagrees():
    """Once an outcome has been read, a blob cannot answer for it again."""
    doc = SC.interpret(DEFECT_PROSE)          # cancellation NOT stated
    held = leg(settlement_rules=doc["rules"], settlement_text_captured=True,
               void_rule="void: 50-50")       # a legacy string that would pay
    assert IS._leg_payout_cents(held, VOID) is None, (
        "the clause reader declined this outcome; falling back to the string "
        "field would let the blob answer a question just refused")


def test_uncaptured_text_establishes_nothing_even_if_a_field_is_populated():
    held = leg(void_rule="If cancelled the market resolves 50-50.",
               settlement_text_captured=False)
    assert IS._leg_payout_cents(held, VOID) is None


# ═════════════════════════════════════════════════════════════════════
# THE PUSH REGION, WHICH THE CLASSIFIER PREVIOUSLY COULD NOT REPRESENT
# ═════════════════════════════════════════════════════════════════════

PUSH_REFUND = ("If the margin lands exactly on the line the bet is a push and "
               "stakes are refunded.")


def test_an_integer_spread_is_refused_while_its_push_rule_is_unread():
    held, _ = read(DEFECT_PROSE, kind=IS.KIND_SPREAD, line=Fraction(-7, 1),
                   backs="A")
    gaps = held.missing_facts()
    assert any("land exactly on the line" in g for g in gaps), gaps
    assert any(SC.R_NOT_STATED in g for g in gaps), gaps


def test_an_integer_spread_grades_once_its_push_rule_is_read():
    held, _ = read(DEFECT_PROSE + " " + PUSH_REFUND, kind=IS.KIND_SPREAD,
                   line=Fraction(-7, 1), backs="A", cost_cents_per_unit=44)
    assert held.missing_facts() == []
    # Margin exactly 7: the A side does not win and the B side does not either.
    exact = IS.Region("margin = 7", lo=7, hi=7)
    assert IS._leg_payout_cents(held, exact) == 44, (
        "the push refunds the basis, which on a contract bought at 44c is 44c")


def test_a_push_pays_both_sides_of_the_same_integer_line_their_own_basis():
    doc = SC.interpret(DEFECT_PROSE + " " + PUSH_REFUND)
    a = leg(condition_id="a", kind=IS.KIND_SPREAD, line=Fraction(-7, 1),
            backs="A", cost_cents_per_unit=60, settlement_text_captured=True,
            settlement_rules=doc["rules"])
    b = leg(condition_id="b", kind=IS.KIND_SPREAD, line=Fraction(-7, 1),
            backs="B", cost_cents_per_unit=40, settlement_text_captured=True,
            settlement_rules=doc["rules"])
    exact = IS.Region("margin = 7", lo=7, hi=7)
    assert IS._leg_payout_cents(a, exact) == 60
    assert IS._leg_payout_cents(b, exact) == 40, (
        "a push is not a B-side win; each side is refunded what it paid")
