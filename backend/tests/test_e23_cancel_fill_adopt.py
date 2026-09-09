"""E23 (2026-09-09, FILL lane 23): a cancelled rest whose venue fills
outran the cancel is booked -- the cancel re-reads the row's FINAL filled
figure (part A, prevention), and a book frozen venue_ledger_disagree whose
surplus is exactly the unbooked fills of its own cancelled rows, named by
order id in the venue's trade log, adopts them (part B, cure) and is left
to the existing thaw.

The rows (hard2/book_986_1426.txt): book 986 aec-atp-ugobla-danrin-2026-09-09,
ORDER_INTENT_BUY_LONG, ratio 0.1, opened 11:15:29, ledger 413, venue 1215,
target 2876, his_net 28764.9, avg_cost 0.56, standing row 412015 filled
411.95 @0.5600 (lines 998, 1019; the whole-share ledger column 413 carries
+1.05 of rounding over the standing row -- the review's HIGH-1, pinned in
test_e23_review_pins.py); its twelve order rows (lines 1003-1014): 5823
increase BUY_LONG GTC 2937 @0.56 cancelled `cancel_pending` filled 6.13,
5908 2874 @0.56 cancelled `ttl` filled 405.82, then TEN rests of 2467 @0.56
-- 5966, 5998, 6056, 6126, 6180, 6218, 6250 (`snapshot_stale`), 6263, 6292,
6326 (`loss_breaker`) -- every one cancelled with venue_state `canceled`,
filled 0 and avg_px NULL, placed 11:45:17 .. 13:16:00, done 11:55:31 ..
13:22:18. The mirror-frozen read (hard2/frozen_1423.txt line 998): frozen
venue_ledger_disagree 14:03:49, read_at 14:23:07, ledger 413, venue 1215,
manual 0, registered 0, frozen_exit {held: frozen_no_his_exit, target:
2876, venue_own: 1215}: 802 shares on our side at our cent the ledger never
booked, and E16's thaw can never fire.

The fixture world is the worker suite's LONG world on the E5 pool: his
28,765 of the long token at 0.56 (ratio 0.1 -> target 2,876), the book
frozen venue_ledger_disagree with ledger 413 at 0.56 and the twelve
cancelled rows above (ids in the fake's own sequence; the venue's order
ids `oid-5823` .. `oid-6326`), the venue holding 1,215 and its trade log
naming fills by those ids.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import pathlib
import re

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e5_frozen_exits import _E5Pool, _plan_exit
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, M, N, NOW, SELL, SLUG, _NoClose, _Venue, _armed, _census, _his, _mkt, _places,
    _ratio_fills, _tick,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
PART_A = ("cancel_fill_late", "cancel_fill_unread")
PART_B = ("disagree_fill_adopted", "disagree_fill_unread", "disagree_fill_unexplained",
          "disagree_fill_ambiguous")
NEW_NAMES = PART_A + PART_B
WIRE = 0.56
HIS = 28765.0                        # his_net 28764.9 (frozen_1423 line 998), whole
LEDGER = 413                         # the ledger column (book_986_1426 line 998)
VENUE = 1215                         # the venue column (frozen_1423 line 998)
SURPLUS = VENUE - LEDGER             # 802 (the column's; the standing row's is 803.05)
REST_QTY = 2467                      # the ten rests' quantity (book_986_1426 lines 1005-1014)
# the twelve rows in 986's own order: (venue id, qty, booked, reason), placed a
# minute apart from 11:15:29 on and done ten minutes after each placement
ROWS_986 = (("oid-5823", 2937, 6.13, "cancel_pending"), ("oid-5908", 2874, 405.82, "ttl"),
            ("oid-5966", REST_QTY, 0.0, "ttl"), ("oid-5998", REST_QTY, 0.0, "ttl"),
            ("oid-6056", REST_QTY, 0.0, "ttl"), ("oid-6126", REST_QTY, 0.0, "ttl"),
            ("oid-6180", REST_QTY, 0.0, "ttl"), ("oid-6218", REST_QTY, 0.0, "ttl"),
            ("oid-6250", REST_QTY, 0.0, "snapshot_stale"), ("oid-6263", REST_QTY, 0.0, "ttl"),
            ("oid-6292", REST_QTY, 0.0, "ttl"), ("oid-6326", REST_QTY, 0.0, "loss_breaker"))
FIRST_PLACED = NOW - 7200.0          # 5823's placement, two hours before the tick
FILLED_AT = NOW - 900.0              # the fills the log names, inside [FIRST_PLACED - 30, NOW]
# the functions the brief names as NOT touched, hashed on 67fbaf3 (the tip
# this lane was built on): a change to any of them is not this lane's
UNTOUCHED = {
    "_thaw": "62950633c3de6c96", "_thaw_verdict": "c23d51c1a5a7c1f4", "_thaw_agrees": "780a13557ddcfbce",
    "_reconcile_placing": "76ab1b2931ee0f33", "_mark_lost": "30a13c986990f19c",
    "_frozen_exit": "ef478fabdfa2ccc0", "_registered_shares": "bd2ccd54d6f79525",
}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _pool(**kw):
    """His 28,765 of the long token at 0.56 (the fixture's long world)."""
    kw.setdefault("fills", _his(HIS, WIRE))
    kw.setdefault("snap", {M: HIS, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _E5Pool(**kw)


def _book_986(p, ledger=LEDGER, rows=ROWS_986, state="frozen", reason="venue_ledger_disagree", **over):
    """Book 986's shape: a frozen venue_ledger_disagree LONG of `ledger`
    at 0.56 (ratio 0.1) with its twelve cancelled rows, each with the
    venue's order id, booked below its quantity."""
    b = p.add_book(ledger=ledger, avg_cost=WIRE, ratio=0.1, target=2876, state=state,
                   frozen_reason=(reason if state == "frozen" else None),
                   frozen_ts=(NOW - 1200 if state == "frozen" else None), **over)
    out = []
    for i, (oid, qty, booked, why) in enumerate(rows):
        placed = FIRST_PLACED + 60.0 * i
        out.append(p.add_order(b, side=BUY, wire=WIRE, qty=qty, order_id=oid, state="cancelled",
                               placed_ts=placed, kind="increase", booked=booked,
                               avg_px=(WIRE if booked else None), venue_state="canceled",
                               reason=why, done_at=placed + 600.0))
    return b, out


def _row(rows, oid):
    return next(o for o in rows if o["order_id"] == oid)


def _log_fill(oid="oid-6292", qty=float(SURPLUS), px=WIRE, ts=FILLED_AT, side="BUY",
              order_qty=float(REST_QTY), order_price=WIRE):
    """One fill of the venue's trade log as pmus.recent_trades hands it:
    a BUY of the long token under one of our order ids."""
    return {"qty": qty, "price": px, "side": side, "ts": ts, "realized_pnl": 0.0,
            "order_id": oid, "order_qty": order_qty, "order_price": order_price}


def _booked_log():
    """The log naming 5823's 6.13 and 5908's 405.82 -- fills the rows
    already booked -- beside 6292's 802."""
    return [_log_fill("oid-5823", 6.13, ts=FIRST_PLACED + 100, order_qty=2937.0),
            _log_fill("oid-5908", 405.82, ts=FIRST_PLACED + 200, order_qty=2874.0),
            _log_fill("oid-6292", float(SURPLUS))]


def _venue(held=VENUE, trades=None, **kw):
    return _NoClose(held={SLUG: held}, bid=0.60, ask=0.61, trades=trades, **kw)


def _trades_reads(v):
    """E23's OWN trade-log reads (since the earliest cancelled row's
    placement - the skew). E24 (FILL lane 24) reads the log for the desk's
    hand FIRST on every disagreement past the tolerance, over [the book's
    open - the skew, now]: those reads are _hand_reads, one per wait on the
    same residual, and never this lane's."""
    return [c for c in v.calls if c[0] == "trades" and c[2] != HAND_SINCE]


HAND_SINCE = NOW - 600 - le._ORPHAN_SKEW_S       # the fixture book opened NOW - 600 (_Pool._book_dict)


def _hand_reads(v):
    return [c for c in v.calls if c[0] == "trades" and c[2] == HAND_SINCE]


def _statuses(v):
    return [c for c in v.calls if c[0] == "status"]


def _dis(b):
    return (b.get("last_plan") or {}).get("disagree_fill")


def _recent(what, book=None):
    return [x for x in ml._RECENT if x["what"] == what and (book is None or x["book"] == book)]


class _LateVenue(_NoClose):
    """The fixture venue with the ORDER STATUS scripted per read: `script`
    maps an order id to a list of readings, one per status call in
    order -- (filled, avg) to answer with, or an Exception to raise --
    and the last reading stands once the list is spent. What the
    cancel's terminal read carries vs what the final read carries is
    the whole of part A."""

    def __init__(self, *a, script=None, **kw):
        super().__init__(*a, **kw)
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.seen = {}

    def order_status(self, oid):
        self.calls.append(("status", oid))
        steps = self.script.get(oid)
        if steps:
            n = self.seen.get(oid, 0)
            step = steps[min(n, len(steps) - 1)]
            self.seen[oid] = n + 1
            if isinstance(step, Exception):
                raise step
            f, avg = step
            o = self.orders[oid]
            o["filled_shares"], o["avg_px"] = f, avg
            return self._norm(o)
        return super().order_status(oid)


# ------------------------------------------- (1) part B: book 986's shape


def test_e23_book_986_the_cancelled_rows_unbooked_fills_are_adopted_and_the_existing_thaw_takes_the_book():
    """Ledger 413, venue 1215, ten cancelled 2467 rows with filled 0; the
    log naming order 6292's id with 802 @0.56 (beside 5823's 6.13 and
    5908's 405.82, already booked) -> the row filled 802 at avg 0.56,
    ledger 1215, the standing row 1215, disagree_fill_adopted 1, the book
    still frozen this tick (the frozen exit held `frozen_fill_this_tick`,
    E5 review F1 / F2) and NEVER thawed by this lane: the EXISTING rule
    for venue_ledger_disagree is E16's two agreeing fresh reads --
    `one_read` on the next tick, `thawed` (venue_agrees) on the one
    after -- then the book plans live toward 2,876."""
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=_booked_log())
    st = _tick(p, v, http=_mkt(HIS))
    o = _row(rows, "oid-6292")
    assert (o["state"], o["booked_filled"], o["filled"], o["avg_px"]) == ("cancelled", 802.0, 802.0, 0.56)
    assert o["reason"] == "adopted from the trade log after the cancel"
    assert b["ledger_net"] == VENUE and b["avg_cost"] == 0.56 and b["state"] == "frozen"
    assert b["frozen_reason"] == "venue_ledger_disagree"
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 1215.0
    assert [a["order_id"] for a in p.rows[b["standing_row_id"]]["raw"]["adds"]] == ["oid-6292"]
    assert _census(st, "disagree_fill_adopted") == 1 and all(_census(st, k) == 0 for k in NEW_NAMES if k != "disagree_fill_adopted")
    assert _census(st, "thaw_held") == 0 and not _places(v)
    # the rows the log named as already booked stand as they were
    assert _row(rows, "oid-5823")["booked_filled"] == 6.13 and _row(rows, "oid-5908")["booked_filled"] == 405.82
    assert all(_row(rows, oid)["booked_filled"] == 0.0 for oid, _q, bk, _w in ROWS_986 if bk == 0.0 and oid != "oid-6292")
    ids = [x["id"] for x in rows]
    assert _dis(b) == {"rows": ids, "shares": 802.0, "delta": SURPLUS, "verdict": "adopted", "at": NOW}
    assert b["last_plan"]["disagree_fill_at"] == NOW and b["last_plan"]["kind"] == "frozen"
    assert _plan_exit(b)["held"] == "frozen_fill_this_tick"
    assert len(_trades_reads(v)) == 1 and _trades_reads(v)[0][1:] == (SLUG, FIRST_PLACED - le._ORPHAN_SKEW_S)
    assert len(_hand_reads(v)) == 1 and v.calls.index(_hand_reads(v)[0]) < v.calls.index(_trades_reads(v)[0])   # E24: the hand first
    rec = _recent("disagree_fill_adopted", b["id"])
    assert len(rec) == 1 and rec[0]["order_row"] == o["id"] and rec[0]["order"] == "oid-6292"
    assert rec[0]["shares"] == 802.0 and rec[0]["px"] == 0.56
    assert ("disagree_fill_adopted", "rn1") in {tuple(k.split("|")) for k in ml.mirror_census_snapshot()}
    # the next tick: venue 1215 == ledger 1215, nothing open, no register -> the EXISTING rule
    # (E16: a held venue_ledger_disagree book thaws on its SECOND consecutive fresh agreeing read)
    v2 = _venue(trades=[])
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(HIS))
    assert b["state"] == "frozen" and _census(st2, "thaw_held") == 1 and b["last_plan"]["thaw_held"] == "one_read"
    assert not _trades_reads(v2) and all(_census(st2, k) == 0 for k in NEW_NAMES) and b["ledger_net"] == VENUE
    v3 = _venue(trades=[])
    st3 = _tick(p, v3, now=NOW + 60, http=_mkt(HIS))
    assert b["state"] == "live" and b["frozen_reason"] is None and _census(st3, "thaw_held") == 0
    assert [x["what"] for x in ml._RECENT if x["book"] == b["id"] and x["what"] == "thawed"] == ["thawed"]
    assert b["last_plan"]["thawed_venue_agrees"] is True and b["last_plan"]["kind"] != "frozen"
    assert b["target"] == 2876 and b["ledger_net"] == VENUE and not _trades_reads(v3)
    # planning as a live book: the plan's increase toward 2,876 is 1,661 = 2,876 - 1,215 at his cent;
    # the placement is clipped by the fixture world's rolling day room (rules.room_scale on
    # t.mirror_day, the existing rail, untouched: 800.88 / 0.56 -> 1,430), never widened
    assert b["last_plan"]["qty"] == 2876 - VENUE and b["last_plan"]["price"] == WIRE and b["last_plan"]["kind"] == "increase"
    pl = _places(v3)
    assert len(pl) == 1 and pl[0][1:3] == (SLUG, WIRE) and pl[0][4] is False
    assert pl[0][3] == int(st3["mirror_day_room"] / WIRE) == 1430 and pl[0][3] < 2876 - VENUE


def test_e23_two_ids_summing_to_the_surplus_adopt_both_rows():
    """6263's id with 300 and 6292's with 502: each id's fills are its
    own row's -- both booked, 802 in all; not ambiguous."""
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-6263", 300.0), _log_fill("oid-6292", 502.0, ts=FILLED_AT + 5)])
    st = _tick(p, v, http=_mkt(HIS))
    assert _row(rows, "oid-6263")["booked_filled"] == 300.0 and _row(rows, "oid-6292")["booked_filled"] == 502.0
    assert all(_row(rows, oid)["reason"] == "adopted from the trade log after the cancel" for oid in ("oid-6263", "oid-6292"))
    assert b["ledger_net"] == VENUE and _census(st, "disagree_fill_adopted") == 1 and _census(st, "disagree_fill_ambiguous") == 0
    assert _dis(b)["verdict"] == "adopted" and _dis(b)["shares"] == 802.0
    assert len(_recent("disagree_fill_adopted", b["id"])) == 2
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 1215.0


def test_e23_the_fills_are_booked_at_the_logs_prices_never_the_wire():
    """A rest fills at or under its cent: 500 @0.56 and 302 @0.55 on one
    id -> the row's 802 at the fill-weighted 0.556234, the book's
    avg_cost moved by the booking, never the wire."""
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-6292", 500.0, px=0.56), _log_fill("oid-6292", 302.0, px=0.55, ts=FILLED_AT + 3)])
    st = _tick(p, v, http=_mkt(HIS))
    o = _row(rows, "oid-6292")
    px = round((500.0 * 0.56 + 302.0 * 0.55) / 802.0, 6)
    assert o["booked_filled"] == 802.0 and o["avg_px"] == px and px == 0.556234
    assert _census(st, "disagree_fill_adopted") == 1 and b["ledger_net"] == VENUE
    assert b["avg_cost"] == pytest.approx((413 * 0.56 + 802 * px) / 1215, abs=1e-6)


# --------------------------------------- (2) (3) (4) (5): the log's holds


def test_e23_the_log_naming_500_is_unexplained_and_books_nothing():
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-6292", 500.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert all(o["booked_filled"] == bk for o, (_i, _q, bk, _w) in zip(rows, ROWS_986))
    assert b["ledger_net"] == LEDGER and b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert _census(st, "disagree_fill_unexplained") == 1 and _census(st, "disagree_fill_adopted") == 0
    assert _dis(b) == {"rows": [x["id"] for x in rows], "shares": 500.0, "delta": SURPLUS, "verdict": "unexplained", "at": NOW}
    assert len(_trades_reads(v)) == 1 and _plan_exit(b)["held"] == "frozen_no_his_exit"
    assert all(o["reason"] in ("ttl", "cancel_pending", "snapshot_stale", "loss_breaker") for o in rows)


def test_e23_the_log_unreadable_is_unread_and_books_nothing():
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades_raise=True)
    st = _tick(p, v, http=_mkt(HIS))
    assert b["ledger_net"] == LEDGER and b["state"] == "frozen" and all(o["state"] == "cancelled" for o in rows)
    assert _census(st, "disagree_fill_unread") == 1 and _census(st, "disagree_fill_adopted") == 0
    assert _dis(b)["verdict"] == "unread" and _dis(b)["delta"] == SURPLUS and b["last_plan"]["disagree_fill_at"] == NOW
    assert len(_trades_reads(v)) == 1 and _plan_exit(b)["held"] == "frozen_no_his_exit"


def test_e23_one_id_summing_to_900_is_ambiguous():
    """The log names MORE than the venue holds: the two readings
    disagree, nothing booked."""
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-6292", 900.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert b["ledger_net"] == LEDGER and _row(rows, "oid-6292")["booked_filled"] == 0.0
    assert _census(st, "disagree_fill_ambiguous") == 1 and _census(st, "disagree_fill_adopted") == 0
    assert _dis(b)["verdict"] == "ambiguous" and _dis(b)["shares"] == 900.0
    # the log under what a row booked (5908 booked 405.82, the log says 100): the readings of one id disagree
    ml._disagree_fill_read_at.clear()                  # book ids repeat across this file's pools
    p2 = _pool()
    b2, rows2 = _book_986(p2)
    v2 = _venue(trades=[_log_fill("oid-5908", 100.0, order_qty=2874.0), _log_fill("oid-6292", 802.0)])
    st2 = _tick(p2, v2, http=_mkt(HIS))
    assert _census(st2, "disagree_fill_ambiguous") == 1 and b2["ledger_net"] == LEDGER
    assert _row(rows2, "oid-6292")["booked_filled"] == 0.0
    # our id on the OTHER side, or a fill of ours the log does not price: the same hold
    ml._disagree_fill_read_at.clear()
    p3 = _pool()
    b3, rows3 = _book_986(p3)
    v3 = _venue(trades=[_log_fill("oid-6292", 802.0, side="SELL")])
    st3 = _tick(p3, v3, http=_mkt(HIS))
    assert _census(st3, "disagree_fill_ambiguous") == 1 and b3["ledger_net"] == LEDGER
    ml._disagree_fill_read_at.clear()
    p4 = _pool()
    b4, rows4 = _book_986(p4)
    v4 = _venue(trades=[_log_fill("oid-6292", 802.0, px=None)])
    st4 = _tick(p4, v4, http=_mkt(HIS))
    assert _census(st4, "disagree_fill_ambiguous") == 1 and b4["ledger_net"] == LEDGER


def test_e23_the_log_naming_an_id_that_is_not_ours_is_ignored_never_booked():
    """A foreign id with 802 beside ours: ours adopted, the foreign one
    ignored; the foreign one alone: unexplained (the log names nothing
    for our ids), one warning line."""
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-not-ours", 802.0), _log_fill("oid-6292", 802.0, ts=FILLED_AT + 1)])
    st = _tick(p, v, http=_mkt(HIS))
    assert _census(st, "disagree_fill_adopted") == 1 and b["ledger_net"] == VENUE
    assert _row(rows, "oid-6292")["booked_filled"] == 802.0
    ml._disagree_fill_read_at.clear()                  # book ids repeat across this file's pools
    p2 = _pool()
    b2, rows2 = _book_986(p2)
    v2 = _venue(trades=[_log_fill("oid-not-ours", 802.0)])
    st2 = _tick(p2, v2, http=_mkt(HIS))
    assert _census(st2, "disagree_fill_unexplained") == 1 and b2["ledger_net"] == LEDGER
    assert all(o["booked_filled"] == bk for o, (_i, _q, bk, _w) in zip(rows2, ROWS_986))
    assert _dis(b2)["verdict"] == "unexplained" and _dis(b2)["shares"] == 0.0


# ------------------------------- (6) (7) (8): the surplus that is not the rows'


def test_e23_manual_shares_on_the_slug_are_unexplained_and_read_no_log():
    p = _pool()
    p.manual_shares[SLUG] = 10.0
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-6292", 802.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert not _trades_reads(v) and len(_hand_reads(v)) == 1 and b["ledger_net"] == LEDGER and b["state"] == "frozen"
    assert _census(st, "disagree_fill_unexplained") == 1 and _census(st, "disagree_fill_adopted") == 0
    assert _dis(b) == {"rows": [x["id"] for x in rows], "shares": 0.0, "delta": SURPLUS, "verdict": "unexplained", "at": NOW}
    assert "disagree_fill_at" not in b["last_plan"]


def test_e23_the_surplus_on_the_other_token_or_past_the_rows_room_is_unexplained_with_no_read():
    """The venue at 100 against the ledger 413: the surplus is on the
    other token (negative on a long book) -> unexplained, no read. The
    venue at 40,000: more than the twelve rows' unfilled room (2,930.87
    + 2,468.18 + 10 x 2,467 = 30,069.05) could have filled -> the same,
    no read."""
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(held=100, trades=[_log_fill("oid-6292", 802.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert not _trades_reads(v) and len(_hand_reads(v)) == 1 and _census(st, "disagree_fill_unexplained") == 1
    assert _dis(b)["delta"] == 100 - LEDGER and _dis(b)["verdict"] == "unexplained" and b["ledger_net"] == LEDGER
    p2 = _pool()
    b2, rows2 = _book_986(p2)
    v2 = _venue(held=40000, trades=[_log_fill("oid-6292", 802.0)])
    st2 = _tick(p2, v2, http=_mkt(HIS))
    assert not _trades_reads(v2) and len(_hand_reads(v2)) == 1 and _census(st2, "disagree_fill_unexplained") == 1 and b2["ledger_net"] == LEDGER
    assert _dis(b2)["delta"] == 40000 - LEDGER


def test_e23_registered_shares_reduce_the_surplus_and_a_full_register_never_reaches_this_lane():
    """E5's register explaining the whole 802: venue == ledger +
    registered, the freeze's branch not entered, the registered book
    (never thaws, F3), nothing of the lane's. A register of 400: the
    surplus 402 sits inside the rows' room, the log's 802 for 6292 is
    more than it -> ambiguous, nothing booked."""
    p = _pool(registered={SLUG: 802.0})
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-6292", 802.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert not _trades_reads(v) and _census(st, "registered_books") == 1
    assert all(_census(st, k) == 0 for k in NEW_NAMES) and "disagree_fill" not in b["last_plan"]
    assert b["ledger_net"] == LEDGER and b["last_plan"]["registered_frozen"] is True
    p2 = _pool(registered={SLUG: 400.0})
    b2, rows2 = _book_986(p2)
    v2 = _venue(trades=[_log_fill("oid-6292", 802.0)])
    st2 = _tick(p2, v2, http=_mkt(HIS))
    assert len(_trades_reads(v2)) == 1 and len(_hand_reads(v2)) == 1 and _census(st2, "disagree_fill_ambiguous") == 1
    assert _dis(b2)["delta"] == 402 and b2["ledger_net"] == LEDGER


def test_e23_a_cancelled_row_booked_to_its_quantity_is_not_a_candidate_and_a_book_without_candidates_reads_nothing():
    """Every row booked whole: the statement's predicate (and the fake's,
    and the worker's own re-read of it) leaves no candidate -> nothing
    read, nothing counted, nothing on the plan (E5's register is the
    road). One candidate left (6292): its 802 is adopted alone."""
    full = tuple((oid, qty, float(qty), why) for oid, qty, _bk, why in ROWS_986)
    p = _pool()
    b, rows = _book_986(p, rows=full)
    v = _venue(trades=[_log_fill("oid-6292", 802.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert not _trades_reads(v) and len(_hand_reads(v)) == 1 and all(_census(st, k) == 0 for k in NEW_NAMES)
    assert "disagree_fill" not in b["last_plan"] and b["ledger_net"] == LEDGER and b["state"] == "frozen"
    one = tuple((oid, qty, (0.0 if oid == "oid-6292" else float(qty)), why) for oid, qty, _bk, why in ROWS_986)
    p2 = _pool()
    b2, rows2 = _book_986(p2, rows=one)
    v2 = _venue(trades=[_log_fill("oid-6292", 802.0), _log_fill("oid-6263", 50.0, ts=FILLED_AT + 2)])
    st2 = _tick(p2, v2, http=_mkt(HIS))
    assert _census(st2, "disagree_fill_adopted") == 1 and b2["ledger_net"] == VENUE
    assert _dis(b2)["rows"] == [_row(rows2, "oid-6292")["id"]]
    assert _row(rows2, "oid-6263")["booked_filled"] == float(REST_QTY), "a whole row is never re-booked"


# ------------------------------------------------- (9) the memo; the wait


def test_e23_the_memo_bounds_the_read_to_one_per_wait_through_e22s_rail(monkeypatch):
    """Two ticks 40 s apart -> one read; 300 s later -> a second; the
    memo rides the plan (`disagree_fill_at`) and the process memo; the
    wait is E22's MIRROR_LOST_FILL_REREAD_S read through rules -- no
    rail of this lane's own."""
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-6292", 500.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 1 and _census(st, "disagree_fill_unexplained") == 1
    assert len(_hand_reads(v)) == 1                                  # E24: the hand's read, on the same wait
    assert b["last_plan"]["disagree_fill_at"] == NOW
    st2 = _tick(p, v, now=NOW + 40, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 1 and len(_hand_reads(v)) == 1, "inside the wait: no second read"
    assert all(_census(st2, k) == 0 for k in NEW_NAMES)
    assert b["last_plan"]["disagree_fill_at"] == NOW and _dis(b)["at"] == NOW, "the memo and the last verdict carried"
    assert b["state"] == "frozen" and b["ledger_net"] == LEDGER
    st3 = _tick(p, v, now=NOW + 300, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 2 and _census(st3, "disagree_fill_unexplained") == 1
    assert len(_hand_reads(v)) == 2
    assert b["last_plan"]["disagree_fill_at"] == NOW + 300
    # the process memo covers a plan that lost the clock (a quiet skip carries _SKIP_CARRIED alone)
    b["last_plan"] = {k: v_ for k, v_ in b["last_plan"].items() if k not in ("disagree_fill_at", "disagree_fill", "hand")}
    _tick(p, v, now=NOW + 340, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 2 and len(_hand_reads(v)) == 2
    # the plan memo covers a restart (the process memo empty)
    ml._disagree_fill_read_at.clear()
    ml._hand_read_at.clear()
    _tick(p, v, now=NOW + 380, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 2 and len(_hand_reads(v)) == 2 and b["last_plan"]["disagree_fill_at"] == NOW + 300
    # the wait lengthened (E22's rail, min_wait_env): 40 s ticks never read again inside it
    monkeypatch.setattr(rules, "MIRROR_LOST_FILL_REREAD_S", 900.0)
    _tick(p, v, now=NOW + 620, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 2 and len(_hand_reads(v)) == 2
    _tick(p, v, now=NOW + 1200, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 3 and len(_hand_reads(v)) == 3
    # then the log names it: adopted after the wait
    v2 = _venue(trades=[_log_fill("oid-6292", 802.0)])
    st4 = _tick(p, v2, now=NOW + 2200, http=_mkt(HIS))
    assert _census(st4, "disagree_fill_adopted") == 1 and b["ledger_net"] == VENUE
    # the worker reads the wait through rules alone; the read is a _venue_read on the budget
    src = inspect.getsource(ml._disagree_fill_adopt)
    assert "rules.MIRROR_LOST_FILL_REREAD_S" in src and '"MIRROR_LOST_FILL_REREAD_S"' not in inspect.getsource(ml)
    assert "await _venue_read(t, t.pmus.recent_trades" in inspect.getsource(ml._cancelled_log_fills)
    assert inspect.getsource(rules).count('min_wait_env("MIRROR_LOST_FILL_REREAD_S"') == 1
    assert "MIRROR_DISAGREE" not in inspect.getsource(rules) and "MIRROR_CANCEL_FILL" not in inspect.getsource(rules)


def test_e23_the_memo_is_bounded():
    for i in range(ml._LOST_FILL_MEMO_MAX + 5):
        ml._disagree_fill_read_at[10_000 + i] = NOW - 10_000 + i
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=[])
    _tick(p, v, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 1 and len(ml._disagree_fill_read_at) <= ml._LOST_FILL_MEMO_MAX
    assert ml._disagree_fill_read_at[b["id"]] == NOW, "the newest clocks are kept"


# --------------------- (10) the transition tick; another freeze reason; the cached read


def test_e23_never_on_the_transition_tick_and_never_under_another_freeze_reason():
    """A LIVE book with the cancelled rows whose walk disagrees (1215 vs
    413): E16's suspect, then the freeze on the second read with
    `transition_tick`, no log read; the adoption runs only on a book
    that was frozen when its tick began. A book frozen placement_lost
    with the same rows is E22's road, never this one."""
    p = _pool()
    b, rows = _book_986(p, state="live")
    v = _venue(trades=[_log_fill("oid-6292", 802.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert _census(st, "venue_ledger_suspect") == 1 and b["state"] == "live"
    assert not _trades_reads(v) and all(_census(st, k) == 0 for k in NEW_NAMES)
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(HIS))
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert _plan_exit(b) == {"held": "transition_tick"} and not _trades_reads(v)
    assert all(_census(st2, k) == 0 for k in NEW_NAMES) and b["ledger_net"] == LEDGER
    # the tick after: frozen when it began -> the road is open
    st3 = _tick(p, v, now=NOW + 60, http=_mkt(HIS))
    assert len(_trades_reads(v)) == 1 and _census(st3, "disagree_fill_adopted") == 1 and b["ledger_net"] == VENUE
    p2 = _pool()
    b2, rows2 = _book_986(p2, reason="placement_lost")
    v2 = _venue(trades=[_log_fill("oid-6292", 802.0)])
    st4 = _tick(p2, v2, http=_mkt(HIS))
    assert not _trades_reads(v2) and all(_census(st4, k) == 0 for k in NEW_NAMES) and b2["ledger_net"] == LEDGER
    assert "disagree_fill" not in b2["last_plan"]
    # the call sits inside the was_frozen arm, after E22's call and before the frozen exit
    src = inspect.getsource(ml._tick_book)
    arm = src[src.index("if was_frozen:\n                # E22"):src.index('plan["frozen_exit"] = {"held": "transition_tick"}')]
    a, d, f = (arm.index("await _lost_fill_adopt("), arm.index("await _disagree_fill_adopt(t, book, r, ledger, registered, prior_plan, plan)"),
               arm.index("await _frozen_exit("))
    assert a < d < f and src.count("_disagree_fill_adopt(") == 1


def test_e23_a_cached_read_reads_nothing_and_unreadable_rows_read_nothing():
    p = _pool()
    b, rows = _book_986(p)
    t = ml._Tick(pool=p, pmus=_venue(trades=[_log_fill("oid-6292", 802.0)]), http=None, now=NOW, stats=ml._new_stats())
    t.walk_at = None
    r = ml._Reading("rn1", CID, SLUG, M, N, [], HIS, 0.0, {}, None, False, False, False, None, None,
                    0.60, 0.61, 0.605, float(VENUE), 0.0, None, True)
    plan = {}
    out = asyncio.run(ml._disagree_fill_adopt(t, b, r, LEDGER, 0.0, {"disagree_fill_at": NOW - 10.0, "disagree_fill": {"verdict": "unread"}}, plan))
    assert out is None and plan == {"disagree_fill_at": NOW - 10.0, "disagree_fill": {"verdict": "unread"}}
    assert not t.pmus.calls and b["ledger_net"] == LEDGER
    t.walk_at = NOW
    p.raise_on.append(("ml-cancelled-rows", RuntimeError("down")))
    assert asyncio.run(ml._disagree_fill_adopt(t, b, r, LEDGER, 0.0, {}, {})) is None and not t.pmus.calls
    # a live book, or one frozen under another name: None before any read
    p.raise_on.clear()
    b["state"] = "live"
    assert asyncio.run(ml._disagree_fill_adopt(t, b, r, LEDGER, 0.0, {}, {})) is None and not t.pmus.calls


def test_e23_a_booking_that_fails_stops_the_adoption_and_a_reason_write_failing_is_logged_once(caplog):
    """The cursor UPDATE raising: `write_failed` counted by _book_delta,
    the row unbooked, `adopt_write_failed` on the plan, nothing counted
    of the lane's; the reason UPDATE raising: the fill IS booked (the
    record, never the money), logged once."""
    p = _pool()
    b, rows = _book_986(p)
    p.raise_on.append(("ml-order-cursor", RuntimeError("down")))
    v = _venue(trades=[_log_fill("oid-6292", 802.0)])
    st = _tick(p, v, http=_mkt(HIS))
    assert _row(rows, "oid-6292")["booked_filled"] == 0.0 and b["ledger_net"] == LEDGER
    # write_failed twice: _book_delta's own count and _freeze's transition count (a reason NEW to a
    # book frozen under another name emits, V3-2) -- both today's; the first reason sticks
    assert _census(st, "write_failed") == 2 and all(_census(st, k) == 0 for k in NEW_NAMES)
    assert _dis(b)["verdict"] == "adopt_write_failed" and b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    ml._disagree_fill_read_at.clear()                  # book ids repeat across this file's pools
    p2 = _pool()
    b2, rows2 = _book_986(p2)
    p2.raise_on.append(("ml-order-reason", RuntimeError("down")))
    v2 = _venue(trades=[_log_fill("oid-6263", 300.0), _log_fill("oid-6292", 502.0, ts=FILLED_AT + 5)])
    with caplog.at_level("WARNING"):
        st2 = _tick(p2, v2, http=_mkt(HIS))
    assert _census(st2, "disagree_fill_adopted") == 1 and b2["ledger_net"] == VENUE
    assert _row(rows2, "oid-6292")["booked_filled"] == 502.0 and _row(rows2, "oid-6292")["reason"] == "ttl"
    assert sum("could not write the adopt reason" in r.message for r in caplog.records) == 1


# ------------------------------------------------------ (11) part A: the cancel


def _live_rest(p, qty=REST_QTY, placed_ts=None):
    """A live long book with one GTC rest of `qty` @0.56 past its TTL
    (the cancel's road, `ttl`), on his 28,765 (target 2,876)."""
    b = p.add_book(ledger=0, ratio=0.1)
    o = p.add_order(b, side=BUY, wire=WIRE, qty=qty, order_id="oid-986", kind="increase",
                    placed_ts=(NOW - rules.MIRROR_REST_TTL_S - 1) if placed_ts is None else placed_ts)
    return b, o


def test_e23_part_a_the_cancel_books_the_final_status_reads_fills_and_the_book_never_freezes():
    """The TTL cancel of a 2,467 @0.56 rest: the cancel's terminal read
    carries filled 0, the FINAL read 802 @0.56 -> booked at the cancel
    (the row cancelled, filled 802, avg 0.56, the ledger 802, the
    standing row 802), cancel_fill_late 1, the book live with the venue
    at 802 agreeing; the re-quote rests the remainder."""
    p = _pool()
    b, o = _live_rest(p)
    v = _LateVenue(held={SLUG: 802}, bid=0.60, ask=0.61, script={"oid-986": [(0.0, None), (0.0, None), (802.0, 0.56)]})
    v.rest("oid-986", price=WIRE, qty=REST_QTY)
    st = _tick(p, v, http=_mkt(HIS))
    oo = p.orders[o["id"]]
    assert (oo["state"], oo["booked_filled"], oo["avg_px"], oo["reason"]) == ("cancelled", 802.0, 0.56, "ttl")
    assert b["ledger_net"] == 802 and b["state"] == "live" and b["frozen_reason"] is None
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 802.0
    assert _census(st, "cancel_fill_late") == 1 and _census(st, "cancel_fill_unread") == 0
    assert _census(st, "venue_ledger_suspect") == 0 and _census(st, "venue_ledger_disagree") == 0
    assert _census(st, "partial_fill") == 1 and _census(st, "cancelled_unfilled") == 0
    # step O's own read (_reconcile_open decides the cancel on it), the cancel's terminal read, ONE final read
    assert len(_statuses(v)) == 3, "the terminal read, then ONE final read"
    rec = _recent("cancel_fill_late", b["id"])
    assert len(rec) == 1 and rec[0]["order_row"] == o["id"] and (rec[0]["answer"], rec[0]["final"], rec[0]["px"]) == (0.0, 802.0, 0.56)
    assert not ml._cancel_reread_pending
    # the re-quote: the remainder toward 2,876 rests at his cent (its size under the day room's clip,
    # the existing rail), planned off the ledger the late booking left
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1:3] == (SLUG, WIRE) and 0 < pl[0][3] <= 2876 - 802
    assert b["last_plan"]["ledger"] == 802 and b["last_plan"]["qty"] == 2876 - 802
    assert ("cancel_fill_late", "rn1") in {tuple(k.split("|")) for k in ml.mirror_census_snapshot()}


def test_e23_part_a_a_final_read_that_carries_no_more_books_the_cancels_answer_as_before():
    """The terminal read 405.82 and the final read the same: booked once
    off the answer as today, nothing of the lane's counted, one extra
    status read and no memo."""
    p = _pool()
    b, o = _live_rest(p, qty=2874)
    v = _LateVenue(held={SLUG: 406}, bid=0.60, ask=0.61, script={"oid-986": [(405.82, 0.56)]})
    v.rest("oid-986", price=WIRE, qty=2874)
    st = _tick(p, v, http=_mkt(HIS))
    oo = p.orders[o["id"]]
    assert (oo["state"], oo["booked_filled"]) == ("cancelled", 405.82) and b["ledger_net"] == 406
    assert all(_census(st, k) == 0 for k in NEW_NAMES) and len(_statuses(v)) == 3
    assert not ml._cancel_reread_pending and b["state"] == "live"


def test_e23_part_a_the_final_read_unreadable_writes_the_answer_memos_the_row_and_the_next_tick_books_it(caplog):
    """The final read raising: the row cancelled off the cancel's answer
    (filled 0), cancel_fill_unread 1, the row memoed (never a freeze,
    the book live); the NEXT tick re-reads it once -- 802 @0.56 -> booked
    as a terminal fill (the row still `cancelled`, reason `ttl`),
    cancel_fill_late 1, the memo emptied. A memo noted on THIS tick is
    never read on it."""
    p = _pool()
    b, o = _live_rest(p)
    v = _LateVenue(held={SLUG: 0}, bid=0.60, ask=0.61,
                   script={"oid-986": [(0.0, None), (0.0, None), RuntimeError("status down"), (802.0, 0.56)]})
    v.rest("oid-986", price=WIRE, qty=REST_QTY)
    with caplog.at_level("WARNING"):
        st = _tick(p, v, http=_mkt(HIS))
    oo = p.orders[o["id"]]
    assert (oo["state"], oo["booked_filled"], oo["reason"]) == ("cancelled", 0.0, "ttl")
    assert b["ledger_net"] == 0 and b["state"] == "live" and _census(st, "cancel_fill_unread") == 1
    assert _census(st, "cancel_fill_late") == 0 and _census(st, "cancelled_unfilled") == 1
    assert len(_statuses(v)) == 3
    ent = ml._cancel_reread_pending[o["id"]]
    assert (ent["oid"], ent["book"], ent["whale"], ent["tries"], ent["reason"]) == ("oid-986", b["id"], "rn1", 0, "ttl")
    assert sum("the final status read could not be made" in r.message for r in caplog.records) == 1
    # the re-quote rested toward 2,876 off a ledger of 0 (its size under the day room's clip); the
    # next tick books the late 802 before any book is planned (the re-quote's rest still standing
    # on the venue: v.orders is the venue's own book)
    assert len(_places(v)) == 1 and b["last_plan"]["ledger"] == 0
    v2 = _LateVenue(held={SLUG: 802}, bid=0.60, ask=0.61, script={"oid-986": [(802.0, 0.56)]})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(HIS))
    assert (oo["state"], oo["booked_filled"], oo["avg_px"], oo["reason"]) == ("cancelled", 802.0, 0.56, "ttl")
    assert b["ledger_net"] == 802 and p.rows[b["standing_row_id"]]["filled_shares"] == 802.0
    assert _census(st2, "cancel_fill_late") == 1 and _census(st2, "cancel_fill_unread") == 0
    assert not ml._cancel_reread_pending and b["state"] == "live"
    # the memo's read comes after step O's own reads (the re-quote's standing rest) and BEFORE the
    # books' quote reads
    kinds = [c[:2] for c in v2.calls]
    assert ("status", "oid-986") in kinds and kinds.index(("status", "oid-986")) < kinds.index(("bbo", SLUG))
    rec = _recent("cancel_fill_late", b["id"])
    assert rec and rec[-1]["order_row"] == o["id"] and (rec[-1]["answer"], rec[-1]["final"]) == (0.0, 802.0)


def test_e23_part_a_the_memo_gives_up_after_its_tries_and_is_bounded(caplog):
    p = _pool()
    b, o = _live_rest(p)
    v = _LateVenue(held={SLUG: 0}, bid=0.60, ask=0.61, script={"oid-986": [(0.0, None), (0.0, None), RuntimeError("down")]})
    v.rest("oid-986", price=WIRE, qty=REST_QTY)
    _tick(p, v, http=_mkt(HIS))
    assert o["id"] in ml._cancel_reread_pending
    for i in range(ml._CANCEL_REREAD_TRIES):
        vi = _LateVenue(held={SLUG: 0}, bid=0.60, ask=0.61, script={"oid-986": [RuntimeError("down")]})
        vi.orders = v.orders
        with caplog.at_level("WARNING"):
            sti = _tick(p, vi, now=NOW + 30 * (i + 1), http=_mkt(HIS))
        assert _census(sti, "cancel_fill_unread") == 1 and b["state"] == "live"
    assert o["id"] not in ml._cancel_reread_pending and p.orders[o["id"]]["booked_filled"] == 0.0
    assert sum("dropped from the memo" in r.message for r in caplog.records) == 1
    # the bound: the oldest entry is dropped and named when the memo is full
    ml._cancel_reread_pending.clear()
    for i in range(ml._CANCEL_REREAD_MEMO_MAX):
        ml._cancel_reread_pending[100_000 + i] = {"oid": f"x{i}", "book": 1, "whale": "rn1", "seq": 0,
                                                  "at": NOW - 1000 + i, "tries": 0, "reason": "ttl"}
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    ml._cancel_reread_note(t, {"id": 7, "order_id": "oid-7", "whale": "rn1"}, {"id": 1}, "ttl")
    assert len(ml._cancel_reread_pending) == ml._CANCEL_REREAD_MEMO_MAX and 100_000 not in ml._cancel_reread_pending
    assert ml._cancel_reread_pending[7]["oid"] == "oid-7"
    # a row no longer 'cancelled' drops out of the memo without a read
    ml._cancel_reread_pending.clear()
    p2 = _pool()
    b2, o2 = _live_rest(p2)
    v2 = _LateVenue(held={SLUG: 0}, bid=0.60, ask=0.61, script={"oid-986": [(0.0, None), (0.0, None), RuntimeError("down")]})
    v2.rest("oid-986", price=WIRE, qty=REST_QTY)
    _tick(p2, v2, http=_mkt(HIS))
    assert o2["id"] in ml._cancel_reread_pending
    p2.orders[o2["id"]]["state"] = "filled"
    v3 = _LateVenue(held={SLUG: 0}, bid=0.60, ask=0.61)
    v3.orders = v2.orders
    _tick(p2, v3, now=NOW + 30, http=_mkt(HIS))
    assert o2["id"] not in ml._cancel_reread_pending and not [c for c in v3.calls if c == ("status", "oid-986")]


def test_e23_part_a_a_final_read_past_the_quantity_is_refused_by_name_and_frozen_overfill():
    """The final read 2,500 on a row of 2,467 (the venue holding 2,500):
    nothing booked off that read (the row cancelled with the answer's
    0), cancel_fill_overfill on the census dict, the book frozen
    `overfill` by the E5 freeze path (the first reason sticks under the
    disagreement that follows; a frozen `overfill` book whose venue
    read agreed with its ledger would thaw by the existing rule -- a
    freeze that ends when its cause ends); the desk is NOT tripped (a
    status figure past the row is a reading nothing can book, not a
    sale past the ledger)."""
    p = _pool()
    b, o = _live_rest(p)
    v = _LateVenue(held={SLUG: 2500}, bid=0.60, ask=0.61, script={"oid-986": [(0.0, None), (0.0, None), (2500.0, 0.56)]})
    v.rest("oid-986", price=WIRE, qty=REST_QTY)
    st = _tick(p, v, http=_mkt(HIS))
    oo = p.orders[o["id"]]
    assert (oo["state"], oo["booked_filled"]) == ("cancelled", 0.0) and b["ledger_net"] == 0
    assert b["state"] == "frozen" and b["frozen_reason"] == "overfill" and _census(st, "overfill") == 1
    assert st["census"].get("cancel_fill_overfill", 0) == 1 and _census(st, "cancel_fill_late") == 0
    assert "cancel_fill_overfill" not in ml.CENSUS_KEYS, "a refusal name on the dict, not a census key"
    assert p.state["mirror_live"] is True and not _places(v)
    rec = _recent("cancel_fill_overfill", b["id"])
    assert rec and (rec[-1]["final"], rec[-1]["answer"]) == (2500.0, 0.0)
    # the same on the memo's re-read
    p2 = _pool()
    b2, o2 = _live_rest(p2)
    v2 = _LateVenue(held={SLUG: 0}, bid=0.60, ask=0.61, script={"oid-986": [(0.0, None), (0.0, None), RuntimeError("down"), (2500.0, 0.56)]})
    v2.rest("oid-986", price=WIRE, qty=REST_QTY)
    _tick(p2, v2, http=_mkt(HIS))
    v3 = _LateVenue(held={SLUG: 2500}, bid=0.60, ask=0.61, script={"oid-986": [(2500.0, 0.56)]})
    v3.orders = v2.orders
    st3 = _tick(p2, v3, now=NOW + 30, http=_mkt(HIS))
    assert st3["census"].get("cancel_fill_overfill", 0) == 1 and b2["frozen_reason"] == "overfill"
    assert p2.orders[o2["id"]]["booked_filled"] == 0.0 and o2["id"] not in ml._cancel_reread_pending


def test_e23_part_a_only_a_rest_with_an_id_is_re_read_and_the_site_sits_before_the_booking():
    assert ml._cancel_reread_wanted({"tif": "GTC", "order_id": "x", "kind": "increase"})
    assert ml._cancel_reread_wanted({"tif": "GTD", "order_id": "x", "kind": "reduce"})
    assert not ml._cancel_reread_wanted({"tif": "GTC", "order_id": None, "kind": "increase"})
    assert not ml._cancel_reread_wanted({"tif": "IOC", "order_id": "x", "kind": "take"})
    assert not ml._cancel_reread_wanted({"tif": "GTC", "order_id": "x", "kind": "take"})
    assert not ml._cancel_reread_wanted({"tif": "CLOSE", "order_id": "x", "kind": "flatten_vanished"})
    src = inspect.getsource(ml._cancel_and_settle)
    i_loop, i_final, i_book, i_state = (src.index("for _i in range(CANCEL_READS):"),
                                        src.index("st = await _cancel_final_figure(t, o, book, st, reason)"),
                                        src.index("await _book_delta(t, o, book, st, maker="),
                                        src.index("out = await _finish_order(t, o, book, st, reason)"))
    assert i_loop < i_final < i_book < i_state, "after the terminal read, before the booking and the row's write"
    assert "le._rest_terminal(st) and _cancel_reread_wanted(o)" in src
    # the memo's read runs after step O and before the books, on the full tick only
    tsrc = inspect.getsource(ml._tick)
    assert tsrc.index("await _reconcile_orders(t)\n") < tsrc.index("await _cancel_reread(t)") < tsrc.index('t.timing["orders"] +=')
    assert tsrc.count("await _cancel_reread(t)") == 1
    assert "_cancel_reread" not in inspect.getsource(ml._fast_tick)
    # the final read is the same paced read _finish_order's caller trusts
    fsrc = inspect.getsource(ml._cancel_final_figure)
    assert 'final = await _order_status(t, str(o["order_id"]))' in fsrc
    assert "_trip_live_off" not in fsrc and "_trip_live_off" not in inspect.getsource(ml._cancel_reread_row)


# ------------------------------------- (12) the E18 triple, the docs, no knob


def test_e23_the_census_place_the_emit_sites_the_untouched_functions_and_no_knob():
    keys = ml.CENSUS_KEYS
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-19:-13 -> -23:-17, -27 -> -31, -31:-27 -> -35:-31, -34:-31 -> -38:-35, -40 -> -44)
    assert keys[-23:-17] == NEW_NAMES and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    # FILL lane 16 (one name) and E21 / FILL lane 10 (six) landed first and sit between lane 11's one and these six (-20 -> -27)
    assert keys[-31] == "cand_market_closed_db" and keys[-35:-31] == ("lost_fill_adopted", "lost_fill_unread", "lost_fill_unexplained", "lost_fill_ambiguous")
    assert keys[-38:-35] == ("he_holds", "he_holds_unread", "reopen_refused") and keys[-44] == "take_in_band"
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert all(ml._new_stats()["census"][k] == 0 for k in NEW_NAMES)
    assert all(k not in ml._INTEG_CENSUS_KEYS for k in NEW_NAMES)
    whole = inspect.getsource(ml)
    a_src = inspect.getsource(ml._cancel_final_figure) + inspect.getsource(ml._cancel_reread_row)
    b_src = inspect.getsource(ml._disagree_fill_adopt)
    for k in PART_A:
        assert whole.count(f'_mirror_stop("{k}", w)') == a_src.count(f'_mirror_stop("{k}", w)') >= 1, k
    for k in PART_B:
        assert whole.count(f'_mirror_stop("{k}", w)') == b_src.count(f'_mirror_stop("{k}", w)') >= 1, k
    assert a_src.count('_mirror_stop("cancel_fill_late", w)') == 2 and a_src.count('_mirror_stop("cancel_fill_unread", w)') == 2
    assert a_src.count('_mirror_stop("cancel_fill_overfill", w)') == 2
    assert b_src.count('_mirror_stop("disagree_fill_adopted", w)') == 1 and b_src.count('_mirror_stop("disagree_fill_unread", w)') == 1
    assert b_src.count('_mirror_stop("disagree_fill_ambiguous", w)') == 4 and b_src.count('_mirror_stop("disagree_fill_unexplained", w)') == 6
    # the booking is the terminal fill's own: _book_delta, the reason statement, no thaw of its own
    assert "_book_delta(t, o, book, st, maker=" in b_src and "_SQL_ORDER_REASON, o[\"id\"], reason" in b_src
    assert '_adopt_reason(o, "adopted from the trade log after the cancel")' in b_src
    assert "await _thaw(" not in b_src and "_SQL_BOOK_THAW" not in b_src and "_thaw_agrees" not in b_src
    assert "_SQL_ORDER_STATE" not in b_src and "_finish_order" not in b_src, "the row keeps its state and its cancel word"
    assert 't.walk_at is None' in b_src and 'frozen_reason") != "venue_ledger_disagree"' in b_src
    # the statements: the open-orders projection with the cancelled predicate, both shapes; the row read
    for s, base in ((ml._SQL_CANCELLED_ROWS, ml._SQL_ORDERS_OPEN), (ml._SQL_CANCELLED_ROWS_047, ml._SQL_ORDERS_OPEN_047)):
        assert ("WHERE o.book_id = $1 AND o.state = 'cancelled' AND o.order_id IS NOT NULL AND o.done_at < to_timestamp($2)"
                " AND o.booked_filled < o.qty - 0.000001" in s and "ml-cancelled-rows" in s)
        assert s.split(" WHERE")[0] == base.split(" WHERE")[0]
    for s, base in ((ml._SQL_ORDER_ROW, ml._SQL_ORDERS_OPEN), (ml._SQL_ORDER_ROW_047, ml._SQL_ORDERS_OPEN_047)):
        assert "WHERE o.id = $1 AND o.state = 'cancelled' AND o.order_id IS NOT NULL" in s and "ml-order-row" in s
        assert s.split(" WHERE")[0] == base.split(" WHERE")[0]
    assert "o.intent" in ml._SQL_CANCELLED_ROWS and "o.intent" not in ml._SQL_CANCELLED_ROWS_047
    import pglast
    for s in (ml._SQL_CANCELLED_ROWS, ml._SQL_CANCELLED_ROWS_047, ml._SQL_ORDER_ROW, ml._SQL_ORDER_ROW_047):
        pglast.parse_sql(s)
    # no decision word written; 059's list stands; no migration
    assert "decision" not in b_src and "decision" not in a_src
    mig = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "cancel_fill" not in mig and "disagree_fill" not in mig
    files = sorted(x.name for x in (ROOT / "backend" / "migrations").glob("*.sql"))
    assert files[-1] == "061_fill_answers_cause_orders_fast.sql"
    # no knob, no rail of the lane's own: E22's wait alone
    rsrc = inspect.getsource(rules)
    assert "CANCEL_FILL" not in rsrc and "DISAGREE_FILL" not in rsrc and "E23" not in rsrc
    assert rsrc.count("min_wait_env(") == 5 and rsrc.count("capped_env(") == 22  # the per-trade cap (2026-09-09 ~21:05Z, owner order): MIRROR_NET_CAP_USD reads unbounded_env, one capped_env fewer (23 -> 22)


def test_e23_the_untouched_functions_are_byte_identical_to_the_tip():
    """The brief's not-touched list, hashed on 67fbaf3 (sha256[:16] of
    the source): a mismatch names the function that moved."""
    got = {name: _sha(getattr(ml, name)) for name in UNTOUCHED}
    assert got == UNTOUCHED, {k: (got[k], UNTOUCHED[k]) for k in got if got[k] != UNTOUCHED[k]}
    for fn in (ml._thaw, ml._thaw_verdict, ml._thaw_agrees, ml._frozen_exit, ml._lost_fill_adopt, ml._registered_shares,
               ml._reconcile_placing, ml._mark_lost, ml._agree_record, ml._second_disagreeing_read, ml._finish_order,
               ml._reconcile_open, ml._book_fill):
        assert "disagree_fill" not in inspect.getsource(fn) and "cancel_fill" not in inspect.getsource(fn), fn.__name__
    lsrc = inspect.getsource(le)
    assert "cancel_fill" not in lsrc and "disagree_fill" not in lsrc and "_LOST_FILL_WINDOW_S = 20 * 60.0" in lsrc


def test_e23_every_name_is_emitted_here(caplog):
    """The lane's six names, each driven once (the worker file's coverage
    read imports this)."""
    for fn in (test_e23_book_986_the_cancelled_rows_unbooked_fills_are_adopted_and_the_existing_thaw_takes_the_book,
               test_e23_the_log_naming_500_is_unexplained_and_books_nothing,
               test_e23_the_log_unreadable_is_unread_and_books_nothing,
               test_e23_one_id_summing_to_900_is_ambiguous,
               test_e23_part_a_the_cancel_books_the_final_status_reads_fills_and_the_book_never_freezes):
        ml._disagree_fill_read_at.clear()              # book ids repeat across this file's pools
        ml._cancel_reread_pending.clear()
        fn()
    ml._disagree_fill_read_at.clear()
    ml._cancel_reread_pending.clear()
    test_e23_part_a_the_final_read_unreadable_writes_the_answer_memos_the_row_and_the_next_tick_books_it(caplog)


def test_e23_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E23 -- .* \(2026-09-09, FILL lane 23\)", doc, re.M), "the E23 section header"
    for k in NEW_NAMES + ("cancel_fill_overfill", "disagree_fill_at", "disagree_fill", "_disagree_fill_adopt",
                          "_cancel_final_figure", "_cancel_reread", "MIRROR_LOST_FILL_REREAD_S",
                          "test_e23_cancel_fill_adopt.py", "986", "6292", "one_read", "transition tick",
                          "venue_ledger_disagree", "_CANCEL_REREAD_TRIES", "_CANCEL_REREAD_MEMO_MAX"):
        assert k in doc, k
