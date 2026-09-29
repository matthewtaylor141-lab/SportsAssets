"""THE ACCOUNT READ: MISSING EXPOSURE IS NOT ZERO EXPOSURE.

Independent review reproduced `read_venue_account()` returning

    ok=True, open_orders=1, working_usd=0.0

for a BUY at $0.50 for 100 contracts whose remaining quantity the venue did not
state. These tests hold that shut and pin the rest of the contract: every value
a dollar figure depends on must be present, well-formed and finite; each venue
response must be complete by its own published shape; and an explicit zero is
still a measurement.

Pure: the venue client is a stand-in at the SDK boundary and `paced_read` is
the identity, so nothing here reaches a network or a database.
"""

import asyncio
import math

import pytest

from sportsassets import bettor_account_exposure as AE
from sportsassets import bettor_funded_account as FACCT
from sportsassets import bettor_funded_execution as FX

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
NEW = "ORDER_STATE_NEW"


def _amt(v):
    return {"value": v, "currency": "USD"}


def _order(**over):
    o = {"id": "o-1", "marketSlug": "aec-mlb-a-b", "intent": LONG,
         "state": NEW, "price": _amt("0.50"), "quantity": 100,
         "leavesQuantity": 100, "cumQuantity": 0}
    for k, v in over.items():
        if v is _DROP:
            o.pop(k, None)
        else:
            o[k] = v
    return o


_DROP = object()


class _Orders:
    def __init__(self, resp):
        self.resp = resp

    def list(self, params=None):
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


class _Portfolio:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def positions(self, params=None):
        self.calls.append(dict(params or {}))
        if not self.pages:
            raise AssertionError("read past the last page")
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


class _Client:
    def __init__(self, orders_resp, position_pages=None):
        self.orders = _Orders(orders_resp)
        self.portfolio = _Portfolio(position_pages if position_pages
                                    is not None else [{"positions": {},
                                                       "eof": True}])


def _ident(call, *, endpoint, max_dispatches=None):
    return call()


def _read(client):
    return asyncio.run(FACCT.read_venue_account(client=client,
                                                paced_read=_ident))


# ═════════════════════════════════════════════════════════════════════
# 1 · THE REPRODUCTION, AND EVERY WAY A FIGURE CAN BE ABSENT OR DAMAGED
# ═════════════════════════════════════════════════════════════════════

def test_the_reproduced_case_is_refused_not_counted_as_zero():
    """$0.50 x 100 BUY, remaining quantity not stated: refused, no total."""
    got = _read(_Client({"orders": [_order(leavesQuantity=None)]}))
    assert got["ok"] is False
    assert got["refusal"] == FACCT.R_ORDER_FIELD_MISSING
    assert got["field"] == "leavesQuantity"
    assert "working_usd" not in got and "held_usd" not in got
    # AND THE SAME WHEN THE KEY IS ABSENT RATHER THAN NULL
    got = _read(_Client({"orders": [_order(leavesQuantity=_DROP)]}))
    assert got["refusal"] == FACCT.R_ORDER_FIELD_MISSING


@pytest.mark.parametrize("field,value,refusal", [
    ("leavesQuantity", "", FACCT.R_ORDER_FIELD_MISSING),
    ("leavesQuantity", "NaN", FACCT.R_ORDER_FIELD_NON_FINITE),
    ("leavesQuantity", "inf", FACCT.R_ORDER_FIELD_NON_FINITE),
    ("leavesQuantity", "abc", FACCT.R_ORDER_FIELD_MALFORMED),
    ("leavesQuantity", True, FACCT.R_ORDER_FIELD_MALFORMED),
    ("leavesQuantity", -5, FACCT.R_ORDER_INCONSISTENT),
    ("leavesQuantity", 101, FACCT.R_ORDER_INCONSISTENT),
    ("price", None, FACCT.R_ORDER_FIELD_MISSING),
    ("price", _DROP, FACCT.R_ORDER_FIELD_MISSING),
    ("price", _amt(None), FACCT.R_ORDER_FIELD_MISSING),
    ("price", _amt("nan"), FACCT.R_ORDER_FIELD_NON_FINITE),
    ("price", {"value": "0.5", "currency": "EUR"}, FACCT.R_ORDER_FIELD_MALFORMED),
    ("price", _amt("1.5"), FACCT.R_ORDER_FIELD_MALFORMED),
    ("price", _amt("0"), FACCT.R_ORDER_FIELD_MALFORMED),
    ("quantity", None, FACCT.R_ORDER_FIELD_MISSING),
    ("quantity", float("inf"), FACCT.R_ORDER_FIELD_NON_FINITE),
    ("intent", None, FACCT.R_ORDER_FIELD_MISSING),
    ("intent", "ORDER_INTENT_SOMETHING", FACCT.R_ORDER_FIELD_MALFORMED),
    ("state", None, FACCT.R_ORDER_FIELD_MISSING),
    ("state", "ORDER_STATE_MYSTERY", FACCT.R_ORDER_FIELD_MALFORMED),
    ("cumQuantity", "NaN", FACCT.R_ORDER_FIELD_NON_FINITE),
])
def test_a_working_buy_with_an_unmeasurable_field_is_refused(field, value,
                                                             refusal):
    got = _read(_Client({"orders": [_order(**{field: value})]}))
    assert got["ok"] is False, got
    assert got["refusal"] == refusal, got
    assert "working_usd" not in got


def test_a_damaged_field_on_a_sell_still_refuses_the_response():
    """A SELL commits nothing new, so its remaining quantity is not NEEDED --
    but a value it does state must be a number, because a damaged field means
    a damaged response."""
    got = _read(_Client({"orders": [
        _order(intent="ORDER_INTENT_SELL_LONG", leavesQuantity="NaN")]}))
    assert got["refusal"] == FACCT.R_ORDER_FIELD_NON_FINITE
    # AN UNSTATED ONE ON A SELL IS NOT NEEDED, AND IS NOT INVENTED
    got = _read(_Client({"orders": [
        _order(intent="ORDER_INTENT_SELL_LONG", leavesQuantity=None)]}))
    assert got["ok"] is True, got
    assert got["working_usd"] == 0.0


def test_a_finished_order_commits_nothing_only_if_it_says_nothing_is_left():
    got = _read(_Client({"orders": [
        _order(state="ORDER_STATE_FILLED", leavesQuantity=0)]}))
    assert got["ok"] is True and got["working_usd"] == 0.0
    got = _read(_Client({"orders": [
        _order(state="ORDER_STATE_CANCELED", leavesQuantity=None)]}))
    assert got["refusal"] == FACCT.R_ORDER_FIELD_MISSING
    got = _read(_Client({"orders": [
        _order(state="ORDER_STATE_FILLED", leavesQuantity=7)]}))
    assert got["refusal"] == FACCT.R_ORDER_INCONSISTENT


# ═════════════════════════════════════════════════════════════════════
# 2 · A RESPONSE IS COMPLETE BY ITS OWN SHAPE, OR IT IS NOT A READING
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("resp", [
    {}, {"orders": None}, {"orders": "[]"}, None, [],
    {"orders": [], "nextCursor": "abc"},
    {"orders": [], "eof": False},
])
def test_an_open_orders_response_that_is_not_the_whole_list_is_refused(resp):
    got = _read(_Client(resp))
    assert got["ok"] is False
    assert got["refusal"] == FACCT.R_ORDERS_RESPONSE_INCOMPLETE, got


def test_a_raising_or_rate_limited_orders_read_is_refused_by_name():
    got = _read(_Client(RuntimeError("boom")))
    assert got["refusal"] == FACCT.R_ORDERS_READ_RAISED
    got = _read(_Client(RuntimeError("429 Too Many Requests")))
    assert got["refusal"] == FACCT.R_ORDERS_RATE_LIMITED


@pytest.mark.parametrize("pages,refusal", [
    ([{"positions": {"aec-x": {"cost": _amt("5")}}, "eof": True}],
     FACCT.R_POSITION_FIELD_MISSING),
    ([{"positions": {"aec-x": {"netPosition": "nan", "cost": _amt("5")}},
       "eof": True}], FACCT.R_POSITION_FIELD_NON_FINITE),
    ([{"positions": {"aec-x": {"netPosition": "ten", "cost": _amt("5")}},
       "eof": True}], FACCT.R_POSITION_FIELD_MALFORMED),
    ([{"positions": {"aec-x": {"netPosition": "10"}}, "eof": True}],
     FACCT.R_POSITION_COST_NOT_STATED),
    ([{"positions": {"aec-x": {"netPosition": "10", "cost": _amt("inf")}},
       "eof": True}], FACCT.R_POSITION_FIELD_NON_FINITE),
    ([{"eof": True}], FACCT.R_POSITIONS_WALK_INCOMPLETE),
    ([{"positions": {"aec-x": {"netPosition": "0"}}, "nextCursor": "c1"},
      {"positions": {"aec-x": {"netPosition": "0"}}, "eof": True}],
     FACCT.R_POSITIONS_WALK_INCOMPLETE),
    ([{"positions": {"": {"netPosition": "0"}}, "eof": True}],
     FACCT.R_POSITIONS_WALK_INCOMPLETE),
])
def test_a_position_walk_that_cannot_be_measured_is_refused(pages, refusal):
    got = _read(_Client({"orders": []}, pages))
    assert got["ok"] is False
    assert got["refusal"] == refusal, got
    assert "held_usd" not in got


# ── THE TERMINATION FIELDS, READ BY TYPE (review of a8de639) ─────────
#
# Reproduced through this reader with a substituted transport: an explicit
# `eof: false` with no cursor, and `eof: "false"` (a truthy string) beside a
# cursor, both ended the walk successfully with held_usd=0.0 after one page.

@pytest.mark.parametrize("pages,refusal", [
    # "not finished", and no way to continue
    ([{"positions": {}, "eof": False}], FACCT.R_PAGE_END_INCONSISTENT),
    ([{"positions": {}, "eof": False, "nextCursor": ""}],
     FACCT.R_PAGE_END_INCONSISTENT),
    # a string is not a boolean, whichever way it reads
    ([{"positions": {}, "eof": "false", "nextCursor": "c1"}],
     FACCT.R_PAGE_END_MALFORMED),
    ([{"positions": {}, "eof": "true"}], FACCT.R_PAGE_END_MALFORMED),
    ([{"positions": {}, "eof": 0}], FACCT.R_PAGE_END_MALFORMED),
    ([{"positions": {}, "eof": None}], FACCT.R_PAGE_END_MALFORMED),
    ([{"positions": {}, "nextCursor": 7}], FACCT.R_PAGE_END_MALFORMED),
    # finished AND continuing is a contradiction
    ([{"positions": {}, "eof": True, "nextCursor": "c1"}],
     FACCT.R_PAGE_END_INCONSISTENT),
    # silent on both
    ([{"positions": {}}], FACCT.R_PAGE_END_NOT_STATED),
    # a cursor that does not advance
    ([{"positions": {}, "nextCursor": "c1"},
      {"positions": {}, "nextCursor": "c1"}], FACCT.R_PAGE_END_INCONSISTENT),
])
def test_a_page_end_that_is_not_a_typed_consistent_statement_refuses(pages,
                                                                     refusal):
    client = _Client({"orders": []}, pages)
    got = _read(client)
    assert got["ok"] is False, got
    assert got["refusal"] == refusal, got
    assert "held_usd" not in got


def test_an_explicitly_complete_empty_account_is_a_measurement():
    """THE POSITIVE CONTROL for the rules above: eof=true with no cursor and
    no positions is a complete read of an empty account."""
    got = _read(_Client({"orders": []}, [{"positions": {}, "eof": True}]))
    assert got["ok"] is True, got
    assert got["held_usd"] == 0.0 and got["pages"] == 1
    got = _read(_Client({"orders": []}, [{"positions": {}, "eof": True,
                                          "nextCursor": ""}]))
    assert got["ok"] is True, got


def test_an_open_orders_page_end_is_typed_too():
    got = _read(_Client({"orders": [], "eof": "false"}))
    assert got["refusal"] == FACCT.R_PAGE_END_MALFORMED, got
    got = _read(_Client({"orders": [], "nextCursor": 3}))
    assert got["refusal"] == FACCT.R_PAGE_END_MALFORMED, got
    got = _read(_Client({"orders": [], "eof": True}))
    assert got["ok"] is True, got


# ── THE ACCOUNT'S OWN TRADES, READ AS STRICTLY ───────────────────────

class _Acts:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def activities(self, params=None):
        self.calls.append(dict(params or {}))
        if not self.pages:
            raise AssertionError("read past the last page")
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


class _ActClient:
    def __init__(self, pages):
        self.portfolio = _Acts(pages)


def _trade(tid, ts_iso, slug="aec-mlb-a-b", own="ord-1"):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"id": tid, "marketSlug": slug, "createTime": ts_iso,
                      "qty": "5", "isAggressor": True,
                      "aggressorExecution": {"order": {"id": own}}}}


SINCE = 1790000000.0      # 2026-09-21T14:13:20Z
AFTER, BEFORE = "2026-09-21T16:00:00Z", "2026-09-21T14:00:00Z"


def _trades(pages):
    return FACCT.read_own_trades_sync(_ActClient(pages), "aec-mlb-a-b", SINCE,
                                      paced_read=lambda f, **k: f())


@pytest.mark.parametrize("pages,refusal", [
    ([{}], FACCT.R_ACTIVITY_RESPONSE_INCOMPLETE),
    ([{"activities": None, "eof": True}],
     FACCT.R_ACTIVITY_RESPONSE_INCOMPLETE),
    ([{"activities": [], "eof": False}], FACCT.R_PAGE_END_INCONSISTENT),
    ([{"activities": [], "eof": "true"}], FACCT.R_PAGE_END_MALFORMED),
    ([{"activities": []}], FACCT.R_PAGE_END_NOT_STATED),
    ([{"activities": [{"type": "ACTIVITY_TYPE_TRADE",
                       "trade": {"id": "t", "marketSlug": "aec-mlb-a-b"}}],
       "eof": True}], FACCT.R_ACTIVITY_TIME_UNREADABLE),
    ([{"activities": [{"type": "ACTIVITY_TYPE_TRADE", "trade": "x"}],
       "eof": True}], FACCT.R_ACTIVITY_FIELD_MALFORMED),
    ([RuntimeError("boom")], FACCT.R_ACTIVITY_READ_RAISED),
    ([{"activities": [_trade("t%d" % i, AFTER)], "nextCursor": "c%d" % i}
      for i in range(FACCT.ACTIVITY_PAGES_MAX + 1)],
     FACCT.R_ACTIVITY_WALK_INCOMPLETE),
])
def test_an_own_trades_walk_that_does_not_establish_the_window_refuses(
        pages, refusal):
    got = _trades(pages)
    assert got["ok"] is False, got
    assert got["refusal"] == refusal, got
    assert "rows" not in got


def test_an_own_trades_walk_ends_on_eof_or_on_a_row_older_than_the_window():
    got = _trades([{"activities": [], "eof": True}])
    assert got["ok"] is True and got["rows"] == []
    assert got["complete_by"] == "THE_VENUE_STATED_EOF"
    got = _trades([{"activities": [_trade("t1", AFTER), _trade("t0", BEFORE)],
                    "nextCursor": "c1"}])
    assert got["ok"] is True, got
    assert [r["trade_id"] for r in got["rows"]] == ["t1"]
    assert got["rows"][0]["own_order_id"] == "ord-1"
    assert got["complete_by"] == "A_ROW_OLDER_THAN_THE_WINDOW_WAS_READ"


def test_a_walk_that_never_reaches_its_end_is_truncated_not_complete():
    pages = [{"positions": {}, "nextCursor": "c%d" % i}
             for i in range(FACCT.POSITIONS_PAGES_MAX + 1)]
    got = _read(_Client({"orders": []}, pages))
    assert got["refusal"] == FACCT.R_POSITIONS_WALK_INCOMPLETE
    assert got["pages"] == FACCT.POSITIONS_PAGES_MAX


# ═════════════════════════════════════════════════════════════════════
# 3 · POSITIVE CONTROLS: A STATED ZERO, AND A CORRECTLY MEASURED ORDER
# ═════════════════════════════════════════════════════════════════════

def test_an_explicitly_stated_zero_is_a_measurement():
    """The refusal is about ABSENCE, not about zero: a BUY that says it has
    nothing left commits nothing, and a flat position states net 0."""
    got = _read(_Client(
        {"orders": [_order(leavesQuantity=0, cumQuantity=100,
                           state="ORDER_STATE_PARTIALLY_FILLED")]},
        [{"positions": {"aec-flat": {"netPosition": "0"}}, "eof": True}]))
    assert got["ok"] is True, got
    assert got["working_usd"] == 0.0
    assert got["held_usd"] == 0.0
    assert got["open_orders"] == 1


def test_a_correctly_measured_account_is_counted_side_aware_across_pages():
    """A LONG at 0.50 x 100 commits $50.00; a SHORT at wire 0.60 x 10 commits
    (1 - 0.60) x 10 = $4.00; two held positions across two pages cost $12.34
    and $7.66. Nothing is estimated."""
    orders = {"orders": [
        _order(),
        _order(id="o-2", intent=SHORT, price=_amt("0.60"), quantity=10,
               leavesQuantity="10"),
        _order(id="o-3", intent="ORDER_INTENT_SELL_LONG", leavesQuantity=5),
    ]}
    pages = [{"positions": {"aec-a": {"netPosition": "20",
                                      "cost": _amt("12.34")}},
              "nextCursor": "p2"},
             {"positions": {"aec-b": {"netPosition": "-10",
                                      "cost": _amt("7.66")}},
              "eof": True}]
    client = _Client(orders, pages)
    got = _read(client)
    assert got["ok"] is True, got
    assert got["working_usd"] == pytest.approx(54.0)
    assert got["working_usd"] == pytest.approx(
        FX.collateral_for(0.50, 100, LONG) + FX.collateral_for(0.60, 10, SHORT))
    assert got["held_usd"] == pytest.approx(20.0)
    assert got["held_slugs"] == ["aec-a", "aec-b"]
    assert got["unresolved_usd"] == 0.0
    # THE WALK FOLLOWED THE CURSOR IT WAS GIVEN
    assert client.portfolio.calls[1].get("cursor") == "p2"
    # AND THE GATE SHAPE CARRIES EXACTLY THOSE FIGURES
    gate = FX.venue_positions_for_gate(got)
    assert gate["held_usd"] == pytest.approx(20.0)
    assert gate["working_usd"] == pytest.approx(54.0)


# ═════════════════════════════════════════════════════════════════════
# 4 · DOWNSTREAM: NO LAYER TURNS AN ABSENT FIGURE BACK INTO ZERO
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("read", [
    {"ok": True, "working_usd": 1.0, "unresolved_usd": 0.0},
    {"ok": True, "held_usd": None, "working_usd": 1.0, "unresolved_usd": 0.0},
    {"ok": True, "held_usd": float("nan"), "working_usd": 1.0,
     "unresolved_usd": 0.0},
    {"ok": True, "held_usd": 1.0, "working_usd": -1.0, "unresolved_usd": 0.0},
    {"ok": True, "held_usd": True, "working_usd": 1.0, "unresolved_usd": 0.0},
    {"ok": False, "held_usd": 1.0, "working_usd": 1.0, "unresolved_usd": 0.0},
])
def test_the_gate_shape_refuses_an_incomplete_read(read):
    assert FX.venue_positions_for_gate(read) is None


class _Conn:
    """Enough of a connection for the lane-local reads to report their
    tables absent, so only the VENUE path is under test here."""

    async def fetchval(self, *a, **k):
        return False

    async def fetch(self, *a, **k):
        return []

    async def fetchrow(self, *a, **k):
        return None


@pytest.mark.parametrize("vp", [
    {"working_usd": 0.0, "unresolved_usd": 0.0},
    {"held_usd": None, "working_usd": 0.0, "unresolved_usd": 0.0},
    {"held_usd": "x", "working_usd": 0.0, "unresolved_usd": 0.0},
    {"held_usd": math.inf, "working_usd": 0.0, "unresolved_usd": 0.0},
])
def test_account_exposure_does_not_default_a_missing_venue_figure(vp):
    got = asyncio.run(AE.account_exposure(_Conn(), account_id="a",
                                          venue_positions=vp))
    venue = got["paths"]["VENUE_HELD_POSITIONS"]
    assert venue["read"] != AE.READ_OK, venue
    assert venue["error"] == "VENUE_READ_INCOMPLETE"
    assert got["TOTAL_USD"] is None
    assert "VENUE_HELD_POSITIONS" in got["unreadable_required_paths"]
    # THE CONTROL: all three stated, including zeros, is a reading
    ok = asyncio.run(AE.account_exposure(
        _Conn(), account_id="a",
        venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                         "unresolved_usd": 0.0}))
    assert ok["paths"]["VENUE_HELD_POSITIONS"]["read"] == AE.READ_OK
