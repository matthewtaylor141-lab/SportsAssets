"""E9 review pins (2026-09-07): the wake fast path, driven against the
worker file's fakes with the builder's own helpers. A passing pin
records what holds; a strict xfail is a defect the review names, with
the failing scenario in its docstring and the fix in the review.

The fold (2026-09-08): the review's five xfails pass as written -- the
fast tick is SERIALISED against the full tick (fast_tick_once holds
_TICK_LOCK inside _FAST_LOCK; tick_once waits on a fast tick's hold,
`_fast_holding`, and keeps `overlap` for a full tick's), the full tick
seeds its E2 guard and ops budget from the fast ticks' spend, his_fills
projects detected_at, the preset's second statement stands alone. The
harnesses that drove the race by hand now show the full tick WAITING and
then reading the rows as the fast tick left them; the fold's own pins
sit at the end (`test_review_fold_*`).
"""
import asyncio
import inspect
import json
import pathlib
import re
import threading
import time

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.ingestion import pipeline
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_d1_fills_dedup import D1_CID, K, T0, _drop, _scratch
from tests.test_e9_fast_path import _bbos, _ev, _fast, _ingest, _skips, _walk
from tests.test_mirror_live_day_cap import _ByMarket
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, CID_A, GAME_KEY, GAME_SLUG_A, LA, M, N, NOW, OA, SLUG, _armed, _census, _fill, _game_world, _his,
    _Http, _kinds, _NoClose, _places, _pool, _reduce_world, _run, _short_book, _short_world, _shorts_on,
    _tick, _Venue,
)

IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
YML = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"


async def _spin(until, n=2000):
    """Yield to the loop until `until()` holds (the fakes never sleep)."""
    for _ in range(n):
        if until():
            return True
        await asyncio.sleep(0)
    return until()


def _pause_read(monkeypatch, pick):
    """Pause the first `_read_market` call `pick(t, cid)` selects until
    the test resumes it; returns a getter for the (paused, resume)
    events, made inside the loop."""
    orig = ml._read_market
    ev: dict = {}

    def _events():
        if "paused" not in ev:
            ev["paused"], ev["resume"] = asyncio.Event(), asyncio.Event()
        return ev["paused"], ev["resume"]

    async def _hook(t, whale, cid, *a, **kw):
        paused, resume = _events()
        if pick(t, cid) and not paused.is_set():
            paused.set()
            await resume.wait()
        return await orig(t, whale, cid, *a, **kw)
    monkeypatch.setattr(ml, "_read_market", _hook)
    return _events


def _game_usd(p):
    """What the game holds and rests: every non-closed book's ledger at
    cost plus the unfilled remainder of every open BUY at its wire."""
    held = sum(float(b["ledger_net"] or 0) * float(b["avg_cost"] or 0) for b in p.books.values()
               if b["state"] != "closed")
    resting = sum((o["qty"] - o["booked_filled"]) * o["wire"] for o in p.orders.values()
                  if o["state"] in ("placing", "open", "unknown") and o["side"] == rules.BUY)
    return round(held + resting, 2)


def _burst_world(n):
    """`n` markets he holds 300 of, each with its own tokens and its own
    per-market rows, every one an increase of 300 for us."""
    fills, snap, by_market = [], {}, {}
    for i in range(n):
        fills.append(_fill(f"tokL{i}", "BUY", 300.0, 0.31, NOW - 3000))
        snap[f"tokL{i}"], snap[f"tokO{i}"] = 300.0, 0.0
    p = _pool(fills=fills, snap=snap)               # the pool copies an empty fills list: build it first
    cids = []
    for i in range(n):
        cid, slug, la, oa = f"0xw{i}", f"aec-atp-w{i}-x{i}-2026-09-06", f"tokL{i}", f"tokO{i}"
        p.markets[cid] = {"closed": False, "resolved": False, "resolved_prices": None}
        p.token_index.update({la: 1, oa: 0})
        p.token_cid.update({la: cid, oa: cid})
        p.add_book(ledger=0, condition_id=cid, us_market_slug=slug, long_asset=la, other_asset=oa,
                   game_key=le._us_game_key(slug))
        by_market[cid] = [{"conditionId": cid, "asset": la, "size": 300}, {"conditionId": cid, "asset": oa, "size": 0}]
        cids.append(cid)
    return p, _ByMarket(by_market), cids


# --------------------------------------------- Q1: double placement

def test_review_q1_a_full_tick_started_beside_a_fast_ticks_take_sizes_the_book_again(monkeypatch):
    """His 300 @ 0.30; the ask is through his level, so the entry is an
    IOC take at his cent that fills at once. The fast tick holds the
    book's lock through its read; a full tick starts beside it (the
    poll, or a wake past WAKE_MIN_GAP_S): its step O reads no order row
    (the fast tick has not placed yet), its books read carries
    ledger_net 0, its walk waits on the lock. The fast tick places, the
    IOC fills 300, the ledger is 300, the lock is released. The full
    tick's _tick_book then runs on ITS dict (ledger 0, open_order_id
    None) and its own open_by_book / nonterminal (empty): target 300
    over ledger 0 is a second take of 300. The unique index cannot
    refuse it (the first row is terminal). Expected: 300 placed in all.
    The fold: the fast tick holds _TICK_LOCK through its read, so the
    full tick WAITS on the tick lock (never on the book's) and starts
    only once the take is booked -- it reads the row at ledger 300, on
    target, and sizes nothing (the venue's own position moves with the
    IOC fill, as the real venue's positions walk would carry it)."""
    his = [_fill(M, "BUY", 300, 0.30, NOW - 3000)]
    p = _pool(fills=his)
    b = p.add_book(ledger=0)

    def _ioc(venue, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        assert tif == IOC_TIF and not sell
        f = min(float(venue.ioc_fill), float(qty))
        venue.portfolio.held[slug] = float(venue.portfolio.held.get(slug, 0.0)) + f
        return {"ok": f > 0, "order_id": oid, "status": "filled" if f >= qty else "canceled",
                "fill_price": price if f > 0 else None, "filled_shares": f, "raw": {"response": {"id": oid}}}
    v = _Venue(bid=0.28, ask=0.29, ioc_fill=300.0, place=_ioc)
    http = _Http()
    _walk()
    events = _pause_read(monkeypatch, lambda t, cid: t.fast)

    async def _drive():
        paused, resume = events()
        p.clock = NOW + 1
        fast = asyncio.create_task(ml.fast_tick_once(p, v, http, cids=[CID], now_ts=NOW + 1))
        assert await _spin(paused.is_set)
        lk = ml._lock_for(b["id"])
        assert lk.locked() and ml._TICK_LOCK.locked() and ml._fast_holding, \
            "the fast tick holds the book's lock and the tick lock through its read"
        full = asyncio.create_task(ml.tick_once(p, v, http, now_ts=NOW + 2))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters)), "the full tick waits on the fast tick's hold"
        assert not lk._waiters and ml._full_tick is None, "not started: nothing read beside the fast tick"
        resume.set()
        return await fast, await full
    fs, st = _run(_drive())
    pl = _places(v)
    assert fs["fast"]["placed"] == 1 and st["status"] != "overlap"
    assert [c[5] for c in pl] == [IOC_TIF] * len(pl), pl
    # THE MONEY: one take of 300 for one fill of his, 300 held against his 300
    assert sum(c[3] for c in pl) == 300, pl
    assert b["ledger_net"] == 300 and _census(st, "on_target") == 1 and _census(st, "take_first") == 1, \
        "the full tick read the take booked (the fast tick's name folded in) and sized nothing"


def test_review_q1_the_full_tick_reads_the_fast_ticks_rest_standing_and_never_reaches_a_second_insert(monkeypatch):
    """The same race with a RESTING entry (the ask above his level). As
    reviewed, the full tick's second INSERT was refused by
    mirror_orders_one_open_per_book (`open_order_pending`) -- the index
    was the guard, not anything the fast path built, and a take's row is
    terminal by the time the full tick plans (CRITICAL-1). The fold: the
    full tick WAITS on the fast tick's hold, its step O then reads the
    rest among the rows and its books read the pointer, so its plan
    KEEPS the standing rest (`open_order_pending` at the plan, the name
    a kept rest has always had) and never sends a second INSERT -- one
    `ml-order-insert` in the pool's log, the fast tick's; the index is
    never asked. The fast tick's own names land on its own census and
    fold into the full tick's."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    http = _Http()
    _walk()
    events = _pause_read(monkeypatch, lambda t, cid: t.fast)

    async def _drive():
        paused, resume = events()
        p.clock = NOW + 1
        fast = asyncio.create_task(ml.fast_tick_once(p, v, http, cids=[CID], now_ts=NOW + 1))
        assert await _spin(paused.is_set)
        full = asyncio.create_task(ml.tick_once(p, v, http, now_ts=NOW + 2))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters))
        resume.set()
        return await fast, await full
    fs, st = _run(_drive())
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 300 and fs["fast"]["placed"] == 1
    inserts = [s for _k, s, _a in p.sent if "ml-order-insert" in s]
    assert len(inserts) == 1, "one INSERT ever sent (the fast tick's): nothing for the index to refuse"
    assert _census(st, "open_order_pending") == 1 and b["last_reason"] == "open_order_pending", \
        "the full tick's plan kept the rest standing"
    assert _census(fs, "rest_placed") == 1 and _census(st, "rest_placed") == 1, "its own census, folded in"
    assert len([o for o in p.orders.values() if o["state"] == "open"]) == 1 and b["open_order_id"] is not None
    assert st["orders_open"] == 1 and st["woken"] == [] and "cancel" not in _kinds(v)


def test_review_q1_a_non_terminal_row_with_no_pointer_on_the_book_is_order_open_before_any_read():
    """A `placing` row whose persist of the book's open_order_id never
    landed (the process died between orders.create and the persist, the
    case _reconcile_placing exists for), or a rest whose cancel read
    'unknown': the book's pointer is NULL and the row is non-terminal.
    The fast tick reads the DB rows (t.nonterminal), so the gate refuses
    `order_open` on the rows alone -- no quote read, no plan, the market
    left to step O (kills M03: the gate reading open_order_id only)."""
    for state, oid in (("placing", None), ("unknown", "oid-9")):
        p = _pool()
        b = p.add_book(ledger=0)
        p.add_order(b, state=state, order_id=oid, placed_ts=NOW - 5)
        b["open_order_id"] = None
        v = _Venue()
        _walk()
        fs = _fast(p, v)
        assert _skips(fs) == {CID: "order_open"}, (state, _skips(fs))
        assert [c for c in v.calls if c[0] == "bbo"] == [] and not _places(v) and b["last_plan"] is None
        assert fs["orders_open"] == 1


def test_review_q1_two_wakes_two_seconds_apart_the_second_fast_tick_sees_the_firsts_rest():
    """The inverse: fast ticks are serialised by _FAST_LOCK and _place
    persists the row before _tick_book returns, so the second fast tick's
    open-orders read and the row's open_order_id both carry the first's
    rest: `order_open`, no second read, no second placement."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    _walk()
    fs = _fast(p, v)
    assert fs["fast"]["placed"] == 1 and b["open_order_id"] is not None
    fs2 = _fast(p, v, now=NOW + 3)
    assert _skips(fs2) == {CID: "order_open"} and len(_places(v)) == 1
    assert [c[1] for c in v.calls if c[0] == "bbo"] == [SLUG], "the second fast tick read nothing"
    # a requeued market the full tick answered meanwhile: order_open too
    ml._FAST_WOKEN[CID] = 1
    fs3 = _run(ml.fast_tick_once(p, v, _Http(), now_ts=NOW + 5))
    assert fs3["fast"]["tries"] == {CID: 1} and _skips(fs3) == {CID: "order_open"} and ml._FAST_WOKEN == {}


# --------------------------------------------- Q2: the game's room

def test_review_q2_a_fast_ticks_increase_beside_the_full_ticks_candidate_stage_is_outside_the_games_room(monkeypatch):
    """A (the total line) holds 300 @ 0.50, on target at the full tick's
    walk; the moneyline of the same game is a CANDIDATE the full tick
    reads after its walk. While the full tick sits in that candidate's
    read, his fill of 7,000 lands on A: the fast tick's gate passes (the
    walk is past A's whole game, A's lock is free, nothing is open) and
    it rests A's increase of 3,500 @ 0.49 = $1,715. The full tick then
    sizes the moneyline on the room it computed off A as its walk left
    it ($150): the new rest is not in its open_by_book, A is not in its
    nonterminal, so the room reads $2,350 and B rests 3,000 @ 0.49 =
    $1,470. The game holds and rests $3,335 against a $2,500 cap.
    The fold: the two never run beside each other -- the fast tick
    holds _TICK_LOCK while it rests A's 3,500 @ 0.49, the full tick
    WAITS on that hold and its candidate stage then reads the room less
    A's rest (step O carries it in open_by_book; the resting add is
    valued at the 0.50 mark, $1,750, beside the $150 held): B is scaled
    to the $600 left, 1,200 shares, and the game rests $2,453."""
    p, a, b, v, http = _game_world(monkeypatch, a_ledger=300)
    del p.books[b["id"]], p.rows[b["standing_row_id"]]           # the moneyline is a candidate
    # his fill lands on A and wakes the fast path, the poll's tick a moment behind
    p.fills.append(_fill(LA, "BUY", 7000.0, 0.50, NOW + 1))
    p.snap[LA] = 7600.0
    for row in http.rows:
        if row["asset"] == LA:
            row["size"] = 7600
    _walk({GAME_SLUG_A: 300.0}, NOW)
    events = _pause_read(monkeypatch, lambda t, cid: t.fast and cid == CID_A)

    async def _drive():
        paused, resume = events()
        p.clock = NOW + 2
        fast = asyncio.create_task(ml.fast_tick_once(p, v, http, cids=[CID_A], now_ts=NOW + 2))
        assert await _spin(paused.is_set), "the fast tick is inside A's read"
        full = asyncio.create_task(ml.tick_once(p, v, http, now_ts=NOW + 3))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters)), "the full tick waits on the fast tick's hold"
        assert ml._full_tick is None, "its walk and its candidate stage have not begun"
        resume.set()
        return await fast, await full
    fs, st = _run(_drive())
    assert _skips(fs) == {} and fs["fast"]["placed"] == 1, fs["fast"]
    a_rest = [o for o in p.orders.values() if o["book_id"] == a["id"] and o["state"] == "open"]
    assert len(a_rest) == 1 and a_rest[0]["qty"] == 3500 and a["target"] == 3800
    assert st["status"] != "overlap" and st["orders_open"] == 1, "the full tick's step O read A's rest"
    assert a_rest[0]["wire"] == 0.49 and a["last_reason"] == "open_order_pending", "kept standing by the full tick"
    new = [bk for bk in p.books.values() if bk["us_market_slug"] == SLUG]
    assert len(new) == 1 and new[0]["last_plan"]["game_exposure"] == 1900.0, "A's $150 held and its rest at the mark"
    assert new[0]["last_plan"]["game_cap"] == "game_cap_scaled" and new[0]["last_plan"]["game_room"] == 600.0
    assert new[0]["target"] == 1200 and new[0]["target"] < new[0]["last_plan"]["target_raw"]
    assert _census(st, "game_cap_scaled") >= 1
    # THE MONEY: the game holds and rests inside the per-game cap
    assert _game_usd(p) == 2453.0 and _game_usd(p) <= float(rules.MIRROR_NET_CAP_USD), _game_usd(p)


def test_review_q2_a_siblings_open_order_in_the_rows_makes_the_room_unreadable_and_a_booked_fill_reads_fresh():
    """The claim the notes make, verified: a sibling with an order open
    in the DB rows is non-terminal to the fast tick, so the game's room
    reads unreadable (`game_unreadable`, room 0) and no increase is
    sized; once that order FILLED and the last full tick booked it
    (its row carries the ledger, nothing open), the fast tick's fresh
    rows read the sibling's held exposure and the room is the cap less
    it -- the sibling's booked fill is on the sibling's row, not on the
    stale walk."""
    p = _pool()
    a = p.add_book(ledger=0)
    p.markets["0xsib"] = {"closed": False, "resolved": False, "resolved_prices": None}
    sib = p.add_book(ledger=0, target=300, condition_id="0xsib", us_market_slug=SLUG + "-total",
                     long_asset="tokLs", other_asset="tokOs", game_key=GAME_KEY)
    o = p.add_order(sib, qty=300, wire=0.30)
    v = _Venue()
    v.rest("oid-1", slug=SLUG + "-total")
    _walk()
    fs = _fast(p, v)
    assert _census(fs, "game_unreadable") == 1 and not _places(v)
    assert a["last_plan"]["game_exposure"] is None and a["last_plan"]["game_room"] == 0
    # the sibling's order filled and booked by a full tick: its row now carries the ledger
    o.update(state="filled", booked_filled=300.0, filled=300.0)
    sib.update(ledger_net=300, avg_cost=0.30, open_order_id=None)
    ml._last_filled = {sib["id"]}
    v.calls.clear()
    fs2 = _fast(p, v, now=NOW + 3)
    assert _skips(fs2) == {} and fs2["fast"]["placed"] == 1
    assert a["last_plan"]["game_exposure"] == 90.0 and a["last_plan"]["game_room"] == 2410.0


# --------------------------------------------- Q3: the rails

def test_review_q3_the_day_stop_the_post_only_latch_and_a_venue_ledger_disagreement_read_the_same(monkeypatch):
    # the day stop: filled today at the cap -> mirror_day_cap on both roads
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 50.0)
    p1 = _pool()
    b1 = p1.add_book(ledger=0)
    p1.add_order(b1, state="filled", booked=300.0, placed_ts=NOW - 100, cash_usd=93.0)
    st = _tick(p1, _Venue())
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    p2.add_order(b2, state="filled", booked=300.0, placed_ts=NOW - 100, cash_usd=93.0)
    _walk()
    fs = _fast(p2, _Venue())
    assert b1["last_reason"] == b2["last_reason"] == "mirror_day_cap"
    assert _census(st, "mirror_day_cap") >= 1 and _census(fs, "mirror_day_cap") >= 1 and _skips(fs) == {}
    # the post-only latch: off, both rest without it
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)
    ml._POST_ONLY_OK = False
    p3 = _pool()
    p3.add_book(ledger=0)
    v3 = _Venue()
    _tick(p3, v3)
    p4 = _pool()
    p4.add_book(ledger=0)
    v4 = _Venue()
    _walk()
    _fast(p4, v4)
    assert _places(v3)[0][7] is False and _places(v4)[0][7] is False
    ml._POST_ONLY_OK = True
    # venue vs ledger: the walk says 300 held, the ledger 0 -> a SUSPECT by
    # the same name on both (E16: the first read never freezes; the fast
    # tick's is a cached walk, so it can never be the second)
    p5 = _pool()
    b5 = p5.add_book(ledger=0)
    _tick(p5, _Venue(held={SLUG: 300}))
    p6 = _pool()
    b6 = p6.add_book(ledger=0)
    _walk({SLUG: 300.0})
    fs6 = _fast(p6, _Venue())
    assert b5["state"] == b6["state"] == "live" and b5["frozen_reason"] is b6["frozen_reason"] is None
    assert b5["last_plan"]["reason"] == b6["last_plan"]["reason"] == "venue_suspect_hold" and _skips(fs6) == {}
    assert b5["last_plan"]["venue_ledger_suspect"]["walk_at"] == NOW and b6["last_plan"]["venue_ledger_suspect"]["walk_at"] is None
    assert b6["last_plan"]["venue_ledger_suspect"]["cached"] == 1


def test_review_q3_a_short_books_cover_after_his_buy_back_goes_through_the_fast_tick_as_through_the_full(monkeypatch):
    """His buy-back in long space is 0.30 (his SELL of the other token at
    0.70): a SHORT book we mirror covers -- an exit, exempt from the ops
    cap and the game cap -- resting post-only at floor(his) 0.30 with
    the ask at 0.32 (`exit_out_of_tol`), the same row, wire and names
    on both roads."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]

    async def _held(t, slug):
        return 300, 0.32
    monkeypatch.setattr(ml, "_pm_held", _held)
    gone = [{"conditionId": CID, "asset": M, "size": 0}, {"conditionId": CID, "asset": N, "size": 0}]
    p1 = _short_world(fills=fills, snap=None)
    b1 = _short_book(p1, ledger=-300)
    v1 = _NoClose(bid=0.30, ask=0.32, held={SLUG: -300})
    st = _tick(p1, v1, http=_Http(rows=list(gone)))
    p2 = _short_world(fills=fills, snap=None)
    b2 = _short_book(p2, ledger=-300)
    v2 = _NoClose(bid=0.30, ask=0.32, held={SLUG: -300})
    _walk({SLUG: -300.0})
    ml._fast_ops = int(rules.MIRROR_MAX_ORDER_OPS_PER_TICK)          # the ops budget spent: an exit is exempt
    fs = _fast(p2, v2, http=_Http(rows=list(gone)))
    assert _skips(fs) == {} and fs["fast"]["placed"] == 1
    assert [c[1:] for c in _places(v2)] == [c[1:] for c in _places(v1)] == [(SLUG, 0.30, 300, True, GTC_TIF, "ORDER_INTENT_BUY_SHORT", True, None)]
    for k in ("exit_px", "exit_px_src", "exit_ceiling", "exit_cover", "exit_rest", "kind", "side", "qty", "reason"):
        assert b1["last_plan"][k] == b2["last_plan"][k], k
    assert b1["last_reason"] == b2["last_reason"]
    for name in ("exit_out_of_tol", "short_cover_out_of_tol", "short_cover_rest", "flatten_rested", "ops_capped"):
        assert _census(st, name) == _census(fs, name), name


# --------------------------------------------- Q4: the price and the read

def test_review_q4_the_fast_tick_reads_through_the_same_functions_with_no_fast_branch():
    read_path = "".join(inspect.getsource(f) for f in (ml._tick_book, ml._read_market, ml._bbo,
                                                       ml._market_snap, ml._snapshot, ml._act, ml._place,
                                                       ml._place_reserved))
    # FILL lane 9 (migration 061): the placement RECORDS which path placed the row -- `bool(t.fast)` as the
    # 061 INSERT's last argument, chosen by the column probe `t.fast_col is True` -- and that is the only
    # reading of the fast flag on the read or the placement: a value written, never a branch on the path
    # (the tick's `t.fast` decides nothing about what is read, sized, priced or sent)
    recorded = read_path.replace("t.order_cols is True and t.fast_col is True", "").replace("bool(t.fast)", "")
    assert "t.fast" not in recorded and "fast_tick" not in read_path, "no fast-tick branch on the read or the placement"
    assert "if t.fast" not in read_path and "not t.fast" not in read_path
    assert read_path.count("bool(t.fast)") == 1 and read_path.count("t.fast_col is True") == 1
    assert "await asyncio.to_thread(ms._paced_bbo, t.pmus, slug)" in inspect.getsource(ml._bbo)
    assert "abs(t.now - ts) > ms.SNAP_MAX_AGE_S" in inspect.getsource(ml._market_snap)
    # the fast tick's `now` is the real clock at its start; tick_once's is the real clock once it holds the lock (FILL lane 7)
    assert "now = time.time() if now_ts is None else float(now_ts)" in inspect.getsource(ml.fast_tick_once)
    assert "now = time.time() if now_ts is None else float(now_ts)" in inspect.getsource(ml.tick_once)


# --------------------------------------------- Q5: the wake's call site

def test_review_q5_the_wake_never_raises_and_never_awaits(monkeypatch):
    src = inspect.getsource(pipeline._mirror_wake)
    assert "await" not in src and inspect.iscoroutinefunction(pipeline._mirror_wake) is False

    def _boom(cid):
        raise RuntimeError("notify down")
    monkeypatch.setattr(le, "_mirror_notify", _boom)
    pipeline._mirror_wake("rn1", CID)                       # swallowed
    monkeypatch.setattr(le, "mirror_mode", lambda u: 1 / 0)
    pipeline._mirror_wake("rn1", CID)                       # swallowed
    monkeypatch.undo()
    monkeypatch.setenv("PMUS_MIRROR", "on;garbage")         # a bad env reads as off: no wake, no raise
    monkeypatch.setenv("PMUS_MIRROR_WHALES", ",,rn1,")
    ml._WOKEN.clear()
    ml._FAST_WOKEN.clear()
    pipeline._mirror_wake("rn1", CID)
    assert ml._WOKEN == set() and ml._FAST_WOKEN == {}
    monkeypatch.setenv("PMUS_MIRROR", "on")
    pipeline._mirror_wake("RN1 ", CID)                      # the allowlist compare is lowered / stripped
    assert ml._WOKEN == {CID} and ml._FAST_WOKEN == {CID: 0}
    # the ingest of a fresh row still returns its id when the wake raises underneath
    monkeypatch.setattr(le, "_mirror_notify", _boom)
    ml._WOKEN.clear()
    (tid, new), _pool_ = _ingest(monkeypatch, _ev(side="SELL"))
    assert (tid, new) == (7, True)


def test_review_q5_a_silent_backfill_row_notify_false_never_wakes(monkeypatch):
    from tests.test_e9_fast_path import _IngestPool
    ml._WOKEN.clear()
    ml._FAST_WOKEN.clear()
    pool = _IngestPool(True)

    async def _get_pool():
        return pool

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(pipeline, "get_pool", _get_pool)
    monkeypatch.setattr(pipeline, "publish", _noop)
    monkeypatch.setattr(pipeline, "_write_outbox", _noop)
    assert _run(pipeline.ingest_trade_result(_ev(side="BUY"), notify=False)) == (7, True)
    assert ml._WOKEN == set() and ml._FAST_WOKEN == {}
    src = inspect.getsource(pipeline.ingest_trade_result)
    assert src.index("if not notify:") < src.index("_mirror_wake(")
    assert "pre_enriched = ev.condition_id is not None" in src, "a row with its condition wakes once: _enrich never runs for it"


# --------------------------------------------- Q6: the loop

def test_review_q6_a_wake_past_the_gap_runs_the_fast_tick_first_and_the_full_tick_waits_then_reads_it_answered(monkeypatch):
    """main() waits on `asyncio.wait_for(_WAKE.wait(), ...)`: the wait is a
    task of its own, so a wake resumes main one hop after the fast run's
    first step. The fast tick therefore STARTS first (takes the tick
    lock, pops the market, enters its prelude); as reviewed the full
    tick then started beside it and the gate refused `full_tick_pending`.
    The fold: the full tick WAITS on the fast tick's hold, the fast tick
    answers the market (its placement), and the full tick then reads it
    woken first with the rest standing -- one placement. One loop,
    cooperative; no thread, no call_soon_threadsafe."""
    ran = []
    orig = ml.fast_tick_once

    async def _rec(*a, **kw):
        st = await orig(*a, **kw)
        ran.append(st)
        return st
    monkeypatch.setattr(ml, "fast_tick_once", _rec)

    async def _nosleep(s):
        await asyncio.sleep(0)
    monkeypatch.setattr(ml, "_fast_sleep", _nosleep)
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    http = _Http()

    async def _main_like():
        ml._last_walk = ({}, time.time())
        ml._arm_fast(p, v, http)
        wait = asyncio.create_task(asyncio.wait_for(ml._WAKE.wait(), timeout=30))
        await asyncio.sleep(0)
        ml.notify(CID)
        await wait
        assert ml._FAST_WOKEN == {}, "the fast run already took the market off the set (under the lock)"
        assert ml._FAST_LOCK.locked() and ml._TICK_LOCK.locked() and ml._fast_holding, \
            "the fast tick is inside its prelude, holding the tick lock, when main resumes"
        st = await ml.tick_once(p, v, http)
        assert ml._fast_task.done(), "the full tick ran only once the fast tick was done"
        await ml._fast_task
        return st
    st = _run(_main_like())
    assert st["woken"] == [CID] and st["status"] != "overlap" and _places(v) and b["open_order_id"] is not None
    assert [r["fast"]["skipped"] for r in ran] == [{}] and ran[0]["fast"]["placed"] == 1
    assert len(_places(v)) == 1 and st["orders_open"] == 1 and _census(st, "fast_tick_placed") == 1
    # the source: the fast tick holds the tick lock inside the fast lock; the full tick
    # waits on a fast tick's hold and returns `overlap` on a full tick's; the fast tick's
    # body and its book path take no tick lock of their own
    assert "async with _FAST_LOCK, _TICK_LOCK:" in inspect.getsource(orig)     # the real fast_tick_once
    src = inspect.getsource(ml.tick_once)
    assert "if _TICK_LOCK.locked() and not _fast_holding:" in src and "_FAST_LOCK" not in src
    assert src.index("not _fast_holding:") < src.index("async with _TICK_LOCK:")
    assert "await" not in src[src.index("not _fast_holding:"):src.index("async with _TICK_LOCK:")]
    assert "_TICK_LOCK" not in inspect.getsource(ml._fast_tick) and "_TICK_LOCK" not in inspect.getsource(ml._fast_book)


def test_review_q6_a_wake_from_a_foreign_thread_schedules_nothing_and_the_poll_reads_it():
    seen = {}

    async def _in_loop():
        ml._arm_fast(_pool(), _Venue(), _Http())

        def _from_thread():
            ml.notify(CID)                      # no running loop in this thread
            seen["task"] = ml._fast_task
        th = threading.Thread(target=_from_thread)
        th.start()
        th.join()
    _run(_in_loop())
    assert seen["task"] is None and ml._FAST_WOKEN == {CID: 0} and CID in ml._WOKEN
    src = inspect.getsource(ml._fast_wake)
    assert "asyncio.get_running_loop()" in src and "call_soon_threadsafe" not in src


# --------------------------------------------- Q7: the budget

def test_review_q7_the_full_ticks_e2_guard_and_ops_budget_start_from_the_fast_ticks_spend():
    """As reviewed (MEDIUM-2) the two counters were reset, not seeded, at
    the full tick's start and the window was counted twice; the fold
    seeds `t.guard_calls` / `t.ops` from them as fast_tick_once seeds
    its own, then resets the three."""
    ml._fast_guard_calls = int(rules.MIRROR_VENUE_CALLS_PER_TICK)      # the fast ticks spent the E2 rail
    p = _pool()                                                        # a candidate market
    st = _tick(p, _Venue())
    assert _census(st, "venue_calls_capped") == 1 and not p.books
    ml._fast_ops = int(rules.MIRROR_MAX_ORDER_OPS_PER_TICK)            # and the ops budget
    p2 = _pool()
    p2.add_book(ledger=0)
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "ops_capped") == 1 and not _places(v2)
    assert ml._fast_guard_calls == 0 and ml._fast_ops == 0, "seeded, then the counters start over"
    # an exit is exempt from the seeded ops budget as from its own
    p3, b3, v3, http3 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
    ml._fast_ops = int(rules.MIRROR_MAX_ORDER_OPS_PER_TICK)
    st3 = _tick(p3, v3, http=http3)
    assert _census(st3, "exit_take") == 1 and len(_places(v3)) == 1 and b3["ledger_net"] == 100
    src = inspect.getsource(ml.tick_once)
    assert "t.guard_calls, t.ops = int(_fast_guard_calls), int(_fast_ops)" in src
    assert src.index("int(_fast_ops)") < src.index("_fast_calls = _fast_guard_calls = _fast_ops = 0")


def test_review_q7_the_guarded_calls_of_one_fast_tick_seed_the_next(monkeypatch):
    """The E2 soft guard's counter carries ACROSS fast ticks: a fast
    tick's placement spends guarded calls, the next fast tick starts
    from them and a candidate is `venue_calls_capped` once they reach
    the rail (kills M35: the spend not accumulated between fast ticks)."""
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()
    fs = _fast(p, v)
    assert fs["fast"]["placed"] == 1
    g = ml._fast_guard_calls
    assert g >= 2, "the placement's guarded calls (preview and create) carried"
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", g)
    p2 = _pool()                                                       # a candidate market
    v2 = _Venue()
    fs2 = _fast(p2, v2, now=NOW + 3)
    assert _skips(fs2) == {CID: "venue_calls_capped"} and not p2.books and [c for c in v2.calls if c[0] == "bbo"] == []


def test_review_q3_a_frozen_book_is_refused_by_the_gate_before_the_lock_and_the_row_read():
    """`not_live` is decided by the gate on the books read (no lock taken,
    no second row read); the re-check under the lock names the same
    (kills M33, which the builder's pin cannot: the re-check hides the
    gate's clause)."""
    p = _pool()
    p.add_book(ledger=300, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 100)
    v = _Venue()
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "not_live"} and [c for c in v.calls if c[0] == "bbo"] == []
    assert not any("ml-book-read" in s for _k, s, _a in p.sent), "refused before the row is re-read under the lock"
    src = inspect.getsource(ml._fast_gate)
    assert src.index('return "book_locked"') < src.index('return "not_live"') < src.index('return "order_open"')


def test_review_q7_a_burst_spends_at_most_the_rail_plus_one_markets_overshoot_and_hot_books_are_still_read():
    """A burst: fast ticks refuse by `budget_spent` once the rail is
    spent (each market checked before its own calls, so at most one
    market's four past the rail); the next full tick subtracts them from
    the quiet and candidate shares only -- its hot books are read
    whatever the fast ticks spent, the shares at their floors."""
    ml._fast_calls = 58
    p, http, cids = _burst_world(3)
    v = _Venue()
    _walk()
    fs = _fast(p, v, cids=cids, http=http)
    assert fs["short"]["timing"]["venue_calls"] == 4 and len(_places(v)) == 1
    assert _skips(fs) == {"0xw1": "budget_spent", "0xw2": "budget_spent"} and ml._fast_calls == 62
    # the full tick: hot books read, the shares at their floors
    p2 = _pool()
    p2.add_book(ledger=300)
    v2 = _Venue(held={SLUG: 300})
    st = _tick(p2, v2)
    tm = st["short"]["timing"]
    assert tm["read"] == 1 and tm["quiet_budget"] == 0 and tm["cand_budget"] == ml.CAND_MIN_PER_TICK
    assert [c[1] for c in v2.calls if c[0] == "bbo"] == [SLUG] and ml._fast_calls == 0


# --------------------------------------------- Q8: part 1's list

def test_review_q8_the_bound_keeps_the_newest_twenty_and_the_five_oldest_read_unseen_to_the_preset():
    fills = [_fill(M, "BUY", 10, 0.31, NOW - 3000 + i, detected_at=NOW - 2999 + i) for i in range(25)]
    p = _pool(fills=fills, snap={M: 250.0, N: 0.0})
    b = p.add_book(ledger=250)
    v = _Venue(held={SLUG: 250})
    rows = [{"conditionId": CID, "asset": M, "size": 250}, {"conditionId": CID, "asset": N, "size": 0}]
    _tick(p, v, http=_Http(rows=list(rows)))
    seen = b["last_plan"]["his_fills_seen"]
    ids = [e["id"] for e in seen]
    assert ids == [str(NOW - 3000 + i) for i in range(5, 25)], "the five oldest dropped"
    # the next tick: the five are seen again as new, and dropped again -- nothing renamed, nothing kept
    _tick(p, v, now=NOW + 30, http=_Http(rows=list(rows)))
    assert b["last_plan"]["his_fills_seen"] == seen
    # what the preset reads for a fill with no entry: 'unseen' (the CASE in the YAML)
    block = YML.read_text()
    block = block[block.index("fills-answered) SQL="):block.index("close-rows) SQL=")]
    assert "WHEN s.e IS NULL THEN 'unseen'" in block


def test_review_q8_a_take_that_filled_is_named_by_its_plan_never_answered_by_an_order_id():
    """`order` is the open_by_book row of a book in placed_books; an IOC
    that filled is terminal and not in open_by_book, so the entry names
    the plan (`take`) with order None, and the preset counts it under
    that name, not under `answered`."""
    his = [_fill(M, "BUY", 300, 0.30, NOW - 3000)]
    p = _pool(fills=his)
    b = p.add_book(ledger=0)
    v = _Venue(bid=0.28, ask=0.29, ioc_fill=300.0)
    st = _tick(p, v)
    assert _census(st, "take_first") == 1 and b["ledger_net"] == 300
    seen = b["last_plan"]["his_fills_seen"]
    assert len(seen) == 1 and seen[0]["order"] is None and seen[0]["name"] == "take", seen


def _preset_sql():
    text = YML.read_text()
    block = text[text.index("fills-answered) SQL="):text.index("close-rows) SQL=")]
    sql = block.split('SQL="', 1)[1].split('"; TO=', 1)[0]
    return [s.strip() for s in sql.split(";") if s.strip()]


PRESET_DDL = """
CREATE TABLE mirror_books(id int, whale text, condition_id text, us_market_slug text, state text,
    last_reason text, last_plan jsonb, updated_at timestamptz);
CREATE TABLE trades(id int, whale_id int, condition_id text, market_slug text, side text, size numeric,
    price numeric, ts timestamptz, detected_at timestamptz, asset text, source text, tx_hash text);
CREATE TABLE whales(id int, username text);
"""


async def _preset_world(stmt_index):
    """hard2/preset_ddl.sql extended with the columns the preset reads
    that the stand-in lacked: mirror_books.last_plan (jsonb),
    mirror_books.updated_at, trades.detected_at (said in the review),
    and -- the landing fold's chain-first collapse -- trades.source and
    trades.tx_hash, both NULL here (a poll-only row with no tx is kept
    whole: the rows read exactly as before). Three fills of his on c1
    inside the hour; the book's row names fill 1 answered by order 9,
    fill 2 `game_cap_full`, fill 3 unseen."""
    stmts = _preset_sql()
    assert len(stmts) == 2
    admin, conn, name = await _scratch()
    try:
        for ddl in ("DROP TABLE whales", "DROP TABLE trades"):
            await conn.execute(ddl)
        for s in PRESET_DDL.split(";"):
            if s.strip():
                await conn.execute(s)
        await conn.execute("INSERT INTO whales VALUES (1, 'rn1')")
        await conn.execute(
            "INSERT INTO trades VALUES"
            " (1, 1, 'c1', 'wta-x-y', 'BUY', 100, 0.40, now() - interval '330 seconds', now() - interval '329 seconds', 'a1'),"
            " (2, 1, 'c1', 'wta-x-y', 'BUY', 50, 0.50, now() - interval '320 seconds', now() - interval '319 seconds', 'a1'),"
            " (3, 1, 'c1', 'wta-x-y', 'SELL', 10, 0.60, now() - interval '305 seconds', now() - interval '304 seconds', 'a1')")
        e1 = await conn.fetchval("SELECT extract(epoch FROM ts)::float8 FROM trades WHERE id = 1")
        e2 = await conn.fetchval("SELECT extract(epoch FROM ts)::float8 FROM trades WHERE id = 2")
        plan = {"his_fills_seen": [
            {"id": "1", "ts": e1, "det": e1 + 1, "at": e1 + 30, "order": 9, "name": "rest_placed"},
            {"id": "2", "ts": e2, "det": e2 + 1, "at": e2 + 40, "order": None, "name": "game_cap_full"}]}
        await conn.execute("INSERT INTO mirror_books VALUES (5, 'rn1', 'c1', 'aec-wta-x-y', 'live', 'rest_placed', "
                           "$1::jsonb, now())", json.dumps(plan))
        return [dict(r) for r in await conn.fetch(stmts[stmt_index])]
    finally:
        await _drop(admin, conn, name)


def test_review_q8_the_fills_answered_presets_first_statement_names_from_the_row_on_real_postgres():
    by_name = {r["name"]: r for r in _run(_preset_world(0))}
    assert set(by_name) == {"answered", "game_cap_full", "unseen"}
    assert by_name["answered"]["fills"] == 1 and float(by_name["answered"]["his_usd"]) == 40.0
    assert float(by_name["answered"]["lag_med_s"]) == 30.0 and float(by_name["game_cap_full"]["lag_med_s"]) == 40.0
    assert by_name["unseen"]["fills"] == 1 and by_name["unseen"]["lag_med_s"] is None
    assert [r["name"] for r in sorted(by_name.values(), key=lambda r: -float(r["his_usd"]))] == ["answered", "game_cap_full", "unseen"]


def test_review_q8_the_fills_answered_presets_second_statement_the_top_unanswered_runs_on_real_postgres():
    """As reviewed (MEDIUM-3) the second statement read FROM n, a CTE of
    the FIRST statement, and a CTE is scoped to its own statement on
    Postgres; folded before the review landed: it carries its own chain."""
    rows = _run(_preset_world(1))
    assert [r["name"] for r in rows] == ["game_cap_full", "unseen"] and [r["id"] for r in rows] == [2, 3]


def test_review_q8_the_help_line_regenerated_from_the_case_block():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels and len(names) == len(set(names)) and "fills-answered" in names


# --------------------------------------------- Q9: the superseded pins and the column

def test_review_q9_his_fills_hands_back_detected_at_on_real_postgres():
    """As reviewed (MEDIUM-1) his_fills added detected_at to its inner CTE
    and the outer SELECT never projected it, so on the real schema every
    his_fills_seen entry carried det None; the fold projects
    `d.detected_at`."""
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await conn.execute(
                "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, size, price, notional, ts, "
                "source, detected_at) VALUES (1, '0xtx1', $1, $2, 'BUY', 100, 0.5, 50, to_timestamp($3), 'chain', "
                "to_timestamp($4))", K, D1_CID, T0, T0 + 1)
            return await ms.his_fills(conn, "rn1", D1_CID)
        finally:
            await _drop(admin, conn, name)
    rows = _run(_go())
    assert len(rows) == 1 and rows[0]["ts"] == float(T0)
    assert rows[0].get("detected_at") == float(T0 + 1), sorted(rows[0])


def test_review_q9_the_collapse_rule_never_reads_detected_at_and_the_pins_moved_by_the_arithmetic():
    src = inspect.getsource(ms.his_fills)
    assert src.count("detected_at") == 3, "the comment, the CTE line and the outer projection (the fold)"
    assert "d.ts,\n               d.detected_at," in src
    # E19b (2026-09-08): D1's one clause again -- lane 8's split / repeat
    # arms were withdrawn from sizing the same day (the fills-vs-venue first
    # run read them farther from the venue: old closer 22, new closer 4) and
    # live on the UNWIRED ms.his_fills_distinct alone; his_fills is
    # 7a4b852's text byte for byte
    assert "(COALESCE(f.source, '') NOT IN ('chain', 's1') AND f.has_net_leg) AS collapsed" in src
    assert "match_sum" not in src and "match_sum" in inspect.getsource(ms.his_fills_distinct)
    assert "WHERE NOT d.collapsed" in src and "ORDER BY d.ts, d.id" in src
    keys = ml.CENSUS_KEYS
    # E12 moved the tail by its three names (-65 -> -68, -66 -> -69), E13 by
    # its one (-68 -> -69, -69 -> -70), E16 by its four, E18 by its six, E17 by its
    # eight, E19 by its one and L7 by its one `event_stale` (-69 -> -89, -70 -> -90), E20 by its one `wrong_sign_hold`, E14b (FILL lane 1) by its one
    # `exit_take_rested` and E14 (FILL lane 2) by its one `take_in_band` (-89 -> -92, -90 -> -93), the convention every builder followed; E9's four stay keys[-8:-4]
    # FILL lane 3 by its three (-92 -> -95, -93 -> -96); T2 (FILL lane 4) by its two (-95 -> -97, -96 -> -98);
    # FILL lane 5 by its three (he_holds / he_holds_unread / reopen_refused: -97 -> -100, -98 -> -101);
    # E22 (FILL lane 22) by its four lost_fill_* names and FILL lane 11 by its one (-100 -> -105, -101 -> -106);
    # E23 (FILL lane 23) by its six cancel_fill_* / disagree_fill_* names (-105 -> -111, -106 -> -112) -- FILL lane 16 (one name) and E21 (FILL lane 10, six) landed first, so every index past this lane's six moved by seven more
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-118:-114 -> -122:-118, -119 -> -123)
    assert keys[-126:-122] == ("books_unreadable", "ratio_stepped", "under_min_notional", "shadow_check_skipped")
    assert keys[-127] == "short_share_cap" and keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")


# --------------------------------------------- Q10: persistence

def test_review_q10_nothing_new_is_persisted_and_a_deploy_leaves_the_market_to_the_full_tick():
    src = inspect.getsource(ml)
    keys = sorted(set(re.findall(r"^(_STATE_[A-Z0-9_]+)\s*=", src, re.M)))
    # (E13 added _STATE_TERMINAL_CONFIRM, the book memo's confirmation beside the E6 memo;
    # FILL lane 14 added _STATE_TICK_RING, the tick ring -- written by the FULL tick's
    # `finally` alone, never by the fast path, which the e9 pin below still holds)
    assert keys == ["_STATE_CAND_MEMO", "_STATE_DEMOTED", "_STATE_FLATTEN", "_STATE_LIVE", "_STATE_LOSS_REARM",
                    "_STATE_LOSS_STOP", "_STATE_OPEN", "_STATE_S4", "_STATE_SIDE_ECHO", "_STATE_TERMINAL_CONFIRM",
                    "_STATE_TERMINAL_MEMO", "_STATE_TICK_RING",
                    "_STATE_WHALES"], "no new ingestion_state key (E9 adds none; _STATE_OPEN is the venue's word)"
    e9 = "".join(inspect.getsource(f) for f in (ml.notify, ml._fast_wake, ml._fast_run, ml._fast_requeue,
                                                ml._fast_tick, ml.fast_tick_once, ml._fast_book,
                                                ml._fast_candidate, ml._fills_seen, ml._write_plan))
    assert "_write_state" not in e9 and "ingestion_state" not in e9
    # a deploy between the wake and the fast tick: the woken sets are process state; the first
    # full tick after boot reads every book (the empty quiet memo) with his fill in hand
    p = _pool()
    b = p.add_book(ledger=0)
    ml.notify(CID)
    ml._FAST_WOKEN.clear()
    ml._WOKEN.clear()                         # the process restarted: nothing woken
    v = _Venue()
    st = _tick(p, v)
    assert st["woken"] == [] and len(_places(v)) == 1 and b["open_order_id"] is not None
    assert _census(st, "rest_placed") == 1


# --------------------------------------------- the fold's own pins (2026-09-08)

def _pause_fetch(monkeypatch, pool, tag):
    """Pause the first pool.fetch whose SQL carries `tag` (the worker's
    /* ml-… */ markers) until the test resumes it; the events are made
    inside the loop, as _pause_read's are."""
    orig = pool.fetch
    ev: dict = {}

    def _events():
        if "paused" not in ev:
            ev["paused"], ev["resume"] = asyncio.Event(), asyncio.Event()
        return ev["paused"], ev["resume"]

    async def _hook(sql, *a):
        paused, resume = _events()
        if tag in sql and not paused.is_set():
            paused.set()
            await resume.wait()
        return await orig(sql, *a)
    monkeypatch.setattr(pool, "fetch", _hook)
    return _events


def test_review_fold_a_wake_during_the_full_ticks_step_o_waits_and_reads_the_market_order_open_once(monkeypatch):
    """(a) His fill lands while the full tick is inside step O (the
    open-orders read). The fast tick takes _FAST_LOCK and WAITS on
    _TICK_LOCK -- the hold is a full tick's, so the flag is down -- with
    the market still on _FAST_WOKEN (popped only under the lock). The
    full tick finishes and rests on the book; the fast tick then holds
    the lock, pops the market, reads the row `order_open` and places
    nothing: one placement for one fill of his, never two."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    http = _Http()
    events = _pause_fetch(monkeypatch, p, "ml-orders-open")

    async def _drive():
        paused, resume = events()
        p.clock = NOW
        full = asyncio.create_task(ml.tick_once(p, v, http, now_ts=NOW))
        assert await _spin(paused.is_set), "the full tick is inside step O"
        assert ml._TICK_LOCK.locked() and not ml._fast_holding and ml._full_tick is not None
        ml.notify(CID)                                  # his fill lands (unarmed: the fast tick is driven here)
        assert ml._FAST_WOKEN == {CID: 0}
        fast = asyncio.create_task(ml.fast_tick_once(p, v, http, now_ts=NOW + 1))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters)), "the fast tick waits on the full tick's hold"
        assert ml._FAST_LOCK.locked() and ml._FAST_WOKEN == {CID: 0}, "not popped while it waits"
        assert not fast.done() and _bbos(v) == []
        resume.set()
        st = await full
        assert ml._full_tick is None and not ml._TICK_LOCK.locked() or ml._fast_holding
        return st, await fast
    st, fs = _run(_drive())
    assert st["status"] != "overlap" and len(_places(v)) == 1 and b["open_order_id"] is not None
    assert _census(st, "rest_placed") == 1 and st["woken"] == [], "the full tick answered it (the wake came after its start)"
    assert fs["woken"] == [CID] and fs["fast"]["tries"] == {CID: 0} and _skips(fs) == {CID: "order_open"}
    assert fs["fast"]["placed"] == 0 and _bbos(v) == [SLUG] and ml._FAST_WOKEN == {}, "read once, by the full tick"
    assert CID in ml._WOKEN, "the next full tick still reads it woken"
    assert ml._fast_holding is False and not ml._TICK_LOCK.locked() and not ml._FAST_LOCK.locked()


def test_review_fold_a_full_tick_waits_on_a_fast_ticks_hold_and_still_returns_overlap_on_a_full_ticks(monkeypatch):
    """(b) A full tick arriving while a FAST tick holds the tick lock
    waits -- it does not return `overlap` -- and runs once the fast tick
    is done; one arriving while another FULL tick holds it still returns
    `overlap` at once (test_l2_review_pins and test_e9_fast_path pin
    that return; the flag is what tells the two holds apart)."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    http = _Http()
    _walk()
    events = _pause_read(monkeypatch, lambda t, cid: t.fast)

    async def _drive():
        paused, resume = events()
        p.clock = NOW + 1
        fast = asyncio.create_task(ml.fast_tick_once(p, v, http, cids=[CID], now_ts=NOW + 1))
        assert await _spin(paused.is_set)
        assert ml._TICK_LOCK.locked() and ml._fast_holding is True
        full = asyncio.create_task(ml.tick_once(p, v, http, now_ts=NOW + 2))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters)), "waiting on the fast tick's hold"
        for _ in range(50):
            await asyncio.sleep(0)
        assert not full.done(), "not returned: no `overlap` on a fast tick's hold"
        resume.set()
        fs = await fast
        assert ml._fast_holding is False
        st = await full
        # a FULL tick's hold: `overlap`, returned at once, nothing read
        async with ml._TICK_LOCK:
            assert not ml._fast_holding
            st2 = await ml.tick_once(p, v, http, now_ts=NOW + 3)
        return fs, st, st2
    fs, st, st2 = _run(_drive())
    assert fs["fast"]["placed"] == 1 and _bbos(v) == [SLUG, SLUG], "the fast tick's read, then the full tick's own"
    assert st["status"] != "overlap" and not st.get("skipped_overlap") and st["tick_s"] >= 0.0
    assert st["orders_open"] == 1 and len(_places(v)) == 1 and b["open_order_id"] is not None
    assert st2["status"] == "overlap" and st2["skipped_overlap"] is True and st2["tick_s"] == 0.0
    assert len(_places(v)) == 1 and _bbos(v) == [SLUG, SLUG], "the overlap tick read nothing"


def test_review_fold_the_fast_tick_that_waited_finds_its_markets_drained_by_the_full_tick_and_places_nothing(monkeypatch):
    """(c) A full tick's hold (the poll's tick, started while the fast
    run waited out its floor or its turn): the fast tick takes
    _FAST_LOCK and waits on _TICK_LOCK with the market still on
    _FAST_WOKEN; the full tick's start drains the set (they are its to
    read, woken first); the fast tick then holds the lock, pops nothing,
    reads nothing, counts no tick. First the hold by hand (the drain
    made where tick_once makes it), then the real full tick -- in ONE
    loop: a contended asyncio.Lock is bound to the loop it waited on."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    http = _Http()
    _walk()
    events = _pause_fetch(monkeypatch, p, "ml-orders-open")

    async def _drive():
        ml._FAST_WOKEN[CID] = 0
        async with ml._TICK_LOCK:                       # a full tick's hold, by hand
            fast = asyncio.create_task(ml.fast_tick_once(p, v, http, now_ts=NOW + 1))
            assert await _spin(lambda: bool(ml._TICK_LOCK._waiters))
            assert ml._FAST_WOKEN == {CID: 0} and ml._FAST_LOCK.locked() and not ml._fast_holding
            ml._FAST_WOKEN.clear()                      # what tick_once does at its start
        fs = await fast
        assert fs["woken"] == [] and fs["fast"]["tries"] == {} and _skips(fs) == {} and fs["fast"]["placed"] == 0
        assert _bbos(v) == [] and not _places(v) and b["last_plan"] is None
        assert _census(fs, "fast_tick") == 0 and ml._fast_acc["n"] == 0 and ml._fast_acc["markets"] == 0
        # the real full tick: woken before it starts, it drains the set and reads
        # the market itself; the fast tick that waited on it finds nothing
        ml.notify(CID)
        assert ml._FAST_WOKEN == {CID: 0} and CID in ml._WOKEN
        paused, resume = events()
        p.clock = NOW + 2
        full = asyncio.create_task(ml.tick_once(p, v, http, now_ts=NOW + 2))
        assert await _spin(paused.is_set), "the full tick is inside step O"
        assert ml._FAST_WOKEN == {}, "drained at the full tick's start"
        fast2 = asyncio.create_task(ml.fast_tick_once(p, v, http, now_ts=NOW + 3))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters))
        resume.set()
        return await full, await fast2
    st, fs2 = _run(_drive())
    assert st["woken"] == [CID] and len(_places(v)) == 1 and b["open_order_id"] is not None
    assert fs2["woken"] == [] and _skips(fs2) == {} and fs2["fast"]["placed"] == 0 and _bbos(v) == [SLUG]
    assert ml._fast_acc["n"] == 0, "no fast tick counted for an empty take"
    # the source: the pop sits under the lock, after the flag is raised
    src = inspect.getsource(ml.fast_tick_once)
    assert src.index("async with _FAST_LOCK, _TICK_LOCK:") < src.index("_fast_holding = True") \
        < src.index("_FAST_WOKEN.pop(")


def test_review_fold_the_wake_to_placement_clock_with_no_full_tick_in_flight_is_unchanged(monkeypatch):
    """(d) The builder's pin re-run: with no full tick in flight the
    wake's fast tick reads and places inside FAST_TICK_MIN_S, sleeps
    nothing and touches no other book -- the lock is uncontended, taken
    and released inside the one run; nothing of the floor moved."""
    from tests.test_e9_fast_path import (
        test_e9_the_fast_tick_reads_and_places_the_woken_market_inside_the_floor_and_touches_no_other_book as pin,
    )
    pin(monkeypatch)
    assert ml._fast_holding is False and not ml._TICK_LOCK.locked() and not ml._FAST_LOCK.locked()
    assert ml.FAST_TICK_MIN_S == 2.0 and ml.FAST_TICK_MAX == 5
    src = inspect.getsource(ml._fast_run)
    assert "_TICK_LOCK" not in src and "await _fast_sleep(gap)" in src, "the floor is slept outside the lock"
