"""PNL program lane 6 (E18) -- the adversarial review's pins (2026-09-08).

The review (hard2/PNL_L6_review.md) pinned each finding twice: once AS
IT STOOD on the builder's patch and once as the FIX (a strict xfail).
The FOLD (2026-09-08) landed CRITICAL-1, HIGH-1, MEDIUM-1 and MEDIUM-2:
the four xfails flipped and their markers are gone (the bodies stand,
each docstring carrying the defect it closed); the four as-it-stood
pins of the defects are deleted; MEDIUM-2's as-it-stood pin is
rewritten to the folded behaviour under its name.

  C1  a target that FELL (his witnessed sale) while our entry rest is
      younger than the floor replaces at once (cause 'qty'); only
      GROWTH (plan qty > leaves) waits under the floor with
      `add_pending`. Before the fold the qty branch kept any move,
      growth or shrink, and carried the SMALLER figure -- a rest that
      outlived his exit for up to 45 s.
  H1  _place_reserved: the IOC's paced re-read (an await) precedes the
      room's READ (_room_qty), so E2's "nothing between this read and
      the take" holds. Before the fold it sat between _room_qty and
      _room_take.
  M1  a re-read WITHOUT the side the IOC needs (the ask on a BUY, the
      bid on a SELL) is `ioc_quote_unread`, never `ask_moved` /
      `bid_moved` (a level never read did not move).
  M2  the floor's 1c keep has ONE direction (the operator's reading of
      the mandate, "entries at his cent"): a young rest stands only
      while its wire is at or UNDER his new cent on a BUY (at or OVER
      on a SELL); his cent moving past the rest replaces at once, as
      before the lane.
  L1  the floor never outlives the TTL, whatever the environment says.
  L2  a short book's ADD rest reads the floor exactly as a long's.

Driven against the worker file's fakes (its autouse rails are imported).
"""
import inspect

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e18_rest_life import GTC_TIF, IOC_TIF, _inserts, _take_world
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _UndefinedColumn, _Venue, _armed, _cancels, _census, _fill, _his, _mkt,
    _places, _pool, _run, _tick,
)


def _rest(wire=0.47, qty=300, leaves=None, placed=1000.0, side=rules.BUY, intent=None):
    return rules.OpenOrder(side, wire, qty, qty if leaves is None else leaves, placed, intent)


# ------------------------------------------------------------------ C1

def test_review_l6_c1_rules_a_target_that_fell_replaces_the_young_rest_at_once_FIX():
    """Folded 2026-09-08. Before the fold a 300-share entry rest 20 s old
    with the plan now 200 (his sale of a third) was 'keep' with
    `add_pending` carrying the SMALLER 200 as if it were growth, the rest
    for 300 standing over his proportion until the floor passed (45 s)
    or it filled. Now: 'replace', cause 'qty', at any age; growth still
    waits under the floor."""
    o = _rest(qty=300)
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 200, 0.47, "x"), 1020.0, wire=0.47) == ("replace", {"cause": "qty"})
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 200, 0.48, "x"), 1020.0, wire=0.48)[0] == "replace"
    assert rules.keep_or_replace(o, mi.Plan(rules.BUY, 200, 0.47, "x"), 1020.0, wire=0.47) == "replace"
    # growth still waits under the floor (book 278's 86 -> 316), the rule the lane built
    v, why = rules.rest_decision(_rest(qty=86), mi.Plan(rules.BUY, 316, 0.47, "x"), 1020.0, wire=0.47)
    assert v == "keep" and why["add_pending"]["qty"] == 316
    # a fall inside a share or MIN_MOVE_FRAC is still the plain keep
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 299, 0.47, "x"), 1020.0, wire=0.47) == ("keep", {"cause": "same"})
    # past the floor the fall replaces as it always did
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 200, 0.47, "x"), 1046.0, wire=0.47) == ("replace", {"cause": "qty"})


def test_review_l6_c1_worker_his_witnessed_sale_re_quotes_the_young_rest_down_at_once_FIX():
    """Folded 2026-09-08. His 300 @0.31 then his SELL of 100 (the snapshot
    and the per-market read agree at 200: no drift); our 300-share BUY
    rest 20 s old at 0.30; the plan is BUY 200. Before the fold the rest
    was NOT cancelled (`kept_min_life`, `add_pending` 200, the row open
    for 300 against a target of 200 for the floor's life). Now it is
    cancelled and re-placed at 200 this tick, `replace_qty` on the row,
    as before the patch."""
    p = _pool(fills=_his(300, sold=100), snap={M: 200.0, N: 0.0})
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.30, qty=300, placed_ts=NOW - 20)
    v = _Venue()
    v.rest("oid-1", "BUY", 0.30, 300)
    st = _tick(p, v, http=_mkt(200.0))
    assert b["target"] == 200 and b["drift"] == 0.0, "no drift: the readings agree at 200"
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and [c[2:6] for c in _places(v)] == [(0.30, 200, False, GTC_TIF)]
    assert p.orders[o["id"]]["state"] == "cancelled" and b["last_plan"]["replaced"] == "replace_qty"
    assert _census(st, "kept_min_life") == 0 and "add_pending" not in b["last_plan"]


# ------------------------------------------------------------------ H1

def test_review_l6_h1_the_re_read_precedes_the_rooms_read_FIX():
    """Folded 2026-09-08. Before the fold the order in _place_reserved was
    _room_qty (the room's read) -> `await _ioc_reread` -> _room_take, and
    the builder's pin read "refused before the room" against _room_take
    alone; E2's invariant is on the READ, and an await between the two
    let two games' IOCs both spend the last clip. Now the re-read sits
    before the room's read."""
    src = inspect.getsource(ml._place_reserved)
    assert src.index("held = await _ioc_reread(") < src.index("qty = _room_qty(") < src.index("_room_take(t, est)")
    # the E2 rule the order once broke, in the worker's own words
    assert "nothing between this read and the take" in src and "No await between the read and the reservation" in inspect.getsource(ml._op_slot)


# ------------------------------------------------------------------ M1

def test_review_l6_m1_a_one_sided_re_read_is_named_unread_FIX():
    """Folded 2026-09-08. The re-read hands back a bid and NO ask (a
    one-sided book, a half-failed read). Before the fold: `ask_moved`
    with ask_at_send None -- the name said the ask moved when it was
    never read. Now `ioc_quote_unread`; still no IOC, the rest placed."""
    p, b, v = _take_world([(0.29, 0.30), (0.29, None)])
    st = _tick(p, v)
    assert _census(st, "ioc_quote_unread") == 1 and _census(st, "ask_moved") == 0
    assert "ask_moved" not in b["last_plan"]
    assert [c[2:6] for c in _places(v)] == [(0.29, 300, False, GTC_TIF)] and b["ledger_net"] == 0
    assert len(_inserts(p)) == 1 and _inserts(p)[0][5] == "GTC"
    # a re-read with NEITHER side is unread as before
    p2, b2, v2 = _take_world([(0.29, 0.30), (None, None)])
    st2 = _tick(p2, v2)
    assert _census(st2, "ioc_quote_unread") == 1 and _census(st2, "ask_moved") == 0
    # the ask present and at his cent, the bid absent: a BUY needs the ask alone -- sent
    p3, b3, v3 = _take_world([(0.29, 0.30), (None, 0.30)])
    st3 = _tick(p3, v3)
    assert _census(st3, "ioc_quote_unread") == 0 and _census(st3, "ask_moved") == 0
    assert [c[2:6] for c in _places(v3)] == [(0.30, 300, False, IOC_TIF)] and _inserts(p3)[0][19] == 0.30


# ------------------------------------------------------------------ M2

def test_review_l6_m2_a_one_cent_move_of_his_cent_keeps_the_rest_only_while_it_is_not_past_him_FOLDED():
    """Folded 2026-09-08 as the mandate's reading ("entries at his cent";
    the operator's call on the review's MEDIUM-2). The program's spec
    ("20 s old, cent moved 1c -> keep") named no direction, and the
    builder kept both: his cent moving DOWN from 0.43 to 0.42 left our
    rest at 0.43 -- 1c ABOVE his new cent -- for the floor's life. Now
    the keep holds only while the rest's wire is at or UNDER his new
    cent on a BUY (0.43 -> 0.44: kept, the queue the lane is built to
    keep) and replaces at once, cause 'cent', when his cent moves past
    the rest (0.43 -> 0.42), as before the lane. The mirror image on a
    SELL: at or OVER his new cent keeps."""
    o = _rest(wire=0.43, qty=100)
    down = rules.rest_decision(o, mi.Plan(rules.BUY, 100, 0.42, "x"), 1020.0, wire=0.42)
    up = rules.rest_decision(o, mi.Plan(rules.BUY, 100, 0.44, "x"), 1020.0, wire=0.44)
    assert down == ("replace", {"cause": "cent", "cent_moved": 0.01})
    assert up[0] == "keep" and up[1]["kept_min_life"] is True and up[1]["cent_moved"] == 0.01
    assert rules.keep_or_replace(o, mi.Plan(rules.BUY, 100, 0.42, "x"), 1020.0, wire=0.42) == "replace"
    # the same cent: the plain keep, no direction to read
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 100, 0.43, "x"), 1020.0, wire=0.43) == ("keep", {"cause": "same"})
    # a SELL rest: kept while at or OVER his new cent, replaced when his cent moves over it
    s = _rest(wire=0.63, qty=100, side=rules.SELL)
    assert rules.rest_decision(s, mi.Plan(rules.SELL, 100, 0.62, "x"), 1020.0, wire=0.62)[0] == "keep"
    assert rules.rest_decision(s, mi.Plan(rules.SELL, 100, 0.64, "x"), 1020.0, wire=0.64) \
        == ("replace", {"cause": "cent", "cent_moved": 0.01})
    # growth beside a move past the rest: the cent clause replaces first, nothing waits
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 300, 0.42, "x"), 1020.0, wire=0.42)[0] == "replace"
    # past the floor both directions re-quote, as before
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 100, 0.42, "x"), 1046.0, wire=0.42)[0] == "replace"
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 100, 0.44, "x"), 1046.0, wire=0.44)[0] == "replace"


# -------------------------------------------------------------- L1, L2

def test_review_l6_l1_the_floor_never_outlives_the_ttl_whatever_the_env_says():
    """min_wait_env has no upper bound, so an operator could set the
    floor to 1e9: the TTL clause decides first, and the caller's ttl_s
    can only tighten it, so the effective floor is min(floor, TTL)."""
    p = mi.Plan(rules.BUY, 100, 0.48, "x")
    o = _rest(qty=100)
    assert rules.rest_decision(o, p, 1031.0, wire=0.48, ttl_s=30.0, min_life_s=1e9) == ("replace", {"cause": "ttl", "rest_age_s": 31.0})
    assert rules.rest_decision(o, p, 1029.0, wire=0.48, ttl_s=30.0, min_life_s=1e9)[0] == "keep"
    assert rules.rest_decision(o, p, 1000.0 + float(rules.MIRROR_REST_TTL_S), wire=0.48, min_life_s=1e9)[0] == "replace"
    assert rules.rest_decision(o, p, 1000.0 + float(rules.MIRROR_REST_TTL_S), wire=0.48, ttl_s=1e9, min_life_s=1e9)[0] == "replace", \
        "ttl_s can only tighten the TTL"


def test_review_l6_l2_a_short_books_add_rest_reads_the_floor_like_a_longs_and_its_cover_never_does():
    """A short book's ADD is a SELL plan on a BUY_SHORT wire (its cent in
    contract space): the same young-rest rule, in the SELL's direction
    (kept while the rest is at or OVER his new cent, folded 2026-09-08);
    its COVER is the worker's `entry=False` (a reduce) and never waits."""
    o = _rest(wire=0.63, qty=100, side=rules.SELL, intent="ORDER_INTENT_BUY_SHORT")
    v, why = rules.rest_decision(o, mi.Plan(rules.SELL, 100, 0.62, "x"), 1020.0, wire=0.62,
                                 intent="ORDER_INTENT_BUY_SHORT")
    assert v == "keep" and why["kept_min_life"] is True
    assert rules.rest_decision(o, mi.Plan(rules.SELL, 100, 0.62, "x"), 1020.0, wire=0.62,
                               intent="ORDER_INTENT_BUY_SHORT", entry=False) == ("replace", {"cause": "cent", "cent_moved": 0.01})
    # his cent moving OVER the short's rest replaces at once (the SELL's direction)
    assert rules.rest_decision(o, mi.Plan(rules.SELL, 100, 0.64, "x"), 1020.0, wire=0.64,
                               intent="ORDER_INTENT_BUY_SHORT") == ("replace", {"cause": "cent", "cent_moved": 0.01})
    # the side/intent change replaces whatever the age
    assert rules.rest_decision(o, mi.Plan(rules.SELL, 100, 0.63, "x"), 1020.0, wire=0.63,
                               intent="ORDER_INTENT_SELL_SHORT") == ("replace", {"cause": "intent"})
