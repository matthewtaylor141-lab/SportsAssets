"""E22 (2026-09-09, FILL lane 22): a lost placement the venue filled AFTER
le._LOST_FILL_WINDOW_S is adopted from the trade log when the venue
position proves it, so a frozen placement_lost book with a 'lost' row
does not stay frozen for the market's life.

The rows (hard2/book_863_0257.txt): book 863 aec-itfme-ryotan-naohon,
ORDER_INTENT_BUY_SHORT, opened 01:42:52 (row 4); its orders (rows 9-12):
4952 increase SELL_LONG GTC 2 @0.3 cancelled `replace` 01:42:52 ->
01:44:01, 4953 SELL_LONG 2 @0.29 filled 0.2900 01:44:01 -> 01:45:13 (the
ledger's -2), 4961 SELL_LONG 9 @0.36 cancelled `replace` 01:46:37 ->
01:47:37, 4965 SELL_LONG GTC 28 @0.3 state lost, reason order_lost,
placed 01:47:37, done 02:07:51; the book frozen placement_lost at
01:49:22 with ledger -2, venue -30, manual 0, registered 0, his_net
-1168.6, target -116, frozen_exit {held: frozen_no_his_exit, target:
-116, venue_own: -30} (hard2/frozen_0257.txt row 4); the shadow's verdict
every 38-66 s (30 verdicts) from 02:34:58 to 02:56:56 "frozen: venue and
ledger disagree" with venue -30 / ledger -2 (book_863_0257 rows 58-87).
The venue's -30 is the booked -2 plus the lost row's 28: the rest filled
(WHEN is in no row: the by-order read named nothing on any tick inside
the window, and the venue's -30 is read as of 02:56:52), and nothing
re-read the log for a 'lost' row.

The fixture world is the worker suite's short world (his 100 long against
400 other; ORDER_INTENT_BUY_SHORT; the leg negative in ledger space) on
the E5 pool, with the book's lost row placed 4,000 s before the tick
(past the 1,200 s window) and the venue's trade log naming its fill at
placed + 2,600 s.
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
    BUY, CID, GTC_TIF, M, N, NOW, SELL, SHORT, SLUG, _NoClose, _Venue, _armed, _census, _fill,
    _his, _mkt, _places, _ratio_fills, _short_book, _shorts_on, _tick,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("lost_fill_adopted", "lost_fill_unread", "lost_fill_unexplained", "lost_fill_ambiguous")
PLACED = NOW - 4000.0                # 01:47:37 against a tick past 02:34: well past the 1,200 s window
FILLED_AT = PLACED + 2600.0          # the fills at "02:31", after the window
LOST_QTY = 28
WIRE = 0.30
# the functions the brief names as NOT touched, hashed on 82ebe77 (the tip
# this lane was built on): a change to any of them is not this lane's
UNTOUCHED = {
    "_reconcile_placing": "76ab1b2931ee0f33", "_trade_log_fills": "21cd27b43901a94e",
    "_mark_lost": "30a13c986990f19c", "_thaw": "62950633c3de6c96", "_thaw_verdict": "c23d51c1a5a7c1f4",
    "_thaw_agrees": "780a13557ddcfbce", "_frozen_exit": "ef478fabdfa2ccc0",
    "_find_lost_placement": "bc9c98e1af5b10eb", "_registered_shares": "bd2ccd54d6f79525",
    "_reconcile_lost_close": "64384e41775e0679",
}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _pool(**kw):
    """His net NEGATIVE on the fixture market (the worker suite's short
    world): 100 of the long token against 400 of the other."""
    kw.setdefault("fills", _his(100, other_size=400, other_px=0.72))
    kw.setdefault("snap", {M: 100.0, N: 400.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _E5Pool(**kw)


def _book_863(p, ledger=-2, lost=LOST_QTY, state="lost", order_id=None, placed=PLACED, **over):
    """Book 863's shape: a frozen placement_lost SHORT of |ledger| at
    0.29 with its lost SELL_LONG row of `lost` @0.30 placed at `placed`."""
    b = _short_book(p, ledger=ledger, avg=0.29, state="frozen", frozen_reason="placement_lost",
                    frozen_ts=NOW - 3900, **over)
    o = p.add_order(b, side=SELL, wire=WIRE, qty=lost, order_id=order_id, state=state,
                    placed_ts=placed, kind="increase",
                    # 4965's mark: done 02:07:51 = placed + the 1,200 s window (a 'lost' row carries it)
                    done_at=(placed + le._LOST_FILL_WINDOW_S) if state == "lost" else None,
                    reason="order_lost" if state == "lost" else None)
    return b, o


def _log_fill(oid="venue-x", qty=20.0, px=WIRE, ts=FILLED_AT, order_qty=float(LOST_QTY), order_price=WIRE):
    """One fill of the venue's trade log as pmus.recent_trades hands it:
    a SELL of the long token (the wire side of a short book's add)."""
    return {"qty": qty, "price": px, "side": "SELL", "ts": ts, "realized_pnl": 0.0,
            "order_id": oid, "order_qty": order_qty, "order_price": order_price}


def _trades_reads(v):
    """E22's OWN trade-log reads (by order, since placed - 30 s). E24 (FILL
    lane 24) reads the log for the desk's hand FIRST on every disagreement
    past the tolerance, over [the book's open - the skew, now]: those reads
    are _hand_reads, one per wait on the same residual, and never this
    lane's."""
    return [c for c in v.calls if c[0] == "trades" and c[2] != HAND_SINCE]


HAND_SINCE = NOW - 600 - le._ORPHAN_SKEW_S       # the fixture book opened NOW - 600 (_Pool._book_dict)


def _hand_reads(v):
    return [c for c in v.calls if c[0] == "trades" and c[2] == HAND_SINCE]


def _lost(b):
    return (b.get("last_plan") or {}).get("lost_fill")


def _venue(held=-30, trades=None, **kw):
    return _NoClose(held={SLUG: held}, bid=0.13, ask=0.14, trades=trades, **kw)


# --------------------------------------------------------- (1) book 863


def test_e22_book_863_the_lost_row_is_adopted_when_the_position_proves_it_and_the_book_thaws_next_tick(monkeypatch):
    """Ledger -2, venue -30, the lost SELL_LONG 28 @0.30, the log naming
    one order with two fills 20 + 8 @0.30 after the window -> adopted,
    the ledger -30 at avg 0.30, the row 'filled' with the adopt reason,
    lost_fill_adopted 1, the book still frozen this tick (the frozen
    exit held `frozen_fill_this_tick`); the next tick's own venue ==
    ledger rule thaws it (E5's one agreeing read for placement_lost) and
    it plans as a live book toward his net."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[_log_fill(qty=20.0), _log_fill(qty=8.0, ts=FILLED_AT + 5)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    oo = p.orders[o["id"]]
    assert (oo["order_id"], oo["state"], oo["booked_filled"], oo["avg_px"]) == ("venue-x", "filled", 28.0, 0.30)
    assert oo["reason"] == "booked from the trade log after the window"
    assert b["ledger_net"] == -30 and b["state"] == "frozen" and b["frozen_reason"] == "placement_lost"
    assert _census(st, "lost_fill_adopted") == 1 and _census(st, "filled_rest") == 1
    assert all(_census(st, k) == 0 for k in NEW_NAMES[1:])
    assert st["census"].get("thaw_held", 0) == 0 and not _places(v)
    assert _lost(b) == {"row": o["id"], "qty": 28, "delta": -28, "verdict": "adopted", "at": NOW, "order": "venue-x"}
    assert b["last_plan"]["lost_fill_at"] == NOW and b["last_plan"]["kind"] == "frozen"
    assert _plan_exit(b)["held"] == "frozen_fill_this_tick"
    assert len(_trades_reads(v)) == 1 and _trades_reads(v)[0][1:] == (SLUG, PLACED - 30.0)
    assert len(_hand_reads(v)) == 1 and v.calls.index(_hand_reads(v)[0]) < v.calls.index(_trades_reads(v)[0])   # E24: the hand first
    rec = [x for x in ml._RECENT if x["what"] == "lost_fill_adopted"]
    assert rec and rec[-1]["book"] == b["id"] and rec[-1]["order"] == "venue-x" and rec[-1]["shares"] == 28.0 and rec[-1]["px"] == 0.30
    assert ("lost_fill_adopted", "rn1") in {tuple(k.split("|")) for k in ml.mirror_census_snapshot()}
    # the next tick: venue -30 == ledger -30, nothing open, no register -> thawed by the existing rule
    v2 = _venue(trades=[])
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert b["state"] == "live" and b["frozen_reason"] is None and _census(st2, "thaw_held") == 0
    assert [x["what"] for x in ml._RECENT if x["book"] == b["id"] and x["what"] == "thawed"] == ["thawed"]
    assert not _trades_reads(v2) and _census(st2, "lost_fill_adopted") == 0
    assert b["last_plan"]["kind"] != "frozen" and b["target"] == -300 and b["ledger_net"] == -30
    # planning as a live book: the increase toward his net, 270 = |-300| - |-30| (a BUY_SHORT at the
    # contract price; on 863 the same road is the increase toward -116)
    assert [c[1:] for c in _places(v2)] == [(SLUG, 0.28, 270, False, GTC_TIF, SHORT, True, None)]


def test_e22_the_window_is_widened_to_now_only_on_this_road_and_the_placing_road_is_byte_identical():
    src = inspect.getsource(ml._lost_fill_adopt)
    assert "(placed - le._ORPHAN_SKEW_S, t.now)" in src and "_trade_log_fills(t, o, placed - 30.0" in src
    assert "by_order" not in src, "BY ORDER: the default, exact quantity and wire"
    rsrc = inspect.getsource(ml._reconcile_placing)
    assert "placed + le._LOST_FILL_WINDOW_S))" in rsrc and "t.now)" not in rsrc.split("_trade_log_fills")[1].split(")")[0] + ")"
    assert le._LOST_FILL_WINDOW_S == 20 * 60.0
    lsrc = inspect.getsource(le)
    assert "Anything later at our cent on this shared account is the\n# owner's (round eight)." in lsrc


# ------------------------------------------- (2) (3) (4) (5): the log's holds


def test_e22_the_log_naming_nothing_is_unexplained_and_books_nothing(monkeypatch):
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert p.orders[o["id"]]["state"] == "lost" and p.orders[o["id"]]["order_id"] is None
    assert b["ledger_net"] == -2 and b["state"] == "frozen" and b["frozen_reason"] == "placement_lost"
    assert _census(st, "lost_fill_unexplained") == 1 and _census(st, "lost_fill_adopted") == 0
    assert _lost(b) == {"row": o["id"], "qty": 28, "delta": -28, "verdict": "unexplained", "at": NOW, "order": None}
    assert len(_trades_reads(v)) == 1 and _plan_exit(b)["held"] == "frozen_no_his_exit"


def test_e22_the_log_unreadable_is_unread_and_books_nothing(monkeypatch):
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades_raise=True)
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert p.orders[o["id"]]["state"] == "lost" and b["ledger_net"] == -2 and b["state"] == "frozen"
    assert _census(st, "lost_fill_unread") == 1 and _census(st, "lost_fill_adopted") == 0
    assert _lost(b)["verdict"] == "unread" and _lost(b)["delta"] == -28
    # the ledger ids unreadable: the same hold (a None from _trade_log_fills), the log read made
    ml._lost_fill_read_at.clear()                      # book ids repeat across this file's pools
    p2 = _pool()
    b2, o2 = _book_863(p2)
    p2.raise_on.append(("ml-ledger-ids", RuntimeError("down")))
    v2 = _venue(trades=[_log_fill(qty=28.0)])
    st2 = _tick(p2, v2, http=_mkt(100.0, 400.0))
    assert _census(st2, "lost_fill_unread") == 1 and b2["ledger_net"] == -2 and p2.orders[o2["id"]]["state"] == "lost"
    assert len(_trades_reads(v2)) == 1 and _lost(b2)["verdict"] == "unread"


def test_e22_two_order_ids_at_the_lost_size_is_ambiguous(monkeypatch):
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[_log_fill(oid="venue-x", qty=28.0), _log_fill(oid="venue-y", qty=28.0, ts=FILLED_AT + 9)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert p.orders[o["id"]]["state"] == "lost" and b["ledger_net"] == -2 and b["state"] == "frozen"
    assert _census(st, "lost_fill_ambiguous") == 1 and _census(st, "lost_fill_adopted") == 0
    assert _lost(b)["verdict"] == "ambiguous" and _lost(b)["order"] is None


def test_e22_fills_summing_to_a_different_size_is_ambiguous(monkeypatch):
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[_log_fill(qty=20.0)])                  # one order, 20 of the 28
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert p.orders[o["id"]]["state"] == "lost" and b["ledger_net"] == -2 and b["state"] == "frozen"
    assert _census(st, "lost_fill_ambiguous") == 1 and _census(st, "lost_fill_adopted") == 0
    assert _lost(b)["verdict"] == "ambiguous" and _lost(b)["order"] == "venue-x"
    # the by-order match is _trade_log_fills' own: a fill of another order size or another cent is not ours
    ml._lost_fill_read_at.clear()                      # book ids repeat across this file's pools
    p2 = _pool()
    b2, o2 = _book_863(p2)
    v2 = _venue(trades=[_log_fill(qty=28.0, order_qty=30.0), _log_fill(oid="venue-z", qty=28.0, order_price=0.31),
                        _log_fill(oid="venue-b", qty=28.0, ts=PLACED - 100.0)])   # before the skew
    st2 = _tick(p2, v2, http=_mkt(100.0, 400.0))
    assert _census(st2, "lost_fill_unexplained") == 1 and b2["ledger_net"] == -2


# --------------------------------- (6) (7): the delta that does not prove it


def test_e22_a_delta_that_is_not_the_lost_quantity_reads_no_log(monkeypatch):
    """Venue -20 against the ledger -2: a delta of 18, not 28 (a partial
    fill) -> lost_fill_unexplained with the delta on the plan and NO
    trade-log read (the read count 0)."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(held=-20, trades=[_log_fill(qty=28.0)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert not _trades_reads(v) and len(_hand_reads(v)) == 1        # E24's hand read alone; nothing of E22's
    assert _census(st, "lost_fill_unexplained") == 1 and _census(st, "lost_fill_adopted") == 0
    assert _lost(b) == {"row": o["id"], "qty": 28, "delta": -18, "verdict": "unexplained", "at": NOW, "order": None}
    assert "lost_fill_at" not in b["last_plan"] and b["ledger_net"] == -2 and b["state"] == "frozen"
    # a second lost row: the surplus is not ONE row's size
    p2 = _pool()
    b2, o2 = _book_863(p2)
    o3 = p2.add_order(b2, side=SELL, wire=0.31, qty=5, order_id=None, state="lost", placed_ts=PLACED + 60,
                      kind="increase", done_at=PLACED + 60 + le._LOST_FILL_WINDOW_S, reason="order_lost")
    v2 = _venue(held=-35, trades=[_log_fill(qty=28.0)])
    st2 = _tick(p2, v2, http=_mkt(100.0, 400.0))
    assert not _trades_reads(v2) and len(_hand_reads(v2)) == 1 and _census(st2, "lost_fill_unexplained") == 1
    assert _lost(b2) == {"row": None, "qty": 33, "delta": -33, "verdict": "unexplained", "at": NOW, "order": None}
    assert p2.orders[o2["id"]]["state"] == "lost" and p2.orders[o3["id"]]["state"] == "lost" and b2["ledger_net"] == -2


def test_e22_the_delta_on_the_other_token_is_unexplained_never_a_booking(monkeypatch):
    """A short of 100 with the lost SELL of 28 and the venue at -72: the
    venue holds 28 MORE of the long token than the ledger (+28 over
    -100), not the SELL row's fill on its side -> unexplained, no log
    read, nothing booked. On 863's own numbers (-2 + 28 = +26) the sign
    flips and E20's wrong_sign_hold names it first: no read, no
    booking, this lane never reached."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p, ledger=-100)
    v = _venue(held=-72, trades=[_log_fill(qty=28.0)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert not _trades_reads(v) and len(_hand_reads(v)) == 1        # E24's hand read alone; nothing of E22's
    assert _census(st, "lost_fill_unexplained") == 1 and _census(st, "lost_fill_adopted") == 0
    assert _lost(b) == {"row": o["id"], "qty": 28, "delta": 28, "verdict": "unexplained", "at": NOW, "order": None}
    assert b["ledger_net"] == -100 and p.orders[o["id"]]["state"] == "lost" and b["state"] == "frozen"
    ml._hand_read_at.clear()                     # E24's memo: book ids repeat across this file's pools, the residual too (28)
    p2 = _pool()
    b2, o2 = _book_863(p2)
    v2 = _venue(held=26, trades=[_log_fill(qty=28.0)])
    st2 = _tick(p2, v2, http=_mkt(100.0, 400.0))
    assert not _trades_reads(v2) and len(_hand_reads(v2)) == 1 and all(_census(st2, k) == 0 for k in NEW_NAMES)
    assert b2["last_plan"]["wrong_sign_hold"]["venue"] == 26 and b2["ledger_net"] == -2 and "lost_fill" not in b2["last_plan"]
    assert p2.orders[o2["id"]]["state"] == "lost" and b2["frozen_reason"] == "placement_lost"
    # the pure rule on both sides: a SELL row's surplus is negative, a BUY row's positive
    assert ml._lost_fill_delta({"qty": 28, "side": SELL}, -30, -2, 0.0) == (-28, -28)
    assert ml._lost_fill_delta({"qty": 28, "side": BUY}, 30, 2, 0.0) == (28, 28)
    assert ml._lost_fill_delta({"qty": 28, "side": BUY}, -30, -2, 0.0) == (-28, 28)
    assert ml._lost_fill_delta({"qty": 300, "side": BUY}, 600, 300, 0.0) == (300, 300)
    assert ml._lost_fill_delta({"qty": 300, "side": BUY}, 600, 300, 100.0) == (200, 300)


# -------------------------------------------------------- (8) the memo


def test_e22_the_memo_bounds_the_read_to_one_per_wait_and_the_wait_only_lengthens(monkeypatch):
    """Two ticks 40 s apart -> one read; 300 s later -> a second; the
    memo rides the plan (`lost_fill_at`) and the process memo; the
    constant is a min_wait_env (the environment may only LENGTHEN it)."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert len(_trades_reads(v)) == 1 and _census(st, "lost_fill_unexplained") == 1
    assert len(_hand_reads(v)) == 1                                  # E24: the hand's read, on the same wait
    assert b["last_plan"]["lost_fill_at"] == NOW
    st2 = _tick(p, v, now=NOW + 40, http=_mkt(100.0, 400.0))
    assert len(_trades_reads(v)) == 1 and len(_hand_reads(v)) == 1, "inside the wait: no second read"
    assert _census(st2, "lost_fill_unexplained") == 0 and all(_census(st2, k) == 0 for k in NEW_NAMES)
    assert b["last_plan"]["lost_fill_at"] == NOW and _lost(b)["at"] == NOW, "the memo and the last verdict carried"
    assert b["state"] == "frozen" and b["ledger_net"] == -2
    st3 = _tick(p, v, now=NOW + 300, http=_mkt(100.0, 400.0))
    assert len(_trades_reads(v)) == 2 and _census(st3, "lost_fill_unexplained") == 1
    assert len(_hand_reads(v)) == 2
    assert b["last_plan"]["lost_fill_at"] == NOW + 300
    # the process memo covers a plan that lost the clock (a quiet skip carries _SKIP_CARRIED alone)
    b["last_plan"] = {k: v_ for k, v_ in b["last_plan"].items() if k not in ("lost_fill_at", "lost_fill", "hand")}
    _tick(p, v, now=NOW + 340, http=_mkt(100.0, 400.0))
    assert len(_trades_reads(v)) == 2 and len(_hand_reads(v)) == 2
    # the plan memo covers a restart (the process memo empty)
    ml._lost_fill_read_at.clear()
    ml._hand_read_at.clear()
    _tick(p, v, now=NOW + 380, http=_mkt(100.0, 400.0))
    assert len(_trades_reads(v)) == 2 and len(_hand_reads(v)) == 2 and b["last_plan"]["lost_fill_at"] == NOW + 300
    # the wait lengthened: 40 s ticks never read again inside it
    monkeypatch.setattr(rules, "MIRROR_LOST_FILL_REREAD_S", 900.0)
    _tick(p, v, now=NOW + 620, http=_mkt(100.0, 400.0))
    assert len(_trades_reads(v)) == 2 and len(_hand_reads(v)) == 2
    _tick(p, v, now=NOW + 1200, http=_mkt(100.0, 400.0))
    assert len(_trades_reads(v)) == 3 and len(_hand_reads(v)) == 3
    # the rail: min_wait_env, default 300; the environment may only lengthen it
    assert 'MIRROR_LOST_FILL_REREAD_S = min_wait_env("MIRROR_LOST_FILL_REREAD_S", 300.0)' in inspect.getsource(rules)
    assert rules.min_wait_env("MIRROR_LOST_FILL_REREAD_S", 300.0) == 300.0
    monkeypatch.setenv("MIRROR_LOST_FILL_REREAD_S", "30")
    assert rules.min_wait_env("MIRROR_LOST_FILL_REREAD_S", 300.0) == 300.0
    monkeypatch.setenv("MIRROR_LOST_FILL_REREAD_S", "900")
    assert rules.min_wait_env("MIRROR_LOST_FILL_REREAD_S", 300.0) == 900.0
    monkeypatch.setenv("MIRROR_LOST_FILL_REREAD_S", "junk")
    assert rules.min_wait_env("MIRROR_LOST_FILL_REREAD_S", 300.0) == 300.0
    assert "MIRROR_LOST_FILL_REREAD_S" in rules.__all__
    # the worker reads it through rules alone (no env read of its own)
    assert '"MIRROR_LOST_FILL_REREAD_S"' not in inspect.getsource(ml)
    assert "rules.MIRROR_LOST_FILL_REREAD_S" in inspect.getsource(ml._lost_fill_adopt)
    # the read counts against the venue budget as every _venue_read does
    assert "await _venue_read(t, t.pmus.recent_trades" in inspect.getsource(ml._trade_log_fills)


def test_e22_the_memo_is_bounded_and_the_matching_delta_is_the_only_road_to_a_read(monkeypatch):
    _shorts_on(monkeypatch)
    for i in range(ml._LOST_FILL_MEMO_MAX + 5):
        ml._lost_fill_read_at[10_000 + i] = NOW - 10_000 + i
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[])
    _tick(p, v, http=_mkt(100.0, 400.0))
    assert len(_trades_reads(v)) == 1 and len(ml._lost_fill_read_at) <= ml._LOST_FILL_MEMO_MAX
    assert ml._lost_fill_read_at[b["id"]] == NOW, "the newest clocks are kept"


# ---------------------------------------- (9) the transition tick; (10) placing


def test_e22_never_on_the_transition_tick(monkeypatch):
    """A LIVE short book whose walk disagrees (venue -30 vs ledger -2)
    with a lost row standing freezes (E16's second read) on this tick
    and reads no log: the adoption runs only on a book that was frozen
    when its tick began (E5 review F1)."""
    _shorts_on(monkeypatch)
    p = _pool()
    b = _short_book(p, ledger=-2, avg=0.29)
    o = p.add_order(b, side=SELL, wire=WIRE, qty=LOST_QTY, order_id=None, state="lost", placed_ts=PLACED,
                    kind="increase", done_at=PLACED + le._LOST_FILL_WINDOW_S, reason="order_lost")
    v = _venue(trades=[_log_fill(qty=28.0)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "venue_ledger_suspect") == 1 and b["state"] == "live"
    assert not _trades_reads(v) and all(_census(st, k) == 0 for k in NEW_NAMES)
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert _plan_exit(b) == {"held": "transition_tick"} and not _trades_reads(v)
    assert all(_census(st2, k) == 0 for k in NEW_NAMES) and p.orders[o["id"]]["state"] == "lost"
    # and a book frozen under ANOTHER name never reads (placement_lost alone is this lane's)
    st3 = _tick(p, v, now=NOW + 60, http=_mkt(100.0, 400.0))
    assert not _trades_reads(v) and all(_census(st3, k) == 0 for k in NEW_NAMES) and b["ledger_net"] == -2
    # the call sits inside the was_frozen arm, before the frozen exit
    src = inspect.getsource(ml._tick_book)
    arm = src[src.index("if was_frozen:\n                # E22"):src.index('plan["frozen_exit"] = {"held": "transition_tick"}')]
    assert arm.index("await _lost_fill_adopt(t, book, r, ledger, registered, prior_plan, plan)") < arm.index("await _frozen_exit(")
    assert src.count("_lost_fill_adopt(") == 1


def test_e22_a_placing_row_takes_reconcile_placing_byte_for_byte(monkeypatch):
    """A 'placing' row without an id on a frozen placement_lost book is
    step O's (_reconcile_placing): the window is round eight's, the
    fill after it is NOT adopted, the row is marked lost past the
    window and only THEN is this lane's road open."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p, state="placing")
    v = _venue(trades=[_log_fill(qty=28.0)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    oo = p.orders[o["id"]]
    assert oo["state"] == "lost" and oo["order_id"] is None and oo["reason"] == "order_lost"
    assert _census(st, "order_lost") == 1 and b["ledger_net"] == -2 and b["state"] == "frozen"
    assert all(_census(st, k) == 0 for k in NEW_NAMES)
    assert len(_trades_reads(v)) == 1, "step O's own read, inside the window"
    # the next tick: the row is 'lost', the venue proves the fill, the widened window adopts it
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert oo["state"] == "filled" and oo["order_id"] == "venue-x" and b["ledger_net"] == -30
    assert _census(st2, "lost_fill_adopted") == 1 and len(_trades_reads(v)) == 2
    # a lost row WITH an id is not this lane's
    p3 = _pool()
    b3, o3 = _book_863(p3, order_id="had-an-id")
    v3 = _venue(trades=[_log_fill(qty=28.0)])
    st3 = _tick(p3, v3, http=_mkt(100.0, 400.0))
    assert not _trades_reads(v3) and all(_census(st3, k) == 0 for k in NEW_NAMES)
    assert p3.orders[o3["id"]]["state"] == "lost" and b3["ledger_net"] == -2 and "lost_fill" not in b3["last_plan"]


# ------------------------------------------------- (11) the register; the cached read


def test_e22_registered_shares_reduce_the_delta_and_a_cached_read_reads_nothing(monkeypatch):
    """E5's register on 863's shape: 28 registered on the short's side ->
    the delta 0 -> no read (the venue's surplus is explained by the
    operator, not the row); the fast tick's cached walk (t.walk_at None)
    -> nothing."""
    _shorts_on(monkeypatch)
    p = _pool(registered={SLUG: -28.0})
    b, o = _book_863(p)
    v = _venue(trades=[_log_fill(qty=28.0)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert not _trades_reads(v) and _census(st, "registered_books") == 1
    # the register explains the whole surplus: venue == ledger + registered, the freeze's own
    # branch is not entered, the book is E5's registered book (never thaws, F3) and this lane
    # never runs -- nothing counted, nothing on the plan
    assert all(_census(st, k) == 0 for k in NEW_NAMES) and "lost_fill" not in b["last_plan"]
    assert b["ledger_net"] == -2 and p.orders[o["id"]]["state"] == "lost" and b["state"] == "frozen"
    assert b["last_plan"]["registered_frozen"] is True and b["last_plan"]["registered"] == -28.0
    # a register that explains PART of it (8 of the 28): the delta 20 is not the row -> unexplained, no read
    ml._lost_fill_read_at.clear()
    p1 = _pool(registered={SLUG: -8.0})
    b1, o1 = _book_863(p1)
    v1 = _venue(trades=[_log_fill(qty=28.0)])
    st1 = _tick(p1, v1, http=_mkt(100.0, 400.0))
    assert not _trades_reads(v1) and len(_hand_reads(v1)) == 1 and _census(st1, "lost_fill_unexplained") == 1
    assert _lost(b1)["delta"] == -20 and b1["ledger_net"] == -2 and p1.orders[o1["id"]]["state"] == "lost"
    # the pure guard on a cached read: nothing read, nothing written, the memo carried
    p2 = _pool()
    b2, o2 = _book_863(p2)
    t = ml._Tick(pool=p2, pmus=_venue(trades=[_log_fill(qty=28.0)]), http=None, now=NOW, stats=ml._new_stats())
    t.walk_at = None
    r = ml._Reading("rn1", CID, SLUG, M, N, [], 100.0, 400.0, {}, None, False, False, False, None, None,
                    0.13, 0.14, 0.135, -30.0, 0.0, None, True)
    plan = {}
    out = asyncio.run(ml._lost_fill_adopt(t, b2, r, -2, 0.0, {"lost_fill_at": NOW - 10.0, "lost_fill": {"verdict": "unread"}}, plan))
    assert out is None and plan == {"lost_fill_at": NOW - 10.0, "lost_fill": {"verdict": "unread"}}
    assert not t.pmus.calls and p2.orders[o2["id"]]["state"] == "lost"
    # the rows unreadable: nothing this tick
    t.walk_at = NOW
    p2.raise_on.append(("ml-lost-rows", RuntimeError("down")))
    assert asyncio.run(ml._lost_fill_adopt(t, b2, r, -2, 0.0, {}, {})) is None and not t.pmus.calls


def test_e22_the_adopt_update_failing_is_logged_once_and_the_row_is_left(monkeypatch, caplog):
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    p.raise_on.append(("ml-order-adopt", RuntimeError("down")))
    v = _venue(trades=[_log_fill(qty=28.0)])
    with caplog.at_level("WARNING"):
        st = _tick(p, v, http=_mkt(100.0, 400.0))
        assert p.orders[o["id"]]["state"] == "lost" and p.orders[o["id"]]["order_id"] is None and b["ledger_net"] == -2
        assert all(_census(st, k) == 0 for k in NEW_NAMES) and _census(st, "write_failed") == 0
        assert _lost(b)["verdict"] == "adopt_write_failed" and _lost(b)["order"] == "venue-x"
        st2 = _tick(p, v, now=NOW + 300, http=_mkt(100.0, 400.0))
        assert len(_trades_reads(v)) == 2 and len(_hand_reads(v)) == 2 and p.orders[o["id"]]["state"] == "lost"
    assert sum("could not adopt venue order" in r.message for r in caplog.records) == 1
    # the write allowed again: the next matching tick adopts
    p.raise_on.clear()
    st3 = _tick(p, v, now=NOW + 600, http=_mkt(100.0, 400.0))
    assert _census(st3, "lost_fill_adopted") == 1 and b["ledger_net"] == -30


def test_e22_a_long_books_lost_buy_adopts_the_same_way(monkeypatch):
    """The long mirror image (E5's fixture: a placement_lost LONG of 300
    with a lost BUY of 300 @0.30; the venue 600): the log naming one
    order of 300 after the window -> adopted, the ledger 600, thawed
    next tick."""
    from tests.test_e5_frozen_exits import _frozen_long, _pool as _e5_pool
    p = _e5_pool(fills=_his(600), snap={M: 600.0, N: 0.0})
    b = _frozen_long(p)
    o = [x for x in p.orders.values() if x["state"] == "lost"][0]
    o["placed_ts"], o["done_at"] = PLACED, PLACED + le._LOST_FILL_WINDOW_S
    buy = {"qty": 300.0, "price": 0.30, "side": "BUY", "ts": FILLED_AT, "realized_pnl": 0.0,
           "order_id": "venue-l", "order_qty": 300.0, "order_price": 0.30}
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, trades=[buy])
    st = _tick(p, v, http=_mkt(600.0))
    assert _census(st, "lost_fill_adopted") == 1 and b["ledger_net"] == 600 and b["state"] == "frozen"
    assert p.orders[o["id"]]["state"] == "filled" and p.orders[o["id"]]["order_id"] == "venue-l"
    assert _lost(b)["delta"] == 300 and p.rows[b["standing_row_id"]]["filled_shares"] == 600.0
    st2 = _tick(p, _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, trades=[]), now=NOW + 30, http=_mkt(600.0))
    assert b["state"] == "live" and _census(st2, "thaw_held") == 0


# -------------------------------------------- (12) the E18 triple, the docs


def test_e22_the_census_place_the_emit_sites_the_untouched_functions_and_no_knob():
    keys = ml.CENSUS_KEYS
    # FILL lane 11 landed after this lane and placed its one name nearer the key (-17:-13 -> -18:-14); -- FILL lane 16 (one name) and E21 (FILL lane 10, six) landed first, so every index past this lane's six moved by seven more
    # E23 (FILL lane 23) its six after that one (-18:-14 -> -24:-20, -14 -> -20, -19 / -21 / -27 -> -25 / -27 / -33)
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-31:-27 -> -35:-31, -27 / -32 / -34 / -40 -> -31 / -36 / -38 / -44, -26 / -25 / -20 -> -30 / -29 / -24)
    assert keys[-35:-31] == NEW_NAMES and keys[-31] == "cand_market_closed_db"
    assert keys[-36] == "reopen_refused" and keys[-38] == "he_holds" and keys[-44] == "take_in_band"
    assert keys[-30] == "turn_woke_fast" and keys[-29] == "fast_order_open" and keys[-24] == "fast_status_unread"
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert all(ml._new_stats()["census"][k] == 0 for k in NEW_NAMES)
    assert all(k not in ml._INTEG_CENSUS_KEYS for k in NEW_NAMES)
    src = inspect.getsource(ml._lost_fill_adopt)
    for k in NEW_NAMES:
        assert inspect.getsource(ml).count(f'_mirror_stop("{k}", w)') == src.count(f'_mirror_stop("{k}", w)') >= 1, k
    assert src.count('_mirror_stop("lost_fill_adopted", w)') == 1
    assert src.count('_mirror_stop("lost_fill_unread", w)') == 1
    assert src.count('_mirror_stop("lost_fill_ambiguous", w)') == 2
    assert src.count('_mirror_stop("lost_fill_unexplained", w)') == 3
    # the adoption is _reconcile_placing's trade-log branch's: the same statement, the same booking calls
    assert "_SQL_ORDER_ADOPT, o[\"id\"], oid, reason" in src and "_book_delta(t, o, book, st, maker=True)" in src
    assert '_finish_order(t, o, book, st, "booked from the trade log after the window")' in src
    assert '_adopt_reason(o, "adopted from the trade log after the window")' in src
    assert "await _thaw(" not in src and "_SQL_BOOK_THAW" not in src, "the existing rule thaws it"
    assert 't.walk_at is None' in src and 'frozen_reason") != "placement_lost"' in src
    # the statement: the open-orders projection with the lost predicate, both shapes
    for s, base in ((ml._SQL_LOST_ROWS, ml._SQL_ORDERS_OPEN), (ml._SQL_LOST_ROWS_047, ml._SQL_ORDERS_OPEN_047)):
        assert ("WHERE o.book_id = $1 AND o.state = 'lost' AND o.order_id IS NULL AND o.done_at < to_timestamp($2)" in s
                and "ml-lost-rows" in s)
        assert s.split(" WHERE")[0] == base.split(" WHERE")[0]
    assert "o.intent" in ml._SQL_LOST_ROWS and "o.intent" not in ml._SQL_LOST_ROWS_047
    import pglast
    for s in (ml._SQL_LOST_ROWS, ml._SQL_LOST_ROWS_047):
        pglast.parse_sql(s)
    # no decision word written; 059's list stands
    assert "decision" not in src
    mig = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "lost_fill" not in mig
    # no knob that raises anything: one min_wait_env, no capped_env of the lane's own
    assert "capped_env(\"MIRROR_LOST" not in inspect.getsource(rules)
    assert inspect.getsource(rules).count('min_wait_env("MIRROR_LOST_FILL_REREAD_S"') == 1


def test_e22_the_untouched_functions_are_byte_identical_to_the_tip():
    """The brief's not-touched list, hashed on 82ebe77 (sha256[:16] of
    the source): a mismatch names the function that moved."""
    got = {name: _sha(getattr(ml, name)) for name in UNTOUCHED}
    assert got == UNTOUCHED, {k: (got[k], UNTOUCHED[k]) for k in got if got[k] != UNTOUCHED[k]}
    # the E5 register path and the reaper name nothing of the lane's
    for fn in (ml._registered_shares, ml._reconcile_lost_close, ml._reconcile_placing, ml._frozen_exit):
        assert "lost_fill" not in inspect.getsource(fn)
    # le's window and its paragraph stand; the reaper names nothing of the lane's
    lsrc = inspect.getsource(le)
    assert "_LOST_FILL_WINDOW_S = 20 * 60.0" in lsrc and "lost_fill_adopt" not in lsrc and "MIRROR_LOST_FILL" not in lsrc


def test_e22_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E22 -- a lost placement the venue filled after the window is adopted from the trade log when the venue position proves it \(2026-09-09, FILL lane 22\)", doc, re.M), "the E22 section header"
    for k in NEW_NAMES + ("MIRROR_LOST_FILL_REREAD_S", "lost_fill_at", "lost_fill", "_lost_fill_adopt", "_LOST_FILL_WINDOW_S",
                          "test_e22_lost_fill_adopt.py", "863", "4965", "transition tick", "placement_lost"):
        assert k in doc, k
