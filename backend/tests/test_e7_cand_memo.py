"""E7 (2026-09-07): the candidate rotation stops re-reading what has not
changed; the data-API wait measured.

Owner 18:3xZ / 19:1xZ: "Need everything running and running at mirror
to him." With E6 live the tick was 47.9 / 51.3 s and the candidate
stage 25.9 / 13.3 s with map_reads_capped 114 / 86 (mirror_tick_2116 /
_2120): 100+ markets waiting on a resolver read, and the 24 h refusal
census (cand_refusals_2117) said the reads were the same answers every
rotation -- `unmapped` 276 markets / 1,667 rows (a paced resolver call
each, e.g. por-est-aro-2026-09-07-total-0pt5 unmapped @20:04 @20:20
@20:35 @20:51 @21:09) and `no_mark` 179 markets / 1,121 rows / $1.01M
(the morning's tennis, the matches over, the venue OPEN with an empty
book, a quote read AND a per-market read at every slot, no memo).
Driven end to end through tick_once against the worker file's fakes
(its autouse rails are imported), plus a stamped pool that hands the
walk the `last_ts` the candidate order ranks on.

THE RULE, EXACTLY (pinned below): a candidate whose quote read found an
OPEN market with no mark (rules.mirror_target's `no_mark` for a
candidate; never a HALTED / SUSPENDED / PREOPEN read, never a read that
raised or named no state) is memoised per (whale, cid) for
NO_MARK_TTL_S = capped_env("MIRROR_NO_MARK_TTL_S", 900, floor 60) and
skipped `cand_no_mark_skipped` (no slot, no venue call) until it runs.
Both candidate memos (unmapped, no_mark) carry the read's `at`; at the
walk a memoised market whose newest fill of his (the stamp the
candidate order already ranks on -- no new read) is NEWER than that
`at`, or that woke this tick, is read this tick, the memo dropped
(`cand_memo_released`). The unmapped memo's TTL DOUBLES on a re-read
with no fill of his since the last read, 900 -> 1800 -> 3600 (the
cap, a constant), and resets to the base on a release; the no_mark TTL
is flat. `map_reads_capped` is never memoised; a book's read writes
neither memo. Both persist under `mirror_cand_memo` (their own key:
E6 pins `mirror_terminal_memo`'s exact shape) under E6's rules --
bounded, once per 60 s, on change, read once at boot, malformed or
unreadable = read. Part B: `short.data_api` = {books_data_wait,
books_data_req, data_rps} beside the (exactly pinned) timing block.
"""
import inspect
import json
import logging
import time as _time
from datetime import datetime, timezone

import pytest

from sportsassets import ratelimit
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import whale_exits as we
from tests.test_e6_tick_budget import TIMING_KEYS
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _Http, _NoThrottle, _Pool, _Venue, _armed, _census, _fill, _flat, _his, _kinds,
    _pool, _ratio_fills, _run, _tick,
)

KEY = ("rn1", CID)
OPEN = "MARKET_STATE_OPEN"
DATA_API_KEYS = ("books_data_wait", "books_data_req", "data_rps")


def _dt(ts):
    """A fill stamp as the driver hands it: an aware timestamptz."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class _StampedPool(_Pool):
    """The worker's pool with the `last_ts` the candidate order ranks
    on, per condition (the fake's query never carried it): what the
    walk judges a memo's release against. Absent = None = no evidence."""

    def __init__(self, *a, stamps=None, **kw):
        super().__init__(*a, **kw)
        self.stamps = dict(stamps or {})

    async def fetch(self, sql, *a):
        s = _flat(sql)
        if "SELECT t.condition_id, max(t.ts) AS last_ts" in s:
            self.queries.append((s, a))
            conds = self.conds if self.conds is not None else ([CID] if self.fills else [])
            return [{"condition_id": c, "last_ts": self.stamps.get(c)} for c in conds]
        return await super().fetch(sql, *a)


def _spool(stamps=None, **kw):
    kw.setdefault("fills", _his())
    kw.setdefault("snap", {M: 300.0, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _StampedPool(stamps=stamps, **kw)


def _empty():
    """The venue OPEN with no makers: the U10 per-market refusal."""
    return _Venue(bid=None, ask=None)


def _bbos(v):
    return [c[1] for c in v.calls if c[0] == "bbo"]


def _memo_writes(p):
    return [x for x in p.sent if "ml-state-write" in x[1] and x[2][0] == ml._STATE_CAND_MEMO]


def _memo_reads(p):
    return [x for x in p.sent if "SELECT value FROM ingestion_state" in x[1] and x[2][0] == ml._STATE_CAND_MEMO]


def _busy(seconds):
    end = _time.monotonic() + seconds
    while _time.monotonic() < end:
        pass


def _map_unmapped_for(cids):
    """ms.map_market that maps nothing for `cids` (one resolver read
    each) and the fixture's way for the rest."""
    orig = ms.map_market

    async def _map(pool, fills, pmus=None, **kw):
        if kw.get("condition_id") in cids:
            kw["budget"].reads += 1
            kw["out"]["venue_reads"] = 1
            return None
        return await orig(pool, fills, pmus, **kw)
    return _map


# ---------------------------------------------------------------- constants

def test_e7_the_constants_and_the_env_can_only_lower_the_no_mark_ttl(monkeypatch):
    assert ml.NO_MARK_TTL_S == 900.0 and ml.UNMAPPED_TTL_MAX_S == 3600.0 and ms.UNMAPPED_TTL_S == 900.0
    assert ml._STATE_CAND_MEMO == "mirror_cand_memo" and ml._STATE_TERMINAL_MEMO == "mirror_terminal_memo"
    for env, want in (("300", 300.0), ("3600", 900.0), ("10", 60.0), ("0", 60.0), ("x", 900.0), ("", 900.0)):
        monkeypatch.setenv("MIRROR_NO_MARK_TTL_S", env)
        assert rules.capped_env("MIRROR_NO_MARK_TTL_S", 900.0, floor=60.0) == want, env
    monkeypatch.delenv("MIRROR_NO_MARK_TTL_S", raising=False)
    src = inspect.getsource(ml)
    assert 'capped_env("MIRROR_NO_MARK_TTL_S", 900.0, floor=60.0)' in src
    # the growth cap is a constant: nothing to lower under the base
    assert "UNMAPPED_TTL_MAX_S = 3600.0" in src and 'capped_env("MIRROR_UNMAPPED' not in src
    # the base TTL, the pacing and the throttle's rate are untouched
    assert "READ_PACING_S = 0.35" in inspect.getsource(ms) and "UNMAPPED_TTL_S = 900.0" in inspect.getsource(ms)
    assert "data_api_max_rps" not in inspect.getsource(ml._market_snap)


def test_e7_the_census_names_sit_before_e6s_key_and_the_pinned_tail_stands():
    keys = ml.CENSUS_KEYS
    assert keys[-4:] == ("cand_no_mark_skipped", "cand_memo_released", "book_quiet_skipped", "cand_terminal_skipped")
    for k in ("cand_no_mark_skipped", "cand_memo_released"):
        assert keys.count(k) == 1 and keys.index(k) >= api_app._DETAIL_MAX_KEYS and ml._new_stats()["census"][k] == 0


# --------------------------------------------- part A: the no_mark memo

def test_e7_a_no_mark_candidate_is_memoised_and_not_re_read_inside_the_ttl():
    p = _spool()
    v = _empty()
    st = _tick(p, v)
    assert _census(st, "no_mark") == 1 and _bbos(v) == [SLUG] and st["snap_market_reads"] == 1 and not p.books
    assert ml._no_mark_until == {KEY: NOW + 900.0} and ml._no_mark_memo == {KEY: NOW}
    assert [r["refusal"] for r in p.cand_refusals] == ["no_mark"]
    # inside the TTL: no quote read, no per-market read, no slot, no row
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert _census(st2, "cand_no_mark_skipped") == 1 and _census(st2, "no_mark") == 0
    assert _bbos(v) == [] and st2["snap_market_reads"] == 0 and st2["reads"] == 0
    assert len(p.cand_refusals) == 1 and st2.get("capped_tick") is not True
    st3 = _tick(p, v, now=NOW + 899)
    assert _census(st3, "cand_no_mark_skipped") == 1 and _bbos(v) == []
    # the TTL ran: read again, memoised again on THIS read (flat TTL)
    st4 = _tick(p, v, now=NOW + 900)
    assert _census(st4, "cand_no_mark_skipped") == 0 and _census(st4, "no_mark") == 1 and _bbos(v) == [SLUG]
    assert ml._no_mark_until == {KEY: NOW + 1800.0} and ml._no_mark_memo == {KEY: NOW + 900}
    # a market that fills in is read on its turn and opens a book
    v2 = _Venue()
    st5 = _tick(p, v2, now=NOW + 1800)
    assert p.books and _census(st5, "no_mark") == 0 and ml._no_mark_until == {KEY: NOW + 1800.0}, \
        "the memo is not touched by a read that found a mark; it simply expired"


def test_e7_his_newer_fill_releases_the_no_mark_memo_and_the_market_is_read():
    p = _spool(stamps={CID: _dt(NOW - 3000)})       # his fixture fill, older than the read
    v = _empty()
    _tick(p, v)
    assert ml._no_mark_memo == {KEY: NOW}
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert _census(st2, "cand_no_mark_skipped") == 1 and _bbos(v) == [], "a stamp older than the read: held"
    # a fill of his NEWER than the memo's at: the memo dropped, the
    # market read this tick, its own refusal named and memoised again
    p.stamps[CID] = _dt(NOW + 10)
    st3 = _tick(p, v, now=NOW + 60)
    assert _census(st3, "cand_memo_released") == 1 and _census(st3, "cand_no_mark_skipped") == 0
    assert _bbos(v) == [SLUG] and _census(st3, "no_mark") == 1 and st3["snap_market_reads"] == 1
    assert ml._no_mark_memo == {KEY: NOW + 60} and ml._no_mark_until == {KEY: NOW + 960.0}
    # the same stamp is now OLDER than the new memo's at: held again
    v.calls.clear()
    st4 = _tick(p, v, now=NOW + 90)
    assert _census(st4, "cand_memo_released") == 0 and _census(st4, "cand_no_mark_skipped") == 1 and _bbos(v) == []
    # released and the market now quoted: a book opens on that very tick
    p.stamps[CID] = _dt(NOW + 100)
    st5 = _tick(p, _Venue(), now=NOW + 120)
    assert _census(st5, "cand_memo_released") == 1 and p.books and ml._no_mark_until == {}


def test_e7_a_woken_market_releases_the_memo_whatever_the_stamp_says():
    p = _spool(stamps={CID: _dt(NOW - 3000)})
    v = _empty()
    _tick(p, v)
    v.calls.clear()
    ml._WOKEN.add(CID)                                  # a fill ingested late carries an old stamp
    st2 = _tick(p, v, now=NOW + 30)
    assert _census(st2, "cand_memo_released") == 1 and _bbos(v) == [SLUG] and st2["woken"] == [CID]
    assert ml._no_mark_memo == {KEY: NOW + 30}


def test_e7_a_stamp_the_walk_cannot_read_releases_nothing_and_grows_nothing(monkeypatch):
    # the plain fake: no `last_ts` at all -- no evidence, the memo's own TTL stands
    p = _pool()
    v = _empty()
    _tick(p, v)
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert _census(st2, "cand_memo_released") == 0 and _census(st2, "cand_no_mark_skipped") == 1 and _bbos(v) == []
    # a naive datetime, a string, a bool, a NaN: not a stamp
    for bad in (datetime.fromtimestamp(NOW + 10), "2026-09-07T21:00:00Z", True, float("nan"), None):
        assert ms._stamp_epoch(bad) is None, bad
    assert ms._stamp_epoch(_dt(NOW + 10)) == pytest.approx(NOW + 10) and ms._stamp_epoch(12.5) == 12.5
    p2 = _spool(stamps={CID: "junk"})
    _tick(p2, _empty(), now=NOW + 100)
    v2 = _empty()
    st3 = _tick(p2, v2, now=NOW + 130)
    assert _census(st3, "cand_no_mark_skipped") == 1 and _bbos(v2) == []
    # the unmapped memo re-read with a fill of no readable stamp among
    # his rows: no growth (a fill with no stamp counts as one). A fresh
    # world: the no_mark memo of the reads above cleared
    ml._no_mark_until.clear()
    ml._no_mark_memo.clear()
    monkeypatch.setattr(ms, "map_market", _map_unmapped_for({CID}))
    p3 = _pool(fills=[_fill(M, "BUY", 300.0, 0.31, None)])
    _tick(p3, _Venue(), now=NOW + 200)
    assert ml._unmapped_memo == {KEY: (NOW + 200, 900.0)}
    _tick(p3, _Venue(), now=NOW + 1100)
    assert ml._unmapped_memo == {KEY: (NOW + 1100, 900.0)}, "an unreadable stamp is a fill: the base TTL"


# --------------------------------------------- part A: the unmapped memo

def test_e7_an_unmapped_markets_ttl_doubles_without_a_fill_and_resets_on_one_capped_at_3600(monkeypatch):
    monkeypatch.setattr(ms, "map_market", _map_unmapped_for({CID}))
    p = _spool(stamps={CID: _dt(NOW - 3000)})
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "unmapped") == 1 and _census(st, "map_venue_read") == 1 and _bbos(v) == []
    assert ml._unmapped_until == {KEY: NOW + 900.0} and ml._unmapped_memo == {KEY: (NOW, 900.0)}
    st2 = _tick(p, v, now=NOW + 30)
    assert _census(st2, "unmapped") == 0 and _census(st2, "map_venue_read") == 0, "memoised: no resolver call"
    # re-read with nothing of his since: 900 -> 1800 -> 3600 -> 3600
    st3 = _tick(p, v, now=NOW + 900)
    assert _census(st3, "unmapped") == 1 and ml._unmapped_memo == {KEY: (NOW + 900, 1800.0)}
    assert ml._unmapped_until == {KEY: NOW + 2700.0}
    _tick(p, v, now=NOW + 2000)
    assert ml._unmapped_memo == {KEY: (NOW + 900, 1800.0)}, "inside the grown TTL: held"
    st4 = _tick(p, v, now=NOW + 2700)
    assert _census(st4, "unmapped") == 1 and ml._unmapped_memo == {KEY: (NOW + 2700, 3600.0)}
    st5 = _tick(p, v, now=NOW + 6300)
    assert _census(st5, "unmapped") == 1 and ml._unmapped_memo == {KEY: (NOW + 6300, 3600.0)}, "the cap"
    assert ml._unmapped_until == {KEY: NOW + 9900.0}
    # his newer fill: released, read at once, the TTL back at the base
    p.stamps[CID] = _dt(NOW + 6400)
    st6 = _tick(p, v, now=NOW + 6430)
    assert _census(st6, "cand_memo_released") == 1 and _census(st6, "unmapped") == 1
    assert ml._unmapped_memo == {KEY: (NOW + 6430, 900.0)} and ml._unmapped_until == {KEY: NOW + 7330.0}
    # a fill of his among the rows the re-read holds resets it too
    p.stamps[CID] = None
    p.fills.append(_fill(M, "BUY", 10.0, 0.31, NOW + 7000))
    _tick(p, v, now=NOW + 7330)
    assert ml._unmapped_memo == {KEY: (NOW + 7330, 900.0)}, "a fill since the last read: the base"
    # an entry cleared by hand restarts at the base (nothing to grow from)
    ml._unmapped_until.clear()
    p.fills.pop()
    _tick(p, v, now=NOW + 7400)
    assert ml._unmapped_memo == {KEY: (NOW + 7400, 900.0)}


def test_e7_every_writer_of_the_unmapped_memo_goes_through_the_one_writer_and_map_reads_capped_never(monkeypatch):
    src = inspect.getsource(ml._tick_candidate)
    assert src.count("_memo_unmapped(t, w, cid, fills)") == 4, "unmapped, map_source_unverified, grammar, long_token_unknown"
    assert "_unmapped_until[" not in src and src.index('return "map_reads_capped"') < src.index("_memo_unmapped(")
    whole = inspect.getsource(ml)
    assert whole.count("_unmapped_until[key] = ") == 1 and whole.count("_no_mark_until[key] = ") == 1
    assert whole.count("_memo_no_mark(t, w, cid, r)") == 1 and "_memo_no_mark(t, w, cid, r)" in src

    async def _capped(pool, fills, pmus=None, **kw):
        kw["out"]["refusal"] = "map_reads_capped"
        return None
    monkeypatch.setattr(ms, "map_market", _capped)
    p = _pool()
    st = _tick(p, _Venue())
    assert _census(st, "map_reads_capped") == 1 and ml._unmapped_until == {} and ml._unmapped_memo == {}
    assert ml._no_mark_until == {} and ml._cand_memo_snapshot(NOW) == {"unmapped": [], "no_mark": []}


def test_e7_env_lowers_the_no_mark_ttl_and_the_memo_honours_it(monkeypatch):
    monkeypatch.setattr(ml, "NO_MARK_TTL_S", 120.0)
    p = _spool()
    v = _empty()
    _tick(p, v)
    assert ml._no_mark_until == {KEY: NOW + 120.0}
    v.calls.clear()
    _tick(p, v, now=NOW + 119)
    assert _bbos(v) == []
    _tick(p, v, now=NOW + 120)
    assert _bbos(v) == [SLUG]


# ------------------------------------ part A: what never writes the memo

def test_e7_a_books_read_never_writes_either_memo():
    p = _pool()
    p.add_book(ledger=300)
    v = _Venue(bid=None, ask=None, held={SLUG: 300})
    st = _tick(p, v)
    assert _census(st, "no_mark") >= 1 and _bbos(v) == [SLUG]
    assert ml._no_mark_until == {} and ml._unmapped_until == {} and ml._no_mark_memo == {} and ml._unmapped_memo == {}
    _tick(p, v, now=NOW + 30)
    assert ml._no_mark_until == {} and _bbos(v) == [SLUG, SLUG], "the book is read every tick, as before"
    for name in ("_memo_no_mark", "_memo_unmapped", "_no_mark_until", "_unmapped_until"):
        assert name not in inspect.getsource(ml._tick_book), name
    whole = inspect.getsource(ml)
    assert whole.count("_memo_no_mark(t, ") == 1 and "_memo_no_mark(t, w, cid, r)" in inspect.getsource(ml._tick_candidate)


def test_e7_a_halted_unread_or_raised_no_mark_is_never_memoised_an_open_bid_only_book_is():
    # HALTED: the candidate's mirror_target refusal is still `no_mark`, the memo empty (those reopen)
    p = _pool()
    st = _tick(p, _Venue(state="MARKET_STATE_HALTED"))
    assert _census(st, "venue_halted") == 1 and _census(st, "no_mark") == 1 and ml._no_mark_until == {}
    # a read that raised (no state)
    p = _pool()
    st = _tick(p, _Venue(raise_bbo=True))
    assert _census(st, "no_quote") == 1 and _census(st, "no_mark") == 1 and ml._no_mark_until == {}
    # empty with no state at all (the SDK-typed shape: not told from a halt)
    p = _pool()
    st = _tick(p, _Venue(bid=None, ask=None, state=None))
    assert _census(st, "no_quote") == 1 and ml._no_mark_until == {}
    # OPEN with a bid and no ask: no mark to read -- the same per-market fact, memoised
    p = _pool()
    st = _tick(p, _Venue(bid=0.30, ask=None))
    assert _census(st, "no_mark") == 1 and ml._no_mark_until == {KEY: NOW + 900.0}
    # OPEN and quoted: nothing memoised, a book opens
    ml._no_mark_until.clear()
    ml._no_mark_memo.clear()
    p = _pool()
    _tick(p, _Venue())
    assert p.books and ml._no_mark_until == {}


def test_e7_no_price_is_not_the_no_mark_fact_and_is_not_memoised():
    """`no_price` (:8239) is his LEVEL unreadable off his own fills --
    a fact about his fills, not the venue's book (the read had a
    mark); it changes exactly when a new fill of his lands. Not the
    same per-market fact: not memoised. `no_quote` is _bbo's name for
    a read that FAILED or an empty book naming no state: a transient
    or an untellable, never memoised either."""
    p = _pool(fills=[_fill(M, "BUY", 300.0, 0.0, NOW - 3000)])
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "no_price") == 1 and _census(st, "no_mark") == 0 and _bbos(v) == [SLUG]
    assert ml._no_mark_until == {} and ml._unmapped_until == {}
    v.calls.clear()
    _tick(p, v, now=NOW + 30)
    assert _bbos(v) == [SLUG], "read again next tick, as before"


def test_e7_a_memo_skipped_candidate_writes_no_refusal_row_and_the_released_read_writes_its_own():
    p = _spool(stamps={CID: _dt(NOW - 3000)})
    v = _empty()
    _tick(p, v)
    _tick(p, v, now=NOW + 30)
    _tick(p, v, now=NOW + 60)
    assert [r["refusal"] for r in p.cand_refusals] == ["no_mark"], "the skips write nothing"
    p.stamps[CID] = _dt(NOW + 70)
    st = _tick(p, _Venue(bid=0.30, ask=None), now=NOW + 90)
    assert _census(st, "cand_memo_released") == 1
    assert [r["refusal"] for r in p.cand_refusals] == ["no_mark"], "the same name inside the restamp window"
    p.stamps[CID] = _dt(NOW + 100)
    st = _tick(p, _Venue(), now=NOW + 120)
    assert _census(st, "cand_memo_released") == 1 and p.books, "released onto a quoted book: opened"
    assert ml._cand_refusal_last[KEY] == ("opened", NOW + 120) and ml._no_mark_until == {}


def test_e7_a_memoised_market_the_cap_left_unread_is_not_named_unless_his_fill_released_it(monkeypatch):
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 5)
    conds = [f"c{i}" for i in range(45)]
    p = _spool(conds=conds)
    v = _Venue()
    _tick(p, v)
    ml._no_mark_until[("rn1", "c44")] = NOW + 900
    ml._no_mark_memo[("rn1", "c44")] = NOW
    ml._unmapped_until[("rn1", "c43")] = NOW + 900
    ml._unmapped_memo[("rn1", "c43")] = (NOW, 900.0)
    ml._unmapped_until[("rn1", "c42")] = NOW + 900          # by hand, no `at` beside it
    p.cand_refusals.clear()
    ml._cand_refusal_last.clear()
    st2 = _tick(p, v, now=NOW + 1)
    unread2 = [r["condition_id"] for r in p.cand_refusals if r["refusal"] == "cand_unread_capped"]
    assert "c44" not in unread2 and "c43" not in unread2 and "c42" not in unread2 and len(unread2) == 37
    assert _census(st2, "cand_unread_capped") == 37 and st2["reads"] == 5
    # his newer fill on c44 and c42: the cap DID cut them -- named; c43 still held
    p.stamps.update({"c44": _dt(NOW + 5), "c42": _dt(NOW + 5), "c43": _dt(NOW - 5)})
    p.cand_refusals.clear()
    ml._cand_refusal_last.clear()
    st3 = _tick(p, v, now=NOW + 10)
    unread3 = [r["condition_id"] for r in p.cand_refusals if r["refusal"] == "cand_unread_capped"]
    assert "c44" in unread3 and "c42" in unread3 and "c43" not in unread3 and len(unread3) == 39
    assert ml._no_mark_until.get(("rn1", "c44")) == NOW + 900, "naming drops nothing: the walk's release does"
    assert _census(st3, "cand_memo_released") == 0


# --------------------------------------------- part A: the memos persist

def test_e7_both_memos_persist_under_their_own_key_bounded_once_per_60s_on_change(monkeypatch):
    monkeypatch.setattr(ms, "map_market", _map_unmapped_for({"0xun"}))
    p = _spool(conds=[CID, "0xun"])
    v = _empty()
    _tick(p, v)
    assert ml._unmapped_until == {("rn1", "0xun"): NOW + 900.0} and ml._no_mark_until == {KEY: NOW + 900.0}
    assert len(_memo_writes(p)) == 1 and not _memo_reads(p), "written this tick; the boot read was made"
    assert p.state[ml._STATE_CAND_MEMO] == {
        "unmapped": [["rn1", "0xun", round(NOW + 900, 1), round(NOW, 1), 900.0]],
        "no_mark": [["rn1", CID, round(NOW + 900, 1), round(NOW, 1)]], "at": ml._iso(NOW)}
    # unchanged: no second write; changed inside 60 s: not yet; at 60 s: written
    _tick(p, v, now=NOW + 30)
    assert len(_memo_writes(p)) == 1
    ml._no_mark_until[("rn1", "0xb")] = NOW + 500
    ml._no_mark_memo[("rn1", "0xb")] = NOW + 40
    _tick(p, v, now=NOW + 59)
    assert len(_memo_writes(p)) == 1
    _tick(p, v, now=NOW + 60)
    assert len(_memo_writes(p)) == 2
    assert [e[1] for e in p.state[ml._STATE_CAND_MEMO]["no_mark"]] == [CID, "0xb"], "the soonest to expire last"
    # an expired entry is dropped on write; an entry with no `at` beside it is not persisted
    ml._no_mark_until[("rn1", "0xnoat")] = NOW + 5000
    _tick(p, v, now=NOW + 520)
    assert [e[1] for e in p.state[ml._STATE_CAND_MEMO]["no_mark"]] == [CID] and len(_memo_writes(p)) == 3
    # the bound: at most _TERMINAL_MEMO_MAX per list, the soonest to expire dropped first
    for i in range(ml._TERMINAL_MEMO_MAX + 5):
        ml._unmapped_until[("rn1", f"0x{i}")] = NOW + 10 + i
        ml._unmapped_memo[("rn1", f"0x{i}")] = (NOW, 900.0)
    snap = ml._cand_memo_snapshot(NOW)
    assert set(snap) == {"unmapped", "no_mark"} and len(snap["unmapped"]) == ml._TERMINAL_MEMO_MAX
    assert snap["unmapped"][0][2] >= snap["unmapped"][-1][2] and all(len(e) == 5 for e in snap["unmapped"])
    # a failed write is logged and retried: the signature is kept only on success
    ml._unmapped_until.clear()
    ml._unmapped_memo.clear()
    p.raise_on.append(("ml-state-write", RuntimeError("down")))
    st = _tick(p, v, now=NOW + 600)
    assert not st["abandoned"] and ml._cand_memo_last["sig"] != ml._terminal_memo_sig(ml._cand_memo_snapshot(NOW + 600))
    p.raise_on.clear()
    _tick(p, v, now=NOW + 670)
    # (the cleared market was read again at NOW + 600 and memoised on that read)
    assert [e[1:] for e in p.state[ml._STATE_CAND_MEMO]["unmapped"]] == [["0xun", round(NOW + 1500, 1), round(NOW + 600, 1), 900.0]]
    assert len(_memo_writes(p)) == 5, "the failed attempt is in `sent` too; the retry landed"
    assert ml._cand_memo_last["sig"] == ml._terminal_memo_sig(ml._cand_memo_snapshot(NOW + 670))
    # the terminal memo's key keeps its exact E6 shape
    assert set(ml._terminal_memo_snapshot(NOW)) == {"cand", "book"}


def test_e7_the_memos_load_at_boot_and_a_deploy_no_longer_re_reads_a_memoised_market(monkeypatch):
    monkeypatch.setattr(ms, "map_market", _map_unmapped_for({"0xun"}))
    ml._terminal_memo_loaded = False
    p = _spool(conds=[CID, "0xun"])
    p.markets["0xun"] = {"closed": False, "resolved": False, "resolved_prices": None}
    p.state[ml._STATE_CAND_MEMO] = {
        "unmapped": [["rn1", "0xun", NOW + 500, NOW - 400, 900.0], ["rn1", "0xgone", NOW - 1, NOW - 901, 900.0]],
        "no_mark": [["rn1", CID, NOW + 700, NOW - 200], ["rn1", "0xgone2", NOW - 1, NOW - 901]],
        "at": "2026-09-07T21:00:00Z"}
    v = _empty()
    st = _tick(p, v)
    assert not st["abandoned"] and ml._terminal_memo_loaded is True
    assert ml._unmapped_until == {("rn1", "0xun"): NOW + 500.0} and ml._unmapped_memo == {("rn1", "0xun"): (NOW - 400.0, 900.0)}
    assert ml._no_mark_until == {KEY: NOW + 700.0} and ml._no_mark_memo == {KEY: NOW - 200.0}
    # the deploy re-read NOTHING memoised: no quote read, no resolver read, no per-market read
    assert _bbos(v) == [] and _census(st, "map_venue_read") == 0 and _census(st, "unmapped") == 0
    assert _census(st, "cand_no_mark_skipped") == 1 and st["snap_market_reads"] == 0 and st["reads"] == 0
    assert len(_memo_reads(p)) == 1 and not _memo_writes(p), "read once; what stands written is not rewritten"
    _tick(p, v, now=NOW + 30)
    assert len(_memo_reads(p)) == 1, "once per process"
    # the loaded `at` is what his fill is judged against after the deploy
    p.stamps[CID] = _dt(NOW - 100)
    st3 = _tick(p, v, now=NOW + 60)
    assert _census(st3, "cand_memo_released") == 1 and _bbos(v) == [SLUG]
    # and the loaded unmapped TTL is what the next re-read grows from
    st4 = _tick(p, v, now=NOW + 500)
    assert _census(st4, "unmapped") == 1 and ml._unmapped_memo[("rn1", "0xun")] == (NOW + 500, 1800.0)


@pytest.mark.parametrize("entry", [
    ["rn1", CID, NOW + 500, NOW - 400],                   # no ttl (the no_mark shape) on the unmapped list
    ["rn1", CID, NOW + 500, NOW - 400, 450.0],            # a TTL under the base
    ["rn1", CID, NOW + 500, NOW - 400, 7200.0],           # a TTL over the cap
    ["rn1", CID, NOW + 500, NOW - 1000, 900.0],           # until past at + ttl
    ["rn1", CID, NOW + 500, NOW + 100, 900.0],            # an `at` in the future
    ["rn1", CID, NOW - 1, NOW - 901, 900.0],              # expired
    ["rn1", CID, "x", NOW - 400, 900.0],                  # until unreadable
    ["rn1", CID, NOW + 500, None, 900.0],                 # at unreadable
    [1, CID, NOW + 500, NOW - 400, 900.0],                # not a whale name
    "junk", 7, None, [],
])
def test_e7_a_malformed_unmapped_entry_is_dropped_and_the_market_read(entry, monkeypatch):
    monkeypatch.setattr(ms, "map_market", _map_unmapped_for({CID}))
    ml._terminal_memo_loaded = False
    p = _spool()
    p.state[ml._STATE_CAND_MEMO] = {"unmapped": [entry], "no_mark": [], "at": "x"}
    st = _tick(p, _Venue())
    assert not st["abandoned"] and _census(st, "unmapped") == 1 and _census(st, "map_venue_read") == 1
    assert ml._unmapped_memo == {KEY: (NOW, 900.0)}, "read this tick, memoised on this read"


@pytest.mark.parametrize("entry", [
    ["rn1", CID, NOW + 500, NOW - 400, 900.0],            # the unmapped shape on the no_mark list
    ["rn1", CID, NOW + 500, NOW - 401],                   # until past at + NO_MARK_TTL_S
    ["rn1", CID, NOW + 500, NOW + 1],                     # an `at` in the future
    ["rn1", CID, NOW + 500, NOW + 600],                   # at past until
    ["rn1", CID, NOW - 1, NOW - 901],                     # expired
    ["rn1", None, NOW + 500, NOW - 400],                  # not a market id
    "junk", 7, None, [],
])
def test_e7_a_malformed_no_mark_entry_is_dropped_and_the_market_read(entry):
    ml._terminal_memo_loaded = False
    p = _spool()
    p.state[ml._STATE_CAND_MEMO] = {"unmapped": [], "no_mark": [entry], "at": "x"}
    v = _empty()
    st = _tick(p, v)
    assert not st["abandoned"] and _census(st, "no_mark") == 1 and _bbos(v) == [SLUG]
    assert ml._no_mark_memo == {KEY: NOW} and _census(st, "cand_no_mark_skipped") == 0


@pytest.mark.parametrize("value", ["x", ["a"], {"unmapped": "x", "no_mark": 3}, {"unmapped": None}, 7, "{not json"])
def test_e7_a_malformed_or_unreadable_key_is_empty_and_the_tick_runs(value, caplog):
    ml._terminal_memo_loaded = False
    p = _spool()
    p.state[ml._STATE_CAND_MEMO] = value
    v = _empty()
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert not st["abandoned"] and _bbos(v) == [SLUG] and ml._unmapped_until == {}
    assert ml._no_mark_until == {KEY: NOW + 900.0}, "read, and memoised on this read"
    # unreadable: empty, logged, never read again
    ml._terminal_memo_loaded = False
    ml._no_mark_until.clear()
    ml._no_mark_memo.clear()
    p2 = _spool()
    p2.raise_on.append(("SELECT value FROM ingestion_state", RuntimeError("down")))
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st2 = _tick(p2, _empty())
    assert ml._terminal_memo_loaded is True and ml._no_mark_until == {} and not st2["abandoned"]
    assert any("mirror_cand_memo unreadable" in r.getMessage() for r in caplog.records)
    p2.raise_on.clear()
    _tick(p2, _empty(), now=NOW + 30)
    assert len(_memo_reads(p2)) == 1, "one boot read per process, whatever it read"


def test_e7_nothing_is_written_before_the_boot_read_and_the_prune_keeps_an_expired_entry_an_hour():
    ml._terminal_memo_loaded = False
    p = _spool()
    p.tables_absent = True                                 # a first tick refused ahead of the boot read
    ml._no_mark_until[KEY] = NOW + 500
    ml._no_mark_memo[KEY] = NOW
    st = _tick(p, _empty())
    assert st["status"] == "degraded" and not _memo_writes(p) and ml._terminal_memo_loaded is False
    # the prune: an expired entry survives UNMAPPED_TTL_MAX_S past its until (the growth reads it), then goes
    ml._unmapped_until[KEY] = NOW - 10
    ml._unmapped_memo[KEY] = (NOW - 910, 900.0)
    ml._unmapped_memo[("rn1", "0xorphan")] = (NOW, 900.0)  # an `at` with no until beside it
    ml._prune_cand_memos(NOW)
    assert ml._unmapped_until == {KEY: NOW - 10} and ml._unmapped_memo == {KEY: (NOW - 910, 900.0)}
    ml._prune_cand_memos(NOW - 10 + ml.UNMAPPED_TTL_MAX_S + 1)
    assert ml._unmapped_until == {} and ml._unmapped_memo == {}


# --------------------------------------------- part B: the data-API wait

def test_e7_the_data_api_block_rides_beside_the_timing_block_and_the_served_cap_holds():
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    tm, da = st["short"]["timing"], st["short"]["data_api"]
    assert tuple(tm) == TIMING_KEYS, "E6's block: these keys and no others, still"
    assert tuple(da) == DATA_API_KEYS, "bounded: these keys and no others"
    for k in ("books_data_wait", "books_data_req"):
        assert isinstance(da[k], float) and da[k] >= 0.0 and da[k] == round(da[k], 1), k
    assert da["data_rps"] == 6.0 and da["books_data_wait"] + da["books_data_req"] <= tm["books_data"] + 0.2
    served = api_app._sanitize_detail(st)
    assert "_truncated_keys" not in served and len(st["integ"]) < api_app._DETAIL_MAX_KEYS
    assert served["short"]["data_api"] == da and served["short"]["timing"] == tm
    assert "data_api" not in st and "data_api" not in st["integ"] and len(ml._new_stats()) <= 40
    assert ml._data_api_block(ml._Tick(pool=p, pmus=_Venue(), http=_Http(), now=NOW, stats=ml._new_stats())) == \
        {"books_data_wait": 0.0, "books_data_req": 0.0, "data_rps": 6.0}


def test_e7_the_wait_and_the_request_are_measured_on_the_books_reads_only(monkeypatch):
    class _Slow:
        async def wait(self):
            _busy(0.12)

        async def acquire(self, priority=False):      # E10: the books' reads take the priority lane
            _busy(0.12)
    monkeypatch.setattr(ratelimit, "_throttle", _Slow())
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    da = st["short"]["data_api"]
    assert da["books_data_wait"] >= 0.1 and da["books_data_req"] < 0.1, da
    assert st["short"]["timing"]["books_data"] >= da["books_data_wait"]

    class _SlowHttp(_Http):
        async def get(self, path, params=None):
            _busy(0.12)
            return await super().get(path, params)
    monkeypatch.setattr(ratelimit, "_throttle", _NoThrottle())
    p2 = _pool()
    p2.add_book(ledger=300)
    st2 = _tick(p2, _Venue(held={SLUG: 300}), http=_SlowHttp())
    da2 = st2["short"]["data_api"]
    assert da2["books_data_req"] >= 0.1 and da2["books_data_wait"] < 0.1, da2
    # a candidate's read (book=False) is never counted here, as E6's books_data never was
    p3 = _pool()
    st3 = _tick(p3, _Venue(), http=_SlowHttp())
    assert p3.books and st3["snap_market_reads"] >= 1
    assert st3["short"]["data_api"]["books_data_req"] == 0.0 and st3["short"]["timing"]["books_data"] == 0.0


def test_e7_market_positions_timing_is_optional_measures_the_split_and_changes_nothing_else(monkeypatch):
    class _T:
        def __init__(self):
            self.waits = 0

        async def wait(self):
            self.waits += 1
            _busy(0.05)
    thr = _T()
    monkeypatch.setattr(ratelimit, "_throttle", thr)
    http = _Http()
    d: dict = {}
    out = _run(we.market_positions(http, "0xabc", CID, long_asset=M, timing=d))
    assert out["complete"] is True and out["by_asset"] == {M: 300.0, N: 0.0}
    assert d["wait"] >= 0.05 and d["req"] >= 0.0 and thr.waits == 1 and len(http.calls) == 1
    # the default: no dict, nothing measured, the same one read
    out2 = _run(we.market_positions(http, "0xabc", CID, long_asset=M))
    assert out2 == {**out, "ts": out2["ts"]} and thr.waits == 2 and len(http.calls) == 2
    # a refused read still records its split (measurement never changes the verdict)
    d2: dict = {}
    assert _run(we.market_positions(_Http(status=503), "0xabc", CID, timing=d2)) is None
    assert d2["wait"] >= 0.05 and d2["req"] >= 0.0
    # the request is the one `_confirm_gone` makes, still
    src = inspect.getsource(we.market_positions)
    assert "data_api_throttle().wait()" in src and '"sizeThreshold": 0})' in src and "venue_pace" not in src.split('"""')[2]


# ------------------------------------------------ the source order, the rails

def test_e7_the_source_order_release_before_the_memo_checks_and_the_boot_read_beside_e6s():
    src = inspect.getsource(ml._tick)
    assert "stamped = await ms.active_conditions(t.pool, w, stamped=True)" in src
    assert src.index("_prune_cand_memos(t.now)") < src.index("for w in sorted(t.allow)")
    assert (src.index("_release_cand_memo(t, w, cid, stamps.get(cid))") < src.index("_unmapped_until.get((w, cid)")
            < src.index("_no_mark_until.get((w, cid)") < src.index("_terminal_until.get((w, cid)")
            < src.index("_walk_candidate(t, w, cid)"))
    assert src.index("(w, cid) in t.books_seen") < src.index("_release_cand_memo(t, w, cid, stamps.get(cid))")
    assert src.index("_load_terminal_memo(t)") < src.index("_load_cand_memo(t)") < src.index("await _read_mode(t)\n    if t.mode")
    assert "_name_unread(t, w, unread, stamps)" in src
    once = inspect.getsource(ml.tick_once)
    assert once.index('["timing"] = _timing_block(t)') < once.index('["data_api"] = _data_api_block(t)') < once.index("_publish_fills_dedup(t)")
    assert once.index("_persist_terminal_memo(t)") < once.index("_persist_cand_memo(t)")
    assert "if not _terminal_memo_loaded:\n        return" in inspect.getsource(ml._persist_cand_memo)
    assert "_STATE_TERMINAL_MEMO" not in inspect.getsource(ml._persist_cand_memo)
    # the stamped call is the one query, unchanged in text; the plain call unchanged in shape
    shadow = inspect.getsource(ms)
    assert shadow.count("FROM trades t") == 3 and "ORDER BY last_ts DESC" in inspect.getsource(ms.active_conditions)


def test_e7_the_stamped_query_is_the_one_query_and_the_plain_call_is_unchanged():
    p = _spool(conds=["c1", "c2"], stamps={"c1": _dt(NOW - 5)})
    assert _run(ms.active_conditions(p, "rn1")) == ["c1", "c2"]
    assert _run(ms.active_conditions(p, "rn1", stamped=True)) == [("c1", pytest.approx(NOW - 5)), ("c2", None)]
    q = [s for s, _ in p.queries if "max(t.ts) AS last_ts" in s]
    assert len(q) == 2 and q[0] == q[1] and "ORDER BY last_ts DESC" in q[0]


def test_e7_the_e6_pins_and_the_worker_rails_stand(monkeypatch):
    from tests import test_e6_tick_budget as e6
    from tests import test_mirror_live_worker as w
    w.test_ledger_dust_is_the_last_census_key_and_no_served_index_moved()
    w.test_the_gate_counters_survive_the_health_endpoints_sanitizer()
    e6.test_e6_book_quiet_skipped_is_a_census_key_before_the_pinned_last_key()
    # each E6 pin starts from the fixture's reset write gates and empty
    # memos; one clock and one process here, so reset between them
    for reset in (ml._terminal_until, ml._terminal_book_until, ml._terminal_book_state, ml._no_mark_until, ml._no_mark_memo):
        reset.clear()
    ml._terminal_memo_last.update(at=0.0, sig=None)
    ml._cand_memo_last.update(at=0.0, sig=None)
    e6.test_e6_the_timing_block_is_present_bounded_and_served_inside_short()
    for reset in (ml._terminal_until, ml._terminal_book_until, ml._terminal_book_state, ml._no_mark_until, ml._no_mark_memo):
        reset.clear()
    ml._terminal_memo_last.update(at=0.0, sig=None)
    ml._cand_memo_last.update(at=0.0, sig=None)
    e6.test_e6_the_terminal_memo_is_written_bounded_once_per_60s_and_only_on_change()
    assert set(ml._terminal_memo_snapshot(NOW)) == {"cand", "book"}
    assert "data_api" not in json.dumps(ml._new_stats()) and len(ml._integ_block(ml._new_stats())) < 40
    src = inspect.getsource(ml._increases_refusal) + inspect.getsource(ml._global_guards) + inspect.getsource(ml._tick_book)
    assert "_no_mark_until" not in src and "_release_cand_memo" not in src, "no guard and no book reads the candidate memos"
