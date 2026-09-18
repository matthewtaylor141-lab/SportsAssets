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
                                 "quantity": 10, "fill_price": 0.41}])
        r = ex.cancel(v, "V-991", "mkt")
        assert r["terminal"] is True
        assert r["status"]["filled"] == 10
        s = ex.settle({}, r["status"])
        assert s["spend"] == 4.10 and s["mayRelease"] is True

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
                                 "quantity": 10, "fill_price": 0.40}])
        r = ex.reconcile(v, "V-1")
        assert r["outcome"] == ex.PARTIAL and r["cashOut"] == 1.60


class TestSpendComesFromTheVenueNotTheTicket:
    def test_a_partial_fill_books_what_the_venue_says(self):
        """The ticket intended $4.00 for ten shares. Six filled."""
        status = {"terminal": True, "filled": 6, "fillPrice": 0.41}
        assert ex.settle({}, status)["spend"] == 2.46

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
