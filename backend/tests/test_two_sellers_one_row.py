"""Two sellers now compete for the same rows. Exactly one may act.

The owner's screenshot settled something I had wrong: our automated
trader IS selling, and correctly. bettortoken1's history shows real
realised gains inside 45 minutes — Sold Braden Shick +$230.73, Sold
Benito Sanchez Martinez +$307.50 and +$299.54, Sold Cezar Cretu
+$136.06 — all from _copy_exit_sweep, our own +20% take-profit rule.

Today I added a SECOND seller: mirror_exit, which sells when the WHALE
exits. Both select `status = 'filled'`.

The race is not hypothetical. _copy_exit_sweep reads its entire row set
up front and then loops, so mirror_exit can claim and sell a position
between that read and the sweep's submit. Selling a position we no
longer hold does not fail on this venue — netPosition is SIGNED, so it
OPENS A SHORT. That is the worst available outcome: an unintended
position, on the leg we just decided to leave, with no gate expecting
it.

So both sellers take the same atomic claim, and every path that does
not end in a sale gives it back. A row stranded in 'exiting' is
invisible to the settlement sweep, which targets 'filled'.
"""

import inspect

from sportsassets.workers import underdog


class TestTheSweepClaimsBeforeSelling:
    def test_it_claims_atomically(self):
        src = inspect.getsource(underdog._copy_exit_sweep)
        assert "SET status='exiting'" in src
        assert "AND status='filled' RETURNING id" in src

    def test_the_claim_precedes_the_order(self):
        src = inspect.getsource(underdog._copy_exit_sweep)
        assert src.index("RETURNING id") < src.index("pmus.submit_fok")

    def test_a_lost_claim_skips_the_row_rather_than_selling(self):
        src = inspect.getsource(underdog._copy_exit_sweep)
        i = src.index("claimed = await pool.fetchval")
        assert "if claimed is None:" in src[i:i + 400]
        assert "continue" in src[i:i + 500]


class TestNoPathStrandsAPosition:
    def test_an_exception_releases_the_claim(self):
        src = inspect.getsource(underdog._copy_exit_sweep)
        blk = src[src.index("except Exception as exc"):]
        assert "_release_claim" in blk[:400]

    def test_an_unfilled_sale_releases_the_claim(self):
        """The position is still ours. Leaving it 'exiting' would retire
        a live position from the sweep that grades it."""
        src = inspect.getsource(underdog._copy_exit_sweep)
        assert "else:" in src
        blk = src[src.index("stats[\"copyexit_cashed\"] += 1"):]
        assert "_release_claim" in blk

    def test_the_release_only_touches_rows_it_claimed(self):
        """`AND status='exiting'` — otherwise a release could resurrect
        a row another path has already cashed out."""
        src = inspect.getsource(underdog._release_claim)
        assert "WHERE id=$1 AND status='exiting'" in src

    def test_the_release_never_raises(self):
        src = inspect.getsource(underdog._release_claim)
        assert "except Exception" in src


class TestBothSellersUseTheSameProtocol:
    def test_mirror_exit_claims_the_same_way(self):
        from sportsassets import live_executor as le

        a = inspect.getsource(le.mirror_exit)
        b = inspect.getsource(underdog._copy_exit_sweep)
        for s in ("SET status='exiting'", "AND status='filled' RETURNING id"):
            assert s in a and s in b, (
                f"{s!r} must be identical in both sellers — two "
                f"different claim protocols is the same race again")

    def test_neither_seller_can_see_a_claimed_row(self):
        """'exiting' is absent from both selectors, so a claimed row is
        invisible to the other seller by construction rather than by
        timing."""
        from sportsassets import live_executor as le

        for src in (inspect.getsource(le.mirror_exit),
                    inspect.getsource(underdog._copy_exit_sweep)):
            sel = src[:src.index("status='exiting'")]
            assert "status = 'filled'" in sel or "status='filled'" in sel
