"""The calibration budget refuses in the directions that cost money.

Every limit here was approved for ONE supervised instrumentation
experiment. The tests that matter are not the ones proving a $1 ticket
fits -- they are the ones proving that an unknown fee, an unknown
minimum quantity, a cancel acknowledgement or a returned sale cannot
quietly buy more room than management granted.
"""

from __future__ import annotations

import pytest

from sportsassets import calibration as cal


def ticket(**over):
    t = {
        "venue": "polymarket-us",
        "environment": "PRODUCTION",
        "account": "bettortoken-main",
        "marketId": "aec-atp-xxx-yyy-2026-09-18",
        "outcome": "XXX to win",
        "side": "BUY",
        "orderType": "LIMIT_GTC_POST_ONLY",
        "clientOrderId": "CAL-0001",
        "expiry": "2026-09-18T23:00:00Z",
        "price": 0.40,
        "quantity": 10,
        "tick": 0.01,
        "venueMinQuantity": 1,
        "entryFeeReserve": 0.20,
        "exitFeeReserve": 0.40,
        "feeModel": "venue schedule 2026-09, conservative upper bound",
        "inventoryPlan": "hold to settlement; no re-entry",
        "operatorStop": "render-ops calibration-stop",
        "stateFresh": True,
    }
    t.update(over)
    return t


ACCOUNT = {"available": 5000.0}


def session():
    return cal.empty_session("CAL-SESSION-1")


class TestTheApprovedNumbers:
    def test_the_three_limits_are_what_management_approved(self):
        assert cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE == 5.00
        assert cal.MAX_SESSION_CUMULATIVE_SPEND == 100.00
        assert cal.MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES == 1

    def test_a_fresh_session_may_commit_the_whole_ceiling_and_no_more(self):
        s = session()
        assert cal.spent(s) == 0.0
        assert cal.reserved(s) == 0.0
        assert cal.remaining(s) == 100.00


class TestAllInCost:
    def test_the_exit_fee_is_part_of_the_lifecycle_cost(self):
        """A $4.99 purchase with a $0.40 exit is a $5.39 lifecycle."""
        assert cal.all_in_cost(10, 0.40, 0.20, 0.40) == 4.60
        assert cal.all_in_cost(1, 4.99, 0.00, 0.40) == 5.39

    def test_a_ticket_that_only_fits_without_its_exit_fee_is_refused(self):
        t = ticket(price=0.48, quantity=10, entryFeeReserve=0.10,
                   exitFeeReserve=0.40)   # 4.80 + 0.50 = 5.30
        assert cal.all_in_cost(10, 0.48, 0.10, 0.40) == 5.30
        assert cal.R_PER_TRADE in cal.refusals(t, session(), ACCOUNT)

    def test_the_reserve_rounds_up_never_down(self):
        """A reserve that rounds down is not a bound."""
        assert cal.all_in_cost(3, 0.3333, 0.0, 0.0) == 1.00   # 0.9999 -> 1.00


class TestUnknownIsARefusalNeverAZero:
    def test_an_unknown_exit_fee_is_refused_by_name(self):
        assert cal.R_FEES_UNBOUNDED in cal.refusals(
            ticket(exitFeeReserve=None), session(), ACCOUNT)

    def test_a_ticket_with_no_fee_model_is_refused_even_with_numbers(self):
        assert cal.R_FEES_UNBOUNDED in cal.refusals(
            ticket(feeModel=""), session(), ACCOUNT)

    def test_an_unknown_venue_minimum_is_not_no_minimum(self):
        assert cal.R_MIN_QUANTITY in cal.refusals(
            ticket(venueMinQuantity=None), session(), ACCOUNT)

    def test_unknown_available_cash_is_not_funded_cash(self):
        assert cal.R_CASH_UNKNOWN in cal.refusals(
            ticket(), session(), {"available": None})

    def test_a_stale_preflight_is_refused(self):
        assert cal.R_STALE_STATE in cal.refusals(
            ticket(stateFresh=None), session(), ACCOUNT)


class TestTheMarketMustFitTheLimitNotTheOtherWay:
    def test_a_minimum_quantity_that_does_not_fit_is_skipped(self):
        """$5 cannot buy 100 shares at 40c. The market is skipped; the
        limit is not raised."""
        t = ticket(quantity=100, venueMinQuantity=100)
        why = cal.refusals(t, session(), ACCOUNT)
        assert cal.R_PER_TRADE in why
        assert cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE == 5.00

    def test_a_price_off_the_venue_tick_is_refused(self):
        assert cal.R_TICK in cal.refusals(
            ticket(price=0.405, tick=0.01), session(), ACCOUNT)

    def test_a_price_on_the_tick_is_accepted_despite_binary_float_error(self):
        # 0.1 + 0.2 != 0.3; a remainder-based tick check rejects this.
        assert cal.on_tick(0.30, 0.01) is True
        assert cal.on_tick(0.07, 0.01) is True
        assert cal.on_tick(0.075, 0.01) is False

    def test_a_fractional_quantity_is_refused(self):
        assert cal.R_QUANTITY in cal.refusals(
            ticket(quantity=10.5), session(), ACCOUNT)


class TestWhatIsNotAuthorised:
    @pytest.mark.parametrize("over,reason", [
        ({"side": "SELL"}, cal.R_SHORT),
        ({"leverage": True}, cal.R_LEVERAGE),
        ({"borrow": 1}, cal.R_LEVERAGE),
        ({"autoReentry": True}, cal.R_AUTO_REENTRY),
        ({"sizeIncreaseAfterOutcome": True}, cal.R_SIZE_INCREASE),
        ({"inventoryPlan": ""}, cal.R_NO_INVENTORY_PLAN),
        ({"operatorStop": ""}, cal.R_NO_STOP),
    ])
    def test_each_is_refused_by_name(self, over, reason):
        assert reason in cal.refusals(ticket(**over), session(), ACCOUNT)

    def test_an_unnamed_ticket_cannot_be_reconciled_so_it_is_refused(self):
        why = cal.refusals(ticket(clientOrderId=""), session(), ACCOUNT)
        assert any(w.startswith(cal.R_IDENTITY) for w in why)


class TestConcurrency:
    def test_one_open_lifecycle_blocks_the_next_ticket(self):
        s = session()
        cal.reserve(s, ticket(), ACCOUNT)
        why = cal.refusals(ticket(clientOrderId="CAL-0002"), s, ACCOUNT)
        assert cal.R_CONCURRENCY in why

    def test_a_filled_exit_is_still_an_open_lifecycle(self):
        """EXIT_FILLED is not reconciled. Late fills land here."""
        s = session()
        cal.reserve(s, ticket(), ACCOUNT)
        cal.advance(s, "CAL-0001", cal.EXIT_FILLED)
        assert len(cal.open_lifecycles(s)) == 1
        assert cal.R_CONCURRENCY in cal.refusals(
            ticket(clientOrderId="CAL-0002"), s, ACCOUNT)


class TestTheReserve:
    def test_the_reserve_is_taken_before_submission(self):
        s = session()
        lc = cal.reserve(s, ticket(), ACCOUNT)
        assert lc["state"] == cal.APPROVED         # not SUBMITTED
        assert cal.reserved(s) == 4.60
        assert cal.remaining(s) == 95.40

    def test_a_cancel_acknowledgement_does_not_release_the_reserve(self):
        s = session()
        cal.reserve(s, ticket(), ACCOUNT)
        cal.advance(s, "CAL-0001", cal.SUBMITTED)
        with pytest.raises(ValueError) as e:
            cal.release(s, "CAL-0001", None, fills_reconciled=False)
        assert "RESERVE_HELD" in str(e.value)
        assert cal.reserved(s) == 4.60

    def test_a_venue_terminal_state_without_reconciled_fills_holds_it(self):
        s = session()
        cal.reserve(s, ticket(), ACCOUNT)
        with pytest.raises(ValueError):
            cal.release(s, "CAL-0001", "CANCELLED", fills_reconciled=False)
        assert cal.reserved(s) == 4.60

    def test_release_needs_both_and_then_frees_the_reserve(self):
        s = session()
        cal.reserve(s, ticket(), ACCOUNT)
        cal.release(s, "CAL-0001", "FILLED", fills_reconciled=True)
        assert cal.reserved(s) == 0.0
        assert cal.open_lifecycles(s) == []

    def test_reserve_refuses_an_inadmissible_ticket(self):
        s = session()
        with pytest.raises(ValueError) as e:
            cal.reserve(s, ticket(exitFeeReserve=None), ACCOUNT)
        assert cal.R_FEES_UNBOUNDED in str(e.value)


class TestSpendIsCumulativeNotBuyingPower:
    def test_proceeds_never_replenish_the_session(self):
        s = session()
        cal.reserve(s, ticket(), ACCOUNT)
        cal.record_spend(s, "CAL-0001", 4.20)
        cal.release(s, "CAL-0001", "FILLED", fills_reconciled=True)
        # A $4.90 sale is not recordable as negative spend...
        with pytest.raises(ValueError):
            cal.record_spend(s, "CAL-0001", -4.90)
        assert cal.spent(s) == 4.20
        assert cal.remaining(s) == 95.80    # NOT 100.00

    def test_the_ceiling_only_falls_and_remaining_never_exceeds_it(self):
        """THE MONOTONE QUANTITY IS `spent`, NOT `remaining`.

        Releasing an over-reserve is not recycling proceeds: a lifecycle
        reserved at $4.60 that cost $4.00 hands back $0.60 that never
        left the account. Refusing that would make every conservative
        fee reserve permanently burn budget. What must never happen is
        `remaining` rising above the ceiling the cumulative spend has
        already set.
        """
        s = session()
        ceilings, spends = [], []
        for i in range(3):
            cid = "CAL-%04d" % (i + 1)
            cal.reserve(s, ticket(clientOrderId=cid), ACCOUNT)
            assert cal.remaining(s) <= cal.MAX_SESSION_CUMULATIVE_SPEND \
                - cal.spent(s)
            cal.record_spend(s, cid, 4.00)
            cal.release(s, cid, "FILLED", fills_reconciled=True)
            ceilings.append(cal.MAX_SESSION_CUMULATIVE_SPEND - cal.spent(s))
            spends.append(cal.spent(s))
            # With nothing at risk, remaining IS the ceiling -- the
            # unspent $0.60 of reserve returned, the $4.00 did not.
            assert cal.remaining(s) == ceilings[-1]
        assert ceilings == sorted(ceilings, reverse=True), ceilings
        assert spends == sorted(spends), spends
        assert cal.spent(s) == 12.00
        assert cal.remaining(s) == 88.00      # NOT 100.00, NOT 86.20

    def test_an_over_reserve_returns_but_the_money_actually_spent_does_not(self):
        s = session()
        cal.reserve(s, ticket(), ACCOUNT)     # reserves 4.60
        cal.record_spend(s, "CAL-0001", 4.00)  # only 4.00 ever left
        assert cal.remaining(s) == 91.40      # 100 - 4.00 spent - 4.60 held
        cal.release(s, "CAL-0001", "FILLED", fills_reconciled=True)
        assert cal.remaining(s) == 96.00      # the unspent 0.60 came back
        assert cal.spent(s) == 4.00           # the 4.00 never will

    def test_the_session_ceiling_refuses_the_ticket_that_would_cross_it(self):
        s = session()
        for i in range(24):                       # 24 x 4.00 = 96.00
            cid = "CAL-%04d" % i
            cal.reserve(s, ticket(clientOrderId=cid, price=0.34,
                                  entryFeeReserve=0.20, exitFeeReserve=0.40),
                        ACCOUNT)
            cal.record_spend(s, cid, 4.00)
            cal.release(s, cid, "FILLED", fills_reconciled=True)
        assert cal.spent(s) == 96.00
        assert cal.remaining(s) == 4.00
        # A 4.60 ticket no longer fits, though it is under the $5 per-trade cap.
        why = cal.refusals(ticket(clientOrderId="CAL-LAST"), s, ACCOUNT)
        assert cal.R_SESSION in why
        assert cal.R_PER_TRADE not in why


class TestFunding:
    def test_an_account_that_cannot_cover_the_ticket_is_refused(self):
        assert cal.R_UNFUNDED in cal.refusals(
            ticket(), session(), {"available": 1.00})


class TestPreflight:
    def test_an_unproved_check_blocks_submission_however_small(self):
        rep = cal.preflight(ticket(), session(), ACCOUNT, checks={})
        assert rep["submittable"] is False
        assert "cancellationPathVerified" in rep["unprovedChecks"]
        assert rep["refusals"] == []          # the BUDGET is fine
        assert rep["allInCost"] == 4.60
        assert rep["maximumAllInExposure"] == 4.60

    def test_every_check_proved_and_the_budget_clear_is_submittable(self):
        checks = {k: True for k in (
            "venueAccountInstrumentVerified", "freshAccountState",
            "freshOpenOrderState", "uniqueClientOrderId",
            "ambiguousResponseHandling", "cancellationPathVerified",
            "partialFillReconciliation", "lateFillReconciliation",
            "durableRecords", "operatorStop", "previewedCost")}
        rep = cal.preflight(ticket(), session(), ACCOUNT, checks=checks)
        assert rep["submittable"] is True
        assert rep["requiresHumanApproval"] is True

    def test_preflight_never_invents_an_ev_estimate(self):
        rep = cal.preflight(ticket(), session(), ACCOUNT, checks={})
        assert "netEv" not in rep
        assert "fillProbability" not in rep
