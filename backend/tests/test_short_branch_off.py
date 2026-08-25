"""The BUY_SHORT entry branch is refused, on a controlled comparison.

31 real fills in the 24h to 2026-08-25:

    BYINTENT BUY_LONG   n=25  over=0  clean=25
    BYINTENT BUY_SHORT  n=6   over=6  clean=0

Perfect separation. And the obvious confound — "0x076daa87's markets
are just bad" — is ruled out WITHIN that one book, on the same day, the
same venue and the same aec- family:

    BUY_SHORT  1086sh @0.23 -> 0.89     ratio 3.87
    BUY_SHORT  1136sh @0.22 -> 0.78     ratio 3.545
    BUY_SHORT   781sh @0.32 -> 0.6853   ratio 2.142
    BUY_LONG   6250sh @0.04 -> 0.04     ratio 1.0
    BUY_LONG   4166sh @0.06 -> 0.06     ratio 1.0
    BUY_LONG    423sh @0.59 -> 0.59     ratio 1.0

Same whale, same everything. The only variable that separates broken
from clean is the intent.

Every short we have ever filled has been wrong. This refuses the branch
— which costs a class of copy we have never once executed correctly and
keeps the 25 that work. It does NOT claim to know WHY, and it is not
the same as inverting the intent: that one-line flip is what caused the
original incident, and it waits for the SIDE verdict.
"""

import inspect

from sportsassets import live_executor as le


class TestTheGateExists:
    def test_the_short_branch_is_refused_in_the_money_path(self):
        src = inspect.getsource(le.maybe_execute)
        assert "short-branch-refused" in src

    def test_it_refuses_before_the_order_is_sent(self):
        src = inspect.getsource(le.maybe_execute)
        assert src.index("short-branch-refused") < src.index(
            "pmus.submit_fok")

    def test_it_is_reopenable_by_env_when_the_cause_is_known(self):
        src = inspect.getsource(le.maybe_execute)
        assert "LIVE_ALLOW_SHORT" in src


class TestItDoesNotStrandOpenShorts:
    """We already HOLD short positions bought before this. Refusing new
    short ENTRIES must not also block the exits that close them, or the
    guard converts a pricing bug into unsellable inventory."""

    def test_the_exit_path_still_derives_a_short_close(self):
        from sportsassets import pmus

        src = inspect.getsource(pmus)
        assert "_exit_intent" in src

    def test_the_refusal_lives_in_the_entry_path_only(self):
        """The gate sits in maybe_execute, which is entries. The exit
        sweep calls submit_fok(sell=True) on its own path."""
        assert "short-branch-refused" not in inspect.getsource(
            le.execute_manual)


class TestTheIntentIsNotInverted:
    """DO NOT FLIP THE INTENT. This is the refutation, on the record.

    The leading theory for hours was that side_intent named the wrong
    leg — that BUY_SHORT was buying the opposite player. The venue's
    own settled data refutes it:

        whale pick   Stefano Travaglia
        venue sides  Harry Wendelken long=true
                     Stefano Travaglia long=false   <- the pick
        we sent      ORDER_INTENT_BUY_SHORT         <- correct for him

    We named the side CORRECTLY and were charged 0.89 for a side the
    whale paid ~0.22 for. Right side, wrong price.

    So the eventual fix is a PRICE fix on the short leg, not an intent
    flip. Had the flip shipped on the theory, every short would have
    bought the wrong player at a plausible-looking price — the original
    incident, reintroduced by the attempt to fix it.

    These assertions have no runtime effect. They exist so the next
    person to reach for the one-line inversion finds the evidence
    against it first.
    """

    # (whale_pick_is_long, intent_we_sent) from the 2026-08-24 receipts
    HARWEN_STETRA = (False, "ORDER_INTENT_BUY_SHORT")

    def test_the_intent_matched_the_picked_side(self):
        pick_is_long, sent = self.HARWEN_STETRA
        expected = ("ORDER_INTENT_BUY_LONG" if pick_is_long
                    else "ORDER_INTENT_BUY_SHORT")
        assert sent == expected, (
            "the venue's own side flags say we named the right leg — "
            "the overspend is a PRICE fault, not an inverted intent")

    def test_the_refusal_does_not_flip_anything(self):
        """The shipped guard REFUSES the branch. It must not quietly
        rewrite the intent, which would be the flip by another name."""
        src = inspect.getsource(le.maybe_execute)
        head = src[:src.index("short-branch-refused")]
        assert 'intent = "ORDER_INTENT_BUY_LONG"' not in head
        assert "_intent = _intent.replace" not in src


class TestTheEvidenceIsRecorded:
    """The counts are the justification. Pin them so a future reader
    cannot mistake this for a hunch, and so re-opening the branch has
    to argue with the numbers."""

    def test_the_comment_carries_the_separation(self):
        src = inspect.getsource(le.maybe_execute)
        assert "n=25" in src and "over=0" in src
        assert "n=6" in src and "over=6" in src

    def test_the_ratios_are_named(self):
        src = inspect.getsource(le.maybe_execute)
        for r in ("3.87", "3.545", "2.142"):
            assert r in src, f"ratio {r} must stay on the record"

    def test_it_does_not_claim_to_know_the_cause(self):
        """An honest guard states its own limits; this one is a stop,
        not a diagnosis."""
        src = inspect.getsource(le.maybe_execute)
        assert "does NOT diagnose WHY" in src or "not diagnose" in src
