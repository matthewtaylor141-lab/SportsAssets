"""E7 review pins (2026-09-07): the candidate memos released by his fills,
persisted; the data-API wait measured.

Each pin answers one of the review brief's questions with the real code
driven through tick_once against the worker file's fakes and the E7
file's stamped pool. Passing pins are what holds; a strict xfail is a
defect the review found, as it stands on the E7 tree -- the docstring
says which finding and what the fix is. Mutant killers are named.
"""
import ast
import asyncio
import hashlib
import inspect

import pytest

from sportsassets import live_executor as le
from sportsassets import ratelimit
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import whale_exits as we
from tests.test_e7_cand_memo import KEY, _bbos, _dt, _empty, _map_unmapped_for, _spool
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _Http, _Venue, _armed, _census, _pool, _run, _tick,
)

# `_confirm_gone` at the base 20aacb3, as ast.get_source_segment hands it back
_CONFIRM_GONE_SHA256 = "1d733395bed30a431eb3b70bf9c5d50b00c4d72693b55ea75f0ee907761af8bc"


def _capped_rows(p):
    return [r["condition_id"] for r in p.cand_refusals if r["refusal"] == "cand_unread_capped"]


# ------------------------------------------------- Q1: money in motion

def test_review_q1_a_fill_stamped_after_the_memo_is_released_when_the_walk_reaches_it():
    """A fill of his stamped AFTER the memo's `at` -- chain or poll,
    however late it is ingested -- releases the memo the tick the walk
    reaches the market (the stamp is the query's own `last_ts`, no
    wake needed); an exit of his on a market we never opened is the
    same fill and releases the same way (the read then finds what the
    venue shows: still empty, memoised again on the new read)."""
    p = _spool(stamps={CID: _dt(NOW - 3000)})
    v = _empty()
    _tick(p, v)
    assert ml._no_mark_memo == {KEY: NOW}
    v.calls.clear()
    p.stamps[CID] = _dt(NOW + 5)                   # his SELL, 5 s after the tick began
    st = _tick(p, v, now=NOW + 40)
    assert _census(st, "cand_memo_released") == 1 and _bbos(v) == [SLUG]
    assert _census(st, "no_mark") == 1 and ml._no_mark_memo == {KEY: NOW + 40}
    # once memoised on the newer read, the same fill holds it (no loop of re-reads)
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 70)
    assert _census(st2, "cand_memo_released") == 0 and _bbos(v) == []


@pytest.mark.xfail(strict=True, reason="review LOW-1: the release compares the fill's OWN stamp with the "
                   "memo's at; a fill stamped before the read and ingested after it (the poll's lag, a "
                   "chain backfill) never releases without a wake -- the brief's 'lands' is the row's "
                   "detected_at, which the same query could hand back beside last_ts")
def test_review_q1_a_fill_stamped_before_the_memo_and_ingested_after_it_is_released_without_a_wake():
    p = _spool(stamps={CID: _dt(NOW - 3000)})
    v = _empty()
    _tick(p, v)                                     # the read at NOW: his newest fill hours old
    v.calls.clear()
    p.stamps[CID] = _dt(NOW - 3)                    # a fill 3 s BEFORE the read, landed after it
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "cand_memo_released") == 1 and _bbos(v) == [SLUG]


def test_review_q1_the_wake_covers_the_late_ingested_fill_only_while_the_process_lives():
    """The wake is process state (_WOKEN): a fill ingested late and
    woken is released on the next tick; the same fill woken just
    before a deploy is a memo the new process loads with the fill's
    old stamp -- held for its TTL (LOW-1's residue, bounded by the
    rail: 900 s no_mark, 3600 s unmapped at most)."""
    p = _spool(stamps={CID: _dt(NOW - 3000)})
    v = _empty()
    _tick(p, v)
    v.calls.clear()
    p.stamps[CID] = _dt(NOW - 3)
    ml._WOKEN.add(CID)
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "cand_memo_released") == 1 and _bbos(v) == [SLUG], "woken: released"
    # the deploy: the memo loaded, the wake gone
    ml._terminal_memo_loaded = False
    ml._no_mark_until.clear()
    ml._no_mark_memo.clear()
    p.state[ml._STATE_CAND_MEMO] = {"unmapped": [], "no_mark": [["rn1", CID, NOW + 930, NOW + 30]], "at": "x"}
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 60)
    assert _census(st2, "cand_no_mark_skipped") == 1 and _bbos(v) == []
    st3 = _tick(p, v, now=NOW + 930)
    assert _bbos(v) == [SLUG] and _census(st3, "cand_no_mark_skipped") == 0, "the rail bounds it"


def test_review_q1_the_wake_fires_for_his_sells_too():
    """Review LOW-2, closed by E9 (2026-09-07) by the road the E9 brief
    named rather than the review's FIX: the wake fires where the fill is
    WRITTEN (ingestion/pipeline.ingest_trade_result -> _mirror_wake ->
    live_executor._mirror_notify), for every newly inserted fill of a
    mirrored whale -- SELL and BUY, chain and poll, the copy probe on or
    off -- and again from _enrich when a chain row learns its condition;
    execute_copy still returns every SELL to mirror_exit above the
    hand-off, and the hand-off's own call stays where the spec 3.1 pins
    hold it (test_mirror_live_handoff fixes the gate block's text). The
    behaviour itself is driven in test_e9_fast_path."""
    from sportsassets.ingestion import pipeline
    src = inspect.getsource(le.execute_copy)
    assert src.count("_mirror_notify(") == 0, "the copy lane's SELL return is untouched"
    pipe = inspect.getsource(pipeline.ingest_trade_result)
    assert pipe.count("_mirror_wake(ev.whale_username, ev.condition_id)") == 1
    assert pipe.index('if not row["was_insert"]') < pipe.index("_mirror_wake(") < pipe.index("fresh = (ev.ts_epoch")
    body = inspect.getsource(pipeline._mirror_wake)
    assert "mirror_mode(" in body and "_mirror_notify(" in body and "copy_probe" not in body
    assert "_mirror_wake(ev.whale_username, ev.condition_id)" in inspect.getsource(pipeline._enrich)


# ------------------------------------------------------- Q2: the stamp

def test_review_q2_the_clocks_share_an_epoch_and_a_skew_is_bounded_by_its_own_width():
    """`at` is the worker's time.time() at the tick's start; the stamp
    is the fill's own timestamptz as an epoch float: one epoch. A venue
    clock BEHIND by S hides only the fills inside the S seconds before
    the read (held; the wake or the TTL); a venue clock AHEAD by S
    re-releases a fill inside the S seconds before the read exactly
    ONCE -- the new memo's `at` is past the stamp -- so neither sign
    makes the memo inert or permanent."""
    # behind: a fill 1 s before the tick's instant is held
    p = _spool(stamps={CID: _dt(NOW - 3000)})
    v = _empty()
    _tick(p, v)
    v.calls.clear()
    p.stamps[CID] = _dt(NOW - 1)
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "cand_no_mark_skipped") == 1 and _bbos(v) == []
    # ahead: a fill stamped 1 s after the tick's instant is released once, then held
    p.stamps[CID] = _dt(NOW + 1)
    st2 = _tick(p, v, now=NOW + 60)
    assert _census(st2, "cand_memo_released") == 1 and _bbos(v) == [SLUG] and ml._no_mark_memo == {KEY: NOW + 60}
    v.calls.clear()
    st3 = _tick(p, v, now=NOW + 90)
    assert _census(st3, "cand_memo_released") == 0 and _census(st3, "cand_no_mark_skipped") == 1 and _bbos(v) == []
    # the epoch: an aware timestamptz and the worker's float agree to the second
    assert ms._stamp_epoch(_dt(NOW + 1)) == pytest.approx(NOW + 1, abs=1e-3)


# ---------------------------------------------------- Q3: the no_mark write

def test_review_q3_the_no_mark_memo_needs_open_and_no_mark_an_off_ladder_mark_is_read_again():
    """Kills M05. `rules.mirror_target` names `no_mark` for a mark OFF
    THE LADDER too (0.995: a decided match the venue still lists OPEN
    and quoted); that read had a mark, so `_memo_no_mark` writes
    nothing and the market is read again next tick, as before E7."""
    p = _pool()
    v = _Venue(bid=0.99, ask=0.995)
    st = _tick(p, v)
    assert _census(st, "no_mark") == 1 and st["venue_state"] == "MARKET_STATE_OPEN", st["census"]
    assert ml._no_mark_until == {} and ml._no_mark_memo == {}
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert _bbos(v) == [SLUG] and _census(st2, "cand_no_mark_skipped") == 0


def test_review_q3_an_open_book_whose_ask_is_off_the_unit_interval_reads_as_no_mark_and_is_memoised():
    """LOW-4 (accept): `_mark_of` hands None for a quote whose ask is
    not inside (0, 1) -- bid 0.50 / ask 1.00 -- so the OPEN read is
    the no_mark fact and memoised like an empty book; nothing could
    open at that quote, and his fill releases it."""
    p = _pool()
    st = _tick(p, _Venue(bid=0.50, ask=1.0))
    assert _census(st, "no_mark") == 1 and ml._no_mark_until == {KEY: NOW + ml.NO_MARK_TTL_S}


# ----------------------------------------------------------- Q4: growth

def test_review_q4_a_market_he_entered_at_first_sight_and_the_venue_lists_20_minutes_later_opens_at_the_next_base_read():
    """Review MEDIUM-2, folded (this pin carried the pre-fold arithmetic:
    read at 0, grown to 1800 at 900, the book opening at 2700 -- 25 min
    after a listing at 1200). With his fill inside the hour the memo
    keeps the base TTL: read at 0 (unmapped, 900), at 900 (unmapped
    again, still 900), the venue lists it at 1200, read again at 1800 --
    the book opens 10 minutes after the listing, the pre-E7 cadence."""
    orig = ms.map_market
    p = _spool(stamps={CID: _dt(NOW - 100)})
    v = _Venue()
    ms.map_market = _map_unmapped_for({CID})
    try:
        _tick(p, v)
        st = _tick(p, v, now=NOW + 900)
        assert _census(st, "unmapped") == 1 and ml._unmapped_memo == {KEY: (NOW + 900, 900.0)}, "no growth inside the hour"
    finally:
        ms.map_market = orig                          # the venue lists it at NOW + 1200
    st2 = _tick(p, v, now=NOW + 1200)
    assert not p.books and _census(st2, "map_venue_read") == 0, "inside the base TTL: not read"
    st3 = _tick(p, v, now=NOW + 1800)
    assert p.books and _census(st3, "cand_memo_released") == 0, "read at the base TTL: the book opens"


def test_review_q4_a_market_he_entered_inside_the_hour_keeps_the_base_ttl():
    """Review MEDIUM-2, folded: no growth while his newest fill on the
    market is inside UNMAPPED_TTL_MAX_S -- a market he entered inside the
    hour keeps the base TTL, listed later or not."""
    orig = ms.map_market
    p = _spool(stamps={CID: _dt(NOW - 100)})
    v = _Venue()
    ms.map_market = _map_unmapped_for({CID})
    try:
        _tick(p, v)
        _tick(p, v, now=NOW + 900)
    finally:
        ms.map_market = orig
    _tick(p, v, now=NOW + 1800)
    assert p.books, "his fill 100 s before the first read: read again at the base TTL"


def test_review_q4_the_growths_wake_clause_is_dead_behind_the_walks_release():
    """M08 is equivalent: the walk releases a woken memo BEFORE the
    read, so `_memo_unmapped` never sees a standing entry for a woken
    market; the clause is harmless and the rule holds either way."""
    orig = ms.map_market
    ms.map_market = _map_unmapped_for({CID})
    try:
        p = _spool(stamps={CID: _dt(NOW - 3000)})
        v = _Venue()
        _tick(p, v)
        ml._WOKEN.add(CID)
        st = _tick(p, v, now=NOW + 900)
        assert _census(st, "cand_memo_released") == 0 and _census(st, "unmapped") == 1, "expired: popped silently"
        assert ml._unmapped_memo == {KEY: (NOW + 900, 900.0)}, "the base: the release emptied prev"
    finally:
        ms.map_market = orig


# ------------------------------------------------------- Q5: persistence

@pytest.mark.xfail(strict=True, reason="review MEDIUM-1: the persisted unmapped memo carries verdicts that are "
                   "facts about the DEPLOY (map_source_unverified reads MIRROR_LIVE_MAP_SRC, a module constant; "
                   "the grammar class's certification) across the deploy that changes them, for up to 3600 s "
                   "grown -- before E7 a deploy re-read them; the fix is a resolver signature in the key, the "
                   "unmapped list dropped at boot when it differs (fail closed: read)")
def test_review_q5_a_persisted_deploy_dependent_verdict_is_re_read_by_the_deploy_that_changed_it(monkeypatch):
    async def _newlane(pool, fills, pmus=None, **kw):
        return {"us_slug": SLUG, "long_asset": M, "other_asset": N, "source": "newlane"}
    monkeypatch.setattr(ms, "map_market", _newlane)
    p = _spool(stamps={CID: _dt(NOW - 3000)})
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "map_source_unverified") == 1 and ml._unmapped_until == {KEY: NOW + 900.0}
    assert p.state[ml._STATE_CAND_MEMO]["unmapped"][0][:2] == ["rn1", CID], "persisted"
    # the deploy that certifies the lane: a new process, the same key
    monkeypatch.setattr(ml, "MIRROR_LIVE_MAP_SRC", frozenset(ml.MIRROR_LIVE_MAP_SRC | {"newlane"}))
    ml._terminal_memo_loaded = False
    ml._unmapped_until.clear()
    ml._unmapped_memo.clear()
    _tick(p, v, now=NOW + 120)
    assert p.books, "the lane is certified: the market he holds is read and opened"


def test_review_q5_a_hand_written_key_skips_a_live_market_at_most_its_rail_and_a_longer_one_not_at_all():
    """A no_mark entry can hold a live market for at most NO_MARK_TTL_S
    past its `at` (and `at` may not be ahead of now); an entry longer
    than the rail -- or than a LOWERED rail -- is dropped and the
    market read at once. Never forever, never nothing."""
    ml._terminal_memo_loaded = False
    p = _spool()
    p.state[ml._STATE_CAND_MEMO] = {"unmapped": [], "no_mark": [["rn1", CID, NOW + 900, NOW]], "at": "x"}
    v = _Venue()
    st = _tick(p, v, now=NOW + 899)
    assert _census(st, "cand_no_mark_skipped") == 1 and not p.books
    _tick(p, v, now=NOW + 900)
    assert p.books, "the rail ran: read, and quoted, a book"
    # the lowered rail against a persisted 900 s memo: dropped, read at once
    ml._terminal_memo_loaded = False
    ml._no_mark_until.clear()
    ml._no_mark_memo.clear()
    orig = ml.NO_MARK_TTL_S
    ml.NO_MARK_TTL_S = 300.0
    try:
        p2 = _spool()
        p2.state[ml._STATE_CAND_MEMO] = {"unmapped": [], "no_mark": [["rn1", CID, NOW + 900, NOW]], "at": "x"}
        st2 = _tick(p2, _Venue())
        assert p2.books and _census(st2, "cand_no_mark_skipped") == 0 and ml._no_mark_until == {}
    finally:
        ml.NO_MARK_TTL_S = orig


def test_review_q5_the_boot_read_waits_for_the_pool_and_is_made_once_it_is_up():
    """A pool not up at the deploy's own moment: the table guard
    refuses the tick BEFORE the boot read, nothing is written
    (MEDIUM-5's guard), and the read is made on the first tick the pool
    answers -- the persisted memo then in force."""
    ml._terminal_memo_loaded = False
    p = _spool()
    p.state[ml._STATE_CAND_MEMO] = {"unmapped": [], "no_mark": [["rn1", CID, NOW + 700, NOW - 200]], "at": "x"}
    p.tables_absent = True
    v = _empty()
    st = _tick(p, v)
    assert st["status"] == "degraded" and ml._terminal_memo_loaded is False and ml._no_mark_until == {}
    assert not [x for x in p.sent if "ml-state-write" in x[1] and x[2][0] == ml._STATE_CAND_MEMO]
    p.tables_absent = False
    st2 = _tick(p, v, now=NOW + 30)
    assert ml._terminal_memo_loaded is True and ml._no_mark_until == {KEY: NOW + 700.0}
    assert _census(st2, "cand_no_mark_skipped") == 1 and _bbos(v) == []


def test_review_q5_an_until_with_no_at_beside_it_is_released_by_any_readable_stamp():
    """Kills M03. An `_unmapped_until` entry with no `at` (written by
    hand, or the pre-E7 shape): fail closed toward the read -- any
    readable stamp releases it; no stamp holds it (no evidence)."""
    orig = ms.map_market
    ms.map_market = _map_unmapped_for({CID})
    try:
        p = _spool(stamps={CID: None})
        v = _Venue()
        ml._unmapped_until[KEY] = NOW + 900
        st = _tick(p, v)
        assert _census(st, "cand_memo_released") == 0 and _census(st, "map_venue_read") == 0, "no stamp: held"
        p.stamps[CID] = _dt(NOW - 3000)
        st2 = _tick(p, v, now=NOW + 30)
        assert _census(st2, "cand_memo_released") == 1 and _census(st2, "map_venue_read") == 1
        assert ml._unmapped_memo == {KEY: (NOW + 30, 900.0)}
    finally:
        ms.map_market = orig


def test_review_q5_an_expired_unmapped_entry_is_dropped_from_the_persisted_snapshot():
    """Kills M22 (the builder's pin covers the no_mark list only)."""
    ml._unmapped_until[KEY] = NOW - 1
    ml._unmapped_memo[KEY] = (NOW - 901, 900.0)
    ml._unmapped_until[("rn1", "0xlive")] = NOW + 100
    ml._unmapped_memo[("rn1", "0xlive")] = (NOW - 800, 900.0)
    snap = ml._cand_memo_snapshot(NOW)
    assert [e[1] for e in snap["unmapped"]] == ["0xlive"]


# ------------------------------------------------- Q6: the walk's arithmetic

def test_review_q6_memo_skips_spend_no_slot_so_the_walk_passes_forty_memoised_markets_under_a_cap_of_five(monkeypatch):
    """A memoised market costs the walk nothing: 45 candidates, the
    first 40 memoised, a cap of 5 -- the forty are passed without a
    slot, the five behind them are read, no `capped_tick`, no
    `cand_unread_capped` row, and the memo skips are the census's only
    trace (no refusal row, by design like `cand_terminal_skipped`).
    With the forty BEHIND the five the cap check at the top of the
    loop stops the walk first (`capped_tick`, the cursor kept, the
    tail never visited): pre-E7 arithmetic, nothing lost -- next tick
    resumes after the cursor and the memoised tail costs nothing."""
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 5)
    conds = [f"c{i}" for i in range(45)]
    p = _spool(conds=conds)
    for c in conds[:40]:
        ml._no_mark_until[("rn1", c)] = NOW + 900
        ml._no_mark_memo[("rn1", c)] = NOW - 100
    st = _tick(p, _Venue())
    assert st["reads"] == 5 and st.get("capped_tick") is not True and not _capped_rows(p)
    assert _census(st, "cand_no_mark_skipped") == 40 and _census(st, "cand_unread_capped") == 0
    assert not [r for r in p.cand_refusals if r["refusal"] == "cand_no_mark_skipped"]
    assert ml._cand_cursor.get("rn1") is None, "the walk reached the end of the list"
    # the forty behind the five: the cap stops the walk at the top of the loop
    for c in conds:
        ml._no_mark_until.pop(("rn1", c), None)
        ml._no_mark_memo.pop(("rn1", c), None)
    for c in conds[5:]:
        ml._no_mark_until[("rn1", c)] = NOW + 900
        ml._no_mark_memo[("rn1", c)] = NOW - 100
    p2 = _spool(conds=conds)
    st2 = _tick(p2, _Venue(), now=NOW + 1)
    assert st2["reads"] == 5 and st2.get("capped_tick") is True and not _capped_rows(p2)
    assert _census(st2, "cand_no_mark_skipped") == 0 and ml._cand_cursor.get("rn1") == "c4"
    st3 = _tick(p2, _Venue(), now=NOW + 2)
    assert st3["reads"] == 5 and _census(st3, "cand_no_mark_skipped") == 40, "resumed after c4: the tail costs nothing"


def test_review_q6_a_woken_memoised_market_the_cap_cut_is_named(monkeypatch):
    """Kills M30. Three woken, memoised markets under a cap of two: the
    first two are released and read, the third is what the cap cut --
    the wake would have released it, so it is named."""
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 2)
    conds = ["c0", "c1", "c2"]
    p = _spool(conds=conds, stamps={c: _dt(NOW - 3000) for c in conds})
    for c in conds:
        ml._no_mark_until[("rn1", c)] = NOW + 900
        ml._no_mark_memo[("rn1", c)] = NOW - 100
    ml._WOKEN.update(conds)
    st = _tick(p, _empty(), now=NOW + 1)
    assert st["reads"] == 2 and _census(st, "cand_memo_released") == 2 and st.get("capped_tick") is True
    assert _capped_rows(p) == ["c2"], p.cand_refusals
    assert ml._no_mark_until.get(("rn1", "c2")) == NOW + 900, "naming drops nothing: the walk's release does"


# --------------------------------------------------------------- Q7: part B

def test_review_q7_a_call_cut_short_in_the_wait_is_all_wait_and_confirm_gone_is_byte_identical(monkeypatch):
    class _Slow:
        async def wait(self):
            await asyncio.sleep(0.3)
    monkeypatch.setattr(ratelimit, "_throttle", _Slow())
    d: dict = {}
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        _run(asyncio.wait_for(we.market_positions(_Http(), "0xabc", CID, timing=d), 0.05))
    assert d["wait"] >= 0.05 and d["req"] == 0.0, d
    src = inspect.getsource(we)
    seg = next(ast.get_source_segment(src, n) for n in ast.parse(src).body
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "_confirm_gone")
    assert hashlib.sha256(seg.encode()).hexdigest() == _CONFIRM_GONE_SHA256
