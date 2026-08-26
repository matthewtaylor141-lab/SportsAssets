"""The slippage column compared two different legs.

api_edge_decay reports avg_slip per whale, and the owner reads it as
"how much worse than the whale's price did we fill". It was computed as
a raw `fill_price - his_price`.

fill_price on a SHORT names the LONG leg -- that is the venue's
convention, and it is why realized_pnl, fill_cash and price_fidelity all
carry intent. his_price is what the whale actually paid on his own side.
So for a short copy the subtraction compared two different legs and
returned roughly -(1 - 2*his): about -0.20 on a mid-priced book.

The production table showed exactly that shape:

    rn1        avg_slip=-0.195
    ferrari    avg_slip=-0.246
    0x076daa87 avg_slip=-0.126

Read literally that says we filled twelve to twenty-five cents per share
BETTER than the whales we are copying, on every whale, which is not a
thing that happens when the order is a FOK at his price plus two cents.
It is the complement signature.

cost_per_share is the single definition every other caller was moved
onto for this same reason.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets.api import app as A
from sportsassets.live_executor import cost_per_share, short_model_confirmed


def _slip(fp, his, intent):
    return cost_per_share(fp, intent) - his


class TestTheDenominationIsTheDefect:
    def test_a_long_is_unchanged(self):
        """Longs were always right, and must stay right."""
        assert _slip(0.62, 0.60, None) == pytest.approx(0.02)
        assert _slip(0.58, 0.60, None) == pytest.approx(-0.02)

    @pytest.mark.skipif(not short_model_confirmed(),
                        reason="short math is gated on the venue model")
    def test_a_short_is_no_longer_compared_against_the_other_leg(self):
        """He paid 0.35 on his side. Our short fills at a venue price of
        0.63, meaning our cost is 0.37 -- a 2c overpay, not a 28c gain."""
        assert _slip(0.63, 0.35, "ORDER_INTENT_BUY_SHORT") == \
            pytest.approx(0.02, abs=1e-9)

    @pytest.mark.skipif(not short_model_confirmed(),
                        reason="short math is gated on the venue model")
    def test_short_leg_mixing_does_NOT_explain_the_reported_negative(self):
        """Recorded because I nearly shipped the opposite claim.

        For a short filled at exactly the whale's price the raw formula
        returns +(1 - 2*his), which is POSITIVE on any book under 50c.
        The production column reads -0.126 to -0.246. The sign is wrong
        for that theory, so the denomination bug -- which is real and is
        fixed -- is NOT the cause of the negative reading.
        """
        for his in (0.35, 0.40, 0.44):
            fp = 1.0 - his          # a short filled at exactly his price
            raw = fp - his
            assert raw == pytest.approx(1 - 2 * his)
            assert raw > 0, "the old formula is POSITIVE here, not negative"
            # Corrected, the same fill is zero slippage.
            assert _slip(fp, his, "ORDER_INTENT_BUY_SHORT") == \
                pytest.approx(0.0, abs=1e-9)

    def test_the_negative_reading_is_left_documented_as_unexplained(self):
        """A comment that asserts a cause it cannot support is worse
        than one that says the question is open."""
        import inspect

        src = inspect.getsource(A.api_edge_decay)
        assert "DOES NOT EXPLAIN" in src
        assert "unexplained" in src

    def test_a_perfect_long_copy_reads_as_zero(self):
        assert _slip(0.60, 0.60, None) == pytest.approx(0.0)


class TestTheEndpointUsesIt:
    def test_it_selects_the_intent(self):
        src = inspect.getsource(A.api_edge_decay)
        assert "ORDER_INTENT_SQL" in src
        assert "AS intent" in src

    def test_it_computes_slip_through_cost_per_share(self):
        src = inspect.getsource(A.api_edge_decay)
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert 'cost_per_share(fp, r["intent"])' in code
        assert 'w["slip_sum"] += (fp - his)' not in code

    def test_unmodeled_rows_still_contribute_their_slippage(self):
        """They are mostly the shorts. Dropping them would silently
        scope this number to longs and hide the very rows that were
        wrong."""
        src = inspect.getsource(A.api_edge_decay)
        i = src.index('w["slip_sum"]')
        j = src.index('w["unmodeled"] += 1')
        assert i < j, "slip must accumulate before the modeling guard"

    def test_the_window_is_a_long_historical_one(self):
        """since_day defaults to 2026-08-01, so latency_median_s and
        avg_slip are multi-week averages, not current readings. Anyone
        quoting them as 'now' is misreading the endpoint."""
        sig = inspect.signature(A.api_edge_decay)
        assert sig.parameters["since_day"].default == "2026-08-01"
