"""The roster was inverted, and the basis that inverted it was blind.

Every prior roster decision graded at RESOLUTION. That basis cannot see
a merge, and these whales close by merging — so the numbers behind the
cuts could not see the exits that decide their P&L.

Re-graded over full ledgers (no truncation), merges counted as exits:

    rn1                   +$222,038 on $23.9M entries  (+0.93%)  WAS CUT
    ferrarichampions2026  +$217,159 on $12.8M entries  (+1.69%)  WAS CUT
    0x076daa87             +$43,897 on $2.9M entries   (+1.53%)  kept
    homerunhazard          -$35,363 on $27.4M entries  (-0.13%)  kept
    swisstony             -$187,613 on $23.0M entries  (-0.82%)  NOW CUT
    0x2c33...           -$1,910,412 on $47.4M entries  (-4.03%)  stays cut

We had cut the two best books and were copying the second-worst. The
settlement basis said the opposite of every one of those lines.

Owner granted the change 2026-08-25.
"""

from sportsassets import live_executor as le

RESTORED = ("rn1", "ferrarichampions2026")
# HomeRunHazard moved from KEPT to CUT on 2026-08-25 (owner order),
# on the merge-inclusive re-grade: -0.14% on $27.56M of entries over
# 46,905 closed lots. The three that remain are +2.05% (0x076daa87),
# +1.66% (ferrari) and +0.94% (rn1). His earlier case — baseball
# totals +1.89%, WNBA +6.50% — was a SETTLEMENT-basis read taken
# before merges were priced as the exits they are.
CUT = ("swisstony", le._W2C33, "homerunhazard")
KEPT = ("0x076daa87",)


class TestTheClipMapMatchesTheDecision:
    def test_the_two_best_books_can_spend_again(self):
        for w in RESTORED:
            assert le.PER_FILL_BY_WHALE[w] > 0, (
                f"{w} is the reason this change exists")

    def test_the_losers_are_blocked(self):
        for w in CUT:
            assert le.PER_FILL_BY_WHALE[w] == 0.0

    def test_the_kept_whales_are_untouched(self):
        for w in KEPT:
            assert le.PER_FILL_BY_WHALE[w] == 250.00

    def test_a_blocked_clip_really_blocks(self):
        """per_fill_usd returning 0 is the cut mechanism — the caller
        skips on 0. If a multiplier could lift a blocked cell off zero
        the block would be decorative."""
        assert le.per_fill_usd("swisstony") == 0.0
        assert le.per_fill_usd(le._W2C33) == 0.0

    def test_no_clip_exceeds_the_owners_ceiling(self):
        for w, v in le.PER_FILL_BY_WHALE.items():
            assert v <= le.LIVE_MAX_CLIP_USD, w


class TestTheTwoListsCannotDiverge:
    """This is the 2026-08-24 bug, pinned so it cannot recur.

    SwissTony was reported "certified and resumed" while a THIRD
    allowlist still excluded him. He placed 2,897 rejections and zero
    orders, and the discrepancy was invisible because each list looked
    correct on its own.

    VERIFIED_PROFITABLE_DEFAULT gates the premap-live lane AND
    mirror_exit; PER_FILL_BY_WHALE gates the money. A whale in one and
    not the other either enters and cannot be followed out, or is
    followed out of a position it could never take.
    """

    def _verified(self):
        return {w.strip() for w in
                le.VERIFIED_PROFITABLE_DEFAULT.lower().split(",") if w.strip()}

    def test_every_spending_whale_is_verified(self):
        spenders = {w for w, v in le.PER_FILL_BY_WHALE.items() if v > 0}
        missing = spenders - self._verified() - {"kch123"}
        assert not missing, (
            f"{missing} can spend but is not in the verified set — it "
            f"would enter positions mirror_exit can never close")

    def test_no_blocked_whale_is_verified(self):
        blocked = {w for w, v in le.PER_FILL_BY_WHALE.items() if v == 0}
        assert not (blocked & self._verified()), (
            "a blocked whale in the verified set reads as live to every "
            "reader of that list")

    def test_both_restored_whales_are_in_both(self):
        for w in RESTORED:
            assert le.PER_FILL_BY_WHALE.get(w, 0) > 0
            assert w in self._verified()

    def test_swisstony_is_in_neither(self):
        assert le.PER_FILL_BY_WHALE["swisstony"] == 0.0
        assert "swisstony" not in self._verified()


class TestTheEvidenceIsOnTheRecord:
    """The numbers live beside the map so a future reversal has to argue
    with them rather than with a preference."""

    def test_the_regrade_is_quoted(self):
        import inspect

        src = inspect.getsource(le)
        for fig in ("+$222,038", "+$217,159", "-$187,613", "-$1,910,412"):
            assert fig in src, f"{fig} must stay on the record"

    def test_the_limits_are_stated_too(self):
        """Realised P&L with large open positions is not proof, and ROI
        on entries flatters a high-turnover book. Saying so beside the
        decision is what keeps it revisable."""
        import inspect

        src = inspect.getsource(le)
        assert "WHAT THIS IS NOT: proof" in src
        assert "open positions" in src


class TestAllTHREEGatesAgree:
    """The clip map, the verified set, and COPY_CUT_WHALES are three
    literals encoding ONE decision.

    On 2026-08-24 SwissTony was resumed in two of them and refused by
    the third; he was reported live and placed 2,897 rejections with $0
    deployed. Today the first two were updated and COPY_CUT_WHALES
    still refused rn1 and ferrari at entry — the same shape, caught
    only because the suite pins them against each other.
    """

    def _verified(self):
        return {w.strip() for w in
                le.VERIFIED_PROFITABLE_DEFAULT.lower().split(",") if w.strip()}

    def test_no_spending_whale_is_also_cut(self):
        spenders = {w for w, v in le.PER_FILL_BY_WHALE.items() if v > 0}
        clash = spenders & {w.lower() for w in le.COPY_CUT_WHALES}
        assert not clash, (
            f"{clash} has a live clip AND sits in COPY_CUT_WHALES — it "
            f"will be refused at entry while every other gate reads it "
            f"as live, which is the 2,897-rejection failure exactly")

    def test_no_verified_whale_is_also_cut(self):
        clash = self._verified() & {w.lower() for w in le.COPY_CUT_WHALES}
        assert not clash, clash

    def test_the_restored_whales_pass_all_three(self):
        for w in RESTORED:
            assert le.PER_FILL_BY_WHALE.get(w, 0) > 0, w
            assert w in self._verified(), w
            assert w.lower() not in {
                c.lower() for c in le.COPY_CUT_WHALES}, w

    def test_the_cut_whale_fails_all_three(self):
        assert le.PER_FILL_BY_WHALE["swisstony"] == 0.0
        assert "swisstony" not in self._verified()
        assert "swisstony" in {c.lower() for c in le.COPY_CUT_WHALES}

    def test_the_worst_book_stays_cut_in_all_three(self):
        w = le._W2C33.lower()
        assert le.PER_FILL_BY_WHALE[le._W2C33] == 0.0
        assert w not in self._verified()
        assert w in {c.lower() for c in le.COPY_CUT_WHALES}
