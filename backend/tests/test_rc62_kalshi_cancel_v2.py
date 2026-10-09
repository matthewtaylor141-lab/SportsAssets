"""rc6.2 agent-truth: Kalshi cancel uses the documented V2 path and parses reduced_by.

docs.kalshi.com api-reference/orders/cancel-order-v2 (fetched 2026-10-09):
"delete /portfolio/events/orders/{order_id}"; 200 "Order cancelled
successfully"; CancelOrderV2Response fields order_id, client_order_id,
reduced_by ("Number of contracts that were canceled (i.e. the remaining
count at time of cancellation)", a fixed-point count string), ts_ms.
api-reference/orders/get-order: "get /portfolio/orders/{order_id}" (no
events variant listed), so the one-order read keeps that path.
Kalshi live money stays disabled; nothing here calls submit.
"""
from decimal import Decimal
import pytest
from sportsassets import kalshi_orders as KO
from sportsassets import kalshi_venue as KV
from tests import kalshi_fixtures as F


def test_cancel_request_is_the_v2_events_path():
    assert KO.cancel_request("ord-7") == {
        "method": "DELETE", "path": "/trade-api/v2/portfolio/events/orders/ord-7"}
    assert "/portfolio/orders/" not in KO.cancel_request("x")["path"]


def test_reduced_by_is_parsed_as_a_decimal_count():
    got = KO.parse_cancel(F.ok({"order_id": "ord-9", "client_order_id": "c-1",
                                "reduced_by": "3.50", "ts_ms": 1791571146000}, 200), "ord-9")
    assert got["outcome"] == "CANCELLED" and got["repost_safe"] is True
    assert got["reduced_by"] == Decimal("3.50") and got["reduced_by_unreadable"] is False
    assert got["order_id"] == "ord-9" and got["client_order_id"] == "c-1"
    assert got["ts_ms"] == 1791571146000
    assert KO.parse_cancel(F.ok({"order_id": "o", "reduced_by": "0"}, 200))["reduced_by"] == Decimal("0")


@pytest.mark.parametrize("rb", [None, "", "abc", "-1", "NaN", "Infinity", True])
def test_unreadable_reduced_by_is_flagged_never_zero(rb):
    body = {"order_id": "ord-9"}
    if rb is not None:
        body["reduced_by"] = rb
    got = KO.parse_cancel(F.ok(body, 200), "ord-9")
    assert got["outcome"] == "CANCELLED"
    assert got["reduced_by"] is None and got["reduced_by_unreadable"] is True


def test_an_ack_for_another_order_is_not_our_cancel():
    got = KO.parse_cancel(F.ok({"order_id": "someone-else", "reduced_by": "1"}, 200), "ord-9")
    assert got["outcome"] == "ERROR" and got["repost_safe"] is False
    assert got["why"] == "CANCEL_ACK_FOR_ANOTHER_ORDER"


def test_other_outcomes_unchanged():
    assert KO.parse_cancel(F.ok({}, 404))["outcome"] == "GONE"
    assert KO.parse_cancel(F.ok({}, 500))["repost_safe"] is False
    assert KO.parse_cancel(KV.Refusal("X"))["outcome"] == "REFUSED"


def test_contract_table_cites_the_docs():
    assert KV.CANCEL_PATH == "/portfolio/events/orders/%s"
    assert KV.CONTRACT["cancel"][0].startswith("DELETE /portfolio/events/orders/{order_id}")
    assert "cancel-order-v2" in KV.CONTRACT["cancel"][1]
    assert KV.CONTRACT["order_by_id"][0].startswith("GET /portfolio/orders/{order_id}")
    assert "get-order" in KV.CONTRACT["order_by_id"][1]
