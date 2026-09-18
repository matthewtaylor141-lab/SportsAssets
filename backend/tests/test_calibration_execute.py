"""The calibration venue path, exercised offline against a mock venue.

Every test here is about a failure mode that has already cost this desk
money or clarity: a lost placement response the venue actually filled
(book 863), a cancel the venue beat with a fill (book 1333), a cancel
storm whose status read never went terminal (18:05Z), and a limit exit
treated as a certainty.

Nothing in this file opens a socket, and nothing in the codebase wires
these functions to a route.
"""

from __future__ import annotations

import pytest

from sportsassets import calibration_execute as ex


def ticket(**over):
    t = {"marketId": "aec-atp-xxx-yyy-2026-09-18", "outcome": "XXX to win",
         # `outcomeSide` names WHICH side of a shared-identifier market.
         # It has no default: the venue reads it through the intent, and
         # the adapter refuses a ticket that does not state it.
         "outcomeSide": "LONG",
         "side": "BUY", "orderType": "LIMIT_GTC_POST_ONLY",
         "clientOrderId": "CAL-0001", "price": 0.40, "quantity": 10,
         "inventoryPlan": "hold to settlement; no re-entry"}
    t.update(over)
    return t


class MockVenue:
    def __init__(self, pre=(), submit=None, raise_on_submit=False,
                 statuses=None, raise_on_status=False,
                 raise_on_pre=False, raise_on_cancel=False):
        self.pre = list(pre)
        self._submit = submit
        self.raise_on_submit = raise_on_submit
        self.statuses = list(statuses or [])
        self.raise_on_status = raise_on_status
        self.raise_on_pre = raise_on_pre
        self.raise_on_cancel = raise_on_cancel
        self.calls = []

    def open_order_ids(self, market_id):
        self.calls.append(("open_order_ids", market_id))
        if self.raise_on_pre:
            raise RuntimeError("venue 503")
        return self.pre

    def submit(self, **kw):
        self.calls.append(("submit", kw))
        if self.raise_on_submit:
            raise RuntimeError("connection reset")
        return self._submit

    def status(self, order_id):
        self.calls.append(("status", order_id))
        if self.raise_on_status:
            raise RuntimeError("InternalServerError")
        return self.statuses.pop(0) if self.statuses else {}

    def cancel(self, order_id, market_id):
        self.calls.append(("cancel", order_id, market_id))
        if self.raise_on_cancel:
            raise RuntimeError("venue 500")
        return {"ok": True}


OK = {"ok": True, "order_id": "V-991", "status": "open",
      "filled_shares": 0, "fill_price": None}


class TestTheRecordComesBeforeTheSend:
    def test_the_pre_image_is_read_before_the_order_goes_out(self):
        v = MockVenue(pre=["V-1", "V-2"], submit=OK)
        r = ex.submit(v, ticket())
        assert [c[0] for c in v.calls] == ["open_order_ids", "submit"]
        assert r["preOpenOrderIds"] == ["V-1", "V-2"]

    def test_without_a_pre_image_nothing_is_sent_at_all(self):
        """An ambiguous send with no pre-image is unreconcilable: we
        could not tell our order from one that was already there. So we
        refuse before sending -- refusing costs nothing."""
        v = MockVenue(raise_on_pre=True, submit=OK)
        r = ex.submit(v, ticket())
        assert r["outcome"] == ex.REFUSED_BY_VENUE
        assert r["sent"] is False
        assert "PRE_IMAGE_UNREADABLE" in r["reason"]
        assert ("submit",) not in [(c[0],) for c in v.calls]


class TestALostResponseIsNotARefusal:
    @pytest.mark.parametrize("kw,reason", [
        ({"raise_on_submit": True}, "SEND_RAISED"),
        ({"submit": None}, "RESPONSE_NOT_A_MAPPING"),
        ({"submit": {"ok": True}}, "NO_ORDER_ID_IN_RESPONSE"),
    ])
    def test_each_unreadable_send_is_ambiguous_not_refused(self, kw, reason):
        v = MockVenue(pre=["V-1"], **kw)
        r = ex.submit(v, ticket())
        assert r["outcome"] == ex.AMBIGUOUS
        assert reason in r["reason"]
        assert r["sent"] is True            # it may well be at the venue
        assert r["preOpenOrderIds"] == ["V-1"]
        assert "tells us nothing" in r["note"]

    def test_a_named_venue_refusal_is_not_ambiguous(self):
        """The venue SAYING it refused is different from silence."""
        v = MockVenue(pre=[], submit={"ok": False,
                                      "status": "post_only_rejected"})
        r = ex.submit(v, ticket())
        assert r["outcome"] == ex.REFUSED_BY_VENUE
        assert r["reason"] == "post_only_rejected"

    def test_nothing_is_ever_retried(self):
        v = MockVenue(pre=[], raise_on_submit=True)
        ex.submit(v, ticket())
        assert sum(1 for c in v.calls if c[0] == "submit") == 1


class TestACancelIsNotATerminalState:
    def test_a_successful_cancel_with_an_open_status_is_not_terminal(self):
        """The 18:05Z shape: the cancel returns 200 and the status read
        keeps saying OPEN. The lifecycle stays open, and so does its
        reserve."""
        v = MockVenue(statuses=[{"status": "OPEN", "filled_shares": 0,
                                 "quantity": 10}])
        r = ex.cancel(v, "V-991", "mkt")
        assert r["outcome"] == ex.CANCEL_SENT
        assert r["terminal"] is False
        assert r["status"]["outcome"] == ex.RESTING

    def test_the_venue_may_have_filled_before_it_saw_the_cancel(self):
        """Book 1333. The cancel succeeds and the order is FILLED."""
        v = MockVenue(statuses=[{"status": "FILLED", "filled_shares": 10,
                                 "quantity": 10, "fill_price": 0.41,
                                 "fees": 0.05}])
        r = ex.cancel(v, "V-991", "mkt")
        assert r["terminal"] is True
        assert r["status"]["filled"] == 10
        s = ex.settle({}, r["status"], quantity=10)
        # The cash is the venue's fill PLUS its fee.
        assert s["spend"] == 4.15
        # ...and the ENTRY being terminal does not release the reserve:
        # ten shares are now held.
        assert s["mayRelease"] is False
        assert "leaves INVENTORY" in s["whyNot"]

    def test_a_cancel_that_raises_still_reads_the_status(self):
        v = MockVenue(raise_on_cancel=True,
                      statuses=[{"status": "OPEN", "filled_shares": 0}])
        r = ex.cancel(v, "V-991", "mkt")
        assert r["outcome"] == ex.AMBIGUOUS
        assert r["ackError"] == "RuntimeError"
        assert ("status", "V-991") in v.calls
        assert r["terminal"] is False

    def test_an_unreadable_status_is_not_a_cancelled_order(self):
        v = MockVenue(raise_on_status=True)
        r = ex.cancel(v, "V-991", "mkt")
        assert r["status"]["outcome"] == ex.UNREADABLE
        assert r["terminal"] is False


class TestReconciliation:
    @pytest.mark.parametrize("state,terminal", [
        ("FILLED", True), ("CANCELLED", True), ("EXPIRED", True),
        ("REJECTED", True), ("OPEN", False), ("PENDING", False),
    ])
    def test_only_the_venues_own_terminal_states_are_terminal(self, state,
                                                              terminal):
        v = MockVenue(statuses=[{"status": state, "filled_shares": 0}])
        assert ex.reconcile(v, "V-1")["terminal"] is terminal

    def test_a_missing_state_is_unreadable_not_resting(self):
        v = MockVenue(statuses=[{"filled_shares": 0}])
        assert ex.reconcile(v, "V-1")["outcome"] == ex.UNREADABLE

    def test_a_partial_is_reported_as_partial(self):
        v = MockVenue(statuses=[{"status": "OPEN", "filled_shares": 4,
                                 "quantity": 10, "fill_price": 0.40,
                                 "fees": 0.02}])
        r = ex.reconcile(v, "V-1")
        assert r["outcome"] == ex.PARTIAL
        assert ex.settle({}, r, quantity=10)["spend"] == 1.62

    def test_an_unreported_fee_travels_as_absent_not_as_zero(self):
        v = MockVenue(statuses=[{"status": "FILLED", "filled_shares": 10,
                                 "quantity": 10, "fill_price": 0.41}])
        r = ex.reconcile(v, "V-1")
        assert "fees" not in r
        assert ex.settle({}, r, quantity=10)["evidence"] == ex.FEES_UNKNOWN

    def test_a_zero_fill_reports_a_real_zero_fee(self):
        v = MockVenue(statuses=[{"status": "OPEN", "filled_shares": 0,
                                 "quantity": 10}])
        assert ex.reconcile(v, "V-1")["fees"] == 0.0


class TestSpendComesFromTheVenueNotTheTicket:
    def test_a_partial_fill_books_what_the_venue_says_plus_its_fee(self):
        """The ticket intended $4.00 for ten shares. Six filled at 41c,
        and the venue charged 3c."""
        status = {"terminal": True, "filled": 6, "fillPrice": 0.41,
                  "fees": 0.03}
        assert ex.settle({}, status, quantity=10)["spend"] == 2.49

    def test_an_unreadable_fill_price_blocks_release(self):
        status = {"terminal": True, "filled": 6, "fillPrice": None}
        s = ex.settle({}, status)
        assert s["spend"] is None and s["mayRelease"] is False

    def test_a_non_terminal_order_may_not_release_its_reserve(self):
        status = {"terminal": False, "filled": 0, "fillPrice": None}
        s = ex.settle({}, status)
        assert s["mayRelease"] is False
        assert "can still fill" in s["whyNot"]


class TestTheExitIsNotAPromise:
    def test_the_plan_declares_its_own_fallback_and_assumes_no_fill(self):
        p = ex.plan_exit(ticket(), 10, 0.45)
        assert p["assumesFill"] is False
        assert p["ifUnfilled"] == "hold to settlement; no re-entry"
        assert "may never fill" in p["note"]


class TestNothingIsWiredToARoute:
    def test_no_handler_imports_the_execution_path(self):
        """The monitoring release must not be able to reach the venue.
        A module that is merely 'not called yet' is one import away from
        being called; this pins that the import does not exist."""
        import pathlib
        api = pathlib.Path(__file__).resolve().parents[1] / "sportsassets" / "api"
        hits = [p.name for p in api.glob("*.py")
                if "calibration_execute" in p.read_text()]
        assert hits == [], hits


# ── the durable handoff, verified rather than described ──────────────

def row(**over):
    r = {"clientOrderId": "CAL-0001", "state": "APPROVED", "reserve": 4.60,
         "marketId": "aec-atp-xxx-yyy-2026-09-18", "outcome": "XXX to win",
         "side": "BUY", "price": 0.40, "quantity": 10, "venueOrderId": None}
    r.update(over)
    return r


class TestTheCallablePathChecksItsApprovedState:
    """The docstring used to say the row exists first. It said it; the
    code did not check it, so `submit()` would have sent for a ticket no
    approval had ever been written for. That is the uncalled-census
    defect one layer down."""

    def test_no_durable_row_refuses_before_any_send(self):
        v = MockVenue(pre=[], submit=OK)
        r = ex.guarded_submit(v, ticket(), None)
        assert r["sent"] is False
        assert ex.H_NO_ROW in r["blockers"]
        assert v.calls == []                       # nothing was even read

    def test_a_row_in_the_wrong_state_refuses(self):
        v = MockVenue(pre=[], submit=OK)
        r = ex.guarded_submit(v, ticket(), row(state="SUBMITTED"))
        assert r["sent"] is False
        assert any(b.startswith(ex.H_WRONG_STATE) for b in r["blockers"])
        assert v.calls == []

    def test_a_row_with_no_reserve_refuses(self):
        r = ex.guarded_submit(MockVenue(), ticket(), row(reserve=0))
        assert ex.H_NO_RESERVE in r["blockers"]

    def test_a_row_that_already_has_a_venue_order_refuses(self):
        """One send per lifecycle. A second would be the double-placement
        this whole discipline exists to prevent."""
        r = ex.guarded_submit(MockVenue(), ticket(), row(venueOrderId="V-1"))
        assert any(b.startswith(ex.H_ALREADY_SENT) for b in r["blockers"])

    def test_an_operator_stop_refuses_even_with_a_perfect_row(self):
        r = ex.guarded_submit(MockVenue(), ticket(), row(),
                              session_stopped=True)
        assert ex.H_STOPPED in r["blockers"]

    @pytest.mark.parametrize("field,value", [
        ("price", 0.41), ("quantity", 11), ("marketId", "another-market"),
        ("outcome", "YYY to win"), ("side", "SELL"),
    ])
    def test_a_ticket_that_drifts_from_the_approved_row_refuses(self, field,
                                                                value):
        """The row is what a human said yes to. A ticket differing in
        any bound field is a different ticket wearing an approved name."""
        r = ex.guarded_submit(MockVenue(), ticket(**{field: value}), row())
        assert any(b.startswith(ex.H_TICKET_DRIFT) for b in r["blockers"]), \
            r["blockers"]

    def test_a_matching_approved_row_sends_exactly_once(self):
        v = MockVenue(pre=["V-0"], submit=OK)
        seen = []
        r = ex.guarded_submit(v, ticket(), row(), persist=seen.append)
        assert r["outcome"] == ex.SUBMITTED
        assert sum(1 for c in v.calls if c[0] == "submit") == 1
        assert [c[0] for c in v.calls] == ["open_order_ids", "submit"]
        assert r["preOpenOrderIds"] == ["V-0"]
        assert r["persisted"] is True and len(seen) == 1
        assert seen[0]["clientOrderId"] == "CAL-0001"

    def test_an_ambiguous_send_is_persisted_too(self):
        """The ambiguous one is the ONLY one that must be written down:
        it is the case where the record is all we will have."""
        v = MockVenue(pre=["V-0"], raise_on_submit=True)
        seen = []
        r = ex.guarded_submit(v, ticket(), row(), persist=seen.append)
        assert r["outcome"] == ex.AMBIGUOUS
        assert r["persisted"] is True
        assert seen[0]["preOpenOrderIds"] == ["V-0"]

    def test_a_failed_persist_is_surfaced_not_swallowed(self):
        def boom(_rec):
            raise RuntimeError("database gone")
        v = MockVenue(pre=[], submit=OK)
        r = ex.guarded_submit(v, ticket(), row(), persist=boom)
        assert r["persisted"] is False
        assert r["persistError"] == "RuntimeError"
        assert "SEND HAPPENED AND THE RECORD DID NOT" in r["note"]


class TestATicketTheAdapterCannotMapIsNeverSent:
    """An unmappable ticket is refused BEFORE the pre-image read.

    It must not come back as an ambiguous send. AMBIGUOUS means the venue
    may hold an order we cannot name, and it holds the reserve and the
    single-lifecycle slot until an operator resolves it. Spending that on
    a ValueError raised before any socket opened would be a fabricated
    unresolved lifecycle.
    """

    @pytest.mark.parametrize("over,marker", [
        ({"outcomeSide": None}, "UNMAPPED_OUTCOME"),
        ({"outcomeSide": "YES"}, "UNMAPPED_OUTCOME"),
        ({"orderType": "LIMIT_WHATEVER"}, "UNMAPPED_ORDER_TYPE"),
        ({"side": "SHORT"}, "UNMAPPED_SIDE"),
    ])
    def test_it_is_not_sent_and_not_ambiguous(self, over, marker):
        v = MockVenue()
        r = ex.submit(v, ticket(**over))
        assert r["outcome"] == ex.NOT_SENT
        assert r["sent"] is False
        assert marker in r["reason"]
        assert v.calls == []          # not even the pre-image was read

    def test_a_mappable_ticket_still_goes_through(self):
        v = MockVenue(submit=OK)
        r = ex.submit(v, ticket())
        assert r["outcome"] == ex.SUBMITTED
        assert [c[0] for c in v.calls] == ["open_order_ids", "submit"]


class TestTheSendWindowIsStampedAroundTheCall:
    """Attribution needs a time interval, and it has to come from the
    send itself. Inferring it afterwards from when someone happened to
    look would admit an order created before we sent anything."""

    def test_every_outcome_that_reached_the_network_carries_a_window(self):
        for kw in ({"submit": OK}, {"raise_on_submit": True},
                   {"submit": "not-a-dict"},
                   {"submit": {"ok": True}},
                   {"submit": {"ok": False,
                               "status": "post_only_rejected"}}):
            r = ex.submit(MockVenue(**kw), ticket())
            assert r.get("sent") is True, kw
            w = r.get("sendWindow")
            assert w and w["sentAfter"] and w["readBefore"], kw
            assert w["sentAfter"] <= w["readBefore"], kw
            assert r["nativeIntent"] == "ORDER_INTENT_BUY_LONG", kw

    def test_the_window_is_absent_where_nothing_was_sent(self):
        assert "sendWindow" not in ex.submit(
            MockVenue(), ticket(outcomeSide=None))
