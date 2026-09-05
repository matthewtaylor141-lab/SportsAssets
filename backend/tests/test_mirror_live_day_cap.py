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
import logging
import types

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
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
    day = _flat(ml._SQL_MIRROR_DAY)
    assert day.endswith("/* ml-mirror-day */")
    assert ("SELECT COALESCE(sum(cash_usd), 0)::float8 AS filled, "
            "COALESCE(sum(CASE WHEN state IN ('placing', 'open', 'unknown') "
            "THEN (qty - COALESCE(booked_filled, 0)) * wire ELSE 0 END), 0)::float8 AS open "
            "FROM mirror_orders WHERE side = 'BUY_LONG' "
            "AND placed_at > now() - interval '24 hours'") in day
    assert "state IN ('placing', 'open', 'unknown')" in _flat(ml._SQL_ORDERS_OPEN)
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, wire=0.50, qty=100)
    p.clock = NOW
    assert p._run("fetchrow", ml._SQL_MIRROR_DAY, ()) == {"filled": 0.0, "open": 50.0}
    with pytest.raises(AssertionError, match="CASE clause"):
        p._run("fetchrow", day.replace("state IN ('placing', 'open', 'unknown')", "state = 'open'"), ())
    rep = _flat(ml._SQL_REPLACES)
    assert rep.endswith("/* ml-replaces */")
    assert ("WHERE book_id = $1 AND reason IN ('replace', 'take') AND tif IN ('GTC', 'GTD') "
            "AND done_at > now() - interval '1 hour'") in rep
    assert "take_capped" in ml.CENSUS_KEYS and "replace_capped" in ml.CENSUS_KEYS
    assert BUY == "BUY_LONG" and SLUG        # the fixture's names, as the statements spell them


# ---------------------------------------------------------- 3. the mode line

def _quiet_stats():
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_EXITS, whales=["rn1"], books_live=2, orders_open=1)
    return stats


def _expected_line(stats):
    return "mirror_live mode=%s whales=%s books=%s open=%s day=%s stats=%s" % (
        stats["mode"], stats["whales"], stats["books_live"], stats["orders_open"],
        stats["mirror_day_room"],
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
    assert line.startswith("mirror_live mode=on whales=['rn1'] books=2 open=1 day=412.5 stats={")
    assert "mirror_day_room" in ml._new_stats() and ml._new_stats()["mirror_day_room"] is None
    # the dial: an int with a floor of one; absent, blank or unparseable is 10
    monkeypatch.delenv("MIRROR_MODE_LINE_EVERY_TICKS", raising=False)
    assert ml._mode_line_every_ticks() == 10
    for raw, want in (("25", 25), (" 7 ", 7), ("1", 1), ("0", 1), ("-3", 1),
                      ("", 10), ("abc", 10), ("2.5", 10)):
        monkeypatch.setenv("MIRROR_MODE_LINE_EVERY_TICKS", raw)
        assert ml._mode_line_every_ticks() == want, raw
