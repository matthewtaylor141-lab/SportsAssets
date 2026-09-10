"""E9 (2026-09-07): the wake fast path; every fill of his on a market we
hold answered or named.

# RE-PINNED at E31 (FILL lane 31, 2026-09-10: every order a post-only rest that never
# crosses; owner order ~03:3xZ "become a maker not taker ... mirror him to a tee"). The
# rule this file exists for is untouched; the EXECUTION under it moves the same way on
# every pin below, and only these ways:
#   the cent 0.30 -> 0.31   this world's book is 0.30 / 0.32 and his level 0.31. An ENTRY
#                           rested at buy_price(his, bid) = the BID 0.30; the maker wire is
#                           min(buy_wire(0.31), 0.32 - 0.01) = 0.31, HIS OWN cent inside
#                           the spread. An EXIT took at ceil(his) - MIRROR_EXIT_TOL = 0.30,
#                           THROUGH the bid; the maker wire is max(sell_wire(0.31), 0.30 +
#                           0.01) = 0.31, his own cent one tick over the bid.
#   IOC -> GTC, post-only   there is no take path left in the worker.
#   `exit_take` 1 -> 0      the counter counted the exit's IOC and is a declared zero from
#                           E31; `rest_placed` counts in its place.
#   `lift=` beside `ioc_fill=`  the shares a TAKER lifts off the fresh rest at create
#                           (`aggressor` False: a maker fill), booking the ledger the IOC
#                           booked, so every ledger figure below is unchanged.

Owner 22:4xZ: "I need it being proportional, directional and only taking
buys within 1c of him and sells (or exits) within 1c of his price. This
means latency must be flawless and exceptional." With E6 + E7 live the
tick was 14.9 s (22:44:50Z) and his fills' ingest lag a median of -1 s,
yet his fill on a market we hold reached our first order after a median
of 27-39 s and a p90 of 468-661 s (latency_2249): one tick of waiting
plus the walk's order, and a market that waited its turn or was refused
with no row that said why. Driven end to end against the worker file's
fakes (its autouse rails are imported).

THE RULE, EXACTLY (pinned below). PART 1: every plan write carries
`last_plan.his_fills_seen` -- one entry per fill of his the tick holds
on the market that no earlier plan recorded: {id, ts, det, at, order,
name}, `order` our order row id when that tick placed on the book, else
`name` the plan's reason as its census name; written once, never
renamed, at most HIS_FILLS_SEEN_MAX = 20 (the newest kept). PART 2: the
wake (notify) fires on EVERY newly inserted fill of a mirrored whale --
BUY and SELL, chain and poll, the copy probe on or off -- from the
ingestion pipeline where the fill is written (and from the enrichment
that gives a chain row its condition); the copy lane's gate keeps its
own call where spec 3.1 pins it. On a wake a FAST TICK runs for the
woken markets only, before the next full tick and beside one in flight:
at most FAST_TICK_MAX = 5 markets, at most one fast tick per
FAST_TICK_MIN_S = 2 s, through _tick_book / _walk_candidate -- the same
functions, rails and names (entries at his cent, exits within
MIRROR_EXIT_TOL, the E6 budget shared through `t.fast_calls`, the E2
guard's and the ops budget's counters seeded, _read_mode and
_global_guards read the loss rails exactly as the full tick does). It
refuses, by name (`fast_tick_skipped`, the reason on its stats), a book
whose per-book lock is held (step O or the walk has it), a book whose
game a full tick in flight has not walked past, a book that is not
live, one with an order open, one with no positions walk younger than
FAST_WALK_MAX_S to plan on, one a fill was booked on after that walk; a
sibling with an order open makes the game's room unreadable (no
increase sized). Any error is `fast_tick_failed` and the market waits
for the full tick. The full tick folds the fast ticks' census in and
publishes `short.fast` beside E6's exactly-pinned timing block; the mode
line prints ` fast=N`.
"""
import inspect
import pathlib
import re
import time as _time

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.ingestion import pipeline
from sportsassets.workers import mirror_live as ml
from tests.test_e6_tick_budget import TIMING_KEYS
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, GAME_KEY, M, N, NOW, SLUG, _Http, _Venue, _armed, _census, _fill, _kinds, _mkt, _places,
    _pool, _reduce_world, _run, _tick,
)

YML = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
FAST_KEYS = ("fast", "n", "markets", "placed", "skipped", "failed", "calls")
HALTED = "MARKET_STATE_HALTED"


def _bbos(v):
    return [c[1] for c in v.calls if c[0] == "bbo"]


def _walk(positions=None, at=None):
    """The last full tick's positions walk, as the fast tick plans on it
    (a fresh walk: no fill booked after it)."""
    ml._last_walk = (dict(positions or {}), NOW if at is None else at)
    ml._last_filled = set()


def _fast(p, v, cids=(CID,), now=None, http=None):
    """One fast tick for the fixture market (`cids` None: whatever the
    wake left in _FAST_WOKEN) at the fixture clock, a second past NOW
    by default (after the walk `_walk` records at NOW)."""
    p.clock = NOW + 1 if now is None else now
    return _run(ml.fast_tick_once(p, v, http if http is not None else _Http(),
                                  cids=None if cids is None else list(cids),
                                  now_ts=NOW + 1 if now is None else now))


def _skips(st):
    return st["fast"]["skipped"]


# ---------------------------------------------------------------- constants

def test_e9_the_constants_the_names_and_the_e7_tail_stand():
    assert ml.FAST_TICK_MAX == 5 and ml.FAST_TICK_MIN_S == 2.0 and ml.HIS_FILLS_SEEN_MAX == 20
    assert ml.FAST_WALK_MAX_S == 90.0 and ml.FAST_RETRIES == 2
    src = inspect.getsource(ml)
    # no env knob of its own: nothing to lower under five markets and two
    # seconds that is not the rails themselves (env may only lower those)
    assert 'capped_env("MIRROR_FAST' not in src and "MIRROR_FAST_TICK" not in src
    keys = ml.CENSUS_KEYS
    new = ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert keys[-8:-4] == new, "before E7's pair"
    assert keys[-4:] == ("cand_no_mark_skipped", "cand_memo_released", "book_quiet_skipped",
                         "cand_terminal_skipped"), "the E7 and E6 tails exactly"
    assert len(set(keys)) == len(keys)
    for k in new:
        assert ml._new_stats()["census"][k] == 0 and keys.index(k) >= api_app._DETAIL_MAX_KEYS


# ------------------------------------------------------- part 2: the wake

class _IngestPool:
    """The pipeline's INSERT ... RETURNING id, was_insert."""

    def __init__(self, insert=True):
        self.insert = insert
        self.sent = []

    async def fetchrow(self, sql, *a):
        self.sent.append(sql)
        return {"id": 7, "was_insert": self.insert}

    async def execute(self, sql, *a):
        self.sent.append(sql)
        return "UPDATE 1"


def _ev(side="BUY", source="poll", cid=CID, whale="rn1", ts=None):
    return pipeline.TradeEvent(whale_id=1, whale_username=whale, tx_hash="0xtx", asset=M, side=side,
                               size=10.0, price=0.31, ts_epoch=int(NOW - 700 if ts is None else ts),
                               source=source, condition_id=cid, outcome="x", outcome_index=1)


def _ingest(monkeypatch, ev, insert=True):
    pool = _IngestPool(insert)

    async def _get_pool():
        return pool

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(pipeline, "get_pool", _get_pool)
    monkeypatch.setattr(pipeline, "publish", _noop)
    monkeypatch.setattr(pipeline, "_write_outbox", _noop)
    return _run(pipeline.ingest_trade_result(ev)), pool


@pytest.mark.parametrize("side,source", [("SELL", "poll"), ("SELL", "chain"), ("BUY", "poll"), ("BUY", "chain")])
def test_e9_the_wake_fires_for_every_ingested_fill_of_his_sell_and_buy_chain_and_poll(monkeypatch, side, source):
    ml._WOKEN.clear()
    (tid, new), pool = _ingest(monkeypatch, _ev(side=side, source=source))
    assert (tid, new) == (7, True)
    assert ml._WOKEN == {CID} and ml._FAST_WOKEN == {CID: 0}, (side, source)
    assert ml._WAKE.is_set()
    ml._WAKE.clear()


def test_e9_the_wake_ignores_a_duplicate_a_condition_less_fill_and_a_whale_the_mirror_does_not_hold(monkeypatch):
    ml._WOKEN.clear()
    _ingest(monkeypatch, _ev(side="SELL"), insert=False)          # a re-delivered fill: no wake
    assert ml._WOKEN == set() and ml._FAST_WOKEN == {}
    _ingest(monkeypatch, _ev(side="SELL", cid=None))               # a chain row with no condition yet
    assert ml._WOKEN == set() and ml._FAST_WOKEN == {}
    _ingest(monkeypatch, _ev(side="BUY", whale="swisstony"))       # not a mirrored whale
    assert ml._WOKEN == set() and ml._FAST_WOKEN == {}
    monkeypatch.setenv("PMUS_MIRROR", "off")                       # the mirror off: nobody
    _ingest(monkeypatch, _ev(side="BUY"))
    assert ml._WOKEN == set() and ml._FAST_WOKEN == {}


def test_e9_the_wake_fires_with_the_copy_probe_off_it_reads_the_deploy_alone_and_sits_before_the_fresh_gate(monkeypatch):
    class _S:
        copy_probe_enabled = False
    monkeypatch.setattr(le, "settings", lambda: _S())
    ml._WOKEN.clear()
    # a fill too old for the copy lane's fresh gate (600 s): the copy path
    # never runs, the wake does
    _ingest(monkeypatch, _ev(side="BUY", ts=NOW - 700))
    assert ml._WOKEN == {CID} and ml._FAST_WOKEN == {CID: 0}
    body = inspect.getsource(pipeline._mirror_wake)
    for bad in ("copy_probe", "settings(", "await", "pool", "fetch", "execute_copy"):
        assert bad not in body, bad
    assert "mirror_mode(" in body and "_mirror_notify(" in body
    src = inspect.getsource(pipeline.ingest_trade_result)
    assert src.index('if not row["was_insert"]') < src.index("if not notify:") \
        < src.index("_mirror_wake(ev.whale_username, ev.condition_id)") < src.index("fresh = (ev.ts_epoch")
    assert src.count("_mirror_wake(") == 1
    # the enrichment that gives a chain row its condition wakes too, after the UPDATE
    en = inspect.getsource(pipeline._enrich)
    assert en.index("UPDATE trades SET condition_id") < en.index("_mirror_wake(ev.whale_username, ev.condition_id)") \
        < en.index("publish(CH_TRADES_ENRICHED")
    # the copy lane's gate keeps its own call exactly where spec 3.1 pins it
    gate = inspect.getsource(le.maybe_execute)
    assert '_mirror_notify(payload.get("condition_id"))' in gate and gate.count("_mirror_notify(") == 1


def test_e9_the_enrichment_wakes_the_moment_a_chain_row_has_its_condition(monkeypatch):
    from sportsassets import gamma
    ml._WOKEN.clear()
    pool = _IngestPool()

    async def _get_pool():
        return pool

    async def _meta(asset):
        return {"condition_id": CID, "outcome": "x", "outcome_index": 1, "title": "t", "slug": "s",
                "event_slug": "e", "sport": "tennis"}

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(pipeline, "get_pool", _get_pool)
    monkeypatch.setattr(pipeline, "publish", _noop)
    monkeypatch.setattr(gamma, "lookup_token_live", _meta)
    ev = _ev(side="SELL", source="chain", cid=None)
    from datetime import datetime, timezone
    _run(pipeline._enrich(7, ev, datetime.now(tz=timezone.utc)))
    assert ev.condition_id == CID and ml._WOKEN == {CID} and ml._FAST_WOKEN == {CID: 0}


def test_e9_notify_outside_a_loop_or_unarmed_schedules_nothing_and_never_raises():
    ml.notify(CID)                                  # no running loop: the poll's tick reads it
    assert ml._FAST_WOKEN == {CID: 0} and ml._fast_task is None and CID in ml._WOKEN
    ml._arm_fast(_pool(), _Venue(), _Http())
    ml.notify(CID)                                  # armed, still no loop: nothing scheduled
    assert ml._fast_task is None
    ml.notify("")                                   # a blank id wakes the poll alone
    assert ml._FAST_WOKEN == {CID: 0}

    async def _in_loop():
        ml._fast_ctx = None
        ml.notify("0xother")                        # unarmed inside a loop: nothing scheduled
        assert ml._fast_task is None and ml._FAST_WOKEN == {CID: 0, "0xother": 0}
    _run(_in_loop())


# --------------------------------------------- part 2: the fast tick itself

def test_e9_the_fast_tick_reads_and_places_the_woken_market_inside_the_floor_and_touches_no_other_book(monkeypatch):
    slept = []

    async def _s(s):
        slept.append(s)
    monkeypatch.setattr(ml, "_fast_sleep", _s)
    p = _pool()
    b = p.add_book(ledger=0)                                # his 300 held, ours 0: an increase
    p.markets["0xother"] = {"closed": False, "resolved": False, "resolved_prices": None}
    other = p.add_book(ledger=0, target=0, condition_id="0xother", us_market_slug="aec-atp-o-o-2026-09-06",
                       long_asset="tokLo", other_asset="tokOo", game_key=le._us_game_key("aec-atp-o-o-2026-09-06"))
    v = _Venue()
    http = _Http()

    async def _drive():
        ml._last_walk = ({}, _time.time())
        ml._arm_fast(p, v, http)
        t0 = _time.monotonic()
        ml.notify(CID)                                      # his fill on the market we hold
        assert ml._fast_task is not None
        await ml._fast_task
        return _time.monotonic() - t0
    took = _run(_drive())
    assert took < ml.FAST_TICK_MIN_S and slept == [], "no floor to wait out: the first wake runs at once"
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1] == SLUG and pl[0][2] == 0.31 and pl[0][3] == 300, pl
    assert _bbos(v) == [SLUG, SLUG], "the woken market alone: the other book is not read"  # E31: the tick's read and the touch-bound rest's re-read at the send
    assert b["open_order_id"] is not None and other["last_plan"] is None
    assert ml._FAST_WOKEN == {} and ml._fast_census["fast_tick"] == 1 and ml._fast_census["fast_tick_placed"] == 1
    assert ml._fast_census["rest_placed"] == 1
    # the fill of his that woke it is answered on the row by our order's id
    seen = b["last_plan"]["his_fills_seen"]
    assert len(seen) == 1 and seen[0]["order"] == b["open_order_id"] and seen[0]["name"] == "rest_placed"


def test_e9_the_bound_of_five_and_the_two_second_floor(monkeypatch):
    slept = []

    async def _s(s):
        slept.append(s)
    monkeypatch.setattr(ml, "_fast_sleep", _s)
    p = _pool()
    slugs = []
    for i in range(7):
        cid, slug = f"0xw{i}", f"aec-atp-w{i}-x{i}-2026-09-06"
        p.markets[cid] = {"closed": False, "resolved": False, "resolved_prices": None}
        p.add_book(ledger=0, target=0, condition_id=cid, us_market_slug=slug,
                   long_asset=f"tokL{i}", other_asset=f"tokO{i}", game_key=le._us_game_key(slug))
        slugs.append(slug)
    v = _Venue()

    async def _drive():
        ml._last_walk = ({}, _time.time())
        ml._fast_last_at = _time.time() - 0.5             # a fast tick half a second ago
        ml._arm_fast(p, v, _Http())
        for i in range(7):
            ml.notify(f"0xw{i}")
        assert ml._fast_task is not None
        await ml._fast_task
    _run(_drive())
    # the floor: the run waited out the rest of the 2 s, then read FIVE,
    # waited the floor again and read the last two
    assert len(slept) == 2 and 1.0 <= slept[0] <= ml.FAST_TICK_MIN_S and slept[1] == ml.FAST_TICK_MIN_S, slept
    assert _bbos(v) == slugs, "every woken market read, in wake order, five then two"
    assert ml._fast_acc["n"] == 2 and ml._fast_acc["markets"] == 7 and ml._FAST_WOKEN == {}
    # a direct call is bounded the same way: seven handed in, five taken
    # (the walk re-stamped at the fixture clock: one stamped past the
    # tick's own clock is refused, fail closed)
    v.calls.clear()
    _walk()
    st = _fast(p, v, cids=[f"0xw{i}" for i in range(7)])
    assert st["woken"] == [f"0xw{i}" for i in range(5)] and len(_bbos(v)) == 5


def test_e9_the_budget_is_shared_with_the_full_tick():
    # the fast ticks spent 30 calls since the last full tick: the quiet
    # budget and the candidate share read them off the same rail
    ml._fast_calls = 30
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    tm = st["short"]["timing"]
    assert tm["quiet_budget"] == 60 - 2 - 1 - ml.CAND_MIN_PER_TICK - 30
    assert tm["cand_budget"] == 60 - tm["venue_calls"] - 30
    assert ml._fast_calls == 0, "the full tick starts the count over"
    # and a fast tick refuses a market once the rail is spent
    ml._fast_calls = int(ml.VENUE_CALLS_PER_TICK)
    p2 = _pool()
    p2.add_book(ledger=0)
    v2 = _Venue()
    _walk()
    fs = _fast(p2, v2)
    assert _skips(fs) == {CID: "budget_spent"} and _bbos(v2) == [] and not _places(v2)
    assert _census(fs, "fast_tick_skipped") == 1 and _census(fs, "fast_tick") == 1
    # the calls a fast tick makes accumulate for the next one and the full tick
    ml._fast_calls = 0
    p3 = _pool()
    p3.add_book(ledger=0)
    v3 = _Venue()
    fs3 = _fast(p3, v3)
    assert _places(v3) and ml._fast_calls == _census(fs3, "venue_calls") == fs3["short"]["timing"]["venue_calls"] == 5


def test_e9_the_e2_guard_and_the_ops_budget_are_seeded_with_the_fast_ticks_spend():
    # the soft guard: a woken CANDIDATE is venue_calls_capped at once
    ml._fast_guard_calls = int(rules.MIRROR_VENUE_CALLS_PER_TICK)
    p = _pool()
    v = _Venue()
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "venue_calls_capped"} and _bbos(v) == [] and not p.books
    assert _census(fs, "venue_calls_capped") == 1
    # the ops budget: an increase is ops_capped, an exit still goes (exempt)
    ml._fast_guard_calls = 0
    ml._fast_ops = int(rules.MIRROR_MAX_ORDER_OPS_PER_TICK)
    p2 = _pool()
    p2.add_book(ledger=0)
    v2 = _Venue()
    fs2 = _fast(p2, v2)
    assert _census(fs2, "ops_capped") == 1 and not _places(v2) and _bbos(v2) == [SLUG]
    p3, b3, v3, http3 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0, lift=200.0)
    _walk({SLUG: 300.0})
    fs3 = _fast(p3, v3, http=http3)
    pl = _places(v3)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][2] == 0.31 and _census(fs3, "exit_take") == 0
    assert _census(fs3, "fast_tick_placed") == 1 and b3["ledger_net"] == 100


def test_e9_a_fast_tick_is_refused_while_the_full_tick_holds_the_books_lock_and_retried_twice():
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    _walk()
    lk = ml._lock_for(b["id"])

    async def _under_lock(cids):
        async with lk:                              # step O or the walk has the book
            return await ml.fast_tick_once(p, v, _Http(), cids=cids, now_ts=NOW + 1)
    fs = _run(_under_lock([CID]))
    assert _skips(fs) == {CID: "book_locked"} and _bbos(v) == [] and not _places(v)
    assert _census(fs, "fast_tick_skipped") == 1 and ml._FAST_WOKEN == {CID: 1}, "put back once"
    fs2 = _run(_under_lock(None))                   # the next fast tick takes it back
    assert _skips(fs2) == {CID: "book_locked"} and ml._FAST_WOKEN == {CID: 2}
    fs3 = _run(_under_lock(None))
    assert _skips(fs3) == {CID: "book_locked"} and ml._FAST_WOKEN == {}, "past FAST_RETRIES: the full tick's"
    assert _bbos(v) == [] and b["last_plan"] is None
    # the lock free: read and placed, the wake answered
    fs4 = _fast(p, v)
    assert _skips(fs4) == {} and _bbos(v) == [SLUG, SLUG] and _places(v) and _census(fs4, "fast_tick_placed") == 1  # E31: the tick's read and the touch-bound rest's re-read at the send
    # the source: the lock is asked, never awaited, before the row is re-read under it
    src = inspect.getsource(ml._fast_gate)
    assert "_lock_for(bid).locked()" in src and "async with" not in src
    fb = inspect.getsource(ml._fast_book)
    assert fb.index("_fast_gate(t, book)") < fb.index("async with lk:") < fb.index("_sql_book_read(t)") \
        < fb.index("await _tick_book(t, fresh)")


def test_e9_a_fast_tick_beside_a_full_tick_in_flight_waits_for_its_walk_to_pass_the_game():
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    _walk()
    full = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    ml._full_tick = full                            # a full tick is running, its walk not yet at the game
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "full_tick_pending"} and _bbos(v) == [] and ml._FAST_WOKEN == {CID: 1}
    full.walk_done = {b["id"]}                      # the walk is past the book's whole game
    ml._FAST_WOKEN.clear()
    fs2 = _fast(p, v)
    assert _skips(fs2) == {} and _bbos(v) == [SLUG, SLUG] and _places(v)  # E31: the tick's read and the touch-bound rest's re-read at the send
    # a cancel-only full tick in flight: nothing placed beside it
    p2 = _pool()
    p2.add_book(ledger=0)
    v2 = _Venue()
    full2 = ml._Tick(pool=p2, pmus=v2, http=None, now=NOW, stats=ml._new_stats())
    full2.cancel_all = "wrong_sign_trip"
    ml._full_tick = full2
    fs3 = _fast(p2, v2)
    assert _skips(fs3) == {CID: "wrong_sign_trip"} and not _places(v2)
    # a candidate never opens beside a full tick in flight
    p3 = _pool()
    ml._full_tick = ml._Tick(pool=p3, pmus=v2, http=None, now=NOW, stats=ml._new_stats())
    fs4 = _fast(p3, _Venue())
    assert _skips(fs4) == {CID: "full_tick_pending"} and not p3.books
    # and a fill booked by the running tick after its walk refuses the book (E5 review F1)
    ml._full_tick = full
    full.walk_done, full.filled_books = {b["id"]}, {b["id"]}
    p.orders.clear()
    b["open_order_id"] = None
    v.calls.clear()
    fs5 = _fast(p, v)
    assert _skips(fs5) == {CID: "fill_after_walk"} and _bbos(v) == []


def test_e9_a_failing_fast_tick_names_fast_tick_failed_and_the_full_tick_reads_the_market(caplog):
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    _walk()
    p.raise_on.append(("ml-standing-read", RuntimeError("db")))
    import logging
    with caplog.at_level(logging.ERROR, logger=ml.log.name):
        fs = _fast(p, v)
    assert _census(fs, "fast_tick_failed") == 1 and _skips(fs) == {CID: "fast_tick_failed"}
    assert not _places(v) and b["last_plan"] is None and ml._FAST_WOKEN == {}
    assert any("fast tick on" in r.getMessage() and "failed" in r.getMessage() for r in caplog.records)
    assert fs["fast"]["placed"] == 0 and ml._fast_acc["failed"] == 1
    # the full tick reads it (woken: hot, first) and places
    p.raise_on.clear()
    ml._WOKEN.add(CID)
    st = _tick(p, v, now=NOW + 5)
    assert _places(v) and b["open_order_id"] is not None and st["woken"] == [CID]
    assert _census(st, "fast_tick_failed") == 1, "the fast tick's census folded into the full tick's"
    # a raise ABOVE the per-market loop (the prelude) names every market
    p2 = _pool()
    p2.add_book(ledger=0)
    p2.raise_on.append(("ml-books-open", RuntimeError("db")))
    fs2 = _fast(p2, _Venue())
    assert _census(fs2, "fast_tick_failed") == 1 and _skips(fs2) == {CID: "fast_tick_failed"}


def test_e9_the_fast_tick_leaves_to_the_full_tick_by_name_a_frozen_book_an_open_order_no_walk_a_stale_walk_a_booked_fill():
    v = _Venue()
    # an order open on the book (the DB rows: step O is the one that books a fill)
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b)
    v.rest("oid-1")
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "order_open"} and _bbos(v) == [] and "cancel" not in _kinds(v)
    # a frozen book: E5's exit path reads the venue, the full tick's
    p2 = _pool()
    p2.add_book(ledger=300, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 100)
    fs2 = _fast(p2, v)
    assert _skips(fs2) == {CID: "not_live"} and _bbos(v) == []
    # no walk to plan on, or one older than FAST_WALK_MAX_S
    p3 = _pool()
    p3.add_book(ledger=0)
    ml._last_walk = None
    fs3 = _fast(p3, v)
    assert _skips(fs3) == {CID: "walk_stale"} and _bbos(v) == []
    _walk(at=NOW + 1 - ml.FAST_WALK_MAX_S - 1)
    fs4 = _fast(p3, v)
    assert _skips(fs4) == {CID: "walk_stale"} and _bbos(v) == []
    _walk(at=NOW + 1 - ml.FAST_WALK_MAX_S)
    fs5 = _fast(p3, v)
    assert _skips(fs5) == {} and _bbos(v) == [SLUG, SLUG], "inside the bound: read"  # E31: the tick's read and the touch-bound rest's re-read at the send
    # a fill the last full tick booked after its walk: the venue reading is stale by it
    v.calls.clear()
    p4 = _pool()
    b4 = p4.add_book(ledger=0)
    _walk()
    ml._last_filled = {b4["id"]}
    fs6 = _fast(p4, v)
    assert _skips(fs6) == {CID: "fill_after_walk"} and _bbos(v) == []
    # none of these is put back: the full tick reads them (woken there too)
    assert ml._FAST_WOKEN == {}


def test_e9_a_sibling_with_an_order_open_makes_the_games_room_unreadable_no_increase_an_exit_still_goes():
    p = _pool()
    b = p.add_book(ledger=0)
    p.markets["0xsib"] = {"closed": False, "resolved": False, "resolved_prices": None}
    sib = p.add_book(ledger=0, target=300, condition_id="0xsib", us_market_slug="aec-atp-branak-alemic-2026-09-02-total",
                     long_asset="tokLs", other_asset="tokOs", game_key=GAME_KEY)
    p.add_order(sib, qty=300, wire=0.30)
    v = _Venue()
    v.rest("oid-1", slug="aec-atp-branak-alemic-2026-09-02-total")
    _walk()
    fs = _fast(p, v)
    assert _census(fs, "game_unreadable") == 1 and not _places(v) and _bbos(v) == [SLUG]
    assert b["last_plan"]["game_exposure"] is None and b["last_plan"]["game_room"] == 0
    # the exit: a reduce is never touched by the game cap
    p2, b2, v2, http2 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0, lift=200.0)
    p2.markets["0xsib"] = {"closed": False, "resolved": False, "resolved_prices": None}
    sib2 = p2.add_book(ledger=0, target=300, condition_id="0xsib", us_market_slug="aec-atp-branak-alemic-2026-09-02-total",
                       long_asset="tokLs", other_asset="tokOs", game_key=GAME_KEY)
    p2.add_order(sib2, qty=300, wire=0.30)
    v2.rest("oid-1", slug="aec-atp-branak-alemic-2026-09-02-total")
    _walk({SLUG: 300.0})
    fs2 = _fast(p2, v2, http=http2)
    assert _census(fs2, "exit_take") == 0 and len(_places(v2)) == 1 and b2["ledger_net"] == 100


def test_e9_the_fast_tick_reads_the_mode_ladder_and_the_loss_rails_exactly_as_the_full_tick(monkeypatch):
    src = inspect.getsource(ml._fast_tick)
    assert src.index("_SQL_TABLE_GUARD") < src.index("_SQL_INTENT_GUARD") < src.index("await _read_mode(t)") \
        < src.index("await _global_guards(t)") < src.index("_sql_books_open(t)")
    for fn in ("account_positions_walk", "_reconcile_orders", "_walk_books", "_candidate_order",
               "mirror_target(", "mi.plan(", "submit_fok", "_place("):
        assert fn not in src, fn                    # no walk, no step O, no rotation, no second planner
    for fn in (ml._fast_book, ml._fast_candidate, ml._fast_gate):
        s = inspect.getsource(fn)
        for bad in ("mirror_target(", "mi.plan(", "submit_fok", "_place(", "_bbo(", "_read_market("):
            assert bad not in s, (fn.__name__, bad)
    assert "await _tick_book(t, fresh)" in inspect.getsource(ml._fast_book)
    assert "await _walk_candidate(t, w, cid)" in inspect.getsource(ml._fast_candidate)
    v = _Venue()
    _walk()
    # SAFE: cancel-only, the full tick's reconcile; nothing read
    monkeypatch.setenv("PMUS_MIRROR", "off")
    p = _pool()
    p.add_book(ledger=0)
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "mode_env_off"} and _bbos(v) == [] and fs["mode"] == "safe"
    monkeypatch.setenv("PMUS_MIRROR", "on")
    # a global guard tripped: named, nothing read
    monkeypatch.setenv("LIVE_COPY_HALT", "on")
    fs2 = _fast(p, v)
    assert _skips(fs2) == {CID: "halted"} and _census(fs2, "halted") == 1 and _bbos(v) == []
    monkeypatch.delenv("LIVE_COPY_HALT")
    # the loss stop standing: the increase refused under the full tick's name
    p.state["mirror_loss_stop"] = {"at": "x"}
    fs3 = _fast(p, v)
    # counted by the guard and again as the increase's refusal, as the full tick counts it
    assert _census(fs3, "mirror_loss_stop") == 2 and not _places(v) and _bbos(v) == [SLUG]
    assert p.books[next(iter(p.books))]["last_reason"] == "mirror_loss_stop"
    p9 = _pool()
    b9 = p9.add_book(ledger=0)
    p9.state["mirror_loss_stop"] = {"at": "x"}
    v9 = _Venue()
    st9 = _tick(p9, v9)
    assert b9["last_reason"] == "mirror_loss_stop" and not _places(v9) and _census(st9, "mirror_loss_stop") >= 2
    # the backoff: a fast tick inside it reads nothing
    del p.state["mirror_loss_stop"]
    v.calls.clear()
    ml._backoff_until = NOW + 30
    fs4 = _fast(p, v)
    assert _skips(fs4) == {CID: "backoff"} and _bbos(v) == [] and fs4["skipped_backoff"] is True
    ml._backoff_until = 0.0
    # exits-only: his SELL is still answered by a fast tick (a reduce is an exit)
    monkeypatch.setenv("PMUS_MIRROR", "exits")
    p5, b5, v5, http5 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0, lift=200.0)
    _walk({SLUG: 300.0})
    fs5 = _fast(p5, v5, http=http5)
    assert fs5["mode"] == "exits" and len(_places(v5)) == 1 and _places(v5)[0][4] is True
    assert _census(fs5, "fast_tick_placed") == 1


def _world(kind):
    """Identical fresh worlds for the same-planner pins. Returns
    (pool, book, venue, http, positions)."""
    if kind == "on_target":
        p = _pool()
        return p, p.add_book(ledger=300), _Venue(held={SLUG: 300}), _Http(), {SLUG: 300.0}
    if kind == "increase":
        p = _pool()
        return p, p.add_book(ledger=0), _Venue(), _Http(), {}
    if kind == "no_mark":
        p = _pool()
        return p, p.add_book(ledger=300), _Venue(held={SLUG: 300}, state=HALTED), _Http(), {SLUG: 300.0}
    if kind == "mode_db_off":
        p = _pool()
        p.state["mirror_live"] = False
        return p, p.add_book(ledger=0), _Venue(), _Http(), {}
    if kind == "exit_take":
        p, b, v, http = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0, lift=200.0)
        return p, b, v, http, {SLUG: 300.0}
    if kind == "exit_out_of_tol":
        p, b, v, http = _reduce_world(bid=0.29, ask=0.32, ioc_fill=200.0, lift=200.0)
        return p, b, v, http, {SLUG: 300.0}
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["on_target", "increase", "no_mark", "mode_db_off", "exit_take", "exit_out_of_tol"])
def test_e9_every_name_and_plan_the_full_tick_writes_is_the_one_the_fast_tick_writes(kind):
    p1, b1, v1, h1, pos = _world(kind)
    st = _tick(p1, v1, http=h1)
    p2, b2, v2, h2, _ = _world(kind)
    _walk(pos, NOW)
    fs = _fast(p2, v2, now=NOW, http=h2)
    assert _skips(fs) == {}
    assert b2["last_reason"] == b1["last_reason"], kind
    # T2 (FILL lane 4): `fills_hwm` rides beside the list (the two worlds
    # share book ids, so the full world's flush is the fast world's memo)
    drop = ("his_fills_seen", "fills_hwm")
    assert {k: v for k, v in b2["last_plan"].items() if k not in drop} == \
        {k: v for k, v in b1["last_plan"].items() if k not in drop}, kind
    assert _places(v2) == _places(v1) and _kinds(v2).count("cancel") == _kinds(v1).count("cancel"), kind
    assert b2["ledger_net"] == b1["ledger_net"] and b2["target"] == b1["target"]
    # the same refusal / outcome names (the fast path's own four aside;
    # the full tick's step R and O names -- its walk pages, its
    # open-orders read -- are its own)
    own = {"fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed", "venue_calls"}
    full = {k for k, n in st["census"].items() if n and k not in own}
    fast = {k for k, n in fs["census"].items() if n and k not in own}
    assert fast <= full, (kind, fast - full)
    # the fill of his is answered the same way on both rows
    e1, e2 = b1["last_plan"]["his_fills_seen"], b2["last_plan"]["his_fills_seen"]
    assert [x["name"] for x in e1] == [x["name"] for x in e2] and [bool(x["order"]) for x in e1] == [bool(x["order"]) for x in e2]


def test_e9_entries_at_his_cent_and_exits_within_the_tolerance_are_the_same_wires_in_a_fast_tick():
    assert rules.MIRROR_EXIT_TOL == 0.01
    p, b, v, http = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0, lift=200.0)     # the bid a cent under his 0.31
    _walk({SLUG: 300.0})
    fs = _fast(p, v, http=http)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][2] == 0.31 and pl[0][5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    assert b["last_plan"]["exit_px"] == 0.31 and b["last_plan"]["exit_px_src"] == "his_fill"
    assert _census(fs, "exit_take") == 0 and _census(fs, "exit_out_of_tol") == 0
    p2, b2, v2, http2 = _reduce_world(bid=0.29, ask=0.32, ioc_fill=200.0, lift=200.0)  # two cents under: the rest at HIS cent
    _walk({SLUG: 300.0})
    fs2 = _fast(p2, v2, http=http2)
    pl2 = _places(v2)
    assert len(pl2) == 1 and pl2[0][2] == 0.31 and pl2[0][5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    # E31: `exit_take` 0 -> 0 (the take is gone with `_exit_take`), but `exit_out_of_tol`
    # 0 -> 1: E4's HELD RECORD travelled with the take branch and is kept on the road that
    # PLACES the exit (mirror_live.py `_exit_held(t, r, ex, plan, w)` under
    # `not rules.at_or_through(SELL, r.bid, r.ask, ex["take"])`), because the exits presets
    # read it and docs 75 lists the name as reachable. Here his take cent is
    # sell_wire(0.31 - MIRROR_EXIT_TOL) = 0.30 and the bid is 0.29, so the touch IS outside
    # his tolerance cent and the tick records the quote and the FLOOR it was read against.
    # In the first world (bid 0.30) the take cent is AT the bid, so nothing is recorded.
    # The rest itself, at his cent 0.31, is what both worlds send either way
    assert _census(fs2, "exit_out_of_tol") == 1 and _census(fs2, "exit_take") == 0
    assert b2["last_plan"]["exit_out_of_tol"]["bid"] == 0.29
    assert b2["last_plan"]["exit_out_of_tol"]["ask"] == 0.32
    assert b2["last_plan"]["exit_out_of_tol"]["floor"] == 0.30
    # the entry rests at his level's cent, post-only, exactly as the full tick's
    p3 = _pool()
    p3.add_book(ledger=0)
    v3 = _Venue()
    _walk()
    _fast(p3, v3)
    pl3 = _places(v3)
    assert len(pl3) == 1 and pl3[0][2] == 0.31 and pl3[0][7] is True and pl3[0][6] == "ORDER_INTENT_BUY_LONG"


# ---------------------------------------------------- part 1: his_fills_seen

def test_e9_his_fills_seen_names_every_fill_once_carries_the_order_id_and_is_bounded_at_twenty():
    fills = [_fill(M, "BUY", 10, 0.31, NOW - 3000 + i, detected_at=NOW - 2999 + i) for i in range(25)]
    p = _pool(fills=fills, snap={M: 250.0, N: 0.0})
    b = p.add_book(ledger=250)
    v = _Venue(held={SLUG: 250})
    _tick(p, v, http=_mkt(250.0))
    seen = b["last_plan"]["his_fills_seen"]
    assert len(seen) == ml.HIS_FILLS_SEEN_MAX == 20
    assert [e["ts"] for e in seen] == [NOW - 3000 + i for i in range(5, 25)], "the newest kept"
    assert all(e["name"] == "on_target" and e["order"] is None and e["at"] == NOW for e in seen)
    assert all(e["det"] == e["ts"] + 1 for e in seen), "the ingest's clock rides beside the fill's"
    assert all(e["id"] == str(e["ts"]) for e in seen)
    # a new fill of his: ONE new entry, the old ones never renamed
    p.fills.append(_fill(M, "BUY", 50, 0.31, NOW + 10))
    p.snap = {M: 300.0, N: 0.0}
    st = _tick(p, v, now=NOW + 30, http=_mkt(300.0))
    assert _places(v) and _census(st, "rest_placed") == 1
    seen2 = b["last_plan"]["his_fills_seen"]
    assert len(seen2) == 20 and seen2[-1]["ts"] == NOW + 10 and seen2[-1]["order"] == b["open_order_id"]
    assert seen2[-1]["name"] == "rest_placed" and seen2[-1]["at"] == NOW + 30
    assert seen2[:-1] == seen[1:], "the answered fills keep their first answer; the oldest dropped"
    # the next tick (the order open, no new fill) renames nothing
    _tick(p, v, now=NOW + 60, http=_mkt(300.0))
    assert b["last_plan"]["his_fills_seen"] == seen2


def test_e9_his_fills_seen_is_written_on_the_skip_the_closing_and_the_unreadable_paths():
    # the quiet skip names a fill older than HOT_S it is the first to hold
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert [e["name"] for e in b["last_plan"]["his_fills_seen"]] == ["on_target"]
    p.fills.append(_fill(M, "BUY", 0.0, 0.31, NOW - 4000))          # an old, sizeless row lands late
    st = _tick(p, v, now=NOW + 30)
    assert _census(st, "book_quiet_skipped") == 1 and b["last_reason"] == "book_quiet_skipped"
    names = sorted(e["name"] for e in b["last_plan"]["his_fills_seen"])
    assert names == ["book_quiet_skipped", "on_target"]
    # the markets row unreadable: named market_unreadable
    p2 = _pool()
    b2 = p2.add_book(ledger=300)
    p2.markets.pop(CID)
    _tick(p2, _Venue(held={SLUG: 300}))
    assert b2["last_reason"] == "market_unreadable"
    assert [e["name"] for e in b2["last_plan"]["his_fills_seen"]] == ["market_unreadable"]
    # the closing branch (the markets row closed): named closing_...
    p3 = _pool()
    b3 = p3.add_book(ledger=300)
    p3.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    _tick(p3, _Venue(held={SLUG: 300}))
    assert b3["last_reason"].startswith("closing")
    assert [e["name"].startswith("closing") for e in b3["last_plan"]["his_fills_seen"]] == [True]
    # a fill with no id and no stamp is nothing to key on: not recorded, never a raise
    p4 = _pool(fills=[{"asset": M, "side": "BUY", "size": 300.0, "price": 0.31}])
    b4 = p4.add_book(ledger=300)
    _tick(p4, _Venue(held={SLUG: 300}))
    assert b4["last_plan"]["his_fills_seen"] == [] and b4["last_reason"] is not None
    # an unreadable prior list is an empty one
    p5 = _pool()
    b5 = p5.add_book(ledger=300, last_plan={"his_fills_seen": "junk", "at": 1.0})
    _tick(p5, _Venue(held={SLUG: 300}))
    assert [e["name"] for e in b5["last_plan"]["his_fills_seen"]] == ["on_target"]
    src = inspect.getsource(ml._write_plan)
    assert 'plan["his_fills_seen"] = _fills_seen(' in src and src.index("_fills_seen(") < src.index("_SQL_BOOK_PLAN")


# ------------------------------------------------ the block, the line, the census

def test_e9_short_fast_is_published_beside_the_timing_block_and_served_whole():
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()
    _fast(p, v)                                                 # placed
    p2 = _pool()
    p2.add_book(ledger=0)
    p2.add_order(p2.books[next(iter(p2.books))])
    _fast(p2, _Venue())                                         # skipped: order_open
    p3 = _pool()
    p3.add_book(ledger=300)
    st = _tick(p3, _Venue(held={SLUG: 300}))
    fb = st["short"]["fast"]
    assert tuple(fb) == FAST_KEYS, "bounded: these keys and no others"
    assert isinstance(fb["fast"], float) and fb["fast"] >= 0.0 and fb["fast"] == round(fb["fast"], 1)
    assert fb["n"] == 2 and fb["markets"] == 2 and fb["placed"] == 1 and fb["skipped"] == 1 and fb["failed"] == 0
    assert fb["calls"] == 5 and all(isinstance(fb[k], int) for k in FAST_KEYS[1:])
    assert tuple(st["short"]["timing"]) == TIMING_KEYS, "E6's block keeps exactly its keys"
    assert tuple(st["short"]["data_api"]) == ("books_data_wait", "books_data_req", "data_rps")
    # the census folded: the fast ticks' names ride this tick's census
    assert _census(st, "fast_tick") == 2 and _census(st, "fast_tick_placed") == 1 and _census(st, "fast_tick_skipped") == 1
    assert _census(st, "rest_placed") == 1
    # served whole, the top level not truncated, integ under the cap
    served = api_app._sanitize_detail(st)
    assert served["short"]["fast"] == fb and "_truncated_keys" not in served and len(st["integ"]) < api_app._DETAIL_MAX_KEYS
    assert len(ml._new_stats()) <= api_app._DETAIL_MAX_KEYS
    # reset on publish: the next full tick reads zero fast ticks
    st2 = _tick(p3, _Venue(held={SLUG: 300}), now=NOW + 30)
    assert st2["short"]["fast"]["n"] == 0 and st2["short"]["fast"]["fast"] == 0.0 and _census(st2, "fast_tick") == 0
    # a fast tick's own stats are its return value, never a heartbeat
    # (main() beats the full tick's alone); its facts sit in ONE block
    fs = _fast(p, v, now=NOW + 40)
    assert set(fs["fast"]) == {"on", "tries", "skipped", "placed"} and fs["fast"]["on"] is True
    assert "heartbeat" not in inspect.getsource(ml.fast_tick_once) and "heartbeat" not in inspect.getsource(ml._fast_run)
    from tests.test_mirror_live_worker import _comparable
    assert "fast" not in _comparable(st)["short"]


def test_e9_the_mode_line_prints_fast_n_after_placed_and_nothing_on_a_dict_without_the_block(caplog):
    import logging
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_ON, whales=["rn1"], books_live=2, orders_open=1)
    stats["short"]["timing"] = {"walk": 1.2, "orders": 0.3, "books": 40.1, "candidates": 12.0,
                                "read": 30, "quiet_skipped": 45, "placed": 4}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert " placed=4 venue=None stats={" in line and " fast=" not in line, "E6's line, unchanged"
    caplog.clear()
    stats["short"]["fast"] = {"fast": 0.4, "n": 2, "markets": 3, "placed": 1, "skipped": 2, "failed": 0, "calls": 6}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS,
                      {"sum": -100.0, "books": 1, "limit": 5000.0, "since": "2026-09-07T17:30:00Z"})
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert (" day=None t=1.2/0.3/40.1/12.0 read=30 quiet=45 placed=4 fast=2 loss=-100.0/5000.0 since 17:30 "
            "venue=None stats={") in line, line
    assert line.index(" fast=") < 200


def test_e9_the_full_tick_takes_the_woken_markets_off_the_fast_path_and_reads_them_first():
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    ml.notify(CID)
    assert ml._FAST_WOKEN == {CID: 0} and CID in ml._WOKEN
    st = _tick(p, v)
    assert st["woken"] == [CID] and ml._FAST_WOKEN == {} and _places(v) and b["open_order_id"]
    assert _census(st, "fast_tick") == 0
    # the full tick in flight is visible to the fast path only while it runs
    assert ml._full_tick is None and ml._last_walk is not None and ml._last_walk[1] == NOW
    assert ml._last_filled == set()


def test_e9_two_fast_ticks_never_overlap_and_a_wake_during_one_is_taken_by_the_next():
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()

    async def _drive():
        async with ml._FAST_LOCK:
            st = await ml.fast_tick_once(p, v, _Http(), cids=[CID], now_ts=NOW + 1)
        assert st["status"] == "overlap" and st["skipped_overlap"] is True and _bbos(v) == []
        ml._FAST_WOKEN[CID] = 0
        st2 = await ml.fast_tick_once(p, v, _Http(), now_ts=NOW + 1)
        assert st2["woken"] == [CID] and _places(v) and ml._FAST_WOKEN == {}
    _run(_drive())


# ---------------------------------------------------------- the preset

def test_e9_the_fills_answered_preset_is_read_only_reads_the_row_and_the_help_line_is_the_case_labels():
    text = YML.read_text()
    assert "fills-answered) SQL=" in text
    block = text[text.index("fills-answered) SQL="):text.index("close-rows) SQL=")]
    assert "need_confirm" not in block and "$ARG" not in block
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE"):
        assert bad not in block, bad
    for needle in ("his_fills_seen", "interval '6 hours'", "LIMIT 30", "mirror_books", "'answered'",
                   "'unseen'", "e->>'name'", "e->>'order'", "e->>'id' = f.id::text",
                   "(s.e->>'at')::float8 - extract(epoch FROM s.ts)", "ORDER BY his_usd DESC"):
        assert needle in block, needle
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    assert "fills-answered" in names and names.index("fills-answered") == names.index("latency-census") + 1
    assert len(names) == len(set(names))
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels, "the line is the case labels, in order (regenerated)"
    assert "latency-census|fills-answered|close-rows" in line


def test_e9_fold_the_fills_answered_preset_collapses_his_rows_chain_first_and_its_second_statement_stands_alone():
    """Landing fold (2026-09-07). (1) The plan's his_fills_seen lists the id of the
    row `his_fills` hands the tick, and that read collapses a per-match poll row
    whose (tx, asset, side) has a chain / s1 row (D1), so the preset's own rows
    must collapse the same way or every poll duplicate is filed 'unseen' with
    its dollars; the window runs over his 6 h rows on mirrored markets only.
    (2) psql runs the preset's two statements one by one: the second SELECT
    carries its own CTE chain (the stand-in run failed with 'relation n does
    not exist' when it read the first statement's)."""
    text = YML.read_text()
    block = text[text.index("fills-answered) SQL="):text.index("close-rows) SQL=")]
    assert "bool_or(COALESCE(t.source, '') IN ('chain', 's1')) OVER (PARTITION BY t.whale_id, " \
           "COALESCE(lower(NULLIF(t.tx_hash, '')), 'row:' || t.id::text), t.asset, upper(t.side)) AS has_net" in block
    assert "WHERE r.net_leg OR NOT r.has_net" in block
    assert block.count("interval '6 hours'") == 2 and block.count("WITH r AS (") == 2
    sql = block.split('SQL="', 1)[1].split('"; TO=', 1)[0]
    stmts = [x.strip() for x in sql.split("; ") if x.strip()]
    assert len(stmts) == 2 and all(x.startswith("WITH r AS (") for x in stmts)
    assert "FROM n WHERE name <> 'answered'" in stmts[1]


# ------------------------------------------------ the standing pins re-run

def test_e9_the_e6_e7_every_guard_and_refusal_order_pins_stand(monkeypatch):
    from tests import test_e6_tick_budget as e6
    from tests import test_e7_cand_memo as e7
    e6.test_e6_the_every_guard_and_refusal_order_pins_stand_and_the_served_cap_holds(monkeypatch)
    e6.test_e6_the_source_pins_the_candidate_break_keeps_u12s_text_and_the_walk_order()
    e6.test_e6_the_timing_block_is_present_bounded_and_served_inside_short()
    e7.test_e7_the_source_order_release_before_the_memo_checks_and_the_boot_read_beside_e6s()
    e7.test_e7_the_census_names_sit_before_e6s_key_and_the_pinned_tail_stands()
    from tests.test_mirror_live_worker import test_ledger_dust_is_the_last_census_key_and_no_served_index_moved
    test_ledger_dust_is_the_last_census_key_and_no_served_index_moved()
    # the identity-only mapping is untouched: the fast candidate maps
    # through the walk's own call and nothing of the resolver moved
    assert "ms.map_market(" not in inspect.getsource(ml._fast_candidate)
    assert "identity" not in inspect.getsource(ml._fast_tick).lower()
    # the shadow never touches an order
    from tests.test_mirror_shadow import test_the_shadow_never_touches_an_order
    test_the_shadow_never_touches_an_order()


def test_e9_the_docs_name_the_lane_and_every_new_name():
    doc = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "mirror-coverage.md").read_text()
    assert "## 30. E9" in doc
    for k in ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed", "his_fills_seen",
              "FAST_TICK_MAX", "FAST_TICK_MIN_S", "fills-answered", "short.fast", "fast=N"):
        assert k in doc, k
