"""E28 (2026-09-09, FILL lane 28; task 100): the full tick's book walk
plans on the row AS IT STANDS under the per-book lock (re-read, as the
fast tick has since E9), and a fill booking never overwrites a ledger
figure it did not read (the ledger write guarded by the figure the
booking was computed from; a miss re-reads and re-books once, a second
miss books nothing).

THE ROWS (hard2/book_1333_2326.txt, the orders table rows 305-312 and
the book table at its foot; hard2/frozen_2317.txt row 6). Book 1333
atc-lib-pal-lqu-2026-09-09-pal, ORDER_INTENT_BUY_LONG, opened 22:28:58Z.
Buys: 8091 take BUY_LONG IOC 823 @0.90 filled 823, 8092 145 @0.90 filled
145 -> 968 bought. Sells: 8124 SELL_LONG IOC 146 @0.91, 8132 37 @0.89,
8147 93 @0.89 (placed 22:49:59, done 22:50:00), 8148 93 @0.89 (placed
22:50:01, done 22:50:01) -> 146 + 37 + 93 + 93 = 369 sold, 599 held --
the VENUE's figure at the freeze (frozen_2317 row 6: ledger 692, venue
599, frozen 22:50:51 venue_ledger_disagree). The LEDGER's 692 = 968 - 146
- 37 - 93: ONE of the two 93-share fills moved it. Both order rows read
filled / booked 93 (the per-order cursor ran twice); the book's ledger
was written twice with the SAME 785 - 93 = 692, because the second
booking's in-memory dict still carried 785. Both cycles read the same
target (his net 6,920 -> 692 at ratio 0.1; the reduce that drove it his
933.6 BUY of the complement @0.10, seen 22:49:48, book_1333 his-fills
table) and both sized 93. The frozen exit later sold 217 @0.92 (8215) on
the venue's 599 - target 382; the book then read ledger 475 / venue 382
/ target 382 -- the same 93 apart, for the market's life.

THE WORLD below is the worker suite's long world on E25's harness (a
long book at ratio 0.1, the whole-book walk STALE so the per-market read
is the venue's word): the book at 785 @0.90, his net read 6,920 (7,850
long less his 930 BUY of the complement @0.10, the witness), the plan
before at 7,850 / target 785 (the fall 930 is under E25's threshold, so
the reduce fires as today), the book 0.89 / 0.91 so the exit take is
ONE SELL IOC at 0.89 -- 8147's shape. The 1333 shape: between step B's
read and the lock another booking writes the row to 692.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import pathlib
import re
import subprocess
import sys

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    BUY, CID, M, N, NOW, SELL, SLUG, _armed, _census, _fill, _Http, _mkt, _places, _Pool, _pool,
    _rails_2026_09_06, _run, _tick, _Venue,
)

IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"   # E31 (FILL lane 31): the only tif the money path sends
ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("walk_row_moved", "walk_row_unread", "walk_row_gone", "ledger_stale_reread", "ledger_stale_refused")
# book 1333's figures (the module docstring)
LEDGER_BEFORE = 785                 # the dict as step B read it (968 - 146 - 37)
LEDGER_AFTER_ONE = 692              # 785 - 93: the row after the first booking
LEDGER_AFTER_TWO = 599              # 692 - 93: the venue's figure at the freeze
NET_BEFORE = 7850.0                 # the plan before's reading (target 785)
NET_1333 = 6920.0                   # his net after the reducing row (target 692)
HIS_REDUCE = 930.0                  # his BUY of the complement, the witness (933.6 on the row; whole shares here)
PLAN_BEFORE = {"kind": "reduce", "reason": "take", "target": LEDGER_BEFORE, "at": NOW - 60.0, "net": NET_BEFORE,
               "reduce_ref": {"target": LEDGER_BEFORE, "at": NOW - 100.0}}
BUILT = _fill(M, "BUY", NET_BEFORE, 0.90, NOW - 3000)                                    # his long as the reading held it
WITNESS = _fill(N, "BUY", HIS_REDUCE, 0.10, NOW - 10, detected_at=NOW - 5, source="chain")   # his reducing row
# the functions this lane does NOT touch, hashed on 6c0830d (the tip this lane was built on); E29 (FILL lane 29,
# the hand exit) landed after and moved two of them -- _tick_book (78d2c4f096b70c00 -> f8b3aa98172bf578, the
# hand_held hold), _tick (a766496554ff357e -> 265461d33df59da6, the memo read at the tick's start) and _fast_tick
# (ce6e086b18c28200 -> c364a8f6ed7f9b3b) -- re-pinned at E29's landing; this lane touched none of the three
# E31 (FILL lane 31, 2026-09-10: every order a post-only rest that never crosses) landed after and moved
# five of them -- _act (59efb48ba79f793c -> 2e7043299fbf834a, the six take arms gone and the fold's
# `maker_no_cent` hold on a standing rest, review CRITICAL-2), _place
# (ab568476817cf795 -> f559a52bfb610ef3, the fail-closed IOC guard, its dead IOC clause dropped), _place_reserved (d55d0d4a63c71c23 ->
# a83a3e9473eb7112, the unconditional flag, the touch-bound re-read, the aggressor read, the cross
# re-price), _reconcile_open (7c3bc726438693ce -> 2b76640dec2d3bd3, the maker rest that stands past its
# TTL) and _tick_book (a9193e21be233a95 -> d121406c0c55e65b, E30's hold comment re-worded) -- and DELETED
# three: `_entry_take`, `_exit_take` and `_ioc_reread` have no caller left. THIS LANE'S OWN SITES
# (_book_fill, _book_hand_reduce, _walk_books, _walk_reread) are untouched by E31.
UNTOUCHED = {
    "_fast_book": "286e6fa4663c3887", "_fast_gate": "1932811194268668", "_act": "2e7043299fbf834a",
    "_place": "f559a52bfb610ef3", "_place_reserved": "a83a3e9473eb7112",
    "_frozen_exit": "ef478fabdfa2ccc0", "_reconcile_open": "2b76640dec2d3bd3",
    "_reconcile_placing": "76ab1b2931ee0f33", "_cancel_reread_row": "594f788a96e60268",
    "_tick_book": "d121406c0c55e65b", "_finish_order": "1db222463610e38c", "_book_delta": "e5363576f6d0a575",
    "_fast_candidate": "922585ffb6856f70", "_walk_candidate": "9c990feba5fdeb57",
    "_fast_tick": "c364a8f6ed7f9b3b", "fast_tick_once": "f0489ca714e21973", "_tick": "265461d33df59da6",
}
# the standing-row statements (live_executor) byte for byte, and the rules module on 6c0830d
LE_UNTOUCHED = {"_book_mirror_sell": "c69f72f67be190cf", "_book_mirror_buy": "f435f319bcc5ded8"}
RULES_SHA_6C0830D = "6dd4e43300f04676"
# the mirror_orders cursor and cash statements byte for byte (6c0830d)
SQL_ORDER_CURSOR = """
UPDATE mirror_orders SET booked_filled = booked_filled + $2, filled = $3, avg_px = $4,
       updated_at = now()
 WHERE id = $1 AND booked_filled = $5 /* ml-order-cursor */
"""
SQL_ORDER_CASH = """
UPDATE mirror_orders SET cash_usd = cash_usd + $2, realized = realized + $3,
       taker_at_placement = taker_at_placement OR $4, updated_at = now()
 WHERE id = $1 /* ml-order-cash */
"""


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _recent(what, book=None):
    return [x for x in ml._RECENT if x["what"] == what and (book is None or x["book"] == book)]


@pytest.fixture(autouse=True)
def _fresh_flags():
    """The once-per-process log flags start clear for every test here."""
    ml._ledger_stale_logged = False
    ml._walk_unread_logged = False
    yield


def _world(monkeypatch, ledger=LEDGER_BEFORE):
    """Book 1333's shape: a long book of `ledger` @0.90 at ratio 0.1, the
    reading 6,920 (target 692) on the stale walk with the per-market read
    agreeing, the plan before at 7,850 / 785."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[BUILT, WITNESS], snap={M: NET_BEFORE, N: 0.0}, snap_at=NOW - 1000)
    b = p.add_book(ledger=ledger, ratio=0.10, avg_cost=0.90, last_plan=dict(PLAN_BEFORE))
    return p, b


def _venue(held, lift=93.0):
    """The 1333 book: 0.89 / 0.91. E31's (FILL lane 31, 2026-09-10) maker
    SELL rests at max(sell_wire(his 0.89), 0.89 + 0.01) = 0.90 -- one tick
    over the bid, inside the spread, never through it (on 6c0830d the same
    cycle sent a SELL IOC at 0.89, THROUGH the bid).

    `ioc_fill` no longer fills anything on the money path, so the 93 come
    through `lift`: the venue fills the fresh post-only rest at create and
    its execution's `aggressor` reads the bool False -- a TAKER hit our
    rest, so we were the maker (`maker_fill_at_create`) and the fill books
    exactly as the IOC's fill booked before this lane. E28's guard, the
    subject of this file, reads the same 93 shares either way."""
    return _Venue(bid=0.89, ask=0.91, held={SLUG: held}, ioc_fill=93.0, lift=lift)


def _other_booking_between_step_b_and_the_lock(monkeypatch, p, b, ledger=LEDGER_AFTER_ONE, state=None):
    """The 1333 shape: the row moves AFTER step B read the dict and BEFORE
    the walk takes the book's lock -- hooked on _lock_for's first call for
    the book (the walk's own acquire; no order stands, so step O never
    asks for it). The row's ledger and its standing row move together, as
    a booking writes them."""
    real = ml._lock_for
    fired = {"n": 0}

    def hooked(bid):
        if bid == b["id"] and fired["n"] == 0:
            fired["n"] = 1
            p.books[bid]["ledger_net"] = ledger
            p.rows[b["standing_row_id"]]["filled_shares"] = float(ledger)
            if state is not None:
                p.books[bid]["state"] = state
        return real(bid)

    monkeypatch.setattr(ml, "_lock_for", hooked)
    return fired


# ------------------------------------------------------------ (1) the 1333 shape on the walk

def test_e28_todays_first_cycle_is_one_sell_rest_of_93_at_090_and_the_ledger_692(monkeypatch):
    """8147's cycle: the dict and the row agree at 785, his net 6,920 ->
    target 692, ONE SELL_LONG of 93, booked to 692. Nothing of this lane's
    counts (the row did not move).

    RE-PINNED at E31 (FILL lane 31, 2026-09-10) -- 0.89 IOC -> 0.90 GTC
    post-only: the exit no longer takes. 8147 went out at 0.89, THROUGH
    the bid of 0.89; the maker wire is max(0.89, 0.89 + 0.01) = 0.90, one
    tick over the bid and inside the 0.89 / 0.91 spread. The venue lifts
    it at create as a maker (`_lifted`), so E28's booking -- the subject
    of this file -- is reached on the same 93 shares, the same target and
    the same ledger."""
    p, b = _world(monkeypatch)
    v = _venue(LEDGER_BEFORE)
    st = _tick(p, v, http=_mkt(NET_1333, 0.0))
    assert b["target"] == LEDGER_AFTER_ONE and len(_places(v)) == 1
    assert _places(v)[0][2:6] == (0.90, 93, True, GTC)
    assert _places(v)[0][7] is True, "post-only on the wire (E31)"
    assert not [c for c in v.calls if c[0] == "place" and c[5] == IOC], "no IOC left on the money path"
    assert b["ledger_net"] == LEDGER_AFTER_ONE and _census(st, "exit_take") == 0
    assert _census(st, "rest_placed") == 1 and _census(st, "maker_fill_at_create") == 1
    assert _census(st, "post_only_ignored") == 0 and _census(st, "post_only_block") == 0
    assert all(_census(st, k) == 0 for k in NEW_NAMES)
    assert not _recent("walk_row_moved", b["id"])


def test_e28_the_1333_shape_with_the_switch_on_plans_on_the_row_as_it_stands_and_sends_nothing(monkeypatch):
    """Step B read 785; between the read and the lock another booking wrote
    the row to 692 (8147's fill). The walk re-reads under the lock: 692
    against the target 692 -> on target, NO second IOC, `walk_row_moved` 1,
    the recent entry naming both ledgers, the ledger 692."""
    p, b = _world(monkeypatch)
    fired = _other_booking_between_step_b_and_the_lock(monkeypatch, p, b)
    v = _venue(LEDGER_AFTER_ONE)
    st = _tick(p, v, http=_mkt(NET_1333, 0.0))
    assert fired["n"] == 1 and not _places(v), "the second 93 never goes out"
    assert b["ledger_net"] == LEDGER_AFTER_ONE and b["target"] == LEDGER_AFTER_ONE
    assert _census(st, "walk_row_moved") == 1 and _census(st, "exit_take") == 0
    assert _census(st, "walk_row_unread") == 0 and _census(st, "walk_row_gone") == 0
    ev = _recent("walk_row_moved", b["id"])
    assert len(ev) == 1 and ev[0]["ledger_was"] == LEDGER_BEFORE and ev[0]["ledger_now"] == LEDGER_AFTER_ONE
    assert ev[0]["cols"] == ["ledger_net"]
    assert b["state"] == "live" and b["frozen_reason"] is None, "the row and the venue agree: no freeze"
    assert _census(st, "ledger_stale_reread") == 0 and _census(st, "ledger_stale_refused") == 0


def test_e28_the_1333_shape_with_the_switch_off_sends_the_second_93_as_on_6c0830d_and_the_guard_still_books_599(monkeypatch):
    """The pin that names today's defect: OFF is 6c0830d's walk byte for
    byte -- the plan is made on the 785 dict and the second 93 goes out
    (8148). What (A) still guarantees: the booking's guarded write misses
    (the row reads 692, the dict read 785), the row is re-read and the
    fill re-booked at 692 - 93 = 599 -- never 692 twice -- so the ledger
    reads the venue's figure and the book does not freeze.

    RE-PINNED at E31 -- 0.89 IOC -> 0.90 GTC post-only, lifted at create
    as a maker. The WALK is byte for byte 6c0830d's with the switch off,
    which is what this test pins; what the walk hands the venue is this
    lane's rest, and the guard reads the same 93 shares either way."""
    monkeypatch.setattr(rules, "MIRROR_WALK_REREAD", False)
    p, b = _world(monkeypatch)
    fired = _other_booking_between_step_b_and_the_lock(monkeypatch, p, b)
    v = _venue(LEDGER_AFTER_ONE)
    st = _tick(p, v, http=_mkt(NET_1333, 0.0))
    assert fired["n"] == 1 and len(_places(v)) == 1 and _places(v)[0][2:6] == (0.90, 93, True, GTC)
    assert _census(st, "walk_row_moved") == 0 and not _recent("walk_row_moved", b["id"])
    assert b["ledger_net"] == LEDGER_AFTER_TWO, "785 - 93 would have been written over 692 on 6c0830d"
    assert _census(st, "ledger_stale_reread") == 1 and _census(st, "ledger_stale_refused") == 0
    ev = _recent("ledger_stale_reread", b["id"])
    assert len(ev) == 1 and ev[0]["ledger_was"] == LEDGER_BEFORE and ev[0]["ledger_now"] == LEDGER_AFTER_ONE
    o = [x for x in p.orders.values() if x["book_id"] == b["id"]][0]
    assert o["booked_filled"] == 93 and o["state"] == "filled"


def test_e28_the_re_read_raising_skips_the_book_this_tick_under_walk_row_unread_and_sends_nothing(monkeypatch, caplog):
    p, b = _world(monkeypatch)
    p.raise_on.append(("ml-book-read", RuntimeError("connection reset")))
    v = _venue(LEDGER_BEFORE)
    with caplog.at_level(logging.WARNING, logger="sportsassets.workers.mirror_live"):
        st = _tick(p, v, http=_mkt(NET_1333, 0.0))
        st2 = _tick(p, _venue(LEDGER_BEFORE), now=NOW + 40, http=_mkt(NET_1333, 0.0))
    assert not _places(v) and b["ledger_net"] == LEDGER_BEFORE and b["last_plan"] == PLAN_BEFORE
    assert _census(st, "walk_row_unread") == 1 and _census(st2, "walk_row_unread") == 1
    assert _census(st, "walk_row_moved") == 0 and _census(st, "walk_row_gone") == 0
    lines = [r.getMessage() for r in caplog.records if "unreadable for the walk's re-read" in r.getMessage()]
    assert len(lines) == 1 and f"book {b['id']}" in lines[0], "logged once per process"


def test_e28_the_row_gone_or_closed_skips_the_book_this_tick_under_walk_row_gone_and_sends_nothing(monkeypatch):
    # (a) the row closed between step B and the lock
    p, b = _world(monkeypatch)
    _other_booking_between_step_b_and_the_lock(monkeypatch, p, b, ledger=LEDGER_BEFORE, state="closed")
    v = _venue(LEDGER_BEFORE)
    st = _tick(p, v, http=_mkt(NET_1333, 0.0))
    assert not _places(v) and _census(st, "walk_row_gone") == 1 and _census(st, "walk_row_moved") == 0
    assert b["last_plan"] == PLAN_BEFORE
    # (b) the row gone
    p2, b2 = _world(monkeypatch)
    real = ml._lock_for

    def hooked(bid):
        p2.books.pop(bid, None)
        return real(bid)

    monkeypatch.setattr(ml, "_lock_for", hooked)
    v2 = _venue(LEDGER_BEFORE)
    st2 = _tick(p2, v2, http=_mkt(NET_1333, 0.0))
    assert not _places(v2) and _census(st2, "walk_row_gone") == 1 and _census(st2, "walk_row_unread") == 0


def test_e28_a_frozen_row_is_walked_as_today_the_frozen_exit_is_the_walks(monkeypatch):
    """`gone` is the walk's population (state <> 'closed'), never
    'frozen': a frozen book keeps its E5 frozen exit on the full tick."""
    p, b = _world(monkeypatch)
    b.update(state="frozen", frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 100, venue_net=599.0)
    v = _venue(599)
    st = _tick(p, v, http=_mkt(NET_1333, 0.0))
    assert st["books_frozen"] == 1 and _census(st, "walk_row_gone") == 0 and _census(st, "walk_row_moved") == 0
    assert "frozen_exit" in b["last_plan"]


def test_e28_an_unchanged_row_counts_nothing_and_a_moved_open_order_or_state_counts_walk_row_moved():
    """The unit: the five planning columns judged, None-safe, numbers as
    numbers; the walk-time keys of the dict stay; the fresh row's
    columns replace the dict's in place."""
    p = _pool()
    b = p.add_book(ledger=785, ratio=0.10, avg_cost=0.90)
    t = ml._Tick(pool=p, pmus=_Venue(), http=_Http(), now=NOW, stats=ml._new_stats(), started=0.0)
    ml._current_stats = t.stats
    d = {**dict(b), "_walk_key": "kept"}
    assert _run(ml._walk_reread(t, d)) is None and t.stats["census"].get("walk_row_moved", 0) == 0
    assert d["_walk_key"] == "kept" and d["ledger_net"] == 785
    p.books[b["id"]]["ledger_net"] = 692.0                       # 692.0 on the row, 785 in the dict
    assert _run(ml._walk_reread(t, d)) is None and t.stats["census"]["walk_row_moved"] == 1
    assert d["ledger_net"] == 692.0 and d["_walk_key"] == "kept"
    assert _run(ml._walk_reread(t, d)) is None and t.stats["census"]["walk_row_moved"] == 1, "692.0 == 692: unmoved"
    p.books[b["id"]]["open_order_id"] = 77
    assert _run(ml._walk_reread(t, d)) is None and t.stats["census"]["walk_row_moved"] == 2 and d["open_order_id"] == 77
    p.books[b["id"]].update(state="frozen", frozen_reason="placement_lost", venue_net=700.0)
    assert _run(ml._walk_reread(t, d)) is None and t.stats["census"]["walk_row_moved"] == 3
    assert _recent("walk_row_moved", b["id"])[-1]["cols"] == ["state", "frozen_reason", "venue_net"]
    p.books[b["id"]]["state"] = "closed"
    assert _run(ml._walk_reread(t, d)) == "walk_row_gone" and t.stats["census"]["walk_row_gone"] == 1
    assert ml._WALK_REREAD_COLS == ("ledger_net", "open_order_id", "state", "frozen_reason", "venue_net")
    assert ml._walk_col_moved(None, None) is False and ml._walk_col_moved(None, 1) is True
    assert ml._walk_col_moved("frozen", "frozen") is False and ml._walk_col_moved(1, 1.0) is False


# ------------------------------------------------------------ (2) the guarded ledger write

def _tick_obj(p, v=None):
    t = ml._Tick(pool=p, pmus=v or _Venue(), http=_Http(), now=NOW, stats=ml._new_stats(), started=0.0)
    ml._current_stats = t.stats
    return t


class _StaleWrites(_Pool):
    """The pool whose guarded ledger statement misses `misses` times
    whatever the row reads (the review's shape: execute answers
    'UPDATE 0')."""
    misses = 0
    ledger_calls = 0

    def _run(self, kind, sql, a):
        if kind == "execute" and ("ml-book-ledger-sell" in sql or "ml-book-ledger-buy" in sql):
            self.ledger_calls += 1
            if self.ledger_calls <= self.misses:
                self.sent.append((kind, sql, a))
                return "UPDATE 0"
        return super()._run(kind, sql, a)


def _sell_row(p, b, qty=93, oid="oid-8148"):
    return p.add_order(b, side=SELL, wire=0.89, qty=qty, kind="take", tif="IOC", state="open", order_id=oid)


def test_e28_a_sell_booking_whose_dict_read_785_while_the_row_reads_692_re_reads_and_books_599_not_692():
    p = _pool()
    b = p.add_book(ledger=LEDGER_BEFORE, ratio=0.10, avg_cost=0.90)
    o = _sell_row(p, b)
    stale = dict(b)                                             # the walk's dict: 785
    p.books[b["id"]]["ledger_net"] = LEDGER_AFTER_ONE           # the row: 692 (8147 booked)
    p.rows[b["standing_row_id"]]["filled_shares"] = float(LEDGER_AFTER_ONE)
    t = _tick_obj(p)
    out = _run(ml._book_fill(t, o, stale, 93.0, 0.89, False, True))
    assert out == "booked"
    assert p.books[b["id"]]["ledger_net"] == LEDGER_AFTER_TWO, "recomputed on the fresh 692, never 785 - 93"
    assert stale["ledger_net"] == LEDGER_AFTER_TWO, "the fresh booking's figures carried through the dict"
    assert o["booked_filled"] == 93 and o["filled"] == 93 and p.rows[b["standing_row_id"]]["filled_shares"] == 599.0
    assert t.stats["census"]["ledger_stale_reread"] == 1 and t.stats["census"].get("ledger_stale_refused", 0) == 0
    ev = _recent("ledger_stale_reread", b["id"])
    assert ev[-1]["ledger_was"] == LEDGER_BEFORE and ev[-1]["ledger_now"] == LEDGER_AFTER_ONE and ev[-1]["order_row"] == o["id"]
    assert b["id"] in t.filled_books
    # the guarded statements: two sell writes went out, the first with the stale guard 785 (missed), the
    # second with the fresh guard 692 (landed); the order cursor ran on both attempts (the first rolled back)
    sells = [a for k, s, a in p.sent if k == "execute" and "ml-book-ledger-sell" in s]
    assert [x[4] for x in sells] == [LEDGER_BEFORE, LEDGER_AFTER_ONE] and [x[1] for x in sells] == [692, 599]
    assert p.tx_events.count("begin") == 2


def test_e28_both_writes_missing_books_nothing_the_row_keeps_its_booked_figure_and_the_log_says_so_once(caplog):
    p = _pool()
    p.__class__ = _StaleWrites
    p.misses, p.ledger_calls = 4, 0                     # two misses on each of the two calls below
    b = p.add_book(ledger=LEDGER_BEFORE, ratio=0.10, avg_cost=0.90)
    o = _sell_row(p, b)
    stale = dict(b)
    p.books[b["id"]]["ledger_net"] = LEDGER_AFTER_ONE
    p.rows[b["standing_row_id"]]["filled_shares"] = float(LEDGER_AFTER_ONE)
    t = _tick_obj(p)
    with caplog.at_level(logging.ERROR, logger="sportsassets.workers.mirror_live"):
        out = _run(ml._book_fill(t, o, stale, 93.0, 0.89, False, True))
        out2 = _run(ml._book_fill(t, o, stale, 93.0, 0.89, False, True))
    assert out == "refused:ledger_stale" and out2 == "refused:ledger_stale"
    assert o["booked_filled"] == 0.0 and o["filled"] == 0.0, "the transaction rolled back: the cursor stands"
    assert p.books[b["id"]]["ledger_net"] == LEDGER_AFTER_ONE and stale["ledger_net"] == LEDGER_BEFORE
    assert p.rows[b["standing_row_id"]]["filled_shares"] == float(LEDGER_AFTER_ONE), "the standing row untouched"
    assert t.stats["census"]["ledger_stale_refused"] == 2 and t.stats["census"].get("ledger_stale_reread", 0) == 0
    assert b["id"] not in t.filled_books
    lines = [r.getMessage() for r in caplog.records if "missed twice" in r.getMessage()]
    assert len(lines) == 1 and f"book {b['id']}" in lines[0] and "785" in lines[0] and "692" in lines[0]
    ev = _recent("ledger_stale_refused", b["id"])
    assert ev[-1]["ledger_was"] == LEDGER_BEFORE and ev[-1]["ledger_now"] == LEDGER_AFTER_ONE
    # the unbooked fill takes _finish_order's own road: the row 'unknown', re-read next tick (5372-5378 on 6c0830d)
    assert "the row stays 'unknown' and is re-read until they book" in inspect.getsource(ml._finish_order)


def test_e28_the_re_read_raising_or_reading_no_ledger_books_nothing():
    # (a) the re-read raises
    p = _pool()
    b = p.add_book(ledger=LEDGER_BEFORE, ratio=0.10, avg_cost=0.90)
    o = _sell_row(p, b)
    stale = dict(b)
    p.books[b["id"]]["ledger_net"] = LEDGER_AFTER_ONE
    p.raise_on.append(("ml-book-read", RuntimeError("connection reset")))
    t = _tick_obj(p)
    assert _run(ml._book_fill(t, o, stale, 93.0, 0.89, False, True)) == "refused:ledger_stale"
    assert o["booked_filled"] == 0.0 and p.books[b["id"]]["ledger_net"] == LEDGER_AFTER_ONE
    assert t.stats["census"]["ledger_stale_refused"] == 1 and _recent("ledger_stale_refused", b["id"])[-1].get("ledger_now") is None
    # (b) the fresh row's ledger_net unreadable (None)
    p2 = _pool()
    b2 = p2.add_book(ledger=LEDGER_BEFORE, ratio=0.10, avg_cost=0.90)
    o2 = _sell_row(p2, b2)
    stale2 = dict(b2)
    p2.books[b2["id"]]["ledger_net"] = None
    t2 = _tick_obj(p2)
    assert _run(ml._book_fill(t2, o2, stale2, 93.0, 0.89, False, True)) == "refused:ledger_stale"
    assert o2["booked_filled"] == 0.0 and t2.stats["census"]["ledger_stale_refused"] == 1
    # (c) the row gone
    p3 = _pool()
    b3 = p3.add_book(ledger=LEDGER_BEFORE, ratio=0.10, avg_cost=0.90)
    o3 = _sell_row(p3, b3)
    stale3 = dict(b3)
    p3.books[b3["id"]]["ledger_net"] = LEDGER_AFTER_ONE
    t3 = _tick_obj(p3)
    real = p3.fetchrow

    async def gone(sql, *a):
        return None if "ml-book-read" in sql else await real(sql, *a)

    p3.fetchrow = gone
    assert _run(ml._book_fill(t3, o3, stale3, 93.0, 0.89, False, True)) == "refused:ledger_stale"
    assert o3["booked_filled"] == 0.0 and t3.stats["census"]["ledger_stale_refused"] == 1


def test_e28_the_buy_side_takes_the_same_guard_the_same_re_read_and_the_same_refusal():
    p = _pool()
    b = p.add_book(ledger=823, ratio=0.10, avg_cost=0.90)          # 8091's 823 in the dict
    o = p.add_order(b, side=BUY, wire=0.90, qty=145, kind="take", tif="IOC", state="open", order_id="oid-8092")
    stale = dict(b)
    p.books[b["id"]]["ledger_net"] = 900                            # another booking moved the row
    p.rows[b["standing_row_id"]]["filled_shares"] = 900.0
    t = _tick_obj(p)
    assert _run(ml._book_fill(t, o, stale, 145.0, 0.90, False, True)) == "booked"
    assert p.books[b["id"]]["ledger_net"] == 1045 and stale["ledger_net"] == 1045 and o["booked_filled"] == 145
    assert t.stats["census"]["ledger_stale_reread"] == 1
    buys = [a for k, s, a in p.sent if k == "execute" and "ml-book-ledger-buy" in s]
    assert [x[5] for x in buys] == [823, 900] and [x[1] for x in buys] == [968, 1045]
    # the buy's second miss
    p2 = _pool()
    p2.__class__ = _StaleWrites
    p2.misses, p2.ledger_calls = 2, 0
    b2 = p2.add_book(ledger=823, ratio=0.10, avg_cost=0.90)
    o2 = p2.add_order(b2, side=BUY, wire=0.90, qty=145, kind="take", tif="IOC", state="open", order_id="oid-8092b")
    stale2 = dict(b2)
    t2 = _tick_obj(p2)
    assert _run(ml._book_fill(t2, o2, stale2, 145.0, 0.90, False, True)) == "refused:ledger_stale"
    assert o2["booked_filled"] == 0.0 and p2.books[b2["id"]]["ledger_net"] == 823 and stale2["ledger_net"] == 823
    assert t2.stats["census"]["ledger_stale_refused"] == 1


def test_e28_the_hand_reduce_takes_the_same_guard_one_re_read_and_the_refusal_on_a_second_miss():
    p = _pool()
    b = p.add_book(ledger=LEDGER_BEFORE, ratio=0.10, avg_cost=0.90)
    stale = dict(b)
    p.books[b["id"]]["ledger_net"] = LEDGER_AFTER_ONE
    p.rows[b["standing_row_id"]]["filled_shares"] = float(LEDGER_AFTER_ONE)
    t = _tick_obj(p)
    out = _run(ml._book_hand_reduce(t, stale, 50.0, 0.90, {"h-1": 50.0}, ["h-1"]))
    assert out == "booked" and p.books[b["id"]]["ledger_net"] == LEDGER_AFTER_ONE - 50 and stale["ledger_net"] == 642
    assert t.stats["census"]["ledger_stale_reread"] == 1 and b["id"] in t.filled_books
    assert p.state[f"{ml._HAND_STATE_PREFIX}{b['id']}"]["adopted"] == {"h-1": 50.0}
    sells = [a for k, s, a in p.sent if k == "execute" and "ml-book-ledger-sell" in s]
    assert [x[4] for x in sells] == [LEDGER_BEFORE, LEDGER_AFTER_ONE]
    p2 = _pool()
    p2.__class__ = _StaleWrites
    p2.misses, p2.ledger_calls = 2, 0
    b2 = p2.add_book(ledger=LEDGER_BEFORE, ratio=0.10, avg_cost=0.90)
    stale2 = dict(b2)
    t2 = _tick_obj(p2)
    assert _run(ml._book_hand_reduce(t2, stale2, 50.0, 0.90, {"h-1": 50.0}, ["h-1"])) == "refused:ledger_stale"
    assert p2.books[b2["id"]]["ledger_net"] == LEDGER_BEFORE and stale2["ledger_net"] == LEDGER_BEFORE
    assert f"{ml._HAND_STATE_PREFIX}{b2['id']}" not in p2.state and b2["id"] not in t2.filled_books
    assert t2.stats["census"]["ledger_stale_refused"] == 1


def test_e28_the_guard_is_the_dicts_ledger_as_read_rounded_as_the_write_rounds_and_the_sql_carries_it():
    assert ml._ledger_guard({"ledger_net": 785}) == 785 and ml._ledger_guard({"ledger_net": 691.6}) == 692
    assert ml._ledger_guard({"ledger_net": None}) == 0 and ml._ledger_guard({}) == 0
    assert ml._ledger_guard({"ledger_net": -300.4, "intent": rules.ORDER_INTENT_SHORT}) == -300
    assert "AND ledger_net = $6 /* ml-book-ledger-buy */" in ml._SQL_BOOK_LEDGER_BUY
    assert "AND ledger_net = $5 /* ml-book-ledger-sell */" in ml._SQL_BOOK_LEDGER_SELL
    assert ml._SQL_BOOK_LEDGER_BUY.count("$") == 6 and ml._SQL_BOOK_LEDGER_SELL.count("$") == 5
    # the cursor, the cash statement and the standing-row statements byte for byte
    assert ml._SQL_ORDER_CURSOR == SQL_ORDER_CURSOR and ml._SQL_ORDER_CASH == SQL_ORDER_CASH
    for name, digest in LE_UNTOUCHED.items():
        assert _sha(getattr(le, name)) == digest, name
    src = inspect.getsource(ml._book_fill)
    for frag in ("_ledger_guard(bk)", "raise _LedgerStale()", "except _LedgerStale:", "fresh = await _ledger_reread(t, book)",
                 "for attempt in (0, 1):", 'return "refused:ledger_stale"', "_ledger_stale_refused(t, book, bk, fresh",
                 "if bk is not book:", '_mirror_stop("ledger_stale_reread", book.get("whale"))'):
        assert frag in src, frag
    assert src.count("raise _LedgerStale()") == 2 and src.count("_ledger_guard(bk)") == 2
    hsrc = inspect.getsource(ml._book_hand_reduce)
    for frag in ("_ledger_guard(bk)", "raise _LedgerStale()", "except _LedgerStale:", "fresh = await _ledger_reread(t, book)",
                 '_mirror_stop("ledger_stale_reread", book.get("whale"))', "_SQL_BOOK_LEDGER_SELL", "_write_state(conn,"):
        assert frag in hsrc, frag
    # the guard reads the dict, never the target or the venue
    for bad in ("target", "venue_net", "_frozen_venue", "his_net"):
        assert bad not in inspect.getsource(ml._ledger_guard), bad


# ------------------------------------------------------------ (3) the rail, the untouched functions, the names

def test_e28_the_walk_reread_switch_only_turns_off_from_the_environment_in_a_fresh_interpreter():
    src = inspect.getsource(rules)
    assert 'MIRROR_WALK_REREAD = env_switch("MIRROR_WALK_REREAD", True)' in src
    assert src.count("MIRROR_WALK_REREAD = ") == 1 and "MIRROR_WALK_REREAD" in rules.__all__
    for bad in ('capped_env("MIRROR_WALK_REREAD', 'min_wait_env("MIRROR_WALK_REREAD', 'unbounded_env("MIRROR_WALK_REREAD'):
        assert bad not in src, bad
    # the rail counts: one switch added (5 -> 6), no cap, no wait
    # env_switch 6 -> 7 at E29's landing (MIRROR_HAND_EXIT)
    assert src.count("env_switch(") == 8 and src.count("capped_env(") == 26 and src.count("min_wait_env(") == 8
    code = ("import json; from sportsassets.analytics import mirror_live_rules as r;"
            " print(json.dumps(r.MIRROR_WALK_REREAD))")
    # env_switch's own rule (rules 99-112): on/1/true/yes -> True, off/0/false/no -> False, anything else --
    # absent, blank, a typo -- the default (True); the environment may only turn it OFF
    for value, want in ((None, True), ("on", True), ("1", True), ("true", True), ("off", False), ("0", False),
                        ("no", False), ("false", False), ("", True), ("junk", True)):
        env = {k: v for k, v in os.environ.items() if k != "MIRROR_WALK_REREAD"}
        if value is not None:
            env["MIRROR_WALK_REREAD"] = value
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True,
                             cwd=str(ROOT / "backend"))
        assert json.loads(out.stdout.strip()) is want, (value, out.stdout, out.stderr[-300:])
    # the worker reads it through the module at call time, at the one site
    wsrc = inspect.getsource(ml._walk_books)
    assert wsrc.count("rules.MIRROR_WALK_REREAD") == 1
    assert "if rules.MIRROR_WALK_REREAD and await _walk_reread(t, book) is not None:" in wsrc
    assert wsrc.index("async with _lock_for(book[\"id\"]):") < wsrc.index("await _walk_reread(t, book)") \
        < wsrc.index("await _tick_book(t, book)")
    assert "MIRROR_WALK_REREAD" not in inspect.getsource(ml._book_fill), "the guard has no switch"
    assert "MIRROR_WALK_REREAD" not in inspect.getsource(ml._book_hand_reduce)


def test_e28_the_fast_ticks_own_re_read_the_act_the_takes_and_the_exit_path_are_byte_for_byte_6c0830d():
    got = {name: _sha(getattr(ml, name)) for name in UNTOUCHED}
    assert got == UNTOUCHED, {k: (got[k], UNTOUCHED[k]) for k in got if got[k] != UNTOUCHED[k]}
    # the fast tick's re-read stands as E9 built it: the row under the lock, then _tick_book on it
    fsrc = inspect.getsource(ml._fast_book)
    assert "row = await t.pool.fetchrow(_sql_book_read(t), bid)" in fsrc and "await _tick_book(t, fresh)" in fsrc
    # the rules module with this lane's one block excised -- and E29's one block, landed after -- hashes to
    # 6c0830d's: no rule of sizing, pricing or refusal moved (the E26 pattern)
    src = inspect.getsource(rules)
    # E31 (FILL lane 31, the newest): the maker wire, the paragraph that makes the take rails
    # documentary, rest_decision's `ttl_stands` and the four exported names -- excised the same way
    s31 = src.index("# THE RAILS THAT GOVERNED A TAKE ARE DOCUMENTARY FROM E31")
    src = src[:s31] + src[src.index("# Market families a book may open on", s31):]
    s31 = src.index("# ------------------------------------------------- E31: the maker wire")
    src = src[:s31] + src[src.index("def plan_wire(p: Plan | None)", s31):]
    src = src.replace('    "MAKER_TICK", "maker_wire", "maker_bound", "maker_compare_wire",\n', "")
    src = src.replace("                  entry: bool | None = None,\n"
                      "                  ttl_stands: bool = False) -> tuple[str, dict]:\n",
                      "                  entry: bool | None = None) -> tuple[str, dict]:\n")
    t31 = src.index("    constant), the mirror of take_allowed's wait.\n")
    t31e = src.index("reads the book's last plan). Everything else is\n", t31) + len(
        "reads the book's last plan). Everything else is\n")
    src = src[:t31] + "    constant), the mirror of take_allowed's wait. Everything else is\n" + src[t31e:]
    src = src.replace("    if age >= ttl and stands is not True and ttl_stands is not True:\n",
                      "    if age >= ttl and stands is not True:\n")
    # E30 (FILL lane 30): the backoff's switch and wait, landed after E29 -- excised the same way
    s30 = src.index("# A REST THE VENUE REJECTS TICK AFTER TICK BACKS OFF (E30")
    e30 = src.index('MIRROR_POST_ONLY_BACKOFF_S = min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0)\n') + len(
        'MIRROR_POST_ONLY_BACKOFF_S = min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0)\n')
    src = (src[:s30] + src[e30:]).replace('    "MIRROR_POST_ONLY_BACKOFF", "MIRROR_POST_ONLY_BACKOFF_S",\n', "")
    s29 = src.index("# THE DESK'S EXIT ENDS THE BOOK'S ADDS (E29")
    e29 = src.index('MIRROR_HAND_EXIT = env_switch("MIRROR_HAND_EXIT", True)\n') + len(
        'MIRROR_HAND_EXIT = env_switch("MIRROR_HAND_EXIT", True)\n')
    src = (src[:s29] + src[e29:]).replace('    "MIRROR_HAND_EXIT",\n', "")
    start = src.index("# THE WALK PLANS ON THE ROW AS IT STANDS (E28")
    end = src.index('MIRROR_WALK_REREAD = env_switch("MIRROR_WALK_REREAD", True)\n') + len(
        'MIRROR_WALK_REREAD = env_switch("MIRROR_WALK_REREAD", True)\n')
    excised = src[:start] + src[end:]
    excised = excised.replace('    "MIRROR_WALK_REREAD",\n', "")
    assert hashlib.sha256(excised.encode()).hexdigest()[:16] == RULES_SHA_6C0830D
    # no migration (061 the newest), no decision word
    mig = sorted(p.name for p in (ROOT / "backend" / "migrations").glob("*.sql"))
    assert mig[-1].startswith("061_")
    assert "decision" not in inspect.getsource(ml._walk_reread) and "decision" not in inspect.getsource(ml._ledger_reread)


def test_e28_the_census_place_the_emit_sites_and_the_docs():
    keys = ml.CENSUS_KEYS
    # E29 (FILL lane 29) landed after with four names between these and E19's: -18:-13 -> -22:-17, 242 -> 246
    assert keys[-33:-28] == NEW_NAMES and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-28:-24] == ("hand_exit", "hand_held", "hand_held_unread", "hand_exit_write_failed")
    assert keys[-37:-33] == ("exit_unconfirmed", "exit_confirmed", "exit_confirm_expired", "exit_flap_averted")
    assert keys[-41:-37] == ("hand_explained", "hand_adopted", "hand_unread", "hand_ambiguous")
    assert keys[-68] == "take_in_band" and keys[-1] == "cand_terminal_skipped"
    assert len(keys) == 257 and len(set(keys)) == len(keys)
    assert all(k not in ml._INTEG_CENSUS_KEYS for k in NEW_NAMES)
    src = inspect.getsource(ml)
    for name, sites in (("walk_row_moved", 1), ("walk_row_unread", 1), ("walk_row_gone", 1),
                        ("ledger_stale_reread", 2), ("ledger_stale_refused", 1)):
        assert src.count(f'_mirror_stop("{name}"') == sites, name
    wsrc = inspect.getsource(ml._walk_reread)
    for name in ("walk_row_moved", "walk_row_unread", "walk_row_gone"):
        assert f'_mirror_stop("{name}", w)' in wsrc, name
    assert '_recent(book["id"], "walk_row_moved", ledger_was=' in wsrc and "book.update(fresh)" in wsrc
    assert '_mirror_stop("ledger_stale_refused", book.get("whale"))' in inspect.getsource(ml._ledger_stale_refused)
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E28 -- .* \(2026-09-09, FILL lane 28\)", doc, re.M), "the E28 section header"
    sec = doc[doc.index("## 70. E28"):]
    for k in NEW_NAMES + ("MIRROR_WALK_REREAD", "_walk_reread", "_ledger_reread", "_book_fill", "_book_hand_reduce",
                          "_SQL_BOOK_LEDGER_SELL", "_SQL_BOOK_LEDGER_BUY", "AND ledger_net = $", "1333", "8147", "8148",
                          "785", "692", "599", "217", "8215", "venue_ledger_disagree", "22:49:59", "22:50:01",
                          "22:50:51", "test_e28_walk_reread.py", "env_switch", "books_data_wall", "_fast_book"):
        assert k in sec, k
    assert doc.rstrip().endswith(sec.rstrip()) and "## 68. E26" in doc[:doc.index("## 70. E28")], "appended last, after 68"


def test_e28_every_name_is_emitted_here(monkeypatch, caplog):
    """The lane's five names, each driven once (the worker file's coverage
    read imports this)."""
    test_e28_the_1333_shape_with_the_switch_on_plans_on_the_row_as_it_stands_and_sends_nothing(monkeypatch)
    test_e28_the_re_read_raising_skips_the_book_this_tick_under_walk_row_unread_and_sends_nothing(monkeypatch, caplog)
    test_e28_the_row_gone_or_closed_skips_the_book_this_tick_under_walk_row_gone_and_sends_nothing(monkeypatch)
    test_e28_a_sell_booking_whose_dict_read_785_while_the_row_reads_692_re_reads_and_books_599_not_692()
    test_e28_both_writes_missing_books_nothing_the_row_keeps_its_booked_figure_and_the_log_says_so_once(caplog)


# ------------------------------------------------------------ the review's pins (the fold)

@pytest.mark.parametrize("shape", ["unread", "gone"])
def test_e28_review_a_skipped_book_is_seen_so_the_candidate_stage_never_reads_its_market(monkeypatch, shape):
    """A book the walk skipped (walk_row_unread / walk_row_gone) never reaches
    _tick_book, whose FIRST line is t.books_seen.add -- so on the lane as built
    the candidate stage read the skipped book's market as bookless
    (_walk_candidate called for it: a mapping read, venue calls, and
    `venue_already_holds` on a market that has a book). On 6c0830d a book
    that RAISED was still seen. The skip must mark the book seen."""
    p, b = _world(monkeypatch)
    if shape == "unread":
        p.raise_on.append(("ml-book-read", RuntimeError("connection reset")))
    else:
        _other_booking_between_step_b_and_the_lock(monkeypatch, p, b, ledger=LEDGER_BEFORE, state="closed")
    calls = []
    real = ml._walk_candidate

    async def spy(t, w, cid):
        calls.append((w, cid))
        return await real(t, w, cid)

    monkeypatch.setattr(ml, "_walk_candidate", spy)
    v = _venue(LEDGER_BEFORE)
    st = _tick(p, v, http=_mkt(NET_1333, 0.0))
    assert not _places(v) and _census(st, f"walk_row_{shape}") == 1
    assert (b["whale"], b["condition_id"]) not in calls, (shape, calls)
    assert _census(st, "venue_already_holds") == 0


def test_e28_review_the_retry_hands_the_standing_row_statement_the_fresh_ledger_not_the_dicts(monkeypatch):
    """The review's M13b: on the retry every input of the booking is the row as
    re-read -- the standing-row statement's ledger argument
    (le._book_mirror_sell's third positional) included, never the dict's 785.
    The row's own shares clamp the sale identically today, so the outcome does
    not tell the two apart; the argument does."""
    p = _pool()
    b = p.add_book(ledger=LEDGER_BEFORE, ratio=0.10, avg_cost=0.90)
    o = _sell_row(p, b)
    stale = dict(b)
    p.books[b["id"]]["ledger_net"] = LEDGER_AFTER_ONE
    p.rows[b["standing_row_id"]]["filled_shares"] = float(LEDGER_AFTER_ONE)
    seen = []
    real = le._book_mirror_sell

    async def spy(conn, sid, shares, price, ledger_net, **kw):
        seen.append(float(ledger_net))
        return await real(conn, sid, shares, price, ledger_net, **kw)

    monkeypatch.setattr(le, "_book_mirror_sell", spy)
    t = _tick_obj(p)
    assert _run(ml._book_fill(t, o, stale, 93.0, 0.89, False, True)) == "booked"
    assert seen == [float(LEDGER_BEFORE), float(LEDGER_AFTER_ONE)], seen
    assert p.books[b["id"]]["ledger_net"] == LEDGER_AFTER_ONE - 93
