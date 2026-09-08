"""E6 review pins (2026-09-07): the tick's venue-call budget, attacked.

Driven end to end through tick_once against the worker file's fakes, as
test_e6_tick_budget is. Nine pins were xfail(strict=True) on the E6 tree
as built -- the DEFECTS the review found (hard2/E6_review.md); the fold
landed each fix and dropped the marks, so every pin here passes. Three
passing pins carried the arithmetic the fixes replace and were adjusted
by the fold, each saying where (the map cap's share, the due tick's
total, the deferred queue's rule).
"""
import json
import logging
import math

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, NOW, SLUG, _Venue, _armed, _census, _fill, _kinds, _many_books, _pool, _tick,
)

EXPIRED = "MARKET_STATE_EXPIRED"
HALTED = "MARKET_STATE_HALTED"


def _bbos(v):
    return [c[1] for c in v.calls if c[0] == "bbo"]


def _timing(st):
    return st["short"]["timing"]


def _open_markets(p, conds):
    for c in conds:
        p.markets[c] = {"closed": False, "resolved": False, "resolved_prices": None}


def _resolver_needing(need, calls):
    """ms.map_market for a walk in which the markets in `need` take one
    resolver (venue) read each and stay unmapped; every other market
    maps the fixture's way (our ledger, no venue read)."""
    orig = ms.map_market

    async def _map(pool, fills, pmus=None, **kw):
        cid, out, budget = kw.get("condition_id"), kw["out"], kw["budget"]
        if cid in need:
            if budget.reads >= budget.cap:
                budget.capped += 1
                out["refusal"] = "map_reads_capped"
                return None
            budget.reads += 1
            out["venue_reads"] = 1
            calls.append(cid)
            return None
        return await orig(pool, fills, pmus, **kw)
    return _map


# ------------------------------------------------------------ the rotation

def test_review_a_deferred_cohort_is_read_within_the_budget_not_all_at_once(monkeypatch):
    """MEDIUM-1 (fixed): the deferred queue is read under
    max(quiet_budget, DEFERRED_MIN_PER_TICK) a tick, never en masse."""
    monkeypatch.setattr(ml, "VENUE_CALLS_PER_TICK", 20)
    p = _pool(conds=[])
    _many_books(p, 120)
    v = _Venue()
    worst = 0
    for i in range(16):                 # E11: the due tick is 10 (was 4) and the queue drains after it
        v.calls.clear()
        _tick(p, v, now=NOW + 30 * i)
        if i >= 1:                                   # tick 1 reads everything: the old behaviour, by design
            worst = max(worst, len(_bbos(v)))
    assert worst <= 2 * ml.VENUE_CALLS_PER_TICK, worst


def test_review_the_deferred_cohort_is_bounded_by_the_quiet_books_and_no_book_waits_twice(monkeypatch):
    """What the FOLDED rule guarantees (MEDIUM-1; this pin carried the
    old rule's "read next tick whatever the budget" and was rewritten
    by the fold): the queue is FIFO -- the head is read each tick,
    max(quiet_budget, DEFERRED_MIN_PER_TICK) of it, and a book queued
    earlier is never read after one queued later; the reads a tick
    makes on quiet books never exceed that; the queue never holds more
    than the quiet books; and every quiet book is read again within
    QUIET_EVERY_TICKS + ceil(N_quiet / slots) ticks of its last read.
    120 quiet books at the floor: quiet_budget 20 - 2 - 10 = 8, slots
    10, so a cycle is 12 ticks and the worst gap QUIET_EVERY_TICKS + 12
    (E11: 21 at 9; it was 15 at 3 -- the loop runs 24 ticks so every
    book is read twice, as 18 covered 15)."""
    monkeypatch.setattr(ml, "VENUE_CALLS_PER_TICK", 20)
    p = _pool(conds=[])
    slugs = _many_books(p, 120)
    v = _Venue()
    read_at: dict = {s: [] for s in slugs}
    queued_at: dict = {}
    slots = None
    for i in range(24):
        v.calls.clear()
        st = _tick(p, v, now=NOW + 30 * i)
        tm = _timing(st)
        slots = max(tm["quiet_budget"], ml.DEFERRED_MIN_PER_TICK)
        read = _bbos(v)
        for s in read:
            read_at[s].append(i + 1)
        if i >= 1:
            assert len(read) <= slots and tm["quiet_reads"] == len(read), (i + 1, len(read), tm)
        # FIFO: what was read this tick from the queue is the head the queue
        # held before it; nothing queued later was read ahead of it
        ids_read = {b["id"] for b in p.books.values() if b["us_market_slug"] in read}
        left = [bid for bid in queued_at if bid not in ids_read]
        assert all(queued_at[a] <= queued_at[b] for a, b in zip(left, left[1:]))
        assert not any(queued_at[a] > queued_at[b] for a in ids_read & set(queued_at) for b in left)
        assert len(ml._quiet_deferred) <= 120
        queued_at = dict(ml._quiet_deferred)
        assert list(queued_at.values()) == sorted(queued_at.values()), "the queue is in deferral order"
    assert tm["quiet_budget"] == 20 - 2 - ml.CAND_MIN_PER_TICK == 8 and slots == 10
    bound = ml.QUIET_EVERY_TICKS + math.ceil(120 / slots)
    for s, ticks in read_at.items():
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        assert len(ticks) >= 2 and max(gaps) <= bound, (s, ticks, bound)


def test_review_hot_books_are_read_when_the_budget_is_negative_and_the_quiet_wait_once(monkeypatch):
    """25 hot books (no mark: a halted venue, read every tick) and 5 quiet
    ones at the floor budget of 20: the budget is negative, the hot
    books are read whatever it says, the due quiet books are deferred
    once and read the next tick -- under the queue's floor
    (DEFERRED_MIN_PER_TICK), which is why a budget of 0 for as long as
    the hot books stand never starves them."""
    monkeypatch.setattr(ml, "VENUE_CALLS_PER_TICK", 20)
    p = _pool(conds=[])
    slugs = _many_books(p, 30)
    hot = slugs[:25]
    v = _Venue(states={s: HALTED for s in hot})
    # E11: the due tick is 1 + QUIET_EVERY_TICKS = 10 (was 4); the ticks
    # before it skip the quiet five
    for i in range(ml.QUIET_EVERY_TICKS):
        v.calls.clear()
        _tick(p, v, now=NOW + 30 * i)
    v.calls.clear()
    st10 = _tick(p, v, now=NOW + 270)
    assert _timing(st10)["quiet_budget"] == 0 and _census(st10, "no_mark") == 25
    assert sorted(_bbos(v)) == sorted(hot) and _census(st10, "book_quiet_skipped") == 5
    v.calls.clear()
    st11 = _tick(p, v, now=NOW + 300)
    assert sorted(_bbos(v)) == sorted(slugs) and _census(st11, "book_quiet_skipped") == 0
    assert not ml._quiet_deferred and _timing(st11)["quiet_reads"] == 5 <= ml.DEFERRED_MIN_PER_TICK


# ------------------------------------------------------ the budget's shares

def test_review_mapped_candidates_still_get_a_quote_read_behind_resolver_reads_at_the_floor(monkeypatch):
    """HIGH-1 (fixed): the quote reads keep their floor under the map
    reads."""
    need = {f"need{i}" for i in range(20)}
    calls: list = []
    monkeypatch.setattr(ms, "map_market", _resolver_needing(need, calls))
    conds = sorted(need) + [f"0xmapped{i}" for i in range(10)]
    p = _pool(conds=conds)
    _open_markets(p, conds)
    _many_books(p, 50)                        # 2 + 50 calls before the candidates: the share is the floor
    v = _Venue()
    st = _tick(p, v)
    assert _timing(st)["cand_budget"] == ml.CAND_MIN_PER_TICK and len(calls) == 10
    quote_reads = len(_bbos(v)) - 50
    assert quote_reads >= 1, (quote_reads, st["census"])


def test_review_with_room_in_the_share_the_mapped_candidates_are_read(monkeypatch):
    """The same walk with 20 hot books: 38 of share, the map cap what
    sits above the quote reads' floor (38 - 10 = 28, HIGH-1's fold;
    this line read 38 on the E6 tree), 20 resolver reads, the ten
    mapped candidates read and a book opened."""
    need = {f"need{i}" for i in range(20)}
    calls: list = []
    monkeypatch.setattr(ms, "map_market", _resolver_needing(need, calls))
    conds = sorted(need) + [f"0xmapped{i}" for i in range(10)]
    p = _pool(conds=conds)
    _open_markets(p, conds)
    _many_books(p, 20)
    v = _Venue()
    st = _tick(p, v)
    tm = _timing(st)
    assert tm["cand_budget"] == 60 - 22 == 38 and tm["map_cap"] == 38 - ml.CAND_MIN_PER_TICK == 28
    assert len(calls) == 20
    assert _census(st, "map_venue_read") == 20 and _census(st, "unmapped") == 20
    assert len(_bbos(v)) - 20 >= 10 and _census(st, "cand_unread_capped") == 0


def test_review_terminal_memo_skipped_books_cost_the_quiet_budget_nothing():
    """MEDIUM-2 (fixed): 40 books the memo skips cost the quiet share
    nothing, so the 30 quiet ones are read on their due tick."""
    p = _pool(conds=[])
    slugs = _many_books(p, 70)
    v = _Venue(states={s: EXPIRED for s in slugs[:40]})
    for i in range(ml.QUIET_EVERY_TICKS):          # E11: the due tick is 10 (was 4)
        st = _tick(p, v, now=NOW + 30 * i)
    assert _census(st, "book_terminal_skipped") == 40
    v.calls.clear()
    st10 = _tick(p, v, now=NOW + 270)
    assert _timing(st10)["quiet_budget"] >= 30, _timing(st10)
    assert _census(st10, "book_quiet_skipped") == 0 and len(_bbos(v)) == 30


def test_review_the_total_venue_calls_over_the_rotation_and_the_map_share(monkeypatch):
    """50 quiet books, no writes, 60 candidates mapping from the ledger:
    the tick's venue calls are 2 + reads, the candidate share the
    floor on the due tick, and the total never above the budget plus
    CAND_MIN_PER_TICK (the floor the brief grants the candidates). The
    map share: with MIRROR_MAP_READS unset the resolver may read up to
    the share less the quote reads' floor -- above ms.MAP_READS_PER_TICK,
    the old per-tick cap (the review's MEDIUM-3, settled by HIGH-1's
    fold: the two map-cap lines read `cand_budget` and 58 on the E6
    tree). The due tick (MEDIUM-4's fold): the quiet share reserves
    the candidates' floor, so it reads 48 of the 50 (two join the
    queue and are read next tick) and the total is the budget exactly
    -- 2 + 50 + 10 on the E6 tree."""
    monkeypatch.delenv("MIRROR_MAP_READS", raising=False)
    p = _pool(conds=[f"c{i}" for i in range(60)])
    _many_books(p, 50)
    v = _Venue()
    totals = []
    due = ml.QUIET_EVERY_TICKS           # E11: the due tick is the 10th (index 9), was the 4th (index 3)
    for i in range(due + 2):
        v.calls.clear()
        st = _tick(p, v, now=NOW + 30 * i)
        tm = _timing(st)
        totals.append(_census(st, "venue_calls"))
        assert _census(st, "venue_calls") == 2 + len(_bbos(v))
        assert _census(st, "venue_calls") <= ml.VENUE_CALLS_PER_TICK + ml.CAND_MIN_PER_TICK
        assert tm["map_cap"] == max(ms.MAP_READS_PER_TICK, tm["cand_budget"] - ml.CAND_MIN_PER_TICK)
    assert totals[due] == 2 + 48 + ml.CAND_MIN_PER_TICK == ml.VENUE_CALLS_PER_TICK, totals
    assert totals[due + 1] == 2 + 2 + ml.MAX_MARKETS_PER_TICK, totals     # the two queued, then the candidates
    assert ml._map_cap(58) == 58 - ml.CAND_MIN_PER_TICK == 48 > ms.MAP_READS_PER_TICK == 10


def test_review_the_due_tick_stays_inside_the_budget():
    """MEDIUM-4 (fixed): the quiet share reserves CAND_MIN_PER_TICK."""
    p = _pool(conds=[f"c{i}" for i in range(60)])
    _many_books(p, 50)
    v = _Venue()
    for i in range(1 + ml.QUIET_EVERY_TICKS):      # E11: through the due tick, 10 (was 4)
        st = _tick(p, v, now=NOW + 30 * i)
    assert _census(st, "venue_calls") <= ml.VENUE_CALLS_PER_TICK, st["census"]["venue_calls"]


def test_review_the_superseded_pins_are_the_budgets_arithmetic():
    """U12 25 + 33, E2 MEDIUM-5 12, r2 / r3 10 (the floor), the E1
    full-game pin's turn on the tick after the rotation (the tenth
    since E11, the fourth under E6): each derived, none fitted."""
    t = ml._Tick(pool=_pool(), pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    t.venue_calls = 2 + 25
    assert ml._cand_budget(t) == 33 == ml.VENUE_CALLS_PER_TICK - 27
    t.venue_calls = 2 + 46
    assert ml._cand_budget(t) == 12
    t.venue_calls = 2 + 46 + 40                            # twenty BUYs: preview + create each
    assert ml._cand_budget(t) == ml.CAND_MIN_PER_TICK == 10
    assert 128 - (40 - 10) == 98
    # the rotation: read on tick 1, read_on = 1 + QUIET_EVERY_TICKS = 10 (E11; 4 at 3); tick 3 is a skip
    assert 1 + ml.QUIET_EVERY_TICKS == 10 and 3 < 10


# ------------------------------------------------------- money in motion

def test_review_a_woken_book_is_read_that_tick_whatever_the_fills_stamp():
    """HIGH-2 (fixed): a woken market's book is hot (t.woken)."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert ml._quiet_memo[b["id"]]["quiet"] is True
    # his SELL of 200, stamped 700 s ago, lands in the trades table now
    # (the poller's lag, a backfill): the copy lane wakes the mirror
    p.fills = p.fills + [_fill(M, "SELL", 200.0, 0.31, NOW + 30 - 700)]
    p.snap[M] = 100.0
    ml.notify(CID)
    v.calls.clear()
    st = _tick(p, v, now=NOW + 30)
    assert st["woken"] == [CID]
    # E18: the second read of the slug is the exit IOC's re-read before its send
    assert _census(st, "book_quiet_skipped") == 0 and _bbos(v) == [SLUG, SLUG], st["census"]


def test_review_a_fresh_fill_of_his_that_lands_between_two_ticks_is_read_next_tick():
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert ml._quiet_memo[b["id"]]["quiet"] is True
    p.fills = p.fills + [_fill(M, "SELL", 200.0, 0.31, NOW + 25)]
    p.snap[M] = 100.0
    v.calls.clear()
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "book_quiet_skipped") == 0 and _bbos(v) == [SLUG, SLUG]    # E18: the IOC's re-read
    assert ml._quiet_memo[b["id"]]["quiet"] is False, "a reduce placed: hot from here"


def test_review_a_quiet_skip_leaves_the_walk_streak_alone():
    """Five quiet books skipped in a row are not five misses: the
    outage streak (ms.MISS_STREAK_ABANDON) reads no verdict off a book
    that was not read, and the tick is not abandoned."""
    p = _pool(conds=[])
    _many_books(p, 5)
    v = _Venue()
    _tick(p, v)
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "book_quiet_skipped") == 5 and not st["abandoned"]
    assert _census(st, "tick_abandoned") == 0 and st.get("abandon_reason") is None
    assert 5 >= ms.MISS_STREAK_ABANDON


def test_review_the_market_row_closes_a_skipped_book_before_any_read_and_step_m_is_first():
    p = _pool(conds=[])
    b = p.add_book(ledger=0, target=0)
    v = _Venue()
    _tick(p, v)
    p.markets[CID] = {"closed": False, "resolved": True, "resolved_prices": [1.0, 0.0]}
    v.calls.clear()
    st = _tick(p, v, now=NOW + 30)
    assert b["state"] == "closed" and "bbo" not in _kinds(v) and _census(st, "book_quiet_skipped") == 0


def test_review_a_skipped_books_row_keeps_the_last_reads_his_net():
    """LOW-1 (fixed): the skip writes _SQL_BOOK_SKIP -- the name and the
    plan -- and no reading column."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert b["his_net"] == 300 and b["venue_net"] == 300
    _tick(p, v, now=NOW + 30)
    assert b["last_reason"] == "book_quiet_skipped"
    assert b["his_net"] == 300 and b["venue_net"] == 300, (b["his_net"], b["venue_net"])


def test_review_the_quiet_memo_forgets_a_closed_book():
    """LOW-2 (fixed): the close writers and the walk's listing drop the
    book from the memo and the queue."""
    p = _pool(conds=[])
    b = p.add_book(ledger=0, target=0)
    v = _Venue()
    _tick(p, v)
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    _tick(p, v, now=NOW + 30)
    assert b["state"] == "closed"
    assert b["id"] not in ml._quiet_memo


# ------------------------------------------------------ the persisted memo

def test_review_a_halted_read_never_lands_in_either_terminal_memo_nor_the_persisted_key():
    p = _pool(conds=["0xcand"])
    p.markets["0xcand"] = {"closed": False, "resolved": False, "resolved_prices": None}
    p.add_book(ledger=300)
    v = _Venue(state=HALTED, held={SLUG: 300})
    st = _tick(p, v)
    assert _census(st, "venue_halted") >= 1
    assert ml._terminal_book_until == {} and ml._terminal_until == {} and ml._terminal_book_state == {}
    # (the fixture starts with no signature, so the empty memo is written
    # once here; in production the boot read stamps it -- what matters is
    # that nothing HALTED is in what was written)
    for x in [x for x in p.sent if "ml-state-write" in x[1] and x[2][0] == ml._STATE_TERMINAL_MEMO]:
        assert json.loads(x[2][1])["book"] == [] and json.loads(x[2][1])["cand"] == []
    assert HALTED not in ms.STATE_TERMINAL
    # the snapshot writes nothing but what the memos hold
    assert ml._terminal_memo_snapshot(NOW) == {"cand": [], "book": []}


def test_review_the_boot_read_drops_a_book_entry_whose_state_is_not_terminal():
    """LOW-3 (fixed): `e[3] in ms.STATE_TERMINAL` at the boot read."""
    ml._terminal_memo_loaded = False
    p = _pool()
    b = p.add_book(ledger=300)
    p.state[ml._STATE_TERMINAL_MEMO] = {"cand": [], "book": [["rn1", CID, NOW + 700, HALTED]],
                                        "at": "2026-09-07T19:00:00Z"}
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v)
    assert ml._terminal_book_until == {}, "a HALTED entry is not a terminal reading"
    assert _census(st, "book_terminal_skipped") == 0 and _bbos(v) == [SLUG] and b["last_reason"] != "no_mark"


def test_review_the_first_memo_write_after_boot_waits_the_60s_gate():
    """The boot read stamps `at` = now, so a memo that changes on the
    first tick is written on the first tick at or past 60 s -- never on
    every tick of a crash loop."""
    ml._terminal_memo_loaded = False
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue(state=EXPIRED)
    _tick(p, v)
    assert ml._terminal_book_until and not [x for x in p.sent if "ml-state-write" in x[1]
                                            and x[2][0] == ml._STATE_TERMINAL_MEMO]
    _tick(p, v, now=NOW + 59)
    assert not [x for x in p.sent if "ml-state-write" in x[1] and x[2][0] == ml._STATE_TERMINAL_MEMO]
    _tick(p, v, now=NOW + 60)
    assert len([x for x in p.sent if "ml-state-write" in x[1] and x[2][0] == ml._STATE_TERMINAL_MEMO]) == 1


def test_review_the_persisted_memo_is_under_a_megabyte_at_its_bound():
    cid = "0x" + "f" * 64
    ml._terminal_until.update({("rn1", f"{cid}{i:05d}"): NOW + 10 + i for i in range(ml._TERMINAL_MEMO_MAX + 50)})
    ml._terminal_book_until.update({("rn1", f"{cid}{i:05d}"): NOW + 10 + i for i in range(ml._TERMINAL_MEMO_MAX + 50)})
    ml._terminal_book_state.update({("rn1", f"{cid}{i:05d}"): "MARKET_STATE_MATCH_AND_CLOSE_AUCTION"
                                    for i in range(ml._TERMINAL_MEMO_MAX + 50)})
    snap = ml._terminal_memo_snapshot(NOW)
    text = json.dumps({**snap, "at": ml._iso(NOW)}, default=str)
    assert len(snap["cand"]) == len(snap["book"]) == ml._TERMINAL_MEMO_MAX
    assert len(text) < 1_000_000, len(text)


def test_review_a_reopened_market_is_never_behind_a_persisted_memo():
    """Only a terminal state is ever memoised (the two writers), so a
    market that reopens from HALTED was never in the memo; and a memo
    the old process wrote is bounded by UNMAPPED_TTL_S from its read."""
    p = _pool()
    p.add_book(ledger=300)
    v = _Venue(state=HALTED, held={SLUG: 300})
    _tick(p, v)
    assert ml._terminal_book_until == {}
    v2 = _Venue(held={SLUG: 300})
    st = _tick(p, v2, now=NOW + 30)
    assert _bbos(v2) == [SLUG] and _census(st, "on_target") == 1, "read the tick it reopened"
    assert ms.UNMAPPED_TTL_S == 900.0


# ------------------------------------------------- the mode line, the served block

def test_review_the_mode_lines_loss_and_sleeve_sit_inside_the_first_400_characters_worst_case(caplog):
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_EXITS, whales=["rn1", "whale_two", "whale_three", "whale_four", "whale_five"],
                 books_live=125, orders_open=40, mirror_day_room=12345.67, venue_state=HALTED,
                 abandoned=True, abandon_reason="positions_unreadable")
    stats["short"]["timing"] = {"walk": 999.9, "orders": 99.9, "books": 9999.9, "candidates": 999.9,
                                "read": 125, "quiet_skipped": 125, "placed": 40}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS,
                      {"sum": -99999.99, "books": 99, "limit": 99999.0, "since": "2026-09-07T17:30:00Z"},
                      {"sum": -99999.99, "limit": 99999.0})
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    prefix = len("2026-09-07 19:12:00,123 INFO sportsassets.workers.mirror_live: ")
    end = line.index(" venue=")
    assert " loss=" in line[:end] and " sleeve=" in line[:end]
    assert prefix + end < 400, (prefix + end, line[:end])


def test_review_the_timing_block_survives_the_sanitizers_depth_and_the_short_block_stays_small():
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    served = api_app._sanitize_detail(st)
    assert served["short"]["timing"] == st["short"]["timing"]
    assert len(st["short"]) < api_app._DETAIL_MAX_KEYS and len(st) <= api_app._DETAIL_MAX_KEYS
    assert all(isinstance(v, (int, float)) for v in st["short"]["timing"].values())
    assert "_truncated_keys" not in served and "_truncated_keys" not in served["short"]


def test_review_env_can_only_lower_both_knobs_and_the_e2_guard_is_untouched(monkeypatch):
    for env, want in (("1000", 60.0), ("45", 45.0), ("19", 20.0), ("-5", 20.0), ("nan", 60.0)):
        monkeypatch.setenv("MIRROR_TICK_VENUE_CALLS", env)
        assert rules.capped_env("MIRROR_TICK_VENUE_CALLS", 60.0, floor=20.0) == want, env
    monkeypatch.setenv("MIRROR_MAP_READS", "x")            # unreadable: the default cap is the ceiling
    assert ml._map_cap(58) == min(ms.MAP_READS_PER_TICK, 58) == 10
    assert rules.MIRROR_VENUE_CALLS_PER_TICK == 80


# ------------------------------------------------ the mutants the suite let live

def test_review_the_boot_read_is_armed_at_import():
    """Mutant M08 (the module starting with the boot read already made)
    survived the builder's pins: the fixture sets the flag by hand both
    ways, so the module's own default was never read."""
    import inspect
    src = inspect.getsource(ml)
    assert "\n_terminal_memo_loaded = False\n" in src
    assert src.index("_terminal_memo_loaded = False") < src.index("async def _load_terminal_memo")


def test_review_a_book_frozen_by_another_step_since_its_quiet_read_is_hot_by_row():
    """Mutant M12 (the `state != live` clause of _hot_by_row dropped)
    survived: every frozen book the pins drive was frozen at its read
    (the verdict is never quiet). A book read quiet on tick 1 and frozen
    by step O between ticks (a lost placement, an overfill) keeps its
    last_plan `at`, so only the state clause makes it hot -- E5's exit
    path must read it that tick."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert ml._quiet_memo[b["id"]]["quiet"] is True
    b.update(state="frozen", frozen_reason="placement_lost", frozen_ts=NOW)
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW + 30, stats=ml._new_stats())
    assert ml._hot_by_row(t, b) is True
    v.calls.clear()
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "book_quiet_skipped") == 0 and _bbos(v) == [SLUG] and st["books_frozen"] == 1


def test_review_a_first_tick_refused_before_the_boot_read_never_overwrites_the_memo():
    """MEDIUM-5 (fixed): _persist_terminal_memo writes nothing before
    the boot read was made."""
    ml._terminal_memo_loaded = False
    ml._terminal_memo_last.update(at=0.0, sig=None)          # what a fresh process holds
    p = _pool()
    p.add_book(ledger=300)
    kept = {"cand": [["rn1", "0xa", NOW + 500]], "book": [["rn1", "0xb", NOW + 700, EXPIRED]],
            "at": "2026-09-07T19:00:00Z"}
    p.state[ml._STATE_TERMINAL_MEMO] = dict(kept)
    p.tables_absent = True
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert _census(st, "tables_absent") == 1 and ml._terminal_memo_loaded is False
    assert p.state[ml._STATE_TERMINAL_MEMO]["cand"] == kept["cand"], p.state[ml._STATE_TERMINAL_MEMO]
    p.tables_absent = False
    _tick(p, _Venue(held={SLUG: 300}), now=NOW + 30)
    assert ml._terminal_until == {("rn1", "0xa"): NOW + 500.0} and ml._terminal_book_until == {("rn1", "0xb"): NOW + 700.0}
