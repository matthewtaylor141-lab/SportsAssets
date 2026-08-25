"""The short leg was priced and costed in the wrong denomination.

Owner, 2026-08-25, after checking Polymarket's global microdata:
"It is the mechanism. This has been our biggest leak."

THE MODEL UNDER TEST: going short is SELLING the contract, `price`
denominates the contract, and a short ties up (1 - price) x qty.

IT IS NOT CONFIRMED, AND THESE TESTS ARM IT DELIBERATELY.

I originally argued the SDK settled it: CreateOrderParams carries no
token id, therefore one market, one ladder, therefore short is a sell.
I put that to the owner as established. It is not sound:

  * polymarket_us/types/markets.py MarketDetail has ZERO occurrences of
    `marketSides` — a field the venue demonstrably returns and pmus.py
    reads seventeen times. The stubs are partial, so absence from them
    is evidence of nothing.
  * pmus.event_board already treats each side's `identifier` as its own
    orderable slug WITH ITS OWN price, and side_ask reads that side's
    own bestAsk. A per-side price exists here. "No second ladder" is
    contradicted by code we ship.

The arithmetic below still fits six of six. But the same six also fit
"we were filled on the opposite leg at its own ask", because that ask
is approximately the complement — a fit is not a mechanism.

So the model ships DISARMED behind LIVE_SHORT_COST_MODEL=confirmed,
which is set only once the venue's own order.side on those rows reads
ORDER_SIDE_SELL. These tests exercise the model, so the fixture arms
it; TestTheGateHoldsWhenUnconfirmed covers the shipped default.

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

import pytest

from sportsassets import live_executor as le


@pytest.fixture(autouse=True)
def _arm_short_model(monkeypatch):
    """The short cost model is DISARMED by default in production —
    see live_executor.short_model_confirmed. These tests exercise the
    model itself, so they arm it explicitly. test_short_model_gate
    covers the disarmed behaviour."""
    monkeypatch.setenv("LIVE_SHORT_COST_MODEL", "confirmed")

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

    def test_the_breaker_is_ACTUALLY_short_aware(self):
        """THE ASSERTION ABOVE IS VACUOUS AND HID A LIVE HALT.

        It checks source-text ORDERING — that `spent = fill_cash(...)`
        appears before `is_overspend(...)` — and never that `spent`, or
        the intent, reaches the breaker. It did not. is_overspend took
        (usd, filled, fill_price) and overspend_ratio re-derived the
        cost as `filled * fill_price`: the long-only formula, a FIFTH
        copy of the denomination rule, and the one wired to a breaker.

        The venue's own receipt: 1,136 shares authorized at $249.92,
        filled at 0.78. Real cost 1136 x 0.22 = $249.92 — exactly the
        authorization, zero overage. The long formula reads
        1136 x 0.78 / 249.92 = 3.545 and trips at 1.01.

        The consequence was not a bad number on a report. overspend_halt
        gates the top of BOTH maybe_execute and mirror_exit, so the
        first correctly-priced short fill would have stopped every whale
        on both legs — and it cannot self-clear, because the clear only
        removes records stamped before ASK_GUARD_SINCE. A manual admin
        clear, triggered by a trade that did nothing wrong.

        Found by an adversarial review of my own diff, hours after I
        wrote a commit message claiming the denomination was fixed in
        every place it lived.
        """
        assert le.is_overspend(249.92, 1136, 0.78, SHORT) is False
        assert le.overspend_ratio(249.92, 1136, 0.78, SHORT) == 1.0

    def test_a_REAL_short_overspend_is_still_caught(self):
        """The breaker must still protect. Same fill against a $100
        authorization is a genuine 2.5x breach."""
        assert le.is_overspend(100.0, 1136, 0.78, SHORT) is True

    def test_long_fills_are_bit_for_bit_unchanged(self):
        """The intent defaults to None, so every existing caller and
        every long fill computes exactly what it did before."""
        for req, sh, px in ((249.73, 1135, 0.32), (250.0, 1000, 0.30),
                            (249.92, 781, 0.32), (250.0, 400, 0.32)):
            assert (le.overspend_ratio(req, sh, px)
                    == le.overspend_ratio(req, sh, px, LONG)
                    == round(sh * px / req, 4))

    def test_the_breaker_and_the_halt_RECORD_cannot_disagree(self):
        """The halt row already stamped the CORRECTED ratio
        (spent / usd) beside a predicate that fired on the uncorrected
        one — the same block reporting two numbers for one fill, one of
        them exonerating. Both derive from fill_cash now."""
        for px, req, sh in ((0.78, 249.92, 1136), (0.6853, 245.78, 781)):
            spent = le.fill_cash(sh, px, SHORT)
            assert le.overspend_ratio(req, sh, px, SHORT) == \
                pytest.approx(round(spent / req, 4), abs=1e-4)

    def test_the_intent_reaches_the_call_site(self):
        """The defect was an ARGUMENT LIST, so read the argument list —
        not the ordering of two lines."""
        import re as _re

        src = inspect.getsource(le.maybe_execute)
        m = _re.search(r"is_overspend\(([^)]*)\)", src)
        assert m, "the breaker call site vanished"
        assert "_fill_intent" in m.group(1), \
            f"breaker called without the intent: is_overspend({m.group(1)})"


class TestTheModelIsArmedOnTheVenuesOwnReceipt:
    """CONFIRMED 2026-08-25 14:38Z, and not by my argument.

    /api/admin/short-truth read the create-order responses we have been
    storing on every row since the beginning:

        SHORTTRUTH n=6 with_venue_side=6 sides={"ORDER_SIDE_SELL":6}
                   within_auth short_model=6/6 long_model=0/6

    `side` is not a field we send — the venue derives it from the
    intent — so six of six is the venue stating that our BUY_SHORT was
    booked as a sell. The SDK argument I originally offered stays
    retracted; this is a different and better kind of evidence.

    So: there was never an overspend, the breaker fired six times on
    correct trades, and filled_usd / pnl / deployed have been wrong by
    (1-p)/p on every short row.
    """

    def test_it_is_armed_by_default_now(self, monkeypatch):
        monkeypatch.delenv("LIVE_SHORT_COST_MODEL", raising=False)
        assert le.short_model_confirmed()
        assert le.wire_limit(0.22, SHORT) == 0.78
        assert le.fill_cash(1136, 0.78, SHORT) == 249.92

    def test_an_explicit_word_disarms_it_in_one_move(self, monkeypatch):
        """If a later reading disagrees, this comes off without a
        deploy."""
        for val in ("off", "0", "no", "disarm", "disarmed", "OFF"):
            monkeypatch.setenv("LIVE_SHORT_COST_MODEL", val)
            assert not le.short_model_confirmed(), val
            assert le.wire_limit(0.22, SHORT) == 0.22
            assert le.fill_cash(1136, 0.78, SHORT) == 886.08

    def test_a_vague_value_does_not_disarm_it(self, monkeypatch):
        """Only the named words turn it off. A stray env value must not
        silently revert a confirmed model."""
        for val in ("", "maybe", "1", "true", "confirmed"):
            monkeypatch.setenv("LIVE_SHORT_COST_MODEL", val)
            assert le.short_model_confirmed(), val

    def test_the_receipt_is_recorded_in_the_code(self):
        """The six rows and the venue's answer live beside the switch,
        so re-opening the question has to argue with the numbers."""
        src = inspect.getsource(le.short_model_confirmed)
        assert "ORDER_SIDE_SELL" in src
        assert "n=6 with_venue_side=6" in src
        assert "there was never an overspend" in src

    def test_longs_are_unaffected_either_way(self, monkeypatch):
        for val in (None, "off"):
            if val is None:
                monkeypatch.delenv("LIVE_SHORT_COST_MODEL", raising=False)
            else:
                monkeypatch.setenv("LIVE_SHORT_COST_MODEL", val)
            assert le.wire_limit(0.45, LONG) == 0.45
            assert le.fill_cash(100, 0.45, LONG) == 45.0
