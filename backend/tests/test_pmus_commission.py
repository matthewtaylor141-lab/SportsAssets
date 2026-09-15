"""The venue's commission fields on executions and orders (Phase 7
rung 10 of the to-a-tee program, owner order 2026-09-02, "I want us to
match everything ... mirror the whales to a tee").

The 2026-09-02 21:52Z probe printed every execution with the keys
commissionNotionalCollected and commissionSpreadPx and never a value.
The SDK types commissionNotionalCollected as an Amount on the Execution
and commissionNotionalTotalCollected as an Amount on the Order
(polymarket_us types/orders.py:90,:108); commissionSpreadPx is not in
the SDK type, so its shape is unobserved. Two promises pinned here:

1. The parse never guesses: an Amount or a bare scalar becomes a float,
   and absent, empty, unparseable, boolean, or non-finite values become
   None (a NaN / Infinity float would be written into the order row by
   json.dumps as a token jsonb rejects; the pmus re-review).
2. The keys are additive AND flag-gated: submit_fok's raw gains
   "executions" beside the untouched "response" ONLY under
   post_only=True (the mirror lane, the only caller the M17 metric
   reads). With the flag off the whole return is byte-for-byte the
   pre-Phase-7 literal, pinned below from HEAD 420a6be as literals
   (same keys, same order, same str()), because live_executor persists
   json.dumps(raw) and str(raw) as the error column for every non-mirror
   caller and the program names a submit_fok return-shape change as not
   parallel-safe. order_status gains commission_usd /
   commission_spread_px / executions beside the untouched _norm_order
   row.
"""

import json
import math

import pytest

from sportsassets import pmus

SLUG = "aec-atp-a-b-2026-09-03"
USD = "USD"


def _amt(v):
    return {"value": v, "currency": USD}


_FILL = {
    "id": "x1", "type": "EXECUTION_TYPE_FILL", "lastShares": "18",
    "lastPx": _amt("0.52"), "tradeId": "t1", "aggressor": False,
    "transactTime": "2026-09-03T00:00:00Z",
    "order": {"id": "ord-1", "state": "ORDER_STATE_FILLED"},
    "commissionNotionalCollected": _amt("0.12"),
    "commissionSpreadPx": _amt("0.005"),
}


# ── 1. the parser ────────────────────────────────────────────────────

def test_commission_fields_parse_an_amount_on_the_execution():
    assert pmus._commission_fields(_FILL) == (0.12, 0.005)


def test_commission_fields_parse_the_order_total():
    """The Order carries the total under a different key (SDK
    types/orders.py:90); it is read only when the execution key is
    absent, so an execution record never reads the order's total."""
    assert pmus._commission_fields(
        {"commissionNotionalTotalCollected": _amt("0.30")}) == (0.30, None)
    assert pmus._commission_fields(
        {"commissionNotionalCollected": _amt("0.12"),
         "commissionNotionalTotalCollected": _amt("0.30")}) == (0.12, None)


@pytest.mark.parametrize("spread, want", [
    (_amt("0.005"), 0.005),   # an Amount, like its sibling
    ("0.005", 0.005),         # a bare string
    (0.005, 0.005),           # a bare number
    (0, 0.0),                 # zero is a value, not an absence
])
def test_commission_spread_px_reads_an_amount_or_a_scalar(spread, want):
    """Its shape is unobserved (not in the SDK type): both readable
    shapes parse, and nothing else does."""
    usd, px = pmus._commission_fields({"commissionSpreadPx": spread})
    assert usd is None and px == want


@pytest.mark.parametrize("rec", [
    {},                                                  # absent
    {"commissionNotionalCollected": None,
     "commissionSpreadPx": None},                        # null
    {"commissionNotionalCollected": _amt(""),
     "commissionSpreadPx": ""},                          # empty
    {"commissionNotionalCollected": _amt("abc"),
     "commissionSpreadPx": "n/a"},                       # unparseable
    {"commissionNotionalCollected": {},
     "commissionSpreadPx": {"currency": USD}},           # no value key
    {"commissionNotionalCollected": True,
     "commissionSpreadPx": _amt(True)},                  # a bool is not $1
    {"commissionNotionalCollected": [1], "commissionSpreadPx": [1]},
    # non-finite: json.dumps would write NaN / Infinity, jsonb refuses
    {"commissionNotionalCollected": _amt("nan"),
     "commissionSpreadPx": "inf"},
    {"commissionNotionalCollected": "Infinity",
     "commissionSpreadPx": "-inf"},
    {"commissionNotionalCollected": float("nan"),
     "commissionSpreadPx": float("inf")},
    {"commissionNotionalCollected": _amt("1e400"),
     "commissionSpreadPx": "1e400"},
])
def test_commission_fields_are_none_when_absent_or_unreadable(rec):
    assert pmus._commission_fields(rec) == (None, None)


@pytest.mark.parametrize("v", ["nan", "inf", "-inf", "Infinity", "1e400",
                               float("nan"), float("inf"), _amt("nan"),
                               _amt("Infinity")])
def test_a_non_finite_commission_never_reaches_the_row_as_a_token(v):
    """The record is what the worker json.dumps into the order row;
    a NaN / Infinity token there fails the jsonb write."""
    rec = pmus._execution_record({**_FILL, "commissionNotionalCollected": v,
                                  "commissionSpreadPx": v})
    assert rec["commission_usd"] is None
    assert rec["commission_spread_px"] is None
    text = json.dumps(rec)
    assert "NaN" not in text and "Infinity" not in text
    # and the finite fields beside them are still read
    assert rec["last_shares"] == 18.0 and rec["last_px"] == 0.52


@pytest.mark.parametrize("rec", [None, "x", 3, [], object()])
def test_commission_fields_refuse_a_non_dict(rec):
    assert pmus._commission_fields(rec) == (None, None)


# ── 2. the execution record ──────────────────────────────────────────

def test_execution_record_carries_the_fill_fields_and_the_commission():
    rec = pmus._execution_record(_FILL)
    assert rec == {
        "id": "x1", "type": "EXECUTION_TYPE_FILL",
        "order_state": "ORDER_STATE_FILLED",
        "last_px": 0.52, "last_shares": 18.0, "trade_id": "t1",
        "aggressor": False, "transact_time": "2026-09-03T00:00:00Z",
        "reject_reason": None, "text": None,
        "commission_usd": 0.12, "commission_spread_px": 0.005,
    }


def test_execution_record_is_all_none_on_an_empty_or_odd_execution():
    for ex in ({}, None, "x", {"order": "not-a-dict", "aggressor": "yes"}):
        rec = pmus._execution_record(ex)
        assert set(rec) == {"id", "type", "order_state", "last_px",
                            "last_shares", "trade_id", "aggressor",
                            "transact_time", "reject_reason", "text",
                            "commission_usd", "commission_spread_px"}
        assert all(v is None for v in rec.values())


# ── 3. submit_fok: the records ride ONLY under post_only=True ────────

class _Orders:
    def __init__(self, create_resp):
        self.created = []
        self._resp = create_resp

    def preview(self, params):
        return {"order": {"cashOrderQty": _amt("30.00")}}

    def create(self, params):
        self.created.append(params)
        return self._resp


class _Markets:
    def retrieve_by_slug(self, slug):
        return {"market": {"slug": slug, "marketSides": [
            {"identifier": slug, "description": "A"},
            {"identifier": slug + "-b", "description": "B"}]}}


def _install(monkeypatch, orders):
    monkeypatch.setattr(
        pmus, "_get_client",
        lambda: type("C", (), {"orders": orders, "markets": _Markets()})())
    monkeypatch.setattr(pmus, "position_side", lambda slug: 5.0)


def test_submit_fok_parses_the_commission_on_each_execution(monkeypatch):
    resp = {"id": "ord-1", "executions": [
        dict(_FILL),
        {"id": "x2", "type": "EXECUTION_TYPE_FILL", "lastShares": "2",
         "lastPx": _amt("0.50"),
         "order": {"id": "ord-1", "state": "ORDER_STATE_FILLED"}}]}
    orders = _Orders(resp)
    _install(monkeypatch, orders)
    r = pmus.submit_fok(SLUG, 0.30, 100, False,
                        "TIME_IN_FORCE_GOOD_TILL_CANCEL",
                        "ORDER_INTENT_BUY_LONG", post_only=True)
    # today's reading of the fill is untouched
    assert r["ok"] is True and r["order_id"] == "ord-1"
    assert r["filled_shares"] == 20.0
    assert r["fill_price"] == pytest.approx((18 * 0.52 + 2 * 0.50) / 20)
    assert r["status"] == "filled"
    # the raw response and the preview are exactly what they were, and
    # the records sit AFTER them so the first two keys never move
    assert r["raw"]["response"] is resp
    assert r["raw"]["preview"] == {"cashOrderQty": _amt("30.00")}
    assert list(r["raw"]) == ["preview", "response", "executions"]
    execs = r["raw"]["executions"]
    assert [e["commission_usd"] for e in execs] == [0.12, None]
    assert [e["commission_spread_px"] for e in execs] == [0.005, None]
    assert [e["last_shares"] for e in execs] == [18.0, 2.0]


def test_submit_fok_with_no_executions_carries_an_empty_list(monkeypatch):
    orders = _Orders({"id": "o1", "executions": []})
    _install(monkeypatch, orders)
    r = pmus.submit_fok(SLUG, 0.30, 100, post_only=True)
    assert r["raw"]["executions"] == []
    assert r["raw"]["response"] == {"id": "o1", "executions": []}


def test_submit_fok_sell_path_carries_the_records_under_the_flag(monkeypatch):
    orders = _Orders({"id": "o1", "executions": [dict(_FILL)]})
    _install(monkeypatch, orders)
    r = pmus.submit_fok(SLUG, 0.30, 100, True, intent="ORDER_INTENT_BUY_LONG",
                        post_only=True)
    assert r["raw"]["preview"] == {}
    assert r["raw"]["executions"][0]["commission_usd"] == 0.12


# ── 3b. the flag-off return is HEAD's literal ────────────────────────
#
# Captured from HEAD 420a6be's submit_fok with the stub client above
# and written out as literals on purpose (never rebuilt by a helper
# that mirrors the implementation): the dict, the raw key ORDER, and
# str(raw), which is the error column live_executor persists.

_HEAD_PREVIEW = {"cashOrderQty": {"value": "30.00", "currency": "USD"}}

_HEAD_FILL_RESPONSE_STR = (
    "{'id': 'ord-1', 'executions': [{'id': 'x1', "
    "'type': 'EXECUTION_TYPE_FILL', 'lastShares': '18', "
    "'lastPx': {'value': '0.52', 'currency': 'USD'}, 'tradeId': 't1', "
    "'aggressor': False, 'transactTime': '2026-09-03T00:00:00Z', "
    "'order': {'id': 'ord-1', 'state': 'ORDER_STATE_FILLED'}, "
    "'commissionNotionalCollected': {'value': '0.12', 'currency': 'USD'}, "
    "'commissionSpreadPx': {'value': '0.005', 'currency': 'USD'}}]}")

_HEAD_PREVIEW_STR = "{'cashOrderQty': {'value': '30.00', 'currency': 'USD'}}"

# (kwargs to submit_fok, the preview HEAD returned in raw, its str)
_FLAG_OFF_SHAPES = [
    (dict(), _HEAD_PREVIEW, _HEAD_PREVIEW_STR),
    (dict(post_only=False), _HEAD_PREVIEW, _HEAD_PREVIEW_STR),
    (dict(sell=True, intent="ORDER_INTENT_BUY_LONG"), {}, "{}"),
]


@pytest.mark.parametrize("kwargs, preview, preview_str", _FLAG_OFF_SHAPES)
def test_flag_off_fill_is_the_head_literal(monkeypatch, kwargs, preview,
                                           preview_str):
    resp = {"id": "ord-1", "executions": [dict(_FILL)]}
    orders = _Orders(resp)
    _install(monkeypatch, orders)
    r = pmus.submit_fok(SLUG, 0.30, 100, **kwargs)
    assert r == {"ok": True, "order_id": "ord-1", "status": "filled",
                 "fill_price": 0.52, "filled_shares": 18.0,
                 "raw": {"preview": preview, "response": resp}}
    assert list(r) == ["ok", "order_id", "status", "fill_price",
                       "filled_shares", "raw"]
    assert list(r["raw"]) == ["preview", "response"]
    assert r["raw"]["response"] is resp
    assert str(r["raw"]) == ("{'preview': " + preview_str +
                             ", 'response': " + _HEAD_FILL_RESPONSE_STR + "}")
    assert "executions" not in r["raw"]
    # no parsed commission key anywhere in what the error column stores
    assert "commission_usd" not in json.dumps(r["raw"])


@pytest.mark.parametrize("kwargs, preview, preview_str", _FLAG_OFF_SHAPES)
def test_flag_off_empty_list_is_the_head_literal(monkeypatch, kwargs,
                                                 preview, preview_str):
    resp = {"id": "o1", "executions": []}
    _install(monkeypatch, _Orders(resp))
    r = pmus.submit_fok(SLUG, 0.30, 100, **kwargs)
    assert r == {"ok": False, "order_id": "o1", "status": "unknown",
                 "fill_price": None, "filled_shares": 0.0,
                 "raw": {"preview": preview, "response": resp}}
    assert list(r["raw"]) == ["preview", "response"]
    assert str(r["raw"]) == ("{'preview': " + preview_str +
                             ", 'response': {'id': 'o1', 'executions': []}}")


@pytest.mark.parametrize("kwargs, preview, preview_str", _FLAG_OFF_SHAPES)
def test_flag_off_none_response_is_the_head_literal(monkeypatch, kwargs,
                                                    preview, preview_str):
    _install(monkeypatch, _Orders(None))
    r = pmus.submit_fok(SLUG, 0.30, 100, **kwargs)
    assert r == {"ok": False, "order_id": None, "status": "unknown",
                 "fill_price": None, "filled_shares": 0.0,
                 "raw": {"preview": preview, "response": None}}
    assert list(r["raw"]) == ["preview", "response"]
    assert str(r["raw"]) == ("{'preview': " + preview_str +
                             ", 'response': None}")


@pytest.mark.parametrize("kwargs, preview, preview_str", _FLAG_OFF_SHAPES)
def test_flag_off_rejected_200_is_the_head_literal(monkeypatch, kwargs,
                                                   preview, preview_str):
    """The second refusal shape without the flag: HEAD's reading of a
    rejected order, with HEAD's raw, and no refusal key anywhere."""
    resp = {"id": "o9", "executions": [{
        "id": "x9", "type": "EXECUTION_TYPE_REJECTED",
        "text": "post only would cross",
        "orderRejectReason": "POST_ONLY_WOULD_CROSS", "lastShares": "0",
        "lastPx": _amt("0"),
        "order": {"id": "o9", "state": "ORDER_STATE_REJECTED"}}]}
    _install(monkeypatch, _Orders(resp))
    r = pmus.submit_fok(SLUG, 0.30, 100, **kwargs)
    assert r == {"ok": False, "order_id": "o9", "status": "rejected",
                 "fill_price": None, "filled_shares": 0.0,
                 "raw": {"preview": preview, "response": resp}}
    assert list(r["raw"]) == ["preview", "response"]
    assert str(r["raw"]) == (
        "{'preview': " + preview_str + ", 'response': {'id': 'o9', "
        "'executions': [{'id': 'x9', 'type': 'EXECUTION_TYPE_REJECTED', "
        "'text': 'post only would cross', "
        "'orderRejectReason': 'POST_ONLY_WOULD_CROSS', 'lastShares': '0', "
        "'lastPx': {'value': '0', 'currency': 'USD'}, "
        "'order': {'id': 'o9', 'state': 'ORDER_STATE_REJECTED'}}]}}")


def test_flag_off_never_builds_a_record(monkeypatch):
    """Not just absent from raw: the record builder is not called at
    all when the flag is off, so a future change to it cannot leak
    into a non-mirror caller's path."""
    calls = []
    real = pmus._execution_record
    monkeypatch.setattr(pmus, "_execution_record",
                        lambda ex: calls.append(ex) or real(ex))
    _install(monkeypatch, _Orders({"id": "ord-1", "executions": [dict(_FILL)]}))
    pmus.submit_fok(SLUG, 0.30, 100)
    pmus.submit_fok(SLUG, 0.30, 100, post_only=False)
    pmus.submit_fok(SLUG, 0.30, 100, True, intent="ORDER_INTENT_BUY_LONG")
    assert calls == []
    pmus.submit_fok(SLUG, 0.30, 100, post_only=True)
    assert len(calls) == 1


def test_a_nan_share_count_under_the_flag_is_not_a_refusal(monkeypatch):
    """A FILL execution whose lastShares the venue printed as NaN
    beside a REJECTED execution: the count is unreadable, not zero,
    so the normal reading stands (status 'rejected', the last state)
    and no take is ever armed on it."""
    resp = {"id": "o9", "executions": [
        {"type": "EXECUTION_TYPE_FILL", "lastShares": "nan",
         "lastPx": _amt("0.30"),
         "order": {"id": "o9", "state": "ORDER_STATE_PARTIALLY_FILLED"}},
        {"id": "x9", "type": "EXECUTION_TYPE_REJECTED", "lastShares": "0",
         "lastPx": _amt("0"),
         "order": {"id": "o9", "state": "ORDER_STATE_REJECTED"}}]}
    _install(monkeypatch, _Orders(resp))
    r = pmus.submit_fok(SLUG, 0.30, 100, post_only=True)
    assert r["status"] == "rejected" and r["ok"] is False
    assert math.isnan(r["filled_shares"])
    assert "post_only_cross" not in r["raw"]
    assert list(r["raw"]) == ["preview", "response", "executions"]


# ── 4. order_status: additive on the row ─────────────────────────────

_ORDER = {
    "id": "o1", "marketSlug": SLUG, "intent": "ORDER_INTENT_BUY_LONG",
    "price": _amt("0.44"), "quantity": 100, "cumQuantity": 25,
    "leavesQuantity": 75, "state": "ORDER_STATE_PARTIALLY_FILLED",
    "avgPx": _amt("0.43"), "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
}


def _install_retrieve(monkeypatch, resp):
    class _O:
        def retrieve(self, oid):
            return resp
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: type("C", (), {"orders": _O()})())


def test_order_status_reads_the_orders_commission_total(monkeypatch):
    o = {**_ORDER, "commissionNotionalTotalCollected": _amt("0.30"),
         "commissionsBasisPoints": "600"}
    _install_retrieve(monkeypatch, {"order": o})
    row = pmus.order_status("o1")
    assert row["commission_usd"] == 0.30
    assert row["commission_spread_px"] is None
    assert row["executions"] is None  # the venue sent no list
    # every _norm_order key is exactly what it was
    base = pmus._norm_order(o)
    for k, v in base.items():
        assert row[k] == v
    assert set(row) == set(base) | {"commission_usd",
                                    "commission_spread_px", "executions"}


def test_order_status_commission_is_none_when_the_venue_omits_it(monkeypatch):
    _install_retrieve(monkeypatch, {"order": dict(_ORDER)})
    row = pmus.order_status("o1")
    assert row["commission_usd"] is None
    assert row["commission_spread_px"] is None
    assert row["executions"] is None
    assert row["order_id"] == "o1" and row["state"] == "partially_filled"


def test_order_status_parses_executions_when_the_read_back_carries_them(
        monkeypatch):
    _install_retrieve(monkeypatch, {"order": dict(_ORDER),
                                    "executions": [dict(_FILL), {}]})
    row = pmus.order_status("o1")
    assert [e["commission_usd"] for e in row["executions"]] == [0.12, None]
    assert [e["commission_spread_px"] for e in row["executions"]] == \
        [0.005, None]
    # an empty list is an empty list, not an absence
    _install_retrieve(monkeypatch, {"order": dict(_ORDER), "executions": []})
    assert pmus.order_status("o1")["executions"] == []
    # a non-list under the key is unreadable: None, never a guess
    _install_retrieve(monkeypatch, {"order": dict(_ORDER),
                                    "executions": "nope"})
    assert pmus.order_status("o1")["executions"] is None


@pytest.mark.parametrize("resp", [None, {}, {"order": None},
                                  {"order": "x"}, {"executions": []}])
def test_order_status_is_still_none_without_an_order(monkeypatch, resp):
    _install_retrieve(monkeypatch, resp)
    assert pmus.order_status("o1") is None
