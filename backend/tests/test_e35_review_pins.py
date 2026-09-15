"""E35-A adversarial review pins (2026-09-10): the findings against the
institutional PREPROD adapter, each pinned as the worker reads it.
Fake session, no network -- the fixtures are the builder's own
(tests/test_e35_pmx_adapter.py)."""
from __future__ import annotations

import pytest

from sportsassets import live_executor as le
from sportsassets import pmus, pmx
from sportsassets.workers import mirror_live as ml
from tests.test_e35_pmx_adapter import SLUG, _Resp, _inst, _order, _routes
from tests.test_e35_pmx_adapter import world as _world  # noqa: F401 -- the builder's fixture


@pytest.fixture
def world(_world):  # noqa: F811 -- pytest binds the builder's fixture by name
    return _world


def _pos(net: float | None, symbol: str = SLUG) -> _Resp:
    rows = [] if net is None else [{"symbol": symbol, "netPosition": str(net), "cost": "0",
                                    "expired": False}]
    return _Resp(200, {"positions": rows, "availablePosition": []})


# ---------------------------------------------------------------- HIGH-1 (D1)

def test_h1_the_covers_row_names_sell_short_so_the_s4_proof_can_pass(world):
    """The S4 read-back proof (mirror_live._s4_probe :12556) refuses the
    cover's row unless `intent == ORDER_INTENT_SELL_SHORT`; the venue has
    no intent field, and a row derived from SIDE_BUY read BUY_LONG --
    `wrong_intent` every hour, no short ever covered. The intent the
    desk sent is remembered under the clordId before the insert."""
    s = world["bind"](_routes(positions=_pos(-300.0)))
    out = pmx.submit_fok(SLUG, 0.25, 1, True, pmx.GTC, "ORDER_INTENT_BUY_SHORT", True, None)
    assert out["order_id"] == "o1" and out["raw"]["side"] == "SIDE_BUY"
    clord = out["raw"]["request"]["clordId"]
    world["bind"](_routes(open_rows=[_order(side="SIDE_BUY", qty="100", price="25", leaves="100",
                                            clordId=clord)]))
    row = pmx.open_orders([SLUG])[0]
    # the worker's own chain (:12545-12563)
    assert row["us_market_slug"].lower() == SLUG
    assert abs(row["price"] - 0.25) < 1e-9 and abs(row["quantity"] - 1.0) < 1e-9
    assert row["venue_side"] == ml.S4_VENUE_BUY
    assert row["intent"] == "ORDER_INTENT_SELL_SHORT" and row["side"] == "SELL"
    assert row["state"] in ml.S4_RESTING_STATES
    # a row this process did not place keeps the derived intent
    other = _order(oid="o2", side="SIDE_BUY", clordId="someone-elses")
    assert pmx._norm_order(other, _inst())["intent"] == "ORDER_INTENT_BUY_LONG"
    assert len(s.calls) >= 1


def test_h1_a_lost_response_still_names_the_short_adds_intent(world):
    """The lost-placement search matches intent to intent when both
    name one (_on_book_matches :5607-5610): a BUY_SHORT add whose
    response was lost must be found under BUY_SHORT, not SELL_LONG."""
    def _drop(call):
        raise ConnectionError("reset by peer")
    s = world["bind"](_routes(insert=_drop))
    with pytest.raises(ConnectionError):
        pmx.submit_fok(SLUG, 0.55, 3, False, pmx.GTC, "ORDER_INTENT_BUY_SHORT", True, None)
    clord = [c for c in s.calls if c["url"].endswith("/v1/trading/orders")][0]["json"]["clordId"]
    world["bind"](_routes(open_rows=[_order(side="SIDE_SELL", clordId=clord)]))
    v = pmx.open_orders([SLUG])[0]
    mine = {"intent": "ORDER_INTENT_BUY_SHORT", "side": "SELL"}
    assert v["intent"] == "ORDER_INTENT_BUY_SHORT" and v["venue_side"] == "ORDER_SIDE_SELL"
    assert ml._on_book_matches(mine, v, 0.55, 3)


# ---------------------------------------------------------------- HIGH-2 (D2)

def test_h2_a_status_read_after_a_cancel_is_the_venues_not_the_snapshots(world):
    """_cancel_open (:6245-6267) re-reads order_status CANCEL_READS times
    0.3 s apart; the 2 s snapshot answered the pre-cancel row every time
    and the book froze `cancel_pending`."""
    s = world["bind"](_routes(open_rows=[_order()]))
    assert pmx.order_status("o1")["state"] == "pending_new"
    assert pmx.cancel_order("o1", SLUG) == {"ok": True}
    gone = _order(state="ORDER_STATE_CANCELED", leaves="0")
    world["bind"](_routes(open_rows=[], report=_Resp(200, {"order": [gone], "nextPageToken": ""})))
    st = pmx.order_status("o1")
    assert st is not None and st["state"] == "canceled" and le._rest_terminal(st)
    assert len(pmx._session.sent("/v1/trading/orders/open")) == 1, "a fresh open read after the cancel"
    assert len(s.calls) >= 2


# -------------------------------------------------------------- MEDIUM-1 (D5)

@pytest.mark.parametrize("intent,net,qty,status", [
    ("ORDER_INTENT_BUY_LONG", 300.0, 3, "ok"),
    ("ORDER_INTENT_BUY_LONG", 200.0, 3, "position_mismatch"),   # sells 1 more than held: a short opened
    ("ORDER_INTENT_BUY_LONG", None, 3, "position_mismatch"),    # nothing held: the whole sell is a short
    ("ORDER_INTENT_BUY_LONG", -300.0, 3, "position_mismatch"),  # the venue holds the short, not the long
    ("ORDER_INTENT_BUY_SHORT", -300.0, 3, "ok"),
    ("ORDER_INTENT_BUY_SHORT", -100.0, 3, "position_mismatch"),  # covers 2 more than short: a long opened
    ("ORDER_INTENT_BUY_SHORT", 300.0, 3, "position_mismatch"),
    (None, 300.0, 3, "ok"),
    (None, None, 1, "position_mismatch"),
])
def test_m1_a_sell_past_the_venues_position_is_refused_never_the_opposite_side(world, intent, net,
                                                                              qty, status):
    s = world["bind"](_routes(positions=_pos(net)))
    out = pmx.submit_fok(SLUG, 0.55, qty, True, pmx.GTC, intent, True, None)
    if status == "ok":
        assert out["order_id"] == "o1" and out["raw"]["venue_net"] == net / 100
    else:
        assert out["status"] == status and out["order_id"] is None and out["ok"] is False
        assert out["raw"]["quantity"] == qty and out["raw"]["venue_net"] == (net or 0.0) / 100
        assert not s.sent("/v1/trading/orders/preview"), "nothing sent"
    assert len(s.sent("/v1/positions")) == 1, "one fresh position read per sell"


# -------------------------------------------------------------- MEDIUM-2 (D8)

def test_m2_terminal_reads_share_one_report_search(world):
    """Twelve report searches a minute; a tick that finishes thirteen
    orders used to freeze the thirteenth `order_state_unknown`. The ids
    the open list dropped ride one search and are cached by id."""
    rows = [_order(oid=f"o{i}") for i in range(1, 15)]
    s = world["bind"](_routes(open_rows=rows))
    assert len(pmx.open_orders()) == 14
    done = [_order(oid=f"o{i}", state="ORDER_STATE_FILLED", cum="300", leaves="0", avgPx="55")
            for i in range(1, 15)]
    world["bind"](_routes(open_rows=[], report=_Resp(200, {"order": done, "nextPageToken": ""})))
    pmx._open_snap["ts"] = 0.0
    for i in range(1, 15):
        st = pmx.order_status(f"o{i}")
        assert st is not None and st["state"] == "filled" and st["filled_shares"] == 3.0
    searches = pmx._session.sent("/v1/report/orders/search")
    assert len(searches) == 1 and len(searches[0]["json"]["orderIds"]) == 14
    assert pmx.BUCKETS["report"].tokens >= 11.0
    assert len(s.calls) >= 1


def test_m2_a_truncated_report_page_without_the_id_is_not_no_record(world):
    world["bind"](_routes(open_rows=[], report=_Resp(200, {"order": [_order(oid="other")],
                                                            "nextPageToken": "p2"})))
    with pytest.raises(pmx.PmxError, match="truncated"):
        pmx.order_status("o1")


# -------------------------------------------------------------- MEDIUM-3 (D3)

def test_m3_recent_trades_never_answers_a_stale_page_as_no_fills(world):
    world["bind"](_routes(report=_Resp(200, {"order": [], "nextPageToken": ""})))
    assert pmx.recent_trades(SLUG, 0.0) == []
    key = next(iter(pmx._rep))
    pmx._rep[key] = (pmx._rep[key][0], pmx._rep[key][1] - pmx.REPORT_TTL_S - 1)
    while pmx.BUCKETS["report"].take():
        pass
    with pytest.raises(pmx.RateBudget):
        pmx.recent_trades(SLUG, 0.0)


# -------------------------------------------------------------- MEDIUM-4 (D4)

def test_m4_an_open_order_states_its_own_scales_when_refdata_has_dropped_the_symbol(world):
    world["bind"](_routes(inst_rows=[], open_rows=[_order(price="550", priceScale="1000",
                                                          symbol="aec-mlb-gone-2026-09-01")]))
    rows = pmx.open_orders()
    assert rows[0]["price"] == 0.55 and rows[0]["quantity"] == 3.0
    # neither the order nor refdata states a price scale: still a raise, never 0.0
    pmx._ref.clear()
    world["bind"](_routes(inst_rows=[], open_rows=[_order()]))
    with pytest.raises(pmx.PmxError, match="scale_unreadable"):
        pmx.open_orders()


# ---------------------------------------------------------- mutant M7 (survived)

def test_m7_open_order_prices_scale_by_price_scale_not_quantity_scale(world):
    """Every builder row had priceScale == fractionalQtyScale == 100, so a
    price divided by the wrong scale passed; an MLB row tells them apart."""
    world["bind"](_routes(inst_rows=[_inst(ps="1000", tick=0.005)],
                          open_rows=[_order(price="555", qty="300", cum="100", leaves="200",
                                            avgPx="550", priceScale="1000")]))
    row = pmx.open_orders()[0]
    assert row["price"] == 0.555 and row["avg_px"] == 0.55
    assert row["quantity"] == 3.0 and row["filled_shares"] == 1.0 and row["leaves"] == 2.0


# ------------------------------------------------------------------ LOW (D6, D7)

def test_l1_every_request_asserts_the_preprod_host_at_send_time(world, monkeypatch):
    world["bind"](_routes())
    monkeypatch.setattr(pmx, "PMX_BASE_URL", "https://api.prod.polymarketexchange.com")
    with pytest.raises(RuntimeError, match="not a preprod host"):
        pmx.open_orders()
    assert pmx.cancel_order("o1", SLUG)["error"].startswith("RuntimeError: pmx: refusing")
    monkeypatch.setattr(pmx, "PMX_AUTH0_DOMAIN", "pmx-prod.us.auth0.com")
    pmx._tok["value"] = None
    with pytest.raises(RuntimeError, match="not a preprod host"):
        pmx._token(now=0.0)
    assert not [c for c in pmx._session.calls if "prod." in c["url"].replace("preprod.", "")]


def test_l1_the_bbo_path_quotes_the_symbol(world):
    s = world["bind"](_routes(inst_rows=[_inst(symbol="a/b?x=1")],
                              bbo=_Resp(200, {"state": "INSTRUMENT_STATE_OPEN"})))
    pmx.bbo_read(None, "a/b?x=1")
    assert s.sent("/bbo")[0]["url"].endswith("/v1/orderbook/a%2Fb%3Fx%3D1/bbo")


def test_l2_report_executions_are_scaled_and_ride_the_read_back(world):
    ex = {"id": "e1", "order": {"id": "o1"}, "type": "EXECUTION_TYPE_FILL", "lastShares": "300",
          "lastPx": "55", "aggressor": False, "commissionNotionalCollected": "1250"}
    done = _order(oid="o1", cum="300", leaves="0", avgPx="55", state="ORDER_STATE_FILLED")
    world["bind"](_routes(open_rows=[], report=_Resp(200, {"order": [done], "nextPageToken": "",
                                                            "lastExecutions": [ex]})))
    out = pmx.submit_fok(SLUG, 0.55, 3, False, pmx.GTC, "ORDER_INTENT_BUY_LONG", True, None)
    assert out["ok"] is True and out["raw"]["read_back"] == "report"
    recs = out["raw"]["executions"]
    assert len(recs) == 1 and recs[0]["last_px"] == 0.55 and recs[0]["last_shares"] == 3.0
    assert recs[0]["commission_usd"] == 0.125 and recs[0]["aggressor"] is False
    assert ml._aggressor_maker(out["raw"]) is True
    # found on open orders: no execution list, [] -- the worker reads [] as the block
    world["bind"](_routes(open_rows=[_order(cum="100", leaves="200", avgPx="55",
                                            state="ORDER_STATE_PARTIALLY_FILLED")]))
    out = pmx.submit_fok(SLUG, 0.55, 3, False, pmx.GTC, "ORDER_INTENT_BUY_LONG", True, None)
    assert out["raw"]["executions"] == [] and ml._aggressor_maker(out["raw"]) is False


# ------------------------------------------------------- the worker's reading of ""

def test_the_empty_status_is_non_terminal_at_placement_and_unknown_would_close_the_row():
    """Item 3 of the review brief, by the worker's own readers: "" wrapped
    as `_place_reserved` wraps it (None) is non-terminal; "" or "unknown"
    in a dict is TERMINAL (`_terminal_state` -> 'cancelled')."""
    assert not le._rest_terminal(None)
    assert le._rest_terminal({"state": ""}) and le._rest_terminal({"state": "unknown"})
    assert ml._terminal_state({"qty": 3}, {"state": "unknown", "filled_shares": 0.0}) == "cancelled"
    for word in ("new", "pending_new", "pending_risk", "partially_filled", "pending_cancel"):
        assert not le._rest_terminal({"state": word})
    for word in ("canceled", "filled", "rejected", "expired", "replaced"):
        assert le._rest_terminal({"state": word})
    assert pmus.OPEN_ORDER_STATES is pmx.OPEN_ORDER_STATES
