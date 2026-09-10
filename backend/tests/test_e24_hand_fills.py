"""E24 (2026-09-09, FILL lane 24): the desk's hand. A fill the venue marks
MANUAL on the account's OWN side of the trade is the desk's, not the
book's: a hand fill that ADDS to the book's side is the fourth explained
term of the venue-vs-ledger comparison (no freeze, never sold or bought
back, the frozen exit never trades it); a hand fill that REDUCES the
book's own shares is adopted as the book's exit at the hand's price (the
standing row, the ledger, realized); the wrong-sign hold reads the venue
net of the hand.

THE ROWS (hard2/vt_1156_1639.txt, manual_own_1648.tsv, book_1156_1624.txt,
h1621_db.txt). Book 1156 aec-wta-qinzhe-eleryb-2026-09-08, BUY_SHORT ratio
0.1: the mirror's own SELL_LONG rows 6852 (618 @0.32, filled 118.82) /
6934 (499) / 6960 (20) / 6968 (42) / 7019 (43) / 7039 (38) / 7053 (18) /
7054 (8) and the take 7024 (BUY 540 @0.37) put the ledger at -246.82 by
16:17:59Z (book_1156_1624 rows 9-17). At 16:17:34.997Z the venue's log
prints order CCZ08XN74SX4 SELL / intent BUY_SHORT / quantity 1802.11 / DAY
/ limit 0.36 / manualOrderIndicator MANUAL, isAggressor true, filled 1802
@0.46, cost 999.9994 (manual_own_1648 row 27): a $1,000 hand short on the
account. The walk read venue -2049 against ledger -246.82 (book_1156_1624
rows 78-79, judged 16:18:15 / 16:18:57), the book froze
venue_ledger_disagree at 16:18:36Z and the frozen exit -- sized on
venue_own -- placed 7060 (BUY 1802 GTC @0.38, replaced) then 7065 (BUY
1813 IOC filled @0.565, "sold 1566 shares past the ledger", rows 18-19;
wlog_frozenreduce_1630). The second hand short CCZ2TT1YCT3P (1980.22 DAY
@0.41, filled 1980 @0.51 at 16:20:55.487Z, row 28) was bought back the
same way (7076, 1980 IOC filled @0.52, row 22). Book 986's 802 surplus was
the hand buy CCX413N48SX0 (802.67 DAY @0.70: 713 @0.61 + 20 @0.60 + 25 +
25 @0.60 + 20 @0.59 and a 0 @0.59 print, rows 14-19), not E23's cancelled
rests. Book 1075 aec-wta-ireesc-mirbul (BUY_SHORT, ledger -3966, avg
0.8623, realized -189.36, state closing; h1621_db row 396) was covered
whole by hand at 15:32:36Z: CCYC06E66SJH BUY / SELL_SHORT 3966.24 IOC @0.99
filled 1000 + 1000 + 1000 + 109 + 157 + 700 @0.97 (rows 21-26). Book 1129
astatc-cs2-prv-furia map2 (BUY_LONG 4852 at 0.3116; h1621_db row 450) was
sold by hand 5038 @0.41 at 14:43:57Z (CCXMNEBPRSJH, row 20): the book's
4,852 and 186 more of the desk's own; book 1071 on the same market read
wrong_sign_hold (h1621_db paired-day row: OPPOSITE).

THE READ. The venue prints BOTH orders of a fill in full; the account's
own side is the aggressor order when isAggressor is true, else the
passive one (pmus.trade_own_order). A counterparty AGGRESSOR's order
arrives with its intent and its manual indicator: the 20 rows of
15:53-16:14Z on 1156's market read isAggressor false / the aggressor
order BUY BUY_LONG MANUAL / the passive order ours (BUY_SHORT AUTOMATIC),
so 'both sides carry an intent' is not a self-trade; `self` is the
counterparty order's id being one of the account's own in the same read
(or one trade id printed twice) -- no row of the day reads so.

The fixture worlds are the worker suite's (test_mirror_live_worker) on
the E5 pool: the SHORT world (his 100 long against 400 other, ratio 1.0)
for 1156 and 1075, the LONG world (his 28,765 at 0.56, ratio 0.1) for
986 and 1129. Every book opened NOW - 600; the hand fills are dated
NOW - 100.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import pathlib
import re
import time

import pytest

from sportsassets import live_executor as le
from sportsassets import pmus
from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e5_frozen_exits import _E5Pool, _plan_exit
from tests.test_e23_cancel_fill_adopt import ROWS_986, _book_986
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, GTC_TIF, M, N, NOW, SELL, SHORT, SLUG, _NoClose, _armed, _census, _his, _mkt, _places,
    _ratio_fills, _short_book, _shorts_on, _tick,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
YML = ROOT / ".github" / "workflows" / "render-ops.yml"
NEW_NAMES = ("hand_explained", "hand_adopted", "hand_unread", "hand_ambiguous")
OPENED = NOW - 600                              # _Pool._book_dict's opened_ts
HAND_SINCE = OPENED - le._ORPHAN_SKEW_S         # the hand read's window opens at the book's open - the skew
AT = NOW - 100                                  # the hand fills' clock
# the desk's orders of the day (manual_own_1648.tsv), verbatim ids and figures
SHORT_1 = ("CCZ08XN74SX4", 1802.0, 0.46, 1802.11, 0.36)     # row 27: 16:17:34.997Z
SHORT_2 = ("CCZ2TT1YCT3P", 1980.0, 0.51, 1980.22, 0.41)     # row 28: 16:20:55.487Z
COVER_1075 = ("CCYC06E66SJH", (1000.0, 1000.0, 1000.0, 109.0, 157.0, 700.0), 0.97, 3966.24, 0.99)
SALE_1129 = ("CCXMNEBPRSJH", 5038.0, 0.41, 5038.0, 0.31)
BUY_986 = ("CCX413N48SX0", ((713.0, 0.61), (20.0, 0.60), (25.0, 0.60), (25.0, 0.60), (20.0, 0.59), (0.0, 0.59)),
           802.67, 0.70)
# the functions this lane leaves byte-identical, hashed on acc672d
UNTOUCHED = {
    "_thaw": "62950633c3de6c96", "_thaw_verdict": "c23d51c1a5a7c1f4", "_thaw_agrees": "780a13557ddcfbce",
    "_reconcile_placing": "76ab1b2931ee0f33", "_mark_lost": "30a13c986990f19c",
    "_lost_fill_adopt": "61c67ae8946f3af4", "_disagree_fill_adopt": "4f1f100ae137d248",
    "_cancel_and_settle": "953ba5587d2ad423", "_cancel_final_figure": "0ac3ba6529adc4ed",
    "_registered_shares": "bd2ccd54d6f79525", "_agree_record": "8f21fb20866343b0",
    "_second_disagreeing_read": "3600354005f1a238", "_frozen_exit": "ef478fabdfa2ccc0",
    "_trade_log_fills": "21cd27b43901a94e", "_cancelled_log_fills": "245c0b604e9356ab",
}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- fixtures


def _short_pool(**kw):
    """The worker suite's short world on the E5 pool: his 100 long against 400 other."""
    kw.setdefault("fills", _his(100, other_size=400, other_px=0.72))
    kw.setdefault("snap", {M: 100.0, N: 400.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _E5Pool(**kw)


def _long_pool(**kw):
    """The long world: his 28,765 of the long token at 0.56 (E23's fixture)."""
    kw.setdefault("fills", _his(28765.0, 0.56))
    kw.setdefault("snap", {M: 28765.0, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _E5Pool(**kw)


def _hand(oid, side, intent, qty, px, ts=AT, order_qty=None, order_px=None, tif="DAY", manual=True,
          self_=False, aggressor=True):
    """One fill of the venue's trade log as pmus.recent_trades hands it
    since E24: the account's own side by isAggressor, `manual` its
    indicator, the own order's figures beside E22's keys."""
    return {"qty": qty, "price": px, "side": side, "ts": ts, "realized_pnl": 0.0,
            "order_id": oid, "order_qty": order_qty, "order_price": order_px, "order_tif": tif,
            "aggressor": aggressor, "manual": manual, "own_order_id": oid, "own_side": side,
            "own_intent": intent, "own_qty": order_qty, "own_price": order_px, "own_tif": tif, "self": self_}


def _short_fill(spec=SHORT_1, ts=AT, **kw):
    oid, qty, px, oq, opx = spec
    return _hand(oid, "SELL", "BUY_SHORT", qty, px, ts=ts, order_qty=oq, order_px=opx, **kw)


def _cover_fills(spec=COVER_1075, ts=AT):
    oid, qtys, px, oq, opx = spec
    return [_hand(oid, "BUY", "SELL_SHORT", q, px, ts=ts + i, order_qty=oq, order_px=opx,
                  tif="IMMEDIATE_OR_CANCEL") for i, q in enumerate(qtys)]


def _sale_1129(ts=AT):
    oid, qty, px, oq, opx = SALE_1129
    return _hand(oid, "SELL", "SELL_LONG", qty, px, ts=ts, order_qty=oq, order_px=opx, tif="IMMEDIATE_OR_CANCEL")


def _buy_986(ts=AT):
    oid, fills, oq, opx = BUY_986
    return [_hand(oid, "BUY", "BUY_LONG", q, px, ts=ts + i, order_qty=oq, order_px=opx) for i, (q, px) in enumerate(fills)]


def _venue(held, trades=None, bid=0.13, ask=0.14, **kw):
    return _NoClose(held={SLUG: held}, bid=bid, ask=ask, trades=trades, **kw)


def _reads(v):
    return [c for c in v.calls if c[0] == "trades"]


def _hand_reads(v):
    return [c for c in v.calls if c[0] == "trades" and c[2] == HAND_SINCE]


def _hand_of(b):
    return (b.get("last_plan") or {}).get("hand")


def _record(p, b):
    return p.state.get(f"mirror_hand:{b['id']}")


def _recent(what, book=None):
    return [x for x in ml._RECENT if x["what"] == what and (book is None or x["book"] == book)]


def _book_1156(p, ledger=-247, **over):
    """Book 1156's shape: a live BUY_SHORT of |ledger| at 0.33 (7053's fill cent is 0.4550 and the
    row's average is not in the read; 0.33 is 6934's, the largest fill)."""
    return _short_book(p, ledger=ledger, avg=0.33, **over)


# ------------------------------------------------ (1) book 1156: the hand short explained


def test_e24_book_1156_the_hand_short_explains_the_venue_and_the_book_stays_live_on_its_ledger(monkeypatch):
    """Ledger -247, venue -2049, the log naming the MANUAL short 1802.11 as
    our own aggressor order -> hand_explained, NO freeze, NO suspect, the
    book live on -247 with his target followed (the increase toward -300:
    53 more at his cent), NO reduce placed; _frozen_venue_own reads -247
    with the hand on the reading. Then the second hand short 200 s later
    (inside E22's wait, the residual moved) -> read at once, still
    explained, still live."""
    _shorts_on(monkeypatch)
    p = _short_pool()
    b = _book_1156(p)
    v = _venue(-2049, trades=[_short_fill()])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert b["state"] == "live" and b["frozen_reason"] is None and b["ledger_net"] == -247
    assert _census(st, "hand_explained") == 1 and all(_census(st, k) == 0 for k in NEW_NAMES[1:])
    assert _census(st, "venue_ledger_suspect") == 0 and _census(st, "venue_ledger_disagree") == 0
    assert _census(st, "wrong_sign_hold") == 0 and _census(st, "wrong_sign_trip") == 0
    assert "venue_ledger_suspect" not in b["last_plan"]
    assert _hand_of(b) == {"net": -1802.0, "adds": -1802.0, "reduces": 0.0, "fills": 1, "at": NOW,
                           "residual": -1802, "orders": ["CCZ08XN74SX4"], "adopted": 0.0, "px": None,
                           "verdict": "explained"}
    assert len(_hand_reads(v)) == 1 and _hand_reads(v)[0][1:] == (SLUG, HAND_SINCE) and len(_reads(v)) == 1
    # his target followed on the LEDGER: the increase 53 = |-300| - |-247| as a BUY_SHORT at his cent,
    # never a reduce (a BUY of the long token) and nothing sized on the desk's 1,802
    pl = _places(v)
    assert [c[1:] for c in pl] == [(SLUG, 0.28, 53, False, GTC_TIF, SHORT, True, None)]
    assert b["target"] == -300 and b["last_plan"]["kind"] == "increase"
    assert ("hand_explained", "rn1") in {tuple(k.split("|")) for k in ml.mirror_census_snapshot()}
    # the seat the frozen exit would size on, with the hand on the reading
    r = ml._Reading("rn1", CID, SLUG, M, N, [], 100.0, 400.0, {}, None, False, False, False, None, None,
                    0.13, 0.14, 0.135, -2049.0, 0.0, None, True)
    assert ml._frozen_venue_own(r) == -2049
    r.hand_adds = -1802
    assert ml._frozen_venue_own(r) == -247
    # 40 s on, the same reading: the memo (no venue call), the record carried, still explained
    st2 = _tick(p, v, now=NOW + 40, http=_mkt(100.0, 400.0))
    assert len(_reads(v)) == 1 and _census(st2, "hand_explained") == 0 and all(_census(st2, k) == 0 for k in NEW_NAMES)
    assert b["state"] == "live" and _hand_of(b)["at"] == NOW and _hand_of(b)["verdict"] == "explained"
    assert "venue_ledger_suspect" not in b["last_plan"] and _census(st2, "venue_ledger_suspect") == 0
    # the second hand short at +200 s: the venue -4029, the residual moved past the tolerance -> a read
    # at once (inside E22's wait), both shorts explained, the book still live, nothing bought back
    v.portfolio.held[SLUG] = -4029
    v.trades = [_short_fill(), _short_fill(SHORT_2, ts=AT + 200)]
    st3 = _tick(p, v, now=NOW + 200, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v)) == 2 and _census(st3, "hand_explained") == 1
    assert b["state"] == "live" and b["ledger_net"] == -247 and "venue_ledger_suspect" not in b["last_plan"]
    assert _hand_of(b)["adds"] == -3782.0 and _hand_of(b)["fills"] == 2 and _hand_of(b)["residual"] == -3782
    assert _hand_of(b)["orders"] == ["CCZ08XN74SX4", "CCZ2TT1YCT3P"] and _hand_of(b)["at"] == NOW + 200
    assert not [c for c in _places(v) if c[4] is True], "nothing of the desk's is bought back"
    assert b["frozen_reason"] is None and _census(st3, "venue_ledger_disagree") == 0


def test_e24_book_1156_frozen_the_frozen_exit_never_buys_the_hand_back_switch_on_or_off(monkeypatch):
    """The book as it stood at 16:18:36Z: frozen venue_ledger_disagree on
    ledger -247 with the venue -2049. The hand explains the reading: the
    thaw's own two-reads rule takes the book (one_read, then thawed), the
    frozen exit sizes on venue_own -247 -- under his -300 it holds
    `frozen_no_his_exit` -- and with rules.MIRROR_FROZEN_EXITS OFF (as the
    operator set it at 16:5xZ) it holds `frozen_exits_off` and the D2 thaw
    holds `thaw_off` with it (E16: the whole clause sits behind the
    switch); either way nothing is placed and the 1,802 is never bought
    back."""
    _shorts_on(monkeypatch)
    for switch in (True, False):
        ml._hand_read_at.clear()                       # book ids repeat across this file's pools
        monkeypatch.setattr(rules, "MIRROR_FROZEN_EXITS", switch)
        p = _short_pool()
        b = _book_1156(p, state="frozen", frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 100)
        v = _venue(-2049, trades=[_short_fill()])
        st = _tick(p, v, http=_mkt(100.0, 400.0))
        assert _census(st, "hand_explained") == 1 and not _places(v) and b["ledger_net"] == -247
        assert b["state"] == "frozen" and _census(st, "thaw_held") == 1
        assert b["last_plan"]["thaw_held"] == ("one_read" if switch else "thaw_off")
        assert _plan_exit(b)["held"] == ("frozen_no_his_exit" if switch else "frozen_exits_off")
        if switch:
            assert _plan_exit(b)["venue_own"] == -247 and _plan_exit(b)["target"] == -300
        assert _census(st, "frozen_reduce") == 0 and _census(st, "frozen_excess_sold") == 0
        # the next fresh agreeing read: thawed by E16's rule (never by this lane), the increase toward
        # -300; with the switch off the book stays frozen by name and nothing is placed at all
        st2 = _tick(p, v, now=NOW + 30, http=_mkt(100.0, 400.0))
        if switch:
            assert b["state"] == "live" and _census(st2, "thaw_held") == 0 and b["last_plan"]["thawed_venue_agrees"] is True
            assert [c[1:] for c in _places(v)] == [(SLUG, 0.28, 53, False, GTC_TIF, SHORT, True, None)]
        else:
            assert b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "thaw_off" and not _places(v)
            assert _plan_exit(b)["held"] == "frozen_exits_off"
        assert len(_hand_reads(v)) == 1, "one hand read for both ticks (the memo)"


# ------------------------------------------------ (2) book 986: the hand buy, E23 never reached


def test_e24_book_986_the_hand_buy_explains_the_surplus_and_e23s_part_b_is_never_called():
    """Ledger 413, venue 1215, the twelve cancelled rows with the venue's
    ids (E23's fixture) and the log naming the hand buy CCX413N48SX0 (713
    @0.61 + 20 @0.60 + 25 + 25 @0.60 + 20 @0.59, the 0 @0.59 print) ->
    hand_explained (the gap 1215 - 413 - 803 = -1, inside the freeze's
    tolerance), the freeze's branch never entered so E23's part B is
    never called (no read since 5823's placement, no `disagree_fill`, no
    row booked), the existing thaw takes the book (one_read, thawed) and
    it plans live toward 2,876 on the ledger 413."""
    p = _long_pool()
    b, rows = _book_986(p)
    v = _NoClose(held={SLUG: 1215}, bid=0.60, ask=0.61, trades=_buy_986())
    st = _tick(p, v, http=_mkt(28765.0))
    assert _census(st, "hand_explained") == 1 and all(_census(st, k) == 0 for k in NEW_NAMES[1:])
    assert all(_census(st, k) == 0 for k in ("disagree_fill_adopted", "disagree_fill_unread",
                                             "disagree_fill_unexplained", "disagree_fill_ambiguous"))
    assert "disagree_fill" not in b["last_plan"] and "disagree_fill_at" not in b["last_plan"]
    assert [c[1:] for c in _reads(v)] == [(SLUG, HAND_SINCE)]
    assert all(o["booked_filled"] == bk for (_oid, _q, bk, _w), o in zip(ROWS_986, rows))
    assert b["ledger_net"] == 413 and b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "one_read"
    assert _hand_of(b)["adds"] == 803.0 and _hand_of(b)["fills"] == 5 and _hand_of(b)["verdict"] == "explained"
    assert _hand_of(b)["orders"] == ["CCX413N48SX0"] and _hand_of(b)["reduces"] == 0.0
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(28765.0))
    assert b["state"] == "live" and b["last_plan"]["thawed_venue_agrees"] is True and len(_reads(v)) == 1
    assert _census(st2, "hand_explained") == 0 and _hand_of(b)["verdict"] == "explained"
    # live on the LEDGER: the increase 2,876 - 413 = 2,463 at his cent, clipped by the fixture's day room
    st3 = _tick(p, v, now=NOW + 60, http=_mkt(28765.0))
    assert b["last_plan"]["kind"] == "increase" and b["last_plan"]["qty"] == 2876 - 413 and b["last_plan"]["price"] == 0.56
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1:3] == (SLUG, 0.56) and pl[0][4] is False and 0 < pl[0][3] <= 2876 - 413
    assert b["ledger_net"] == 413, "nothing of the desk's 803 is the book's"


# ------------------------------------------------ (3) book 1075: the hand cover adopted, the book closes


def test_e24_book_1075_the_hand_cover_is_adopted_as_the_books_exit_and_the_close_takes_the_flat_row(monkeypatch):
    """State closing, ledger -3966 at 0.8623 (realized -189.36), the venue
    flat since the desk covered the short by hand (six fills @0.97 under
    CCYC06E66SJH, 3966.24 IOC @0.99) -> hand_adopted 3966 @0.97: the
    standing row sold flat, the ledger 0, realized -189.36 + (0.8623 -
    0.97) x 3966 = -616.4982, the adoption recorded per hand order id,
    and the book closed through the closing branch's own close
    (closed_cashed_out) on the same tick -- nothing placed, never a cover
    of 3,966 bought at 0.97."""
    _shorts_on(monkeypatch)
    p = _short_pool()
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    b = _short_book(p, ledger=-3966, avg=0.8623, state="closing", realized_pnl=-189.36)
    v = _venue(0, trades=_cover_fills())
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "hand_adopted") == 1 and all(_census(st, k) == 0 for k in NEW_NAMES if k != "hand_adopted")
    assert b["ledger_net"] == 0 and b["realized_pnl"] == pytest.approx(-189.36 + (0.8623 - 0.97) * 3966, abs=1e-4)
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 0.0
    assert p.rows[b["standing_row_id"]]["pnl"] == pytest.approx((0.8623 - 0.97) * 3966, abs=1e-4)
    assert b["state"] == "closed" and _census(st, "closed_cashed_out") == 1
    assert not _places(v) and "close" not in [c[0] for c in v.calls]
    assert [c[1:] for c in _reads(v)] == [(SLUG, HAND_SINCE)]
    rec = _record(p, b)
    assert rec["adopted"] == {"CCYC06E66SJH": 3966.0} and rec["px"] == 0.97 and rec["orders"] == ["CCYC06E66SJH"]
    ent = _recent("hand_adopted", b["id"])
    assert len(ent) == 1 and ent[0]["shares"] == 3966.0 and ent[0]["px"] == 0.97 and ent[0]["ledger"] == 0
    assert ent[0]["orders"] == ["CCYC06E66SJH"]
    assert ("hand_adopted", "rn1") in {tuple(k.split("|")) for k in ml.mirror_census_snapshot()}


def test_e24_a_closing_book_reads_nothing_on_a_cached_walk_a_flat_ledger_or_an_open_order(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_pool()
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    b = _short_book(p, ledger=-3966, avg=0.8623, state="closing")
    v = _venue(0, trades=_cover_fills())
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    t.positions = {SLUG: 0.0}
    t.walk_at = None
    asyncio.run(ml._hand_closing(t, b, {}))
    assert not v.calls and b["ledger_net"] == -3966
    t.walk_at = NOW
    o = p.add_order(b, side=BUY, wire=0.9, qty=100, order_id="oid-open", state="open")
    t.open_by_book[b["id"]] = (o, {})
    asyncio.run(ml._hand_closing(t, b, {}))
    assert not v.calls and b["ledger_net"] == -3966
    t.open_by_book.clear()
    b["open_order_id"] = None
    b["ledger_net"] = 0
    asyncio.run(ml._hand_closing(t, b, {}))
    assert not v.calls
    # agreeing (venue -3966 against the ledger -3966): no read
    b["ledger_net"] = -3966
    t.positions = {SLUG: -3966.0}
    asyncio.run(ml._hand_closing(t, b, {}))
    assert not v.calls


# ------------------------------------------------ (4) book 1129: the hand sale past the book's shares


def test_e24_book_1129_the_hand_sale_adopts_the_books_4852_and_explains_the_desks_186_the_hold_never_taken():
    """Long 4,852 at 0.3116, venue -186, the hand sale 5038 @0.41 -> 4,852
    adopted as the book's exit (realized (0.41 - 0.3116) x 4852 =
    477.4368, the ledger 0), the desk's 186 short as hand_adds explained,
    the wrong-sign hold NOT taken (the book's own venue reading is 0, not
    -186), nothing placed on the adoption tick; the next tick judges the
    book on its ledger 0 (the residual -186 moved -> a read at once; the
    record takes the 4,852 off the sale; the 186 explained) and plans
    live toward his 2,876 -- HELD `hand_held` since E29 (the desk's exit
    ends the book's adds; before E29 the increase rested at his cent)."""
    p = _long_pool()
    b = p.add_book(ledger=4852, avg_cost=0.3116, ratio=0.1, target=2876)
    v = _NoClose(held={SLUG: -186}, bid=0.60, ask=0.61, trades=[_sale_1129()])
    st = _tick(p, v, http=_mkt(28765.0))
    assert _census(st, "hand_adopted") == 1 and _census(st, "hand_explained") == 1
    assert _census(st, "hand_unread") == 0 and _census(st, "hand_ambiguous") == 0
    assert _census(st, "wrong_sign_hold") == 0 and _census(st, "wrong_sign_trip") == 0 and _census(st, "venue_ledger_suspect") == 0
    assert b["state"] == "live" and b["ledger_net"] == 0 and b["realized_pnl"] == pytest.approx((0.41 - 0.3116) * 4852, abs=1e-4)
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 0.0 and not _places(v)
    assert b["last_plan"]["kind"] == "hand_adopted" and b["last_reason"] == "hand_adopted"
    h = _hand_of(b)
    assert h["verdict"] == "adopted" and h["adopted"] == 4852.0 and h["px"] == 0.41 and h["adds"] == -186.0
    assert h["reduces"] == -4852.0 and h["net"] == -5038.0 and h["residual"] == -5038 and h["orders"] == ["CCXMNEBPRSJH"]
    assert _record(p, b)["adopted"] == {"CCXMNEBPRSJH": 4852.0}
    assert p.state.get("mirror_live") is True, "the desk is not tripped"
    # the next tick: ledger 0 against venue -186 -- the residual moved, the log re-read, the recorded
    # 4,852 taken off the sale, the 186 explained; the book plans live on its ledger 0
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(28765.0))
    assert len(_reads(v)) == 2 and _census(st2, "hand_explained") == 1 and _census(st2, "hand_adopted") == 0
    assert b["state"] == "live" and b["ledger_net"] == 0 and _census(st2, "wrong_sign_hold") == 0
    assert _hand_of(b) == {"net": -186.0, "adds": -186.0, "reduces": 0.0, "fills": 1, "at": NOW + 30, "residual": -186,
                           "orders": ["CCXMNEBPRSJH"], "adopted": 0.0, "px": None, "verdict": "explained"}
    assert _record(p, b)["adopted"] == {"CCXMNEBPRSJH": 4852.0}, "never booked twice"
    # E29 (FILL lane 29; book 1317's four re-entries): the desk's exit ends the book's adds -- the plan still
    # reads the increase toward 2,876 on the ledger 0 (kind increase, target 2876) but it is HELD `hand_held`,
    # nothing placed; before E29 this tick placed the increase at 0.56 (re-pinned: the rule changed)
    assert b["last_plan"]["kind"] == "increase" and b["last_plan"]["target"] == 2876 and b["last_plan"]["hold"] == "hand_held"
    assert not _places(v) and _census(st2, "hand_held") == 1 and b["last_plan"]["hand_exit"]["shares"] == 4852.0


def test_e24_a_hand_reduce_of_part_of_the_book_is_adopted_and_the_rest_of_the_leg_stands(monkeypatch):
    """A short of 300 covered by hand 100 @0.30: adopted 100 (the ledger
    -200, realized (0.32 - 0.30) x 100), no hand_adds, the book judged on
    -200 next tick (his -300: the increase of 100 HELD `hand_held` since
    E29 -- the desk's exit ends the book's adds; it rested at his cent
    before)."""
    _shorts_on(monkeypatch)
    p = _short_pool()
    b = _short_book(p, ledger=-300, avg=0.32)
    v = _venue(-200, trades=[_hand("H-1", "BUY", "SELL_SHORT", 100.0, 0.30, order_qty=100.0, order_px=0.31,
                                    tif="IMMEDIATE_OR_CANCEL")])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "hand_adopted") == 1 and _census(st, "hand_explained") == 0
    assert b["ledger_net"] == -200 and b["realized_pnl"] == pytest.approx((0.32 - 0.30) * 100, abs=1e-6)
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 200.0 and not _places(v)
    assert _hand_of(b)["adds"] == 0.0 and _hand_of(b)["reduces"] == 100.0 and _hand_of(b)["adopted"] == 100.0
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert len(_reads(v)) == 1, "venue -200 == ledger -200: no disagreement, no read"
    assert all(_census(st2, k) == 0 for k in NEW_NAMES) and b["state"] == "live"
    # E29 (FILL lane 29): the desk covered 100 by hand, so the book is hand-exited -- the increase of 100 toward
    # his -300 is HELD `hand_held` (target -300, ledger -200, nothing placed); before E29 it rested at his cent
    # 0.28 (re-pinned: the rule changed -- book 1317's re-entries after four hand covers)
    assert not _places(v) and _census(st2, "hand_held") == 1 and b["last_plan"]["hold"] == "hand_held"
    assert b["last_plan"]["target"] == -300 and b["last_plan"]["ledger"] == -200


# ------------------------------------------------ (5) the reads that explain nothing


def test_e24_a_counterparty_app_order_on_the_other_side_is_not_a_hand_fill(monkeypatch):
    """The 15:53Z Zheng buys: isAggressor false, our passive rest
    (BUY_SHORT AUTOMATIC) filled by a counterparty's MANUAL BUY_LONG. The
    parsed row carries `manual` False on OUR side, so it explains
    nothing: the first read is E16's suspect as today, the second fresh
    read the freeze."""
    _shorts_on(monkeypatch)
    p = _short_pool()
    b = _book_1156(p)
    cp = _hand("CCYHSY30WSJH", "SELL", "BUY_SHORT", 15.0, 0.32, order_qty=618.0, order_px=0.32, tif="GOOD_TILL_CANCEL",
               manual=False, aggressor=False)
    v = _venue(-2049, trades=[cp])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "hand_ambiguous") == 1 and _census(st, "hand_explained") == 0
    assert _census(st, "venue_ledger_suspect") == 1 and b["state"] == "live" and "venue_ledger_suspect" in b["last_plan"]
    assert _hand_of(b)["fills"] == 0 and _hand_of(b)["verdict"] == "ambiguous" and _hand_of(b)["adds"] == 0.0
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree" and len(_hand_reads(v)) == 1
    assert _census(st2, "hand_ambiguous") == 0, "the verdict carried inside the wait, not re-read"


def test_e24_the_log_unreadable_is_hand_unread_and_todays_freeze(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_pool()
    b = _book_1156(p)
    v = _venue(-2049, trades_raise=True)
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "hand_unread") == 1 and _census(st, "venue_ledger_suspect") == 1 and b["state"] == "live"
    assert _hand_of(b)["verdict"] == "unread" and _hand_of(b)["at"] == NOW and b["ledger_net"] == -247
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree" and not _places(v)
    # the book's open clock unreadable -> unread, no venue call; the adoption record malformed -> unread,
    # and (the review's LOW-3) the record is read BEFORE the log, so no venue call is spent on it either
    ml._hand_read_at.clear()
    p2 = _short_pool()
    b2 = _book_1156(p2)
    b2["opened_ts"] = None
    v2 = _venue(-2049, trades=[_short_fill()])
    st3 = _tick(p2, v2, http=_mkt(100.0, 400.0))
    assert _census(st3, "hand_unread") == 1 and not _reads(v2)
    ml._hand_read_at.clear()
    p3 = _short_pool()
    b3 = _book_1156(p3)
    p3.state[f"mirror_hand:{b3['id']}"] = {"adopted": "junk"}
    v3 = _venue(-2049, trades=[_short_fill()])
    st4 = _tick(p3, v3, http=_mkt(100.0, 400.0))
    assert _census(st4, "hand_unread") == 1 and _census(st4, "hand_explained") == 0 and b3["ledger_net"] == -247
    assert not _reads(v3), "an unreadable record spends no venue call: the record is read before the log"


def test_e24_a_hand_net_that_leaves_the_comparison_outside_the_tolerance_is_ambiguous_either_way(monkeypatch):
    """The hand short of 1,000 against a residual of 1,802 (the venue holds
    802 more than the ledger and the hand explain) -> ambiguous, nothing
    adopted, the suspect as today; a hand short of 2,600 (the log naming
    MORE than the venue holds) -> ambiguous the other way."""
    _shorts_on(monkeypatch)
    for qty in (1000.0, 2600.0):
        ml._hand_read_at.clear()
        p = _short_pool()
        b = _book_1156(p)
        v = _venue(-2049, trades=[_hand("CCZ08XN74SX4", "SELL", "BUY_SHORT", qty, 0.46, order_qty=qty, order_px=0.36)])
        st = _tick(p, v, http=_mkt(100.0, 400.0))
        assert _census(st, "hand_ambiguous") == 1 and _census(st, "hand_explained") == 0
        assert _census(st, "venue_ledger_suspect") == 1 and b["ledger_net"] == -247 and b["state"] == "live"
        assert _hand_of(b)["verdict"] == "ambiguous" and _hand_of(b)["adds"] == -qty and _hand_of(b)["adopted"] == 0.0
        assert _hand_of(b)["reduces"] == 0.0 and "hand" not in b["last_plan"].get("venue_ledger_suspect", {})
        assert not [c for c in _places(v) if c[4] is True]


def test_e24_a_self_trade_an_unpriced_fill_and_one_order_printed_twice_with_disagreeing_figures_are_ambiguous(monkeypatch):
    _shorts_on(monkeypatch)
    shapes = {
        "self": [_short_fill(self_=True)],
        "unpriced": [_hand("CCZ08XN74SX4", "SELL", "BUY_SHORT", 1802.0, 0.0, order_qty=1802.11, order_px=0.36)],
        "no_order": [{**_short_fill(), "own_order_id": None}],
        "no_side": [{**_short_fill(), "own_side": None}],
        "twice": [_hand("CCZ08XN74SX4", "SELL", "BUY_SHORT", 1000.0, 0.46, order_qty=1802.11, order_px=0.36),
                  _hand("CCZ08XN74SX4", "SELL", "BUY_SHORT", 802.0, 0.46, ts=AT + 1, order_qty=1802.11, order_px=0.37)],
    }
    for name, trades in shapes.items():
        ml._hand_read_at.clear()
        p = _short_pool()
        b = _book_1156(p)
        v = _venue(-2049, trades=trades)
        st = _tick(p, v, http=_mkt(100.0, 400.0))
        assert _census(st, "hand_ambiguous") == 1 and _census(st, "hand_explained") == 0, name
        assert _hand_of(b)["verdict"] == "ambiguous" and b["ledger_net"] == -247 and _census(st, "venue_ledger_suspect") == 1, name
    # the same order printed twice with AGREEING figures is the ordinary partial-fill shape: explained
    ml._hand_read_at.clear()
    p = _short_pool()
    b = _book_1156(p)
    v = _venue(-2049, trades=[_hand("CCZ08XN74SX4", "SELL", "BUY_SHORT", 1000.0, 0.46, order_qty=1802.11, order_px=0.36),
                              _hand("CCZ08XN74SX4", "SELL", "BUY_SHORT", 802.0, 0.46, ts=AT + 1, order_qty=1802.11, order_px=0.36)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "hand_explained") == 1 and _hand_of(b)["fills"] == 2 and b["state"] == "live"


def test_e24_a_fill_dated_before_the_window_is_not_counted_and_a_zero_print_is_nothing(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_pool()
    b = _book_1156(p)
    early = _short_fill(ts=HAND_SINCE - 1.0)
    v = _venue(-2049, trades=[early, {**_short_fill(), "qty": 0.0}])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "hand_ambiguous") == 1 and _hand_of(b)["fills"] == 0 and _census(st, "venue_ledger_suspect") == 1
    # inside the window with the zero print beside it: explained
    ml._hand_read_at.clear()
    p2 = _short_pool()
    b2 = _book_1156(p2)
    v2 = _venue(-2049, trades=[_short_fill(), {**_short_fill(), "qty": 0.0, "ts": AT + 1}])
    st2 = _tick(p2, v2, http=_mkt(100.0, 400.0))
    assert _census(st2, "hand_explained") == 1 and _hand_of(b2)["fills"] == 1


def test_e24_a_register_row_explains_first_and_the_hand_is_never_read(monkeypatch):
    """E5's register naming the 1,802 on the slug: venue == ledger +
    registered, the freeze's branch never entered, no log read, nothing
    of the lane's; a register of 800: the hand explains the 1,002 left
    (1802 - 800) only when it reads so -- the hand short of 1,802 does
    not (the two together over-explain by 800 -> ambiguous)."""
    _shorts_on(monkeypatch)
    p = _short_pool(registered={SLUG: -1802.0})
    b = _book_1156(p)
    v = _venue(-2049, trades=[_short_fill()])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert not _reads(v) and _census(st, "registered_books") == 1 and all(_census(st, k) == 0 for k in NEW_NAMES)
    assert "hand" not in b["last_plan"] and b["ledger_net"] == -247
    p2 = _short_pool(registered={SLUG: -800.0})
    b2 = _book_1156(p2)
    v2 = _venue(-2049, trades=[_short_fill()])
    st2 = _tick(p2, v2, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v2)) == 1 and _census(st2, "hand_ambiguous") == 1 and _hand_of(b2)["residual"] == -1002
    # the desk's `manual` rows first, the same way
    ml._hand_read_at.clear()
    p3 = _short_pool()
    p3.manual_shares[SLUG] = -1802.0
    b3 = _book_1156(p3)
    v3 = _venue(-2049, trades=[_short_fill()])
    st3 = _tick(p3, v3, http=_mkt(100.0, 400.0))
    assert not _reads(v3) and all(_census(st3, k) == 0 for k in NEW_NAMES) and "hand" not in b3["last_plan"]


# ------------------------------------------------ (6) the memo, the cached walk, the budget


def test_e24_the_memo_is_e22s_wait_on_the_same_residual_and_a_cached_walk_or_a_spent_budget_reads_nothing(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_pool()
    b = _book_1156(p)
    v = _venue(-2049, trades=[_hand("CCZ08XN74SX4", "SELL", "BUY_SHORT", 1000.0, 0.46, order_qty=1000.0, order_px=0.36)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v)) == 1 and _census(st, "hand_ambiguous") == 1
    _tick(p, v, now=NOW + 40, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v)) == 1, "inside the wait on the same residual: no second read"
    st3 = _tick(p, v, now=NOW + 300, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v)) == 2 and _census(st3, "hand_ambiguous") == 1 and _hand_of(b)["at"] == NOW + 300
    # the process memo covers a plan that lost the record (a quiet skip carries _SKIP_CARRIED alone)
    b["last_plan"] = {k: x for k, x in b["last_plan"].items() if k != "hand"}
    _tick(p, v, now=NOW + 340, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v)) == 2 and _hand_of(b)["at"] == NOW + 300, "the memo's record carried back onto the plan"
    # the plan's record covers a restart (the process memo empty)
    ml._hand_read_at.clear()
    _tick(p, v, now=NOW + 380, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v)) == 2
    # the wait lengthened through E22's rail (min_wait_env: the environment may only LENGTHEN it)
    monkeypatch.setattr(rules, "MIRROR_LOST_FILL_REREAD_S", 900.0)
    _tick(p, v, now=NOW + 620, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v)) == 2
    _tick(p, v, now=NOW + 1200, http=_mkt(100.0, 400.0))
    assert len(_hand_reads(v)) == 3
    assert rules.min_wait_env("MIRROR_LOST_FILL_REREAD_S", 300.0) == 300.0
    monkeypatch.setenv("MIRROR_LOST_FILL_REREAD_S", "30")
    assert rules.min_wait_env("MIRROR_LOST_FILL_REREAD_S", 300.0) == 300.0
    # the pure guards: a cached walk returns the carried record and calls nothing; a spent budget the same
    ml._hand_read_at.clear()
    p2 = _short_pool()
    b2 = _book_1156(p2)
    v2 = _venue(-2049, trades=[_short_fill()])
    t = ml._Tick(pool=p2, pmus=v2, http=None, now=NOW, stats=ml._new_stats())
    t.walk_at = None
    prior = {"hand": {"verdict": "explained", "adds": -1802.0, "at": NOW - 10.0, "residual": -1802}}
    plan = {}
    out = asyncio.run(ml._hand_net(t, b2, -2049, -247, 0.0, 0.0, prior, plan))
    assert out == prior["hand"] and plan == {"hand": prior["hand"]} and not v2.calls
    assert asyncio.run(ml._hand_net(t, b2, -2049, -247, 0.0, 0.0, {}, {})) is None and not v2.calls
    t.walk_at = NOW
    t.venue_calls = ml.VENUE_CALLS_PER_TICK
    assert asyncio.run(ml._hand_net(t, b2, -2049, -247, 0.0, 0.0, {}, {})) is None and not v2.calls
    t.venue_calls = 0
    out = asyncio.run(ml._hand_net(t, b2, -2049, -247, 0.0, 0.0, {}, {}))
    assert out["verdict"] == "explained" and len(v2.calls) == 1 and v2.calls[0] == ("trades", SLUG, HAND_SINCE)


def test_e24_the_memo_is_bounded():
    ml._hand_read_at.clear()
    for i in range(ml._LOST_FILL_MEMO_MAX + 1):
        ml._hand_read_at[i] = {"at": float(i), "residual": 0, "hand": {}}
    p = _short_pool()
    b = _book_1156(p)
    v = _venue(-2049, trades=[_short_fill()])
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    t.walk_at = NOW
    asyncio.run(ml._hand_net(t, b, -2049, -247, 0.0, 0.0, {}, {}))
    assert len(ml._hand_read_at) <= ml._LOST_FILL_MEMO_MAX // 2 + 2 and b["id"] in ml._hand_read_at
    ml._hand_read_at.clear()


# ------------------------------------------------ (7) the booking that fails; the record


def test_e24_a_booking_that_fails_adopts_nothing_records_nothing_and_freezes_write_failed(monkeypatch, caplog):
    p = _long_pool()
    b = p.add_book(ledger=4852, avg_cost=0.3116, ratio=0.1, target=2876)
    p.raise_on.append(("ml-book-ledger-sell", RuntimeError("down")))
    v = _NoClose(held={SLUG: -186}, bid=0.60, ask=0.61, trades=[_sale_1129()])
    with caplog.at_level("ERROR"):
        st = _tick(p, v, http=_mkt(28765.0))
    assert all(_census(st, k) == 0 for k in NEW_NAMES) and _census(st, "write_failed") == 2   # the booking's and the freeze's
    assert b["ledger_net"] == 4852 and b["state"] == "frozen" and b["frozen_reason"] == "write_failed"
    assert _record(p, b) is None and _hand_of(b)["verdict"] == "adopt_write_failed" and not _places(v)
    assert sum("booking the desk's hand reduce failed" in r.message for r in caplog.records) == 1
    # the record and the booking are one transaction: a refused sale writes no record either
    ml._hand_read_at.clear()
    p2 = _long_pool()
    b2 = p2.add_book(ledger=4852, avg_cost=0.3116, ratio=0.1, target=2876)
    p2.rows[b2["standing_row_id"]]["fill_price"] = None            # no entry price: the primitive refuses
    v2 = _NoClose(held={SLUG: -186}, bid=0.60, ask=0.61, trades=[_sale_1129()])
    st2 = _tick(p2, v2, http=_mkt(28765.0))
    assert all(_census(st2, k) == 0 for k in NEW_NAMES) and b2["ledger_net"] == 4852 and _record(p2, b2) is None
    assert _hand_of(b2)["verdict"] == "adopt_write_failed" and _census(st2, "write_failed") == 0
    # today's reading stands: 1129's shape without the adoption is E20's wrong-sign hold, nothing placed
    assert b2["state"] == "frozen" and b2["frozen_reason"] == "wrong_sign_hold" and not _places(v2)


def test_e24_a_record_naming_more_than_the_log_is_ambiguous_and_nothing_is_booked_again():
    p = _long_pool()
    b = p.add_book(ledger=4852, avg_cost=0.3116, ratio=0.1, target=2876)
    p.state[f"mirror_hand:{b['id']}"] = {"adopted": {"CCXMNEBPRSJH": 6000.0}}
    v = _NoClose(held={SLUG: -186}, bid=0.60, ask=0.61, trades=[_sale_1129()])
    st = _tick(p, v, http=_mkt(28765.0))
    assert _census(st, "hand_ambiguous") == 1 and b["ledger_net"] == 4852
    assert p.state[f"mirror_hand:{b['id']}"] == {"adopted": {"CCXMNEBPRSJH": 6000.0}}


# ------------------------------------------------ (8) the parser: the own side, the self rule


def _venue_row(tid, agg_order, pas_order, is_aggressor, qty="1802", price="0.4600",
               create="2026-09-09T16:17:34.997970075Z"):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"id": tid, "marketSlug": SLUG, "isAggressor": is_aggressor, "qty": qty,
                      "price": {"value": price, "currency": "USD"}, "createTime": create,
                      "aggressorExecution": {"order": agg_order}, "passiveExecution": {"order": pas_order}}}


def _order(oid, side, intent, manual, qty, price, tif="TIME_IN_FORCE_DAY"):
    return {"id": oid, "marketSlug": SLUG, "side": f"ORDER_SIDE_{side}", "type": "ORDER_TYPE_LIMIT",
            "price": {"value": price, "currency": "USD"}, "quantity": qty, "tif": tif,
            "intent": f"ORDER_INTENT_{intent}", "manualOrderIndicator": f"MANUAL_ORDER_INDICATOR_{manual}"}


# vt_1156_1639.txt row 94 (the hand short, isAggressor true) and row 114 (the 15:53:47Z Zheng buy that
# hit our resting 6852: isAggressor false, the AGGRESSOR order a counterparty's app order carrying an
# intent and the MANUAL indicator), each trimmed to the fields the parser reads
ROW_94 = _venue_row("CCZ5XVSAWSJV",
                    _order("CCZ08XN74SX4", "SELL", "BUY_SHORT", "MANUAL", 1802.11, "0.36"),
                    _order("CCY6J92MGT54", "BUY", "UNDEFINED", "UNDEFINED", 9286.81, "0.46"), True)
ROW_114 = _venue_row("CCYV21786SJV",
                     _order("CCYNKVHYMT75", "BUY", "BUY_LONG", "MANUAL", 15, "0.42"),
                     _order("CCYHSY30WSJH", "SELL", "BUY_SHORT", "AUTOMATIC", 618, "0.32",
                            tif="TIME_IN_FORCE_GOOD_TILL_CANCEL"),
                     False, qty="15", price="0.3200", create="2026-09-09T15:53:47.121056503Z")


class _Portfolio:
    def __init__(self, acts):
        self.acts = list(acts)

    def activities(self, params):
        return {"activities": self.acts, "eof": True}


class _Client:
    def __init__(self, acts):
        self.portfolio = _Portfolio(acts)


def _parsed(monkeypatch, acts):
    monkeypatch.setattr(pmus, "_client", _Client(acts))
    return pmus.recent_trades(SLUG, 0.0)


def test_e24_the_parser_picks_the_accounts_own_side_by_is_aggressor_never_by_the_intent(monkeypatch):
    out = _parsed(monkeypatch, [ROW_94, ROW_114])
    ours, theirs = out
    # row 94: the hand short is OUR aggressor order
    assert ours["manual"] is True and ours["own_order_id"] == "CCZ08XN74SX4" and ours["aggressor"] is True
    assert ours["own_side"] == "SELL" and ours["own_intent"] == "BUY_SHORT" and ours["own_tif"] == "DAY"
    assert ours["own_qty"] == 1802.11 and ours["own_price"] == 0.36 and ours["self"] is False
    assert (ours["qty"], ours["price"]) == (1802.0, 0.46)
    # row 114: the counterparty's app order is the AGGRESSOR (BUY_LONG, MANUAL); ours is the passive rest
    assert theirs["manual"] is False and theirs["own_order_id"] == "CCYHSY30WSJH" and theirs["aggressor"] is False
    assert theirs["own_side"] == "SELL" and theirs["own_intent"] == "BUY_SHORT" and theirs["own_tif"] == "GOOD_TILL_CANCEL"
    assert theirs["self"] is False, "both sides carry an intent and the venue's MANUAL word: NOT a self-trade"
    # E22's keys byte for byte (trade_order: the first execution order named, the aggressor's)
    for row in out:
        for k in ("qty", "price", "side", "ts", "realized_pnl", "order_id", "order_qty", "order_price", "order_tif", "aggressor"):
            assert k in row, k
    assert theirs["order_id"] == "CCYNKVHYMT75" and theirs["side"] == "ORDER_SIDE_BUY"
    # the own side unknown (isAggressor absent): manual None, no own order, never a hand fill
    blank = _venue_row("T-3", _order("A", "SELL", "BUY_SHORT", "MANUAL", 10, "0.4"),
                       _order("B", "BUY", "UNDEFINED", "UNDEFINED", 10, "0.4"), None)
    row = _parsed(monkeypatch, [blank])[0]
    assert row["manual"] is None and row["own_order_id"] is None and row["own_side"] is None and row["self"] is False
    # an indicator word that is neither MANUAL nor absent reads False; an absent one None
    auto = _venue_row("T-4", _order("A", "SELL", "BUY_SHORT", "AUTOMATIC", 10, "0.4"),
                      _order("B", "BUY", "UNDEFINED", "UNDEFINED", 10, "0.4"), True)
    assert _parsed(monkeypatch, [auto])[0]["manual"] is False
    none = _venue_row("T-5", {k: v for k, v in _order("A", "SELL", "BUY_SHORT", "MANUAL", 10, "0.4").items()
                              if k != "manualOrderIndicator"},
                      _order("B", "BUY", "UNDEFINED", "UNDEFINED", 10, "0.4"), True)
    assert _parsed(monkeypatch, [none])[0]["manual"] is None
    assert pmus.trade_own_order({"isAggressor": "true"}) == {} and pmus.trade_other_order({}) == {}


def test_e24_the_self_rule_is_the_counterparty_being_one_of_our_own_orders_or_a_trade_printed_twice(monkeypatch):
    # the account on both sides: the venue prints one row per side, each naming the other's order
    a = _venue_row("T-1", _order("O-1", "SELL", "BUY_SHORT", "MANUAL", 10, "0.4"),
                   _order("O-2", "BUY", "BUY_LONG", "AUTOMATIC", 10, "0.4"), True)
    b = _venue_row("T-2", _order("O-1", "SELL", "BUY_SHORT", "MANUAL", 10, "0.4"),
                   _order("O-2", "BUY", "BUY_LONG", "AUTOMATIC", 10, "0.4"), False)
    out = _parsed(monkeypatch, [a, b])
    assert [r["self"] for r in out] == [True, True] and [r["own_order_id"] for r in out] == ["O-1", "O-2"]
    # one trade id printed twice
    c = _venue_row("T-9", _order("O-7", "SELL", "BUY_SHORT", "MANUAL", 10, "0.4"),
                   _order("X-1", "BUY", "UNDEFINED", "UNDEFINED", 10, "0.4"), True)
    out = _parsed(monkeypatch, [c, dict(c)])
    assert [r["self"] for r in out] == [True, True]
    # the day's rows: the 20 counterparty app orders are not self-trades (row 114 beside row 94)
    assert [r["self"] for r in _parsed(monkeypatch, [ROW_94, ROW_114])] == [False, False]


# ------------------------------------------------ (9) the census place, the emit sites, the names


def test_e24_the_census_place_the_emit_sites_the_untouched_functions_and_no_knob():
    keys = ml.CENSUS_KEYS
    # E25 (FILL lane 25) landed its four names after this block: -17:-13 -> -21:-17, every older index by four
    assert keys[-30:-26] == NEW_NAMES and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-36:-30] == ("cancel_fill_late", "cancel_fill_unread", "disagree_fill_adopted", "disagree_fill_unread",
                             "disagree_fill_unexplained", "disagree_fill_ambiguous")
    assert keys[-44] == "cand_market_closed_db" and keys[-48:-44] == ("lost_fill_adopted", "lost_fill_unread",
                                                                      "lost_fill_unexplained", "lost_fill_ambiguous")
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert all(ml._new_stats()["census"][k] == 0 for k in NEW_NAMES)
    src = inspect.getsource(ml._hand_net)
    assert src.count('_mirror_stop("hand_explained"') == 1 and src.count('_mirror_stop("hand_adopted"') == 1
    assert src.count('_mirror_stop("hand_unread"') == 3 and src.count('_mirror_stop("hand_ambiguous"') == 4
    assert "await _venue_read(t, t.pmus.recent_trades" in inspect.getsource(ml._hand_log_fills)
    assert "rules.MIRROR_LOST_FILL_REREAD_S" in src and "mi.VENUE_LEDGER_TOL_SHARES" in src
    # the call sites: the disagree branch (before the wrong-sign check and the suspect), the closing branch
    tb = inspect.getsource(ml._tick_book)
    assert tb.count("await _hand_net(") == 1 and tb.count("await _hand_closing(t, book, plan)") == 1
    assert tb.index("await _hand_net(") < tb.index("(venue_own < 0) != (ledger < 0)")
    assert tb.index("await _hand_closing(t, book, plan)") < tb.index("await _hand_net(")
    assert tb.index("await _hand_net(") < tb.index("_second_disagreeing_read(prior_suspect, delta, t)")
    assert "genuine = abs(abs(venue_own) - abs(ledger)) <= mi.VENUE_LEDGER_TOL_SHARES" in tb
    assert "venue_seat = float(venue_int - r.manual - registered) - float(hand_adds)" in tb
    assert tb.count("venue_seat") >= 4 and "float(venue_int - r.manual - registered)," not in tb
    assert "venue_flat=abs(float(r.venue or 0.0) - hand_adds) < FLAT_TOL_SHARES" in tb
    assert "and hand_adds == 0\n" in tb, "the short proof is never recorded on a reading with the desk's shares in it"
    assert 'int(getattr(r, "hand_adds", 0) or 0)' in inspect.getsource(ml._frozen_venue_own)
    # the booking: the standing row's sale, the ledger and the record in ONE transaction; no order row
    bk = inspect.getsource(ml._book_hand_reduce)
    assert "async with conn.transaction():" in bk and "le._book_mirror_sell(conn," in bk
    assert "_SQL_BOOK_LEDGER_SELL" in bk and "_write_state(conn," in bk and "INSERT INTO mirror_orders" not in bk
    assert "t.filled_books.add(book[\"id\"])" in bk
    # no rail of the lane's own: E22's wait and the freeze's tolerance; no knob, no migration, no decision word
    rsrc = inspect.getsource(rules)
    # E25 (FILL lane 25) added three capped_env rails and two min_wait_env waits (22 -> 25 on the tip that carries the per-trade cap: 23 -> 22 there, then E25's three, 5 -> 7; then E27's MIRROR_TAKE_BAND_FRAC, 25 -> 26)
    # E29 (FILL lane 29) adds the one switch line MIRROR_HAND_EXIT (env_switch, the environment may only turn it OFF):
    # no knob of the hand's own besides it, no capped_env / min_wait_env moved (26 / 7 stand)
    assert rsrc.count("min_wait_env(") == 7 and rsrc.count("capped_env(") == 26
    assert "MIRROR_HAND" not in rsrc.replace("MIRROR_HAND_EXIT", "") and rsrc.count("env_switch(\"MIRROR_HAND_EXIT\"") == 1
    assert '"MIRROR_LOST_FILL_REREAD_S"' not in inspect.getsource(ml)
    assert sorted(x.name for x in (ROOT / "backend" / "migrations").glob("*.sql"))[-1].startswith("061_")
    assert "hand" not in (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "hand" not in ml._SKIP_CARRIED
    assert mi.VENUE_LEDGER_TOL_SHARES == 1.0


def test_e24_the_untouched_functions_are_byte_identical_to_the_tip():
    for name, digest in UNTOUCHED.items():
        assert _sha(getattr(ml, name)) == digest, name


def test_e24_every_name_is_emitted_here(monkeypatch, caplog):
    """The lane's four names, each driven once (the worker file's coverage
    read imports this)."""
    for fn in (test_e24_book_1156_the_hand_short_explains_the_venue_and_the_book_stays_live_on_its_ledger,
               test_e24_book_1075_the_hand_cover_is_adopted_as_the_books_exit_and_the_close_takes_the_flat_row,
               test_e24_the_log_unreadable_is_hand_unread_and_todays_freeze,
               test_e24_a_hand_net_that_leaves_the_comparison_outside_the_tolerance_is_ambiguous_either_way):
        ml._hand_read_at.clear()                       # book ids repeat across this file's pools
        ml._disagree_fill_read_at.clear()
        ml._lost_fill_read_at.clear()
        fn(monkeypatch)
    ml._hand_read_at.clear()


# ------------------------------------------------ (10) the presets, the docs


def test_e24_the_presets_print_the_hand_beside_manual_and_registered_and_parse():
    pglast = pytest.importorskip("pglast")
    text = YML.read_text()
    fz = text[text.index("mirror-frozen) SQL=\"") + len("mirror-frozen) SQL=\""):]
    fz = fz[:fz.index('"; TO=')]
    assert "AS registered, left(COALESCE((b.last_plan->'hand')::text, ''), 120) AS hand, round(b.his_net::numeric, 1) AS his_net" in fz
    bk = text[text.index("SQL=\"SELECT b.id, b.us_market_slug AS slug, b.condition_id"):]
    bk = bk[len("SQL=\""):bk.index('"; TO=')]
    assert "b.standing_row_id AS row, left(COALESCE((b.last_plan->'hand')::text, ''), 160) AS hand, left(b.last_plan::text, 1200) AS last_plan" in bk
    for sql in (fz, bk.replace("$BK", "1")):
        tree = pglast.parse_sql(sql)
        assert all(type(s.stmt).__name__ == "SelectStmt" for s in tree), "read-only"
    # the hourly gains nothing: its nine presets and the frozen read's text are untouched by the column
    assert text.count("last_plan->'hand'") == 3          # the two presets and the comment over mirror-frozen
    # the help arm and the labels' order stand (no new label): `hourly` last
    labels = re.findall(r"^\s{16}([a-z0-9-]+|book=\*|paired-day=\*|paired-ratio=\*|tennis-witness=\*)\)", text, re.M)
    assert labels[-1] == "hourly"


def test_e24_the_docs_name_the_rule_and_docs_63_names_986s_hand_buy():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E24 -- .* \(2026-09-09, FILL lane 24\)", doc, re.M), "the E24 section header"
    sec = doc[doc.index("## 64. E24"):]
    for k in NEW_NAMES + ("_hand_net", "_book_hand_reduce", "_hand_closing", "trade_own_order", "isAggressor",
                          "MANUAL_ORDER_INDICATOR_MANUAL", "MIRROR_LOST_FILL_REREAD_S", "VENUE_LEDGER_TOL_SHARES",
                          "mirror_hand:", "test_e24_hand_fills.py", "1156", "986", "1075", "1129", "CCZ08XN74SX4",
                          "CCZ2TT1YCT3P", "CCX413N48SX0", "CCYC06E66SJH", "CCXMNEBPRSJH", "hand_adds",
                          "_frozen_venue_own", "wrong_sign_hold", "MIRROR_FROZEN_EXITS", "self", "own_order_id"):
        assert k in sec, k
    e23 = doc[doc.index("## 63. E23"):doc.index("## 64. E24")]
    assert "CCX413N48SX0" in e23 and "802.67" in e23 and "E24" in e23
