"""Half of every spread pick was refused for stating the other team's sign.

A spread title names ONE team's handicap: "Spread: Doncaster (-1.5)".
match_side took that sign as the whale's whenever his outcome text
carried none:

    his_signed = signed_line(outcome) or signed_line(his_title)

So a bare "Middlesbrough" pick borrowed Doncaster's -1.5, mismatched
Middlesbrough's own +1.5 row, and refused. Reproduced against the live
matcher before the fix: "Doncaster" resolved, "Middlesbrough" returned
None — for the same market, the same title, the same rows.

THIS IS THE MOST DANGEROUS CHANGE OF THE DAY, because the sign guard
is the 2026-08-24 inversion protection: _norm erases +/-, so
"Chiefs -3.5" and "Chiefs +3.5" normalize identically and only the sign
separates giving points from getting them. So the stated-sign path is
UNCHANGED and every inversion case is pinned below. What changes is the
UNSTATED case, which previously compared every row against an empty
string and refused all of them — a guard blocking everything, which is
an outage wearing a guard's uniform.

The safety net when the sign is unstated is the uniqueness rule that
was already there on every branch of the named path: len(...) == 1, or
refuse. A team listed at two handicaps returns nothing rather than a
coin flip.
"""

import pytest

from sportsassets.workers import premap as p

TITLE = "Spread: Doncaster (-1.5)"
SLUG = "efl-don-mid-2026-08-25-spread-away-1pt5"


def _rows(*specs):
    return [{"identifier": i, "side_norm": p._norm(d), "line": l,
             "signed": p.signed_line(d), "kind": "side",
             "question": "Spread"} for i, d, l in specs]


GAME = _rows(("asc-mid", "Middlesbrough +1.5", "1.5"),
             ("asc-don", "Doncaster -1.5", "1.5"))


def _m(pick, rows=GAME, title=TITLE, slug=SLUG):
    hit = p.match_side(rows, pick, title, slug)
    return hit["identifier"] if hit else None


class TestTheRefusalThatShouldNotHaveHappened:
    def test_the_team_NOT_named_in_the_title_now_matches(self):
        assert _m("Middlesbrough") == "asc-mid"

    def test_the_team_named_in_the_title_still_matches(self):
        assert _m("Doncaster") == "asc-don"

    def test_both_sides_of_one_market_are_now_reachable(self):
        assert {_m("Middlesbrough"), _m("Doncaster")} == {"asc-mid",
                                                          "asc-don"}


class TestTheInversionGuardIsUNTOUCHED:
    """Every one of these is a whale who STATED a sign. That path did
    not change and must not."""

    def test_a_stated_sign_that_matches_resolves(self):
        assert _m("Middlesbrough +1.5") == "asc-mid"
        assert _m("Doncaster -1.5") == "asc-don"

    def test_a_stated_sign_that_is_WRONG_refuses(self):
        """He asked for Middlesbrough -1.5; the venue lists
        Middlesbrough only at +1.5. That is the opposite bet."""
        assert _m("Middlesbrough -1.5") is None
        assert _m("Doncaster +1.5") is None

    def test_the_2026_08_24_incident_case_still_refuses(self):
        kc = _rows(("asc-kc", "Kansas City Chiefs -3.5", "3.5"))
        assert p.match_side(kc, "Kansas City Chiefs +3.5",
                            "Spread: KC (-3.5)",
                            "nfl-kc-buf-2026-08-25-spread-away-3pt5") is None

    def test_a_stated_sign_against_a_row_with_NO_sign_refuses(self):
        rows = [{"identifier": "x", "side_norm": p._norm("Chiefs -3.5"),
                 "line": "3.5", "signed": "", "kind": "side",
                 "question": "Spread"}]
        assert p.match_side(rows, "Chiefs -3.5", "Spread",
                            "nfl-kc-buf-2026-08-25-spread-away-3pt5") is None


class TestAmbiguityStillRefuses:
    """The safety net for an unstated sign is the uniqueness rule that
    was already on every branch."""

    def test_a_team_listed_at_two_handicaps_returns_nothing(self):
        alt = _rows(("a", "Chiefs -3.5", "3.5"), ("b", "Chiefs +3.5", "3.5"))
        assert p.match_side(alt, "Chiefs", "Spread",
                            "nfl-kc-buf-2026-08-25-spread-away-3pt5") is None

    def test_the_magnitude_must_still_agree(self):
        far = _rows(("asc-mid", "Middlesbrough +9.5", "9.5"))
        assert p.match_side(far, "Middlesbrough", TITLE, SLUG) is None

    def test_an_unlined_row_still_refuses_a_lined_pick(self):
        bare = _rows(("asc-mid", "Middlesbrough", ""))
        assert p.match_side(bare, "Middlesbrough", TITLE, SLUG) is None


class TestWhoseSignIsIt:
    def test_a_title_about_HIS_team_lends_its_sign(self):
        assert p._title_sign_is_his("Spread: Doncaster (-1.5)",
                                    "Doncaster") is True

    def test_a_title_about_the_OTHER_team_does_not(self):
        assert p._title_sign_is_his("Spread: Doncaster (-1.5)",
                                    "Middlesbrough") is False

    def test_a_two_subject_title_lends_nothing(self):
        """'Doncaster vs Middlesbrough (-1.5)' names two teams and the
        sign belongs to one of them. There is no way to tell which, so
        it is treated as unstated rather than guessed."""
        assert p._title_sign_is_his(
            "Doncaster vs Middlesbrough (-1.5)", "Doncaster") is False

    def test_missing_inputs_lend_nothing(self):
        assert p._title_sign_is_his(None, "Doncaster") is False
        assert p._title_sign_is_his("Spread: Doncaster (-1.5)", None) is False
        assert p._title_sign_is_his("", "") is False

    def test_punctuation_does_not_break_the_subject_test(self):
        assert p._title_sign_is_his("Spread: Inter Miami C.F. (-1.5)",
                                    "Inter Miami CF") is True


class TestTheYesNoBranchIsDeliberatelyUntouched:
    """On a lined Yes/No market the line and its sign describe THE
    MARKET, not a side — Yes and No share them. Only the named branch
    had a per-side sign to misattribute."""

    def test_the_yes_no_sign_source_is_unchanged(self):
        import inspect

        src = inspect.getsource(p.match_side)
        assert ("his_signed_yn = signed_line(outcome) or "
                "signed_line(his_title)") in src
