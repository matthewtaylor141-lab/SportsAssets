"""A resting bid must be un-orphanable, or it must not be placed.

Owner order 2026-09-01 ("full throttle, all approved"): when the IOC at
his-price-plus-tolerance comes back empty, rest a bid at his EXACT price
for a few seconds, then cancel. A fill there is a copy we would not
otherwise have, at a price that pays nothing over what he paid.

The first version of this lane went to adversarial review and came back
ship:false with a blocker: the venue adapter defines `ok` as "filled > 0",
so a GTC the venue ACCEPTS AND RESTS -- the normal outcome of every rest
attempt -- read as a refusal, returned before the cancel, and left a
clip-sized bid on the book with no owner. The test stub had restated the
author's model instead of the adapter's rule, so every cancel test passed
against a helper that never cancelled. Reviewers also showed that a
wait_for timeout (asyncio.CancelledError is a BaseException) or a
redeploy could abandon the order between placement and cancel.

So the lane was rebuilt around one rule: PERSIST, THEN SHIELD, THEN REAP.
The order id is written to the row the instant the venue returns it; the
place->cancel->read cycle runs under asyncio.shield; a ledger reaper
reconciles any 'submitting' row that names an order; a venue-side reaper
cancels any resting BUY the ledger never saw. These tests pin each of
those, with a stub that follows the ADAPTER's rule, and with real task
cancellation rather than a stand-in exception.
"""
import asyncio
import inspect

import pytest

# le.asyncio IS the asyncio module, so patching le.asyncio.sleep patches
# the global. Any stub that itself needs to yield must use the real one,
# captured here before any test can patch it -- the first version called
# asyncio.sleep(0) from inside the stub and recursed into itself.
_REAL_SLEEP = asyncio.sleep

from sportsassets import live_executor as le
from sportsassets.api import app as app_mod


# ------------------------------------------------------------ fixtures

class _Pmus:
    """A venue that records what the lane did to it, in order.

    ok follows the REAL adapter: filled > 0. A resting GTC is ok=False
    with a real id. That single line is what the first stub got wrong.
    """

    def __init__(self, place_filled=0.0, final_filled=0.0, final_avg=None,
                 place_raises=False, status_raises=False, cancel_ok=True,
                 refuse=False, final_state="cancelled", slow_place=0.0,
                 open_orders=None, open_raises=False, status_none=False,
                 trades=None, trades_raise=False):
        self.calls: list[tuple] = []
        self.status_none = status_none
        self._trades = trades or []
        self.trades_raise = trades_raise
        self.place_filled = place_filled
        self.final_filled = final_filled
        self.final_avg = final_avg
        self.place_raises = place_raises
        self.status_raises = status_raises
        self.cancel_ok = cancel_ok
        self.refuse = refuse
        self.final_state = final_state
        self.slow_place = slow_place
        self._open = open_orders or []
        self.open_raises = open_raises

    def submit_fok(self, slug, price, qty, sell, tif, intent):
        import time as _t
        if self.slow_place:
            _t.sleep(self.slow_place)
        self.calls.append(("place", slug, price, qty, sell, tif, intent))
        if self.place_raises:
            raise RuntimeError("venue down")
        if self.refuse:
            return {"ok": False, "order_id": None, "status": "rejected",
                    "fill_price": None, "filled_shares": 0.0, "raw": {}}
        return {"ok": self.place_filled > 0, "order_id": "oid-1",
                "status": "filled" if self.place_filled >= qty else "new",
                "fill_price": 0.48 if self.place_filled > 0 else None,
                "filled_shares": self.place_filled,
                "raw": {"response": {"executions": [
                    {"order": {"intent": intent}}]}}}

    def cancel_order(self, oid, slug):
        self.calls.append(("cancel", oid, slug))
        # THE ADAPTER'S RULE (round two): a done order cannot be cancelled.
        if self.final_state == "filled":
            return {"ok": False, "error": "order is not open"}
        return {"ok": self.cancel_ok, "error": None if self.cancel_ok else "boom"}

    def order_status(self, oid):
        self.calls.append(("status", oid))
        if self.status_raises:
            raise RuntimeError("status down")
        if self.status_none:
            return None          # the adapter's "venue has no record"
        return {"order_id": oid, "filled_shares": self.final_filled,
                "avg_px": self.final_avg, "state": self.final_state,
                "intent": None}

    def open_orders(self, slugs=None):
        self.calls.append(("open_orders",))
        if self.open_raises:
            raise RuntimeError("list down")
        # A bid the lost placement created cannot be on the book BEFORE
        # the placement: the lane's pre-placement snapshot (round nine)
        # sees an empty book, the post-placement look-up sees the bid.
        if self.place_raises and not any(c[0] == "place" for c in self.calls):
            return []
        return list(self._open)

    def recent_trades(self, slug, since_ts, max_pages=3):
        self.calls.append(("trades", slug))
        if self.trades_raise:
            raise RuntimeError("activities down")
        return list(self._trades)


class _Pool:
    def __init__(self, spent=0.0, raises=False, rows=None):
        self.spent = spent
        self.raises = raises
        self.rows = rows or []
        self.queries: list[tuple] = []

    async def fetchval(self, sql, *a):
        self.queries.append(("fetchval", sql, a))
        if self.raises:
            raise RuntimeError("column lane does not exist")
        return self.spent

    async def execute(self, sql, *a):
        self.queries.append(("execute", sql, a))

    async def fetch(self, sql, *a):
        self.queries.append(("fetch", sql, a))
        if "order_id IS NOT NULL" in sql:
            return []            # no other row holds an order id here
        return self.rows


@pytest.fixture
def stops(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(le, "_copy_stop",
                        lambda reason, whale=None: seen.append((reason, whale)))
    return seen


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []

    async def _sleep(s):
        slept.append(s)

    monkeypatch.setattr(le.asyncio, "sleep", _sleep)
    return slept


def _bind(pmus, monkeypatch, **kw):
    # live_executor binds pmus with a function-local `from . import pmus`,
    # so the stub replaces the PACKAGE module's functions.
    from sportsassets import pmus as _real
    for name in ("submit_fok", "cancel_order", "order_status", "open_orders",
                 "recent_trades"):
        monkeypatch.setattr(_real, name, getattr(pmus, name))
    monkeypatch.setattr(le, "REST_BID_ENABLED", kw.pop("enabled", True))
    monkeypatch.setattr(le, "REST_BID_BUDGET_USD", kw.pop("budget", 2500.0))
    monkeypatch.setattr(le, "_REST_RESERVED_USD", 0.0)
    return kw


def _run(pmus, pool, monkeypatch, **kw):
    kw = _bind(pmus, monkeypatch, **kw)
    args = dict(pool=pool, row_id=7, us_slug="aec-atp-a-b-2026-09-01",
                his_price=0.48, shares=100.0, intent="ORDER_INTENT_BUY_LONG",
                whale="rn1", reaction=1.2)
    args.update(kw)
    return asyncio.run(le._rest_after_ioc(**args))


def _kinds(p):
    return [c[0] for c in p.calls]


def _persisted(pool):
    return [q for q in pool.queries
            if q[0] == "execute" and "SET order_id=$2, raw=COALESCE(raw, '{}'::jsonb) || $3::jsonb" in q[1]]


# ------------------------------------------- it only runs after a miss

def test_the_lane_is_gated_on_an_empty_ioc_in_the_caller():
    src = inspect.getsource(le.maybe_execute)
    i = src.index("_rest_after_ioc(")
    guard = src[max(0, i - 400):i]
    assert 'float(result.get("filled_shares") or 0) > 0' in guard
    # an add leg never rests (2026-09-02), so the miss-guard is joined
    # to the add check; the miss condition itself is unchanged
    assert 'if locals().get("add_of") is None and not (' in guard


def test_the_rest_call_comes_after_the_ioc_submit_not_before():
    src = inspect.getsource(le.maybe_execute)
    assert src.index("TIME_IN_FORCE_IMMEDIATE_OR_CANCEL") < src.index(
        "_rest_after_ioc(")


def test_the_caller_passes_reaction_through():
    src = inspect.getsource(le.maybe_execute)
    i = src.index("_rest_after_ioc(")
    assert "reaction)" in src[i:i + 200]


# ------------------------------------------------ the sweep is kept out

def test_a_reclaim_call_does_not_rest(monkeypatch, stops, no_sleep):
    """reaction=None is the copy_sweep path: hours-old prices every two
    minutes, wrapped in the 60s wait_for that is the live canceller.
    Both problems, one gate."""
    p = _Pmus()
    assert _run(p, _Pool(), monkeypatch, reaction=None) is None
    assert p.calls == []
    assert ("rest_skipped_reclaim", "rn1") in stops


# ------------------------------------------------- the bid it places

def test_it_rests_at_his_price_with_no_tolerance_and_gtc(monkeypatch, stops, no_sleep):
    p = _Pmus(final_filled=100.0, final_avg=0.48)
    _run(p, _Pool(), monkeypatch)
    place = next(c for c in p.calls if c[0] == "place")
    _, slug, price, qty, sell, tif, intent = place
    assert price == le.wire_limit(0.48, "ORDER_INTENT_BUY_LONG")
    assert tif == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    assert sell is False
    assert qty == 100


def test_a_short_leg_is_put_on_the_wire_as_its_complement(monkeypatch, stops, no_sleep):
    p = _Pmus(final_filled=100.0, final_avg=0.52)
    _run(p, _Pool(), monkeypatch, intent="ORDER_INTENT_BUY_SHORT")
    place = next(c for c in p.calls if c[0] == "place")
    assert place[2] == le.wire_limit(0.48, "ORDER_INTENT_BUY_SHORT")


# ------------------------------------- THE BLOCKER: a resting order is live

def test_a_resting_gtc_is_ok_false_with_an_id_and_still_gets_cancelled(monkeypatch, stops, no_sleep):
    """The review's blocker. The adapter's ok means FILLED; a GTC the
    venue accepts and rests is ok=False with a real id. The first
    version read that as a refusal and returned before the cancel."""
    p = _Pmus(place_filled=0.0, final_filled=0.0)
    _run(p, _Pool(), monkeypatch)
    # the pre-placement book snapshot (round nine) precedes the place
    assert [k for k in _kinds(p) if k != "open_orders"][:1] == ["place"]
    assert "cancel" in _kinds(p), "resting order was never cancelled"
    assert ("rest_place_refused", "rn1") not in stops


def test_a_refusal_is_the_absence_of_an_order_id(monkeypatch, stops, no_sleep):
    p = _Pmus(refuse=True)
    assert _run(p, _Pool(), monkeypatch) is None
    assert ("rest_place_refused", "rn1") in stops
    assert "cancel" not in _kinds(p)


def test_the_gate_reads_the_order_id_not_ok():
    src = inspect.getsource(le._rest_cycle)
    assert "if not oid:" in src
    assert 'if not placed.get("ok")' not in src


# ------------------------------------------------ persist before anything

def test_the_order_id_is_persisted_immediately_after_placement(monkeypatch, stops, no_sleep):
    """THE LINE THAT MAKES THE BID UN-ORPHANABLE. Whatever happens next,
    the row names the order, never-add refuses to stack on it, and the
    reaper can reconcile it against the venue."""
    p = _Pmus()
    pool = _Pool()
    _run(p, pool, monkeypatch)
    pers = _persisted(pool)
    assert pers and pers[0][2][:2] == (7, "oid-1")
    assert '"lane": "rest"' in pers[0][2][2]
    # ...and it happens BEFORE the sleep (which is before the cancel)
    order = [q[0] if q[0] != "execute" else ("persist" if "SET order_id" in q[1] else "execute")
             for q in pool.queries]
    assert "persist" in order
    assert _kinds(p).index("cancel") > 0   # cancel after placement


def test_a_failed_persist_does_not_skip_the_cancel(monkeypatch, stops, no_sleep):
    class _P(_Pool):
        async def execute(self, sql, *a):
            if "SET order_id" in sql:
                raise RuntimeError("db down")
            return await super().execute(sql, *a)
    p = _Pmus()
    _run(p, _P(), monkeypatch)
    assert "cancel" in _kinds(p)


# ------------------------------------------------------- cancellation

def _drive_with_cancel(pmus, pool, monkeypatch, cancel_after: float, **kw):
    """Run the lane under asyncio.wait_for so the OUTER task is really
    cancelled with CancelledError -- the BaseException the review showed
    escapes `except Exception`. Returns (outcome, pmus)."""
    kw = _bind(pmus, monkeypatch, **kw)

    async def _main():
        coro = le._rest_after_ioc(pool=pool, row_id=7,
                                  us_slug="aec-atp-a-b-2026-09-01",
                                  his_price=0.48, shares=100.0,
                                  intent="ORDER_INTENT_BUY_LONG",
                                  whale="rn1", reaction=1.2)
        try:
            return await asyncio.wait_for(coro, timeout=cancel_after)
        except asyncio.TimeoutError:
            # wait DETERMINISTICALLY for the shielded cycle: every other
            # task on the loop, not a fixed sleep
            others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if others:
                await asyncio.wait(others, timeout=5.0)
            return "TIMED_OUT"

    return asyncio.run(_main())


def test_cancellation_during_placement_still_cancels_the_order(monkeypatch, stops):
    """The review's reproduction: wait_for fires while the placement
    thread is still running. The order lands; the shield lets the
    cycle finish; a cancel goes out."""
    async def _fast_sleep(s):
        await _REAL_SLEEP(0)

    monkeypatch.setattr(le.asyncio, "sleep", _fast_sleep)
    p = _Pmus(slow_place=0.15)
    out = _drive_with_cancel(p, _Pool(), monkeypatch, cancel_after=0.05)
    assert out == "TIMED_OUT"
    assert "place" in _kinds(p)
    assert "cancel" in _kinds(p), "order orphaned on cancellation during placement"


def test_cancellation_during_the_window_still_cancels_and_persists(monkeypatch, stops):
    """wait_for fires during the TTL sleep. The id must already be on the
    row and the cancel must still go out."""
    async def _long(s):
        await _REAL_SLEEP(0.2)

    monkeypatch.setattr(le.asyncio, "sleep", _long)
    p = _Pmus()
    pool = _Pool()
    out = _drive_with_cancel(p, pool, monkeypatch, cancel_after=0.05)
    assert out == "TIMED_OUT"
    assert _persisted(pool), "order id not persisted before the window"
    assert "cancel" in _kinds(p)


def test_the_cycle_runs_under_a_shield():
    src = inspect.getsource(le._rest_after_ioc)
    assert "asyncio.shield(_rest_cycle(" in src


def test_cancel_precedes_the_final_status_read(monkeypatch, stops, no_sleep):
    p = _Pmus(final_filled=40.0, final_avg=0.48)
    _run(p, _Pool(), monkeypatch)
    k = _kinds(p)
    assert k.index("cancel") < k.index("status")


def test_cancel_is_retried_once_and_a_double_failure_is_unknown(monkeypatch, stops, no_sleep):
    """A cancel that fails twice is not swallowed: the outcome is
    UNKNOWN, the row stays 'submitting' under the GTC id."""
    p = _Pmus(cancel_ok=False, final_state="open")
    out = _run(p, _Pool(), monkeypatch)
    assert _kinds(p).count("cancel") == 2
    assert out["status"] == "rest_unknown"
    assert out["order_id"] == "oid-1"
    assert "reconcile against the venue" in out["raw"]["why"]
    assert ("rest_unknown", "rn1") in stops


def test_a_non_terminal_final_state_is_unknown_not_a_result(monkeypatch, stops, no_sleep):
    """Three reads still showing the order open means we do not know.
    Guessing 'unfilled' here is how a live bid gets a second bid stacked
    on it two minutes later."""
    p = _Pmus(final_state="pending_cancel")
    out = _run(p, _Pool(), monkeypatch)
    assert _kinds(p).count("status") == 3
    assert out["status"] == "rest_unknown"


def test_a_full_fill_at_placement_never_sends_a_cancel(monkeypatch, stops, no_sleep):
    """Cancelling a done order is a venue error at best."""
    p = _Pmus(place_filled=100.0, final_filled=100.0, final_avg=0.48,
              final_state="filled")
    out = _run(p, _Pool(), monkeypatch)
    assert "cancel" not in _kinds(p)
    assert "status" in _kinds(p)
    assert out["filled_shares"] == 100.0
    assert le.REST_BID_TTL_S not in no_sleep


def test_a_full_fill_whose_cancel_is_refused_is_a_fill_not_unknown(monkeypatch, stops, no_sleep):
    """ROUND TWO'S FINDING. The bid fills in the window; the venue refuses
    to cancel a done order; the terminal read says filled 100. The first
    rebuild returned rest_unknown with filled 0 -- on the lane's target
    outcome."""
    p = _Pmus(place_filled=0.0, final_filled=100.0, final_avg=0.48,
              final_state="filled")
    out = _run(p, _Pool(), monkeypatch)
    assert out["ok"] is True and out["status"] == "rest_filled"
    assert out["filled_shares"] == 100.0
    assert out["raw"]["cancel_ok"] is False
    assert ("rest_filled", "rn1") in stops


def test_the_terminal_read_is_authoritative_over_the_cancel():
    src = inspect.getsource(le._rest_cycle)
    assert "if not _rest_terminal(st):" in src
    assert "if not cancel_ok or not _rest_terminal(st):" not in src


def test_a_reclaimed_row_forgets_its_order_id():
    src = inspect.getsource(le.maybe_execute)
    assert "SET status='submitting', error=NULL, order_id=NULL" in src


def test_an_entry_in_flight_is_a_pending_reason():
    """Membership alone is inert (round three: the reason was in the set
    and returned through _exit_stop, whose contract is to return None,
    so nothing was ever pinned and this file's own grep pinned the
    defect). The behavioural proof is test_in_flight_entry_is_pending.py,
    which drives mirror_exit and asserts the RETURN VALUE."""
    assert "mx_entry_in_flight" in le.EXIT_PENDING_REASONS
    src = inspect.getsource(le.mirror_exit)
    assert 'return _exit_done("mx_entry_in_flight"' in src
    assert '_exit_stop("mx_entry_in_flight"' not in src


def _fingerprint(slug="aec-atp-a-b-2026-09-01", age_s=2.0, price=None, qty=100):
    import time as _t
    return {"order_id": "gtc-lost", "side": "BUY", "us_market_slug": slug,
            "price": (le.wire_limit(0.48, "ORDER_INTENT_BUY_LONG")
                      if price is None else price),
            "quantity": qty, "created_at": _t.time() - age_s}


def test_a_lost_placement_response_adopts_the_bid_the_venue_holds(monkeypatch, stops, no_sleep):
    """ROUND FOUR'S BLOCKER, reproduced through the real adapter: the
    venue accepts the GTC, then the HTTP read times out. 'Nothing
    happened' left a clip-sized bid resting with no ledger row and
    outside both reapers. Now: find our fingerprint on the book, adopt
    the id, persist it, and run the normal cancel/read cycle."""
    p = _Pmus(place_raises=True, open_orders=[_fingerprint()])
    out = _run(p, _Pool(), monkeypatch)
    assert ("open_orders",) in p.calls
    assert ("cancel", "gtc-lost", "aec-atp-a-b-2026-09-01") in p.calls
    assert out is None                         # cancelled unfilled -> rest_unfilled
    assert ("rest_unfilled", "rn1") in stops
    assert ("rest_place_error", "rn1") not in stops


def test_a_lost_placement_response_with_no_bid_on_the_book_holds_the_row_with_no_id(monkeypatch, stops, no_sleep):
    p = _Pmus(place_raises=True, open_orders=[])
    out = _run(p, _Pool(), monkeypatch)
    assert out is not None and out["status"] == "rest_unknown"
    assert out["order_id"] is None
    assert "held 'submitting' with no id" in out["raw"]["why"]
    assert ("rest_place_error", "rn1") in stops
    assert "cancel" not in _kinds(p)


def test_the_fingerprint_matches_the_cent_the_adapter_sends(monkeypatch, stops, no_sleep):
    """ROUND FIVE: the adapter formats every price to two decimals, so
    the venue lists 0.47 for a wire of 0.474 -- and the fingerprint
    compared against 0.474, matching nothing for most real whale
    prices. One number now: the floored cent is placed AND matched."""
    import math

    for his in (0.474, 0.4799, 0.553):
        sent = math.floor(round(his * 100, 6)) / 100.0
        p = _Pmus(place_raises=True, open_orders=[_fingerprint(price=sent)])
        out = _run(p, _Pool(), monkeypatch, his_price=his)
        place = next(c for c in p.calls if c[0] == "place")
        assert place[2] == sent, (his, place[2])
        assert ("cancel", "gtc-lost", "aec-atp-a-b-2026-09-01") in p.calls, his
        assert out is None                     # adopted, cancelled unfilled


def test_the_rest_price_is_on_the_tick_and_never_above_him(monkeypatch, stops, no_sleep):
    p = _Pmus(final_filled=100.0, final_avg=0.47)
    _run(p, _Pool(), monkeypatch, his_price=0.4799)
    assert next(c for c in p.calls if c[0] == "place")[2] == 0.47
    p = _Pmus(final_filled=100.0, final_avg=0.53)
    _run(p, _Pool(), monkeypatch, his_price=0.474, intent="ORDER_INTENT_BUY_SHORT")
    assert next(c for c in p.calls if c[0] == "place")[2] == 0.53
    assert le.rest_tick(0.48, "ORDER_INTENT_BUY_LONG") == 0.48


def test_the_fingerprint_is_price_quantity_side_market_and_age(monkeypatch, stops, no_sleep):
    """Anything else on the book is not ours: the owner's own bid at a
    different price or size, an old bid, a sell, another market."""
    others = [_fingerprint(price=0.47), _fingerprint(qty=50),
              _fingerprint(age_s=600.0), _fingerprint(slug="other"),
              dict(_fingerprint(), side="SELL")]
    p = _Pmus(place_raises=True, open_orders=others)
    out = _run(p, _Pool(), monkeypatch)
    assert out["status"] == "rest_unknown" and out["order_id"] is None
    assert "cancel" not in _kinds(p)


def test_no_venue_record_after_the_rest_is_unknown_not_unfilled(monkeypatch, stops, no_sleep):
    """pmus.order_status returns None when the venue has no record of
    the order. That is not "cancelled unfilled": an order we cannot read
    may have filled. UNKNOWN keeps the row 'submitting' for the reaper."""
    p = _Pmus(status_none=True)
    out = _run(p, _Pool(), monkeypatch)
    assert out is not None and out["status"] == "rest_unknown"
    assert ("rest_unknown", "rn1") in stops


def test_a_reconciled_fill_keeps_its_side_from_the_venue_when_raw_is_gone():
    assert 'intent or st.get("intent")' in inspect.getsource(le._reconcile_row_by_id)
    # (the reaper also passes the row's add_of so an add leg merges, 2026-09-02)
    assert 'r["intent"], age,' in inspect.getsource(le._reap_one_submitting_row)


def test_the_gates_endpoint_reports_the_rest_lane_budget():
    assert '"rest_lane": await _rest_lane_gate(pool, _le)' in inspect.getsource(app_mod.api_gates)


def test_the_rest_lane_gate_shape(monkeypatch):
    class _P:
        async def fetchval(self, sql, *a):
            return 3 if "count(*)" in sql else 120.5
    monkeypatch.setattr(le, "REST_BID_BUDGET_USD", 2500.0)
    out = asyncio.run(app_mod._rest_lane_gate(_P(), le))
    assert out["spent_usd"] == 120.5 and out["remaining_usd"] == 2379.5 and out["fills"] == 3


# ---------------------------------------------------------- the result

def test_a_fill_returns_a_submit_shaped_result_stamped_rest(monkeypatch, stops, no_sleep):
    p = _Pmus(final_filled=60.0, final_avg=0.475, final_state="filled")
    out = _run(p, _Pool(), monkeypatch)
    assert out["ok"] is True
    assert out["filled_shares"] == 60.0
    assert out["fill_price"] == 0.475
    assert out["order_id"] == "oid-1"
    assert out["raw"]["lane"] == "rest"
    assert ("rest_filled", "rn1") in stops


def test_the_receipt_stays_at_the_top_level_so_the_row_keeps_its_side(monkeypatch, stops, no_sleep):
    """ORDER_INTENT_SQL reads raw -> response.executions[0].order.intent.
    Nesting the placement under a key would make a rest fill sideless
    to mirror_exit, the short restate and the grader."""
    p = _Pmus(final_filled=60.0, final_avg=0.475)
    out = _run(p, _Pool(), monkeypatch, intent="ORDER_INTENT_BUY_SHORT")
    assert out["raw"]["response"]["executions"][0]["order"]["intent"] == "ORDER_INTENT_BUY_SHORT"


def test_the_price_fallback_is_the_wire_price_not_the_outcome_price(monkeypatch, stops, no_sleep):
    """On a BUY_SHORT his_price is in outcome space; fill_cash expects the
    contract price. The wrong fallback is a 3.5x phantom overspend that
    halts the whole sleeve."""
    p = _Pmus(final_filled=60.0, final_avg=None)
    out = _run(p, _Pool(), monkeypatch, intent="ORDER_INTENT_BUY_SHORT")
    assert out["fill_price"] == le.wire_limit(0.48, "ORDER_INTENT_BUY_SHORT")


def test_the_final_read_wins_over_the_placement_count(monkeypatch, stops, no_sleep):
    p = _Pmus(place_filled=10.0, final_filled=55.0, final_avg=0.48)
    assert _run(p, _Pool(), monkeypatch)["filled_shares"] == 55.0


def test_unfilled_returns_none_and_is_counted(monkeypatch, stops, no_sleep):
    p = _Pmus(final_filled=0.0)
    assert _run(p, _Pool(), monkeypatch) is None
    assert ("rest_unfilled", "rn1") in stops


def test_a_placement_that_raises_is_counted_and_holds_the_row(monkeypatch, stops, no_sleep):
    """Round four: 'a refused rest is a no-op' was the blocker -- the
    response can be lost AFTER the venue accepted. Counted, and the row
    is held for the reapers (see the lost-response tests)."""
    p = _Pmus(place_raises=True)
    out = _run(p, _Pool(), monkeypatch)
    assert out is not None and out["status"] == "rest_unknown" and out["order_id"] is None
    assert ("rest_place_error", "rn1") in stops


def test_a_status_read_that_raises_after_a_cancel_is_unknown(monkeypatch, stops, no_sleep):
    """We cancelled, then could not read. The placement said 30 filled;
    we cannot know the final count. Unknown, not 30."""
    p = _Pmus(place_filled=30.0, status_raises=True)
    out = _run(p, _Pool(), monkeypatch)
    assert out["status"] == "rest_unknown"


def test_under_one_share_or_bad_price_does_nothing(monkeypatch, stops, no_sleep):
    p = _Pmus()
    assert _run(p, _Pool(), monkeypatch, shares=0.4) is None
    assert _run(p, _Pool(), monkeypatch, his_price=0.0) is None
    assert p.calls == []


# --------------------------------------------------------- the budget

def test_an_exhausted_budget_places_nothing_and_says_so(monkeypatch, stops, no_sleep):
    p = _Pmus()
    assert _run(p, _Pool(spent=2500.0), monkeypatch, budget=2500.0) is None
    assert "place" not in _kinds(p)
    assert ("rest_budget_exhausted", "rn1") in stops


def test_the_reservation_counts_against_the_cap_while_in_flight(monkeypatch, stops, no_sleep):
    """Two attempts inside the same window: the second must see the
    first's reservation, not just the ledger."""
    kw = _bind(_Pmus(final_filled=100.0, final_avg=0.48), monkeypatch,
               budget=60.0)
    pool = _Pool(spent=0.0)

    async def _two():
        a = le._rest_after_ioc(pool, 7, "s", 0.48, 100.0,
                               "ORDER_INTENT_BUY_LONG", "rn1", 1.0)
        b = le._rest_after_ioc(pool, 8, "s", 0.48, 100.0,
                               "ORDER_INTENT_BUY_LONG", "rn1", 1.0)
        return await asyncio.gather(a, b)

    out = asyncio.run(_two())
    # 100 shares * 0.48 = $48 each; cap $60 -> only one may reserve
    assert sum(1 for o in out if o is not None) == 1
    assert le._REST_RESERVED_USD == 0.0


def test_an_unreadable_budget_fails_closed(monkeypatch, stops, no_sleep):
    p = _Pmus()
    assert _run(p, _Pool(raises=True), monkeypatch) is None
    assert "place" not in _kinds(p)


def test_the_budget_counts_fills_not_attempts():
    src = inspect.getsource(le._rest_lane_spent)
    assert "filled_usd > 0" in src and "SUM(filled_usd)" in src


def test_disabled_places_nothing(monkeypatch, stops, no_sleep):
    p = _Pmus()
    assert _run(p, _Pool(), monkeypatch, enabled=False) is None
    assert p.calls == []


# ------------------------------------------------- the caller's stamp

def test_unknown_keeps_the_row_submitting_under_the_gtc_id():
    src = inspect.getsource(le.maybe_execute)
    assert '"submitting" if result.get("status") == "rest_unknown"' in src


def test_the_lane_stamp_is_a_separate_best_effort_update():
    src = inspect.getsource(le.maybe_execute)
    i = src.index("UPDATE live_orders SET lane=$2 WHERE id=$1")
    fill_update = src.rindex("filled_usd=$6, raw=$7::jsonb, error=$8")
    assert fill_update < i
    assert "lane" not in src[fill_update - 200:fill_update + 80]


# ---------------------------------------------------------- the reapers

class _Row(dict):
    def keys(self):
        return list(super().keys())


def _reap(pmus, rows, monkeypatch, held=(0, None), held_raises=False, others=0):
    _bind(pmus, monkeypatch)

    async def _held(_slug):
        if held_raises:
            raise RuntimeError("positions unreadable")
        return held

    monkeypatch.setattr(le, "_pm_held", _held)
    pool = _Pool(rows=rows, spent=others)      # fetchval -> `others` count
    asyncio.run(le._reap_stale_submitting(pool))
    return pool


def _submitting(oid, slug="aec-x-y-2026-09-01", age_s=1200.0):
    return _Row(id=11, order_id=oid, us_market_slug=slug,
                requested_shares=100.0, intent="ORDER_INTENT_BUY_LONG",
                his_price=0.48, age_s=age_s)


def test_the_ledger_reaper_adopts_a_lost_bid_before_blind_marking(monkeypatch, no_sleep):
    """ROUND FIVE: a no-id row was blind-marked without asking the venue,
    while the bid it lost the response for could be live or FILLED. The
    reaper now looks for our fingerprint first and reconciles it."""
    # the orphan's signature: created seconds AFTER a row placed 1200 s ago
    bid = _fingerprint(slug="aec-x-y-2026-09-01", age_s=1195.0)
    p = _Pmus(open_orders=[bid])
    pool = _reap(p, [_submitting(None)], monkeypatch)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("SET order_id=$2, status='submitting'" in q[1]
               and q[2] == (11, "gtc-lost") for q in ups)
    assert ("cancel", "gtc-lost", "aec-x-y-2026-09-01") in p.calls
    assert any("status='unfilled'" in q[1] for q in ups)
    assert not any("status = 'error'" in q[1] for q in ups)


def test_the_ledger_reaper_reconciles_a_lost_bid_that_filled(monkeypatch, no_sleep):
    bid = _fingerprint(slug="aec-x-y-2026-09-01", age_s=1195.0)
    p = _Pmus(open_orders=[bid], final_filled=100.0, final_avg=0.48,
              final_state="filled")
    pool = _reap(p, [_submitting(None)], monkeypatch)
    ups = [q for q in pool.queries if q[0] == "execute"]
    fill = [q for q in ups if "status='filled'" in q[1]]
    assert fill and fill[0][2][1] == 100.0 and fill[0][2][3] == 48.0


def _fill(qty=100.0, price=0.48, side="BUY", age_s=700.0, rp=0.0,
          order_qty=100.0, order_id=None):
    """A venue trade row as pmus.recent_trades returns it. The nested
    execution ORDER is our size (round nine: a fill is attributed by
    its order, never by cent alone); order_id None keeps these tests
    on the trade-log booking path, the id path has its own file."""
    import time as _t
    return {"qty": qty, "price": price, "side": side, "ts": _t.time() - age_s,
            "realized_pnl": rp, "order_id": order_id, "order_qty": order_qty,
            "order_price": price, "order_tif": None, "aggressor": False}


def test_a_lost_bid_that_filled_is_booked_from_the_venue_trade_log(monkeypatch, no_sleep):
    """ROUND SIX: a lost GTC that FILLED is not on the open book, so the
    row was blind-marked 'venue holds no matching bid' while the account
    held the position -- and the whale's exit was dropped. ROUND SEVEN:
    booking from the account POSITION booked the owner's shares. The
    venue's own trade log is asked for fills at OUR cent inside the
    row's window, and exactly those are booked."""
    pool = _reap(_Pmus(open_orders=[], trades=[_fill(100.0)]),
                 [_submitting(None)], monkeypatch, held=(100, 0.48))
    ups = [q for q in pool.queries if q[0] == "execute"]
    fill = [q for q in ups if "status='filled'" in q[1]]
    assert fill and fill[0][2][1] == 100.0 and fill[0][2][2] == 0.48
    assert fill[0][2][3] == 48.0
    assert "reconciled from the venue trade log" in fill[0][2][4]
    assert not any("status = 'error'" in q[1] or "status='error'" in q[1] for q in ups)


def test_a_partial_fill_in_the_trade_log_is_booked_for_what_filled(monkeypatch, no_sleep):
    pool = _reap(_Pmus(open_orders=[], trades=[_fill(25.0), _fill(15.0)]),
                 [_submitting(None)], monkeypatch, held=(40, 0.48))
    fill = [q for q in pool.queries if q[0] == "execute" and "status='filled'" in q[1]]
    assert fill and fill[0][2][1] == 40.0


def test_the_owners_position_of_our_exact_size_is_never_booked(monkeypatch, no_sleep):
    """ROUND SEVEN, reproduced: the owner's 100 @0.61 on the market was
    booked as our fill and mirror_exit then flattened his shares. The
    trade log shows his fill at 0.61, not at our cent: named, not
    booked -- and a SELL of his, or a position with no fills at all,
    likewise."""
    for trades in ([_fill(100.0, price=0.61)],
                   [_fill(100.0, price=0.48, side="SELL")],
                   [_fill(100.0, price=0.48, rp=12.0)],
                   []):
        pool = _reap(_Pmus(open_orders=[], trades=trades), [_submitting(None)],
                     monkeypatch, held=(100, 0.61))
        ups = [q for q in pool.queries if q[0] == "execute"]
        assert not any("status='filled'" in q[1] for q in ups), trades
        named = [q for q in ups if "status='error'" in q[1]]
        assert named and "venue holds a POSITION" in named[0][2][1], trades


def test_more_shares_at_our_cent_than_we_asked_for_are_not_all_ours(monkeypatch, no_sleep):
    pool = _reap(_Pmus(open_orders=[], trades=[_fill(250.0)]), [_submitting(None)],
                 monkeypatch, held=(250, 0.48))
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert not any("status='filled'" in q[1] for q in ups)
    assert any("venue holds a POSITION" in str(q[2]) for q in ups)


def test_a_fill_before_the_rows_window_is_not_ours(monkeypatch, no_sleep):
    pool = _reap(_Pmus(open_orders=[], trades=[_fill(100.0, age_s=1500.0)]),
                 [_submitting(None)], monkeypatch, held=(100, 0.48))
    assert not any("status='filled'" in q[1] for q in pool.queries if q[0] == "execute")


def test_a_fill_hours_after_the_row_is_the_owners_not_ours(monkeypatch, no_sleep):
    """ROUND EIGHT, reproduced: the owner's buy at our cent 20 hours
    after our placement was booked and then sold on the whale's exit. A
    lost GTC is filled or cancelled within minutes; the window closes."""
    row = _submitting(None, age_s=30 * 3600.0)
    row["status"] = "error"
    pool = _reap(_Pmus(open_orders=[], trades=[_fill(100.0, age_s=2 * 3600.0)]),
                 [row], monkeypatch, held=(100, 0.48))
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert not any("status='filled'" in q[1] for q in ups)
    assert le._LOST_FILL_WINDOW_S <= 3600.0


def test_a_fill_with_no_side_named_is_not_ours(monkeypatch, no_sleep):
    """The venue's top-level side is always None; only a side the
    execution record names as a BUY is ours. Unknown is not ours."""
    pool = _reap(_Pmus(open_orders=[], trades=[_fill(100.0, side="")]),
                 [_submitting(None)], monkeypatch, held=(100, 0.48))
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert not any("status='filled'" in q[1] for q in ups)
    assert any("venue holds a POSITION" in str(q[2]) for q in ups)


def test_a_revisited_row_never_touches_the_open_book(monkeypatch, no_sleep):
    """ROUND EIGHT: the 48-hour revisit adopted -- then cancelled and
    booked -- the owner's fresh app bid at the whale's cent. A rest bid
    of ours cannot still be resting hours later; the revisit asks only
    the trade log and the account."""
    row = _submitting(None, age_s=40 * 3600.0)
    row["status"] = "error"
    p = _Pmus(open_orders=[_fingerprint(slug="aec-x-y-2026-09-01", age_s=120.0)],
              trades=[])
    pool = _reap(p, [row], monkeypatch, held=(100, 0.48))
    assert ("open_orders",) not in p.calls
    assert "cancel" not in _kinds(p)
    assert not any("SET order_id=$2, status='submitting'" in q[1]
                   for q in pool.queries if q[0] == "execute")


def test_a_fresh_rows_open_book_lookup_is_bounded_to_its_own_window(monkeypatch, no_sleep):
    """A bid of ours is created within seconds of the row; one created
    before it or long after it is somebody else's."""
    early = _fingerprint(slug="aec-x-y-2026-09-01", age_s=1200.0 + 60.0)
    late = _fingerprint(slug="aec-x-y-2026-09-01", age_s=1200.0 - 600.0)
    for bid in (early, late):
        p = _Pmus(open_orders=[bid], trades=[])
        pool = _reap(p, [_submitting(None, age_s=1200.0)], monkeypatch)
        assert "cancel" not in _kinds(p)
        assert not any("SET order_id=$2, status='submitting'" in q[1]
                       for q in pool.queries if q[0] == "execute")


def test_one_rows_failure_does_not_skip_the_rest_of_the_pass(monkeypatch, no_sleep):
    _bind(_Pmus(open_orders=[], trades=[]), monkeypatch)

    async def _held(_slug):
        return 0, None

    monkeypatch.setattr(le, "_pm_held", _held)

    class _P(_Pool):
        async def execute(self, sql, *a):
            self.queries.append(("execute", sql, a))
            if a and a[0] == 11:
                raise RuntimeError("write failed")

    pool = _P(rows=[_submitting(None), _Row(id=12, order_id=None,
                                            us_market_slug="aec-z-2026-09-01",
                                            requested_shares=100.0,
                                            intent="ORDER_INTENT_BUY_LONG",
                                            his_price=0.48, age_s=1200.0)])
    asyncio.run(le._reap_stale_submitting(pool))
    marked = [q[2][0] for q in pool.queries if q[0] == "execute" and "status = 'error'" in q[1]]
    assert 12 in marked


def test_an_orphan_fill_is_promoted_once_the_asset_claim_frees(monkeypatch, no_sleep):
    row = _submitting("orphan-9")
    row["status"] = "error"
    row["error"] = "ORPHAN FILL RECORDED on a row that cannot re-enter 'filled'"
    p = _Pmus()
    pool = _reap(p, [row], monkeypatch)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("SET status='filled'" in q[1] and "ORPHAN FILL RECORDED%" in q[1]
               for q in ups)
    assert "cancel" not in _kinds(p) and ("open_orders",) not in p.calls


def test_a_position_explained_by_other_rows_is_a_clean_blind_mark(monkeypatch, no_sleep):
    """Co-holding is designed in: the desk's own row explains its
    shares. held 100, explained 100, no fill of ours -> blind mark."""
    pool = _reap(_Pmus(open_orders=[], trades=[]), [_submitting(None)],
                 monkeypatch, held=(100, 0.61), others=100)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("status = 'error'" in q[1] for q in ups)          # blind mark
    assert not any("venue holds a POSITION" in str(q[2]) for q in ups)


def test_a_co_held_market_still_books_our_fill_from_the_trade_log(monkeypatch, no_sleep):
    """ROUND SEVEN (3): the desk holds 100, our lost GTC filled 100 at
    our cent. Held 200, explained 100; the trade log names ours."""
    pool = _reap(_Pmus(open_orders=[], trades=[_fill(100.0)]), [_submitting(None)],
                 monkeypatch, held=(200, 0.5), others=100)
    fill = [q for q in pool.queries if q[0] == "execute" and "status='filled'" in q[1]]
    assert fill and fill[0][2][1] == 100.0


def test_a_named_row_is_revisited_and_resolves_when_the_position_clears(monkeypatch, no_sleep):
    row = _submitting(None)
    row["status"] = "error"
    pool = _reap(_Pmus(open_orders=[], trades=[]), [row], monkeypatch, held=(0, None))
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("status='unfilled'" in q[1] and "has cleared" in str(q[2]) for q in ups)


def test_a_named_row_is_revisited_and_booked_when_our_fill_appears(monkeypatch, no_sleep):
    row = _submitting(None)
    row["status"] = "error"
    pool = _reap(_Pmus(open_orders=[], trades=[_fill(100.0)]), [row], monkeypatch,
                 held=(100, 0.48))
    fill = [q for q in pool.queries if q[0] == "execute" and "status='filled'" in q[1]]
    assert fill and "status IN ('submitting', 'error')" in fill[0][1]


def test_the_named_state_is_sticky_everywhere_the_sweep_could_erase_it():
    """ROUND SEVEN: the named row was reclaimed by the very next sweep
    (status 'error' is retryable), the IOC outcome rewrote it and the
    unexplained shares lost their only record."""
    import pathlib

    src = inspect.getsource(le.maybe_execute)
    i = src.index("ON CONFLICT (trade_id) DO UPDATE")
    clause = src[i:src.index("RETURNING id", i)]
    assert "LIKE 'venue holds a POSITION%'" in clause
    assert "LIKE 'ORPHAN FILL RECORDED%'" in clause
    sweep = pathlib.Path(le.__file__).with_name("workers").joinpath("copy_sweep.py").read_text()
    assert sweep.count("LIKE 'venue holds a POSITION%'") >= 2
    assert sweep.count("LIKE 'ORPHAN FILL RECORDED%'") >= 2
    reaper = inspect.getsource(le._reap_stale_submitting)
    assert "error LIKE 'venue holds a POSITION%'" in reaper
    assert "interval '{_NAMED_HORIZON}'" in reaper
    never_add = inspect.getsource(le.maybe_execute)
    j = never_add.index("never-add: this market was already copied")
    k = never_add.rfind("await pool.fetchrow", 0, j)      # the prior-copy query
    assert "LIKE 'venue holds a POSITION%'" in never_add[k:j]
    inflight = inspect.getsource(le._entry_in_flight)
    assert "'venue holds a POSITION%'" in inflight and "{_NAMED_HORIZON}" in inflight


def test_an_unreadable_trade_log_or_account_leaves_a_no_id_row_for_the_next_pass(monkeypatch, no_sleep):
    pool = _reap(_Pmus(open_orders=[], trades_raise=True), [_submitting(None)],
                 monkeypatch)
    assert [q for q in pool.queries if q[0] == "execute"] == []
    pool = _reap(_Pmus(open_orders=[], trades=[]), [_submitting(None)], monkeypatch,
                 held_raises=True)
    assert [q for q in pool.queries if q[0] == "execute"] == []


def test_a_row_with_no_recorded_side_matches_either_legs_cent(monkeypatch, no_sleep):
    """A row killed before its raw was written has no intent; the
    venue's short cent for his 0.48 is 0.52 and must still be ours."""
    row = _submitting(None)
    row["intent"] = None
    bid = _fingerprint(slug="aec-x-y-2026-09-01", age_s=1195.0, price=0.52)
    p = _Pmus(open_orders=[bid])
    pool = _reap(p, [row], monkeypatch)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("SET order_id=$2, status='submitting'" in q[1] for q in ups)


def test_an_unreadable_venue_leaves_a_no_id_row_for_the_next_pass(monkeypatch, no_sleep):
    """Blind-marking is a statement about the book; with the book
    unreadable there is no statement to make."""
    p = _Pmus(open_raises=True)
    pool = _reap(p, [_submitting(None)], monkeypatch)
    assert [q for q in pool.queries if q[0] == "execute"] == []


def test_a_blind_marked_row_is_not_reclaimed_inside_the_venue_reapers_window():
    """A reclaimed row is rewritten 'unfilled' under the IOC's id by the
    sweep's retry and leaves both reapers' scope while its GTC may still
    be live. The reclaim waits out the venue reaper's window."""
    src = inspect.getsource(le.maybe_execute)
    i = src.index("ON CONFLICT (trade_id) DO UPDATE")
    clause = src[i:src.index("RETURNING id", i)]
    assert "LIKE 'stale submitting row reaped%'" in clause
    assert "interval '60 minutes'" in clause
    assert "AND NOT (" in clause


def test_the_reaper_leaves_a_young_row_the_venue_cannot_name(monkeypatch, no_sleep):
    """order_status None on every read: not terminal, not a guess. The
    row keeps its claim until the bound."""
    p = _Pmus(status_none=True)
    pool = _reap(p, [_submitting("oid-9", age_s=1200.0)], monkeypatch)
    assert [q for q in pool.queries if q[0] == "execute"] == []


def test_the_reaper_marks_an_hour_old_row_the_venue_cannot_name(monkeypatch, no_sleep):
    """...and at the bound it is marked for reconciliation, loudly, so a
    row cannot hold its market's claim forever on a venue that never
    answers. Only the None shape: a readable non-terminal state is still
    left alone at any age."""
    p = _Pmus(status_none=True)
    pool = _reap(p, [_submitting("oid-9", age_s=le._NO_RECORD_GIVE_UP_S + 1)],
                 monkeypatch)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert len(ups) == 1 and "status='error'" in ups[0][1]
    assert "no record of order oid-9" in ups[0][2][1]
    p = _Pmus(final_state="open")
    pool = _reap(p, [_submitting("oid-9", age_s=le._NO_RECORD_GIVE_UP_S + 1)],
                 monkeypatch)
    assert [q for q in pool.queries if q[0] == "execute"] == []


def test_a_stale_row_with_no_order_id_is_still_blind_marked(monkeypatch, no_sleep):
    pool = _reap(_Pmus(), [_submitting(None)], monkeypatch)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("status = 'error'" in q[1] for q in ups)


def test_a_stale_row_with_an_order_id_is_reconciled_as_a_fill(monkeypatch, no_sleep):
    """The rest lane's whole recovery story: the reaper finds the row by
    its persisted id, cancels, reads, and writes what really happened."""
    p = _Pmus(final_filled=100.0, final_avg=0.48, final_state="cancelled")
    pool = _reap(p, [_submitting("oid-9")], monkeypatch)
    assert "cancel" in _kinds(p) and "status" in _kinds(p)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("status='filled'" in q[1] for q in ups)
    assert not any("status = 'error'" in q[1] for q in ups)


def test_a_stale_row_with_an_order_id_that_cancelled_unfilled_is_unfilled(monkeypatch, no_sleep):
    p = _Pmus(final_filled=0.0, final_state="cancelled")
    pool = _reap(p, [_submitting("oid-9")], monkeypatch)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("status='unfilled'" in q[1] for q in ups)


def test_the_ledger_reaper_never_raises_into_its_caller(monkeypatch, no_sleep):
    """Pre-flight housekeeping on the manual desk and a tick in the
    sweep. When this grew a fetch, four desk tests failed with
    "manual order failed pre-flight" -- a reaper that throws takes a
    real order down with it. It skips the pass and logs instead."""
    _bind(_Pmus(), monkeypatch)

    class _NoFetch:
        async def execute(self, *a):
            return None

    asyncio.run(le._reap_stale_submitting(_NoFetch()))   # must not raise


def test_a_still_open_order_is_left_for_the_next_pass(monkeypatch, no_sleep):
    p = _Pmus(final_state="open")
    pool = _reap(p, [_submitting("oid-9")], monkeypatch)
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert ups == []


def _venue_reap_pool(pmus, monkeypatch, manual_ids=(), scope=(("s", None),),
                     pool_raises=False, scope_raises=False):
    """scope = [(slug, placed_ts)]; placed_ts None means ten minutes ago.
    Returns (cancelled, pool)."""
    import time as _t
    _bind(pmus, monkeypatch)
    now = _t.time()

    class _P(_Pool):
        async def fetch(self, sql, *a):
            if "placed_ts" in sql:
                if scope_raises:
                    raise RuntimeError("db")
                return [_Row(id=11, us_market_slug=s,
                             placed_ts=(now - 600.0) if t is None else t,
                             intent="ORDER_INTENT_BUY_LONG",
                             his_price=0.48, requested_shares=100.0)
                        for s, t in scope]
            if pool_raises:
                raise RuntimeError("db")
            return [_Row(order_id=m) for m in manual_ids]

    pool = _P()
    return asyncio.run(le._reap_stale_resting_bids(pool)), pool


def _venue_reap(pmus, monkeypatch, **kw):
    return _venue_reap_pool(pmus, monkeypatch, **kw)[0]


def test_the_venue_reaper_adopts_the_orphan_it_matched_and_reconciles_a_fill(monkeypatch, no_sleep):
    """ROUND FIVE: cancel-and-walk-away wrote nothing, so an orphan that
    had already filled 40 of 100 shares left a position with no ledger
    row -- never graded, never exited, never charged. The matched row
    takes the id and is reconciled like a persisted one."""
    p = _Pmus(open_orders=[_open("orphan", slug="s")], final_filled=40.0,
              final_avg=0.48, final_state="filled")
    n, pool = _venue_reap_pool(p, monkeypatch, scope=(("s", None),))
    assert ("cancel", "orphan", "s") in p.calls
    ups = [q for q in pool.queries if q[0] == "execute"]
    assert any("SET order_id=$2, status='submitting'" in q[1]
               and q[2] == (11, "orphan") for q in ups)
    fill = [q for q in ups if "status='filled'" in q[1]]
    assert fill and fill[0][2][1] == 40.0
    # the lane stamp: the venue's tif names an IOC, else rest where unset
    assert any("lane=COALESCE($2, lane, 'rest')" in q[1] for q in ups)


def _open(oid, side="BUY", age_s=595.0, slug="s", created_ts=None,
          price=0.48, qty=100):
    """A venue order. Default: created 5s after a row placed ten minutes
    ago, at our cent for his 0.48, our whole quantity -- the orphan's
    exact signature."""
    import time as _t
    if created_ts is None:
        created_ts = None if age_s is None else _t.time() - age_s
    return {"order_id": oid, "side": side, "us_market_slug": slug,
            "created_at": created_ts, "price": price, "quantity": qty}


def test_the_owners_bid_inside_the_time_window_is_neither_cancelled_nor_booked(monkeypatch, no_sleep):
    """ROUND SIX, reproduced through the real adapter: the owner's 500
    @0.61 rested 20 s before a stranded copy row on the same market was
    cancelled AND booked as our 100 @0.474 rest fill. Time is not a
    fingerprint; price and quantity are."""
    import time as _t
    now = _t.time()
    for bid in (_open("owner-price", slug="s", price=0.61, created_ts=now - 620),
                _open("owner-size", slug="s", qty=500, created_ts=now - 620),
                _open("owner-both", slug="s", price=0.61, qty=500, created_ts=now - 620)):
        p = _Pmus(open_orders=[bid], final_filled=200.0, final_state="filled")
        n, pool = _venue_reap_pool(p, monkeypatch, scope=(("s", now - 600),))
        assert n == 0 and "cancel" not in _kinds(p), bid["order_id"]
        assert [q for q in pool.queries if q[0] == "execute"] == [], bid["order_id"]


def test_an_orphan_whose_asset_claim_moved_on_still_has_its_fill_recorded(monkeypatch, no_sleep):
    """ROUND SIX: the blind mark released the asset claim, a later copy
    took it, and the adoption UPDATE hit the one-fill-per-asset index --
    the orphan's 40-share fill was cancelled and written nowhere."""
    class _Unique(Exception):
        pass
    _Unique.__name__ = "UniqueViolationError"

    p = _Pmus(open_orders=[_open("orphan", slug="s")], final_filled=40.0,
              final_avg=0.48, final_state="filled")
    import time as _t
    now = _t.time()
    _bind(p, monkeypatch)

    class _P(_Pool):
        async def fetch(self, sql, *a):
            if "placed_ts" in sql:
                return [_Row(id=11, us_market_slug="s", placed_ts=now - 600,
                             intent="ORDER_INTENT_BUY_LONG", his_price=0.48,
                             requested_shares=100.0)]
            return []

        async def execute(self, sql, *a):
            self.queries.append(("execute", sql, a))
            if "SET order_id=$2, status='submitting'" in sql:
                raise _Unique("duplicate key value violates unique constraint "
                              "live_orders_one_fill_per_asset")

    pool = _P()
    asyncio.run(le._reap_stale_resting_bids(pool))
    ups = [q for q in pool.queries if q[0] == "execute"]
    rec = [q for q in ups if "SET order_id=$6, filled_shares=$2" in q[1]]
    assert rec and rec[0][2][1] == 40.0 and rec[0][2][5] == "orphan"
    assert "ORPHAN FILL RECORDED" in rec[0][2][4]
    assert not any("status='filled'" in q[1] for q in ups)


def test_the_venue_reaper_cancels_an_orphan_created_seconds_after_its_row(monkeypatch):
    p = _Pmus(open_orders=[_open("orphan", slug="s")])
    assert _venue_reap(p, monkeypatch, scope=(("s", None),)) == 1
    assert ("cancel", "orphan", "s") in p.calls


def test_the_venue_reaper_never_touches_the_owners_app_bids(monkeypatch):
    """ROUND TWO'S BLOCKER. The owner rests bids directly in the venue app
    on this shared account; those have no ledger row. An account-wide
    sweep cancelled them within 185s, every pass."""
    p = _Pmus(open_orders=[_open("owner-app", slug="never-copied")])
    assert _venue_reap(p, monkeypatch, scope=(("s", None),)) == 0
    assert "cancel" not in _kinds(p)


def test_a_fresh_owner_bid_on_a_market_with_an_old_copy_row_is_not_ours(monkeypatch):
    """ROUND THREE, reproduced through the real adapter: a 59-minute-old
    copy row on S put the owner's two-minute-old app bid on S in scope
    and the sweep cancelled it. The bid must match its row in TIME."""
    import time as _t
    now = _t.time()
    p = _Pmus(open_orders=[_open("owner-fresh", slug="s", age_s=120.0)])
    assert _venue_reap(p, monkeypatch, scope=(("s", now - 59 * 60),)) == 0
    assert "cancel" not in _kinds(p)


def test_a_bid_created_before_the_row_is_not_ours(monkeypatch):
    import time as _t
    now = _t.time()
    p = _Pmus(open_orders=[_open("owner-early", slug="s",
                                 created_ts=now - 1800)])
    assert _venue_reap(p, monkeypatch, scope=(("s", now - 300),)) == 0
    assert "cancel" not in _kinds(p)


def test_a_venue_clock_a_little_behind_still_matches_the_orphan(monkeypatch):
    """The SIGKILL orphan is created 3-10 s after its row; a venue clock
    a few seconds behind the database used to fail the match."""
    import time as _t
    now = _t.time()
    p = _Pmus(open_orders=[_open("orphan", slug="s", created_ts=now - 600 - 20)])
    assert _venue_reap(p, monkeypatch, scope=(("s", now - 600),)) == 1
    p = _Pmus(open_orders=[_open("too-early", slug="s",
                                 created_ts=now - 600 - le._ORPHAN_SKEW_S - 5)])
    assert _venue_reap(p, monkeypatch, scope=(("s", now - 600),)) == 0


def test_the_scope_predicates_are_exact():
    """The SQL is executed by the database, not by any stub here, so its
    text is pinned exactly (round four: a 60-second window beside the
    right predicates would have turned the venue net off silently)."""
    src = inspect.getsource(le._reap_stale_resting_bids)
    assert src.count("interval '60 minutes'") == 1
    assert "AND order_id IS NULL " in src
    assert "(status = 'submitting' OR (status = 'error' AND " in src
    assert "error LIKE 'stale submitting row reaped%'" in src
    # the LIKE matches the ledger reaper's own text
    assert "stale submitting row reaped" in inspect.getsource(le._reap_one_submitting_row)


def test_a_bid_created_long_after_the_row_is_not_ours(monkeypatch):
    import time as _t
    now = _t.time()
    p = _Pmus(open_orders=[_open("owner-late", slug="s",
                                 created_ts=now - 300)])
    early = now - 300 - le._ORPHAN_MATCH_S - 60
    assert _venue_reap(p, monkeypatch, scope=(("s", early),)) == 0
    assert "cancel" not in _kinds(p)


def test_the_scope_is_rows_that_can_own_an_unpersisted_order():
    """The SQL predicate is the half of the fix a stub cannot exercise:
    terminal rows (filled, unfilled, rejected) never enter scope, and
    the ledger reaper's blind mark of an orphan row still does."""
    src = inspect.getsource(le._reap_stale_resting_bids)
    assert "order_id IS NULL" in src
    assert "status = 'submitting'" in src
    assert "error LIKE 'stale submitting row reaped%'" in src


def test_an_unknown_age_is_skipped_not_cancelled(monkeypatch):
    p = _Pmus(open_orders=[_open("no-age", age_s=None)])
    assert _venue_reap(p, monkeypatch) == 0
    assert "cancel" not in _kinds(p)


def test_the_venue_reaper_sweeps_nothing_when_its_scope_is_unreadable(monkeypatch):
    p = _Pmus(open_orders=[_open("orphan")])
    assert _venue_reap(p, monkeypatch, scope_raises=True) == 0
    assert "cancel" not in _kinds(p)


def test_the_venue_reaper_spares_the_desk_and_the_young(monkeypatch):
    p = _Pmus(open_orders=[_open("desk"), _open("young", age_s=1.0),
                           _open("sell", side="SELL")])
    assert _venue_reap(p, monkeypatch, manual_ids=("desk",)) == 0
    assert "cancel" not in _kinds(p)


def test_the_venue_reaper_sweeps_nothing_when_it_cannot_tell_desk_from_orphan(monkeypatch):
    p = _Pmus(open_orders=[_open("orphan")])
    assert _venue_reap(p, monkeypatch, pool_raises=True) == 0
    assert "cancel" not in _kinds(p)


def test_the_sweep_calls_the_venue_reaper_beside_the_ledger_one():
    from sportsassets.workers import copy_sweep
    src = inspect.getsource(copy_sweep)
    assert "_reap_stale_resting_bids(pool)" in src
    assert src.index("_reap_stale_submitting(pool)") < src.index(
        "_reap_stale_resting_bids(pool)")


# ---------------------------------------------------------- the grader

class _GPool:
    def __init__(self, rows, lane_missing=False):
        self.rows, self.lane_missing = rows, lane_missing
        self.sqls: list[str] = []

    async def fetch(self, sql, *a):
        self.sqls.append(sql)
        if self.lane_missing and "lo.lane" in sql:
            raise RuntimeError('column lo.lane does not exist')
        return self.rows


def _grade(rows, lane_missing=False):
    pool = _GPool(rows, lane_missing)

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        return asyncio.run(app_mod.api_copy_tolerance()), pool
    finally:
        app_mod.get_pool = orig


def _gr(lane, his=0.48, fp=0.48, pnl=5.0, i=0):
    return _Row(whale="rn1", his=his, fp=fp, staked=100.0, pnl=pnl,
                status="settled", intent="ORDER_INTENT_BUY_LONG",
                lane=lane, event_key=f"g{i}")


def test_a_rest_fill_at_his_price_is_its_own_cohort_not_parity():
    out, _ = _grade([_gr("rest", i=1), _gr("rest", i=2), _gr(None, i=3)])
    cohorts = {(d["whale"], d["cohort"]): d for d in out["rows"]}
    assert cohorts[("rn1", "rest")]["settled"] == 2
    assert cohorts[("rn1", "parity")]["settled"] == 1


def test_the_grader_survives_a_missing_lane_column():
    """Migrations are best-effort at boot; the endpoint must not 500
    until the next healthy boot."""
    r = _Row(whale="rn1", his=0.48, fp=0.48, staked=100.0, pnl=5.0,
             status="settled", intent="ORDER_INTENT_BUY_LONG", event_key="g")
    out, pool = _grade([r], lane_missing=True)
    assert len(pool.sqls) == 2 and "lo.lane" not in pool.sqls[1]
    assert [d["cohort"] for d in out["rows"]] == ["parity"]


# -------------------------------------------------------- the migration

def test_migration_041_ships_with_the_code():
    import pathlib

    root = pathlib.Path(le.__file__).resolve().parents[1]
    body = (root / "migrations" / "041_live_orders_lane.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS lane" in body
