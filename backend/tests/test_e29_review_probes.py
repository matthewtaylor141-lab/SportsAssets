"""E29 (FILL lane 29) -- the adversarial review's probes. Each test names
the finding it stands for (FILL_L29_review.md); a test that FAILS on the
builder's patch is the finding's reproduction, one that passes is the
delta that kills a surviving mutant.

HIGH-1: the hand-exit memo is a whole-dict overwrite from the tick-start
snapshot with no lock -- two books marked in the same tick (the walk runs
games in parallel under MIRROR_BOOK_CONCURRENCY 6) lose one entry, and a
lost entry RELEASES that market by the lane's own rule (`memo` True while
the readable memo names nothing): the next tick re-adds the whole target.
"""
from __future__ import annotations

import asyncio

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e24_hand_fills import _hand_of, _long_pool, _sale_1129  # noqa: F401 -- the autouse rails
from tests.test_e29_hand_exit import (  # noqa: F401
    COVER_1, HIS_NET, KEY, LEDGER, NEW_NAMES, _book_1317, _cover, _held_plan, _http_1317, _hx, _memo,
    _memo_entry, _pool_1317, _sells, _v1317,
)
from tests.test_e9_fast_path import _fast, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, NOW, SLUG, _NoClose, _armed, _census, _his, _mkt, _places, _run, _shorts_on, _tick,
)


def _reset():
    ml._hand_read_at.clear()
    ml._disagree_fill_read_at.clear()
    ml._lost_fill_read_at.clear()


def _state_reads(p, key):
    return [a for k, s, a in p.sent if "SELECT value FROM ingestion_state" in s and a and a[0] == key]


# ------------------------------------------------ HIGH-1: two marks in one tick, one entry lost


def test_review_high1_two_marks_in_one_tick_keep_both_memo_entries(monkeypatch):
    """Two books on two games, each adopting a hand cover in the SAME tick:
    the walk ticks games concurrently, and each mark's memo write copies
    t.hand_exits, awaits the database (asyncpg yields; the fake did not,
    so the round trip is modelled by one yield) and writes the whole dict
    back. Both entries must be in the memo afterwards -- a lost one is a
    RELEASE of that market on the next tick (the lane's own rule: memo
    True on the row while the readable memo names nothing)."""
    real = ml._write_state

    async def _ws(pool, key, value):
        await asyncio.sleep(0)                  # the database round trip
        return await real(pool, key, value)
    monkeypatch.setattr(ml, "_write_state", _ws)
    p = _pool_1317()
    t = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    t.hand_exits = {}

    async def _both():
        return await asyncio.gather(
            ml._hand_exit_memo_write(t, "rn1:0xa", {"at": 1.0, "book_id": 1, "slug": "a", "shares": 1.0, "px": 0.3}),
            ml._hand_exit_memo_write(t, "rn1:0xb", {"at": 2.0, "book_id": 2, "slug": "b", "shares": 2.0, "px": 0.4}))
    assert _run(_both()) == [True, True]
    assert set(p.state["mirror_hand_exit"]) == {"rn1:0xa", "rn1:0xb"}, "a mark lost under the walk's concurrency"
    assert set(t.hand_exits) == {"rn1:0xa", "rn1:0xb"}


def test_review_high1_a_lost_entry_is_a_release_and_the_whole_target_goes_out(monkeypatch):
    """The consequence, on the 1317 shape: the row's record says its write
    landed (memo True) while the memo -- overwritten by another book's mark
    -- no longer names the market: the tick reads RELEASED and places the
    1,039 SELL_LONG re-entry. This is the builder's own race pin read the
    other way round: it is what a lost entry costs."""
    _shorts_on(monkeypatch)
    _reset()
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {"rn1:0xother": _memo_entry(book_id=9)}      # the other book's mark, alone
    b = _book_1317(p, ledger=-1421, last_plan=_held_plan(memo=True))
    v = _v1317(-1421)
    st = _tick(p, v, http=_http_1317())
    assert len(_sells(v)) == 1 and _sells(v)[0][3] == int(COVER_1) and _census(st, "hand_held") == 0
    assert "hand_exit" not in b["last_plan"], "the record dropped: the market is open again with nobody's release"


# ------------------------------------------------ MEDIUM: the memo is read once per tick, absence is empty


def test_review_medium_the_memo_is_read_once_per_tick_whatever_the_candidates(monkeypatch):
    _shorts_on(monkeypatch)
    _reset()
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    v = _v1317(0)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held") == 1
    assert len(_state_reads(p, "mirror_hand_exit")) == 1, "ONE read at the tick's start, none per candidate"


def test_review_medium_an_absent_memo_reads_as_empty_not_unreadable(monkeypatch):
    """A fresh deploy has no 'mirror_hand_exit' key until the first mark:
    the tick reads it as an empty dict (readable, naming nothing), never
    as None -- else every candidate would take the closed-row read every
    tick and every live book with an unreadable plan would be held
    hand_held_unread until the first hand exit."""
    _shorts_on(monkeypatch)
    _reset()
    p = _pool_1317()
    assert "mirror_hand_exit" not in p.state
    t = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    _run(ml._load_hand_exits(t))
    assert t.hand_exits == {}
    # the world: the candidate opens with no closed-row read; a live book with an unreadable plan adds as today
    v = _v1317(0)
    st = _tick(p, v, http=_http_1317())
    assert all(_census(st, k) == 0 for k in NEW_NAMES) and len(p.books) == 1
    # lane 5's _flip_since reads the same row once at the open; the hand fallback would be a second read
    assert len([s for k, s, a in p.sent if "ml-book-flip" in s]) == 1, "no closed-row read on a readable, empty memo"
    p2 = _pool_1317()
    b2 = _book_1317(p2, ledger=-1421, last_plan="{not json")
    v2 = _v1317(-1421)
    st2 = _tick(p2, v2, http=_http_1317())
    assert _census(st2, "hand_held_unread") == 0 and len(_sells(v2)) == 1


# ------------------------------------------------ MEDIUM: the quiet skip carries the record


def test_review_medium_the_quiet_skip_carries_the_hand_exit_record(monkeypatch):
    """A hand-exited book whose target equals its ledger (his net fell to
    -14,210: target -1,421) plans `on target`, and the tick after is
    quiet-skipped: the skip's UPDATE replaces the plan and must carry the
    record, else the row loses its mark over a quiet spell and holds on
    the memo alone (nothing, the tick the memo cannot be read)."""
    _shorts_on(monkeypatch)
    _reset()
    p = _pool_1317(fills=_his(100.0, other_size=14310.0, other_px=0.71), snap={"tok-m": 100.0, "tok-n": 14310.0})
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    b = _book_1317(p, ledger=-1421, last_plan=_held_plan(target=-1421, net=-14210.0))
    v = _v1317(-1421)
    st = _tick(p, v, http=_mkt(100.0, 14310.0))
    assert b["last_plan"]["target"] == -1421 and b["last_reason"] == "on target" and not _places(v)
    assert _hx(b) is not None, "the on-target plan carries the record"
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(100.0, 14310.0))
    assert _census(st2, "book_quiet_skipped") == 1 and b["last_reason"] == "book_quiet_skipped"
    assert _hx(b) is not None and _hx(b)["shares"] == COVER_1, "the skip's plan carries the record"


# ------------------------------------------------ LOW: the fast wake on the memo alone; no mark on a failed booking


def test_review_low_the_fast_wake_holds_on_the_memo_alone(monkeypatch):
    _shorts_on(monkeypatch)
    _reset()
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    plan_a = {k: val for k, val in _held_plan().items() if k != "hand_exit"}
    b = _book_1317(p, ledger=-1421, last_plan=plan_a)
    v = _v1317(-1421)
    _walk({SLUG: -1421.0})
    fs = _fast(p, v, http=_http_1317())
    assert _census(fs, "hand_held") == 1 and not _places(v) and b["last_plan"]["hold"] == "hand_held"


def test_review_low_a_booking_that_fails_marks_nothing(monkeypatch, caplog):
    """E24's adopt_write_failed shape (book 1129, the ledger sell raising):
    nothing adopted, so nothing marked -- no hand_exit on the plan, no
    memo, none of the four names."""
    _reset()
    p = _long_pool()
    b = p.add_book(ledger=4852, avg_cost=0.3116, ratio=0.1, target=2876)
    p.raise_on.append(("ml-book-ledger-sell", RuntimeError("down")))
    v = _NoClose(held={SLUG: -186}, bid=0.60, ask=0.61, trades=[_sale_1129()])
    with caplog.at_level("ERROR"):
        st = _tick(p, v, http=_mkt(28765.0))
    assert _hand_of(b)["verdict"] == "adopt_write_failed" and b["ledger_net"] == 4852
    assert all(_census(st, k) == 0 for k in NEW_NAMES) and _memo(p) is None and _hx(b) is None


# ------------------------------------------------ LOW: the hold is keyed by whale AND condition


def test_review_low_another_whales_hand_exit_on_the_condition_holds_nothing_of_ours(monkeypatch):
    """The memo key is '<whale>:<condition_id>': an entry for another whale
    on the same condition is not this whale's hold -- the candidate opens
    as today (one book), none of the four names."""
    _shorts_on(monkeypatch)
    _reset()
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {f"other:{CID}": _memo_entry(book_id=9)}
    v = _v1317(0)
    st = _tick(p, v, http=_http_1317())
    assert all(_census(st, k) == 0 for k in NEW_NAMES) and len(p.books) == 1


def test_review_low_the_candidate_refusal_spends_no_mapping_read(monkeypatch):
    """Before the mapping means before the resolver's venue reads and its
    cache: neither map_venue_read nor map_cache_hit moves on a refused
    candidate (the source pin says so by text; this says it by the census)."""
    _shorts_on(monkeypatch)
    _reset()
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    v = _v1317(0)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held") == 1 and _census(st, "map_venue_read") == 0 and _census(st, "map_cache_hit") == 0
    assert _census(st, "unmapped") == 0 and _census(st, "map_reads_capped") == 0
