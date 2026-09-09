"""E24 (FILL lane 24) -- the adversarial review's pins, on the lane's own
fixtures (tests/test_e24_hand_fills.py): the hand reduce under a standing
order of ours, the hand fill dated inside the tick, the adoption's price
by time order, and the explained book judged on the unrounded hand."""
from __future__ import annotations

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e24_hand_fills import (  # noqa: F401 -- the autouse rails ride along
    AT, HAND_SINCE, NEW_NAMES, SLUG, _book_1156, _census, _hand, _hand_of, _hand_reads, _long_pool, _mkt,
    _places, _reads, _record, _short_fill, _short_pool, _shorts_on, _tick, _venue,
)
from tests.test_mirror_live_worker import NOW, SELL, _NoClose, _armed  # noqa: F401 -- the autouse rails


def _sale(oid, qty, px, ts=AT):
    return _hand(oid, "SELL", "SELL_LONG", qty, px, ts=ts, order_qty=qty, order_px=px, tif="IMMEDIATE_OR_CANCEL")


def test_rev_h1_a_hand_reduce_under_a_standing_order_of_ours_cancels_the_order_and_adopts_next_tick():
    """HIGH-1. Long 1,000 at 0.50 with OUR exit rest SELL 600 @0.56 standing
    (GTC, filled 0); the desk sells 600 by hand @0.55 (the walk reads 400).
    Adopted under the standing rest, the ledger falls to 400 while the
    rest -- sized on 1,000 -- still stands; its fill of 600 on the next
    tick is a SALE 200 past the ledger: _book_fill's overfill, the freeze
    and the desk trip (the 20:57:19Z class). The rule: with an order of
    ours in flight the hand reduce is NOT adopted -- the order is cancelled
    under the hand's name, nothing is planned on that tick, and the log is
    read again on the next fresh walk with nothing standing."""
    p = _long_pool()
    b = p.add_book(ledger=1000, avg_cost=0.50, ratio=0.1, target=2876)
    o = p.add_order(b, side=SELL, wire=0.56, qty=600, order_id="oid-rest", state="open", kind="reduce")
    b["open_order_id"] = o["id"]
    v = _NoClose(held={SLUG: 400}, bid=0.55, ask=0.56, trades=[_sale("H-1", 600.0, 0.55)])
    v.rest("oid-rest", "SELL", 0.56, 600)
    st = _tick(p, v, http=_mkt(28765.0))
    # nothing adopted under the standing order: the ledger stands, the rest is cancelled, nothing placed
    assert _census(st, "hand_adopted") == 0 and b["ledger_net"] == 1000 and _record(p, b) is None
    assert p.orders[o["id"]]["state"] == "cancelled" and ("cancel", "oid-rest", SLUG) in v.calls
    assert not _places(v) and b["state"] == "live"
    assert b["last_plan"]["kind"] == "hand_order_open" and b["last_plan"]["hand_order_open"]["reduces"] == -600.0
    assert "hand" not in b["last_plan"] and b["id"] not in ml._hand_read_at, "no memo: the next fresh walk reads again"
    # the next tick: nothing standing -> the hand's 600 adopted at 0.55, the ledger 400 == the venue 400
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(28765.0))
    assert _census(st2, "hand_adopted") == 1 and b["ledger_net"] == 400 and len(_hand_reads(v)) == 2
    assert _record(p, b)["adopted"] == {"H-1": 600.0} and b["realized_pnl"] == pytest.approx((0.55 - 0.50) * 600, abs=1e-4)
    assert p.state.get("mirror_live") is True, "the desk is never tripped"
    # and had the rest filled between the ticks it is booked on the ledger it was sized on, never past it:
    # a third tick with the venue at 400 agreeing plans live on 400
    st3 = _tick(p, v, now=NOW + 60, http=_mkt(28765.0))
    assert b["state"] == "live" and _census(st3, "overfill") == 0 and p.state.get("mirror_live") is True


def test_rev_h1_the_trip_the_rule_prevents_a_standing_rest_filling_past_the_adopted_ledger():
    """The shape HIGH-1 guards against, driven through the ledger the
    adoption would leave: a SELL of 600 booked on a ledger of 400 is the
    overfill (freeze, trip) -- the primitive's own rule, pinned so the
    guard's reason is in the file."""
    p = _long_pool()
    b = p.add_book(ledger=400, avg_cost=0.50, ratio=0.1, target=2876)
    o = p.add_order(b, side=SELL, wire=0.56, qty=600, order_id="oid-rest", state="open", kind="reduce")
    b["open_order_id"] = o["id"]
    v = _NoClose(held={SLUG: -200}, bid=0.55, ask=0.56, fills={"oid-rest": (600.0, 0.56)})
    v.rest("oid-rest", "SELL", 0.56, 600)
    st = _tick(p, v, http=_mkt(28765.0))
    assert _census(st, "overfill") == 1 and b["state"] == "frozen" and b["frozen_reason"] == "overfill"
    assert p.state.get("mirror_live") is False, "a sale past the ledger trips the desk"


def test_rev_h1_a_hand_reduce_under_a_nonterminal_order_adopts_nothing_and_reads_again(monkeypatch):
    """An order of ours the venue's status read cannot answer ('unknown',
    t.nonterminal): nothing to cancel, nothing adopted, no memo -- read
    again next tick."""
    _shorts_on(monkeypatch)
    p = _long_pool()
    b = p.add_book(ledger=1000, avg_cost=0.50, ratio=0.1, target=2876)
    o = p.add_order(b, side=SELL, wire=0.56, qty=600, order_id="oid-unk", state="open", kind="reduce")
    b["open_order_id"] = o["id"]
    v = _NoClose(held={SLUG: 400}, bid=0.55, ask=0.56, trades=[_sale("H-1", 600.0, 0.55)], status_none=True)
    st = _tick(p, v, http=_mkt(28765.0))
    assert _census(st, "hand_adopted") == 0 and b["ledger_net"] == 1000 and _record(p, b) is None
    assert b["id"] not in ml._hand_read_at


def test_rev_h2_a_hand_fill_the_venue_dates_inside_the_tick_is_counted_not_held_ambiguous(monkeypatch):
    """HIGH-2. The tick's clock is its START; the walk (step R) runs
    seconds later and holds a hand fill the venue dates after t.now. Bound
    at t.now the fill is excluded, the reading is `hand_ambiguous`, the
    memo holds it 300 s on that residual, the second read freezes the
    book and the frozen exit sizes on the venue WITH the hand's shares --
    the 1156 fight for one wait. The gap check refuses what the walk does
    not hold; the clock bound adds nothing but this hole."""
    _shorts_on(monkeypatch)
    p = _short_pool()
    b = _book_1156(p)
    v = _venue(-2049, trades=[_short_fill(ts=NOW + 3.0)])       # dated 3 s after the tick's clock, inside the walk
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "hand_explained") == 1 and _census(st, "hand_ambiguous") == 0
    assert b["state"] == "live" and _census(st, "venue_ledger_suspect") == 0 and _hand_of(b)["adds"] == -1802.0
    # a fill the walk does NOT hold (the venue still reads the ledger) is refused by the gap check as before
    ml._hand_read_at.clear()
    p2 = _short_pool()
    b2 = _book_1156(p2)
    v2 = _venue(-1247, trades=[_short_fill(ts=NOW + 3.0)])      # the venue holds 1,000 the log does not name
    st2 = _tick(p2, v2, http=_mkt(100.0, 400.0))
    assert _census(st2, "hand_ambiguous") == 1 and _census(st2, "hand_explained") == 0 and b2["ledger_net"] == -247


def test_rev_m12_the_adoption_prices_the_earliest_opposite_fills_first():
    """The surviving mutant M12: the walk's time order is the price. Long
    100 at 0.30; the desk sells 60 @0.50 first and 60 @0.40 ten seconds
    later (the venue -20). The book's 100 are the FIRST 100 sold -- 60
    @0.50 + 40 @0.40 -> 0.46 -- never the venue's newest-first 60 @0.40 +
    40 @0.50 -> 0.44; the last 20 are the desk's own."""
    p = _long_pool()
    b = p.add_book(ledger=100, avg_cost=0.30, ratio=0.1, target=2876)
    v = _NoClose(held={SLUG: -20}, bid=0.60, ask=0.61,
                 trades=[_sale("H-2", 60.0, 0.40, ts=AT + 10), _sale("H-1", 60.0, 0.50, ts=AT)])   # the log's newest-first order
    st = _tick(p, v, http=_mkt(28765.0))
    assert _census(st, "hand_adopted") == 1 and b["ledger_net"] == 0
    h = _hand_of(b)
    assert h["px"] == pytest.approx(0.46) and h["adds"] == -20.0 and h["reduces"] == -100.0 and h["adopted"] == 100.0
    assert b["realized_pnl"] == pytest.approx((0.46 - 0.30) * 100, abs=1e-4)
    assert _record(p, b)["adopted"] == {"H-1": 60.0, "H-2": 40.0}


def test_rev_l1_an_explained_book_is_judged_on_the_unrounded_hand(monkeypatch):
    """LOW. The explanation is judged on the float `gap`, the tick's
    `delta` on the ROUNDED hand_adds: a hand add of 1,801.5 beside a
    fractional manual row can leave |delta| past the tolerance on a book
    the hand explained (gap 0.7, delta 1.2) and E16's suspect fires on
    it. The explained book's delta is the gap it was explained on."""
    _shorts_on(monkeypatch)
    p = _short_pool()
    p.manual_shares[SLUG] = -0.2
    b = _book_1156(p)
    # gap = -2048 + 247 + 0.2 + 1801.5 = 0.7 (explained); the rounded hand_adds -1802 would read delta 1.2
    v = _venue(-2048, trades=[_hand("CCZ08XN74SX4", "SELL", "BUY_SHORT", 1801.5, 0.46, order_qty=1801.5, order_px=0.36)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "hand_explained") == 1 and _hand_of(b)["verdict"] == "explained"
    assert _census(st, "venue_ledger_suspect") == 0 and "venue_ledger_suspect" not in b["last_plan"]
    assert b["state"] == "live" and b["ledger_net"] == -247
