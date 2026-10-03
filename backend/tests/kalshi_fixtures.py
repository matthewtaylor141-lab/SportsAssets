"""Recorded/stubbed Kalshi responses for the credential-free adapter tests.

Every field name below is one the edge-engine adapter reads from a live
response (edge-engine/src/edge/venues/kalshi.py), in the dialects it
documents: fixed-point strings (`position_fp` '526.00', `count_fp`
'263.00'), dollar strings (`market_exposure_dollars` '199.880000',
`yes_price_dollars`), and the legacy cents integers kept as fallbacks.
No value here came from a real account; the tickers follow the venue's
series grammar (KXMLBGAME-...) and are synthetic.
"""
from __future__ import annotations

from sportsassets import kalshi_venue as KV

TICKER_NYY = "KXMLBGAME-26OCT031905BOSNYY-NYY"
TICKER_BOS = "KXMLBGAME-26OCT031905BOSNYY-BOS"
EVENT = "KXMLBGAME-26OCT031905BOSNYY"


def ok(body, status=200):
    return KV.Response(status=status, body=body)


BALANCE_1234 = ok({"balance": 1234})                     # cents (kalshi.py:388)

POSITIONS_EMPTY = ok({"market_positions": [], "cursor": ""})
POSITIONS_FLAT_ROW = ok({"market_positions": [
    {"ticker": TICKER_NYY, "position_fp": "0.00",
     "market_exposure_dollars": "0.000000"}], "cursor": ""})
POSITIONS_HELD = ok({"market_positions": [
    {"ticker": TICKER_NYY, "position_fp": "526.00",
     "market_exposure_dollars": "199.880000"},
    {"ticker": TICKER_BOS, "position": 3, "market_exposure": 141}],  # legacy cents
    "cursor": ""})

ORDERS_EMPTY = ok({"orders": [], "cursor": ""})
ORDERS_RESTING = ok({"orders": [
    {"order_id": "ord-rest-1", "client_order_id": "c-1", "ticker": TICKER_NYY,
     "status": "resting", "action": "buy", "side": "yes",
     "fill_count": "0.00", "remaining_count": "5.00"}], "cursor": ""})

FILLS_EMPTY = ok({"fills": [], "cursor": ""})
SETTLEMENTS_EMPTY = ok({"settlements": [], "cursor": ""})
SETTLEMENTS_ONE = ok({"settlements": [
    {"ticker": TICKER_BOS, "market_result": "no",
     "yes_total_cost_dollars": "1.41", "no_total_cost_dollars": "0.00",
     "revenue_dollars": "0.00", "settled_time": "2026-10-02T03:10:00Z"}],
    "cursor": ""})


def fill(trade_id, order_id, *, ticker=TICKER_NYY, action="buy", side="yes",
         count="3.00", price="0.4500", fee="0.0500", created="2026-10-03T19:06:01Z",
         dialect="fp"):
    if dialect == "fp":
        f = {"trade_id": trade_id, "order_id": order_id, "ticker": ticker,
             "side": side, "action": action, "count_fp": count,
             "yes_price_dollars": price if side == "yes" else None,
             "no_price_dollars": price if side == "no" else None,
             "is_taker": True, "created_time": created}
        if fee is not None:
            f["fee_dollars"] = fee
    else:  # legacy cents dialect
        cents = int(round(float(price) * 100))
        f = {"trade_id": trade_id, "order_id": order_id, "ticker": ticker,
             "side": side, "action": action, "count": int(float(count)),
             "yes_price": cents if side == "yes" else 100 - cents,
             "no_price": 100 - cents if side == "yes" else cents,
             "is_taker": False, "created_time": created}
        if fee is not None:
            f["fee"] = int(round(float(fee) * 100))
    return {k: v for k, v in f.items() if v is not None}


def empty_account():
    return {"balance": ok({"balance": 0}), "positions": [POSITIONS_EMPTY],
            "orders": [ORDERS_EMPTY], "fills": [FILLS_EMPTY],
            "settlements": [SETTLEMENTS_EMPTY]}


def busy_account():
    return {"balance": BALANCE_1234, "positions": [POSITIONS_HELD],
            "orders": [ORDERS_RESTING],
            "fills": [ok({"fills": [fill("t-1", "ord-a")], "cursor": ""})],
            "settlements": [SETTLEMENTS_ONE]}


# acknowledgement bodies (kalshi.py:494-527)
ACK_RESTING = ok({"order": {"order_id": "ord-1", "status": "resting",
                            "fill_count": "0.00", "client_order_id": "c-x"}}, 201)
ACK_EXECUTED = ok({"order": {"order_id": "ord-2", "status": "executed",
                             "fill_count": "2.00"}}, 201)
ACK_NO_FILL_FIELD = ok({"order": {"order_id": "ord-3", "status": "resting"}}, 201)
ACK_REMAINING_ONLY = ok({"order": {"order_id": "ord-4", "status": "canceled",
                                   "remaining_count": "1.00"}}, 200)
ACK_WITHOUT_ID = ok({"order": {"status": "resting"}}, 201)


class RaisingTransport:
    """Proves no HTTP call is attempted: ANY send fails the test."""

    calls = 0

    def send(self, *a, **k):
        RaisingTransport.calls += 1
        raise AssertionError("a request was attempted: %r %r" % (a[:2], k.get("params")))


class RecordingTransport:
    """Returns queued responses and records what would have been sent."""

    def __init__(self, *responses, raise_exc=None):
        self.responses = list(responses)
        self.sent = []
        self.raise_exc = raise_exc

    def send(self, method, url, *, headers, params, json_body, timeout):
        self.sent.append({"method": method, "url": url, "headers": dict(headers),
                          "params": params, "json": json_body})
        if self.raise_exc:
            raise self.raise_exc
        return self.responses.pop(0) if self.responses else ok({})
