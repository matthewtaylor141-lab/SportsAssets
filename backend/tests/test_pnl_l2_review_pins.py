"""PNL lane 2 (E16) -- the adversarial review's pins (2026-09-08).

Applied on top of hard2/PNL_L2.patch. A pin named `_r<n>_` after a
finding pinned the DEFECT AS IT STANDS when the review wrote it; the
four findings were FOLDED on 2026-09-08 and each of those pins now
asserts the fixed behaviour its docstring named (the body kept, the
`as it stands` lines inverted); the others are rails the lane's own
tests left unpinned (each kills a mutant the review ran).

  R1 (MEDIUM)  a suspect record written by a CACHED read (the fast tick,
               t.walk_at None) counts as the FIRST of the two reads:
               one fresh disagreeing walk after it freezes the book.
  R2 (MEDIUM)  the transition tick (and any tick that refuses before
               _frozen_reduce_on_fill runs) moves `fills_at` past a sale
               of his it never answered: with the walk unread that sale
               is never witnessed.
  R4 (LOW)     _venue_market_ended reads the suspect nowhere: a LIVE
               flat book under a suspect ends on the venue's terminal
               word the tick after (pre-E16 it froze first, and the
               freeze reason held E13 off).
  R5 (LOW)     an on-fill rest keeps standing when he re-buys past our
               proportion while the walk stays unread (cancel=False on
               every on-fill verdict; E5 F4's cancel is the read path's).
  R3, R6, R7   rails: a collapse never sells on the frozen book; the
               switch's words; the thaw statement on the real schema.
"""
import asyncio
import inspect
import json

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e5_frozen_exits import _frozen_long, _placed, _plan_exit
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_e13_venue_close import EXPIRED, KEY, _drop, _scratch
from tests.test_e16_freeze_two_reads import _pool, _suspect, _thaw_off, _unread, _witness
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    GTC_TIF, IOC_TIF, M, NOW, SELL, SLUG, _Venue, _armed, _cancels, _census, _fill, _his, _places, _tick,
)


# ------------------------------------------------------------ R1 (finding)

def test_r1_a_cached_first_read_and_one_fresh_read_never_freeze_folded():
    """FINDING R1, pinned AS IT STOOD, folded 2026-09-08. No full tick has
    read the book; the fast tick plans it on the cached walk (300
    against a ledger of 0) and writes a suspect with `walk_at: None,
    cached: 1`. The NEXT full tick's fresh walk disagreeing the same way
    FROZE the book: one fresh read, not two. _second_disagreeing_read
    asked only whether THIS tick walked, never whether the prior record
    did. The fix (folded): a prior whose `walk_at` is None is not a first
    read (return False), so the fresh read after it writes a fresh
    record and the one after that freezes. Now: the book stays live with
    a fresh suspect at NOW + 15 (`walk_at` NOW + 15, cached 0), the
    census counts a second suspect and no freeze; the fresh walk after
    THAT freezes."""
    p = _pool()
    b = p.add_book(ledger=0)
    _walk({SLUG: 300.0})
    fs = _fast(p, _Venue())
    assert _skips(fs) == {} and b["state"] == "live" and b["frozen_reason"] is None
    assert _suspect(b) == {"venue": 300, "explained": 0.0, "delta": 300.0, "at": NOW + 1, "walk_at": None, "cached": 1}
    st = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 15)
    # folded: the first FRESH walk is the first read -- live, a fresh suspect
    assert b["state"] == "live" and b["frozen_reason"] is None
    # (the fast tick's suspect and this walk's land on the same census: two, no freeze)
    assert _census(st, "venue_ledger_disagree") == 0 and _census(st, "venue_ledger_suspect") == 2
    assert _suspect(b) == {"venue": 300, "explained": 0.0, "delta": 300.0, "at": NOW + 15, "walk_at": NOW + 15, "cached": 0}
    assert b["last_plan"]["reason"] == "venue_suspect_hold" and "frozen_exit" not in b["last_plan"]
    # the rule at the unit: a cached prior is never the first read
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW + 15, stats=ml._new_stats())
    t.walk_at = NOW + 15
    assert ml._second_disagreeing_read({"delta": 300.0, "walk_at": None, "cached": 1}, 300.0, t) is False
    assert ml._second_disagreeing_read({"delta": 300.0, "walk_at": NOW + 15, "cached": 0}, 300.0, t) is True
    # the second FRESH read freezes, as the lane built it
    st2 = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 30)
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert _census(st2, "venue_ledger_disagree") == 1 and _plan_exit(b) == {"held": "transition_tick"}
    assert _suspect(b)["walk_at"] == NOW + 15, "the first read the freeze stood on was the fresh one"


# ------------------------------------------------------------ R2 (finding)

def test_r2_his_sale_landing_on_the_freeze_tick_is_witnessed_next_tick_folded():
    """FINDING R2, pinned AS IT STOOD, folded 2026-09-08. Ledger 300, he
    holds 300, the walk reads 600 twice (fresh): tick 1 the suspect,
    tick 2 the freeze with the transition-tick hold (F1: no exit). His
    SELL of 90 was ingested BETWEEN the two walks (clock NOW + 10): the
    freeze tick held it in `fills` and wrote `fills_at` = NOW + 10 --
    the sale's own clock -- so tick 3, the walk unread, read no witness
    after the clock and refused `frozen_venue_unread`: his 30% sale was
    never followed while the walk stayed unread (the read path would
    size it from venue_own when the walk reads; 266's walk did not for
    30 min). The same consumption happened on a `frozen_fill_this_tick`
    tick and under `frozen_exits_off`. The fix (folded): _frozen_clock
    keeps the prior plan's `fills_at` (or, on the transition tick, the
    newest clock among the last LIVE plan's `his_fills_seen`) whenever
    _frozen_reduce_on_fill did not run. Now: the freeze tick's clock is
    NOW - 3000 (his BUY, the one fill the live plan answered) and tick 3
    sells 90 at his cent (the take at 0.30, the bid there), marked
    frozen_reduce_on_fill, the ledger 210, the clock then the sale's."""
    p = _pool(snap=None)
    b = p.add_book(ledger=300, avg_cost=0.31)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    _tick(p, v, http=_unread())
    assert b["state"] == "live" and _suspect(b)["delta"] == 300.0 and not _places(v)
    p.fills = _his(300) + [_fill(M, "SELL", 90, 0.31, NOW + 10)]
    v2 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=90)
    _tick(p, v2, now=NOW + 15, http=_unread())
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree" and not _places(v2)
    assert _plan_exit(b) == {"held": "transition_tick"}
    assert b["last_plan"]["fills_at"] == NOW - 3000, "the last LIVE plan's fills' clock: the sale stays witnessable"
    # E31 (FILL lane 31, 2026-09-10): the frozen exit is a post-only rest, not an
    # IOC, so `lift` (the taker who hits the fresh rest at create, aggressor False)
    # stands where `ioc_fill` filled it
    v3 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=90, lift=90)
    st3 = _tick(p, v3, now=NOW + 30, http=_unread())
    # folded: the sale witnessed, 90 sold at his cent off the LEDGER, marked.
    # RE-PINNED at E31: 0.30 IOC -> 0.31 GTC post-only. His cent is 0.31 (his SELL
    # at 0.31, sell_wire's ceiling) and the maker SELL wire is max(0.31, bid 0.30 +
    # 0.01) = 0.31, so the exit rests AT HIS CENT one tick over the bid instead of
    # crossing down to it. The row's kind and reason follow: 'take' / 'IOC' /
    # 'frozen_reduce_on_fill: take' -> 'reduce' / 'GTC' / 'frozen_reduce_on_fill: reduce'
    assert [c[1:] for c in _places(v3)] == [(SLUG, 0.31, 90, True, GTC_TIF, "ORDER_INTENT_BUY_LONG", True, None)]
    o = _placed(p)[0]
    assert (o["kind"], o["side"], o["tif"], o["reason"], o["qty"], o["state"]) == (
        "reduce", SELL, "GTC", "frozen_reduce_on_fill: reduce", 90, "filled")
    assert _census(st3, "frozen_reduce_on_fill") == 1 and _census(st3, "frozen_venue_unread") == 0
    assert b["ledger_net"] == 210 and b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    fx = _plan_exit(b)
    assert (fx["why"], fx["since"], fx["witnessed"], fx["target"], fx["ledger"], fx["qty"], fx["reduce_on_fill"]) == (
        "his_market_read", NOW - 3000, 90.0, 210, 300, 90, True)
    assert _witness(b)["placed"] is True and b["last_plan"]["fills_at"] == NOW + 10, "placed: the clock moves to the sale"
    # the unit: the three holds keep the prior clock; the transition tick reads the live plan's answered fills
    live = {"his_fills_seen": [{"id": "a", "ts": NOW - 3000, "det": NOW - 2990}, {"id": "b", "ts": NOW - 5000, "det": None}]}
    fills = _his(300) + [_fill(M, "SELL", 90, 0.31, NOW + 10)]
    assert ml._frozen_clock({"frozen_exit": {"held": "transition_tick"}}, live, fills, NOW + 15) == NOW - 2990
    assert ml._frozen_clock({"frozen_exit": {"held": "transition_tick"}}, {}, fills, NOW + 15) == NOW + 10
    for held in ("frozen_fill_this_tick", "frozen_exits_off", "transition_tick"):
        assert ml._frozen_clock({"frozen_exit": {"held": held}}, {"fills_at": NOW - 100}, fills, NOW + 15) == NOW - 100
    assert ml._frozen_clock({"frozen_exit": {"held": "frozen_fill_this_tick"}}, live, fills, NOW + 15) == NOW + 10
    assert ml._frozen_clock({"frozen_exit": {"held": "frozen_venue_unread"}}, {"fills_at": NOW - 100}, fills, NOW + 15) == NOW + 10


# ---------------------------------------------------------------- R3 (rail)

def test_r3_a_fall_of_the_fills_net_with_no_reducing_fill_never_sells_on_the_frozen_book(monkeypatch):
    """The chain-first collapse (E12b's world): his BUY re-read smaller
    (300 -> 210, the same clock) is a fall of the fills' net with NO
    reducing fill; a smaller BUY row with a NEW ingest clock is an add,
    not a sale. The frozen book with the walk unread sells nothing on
    either -- `frozen_venue_unread`, no `frozen_witness`, the ledger
    300. (The mutant `witnessed <= 0.0` -> `< 0.0` sizes on witnessed 0:
    the target 210 < 300 would sell 90 he never sold; the lane's tests
    catch it by the plan's record, this one by the money -- nothing
    placed, the ledger unmoved.)"""
    _thaw_off(monkeypatch)
    p = _pool(snap=None)
    b = _frozen_long(p, reason="venue_ledger_disagree")
    _tick(p, _Venue(held={SLUG: 300}, bid=0.30, ask=0.32), http=_unread())
    assert b["last_plan"]["fills_at"] == NOW - 3000
    p.fills = _his(210)
    v2 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st2 = _tick(p, v2, now=NOW + 15, http=_unread())
    assert not _places(v2) and _census(st2, "frozen_reduce_on_fill") == 0 and _census(st2, "frozen_venue_unread") == 1
    assert "frozen_witness" not in b["last_plan"] and b["ledger_net"] == 300
    p.fills = [_fill(M, "BUY", 210, 0.31, NOW - 3000, detected_at=NOW + 20)]
    v3 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st3 = _tick(p, v3, now=NOW + 30, http=_unread())
    assert not _places(v3) and _census(st3, "frozen_reduce_on_fill") == 0 and _census(st3, "frozen_venue_unread") == 1
    assert "frozen_witness" not in b["last_plan"] and b["ledger_net"] == 300 and b["state"] == "frozen"


# ------------------------------------------------------------ R4 (finding)

def test_r4_the_venue_terminal_close_reads_the_suspect_folded():
    """FINDING R4, pinned AS IT STOOD, folded 2026-09-08. A LIVE book,
    ledger 0, whose last plan carries a suspect saying the venue holds
    300 (the vanish flatten sold OUR 300; the walk reported 600): the
    suspect tick held the close (`venue_ledger_suspect`), but step M
    runs BEFORE the freeze section next tick and _venue_market_ended
    asked only the ledger and, on a FROZEN book, the reason and the
    plan's `venue`. Live under a suspect it answered the terminal state,
    and the E13 close ended the book with the 300 nobody's. Pre-E16 the
    one-read freeze put the book under _VENUE_MAY_HOLD_REASONS first
    (None). The fix (folded): a live book whose last plan carries
    `venue_ledger_suspect` answers None (the same reading the frozen
    clause makes); the same book with no suspect on its plan still
    ends; the row's jsonb served as text decodes as every reader's."""
    book = {"id": 9, "ledger_net": 0, "state": "live", "whale": KEY[0], "condition_id": KEY[1],
            "last_plan": {"venue": 300, "venue_ledger_suspect": {"venue": 300, "explained": 0.0,
                                                                 "delta": 300.0, "walk_at": NOW}}}
    ml._terminal_book_confirmed[KEY] = EXPIRED
    try:
        assert ml._venue_market_ended(book) is None             # folded: the suspect holds the close
        assert ml._venue_market_ended({**book, "last_plan": json.dumps(book["last_plan"])}) is None
        assert ml._venue_market_ended({**book, "last_plan": {"venue": 0}}) == EXPIRED   # no suspect: ends
        assert ml._venue_market_ended({**book, "last_plan": None}) == EXPIRED
        frozen = {**book, "state": "frozen", "frozen_reason": "venue_ledger_disagree"}
        assert ml._venue_market_ended(frozen) is None           # the frozen clause, unchanged
        src = inspect.getsource(ml._venue_market_ended)
        assert "venue_ledger_suspect" in src                   # the reading it now makes
    finally:
        ml._terminal_book_confirmed.pop(KEY, None)


# ------------------------------------------------------------ R5 (finding)

def test_r5_an_on_fill_rest_is_cancelled_when_he_re_buys_past_our_proportion_folded(monkeypatch):
    """FINDING R5, pinned AS IT STOOD, folded 2026-09-08. Tick 2 rests
    the on-fill reduce (90 at 0.31, the bid away). Tick 3, the walk
    still unread: he sold 10 more and re-bought 200 (net 400 > our 300:
    `under_proportion`, he came back). The verdict was refused with
    cancel=False, so the rest at his OLD exit cent kept standing and
    could fill while his net was above ours -- E5 F4's rule ("a frozen
    one may not keep selling at his old exit cent while he buys") is
    the READ path's `frozen_no_his_exit` cancel and did not reach here.
    Bounded by the rest's TTL and the next read walk. The fix (folded):
    `under_proportion` cancels a standing on-fill rest (cancel=True for
    that verdict alone; the unread-target and unknown-plan verdicts
    keep V3-3's hold). Now: the rest is cancelled under the refusal's
    name, nothing placed, the ledger 300; and a tick with NO witness
    after it still keeps a standing rest (the hold, unchanged)."""
    _thaw_off(monkeypatch)
    p = _pool(snap=None)
    b = _frozen_long(p, reason="venue_ledger_disagree")
    _tick(p, _Venue(held={SLUG: 300}, bid=0.28, ask=0.32), http=_unread())
    p.fills = _his(300, sold=90)
    v2 = _Venue(held={SLUG: 300}, bid=0.28, ask=0.32)
    _tick(p, v2, now=NOW + 15, http=_unread())
    o = _placed(p)[0]
    assert (o["kind"], o["reason"], o["state"], o["qty"], o["tif"]) == ("reduce", "frozen_reduce_on_fill", "open", 90, "GTC")
    p.fills = _his(300, sold=90) + [_fill(M, "SELL", 10, 0.31, NOW + 20), _fill(M, "BUY", 200, 0.33, NOW + 25)]
    v3 = _Venue(held={SLUG: 300}, bid=0.28, ask=0.32)
    v3.orders = v2.orders
    st3 = _tick(p, v3, now=NOW + 30, http=_unread())
    assert _witness(b)["held"] == "under_proportion" and _witness(b)["target"] == 400 and _witness(b)["witnessed"] == 10.0
    # folded: the rest is cancelled under the refusal's name, nothing placed, the ledger unmoved
    assert [c[1] for c in _cancels(v3)] == [o["order_id"]] and not _places(v3) and o["state"] == "cancelled"
    assert _plan_exit(b) == {"held": "frozen_venue_unread", "why": "his_market_read", "cancelled": o["id"]}
    assert _census(st3, "frozen_venue_unread") == 1 and b["ledger_net"] == 300 and b["state"] == "frozen"
    # the hold kept: a rest standing through a tick that witnessed nothing (V3-3)
    p2 = _pool(snap=None)
    b2 = _frozen_long(p2, reason="venue_ledger_disagree")
    _tick(p2, _Venue(held={SLUG: 300}, bid=0.28, ask=0.32), http=_unread())
    p2.fills = _his(300, sold=90)
    w2 = _Venue(held={SLUG: 300}, bid=0.28, ask=0.32)
    _tick(p2, w2, now=NOW + 15, http=_unread())
    o2 = _placed(p2)[0]
    w3 = _Venue(held={SLUG: 300}, bid=0.28, ask=0.32)
    w3.orders = w2.orders
    _tick(p2, w3, now=NOW + 30, http=_unread())
    assert not _cancels(w3) and not _places(w3) and o2["state"] == "open" and "frozen_witness" not in b2["last_plan"]
    assert _plan_exit(b2) == {"held": "frozen_venue_unread", "why": "his_market_read"}


# ---------------------------------------------------------------- R6 (rail)

def test_r6_the_switch_reads_the_four_words_alone_and_the_module_constant_is_what_the_tick_reads(monkeypatch):
    """Beside the lane's truth table: the words a hand might type that
    are NOT on -- 'y', 't', 'Y', '1.0', 'on=1', 'yes,', 'true;' -- and the
    padded forms of the four that are; and the constant, not the
    environment, is what _thaw_verdict reads at call time (an env change
    after import moves nothing until a restart: pinned so a hot re-read
    is never added by accident)."""
    for raw in ("y", "t", "Y", "T", "1.0", "on=1", "yes,", "true;", "01", "-1", "oui", "si"):
        monkeypatch.setenv("PMUS_MIRROR_AUTO_THAW", raw)
        assert ml._auto_thaw_switch() is False, raw
    for raw in ("on\n", "\ttrue", " 1 ", "YES", "On", "tRuE"):
        monkeypatch.setenv("PMUS_MIRROR_AUTO_THAW", raw)
        assert ml._auto_thaw_switch() is True, raw
    monkeypatch.setattr(ml, "MIRROR_FROZEN_THAW", True)
    monkeypatch.setenv("PMUS_MIRROR_AUTO_THAW", "off")      # the environment moved after import
    p = _pool()
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    t.walk_at = NOW
    one = {"venue_agrees": {"reads": 1, "at": NOW - 15, "walk_at": NOW - 15}}
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, one, {}) == "venue_agrees"
    monkeypatch.setattr(ml, "MIRROR_FROZEN_THAW", False)
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, one, {}) == "thaw_off"
    monkeypatch.setattr(ml, "MIRROR_FROZEN_THAW", True)
    monkeypatch.setattr(rules, "MIRROR_FROZEN_EXITS", False)
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, one, {}) == "thaw_off"
    # a held book under any other reason: None (E5's one-read thaw), whatever the count
    monkeypatch.setattr(rules, "MIRROR_FROZEN_EXITS", True)
    for reason in ("placement_lost", "order_lost", "lost_ambiguous", "wrong_sign_trip", "cancel_pending", None):
        assert ml._thaw_verdict(t, {"frozen_reason": reason}, 300, one, {}) is None, reason


# --------------------------------------------------- R7 (the real schema)

def test_r7_the_thaw_statement_on_the_real_schema_names_the_one_reason_and_zeroes_the_ticks():
    """Migration 047's mirror_books on a scratch Postgres: the D2
    statement thaws a frozen venue_ledger_disagree row (state live,
    frozen_reason NULL, frozen_at NULL, frozen_ticks 0) and touches
    nothing else -- a placement_lost row, a live row -- while E5's own
    statement still thaws the placement_lost row on its one read."""
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await conn.execute(
                "INSERT INTO mirror_books (whale, condition_id, us_market_slug, long_asset, state, frozen_reason, "
                "frozen_at, frozen_ticks) VALUES ('rn1', 'c1', 's-1', 'tok', 'frozen', 'venue_ledger_disagree', now(), 7), "
                "('rn1', 'c2', 's-2', 'tok', 'frozen', 'placement_lost', now(), 3), "
                "('rn1', 'c3', 's-3', 'tok', 'live', NULL, NULL, 0)")
            ids = {r["condition_id"]: r["id"] for r in await conn.fetch("SELECT id, condition_id FROM mirror_books")}
            assert await conn.execute(ml._SQL_BOOK_THAW_AGREES, ids["c2"]) == "UPDATE 0"
            assert await conn.execute(ml._SQL_BOOK_THAW_AGREES, ids["c3"]) == "UPDATE 0"
            assert await conn.execute(ml._SQL_BOOK_THAW_AGREES, ids["c1"]) == "UPDATE 1"
            assert await conn.execute(ml._SQL_BOOK_THAW_AGREES, ids["c1"]) == "UPDATE 0"
            rows = {r["condition_id"]: dict(r) for r in await conn.fetch(
                "SELECT condition_id, state, frozen_reason, frozen_at, frozen_ticks FROM mirror_books")}
            assert (rows["c1"]["state"], rows["c1"]["frozen_reason"], rows["c1"]["frozen_at"], rows["c1"]["frozen_ticks"]) == (
                "live", None, None, 0)
            assert (rows["c2"]["state"], rows["c2"]["frozen_reason"], rows["c2"]["frozen_ticks"]) == ("frozen", "placement_lost", 3)
            assert rows["c2"]["frozen_at"] is not None and rows["c3"]["state"] == "live"
            assert await conn.execute(ml._SQL_BOOK_THAW, ids["c2"]) == "UPDATE 1"
            r2 = await conn.fetchrow("SELECT state, frozen_reason, frozen_ticks FROM mirror_books WHERE id = $1", ids["c2"])
            assert (r2["state"], r2["frozen_reason"], r2["frozen_ticks"]) == ("live", None, 3)
        finally:
            await _drop(admin, conn, name)
    asyncio.run(_go())


def test_r7b_the_rails_the_review_read_are_in_the_source():
    """The fast tick plans through the one function (no copy of the
    freeze rule); the frozen book is refused by the fast gate before it
    (`not_live`); the E13 close runs before the freeze section on its
    own plan; every on-fill verdict is a refusal or a reduce."""
    src = inspect.getsource(ml._tick_book)
    assert src.index("venue_ended = None if closed_read else _venue_market_ended(book)") < src.index("delta = venue_int - explained")
    assert "_second_disagreeing_read" not in inspect.getsource(ml._fast_book) + inspect.getsource(ml._fast_tick)
    assert 'return "not_live"' in inspect.getsource(ml._fast_gate)
    fr = inspect.getsource(ml._frozen_reduce_on_fill)
    assert 'await _act(t, book, r, p, "reduce", his_px, plan)' in fr and fr.count("cancel=False") == 2
    assert "book[\"_frozen_venue\"] = ledger" in fr and "_frozen_venue_own" not in fr
    assert "min(int(qty), max(0, int(fv)))" in inspect.getsource(ml._sell_qty)
    with pytest.raises(AssertionError):
        assert "frozen_reduce_on_fill" in inspect.getsource(ml._frozen_reduce_stands)   # the prefix match is the reader
