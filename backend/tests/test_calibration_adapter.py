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
    t = {"marketId": "aec-atp-sin-alc-2026-09-18", "outcome": "SIN to win",
         "side": "BUY", "orderType": "LIMIT_GTC_POST_ONLY",
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
                 quantity=10, side="BUY",
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
                 order_type="LIMIT_GTC", client_order_id="CAL-0001")
        blob = repr(orders.created[0])
        assert "CAL-0001" not in blob
        assert not any("client" in k.lower() for k in orders.created[0])
        assert ad.order_params(ticket())["clientOrderIdSent"] is False
        assert "no client-identifier field" in ad.CLIENT_ORDER_ID_IS_NOT_SENT

    def test_a_non_post_only_type_does_not_set_the_flag(self, monkeypatch):
        orders = FakeOrders()
        v = self._venue(monkeypatch, orders)
        v.submit(market_id="m", price=0.4, quantity=10, side="BUY",
                 order_type="LIMIT_GTC", client_order_id=None)
        assert "participateDontInitiate" not in orders.created[0]

    def test_the_real_preview_guard_runs_before_the_create(self,
                                                           monkeypatch):
        """The adapter goes through pmus.submit_fok, which previews and
        refuses a preview it cannot read. A wiring that skipped it would
        have looked identical from outside."""
        orders = FakeOrders()
        v = self._venue(monkeypatch, orders)
        v.submit(market_id="m", price=0.4, quantity=10, side="BUY",
                 order_type="LIMIT_GTC", client_order_id=None)
        assert len(orders.previewed) == 1
        assert orders.previewed[0]["request"]["tif"] == \
            "TIME_IN_FORCE_GOOD_TILL_CANCEL"

    def test_an_unreadable_preview_refuses_and_creates_nothing(self,
                                                               monkeypatch):
        orders = FakeOrders(preview_resp={"order": {}})   # no cost stated
        v = self._venue(monkeypatch, orders)
        r = v.submit(market_id="m", price=0.4, quantity=10, side="BUY",
                     order_type="LIMIT_GTC", client_order_id=None)
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
        assert got["paginationOffered"] is False and got["pages"] == 1

    def test_an_unresolved_walk_raises_rather_than_returning_a_partial(self):
        """A partial pre-image is as dangerous as an unreadable one: a
        new order could hide behind the page we never fetched."""
        def reader(_slugs, cursor=None):
            return {"orders": [{"id": "X%s" % cursor}], "nextCursor": "more"}
        with pytest.raises(ex.VenueUnreadable) as e:
            ad.read_open_order_ids("m", reader=reader, max_pages=3)
        assert "PAGINATION_UNRESOLVED" in str(e.value)


class TestAttributionWithoutAClientIdentifier:
    def test_exactly_one_new_resting_order_is_an_attribution(self):
        got = ad.adopt_from_pre_image(["A", "B"], ["A", "B", "C"])
        assert got["outcome"] == ad.ADOPTED and got["venueOrderId"] == "C"

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
