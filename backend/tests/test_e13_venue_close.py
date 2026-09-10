"""E13 (2026-09-08): a flat book ends when ITS venue market has ended;
the resolution sweep never starves.

Lane A: the book's own quote read carrying one of the venue's terminal
states (ms.STATE_TERMINAL) TWICE at least ms.UNMAPPED_TTL_S apart -- the
W1 memo's write and the re-read after its TTL, nothing non-terminal
between -- CONFIRMS the state, and step M closes a FLAT book on it
('closing' under `venue_market_ended`, then 'cancelled' / 'cashed_out'
through _maybe_close_episode as the gamma path does), a frozen flat book
(455's shape) included. One terminal read never closes; OPEN / None /
HALTED on the re-read clears the pending confirmation; a book with shares
held is never touched by it (the settle's; a frozen one keeps its frozen
exit); the gamma path ('market_closed') is byte for byte as before. The
confirmation persists beside the E6 memo (`mirror_terminal_confirm`).

Lane B (real Postgres): unresolved_traded_condition_ids asks the desk's
markets first (books, then live rows -- all of them), then the newest
traded first, then a rotating window from a cursor in ingestion_state
(`resolution_sweep_cursor`) that wraps; de-duplicated, the limit
honoured, the CLOB fallback in the same order, the cursor tolerant of
absence and junk.

Driven on the real planner through the worker file's fakes; the sweep
against a scratch Postgres where one answers (skips visibly otherwise).
"""
import datetime as _dt
import inspect
import json
import pathlib
import uuid

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.analytics import resolution
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_e12_flow_only import DSN_BASE, MIG_DIR
from tests.test_e5_frozen_exits import _frozen_long
from tests.test_e5_frozen_exits import _pool as _e5_pool
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    BUY, CID, M, N, NOW, SELL, SLUG, _ZZ, _armed, _cancels, _census, _his, _kinds, _mkt, _places, _pool, _run, _tick,
    _Venue,
)

EXPIRED = "MARKET_STATE_EXPIRED"
CLOSED = "MARKET_STATE_CLOSED"
HALTED = "MARKET_STATE_HALTED"
SUSPENDED = "MARKET_STATE_SUSPENDED"
PREOPEN = "MARKET_STATE_PREOPEN"
OPEN = "MARKET_STATE_OPEN"
TTL = float(ms.UNMAPPED_TTL_S)
KEY = ("rn1", CID)
ROOT = pathlib.Path(__file__).resolve().parents[2]


def _seed(seen_at, confirmed=None, until=None, state=EXPIRED):
    """A memo standing as an earlier run of reads left it."""
    ml._terminal_book_seen[KEY] = float(seen_at)
    if confirmed is not None:
        ml._terminal_book_confirmed[KEY] = confirmed
    if until is not None:
        ml._terminal_book_until[KEY] = float(until)
        ml._terminal_book_state[KEY] = state


def _state_writes(p, book_id):
    return [a for k, s, a in p.sent if "ml-book-state" in s and a[0] == book_id]


def _bbos(v):
    return [c for c in v.calls if c[0] == "bbo"]


# ------------------------------------------------------- lane A: the close

def test_e13_a_live_flat_book_closes_cancelled_on_the_second_terminal_read_a_ttl_apart_never_on_one():
    assert TTL == 900.0
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    row = p.rows[b["standing_row_id"]]
    # read 1: EXPIRED -- the memo written, the first read's instant kept, nothing confirmed, no close
    st = _tick(p, _Venue(state=EXPIRED))
    assert b["state"] == "live" and b["last_reason"] == "no_mark" and row["status"] == "filled"
    assert ml._terminal_book_until == {KEY: NOW + TTL} and ml._terminal_book_state == {KEY: EXPIRED}
    assert ml._terminal_book_seen == {KEY: NOW} and ml._terminal_book_confirmed == {}
    assert _census(st, "venue_market_ended") == 0 and _census(st, "market_closed") == 0
    # inside the TTL: the memo skips the read; nothing confirms, nothing closes
    v2 = _Venue(state=EXPIRED)
    st2 = _tick(p, v2, now=NOW + 30)
    assert not _bbos(v2) and _census(st2, "book_terminal_skipped") == 1 and b["state"] == "live"
    assert b["last_reason"] == "no_mark" and b["last_plan"]["venue_terminal"] == EXPIRED
    assert ml._terminal_book_confirmed == {} and ml._terminal_book_seen == {KEY: NOW}
    # the re-read after the TTL says EXPIRED again: CONFIRMED (the read tick itself plans no_mark)
    v3 = _Venue(state=EXPIRED)
    st3 = _tick(p, v3, now=NOW + TTL + 1)
    assert _bbos(v3) and b["state"] == "live" and b["last_reason"] == "no_mark"
    assert ml._terminal_book_confirmed == {KEY: EXPIRED} and ml._terminal_book_seen == {KEY: NOW}
    assert ml._terminal_book_until == {KEY: NOW + TTL + 1 + TTL}
    assert _census(st3, "venue_market_ended") == 0
    # the next tick: step M ends the book on the venue's word -- 'closing' named, then 'cancelled'
    v4 = _Venue(state=EXPIRED)
    st4 = _tick(p, v4, now=NOW + TTL + 31)
    assert b["state"] == "closed" and b["last_reason"] == "closed_cancelled" and row["status"] == "cancelled"
    assert not _bbos(v4) and not _places(v4), "closed before any read"
    assert _census(st4, "venue_market_ended") == 1 and _census(st4, "closed_cancelled") == 1
    assert _census(st4, "market_closed") == 0 and st4["closed_books"] == 1
    assert [a[1:] for a in _state_writes(p, b["id"])] == [("closing", "venue_market_ended"),
                                                          ("closed", "closed_cancelled")]
    assert KEY not in ml._terminal_book_seen and KEY not in ml._terminal_book_confirmed, "forgotten on close"
    # ~15 min: the memo's TTL between the two reads, then one tick
    assert ml._terminal_book_until[KEY] > NOW + TTL


def test_e13_a_flat_book_that_bought_closes_cashed_out_and_a_closed_state_confirms_like_expired():
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=93.0, avg_cost=0.31)
    row = p.rows[b["standing_row_id"]]
    _tick(p, _Venue(state=CLOSED))
    _tick(p, _Venue(state=CLOSED), now=NOW + TTL + 1)
    assert ml._terminal_book_confirmed == {KEY: CLOSED} and b["state"] == "live"
    st = _tick(p, _Venue(state=CLOSED), now=NOW + TTL + 31)
    assert b["state"] == "closed" and row["status"] == "cashed_out" and b["last_reason"] == "closed_cashed_out"
    assert _census(st, "venue_market_ended") == 1 and _census(st, "closed_cashed_out") == 1


def test_e13_book_455s_shape_a_frozen_flat_book_behind_the_memo_closes_the_same_way():
    """frozen cancel_pending, ledger 0, nothing non-terminal, the memo
    standing with its first read 800 s ago: skipped this tick, confirmed
    on the re-read after the TTL, closed by step M the tick after --
    the memo path's early return no longer strands it."""
    p = _e5_pool()
    b = p.add_book(ledger=0, avg_cost=None, state="frozen", frozen_reason="cancel_pending", frozen_ts=NOW - 100)
    row = p.rows[b["standing_row_id"]]
    _seed(NOW - 800, until=NOW + 100)
    v = _Venue(state=EXPIRED)
    st = _tick(p, v)
    assert b["state"] == "frozen" and not _bbos(v) and _census(st, "book_terminal_skipped") == 1
    assert ml._terminal_book_confirmed == {} and st["books_frozen"] == 1
    v2 = _Venue(state=EXPIRED)
    st2 = _tick(p, v2, now=NOW + 101)
    assert _bbos(v2) and ml._terminal_book_confirmed == {KEY: EXPIRED} and b["state"] == "frozen"
    assert _census(st2, "venue_market_ended") == 0
    v3 = _Venue(state=EXPIRED)
    st3 = _tick(p, v3, now=NOW + 131)
    assert b["state"] == "closed" and row["status"] == "cancelled" and not _bbos(v3)
    assert _census(st3, "venue_market_ended") == 1 and _census(st3, "closed_cancelled") == 1
    assert [a[1:] for a in _state_writes(p, b["id"])] == [("closing", "venue_market_ended"),
                                                          ("closed", "closed_cancelled")]


def test_e13_a_frozen_book_with_shares_held_is_never_closed_on_the_venues_word_and_its_frozen_exit_still_plans():
    """43's shape: shares held, frozen. A confirmed memo standing on it
    is ignored by step M (the book is not flat), the OPEN read clears
    it, and the E5 frozen exit plans exactly as before (E5's first pin:
    SELL 500 off the venue at his cent)."""
    p = _e5_pool(fills=_his(300, sold=200), snap=None)
    b = _frozen_long(p)
    _seed(NOW - 2000, confirmed=EXPIRED)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, lift=500)
    st = _tick(p, v, http=_mkt(100))
    assert b["state"] == "frozen" and b["frozen_reason"] == "placement_lost"
    assert _census(st, "venue_market_ended") == 0 and _census(st, "frozen_reduce") == 1
    # E31 (FILL lane 31, 2026-09-10): the frozen exit is a post-only rest at the
    # maker wire max(sell_wire(his 0.31), bid 0.30 + MAKER_TICK) = 0.31, lifted at
    # create -- where it was ONE IOC at the take cent 0.30
    assert [c[2:5] for c in _places(v)] == [(0.31, 500, True)]
    assert ml._terminal_book_seen == {} and ml._terminal_book_confirmed == {}, "OPEN clears it"
    assert not _state_writes(p, b["id"])
    # the market read EXPIRED on the held frozen book (43 itself): memo, no close, still frozen
    p2 = _e5_pool()
    b2 = _frozen_long(p2, ledger=300, reason="venue_ledger_disagree", lost=0)
    _seed(NOW - 2000, confirmed=EXPIRED)
    st2 = _tick(p2, _Venue(state=EXPIRED, held={SLUG: 300}))
    assert b2["state"] == "frozen" and _census(st2, "venue_market_ended") == 0
    assert not _state_writes(p2, b2["id"]) and ml._terminal_book_confirmed == {KEY: EXPIRED}
    # and a LIVE held book on a confirmed terminal market: live, no_mark, the settle's
    p3 = _pool()
    b3 = p3.add_book(ledger=300)
    _seed(NOW - 2000, confirmed=EXPIRED)
    st3 = _tick(p3, _Venue(state=EXPIRED, held={SLUG: 300}))
    assert b3["state"] == "live" and b3["last_reason"] == "no_mark" and _census(st3, "venue_market_ended") == 0
    assert ml._venue_market_ended(b3) is None


@pytest.mark.parametrize("reason", sorted(ml._VENUE_MAY_HOLD_REASONS))
def test_e13_review_f1_a_frozen_book_the_venue_may_hold_shares_through_is_never_closed_on_its_word(reason):
    """Review F1 fold: a frozen book's ledger is not its position. Under
    venue_ledger_disagree / placement_lost / lost_ambiguous / order_lost /
    wrong_sign_trip the venue may hold shares the ledger never booked
    (book 77: ledger 0, the register's 1,128), so the verdict is None
    whatever the last plan's venue reading says -- even flat."""
    ml._terminal_book_confirmed[KEY] = EXPIRED
    b = {"whale": "rn1", "condition_id": CID, "ledger_net": 0, "state": "frozen", "frozen_reason": reason,
         "last_plan": {"venue": 0.0}}
    assert ml._venue_market_ended(b) is None
    assert ml._venue_market_ended({**b, "state": "live"}) == EXPIRED, "the reason gates a FROZEN book only"
    assert reason not in ("cancel_pending", "order_state_unknown", "row_not_live")


@pytest.mark.parametrize("reason", ["cancel_pending", "order_state_unknown", "row_not_live", "overfill",
                                    "write_failed"])
def test_e13_review_f1_a_frozen_book_closes_only_on_its_last_plans_own_flat_venue_reading(reason):
    """Review F1 fold, the other gate: under any other freeze the book
    ends on the venue's word ONLY when its last plan carries the venue's
    own position (`venue`, the last read tick's) as a number that is
    flat. Absent (no plan, a memo-skip plan without it), non-numeric (a
    string, a bool, NaN) or held (|venue| >= FLAT_TOL_SHARES): None, the
    book waits for the settle. 455's shape -- plan venue 0 -- closes."""
    ml._terminal_book_confirmed[KEY] = EXPIRED
    base = {"whale": "rn1", "condition_id": CID, "ledger_net": 0, "state": "frozen", "frozen_reason": reason}
    f = ml._venue_market_ended
    assert f({**base, "last_plan": {"venue": 0.0}}) == EXPIRED
    assert f({**base, "last_plan": {"venue": 0}}) == EXPIRED
    assert f({**base, "last_plan": {"venue": rules.FLAT_TOL_SHARES / 2}}) == EXPIRED
    assert f({**base, "last_plan": {"venue": rules.FLAT_TOL_SHARES}}) is None, "held at the rules' own edge"
    assert f({**base, "last_plan": {"venue": -rules.FLAT_TOL_SHARES}}) is None
    assert f({**base, "last_plan": {"venue": 1128}}) is None and f({**base, "last_plan": {"venue": -300.0}}) is None
    assert f({**base}) is None and f({**base, "last_plan": None}) is None, "no plan: no reading"
    assert f({**base, "last_plan": {"kind": "no_plan", "venue_terminal": EXPIRED}}) is None, "the memo skip's plan"
    assert f({**base, "last_plan": {"venue": "0"}}) is None and f({**base, "last_plan": {"venue": False}}) is None
    assert f({**base, "last_plan": {"venue": float("nan")}}) is None and f({**base, "last_plan": "junk"}) is None
    # a live book is its ledger (venue == ledger + the explained, else it would be frozen): the plan is not read
    assert f({**base, "state": "live"}) == EXPIRED and f({**base, "state": "live", "last_plan": {"venue": 9}}) == EXPIRED


def test_e13_a_book_with_an_open_order_is_cancelled_first_and_closed_next_tick(monkeypatch):
    """The cancel under the venue's name, as the gamma path's under its
    own: a cancel that lands closes the book the same tick; a cancel the
    ops budget refused leaves the order standing, the book 'closing'
    (`orders_open`), and the next tick's budget cancels, then closes."""
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    o = p.add_order(b, side=BUY, wire=0.30, qty=300)
    _seed(NOW - 2000, confirmed=EXPIRED, until=NOW + 500)
    v = _Venue(state=EXPIRED)
    v.rest("oid-1", "BUY", 0.30, 300)
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and not _places(v) and not _bbos(v)
    assert o["state"] == "cancelled" and o["reason"] == "venue_market_ended"
    assert b["state"] == "closed" and _census(st, "venue_market_ended") == 1 and _census(st, "closed_cancelled") == 1
    # the ops-capped cancel: book a's TTL cancel takes the tick's one op
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 1)
    p = _pool()
    a = p.add_book(ledger=0, updated_ts=NOW - 100)
    p.add_order(a, order_id="oid-a", placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    b = p.add_book(ledger=0, gross_buy=0.0, updated_ts=NOW - 10, **_ZZ)
    p.markets["0xzz"] = {"closed": False, "resolved": False, "resolved_prices": None}
    ob = p.add_order(b, order_id="oid-b", placed_ts=NOW - 30, us_market_slug=_ZZ["us_market_slug"])
    ml._terminal_book_seen[("rn1", "0xzz")] = NOW - 2000
    ml._terminal_book_confirmed[("rn1", "0xzz")] = EXPIRED
    v = _Venue()
    v.rest("oid-a")
    v.rest("oid-b", slug=_ZZ["us_market_slug"])
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-a", SLUG)] and _census(st, "ops_capped") >= 1
    assert b["state"] == "closing" and ob["state"] == "open", "never closed over a resting order"
    assert b["last_reason"] == "closing: orders_open"
    assert b["last_plan"]["kind"] == "closing" and b["last_plan"]["venue_terminal"] == EXPIRED
    assert b["last_plan"]["market_live"] is True, "the gamma row still reads live: said on the plan"
    assert _census(st, "venue_market_ended") == 1 and _census(st, "closed_cancelled") == 0
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 20)
    v2 = _Venue()
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30)
    assert ("cancel", "oid-b", _ZZ["us_market_slug"]) in v2.calls
    # (step O cancels a rest on a 'closing' book under the state's name
    # before step M runs, as it does on the gamma path's closing book)
    assert ob["state"] == "cancelled" and ob["reason"] in ("closing", "venue_market_ended")
    assert b["state"] == "closed" and _census(st2, "closed_cancelled") == 1
    assert _census(st2, "venue_market_ended") == 0, "named once, on entering 'closing'"
    assert [x[1:] for x in _state_writes(p, b["id"])] == [("closing", "venue_market_ended"),
                                                          ("closed", "closed_cancelled")]


@pytest.mark.parametrize("venue", [
    pytest.param(lambda: _Venue(), id="open"),
    pytest.param(lambda: _Venue(raise_bbo=True), id="none"),
    pytest.param(lambda: _Venue(state=HALTED), id="halted"),
    pytest.param(lambda: _Venue(state=SUSPENDED), id="suspended"),
    pytest.param(lambda: _Venue(state=PREOPEN), id="preopen"),
])
def test_e13_a_non_terminal_or_unread_state_on_the_re_read_clears_the_pending_confirmation(venue):
    # paired out (target 0), so the OPEN read rests nothing and the memo's guard is not in play
    p = _pool(fills=_his(300, other_size=300), snap={M: 300.0, N: 300.0})
    b = p.add_book(ledger=0, gross_buy=0.0)
    _seed(NOW - 2000)                                   # the memo's TTL has run: the re-read is due
    st = _tick(p, venue())
    assert b["state"] == "live" and ml._terminal_book_seen == {} and ml._terminal_book_confirmed == {}
    assert _census(st, "venue_market_ended") == 0
    # a terminal read later starts a NEW run: first read, nothing confirmed
    # (the on-target book would be the quiet rotation's to skip: read it)
    ml._quiet_memo.clear()
    _tick(p, _Venue(state=EXPIRED), now=NOW + 30)
    assert ml._terminal_book_seen == {KEY: NOW + 30} and ml._terminal_book_confirmed == {}
    assert b["state"] == "live"
    # and a confirmed state is cleared by the same reads
    ml._terminal_book_until.clear()
    _seed(NOW - 2000, confirmed=EXPIRED)
    p2 = _pool()
    b2 = p2.add_book(ledger=300)                        # held: step M ignores the confirmation, the read clears it
    _tick(p2, venue())
    assert b2["state"] in ("live", "frozen") and ml._terminal_book_confirmed == {}


def test_e13_a_second_terminal_read_inside_the_ttl_confirms_nothing():
    """The memo dropped under the read (a restart that lost it, the
    until expired by hand): a re-read 100 s after the first is not the
    pair the rule asks for."""
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    _seed(NOW - 100)
    _tick(p, _Venue(state=EXPIRED))
    assert ml._terminal_book_seen == {KEY: NOW - 100} and ml._terminal_book_confirmed == {}
    assert b["state"] == "live"
    # a first read stamped in the future is junk: a new first read
    ml._terminal_book_until.clear()
    _seed(NOW + 50)
    _tick(p, _Venue(state=EXPIRED), now=NOW + 30)
    assert ml._terminal_book_seen == {KEY: NOW + 30} and ml._terminal_book_confirmed == {}


def test_e13_the_memo_writer_keeps_its_guard_nothing_written_while_an_order_is_open():
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    p.add_order(b, side=BUY, wire=0.30, qty=300)
    v = _Venue(state=EXPIRED)
    v.rest("oid-1", "BUY", 0.30, 300)
    _tick(p, v)
    assert ml._terminal_book_until == {} and ml._terminal_book_seen == {} and ml._terminal_book_confirmed == {}
    assert _cancels(v) and b["state"] == "live"
    # the order gone: the next terminal read is the first of the run
    _tick(p, _Venue(state=EXPIRED), now=NOW + 30)
    assert ml._terminal_book_seen == {KEY: NOW + 30} and ml._terminal_book_confirmed == {}


def test_e13_the_gamma_path_is_byte_for_byte_as_before_and_wins_the_name():
    # a never-filled book on a closed gamma row: 'market_closed', closed cancelled, no venue name
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st = _tick(p, _Venue())
    assert b["state"] == "closed" and p.rows[b["standing_row_id"]]["status"] == "cancelled"
    assert _census(st, "market_closed") == 1 and _census(st, "venue_market_ended") == 0
    assert [a[1:] for a in _state_writes(p, b["id"])] == [("closing", "market_closed"), ("closed", "closed_cancelled")]
    # both signals at once: the gamma row names it
    p2 = _pool()
    b2 = p2.add_book(ledger=0, gross_buy=0.0)
    p2.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    _seed(NOW - 2000, confirmed=EXPIRED)
    st2 = _tick(p2, _Venue(state=EXPIRED))
    assert b2["state"] == "closed" and _census(st2, "market_closed") == 1 and _census(st2, "venue_market_ended") == 0
    assert _state_writes(p2, b2["id"])[0][1:] == ("closing", "market_closed")
    assert "venue_terminal" not in (b2.get("last_plan") or {})
    # a held book on a closed gamma row: 'closing' (held), cancelled, waits for the settle -- as before
    p3 = _pool()
    b3 = p3.add_book(ledger=300)
    p3.add_order(b3, side=SELL, wire=0.33, qty=100, kind="reduce")
    p3.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    v3 = _Venue(held={SLUG: 300})
    v3.rest("oid-1", "SELL", 0.33, 100)
    st3 = _tick(p3, v3)
    assert _cancels(v3) and not _places(v3) and b3["state"] == "closing"
    assert _census(st3, "market_closed") == 1 and "bbo" not in _kinds(v3)
    assert (b3["last_plan"]["kind"], b3["last_plan"]["market_live"]) == ("closing", False)
    assert "venue_terminal" not in b3["last_plan"] and b3["last_reason"] == "closing: held"
    # the source: the gamma reading and its name are the old text
    src = inspect.getsource(ml._tick_book)
    for s in ('closed_read = mk is not None and (mk["closed"] is True or mk["resolved"] is True)',
              'venue_ended = None if closed_read else _venue_market_ended(book)',
              'close_name = "venue_market_ended" if (venue_ended is not None and not closed_read) else "market_closed"',
              'False if venue_ended is not None else market_live'):
        assert s in src, s
    assert src.index("_venue_market_ended(book)") < src.index("_terminal_book_until.get((w, cid), 0.0) > t.now"), \
        "the venue close runs in step M, before the memo path's early return"
    assert src.index("_venue_market_ended(book)") < src.index('if book.get("state") == "frozen":')


def test_e13_the_confirmation_persists_beside_the_e6_memo_and_a_restart_closes_on_it():
    assert ml._STATE_TERMINAL_CONFIRM == "mirror_terminal_confirm"
    p = _pool()
    p.add_book(ledger=0, gross_buy=0.0)
    _tick(p, _Venue(state=EXPIRED))
    assert p.state[ml._STATE_TERMINAL_CONFIRM] == [["rn1", CID, round(NOW, 1), None]]
    assert p.state[ml._STATE_TERMINAL_MEMO] == {
        "cand": [], "book": [["rn1", CID, round(NOW + TTL, 1), EXPIRED]], "at": ml._iso(NOW)}, "the E6 key untouched"
    _tick(p, _Venue(state=EXPIRED), now=NOW + TTL + 1)
    assert p.state[ml._STATE_TERMINAL_CONFIRM] == [["rn1", CID, round(NOW, 1), EXPIRED]]
    # a fresh process: the boot read holds the pair, and the flat book ends on the first tick
    ml._terminal_memo_loaded = False
    ml._terminal_book_seen.clear()
    ml._terminal_book_confirmed.clear()
    ml._terminal_book_until.clear()
    ml._terminal_book_state.clear()
    p2 = _pool()
    b2 = p2.add_book(ledger=0, gross_buy=0.0)
    p2.state[ml._STATE_TERMINAL_CONFIRM] = [
        ["rn1", CID, NOW - 1000, EXPIRED], ["rn1", "0xfuture", NOW + 50, EXPIRED], ["rn1", "0xhalted", NOW - 5, HALTED],
        "junk", ["rn1", "0xshort"], [1, "0xnum", NOW - 5, None], ["rn1", "0xpending", NOW - 5, None],
        ["rn1", "0xnan", "x", None]]
    st = _tick(p2, _Venue(state=EXPIRED))
    assert b2["state"] == "closed" and _census(st, "venue_market_ended") == 1
    assert ml._terminal_book_seen == {("rn1", "0xpending"): NOW - 5.0} and ml._terminal_book_confirmed == {}
    # unreadable or the wrong shape: empty, logged, never a raise
    for junk in ("x", 7, {"a": 1}):
        ml._terminal_memo_loaded = False
        ml._terminal_book_seen.clear()
        p3 = _pool()
        p3.add_book(ledger=300)
        p3.state[ml._STATE_TERMINAL_CONFIRM] = junk
        st3 = _tick(p3, _Venue(held={SLUG: 300}))
        assert not st3["abandoned"] and ml._terminal_book_seen == {} and ml._terminal_book_confirmed == {}
    # the snapshot: bounded, a future or unreadable first read dropped, newest first
    ml._terminal_book_seen.clear()
    ml._terminal_book_confirmed.clear()
    ml._terminal_book_seen.update({("rn1", "0xa"): NOW - 10, ("rn1", "0xb"): NOW - 5, ("rn1", "0xf"): NOW + 1,
                                   ("rn1", "0xx"): "x"})
    ml._terminal_book_confirmed[("rn1", "0xa")] = EXPIRED
    assert ml._terminal_confirm_snapshot(NOW) == [["rn1", "0xb", round(NOW - 5, 1), None],
                                                  ["rn1", "0xa", round(NOW - 10, 1), EXPIRED]]
    ml._terminal_book_seen.update({("rn1", f"0x{i}"): NOW - 100 - i for i in range(ml._TERMINAL_MEMO_MAX + 5)})
    assert len(ml._terminal_confirm_snapshot(NOW)) == ml._TERMINAL_MEMO_MAX


def test_e13_the_verdict_fails_closed_and_the_names_are_where_the_pins_expect_them():
    f = ml._venue_market_ended
    ml._terminal_book_confirmed[KEY] = EXPIRED
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": 0}) == EXPIRED
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": None}) == EXPIRED
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": 1e-9}) == EXPIRED
    for ledger in (1, -1, 0.5, "x", 300, -1042, float("nan")):
        assert f({"whale": "rn1", "condition_id": CID, "ledger_net": ledger}) is None, ledger
    assert f({"whale": "rn1", "condition_id": "0xother", "ledger_net": 0}) is None
    ml._terminal_book_confirmed[KEY] = HALTED
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": 0}) is None
    ml._terminal_book_confirmed[KEY] = 7
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": 0}) is None
    ml._terminal_book_confirmed.clear()
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": 0}) is None
    # the census: the one new name, before `registered_no_increase` (the E12 pin holds keys[-12])
    keys = ml.CENSUS_KEYS
    assert "venue_market_ended" in keys and keys.index("venue_market_ended") < keys.index("registered_no_increase")
    assert keys[-12] == "registered_no_increase" and keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert ml._new_stats()["census"]["venue_market_ended"] == 0
    # the docstrings
    assert "THE CONFIRMATION" in ml._memo_terminal_book.__doc__
    assert "Fail closed" in ml._venue_market_ended.__doc__
    src = inspect.getsource(ml)
    assert "THE VENUE'S OWN TERMINAL STATE ENDS A FLAT BOOK (E13" in src
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert "## 34. E13" in doc
    for s in ("venue_market_ended", "resolution_sweep_cursor", "49,213", "mirror_terminal_confirm",
              "UNMAPPED_TTL_S", "455"):
        assert s in doc, s


# --------------------------------------------- lane B: the sweep's order

def _pin_module_docstring():
    d = resolution.__doc__
    for s in ("THE SWEEP STARVED", "49,213", "LIMIT 500", "NO ORDER BY", "`newly_resolved: 4`",
              "THE DESK'S MARKETS FIRST", "THEN NEWEST FIRST", "A ROTATING WINDOW", "resolution_sweep_cursor",
              "40 condition_ids", "clob_batch=300"):
        assert s in d, s


def test_e13_the_sweep_says_what_starved_and_the_cursor_reads_junk_as_the_start():
    _pin_module_docstring()
    assert resolution.SWEEP_CURSOR_KEY == "resolution_sweep_cursor"
    c = resolution._cursor_of
    assert c(None) == "" and c("junk") == "" and c('{"cursor": 5}') == "" and c('{"x": "y"}') == ""
    assert c('"a"') == "" and c(7) == "" and c(["a"]) == "" and c(b"{") == ""
    assert c('{"cursor": "0xabc", "at": "x"}') == "0xabc" and c({"cursor": "0xdef"}) == "0xdef"
    assert c('{"cursor": ""}') == ""
    assert "resolution-desk" in resolution.SQL_DESK and "resolution-newest" in resolution.SQL_NEWEST
    assert "resolution-rotate" in resolution.SQL_ROTATE and resolution.NEWEST_WINDOW_S == 14 * 86400
    assert "ORDER BY c.rank, c.condition_id" in resolution.SQL_DESK
    assert "ORDER BY max(t.ts) DESC, t.condition_id" in resolution.SQL_NEWEST
    assert "ORDER BY t.condition_id" in resolution.SQL_ROTATE and "t.condition_id > $2" in resolution.SQL_ROTATE
    assert "state <> 'closed'" in resolution.SQL_DESK and "('submitting', 'filled', 'exiting')" in resolution.SQL_DESK
    assert inspect.signature(resolution.unresolved_traded_condition_ids).parameters["limit"].default == 500


async def _scratch():
    asyncpg = pytest.importorskip("asyncpg")
    try:
        admin = await asyncpg.connect(DSN_BASE, timeout=4)
    except Exception:  # noqa: BLE001 — no local PG: skip, never fake
        pytest.skip("no local postgres for the E13 sweep pin")
    name = "e13_sweep_" + uuid.uuid4().hex[:10]
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
    await conn.execute((MIG_DIR / "001_init.sql").read_text())
    await conn.execute("CREATE TABLE live_orders (id bigserial PRIMARY KEY, us_market_slug text, lane text, "
                       "condition_id text, status text)")
    await conn.execute((MIG_DIR / "047_mirror_live.sql").read_text())
    await conn.execute("INSERT INTO whales (id, address, username) VALUES (1, '0xabc', 'rn1')")
    return admin, conn, name


async def _drop(admin, conn, name):
    await conn.close()
    await admin.execute(f'DROP DATABASE "{name}"')
    await admin.close()


_N = {"n": 0}


async def _trade(conn, cid, age_s):
    _N["n"] += 1
    ts = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(seconds=age_s)
    await conn.execute(
        "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, size, price, notional, ts, source, "
        "detected_at, dedupe_key) VALUES (1, $1, 'tok', $2, 'BUY', 1, 0.5, 0.5, $3, 'poll', $3, $1)",
        f"tx{_N['n']}", cid, ts)


async def _market(conn, cid, resolved):
    await conn.execute("INSERT INTO markets (condition_id, resolved, closed) VALUES ($1, $2, $2)", cid, resolved)


async def _seed_world(conn):
    """The world: two desk conditions (a book's, a live row's), a closed
    book's and a resolved book's (neither desk), three recent trades
    (newest n1, n2, n3; the book's a_book traded newest of all), six old
    ones o1..o6 outside the newest window, one resolved old one."""
    old = 30 * 86400
    await conn.execute("INSERT INTO mirror_books (whale, condition_id, us_market_slug, long_asset, state) VALUES "
                       "('rn1', 'a_book', 's-a', 'tok', 'live'), ('rn1', 'z_closed', 's-z', 'tok', 'closed'), "
                       "('rn1', 'r_book', 's-r', 'tok', 'frozen')")
    await conn.execute("INSERT INTO live_orders (condition_id, status) VALUES ('b_row', 'filled'), "
                       "('u_row', 'unfilled'), ('e_row', 'error'), (NULL, 'filled')")
    await _market(conn, "r_book", True)
    await _market(conn, "o_res", True)
    await _market(conn, "n2", False)
    for cid, age in (("a_book", 600), ("b_row", old), ("z_closed", old), ("r_book", 100), ("n1", 3600),
                     ("n2", 7200), ("n3", 10800), ("o_res", 50)):
        await _trade(conn, cid, age)
    for i in range(1, 7):
        await _trade(conn, f"o{i}", old + i)
    await _trade(conn, "n2", 7300)                       # a second fill: de-duplicated


async def _cursor(conn):
    raw = await conn.fetchval("SELECT value FROM ingestion_state WHERE key = $1", resolution.SWEEP_CURSOR_KEY)
    return raw if raw is None else json.loads(raw)


def test_e13_the_sweep_asks_the_desks_markets_first_then_newest_then_rotates_and_wraps(monkeypatch):
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await _seed_world(conn)

            async def _gp():
                return conn
            monkeypatch.setattr(resolution, "get_pool", _gp)
            ids = resolution.unresolved_traded_condition_ids
            # the cursor absent: the rotation starts at the beginning
            assert await _cursor(conn) is None
            got = await ids(limit=6)
            assert got == ["a_book", "b_row", "n1", "n2", "n3", "o1"], got
            assert (await _cursor(conn))["cursor"] == "o1"
            # the same call again: the desk's and the newest again, the rotation moved on
            assert await ids(limit=6) == ["a_book", "b_row", "n1", "n2", "n3", "o2"]
            assert (await _cursor(conn))["cursor"] == "o2"
            # a wider window: two of the rotation, the cursor on the last swept
            assert await ids(limit=7) == ["a_book", "b_row", "n1", "n2", "n3", "o3", "o4"]
            assert (await _cursor(conn))["cursor"] == "o4"
            # the end reached: wrapped to the start for what is left
            assert await ids(limit=9) == ["a_book", "b_row", "n1", "n2", "n3", "o5", "o6", "z_closed", "o1"]
            assert (await _cursor(conn))["cursor"] == "o1"
            # the limit honoured (the desk's never cut); de-dup: a_book traded newest of all, once
            assert await ids(limit=2) == ["a_book", "b_row"] and await ids(limit=1) == ["a_book", "b_row"]
            assert await ids(limit=0) == ["a_book", "b_row"]
            assert await ids(limit=3) == ["a_book", "b_row", "n1"]
            # the resolved, the closed book's, the unfilled / error rows', the NULL: never
            everything = await ids(limit=100)
            assert everything == ["a_book", "b_row", "n1", "n2", "n3", "o2", "o3", "o4", "o5", "o6", "z_closed", "o1"]
            assert "r_book" not in everything and "o_res" not in everything
            assert (await _cursor(conn))["cursor"] == "o1"
            # junk in the key: the start; a string; a bad shape
            for junk in ('"x"', '{"cursor": 5}', '[1]', '{"a": "b"}'):
                await conn.execute("INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
                                   "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                                   resolution.SWEEP_CURSOR_KEY, junk)
                assert await ids(limit=6) == ["a_book", "b_row", "n1", "n2", "n3", "o1"], junk
                assert (await _cursor(conn))["cursor"] == "o1"
            # the cursor's own shape
            cur = await _cursor(conn)
            assert set(cur) == {"cursor", "at"} and cur["at"].endswith("+00:00")
            # a resolution landing drops a condition from every stage
            await _market(conn, "n1", True)
            assert await ids(limit=6) == ["a_book", "b_row", "n2", "n3", "o2", "o3"]
            # the desk's set unreadable (the table gone): the rest is still asked, logged
            await conn.execute("DROP TABLE mirror_books CASCADE")
            assert await ids(limit=4) == ["a_book", "n2", "n3", "o4"]
        finally:
            await _drop(admin, conn, name)
    _run(_go())


def test_e13_the_clob_fallback_reads_the_same_order(monkeypatch):
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await _seed_world(conn)

            async def _gp():
                return conn
            monkeypatch.setattr(resolution, "get_pool", _gp)
            asked = {"gamma": None, "clob": []}

            class _Client:
                async def fetch_by_condition_ids(self, cids):
                    asked["gamma"] = list(cids)
                    return []

            async def _clob(_http, cid):
                asked["clob"].append(cid)
                return None
            import sportsassets.clob as clob
            monkeypatch.setattr(clob, "fetch_clob_market", _clob)
            newly = await resolution.sweep_resolutions(_Client(), clob_batch=7)
            assert newly == 0
            assert asked["gamma"] == ["a_book", "b_row", "n1", "n2", "n3", "o1", "o2", "o3", "o4", "o5", "o6",
                                      "z_closed"], asked
            # the fallback: the desk's first, the newest, then the rotation from where the gamma pass left it
            assert asked["clob"] == ["a_book", "b_row", "n1", "n2", "n3", "o1", "o2"], asked
            assert (await _cursor(conn))["cursor"] == "o2"
        finally:
            await _drop(admin, conn, name)
    _run(_go())
