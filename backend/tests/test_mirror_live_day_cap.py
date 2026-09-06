"""MIRROR LIVE: the adversarial pre-flight of 2026-09-05, run before the
owner's "switch on the mirror system 100%" order. Three findings in
workers/mirror_live, each pinned here or in test_mirror_live_worker's
section 15, with that file's fakes (the in-memory pool that dispatches
every worker statement by its `ml-<name>` tag, the stateful venue) and
its armed fixture:

  1. THE DAY CAP COUNTED ONLY WHAT FILLED. _SQL_MIRROR_DAY summed
     mirror_orders.cash_usd, which is written on booked fills alone,
     and _place took a placement off t.mirror_day for its own tick
     only, so an unfilled rest counted on no later tick: with the room
     nearly spent, later ticks could each rest the same last dollars
     again, up to MIRROR_DAY_USD + 4 x LIVE_MAX_CLIP_USD standing in a
     rolling day. The read now returns two columns -- what FILLED and
     what RESTS (the unfilled remainder at the wire of every BUY_LONG
     row that may still fill) -- and _global_guards reads them apart:
     the BLOCK ('mirror_day_cap', which cancels every resting BUY) is
     on what filled alone, the SIZING room is the cap less both, so a
     day full by rests refuses a new rest 'over_room' and lets the
     rests stand instead of cancelling and re-resting them every
     other tick (the churn the review of the first cut found). A
     same-tick replace, TTL or take cancel gives the cancelled rest's
     remainder back to the room so the re-quote is at full size.
     Pinned: the two-tick scenario (fails against the original
     statement, which the fake pool reads from the statement's own
     text); the rests stand across ticks on a day full by rests; a
     filled day still blocks and cancels; the give-back, and its edges
     from the re-review's mutants (the remainder, not the whole rest;
     BUY rests only; 'expired' as well as 'cancelled'; the read's own
     24 h window; a tick that never read the day); the per-tick
     decrement; no double count of a partial fill; a terminal row's
     remainder does not count; the statement's text by its tag.
  2. THE TAKE'S CANCEL SPENDS THE REPLACE BUDGET (test_mirror_live_worker
     section 15, beside the census coverage it feeds; the statement's
     text is pinned here).
  3. THE OPERATOR CAN READ THE MODE ON A QUIET TICK: main() logs one
     INFO line every MODE_LINE_EVERY_TICKS ticks whatever the tick did.
     Pinned by driving main() with a fake tick_once: the exact line on
     ticks 10 and 20, not on tick 9; the cadence helper; the env dial
     and its floor.
"""
import inspect
import logging
import types

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_mirror_live_worker import _armed  # noqa: F401 -- the autouse fixture, armed here too
from tests.test_mirror_live_worker import (
    BUY, CID, M, N, NOW, SELL, SLUG, _cancels, _census, _fill, _his, _Http, _places, _pool,
    _run, _tick, _Venue,
)

OTHER_CID, OTHER_SLUG, M2, N2 = "0xother", "aec-atp-other-2026-09-02", "tok-m2", "tok-n2"


def _flat(s: str) -> str:
    return " ".join(s.split())


# ------------------------------------------------------------ 1. the day cap

class _ByMarket(_Http):
    """The data API's `/positions` answering FOR THE MARKET ASKED: the
    worker's per-market read refuses a row from another condition, so a
    second book on a second market needs its own rows."""

    def __init__(self, by_market: dict):
        super().__init__(rows=[])
        self.by_market = by_market

    async def get(self, path, params=None):
        self.rows = list(self.by_market.get((params or {}).get("market"), []))
        return await super().get(path, params)


def _two_markets():
    """rn1 long 300 on the fixture market AND on a second one, both
    readable: his fills, the whole-book walk, the per-market rows, the
    markets row and the token index for each."""
    fills = _his() + [_fill(M2, "BUY", 300.0, 0.31, NOW - 2500)]
    p = _pool(fills=fills, snap={M: 300.0, N: 0.0, M2: 300.0, N2: 0.0})
    p.markets[OTHER_CID] = {"closed": False, "resolved": False, "resolved_prices": None}
    p.token_index.update({M2: 1, N2: 0})
    p.token_cid.update({M2: OTHER_CID, N2: OTHER_CID})
    http = _ByMarket({
        CID: [{"conditionId": CID, "asset": M, "size": 300},
              {"conditionId": CID, "asset": N, "size": 0}],
        OTHER_CID: [{"conditionId": OTHER_CID, "asset": M2, "size": 300},
                    {"conditionId": OTHER_CID, "asset": N2, "size": 0}]})
    return p, http


def _other_book(p):
    """A second live book, on the second market."""
    return p.add_book(ledger=0, us_market_slug=OTHER_SLUG, condition_id=OTHER_CID,
                      long_asset=M2, other_asset=N2)


@pytest.mark.parametrize("little, refusal", [(0.1, "over_room"), (0.0, "over_room")])
def test_a_standing_unfilled_rest_counts_against_the_day_on_the_next_tick(
        monkeypatch, little, refusal):
    """TWO TICKS. Tick 1: one book rests a BUY of $90 (300 @ 0.30) with
    the mirror's day room at $90 + `little`. Tick 2: that rest still
    stands unfilled, and a second book on a second market wants the
    same $90. It must be refused on the day cap -- `over_room`, _act's
    room scaling against t.mirror_day, whether a little room is left
    (not a share's worth) or none at all: the room counts the rest,
    the BLOCK does not (nothing filled), so the standing rest is never
    cancelled -- and nothing new may rest. Against the original
    _SQL_MIRROR_DAY the second book rests another 300 shares on tick 2
    and this test fails: the fake pool reads the day columns from the
    statement's text."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 90.0 + little)
    p, http = _two_markets()
    b1 = p.add_book(ledger=0)
    v = _Venue(bid=0.30, ask=0.32)
    st1 = _tick(p, v, http=http)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 300 and pl[0][2] == 0.30 and pl[0][4] is False
    assert _census(st1, "rest_placed") == 1 and b1["open_order_id"] is not None
    rest = p.orders[b1["open_order_id"]]
    assert rest["state"] == "open" and rest["booked_filled"] == 0.0 and rest["cash_usd"] == 0.0
    # tick 2: the rest stands unfilled; a second book wants the same $90
    b2 = _other_book(p)
    st2 = _tick(p, v, now=NOW + 30, http=http)
    assert len(_places(v)) == 1, ("the standing rest's $90 is the day's; nothing new rested",
                                  _places(v))
    assert _census(st2, refusal) >= 1, st2["census"]
    assert b2["open_order_id"] is None
    assert not [o for o in p.orders.values() if o["book_id"] == b2["id"]]
    assert p.orders[rest["id"]]["state"] == "open" and not _cancels(v)
    assert _census(st2, "open_order_pending") >= 1
    assert _census(st2, "mirror_day_cap") == 0 and st2["mirror_day_room"] == pytest.approx(little)


def test_a_day_full_by_rests_does_not_block_and_the_rests_stand_across_ticks(monkeypatch):
    """THE CHURN, driven. The first cut read one sum -- filled plus
    resting -- and the block `t.mirror_day <= 0` tripped on rests that
    brought it to exactly the cap; _reconcile_open answers the block
    by cancelling every resting BUY, the very rests being counted, so
    each book alternated cancel / re-rest every other tick for as long
    as the day stayed full, unbounded per hour (the cancel is no
    replace and spends no budget), restarting the take wait and the
    TTL, absent from the venue half the time. Now: a day EXACTLY full
    by rests (rests sum to the cap, nothing filled) sets no block, the
    rest stands through ticks 2, 3 and 4 -- no cancel, cancelled_unfilled
    0, no 'mirror_day_cap' in any census -- while the second book on
    the second market is refused 'over_room' on every one of them, and
    the published room reads 0.00 and survives the health endpoint's
    sanitizer at the top level."""
    from sportsassets.api import app as api_app
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 90.0)
    p, http = _two_markets()
    b1 = p.add_book(ledger=0)
    v = _Venue(bid=0.30, ask=0.32)
    st1 = _tick(p, v, http=http)
    assert _census(st1, "rest_placed") == 1 and len(_places(v)) == 1
    rest = p.orders[b1["open_order_id"]]
    assert rest["qty"] * rest["wire"] == pytest.approx(90.0) and rest["cash_usd"] == 0.0
    b2 = _other_book(p)
    for n in (2, 3, 4):
        st = _tick(p, v, now=NOW + 30 * (n - 1), http=http)
        assert _census(st, "mirror_day_cap") == 0, (n, st["census"])
        assert _census(st, "cancelled_unfilled") == 0 and st["cancelled"] == 0, (n, st["census"])
        assert not _cancels(v), (n, _cancels(v))
        assert p.orders[rest["id"]]["state"] == "open" and b1["open_order_id"] == rest["id"], n
        assert _census(st, "open_order_pending") >= 1, (n, st["census"])
        assert _census(st, "over_room") >= 1, (n, st["census"])
        assert st["mirror_day_room"] == 0.0 and st["orders_open"] == 1, n
        assert api_app._sanitize_detail(st)["mirror_day_room"] == 0.0, n
    assert len(_places(v)) == 1, _places(v)
    assert b2["open_order_id"] is None
    assert not [o for o in p.orders.values() if o["book_id"] == b2["id"]]


def test_a_day_filled_to_the_cap_still_blocks_and_cancels_the_rests(monkeypatch):
    """The other half of the split, the original behaviour pinned: when
    what FILLED reaches MIRROR_DAY_USD the block is set by name, every
    resting BUY is cancelled under it (_reconcile_open), nothing new
    rests, and the room reads the overspend below zero."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 90.0)
    p, http = _two_markets()
    b1 = _other_book(p)
    b1["ledger_net"] = 300
    # b1 bought $90 today: the filled row's cash is the day's spend
    p.add_order(b1, state="filled", order_id="x-filled", booked=300.0, cash_usd=90.0,
                done_at=NOW - 100)
    b2 = p.add_book(ledger=0)
    rest = p.add_order(b2)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-1")
    st = _tick(p, v, http=http)
    assert _census(st, "mirror_day_cap") >= 1, st["census"]
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[rest["id"]]["state"] == "cancelled" and b2["open_order_id"] is None
    assert _census(st, "cancelled_unfilled") == 1 and not _places(v)
    assert st["mirror_day_room"] == pytest.approx(-90.0)
    # a dollar under the cap filled: no block, the rest stands, and the
    # room is what is left after what filled and what rests
    p, http = _two_markets()
    b1 = _other_book(p)
    b1["ledger_net"] = 300
    p.add_order(b1, state="filled", order_id="x-filled", booked=300.0, cash_usd=89.0,
                done_at=NOW - 100)
    b2 = p.add_book(ledger=0)
    rest = p.add_order(b2)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-1")
    st = _tick(p, v, http=http)
    assert _census(st, "mirror_day_cap") == 0 and not _cancels(v) and not _places(v)
    assert p.orders[rest["id"]]["state"] == "open"
    assert st["mirror_day_room"] == pytest.approx(90.0 - 89.0 - 90.0)


def test_a_ttl_cancel_gives_the_rests_remainder_back_and_the_requote_is_at_full_size(
        monkeypatch):
    """THE GIVE-BACK (review of the first cut, 2026-09-05). The day
    room is $105: one $90 rest and $15 over. The rest has outlived its
    TTL, so this tick cancels it and re-quotes. The tick-start read
    counted the rest's $90 as standing, and only _place adjusts the
    reading, so without the give-back the re-quote would be sized
    from $15 -- 50 shares at 0.30 -- and replaced up to 300 next tick,
    one extra replace per re-quote at high utilisation. With it the
    cancelled rest's unfilled remainder returns to the room in
    _finish_order and the re-quote is the full 300."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 105.0)
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[o["id"]]["state"] == "cancelled" and st["requotes"] == 1
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 300 and pl[0][2] == 0.30, pl
    assert _census(st, "rest_placed") == 1 and _census(st, "over_room") == 0
    new = [x for x in p.orders.values() if x["id"] != o["id"]][0]
    assert new["state"] == "open" and new["qty"] == 300 and b["open_order_id"] == new["id"]
    # a partial fill gives back the REMAINDER only: 100 of 300 booked
    # before the TTL cancel (the venue holds them), $60 returns, the
    # room is 105 - 90 + 60 = 75, and the re-quote for the 200 left is
    # sized from it -> 200 (from $15 alone it would be 50)
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 100}, fills={"oid-1": (100.0, 0.30)})
    v.rest("oid-1")
    st = _tick(p, v)
    assert p.orders[o["id"]]["state"] == "cancelled" and b["ledger_net"] == 100
    assert _census(st, "partial_fill") == 1 and b["state"] == "live"
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 200, pl


def test_a_partial_fills_give_back_is_the_remainder_and_the_requote_is_room_limited(monkeypatch):
    """The give-back is qty - booked_filled at the wire, NOT qty at the
    wire (a mutant of the re-review, 2026-09-05). Cap $75: the tick-
    start read is -$15 (a $90 rest stands); 100 of 300 book before the
    TTL cancel; the give-back is the REMAINDER's $60, the room $45, and
    the re-quote for the 200 left is room-limited to 150. Giving back
    the whole $90 would size it 200 (room $75) and the day would stand
    at 30 + 60 = $90 against a $75 cap."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 75.0)
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 100}, fills={"oid-1": (100.0, 0.30)})
    v.rest("oid-1")
    st = _tick(p, v)
    assert p.orders[o["id"]]["state"] == "cancelled" and b["ledger_net"] == 100
    assert _census(st, "partial_fill") == 1 and b["state"] == "live"
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 150, pl


def test_a_cancelled_sell_rest_gives_nothing_back(monkeypatch):
    """A SELL was never in the read nor decremented, so its cancel owes
    the room nothing. Cap $100, $90 filled on book A today (room $10);
    A's stale SELL rest of 300 @ 0.30 is TTL-cancelled first in the
    tick; book B then wants $90 and gets the $10's 33 shares. A
    give-back of the SELL's $90 would let B rest 300."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p, http = _two_markets()
    a = p.add_book(ledger=300, us_market_slug=OTHER_SLUG, condition_id=OTHER_CID,
                   long_asset=M2, other_asset=N2)
    p.add_order(a, state="filled", order_id="x-filled", booked=300.0, cash_usd=90.0,
                done_at=NOW - 100)
    s = p.add_order(a, side=SELL, kind="reduce", order_id="oid-s", us_market_slug=OTHER_SLUG,
                    placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    b = p.add_book(ledger=0)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-s", side="SELL", slug=OTHER_SLUG)
    st = _tick(p, v, http=http)
    assert ("cancel", "oid-s", OTHER_SLUG) in _cancels(v)
    assert p.orders[s["id"]]["state"] == "cancelled"
    mine = [c for c in _places(v) if c[1] == SLUG and c[4] is False]
    assert len(mine) == 1 and mine[0][3] == 33, (mine, st["census"])
    assert b["open_order_id"] is not None


def test_an_expired_gtd_rest_gives_its_remainder_back(monkeypatch):
    """The give-back is for 'expired' as well as 'cancelled': a GTD rest
    the venue EXPIRED reads terminal in _reconcile_open and finishes
    'expired'; the room ($105) must get its $90 back so the same-tick
    re-quote is 300, not 50."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 105.0)
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, tif="GTD", placed_ts=NOW - 100)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-1", state="expired")
    st = _tick(p, v)
    assert p.orders[o["id"]]["state"] == "expired" and _census(st, "expired") == 1
    assert not _cancels(v)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 300, (pl, st["census"])


def test_a_rest_older_than_the_reads_window_gives_nothing_back(monkeypatch):
    """The give-back's window is the read's own. A BUY rest placed more
    than 24 h ago fell out of _SQL_MIRROR_DAY already (the fake reads
    the window from the statement), so the tick-start room never held
    it and its cancel owes nothing. Cap $105, two books on two markets
    in ONE tick: A's day-old rest is TTL-cancelled and re-quoted at 300
    ($90, room $15 left); B then gets the $15's 50 shares. Giving the
    old rest's $90 back would let B rest 300 and the day stand at $180
    against a $105 cap."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 105.0)
    p, http = _two_markets()
    a = p.add_book(ledger=0)
    o = p.add_order(a, placed_ts=NOW - 86400.0 - 1)
    b = _other_book(p)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-1")
    st = _tick(p, v, http=http)
    assert ("cancel", "oid-1", SLUG) in _cancels(v)
    assert p.orders[o["id"]]["state"] == "cancelled"
    pl = _places(v)
    assert [(c[1], c[3]) for c in pl] == [(SLUG, 300), (OTHER_SLUG, 50)], (pl, st["census"])
    assert a["open_order_id"] is not None and b["open_order_id"] is not None


def test_a_cancel_on_a_tick_that_never_read_the_day_is_not_a_book_error(monkeypatch):
    """In exits mode _global_guards returns before the day read, so
    t.mirror_day is None; a BUY rest from an earlier tick is cancelled
    under the mode's refusal and the cancel must be booked, never
    swallowed as book_error by a give-back against None."""
    monkeypatch.setenv("PMUS_MIRROR", "exits")
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, placed_ts=NOW - 100)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[o["id"]]["state"] == "cancelled" and b["open_order_id"] is None
    assert _census(st, "cancelled_unfilled") == 1, st["census"]
    assert _census(st, "book_error") == 0, st["census"]
    assert st["cancelled"] == 1


def test_the_per_tick_decrement_in_place_refuses_the_second_book_in_the_same_tick(monkeypatch):
    """The within-tick half of the rail, pinned: the day room is one
    rest ($90 + a little) and two books on two markets each want $90
    in ONE tick. The first rests; _place takes its notional off
    t.mirror_day for this tick, so the second is refused 'over_room'.
    Without the decrement both would rest and the day would stand at
    twice its room until the next tick's read."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 90.1)
    p, http = _two_markets()
    b1 = p.add_book(ledger=0)
    b2 = _other_book(p)
    v = _Venue(bid=0.30, ask=0.32)
    st = _tick(p, v, http=http)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 300, pl
    assert _census(st, "rest_placed") == 1 and _census(st, "over_room") == 1, st["census"]
    assert sum(1 for b in (b1, b2) if b["open_order_id"] is not None) == 1
    assert len([o for o in p.orders.values() if o["side"] == BUY]) == 1


def _guards(p, whale="rn1"):
    """_global_guards on a tick in mode ON: the day room it computes."""
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    t.mode, t.allow = ml.MODE_ON, {whale}
    p.clock = NOW
    _run(ml._global_guards(t))
    return t


def test_a_partial_fill_is_not_counted_twice(monkeypatch):
    """A rest of 100 @ 0.50 with 40 booked -- cash_usd written for the
    40 ($20) -- reads 20 + 60 x 0.50 = $50 against the day, not $70."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p = _pool()
    b = p.add_book(ledger=40)
    p.add_order(b, wire=0.50, qty=100, booked=40.0, cash_usd=20.0)
    t = _guards(p)
    assert t.increase_block is None and t.mirror_day == pytest.approx(50.0)
    assert t.stats["mirror_day_room"] == 50.0


@pytest.mark.parametrize("state, counts", [
    ("placing", True), ("open", True), ("unknown", True),
    ("filled", False), ("cancelled", False), ("expired", False), ("rejected", False),
    ("lost", False)])
def test_only_a_row_that_may_still_fill_counts_its_remainder(monkeypatch, state, counts):
    """The non-terminal states -- the set the one-open-per-book index
    and _SQL_ORDERS_OPEN name -- count qty - booked_filled at the wire;
    a terminal row counts its cash alone (here none)."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, wire=0.50, qty=100, state=state, order_id=("x" if state != "placing" else None))
    t = _guards(p)
    assert t.mirror_day == pytest.approx(50.0 if counts else 100.0)
    # a SELL rest is never the day's spend, whatever its state
    p2 = _pool()
    b2 = p2.add_book(ledger=300)
    p2.add_order(b2, side=rules.SELL, wire=0.50, qty=100, state=state, order_id="y")
    assert _guards(p2).mirror_day == pytest.approx(100.0)


def test_the_statements_are_pinned_by_tag():
    """The fake pool reads both predicates from the statement text;
    this pins the text the worker sends, and the fake's own contract:
    a CASE clause it does not model raises, it never falls back to
    cash alone."""
    # the 047 shape, sent while migration 050's column is absent, is the
    # statement as first pinned; the 050 shape (P2 rung S0) names the
    # wire intent beside the plan side and prices a BUY_SHORT row's
    # remainder at its collateral, 1 - wire -- a long BUY row reads the
    # same figure through either
    day047 = _flat(ml._SQL_MIRROR_DAY_047)
    assert day047.endswith("/* ml-mirror-day */")
    assert ("SELECT COALESCE(sum(cash_usd), 0)::float8 AS filled, "
            "COALESCE(sum(CASE WHEN state IN ('placing', 'open', 'unknown') "
            "THEN (qty - COALESCE(booked_filled, 0)) * wire ELSE 0 END), 0)::float8 AS open "
            "FROM mirror_orders WHERE side = 'BUY_LONG' "
            "AND placed_at > now() - interval '24 hours'") in day047
    day = _flat(ml._SQL_MIRROR_DAY)
    assert day.endswith("/* ml-mirror-day */")
    assert ("SELECT COALESCE(sum(cash_usd), 0)::float8 AS filled, "
            "COALESCE(sum(CASE WHEN state IN ('placing', 'open', 'unknown') "
            "THEN (qty - COALESCE(booked_filled, 0)) "
            "* (CASE WHEN intent = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire ELSE wire END) "
            "ELSE 0 END), 0)::float8 AS open "
            "FROM mirror_orders WHERE ((side = 'BUY_LONG' AND intent = 'ORDER_INTENT_BUY_LONG') "
            "OR intent = 'ORDER_INTENT_BUY_SHORT') "
            "AND placed_at > now() - interval '24 hours'") in day
    assert "state IN ('placing', 'open', 'unknown')" in _flat(ml._SQL_ORDERS_OPEN)
    assert "state IN ('placing', 'open', 'unknown')" in _flat(ml._SQL_ORDERS_OPEN_047)
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, wire=0.50, qty=100)
    p.clock = NOW
    assert p._run("fetchrow", ml._SQL_MIRROR_DAY, ()) == {"filled": 0.0, "open": 50.0}
    assert p._run("fetchrow", ml._SQL_MIRROR_DAY_047, ()) == {"filled": 0.0, "open": 50.0}
    with pytest.raises(AssertionError, match="CASE clause"):
        p._run("fetchrow", day.replace("state IN ('placing', 'open', 'unknown')", "state = 'open'"), ())
    rep = _flat(ml._SQL_REPLACES)
    assert rep.endswith("/* ml-replaces */")
    assert ("WHERE book_id = $1 AND reason IN ('replace', 'take') AND tif IN ('GTC', 'GTD') "
            "AND done_at > now() - interval '1 hour'") in rep
    assert "take_capped" in ml.CENSUS_KEYS and "replace_capped" in ml.CENSUS_KEYS
    assert BUY == "BUY_LONG" and SLUG        # the fixture's names, as the statements spell them


# ------------------------------------ 4. P2 rung S0: the short side of the day
#
# A BUY_SHORT row (the plan side SELL_LONG carrying the wire intent
# BUY_SHORT) counts against the day at its COLLATERAL, (1 - wire) x qty
# resting and fill_cash filled; a short book's cover rows and a long
# book's sells count nothing; every long row reads as it did.

def test_a_buy_short_row_counts_against_the_day_at_its_collateral_and_long_rows_are_unchanged():
    from sportsassets import live_executor as le
    p = _pool()
    b = p.add_book(ledger=0)
    s = p.add_book(ledger=-300, intent="ORDER_INTENT_BUY_SHORT", avg_cost=0.32, gross_buy=204.0)
    p.clock = NOW
    # a long rest of 100 @ 0.50 and a short add of 100 @ contract 0.32 (0.68 collateral)
    p.add_order(b, wire=0.50, qty=100)
    p.add_order(s, side=SELL, wire=0.32, qty=100, kind="increase")
    day = p._run("fetchrow", ml._SQL_MIRROR_DAY, ())
    assert day == {"filled": 0.0, "open": pytest.approx(50.0 + 68.0)}
    # the 047 shape, sent while the column is absent, reads the long row alone
    assert p._run("fetchrow", ml._SQL_MIRROR_DAY_047, ()) == {"filled": 0.0, "open": 50.0}
    # a short cover (plan BUY_LONG, wire SELL_SHORT) and a long sell count nothing
    p.add_order(s, side=BUY, wire=0.30, qty=50, kind="reduce", state="open", order_id="oid-c")
    p.add_order(b, side=SELL, wire=0.55, qty=50, kind="reduce", state="open", order_id="oid-d")
    assert p._run("fetchrow", ml._SQL_MIRROR_DAY, ())["open"] == pytest.approx(118.0)
    # filled: the cash the fills cost, as _book_fill writes it on either sign
    for o in p.orders.values():
        if o["intent"] == "ORDER_INTENT_BUY_SHORT":
            o.update(state="filled", booked_filled=100.0, cash_usd=le.fill_cash(100, 0.32, o["intent"]))
    day = p._run("fetchrow", ml._SQL_MIRROR_DAY, ())
    assert day == {"filled": pytest.approx(68.0), "open": pytest.approx(50.0)}
    # a row written before 050 reads the column's DEFAULT: a long BUY counts, a long SELL does not
    old_buy = p.add_order(b, wire=0.40, qty=10, order_id="oid-e")
    old_sell = p.add_order(b, side=SELL, wire=0.60, qty=10, order_id="oid-f", kind="reduce")
    del old_buy["intent"], old_sell["intent"]
    assert p._run("fetchrow", ml._SQL_MIRROR_DAY, ())["open"] == pytest.approx(54.0)


def test_a_resting_buy_short_consumes_the_day_room_and_gives_its_collateral_back_on_cancel(monkeypatch):
    from tests.test_mirror_live_worker import _mkt, _shorts_on
    _shorts_on(monkeypatch)
    p = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    v = _Venue()
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "short_open") == 1 and _places(v)[0][3] == 300
    o = next(iter(p.orders.values()))
    assert o["intent"] == "ORDER_INTENT_BUY_SHORT" and o["wire"] == 0.32
    # the day room the tick read before the rest was placed
    assert st["mirror_day_room"] == pytest.approx(rules.MIRROR_DAY_USD)
    # the next tick's read counts the resting remainder at its collateral, 300 x 0.68
    v2 = _Venue(held={})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert st2["mirror_day_room"] == pytest.approx(rules.MIRROR_DAY_USD - 300 * 0.68)
    assert o["state"] == "open"
    # a TTL cancel gives the remainder back at the collateral, and the
    # re-quote that follows in the same tick is at full size
    v3 = _Venue(held={})
    v3.orders = v.orders
    st3 = _tick(p, v3, now=NOW + rules.MIRROR_REST_TTL_S + 1, http=_mkt(100.0, 400.0))
    assert _census(st3, "cancelled_unfilled") == 1 and o["state"] == "cancelled"
    requote = [x for x in _places(v3)]
    assert len(requote) == 1 and requote[0][3] == 300 and requote[0][6] == "ORDER_INTENT_BUY_SHORT"


def test_a_second_short_book_in_the_same_tick_is_sized_off_the_room_net_of_the_firsts_collateral(monkeypatch):
    """The within-tick half of the rail on the SHORT side (mutation
    lens, mutant h3): _place takes the first short rest's COLLATERAL
    (300 x 0.68 = $204) off t.mirror_day, not its contract notional
    (300 x 0.32 = $96), so with $250 of day room the second short book
    on a second market is sized off $46 -- 67 shares of a 0.68 leg --
    not off $154."""
    from tests.test_mirror_live_worker import _short_book, _shorts_on
    _shorts_on(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 250.0)
    fills = _his(100, other_size=400, other_px=0.72) + [
        _fill(M2, "BUY", 100.0, 0.31, NOW - 2400), _fill(N2, "BUY", 400.0, 0.72, NOW - 2300)]
    p = _pool(fills=fills, snap={M: 100.0, N: 400.0, M2: 100.0, N2: 400.0})
    p.markets[OTHER_CID] = {"closed": False, "resolved": False, "resolved_prices": None}
    p.token_index.update({M2: 1, N2: 0})
    p.token_cid.update({M2: OTHER_CID, N2: OTHER_CID})
    # two open short books, flat, one on each market (his net -300 on both)
    b1 = _short_book(p, ledger=0)
    b2 = _short_book(p, ledger=0, us_market_slug=OTHER_SLUG, condition_id=OTHER_CID,
                     long_asset=M2, other_asset=N2)
    http = _ByMarket({
        CID: [{"conditionId": CID, "asset": M, "size": 100},
              {"conditionId": CID, "asset": N, "size": 400}],
        OTHER_CID: [{"conditionId": OTHER_CID, "asset": M2, "size": 100},
                    {"conditionId": OTHER_CID, "asset": N2, "size": 400}]})
    v = _Venue(bid=0.30, ask=0.32)
    st = _tick(p, v, http=http)
    assert (b1["target"], b2["target"]) == (-300, -300)
    pl = _places(v)
    assert len(pl) == 2 and [x[6] for x in pl] == ["ORDER_INTENT_BUY_SHORT"] * 2, pl
    assert [x[1] for x in pl] == [SLUG, OTHER_SLUG]
    assert [x[3] for x in pl] == [300, int((250.0 - 300 * 0.68) / 0.68)] == [300, 67], pl
    assert _census(st, "short_open") == 2 and _census(st, "rest_placed") == 2
    assert st["mirror_day_room"] == pytest.approx(250.0)
    # and the next tick's read counts both rests at their collateral
    v2 = _Venue(bid=0.30, ask=0.32)
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=http)
    assert st2["mirror_day_room"] == pytest.approx(250.0 - (300 + 67) * 0.68)


# ---------------------------------------------------------- 3. the mode line

def _quiet_stats():
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_EXITS, whales=["rn1"], books_live=2, orders_open=1)
    return stats


def _expected_line(stats):
    return "mirror_live mode=%s whales=%s books=%s open=%s day=%s venue=%s stats=%s" % (
        stats["mode"], stats["whales"], stats["books_live"], stats["orders_open"],
        stats["mirror_day_room"], stats["venue_state"],
        {k: v for k, v in stats.items() if k not in ("census", "recent")})


def _mode_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")]


class _Stop(BaseException):
    """Ends main()'s loop: not an Exception, so its `except Exception`
    cannot swallow it."""


def test_main_logs_the_mode_line_on_ticks_10_and_20_and_not_on_tick_9(monkeypatch, caplog):
    """main() driven for 20 quiet ticks (no ops, no abandon) with a fake
    tick_once that records, on each call, how many mode lines stand in
    the log BEFORE it: none through tick 9, one after tick 10, still
    one before tick 20, two after it. Each is the exact line; the
    ops/abandoned line never appears on a quiet tick."""
    stats = _quiet_stats()
    before = {}

    async def _tick_once(pool, pmus, http):
        n = len(before) + 1
        before[n] = len(_mode_lines(caplog))
        if n > 20:
            raise _Stop()
        return dict(stats)

    async def _get_pool():
        return object()

    async def _heartbeat(*a, **k):
        return None

    monkeypatch.setattr(ml, "tick_once", _tick_once)
    monkeypatch.setattr(ml, "get_pool", _get_pool)
    monkeypatch.setattr(ml, "heartbeat", _heartbeat)
    monkeypatch.setattr(ml, "settings",
                        lambda: types.SimpleNamespace(data_api_base="http://data.invalid"))
    monkeypatch.setattr(ml, "POLL_S", 0.0)
    monkeypatch.setattr(ml, "WAKE_MIN_GAP_S", 0.0)
    ml._WAKE.clear()
    assert ml.MODE_LINE_EVERY_TICKS == 10
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        with pytest.raises(_Stop):
            _run(ml.main())
    assert before[9] == 0 and before[10] == 0, "nothing through tick 9"
    assert before[11] == 1 and before[20] == 1, "one line, on tick 10"
    assert before[21] == 2, "the second, on tick 20"
    lines = _mode_lines(caplog)
    assert lines == [_expected_line(stats)] * 2
    assert all(r.levelno == logging.INFO for r in caplog.records if r.getMessage() in lines)
    assert not [r for r in caplog.records if r.getMessage().startswith("mirror_live: {")]


def test_the_cadence_helper_and_the_env_dial(monkeypatch, caplog):
    stats = _quiet_stats()
    monkeypatch.setattr(ml, "MODE_LINE_EVERY_TICKS", 3)
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        for n in range(0, 10):
            ml._mode_line(dict(stats), n)
    assert len(_mode_lines(caplog)) == 3          # ticks 3, 6, 9; never tick 0
    assert _mode_lines(caplog)[0] == _expected_line(stats)
    # the stats' own keys are what the line is built from: `day` is the
    # ROOM the tick read (mirror_day_room, dollars; None on the quiet
    # exits tick above, which never reads it), never the census count
    # of the cap's refusals, and census/recent never print
    assert " day=None " in _mode_lines(caplog)[0]
    caplog.clear()
    stats.update(mode=ml.MODE_ON, mirror_day_room=412.5)
    stats["census"]["mirror_day_cap"] = 4
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(stats, 3)
    line = _mode_lines(caplog)[0]
    assert " day=412.5 " in line and " day=4 " not in line
    assert "'census'" not in line and "'recent'" not in line
    assert line.startswith("mirror_live mode=on whales=['rn1'] books=2 open=1 day=412.5 venue=None stats={")
    assert "mirror_day_room" in ml._new_stats() and ml._new_stats()["mirror_day_room"] is None
    # the dial: an int with a floor of one; absent, blank or unparseable is 10
    monkeypatch.delenv("MIRROR_MODE_LINE_EVERY_TICKS", raising=False)
    assert ml._mode_line_every_ticks() == 10
    for raw, want in (("25", 25), (" 7 ", 7), ("1", 1), ("0", 1), ("-3", 1),
                      ("", 10), ("abc", 10), ("2.5", 10)):
        monkeypatch.setenv("MIRROR_MODE_LINE_EVERY_TICKS", raw)
        assert ml._mode_line_every_ticks() == want, raw


# ------------------- 4. the venue's market state, and the backed-off tick

THIRD_CID, THIRD_SLUG, M3, N3 = "0xthird", "aec-atp-third-2026-09-02", "tok-m3", "tok-n3"
HALTED = "MARKET_STATE_HALTED"


def _three_markets():
    """rn1 long 300 on the fixture market, a second and a third, all
    readable (the _two_markets shape, one market wider)."""
    fills = _his() + [_fill(M2, "BUY", 300.0, 0.31, NOW - 2500), _fill(M3, "BUY", 300.0, 0.31, NOW - 2400)]
    p = _pool(fills=fills, snap={M: 300.0, N: 0.0, M2: 300.0, N2: 0.0, M3: 300.0, N3: 0.0})
    for cid in (OTHER_CID, THIRD_CID):
        p.markets[cid] = {"closed": False, "resolved": False, "resolved_prices": None}
    p.token_index.update({M2: 1, N2: 0, M3: 1, N3: 0})
    p.token_cid.update({M2: OTHER_CID, N2: OTHER_CID, M3: THIRD_CID, N3: THIRD_CID})
    http = _ByMarket({
        CID: [{"conditionId": CID, "asset": M, "size": 300},
              {"conditionId": CID, "asset": N, "size": 0}],
        OTHER_CID: [{"conditionId": OTHER_CID, "asset": M2, "size": 300},
                    {"conditionId": OTHER_CID, "asset": N2, "size": 0}],
        THIRD_CID: [{"conditionId": THIRD_CID, "asset": M3, "size": 300},
                    {"conditionId": THIRD_CID, "asset": N3, "size": 0}]})
    return p, http


def test_a_mixed_tick_two_halted_books_then_an_open_quoted_one_does_not_abandon():
    """The miss streak is a STREAK: two halted markets and then an OPEN
    quoted one reset it, the tick goes on, and the open market is
    placed on. `states` overrides the venue's state per slug."""
    p, http = _three_markets()
    b1 = p.add_book(ledger=0)
    b2 = _other_book(p)
    b3 = p.add_book(ledger=0, us_market_slug=THIRD_SLUG, condition_id=THIRD_CID,
                    long_asset=M3, other_asset=N3)
    v = _Venue(bid=0.30, ask=0.32, states={SLUG: HALTED, OTHER_SLUG: HALTED})
    st = _tick(p, v, http=http)
    assert [c[1] for c in v.calls if c[0] == "bbo"] == [SLUG, OTHER_SLUG, THIRD_SLUG]
    assert not st["abandoned"] and "abandon_reason" not in st
    assert _census(st, "venue_halted") == 2 and _census(st, "no_quote") == 0, st["census"]
    assert st["venue_state"] == HALTED, "two of three reads: the most common state"
    pl = _places(v)
    assert [(c[1], c[3]) for c in pl] == [(THIRD_SLUG, 300)], pl
    assert b3["open_order_id"] is not None
    assert b1["open_order_id"] is None and b2["open_order_id"] is None, "never placed on a halted market"
    # the same three, all halted: three EXISTING BOOKS, so the streak
    # never moves (U10, 2026-09-06: a non-OPEN read on a book is the
    # book's own to handle) -- every book is walked and held under its
    # own name, nothing is placed, the tick goes on
    p, http = _three_markets()
    books = [p.add_book(ledger=0), _other_book(p),
             p.add_book(ledger=0, us_market_slug=THIRD_SLUG, condition_id=THIRD_CID,
                        long_asset=M3, other_asset=N3)]
    v = _Venue(state=HALTED)
    st = _tick(p, v, http=http)
    assert not st["abandoned"] and "abandon_reason" not in st and not _places(v)
    assert _census(st, "venue_halted") == 3 and _census(st, "tick_abandoned") == 0, st["census"]
    assert [c[1] for c in v.calls if c[0] == "bbo"] == [SLUG, OTHER_SLUG, THIRD_SLUG], "every book read"
    assert [b["last_reason"] for b in books] == ["no_mark"] * 3, "each held under the plan's own name"
    assert st["venue_state"] == HALTED
    # three halted CANDIDATES on the same clock: the venue-wide reading, as before
    p2 = _pool(conds=["c1", "c2", "c3"])
    v2 = _Venue(state=HALTED)
    st2 = _tick(p2, v2)
    assert st2["abandoned"] and st2["abandon_reason"] == "venue_halted" and not _places(v2)
    assert _census(st2, "venue_halted") == 3 and st2["reads"] == 3


class _SeqVenue(_Venue):
    """A venue whose quote reads answer in ORDER, one bbo_read dict per
    call (the last one repeats), so a miss can follow a quote."""

    def __init__(self, answers, **kw):
        super().__init__(**kw)
        self.answers = list(answers)

    def bbo_read(self, client, slug):
        self.calls.append(("bbo", slug))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return {"bid": None, "ask": None, "state": None, "error": None, **a}


_H = {"state": HALTED}
_Q = {"bid": 0.30, "ask": 0.32, "state": "MARKET_STATE_OPEN"}


def test_a_quoted_read_resets_the_miss_streak_so_a_miss_after_it_starts_a_new_one():
    """THE RESET, PINNED (review of U9: deleting `t.misses = 0` survived
    the mixed-tick test above, whose quoted read was the LAST read).
    Four candidates, four reads in sequence -- halted, halted, quoted
    OPEN, halted: with the reset the streak reads 1, 2, 0, 1 and the
    tick goes on; without it the fourth read is the third miss and the
    tick abandons. A `cN` candidate has no markets row on this fixture,
    so the quoted one is refused `market_unreadable` AFTER its read and
    no book opens to re-read the slug: exactly four reads, three of
    them venue_halted."""
    p = _pool(conds=["c1", "c2", "c3", "c4"])
    v = _SeqVenue([_H, _H, _Q, _H])
    st = _tick(p, v)
    assert [c for c in v.calls if c[0] == "bbo"] == [("bbo", SLUG)] * 4 and st["reads"] == 4
    assert _census(st, "venue_halted") == 3 and _census(st, "no_quote") == 0, st["census"]
    assert not st["abandoned"] and "abandon_reason" not in st and st["status"] == "ok"
    assert _census(st, "tick_abandoned") == 0 and _census(st, "market_unreadable") == 1, st["census"]
    assert st["venue_state"] == HALTED and not p.books and not _places(v)
    # the same four reads without a quote between them: the third abandons, the fourth never happens
    p2 = _pool(conds=["c1", "c2", "c3", "c4"])
    v2 = _SeqVenue([_H, _H, _H, _Q])
    st2 = _tick(p2, v2)
    assert st2["abandoned"] and st2["abandon_reason"] == "venue_halted" and st2["reads"] == 3
    # at _bbo's own level: the streak goes 1, 2, 0, 1
    t = ml._Tick(pool=_pool(), pmus=_SeqVenue([_H, _H, _Q, _H]), http=None, now=NOW,
                 stats=ml._new_stats())
    seen = []
    for _ in range(4):
        _run(ml._bbo(t, SLUG))
        seen.append(t.misses)
    assert seen == [1, 2, 0, 1] and t.reads == 4 and not t.abandoned
    assert t.stats["venue_state"] == HALTED and t.venue_states == {HALTED: 3, "MARKET_STATE_OPEN": 1}


# ------------------------------------- 4. the miss streak counts venue-wide evidence only (U10)

OPEN, EXPIRED = "MARKET_STATE_OPEN", "MARKET_STATE_EXPIRED"
_E = {"state": OPEN}                  # OPEN, empty book: a thin market, the venue is up
_X = {"state": EXPIRED}               # a market that has ended, empty
_N = {}                               # the SDK-typed shape: no state, empty


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def _bbo_seq(answers, book=False):
    """t.misses after each read of `answers`, at _bbo's own level; the
    tick's census is the one _mirror_stop writes (tick_once's binding)."""
    t = ml._Tick(pool=_pool(), pmus=_SeqVenue(answers), http=None, now=NOW, stats=ml._new_stats())
    seen = []
    prior = ml._current_stats
    ml._current_stats = t.stats
    try:
        for _ in answers:
            _run(ml._bbo(t, SLUG, book=book))
            seen.append(t.misses)
    finally:
        ml._current_stats = prior
    return t, seen


def test_three_open_empty_candidates_are_refused_no_quote_and_the_walk_goes_on(caplog):
    """U10 (2026-09-06, 00:40Z-00:47Z, deploy dd1ed77, venue OPEN):
    every tick ended `tick abandoned (no_quote), backing off 60.0s`
    while books opened between them -- the venue was quoting the
    markets he is in, and three thin markets with empty books in a
    row tripped the streak built for a venue-wide outage: the walk
    broke, the next tick waited 60 s, one new book per ~90 s. With an
    OPEN state, an empty book is a PER-MARKET refusal: c1..c3 are
    refused `no_quote` by name, the streak stands at 0, the fourth
    candidate (quoted) opens a book and rests in the SAME tick, nothing
    abandons and nothing backs off (the next tick 30 s on runs)."""
    assert ms.MISS_STREAK_ABANDON == 3
    p = _pool(conds=["c1", "c2", "c3", CID])
    v = _SeqVenue([_E, _E, _E, _Q])
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    # five reads: c1..c3, the fourth candidate, and the book it opened
    # planned in the same tick (its own read of the slug)
    assert [c for c in v.calls if c[0] == "bbo"] == [("bbo", SLUG)] * 5 and st["reads"] == 5
    assert not st["abandoned"] and "abandon_reason" not in st and st["status"] == "ok"
    assert _census(st, "no_quote") == 3 and _census(st, "venue_halted") == 0, st["census"]
    assert _census(st, "tick_abandoned") == 0 and _census(st, "no_mark") == 3, st["census"]
    assert st["venue_state"] == OPEN
    assert len(p.books) == 1 and next(iter(p.books.values()))["condition_id"] == CID
    assert [(c[1], c[3]) for c in _places(v)] == [(SLUG, 300)], "the fourth candidate rested"
    assert ml._backoff_until == 0.0 and not [w for w in _warnings(caplog) if "abandoned" in w]
    # the next tick is not backed off
    st2 = _tick(p, _Venue(), now=NOW + ml.POLL_S, keep_backoff=True)
    assert not st2.get("skipped_backoff") and st2["reads"] >= 1
    # an EXISTING book (quoted, rested) walked before three OPEN-empty
    # candidates: planned as ever, and the tick goes on past them
    p = _pool(conds=["c1", "c2", "c3"])
    b = p.add_book(ledger=0)
    v = _SeqVenue([_Q, _E, _E, _E])
    st = _tick(p, v)
    assert not st["abandoned"] and _census(st, "no_quote") == 3 and st["reads"] == 4
    assert b["open_order_id"] is not None and st["books_live"] == 1
    assert [(c[1], c[3]) for c in _places(v)] == [(SLUG, 300)]


def test_an_open_empty_read_neither_steps_nor_resets_the_streak():
    """The three sequences the brief pins, at the tick level and at
    _bbo's own. OPEN-empty, HALTED x3: abandons `venue_halted` -- the
    OPEN-empty read did not RESET the streak either (a thin market is
    not evidence the venue is up, only a quote is). HALTED x2, quoted,
    HALTED x2: no abandon (the quote reset). No state x3 (the
    SDK-typed shape, which cannot be told from a halt): abandons
    `no_quote` as before, the conservative default. At _bbo's level
    the streak for [OPEN-empty, HALTED, quoted, HALTED, OPEN-empty]
    reads 0, 1, 0, 1, 1."""
    p = _pool(conds=["c1", "c2", "c3", "c4"])
    v = _SeqVenue([_E, _H, _H, _H])
    st = _tick(p, v)
    assert st["abandoned"] and st["abandon_reason"] == "venue_halted" and st["reads"] == 4
    assert _census(st, "no_quote") == 1 and _census(st, "venue_halted") == 3, st["census"]
    assert ml._backoff_until == NOW + ms.BACKOFF_S
    p = _pool(conds=["c1", "c2", "c3", "c4", "c5"])
    v = _SeqVenue([_H, _H, _Q, _H, _H])
    st = _tick(p, v)
    assert not st["abandoned"] and st["reads"] == 5 and _census(st, "venue_halted") == 4
    p = _pool(conds=["c1", "c2", "c3", "c4"])
    v = _SeqVenue([_N, _N, _N, _Q])
    st = _tick(p, v)
    assert st["abandoned"] and st["abandon_reason"] == "no_quote" and st["reads"] == 3
    assert _census(st, "no_quote") == 3 and st["venue_state"] is None
    t, seen = _bbo_seq([_E, _H, _Q, _H, _E])
    assert seen == [0, 1, 0, 1, 1] and not t.abandoned and t.reads == 5
    assert t.stats["census"]["no_quote"] == 2 and t.stats["census"]["venue_halted"] == 2
    # the census serves both names: `no_quote` sits under the endpoint's
    # 40-key cap in CENSUS_KEYS order, `venue_halted` rides on `integ`
    from sportsassets.api import app as api_app
    assert ml.CENSUS_KEYS.index("no_quote") < api_app._DETAIL_MAX_KEYS
    assert "venue_halted" in ml._INTEG_CENSUS_KEYS
    t.stats["integ"] = ml._integ_block(t.stats)          # the projection tick_once writes last
    served = api_app._sanitize_detail(t.stats)
    assert served["census"]["no_quote"] == 2 and served["integ"]["venue_halted"] == 2


def test_a_non_open_read_on_an_existing_book_never_counts_toward_the_streak(caplog):
    """TODAY'S LOG (2026-09-06 12:32Z): `tick abandoned (venue_halted:
    MARKET_STATE_EXPIRED), backing off 60.0s` between placements. One
    open book whose market had ended at the venue (the markets row
    still reading live), and his morning's expired markets still
    inside the candidate lookback: the book's read and the expired
    candidates' reads each counted a miss, the third abandoned the
    tick before the quoted candidates were reached, and the loop
    backed off. Neither is venue-outage evidence: a read on an
    EXISTING book is the book's own to handle (`venue_halted` on the
    census, held `no_mark`, its rests cancelled, closed once the
    markets row reads closed) and a TERMINAL state (EXPIRED, CLOSED,
    TERMINATED) is a per-market fact on any read. The tick goes on,
    the quoted candidate opens a book and rests."""
    p = _pool(conds=["c1", "c2", CID])
    p.markets[OTHER_CID] = {"closed": False, "resolved": False, "resolved_prices": None}
    b = _other_book(p)
    v = _SeqVenue([_X, _X, _X, _Q])             # the book, c1, c2, then the fixture market
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    # the book, c1, c2, the fixture candidate, and the book it opened planned in-tick
    assert [c[1] for c in v.calls if c[0] == "bbo"] == [OTHER_SLUG, SLUG, SLUG, SLUG, SLUG]
    assert not st["abandoned"] and "abandon_reason" not in st and st["status"] == "ok"
    assert _census(st, "venue_halted") == 3 and _census(st, "no_quote") == 0, st["census"]
    assert _census(st, "tick_abandoned") == 0 and ml._backoff_until == 0.0
    assert st["venue_state"] == EXPIRED, "the state is still published"
    assert b["last_reason"] == "no_mark" and b["open_order_id"] is None and b["state"] == "live"
    opened = [x for x in p.books.values() if x["id"] != b["id"]]
    assert len(opened) == 1 and opened[0]["condition_id"] == CID
    assert [(c[1], c[3]) for c in _places(v)] == [(SLUG, 300)]
    assert not [w for w in _warnings(caplog) if "abandoned" in w]
    # the same book HALTED (an in-play halt, the venue's own shape for a
    # fight in progress) three ticks running: the book is held, never
    # the tick -- and the reset a quoted candidate gives is not needed
    p = _pool(conds=["c1", "c2"])
    p.markets[OTHER_CID] = {"closed": False, "resolved": False, "resolved_prices": None}
    b = _other_book(p)
    v = _Venue(states={OTHER_SLUG: HALTED}, bid=None, ask=None, state=None)
    st = _tick(p, v)
    assert not st["abandoned"], "one book read plus two no-state empties: 0, 1, 2"
    assert _census(st, "venue_halted") == 1 and _census(st, "no_quote") == 2
    assert b["last_reason"] == "no_mark"
    # at _bbo's level, a book read: HALTED, EXPIRED and CLOSED-with-a-
    # stale-book leave the streak where it stood, an unreadable read
    # and a no-state empty read still count, a quote still resets
    t, seen = _bbo_seq([_H, _X, {"bid": 0.01, "ask": 0.20, "state": "MARKET_STATE_CLOSED"},
                        _N, {"error": "RuntimeError"}, _Q, _H], book=True)
    assert seen == [0, 0, 0, 1, 2, 0, 0] and not t.abandoned
    assert t.stats["census"]["venue_halted"] == 4 and t.stats["census"]["no_quote"] == 2
    # the source: the book's read is the one that says so
    src = inspect.getsource(ml._tick_book)
    assert "book=True" in src and "book=True" not in inspect.getsource(ml._tick_candidate)


def test_a_terminal_state_counts_nowhere_and_a_non_terminal_one_still_abandons_three_candidates(caplog):
    """Three CANDIDATES on ended markets (the venue says EXPIRED, or
    CLOSED with a settled market's stale rests -- the probe payloads
    of 2026-09-05): each refused `venue_halted`, none a miss, no
    abandon. The same three SUSPENDED, or HALTED: the venue-wide
    reading, abandoned under the state's own word. The terminal set
    is one definition, the shadow's, read by both workers."""
    assert ms.STATE_TERMINAL == {"MARKET_STATE_EXPIRED", "MARKET_STATE_CLOSED", "MARKET_STATE_TERMINATED"}
    assert "MARKET_STATE_OPEN" not in ms.STATE_TERMINAL and HALTED not in ms.STATE_TERMINAL
    for venue in (_Venue(bid=None, ask=None, state=EXPIRED),
                  _Venue(bid=0.01, ask=0.20, state="MARKET_STATE_CLOSED"),
                  _Venue(bid=None, ask=None, state="MARKET_STATE_TERMINATED")):
        p = _pool(conds=["c1", "c2", "c3", "c4"])
        st = _tick(p, venue)
        assert not st["abandoned"] and st["reads"] == 4, venue.state
        assert _census(st, "venue_halted") == 4 and _census(st, "no_quote") == 0, st["census"]
        assert st["venue_state"] == venue.state and not p.books and not _places(venue)
    for state in ("MARKET_STATE_SUSPENDED", HALTED, "MARKET_STATE_PREOPEN"):
        p = _pool(conds=["c1", "c2", "c3", "c4"])
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=ml.log.name):
            st = _tick(p, _Venue(bid=None, ask=None, state=state))
        assert st["abandoned"] and st["abandon_reason"] == "venue_halted" and st["reads"] == 3, state
        assert f"mirror_live: tick abandoned (venue_halted: {state}), backing off 60.0s" in _warnings(caplog)


def test_a_backed_off_tick_after_a_no_venue_safe_tick_prints_mode_safe(monkeypatch, caplog):
    """The mode the worker holds is captured AFTER the no_venue
    narrowing: a tick that read PMUS_MIRROR=on and then found no
    venue armed is a SAFE tick, and the backed-off tick that follows
    it prints mode=safe -- never the `on` the environment said before
    the narrowing."""
    monkeypatch.setattr(le, "active_venue", lambda: None)
    st = _tick(_pool(), _Venue())
    assert st["mode"] == ml.MODE_SAFE and _census(st, "no_venue") >= 1 and not st["abandoned"]
    assert ml._last_mode == ml.MODE_SAFE and ml._last_whales == ["rn1"]
    ml._backoff_until = NOW + ms.BACKOFF_S
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        st2 = _tick(_pool(), _Venue(), now=NOW + 1, keep_backoff=True)
        ml._mode_line(st2, ml.MODE_LINE_EVERY_TICKS)
    assert st2["skipped_backoff"] is True and st2["backoff_left_s"] == 59.0
    assert st2["mode"] == ml.MODE_SAFE and st2["whales"] == ["rn1"] and st2["reads"] == 0
    line = _mode_lines(caplog)[0]
    assert line.startswith("mirror_live mode=safe whales=['rn1'] books=0 open=0 day=None venue=None backoff=59.0 stats={")
    assert "mode=on" not in line
    src = inspect.getsource(ml._tick)
    assert src.index('_mirror_stop("no_venue")') < src.index("_last_mode, _last_whales = t.mode")


def test_a_tick_inside_the_backoff_carries_the_mode_the_worker_holds_and_the_seconds_left(caplog):
    """THE MODE LINE ON A BACKED-OFF TICK LIED (live log 23:09-23:25Z,
    2026-09-05): a skipped tick returned _new_stats() whole, so every
    10th tick that fell inside a 60 s backoff printed `mode=safe
    whales=[]` between `mode=on` abandons. The skipped tick now carries
    the last completed tick's mode and allowlist, `skipped_backoff` and
    the seconds left, and the mode line prints `backoff=`."""
    p = _pool(conds=["c1", "c2", "c3"])
    st = _tick(p, _Venue(state=HALTED))
    assert st["abandoned"] and st["mode"] == ml.MODE_ON and st["whales"] == ["rn1"]
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        st2 = _tick(_pool(), _Venue(), now=NOW + 1, keep_backoff=True)
        ml._mode_line(st2, ml.MODE_LINE_EVERY_TICKS)
    assert st2["skipped_backoff"] is True and st2["backoff_left_s"] == 59.0
    assert st2["mode"] == ml.MODE_ON and st2["whales"] == ["rn1"], "the mode the worker holds"
    assert st2["reads"] == 0 and not st2["abandoned"] and st2["status"] == "ok"
    line = _mode_lines(caplog)[0]
    assert line.startswith("mirror_live mode=on whales=['rn1'] books=0 open=0 day=None venue=None backoff=59.0 stats={")
    assert "mode=safe" not in line
    # a tick in SAFE mode that abandons... cannot: SAFE reads no market.
    # A SAFE tick's mode is still SAFE on the following skipped tick.
    ml._last_mode, ml._last_whales = ml.MODE_SAFE, []
    st3 = _tick(_pool(), _Venue(), now=NOW + 2, keep_backoff=True)
    assert st3["skipped_backoff"] and st3["mode"] == ml.MODE_SAFE and st3["whales"] == []
    # no tick has completed yet: the mode is READ, the real way
    ml._last_mode = None
    st4 = _tick(_pool(), _Venue(), now=NOW + 3, keep_backoff=True)
    assert st4["skipped_backoff"] and st4["mode"] == ml.MODE_ON and st4["whales"] == ["rn1"]


def test_main_prints_backoff_and_never_mode_safe_on_a_backed_off_tick(monkeypatch, caplog):
    """main() driven with a fake tick_once that returns a backed-off
    tick on tick 10 (mode on, skipped_backoff, 42.5 s left) and a
    quiet tick everywhere else: the tick-10 line says `mode=on ...
    backoff=42.5`, the tick-20 line is the quiet one."""
    quiet = _quiet_stats()
    quiet.update(mode=ml.MODE_ON)
    backed = ml._new_stats()
    backed.update(mode=ml.MODE_ON, whales=["rn1"], skipped_backoff=True, backoff_left_s=42.5)
    seen = {"n": 0}

    async def _tick_once(pool, pmus, http):
        seen["n"] += 1
        if seen["n"] > 20:
            raise _Stop()
        return dict(backed if seen["n"] == 10 else quiet)

    async def _get_pool():
        return object()

    async def _heartbeat(*a, **k):
        return None

    monkeypatch.setattr(ml, "tick_once", _tick_once)
    monkeypatch.setattr(ml, "get_pool", _get_pool)
    monkeypatch.setattr(ml, "heartbeat", _heartbeat)
    monkeypatch.setattr(ml, "settings",
                        lambda: types.SimpleNamespace(data_api_base="http://data.invalid"))
    monkeypatch.setattr(ml, "POLL_S", 0.0)
    monkeypatch.setattr(ml, "WAKE_MIN_GAP_S", 0.0)
    ml._WAKE.clear()
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        with pytest.raises(_Stop):
            _run(ml.main())
    lines = _mode_lines(caplog)
    assert len(lines) == 2
    assert lines[0].startswith("mirror_live mode=on whales=['rn1'] books=0 open=0 day=None venue=None backoff=42.5 stats={")
    assert "'skipped_backoff': True" in lines[0] and "'backoff_left_s': 42.5" in lines[0]
    assert lines[1] == _expected_line(quiet) and "backoff=" not in lines[1]
    assert not [ln for ln in lines if "mode=safe" in ln]
    # an abandoned tick's line names the reason after the venue's state
    ab = ml._new_stats()
    ab.update(mode=ml.MODE_ON, whales=["rn1"], abandoned=True, abandon_reason="venue_halted",
              venue_state=HALTED, status="degraded")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(ab, ml.MODE_LINE_EVERY_TICKS)
    assert _mode_lines(caplog)[0].startswith(
        "mirror_live mode=on whales=['rn1'] books=0 open=0 day=None venue=MARKET_STATE_HALTED "
        "abandon=venue_halted stats={")
