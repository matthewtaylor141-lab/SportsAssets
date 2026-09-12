"""E21 (2026-09-09; FILL program lane 10): the add's take -- his ADDING
fill re-plans a book with an ENTRY rest standing at fast-tick speed, and
the take off the rest is sized at the plan's quantity.

The rows (hard2/h2225.txt, the 22:25Z hourly). fills-answered named
`open_order_pending` on 95 fills / $22,841.69 at a lag median of 3 s
(938): the tick SAW the fill and refused it because a rest of ours stood.
Book 760's three adds of 2,983 sh @0.823-0.824 at 19:51:22 / 19:52:55 /
19:53:58 against ONE standing rest (983-985), named at lags 3 / 26 / -37
s; its fills-missed rows missed_replace 3 / $6,308.07 / +0.1788 / lag 77
s (1119) and filled 2 / $5,055.54 (1128). Book 825: rest 4703 placed
22:18:24 for his 22:18:19 fill (869), his next fill 22:18:25 answered by
4707 at 22:19:53 = 88 s (866); book 826: 4695 at 22:16:50 for his
22:16:49 (876), his next fill 22:16:51 answered by 4702 at 22:17:59 = 68
s (870) -- the only two first orders over 60 s in the 22Z hour, whose
`increase` line reads 48 placed / 26 filled / his_to_order p90 116 s
(835). Book 816: 4692, 1,403 sh, filled 631.85 while standing, then 752
re-quoted (872-882). The class refused:open_order_pending is 38 /
$44,884.07 / 36 resolved / +0.5465 (1002); book 347 (frozen short) holds
2 / $31,970.05 of it unresolved (1088).

THE RULE. The fast gate's order_open clause, AFTER lane 3's
order_open_his_exit test and behind rules.MIRROR_FAST_ADD_REPLAN (env may
only turn it OFF), admits a woken ADDING fill of his on a book with an
ENTRY rest standing (_order_open_his_add: the row open / increase / not
an IOC / the add leg, placed at or before the tick's clock, no reducing
fill after it, an add on the axis after it); _fast_book then makes the
fast tick's own step O -- ONE paced status read, nothing booked: blank ->
`fast_status_unread` with the row untouched and NO freeze; a fill past
the booked figure -> `fill_after_walk`; terminal -> `order_open`; open ->
the row on t.open_by_book, census `fast_his_add`, then _tick_book / _act
as the full tick runs them (keep under the floor or inside the
hysteresis, replace past the floor, or the take off the rest). The take
off a LONG book's entry rest is sized at the PLAN's quantity through
_room_qty (both tick paths, behind the switch) and its IOC writes
`take_on_add`; an exit's take and a short book's add keep min(plan,
leaves) byte for byte. Six census names; plan `fast_add`; no rail, no
migration; OFF = today on both paths.

Driven against the worker file's fakes (its autouse rails are imported)
and E9's fast-tick helpers.
"""
from __future__ import annotations

import hashlib
import inspect
import pathlib
import re
import types

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, M, N, NOW, SELL, SLUG, _Venue, _armed, _cancels, _census, _fill, _kinds, _mkt, _places,
    _pool, _run, _short_http, _short_world, _shorts_on, _tick,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("fast_order_open", "fast_his_add", "fast_add_kept", "fast_add_replaced", "fast_add_took",
             "fast_status_unread")
GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"

# the functions the plan names as NOT touched, hashed on the tip this lane
# was built on (219f140): a change to any of them is not this lane's
UNTOUCHED = {
    "rest_decision": ("2f8b8feef14fbd62", rules.rest_decision), "take_allowed": ("dc3079622052b6ff", rules.take_allowed),
    "at_or_through": ("4aece58b61ee21bc", rules.at_or_through),
    # _take_band read 972e473fcdf4a2c0 on 219f140; E27 (FILL lane 27, 2026-09-09: the band's width
    # stamped as `band` with `frac` {his_px, cost, width}) landed after this lane and moved it --
    # re-cut at E27's landing, not this lane's (the verdicts and the arm this lane pins are unchanged)
    # E31 (FILL lane 31, 2026-09-10): `_take_band` is DELETED with the band arm this lane's
    # fast-tick admission fed, and `_short_wire` is re-cut for the maker cent
    "_short_wire": ("12afe5fcd5b0c248", ml._short_wire),
    "_order_open_his_exit": ("1af54b54e3e0dd16", ml._order_open_his_exit),
    "_fast_open_entry_rest": ("618ec2943215df41", ml._fast_open_entry_rest),
    "_fast_candidate": ("922585ffb6856f70", ml._fast_candidate), "_reconcile_open": ("2b76640dec2d3bd3", ml._reconcile_open),
    "_order_status": ("04620411d1d55e0e", ml._order_status), "_finish_order": ("1db222463610e38c", ml._finish_order),
    # E29 (FILL lane 29): the hand_held refusal before the mapping moved _tick_candidate 199a7d617e704397 -> c4fd4eb511c20f75
    "_tick_candidate": ("c4fd4eb511c20f75", ml._tick_candidate), "_lost_fill_adopt": ("61c67ae8946f3af4", ml._lost_fill_adopt),
    # E31: `_exit_take` and `_ioc_reread` are DELETED (no caller left); `_reconcile_open` is re-cut
    # for the maker rest that stands past its TTL
    "_requotes_this_hour": ("f45f581625d2dea8", ml._requotes_this_hour),
    # _cancel_and_settle read ca65dea6b7703ba4 on 219f140; E23 (FILL lane 23, the cancel's final
    # status re-read) landed after this lane and moved it -- re-cut at E23's landing, not this lane's
    "_cancel_and_settle": ("953ba5587d2ad423", ml._cancel_and_settle), "_freeze": ("2fc0334b92c9c352", ml._freeze),
    "restored_block": ("4ad2364970b35ec1", mi.restored_block), "adding_since": ("0b83e6c13d44caad", mi.adding_since),
    "reducing_on": ("1d8174a6e061b4a9", mi.reducing_on),
}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _inserts(p):
    return [a for k, s, a in p.sent if "ml-order-insert" in s]


def _status_calls(v):
    return [c for c in v.calls if c[0] == "status"]


def _state_writes(p):
    return [a for k, s, a in p.sent if "ml-order-state" in s]


def _freeze_writes(p):
    return [a for k, s, a in p.sent if "ml-book-freeze" in s]


def _rested(p, v, his=300.0):
    """A long book with an ENTRY rest standing, placed by a FULL tick at
    NOW: his `his` shares @0.31 (the fixture's default 300), bid 0.30 /
    ask 0.32 -> the rest for the plan's whole quantity.

    E31 (FILL lane 31, 2026-09-10) MOVED THE CENT, ONE WAY, FOR EVERY
    WORLD IN THIS FILE: a long add rested at buy_price(0.31, 0.30) =
    floor(min(his, bid)) = 0.30, which JOINED THE BID a cent under him.
    It now rests at rules.maker_wire(BUY, 0.31, 0.30, 0.32) =
    min(buy_wire(0.31), 0.32 - MAKER_TICK) = 0.31 -- HIS OWN CENT, inside
    the spread. The quantity, the leg, the intent and the row are
    unchanged. Returns (book, the rest's row id)."""
    b = p.add_book(ledger=0)
    _tick(p, v, http=_mkt(his))
    assert b["open_order_id"] and len(_places(v)) == 1 and p.orders[b["open_order_id"]]["kind"] == "increase"
    v.calls.clear()
    return b, b["open_order_id"]


def _add(p, size, ts, px=0.31, asset=M, side="BUY"):
    """His ADD on the market, ingested a second after its stamp."""
    p.fills.append(_fill(asset, side, float(size), px, ts, detected_at=ts + 1))


def _row(p, ts):
    return p.fill_answers[("rn1", str(ts))]


# ------------------------------------------------------------ (1) book 760


def test_e21_book_760s_shape_kept_under_the_floor_then_replaced_through_the_band_then_took_at_the_grown_quantity(monkeypatch):
    """h2225 983-985 / 1119 / 1128: three adds against one standing rest.
    Wake 1 with the rest 20 s old -> the gate admits (`fast_his_add`),
    keep `min_life` + add_pending, `fast_add_kept`, NO cancel, nothing
    placed. Wake 2 at 93 s (past the floor) -> `replace_qty`, the cancel,
    one rest for the GROWN quantity at his cent, `fast_add_replaced`,
    _SQL_REPLACES + 1. Wake 3 with the ask arriving AT his cent on the
    young new rest.

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10). The gate, the three
    branches, the ages, the grown quantity, the replace's rail and lane
    9's stamps are byte for byte; what moved is the EXECUTION:

      * every rest is at his own cent 0.31, not the bid's 0.30
        (`_rested`'s note);
      * wake 2's `take_in_band` IOC at 0.32 is GONE -- lane 2's band arm
        left _act with the other five, so the wake places ONE order, the
        rest for the grown 497 at his cent, and nothing crosses;
      * wake 3 is the clause this lane exists for. The ask falling to
        0.31 IS THE MARKET COMING TO OUR REST, not a reason to buy the
        spread: `rules.maker_compare_wire(BUY, standing 0.31, his cent
        0.31, maker wire 0.30)` returns the STANDING wire, so there is no
        cent move, and the rest of 497 at 0.31 stands to be lifted by the
        taker who came there. `fast_add_took` / `take_at_his_level` /
        `take_on_add` are retired; the branch is `kept`, the rest is
        untouched and the ledger does not move on this fixture, which
        never scripts a lift."""
    ml._fill_hwm.clear()
    p = _pool()
    v = _Venue()
    b, o1 = _rested(p, v)
    # E31: the rest's wire 0.30 -> 0.31 (his own cent inside the spread)
    assert p.orders[o1]["qty"] == 300 and p.orders[o1]["wire"] == 0.31
    # wake 1: his add of 97 at NOW + 10; the rest 20 s old
    _add(p, 97, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs = _fast(p, v, now=NOW + 20, http=_mkt(397.0))
    assert _skips(fs) == {} and _census(fs, "fast_his_add") == 1 and _census(fs, "fast_add_kept") == 1
    assert _census(fs, "open_order_pending") == 1 and _census(fs, "kept_min_life") == 1
    assert _census(fs, "fast_add_replaced") == 0 and _census(fs, "fast_add_took") == 0 and _census(fs, "fast_order_open") == 0
    assert not _cancels(v) and not _places(v) and len(_status_calls(v)) == 1, "one status read, nothing cancelled, nothing placed"
    lp = b["last_plan"]
    assert lp["fast_add"] == {"rest": o1, "rest_age_s": 20.0, "his_add_sh": 97.0, "since": NOW, "branch": "kept"}
    # E31: the pending add's wire 0.30 -> 0.31, the maker cent it will be placed at
    assert lp["add_pending"] == {"qty": 397, "wire": 0.31, "since": NOW + 20} and lp["rest_cause"] == "min_life"
    assert lp["maker"] == {"wire": 0.31, "bound": 0.31, "his_cent": 0.31, "clause": "his_cent",
                           "side": BUY, "bid": 0.30, "ask": 0.32, "at": NOW + 20, "hint": None}
    assert lp["decision"] == "kept_min_life" and lp["open_order"] == o1 and lp["rest_life"]["age_s"] == 20.0
    assert p.orders[o1]["state"] == "open" and b["open_order_id"] == o1 and _state_writes(p) == []
    r1 = _row(p, NOW + 10)
    assert (r1["name"], r1["cause"], r1["rest_id"], r1["fast"]) == ("open_order_pending", "min_life", o1, True), "lane 9's stamps intact"
    # wake 2: another add at NOW + 80; the rest 93 s old, the ask 0.32 a cent over his 0.31 with the band on
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    _add(p, 100, NOW + 80)
    _walk({M: 0.0, N: 0.0}, NOW + 85)
    v.calls.clear()
    fs2 = _fast(p, v, now=NOW + 93, http=_mkt(497.0))
    assert _skips(fs2) == {} and _census(fs2, "fast_his_add") == 1 and _census(fs2, "fast_add_replaced") == 1
    assert _census(fs2, "fast_add_kept") == 0 and _census(fs2, "fast_add_took") == 0 and fs2["requotes"] == 1
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and p.orders[o1]["state"] == "cancelled" and p.orders[o1]["reason"] == "replace"
    assert [tuple(a) for k, s, a in p.sent if "ml-order-decision" in s] == [(o1, "replace_qty")]
    # E31: the two orders become ONE. Lane 2's band IOC at 0.32 (`take_in_band`) left _act
    # with the six take arms, so the wake places only the rest for the grown 497 -- and at
    # HIS cent 0.31, not the bid's 0.30. post_only True, GTC, never at or through the ask
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.31, 497, False, GTC_TIF)], \
        "one post-only rest for the grown 497 at his own cent; no band IOC"
    assert [c[7] for c in _places(v)] == [True]
    assert _census(fs2, "take_in_band") == 0 and _census(fs2, "rest_placed") == 1
    o2 = b["open_order_id"]
    assert o2 and o2 != o1 and p.orders[o2]["qty"] == 497 and p.orders[o2]["wire"] == 0.31
    lp2 = b["last_plan"]
    assert lp2["fast_add"]["branch"] == "replaced" and lp2["fast_add"]["rest"] == o1 and lp2["fast_add"]["rest_age_s"] == 93.0
    assert lp2["replaced"] == "replace_qty" and lp2["open_order"] == o1 and lp2["decision"] == "rest"
    assert _run(ml._requotes_this_hour(types.SimpleNamespace(pool=p), b)) == 1, "_SQL_REPLACES counts the fast tick's replace"
    r2 = _row(p, NOW + 80)
    assert (r2["name"], r2["order_id"], r2["cause"], r2["rest_id"], r2["fast"]) == ("rest_placed", o2, None, o1, True)
    # wake 3: a third add at NOW + 95; the new rest 7 s old, the ask ARRIVING AT his cent
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    _add(p, 100, NOW + 95)
    _walk({M: 0.0, N: 0.0}, NOW + 97)
    v.calls.clear()
    v.ask, v.ioc_fill = 0.31, 100.0
    fs3 = _fast(p, v, now=NOW + 100, http=_mkt(597.0))
    # E31: the ask coming DOWN to his cent used to cancel the rest and fire one IOC at 0.31
    # for the grown 597 (`take_at_his_level` / `fast_add_took` / decision `take_on_add`).
    # Every one of those is retired. The ask arriving at our resting bid is THE MARKET COMING
    # TO US: maker_compare_wire(BUY, standing 0.31, his cent 0.31, maker 0.30) returns the
    # STANDING 0.31 -- a rest is never re-quoted DOWN with a falling ask, and the taker who
    # came to 0.31 lifts it there. So: nothing cancelled, nothing placed, the branch `kept`
    assert _skips(fs3) == {} and _census(fs3, "fast_his_add") == 1 and _census(fs3, "fast_add_took") == 0
    assert _census(fs3, "take_at_his_level") == 0 and _census(fs3, "kept_min_life") == 1 and _census(fs3, "fast_add_kept") == 1
    assert not _cancels(v) and not _places(v)
    assert p.orders[o2]["state"] == "open" and p.orders[o2]["kind"] == "increase" and b["open_order_id"] == o2
    # the maker record says WHY: his cent is still 0.31, the touch bound has fallen to 0.30,
    # and the standing rest is not moved away from him to follow it
    assert b["last_plan"]["maker"]["his_cent"] == 0.31 and b["last_plan"]["maker"]["bound"] == 0.30
    assert rules.maker_compare_wire(BUY, 0.31, 0.31, 0.30) == 0.31
    ins = _inserts(p)
    # the last two rows written are the two RESTS of wakes 0 and 2; no `take` row exists
    assert (ins[-2][3], ins[-2][5], ins[-2][10], ins[-2][11], ins[-2][20], ins[-2][22]) == \
        ("increase", "GTC", 0.31, 300, "rest", False)
    assert (ins[-1][3], ins[-1][5], ins[-1][10], ins[-1][11], ins[-1][20], ins[-1][22]) == \
        ("increase", "GTC", 0.31, 497, "rest", True)
    assert not any(x[3] == "take" or x[5] == "IOC" or x[20] == "take_on_add" for x in ins)
    assert b["ledger_net"] == 0 and b["last_plan"]["fast_add"]["branch"] == "kept"
    assert b["last_plan"].get("take_filled") is None and b["last_plan"]["decision"] == "kept_min_life"
    assert _run(ml._requotes_this_hour(types.SimpleNamespace(pool=p), b)) == 1, \
        "1 -> was 2: the take's own cancel spent a replace of the hour's rail and there is no take"


# ------------------------------------------------------ (2) / (3) books 825 / 826


def test_e21_books_825_and_826s_shape_the_wake_a_second_after_the_rest_is_admitted_and_kept_and_off_is_order_open(monkeypatch):
    """h2225 866 / 869 (4703 placed 22:18:24 for his 22:18:19 fill, his
    next fill 22:18:25, answered today by 4707 at 22:19:53 = 88 s) and
    870 / 876 (4695 at 22:16:50, his 22:16:51 fill, 4702 at 22:17:59 =
    68 s): the wake one second after the rest passes the gate
    (adding_since > 0 against placed_ts), keep `min_life` at age 2 s,
    nothing cancelled -- the decision made on the wake, not a poll later.
    Today's `order_open` skip is pinned OFF under the switch: no status
    read, no venue call at all."""
    p = _pool()
    v = _Venue()
    b, o1 = _rested(p, v)
    _add(p, 50, NOW + 1)
    _walk({M: 0.0, N: 0.0}, NOW + 1)
    fs = _fast(p, v, now=NOW + 2, http=_mkt(350.0))
    assert _skips(fs) == {} and _census(fs, "fast_his_add") == 1 and _census(fs, "fast_add_kept") == 1
    assert _census(fs, "kept_min_life") == 1 and not _cancels(v) and not _places(v)
    assert b["last_plan"]["fast_add"]["rest_age_s"] == 2.0 and b["last_plan"]["rest_life"]["age_s"] == 2.0
    assert b["last_plan"]["add_pending"]["qty"] == 350 and b["open_order_id"] == o1
    # the switch OFF: today byte for byte -- `order_open`, no status read, no venue call
    monkeypatch.setattr(rules, "MIRROR_FAST_ADD_REPLAN", False)
    v.calls.clear()
    _walk({M: 0.0, N: 0.0}, NOW + 1)
    fs2 = _fast(p, v, now=NOW + 3, http=_mkt(350.0))
    assert _skips(fs2) == {CID: "order_open"} and _census(fs2, "fast_order_open") == 1 and _census(fs2, "fast_his_add") == 0
    assert v.calls == [] and _census(fs2, "fast_tick_skipped") == 1 and _census(fs2, "venue_calls") == 0
    assert b["last_plan"]["fast_add"]["rest_age_s"] == 2.0, "no plan written by the skipped wake"


# ------------------------------------------------- (4) book 816, the short book


def test_e21_book_816s_shape_a_fill_past_the_booked_figure_is_fill_after_walk_and_the_full_tick_books_it():
    """h2225 872-882: 4692 (1,403 sh) filled 631.85 while standing. The
    wake's status read shows the fill -> `fill_after_walk`, nothing
    placed, nothing booked, no state write on the fast tick; the full
    tick's own step O books it and sizes the re-quote as today."""
    p = _pool()
    v = _Venue()
    b, o1 = _rested(p, v)
    v.fills["oid-1"] = (100.0, 0.30)
    _add(p, 100, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs = _fast(p, v, now=NOW + 20, http=_mkt(400.0))
    assert _skips(fs) == {CID: "fill_after_walk"} and _census(fs, "fast_his_add") == 0 and _census(fs, "fast_status_unread") == 0
    assert len(_status_calls(v)) == 1 and not _cancels(v) and not _places(v) and _kinds(v) == ["status"]
    assert b["ledger_net"] == 0 and p.orders[o1]["booked_filled"] == 0.0 and _state_writes(p) == [], "the fast tick books nothing"
    assert b["last_plan"]["fast_add"] is None if "fast_add" in b["last_plan"] else True
    # the full tick's step O books the 100 and plans on the ledger as today
    st = _tick(p, v, now=NOW + 30, http=_mkt(400.0))
    assert b["ledger_net"] == 100 and p.orders[o1]["booked_filled"] == 100.0 and _census(st, "partial_fill") == 1


def test_e21_on_a_short_book_the_gate_passes_for_the_short_axis_add_and_the_plan_never_writes_take_on_add(monkeypatch):
    """A SELL_LONG entry rest on ORDER_INTENT_BUY_SHORT (826's row shape,
    h2225 876): his add on the SHORT axis (a BUY of the other token)
    after the rest passes the gate; the plan goes through _act's short
    rest path (the wire _short_wire's contract cent), kept under the
    floor -- never a `take_on_add`, never the grown-quantity sizing: on
    a short book the lane changes cadence only (pinned by source too)."""
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue(bid=0.31, ask=0.32)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert b["intent"] == "ORDER_INTENT_BUY_SHORT" and _census(st, "short_open") == 1 and b["open_order_id"]
    o1 = b["open_order_id"]
    assert p.orders[o1]["side"] == SELL and p.orders[o1]["kind"] == "increase" and p.orders[o1]["wire"] == 0.32
    v.calls.clear()
    p.fills.append(_fill(N, "BUY", 100.0, 0.72, NOW + 10, detected_at=NOW + 11))
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs = _fast(p, v, now=NOW + 20, http=_mkt(100.0, 500.0))
    assert _skips(fs) == {} and _census(fs, "fast_his_add") == 1 and _census(fs, "fast_add_kept") == 1
    assert _census(fs, "fast_add_took") == 0 and _census(fs, "kept_min_life") == 1 and not _cancels(v) and not _places(v)
    assert b["last_plan"]["fast_add"]["his_add_sh"] == 100.0 and b["last_plan"]["fast_add"]["branch"] == "kept"
    assert "take_band" not in b["last_plan"]
    asrc = inspect.getsource(ml._act)
    # E31: the two source pins here were the TAKE ARM's own gate and its grown-quantity
    # sizing (`if rules.MIRROR_FAST_ADD_REPLAN and not is_exit and not short:` and
    # `grew = rules.MIRROR_FAST_ADD_REPLAN and not short and _plan_grew_past_rest(p, leaves)`).
    # Both left _act with the arm. The pin they carried -- A SHORT BOOK'S ADD NEVER TAKES --
    # now holds for EVERY book by construction, which is strictly stronger: no arm of _act
    # can send a take at all, and `_place` refuses a non-GTC time in force by name
    assert "if rules.MIRROR_FAST_ADD_REPLAN and not is_exit and not short:" not in asrc
    assert "_plan_grew_past_rest(p, leaves)" not in asrc
    assert "take_on_add" not in asrc and "IMMEDIATE_OR_CANCEL" not in asrc
    for gone in ("_entry_take", "_exit_take", "_take_band", "_short_take_band"):
        assert not hasattr(ml, gone), gone
    assert '_mirror_stop("ioc_refused", ' in inspect.getsource(ml._place)
    # `_plan_grew_past_rest` stays a PURE helper with its own unit pins below (the record of
    # the rule the arm governed), with no reader left on the money path
    assert "_plan_grew_past_rest" not in asrc
    # the pure reading on a short book: the SELL row is the add leg; his SELL of the long token adds too
    t = types.SimpleNamespace(fast_open={7: {"side": SELL, "tif": "GTC", "state": "open", "kind": "increase",
                                             "order_id": "x", "placed_ts": NOW - 30}}, now=NOW)
    book = {"id": 7, "intent": "ORDER_INTENT_BUY_SHORT", "long_asset": M, "other_asset": N}
    assert ml._order_open_his_add(t, book, [_fill(M, "SELL", 20, 0.31, NOW - 5)]) is True
    assert ml._order_open_his_add(t, book, [_fill(M, "BUY", 20, 0.31, NOW - 5)]) is False, "a long-axis add reduces the short"


# ----------------------------------------------- (5) lane 3's test runs first


def test_e21_a_reducing_wake_with_an_entry_rest_is_lane_3s_order_open_his_exit_counted_and_nothing_cancelled():
    from tests import test_fill_x1_exit_band as x1
    x1.test_x1_the_fast_gate_names_order_open_his_exit_on_his_reducing_fill_with_an_entry_rest_standing_and_cancels_nothing()
    # his sale AND his add both after the rest: the reducing test wins (first), no status read
    p = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 100, 0.31, NOW - 5),
                     _fill(M, "BUY", 150, 0.31, NOW - 4)])
    b = p.add_book(ledger=0, last_plan={"kind": "increase", "reduce_ref": {"target": 300, "at": NOW - 100}})
    p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "order_open_his_exit"} and _census(fs, "order_open_his_exit") == 1
    assert _census(fs, "fast_his_add") == 0 and _status_calls(v) == [] and not _cancels(v)
    # the pure reading: a reducing fill after the placement makes the add test False whatever the adds
    t = types.SimpleNamespace(fast_open={b["id"]: p.orders[b["open_order_id"]]}, now=NOW + 1)
    assert ml._order_open_his_add(t, b, p.fills) is False


# --------------------------- (6) 347 frozen, 334's exit rest, the other rows


def test_e21_book_347_is_not_live_an_exit_rest_a_flatten_a_cover_a_placing_or_unknown_row_is_order_open():
    v = _Venue()
    # book 347's shape (h2225 1084 / 1088): frozen -> `not_live`, no read
    p = _pool()
    p.add_book(ledger=300, state="frozen", frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 100)
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "not_live"} and _status_calls(v) == []
    # book 334's shape (docs 46): an EXIT rest standing and an adding wake -> `order_open` (the row's side
    # is the reduce leg: no fills read, no status read)
    p2 = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "BUY", 50, 0.31, NOW - 5)])
    b2 = p2.add_book(ledger=300)
    p2.add_order(b2, side=SELL, kind="reduce", wire=0.55)
    v2 = _Venue()
    v2.rest("oid-1", "SELL", 0.55, 300)
    _walk({M: 300.0, N: 0.0})
    fs2 = _fast(p2, v2, http=_mkt(350.0))
    assert _skips(fs2) == {CID: "order_open"} and _census(fs2, "fast_order_open") == 1 and _status_calls(v2) == []
    # the pure reading on every other row shape
    fills = [_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "BUY", 50, 0.31, NOW - 5)]
    book = {"id": 7, "intent": "ORDER_INTENT_BUY_LONG", "long_asset": M, "other_asset": N}
    base = {"side": BUY, "tif": "GTC", "state": "open", "kind": "increase", "order_id": "oid-1", "placed_ts": NOW - 30}

    def _t(row, now=NOW):
        return types.SimpleNamespace(fast_open={7: row}, now=now)

    assert ml._order_open_his_add(_t(base), book, fills) is True
    for over in ({"kind": "flatten_paired", "side": SELL}, {"kind": "flatten_vanished", "side": SELL},
                 {"kind": "reduce", "side": SELL}, {"kind": "take", "tif": "IOC"}, {"tif": "IOC"},
                 {"state": "placing"}, {"state": "unknown"}, {"order_id": None}, {"order_id": ""},
                 {"placed_ts": None}, {"placed_ts": "soon"}, {"side": "x"}, {"kind": "reduce"}):
        assert ml._order_open_his_add(_t({**base, **over}), book, fills) is False, over
    assert ml._order_open_his_add(_t(base), {**book, "intent": None}, fills) is False, "intent unreadable"
    assert ml._order_open_his_add(_t(base), {**book, "intent": "junk"}, fills) is False
    assert ml._order_open_his_add(_t(base), book, None) is False and ml._order_open_his_add(_t(base), book, "x") is False
    assert ml._order_open_his_add(types.SimpleNamespace(fast_open={}, now=NOW), book, fills) is False
    assert ml._order_open_his_add(_t(base), book, [fills[0]]) is False, "no add after the placement"
    assert ml._order_open_his_add(_t(base), book, fills + [_fill(M, "SELL", 10, 0.31, NOW - 4)]) is False, "a reduce after it"
    # a rest placed AFTER this tick's clock is the full tick's to read (its clock stamped after the lock)
    assert ml._order_open_his_add(_t({**base, "placed_ts": NOW - 30}, now=NOW - 31), book, fills) is False
    assert ml._order_open_his_add(_t({**base, "placed_ts": NOW - 30}, now=NOW - 30), book, fills) is True


# ------------------------------------------------- (7) the backfilled add


def test_e21_a_backfilled_add_of_his_history_is_order_open_and_one_stamped_inside_late_fill_s_is_admitted():
    """docs 33's shape: a row of his HISTORY inserted now -- clocked after
    our rest, stamped hours before it -- is not an add he made since the
    rest (mi.adding_since reads 0): `order_open`. The same add stamped
    inside LATE_FILL_S is admitted and the plan reads no flow rise."""
    p = _pool()
    v = _Venue()
    b, o1 = _rested(p, v)
    p.fills.append(_fill(M, "BUY", 2000.0, 0.33, NOW - 4000, detected_at=NOW + 10))
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs = _fast(p, v, now=NOW + 20, http=_mkt(2300.0))
    assert _skips(fs) == {CID: "order_open"} and _census(fs, "fast_order_open") == 1 and _status_calls(v) == []
    assert mi.adding_since(p.fills, M, N, 1.0, NOW) == 0.0 and mi.LATE_FILL_S == 900.0
    # stamped inside the allowance (a late poll row, minutes): admitted
    p2 = _pool()
    v2 = _Venue()
    b2, o2 = _rested(p2, v2)
    p2.fills.append(_fill(M, "BUY", 50.0, 0.31, NOW - 100, detected_at=NOW + 10))
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs2 = _fast(p2, v2, now=NOW + 20, http=_mkt(350.0))
    assert _skips(fs2) == {} and _census(fs2, "fast_his_add") == 1 and _census(fs2, "fast_add_kept") == 1
    assert not b2["last_plan"].get("flow_fills_grew") and b2["last_plan"]["fast_add"]["his_add_sh"] == 50.0


# ------------------------------------------------------------- (8) the rails


def test_e21_the_rails_replace_capped_take_capped_over_room_the_switch_off_and_budget_spent(monkeypatch):
    cap = rules.MIRROR_MAX_REPLACES_PER_HOUR
    assert cap == 12
    # (a) twelve replaces in the hour: the re-plan past the floor is `replace_capped`, the rest stands
    p = _pool()
    v = _Venue()
    b = p.add_book(ledger=0)
    for _ in range(cap):
        p.add_order(b, state="cancelled", reason="take", tif="GTC", done_at=NOW - 100, order_id=None)
    st0 = _tick(p, v)
    o1 = b["open_order_id"]
    assert _census(st0, "rest_placed") == 1 and o1
    v.calls.clear()
    _add(p, 100, NOW + 50)
    _walk({M: 0.0, N: 0.0}, NOW + 52)
    fs = _fast(p, v, now=NOW + 60, http=_mkt(400.0))
    assert _skips(fs) == {} and _census(fs, "fast_his_add") == 1 and _census(fs, "replace_capped") == 1
    assert _census(fs, "fast_add_replaced") == 0 and not _cancels(v) and not _places(v)
    assert p.orders[o1]["state"] == "open" and b["open_order_id"] == o1
    assert b["last_plan"]["fast_add"]["branch"] == "replace_capped" and b["last_plan"]["rest_cause"] == "replace_capped"
    # (b) THE BUDGET WITH THE ASK AT HIS CENT. E31 RETIRED THE SECOND CAPPED REFUSAL.
    # Before this lane the ask arriving at his cent on a young rest went down the take arm,
    # and the hour's rail refused it there under its own name `take_capped` (the branch and
    # the rest_cause both read that word). There is no take arm, so there is no second
    # refusal: the hour's rail is ONE rail, `replace_capped`, and it bites on the replace the
    # grown quantity asks for. The wake is moved past MIRROR_REST_MIN_LIFE_S (20 s -> 60 s,
    # (a)'s own clock) so the replace is actually reached rather than held by the rest-life
    # floor first -- otherwise the rail would not be exercised at all. `take_capped` stays
    # DECLARED on CENSUS_KEYS and in _REST_STOOD_NAMES as the record of the rule it governed
    p2 = _pool()
    v2 = _Venue()
    b2 = p2.add_book(ledger=0)
    for _ in range(cap):
        p2.add_order(b2, state="cancelled", reason="take", tif="GTC", done_at=NOW - 100, order_id=None)
    _tick(p2, v2)
    o2 = b2["open_order_id"]
    v2.calls.clear()
    v2.ask = 0.31
    _add(p2, 100, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 52)
    fs2 = _fast(p2, v2, now=NOW + 60, http=_mkt(400.0))
    assert _skips(fs2) == {} and _census(fs2, "take_capped") == 0 and _census(fs2, "fast_add_took") == 0
    assert _census(fs2, "replace_capped") == 1, "one rail, under its own name"
    assert not _cancels(v2) and not _places(v2) and p2.orders[o2]["state"] == "open"
    assert p2.orders[o2]["wire"] == 0.31 and b2["open_order_id"] == o2, "the rest stands, at his cent"
    assert b2["last_plan"]["fast_add"]["branch"] == "replace_capped" and b2["last_plan"]["rest_cause"] == "replace_capped"
    assert '_fast_add_branch(plan, "take_capped", w)' not in inspect.getsource(ml._act)
    # (c) the room under a share at his cent after the cancel: `over_room`, nothing placed.
    # The wake moves 20 s -> 60 s for the same reason as (b): the take arm reached the room
    # read on a young rest, the replace arm reaches it past the rest-life floor. The refusal,
    # the cancel that precedes it and the empty placement list are unchanged
    p3 = _pool()
    v3 = _Venue()
    b3, o3 = _rested(p3, v3)
    monkeypatch.setattr(rules, "MIRROR_CLIP_USD", 0.15)
    v3.ask = 0.31
    _add(p3, 100, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 52)
    fs3 = _fast(p3, v3, now=NOW + 60, http=_mkt(400.0))
    assert _skips(fs3) == {} and _census(fs3, "over_room") == 1 and _census(fs3, "fast_add_took") == 0
    assert _cancels(v3) == [("cancel", "oid-1", SLUG)] and not _places(v3) and p3.orders[o3]["state"] == "cancelled"
    # E31: the branch word is `replaced`, not `over_room`. The take arm stamped `over_room`
    # itself; on the replace road the room is read inside _place_reserved, AFTER the branch
    # is stamped, so the census name is the one that carries the refusal
    assert b3["last_plan"]["fast_add"]["branch"] == "replaced" and b3["open_order_id"] is None
    assert b3["last_plan"]["replaced"] == "replace_qty"
    assert '_fast_add_branch(plan, "over_room", w)' not in inspect.getsource(ml._act)
    monkeypatch.setattr(rules, "MIRROR_CLIP_USD", 2500.0)
    # (d) the switch OFF: `order_open`, no status read, no venue call
    monkeypatch.setattr(rules, "MIRROR_FAST_ADD_REPLAN", False)
    p4 = _pool()
    v4 = _Venue()
    b4, o4 = _rested(p4, v4)
    _add(p4, 100, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs4 = _fast(p4, v4, now=NOW + 20, http=_mkt(400.0))
    assert _skips(fs4) == {CID: "order_open"} and v4.calls == [] and _census(fs4, "fast_order_open") == 1
    assert _census(fs4, "fast_his_add") == 0 and "fast_add" not in (b4["last_plan"] or {})
    monkeypatch.setattr(rules, "MIRROR_FAST_ADD_REPLAN", True)
    # (e) the venue budget at its cap: `budget_spent`, no status read
    p5 = _pool()
    v5 = _Venue()
    b5, o5 = _rested(p5, v5)
    _add(p5, 100, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    monkeypatch.setattr(ml, "_fast_calls", int(ml.VENUE_CALLS_PER_TICK))
    fs5 = _fast(p5, v5, now=NOW + 20, http=_mkt(400.0))
    assert _skips(fs5) == {CID: "budget_spent"} and v5.calls == []
    monkeypatch.setattr(ml, "_fast_calls", 0)


# ---------------------------------------------- (9) the blank status read


def test_e21_a_blank_status_read_is_fast_status_unread_with_the_row_untouched_and_no_freeze_the_full_tick_freezes_at_its_own_step_o(monkeypatch):
    """The one place the fast tick does NOT inherit the full tick's rule,
    by name: _reconcile_open freezes `order_state_unknown` on a blank
    read (worker 5163-5174), and a fast tick fires on every fill wake
    (FAST_TICK_MIN_S 2 s), so a venue blip on the status read would
    freeze books at WAKE cadence; the fast step O names
    `fast_status_unread` instead, writes nothing, freezes nothing, and
    the full tick's own step O applies the freeze a poll later exactly
    as today."""
    frozen = []
    orig = ml._freeze

    async def _spy(t, book, reason, detail=None):
        frozen.append(reason)
        return await orig(t, book, reason, detail)
    monkeypatch.setattr(ml, "_freeze", _spy)
    for blank in ("status_none", "status_raises"):
        p = _pool()
        v = _Venue()
        b, o1 = _rested(p, v)
        before_plan, before_writes = dict(b["last_plan"]), len(_state_writes(p))
        setattr(v, blank, True)
        _add(p, 100, NOW + 10)
        _walk({M: 0.0, N: 0.0}, NOW + 12)
        fs = _fast(p, v, now=NOW + 20, http=_mkt(400.0))
        assert _skips(fs) == {CID: "fast_status_unread"} and _census(fs, "fast_status_unread") == 1, blank
        assert _census(fs, "fast_his_add") == 0 and _census(fs, "order_state_unknown") == 0 and frozen == []
        assert len(_status_calls(v)) == 1 and not _cancels(v) and not _places(v)
        assert p.orders[o1]["state"] == "open" and b["state"] == "live" and b["open_order_id"] == o1
        assert len(_state_writes(p)) == before_writes and _freeze_writes(p) == [] and b["last_plan"] == before_plan
        # the full tick's own step O, the read still blank: `order_state_unknown`, byte-identical to today
        st = _tick(p, v, now=NOW + 30, http=_mkt(400.0))
        assert _census(st, "order_state_unknown") == 1 and frozen == ["order_state_unknown"] and b["state"] == "frozen"
        assert p.orders[o1]["state"] == "unknown"
        frozen.clear()
    # a status that carries no filled figure is unread too (never a guess)
    p = _pool()
    v = _Venue()
    b, o1 = _rested(p, v)
    v.orders["oid-1"]["filled_shares"] = None
    _add(p, 100, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs = _fast(p, v, now=NOW + 20, http=_mkt(400.0))
    assert _skips(fs) == {CID: "fast_status_unread"} and frozen == [] and p.orders[o1]["state"] == "open"
    # a terminal state on the read: `order_open` (the full tick's _finish_order books it)
    p = _pool()
    v = _Venue()
    b, o1 = _rested(p, v)
    v.orders["oid-1"]["state"] = "cancelled"
    _add(p, 100, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs = _fast(p, v, now=NOW + 20, http=_mkt(400.0))
    assert _skips(fs) == {CID: "order_open"} and _census(fs, "fast_order_open") == 1 and p.orders[o1]["state"] == "open"
    assert _state_writes(p)[len(_state_writes(p)):] == [] and frozen == []
    # the source: the fast step O never writes a state, never freezes, never books; one status read
    src = inspect.getsource(ml._fast_step_o) + inspect.getsource(ml._fast_book)
    for absent in ("_freeze(", "_SQL_ORDER_STATE", "_book_delta(", "_reconcile_open(", "_finish_order(", "nonterminal.add("):
        assert absent not in src, absent
    assert inspect.getsource(ml._fast_step_o).count("await _order_status(") == 1


# ------------------------------------------------------------ (10) the sizing


def test_e21_the_take_off_the_rest_sizes_at_the_plans_grown_quantity_through_the_room_and_an_exit_keeps_the_min(monkeypatch):
    """Rest 200 leaves 200, his add grows the plan to 260; the room worth
    230 shares clamps it; the switch OFF; the exit's and the cover's
    min(plan, leaves). On the FULL tick.

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10). The SUBJECT of this test
    -- the TAKE off the rest, sized at the plan's grown quantity -- is
    retired: `_entry_take` is deleted, `_act` has no take arm, `_place`
    refuses a non-GTC time in force by name, and `take_at_his_level` /
    `take_on_add` / `fast_add_took` are declared zeros. What the rule
    HALF that survives says is that THE ORDER IS SIZED AT THE PLAN'S GROWN
    QUANTITY THROUGH _room_qty, and under E31 that order is the REST --
    every add is re-scaled inside `_place_reserved` at the cent that goes
    out. So each world here now pins the rest where it pinned the IOC:

      ON      cancel + ONE post-only rest of 260 (was: IOC 260 at 0.31
              then a rest of 160 at 0.30)
      ROOM    the same, clamped by the clip (was: IOC 230 + rest 130)
      OFF     identical to ON -- MIRROR_FAST_ADD_REPLAN's only reader is
              now the FAST gate, so it cannot change a full tick at all
              (was: today's min(plan, leaves) = 200 and the word 'take')

    The cent moved too, and the OTHER way from the entry's: with the ask
    at 0.31 his own cent 0.31 WOULD CROSS, so the rest sits at the touch
    bound 0.32 - ... = ask - MAKER_TICK = 0.30 and the tick counts
    `maker_rest_at_touch`. And the clock moved: the wake is at NOW + 60,
    past rules.MIRROR_REST_MIN_LIFE_S, because the take arm reached the
    room read on a YOUNG rest and the replace arm reaches it past the
    rest-life floor -- at NOW + 20 the rest is simply kept (pinned below,
    so the floor is not silently skipped)."""
    # ON: the rest for the grown 260
    p = _pool(fills=[_fill(M, "BUY", 200, 0.31, NOW - 3000)])
    v = _Venue()
    b, o1 = _rested(p, v, his=200.0)
    assert p.orders[o1]["qty"] == 200 and p.orders[o1]["wire"] == 0.31
    _add(p, 60, NOW + 10)
    v.ask, v.ioc_fill = 0.31, 100.0
    st = _tick(p, v, now=NOW + 60, http=_mkt(260.0))
    assert _census(st, "take_at_his_level") == 0 and _census(st, "fast_add_took") == 0
    assert _census(st, "rest_placed") == 1 and _census(st, "maker_rest_at_touch") == 1
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.30, 260, False, GTC_TIF)], "one rest, the grown quantity"
    assert [c[7] for c in _places(v)] == [True] and _cancels(v) == [("cancel", "oid-1", SLUG)]
    rest = _inserts(p)[-1]
    assert (rest[3], rest[5], rest[10], rest[11], rest[20], rest[22]) == ("increase", "GTC", 0.30, 260, "rest", False)
    assert not any(x[3] == "take" or x[5] == "IOC" for x in _inserts(p)), "no take row on any road"
    assert b["ledger_net"] == 0 and "fast_add" not in b["last_plan"], "a full tick's plan carries no fast_add"
    assert b["last_plan"]["maker"]["clause"] == "touch" and b["last_plan"]["maker"]["his_cent"] == 0.31
    assert b["last_plan"]["replaced"] == "replace_qty"
    # the rest-life floor still governs: the same world at NOW + 20 keeps the rest, sends nothing
    p0 = _pool(fills=[_fill(M, "BUY", 200, 0.31, NOW - 3000)])
    v0 = _Venue()
    b0, oo = _rested(p0, v0, his=200.0)
    _add(p0, 60, NOW + 10)
    v0.ask = 0.31
    st0 = _tick(p0, v0, now=NOW + 20, http=_mkt(260.0))
    assert _census(st0, "kept_min_life") == 1 and not _places(v0) and not _cancels(v0)
    assert p0.orders[oo]["state"] == "open" and p0.orders[oo]["qty"] == 200
    # the plan the rest is sized from is unchanged, and so is the hysteresis it reads
    assert rules.rest_decision(rules.OpenOrder(BUY, 0.31, 200, 200.0, NOW, "ORDER_INTENT_BUY_LONG"),
                               mi.Plan(BUY, 260, 0.31, "x"), NOW + 20, wire=0.31)[1]["add_pending"]["qty"] == 260
    assert ml._plan_grew_past_rest(mi.Plan(BUY, 260, 0.31, "x"), 200.0) is True
    # the hysteresis: 200 -> 203 is inside 2%, no growth (and rest_decision agrees: keep `same`)
    assert ml._plan_grew_past_rest(mi.Plan(BUY, 203, 0.31, "x"), 200.0) is False
    assert rules.rest_decision(rules.OpenOrder(BUY, 0.31, 200, 200.0, NOW, "ORDER_INTENT_BUY_LONG"),
                               mi.Plan(BUY, 203, 0.31, "x"), NOW + 20, wire=0.31) == ("keep", {"cause": "same"})
    assert ml._plan_grew_past_rest(mi.Plan(BUY, 201, 0.31, "x"), 200.0) is False, "a share under 2% of 201 (4.02)"
    assert ml._plan_grew_past_rest(mi.Plan(BUY, 30, 0.31, "x"), 29.0) is True, "a share, over 2% of 30 (0.6)"
    assert ml._plan_grew_past_rest(None, 200.0) is False and ml._plan_grew_past_rest(mi.Plan(BUY, 260, 0.31, "x"), None) is False
    # THE ROOM: a clip worth 230 shares at his cent 0.31 is $71.30, and the rest goes out at
    # the touch bound 0.30 -- so the room is read AT THE CENT THAT GOES OUT and buys 237, not
    # 230. That is E2's invariant kept under E31: `_place_reserved` re-scales the add through
    # _room_qty after the touch-bound re-read, never at the cent the plan was made on
    p2 = _pool(fills=[_fill(M, "BUY", 200, 0.31, NOW - 3000)])
    v2 = _Venue()
    b2, o2 = _rested(p2, v2, his=200.0)
    monkeypatch.setattr(rules, "MIRROR_CLIP_USD", round(230 * 0.31, 2))
    _add(p2, 60, NOW + 10)
    v2.ask, v2.ioc_fill = 0.31, 100.0
    st2 = _tick(p2, v2, now=NOW + 60, http=_mkt(260.0))
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.30, 237, False, GTC_TIF)]
    assert int(round(230 * 0.31, 2) / 0.30) == 237, "the clip divided by the cent that went out"
    assert _census(st2, "over_room") == 0 and _inserts(p2)[-1][20] == "rest"
    monkeypatch.setattr(rules, "MIRROR_CLIP_USD", 2500.0)
    # OFF: E31 leaves MIRROR_FAST_ADD_REPLAN one reader, the FAST gate, so a FULL tick is
    # byte for byte the ON tick -- where before this lane OFF sent min(plan, leaves) = 200
    # under the word 'take'
    monkeypatch.setattr(rules, "MIRROR_FAST_ADD_REPLAN", False)
    p3 = _pool(fills=[_fill(M, "BUY", 200, 0.31, NOW - 3000)])
    v3 = _Venue()
    b3, o3 = _rested(p3, v3, his=200.0)
    _add(p3, 60, NOW + 10)
    v3.ask, v3.ioc_fill = 0.31, 100.0
    _tick(p3, v3, now=NOW + 60, http=_mkt(260.0))
    assert [c[1:6] for c in _places(v3)] == [(SLUG, 0.30, 260, False, GTC_TIF)]
    assert _inserts(p3)[-1][20] == "rest" and _inserts(p3)[-1][11] == 260
    monkeypatch.setattr(rules, "MIRROR_FAST_ADD_REPLAN", True)
    # THE SOURCE. Every `left = ...` line pinned here lived inside a take arm and left with
    # it, and so did the switch's second and third readers. What stands in their place is the
    # ONE sizing every order now goes through, in _place_reserved, at the wire that goes out
    asrc = inspect.getsource(ml._act)
    for gone in ('left = int(min(p.qty, max(0.0, float(o["qty"]) - float(o.get("booked_filled") or 0.0))))',
                 'left = _sell_qty(book, int(min(p.qty, max(0.0, float(o["qty"])',
                 'left = _cover_qty(book, int(min(p.qty, max(0.0, float(o["qty"])',
                 "left = _room_qty(t, int(p.qty), take_lvl, intent)",
                 "if rules.MIRROR_FAST_ADD_REPLAN and not is_exit and not short:",
                 "take_lvl", "_entry_take", "on_add"):
        assert gone not in asrc, gone
    assert not hasattr(ml, "_entry_take"), "E31: the entry take's wrapper is gone"
    psrc = inspect.getsource(ml._place_reserved)
    assert psrc.count("qty = _room_qty(t, int(qty), wire, intent)") == 1, "every add, re-scaled once"
    assert psrc.index("wire = await _rest_reread(") < psrc.index("qty = _room_qty(t, int(qty), wire, intent)"), \
        "the room is scaled at the cent that goes out, after the touch-bound re-read"
    from tests.test_e31_maker_only import _code
    assert psrc.count("on_add=bool(on_add)") == 0
    assert "is_take" not in _code(ml._place_reserved), "no code line reads a take flag; the paragraph names it"
    # the exit's and the cover's min(plan, leaves) survive on the rest roads they always had
    assert asrc.count("_sell_qty(book, p.qty)") == 1 and asrc.count("_cover_qty(book, p.qty)") == 1


# ------------------------------------------------ (11) the E9 pin unchanged


def test_e21_the_e9_order_open_pin_stands_and_the_same_fixture_with_an_add_after_the_rest_is_admitted():
    from tests import test_e9_fast_path as e9
    e9.test_e9_the_fast_tick_leaves_to_the_full_tick_by_name_a_frozen_book_an_open_order_no_walk_a_stale_walk_a_booked_fill()
    # E9's fixture (a rest standing, no adding fill after it): `order_open`, no cancel, no status read
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "order_open"} and _census(fs, "fast_order_open") == 1 and v.calls == []
    # the same fixture with his add AFTER the rest's placed_ts (NOW - 30): admitted
    p2 = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "BUY", 50, 0.31, NOW - 5)])
    b2 = p2.add_book(ledger=0)
    p2.add_order(b2)
    v2 = _Venue()
    v2.rest("oid-1")
    _walk()
    fs2 = _fast(p2, v2, http=_mkt(350.0))
    assert _skips(fs2) == {} and _census(fs2, "fast_his_add") == 1 and _census(fs2, "fast_add_kept") == 1
    assert len(_status_calls(v2)) == 1 and not _cancels(v2)


# --------------------------------------------------- (12) the decision word


def test_e21_order_decision_writes_take_on_add_on_an_adds_ioc_alone_and_only_for_on_add_exactly_true():
    od = rules.order_decision
    assert od("add", True, False, on_add=True) == "take_on_add"
    assert od("add", True, False, in_band=True, on_add=True) == "take_in_band", "the band's word wins"
    assert od("add", True, True, on_add=True) == "cover" and od("reduce", True, True, on_add=True) == "cover"
    assert od("reduce", True, False, on_add=True) == "take" and od("reduce", True, False, in_band=True, on_add=True) == "exit_take_in_band"
    assert od("add", False, False, on_add=True) == "rest" and od("reduce", False, False, on_add=True) == "exit_rest"
    assert od("add", True, False, on_add="yes") == "take" and od("add", True, False, on_add=1) == "take", "a truthy non-bool is not True"
    assert od("add", True, False, on_add=True, in_band="x") == "take", "a junk in_band is never the add's word"
    assert od(None, True, False, on_add=True) == "take" and od("x", True, False, on_add=True) == "take"
    # every default path is E18's / E14's / lane 3's
    assert od("add", True, False) == "take" and od("add", True, False, True) == "take_in_band"
    assert od("reduce", True, False) == "take" and od("add", False, False) == "rest" and od("reduce", False, False) == "exit_rest"
    assert od("add", True, True) == "cover" and od("reduce", True, True, True) == "cover_in_band"
    # the word is spelled once in the body and written by _place_reserved through order_decision alone
    body = inspect.getsource(od).split('"""')[2]
    assert body.count('"take_on_add"') == 1
    assert "take_on_add" not in inspect.getsource(ml._place_reserved).replace("'take_on_add'", "")
    # 059's comment stands as written (the list it names is restated in docs section 54)
    mig = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "take_on_add" not in mig and "'take_in_band' is\n-- reserved" in mig
    # re-pinned 2026-09-12: 062 now exists and is run 83's observability, not
    # this lane's. The guard is rewritten to check what it actually means -- no
    # migration belongs to THIS wave -- which is stronger than "062 is absent"
    # and does not need re-pinning every time a later lane adds a file.
    migs = sorted(x.name for x in (ROOT / "backend" / "migrations").glob("*.sql"))
    assert not [m for m in migs
                if "on_add" in m or "fast_add" in m or "replan" in m], \
        "no migration in this wave"
    assert "062_rn1_observability.sql" in migs, "062 is run 83's, not this lane's"


# --------------------------------- (13) the census, the emit sites, the docs


def test_e21_the_census_place_new_stats_the_emit_sites_the_switch_the_untouched_functions_and_no_knob():
    keys = ml.CENSUS_KEYS
    # E23 (FILL lane 23, six names) landed after this lane and sits between these six and the key (-19:-13 -> -25:-19)
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-25:-19 -> -29:-23, -26 / -27 -> -30 / -31)
    assert keys[-53:-47] == NEW_NAMES
    # FILL lane 16 (one name, turn_woke_fast) landed first and sits between lane 11's one and these six
    assert keys[-54] == "turn_woke_fast" and keys[-55] == "cand_market_closed_db"
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert len(set(keys)) == len(keys) and all(ml._new_stats()["census"][k] == 0 for k in NEW_NAMES)
    assert all(k not in ml._INTEG_CENSUS_KEYS for k in NEW_NAMES)
    src = inspect.getsource(ml)
    assert src.count('_mirror_stop("fast_order_open")') == 1 and '_mirror_stop("fast_order_open")' in inspect.getsource(ml._fast_skip)
    assert src.count('_mirror_stop("fast_his_add", fresh.get("whale"))') == 1
    assert src.count('_mirror_stop("fast_status_unread", fresh.get("whale"))') == 1
    assert ml._FAST_ADD_COUNTED == {"kept": "fast_add_kept", "replaced": "fast_add_replaced", "took": "fast_add_took"}
    assert "_mirror_stop(name, whale)" in inspect.getsource(ml._fast_add_branch)
    for name in ("fast_add_kept", "fast_add_replaced", "fast_add_took"):
        assert src.count(f'"{name}"') == 2, name        # CENSUS_KEYS and _FAST_ADD_COUNTED
    # THE BRANCH SITES IN _act, RE-PINNED AT E31. Every site that belonged to a take arm
    # left with the arm; every site on a rest road stands:
    #   kept          3 -> 2   the exit rest's arm and the cover rest's arm merged into one
    #                          `priced_exit` arm (both now hold the same maker rest at his
    #                          cent); the entry's keep is the other
    #   took          1 -> 0   there is no take to take
    #   take_capped   1 -> 0   the take's own capped refusal; `replace_capped` is now the
    #                          hour's only rail (pinned end to end in the rails test)
    #   over_room     1 -> 0   the take arm's own room refusal; the rest's room is read in
    #                          _place_reserved, after the branch is stamped
    #   named         2 -> 1   the take's cancel-outcome arm went with the take
    #   replaced / replace_capped / decision   unmoved
    asrc = inspect.getsource(ml._act)
    assert asrc.count('_fast_add_branch(plan, "kept", w)') == 2 and '_fast_add_branch(plan, "took", w)' not in asrc
    assert asrc.count('_fast_add_branch(plan, "replaced", w)') == 1 and '_fast_add_branch(plan, "take_capped", w)' not in asrc
    assert asrc.count('_fast_add_branch(plan, "replace_capped", w)') == 1 and '_fast_add_branch(plan, "over_room", w)' not in asrc
    assert asrc.count("_fast_add_branch(plan, named, w)") == 1 and asrc.count("_fast_add_branch(plan, decision, w)") == 1
    # `fast_add_took` keeps its place in _FAST_ADD_COUNTED and on CENSUS_KEYS as a DECLARED
    # ZERO -- the record of the branch it counted -- with no writer left
    assert ml._new_stats()["census"]["fast_add_took"] == 0 and "fast_add_took" in ml.CENSUS_KEYS
    # lane 9's stamps stand: rest_cause at the keep returns and on the refusals that keep a rest
    assert asrc.count('plan["rest_cause"] = _rest_cause(book, plan, why)') == 2
    assert 'plan["rest_cause"] = "take_capped"' not in asrc and asrc.count('plan["rest_cause"] = "replace_capped"') == 1
    assert asrc.count('plan["rest_cause"] = "maker_no_cent"') == 1, "E31's own hold, at take_capped's site"
    # the switch: env_switch, default ON, read through the rules module alone; no numeric constant, no wait
    rsrc = inspect.getsource(rules)
    assert rsrc.count('MIRROR_FAST_ADD_REPLAN = env_switch("MIRROR_FAST_ADD_REPLAN", True)') == 1
    assert "MIRROR_FAST_ADD_REPLAN" in rules.__all__ and rules.env_switch("MIRROR_FAST_ADD_REPLAN", True) is True
    assert 'capped_env("MIRROR_FAST' not in rsrc and 'min_wait_env("MIRROR_FAST' not in rsrc and "MIRROR_FAST_ADD" not in src.replace("rules.MIRROR_FAST_ADD_REPLAN", "")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#") and "rules.MIRROR_FAST_ADD_REPLAN" in ln
                     and ("if " in ln or "grew = " in ln))
    # E31: 3 -> 1. The switch had three readers -- the FAST GATE, the take's grown-quantity
    # SIZING and the take row's WORD (`take_on_add`). The last two lived inside the take arm
    # and left with it, so the gate is the only reader that survives and the rail can no
    # longer change what a FULL tick sends at all (pinned end to end in the sizing test).
    # Its default, its direction (env may only turn it OFF) and its shape are untouched
    assert code.count("rules.MIRROR_FAST_ADD_REPLAN") == 1, "the gate alone -- read at call time, nowhere else"
    assert "_order_open_his_add(t, book, fills)" in code, "and that reader IS the gate"
    assert inspect.getsource(ml._fast_gate).split('"""')[2].count("rules.MIRROR_FAST_ADD_REPLAN") == 1
    assert asrc.split('"""')[2].count("rules.MIRROR_FAST_ADD_REPLAN") == 0, "2 -> 0: _act reads no switch"
    for fn in (ml._fast_book, ml._fast_step_o, ml._place_reserved, ml._order_open_his_add, ml._fast_add_field):
        assert "MIRROR_FAST_ADD_REPLAN" not in inspect.getsource(fn), fn.__name__
    # the gate's order: lane 3's exit test first, the add test after it, the walk and fill clauses after both
    gsrc = inspect.getsource(ml._fast_gate)
    assert gsrc.index("_order_open_his_exit(t, book, fills)") < gsrc.index("_order_open_his_add(t, book, fills)") \
        < gsrc.index("t.fast_add.add(bid)") < gsrc.index('return "walk_stale"') < gsrc.index('return "fill_after_walk"')
    fsrc = inspect.getsource(ml._fast_book)
    assert fsrc.index("if bid not in t.fast_add:") < fsrc.index("await _fast_step_o(t, fresh)") < fsrc.index("await _tick_book(t, fresh)")
    assert "_cancel_and_settle" not in fsrc and "_place(" not in fsrc
    # the untouched functions and constants, on 219f140
    for name, (digest, fn) in UNTOUCHED.items():
        assert _sha(fn) == digest, name
    assert (rules.REST_MIN_LIFE_CENT_MOVE, mi.MIN_MOVE_FRAC, rules.MIRROR_REST_TTL_S, rules.MIRROR_REST_MIN_LIFE_S) == (0.02, 0.02, 600.0, 45.0)
    assert (ml.FAST_TICK_MAX, ml.FAST_TICK_MIN_S, ml.VENUE_CALLS_PER_TICK, rules.MIRROR_MAX_REPLACES_PER_HOUR) == (5, 2.0, 60, 12)
    assert (rules.MIRROR_TAKE_AFTER_S, rules.MIRROR_CLIP_USD) == (0.0, 2500.0)
    # E27 (FILL lane 27, 2026-09-09, owner order) re-pinned: the band's default is 0.02 (capped at 5% of the
    # cost per share); the rail's shape is lane 2's and this lane still touches neither
    assert 'MIRROR_TAKE_BAND = capped_env("MIRROR_TAKE_BAND", 0.02, floor=0.0)' in rsrc, "the band's rail untouched by this lane"
    assert "reason IN ('replace', 'take') AND tif IN ('GTC', 'GTD')" in ml._SQL_REPLACES
    assert '    if age < 0:\n        return "replace", {"cause": "future"}\n' in inspect.getsource(rules.rest_decision)
    assert "if (not _exit_or_flip(book, p, o)\n                    and await _requotes_this_hour(t, book) >= rules.MIRROR_MAX_REPLACES_PER_HOUR):" in asrc
    # lane 7's stamp and lane 11's pre-check stand
    tsrc = inspect.getsource(ml.tick_once)
    assert tsrc.index("async with _TICK_LOCK:") < tsrc.index("now = time.time() if now_ts is None else float(now_ts)")
    assert "cand_market_closed_db" in inspect.getsource(ml._tick_candidate)
    # the _Tick field, always empty on a full tick
    assert ml._Tick.__dataclass_fields__["fast_add"].default_factory is set
    assert "fast_add" not in ml._SKIP_CARRIED if hasattr(ml, "_SKIP_CARRIED") else True


def test_e21_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E21, the add's take \(2026-09-09, FILL lane 10\)", doc, re.M), "the section header"
    for k in NEW_NAMES + ("MIRROR_FAST_ADD_REPLAN", "take_on_add", "_order_open_his_add", "_fast_step_o", "fast_add",
                          "order_state_unknown", "fill_after_walk", "_room_qty", "test_e21_fast_add_replan.py",
                          "MIRROR_FAST_ADD_REPLAN=0", "382.28", "705.75", "12,914.02", "$0"):
        assert k in doc, k


def test_e21_every_name_is_emitted_here(monkeypatch):
    """The lane's six names, each driven once (the worker file's coverage
    read imports this)."""
    test_e21_book_760s_shape_kept_under_the_floor_then_replaced_through_the_band_then_took_at_the_grown_quantity(monkeypatch)
    test_e21_the_e9_order_open_pin_stands_and_the_same_fixture_with_an_add_after_the_rest_is_admitted()
    test_e21_a_blank_status_read_is_fast_status_unread_with_the_row_untouched_and_no_freeze_the_full_tick_freezes_at_its_own_step_o(monkeypatch)


# ------------------------------------------ the review's deltas (FILL_L10_review.md)


def test_e21_a_sibling_books_order_in_the_same_game_is_order_open_game_unread_and_the_full_tick_keeps_the_rest():
    """The review's CRITICAL-1. The fast tick makes step O for the admitted
    book ALONE, so a SIBLING book of the same game with an order standing
    reads the game's room UNREADABLE (_resting_add_usd None ->
    _game_exposure None -> rules.game_room 0 -> the target clamped to the
    ledger, `game_unreadable`), and _act answered the admitted book's plan
    by CANCELLING its rest under `on_target` -- a cancel the full tick,
    whose step O reads every sibling, never makes (books 825 / 829 are one
    game, lib-flu-cpa-2026-09-08: h2225 866 / 874). Under the gate's
    sibling clause the wake is refused `order_open_game_unread`: no status
    read, no cancel, no plan; the full tick reads the sibling's rest ($40)
    and keeps the admitted rest `min_life`."""
    p = _pool()
    v = _Venue()
    b, o1 = _rested(p, v)
    b2 = p.add_book(ledger=0, condition_id="cid-2", us_market_slug="slug-2")
    p.add_order(b2, order_id="oid-2", wire=0.40, qty=100)
    v.rest("oid-2", price=0.40, qty=100, slug="slug-2")
    v.calls.clear()
    assert ml._game_key_of(b) == ml._game_key_of(b2)
    before = dict(b["last_plan"])
    _add(p, 97, NOW + 10)
    _walk({M: 0.0, N: 0.0}, NOW + 12)
    fs = _fast(p, v, now=NOW + 20, http=_mkt(397.0))
    assert _skips(fs) == {CID: "order_open_game_unread"} and _census(fs, "fast_tick_skipped") == 1
    assert _census(fs, "fast_order_open") == 0 and _census(fs, "fast_his_add") == 0 and _census(fs, "game_unreadable") == 0
    assert _status_calls(v) == [] and not _cancels(v) and not _places(v) and v.calls == []
    assert p.orders[o1]["state"] == "open" and b["open_order_id"] == o1 and b["last_plan"] == before
    # the full tick on the same world: step O reads the sibling's rest, the game is readable, the rest is KEPT
    st = _tick(p, v, now=NOW + 30, http=_mkt(397.0))
    assert _census(st, "kept_min_life") == 1 and _census(st, "open_order_pending") >= 1 and _census(st, "on_target") == 0
    assert p.orders[o1]["state"] == "open" and b["open_order_id"] == o1 and b["last_plan"]["rest_cause"] == "min_life"
    assert b["last_plan"]["game_exposure"] == 40.0 and b["last_plan"].get("game_cap") is None, "the sibling's $40 rest read, the room 2460 not binding"
    # the pure reading
    t = types.SimpleNamespace(game_books={("game", "g"): [{"id": 7}, {"id": 8}]}, nonterminal={7})
    assert ml._game_siblings_readable(t, {"id": 7, "game_key": "g"}) is True, "the admitted book itself may be non-terminal"
    t.nonterminal = {7, 8}
    assert ml._game_siblings_readable(t, {"id": 7, "game_key": "g"}) is False, "a sibling's order this tick did not read"
    assert ml._game_siblings_readable(t, {"id": 7, "game_key": None}) is True, "a book with no game key is its own game"
    assert ml._game_siblings_readable(types.SimpleNamespace(nonterminal={8}), {"id": 7, "game_key": "g"}) is False, "unreadable"
    # the gate's order: lane 3's exit test, the add test, the sibling test, t.fast_add, the walk, the fill
    gsrc = inspect.getsource(ml._fast_gate)
    assert gsrc.index("_order_open_his_add(t, book, fills)") < gsrc.index("_game_siblings_readable(t, book)") \
        < gsrc.index("t.fast_add.add(bid)") < gsrc.index('return "walk_stale"')


def test_e21_the_fast_step_o_reads_a_figureless_status_as_unread_and_an_open_one_takes_the_book_off_nonterminal(monkeypatch):
    """The review's surviving mutants M09 / M11. A status the venue
    ANSWERED without a filled figure (not a raising read: the worker
    file's fake raises inside its own normaliser on a None figure, so the
    lane's 'no filled figure' case was a blank read) is `fast_status_unread`
    with the book untouched; an open status with no new fill takes THIS
    book alone off t.nonterminal and puts the row on t.open_by_book."""
    row = {"id": 701, "order_id": "oid-1", "booked_filled": 0.0, "qty": 300, "wire": 0.30, "side": BUY}
    fresh = {"id": 7, "open_order_id": 701, "whale": "rn1"}
    reads, statuses = [], []

    async def _st(t, oid):
        reads.append(oid)
        return statuses.pop(0)

    def _t():
        return types.SimpleNamespace(fast_open={7: row}, open_by_book={}, nonterminal={7, 9})
    monkeypatch.setattr(ml, "_order_status", _st)
    statuses[:] = [{"state": "open", "leaves": 300.0}]
    t = _t()
    assert _run(ml._fast_step_o(t, fresh)) == "fast_status_unread"
    assert t.nonterminal == {7, 9} and t.open_by_book == {} and reads == ["oid-1"]
    statuses[:] = [{"state": "open", "filled_shares": 0.0, "leaves": 300.0}]
    t = _t()
    assert _run(ml._fast_step_o(t, fresh)) is None
    assert t.nonterminal == {9} and t.open_by_book[7] == (row, {"state": "open", "filled_shares": 0.0, "leaves": 300.0})
    statuses[:] = [None]
    t = _t()
    assert _run(ml._fast_step_o(t, fresh)) == "fast_status_unread" and t.nonterminal == {7, 9} and t.open_by_book == {}


def test_e21_plan_grew_past_rest_agrees_with_rest_decision_at_the_hysteresis_boundary():
    """The review's surviving mutant M14: a share that is EXACTLY
    MIN_MOVE_FRAC of the plan (49 -> 50) is inside rest_decision's
    hysteresis (diff <= MIN_MOVE_FRAC * q: keep `same`, no add_pending), so
    the take's word must not read it as grown."""
    oo = rules.OpenOrder(BUY, 0.30, 49, 49.0, NOW, "ORDER_INTENT_BUY_LONG")
    assert rules.rest_decision(oo, mi.Plan(BUY, 50, 0.31, "x"), NOW + 20, wire=0.30) == ("keep", {"cause": "same"})
    assert ml._plan_grew_past_rest(mi.Plan(BUY, 50, 0.31, "x"), 49.0) is False
    assert rules.rest_decision(oo, mi.Plan(BUY, 51, 0.31, "x"), NOW + 20, wire=0.30)[1]["add_pending"]["qty"] == 51
    assert ml._plan_grew_past_rest(mi.Plan(BUY, 51, 0.31, "x"), 49.0) is True
