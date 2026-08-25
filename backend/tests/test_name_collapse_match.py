"""An apostrophe was splitting one name into two spellings.

_norm turns punctuation into spaces, so the venue's
"Christopher O'Connell" becomes "christopher o connell" while the whale
feed's "Christopher OConnell" becomes "christopher oconnell". Exact
equality fails on a difference that is not a difference.

Measured, not guessed. The unmapped census attributed 400 sampled rows:

    no_key_intersection          207  (51.8%)
    resolves                     101  (25.3%)
    no_side_match                 47  (11.8%)
    type_prefix_filter_emptied    45  (11.3%)

and the first no_side_match example it printed was exactly that pair:
outcome 'Christopher OConnell' against sides ['christopher o connell',
'stefanos sakellaridis'].

The precision rules in match_side are each a shipped incident, so this
is added the narrow way: only after exact equality has found nothing,
and still requiring a UNIQUE hit. Ambiguity refuses, as it does
everywhere else in that function.
"""

from sportsassets.workers.premap import match_side


def _row(side, ident, line=None, signed=None):
    return {"side_norm": side, "identifier": ident, "line": line,
            "signed": signed, "intent": "ORDER_INTENT_BUY_LONG",
            "question": "q", "event_title": "e", "kind": "moneyline"}


TENNIS = [_row("christopher o connell", "A"),
          _row("stefanos sakellaridis", "B")]


class TestTheRealCase:
    def test_the_census_example_now_matches(self):
        assert match_side(TENNIS, "Christopher OConnell", "t")["identifier"] \
            == "A"

    def test_it_works_in_the_other_direction_too(self):
        rows = [_row("christopheroconnell", "A"), _row("someone else", "B")]
        assert match_side(rows, "Christopher O'Connell", "t")["identifier"] \
            == "A"

    def test_the_other_side_is_not_dragged_in(self):
        assert match_side(TENNIS, "Stefanos Sakellaridis", "t")["identifier"] \
            == "B"


class TestItStillRefusesEverythingItShould:
    def test_a_name_on_neither_side_refuses(self):
        assert match_side(TENNIS, "Roger Federer", "t") is None

    def test_collapsed_ambiguity_refuses_rather_than_picking(self):
        """Two sides collapsing to the same string is exactly when a
        wrong-side fill would happen. len() != 1 refuses."""
        amb = [_row("jo nathan", "X"), _row("j onathan", "Y")]
        assert match_side(amb, "Jonathan", "t") is None

    def test_exact_equality_still_wins_first(self):
        """The collapsed pass must never override an exact hit."""
        rows = [_row("ab", "EXACT"), _row("a b", "COLLAPSED")]
        assert match_side(rows, "AB", "t")["identifier"] == "EXACT"

    def test_a_yes_no_pick_is_untouched(self):
        """Yes/No matching only literal yes/no sides is the 2026-08-24
        inversion incident. Nothing here may loosen it."""
        assert match_side(TENNIS, "Yes", "t") is None
        assert match_side(TENNIS, "No", "t") is None

    def test_an_empty_outcome_still_refuses(self):
        assert match_side(TENNIS, "", "t") is None
        assert match_side(TENNIS, None, "t") is None


class TestLineDisciplineSurvives:
    def test_a_lined_row_still_refuses_an_unlined_pick(self):
        rows = [_row("kansas city chiefs", "L", line="3.5")]
        assert match_side(rows, "Kansas City Chiefs", "no line here") is None

    def test_a_collapsed_match_still_passes_through_the_line_check(self):
        """The collapse only changes NAME comparison. A row whose line
        disagrees must still be refused."""
        rows = [_row("patrick o brien", "L", line="9.5")]
        assert match_side(rows, "Patrick OBrien", "Over 2.5") is None
