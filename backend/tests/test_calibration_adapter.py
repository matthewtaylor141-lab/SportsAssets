"""THE REAL ADAPTER, against a recording SDK client.

The earlier suite passed a mock object whose four methods accepted
whatever they were handed. That proves sequencing and proves nothing
about the wire: it is exactly the shape of test that let
`TIME_IN_FORCE_GOOD_TILL_CANCELLED` and a silently-dropped
`client_order_id` sit in `live_venue()` looking correct.

So these tests patch `pmus._get_client` with a fake SDK client that
RECORDS the params dict `orders.create` is actually called with, and
assert the mapping field by field. Nothing is accepted because the mock
was permissive; every field is compared to what the venue is documented
to take elsewhere in this codebase.
"""

from __future__ import annotations

import pytest

from sportsassets import calibration_adapter as ad
from sportsassets import calibration_execute as ex
from sportsassets import execution_gate as _gate


@pytest.fixture(autouse=True)
def _authorized_system():
    """THIS FILE'S SUBJECT IS WHAT REACHES THE VENUE, so it has to run
    as a system that is allowed to send something.

    pmus.submit_fok gained a fail-closed authorization check at the
    venue boundary on 2026-09-21. Unbound, it denies -- correctly, since
    a process that cannot read the kill switch has no business placing
    an order -- and these ten tests then assert on params that were
    never built.

    The gate is armed here with a permissive snapshot, so a refusal
    inside this file still means what it has always meant: the adapter
    or the preview guard refused, not the kill switch. Tests that the
    gate itself should stop live in test_execution_gate.py, where the
    denial IS the subject.

    The injection hook refuses to run outside pytest and no production
    flag is touched."""
    import time
    _gate._install_snapshot_for_tests(_gate.Snapshot(
        paused=False, venue="polymarket-us", copy_halted=False,
        loss_stop=False, overspend=False, read_at=time.time(),
        ok=True, why="test: authorized"))
    yield
    _gate._restore_for_tests()


# ── a recording stand-in for the venue SDK ───────────────────────────

class FakeOrders:
    """Records what the SDK was actually called with.

    `preview` is here because `pmus.submit_fok` PREVIEWS before it
    creates -- a cost-tolerance guard that has caught real mispricing.
    A fake without it would have made the adapter look wired while
    skipping the guard the money path depends on.
    """

    def __init__(self, create_resp=None, list_resp=None, raises=None,
                 preview_resp=None):
        self.created, self.listed, self.previewed = [], [], []
        self._create = create_resp or {"id": "V-991", "executions": []}
        self._list = list_resp if list_resp is not None else {"orders": []}
        self._raises = raises
        self._preview = preview_resp

    def preview(self, params):
        """The venue's preview, in the shape pmus actually reads.

        `submit_fok` takes `preview["order"]` and reads `cashOrderQty`
        off it; a preview that states no cost is REFUSED rather than
        assumed to agree (the 2026-08-25 fail-open, which let five fills
        through at 1.15x-3.87x the clip). The first version of this fake
        returned a top-level "cost" key, and the real guard duly refused
        the order -- which is the guard working, and exactly why this
        suite drives the real adapter instead of a permissive mock.
        """
        self.previewed.append(params)
        if self._preview is not None:
            return self._preview
        req = (params or {}).get("request") or {}
        qty = int(req.get("quantity") or 0)
        px = float(((req.get("price") or {}).get("value")) or 0.0)
        return {"order": dict(req, cashOrderQty={
            "value": "%.4f" % (qty * px), "currency": "USD"})}

    def create(self, params):
        self.created.append(params)
        return self._create

    def list(self, params=None):
        self.listed.append(params)
        if self._raises:
            raise self._raises
        return self._list


class FakeClient:
    def __init__(self, orders):
        self.orders = orders


def ticket(**over):
    # `outcome` is the human description of the side; `outcomeSide` is the
    # machine selector the venue reads through the intent. They are
    # separate fields because only the second one can be mapped.
    t = {"marketId": "aec-atp-sin-alc-2026-09-18", "outcome": "SIN to win",
         "outcomeSide": "LONG", "side": "BUY",
         "orderType": "LIMIT_GTC_POST_ONLY",
         "clientOrderId": "CAL-0001", "price": 0.40, "quantity": 10}
    t.update(over)
    return t


class TestTheMappingIsExplicit:
    def test_every_known_order_type_maps_to_a_venue_tif(self):
        for kind, (tif, _post) in ad.ORDER_TYPES.items():
            assert tif.startswith("TIME_IN_FORCE_"), kind
            assert tif != ad.NOT_A_VENUE_TIF, kind

    def test_the_misspelled_token_is_not_reachable(self):
        """It appeared once in this repository, in the old live_venue(),
        and the venue does not take it."""
        assert ad.NOT_A_VENUE_TIF == "TIME_IN_FORCE_GOOD_TILL_CANCELLED"
        assert ad.NOT_A_VENUE_TIF not in ad.VENUE_TIFS
        assert "TIME_IN_FORCE_GOOD_TILL_CANCEL" in ad.VENUE_TIFS

    def test_post_only_is_set_only_for_the_post_only_type(self):
        assert ad.order_params(ticket())["post_only"] is True
        assert ad.order_params(
            ticket(orderType="LIMIT_GTC"))["post_only"] is False

    def test_an_unmapped_order_type_is_refused_not_guessed(self):
        with pytest.raises(ValueError) as e:
            ad.order_params(ticket(orderType="LIMIT_SOMETHING"))
        assert ad.R_UNMAPPED_ORDER_TYPE in str(e.value)

    def test_an_unmapped_side_is_refused(self):
        with pytest.raises(ValueError) as e:
            ad.order_params(ticket(side="SHORT"))
        assert ad.R_UNMAPPED_SIDE in str(e.value)

    def test_the_intent_is_stated_never_defaulted(self):
        """On a shared-identifier market the venue picks our side from
        the intent, not the slug -- the wrong-side incident."""
        assert ad.order_params(ticket())["intent"] == "ORDER_INTENT_BUY_LONG"

    def test_a_ticket_that_does_not_name_its_outcome_side_is_refused(self):
        """The first version defaulted to LONG. On this venue the two
        sides of a market share one identifier, so a defaulted side is a
        coin flip with money behind it."""
        for missing in (None, "", "YES", "SIN to win"):
            with pytest.raises(ValueError) as e:
                ad.order_params(ticket(outcomeSide=missing))
            assert ad.R_UNMAPPED_OUTCOME in str(e.value)

    def test_every_operation_and_outcome_maps_to_a_stated_native_intent(self):
        want = {
            ("BUY", "LONG"): ("ORDER_INTENT_BUY_LONG", False,
                              "ORDER_INTENT_BUY_LONG"),
            ("BUY", "SHORT"): ("ORDER_INTENT_BUY_SHORT", False,
                               "ORDER_INTENT_BUY_SHORT"),
            ("SELL", "LONG"): ("ORDER_INTENT_BUY_LONG", True,
                               "ORDER_INTENT_SELL_LONG"),
            ("SELL", "SHORT"): ("ORDER_INTENT_BUY_SHORT", True,
                                "ORDER_INTENT_SELL_SHORT"),
        }
        assert set(ad.INTENTS) == set(want)
        for (op, side), (arg, sell, native) in want.items():
            p = ad.order_params(ticket(side=op, outcomeSide=side))
            assert p["intent"] == arg, (op, side)
            assert p["sell"] is sell, (op, side)
            assert p["nativeIntentExpected"] == native, (op, side)

    def test_the_intent_argument_is_one_pmus_will_actually_accept(self):
        """pmus.submit_fok answers 'bad_intent' and sends NOTHING for any
        intent outside {BUY_LONG, BUY_SHORT}, so a SELL_* token passed as
        the argument would be a silent no-op, not a sell."""
        import inspect as _inspect
        from sportsassets import pmus
        src = _inspect.getsource(pmus.submit_fok)
        assert 'if intent is not None and intent not in ("ORDER_INTENT_BUY_LONG",' in src
        for (op, side) in ad.INTENTS:
            arg = ad.order_params(ticket(side=op, outcomeSide=side))["intent"]
            assert arg in ad.ACCEPTED_INTENT_ARGUMENTS, (op, side)


class TestWhatActuallyReachesTheVenue:
    @staticmethod
    def _venue(monkeypatch, orders):
        from sportsassets import pmus
        monkeypatch.setattr(pmus, "_get_client", lambda: FakeClient(orders))
        return ad.LiveVenue()

    def test_the_created_params_carry_the_mapped_tif_and_post_only_flag(
            self, monkeypatch):
        orders = FakeOrders()
        v = self._venue(monkeypatch, orders)
        v.submit(market_id="aec-atp-sin-alc-2026-09-18", price=0.40,
                 quantity=10, side="BUY", outcome_side="LONG",
                 order_type="LIMIT_GTC_POST_ONLY",
                 client_order_id="CAL-0001")
        assert len(orders.created) == 1
        p = orders.created[0]
        assert p["marketSlug"] == "aec-atp-sin-alc-2026-09-18"
        assert p["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
        assert p["participateDontInitiate"] is True
        assert p["intent"] == "ORDER_INTENT_BUY_LONG"
        assert p["type"] == "ORDER_TYPE_LIMIT"
        assert p["quantity"] == 10

    def test_the_client_order_id_is_not_on_the_wire_and_says_so(
            self, monkeypatch):
        """The venue has no field for it. The old code passed it into a
        function with no such parameter, which would have dropped it
        without a word."""
        orders = FakeOrders()
        v = self._venue(monkeypatch, orders)
        v.submit(market_id="m", price=0.40, quantity=10, side="BUY",
                 outcome_side="LONG", order_type="LIMIT_GTC",
                 client_order_id="CAL-0001")
        blob = repr(orders.created[0])
        assert "CAL-0001" not in blob
        assert not any("client" in k.lower() for k in orders.created[0])
        assert ad.order_params(ticket())["clientOrderIdSent"] is False
        assert "no client-identifier field" in ad.CLIENT_ORDER_ID_IS_NOT_SENT

    def test_a_non_post_only_type_does_not_set_the_flag(self, monkeypatch):
        orders = FakeOrders()
        v = self._venue(monkeypatch, orders)
        v.submit(market_id="m", price=0.4, quantity=10, side="BUY",
                 outcome_side="LONG", order_type="LIMIT_GTC",
                 client_order_id=None)
        assert "participateDontInitiate" not in orders.created[0]

    def test_the_real_preview_guard_runs_before_the_create(self,
                                                           monkeypatch):
        """The adapter goes through pmus.submit_fok, which previews and
        refuses a preview it cannot read. A wiring that skipped it would
        have looked identical from outside."""
        orders = FakeOrders()
        v = self._venue(monkeypatch, orders)
        v.submit(market_id="m", price=0.4, quantity=10, side="BUY",
                 outcome_side="LONG", order_type="LIMIT_GTC",
                 client_order_id=None)
        assert len(orders.previewed) == 1
        assert orders.previewed[0]["request"]["tif"] == \
            "TIME_IN_FORCE_GOOD_TILL_CANCEL"

    def test_an_unreadable_preview_refuses_and_creates_nothing(self,
                                                               monkeypatch):
        orders = FakeOrders(preview_resp={"order": {}})   # no cost stated
        v = self._venue(monkeypatch, orders)
        r = v.submit(market_id="m", price=0.4, quantity=10, side="BUY",
                     outcome_side="LONG", order_type="LIMIT_GTC",
                     client_order_id=None)
        assert r["ok"] is False and r["status"] == "preview_unreadable"
        assert orders.created == []


class TestOpenOrdersUnreadableVersusEmpty:
    @staticmethod
    def _reader(monkeypatch, orders):
        from sportsassets import pmus
        monkeypatch.setattr(pmus, "_get_client", lambda: FakeClient(orders))
        return pmus.open_orders

    def test_an_empty_book_is_an_empty_list(self, monkeypatch):
        got = ad.read_open_order_ids(
            "m", reader=self._reader(monkeypatch, FakeOrders(list_resp={
                "orders": []})))
        assert got["ids"] == []

    def test_an_unreadable_read_raises_and_is_not_an_empty_list(
            self, monkeypatch):
        reader = self._reader(monkeypatch,
                              FakeOrders(raises=RuntimeError("venue 503")))
        with pytest.raises(ex.VenueUnreadable) as e:
            ad.read_open_order_ids("m", reader=reader)
        assert "OPEN_ORDERS_UNREADABLE" in str(e.value)

    def test_an_order_without_an_id_breaks_the_pre_image_and_raises(self):
        def reader(_slugs):
            return [{"status": "open"}]
        with pytest.raises(ex.VenueUnreadable) as e:
            ad.read_open_order_ids("m", reader=reader)
        assert "OPEN_ORDER_WITHOUT_AN_ID" in str(e.value)

    def test_ids_are_read_from_whichever_key_the_venue_used(self):
        def reader(_slugs):
            return [{"order_id": "A"}, {"orderId": "B"}, {"id": "C"}]
        assert ad.read_open_order_ids("m", reader=reader)["ids"] == \
            ["A", "B", "C"]

    def test_the_market_filter_is_actually_applied(self, monkeypatch):
        orders = FakeOrders()
        reader = self._reader(monkeypatch, orders)
        ad.read_open_order_ids("aec-atp-sin-alc", reader=reader)
        assert orders.listed == [{"slugs": ["aec-atp-sin-alc"]}]


class TestPaginationIsVerifiedNotAssumed:
    def test_a_cursor_is_followed_to_exhaustion(self):
        pages = [{"orders": [{"id": "A"}], "nextCursor": "c1"},
                 {"orders": [{"id": "B"}], "nextCursor": "c2"},
                 {"orders": [{"id": "C"}]}]
        seen = []

        def reader(_slugs, cursor=None):
            seen.append(cursor)
            return pages[len(seen) - 1]
        got = ad.read_open_order_ids("m", reader=reader)
        assert got["ids"] == ["A", "B", "C"]
        assert got["pages"] == 3 and got["paginated"] is True
        assert seen == [None, "c1", "c2"]

    def test_an_endpoint_that_offers_no_cursor_is_recorded_as_such(self):
        """The verified reader takes no cursor argument. That is the
        endpoint not offering pagination, and it is reported -- not
        silently assumed to mean one page was all there was."""
        def reader(_slugs):
            return [{"id": "A"}]
        got = ad.read_open_order_ids("m", reader=reader)
        assert got["readerAcceptsCursor"] is False
        assert got["paginated"] is False and got["pages"] == 1

    def test_an_unresolved_walk_raises_rather_than_returning_a_partial(self):
        """A partial pre-image is as dangerous as an unreadable one: a
        new order could hide behind the page we never fetched."""
        def reader(_slugs, cursor=None):
            return {"orders": [{"id": "X%s" % cursor}], "nextCursor": "more"}
        with pytest.raises(ex.VenueUnreadable) as e:
            ad.read_open_order_ids("m", reader=reader, max_pages=3)
        assert "PAGINATION_UNRESOLVED" in str(e.value)


class TestAttributionWithoutAClientIdentifier:
    def test_no_new_order_is_not_proof_it_was_never_placed(self):
        got = ad.adopt_from_pre_image(["A"], ["A"])
        assert got["outcome"] == ad.NONE_NEW
        assert got["venueOrderId"] is None
        assert "may have filled" in got["note"]

    def test_two_new_orders_refuse_to_choose(self):
        got = ad.adopt_from_pre_image([], ["C", "D"])
        assert got["outcome"] == ad.AMBIGUOUS_NEW
        assert got["venueOrderId"] is None
        assert got["new"] == ["C", "D"]


class TestTheStubIsGone:
    def test_live_venue_no_longer_raises_not_implemented(self, monkeypatch):
        from sportsassets import pmus
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: FakeClient(FakeOrders()))
        v = ex.live_venue()
        assert v.open_order_ids("m") == []

    def test_live_venue_is_the_real_adapter(self, monkeypatch):
        from sportsassets import pmus
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: FakeClient(FakeOrders()))
        assert isinstance(ex.live_venue(), ad.LiveVenue)


class TestTheNativeIntentThatReachesTheVenue:
    """Through the PRODUCTION entry point, not a stand-in for it.

    `order_params` states the native intent it expects; these drive
    `pmus.submit_fok` for all four (operation, outcome) pairs and read the
    intent off the CreateOrderParams the SDK was actually handed. The
    first version of the adapter emitted BUY_LONG for both sides, which
    would have bought the wrong side of a SHORT ticket and would have
    been a no-op 'bad_intent' on an exit.
    """

    @staticmethod
    def _venue(monkeypatch, orders):
        from sportsassets import pmus
        monkeypatch.setattr(pmus, "_get_client", lambda: FakeClient(orders))
        return ad.LiveVenue()

    @pytest.mark.parametrize("op,side,native", [
        ("BUY", "LONG", "ORDER_INTENT_BUY_LONG"),
        ("BUY", "SHORT", "ORDER_INTENT_BUY_SHORT"),
        ("SELL", "LONG", "ORDER_INTENT_SELL_LONG"),
        ("SELL", "SHORT", "ORDER_INTENT_SELL_SHORT"),
    ])
    def test_the_venue_receives_the_native_intent_for_each_case(
            self, monkeypatch, op, side, native):
        orders = FakeOrders()
        v = self._venue(monkeypatch, orders)
        v.submit(market_id="aec-atp-sin-alc-2026-09-18", price=0.40,
                 quantity=10, side=op, outcome_side=side,
                 order_type="LIMIT_GTC")
        assert len(orders.created) == 1, (op, side)
        assert orders.created[0]["intent"] == native, (op, side)
        expected = ad.order_params(ticket(side=op, outcomeSide=side,
                                          orderType="LIMIT_GTC"))
        assert expected["nativeIntentExpected"] == native

    def test_a_short_ticket_does_not_reach_the_venue_as_a_long_one(
            self, monkeypatch):
        """The exact defect: BUY_LONG emitted for a SHORT outcome buys
        the other side of a shared-identifier market."""
        orders = FakeOrders()
        v = self._venue(monkeypatch, orders)
        v.submit(market_id="m", price=0.4, quantity=10, side="BUY",
                 outcome_side="SHORT", order_type="LIMIT_GTC")
        assert orders.created[0]["intent"] != "ORDER_INTENT_BUY_LONG"

    def test_naming_the_intent_keeps_the_exit_off_the_inferring_branch(self):
        """pmus._exit_intent falls back to reading the venue POSITION
        when the caller names no intent. The adapter always names one, so
        that branch is never the thing that picks our side."""
        import inspect as _inspect
        from sportsassets import pmus
        src = _inspect.getsource(pmus._exit_intent)
        assert "position_side(us_market_slug)" in src      # the fallback
        for side in ("LONG", "SHORT"):
            p = ad.order_params(ticket(side="SELL", outcomeSide=side))
            assert p["intent"] in ad.ACCEPTED_INTENT_ARGUMENTS
            assert p["intent"] is not None


class TestUnreadableNeverBecomesEmpty:
    """Every shape that is not a response carrying orders must RAISE.

    `list(resp or ())` turned None into []; `resp.get("orders") or []`
    turned an error envelope into []. Either one lets an outage read as a
    clean market, and a clean market is what makes an ambiguous send look
    like a first order.
    """

    @pytest.mark.parametrize("resp,marker", [
        (None, "RESPONSE_IS_NONE"),
        ({"error": "rate limited"}, "ERROR_ENVELOPE"),
        ({"errors": [{"code": 500}]}, "ERROR_ENVELOPE"),
        ({"detail": "unauthorized"}, "ERROR_ENVELOPE"),
        ({}, "HAS_NO_ORDERS_FIELD"),
        ({"data": []}, "HAS_NO_ORDERS_FIELD"),
        ({"orders": None}, "FIELD_IS_NULL"),
        ({"orders": "nope"}, "FIELD_IS_NOT_A_LIST"),
        ("<html>503</html>", "NOT_A_MAPPING_OR_LIST"),
        (7, "NOT_A_MAPPING_OR_LIST"),
    ])
    def test_a_shape_that_is_not_orders_raises_rather_than_reading_empty(
            self, resp, marker):
        def reader(_slugs):
            return resp
        with pytest.raises(ex.VenueUnreadable) as e:
            ad.read_open_order_ids("m", reader=reader)
        assert marker in str(e.value)

    def test_the_only_empty_that_counts_is_an_explicit_empty_order_list(self):
        for resp in ({"orders": []}, []):
            assert ad.read_open_order_ids(
                "m", reader=lambda _s: resp)["ids"] == []

    def test_a_non_mapping_row_raises_instead_of_being_dropped(self):
        """pmus.open_orders silently drops a row that is not a mapping.
        A dropped row is an order we cannot see in the after-image."""
        def reader(_slugs):
            return {"orders": [{"id": "A"}, "not-an-order"]}
        with pytest.raises(ex.VenueUnreadable) as e:
            ad.read_open_order_ids("m", reader=reader)
        assert "ROW_NOT_A_MAPPING" in str(e.value)

    def test_the_upstream_fail_open_is_named_and_routed_around(self):
        """pmus.open_orders coerces None and error envelopes to [] before
        this module can see them, so the adapter reads the raw response.
        Pinned so the workaround cannot be quietly removed."""
        import inspect as _inspect
        from sportsassets import pmus
        src = _inspect.getsource(pmus.open_orders)
        assert "or {}" in src and 'resp.get("orders") or []' in src
        assert ad.LiveVenue.__init__.__doc__ is None       # shape guard
        v_src = _inspect.getsource(ad.LiveVenue.__init__)
        assert "raw_open_orders" in v_src
        assert "pmus.open_orders" not in v_src


class TestPaginationIsNotInferredFromAnException:
    def test_a_type_error_raised_inside_the_reader_is_unreadable(self):
        """The old code caught TypeError and read it as 'this endpoint
        offers no cursor', ending the walk on a partial page. A TypeError
        from inside the reader says nothing about pagination."""
        def reader(_slugs, cursor=None):
            raise TypeError("SDK changed under us")
        with pytest.raises(ex.VenueUnreadable) as e:
            ad.read_open_order_ids("m", reader=reader)
        assert "OPEN_ORDERS_UNREADABLE: TypeError" in str(e.value)

    def test_support_is_decided_by_the_signature_not_by_trying_it(self):
        def with_cursor(_slugs, cursor=None):
            return {"orders": []}

        def without_cursor(_slugs):
            return {"orders": []}

        def with_kwargs(_slugs, **kw):
            return {"orders": []}
        assert ad._reader_takes_a_cursor(with_cursor) is True
        assert ad._reader_takes_a_cursor(without_cursor) is False
        assert ad._reader_takes_a_cursor(with_kwargs) is True

    def test_a_cursor_the_reader_cannot_follow_raises(self):
        """The venue is offering pages this reader has no way to ask for.
        That is an incomplete pre-image, not a one-page account."""
        def reader(_slugs):
            return {"orders": [{"id": "A"}], "nextCursor": "c1"}
        with pytest.raises(ex.VenueUnreadable) as e:
            ad.read_open_order_ids("m", reader=reader)
        assert "CURSOR_THE_READER_CANNOT_FOLLOW" in str(e.value)

    def test_the_real_reader_takes_no_cursor_and_that_is_recorded(self):
        """Documented from the function, not assumed: pmus.open_orders
        and the raw reader both take only slugs."""
        assert ad._reader_takes_a_cursor(ad.raw_open_orders) is False


def approved(**over):
    e = {"market": "aec-atp-sin-alc-2026-09-18", "outcomeSide": "LONG",
         "nativeIntent": "ORDER_INTENT_BUY_LONG", "price": 0.40,
         "quantity": 10}
    e.update(over)
    return e


def venue_row(**over):
    r = {"id": "C", "marketSlug": "aec-atp-sin-alc-2026-09-18",
         "intent": "ORDER_INTENT_BUY_LONG",
         "price": {"value": "0.4000", "currency": "USD"},
         "quantity": 10, "createTime": "2026-09-18T17:00:05Z"}
    r.update(over)
    return r


WINDOW = {"sentAfter": "2026-09-18T17:00:00Z",
          "readBefore": "2026-09-18T17:00:30Z"}
ISOLATED = {"CONDITION": ad.ISOLATION_SOLE_CLAIM, "HOLDS": True,
            "EVIDENCE": {"claimId": "CAL-0001",
                         "openLifecycles": 1,
                         "mirrorLive": False,
                         "manualOrdersOnMarket": 0}}


class TestOneNewIdIsOnlyACandidate:
    """A difference of one is not an attribution.

    Book 863 and book 1333 are both cases where the desk believed a
    single reading and the venue disagreed. The pre-image difference
    NARROWS the field; the corroboration is what identifies the order.
    """

    def test_a_corroborated_single_new_order_is_an_attribution(self):
        got = ad.adopt_from_pre_image(
            ["A", "B"], ["A", "B", "C"], expected=approved(),
            rows=[venue_row()], window=WINDOW, writer_isolation=ISOLATED)
        assert got["outcome"] == ad.ADOPTED and got["venueOrderId"] == "C"
        assert got["writerIsolation"] == ad.ISOLATION_SOLE_CLAIM

    def test_a_single_new_order_with_no_evidence_stays_ambiguous(self):
        """This is the old behaviour, and it is now refused."""
        got = ad.adopt_from_pre_image(["A", "B"], ["A", "B", "C"])
        assert got["outcome"] == ad.UNCORROBORATED
        assert got["venueOrderId"] is None
        assert got["candidate"] == "C"
        assert got["blocker"] == "NO_EXPECTED_TICKET_TO_CORROBORATE_AGAINST"

    @pytest.mark.parametrize("over,blocker", [
        ({"marketSlug": "some-other-market"}, "MARKET_MISMATCH"),
        ({"intent": "ORDER_INTENT_BUY_SHORT"}, "INTENT_MISMATCH"),
        ({"price": {"value": "0.4100"}}, "PRICE_MISMATCH"),
        ({"quantity": 11}, "QUANTITY_MISMATCH"),
        ({"createTime": "2026-09-18T16:59:00Z"},
         "CREATED_OUTSIDE_THE_SEND_WINDOW"),
        ({"createTime": "2026-09-18T17:05:00Z"},
         "CREATED_OUTSIDE_THE_SEND_WINDOW"),
    ])
    def test_any_field_that_disagrees_leaves_it_unattributed(self, over,
                                                             blocker):
        got = ad.adopt_from_pre_image(
            [], ["C"], expected=approved(), rows=[venue_row(**over)],
            window=WINDOW, writer_isolation=ISOLATED)
        assert got["outcome"] == ad.UNCORROBORATED, over
        assert got["blocker"] == blocker, over

    @pytest.mark.parametrize("over,blocker", [
        ({"marketSlug": None}, "ORDER_NAMES_NO_MARKET"),
        ({"intent": None}, "ORDER_NAMES_NO_INTENT"),
        ({"price": {}}, "ORDER_PRICE_UNREADABLE"),
        ({"quantity": None}, "ORDER_QUANTITY_UNREADABLE"),
        ({"createTime": None}, "ORDER_NAMES_NO_CREATION_TIME"),
    ])
    def test_a_field_the_venue_did_not_report_is_not_a_match(self, over,
                                                             blocker):
        got = ad.adopt_from_pre_image(
            [], ["C"], expected=approved(), rows=[venue_row(**over)],
            window=WINDOW, writer_isolation=ISOLATED)
        assert got["outcome"] == ad.UNCORROBORATED, over
        assert got["blocker"] == blocker, over

    def test_without_a_send_window_the_time_evidence_is_not_established(self):
        got = ad.adopt_from_pre_image(
            [], ["C"], expected=approved(), rows=[venue_row()],
            window={}, writer_isolation=ISOLATED)
        assert got["blocker"] == "SEND_WINDOW_NOT_ESTABLISHED"

    def test_the_candidate_must_have_a_row_of_its_own(self):
        got = ad.adopt_from_pre_image(
            [], ["C"], expected=approved(), rows=[venue_row(id="OTHER")],
            window=WINDOW, writer_isolation=ISOLATED)
        assert got["blocker"] == "NO_ORDER_ROW_FOR_THE_CANDIDATE_ID"


class TestWriterIsolationIsJustifiedNotAssumed:
    @pytest.mark.parametrize("iso,blocker", [
        (None, "WRITER_ISOLATION_NOT_ASSERTED"),
        ("we are the only writer", "WRITER_ISOLATION_NOT_ASSERTED"),
        ({"CONDITION": "PROBABLY_FINE", "HOLDS": True, "EVIDENCE": {"a": 1}},
         "WRITER_ISOLATION_CONDITION_UNKNOWN"),
        ({"CONDITION": ad.ISOLATION_SOLE_CLAIM, "HOLDS": False,
          "EVIDENCE": {"a": 1}}, "WRITER_ISOLATION_DOES_NOT_HOLD"),
        ({"CONDITION": ad.ISOLATION_SOLE_CLAIM, "HOLDS": "yes",
          "EVIDENCE": {"a": 1}}, "WRITER_ISOLATION_DOES_NOT_HOLD"),
        ({"CONDITION": ad.ISOLATION_SOLE_CLAIM, "HOLDS": True},
         "WRITER_ISOLATION_UNJUSTIFIED"),
        ({"CONDITION": ad.ISOLATION_SOLE_CLAIM, "HOLDS": True,
          "EVIDENCE": {}}, "WRITER_ISOLATION_UNJUSTIFIED"),
    ])
    def test_a_fully_matching_order_is_still_not_ours_without_isolation(
            self, iso, blocker):
        got = ad.adopt_from_pre_image(
            [], ["C"], expected=approved(), rows=[venue_row()],
            window=WINDOW, writer_isolation=iso)
        assert got["outcome"] == ad.UNCORROBORATED
        assert got["blocker"] == blocker


class TestAmbiguityIsNeverResolvedByCancelling:
    def test_the_attribution_path_calls_nothing_that_cancels(self):
        """Checked on the CALLS, not on the prose. A docstring saying a
        function does not cancel is not evidence that it does not; an
        earlier test in this codebase matched its own commentary and
        proved nothing."""
        import ast
        import inspect as _inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(
            _inspect.getsource(ad.adopt_from_pre_image)))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                called.add(getattr(f, "attr", None) or getattr(f, "id", None))
        assert not any("cancel" in (c or "").lower() for c in called), called
        # and nothing it does call reaches one either
        assert called <= {"set", "map", "str", "dict", "sorted", "len",
                          "isinstance", "_order_id", "corroborate",
                          "isolation_holds", "by_id.get", "get"}, called
        assert "NOT cancelled to find out whose it is" in \
            ad.NEVER_CANCEL_TO_RESOLVE

    def test_every_refusing_outcome_carries_the_rule(self):
        for got in (ad.adopt_from_pre_image([], []),
                    ad.adopt_from_pre_image([], ["C", "D"]),
                    ad.adopt_from_pre_image([], ["C"])):
            assert got["neverCancelToResolve"] == ad.NEVER_CANCEL_TO_RESOLVE
