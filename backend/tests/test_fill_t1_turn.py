"""FILL lane 5 (2026-09-08): the turn's record and the flat-clock guard --
a flip never loses the market unnamed; a flat book never ends on the
clock while he holds.

(a) A sign-flip close is a TURN: the closing plan records `turn = {from,
to, his_net, at}` (mirror_live._maybe_close_episode, before the state
write; the read path's _write_plan carries it onto the closed row). (b)
Every candidate refusal WITH a verdict on the market (the quote read
made) on a condition whose newest closed book is a turn is written on
that closed book's plan as `reopen_refused = {name, at, his_net, ask,
his_px, band}` beside migration 054's row (`_SQL_BOOK_REOPEN_REFUSED`:
the plan merged, `updated_at` untouched, the row still 'closed'), and
counted `reopen_refused`; no refusal is widened. (c) A book flat at
target 0 is not closed by MIRROR_FLAT_CLOSE_S (3,600 s) while his fills
show him holding a token on the book's own side
(rules.he_holds_on_axis -> rules.episode_close_reason(he_holds=...):
`he_holds`, or `he_holds_unread` when the sizes could not be read -- the
quiet skip hands None). The market's end, his confirmed vanish and the
sign flip close exactly as before: THE FLIP CLOSE IS NEVER GUARDED
(FILL_plan section 0 item 3, section 5). No rail, no knob, no decision
word, no migration.

The shapes (hard2/): book 467 (post_exits_1707 333: a SHORT flattened
12:05:40, then a LONG on one condition); Martinez 534 (hourly_1737 1172:
the SHORT closed on the flip at 12:10:17; task 71: the reopen refused
`drift`, then `side_band` with the market at 0.81 / 0.82 over his 0.61;
his 12:59:01 fill); book 347 (book_347_1750 398 / 544-557: closed
`standing row settled` at 03:44:29, the venue EXPIRED at 03:36:01, his
216,430.6 net still on the candidate rows, refused `no_mark` under the
terminal memo); the three closes of the day were flips (post_booksnew
385: book 309's plan close cashed_out under sign_flip), so the flat-clock
hold has no fixture in today's rows and is driven on his 8 shares; books
611 / 661 (post_fvv_1707 334 / 339: live flow books) as no-ops. Driven
on the real planner through the worker file's fakes; the E9 fast path
through test_e9_fast_path's helpers.
"""
import hashlib
import inspect
import json
import logging
import pathlib
import re

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_e12_flow_only import _block_and_add, _flow_book
from tests.test_e19_smaller_reading import FILLS_NET, VENUE_NET
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    CID, INTENT, M, N, NOW, SHORT, SLUG, _armed, _census, _fill, _his, _Http, _kinds, _mkt, _places, _pool,
    _rails_2026_09_06, _short_book, _shorts_on, _tick, _Venue,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("he_holds", "he_holds_unread", "reopen_refused")
CLOCK = float(rules.MIRROR_FLAT_CLOSE_S)
KEY = ("rn1", CID)
EXPIRED = "MARKET_STATE_EXPIRED"
# the per-market read naming neither token: the whole-book walk is the
# reading (drift_src 'book'; test_e19's fixture for the `drift` refusal)
WALK_ELSEWHERE = _Http(rows=[{"asset": "tok-elsewhere", "size": 900}])
# the functions the plan names as NOT touched, hashed on the tip this
# lane was built on (84317ae): a change to any of them is not this lane's
UNTOUCHED = {
    "admission": "a10630d6d3a3a62c", "select_flatten": "00e5b189ccb84bdb",
    "_flip_since": "3cdab3ea4d75e7c5", "_open_flow": "66c52fc21b5af319", "_fast_gate": "83032baf48677574",    # FILL lane 3 (landed first) split its order_open clause; lane 5 does not touch it
    "_venue_market_ended": "1a02ebcd800fe3c0", "_memo_terminal_book": "78bd691a2807a7a8",
    "_close_settled": "086987c757c5c3df",
}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _turned_close(p, net=FILLS_NET, closed_at=NOW - 26.0, **over):
    """Martinez 534's closed SHORT book as the flip close left it (the
    flip tick's plan: sign_flip, his net, and -- since this lane -- the
    turn), 26 s before the tick (12:10:17 -> 12:10:43)."""
    over.setdefault("last_plan", {"sign_flip": True, "net": net, "close": "cashed_out", "kind": "flatten_paired",
                                  "turn": {"from": SHORT, "to": INTENT, "his_net": net, "at": closed_at}})
    return _short_book(p, ledger=0, state="closed", closed_at=closed_at, updated_ts=closed_at,
                       last_reason="closed_cashed_out", standing_status="cashed_out", **over)


def _turn_reads(p):
    return [x for x in p.sent if "ml-book-turn" in x[1]]


def _reopen_writes(p):
    return [x for x in p.sent if "ml-book-reopen-refused" in x[1]]


# ------------------------------------------------------------------ the rules

def test_t1_the_flat_clock_alone_never_closes_a_book_he_holds_and_the_three_closes_win():
    flat = rules.BookState(ledger_net=0.0, gross_buy_usd=10.0)
    never = rules.BookState()
    bought = rules.book_buy(rules.BookState(), 20, 0.5).state
    ecr = rules.episode_close_reason
    assert CLOCK == 3600.0
    # due on the clock alone: he holds -> held; unread -> held; read as not held -> the close as before
    assert ecr(flat, False, False, CLOCK + 1, 0, he_holds=True) == "he_holds"
    assert ecr(flat, False, False, CLOCK + 1, 0, he_holds=None) == "he_holds_unread"
    assert ecr(flat, False, False, CLOCK + 1, 0) == "he_holds_unread", "the default is unread"
    for junk in (1, 0, "False", "", 0.0, [], "True", -1):
        assert ecr(flat, False, False, CLOCK + 1, 0, he_holds=junk) == "he_holds_unread", junk
    assert ecr(flat, False, False, CLOCK + 1, 0, he_holds=False) == "cashed_out"
    assert ecr(flat, False, False, CLOCK, 0, he_holds=False) == "cashed_out"
    assert ecr(never, False, False, CLOCK + 1, 0, he_holds=False) == "cancelled"
    # before the clock: not_due whatever he holds
    for hh in (True, False, None):
        assert ecr(flat, False, False, CLOCK - 1, 0, he_holds=hh) == "not_due", hh
        assert ecr(flat, False, False, None, 0, he_holds=hh) == "not_due", hh
        assert ecr(flat, None, None, CLOCK - 1, 0, he_holds=hh) == "not_due", hh
    # the market's end wins, with or without the clock, whatever he holds
    for hh in (True, None):
        assert ecr(flat, True, False, None, 0, he_holds=hh) == "cashed_out", hh
        assert ecr(flat, True, False, CLOCK + 1, 0, he_holds=hh) == "cashed_out", hh
        assert ecr(never, True, False, CLOCK + 1, 0, he_holds=hh) == "cancelled", hh
        # his confirmed vanish wins
        assert ecr(flat, False, True, None, 0, he_holds=hh) == "cashed_out", hh
        assert ecr(flat, False, True, CLOCK + 1, 0, he_holds=hh) == "cashed_out", hh
        # THE FLIP CLOSE IS NEVER GUARDED (section 0 item 3; section 5)
        assert ecr(flat, False, False, None, 0, sign_flipped=True, he_holds=hh) == "cashed_out", hh
        assert ecr(flat, False, False, CLOCK + 1, 0, sign_flipped=True, he_holds=hh) == "cashed_out", hh
        assert ecr(never, False, False, None, 0, sign_flipped=True, he_holds=hh) == "cancelled", hh
        assert ecr(bought, False, False, None, 0, sign_flipped=True, he_holds=hh) == "sign_flip", hh
        # held shares and open orders read as before
        assert ecr(bought, False, False, CLOCK + 1, 0, he_holds=hh) == "held", hh
        assert ecr(flat, False, False, CLOCK + 1, 1, he_holds=hh) == "orders_open", hh
    # the wait can only lengthen, as before: a lengthened wait is not_due before it, the guard after it
    assert ecr(flat, False, False, CLOCK + 1, 0, flat_close_s=2 * CLOCK, he_holds=False) == "not_due"
    assert ecr(flat, False, False, CLOCK + 1, 0, flat_close_s=0.0, he_holds=False) == "cashed_out"
    assert ecr(flat, False, False, CLOCK - 1, 0, flat_close_s=0.0, he_holds=True) == "not_due"
    assert ecr(flat, False, False, 2 * CLOCK, 0, flat_close_s=2 * CLOCK, he_holds=True) == "he_holds"
    # episode_close() inherits None: the clock-alone path closes nothing through it; the three closes do
    assert rules.episode_close(flat, False, False, CLOCK + 1, 0) is None
    assert rules.episode_close(flat, True, False, None, 0) == "cashed_out"
    assert rules.episode_close(flat, False, True, None, 0) == "cashed_out"
    assert inspect.signature(ecr).parameters["he_holds"].default is None
    assert "he_holds" not in inspect.signature(rules.episode_close).parameters
    # the docstring's table names both words
    doc = ecr.__doc__
    assert "'he_holds'" in doc and "'he_holds_unread'" in doc
    # the guard sits on the clock branch alone: the effective limit line stands
    src = inspect.getsource(ecr)
    assert "limit = max(limit, float(MIRROR_FLAT_CLOSE_S))" in src
    assert src.index("if due and he_holds is not False:") > src.index("flat >= limit")
    assert src.index("if due and he_holds is not False:") < src.index('return "not_due"')


def test_t1_he_holds_on_axis_sweeps():
    f = rules.he_holds_on_axis
    # long axis: his long size; short axis: his other size
    assert f(8.0, 0.0, False) is True and f(0.0, 0.0, False) is False and f(0.0, 500.0, False) is False
    assert f(0.0, 500.0, True) is True and f(8.0, 0.0, True) is False and f(0.0, 0.0, True) is False
    assert f(8, 0, False) is True and f(0, 8, True) is True and f(8, 8, False) is True and f(8, 8, True) is True
    assert f(0.5, 0.0, False) is True, "a fraction of a share is a holding"
    assert f(-0.0, 0.0, False) is False and f(0.0, -0.0, True) is False
    # `short` read as a bool
    assert f(0.0, 5.0, 1) is True and f(5.0, 0.0, 0) is True and f(5.0, 0.0, None) is True
    # a size that was not read -- on EITHER token -- is no reading: None
    for bad in (None, True, False, "8", "", float("nan"), float("inf"), -float("inf"), -1.0, -1, [8.0], {"size": 8}):
        assert f(bad, 0.0, False) is None, bad
        assert f(0.0, bad, False) is None, bad
        assert f(bad, 0.0, True) is None, bad
        assert f(0.0, bad, True) is None, bad
        assert f(bad, bad, False) is None, bad
    assert f(8.0, -1.0, False) is None, "the other token's size unreadable holds the long book too"


# --------------------------------------------------------- the turn's record

def test_t1_book_467s_shape_a_short_flipped_long_closes_under_a_turn_and_the_long_side_opens(monkeypatch):
    """post_exits_1707 333: a SHORT episode flattened, then a LONG one on
    the same condition. His net +300 against our short of 300: tick 1 the
    flip flattens by the priced cover (S4); tick 2 the venue reads 0 and
    the flip IS the close -- 30 s, never the hour, `he_holds` never
    consulted on it -- with `turn` on the closed row; tick 3 the long
    side opens as episode 2 and nothing is written on the closed row."""
    _shorts_on(monkeypatch)
    p = _pool()                                   # his net +300: the default fixture
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300}, ioc_fill=300.0)
    st = _tick(p, v)
    assert _census(st, "sign_flip") == 1 and b["last_plan"]["sign_flip"] is True and b["ledger_net"] == 0
    assert b["state"] == "live" and "turn" not in b["last_plan"], "the close waits for the venue's own 0"
    assert _census(st, "he_holds") == 0 and _census(st, "he_holds_unread") == 0
    st2 = _tick(p, _Venue(held={}), now=NOW + 30)
    lp = b["last_plan"]
    assert b["state"] == "closed" and lp["close"] == "cashed_out" and _census(st2, "closed_cashed_out") == 1
    assert lp["sign_flip"] is True and lp["turn"] == {"from": SHORT, "to": INTENT, "his_net": 300.0, "at": NOW + 30}
    assert lp["turn"]["his_net"] == lp["net"]
    assert _census(st2, "he_holds") == 0 and _census(st2, "he_holds_unread") == 0, "the flip is never guarded"
    # book 309's row shape (post_booksnew_1707 385): the close tick's plan write lands AFTER the
    # close, so last_reason reads `on target` while the plan's close reads cashed_out -- the
    # write that carries the turn onto the closed row (lane 0b's preset reads either)
    assert b["last_reason"] == "on target" and b["closed_at"] == NOW
    # the tick after: the long side opens as a new episode; the closed row carries no refusal
    v3 = _Venue(held={})
    st3 = _tick(p, v3, now=NOW + 60)
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == INTENT and books[1]["episode"] == 2 and books[1]["target"] == 300
    assert len(_places(v3)) == 1 and _places(v3)[0][6] == INTENT
    assert "reopen_refused" not in b["last_plan"] and _census(st3, "reopen_refused") == 0 and p.cand_refusals == []
    assert not _reopen_writes(p), "an open is no refusal"
    assert lp["turn"]["at"] == NOW + 30, "the turn stands as the close wrote it"


def test_t1_the_long_to_short_turn_and_the_e15_flip_witness_pin_re_run(monkeypatch):
    """The other direction (test_mirror_short_sign_flip's long book: his
    100 long against 400 other, our 300 long): the flatten rests at his
    equivalent's cent, fills, and the close under the flip records
    `turn = {from: BUY_LONG, to: BUY_SHORT, his_net: -300, at}`; the
    short episode opens next tick. Then E15's flip_witness pin, verbatim
    -- the witness rides beside the turn, untouched."""
    _shorts_on(monkeypatch)
    p = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    b = p.add_book(ledger=300)
    v = _Venue(bid=0.26, held={SLUG: 300})
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "sign_flip") == 1 and b["last_plan"]["close"] == "orders_open" and "turn" not in b["last_plan"]
    v2 = _Venue(held={}, fills={"oid-1": (300.0, 0.32)})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(100.0, 400.0))
    lp = b["last_plan"]
    assert b["state"] == "closed" and lp["close"] == "cashed_out" and _census(st2, "closed_cashed_out") == 1
    assert lp["turn"] == {"from": INTENT, "to": SHORT, "his_net": -300.0, "at": NOW + 30}
    assert _census(st2, "he_holds") == 0 and _census(st2, "he_holds_unread") == 0
    st4 = _tick(p, _Venue(held={}), now=NOW + 60, http=_mkt(100.0, 400.0))
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == SHORT and books[1]["episode"] == 2
    assert _census(st4, "short_open") == 1 and "reopen_refused" not in b["last_plan"]
    # E15's pin, re-run as written: the witnessed crossing sizes the reopen at once
    from tests import test_e15_witnessed_reduce as e15
    e15.test_e15_the_flip_reopen_with_a_witnessed_crossing_sizes_ratio_times_his_net_at_once(monkeypatch)


def test_t1_the_turn_is_written_by_the_close_alone_never_by_a_clock_or_vanish_close(monkeypatch):
    """A flat book closed on his confirmed vanish (no flip) and one closed
    by the market's end carry no `turn`: the word names the flip."""
    _rails_2026_09_06(monkeypatch)
    # the market's end, his 8 shares still on the book's side: closed cashed_out as before, no turn, no he_holds
    p = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000)], snap={M: 8.0, N: 0.0})
    b = p.add_book(ledger=0, ratio=0.10, gross_buy=3.1, avg_cost=0.31,
                   last_plan={"flat_since": NOW - (CLOCK + 1), "target": 0})
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st = _tick(p, _Venue(held={SLUG: 0}), http=_mkt(8.0, 0.0))
    assert b["state"] == "closed" and _census(st, "closed_cashed_out") == 1 and _census(st, "market_closed") == 1
    assert "turn" not in b["last_plan"] and _census(st, "he_holds") == 0 and _census(st, "he_holds_unread") == 0
    # his vanish confirmed on a flat book: cashed_out at once, no turn
    p2 = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000), _fill(M, "SELL", 8, 0.31, NOW - 2000)],
               snap={M: 0.0, N: 0.0})
    b2 = p2.add_book(ledger=0, ratio=0.10, gross_buy=3.1, avg_cost=0.31, last_plan={"target": 0})
    st2 = _tick(p2, _Venue(held={SLUG: 0}), http=_mkt(0.0, 0.0))
    assert b2["state"] == "closed" and _census(st2, "closed_cashed_out") == 1
    assert "turn" not in b2["last_plan"] and _census(st2, "he_holds") == 0 and _census(st2, "he_holds_unread") == 0


def test_t1_the_turn_carries_the_close_ticks_net_never_the_rows_last_written_one(monkeypatch):
    """Review (the surviving mutant M32): `turn.his_net` is the closing
    plan's `net` -- the reading the flip was judged on THIS tick -- never
    the row's `his_net` column, which is the prior tick's write. His net
    moves +300 -> +500 between the cover tick and the close tick: the
    turn reads 500.0 (== plan.net) while the row still reads 300 at the
    close (its own plan write lands after it)."""
    _shorts_on(monkeypatch)
    p = _pool()                                   # his +300: the default fixture
    b = _short_book(p, ledger=-300)
    _tick(p, _Venue(held={SLUG: -300}, ioc_fill=300.0))
    assert b["last_plan"]["sign_flip"] is True and b["ledger_net"] == 0 and b["last_plan"]["net"] == 300.0
    p.fills, p.snap = _his(500), {M: 500.0, N: 0.0}
    st2 = _tick(p, _Venue(held={}), now=NOW + 30, http=_mkt(500.0, 0.0))
    lp = b["last_plan"]
    assert b["state"] == "closed" and lp["close"] == "cashed_out" and _census(st2, "closed_cashed_out") == 1
    assert lp["net"] == 500.0 and lp["turn"]["his_net"] == 500.0 and lp["turn"]["from"] == SHORT, lp["turn"]


# ------------------------------------------------------- the reopen refused

def test_t1_martinez_534s_shape_the_reopen_refused_drift_then_side_band_on_the_closed_row_then_his_fill_wakes_it(monkeypatch):
    """hourly_1737 1172 / task 71. The SHORT closed on the flip at
    12:10:17; at 12:10:43 the candidate was refused `drift` (his fills
    11,974.6 against the venue's 25,104; here the whole-book walk fresh
    and the per-market read naming neither token, so drift_src is 'book'
    and E19's smaller reading never sizes); later `side_band` (0.82 over
    his 0.61 > 0.15). Each refusal lands on the closed row's plan as
    `reopen_refused` (the newest overwrites), the 054 rows keep both,
    `reopen_refused` counts once per tick, ONE turn read per tick, and
    the closed row's updated_ts never moves. His 12:59:01 fill wakes the
    condition: the fast tick routes it to _fast_candidate -- the same
    refusal, the same record -- never `fast_tick_skipped: order_open`."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10)], snap={M: VENUE_NET, N: 0.0})
    b = _turned_close(p)
    st = _tick(p, _Venue(bid=0.60, ask=0.61), http=WALK_ELSEWHERE)
    assert len(p.books) == 1 and _census(st, "drift") >= 1 and _census(st, "drift_smaller_open") == 0
    assert [r["refusal"] for r in p.cand_refusals] == ["drift"]
    row = p.cand_refusals[0]
    rr = b["last_plan"]["reopen_refused"]
    assert rr == {"name": "drift", "at": NOW, "his_net": row["his_net"], "ask": 0.61, "his_px": 0.61, "band": 0.15}
    assert rr["his_net"] is not None and rr["band"] == float(rules.LIVE_SIDE_PRICE_BAND_MAX)
    assert _census(st, "reopen_refused") == 1 and len(_turn_reads(p)) == 1 and len(_reopen_writes(p)) == 1
    assert b["updated_ts"] == NOW - 26.0 and b["state"] == "closed" and b["last_plan"]["turn"]["from"] == SHORT
    assert b["last_plan"]["sign_flip"] is True and b["last_plan"]["close"] == "cashed_out", "merged, never replaced"
    # later: the per-market read agrees with his fills (no drift), the market at 0.81 / 0.82: side_band
    p.snap = None
    st2 = _tick(p, _Venue(bid=0.81, ask=0.82), now=NOW + 30, http=_mkt(FILLS_NET, 0.0))
    assert len(p.books) == 1 and _census(st2, "side_band") >= 1 and _census(st2, "drift") == 0
    assert [r["refusal"] for r in p.cand_refusals] == ["drift", "side_band"]
    rr2 = b["last_plan"]["reopen_refused"]
    assert rr2 == {"name": "side_band", "at": NOW + 30, "his_net": p.cand_refusals[1]["his_net"], "ask": 0.82,
                   "his_px": 0.61, "band": 0.15}
    assert _census(st2, "reopen_refused") == 1 and len(_turn_reads(p)) == 2 and len(_reopen_writes(p)) == 2
    assert b["updated_ts"] == NOW - 26.0, "the fills-missed census picks the newest-UPDATED book: never bumped"
    # his 12:59:01 fill wakes the condition (E9): no book, so _fast_candidate walks it -- never order_open
    _walk()
    fs = _fast(p, _Venue(bid=0.81, ask=0.82), now=NOW + 60, http=_mkt(FILLS_NET, 0.0))
    assert _skips(fs).get(CID) != "order_open" and len(p.books) == 1
    assert _census(fs, "side_band") >= 1 and _census(fs, "reopen_refused") == 1
    assert b["last_plan"]["reopen_refused"]["at"] == NOW + 60 and b["last_plan"]["reopen_refused"]["name"] == "side_band"
    assert b["updated_ts"] == NOW - 26.0 and len(_turn_reads(p)) == 3
    assert [r["refusal"] for r in p.cand_refusals] == ["drift", "side_band"], "the same name inside the restamp window: no new row"


def test_t1_book_347s_shape_a_settle_closed_book_on_an_expired_market_writes_no_reopen_entry_and_consults_no_he_holds(monkeypatch):
    """book_347_1750 398 / 544-557 / 566: the SHORT closed `standing row
    settled` at 03:44:29 on a market the venue read EXPIRED at 03:36:01,
    his 216,430.6 net still on every candidate row, each refused
    `no_mark` under the terminal memo (900 s). The settle closed it, not
    the clock (he_holds never consulted); no turn on its plan, so the
    refusal writes nothing on it; the next tick skips under the memo."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 216_430.6, 0.50, NOW - 3000)], snap={M: 0.0, N: 216_430.6})
    b = _short_book(p, ledger=-862, avg=0.4957, state="closed", closed_at=NOW - 3 * 3600, updated_ts=NOW - 3 * 3600,
                    last_reason="closed: standing row settled", standing_status="settled",
                    last_plan={"kind": "no_plan", "at": NOW - 4000, "venue_terminal": EXPIRED})
    st = _tick(p, _Venue(bid=None, ask=None, state=EXPIRED), http=_mkt(0.0, 216_430.6))
    assert len(p.books) == 1 and b["state"] == "closed" and b["last_reason"] == "closed: standing row settled"
    assert [r["refusal"] for r in p.cand_refusals] == ["no_mark"] and p.cand_refusals[0]["his_net"] is not None
    assert "reopen_refused" not in b["last_plan"] and "turn" not in b["last_plan"]
    assert _census(st, "reopen_refused") == 0 and _census(st, "he_holds") == 0 and _census(st, "he_holds_unread") == 0
    assert len(_turn_reads(p)) == 1 and not _reopen_writes(p), "the row was read once and did not turn"
    assert ml._terminal_until.get(KEY) == NOW + ms.UNMAPPED_TTL_S and ms.UNMAPPED_TTL_S == 900.0
    assert b["updated_ts"] == NOW - 3 * 3600
    st2 = _tick(p, _Venue(bid=None, ask=None, state=EXPIRED), now=NOW + 30, http=_mkt(0.0, 216_430.6))
    assert _census(st2, "cand_terminal_skipped") == 1 and len(_turn_reads(p)) == 1
    assert "reopen_refused" not in b["last_plan"] and len(p.cand_refusals) == 1


def test_t1_the_reopen_record_fails_closed_the_write_failing_the_read_failing_a_refusal_before_the_read(monkeypatch, caplog):
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    # the plan UPDATE fails: counted `reopen_refused_write_failed`, logged once, the 054 row stands, no entry
    p = _pool(fills=[_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10)], snap={M: VENUE_NET, N: 0.0})
    b = _turned_close(p)
    p.raise_on.append(("ml-book-reopen-refused", RuntimeError("db")))
    with caplog.at_level(logging.WARNING):
        st = _tick(p, _Venue(bid=0.60, ask=0.61), http=WALK_ELSEWHERE)
        st2 = _tick(p, _Venue(bid=0.60, ask=0.61), now=NOW + 30, http=WALK_ELSEWHERE)
    assert [r["refusal"] for r in p.cand_refusals] == ["drift"] and "reopen_refused" not in b["last_plan"]
    assert st["census"].get("reopen_refused_write_failed") == 1 and st2["census"].get("reopen_refused_write_failed") == 1
    assert _census(st, "reopen_refused") == 0 and _census(st2, "reopen_refused") == 0
    assert sum("reopen_refused write on book" in r.getMessage() for r in caplog.records) == 1, "logged once per process"
    assert b["updated_ts"] == NOW - 26.0 and len(p.books) == 1
    # the turn read fails: no entry, nothing counted, the 054 row as today; _flip_since's own read untouched
    # (the refusal rows' memo is process-wide: cleared between the worlds so each writes its row)
    ml._cand_refusal_last.clear()
    p2 = _pool(fills=[_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10)], snap={M: VENUE_NET, N: 0.0})
    b2 = _turned_close(p2)
    p2.raise_on.append(("ml-book-turn", RuntimeError("db")))
    st3 = _tick(p2, _Venue(bid=0.60, ask=0.61), http=WALK_ELSEWHERE)
    assert [r["refusal"] for r in p2.cand_refusals] == ["drift"] and "reopen_refused" not in b2["last_plan"]
    assert _census(st3, "reopen_refused") == 0 and st3["census"].get("reopen_refused_write_failed") is None
    assert not _reopen_writes(p2) and len(p2.books) == 1
    # a closed book WITHOUT a turn (a clock close): the refusal writes nothing on it
    ml._cand_refusal_last.clear()
    p3 = _pool(fills=[_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10)], snap={M: VENUE_NET, N: 0.0})
    b3 = _turned_close(p3, last_plan={"close": "cashed_out", "net": 0.0, "kind": "flatten_paired"})
    st4 = _tick(p3, _Venue(bid=0.60, ask=0.61), http=WALK_ELSEWHERE)
    assert [r["refusal"] for r in p3.cand_refusals] == ["drift"] and "reopen_refused" not in b3["last_plan"]
    assert _census(st4, "reopen_refused") == 0 and len(_turn_reads(p3)) == 1 and not _reopen_writes(p3)
    # a refusal BEFORE the market read (unmapped) on a turned market: no read, no entry
    ml._cand_refusal_last.clear()
    p4 = _pool(fills=[_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10)], snap={M: VENUE_NET, N: 0.0}, mapped=False)
    b4 = _turned_close(p4)
    st5 = _tick(p4, _Venue(bid=0.60, ask=0.61), http=WALK_ELSEWHERE)
    assert _census(st5, "unmapped") >= 1 and [r["refusal"] for r in p4.cand_refusals] == ["unmapped"]
    assert not _turn_reads(p4) and not _reopen_writes(p4) and "reopen_refused" not in b4["last_plan"]
    assert _census(st5, "reopen_refused") == 0
    # a live book on the market (the reopen happened): the candidate is not walked, nothing written
    ml._cand_refusal_last.clear()
    p5 = _pool(fills=[_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10)], snap={M: FILLS_NET, N: 0.0})
    b5 = _turned_close(p5)
    live = p5.add_book(ledger=100, ratio=0.10, avg_cost=0.61, episode=2)
    st6 = _tick(p5, _Venue(bid=0.60, ask=0.61, held={SLUG: 100}), http=_mkt(FILLS_NET, 0.0))
    assert not _turn_reads(p5) and not _reopen_writes(p5) and "reopen_refused" not in b5["last_plan"]
    assert live["state"] == "live" and _census(st6, "reopen_refused") == 0 and p5.cand_refusals == []


def test_t1_the_reopen_record_admits_nothing_and_widens_no_refusal():
    """The record is made AFTER the refusal is named: rules.admission and
    the candidate's refusal sites are the tip's (hashed); the statement
    merges the plan on a 'closed' row only and names no updated_at."""
    assert _sha(rules.admission) == UNTOUCHED["admission"]
    assert _sha(rules.select_flatten) == UNTOUCHED["select_flatten"]
    for name in ("_flip_since", "_open_flow", "_fast_gate", "_venue_market_ended", "_memo_terminal_book",
                 "_close_settled"):
        assert _sha(getattr(ml, name)) == UNTOUCHED[name], name
    assert float(rules.MIRROR_DRIFT_MAX) == 0.05 and float(rules.MIRROR_FLAT_CLOSE_S) == 3600.0
    src = inspect.getsource(ml._walk_candidate)
    assert src.index("_note_candidate_refusal(t, whale, cid, name, d)") < src.index("_note_reopen_refused(t, whale, cid, name, d)")
    wsrc = inspect.getsource(ml._note_reopen_refused)
    assert 'if t.abandoned or "mark" not in d:' in wsrc and 'if "reopen_of" not in d:' in wsrc
    assert "CAND_REFUSAL_WRITE_TIMEOUT_S" in wsrc and "CAND_REFUSAL_WRITE_TIMEOUT_S" in inspect.getsource(ml._reopen_of)
    assert ml.CAND_REFUSAL_WRITE_TIMEOUT_S == 5.0
    sql = ml._SQL_BOOK_REOPEN_REFUSED
    assert "updated_at" not in sql and "state = 'closed'" in sql
    assert "SET last_plan = COALESCE(last_plan, '{}'::jsonb) || $2::jsonb" in sql and "ml-book-reopen-refused" in sql
    # the same row _SQL_BOOK_FLIP reads (E15's witness): the WHERE and ORDER BY clauses byte for byte
    def _tail(s):
        return " ".join(s[s.index("WHERE"):s.index("/*")].split())
    assert _tail(ml._SQL_BOOK_TURN) == _tail(ml._SQL_BOOK_FLIP)
    assert ml._SQL_BOOK_TURN.lstrip().startswith("SELECT id, last_plan FROM mirror_books")
    from pglast import parse_sql
    parse_sql(ml._SQL_BOOK_REOPEN_REFUSED)
    parse_sql(ml._SQL_BOOK_TURN)
    # no decision word: the lane places nothing; order_decision's words are E14's
    assert "reopen_refused" not in inspect.getsource(rules.order_decision)
    mig = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "he_holds" not in mig and "reopen_refused" not in mig


# ---------------------------------------------------------- the flat-clock hold

def test_t1_the_flat_clock_hold_a_long_book_flat_at_target_0_on_his_8_shares_is_held_he_holds_and_the_quiet_skip_reads_unread(monkeypatch):
    """A long book at ratio 0.1 on his 8 shares (target 0: under a share),
    flat for 3,601 s: the read tick's verdict is `he_holds`, the book
    live, nothing placed, `flat_since` carried; E6's quiet skips between
    reads hand None and read `he_holds_unread` -- held, never closed --
    and the due tick reads `he_holds` again. Never `closed_cashed_out`."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000)], snap={M: 8.0, N: 0.0})
    b = p.add_book(ledger=0, ratio=0.10, gross_buy=3.1, avg_cost=0.31,
                   last_plan={"flat_since": NOW - (CLOCK + 1), "target": 0})
    q = int(ml.QUIET_EVERY_TICKS)
    assert q >= 2
    verdicts, reasons, closes = [], [], 0
    for i in range(q + 1):
        now = NOW + 30.0 * i
        p.snap_at = now - 40
        v = _Venue(held={SLUG: 0})
        st = _tick(p, v, now=now, http=_mkt(8.0, 0.0))
        lp = b["last_plan"]
        assert b["state"] == "live" and b["target"] == 0 and not _places(v), (i, lp)
        assert lp["flat_since"] == NOW - (CLOCK + 1), (i, lp)
        verdicts.append(lp["close"])
        reasons.append(b["last_reason"])
        closes += _census(st, "closed_cashed_out") + _census(st, "closed_cancelled")
        assert _census(st, "he_holds") + _census(st, "he_holds_unread") == 1, (i, st["census"])
        assert _census(st, "he_holds") == (1 if lp["close"] == "he_holds" else 0), (i, lp)
    assert closes == 0 and st["books_live"] == 1 and len(p.books) == 1
    # the read, the quiet skips, the read again on the due tick
    assert verdicts[0] == "he_holds" and verdicts[q] == "he_holds", verdicts
    assert all(x == "he_holds_unread" for x in verdicts[1:q]), verdicts
    assert all(r == "book_quiet_skipped" for r in reasons[1:q]) and "book_quiet_skipped" not in (reasons[0], reasons[q]), reasons
    assert "turn" not in b["last_plan"]


def test_t1_the_flat_clock_hold_on_a_short_book_reads_his_other_token(monkeypatch):
    """The short mirror image: a short book flat at target 0 on his 8
    shares of the OTHER token is held `he_holds`; on his 8 shares of the
    long token (nothing on the book's side) the clock closes as before."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 8, 0.69, NOW - 3000)], snap={M: 0.0, N: 8.0})
    b = _short_book(p, ledger=0, avg=0.31, ratio=0.10, last_plan={"flat_since": NOW - (CLOCK + 1), "target": 0})
    st = _tick(p, _Venue(held={SLUG: 0}), http=_mkt(0.0, 8.0))
    assert b["state"] == "live" and b["last_plan"]["close"] == "he_holds" and _census(st, "he_holds") == 1
    assert _census(st, "closed_cashed_out") == 0 and _census(st, "closed_cancelled") == 0
    p2 = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000)], snap={M: 8.0, N: 0.0})
    b2 = _short_book(p2, ledger=0, avg=0.31, ratio=0.10, last_plan={"flat_since": NOW - (CLOCK + 1), "target": 0})
    st2 = _tick(p2, _Venue(held={SLUG: 0}), http=_mkt(8.0, 0.0))
    assert b2["state"] == "closed" and _census(st2, "he_holds") == 0 and _census(st2, "he_holds_unread") == 0
    assert b2["last_plan"]["close"] in ("cashed_out", "cancelled") and "turn" not in b2["last_plan"]


def test_t1_his_sizes_at_zero_with_the_vanish_unconfirmed_close_on_the_clock_as_today(monkeypatch):
    """The same long book, his fills at 0 / 0 and the vanish NOT confirmed
    (the data API still lists him): `not_due` at 3,599 s on the read
    tick; past the clock the quiet skips (E6's rotation: no reading)
    hold it `he_holds_unread`, and the next READ tick reads his 0 / 0 and
    closes `cashed_out` -- today's clock close, on the read tick (the
    one cost of the guard: a quiet flat book's clock close waits for its
    rotation read, at most QUIET_EVERY_TICKS ticks), beside test_e12's
    `flow_wait` pin (which runs unchanged in its own file). A book with
    a fill of his inside HOT_S is read every tick and closes at 3,600 s
    exactly."""
    _rails_2026_09_06(monkeypatch)

    async def _not_gone(t, whale, asset):
        return False
    monkeypatch.setattr(ml, "_confirm_gone", _not_gone)
    p = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000), _fill(M, "SELL", 8, 0.31, NOW - 2000)],
              snap={M: 0.0, N: 0.0})
    b = p.add_book(ledger=0, ratio=0.10, gross_buy=3.1, avg_cost=0.31,
                   last_plan={"flat_since": NOW - (CLOCK - 1), "target": 0})
    st = _tick(p, _Venue(held={SLUG: 0}), http=_mkt(0.0, 0.0))
    assert b["state"] == "live" and b["last_plan"]["close"] == "not_due"
    assert _census(st, "he_holds") == 0 and _census(st, "he_holds_unread") == 0
    q = int(ml.QUIET_EVERY_TICKS)
    for i in range(1, q):
        st2 = _tick(p, _Venue(held={SLUG: 0}), now=NOW + 30.0 * i, http=_mkt(0.0, 0.0))
        assert b["state"] == "live" and b["last_reason"] == "book_quiet_skipped", i
        assert b["last_plan"]["close"] == "he_holds_unread" and _census(st2, "he_holds_unread") == 1, i
        assert _census(st2, "closed_cashed_out") == 0, i
    st3 = _tick(p, _Venue(held={SLUG: 0}), now=NOW + 30.0 * q, http=_mkt(0.0, 0.0))
    assert b["state"] == "closed" and b["last_plan"]["close"] == "cashed_out" and _census(st3, "closed_cashed_out") == 1
    assert _census(st3, "he_holds") == 0 and _census(st3, "he_holds_unread") == 0 and "turn" not in b["last_plan"]
    # hot (a fill of his inside HOT_S): read every tick, closed at 3,600 s exactly
    p2 = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000), _fill(M, "SELL", 8, 0.31, NOW - 100)],
               snap={M: 0.0, N: 0.0})
    b2 = p2.add_book(ledger=0, ratio=0.10, gross_buy=3.1, avg_cost=0.31,
                     last_plan={"flat_since": NOW - (CLOCK - 1), "target": 0})
    _tick(p2, _Venue(held={SLUG: 0}), http=_mkt(0.0, 0.0))
    assert b2["state"] == "live" and b2["last_plan"]["close"] == "not_due"
    st4 = _tick(p2, _Venue(held={SLUG: 0}), now=NOW + 1, http=_mkt(0.0, 0.0))
    assert b2["state"] == "closed" and b2["last_plan"]["close"] == "cashed_out" and _census(st4, "closed_cashed_out") == 1
    assert _census(st4, "he_holds") == 0 and _census(st4, "he_holds_unread") == 0


def test_t1_a_closing_book_whose_market_row_cannot_be_read_is_held_unread_on_the_clock_not_closed(monkeypatch):
    """The one path where the guard reads None outside the quiet skip: a
    book already 'closing' (the market read closed on an earlier tick)
    whose markets row cannot be read THIS tick hands the close
    market_live None -- the flat clock alone -- and is held
    `he_holds_unread` (before this lane: closed on the clock with no
    reading at all). The row read again closed: closed as before."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 8, 0.31, NOW - 3000)], snap={M: 8.0, N: 0.0})
    b = p.add_book(ledger=0, ratio=0.10, gross_buy=3.1, avg_cost=0.31, state="closing",
                   last_reason="market_closed", last_plan={"flat_since": NOW - (CLOCK + 1), "target": 0})
    p.markets = {}
    st = _tick(p, _Venue(held={SLUG: 0}), http=_mkt(8.0, 0.0))
    assert b["state"] == "closing" and b["last_reason"] == "closing: he_holds_unread"
    assert b["last_plan"]["kind"] == "closing" and b["last_plan"]["market_live"] is None
    assert _census(st, "he_holds_unread") == 1 and _census(st, "he_holds") == 0
    assert _census(st, "closed_cashed_out") == 0 and _census(st, "closed_cancelled") == 0
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st2 = _tick(p, _Venue(held={SLUG: 0}), now=NOW + 30, http=_mkt(8.0, 0.0))
    assert b["state"] == "closed" and _census(st2, "closed_cashed_out") == 1 and _census(st2, "he_holds_unread") == 0


# ------------------------------------------------------------------ no-ops

def test_t1_live_flow_books_611_and_661_are_never_touched(monkeypatch):
    """post_fvv_1707 334 / 339 and hourly_1737 865: live flow books. 611's
    shape -- the block-and-add world, 153 held under a 3,918-shaped
    target -- and 661's -- a fresh per-market read past MIRROR_DRIFT_MAX
    (0.228831, 'market') on a held book. Neither is flat at target 0, so
    the guard never reads: no `he_holds`, no `he_holds_unread`, no
    `turn`, no `reopen_refused`; the plan's `close` reads `orders_open`
    (an increase rests) or `held` -- never a word of this lane's."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_block_and_add(), snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p, ledger=153)
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 153})
    st = _tick(p, v, http=_mkt(11_000.0))
    lp = b["last_plan"]
    assert b["state"] == "live" and lp["close"] == "orders_open" and len(_places(v)) == 1
    assert "turn" not in lp and "reopen_refused" not in lp
    assert _census(st, "he_holds") == 0 and _census(st, "he_holds_unread") == 0 and _census(st, "reopen_refused") == 0
    assert not _turn_reads(p) and not _reopen_writes(p)
    p2 = _pool(fills=_block_and_add(), snap={M: 11_000.0, N: 0.0})
    b2 = _flow_book(p2, ledger=1129, flow_base=0.0, flow_last_net=10_222.9)
    st2 = _tick(p2, _Venue(bid=0.71, ask=0.73, held={SLUG: 1129}), http=_mkt(13_517.0))     # 0.2288 over the fills
    lp2 = b2["last_plan"]
    assert b2["state"] == "live" and lp2["close"] in ("held", "orders_open") and b2["ledger_net"] == 1129
    assert "turn" not in lp2 and "reopen_refused" not in lp2
    assert _census(st2, "he_holds") == 0 and _census(st2, "he_holds_unread") == 0 and _census(st2, "reopen_refused") == 0
    assert not _turn_reads(p2) and not _reopen_writes(p2)


# ----------------------------------------------------- the names, the docs

def test_t1_the_census_place_the_emit_sites_the_call_sites_and_no_knob():
    keys = ml.CENSUS_KEYS
    # E22 (FILL lane 22) placed its four lost_fill_* names after these three, nearer the key (-16:-13 -> -20:-17)
    assert keys[-20:-17] == NEW_NAMES
    # FILL lane 3 (three names) and T2 (two) landed ahead of this lane and sit between E14's name and these three (take_in_band -17 -> -22 -> -26)
    assert keys[-26] == "take_in_band" and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert all(ml._new_stats()["census"][k] == 0 for k in NEW_NAMES)
    assert all(k not in ml._INTEG_CENSUS_KEYS for k in NEW_NAMES)
    # the emit sites: the hold in _maybe_close_episode, the record in _note_reopen_refused
    msrc = inspect.getsource(ml._maybe_close_episode)
    assert 'if why in ("he_holds", "he_holds_unread"):' in msrc and '_mirror_stop(why, book["whale"])' in msrc
    assert "he_holds=he_holds" in msrc
    assert 'plan["turn"] = {"from": book.get("intent"),' in msrc
    assert msrc.index('if plan.get("sign_flip") is True:\n        # FILL lane 5') < msrc.index("await t.pool.execute(_SQL_BOOK_STATE")
    assert msrc.index("verdict = await le._close_mirror_episode") < msrc.index('plan["turn"]'), "the turn rides a close that happened"
    nsrc = inspect.getsource(ml._note_reopen_refused)
    assert '_mirror_stop("reopen_refused", whale)' in nsrc and '_mirror_stop("reopen_refused_write_failed")' in nsrc
    assert nsrc.index("_SQL_BOOK_REOPEN_REFUSED") < nsrc.index('_mirror_stop("reopen_refused", whale)'), "counted on the write"
    # the call sites: the read path hands the axis reading; the quiet skip's call hands nothing (None)
    tb = inspect.getsource(ml._tick_book)
    assert tb.count("he_holds=rules.he_holds_on_axis(r.his_long, r.his_other, short)") == 1
    skip = tb[tb.index('plan["close"] = await _maybe_close_episode(t, book, market_live, False,'):]
    skip = skip[:skip.index("flow_wait=") + 200]
    assert "he_holds" not in skip
    closing = tb[tb.index("why = await _maybe_close_episode(t, book, False if venue_ended"):]
    assert "he_holds" not in closing[:400]
    assert inspect.getsource(ml).count("_mirror_stop(why, book[\"whale\"])") == 1
    # no rail, no knob: nothing of the lane's own read from the environment, the flat clock as before
    src = inspect.getsource(ml) + inspect.getsource(rules)
    for word in ("MIRROR_HE_HOLDS", "MIRROR_REOPEN", "MIRROR_TURN", "MIRROR_FLAT_CLOSE_S\", 3600.0, floor"):
        assert word not in src, word
    assert 'MIRROR_FLAT_CLOSE_S = capped_env("MIRROR_FLAT_CLOSE_S", 3600.0)' in inspect.getsource(rules)
    assert "he_holds" not in inspect.getsource(ml._fast_gate)


def test_t1_every_name_is_emitted_here(monkeypatch, caplog):
    """The lane's three names, each driven once (the worker file's coverage
    read imports this)."""
    test_t1_the_flat_clock_hold_a_long_book_flat_at_target_0_on_his_8_shares_is_held_he_holds_and_the_quiet_skip_reads_unread(monkeypatch)
    test_t1_martinez_534s_shape_the_reopen_refused_drift_then_side_band_on_the_closed_row_then_his_fill_wakes_it(monkeypatch)
    from tests.test_mirror_live_worker import SEEN
    assert set(NEW_NAMES) <= SEEN


def test_t1_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    m = re.search(r"^## (\d+)\. .*\(2026-09-08, FILL lane 5\)", doc, re.M)
    assert m, "the FILL lane 5 section header"
    section = doc[m.start():]
    for k in NEW_NAMES + ("turn", "reopen_refused_write_failed", "he_holds_on_axis", "episode_close_reason",
                          "_SQL_BOOK_REOPEN_REFUSED", "updated_at", "MIRROR_FLAT_CLOSE_S", "test_fill_t1_turn.py",
                          "sign_flip", "drift", "side_band", "054", "059", "closed-while-he-traded"):
        assert k in section, k
    for word in ("never guarded", "flip close"):
        assert word in section.lower(), word
