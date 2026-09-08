"""E6 (2026-09-07): the tick's time, measured and budgeted.

Owner 18:3xZ / 19:1xZ: "Need everything running and running at mirror
to him." The tick was 80-127 s with 74-77 live books (19:10Z 79.8 s,
19:21Z 98.7 s, 19:28Z 127.2 s; venue_calls 89-112) and his median entry
latency measured 86.8 s: a tick that long IS the latency. Three parts,
all in mirror_live.py, driven end to end through tick_once against the
worker file's fakes (its autouse rails are imported):

  part 1  the timing block (`short.timing`: seconds by step, the books
          by outcome class, the budget's numbers) and the mode line's
          `t=walk/orders/books/cands`;
  part 2  the venue-call budget (VENUE_CALLS_PER_TICK, env may only
          lower it): the always-read classes, the quiet rotation
          (QUIET_EVERY_TICKS, the deferred queue), step M and the flat
          close on a skipped book, the skip's plan, the candidate
          stage's share (map reads and quote reads together, the old
          map cap the floor, the quote reads never below
          CAND_MIN_PER_TICK);
  part 3  the terminal memos persisted under one bounded key, written
          once per 60 s on change, read once at boot, malformed = empty.

THE RULE, EXACTLY (pinned below; the review fold's rule, hard2/
E6_fold_notes.md): a book is HOT -- read every tick, never budgeted --
when this process has never read it (or the row was written by another
hand since), its last read was not on target with nothing placed and
nothing open, it is not live (frozen: E5's exit path), an order is open
or non-terminal on it, its last plan was `exit_take` /
`take_at_his_level` / `reduce_unfilled`, its market woke this tick, or
a fill of his on its market sits inside HOT_S (600 s) of the tick.
Every other book is QUIET: read on the QUIET_EVERY_TICKS-th (3rd) tick
after its last read (`read_on`), skipped `book_quiet_skipped` before
then; a quiet book DUE for its read that the budget cannot fit joins a
FIFO queue (`_quiet_deferred`) whose head the next ticks read FIRST,
max(quiet_budget, DEFERRED_MIN_PER_TICK) a tick, the overflow staying
queued -- a queued book's worst wait past its due tick is
ceil(N_quiet / max(quiet_budget, DEFERRED_MIN_PER_TICK)) ticks. Step M,
the standing-row close and the episode close run on a skipped book; no
order path is touched; the skip writes its name and its plan only.
"""
import inspect
import json
import logging

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, M, N, NOW, SLUG, _Http, _Pool, _Venue, _armed, _cancels, _census, _fill, _his,
    _kinds, _many_books, _places, _pool, _tick,
)

EXPIRED = "MARKET_STATE_EXPIRED"
TIMING_KEYS = ("walk", "orders", "books", "books_venue", "books_data", "candidates",
               "venue_calls", "snap_market_reads",
               "read", "on_target", "placed", "no_mark", "terminal_skipped", "quiet_skipped",
               "budget", "quiet_budget", "quiet_reads", "cand_budget", "map_cap", "cand_cap")


def _bbos(v):
    return [c[1] for c in v.calls if c[0] == "bbo"]


def _timing(st):
    return st["short"]["timing"]


def _quiet_book(p=None, ledger=300, **kw):
    """The fixture book ON TARGET with nothing open: his 300 @ 0.31 three
    thousand seconds ago (outside HOT_S), our ledger 300 held on the
    venue (_v). Tick 1 reads it on target; the rotation applies from
    tick 2."""
    p = p or _pool()
    return p, p.add_book(ledger=ledger, **kw)


def _v(held=300, **kw):
    """The venue holding the fixture book's ledger (venue == ledger:
    never a freeze); `held` 0 for a flat book."""
    return _Venue(held={SLUG: held}, **kw)


def _busy(seconds):
    """A real wait: the rails patch time.sleep to a recorder."""
    import time as _time
    end = _time.monotonic() + seconds
    while _time.monotonic() < end:
        pass


# ---------------------------------------------------------------- constants

def test_e6_the_constants_and_the_env_can_only_lower_the_budget(monkeypatch):
    assert ml.TICK_TARGET_S == 25.0 and ml.HOT_S == 600.0 and ml.QUIET_EVERY_TICKS == 9   # E11: 3 -> 9
    assert ml.CAND_MIN_PER_TICK == 10 and ml.VENUE_CALLS_PER_TICK == 60
    assert ml.QUIET_EXIT_PLANS == frozenset({"exit_take", "take_at_his_level", "reduce_unfilled"})
    # capped_env: within [floor 20, default 60]; unreadable is the default
    for env, want in (("30", 30.0), ("100", 60.0), ("5", 20.0), ("0", 20.0), ("x", 60.0), ("", 60.0)):
        monkeypatch.setenv("MIRROR_TICK_VENUE_CALLS", env)
        assert rules.capped_env("MIRROR_TICK_VENUE_CALLS", 60.0, floor=20.0) == want, env
    monkeypatch.delenv("MIRROR_TICK_VENUE_CALLS", raising=False)
    assert rules.capped_env("MIRROR_TICK_VENUE_CALLS", 60.0, floor=20.0) == 60.0
    src = inspect.getsource(ml)
    assert 'capped_env("MIRROR_TICK_VENUE_CALLS", 60.0, floor=20.0)' in src
    # E2's soft guard keeps its own name and its own number
    assert rules.MIRROR_VENUE_CALLS_PER_TICK == 80 and "MIRROR_VENUE_CALLS_PER_TICK" in inspect.getsource(rules)


def test_e6_book_quiet_skipped_is_a_census_key_before_the_pinned_last_key():
    keys = ml.CENSUS_KEYS
    assert keys[-2] == "book_quiet_skipped" and keys[-1] == "cand_terminal_skipped"
    assert keys.count("book_quiet_skipped") == 1 and keys.index("book_quiet_skipped") >= api_app._DETAIL_MAX_KEYS
    assert ml._new_stats()["census"]["book_quiet_skipped"] == 0


# ------------------------------------------------------- part 1: the timing

def test_e6_the_timing_block_is_present_bounded_and_served_inside_short():
    p, b = _quiet_book()
    st = _tick(p, _v())
    tm = _timing(st)
    assert tuple(tm) == TIMING_KEYS, "bounded: these keys and no others"
    for k in ("walk", "orders", "books", "books_venue", "books_data", "candidates"):
        assert isinstance(tm[k], float) and tm[k] >= 0.0 and tm[k] == round(tm[k], 1), k
    for k in TIMING_KEYS[6:]:
        assert isinstance(tm[k], int) and tm[k] >= 0, k
    assert tm["venue_calls"] == _census(st, "venue_calls") and tm["snap_market_reads"] == st["snap_market_reads"]
    assert tm["read"] == 1 and tm["on_target"] == 1 and tm["placed"] == 0 and tm["quiet_skipped"] == 0
    assert tm["budget"] == 60 and tm["cand_cap"] == 40 and tm["cand_budget"] == 60 - tm["venue_calls"]
    assert tm["quiet_budget"] == 60 - 2 - 1 - ml.CAND_MIN_PER_TICK, \
        "steps R and O, one read per hot book (never read: hot), the candidates' floor (review MEDIUM-4)"
    # the served surface: the top level is not truncated, `integ` stays
    # under the cap, `fills_dedup` is whole, and the block is served
    # inside `short` with its numbers as numbers
    served = api_app._sanitize_detail(st)
    assert "_truncated_keys" not in served and len(st["integ"]) < api_app._DETAIL_MAX_KEYS
    assert list(ml._new_stats()).index("short") < api_app._DETAIL_MAX_KEYS and len(ml._new_stats()) <= 40
    assert served["short"]["timing"] == tm and served["fills_dedup"] == st["fills_dedup"]
    assert "timing" not in st and "timing" not in st["integ"]


def test_e6_the_outcome_classes_count_every_book_read_or_skipped():
    p = _pool()
    on = p.add_book(ledger=300)                                     # on target
    halted = p.add_book(ledger=300, condition_id="0xhalt", us_market_slug="aec-atp-halt-x-2026-09-06",
                        long_asset="tokLh", other_asset="tokOh")     # no mark
    p.markets["0xhalt"] = {"closed": False, "resolved": False, "resolved_prices": None}
    v = _Venue(held={SLUG: 300, "aec-atp-halt-x-2026-09-06": 300},
               states={"aec-atp-halt-x-2026-09-06": "MARKET_STATE_HALTED"})
    st = _tick(p, v)
    tm = _timing(st)
    assert tm["read"] == 2 and tm["on_target"] == 1 and tm["no_mark"] == 1 and tm["placed"] == 0, tm
    assert on["last_plan"]["reason"] == "on target" and halted["last_reason"] == "no_mark"
    # tick 2: the on-target book is quiet (skipped), the no-mark one hot
    st2 = _tick(p, v, now=NOW + 30)
    tm2 = _timing(st2)
    assert tm2["quiet_skipped"] == 1 and tm2["read"] == 1 and tm2["no_mark"] == 1 and tm2["on_target"] == 0, tm2
    assert on["last_reason"] == "book_quiet_skipped"
    # a placement counts `placed`, whatever the plan said
    p2 = _pool()
    inc = p2.add_book(ledger=0)
    v2 = _Venue()
    st3 = _tick(p2, v2)
    assert _places(v2) and _timing(st3)["placed"] == 1 and _timing(st3)["on_target"] == 0
    assert inc["open_order_id"] is not None and ml._quiet_memo[inc["id"]]["quiet"] is False
    # a terminal-skipped book (W1's memo) counts under its own class
    p3 = _pool()
    p3.add_book(ledger=0)
    v3 = _Venue(state=EXPIRED)
    _tick(p3, v3)
    st4 = _tick(p3, v3, now=NOW + 30)
    assert _timing(st4)["terminal_skipped"] == 1 and _timing(st4)["read"] == 0


def test_e6_the_book_reads_time_is_summed_per_call(monkeypatch):
    orig = ms._paced_bbo

    def _slow(pmus, slug):
        _busy(0.06)
        return orig(pmus, slug)
    monkeypatch.setattr(ms, "_paced_bbo", _slow)
    p = _pool()
    _many_books(p, 3)
    st = _tick(p, _Venue())                 # three books, the candidate opens a fourth (its own read)
    tm = _timing(st)
    # summed PER CALL: the walk runs its three reads MIRROR_BOOK_CONCURRENCY
    # at a time, so the venue part (>= 4 x 0.06 s with the opened book's
    # own read) exceeds the step's wall time
    assert tm["read"] == 4 and tm["books_venue"] >= 0.2 and tm["books"] >= 0.1, tm
    assert tm["books_venue"] >= tm["books"], "per call under the parallel walk"
    assert tm["candidates"] >= 0.1, "the opened book's own read lands in the candidate stage"
    # the paced writes ride on the same accumulator (_paced, under its lock)
    before = ml._paced_seconds()
    ml._paced(lambda: _busy(0.02))
    assert ml._paced_seconds() - before >= 0.02
    assert "pace(ms.READ_PACING_S)" in inspect.getsource(ml._paced)


def test_e6_the_mode_line_carries_t_before_venue_and_the_quiet_line_is_unchanged(caplog):
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_ON, whales=["rn1"], books_live=2, orders_open=1)
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert line.startswith("mirror_live mode=on whales=['rn1'] books=2 open=1 day=None venue=None stats={")
    assert " t=" not in line, "a dict with no timing prints no fragment (the L1 pins' shape)"
    caplog.clear()
    stats["short"]["timing"] = {"walk": 1.2, "orders": 0.3, "books": 40.1, "candidates": 12.0,
                                "read": 30, "quiet_skipped": 45, "placed": 4}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS,
                      {"sum": -100.0, "books": 1, "limit": 5000.0, "since": "2026-09-07T17:30:00Z"})
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert (" day=None t=1.2/0.3/40.1/12.0 read=30 quiet=45 placed=4 loss=-100.0/5000.0 since 17:30 "
            "venue=None stats={") in line, line
    assert line.index(" t=") < 200, "inside the first 400 characters the logs action keeps"
    # a backed-off tick prints no time (its line is pinned as it was)
    caplog.clear()
    stats.update(skipped_backoff=True, backoff_left_s=59.0)
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert " t=" not in line and " day=None venue=None backoff=59.0 stats={" in line


# --------------------------------------------- part 2: the always-read books

def test_e6_a_book_with_an_open_order_is_never_skipped():
    p = _pool()
    b = p.add_book(ledger=0)                  # an increase: the tick rests a BUY the venue never fills
    v = _Venue()
    for i in range(4):
        st = _tick(p, v, now=NOW + 30 * i)
        assert _census(st, "book_quiet_skipped") == 0 and _bbos(v).count(SLUG) == i + 1, i
        assert b["open_order_id"] is not None and b["id"] not in ml._quiet_deferred
        assert ml._quiet_memo[b["id"]]["quiet"] is False


def test_e6_a_frozen_book_is_never_skipped():
    p = _pool()
    b = p.add_book(ledger=300, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 60)
    v = _Venue(held={SLUG: 600})              # venue != ledger: frozen every tick (E5's exit path)
    for i in range(4):
        st = _tick(p, v, now=NOW + 30 * i)
        assert _census(st, "book_quiet_skipped") == 0 and len(_bbos(v)) == i + 1, i
        assert b["state"] == "frozen" and st["books_frozen"] == 1


def test_e6_a_not_on_target_book_is_never_skipped():
    # no mark (a halted venue): held `no_mark` every tick, read every tick
    p = _pool()
    b = p.add_book(ledger=300)
    v = _v(state="MARKET_STATE_HALTED", bid=None, ask=None)
    for i in range(4):
        st = _tick(p, v, now=NOW + 30 * i)
        assert _census(st, "book_quiet_skipped") == 0 and _census(st, "no_mark") == 1 and len(_bbos(v)) == i + 1
        assert b["last_reason"] == "no_mark"
    # a reduce pending (his net fell): the read every tick, the rest placed
    p2 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    p2.add_book(ledger=300, avg_cost=0.31)
    v2 = _Venue(held={SLUG: 300})
    st = _tick(p2, v2)
    assert _places(v2) and _timing(st)["placed"] == 1 and _census(st, "book_quiet_skipped") == 0
    st2 = _tick(p2, v2, now=NOW + 30)
    assert _census(st2, "book_quiet_skipped") == 0 and len(_bbos(v2)) == 2


def test_e6_a_book_with_his_fill_inside_hot_s_is_never_skipped():
    p = _pool(fills=[_fill(M, "BUY", 300.0, 0.31, NOW - 100)])
    p.add_book(ledger=300)
    v = _v()
    for i in range(4):
        st = _tick(p, v, now=NOW + 30 * i)
        assert _census(st, "book_quiet_skipped") == 0 and _census(st, "on_target") == 1 and len(_bbos(v)) == i + 1
    # the edge: exactly HOT_S old is inside; one second older is quiet
    assert ml._his_fill_since([{"ts": NOW - ml.HOT_S}], NOW - ml.HOT_S) is True
    assert ml._his_fill_since([{"ts": NOW - ml.HOT_S - 1}], NOW - ml.HOT_S) is False
    assert ml._his_fill_since([{"ts": None}], NOW) is True, "an unreadable stamp is a hot book"
    assert ml._his_fill_since([], NOW) is False


def test_e6_a_book_whose_last_plan_was_an_exit_take_or_unfilled_reduce_is_never_skipped():
    for name in sorted(ml.QUIET_EXIT_PLANS):
        p, b = _quiet_book()
        v = _v()
        _tick(p, v)
        assert b["last_plan"]["reason"] == "on target"
        b["last_plan"] = {**b["last_plan"], "kind": name}
        st = _tick(p, v, now=NOW + 30)
        assert _census(st, "book_quiet_skipped") == 0 and len(_bbos(v)) == 2, name
    assert ml._exit_plan_stands({"last_reason": "exit_take", "last_plan": None}) is True
    assert ml._exit_plan_stands({"last_reason": "on target", "last_plan": {"reason": "on target"}}) is False
    assert ml._exit_plan_stands({"last_plan": object()}) is True, "unreadable is hot"


def test_e6_a_woken_market_with_his_fill_is_read_first_and_hot():
    p, b = _quiet_book()
    v = _v()
    _tick(p, v)
    p.fills = p.fills + [_fill(M, "BUY", 50.0, 0.31, NOW + 20)]
    p.snap[M] = 350.0
    ml.notify(CID)
    st = _tick(p, v, now=NOW + 30)
    assert st["woken"] == [CID] and _census(st, "book_quiet_skipped") == 0 and len(_bbos(v)) == 2


# ------------------------------------------------- part 2: the quiet rotation

def test_e6_a_quiet_book_is_read_on_its_turn_and_skipped_otherwise_never_twice_deferred():
    p, b = _quiet_book()
    v = _v()
    st1 = _tick(p, v)
    assert _census(st1, "on_target") == 1 and ml._quiet_memo[b["id"]]["quiet"] is True
    assert ml._quiet_memo[b["id"]] == {"seq": 1, "quiet": True, "at": NOW}
    # ticks 2 to 9: skipped, no venue call, the plan names the tick (E11:
    # the rotation is 9 ticks, was 3 -- ticks 2 and 3 skipped, tick 4 the
    # turn; the same numbers off the constant)
    turn = 1 + ml.QUIET_EVERY_TICKS
    assert turn == 10
    for i in range(2, turn):
        now = NOW + 30 * (i - 1)
        v.calls.clear()
        st = _tick(p, v, now=now)
        assert "bbo" not in _kinds(v) and st["reads"] == 0 and not st.get("snap_market_planned"), i
        assert _census(st, "book_quiet_skipped") == 1 and _census(st, "on_target") == 0
        assert b["last_reason"] == "book_quiet_skipped" and b["state"] == "live"
        pl = b["last_plan"]
        assert pl["kind"] == "no_plan" and pl["read_on"] == 10 and pl["tick"] == i and pl["last_read"] == 1
        assert pl["at"] == now and pl["mark"] == 0.31 and pl["bid"] == 0.30 and pl["ask"] == 0.32
        assert not _places(v) and not _cancels(v) and st["ops"] == 0
        assert _timing(st)["quiet_skipped"] == 1 and _timing(st)["read"] == 0
    # tick 10: its turn -- read, on target, the memo moves
    v.calls.clear()
    st10 = _tick(p, v, now=NOW + 270)
    assert _bbos(v) == [SLUG] and _census(st10, "on_target") == 1 and _census(st10, "book_quiet_skipped") == 0
    assert ml._quiet_memo[b["id"]] == {"seq": 10, "quiet": True, "at": NOW + 270}
    st11 = _tick(p, v, now=NOW + 300)
    assert _census(st11, "book_quiet_skipped") == 1 and b["last_plan"]["read_on"] == 19


def _read_ids(p, v):
    return {b["id"] for b in p.books.values() if b["us_market_slug"] in _bbos(v)}


def test_e6_a_due_quiet_book_the_budget_cannot_fit_is_deferred_once_and_read_next_tick(monkeypatch):
    """REWRITTEN BY THE REVIEW FOLD (MEDIUM-1; the name kept). As built,
    the 12 books tick 4 could not fit were read on tick 5 whatever the
    budget said; the fold reads the deferred QUEUE first, FIFO, under
    max(quiet_budget, DEFERRED_MIN_PER_TICK) a tick, the overflow
    staying queued. 30 quiet books at the budget's floor (20): the due
    tick's quiet share is 20 - 2 (steps R and O) - 10 (the candidates'
    floor, MEDIUM-4) = 8, so 8 are read and 22 join the queue with
    `read_on` their turn (the next tick, the one after, the third at
    ten a tick); the next tick reads the queue's first ten, the one
    after the next ten, the third the last two; from there every book
    is read again within QUIET_EVERY_TICKS + ceil(30 / 10) ticks of its
    last read, never more than ten quiet reads a tick. E11: the due
    tick is 10 (1 + QUIET_EVERY_TICKS; it was 4 at 3), the queue's
    turns 11 / 12 / 13 (were 5 / 6 / 7), and the due-tick cohort comes
    round again on 19 -- at 3 the queue's third tick WAS that cohort's
    next turn (7 = 4 + 3) and read six of it beside the two; at 9 the
    two are read alone and ticks 14-18 read no quiet book. The same
    arithmetic off the constant, nothing else moved."""
    monkeypatch.setattr(ml, "VENUE_CALLS_PER_TICK", 20)
    p = _pool(conds=[])
    slugs = _many_books(p, 30)
    v = _Venue()
    st1 = _tick(p, v)
    assert sorted(_bbos(v)) == sorted(slugs) and _census(st1, "on_target") == 30, "an empty memo reads everything"
    assert _timing(st1)["quiet_budget"] == 0 and _timing(st1)["cand_budget"] == ml.CAND_MIN_PER_TICK
    due = 1 + ml.QUIET_EVERY_TICKS
    assert due == 10
    for seq in range(2, due):
        v.calls.clear()
        st = _tick(p, v, now=NOW + 30 * (seq - 1))
        assert not _bbos(v) and _census(st, "book_quiet_skipped") == 30
    v.calls.clear()
    st10 = _tick(p, v, now=NOW + 270)
    read10 = _read_ids(p, v)
    assert len(read10) == 8 and _census(st10, "book_quiet_skipped") == 22, (len(read10), st10["census"])
    assert _timing(st10)["quiet_budget"] == 20 - 2 - ml.CAND_MIN_PER_TICK == 8 and _timing(st10)["quiet_reads"] == 8
    queue = list(ml._quiet_deferred)
    assert len(queue) == 22 and set(queue) == {b["id"] for b in p.books.values() if b["last_reason"] == "book_quiet_skipped"}
    assert all(ml._quiet_deferred[i] == 10 for i in queue), "queued on tick 10"
    assert [p.books[i]["last_plan"]["read_on"] for i in queue] == [11] * 10 + [12] * 10 + [13] * 2, "its turn in the queue"
    v.calls.clear()
    st11 = _tick(p, v, now=NOW + 300)
    assert _read_ids(p, v) == set(queue[:10]) and _census(st11, "book_quiet_skipped") == 20
    assert _timing(st11)["quiet_reads"] == 10 == ml.DEFERRED_MIN_PER_TICK > _timing(st11)["quiet_budget"] == 8
    assert list(ml._quiet_deferred) == queue[10:], "FIFO: the head read, the rest in order"
    v.calls.clear()
    st12 = _tick(p, v, now=NOW + 330)
    assert _read_ids(p, v) == set(queue[10:20]) and _census(st12, "book_quiet_skipped") == 20
    assert list(ml._quiet_deferred) == queue[20:]
    v.calls.clear()
    st13 = _tick(p, v, now=NOW + 360)
    read13 = _read_ids(p, v)
    # E11: the queue's last two alone -- the tick-10 cohort is not due
    # again until tick 19 (at 3 the queue's third tick, 7, was that
    # cohort's next turn and read six of it beside the two)
    assert read13 == set(queue[20:]) and _census(st13, "book_quiet_skipped") == 28 and not ml._quiet_deferred
    last = {b["id"]: 13 if b["id"] in read13 else (12 if b["id"] in set(queue[10:20]) else (11 if b["id"] in set(queue[:10]) else 10))
            for b in p.books.values()}
    # ticks 14-18: nothing due, nothing read -- a quiet book is read on
    # its turn and never before it
    for seq in range(14, 19):
        v.calls.clear()
        st = _tick(p, v, now=NOW + 30 * (seq - 1))
        assert not _read_ids(p, v) and _census(st, "book_quiet_skipped") == 30, seq
    # tick 19: the tick-10 cohort's turn, the eight inside the budget
    v.calls.clear()
    st19 = _tick(p, v, now=NOW + 30 * 18)
    assert _read_ids(p, v) == read10 and _timing(st19)["quiet_reads"] == 8 and not ml._quiet_deferred
    for i in read10:
        last[i] = 19
    # steady state: never more than the slots a tick, every book read
    # again within QUIET_EVERY_TICKS + ceil(30 / 10) ticks of its last read
    for seq in range(20, 40):
        v.calls.clear()
        st = _tick(p, v, now=NOW + 30 * (seq - 1))
        read = _read_ids(p, v)
        assert len(read) <= max(_timing(st)["quiet_budget"], ml.DEFERRED_MIN_PER_TICK) == 10, seq
        assert _timing(st)["quiet_reads"] == len(read) and _census(st, "book_quiet_skipped") == 30 - len(read)
        for i in read:
            assert seq - last[i] <= ml.QUIET_EVERY_TICKS + 3, (seq, i, last[i])
            last[i] = seq
    assert max(seq - t for t in last.values()) <= ml.QUIET_EVERY_TICKS + 3


def test_e6_the_quiet_budget_counts_the_hot_books_by_row():
    p = _pool()
    quiet = p.add_book(ledger=300)
    frozen = p.add_book(ledger=300, state="frozen", frozen_reason="placement_lost", condition_id="0xf",
                        us_market_slug="aec-atp-f-x-2026-09-06", long_asset="tokLf", other_asset="tokOf")
    opened = p.add_book(ledger=300, condition_id="0xo", us_market_slug="aec-atp-o-x-2026-09-06",
                        long_asset="tokLo", other_asset="tokOo")
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    t.venue_calls = 2
    assert ml._hot_by_row(t, quiet) is True, "never read by this process"
    ml._quiet_memo[quiet["id"]] = {"seq": 1, "quiet": True, "at": NOW}
    quiet["last_plan"] = {"at": NOW, "reason": "on target"}
    assert ml._hot_by_row(t, quiet) is False and ml._hot_by_row(t, frozen) is True
    ml._quiet_memo[opened["id"]] = {"seq": 1, "quiet": True, "at": NOW}
    opened["last_plan"] = {"at": NOW}
    t.open_by_book[opened["id"]] = ({}, {})
    assert ml._hot_by_row(t, opened) is True
    assert ml._quiet_budget(t, [quiet, frozen, opened]) == 60 - 2 - 2 - ml.CAND_MIN_PER_TICK
    t.venue_calls = 200
    assert ml._quiet_budget(t, [quiet, frozen, opened]) == 0, "never negative"
    # review fold: a book the terminal memo will skip is hot by row and
    # costs no read (MEDIUM-2); a woken book is hot (HIGH-2)
    t.venue_calls = 2
    ml._terminal_book_until[("rn1", "0xf")] = NOW + 100
    assert ml._hot_by_row(t, frozen) is True and ml._memo_skips(t, frozen) is True
    assert ml._quiet_budget(t, [quiet, frozen, opened]) == 60 - 2 - 1 - ml.CAND_MIN_PER_TICK
    t.woken = {CID}
    assert ml._hot_by_row(t, quiet) is True
    assert ml._quiet_budget(t, [quiet, frozen, opened]) == 60 - 2 - 2 - ml.CAND_MIN_PER_TICK


def test_e6_step_m_runs_on_a_skipped_book_a_closed_row_closes_it_that_tick():
    p, b = _quiet_book(ledger=0)
    v = _v(held=0)
    _tick(p, v)
    v.calls.clear()
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st = _tick(p, v, now=NOW + 30)
    assert "bbo" not in _kinds(v) and st["reads"] == 0
    assert _census(st, "market_closed") == 1 and _census(st, "book_quiet_skipped") == 0
    assert b["state"] == "closed" and _census(st, "closed_cancelled") == 1, (b["state"], st["census"])
    # and the standing row's retirement, read before the memo
    p2, b2 = _quiet_book(ledger=0)
    _tick(p2, v)
    p2.rows[b2["standing_row_id"]]["status"] = "settled"
    st2 = _tick(p2, v, now=NOW + 30)
    assert b2["state"] == "closed" and _census(st2, "book_quiet_skipped") == 0


def test_e6_the_flat_close_runs_on_a_skipped_book_with_the_carried_clock():
    p = _pool(fills=_his(300, other_size=300), snap={M: 300.0, N: 300.0})   # paired out: target 0
    b = p.add_book(ledger=0, gross_buy=93.0, avg_cost=0.31)
    row = p.rows[b["standing_row_id"]]
    st = _tick(p, _Venue())
    assert _census(st, "on_target") == 1 and b["last_plan"]["flat_since"] == NOW and b["last_plan"]["close"] == "not_due"
    v2 = _Venue()
    st2 = _tick(p, v2, now=NOW + 30)
    assert _census(st2, "book_quiet_skipped") == 1 and "bbo" not in _kinds(v2)
    assert b["last_plan"]["flat_since"] == NOW and b["last_plan"]["close"] == "not_due", "the clock carried"
    v3 = _Venue()
    st3 = _tick(p, v3, now=NOW + rules.MIRROR_FLAT_CLOSE_S + 1)
    assert "bbo" not in _kinds(v3) and _census(st3, "book_quiet_skipped") == 1
    assert row["status"] == "cashed_out" and b["state"] == "closed" and _census(st3, "closed_cashed_out") == 1


def test_e6_a_skipped_books_plan_carries_what_the_next_read_and_a_sibling_read_off_the_row():
    assert ml._SKIP_CARRIED == ("flat_since", "short_proof", "mark", "bid", "ask", "exit_px_src")
    p, b = _quiet_book()
    v = _v()
    _tick(p, v)
    b["last_plan"] = {**b["last_plan"], "short_proof": "ok", "exit_px_src": "his_sell"}
    st = _tick(p, v, now=NOW + 30)
    pl = b["last_plan"]
    assert pl["short_proof"] == "ok" and pl["exit_px_src"] == "his_sell" and pl["mark"] == 0.31
    assert pl["kind"] == "no_plan" and st["reads"] == 0
    # the sibling's game room reads the skipped book at its carried mark
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW + 60, stats=ml._new_stats())
    assert ml._held_exposure(t, b) == pytest.approx(rules.book_exposure(300, 0.31, 0.31, b["intent"], 0.0))


def test_e6_a_row_written_by_another_hand_or_a_malformed_memo_is_read_never_skipped():
    p, b = _quiet_book()
    v = _v()
    _tick(p, v)
    b["last_plan"] = {**b["last_plan"], "at": NOW - 1}          # another process wrote the row
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "book_quiet_skipped") == 0 and len(_bbos(v)) == 2
    ml._quiet_memo[b["id"]] = {"garbage": True}                   # a memo this cannot read
    st = _tick(p, v, now=NOW + 60)
    assert _census(st, "book_quiet_skipped") == 0 and _census(st, "book_error") == 0 and len(_bbos(v)) == 3
    b["last_plan"] = "not a plan"                                  # an unreadable row
    st = _tick(p, v, now=NOW + 90)
    assert _census(st, "book_quiet_skipped") == 0 and len(_bbos(v)) == 4
    ml._quiet_memo.clear()                                         # a deploy: read everything
    st = _tick(p, v, now=NOW + 120)
    assert _census(st, "book_quiet_skipped") == 0 and len(_bbos(v)) == 5


def test_e6_the_quiet_rule_sits_after_step_m_and_the_terminal_memo_and_touches_no_order_path():
    src = inspect.getsource(ml._tick_book)
    assert (src.index("await _market(t, cid)") < src.index("_terminal_book_until.get(")
            < src.index("_quiet_skip(t, book, fills)") < src.index("_read_market("))
    skip = src[src.index("_quiet_skip(t, book, fills)"):src.index("_read_market(")]
    assert "_maybe_close_episode(" in skip and "_cancel" not in skip and "_place" not in skip
    assert "_quiet_skip" not in inspect.getsource(ml._tick_candidate)
    assert "_quiet_skip" not in inspect.getsource(ml._bbo) and "_quiet_skip" not in inspect.getsource(ml._act)
    # the shadow's invariant stands
    from tests.test_mirror_shadow import test_the_shadow_never_touches_an_order
    test_the_shadow_never_touches_an_order()


# ----------------------------------------------- part 2: the candidate stage

def test_e6_the_candidate_walk_takes_what_the_budget_leaves_never_below_cand_min(monkeypatch):
    # 25 books, 41 candidates: 60 - (1 + 1 + 25) = 33 candidates, under the cap
    p = _pool(conds=[f"c{i}" for i in range(41)])
    _many_books(p, 25)
    st = _tick(p, _Venue())
    assert st["reads"] == 25 + 33 and st["capped_tick"] is True and _census(st, "cand_unread_capped") == 8
    assert _timing(st)["cand_budget"] == 33 and _timing(st)["cand_cap"] == 33
    assert _timing(st)["map_cap"] == 33 - ml.CAND_MIN_PER_TICK, "the resolver's share sits above the quote reads' floor"
    # the budget at the floor: the stage never starves below 10
    monkeypatch.setattr(ml, "VENUE_CALLS_PER_TICK", 20)
    p2 = _pool(conds=[f"c{i}" for i in range(41)])
    _many_books(p2, 25)
    st2 = _tick(p2, _Venue())
    assert st2["reads"] == 25 + 10 and _timing(st2)["cand_budget"] == 10
    # the cap under the floor still wins: env may lower it
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 5)
    p3 = _pool(conds=[f"c{i}" for i in range(41)])
    _many_books(p3, 25)
    st3 = _tick(p3, _Venue())
    assert st3["reads"] == 25 + 5
    # a lone book: the whole cap of 40 as before
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 40)
    monkeypatch.setattr(ml, "VENUE_CALLS_PER_TICK", 60)
    p4 = _pool(conds=[f"c{i}" for i in range(41)])
    p4.add_book(ledger=300)
    st4 = _tick(p4, _Venue())
    assert st4["reads"] == 1 + 40
    t = ml._Tick(pool=p4, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    t.venue_calls = 55
    assert ml._cand_budget(t) == 10
    t.venue_calls = 20
    t.cand_budget = ml._cand_budget(t)
    assert t.cand_budget == 40 and ml._cand_cap(t) == 40
    t.map_budget.reads = 25
    assert ml._cand_cap(t) == 15, "the map reads spend the share first"
    t.map_budget.reads = 35
    assert ml._cand_cap(t) == ml.CAND_MIN_PER_TICK == 10, "never under the quote reads' floor (review HIGH-1)"


def _map_reader(calls):
    """ms.map_market as the candidate stage sees it for a market that
    needs the resolver: one venue read per call under the budget, the
    market unmapped; past the cap `map_reads_capped`."""
    async def _map(pool, fills, pmus=None, **kw):
        out, budget = kw["out"], kw["budget"]
        if budget.reads >= budget.cap:
            budget.capped += 1
            out["refusal"] = "map_reads_capped"
            return None
        budget.reads += 1
        out["venue_reads"] = 1
        calls.append(kw.get("condition_id"))
        return None
    return _map


def test_e6_the_map_reads_are_the_budgets_floor_never_its_ceiling(monkeypatch):
    """The coordinator's pin (the 19:42Z tick: map_reads_capped 108 at
    map_venue_read 10). Budget 60, 20 hot books: the resolver reads
    what sits above the quote reads' floor, 38 - 10 = 28 map reads in
    one tick (the review fold, HIGH-1: this pin read "at least 30" and
    a walk cut by the share; now the walk goes on past the map cap
    under `map_reads_capped`, as before E6, and the mapped candidates
    behind the resolver reads keep their quote reads); budget 20 (the
    floor): the old cap of 10 holds."""
    assert ms.MAP_READS_PER_TICK == 10
    calls = []
    monkeypatch.setattr(ms, "map_market", _map_reader(calls))
    p = _pool(conds=[f"c{i}" for i in range(60)])
    _many_books(p, 20)
    st = _tick(p, _Venue())
    assert st["books_live"] == 20 and _census(st, "map_venue_read") == len(calls) == 28 > ms.MAP_READS_PER_TICK
    assert _census(st, "map_venue_read") == 60 - 22 - ml.CAND_MIN_PER_TICK and _timing(st)["map_cap"] == 28
    assert _census(st, "unmapped") == 28 and _census(st, "map_reads_capped") == 60 - 28
    assert not st.get("capped_tick") and _census(st, "cand_unread_capped") == 0, "the walk is not cut by the map reads"
    assert _timing(st)["cand_cap"] == ml.CAND_MIN_PER_TICK, "the quote reads' floor stands behind the map reads"
    assert _census(st, "venue_calls_capped") == 0, "under E2's guard of 80"
    monkeypatch.setattr(ml, "VENUE_CALLS_PER_TICK", 20)
    monkeypatch.setattr(ml, "_unmapped_until", {})
    calls.clear()
    p2 = _pool(conds=[f"c{i}" for i in range(60)])
    _many_books(p2, 20)
    st2 = _tick(p2, _Venue())
    assert len(calls) == 10 and _census(st2, "map_venue_read") == 10 and _timing(st2)["map_cap"] == 10


def test_e6_an_operator_lowered_map_cap_is_honoured_as_a_ceiling(monkeypatch):
    monkeypatch.delenv("MIRROR_MAP_READS", raising=False)
    # the share less the quote reads' floor (review HIGH-1), the old cap the floor of that
    assert ml._map_cap(38) == 38 - ml.CAND_MIN_PER_TICK == 28 and ml._map_cap(20) == 10 and ml._map_cap(3) == 10
    monkeypatch.setenv("MIRROR_MAP_READS", "3")
    monkeypatch.setattr(ms, "MAP_READS_PER_TICK", 3)          # what the reloaded capped_env would hold
    assert ml._map_cap(38) == 3 and ml._map_cap(12) == 3 and ml._map_cap(2) == 3, "set: the env's value, the floor"
    monkeypatch.setenv("MIRROR_MAP_READS", "10")
    monkeypatch.setattr(ms, "MAP_READS_PER_TICK", 10)
    assert ml._map_cap(38) == 10, "set to the old cap is the old cap"


def test_e6_quiet_exit_plans_are_census_names_and_money_is_covered_by_the_other_clauses(monkeypatch):
    """Review LOW-4 (the fact, pinned; docs section 28): the three
    QUIET_EXIT_PLANS names are counted by _mirror_stop and never
    written as a row's last_reason or a plan's kind / reason, so
    _exit_plan_stands matches hand-written rows only. Money is covered
    by the other clauses: an exit's take leaves an open order or a
    placement, and a placed / open book's verdict is never quiet -- so
    with the exit clause emptied a book that reduced last tick is still
    hot by row."""
    src = inspect.getsource(ml)
    for name in sorted(ml.QUIET_EXIT_PLANS):
        assert f'_mirror_stop("{name}"' in src, name
        for field in ("kind", "reason", "last_reason"):
            assert f'{field}="{name}"' not in src and f'"{field}": "{name}"' not in src, (field, name)
    assert ml._exit_plan_stands({"last_reason": "exit_take"}) and ml._exit_plan_stands({"last_plan": {"kind": "reduce_unfilled"}})
    assert not ml._exit_plan_stands({"last_reason": "on_target", "last_plan": {"kind": "take", "reason": "on target"}})
    monkeypatch.setattr(ml, "QUIET_EXIT_PLANS", frozenset())
    p = _pool()
    b = p.add_book(ledger=300)
    v = _v()
    _tick(p, v)
    assert ml._quiet_memo[b["id"]]["quiet"] is True
    p.fills = p.fills + [_fill(M, "SELL", 200.0, 0.31, NOW + 25)]
    p.snap[M] = 100.0
    st = _tick(p, v, now=NOW + 30)
    assert _timing(st)["placed"] == 1 and ml._quiet_memo[b["id"]]["quiet"] is False, "a reduce placed: hot from here"
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW + 60, stats=ml._new_stats())
    assert ml._hot_by_row(t, b) is True and not ml._exit_plan_stands(b)


def test_e6_the_source_pins_the_candidate_break_keeps_u12s_text_and_the_walk_order():
    src = inspect.getsource(ml._tick)
    assert "t.cand_reads >= MAX_MARKETS_PER_TICK or t.cand_reads >= t.cand_cap" in src
    assert src.index("t.quiet_budget = _quiet_budget(t, books)") < src.index("await _walk_books(t, _woken_first(books, woken))")
    assert src.index("await _walk_books(t, _woken_first(books, woken))") < src.index("t.cand_budget = _cand_budget(t)")
    assert src.index("t.map_budget.cap = _map_cap(t.cand_budget)") < src.index("t.cand_cap = _cand_cap(t)")
    assert src.index("t.cand_cap = _cand_cap(t)") < src.index("t.guard_calls >= rules.MIRROR_VENUE_CALLS_PER_TICK")
    assert src.index("_tick_seq += 1") < src.index("_load_terminal_memo(t)") < src.index("await _read_mode(t)\n    if t.mode")
    once = inspect.getsource(ml.tick_once)
    assert once.index("_publish_fills_dedup(t)") > once.index('stats.setdefault("short", {})["timing"] = _timing_block(t)')
    assert once.index("_persist_terminal_memo(t)") > once.index("_publish_fills_dedup(t)")


# ------------------------------------------ part 3: the terminal memo persists

def _memo_writes(p):
    return [x for x in p.sent if "ml-state-write" in x[1] and x[2][0] == ml._STATE_TERMINAL_MEMO]


def _memo_reads(p):
    return [x for x in p.sent if "SELECT value FROM ingestion_state" in x[1] and x[2][0] == ml._STATE_TERMINAL_MEMO]


def test_e6_the_terminal_memo_is_written_bounded_once_per_60s_and_only_on_change():
    assert ml._STATE_TERMINAL_MEMO == "mirror_terminal_memo" and ml.TERMINAL_MEMO_WRITE_S == 60.0
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue(state=EXPIRED)
    _tick(p, v)
    assert ml._terminal_book_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}
    assert len(_memo_writes(p)) == 1 and not _memo_reads(p), "written this tick; the boot read was made"
    assert p.state[ml._STATE_TERMINAL_MEMO] == {
        "cand": [], "book": [["rn1", CID, round(NOW + ms.UNMAPPED_TTL_S, 1), EXPIRED]], "at": ml._iso(NOW)}
    # unchanged: no second write
    _tick(p, v, now=NOW + 30)
    assert len(_memo_writes(p)) == 1
    # changed inside 60 s of the last write: not yet; at 60 s: written
    ml._terminal_until[("rn1", "0xcand")] = NOW + 900
    _tick(p, v, now=NOW + 59)
    assert len(_memo_writes(p)) == 1
    _tick(p, v, now=NOW + 60)
    assert len(_memo_writes(p)) == 2 and p.state[ml._STATE_TERMINAL_MEMO]["cand"] == [["rn1", "0xcand", round(NOW + 900, 1)]]
    # bounded: an entry whose until has passed is dropped on write (and
    # its passing IS a change, written once the 60 s gate opens)
    ml._terminal_until[("rn1", "0xold")] = NOW + 300
    _tick(p, v, now=NOW + 200)
    assert sorted(e[1] for e in p.state[ml._STATE_TERMINAL_MEMO]["cand"]) == ["0xcand", "0xold"]
    assert len(_memo_writes(p)) == 3
    _tick(p, v, now=NOW + 330)
    assert [e[1] for e in p.state[ml._STATE_TERMINAL_MEMO]["cand"]] == ["0xcand"] and len(_memo_writes(p)) == 4
    ml._terminal_until[("rn1", "0xdead")] = NOW - 1               # already past: not a change, no write
    _tick(p, v, now=NOW + 395)
    assert len(_memo_writes(p)) == 4
    # the cap
    snap = ml._terminal_memo_snapshot(NOW)
    assert set(snap) == {"cand", "book"} and ml._TERMINAL_MEMO_MAX == 4000
    ml._terminal_until.update({("rn1", f"0x{i}"): NOW + 10 + i for i in range(ml._TERMINAL_MEMO_MAX + 5)})
    snap = ml._terminal_memo_snapshot(NOW)
    assert len(snap["cand"]) == ml._TERMINAL_MEMO_MAX and snap["cand"][0][2] >= snap["cand"][-1][2], \
        "the soonest to expire dropped first"
    # a failed write is logged and retried: the signature is kept only on success
    p.raise_on.append(("ml-state-write", RuntimeError("down")))
    ml._terminal_until.clear()
    ml._terminal_until[("rn1", "0xnew")] = NOW + 5000
    st = _tick(p, v, now=NOW + 400)
    assert not st["abandoned"] and ml._terminal_memo_last["sig"] != ml._terminal_memo_sig(ml._terminal_memo_snapshot(NOW + 400))
    p.raise_on.clear()
    _tick(p, v, now=NOW + 470)
    assert [e[1] for e in p.state[ml._STATE_TERMINAL_MEMO]["cand"]] == ["0xnew"]


def test_e6_the_terminal_memo_is_read_once_at_boot_expired_entries_dropped():
    ml._terminal_memo_loaded = False
    p = _pool()
    p.add_book(ledger=300)
    p.state[ml._STATE_TERMINAL_MEMO] = {
        "cand": [["rn1", "0xa", NOW + 500], ["rn1", "0xgone", NOW - 1], ["rn1", "0xbad"], "junk",
                 ["rn1", "0xnan", "x"], [1, "0xnum", NOW + 500]],
        "book": [["rn1", "0xb", NOW + 700, EXPIRED], ["rn1", "0xnostate", NOW + 700, None],
                 ["rn1", "0xgone", NOW - 1, EXPIRED]],
        "at": "2026-09-07T19:00:00Z"}
    st = _tick(p, _v())
    assert not st["abandoned"] and ml._terminal_memo_loaded is True and _census(st, "on_target") == 1
    assert ml._terminal_until == {("rn1", "0xa"): NOW + 500.0}
    assert ml._terminal_book_until == {("rn1", "0xb"): NOW + 700.0} and ml._terminal_book_state == {("rn1", "0xb"): EXPIRED}
    assert len(_memo_reads(p)) == 1 and not _memo_writes(p), "read once; what stands written is not rewritten"
    _tick(p, _v(), now=NOW + 30)
    assert len(_memo_reads(p)) == 1, "once per process"
    # a candidate on the memoised market spends no slot after the deploy
    p2 = _pool(conds=["0xa", CID])
    p2.markets["0xa"] = {"closed": False, "resolved": False, "resolved_prices": None}
    st2 = _tick(p2, _Venue(), now=NOW + 60)
    assert _census(st2, "cand_terminal_skipped") == 1


@pytest.mark.parametrize("value", ["x", ["a"], {"cand": "x", "book": 3}, {"cand": [["rn1"]], "book": None}, 7])
def test_e6_a_malformed_terminal_memo_is_empty_and_the_tick_runs(value, caplog):
    ml._terminal_memo_loaded = False
    p = _pool()
    p.add_book(ledger=300)
    p.state[ml._STATE_TERMINAL_MEMO] = value
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _v())
    assert not st["abandoned"] and _census(st, "on_target") == 1
    assert ml._terminal_until == {} and ml._terminal_book_until == {} and ml._terminal_book_state == {}


def test_e6_an_unreadable_terminal_memo_is_empty_and_never_read_again(caplog):
    ml._terminal_memo_loaded = False
    p = _pool()
    p.add_book(ledger=300)
    p.raise_on.append(("SELECT value FROM ingestion_state", RuntimeError("down")))
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _Venue())
    assert ml._terminal_memo_loaded is True and ml._terminal_until == {} and ml._terminal_book_until == {}
    assert any("mirror_terminal_memo unreadable" in r.getMessage() for r in caplog.records)
    assert st["mode"] == ml.MODE_EXITS and not st["abandoned"], "the tick went on (the mode key raised too: exits)"
    p.raise_on.clear()
    _tick(p, _v(), now=NOW + 30)
    assert len(_memo_reads(p)) == 1, "one boot read per process, whatever it read"
    # json garbage on the key is `malformed`, empty too
    ml._terminal_memo_loaded = False
    p2 = _pool()
    p2.add_book(ledger=300)
    p2.state[ml._STATE_TERMINAL_MEMO] = "{not json"
    st2 = _tick(p2, _v())
    assert not st2["abandoned"] and ml._terminal_until == {} and ml._terminal_book_until == {}


# --------------------------------------------------------- the rails stand

def test_e6_the_every_guard_and_refusal_order_pins_stand_and_the_served_cap_holds(monkeypatch):
    from tests import test_mirror_live_worker as w
    w.test_ledger_dust_is_the_last_census_key_and_no_served_index_moved()
    w.test_the_gate_counters_survive_the_health_endpoints_sanitizer()
    src = inspect.getsource(ml._increases_refusal) + inspect.getsource(ml._global_guards)
    assert "_quiet" not in src and "VENUE_CALLS_PER_TICK" not in src, "no guard reads the budget"
    assert len(ml._integ_block(ml._new_stats())) < 40
    assert "timing" not in json.dumps(ml._new_stats()), "present after a tick, never a 41st top-level key"
