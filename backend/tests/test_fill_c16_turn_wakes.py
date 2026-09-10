"""FILL lane 16 (2026-09-09): the turn wakes the fast path -- a sign-flip
close hands the condition to _fast_wake, so the reopen's admission runs
on the next fast tick instead of the next candidate rotation or his next
fill's wake.

THE RULE. When a book closes under a sign flip (the turn lane 5 records
as `plan.turn`, docs section 50), the closing tick -- full or fast --
wakes the market for the fast path exactly as his fill does (notify's
fast half, `_fast_wake`): once the close's state write has succeeded,
`_turn_wake(book)` puts the condition in the fast path's woken set,
`plan.turn.woke` records whether it did, and `turn_woke_fast` counts the
same. The next fast tick finds no open book on the condition and runs
`_fast_candidate` -> `_walk_candidate` -> `rules.admission` with every
clause and refusal exactly as today (`side_band`, `drift`,
`snapshot_stale`, `venue_already_holds`, the game cap, the short gates);
a refusal at the wake is recorded by lane 5's `reopen_refused` and the
next wake is his next fill, as today. Nothing is admitted that is
refused today. No rail, no plan field beyond `turn.woke`, no decision
word, no migration.

THE SHAPES (hard2/cwht_2304.txt): book 741 (row 523: a LONG closed
`cashed_out` at 19:12:50 with sign_flip true, his net -2,924.7; the next
book 758 opened 19:13:47 = 56 s with no refusal between -- the
`side_band@19:26` on its path is 758's); book 529 (row 526: 11:28:16 ->
534 at 11:28:57 = 41 s, likewise cadence); Martinez 534 (row 549: the
SHORT closed 12:10:17, path `drift@12:10 drift@12:11 side_band@12:15`,
never reopened); book 353 (row 555: `side_band@01:22` stamped at the
01:21:57 close, next 369 at 01:32:22 = 625 s). Driven on lane 5's flip
fixtures (book 467's SHORT -> LONG turn and the LONG -> SHORT turn of
test_fill_t1_turn / test_mirror_short_sign_flip), E17's settle fixture
and test_e9_fast_path's `_fast` / `_walk` helpers.
"""
import asyncio
import hashlib
import inspect
import logging
import pathlib
import re
import time as _time

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e17_standing_reanchor import _retired, _settled_writes
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_e19_smaller_reading import FILLS_NET, VENUE_NET
from tests.test_fill_t1_turn import WALK_ELSEWHERE, _reopen_writes, _turn_reads
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    CID, INTENT, M, N, NOW, SHORT, SLUG, _armed, _census, _fill, _his, _Http, _mkt, _places, _pool,
    _rails_2026_09_06, _run, _short_book, _shorts_on, _tick, _Venue,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAME = "turn_woke_fast"
CLOCK = float(rules.MIRROR_FLAT_CLOSE_S)
OPEN = "MARKET_STATE_OPEN"
# the functions the plan names as NOT touched, hashed on the tip this lane
# was built on (219f140): a change to any of them is not this lane's
UNTOUCHED = {
    "_fast_candidate": "922585ffb6856f70", "_walk_candidate": "9c990feba5fdeb57",
    "_flip_since": "3cdab3ea4d75e7c5", "_fast_gate": "1932811194268668",
    "_fast_wake": "5e5b0cc324a652fe", "_fast_run": "cbd93bdfdf69d254",
    # fast_tick_once: 219f140 read 1aac535d56cae681; lane 14 (41d5e40) hands t_acquired in -- landed ahead, not this lane
    "fast_tick_once": "f0489ca714e21973", "_fast_book": "286e6fa4663c3887",
    # _fast_tick: 219f140 read 89893be25bcee246; lane 14 (41d5e40) stamps the prelude there -- landed ahead, not this lane
    # E29 (FILL lane 29): the hand-exit memo read at the fast tick's start (_load_hand_exits) -- ce6e086b18c28200 -> c364a8f6ed7f9b3b
    "_fast_tick": "c364a8f6ed7f9b3b", "notify": "ea431c3f6159798c",
    "_fast_requeue": "cb24c3ac1c0c7273", "_close_settled": "086987c757c5c3df",
}
RULES_UNTOUCHED = {"admission": "a10630d6d3a3a62c"}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _short_flipped_long(monkeypatch):
    """Book 467's shape (lane 5): a SHORT of 300 on his +300 -- tick 1 the
    priced cover fills, the flip waits for the venue's own 0. Returns the
    pool and the book, ready for the close tick."""
    _shorts_on(monkeypatch)
    p = _pool()                                   # his net +300: the default fixture
    b = _short_book(p, ledger=-300)
    st = _tick(p, _Venue(held={SLUG: -300}, ioc_fill=300.0, lift=300.0))
    assert _census(st, "sign_flip") == 1 and b["last_plan"]["sign_flip"] is True and b["ledger_net"] == 0
    assert b["state"] == "live" and "turn" not in b["last_plan"] and ml._FAST_WOKEN == {}
    return p, b


def _long_flipped_short(monkeypatch):
    """The LONG -> SHORT turn (test_mirror_short_sign_flip's long book: his
    100 long against 400 other, our 300 long): tick 1 the flatten rests at
    his equivalent's cent. Returns the pool, the book and the venue whose
    orders the close tick's venue must carry."""
    _shorts_on(monkeypatch)
    p = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    b = p.add_book(ledger=300)
    v = _Venue(bid=0.26, held={SLUG: 300})
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "sign_flip") == 1 and b["last_plan"]["close"] == "orders_open" and "turn" not in b["last_plan"]
    assert ml._FAST_WOKEN == {}
    return p, b, v


def _closed_with_turn(st, b, frm, to, his_net, at):
    lp = b["last_plan"]
    assert b["state"] == "closed" and lp["close"] == "cashed_out" and _census(st, "closed_cashed_out") == 1
    assert lp["turn"] == {"from": frm, "to": to, "his_net": his_net, "at": at, "woke": True}, lp["turn"]
    assert _census(st, NEW_NAME) == 1
    assert ml._FAST_WOKEN == {CID: 0}, "the market sits in the fast path's woken set, a fresh wake (tries 0)"
    return lp


# ------------------------------------------------ the cadence reopens (741, 529)

def test_c16_book_741s_shape_the_flip_close_on_a_full_tick_wakes_the_market_and_the_next_fast_tick_opens_the_other_side(monkeypatch):
    """cwht 523: the LONG closed under the flip; 758 opened 56 s later with
    no refusal between -- cadence. Here: the close tick wakes the condition
    (turn.woke true, turn_woke_fast 1, the fixture unarmed so nothing is
    scheduled and the market waits in the woken set); the next fast tick
    takes it, finds no open book, and _fast_candidate opens the SHORT as
    episode 2 -- no 054 row, no reopen_refused, the closed row untouched."""
    ml._cand_refusal_last.clear()
    p, b, v = _long_flipped_short(monkeypatch)
    v2 = _Venue(held={}, fills={"oid-1": (300.0, 0.32)})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(100.0, 400.0))
    lp = _closed_with_turn(st2, b, INTENT, SHORT, -300.0, NOW + 30)
    assert ml._fast_task is None, "unarmed: nothing scheduled, the woken set holds the market"
    assert lp["turn"]["woke"] is True and lp["turn"]["his_net"] == lp["net"]
    # the next fast tick: the woken set alone (cids None), the open books re-read, none on the market
    _walk()
    v3 = _Venue(held={})
    fs = _fast(p, v3, cids=None, now=NOW + 32, http=_mkt(100.0, 400.0))
    assert fs["woken"] == [CID] and fs["fast"]["tries"] == {CID: 0} and _skips(fs).get(CID) is None, fs["fast"]
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == SHORT and books[1]["episode"] == 2 and books[1]["state"] == "live"
    assert len(_places(v3)) == 1 and _places(v3)[0][6] == SHORT
    assert _census(fs, "short_open") == 1 and _census(fs, "fast_tick_placed") == 1 and fs["fast"]["placed"] == 1
    assert ml._FAST_WOKEN == {} and _census(fs, NEW_NAME) == 0
    assert "reopen_refused" not in b["last_plan"] and _census(fs, "reopen_refused") == 0 and p.cand_refusals == []
    assert not _reopen_writes(p) and not _turn_reads(p), "an open reads no turn and writes nothing on the closed row"
    assert b["last_plan"]["turn"] == lp["turn"], "the turn stands as the close wrote it"


def test_c16_book_529s_shape_the_armed_path_schedules_the_fast_tick_itself_and_it_opens_the_other_side(monkeypatch):
    """cwht 526: 529 closed 11:28:16, 534 opened 11:28:57 = 41 s, likewise
    cadence. The path ARMED (main's _arm_fast): the close tick's wake
    schedules one fast run, which waits on the tick lock and opens the
    other side the moment the full tick lets go -- no test-side wake, no
    floor to wait out (the first fast tick runs at once)."""
    ml._cand_refusal_last.clear()
    slept = []

    async def _s(s):
        slept.append(s)
    monkeypatch.setattr(ml, "_fast_sleep", _s)
    p, b, v = _long_flipped_short(monkeypatch)
    v2 = _Venue(held={}, fills={"oid-1": (300.0, 0.32)})
    v2.orders = v.orders
    http = _mkt(100.0, 400.0)

    async def _drive():
        now2 = _time.time()                       # the close on the real clock: the walk it leaves is fresh for the fast tick
        ml._backoff_until = 0.0
        p.clock = now2
        ml._arm_fast(p, v2, http)
        st2 = await ml.tick_once(p, v2, http, now_ts=now2)
        assert ml._fast_task is not None and not ml._fast_task.done(), "the close scheduled the fast run"
        assert ml._FAST_WOKEN == {CID: 0} and _census(st2, NEW_NAME) == 1
        assert b["state"] == "closed" and b["last_plan"]["turn"]["woke"] is True
        await ml._fast_task
        return st2
    _run(_drive())
    assert slept == [], "no floor to wait out: the first wake runs at once"
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == SHORT and books[1]["episode"] == 2
    assert len(_places(v2)) == 1 and _places(v2)[-1][6] == SHORT
    assert ml._FAST_WOKEN == {} and ml._fast_census["fast_tick"] == 1 and ml._fast_census["fast_tick_placed"] == 1
    assert ml._fast_census["short_open"] == 1 and ml._fast_census[NEW_NAME] == 0
    assert "reopen_refused" not in b["last_plan"] and p.cand_refusals == []


def test_c16_the_same_close_on_a_fast_tick_puts_the_market_back_for_the_next_fast_tick(monkeypatch):
    """Book 467's shape on the FAST path: his flipping fill woke the book;
    the fast tick takes the market off the woken set at its start, its
    _tick_book closes the SHORT under the flip, and the re-wake puts the
    condition BACK -- the same tick's `books` were read before the close,
    so it is the NEXT fast tick that re-reads them, finds none, and opens
    the LONG through _fast_candidate."""
    ml._cand_refusal_last.clear()
    p, b = _short_flipped_long(monkeypatch)
    _walk()
    ml._FAST_WOKEN[CID] = 0                       # his flipping fill's wake
    fs1 = _fast(p, _Venue(held={}), cids=None, now=NOW + 30)
    assert fs1["woken"] == [CID] and _skips(fs1).get(CID) is None
    lp = _closed_with_turn(fs1, b, SHORT, INTENT, 300.0, NOW + 30)
    assert lp["turn"]["woke"] is True and len(p.books) == 1 and fs1["fast"]["placed"] == 0
    assert ml._fast_task is None, "the direct call schedules nothing (unarmed); the run loop's next iteration is the tick"
    # the next fast tick: taken again (a fresh wake: tries 0), no open book on the market -> the candidate opens it
    v2 = _Venue(held={})
    fs2 = _fast(p, v2, cids=None, now=NOW + 32)
    assert fs2["woken"] == [CID] and fs2["fast"]["tries"] == {CID: 0} and _skips(fs2).get(CID) is None, fs2["fast"]
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == INTENT and books[1]["episode"] == 2 and books[1]["target"] == 300
    assert len(_places(v2)) == 1 and _places(v2)[0][6] == INTENT
    assert _census(fs2, "fast_tick_placed") == 1 and _census(fs2, NEW_NAME) == 0 and ml._FAST_WOKEN == {}
    assert "reopen_refused" not in b["last_plan"] and p.cand_refusals == []


def test_c16_a_cancelled_verdict_flip_close_wakes_exactly_as_a_cashed_out_one(monkeypatch):
    """cwht_2304 517-519: the smu-flst chain (331 -> 333 -> 336) -- three
    sign-flip closes with the `cancelled` verdict (sign_flip true, nothing
    ever bought: the flow-only book of E12) at lags 30 / 37 / 55 s, the rows
    this lane's own live proof cites. A LONG book at ledger 0 that never
    bought, his net flipped (100 long against 400 other), the venue flat ->
    closed `cancelled` under the turn, woke, counted -- the verdict word is
    not the wake's condition (kills: the wake gated on `cashed_out`)."""
    _shorts_on(monkeypatch)
    ml._cand_refusal_last.clear()
    p = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    b = p.add_book(ledger=0, gross_buy=0.0)
    st = _tick(p, _Venue(held={}), http=_mkt(100.0, 400.0))
    lp = b["last_plan"]
    assert b["state"] == "closed" and lp["close"] == "cancelled" and _census(st, "closed_cancelled") == 1
    assert _census(st, "closed_cashed_out") == 0 and lp["sign_flip"] is True
    assert lp["turn"] == {"from": INTENT, "to": SHORT, "his_net": -300.0, "at": NOW, "woke": True}, lp["turn"]
    assert _census(st, NEW_NAME) == 1 and ml._FAST_WOKEN == {CID: 0}
    # the next fast tick opens the SHORT as episode 2, as after a cashed_out turn
    _walk()
    v2 = _Venue(held={})
    fs = _fast(p, v2, cids=None, now=NOW + 2, http=_mkt(100.0, 400.0))
    assert fs["woken"] == [CID] and _skips(fs).get(CID) is None, fs["fast"]
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == SHORT and books[1]["episode"] == 2
    assert len(_places(v2)) == 1 and _places(v2)[0][6] == SHORT and ml._FAST_WOKEN == {}
    assert "reopen_refused" not in b["last_plan"] and not _reopen_writes(p) and not _turn_reads(p)


def test_c16_the_fast_close_re_wake_is_taken_by_the_run_loops_next_iteration_after_the_floor(monkeypatch):
    """The armed end-to-end of the FAST tick's own close (pinned above by
    two direct fast ticks): his flipping fill's notify schedules the run;
    its first fast tick closes the SHORT under the turn and puts the market
    back; _fast_run sleeps the floor once and its next fast tick opens the
    LONG -- one run, two fast ticks, one floor, no test-side re-wake."""
    ml._cand_refusal_last.clear()
    slept = []

    async def _s(s):
        slept.append(s)
    monkeypatch.setattr(ml, "_fast_sleep", _s)
    p, b = _short_flipped_long(monkeypatch)
    v = _Venue(held={})

    async def _drive():
        now2 = _time.time()
        ml._backoff_until = 0.0
        p.clock = now2
        ml._last_walk = ({}, now2)          # the last full tick's walk: the venue at 0
        ml._last_filled = set()
        ml._arm_fast(p, v, _Http())
        ml.notify(CID)                      # his flipping fill's wake
        assert ml._fast_task is not None
        await ml._fast_task
    _run(_drive())
    assert slept == [ml.FAST_TICK_MIN_S], slept
    assert b["state"] == "closed" and b["last_plan"]["turn"]["woke"] is True
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == INTENT and books[1]["episode"] == 2
    assert len(_places(v)) == 1 and _places(v)[0][6] == INTENT
    assert ml._fast_census["fast_tick"] == 2 and ml._fast_census[NEW_NAME] == 1 and ml._fast_census["fast_tick_placed"] == 1
    assert ml._FAST_WOKEN == {} and "reopen_refused" not in b["last_plan"]


# ----------------------------------------- the refusals at the wake (534, 353)

def test_c16_martinez_534s_shape_the_woken_candidate_is_refused_drift_by_name_and_nothing_wakes_again_until_his_next_fill(monkeypatch):
    """cwht 549 / task 71: the SHORT closed on the flip at 12:10:17, the
    reopen refused `drift` at 12:10:43 (his fills 11,974.6 against the
    venue's 25,104; the whole-book walk fresh and the per-market read
    naming neither token). Here the wake runs the same admission 2 s
    after the close: refused `drift` BY NAME, the 054 row and lane 5's
    `reopen_refused` written on the closed row, nothing opens, the woken
    set empty -- no second wake until his next fill (notify)."""
    _rails_2026_09_06(monkeypatch)
    ml._cand_refusal_last.clear()
    p, b = _short_flipped_long(monkeypatch)
    st2 = _tick(p, _Venue(held={}), now=NOW + 30)
    _closed_with_turn(st2, b, SHORT, INTENT, 300.0, NOW + 30)
    # Martinez's reading at the wake: his fills against the venue's whole-book walk
    p.fills, p.snap, p.snap_at = [_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10)], {M: VENUE_NET, N: 0.0}, NOW - 8
    _walk()
    fs = _fast(p, _Venue(bid=0.60, ask=0.61), cids=None, now=NOW + 32, http=WALK_ELSEWHERE)
    assert fs["woken"] == [CID] and _skips(fs).get(CID) != "order_open"
    assert len(p.books) == 1 and b["state"] == "closed" and fs["fast"]["placed"] == 0
    assert _census(fs, "drift") >= 1 and _census(fs, "drift_smaller_open") == 0
    assert [r["refusal"] for r in p.cand_refusals] == ["drift"], "the 054 row, by name"
    rr = b["last_plan"]["reopen_refused"]
    assert rr["name"] == "drift" and rr["at"] == NOW + 32 and rr["band"] == float(rules.LIVE_SIDE_PRICE_BAND_MAX)
    assert _census(fs, "reopen_refused") == 1 and len(_turn_reads(p)) == 1 and len(_reopen_writes(p)) == 1
    assert b["last_plan"]["turn"]["woke"] is True and b["last_plan"]["sign_flip"] is True, "merged, never replaced"
    assert ml._FAST_WOKEN == {} and _census(fs, NEW_NAME) == 0, "a refusal wakes nothing again"
    # his next fill is the next wake, as today
    ml.notify(CID)
    assert ml._FAST_WOKEN == {CID: 0}


def test_c16_book_353s_shape_side_band_at_the_wake_then_his_next_fills_wake_is_admitted_as_today(monkeypatch):
    """cwht 555: `side_band@01:22` stamped at the 01:21:57 close, the next
    book at 01:32:22 = 625 s -- the band's wait, which no wake moves. The
    ask 0.52 against his 0.31 at the wake -> `side_band` by name and lane
    5's record; his next fill's wake with the ask back at 0.32 -> the
    LONG opens, exactly as today."""
    _rails_2026_09_06(monkeypatch)
    ml._cand_refusal_last.clear()
    p, b = _short_flipped_long(monkeypatch)
    st2 = _tick(p, _Venue(held={}), now=NOW + 30)
    _closed_with_turn(st2, b, SHORT, INTENT, 300.0, NOW + 30)
    _walk()
    v = _Venue(bid=0.50, ask=0.52)
    fs = _fast(p, v, cids=None, now=NOW + 32)
    assert fs["woken"] == [CID] and len(p.books) == 1 and not _places(v)
    assert _census(fs, "side_band") >= 1 and [r["refusal"] for r in p.cand_refusals] == ["side_band"]
    rr = b["last_plan"]["reopen_refused"]
    assert rr["name"] == "side_band" and rr["at"] == NOW + 32 and rr["ask"] == 0.52
    assert _census(fs, "reopen_refused") == 1 and ml._FAST_WOKEN == {}
    # his next fill's wake: the ask back inside the band, admitted as today
    ml.notify(CID)
    assert ml._FAST_WOKEN == {CID: 0}
    v2 = _Venue(held={})
    fs2 = _fast(p, v2, cids=None, now=NOW + 40)
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == INTENT and books[1]["episode"] == 2
    assert len(_places(v2)) == 1 and _places(v2)[0][6] == INTENT and _census(fs2, "fast_tick_placed") == 1
    assert b["last_plan"]["reopen_refused"] == rr, "the refusal stands on the closed row as lane 5 left it"
    assert ml._FAST_WOKEN == {}


# --------------------------------------------- the flip's path alone (no wake)

def test_c16_a_flat_clock_close_a_market_end_close_and_a_settle_close_wake_nothing(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    # the flat clock: a short book flat at target 0 on his 8 shares of the LONG token (nothing on its side)
    p = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000)], snap={M: 8.0, N: 0.0})
    b = _short_book(p, ledger=0, avg=0.31, ratio=0.10, last_plan={"flat_since": NOW - (CLOCK + 1), "target": 0})
    st = _tick(p, _Venue(held={SLUG: 0}), http=_mkt(8.0, 0.0))
    assert b["state"] == "closed" and b["last_plan"]["close"] in ("cashed_out", "cancelled")
    assert "turn" not in b["last_plan"] and _census(st, NEW_NAME) == 0 and ml._FAST_WOKEN == {} and ml._fast_task is None
    # the market's end on a flat long book he still holds: closed cashed_out as before, no turn, no wake
    p2 = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000)], snap={M: 8.0, N: 0.0})
    b2 = p2.add_book(ledger=0, ratio=0.10, gross_buy=3.1, avg_cost=0.31,
                     last_plan={"flat_since": NOW - (CLOCK + 1), "target": 0})
    p2.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st2 = _tick(p2, _Venue(held={SLUG: 0}), http=_mkt(8.0, 0.0))
    assert b2["state"] == "closed" and _census(st2, "closed_cashed_out") == 1 and _census(st2, "market_closed") == 1
    assert "turn" not in b2["last_plan"] and _census(st2, NEW_NAME) == 0 and ml._FAST_WOKEN == {}
    # the venue's own settle (E17): _close_settled, never _maybe_close_episode -- no turn, no wake
    p3 = _pool()
    p3.markets[CID] = {"closed": False, "resolved": False, "resolved_prices": None}
    b3, row = _retired(p3, pnl=5.2, settled_at=NOW - 10)
    st3 = _tick(p3, _Venue(state=OPEN, held={SLUG: 13}))
    assert b3["state"] == "closed" and b3["last_reason"] == "closed: standing row settled" and row["status"] == "settled"
    assert _settled_writes(p3, b3["id"]) == [(b3["id"], 5.2, None, None, "closed: standing row settled")]
    assert "turn" not in (b3.get("last_plan") or {}) and _census(st3, NEW_NAME) == 0 and ml._FAST_WOKEN == {}
    assert "_turn_wake" not in inspect.getsource(ml._close_settled) and "_fast_wake" not in inspect.getsource(ml._close_settled)


# ------------------------------------------------------------- fail closed

def test_c16_the_state_write_failing_wakes_nothing_and_the_book_is_not_closed(monkeypatch):
    """The wake follows the write: `ml-book-state` raising on the close
    tick leaves the book live (book_error, as today), the woken set empty,
    nothing counted, the row carrying the prior tick's plan (sign_flip,
    no turn -- section 50's own sentence). What follows is today's
    behaviour, pinned so nobody reads it as the wake's: the standing row
    was already retired by le._close_mirror_episode BEFORE the write, so
    the NEXT tick closes the book through E17's retired-row path
    (_close_settled, `closed: standing row cashed_out`) -- no turn, no
    wake, and the reopen is the full tick's candidate stage as before
    this lane. The turn and the wake are lost with the write; named,
    not folded (_close_settled is hashed untouched)."""
    p, b = _short_flipped_long(monkeypatch)
    p.raise_on.append(("ml-book-state", RuntimeError("db")))
    st2 = _tick(p, _Venue(held={}), now=NOW + 30)
    assert b["state"] == "live" and _census(st2, "closed_cashed_out") == 0 and _census(st2, "book_error") == 1
    assert ml._FAST_WOKEN == {} and _census(st2, NEW_NAME) == 0 and ml._fast_task is None
    assert "turn" not in b["last_plan"] and b["last_plan"]["sign_flip"] is True, "the prior tick's plan"
    assert p.rows[b["standing_row_id"]]["status"] == "cashed_out", "the episode close ran before the write"
    p.raise_on.clear()
    st3 = _tick(p, _Venue(held={}), now=NOW + 60)
    assert b["state"] == "closed" and b["last_reason"] == "closed: standing row cashed_out"
    assert _settled_writes(p, b["id"]) == [(b["id"], None, None, None, "closed: standing row cashed_out")]
    assert "turn" not in b["last_plan"] and _census(st3, NEW_NAME) == 0 and _census(st3, "closed_cashed_out") == 0
    assert ml._FAST_WOKEN == {} and ml._fast_task is None, "no wake: the full tick's candidate stage reads the market as today"


def test_c16_the_woken_set_bound_refuses_the_wake_by_name_the_book_still_closes(monkeypatch):
    """_WOKEN_MAX (200) reached: the wake is refused exactly as his fill's
    is -- `turn.woke` False, nothing counted, the market not in the set;
    the close itself stands. Driven on the fast tick's own close (a full
    tick drains the set at its start)."""
    p, b = _short_flipped_long(monkeypatch)
    _walk()
    for i in range(ml._WOKEN_MAX):
        ml._FAST_WOKEN[f"0xfull{i}"] = 0
    assert len(ml._FAST_WOKEN) == ml._WOKEN_MAX == 200
    fs = _fast(p, _Venue(held={}), cids=[CID], now=NOW + 30)
    lp = b["last_plan"]
    assert b["state"] == "closed" and lp["close"] == "cashed_out" and _census(fs, "closed_cashed_out") == 1
    assert lp["turn"] == {"from": SHORT, "to": INTENT, "his_net": 300.0, "at": NOW + 30, "woke": False}, lp["turn"]
    assert _census(fs, NEW_NAME) == 0 and CID not in ml._FAST_WOKEN and len(ml._FAST_WOKEN) == 200


def test_c16_turn_wake_fails_closed_a_blank_condition_and_a_raising_wake_read_false_and_never_raise(monkeypatch, caplog):
    assert ml._turn_wake({"id": 1, "condition_id": ""}) is False and ml._FAST_WOKEN == {}
    assert ml._turn_wake({"id": 1, "condition_id": None}) is False and ml._turn_wake({"id": 1}) is False
    assert ml._FAST_WOKEN == {}

    def _boom(cid):
        raise RuntimeError("loop gone")
    monkeypatch.setattr(ml, "_fast_wake", _boom)
    with caplog.at_level(logging.DEBUG, logger="sportsassets.workers.mirror_live"):
        assert ml._turn_wake({"id": 7, "condition_id": CID}) is False
    assert ml._FAST_WOKEN == {} and any("turn wake for book 7 dropped" in r.getMessage() for r in caplog.records)
    monkeypatch.undo()
    # the plain wake: in the set, tries 0; a second wake on the same market is one entry
    assert ml._turn_wake({"id": 7, "condition_id": f" {CID} "}) is True and ml._FAST_WOKEN == {CID: 0}
    assert ml._turn_wake({"id": 7, "condition_id": CID}) is True and ml._FAST_WOKEN == {CID: 0}
    ml._FAST_WOKEN.clear()


def test_c16_unarmed_or_outside_a_loop_the_wake_schedules_nothing_and_the_full_tick_reads_the_market():
    """No running loop / the path unarmed: _fast_wake returns after the
    set (nothing scheduled), and the next FULL tick drains the set at its
    start and reads the market in its own candidate stage -- as today."""
    assert ml._fast_ctx is None
    assert ml._turn_wake({"id": 1, "condition_id": CID}) is True     # outside a loop
    assert ml._FAST_WOKEN == {CID: 0} and ml._fast_task is None

    async def _in_loop():
        assert ml._turn_wake({"id": 1, "condition_id": "0xother"}) is True
        assert ml._fast_task is None and ml._FAST_WOKEN == {CID: 0, "0xother": 0}
    _run(_in_loop())
    p = _pool()
    p.add_book(ledger=0)
    st = _tick(p, _Venue())
    assert ml._FAST_WOKEN == {} and _census(st, "fast_tick") == 0, "the full tick drained the set, as it drains his fill's"


# ---------------------------------------- admission untouched, the clause order

def test_c16_admission_and_the_fast_path_are_untouched_and_the_wake_is_a_courtesy_never_a_condition():
    for name, h in UNTOUCHED.items():
        assert _sha(getattr(ml, name)) == h, name
    for name, h in RULES_UNTOUCHED.items():
        assert _sha(getattr(rules, name)) == h, name
    assert ml._WOKEN_MAX == 200 and ml.FAST_TICK_MIN_S == 2.0 and ml.FAST_TICK_MAX == 5 and ml.FAST_RETRIES == 2
    # _fast_candidate's clauses run in their own order before the walk
    src = inspect.getsource(ml._fast_candidate)
    order = ['"full_tick_pending"', '"walk_stale"', '"unmapped"', '"cand_no_mark_skipped"', '"cand_terminal_skipped"',
             '"venue_calls_capped"', '"capped_tick"', "await _walk_candidate(t, w, cid)"]
    idx = [src.index(x) for x in order]
    assert idx == sorted(idx), idx
    # the wake: after the state write, under the flip's branch alone, once in the module; _fast_wake alone (never notify: no full-tick wake, no cadence change)
    msrc = inspect.getsource(ml._maybe_close_episode)
    assert msrc.count('plan["turn"]["woke"] = _turn_wake(book)') == 1
    assert msrc.index("await t.pool.execute(_SQL_BOOK_STATE") < msrc.index("_turn_wake(book)")
    assert msrc.index('plan["turn"] = {"from": book.get("intent"),') < msrc.index("await t.pool.execute(_SQL_BOOK_STATE")
    tail = msrc[msrc.index("await t.pool.execute(_SQL_BOOK_STATE"):]
    # lane 5's own predicate, re-read after the write (lane 5's text before the write is byte-identical: its pin holds)
    assert tail.index('if plan.get("sign_flip") is True:') < tail.index("_turn_wake(book)")
    assert msrc.count('if plan.get("sign_flip") is True:') == 2 and 'if plan.get("sign_flip") is True:\n        # FILL lane 5' in msrc
    assert msrc.count('_mirror_stop("turn_woke_fast", book["whale"])') == 1
    assert "_WAKE.set" not in msrc and "_WOKEN.add" not in msrc and "notify(" not in msrc
    assert inspect.getsource(ml).count('_mirror_stop("turn_woke_fast"') == 1
    wsrc = inspect.getsource(ml._turn_wake)
    assert "_fast_wake(cid)" in wsrc and "notify(" not in wsrc and "_WOKEN.add" not in wsrc and "_WAKE.set" not in wsrc
    assert "return cid in _FAST_WOKEN" in wsrc and "except Exception" in wsrc
    # the full tick's own drain of the fast set is untouched (the wake is dropped there as his fill's is)
    assert "_FAST_WOKEN.clear()" in inspect.getsource(ml.tick_once)
    # the terminal memo stands: 900 s, released by his fill's wake alone
    from sportsassets.workers import mirror_shadow as ms
    assert ms.UNMAPPED_TTL_S == 900.0


def test_c16_the_terminal_memo_and_book_exists_stand_at_the_wake(monkeypatch):
    """A turned market whose next book already exists -> `book_exists`
    (the candidate is not walked: the fast tick reads the open book
    instead); a turned market under the terminal memo -> the memo's
    skip, no read."""
    _rails_2026_09_06(monkeypatch)
    ml._cand_refusal_last.clear()
    p, b = _short_flipped_long(monkeypatch)
    st2 = _tick(p, _Venue(held={}), now=NOW + 30)
    _closed_with_turn(st2, b, SHORT, INTENT, 300.0, NOW + 30)
    # the terminal memo on the market (a venue read that ended it): the woken candidate is skipped by name
    ml._terminal_until[("rn1", CID)] = NOW + 900.0
    _walk()
    v = _Venue(held={})
    fs = _fast(p, v, cids=None, now=NOW + 32)
    assert _skips(fs).get(CID) == "cand_terminal_skipped" and _census(fs, "cand_terminal_skipped") == 1
    assert len(p.books) == 1 and not _places(v) and p.cand_refusals == [] and ml._FAST_WOKEN == {}
    ml._terminal_until.clear()
    # the next book already exists on the market: the fast tick reads THAT book, never the candidate
    live = p.add_book(ledger=100, ratio=0.10, avg_cost=0.31, episode=2)
    ml._FAST_WOKEN[CID] = 0
    v2 = _Venue(held={SLUG: 100})
    fs2 = _fast(p, v2, cids=None, now=NOW + 34)
    assert live["state"] == "live" and len(p.books) == 2 and p.cand_refusals == []
    assert "reopen_refused" not in b["last_plan"] and not _turn_reads(p)


def test_c16_the_wake_releases_the_no_mark_memo_as_his_fills_wake_does_only_the_bare_terminal_memo_stands(monkeypatch):
    """_release_cand_memo (the walk's rule, untouched): a market that WOKE
    this tick has its `no_mark` / `unmapped` memo dropped -- and the
    terminal memo when its event-stale sibling holds an `at` -- whatever his
    newest fill's stamp says (mirror_live 7289-7297). The turn's wake is a
    wake: a no_mark memo set AFTER his newest fill is released at the wake
    (`cand_memo_released`), the market is READ, and admission admits or
    refuses by name as today. Only a terminal memo with no sibling (D1's
    word) stands its TTL at the wake (the lane's own test). Pinned so the
    fail-closed list says what the code does."""
    _rails_2026_09_06(monkeypatch)
    ml._cand_refusal_last.clear()
    p, b = _short_flipped_long(monkeypatch)
    st2 = _tick(p, _Venue(held={}), now=NOW + 30)
    _closed_with_turn(st2, b, SHORT, INTENT, 300.0, NOW + 30)
    # a no_mark memo written after his newest fill (NOW - 3000): the stamp alone would not release it
    ml._no_mark_until[("rn1", CID)] = NOW + 900.0
    ml._no_mark_memo[("rn1", CID)] = NOW + 31.0
    _walk()
    v = _Venue(held={})
    fs = _fast(p, v, cids=None, now=NOW + 32)
    assert fs["woken"] == [CID] and _skips(fs).get(CID) is None, fs["fast"]
    assert _census(fs, "cand_memo_released") == 1 and ("rn1", CID) not in ml._no_mark_until
    assert _census(fs, "cand_no_mark_skipped") == 0
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == INTENT and books[1]["episode"] == 2, "read at the wake, admitted as today"
    assert len(_places(v)) == 1 and ml._FAST_WOKEN == {}


def test_c16_the_turn_wake_never_sets_the_full_ticks_wake_or_its_woken_set(monkeypatch):
    """The wake is _fast_wake alone: no `_WAKE.set()`, no `_WOKEN.add` --
    a full tick is NOT started on a turn (FILL_plan_C.md 'Not built': C6 B)
    -- pinned on behaviour, not on the source (kills: a `_WAKE.set()` /
    `_WOKEN.add(cid)` beside the wake in _maybe_close_episode)."""
    ml._cand_refusal_last.clear()
    p, b, v = _long_flipped_short(monkeypatch)
    v2 = _Venue(held={}, fills={"oid-1": (300.0, 0.32)})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(100.0, 400.0))
    _closed_with_turn(st2, b, INTENT, SHORT, -300.0, NOW + 30)
    assert ml._WOKEN == set() and not ml._WAKE.is_set(), "the full tick's wake is untouched by a turn"
    # the same on the fast tick's own close
    ml._FAST_WOKEN.clear()
    p2, b2 = _short_flipped_long(monkeypatch)
    _walk()
    ml._FAST_WOKEN[CID] = 0
    fs = _fast(p2, _Venue(held={}), cids=None, now=NOW + 30)
    _closed_with_turn(fs, b2, SHORT, INTENT, 300.0, NOW + 30)
    assert ml._WOKEN == set() and not ml._WAKE.is_set()


# ----------------------------------------------------- the names, the docs

def test_c16_the_census_place_the_emit_site_no_rail_no_decision_word_no_migration():
    keys = ml.CENSUS_KEYS
    # E21 (FILL lane 10, six fast_* names) landed after this lane, between this name and the key (-14 -> -20)
    # E23 (FILL lane 23, six names) landed after this lane, between E21's six and the key (-20 -> -26)
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-26 / -27 / -25 -> -30 / -31 / -29, -31:-27 -> -35:-31, -34:-31 -> -38:-35)
    assert keys[-54] == NEW_NAME and keys[-55] == "cand_market_closed_db" and keys[-53] == "fast_order_open"
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-59:-55] == ("lost_fill_adopted", "lost_fill_unread", "lost_fill_unexplained", "lost_fill_ambiguous")
    assert keys[-62:-59] == ("he_holds", "he_holds_unread", "reopen_refused")
    assert keys[-1] == "cand_terminal_skipped" and keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert keys.count(NEW_NAME) == 1 and len(set(keys)) == len(keys)
    assert ml._new_stats()["census"][NEW_NAME] == 0 and NEW_NAME not in ml._INTEG_CENSUS_KEYS
    # no rail, no knob: nothing of the lane's own read from the environment
    src = inspect.getsource(ml) + inspect.getsource(rules)
    for word in ("MIRROR_TURN", "MIRROR_WAKE", "MIRROR_FAST_WAKE", "MIRROR_REOPEN"):
        assert word not in src, word
    # no decision word, no migration: 059's list stands; 061 is still the newest file
    assert "turn_woke" not in inspect.getsource(rules.order_decision) and "woke" not in inspect.getsource(rules)
    mig = ROOT / "backend" / "migrations"
    assert "turn_woke" not in (mig / "059_mirror_orders_send_record.sql").read_text()
    assert sorted(p.name for p in mig.glob("*.sql"))[-1] == "061_fill_answers_cause_orders_fast.sql"
    # the render-ops presets are untouched by this lane: lane 0b's closed-while-he-traded already prints the turn
    yml = (ROOT / ".github" / "workflows" / "render-ops.yml").read_text()
    assert "turn_woke" not in yml and "b.last_plan->'turn'" in yml


def test_c16_every_name_is_emitted_here(monkeypatch):
    """The lane's one name, driven once (the worker file's coverage read
    imports this)."""
    test_c16_the_same_close_on_a_fast_tick_puts_the_market_back_for_the_next_fast_tick(monkeypatch)
    from tests.test_mirror_live_worker import SEEN
    assert NEW_NAME in SEEN


def test_c16_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    m = re.search(r"^## (\d+)\. .*\(2026-09-09, FILL lane 16\)", doc, re.M)
    assert m, "the FILL lane 16 section header"
    assert m.group(1) == "60"
    section = doc[m.start():]
    for k in (NEW_NAME, "turn.woke", "_turn_wake", "_fast_wake", "_fast_candidate", "_walk_candidate", "_WOKEN_MAX",
              "FAST_TICK_MIN_S", "reopen_refused", "book_exists", "cand_terminal_skipped", "side_band", "drift",
              "closed-while-he-traded", "lag_s", "test_fill_c16_turn_wakes.py", "-$116.09", "41", "56", "1,106"):
        assert k in section, k
    for word in ("flip's path alone", "nothing is admitted", "does not fix"):
        assert word in section.lower(), word
