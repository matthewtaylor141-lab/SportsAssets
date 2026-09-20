"""The retraction of 2026-09-20, pinned so it cannot quietly come back.

Owner directive, "DO NOT STOP THE BETTOR ARCHITECTURE":

    §1 "Do not state: STRATEGY CLASS A = FALSIFIED or: STRATEGY CLASS B
       = FALSIFIED from the current maker measurement. The measured
       adverse-selection term: -0.0140/share is CONDITIONAL ON RN1
       CHOOSING TO LIFT THE RESTING OFFER. ... Preserve that scope."

    §2 "DO NOT COMBINE DIFFERENT POPULATIONS INTO A 'BASE CASE EV'
       ... Those are different: VENUES POPULATIONS TIME WINDOWS FLOW
       SELECTION REGIMES."

What actually happened: a PMUS gross half-spread (+0.0050) was
subtracted against a Polymarket-CLOB adverse-selection term (-0.0140)
measured on rows that exist only because RN1 traded them, and the
difference was put in a headline as BETTOR's maker EV. Both mismatches
were named as limitations in the same document. Naming a limitation
does not license the claim.

The failure mode is not a typo and it is not caught by review, because
each input is individually correct and correctly cited. It is caught by
asking, of every arithmetic combination, whether the terms describe the
same population. That question is what these tests ask.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERDICT = ROOT / "research" / "beta48" / "SPRINT_ECONOMIC_VERDICT.md"
FINDINGS = ROOT / "research" / "beta48" / "MAKER_FIRST_FINDINGS.md"

# The scope statement MAKER_FIRST_FINDINGS.md makes about its own
# result. The owner directed that it be preserved; it is the sentence
# that makes the RN1 number a statement about one flow regime rather
# than about market making.
SCOPE = ("Nothing here says market making is unprofitable in general; "
         "it says")
SCOPE_TAIL = "making a market to this particular informed flow, at the touch"

RETRACTED = ("BASE_CASE_EV", "STRATEGY CLASS A = FALSIFIED",
             "STRATEGY CLASS B = FALSIFIED")


def _verdict():
    return VERDICT.read_text(encoding="utf-8")


def _normalised(text):
    """Collapse the line wrapping so a sentence is findable across a
    hard break."""
    return re.sub(r"\s+", " ", text)


# ── the findings document keeps its own scope ────────────────────────

def test_the_findings_document_still_states_its_own_scope():
    t = _normalised(FINDINGS.read_text(encoding="utf-8"))
    assert SCOPE in t
    assert SCOPE_TAIL in t


def test_the_verdict_quotes_that_scope_rather_than_paraphrasing_it():
    t = _normalised(_verdict())
    assert SCOPE in t, "the scope statement must be carried, verbatim"
    assert SCOPE_TAIL in t


# ── the three retracted statements ───────────────────────────────────

def test_each_retracted_statement_appears_only_inside_the_retraction():
    """Present, marked RETRACTED, and nowhere else. Deleting them would
    hide that the claim was ever made; leaving one loose would let it
    be quoted as current."""
    lines = _verdict().splitlines()
    for claim in RETRACTED:
        hits = [ln for ln in lines if claim in ln]
        assert hits, f"{claim} must remain on record as retracted"
        for ln in hits:
            assert "RETRACTED" in ln, f"loose claim: {ln!r}"


def test_the_combined_number_is_not_asserted_anywhere():
    t = _verdict()
    for ln in t.splitlines():
        if "-0.0059" in ln:
            assert "RETRACTED" in ln, f"combined figure asserted: {ln!r}"


def test_the_two_terms_are_recorded_as_different_populations():
    t = _normalised(_verdict())
    assert "PMUS" in t and "Polymarket global CLOB" in t
    assert "because RN1 traded them" in t


# ── the classification the owner specified ───────────────────────────

def test_the_current_pmus_classification_is_present_and_unresolved():
    t = _verdict()
    assert "CURRENT_PMUS_GROSS_MAKER_SPREAD" in t
    for unresolved in ("CURRENT_PMUS_UNCONDITIONAL_ADVERSE_SELECTION",
                       "CURRENT_PMUS_P_FILL",
                       "CURRENT_PMUS_MAKER_NET_EV"):
        line = next(ln for ln in t.splitlines() if unresolved in ln)
        assert "NOT_IDENTIFIED" in line, line


def test_the_rn1_terms_are_labelled_selected_historical_flow():
    t = _normalised(_verdict())
    assert "RN1_CONDITIONAL_ADVERSE_SELECTION" in t
    assert "RN1_CONDITIONAL_MAKER_NET_BEFORE_REBATE" in t
    assert "MEASURED_HISTORICAL_SELECTED_FLOW" in t
    assert "SELECTION_BIAS = SEVERE" in t


def test_classes_a_and_b_are_open_not_falsified():
    t = _normalised(_verdict())
    assert "INSUFFICIENT_EVIDENCE" in t


# ── what may still be called falsified, and on what ──────────────────

def test_the_surviving_falsifications_are_single_population():
    """Classes C and D are falsified on evidence drawn from one venue
    and one population each -- which is precisely why they survive the
    correction that removed A and B."""
    t = _normalised(_verdict())
    assert "Class C" in t and "FALSIFIED" in t
    assert "Class D" in t
    assert "1,170" in t          # D's own holdout population
    assert "3,732" in t          # C's own observation count


# ── the engine does not carry a cross-venue constant ─────────────────

def test_the_maker_engine_sources_adverse_selection_from_own_fills():
    """The one place the retracted arithmetic could become permanent is
    a default value baked into the component table."""
    from sportsassets import bettor_maker_engine as me
    src = Path(me.__file__).read_text(encoding="utf-8")
    assert "MARKOUTS_AFTER_SUPPORTED_FILL" in src
    assert "-0.0140" not in src
    assert "0.0140" not in src


def test_an_unsupplied_adverse_selection_stays_unidentified():
    from sportsassets import bettor_maker_engine as me
    for action in ("MAKE_YES", "MAKE_NO", "MAKE_BOTH"):
        table = me.components(action)
        row = next(c for c in table["components"]
                   if c["component"] == "ADVERSE_SELECTION")
        assert row["status"] != "IDENTIFIED", action
        assert "ADVERSE_SELECTION" in table["componentsMissing"], action
