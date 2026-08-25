"""The short leg was priced and costed in the wrong denomination.

Owner, 2026-08-25, after checking Polymarket's global microdata:
"It is the mechanism. This has been our biggest leak."

THE VENUE SHAPE (polymarket_us/types/orders.py):
  * CreateOrderParams takes marketSlug and intent and carries NO token
    or asset id. There is ONE market and ONE price ladder — you cannot
    name a "short token" because none exists.
  * Order carries `side` (ORDER_SIDE_BUY/SELL) but CreateOrderParams
    does not. `side` is returned, never sent: the venue DERIVES it from
    the intent.

That is a futures contract. Going short is SELLING it, `price`
denominates the contract, and a short ties up (1 - price) x qty.

TWO CONSEQUENCES, and they are different bugs:

1. THE WIRE PRICE WAS TOO LOOSE. We sent the whale's own outcome price
   for both intents. Sending 0.22 as a sell limit authorises selling at
   anything >= 0.22 — paying up to 0.78 a contract when we meant 0.22.
   3.5x wider than intended on the worst row. The six fills came in
   under it by luck, not by control.

2. THE CASH WAS COMPUTED WITH THE LONG FORMULA. `filled * fill_price`
   on a short row reports the contract's price, not our cost. That one
   line manufactured "BUY_SHORT n=6 over=6 clean=0", and I took a
   correct class of trade off the board for a day because of it.

Under the right formula every one of the six lands at or under what we
authorised, one exact to the cent, worst overage $0.00 across all six.
"""

import inspect

from sportsassets import live_executor as le

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"
SELL_SHORT = "ORDER_INTENT_SELL_SHORT"

# (requested_usd, qty, our_limit, venue_fill_price) — 0x076daa87, 08-24
ROWS = [
    (249.78, 1086, 0.23, 0.89),
    (249.92, 1136, 0.22, 0.78),
    (249.92, 781, 0.32, 0.6853),
    (249.75, 675, 0.37, 0.65),
    (249.75, 555, 0.45, 0.56),
    (249.60, 520, 0.48, 0.55),
]


class TestIntentDetection:
    def test_both_short_intents_are_short(self):
        assert le.is_short_intent(SHORT)
        assert le.is_short_intent(SELL_SHORT)

    def test_long_intents_are_not(self):
        assert not le.is_short_intent(LONG)
        assert not le.is_short_intent("ORDER_INTENT_SELL_LONG")

    def test_absent_intent_is_treated_as_long(self):
        """Fail toward the formula that has always been correct for the
        class we have actually been trading."""
        assert not le.is_short_intent(None)
        assert not le.is_short_intent("")


class TestTheCashFormula:
    def test_every_row_is_within_authorization(self):
        for req, qty, _lim, f in ROWS:
            cash = le.fill_cash(qty, f, SHORT)
            assert cash <= req + 0.01, (
                f"qty {qty} @ {f}: cost {cash} exceeds authorised {req}")

    def test_one_row_is_exact_to_the_cent(self):
        assert le.fill_cash(1136, 0.78, SHORT) == 249.92

    def test_the_long_formula_breaches_on_every_row(self):
        """What the old line reported, and why the breaker fired six
        times on correct trades."""
        for req, qty, _lim, f in ROWS:
            assert le.fill_cash(qty, f, LONG) > req + 0.01

    def test_longs_are_completely_unchanged(self):
        for qty, px in [(100, 0.45), (1086, 0.89), (6250, 0.04)]:
            assert le.fill_cash(qty, px, LONG) == round(qty * px, 2)

    def test_degenerate_inputs_are_zero_not_a_windfall(self):
        """A short with no fill price must not book (1 - 0) * qty as
        cost — that would invent money out of a missing field."""
        assert le.fill_cash(100, None, SHORT) == 0.0
        assert le.fill_cash(100, 0, SHORT) == 0.0
        assert le.fill_cash(0, 0.5, SHORT) == 0.0
        assert le.fill_cash(-5, 0.5, SHORT) == 0.0


class TestTheWireLimit:
    def test_a_long_goes_out_unchanged(self):
        for p in (0.04, 0.23, 0.45, 0.67, 0.99):
            assert le.wire_limit(p, LONG) == p

    def test_a_short_goes_out_as_the_complement(self):
        assert le.wire_limit(0.22, SHORT) == 0.78
        assert le.wire_limit(0.45, SHORT) == 0.55

    def test_rounding_never_authorises_more_than_intended(self):
        """Ceiling on the sell side: a sell limit that rounds DOWN would
        authorise paying more, which is the whole bug in miniature."""
        for lim in (0.225, 0.3333, 0.6851, 0.019):
            w = le.wire_limit(lim, SHORT)
            assert w >= 1.0 - lim - 1e-9
            assert (1.0 - w) <= lim + 1e-9, (
                f"wire {w} authorises paying {1 - w} for a {lim} budget")

    def test_it_is_a_tightening_on_every_real_row(self):
        """The money each row authorises must FALL. A money gate may be
        tightened, never loosened, and this proves the direction."""
        for _req, qty, lim, _f in ROWS:
            before = (1.0 - lim) * qty      # what the raw limit allowed
            after = (1.0 - le.wire_limit(lim, SHORT)) * qty
            assert after <= before, f"limit {lim} got looser, not tighter"
            assert after <= lim * qty + 0.01

    def test_the_worst_row_tightens_by_more_than_three_times(self):
        lim = 0.22
        assert round((1.0 - lim) / (1.0 - le.wire_limit(lim, SHORT)), 2) == 3.55


class TestTheRoundTrip:
    """Wire price and cash formula must agree: a fill AT our wire limit
    should cost exactly the budget we sized for. If these two disagree
    the sizing is fiction."""

    def test_a_fill_at_the_wire_limit_costs_the_intended_budget(self):
        for _req, qty, lim, _f in ROWS:
            w = le.wire_limit(lim, SHORT)
            cash = le.fill_cash(qty, w, SHORT)
            intended = round(qty * lim, 2)
            assert cash <= intended + 0.01, (
                f"a fill at our own limit {w} costs {cash}, over the "
                f"{intended} we sized for")

    def test_a_better_fill_costs_strictly_less(self):
        """On a short, a HIGHER contract price is a better entry."""
        assert le.fill_cash(1000, 0.85, SHORT) < le.fill_cash(1000, 0.78, SHORT)


class TestItIsWiredIntoTheMoneyPath:
    def test_the_submit_uses_the_wire_price_not_the_raw_limit(self):
        src = inspect.getsource(le.maybe_execute)
        assert "_wire = wire_limit(limit, _intent)" in src
        i = src.index("_wire = wire_limit")
        j = src.index("pmus.submit_fok", i)
        assert "mapping[\"market_slug\"], _wire" in src[i:j + 120]

    def test_the_recorded_cost_is_intent_aware(self):
        src = inspect.getsource(le.maybe_execute)
        assert "spent = fill_cash(filled, fill_price, _fill_intent)" in src
        assert "round(filled * (fill_price or 0), 2)" not in src

    def test_the_overspend_breaker_now_sees_the_corrected_cost(self):
        """is_overspend is fed `spent`. If that still carried the long
        formula the breaker would keep halting the sleeve on correct
        short fills."""
        src = inspect.getsource(le.maybe_execute)
        assert src.index("spent = fill_cash(") < src.index("is_overspend(")
