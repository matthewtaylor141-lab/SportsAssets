"""E30 (2026-09-10, FILL lane 30): A POST-ONLY REJECTION IS READ AND KEPT,
AND A REST THE VENUE REJECTS TICK AFTER TICK BACKS OFF.

THE ROWS (hard2/book_1383_0234.txt, the book=1383 read at 02:34:57Z). Book
1383 aec-itfme-sanshi-saktan-2026-09-10, ORDER_INTENT_BUY_SHORT, live, the
flip reopen after book 1360 closed at 02:31:12Z (row 27); his_net -1,457.4,
target -145, ledger 0, venue 0 (row 4); the plan's price and his_level 0.89,
the venue bid 0.75 / ask 0.76 (row 5). The orders: 8561 (02:31:20), 8562,
8564, 8566, 8567, 8569, 8570, 8572, 8574 (02:33:53) -- NINE 'increase
SELL_LONG GTC 145 @0.89' rows, each 'rejected / post_only_rejected /
post_only_rejected:400', one per tick for 2.5 minutes (rows 8-16); then 8576
at 02:34:05, the SAME order at the SAME wire, 'open / new' (row 17). A SELL
at 0.89 above an ask of 0.76 cannot cross the book, so the nine 400s were
not the venue's crossing refusal, and nothing on file says what they were:
mirror-refusals at 02:34:40Z printed the receipt column EMPTY on every
rejected row (row 29); the mirror-tick 'recent' block at 02:33:14Z showed
take_disarmed market_away then post_only_rejected code 400 four times
inside 60 s (row 31); the workers' log filtered 'post_only' over 23:37-02:37Z
carried no line naming the venue's body (row 33).

THE RULE (docs 73). (A) The rejected row's receipt is written from the
adapter's raw (the named fields and the body's head, through U13's bound),
ONE WARNING per process per (book, status code), the reason 'post_only_
rejected:<code>' gaining ':cross' only when the raw's own post_only_cross
names the crossing shape. (B) Three consecutive rejections at one side,
wire and code hold the book's REST for MIRROR_POST_ONLY_BACKOFF_S after
the last one (the plan's `hold: post_only_backoff`, no GTC placed, census
once per held plan); the take inside E27's band and every exit run as
today; after the wait the rest is tried once more. (C) The switch
MIRROR_POST_ONLY_BACKOFF: OFF is 66144cf for the backoff; the record is not
behind it.

The world: the worker suite's short world on the E5 pool -- his 100 long
against 1,557.4 other (= -1,457.4; his last move the other token at 0.11, so
his level in long space is 0.89), a flat BUY_SHORT book at ratio 0.1 (the
target 0.1 x -1,457.4 = -145.74 -> -145), the quote 0.75 / 0.76, the rest
at the short wire ceil(max(0.89, 0.76)) = 0.89 for 145. The venue's 400
body is a FIXTURE ({"message": "order rejected", "code": "ORDER_REJECTED"}):
nothing on file carries the venue's words -- that absence is the incident.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import types

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests import test_e27_take_tolerance as e27
from tests.test_e29_hand_exit import (
    KEY as KEY_1317, _book_1317, _held_plan, _http_1317, _memo_entry, _pool_1317, _v1317,
)
from tests.test_e5_frozen_exits import _E5Pool
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails ride along
    BUY, CID, GTC_TIF, IOC_TIF, M, N, NOW, SELL, SHORT, SLUG, _NoClose, _Venue, _armed, _census, _fill, _his,
    _mkt, _places, _ratio_fills, _run, _short_book, _shorts_on, _tick,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("post_only_backoff",)
HIS_NET = -1457.4                 # book_1383_0234 row 4: his_net -1457.4
OTHER = 1557.4                    # his 100 long against 1,557.4 other = -1,457.4
TARGET = -145                     # row 4: target -145 (0.1 x -1,457.4 = -145.74 -> -145)
HIS = 0.89                        # row 5: his_level 0.89 (his last move the other token at 0.11)
BID, ASK = 0.75, 0.76             # row 5
QTY, WIRE = 145, 0.89             # rows 8-17: 145 @0.89
CODE = 400                        # rows 8-16: post_only_rejected:400
WAIT = 60.0                       # MIRROR_POST_ONLY_BACKOFF_S's default
# a FIXTURE body: the SDK's client raises BadRequestError(message, response=, body=response.json()) on a 400
# (polymarket_us/client.py _handle_error_response) and the adapter keeps message / body / status_code / the
# exception's class name in raw (pmus._post_only_refusal); the venue's real words are not on file
BODY = {"message": "order rejected", "code": "ORDER_REJECTED"}
RAW_400 = {"status_code": 400, "error": "order rejected", "error_type": "BadRequestError", "body": BODY}
# the functions this lane leaves byte for byte, hashed on 66144cf
UNTOUCHED = {
    "_act": "59efb48ba79f793c", "_exit_take": "750acd709c826566", "_frozen_exit": "ef478fabdfa2ccc0",
    "_entry_take": "2266c2b346674491", "_flatten_vanished": "22930dc6e3e85816", "_maybe_close_episode": "59e28ff01f960660",
    "_fast_book": "286e6fa4663c3887", "_fast_gate": "1932811194268668", "_fast_candidate": "922585ffb6856f70",
    "_walk_candidate": "9c990feba5fdeb57", "_tick": "265461d33df59da6", "_fast_tick": "c364a8f6ed7f9b3b",
    "_place": "ab568476817cf795", "_wire_for": "a2d57ccd3742dc61", "_book_delta": "e5363576f6d0a575",
    "_finish_order": "1db222463610e38c", "_cancel_open_for": "639e841a3d2109eb", "_cancel_and_settle": "953ba5587d2ad423",
    "_ioc_reread": "cd3dbab5e5819257", "_write_plan": "dadc0a9064f21d53", "_disarm_take": "fd41b2f10a4ba39d",
    "_hand_exited": "2a446c318de4efa5", "_raw_rate_limit": "13bbb840a5cc60fc", "_refusal_receipt": "fdeaa22b213451f9",
    "_short_take_band": "cef97e6aacfd6875", "_take_band": "bd745846c0afd55d", "_short_wire": "25efb8c189941579",
    "_room_qty": "458b3fea5e2d235b", "_walk_reread": "e52eb75d62004a4a", "_book_fill": "60251768f66c9816",
    "_hand_net": "b3c3f0b2d40cbf06", "_increases_refusal": "f984cc09584bd265", "_increase_recheck": "8803d82bd627354e",
    "_lost_response": "14ebcadc6c89e457", "_place_rate_limited": "2b636c25d73a3b9c",
}
RULES_UNTOUCHED = {
    "take_arms": "b0712205d38eeea7", "take_allowed": "dc3079622052b6ff", "at_or_through": "4aece58b61ee21bc",
    "order_decision": "b22c4fbc29d68662", "rest_decision": "b1962f2cbb21c6c4", "env_switch": "68fcff0553b43e26",
    "min_wait_env": "1f815d26a9e3faeb", "capped_env": "ccda6c0b56efa53c", "wire_side": "281caf3a1cbe3520",
    "leg_action": "780780069c3a8eb4", "exit_terms": "2e4cd4a9edeb10a9",
}
PLACE_RESERVED_ON_TIP = "6c83b8e547c83e0a"    # _place_reserved on 66144cf (the lane's lines excised below)
TICK_BOOK_ON_TIP = "f8b3aa98172bf578"         # _tick_book on 66144cf (the lane's block excised below)
RULES_ON_TIP = "7e521ec5ffa67f5f"             # mirror_live_rules on 66144cf (the lane's block excised below)
PMUS_UNTOUCHED = {"_post_only_refusal": "7becc8060b5ec9de", "_post_only_cross": "41341b4b46075c53"}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _sha_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- fixtures


def _reject(raw=None, ioc_fill=None):
    """A venue whose every REST is the adapter's post_only_rejected shape
    carrying `raw` (the HTTP 400 path, pmus._post_only_refusal's keys) and
    whose every IOC fills at the wire (`ioc_fill` None: the whole
    quantity; a number: that many)."""
    raw = RAW_400 if raw is None else raw

    def _place(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        if tif == IOC_TIF:
            f = float(qty) if ioc_fill is None else min(float(ioc_fill), float(qty))
            return {"ok": f > 0, "order_id": oid, "status": "filled" if f >= qty else "canceled",
                    "fill_price": price if f > 0 else None, "filled_shares": f, "raw": {"response": {"id": oid}}}
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": (dict(raw, preview={"marketSlug": slug, "price": price, "quantity": qty})
                                              if isinstance(raw, dict) else raw)}
    return _place


def _p1383(**kw):
    """The 1383 world: his 100 long against 1,557.4 other at 0.11 (his level 0.89)."""
    kw.setdefault("fills", _his(100.0, other_size=OTHER, other_px=1.0 - HIS))
    kw.setdefault("snap", {M: 100.0, N: OTHER})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _E5Pool(**kw)


def _b1383(p, ledger=0, **over):
    if ledger:
        return _short_book(p, ledger=ledger, avg=HIS, ratio=0.1, **over)
    return p.add_book(ledger=0, intent=SHORT, ratio=0.1, **over)


def _http_1383():
    return _mkt(100.0, OTHER)


def _v(**kw):
    kw.setdefault("bid", BID)
    kw.setdefault("ask", ASK)
    kw.setdefault("place", _reject())
    return _Venue(**kw)


def _rests(v):
    return [c for c in _places(v) if c[5] == GTC_TIF]


def _iocs(v):
    return [c for c in _places(v) if c[5] == IOC_TIF]


def _sells(v):
    return [c for c in _places(v) if c[4] is False]


def _buys(v):
    return [c for c in _places(v) if c[4] is True]


def _rows(p):
    return sorted(p.orders.values(), key=lambda o: o["id"])


def _warns(caplog):
    return [r for r in caplog.records if "post-only REJECTED" in r.getMessage()]


def _holds(book):
    return [x for x in ml._RECENT if x["what"] == "post_only_backoff" and x["book"] == book["id"]]


def _streak(book):
    return ml._post_only_streak.get(book["id"])


def _seed(book, side=SELL, wire=WIRE, ledger=0.0, n=3, code=CODE, last_at=NOW - 5.0):
    """A book three rejections deep at this side and wire, the last one
    five seconds ago -- the streak as _post_only_note leaves it."""
    ml._post_only_streak[book["id"]] = {"n": n, "side": side, "wire": wire, "price": wire, "code": code,
                                        "last_at": last_at, "ledger": ledger, "hold_since": None}


def _three(p, v, monkeypatch, http=None):
    """The 1383 shape's first three ticks: three rejected rests at 0.89."""
    _shorts_on(monkeypatch)
    b = _b1383(p)
    for now in (NOW, NOW + 30, NOW + 60):
        st = _tick(p, v, now=now, http=http or _http_1383())
        assert _census(st, "post_only_rejected") == 1 and _census(st, "post_only_backoff") == 0
    assert len(_rests(v)) == 3 and _streak(b)["n"] == 3
    return b


# -------------------------------------------- (1) the 1383 shape: the record, then the hold


def test_e30_book_1383_the_rejected_rest_keeps_the_body_logs_once_and_the_third_rejection_holds_the_rest(monkeypatch, caplog):
    """Ticks at NOW, +30, +60: the SELL_LONG GTC 145 @0.89 rejected 400
    each time (8561 / 8562 / 8564's shape) -- the row 'rejected', reason
    'post_only_rejected:400' (no ':cross': a 400 carries no crossing
    field), the receipt carrying status_code / error_type / error / the
    body's head and NOT the preview, the WARNING ONCE (the second and
    third rejections log nothing), census post_only_rejected as today.
    The fourth tick (+90) is HELD: the plan's hold post_only_backoff
    {since +90, until +120 (= the last rejection + 60), n 3, code 400}, no
    fourth GTC (the venue's call counter stays at three), census
    post_only_backoff 1 and post_only_rejected 0, the row's last_reason
    post_only_backoff, the recent entry once per hold. +100: held again
    (counted again, no new recent entry). +121: the wait has passed --
    the rest is tried ONCE MORE (rejected: n 4), +130: held again with a
    fresh wait (until +181), the second recent entry."""
    caplog.set_level(logging.WARNING, logger="sportsassets.workers.mirror_live")
    p = _p1383()
    v = _v()
    b = _three(p, v, monkeypatch)
    assert b["target"] == TARGET and b["intent"] == SHORT and b["ledger_net"] == 0
    assert [c[1:6] for c in _places(v)] == [(SLUG, WIRE, QTY, False, GTC_TIF)] * 3
    rows = _rows(p)
    assert len(rows) == 3
    for o in rows:
        assert (o["state"], o["side"], o["wire"], o["qty"], o["reason"]) == ("rejected", SELL, WIRE, QTY, "post_only_rejected:400")
        assert o["order_id"] is None and o["intent"] == SHORT
        assert o["receipt"] == {"status_code": 400, "error_type": "BadRequestError", "error": "order rejected",
                                "body": json.dumps(BODY)}, "the venue's words on the row; never the preview"
    assert len(_warns(caplog)) == 1, "one WARNING per process per (book, code)"
    msg = _warns(caplog)[0].getMessage()
    assert f"book {b['id']}" in msg and "SELL_LONG" in msg and "x145 @0.89" in msg and "status 400" in msg and "order rejected" in msg
    assert "hold" not in b["last_plan"] and b["last_reason"] == "post_only_rejected"
    # the fourth tick: HELD
    st4 = _tick(p, v, now=NOW + 90, http=_http_1383())
    lp = b["last_plan"]
    assert lp["hold"] == "post_only_backoff" and lp["kind"] == "increase" and lp["side"] == SELL and lp["qty"] == QTY
    assert lp["post_only_backoff"] == {"since": NOW + 90, "until": NOW + 60 + WAIT, "n": 3, "code": 400}
    assert lp["target"] == TARGET and lp["price"] == HIS and lp["his_level"] == HIS
    assert len(_places(v)) == 3, "no fourth GTC inside the wait"
    assert _census(st4, "post_only_backoff") == 1 and _census(st4, "post_only_rejected") == 0
    assert b["last_reason"] == "post_only_backoff" and len(_rows(p)) == 3
    assert [(x["n"], x["code"], x["until"]) for x in _holds(b)] == [(3, 400, round(NOW + 60 + WAIT, 1))]
    assert len(_warns(caplog)) == 1
    # +100: held again -- counted per held plan, the recent entry once per hold
    st5 = _tick(p, v, now=NOW + 100, http=_http_1383())
    assert _census(st5, "post_only_backoff") == 1 and len(_places(v)) == 3 and len(_holds(b)) == 1
    assert b["last_plan"]["post_only_backoff"]["since"] == NOW + 90 and b["last_reason"] == "post_only_backoff"
    # +121: the wait has passed -- the rest is tried once more; rejected again
    st6 = _tick(p, v, now=NOW + 121, http=_http_1383())
    assert len(_rests(v)) == 4 and _census(st6, "post_only_rejected") == 1 and _census(st6, "post_only_backoff") == 0
    assert "hold" not in b["last_plan"] and _streak(b)["n"] == 4 and _streak(b)["last_at"] == NOW + 121
    assert b["last_reason"] == "post_only_rejected" and len(_warns(caplog)) == 1
    # +130: held again, a fresh wait from the fourth rejection
    st7 = _tick(p, v, now=NOW + 130, http=_http_1383())
    assert _census(st7, "post_only_backoff") == 1 and len(_places(v)) == 4
    assert b["last_plan"]["post_only_backoff"] == {"since": NOW + 130, "until": NOW + 121 + WAIT, "n": 4, "code": 400}
    assert [(x["n"], x["until"]) for x in _holds(b)] == [(3, round(NOW + 60 + WAIT, 1)), (4, round(NOW + 121 + WAIT, 1))]
    assert b["state"] == "live" and b["ledger_net"] == 0


def test_e30_with_the_switch_off_the_fourth_tick_places_the_rest_as_66144cf_does(monkeypatch, caplog):
    """THE PIN THAT NAMES THE DEFECT. MIRROR_POST_ONLY_BACKOFF off: four
    ticks, four rejected GTC rests at 0.89 (8561 .. 8566's shape), no
    hold, post_only_backoff 0 on every tick -- and the record is NOT
    behind the switch: every row keeps the receipt, the WARNING once,
    the count still advances (a measurement)."""
    caplog.set_level(logging.WARNING, logger="sportsassets.workers.mirror_live")
    monkeypatch.setattr(rules, "MIRROR_POST_ONLY_BACKOFF", False)
    p = _p1383()
    v = _v()
    b = _three(p, v, monkeypatch)
    st4 = _tick(p, v, now=NOW + 90, http=_http_1383())
    assert len(_rests(v)) == 4 and _census(st4, "post_only_rejected") == 1 and _census(st4, "post_only_backoff") == 0
    assert "hold" not in b["last_plan"] and "post_only_backoff" not in b["last_plan"] and b["last_reason"] == "post_only_rejected"
    assert all(o["receipt"]["status_code"] == 400 and o["reason"] == "post_only_rejected:400" for o in _rows(p))
    assert len(_warns(caplog)) == 1 and _streak(b)["n"] == 4 and not _holds(b)


# -------------------------------------------- (2) under the hold: the takes and the exits fire


def test_e30_the_at_level_take_fires_under_the_hold_and_the_accepted_ioc_resets_the_count(monkeypatch):
    """Held after three rejections; the fifth tick's book is locked at
    0.89 / 0.89 (the short wire ceil(max(0.89, 0.89)) = 0.89, so the plan's
    wire is the streak's and the hold stands) -> the take at the wire is
    an IOC, not post-only: ONE SELL IOC at 0.89 for 145 goes out
    (decision 'take'), no GTC, the ledger -145, the accepted placement
    resets the count (the streak entry gone); the plan of that tick still
    reads the hold. A partial fill (45): the IOC goes, the remainder's
    rest is NOT placed under the held plan; the next tick (the count
    reset, the quote back at 0.75 / 0.76) rests the 100 at 0.89 as today
    (a remainder of 45 at 0.11 of collateral a share is $4.95, under
    mi.plan's $5 dead band, so the fixture fills 45 and leaves 100)."""
    p = _p1383()
    v = _v()
    b = _three(p, v, monkeypatch)
    v5 = _Venue(bid=HIS, ask=HIS, place=_reject())
    st5 = _tick(p, v5, now=NOW + 90, http=_http_1383())
    assert [c[1:6] for c in _places(v5)] == [(SLUG, HIS, QTY, False, IOC_TIF)]
    assert _census(st5, "post_only_backoff") == 1 and _census(st5, "take_placed") == 1 and _census(st5, "take_first") == 1
    assert _census(st5, "rest_placed") == 0 and b["ledger_net"] == -QTY and b["open_order_id"] is None
    assert b["last_plan"]["hold"] == "post_only_backoff" and b["last_plan"]["decision"] == "take"
    assert _streak(b) is None, "the accepted IOC ends the streak"
    o = [x for x in _rows(p) if x["tif"] == "IOC"]
    assert len(o) == 1 and o[0]["state"] == "filled" and o[0]["kind"] == "take" and o[0]["wire"] == HIS
    # the partial fill: the IOC goes, the remainder rests on the NEXT tick, not under the held plan
    p2 = _p1383()
    v2 = _v()
    b2 = _three(p2, v2, monkeypatch)
    v5b = _Venue(bid=HIS, ask=HIS, place=_reject(ioc_fill=45.0))
    st = _tick(p2, v5b, now=NOW + 90, http=_http_1383())
    assert [c[1:6] for c in _places(v5b)] == [(SLUG, HIS, QTY, False, IOC_TIF)] and b2["ledger_net"] == -45
    assert _census(st, "take_placed") == 1 and _census(st, "rest_placed") == 0 and _streak(b2) is None
    v6 = _Venue(bid=BID, ask=ASK, held={SLUG: -45})                 # the venue accepting, holding the 45, the quote as before
    st6 = _tick(p2, v6, now=NOW + 120, http=_http_1383())
    assert [c[1:6] for c in _places(v6)] == [(SLUG, WIRE, QTY - 45, False, GTC_TIF)]
    assert _census(st6, "rest_placed") == 1 and _census(st6, "post_only_backoff") == 0 and "hold" not in b2["last_plan"]
    assert b2["open_order_id"] is not None and _streak(b2) is None


def test_e30_the_band_take_on_a_long_book_fires_under_the_hold(monkeypatch):
    """E27's band on a LONG add: his 0.52, the ask three cents over
    (0.55 / 0.56: `out`, the rest at his cent 0.52) rejected 400 three
    times -> held; the ask then inside the band (0.53 / 0.54, the rest's
    wire still 0.52) -> ONE BUY IOC at the band cent 0.54 for 300
    (take_in_band 1, decision 'take_in_band'), no GTC, the ledger 300, the
    count reset."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", 0.05)
    p = _E5Pool(fills=_his(300.0, long_px=0.52), snap={M: 300.0, N: 0.0}, snap_at=NOW - 40, ratio_fills=_ratio_fills())
    b = p.add_book(ledger=0)
    v = _Venue(bid=0.55, ask=0.56, place=_reject())
    for now in (NOW, NOW + 30, NOW + 60):
        st = _tick(p, v, now=now)
        assert _census(st, "post_only_rejected") == 1 and b["last_plan"]["take_band"]["verdict"] == "out"
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.52, 300, False, GTC_TIF)] * 3 and _streak(b)["n"] == 3
    st4 = _tick(p, v, now=NOW + 90)
    assert _census(st4, "post_only_backoff") == 1 and len(_places(v)) == 3 and b["last_plan"]["hold"] == "post_only_backoff"
    v5 = _Venue(bid=0.53, ask=0.54, place=_reject())
    st5 = _tick(p, v5, now=NOW + 100)
    assert [c[1:6] for c in _places(v5)] == [(SLUG, 0.54, 300, False, IOC_TIF)]
    assert _census(st5, "take_in_band") == 1 and _census(st5, "post_only_backoff") == 1 and _census(st5, "rest_placed") == 0
    assert b["ledger_net"] == 300 and b["last_plan"]["decision"] == "take_in_band" and _streak(b) is None


def test_e30_his_reduce_under_the_hold_covers_as_today(monkeypatch):
    """A held short of 100 (target -145, the 45 add rejected three
    times: the streak seeded as _post_only_note leaves it); his net falls
    -1,457.4 -> -500 on his SELL of 957.4 of the other token (witnessed,
    the venue's per-market read confirming) -> the target -50 and the
    cover of 50 -- a BUY of the long token, a reduce -- goes out as
    today: never judged, never held; post_only_backoff 0."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100.0, 0.31, NOW - 3000), _fill(N, "BUY", OTHER, 1.0 - HIS, NOW - 2000),
             _fill(N, "SELL", 957.4, 1.0 - HIS, NOW - 10, detected_at=NOW - 5, source="s1")]
    p = _p1383(fills=fills, snap={M: 100.0, N: OTHER - 957.4})
    b = _b1383(p, ledger=-100)
    _seed(b, ledger=-100.0)
    v = _Venue(bid=BID, ask=ASK, held={SLUG: -100}, place=_reject())
    st = _tick(p, v, http=_mkt(100.0, OTHER - 957.4))
    assert b["last_plan"]["target"] == -50 and b["last_plan"]["kind"] == "reduce"
    assert len(_buys(v)) == 1 and _buys(v)[0][3] == 50 and not _sells(v)
    assert _census(st, "post_only_backoff") == 0 and "hold" not in b["last_plan"] and b["ledger_net"] == -50


def test_e30_his_flip_under_the_hold_flattens_and_closes_as_today(monkeypatch):
    """Lane 5's flip on a held book: his net flips to +3,000 against our
    short of 100 (his last move the other token at 0.72: his buy-back in
    long space 0.28, the ask at his cent) -> the paired flatten covers 100
    by ONE BUY IOC as today (never held); the venue at 0 next tick -> the
    flip IS the close (cashed_out)."""
    _shorts_on(monkeypatch)
    p = _p1383(fills=_his(3100.0, other_size=100.0), snap={M: 3100.0, N: 100.0})
    b = _b1383(p, ledger=-100)
    _seed(b, ledger=-100.0)
    v = _NoClose(held={SLUG: -100}, bid=0.27, ask=0.28, ioc_fill=100.0)
    st = _tick(p, v, http=_mkt(3100.0, 100.0))
    assert _census(st, "sign_flip") == 1 and b["last_plan"]["sign_flip"] is True and b["ledger_net"] == 0
    assert len(_buys(v)) == 1 and _buys(v)[0][3] == 100 and _census(st, "post_only_backoff") == 0
    v2 = _NoClose(held={}, bid=0.27, ask=0.28)
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(3100.0, 100.0))
    assert b["state"] == "closed" and b["last_plan"]["close"] == "cashed_out" and _census(st2, "closed_cashed_out") == 1
    assert _census(st2, "post_only_backoff") == 0


def test_e30_e29s_hand_hold_takes_precedence_over_the_backoff(monkeypatch):
    """Book 1317's held shape (the memo naming the market, the row's
    hand_exit) with the streak ALSO three deep at its rest's side and
    wire: hand_held, never post_only_backoff -- the hold's name governs
    the row, nothing placed, the plan carries no post_only_backoff."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {KEY_1317: _memo_entry()}
    b = _book_1317(p, ledger=-1421, last_plan=_held_plan())
    _seed(b, wire=0.31, ledger=-1421.0)
    v = _v1317(-1421)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held") == 1 and _census(st, "post_only_backoff") == 0 and not _places(v)
    assert b["last_plan"]["hold"] == "hand_held" and "post_only_backoff" not in b["last_plan"]
    assert b["last_reason"] == "hand_held" and _streak(b) is not None, "the streak stands; the hand hold is judged first"


# -------------------------------------------- (3) the wait, the acceptance, the resets


def test_e30_after_the_wait_the_accepted_placement_resets_the_count_and_nothing_holds(monkeypatch):
    """Held after three rejections; after the wait the venue accepts the
    same order at the same wire (8576's shape, 'open / new'): the rest
    placed, the streak entry gone, no hold; the tick after reads the
    standing rest (open_order_pending) and holds nothing."""
    p = _p1383()
    v = _v()
    b = _three(p, v, monkeypatch)
    st4 = _tick(p, v, now=NOW + 90, http=_http_1383())
    assert _census(st4, "post_only_backoff") == 1 and len(_places(v)) == 3
    v2 = _Venue(bid=BID, ask=ASK)
    st5 = _tick(p, v2, now=NOW + 121, http=_http_1383())
    assert [c[1:6] for c in _places(v2)] == [(SLUG, WIRE, QTY, False, GTC_TIF)]
    assert _census(st5, "rest_placed") == 1 and _census(st5, "post_only_backoff") == 0 and _census(st5, "post_only_rejected") == 0
    assert b["open_order_id"] is not None and _streak(b) is None and "hold" not in b["last_plan"]
    o = _rows(p)[-1]
    assert (o["state"], o["order_id"], o["reason"], o["wire"], o["qty"]) == ("open", "oid-1", "increase", WIRE, QTY)
    st6 = _tick(p, v2, now=NOW + 150, http=_http_1383())
    assert _census(st6, "post_only_backoff") == 0 and len(_places(v2)) == 1 and _streak(b) is None


def test_e30_a_raw_that_is_not_a_dict_keeps_the_receipt_null_and_the_count_still_advances(monkeypatch, caplog):
    """The adapter's raw a list: no code, no body -- the row's reason
    'post_only_rejected:None', the receipt NULL (as before), the count
    advanced under code None; three of them hold the rest."""
    caplog.set_level(logging.WARNING, logger="sportsassets.workers.mirror_live")
    _shorts_on(monkeypatch)
    p = _p1383()
    b = _b1383(p)
    v = _v(place=_reject(raw=["not", "a", "dict"]))
    for now in (NOW, NOW + 30, NOW + 60):
        st = _tick(p, v, now=now, http=_http_1383())
        assert _census(st, "post_only_rejected") == 1
    assert all(o["reason"] == "post_only_rejected:None" and o["receipt"] is None for o in _rows(p))
    assert _streak(b)["n"] == 3 and _streak(b)["code"] is None
    assert len(_warns(caplog)) == 1 and "<raw not a dict>" in _warns(caplog)[0].getMessage()
    st4 = _tick(p, v, now=NOW + 90, http=_http_1383())
    assert _census(st4, "post_only_backoff") == 1 and len(_places(v)) == 3
    assert b["last_plan"]["post_only_backoff"]["code"] is None


def test_e30_the_receipt_write_failing_is_logged_once_and_changes_nothing_else(monkeypatch, caplog):
    """The receipt merge raising: the row still 'rejected' with its
    reason, the receipt NULL, census post_only_rejected as today, the
    count advanced, ONE log line per process over two failures."""
    caplog.set_level(logging.WARNING, logger="sportsassets.workers.mirror_live")
    _shorts_on(monkeypatch)
    p = _p1383()
    p.raise_on.append(("ml-order-receipt", RuntimeError("db down")))
    b = _b1383(p)
    v = _v()
    for now in (NOW, NOW + 30):
        st = _tick(p, v, now=now, http=_http_1383())
        assert _census(st, "post_only_rejected") == 1 and _census(st, "post_only_backoff") == 0
    assert all(o["state"] == "rejected" and o["reason"] == "post_only_rejected:400" and o["receipt"] is None for o in _rows(p))
    assert _streak(b)["n"] == 2 and len(_rests(v)) == 2
    fails = [r for r in caplog.records if "post-only receipt write failed" in r.getMessage()]
    assert len(fails) == 1 and len(_warns(caplog)) == 1


def test_e30_the_streak_unit_every_reset_the_wait_the_recent_entry_and_the_bound(monkeypatch):
    """_post_only_note / _post_only_held: no entry -> None; one and two
    -> None; three -> the hold {since, until, n, code} with the recent
    entry once per hold (the same `since` on the next tick); the wait
    passed -> None (the entry stands); a different side, wire or code
    starts a fresh count; a ledger that moved resets; a plan whose side or
    wire differs resets (the entry popped); an entry that is not a dict is
    popped; the switch off -> None with the entry kept; the bound drops
    the oldest book past POST_ONLY_STREAK_MAX."""
    t = ml._Tick(pool=None, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    book = {"id": 7, "ledger_net": 0, "us_market_slug": SLUG, "intent": SHORT}
    plan = types.SimpleNamespace(side=SELL, price=HIS)
    assert ml._post_only_held(t, book, plan, WIRE) is None
    for n in (1, 2):
        ent = ml._post_only_note(t, book, SELL, WIRE, HIS, 400)
        assert ent["n"] == n and ml._post_only_held(t, book, plan, WIRE) is None
    ent = ml._post_only_note(t, book, SELL, WIRE, HIS, 400)
    assert ent == {"n": 3, "side": SELL, "wire": WIRE, "price": HIS, "code": 400, "last_at": NOW, "ledger": 0.0, "hold_since": None}
    ml._RECENT.clear()
    t2 = ml._Tick(pool=None, pmus=None, http=None, now=NOW + 30, stats=ml._new_stats())
    assert ml._post_only_held(t2, book, plan, WIRE) == {"since": NOW + 30, "until": NOW + WAIT, "n": 3, "code": 400}
    assert [x for x in ml._RECENT if x["what"] == "post_only_backoff"] == [
        {"at": ml._RECENT[-1]["at"], "book": 7, "what": "post_only_backoff", "n": 3, "code": 400, "until": round(NOW + WAIT, 1)}]
    t3 = ml._Tick(pool=None, pmus=None, http=None, now=NOW + 40, stats=ml._new_stats())
    assert ml._post_only_held(t3, book, plan, WIRE)["since"] == NOW + 30 and len(ml._RECENT) == 1, "once per hold"
    t4 = ml._Tick(pool=None, pmus=None, http=None, now=NOW + WAIT, stats=ml._new_stats())
    assert ml._post_only_held(t4, book, plan, WIRE) is None and ml._post_only_streak[7]["n"] == 3, "the wait passed: tried once more"
    # a fourth rejection after the wait: n 4, the hold's clock fresh
    ml._post_only_note(t4, book, SELL, WIRE, HIS, 400)
    assert ml._post_only_streak[7]["n"] == 4 and ml._post_only_streak[7]["hold_since"] is None
    t5 = ml._Tick(pool=None, pmus=None, http=None, now=NOW + WAIT + 10, stats=ml._new_stats())
    assert ml._post_only_held(t5, book, plan, WIRE) == {"since": NOW + WAIT + 10, "until": NOW + 2 * WAIT, "n": 4, "code": 400}
    # a different code, wire or side starts a fresh count (three IDENTICAL rejections)
    assert ml._post_only_note(t5, book, SELL, WIRE, HIS, 429)["n"] == 1
    assert ml._post_only_note(t5, book, SELL, WIRE, HIS, 429)["n"] == 2
    assert ml._post_only_note(t5, book, SELL, 0.90, HIS, 429)["n"] == 1
    assert ml._post_only_note(t5, book, BUY, 0.90, HIS, 429)["n"] == 1
    # the plan's side or wire differing, or the ledger moved: the entry popped, nothing held
    _seed(book)
    assert ml._post_only_held(t2, book, types.SimpleNamespace(side=BUY, price=HIS), WIRE) is None and 7 not in ml._post_only_streak
    _seed(book)
    assert ml._post_only_held(t2, book, plan, 0.90) is None and 7 not in ml._post_only_streak
    _seed(book)
    moved = dict(book, ledger_net=-45)
    assert ml._post_only_held(t2, moved, plan, WIRE) is None and 7 not in ml._post_only_streak
    ml._post_only_streak[7] = "junk"
    assert ml._post_only_held(t2, book, plan, WIRE) is None and 7 not in ml._post_only_streak
    _seed(book)
    monkeypatch.setattr(rules, "MIRROR_POST_ONLY_BACKOFF", False)
    assert ml._post_only_held(t2, book, plan, WIRE) is None and ml._post_only_streak[7]["n"] == 3, "OFF: the entry kept, nothing held"
    monkeypatch.setattr(rules, "MIRROR_POST_ONLY_BACKOFF", True)
    # a lengthened wait read through the module at call time
    monkeypatch.setattr(rules, "MIRROR_POST_ONLY_BACKOFF_S", 120.0)
    _seed(book, last_at=NOW)
    assert ml._post_only_held(t4, book, plan, WIRE)["until"] == NOW + 120.0
    # the bound: the oldest book dropped past POST_ONLY_STREAK_MAX
    ml._post_only_streak.clear()
    for i in range(ml.POST_ONLY_STREAK_MAX):
        ml._post_only_streak[1000 + i] = {"n": 1, "side": SELL, "wire": WIRE, "code": 400, "last_at": float(i)}
    ml._post_only_note(t, {"id": 9999, "ledger_net": 0}, SELL, WIRE, HIS, 400)
    assert len(ml._post_only_streak) == ml.POST_ONLY_STREAK_MAX and 1000 not in ml._post_only_streak and 9999 in ml._post_only_streak


def test_e30_the_receipt_and_the_words_unit():
    """_post_only_receipt: the named fields and the body's head, never
    the preview; the head bounded at POST_ONLY_BODY_HEAD with the flag; a
    string, a list or a None body; a raw that is not a dict -> None; the
    whole through _refusal_receipt is valid JSON under U13's cap.
    _post_only_word: ':cross' only when take_arms reads the crossing
    shape AND the raw's own post_only_cross is True."""
    raw = dict(RAW_400, preview={"marketSlug": SLUG, "price": 0.89, "quantity": 145}, order_id=None)
    rec = ml._post_only_receipt(raw)
    assert rec == {"status_code": 400, "error_type": "BadRequestError", "error": "order rejected", "order_id": None,
                   "body": json.dumps(BODY)}
    assert "preview" not in rec and json.loads(ml._refusal_receipt(rec)) == rec
    big = ml._post_only_receipt(dict(RAW_400, body={"message": "m" * 5000}))
    assert len(big["body"]) == ml.POST_ONLY_BODY_HEAD and big["body_truncated"] is True and len(ml._refusal_receipt(big)) <= 4000
    assert ml._post_only_receipt({"status_code": 400, "body": "plain text"})["body"] == "plain text"
    assert ml._post_only_receipt({"status_code": 400, "body": ["a", 1]})["body"] == '["a", 1]'
    assert ml._post_only_receipt({"status_code": 400, "body": None}) == {"status_code": 400, "body": None}
    assert ml._post_only_receipt({}) == {} and ml._post_only_receipt(None) is None and ml._post_only_receipt(["x"]) is None
    assert ml._post_only_receipt("400") is None
    shape_200 = {"status_code": 200, "order_state": "ORDER_STATE_REJECTED", "execution_type": "EXECUTION_TYPE_REJECTED",
                 "post_only_cross": True, "reject_reason": "post_only_cross", "text": None, "order_id": "o-1"}
    assert ml._post_only_receipt(shape_200) == shape_200
    assert ml._post_only_word(shape_200) == ":cross" and ml._post_only_word(RAW_400) == ""
    assert ml._post_only_word(dict(RAW_400, post_only_cross=True)) == ":cross", "a 400 naming the cross by the field"
    assert ml._post_only_word({"status_code": 200, "post_only_cross": True}) == "", "no execution type: take_arms says no"
    assert ml._post_only_word({"status_code": 429, "post_only_cross": True}) == "" and ml._post_only_word(None) == ""
    assert ml._post_only_word(400) == "" and ml._post_only_word(["x"]) == ""
    assert ml._post_only_body_head(["x"]) == "<raw not a dict>" and "order rejected" in ml._post_only_body_head(RAW_400)


# -------------------------------------------- (4) the rails


def test_e30_the_switch_and_the_wait_read_from_the_environment_only_one_way_in_a_fresh_interpreter():
    """Lane 0a's fresh-interpreter pattern: MIRROR_POST_ONLY_BACKOFF absent /
    on / 1 / true / junk / blank -> True (the default); off / 0 / no /
    false -> False. MIRROR_POST_ONLY_BACKOFF_S 30 -> 60.0 (never shorter),
    120 -> 120.0 (lengthened), junk / -5 / absent -> 60.0."""
    src = inspect.getsource(rules)
    assert 'MIRROR_POST_ONLY_BACKOFF = env_switch("MIRROR_POST_ONLY_BACKOFF", True)' in src
    assert 'MIRROR_POST_ONLY_BACKOFF_S = min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0)' in src
    assert src.count("env_switch(") == 8 and src.count("min_wait_env(") == 8 and src.count("capped_env(") == 26
    assert "MIRROR_POST_ONLY_BACKOFF" in rules.__all__ and "MIRROR_POST_ONLY_BACKOFF_S" in rules.__all__
    assert rules.MIRROR_POST_ONLY_BACKOFF is True and rules.MIRROR_POST_ONLY_BACKOFF_S == 60.0
    code = ("import json; from sportsassets.analytics import mirror_live_rules as r; "
            "print(json.dumps([r.MIRROR_POST_ONLY_BACKOFF, r.MIRROR_POST_ONLY_BACKOFF_S]))")
    cases = [((None, None), (True, 60.0)), (("on", None), (True, 60.0)), (("1", None), (True, 60.0)),
             (("true", None), (True, 60.0)), (("junk", None), (True, 60.0)), (("", None), (True, 60.0)),
             (("off", None), (False, 60.0)), (("0", None), (False, 60.0)), (("no", None), (False, 60.0)),
             (("false", None), (False, 60.0)),
             ((None, "30"), (True, 60.0)), ((None, "120"), (True, 120.0)), ((None, "junk"), (True, 60.0)),
             ((None, "-5"), (True, 60.0)), ((None, "1e400"), (True, 60.0)), (("off", "600"), (False, 600.0))]
    for (sw, wait), want in cases:
        env = {k: v for k, v in os.environ.items() if k not in ("MIRROR_POST_ONLY_BACKOFF", "MIRROR_POST_ONLY_BACKOFF_S")}
        if sw is not None:
            env["MIRROR_POST_ONLY_BACKOFF"] = sw
        if wait is not None:
            env["MIRROR_POST_ONLY_BACKOFF_S"] = wait
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True,
                             cwd=str(ROOT / "backend"))
        got = json.loads(out.stdout.strip())
        assert (got[0] is want[0]) and got[1] == want[1], (sw, wait, out.stdout, out.stderr[-300:])


def test_e30_the_wait_only_lengthens_in_process(monkeypatch):
    monkeypatch.setenv("MIRROR_POST_ONLY_BACKOFF_S", "30")
    assert rules.min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0) == 60.0
    monkeypatch.setenv("MIRROR_POST_ONLY_BACKOFF_S", "120")
    assert rules.min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0) == 120.0
    monkeypatch.setenv("MIRROR_POST_ONLY_BACKOFF_S", "junk")
    assert rules.min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0) == 60.0
    monkeypatch.delenv("MIRROR_POST_ONLY_BACKOFF_S", raising=False)
    assert rules.min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0) == 60.0
    # the bounds live in the worker, not in rules: constants, never knobs
    assert ml.POST_ONLY_BACKOFF_N == 3 and ml.POST_ONLY_STREAK_MAX == 500 and ml.POST_ONLY_BODY_HEAD == 600
    rsrc = inspect.getsource(rules)
    assert "POST_ONLY_BACKOFF_N =" not in rsrc and "POST_ONLY_STREAK_MAX" not in rsrc and "POST_ONLY_BODY_HEAD" not in rsrc
    wsrc = inspect.getsource(ml)
    assert 'env_switch("MIRROR_POST_ONLY' not in wsrc and 'min_wait_env("MIRROR_POST_ONLY' not in wsrc, "read through rules alone"


# -------------------------------------------- (5) the names, the sites, the untouched records


def test_e30_the_census_place_the_emit_sites_and_the_records():
    keys = ml.CENSUS_KEYS
    assert keys[-14] == "post_only_backoff" and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-18:-14] == ("hand_exit", "hand_held", "hand_held_unread", "hand_exit_write_failed")
    assert keys[-23:-18] == ("walk_row_moved", "walk_row_unread", "walk_row_gone", "ledger_stale_reread", "ledger_stale_refused")
    assert keys[-27:-23] == ("exit_unconfirmed", "exit_confirmed", "exit_confirm_expired", "exit_flap_averted")
    assert keys[-31:-27] == ("hand_explained", "hand_adopted", "hand_unread", "hand_ambiguous")
    assert keys[-58] == "take_in_band" and keys[-1] == "cand_terminal_skipped"
    assert len(keys) == 247 and len(set(keys)) == len(keys) and keys.count("post_only_backoff") == 1
    assert ml._new_stats()["census"]["post_only_backoff"] == 0
    assert "post_only_backoff" not in rules.P2_INTEGRITY_COUNTERS
    src = inspect.getsource(ml)
    assert src.count('_mirror_stop("post_only_backoff", w)') == 1, "one emit site: the planner, once per held plan"
    tb = inspect.getsource(ml._tick_book)
    assert '_mirror_stop("post_only_backoff", w)' in tb
    # judged LAST among the holds: after E29's hand hold, on the add branch, with the plan's side and wire
    assert tb.index("hand_hold = _hand_exited(t, book, prior_plan)") < tb.index('if action == "add":') \
        < tb.index('plan["hold"] = inc_refusal') < tb.index("elif inc_refusal is None:") \
        < tb.index('po_hold = _post_only_held(t, book, p, _wire_for(p, his_px, r, book.get("intent"), None))') \
        < tb.index('plan["hold"] = "post_only_backoff"') < tb.index('plan["post_only_backoff"] = po_hold')
    assert tb.count("_post_only_held(") == 1 and "post_only" not in tb[:tb.index('if action == "add":')]
    # the guard in _place_reserved: a GTC add under the hold refused before any read; an IOC passes
    pr = inspect.getsource(ml._place_reserved)
    guard = 'if action == "add" and tif != "IOC" and plan.get("hold") == "post_only_backoff":'
    assert pr.count(guard) == 1 and pr.index(guard) < pr.index("_short_open_refusal(t)") < pr.index("orders = await _read_open(t)")
    assert pr.index(guard) < pr.index("_room_take(t, est)") and 'return "post_only_backoff"' in pr
    # the branch: the receipt through _refusal_receipt's bound onto the receipt merge, the word, the log, the count
    assert "await t.pool.execute(_SQL_ORDER_RECEIPT, o[\"id\"], _refusal_receipt(body))" in pr
    assert 'f"post_only_rejected:{code}{_post_only_word(raw)}"' in pr and "_post_only_log_once(book, side, wire, int(qty), code, raw)" in pr
    assert 'code = raw.get("status_code") if isinstance(raw, dict) else None' in pr
    assert "_post_only_note(t, book, side, wire, o.get(\"price\"), code)" in pr and pr.count("_post_only_streak.pop(book[\"id\"], None)") == 1
    assert pr.index("rules.take_arms(raw if isinstance(raw, dict) else code)") > pr.index("_SQL_ORDER_REASON"), "the arm as today"
    assert "_SQL_BOOK_ARM" in pr and "_raw_rate_limit(raw)" in pr, "the take arm and the 429 circuit stand"
    # the reset sits on the accepted placement, right after the id is persisted
    assert pr.index("_SQL_ORDER_PERSIST_ID") < pr.index("_post_only_streak.pop(book[\"id\"], None)") < pr.index("_SQL_BOOK_OPEN_ORDER")
    # the receipt: the named fields alone, never the preview / a header / a key
    assert ml._POST_ONLY_RECEIPT_KEYS == ("status_code", "error_type", "error", "post_only_cross", "execution_type",
                                          "order_state", "reject_reason", "text", "order_id")
    assert "preview" not in ml._POST_ONLY_RECEIPT_KEYS
    # _POST_ONLY_OK / _post_only_enabled untouched; the statements untouched
    assert inspect.getsource(ml._post_only_enabled) == ('def _post_only_enabled() -> bool:\n'
                                                        '    return (_POST_ONLY_OK and os.environ.get("PMUS_MIRROR_POST_ONLY", "on")\n'
                                                        '            .strip().lower() not in _OFF_VALUES)\n')
    assert ml._SQL_ORDER_REASON == "UPDATE mirror_orders SET reason = $2, updated_at = now() WHERE id = $1 /* ml-order-reason */"
    assert "ml-order-receipt" in ml._SQL_ORDER_RECEIPT and "COALESCE(receipt, '{}'::jsonb) || $2::jsonb" in ml._SQL_ORDER_RECEIPT
    assert "receipt = $4::jsonb" in ml._SQL_ORDER_REFUSED and ml._REFUSAL_RECEIPT_MAX == 4000
    # no migration (061 the newest), no decision word, render-ops.yml untouched
    mig = sorted(x.name for x in (ROOT / "backend" / "migrations").glob("*.sql"))
    assert mig[-1].startswith("061_")
    assert "backoff" not in (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "post_only_backoff" not in inspect.getsource(rules.order_decision)
    assert hashlib.sha256((ROOT / ".github" / "workflows" / "render-ops.yml").read_bytes()).hexdigest()[:16] == e27.RENDER_OPS_SHA


def test_e30_the_untouched_functions_are_byte_for_byte_66144cf_and_the_touched_ones_with_the_lanes_lines_excised():
    """Every exit path, the takes, the wrapper _place, the adapter's two
    refusal readers and rules.take_arms hashed on 66144cf; _place_reserved
    with this lane's lines put back as they were, _tick_book with the
    lane's block excised and the rules module with the lane's block
    excised hash to 66144cf's."""
    for name, digest in UNTOUCHED.items():
        assert _sha(getattr(ml, name)) == digest, name
    for name, digest in RULES_UNTOUCHED.items():
        assert _sha(getattr(rules, name)) == digest, name
    import sportsassets.pmus as pm
    for name, digest in PMUS_UNTOUCHED.items():
        assert _sha(getattr(pm, name)) == digest, name
    # _place_reserved: the six edits reversed
    pr = inspect.getsource(ml._place_reserved)
    pr = pr.replace("    global _POST_ONLY_OK, _post_only_receipt_logged\n", "    global _POST_ONLY_OK\n", 1)
    a = pr.index('    if action == "add" and tif != "IOC" and plan.get("hold") == "post_only_backoff":\n')
    b = pr.index('        return "post_only_backoff"\n', a) + len('        return "post_only_backoff"\n')
    pr = pr[:a] + pr[b:]
    a = pr.index("        # E30 (FILL lane 30): a raw that is not a dict carries no code and\n")
    b = pr.index('        code = raw.get("status_code") if isinstance(raw, dict) else None\n', a) + len(
        '        code = raw.get("status_code") if isinstance(raw, dict) else None\n')
    pr = pr[:a] + '        code = (raw or {}).get("status_code")\n' + pr[b:]
    a = pr.index("        # E30: the reason keeps its shape and gains ':cross' when the raw\n")
    b = pr.index('f"post_only_rejected:{code}{_post_only_word(raw)}")\n', a) + len('f"post_only_rejected:{code}{_post_only_word(raw)}")\n')
    pr = pr[:a] + '        await t.pool.execute(_SQL_ORDER_REASON, o["id"], f"post_only_rejected:{code}")\n' + pr[b:]
    a = pr.index("        # E30 (A): THE BODY IS KEPT.")
    b = pr.index('        _post_only_note(t, book, side, wire, o.get("price"), code)\n', a) + len(
        '        _post_only_note(t, book, side, wire, o.get("price"), code)\n')
    pr = pr[:a] + pr[b:]
    a = pr.index("    # E30: an ACCEPTED placement (a rest, an IOC) ends the book's\n")
    b = pr.index('    _post_only_streak.pop(book["id"], None)\n', a) + len('    _post_only_streak.pop(book["id"], None)\n')
    pr = pr[:a] + pr[b:]
    assert "post_only_backoff" not in pr and "_post_only_note" not in pr and "_post_only_word" not in pr
    assert _sha_text(pr) == PLACE_RESERVED_ON_TIP, "the placement byte for byte 66144cf, the lane's lines aside"
    # _tick_book: the one block excised
    tb = inspect.getsource(ml._tick_book)
    a = tb.index("        elif inc_refusal is None:\n            # E30 (FILL lane 30; book 1383)")
    b = tb.index('                _mirror_stop("post_only_backoff", w)\n', a) + len('                _mirror_stop("post_only_backoff", w)\n')
    tb = tb[:a] + tb[b:]
    assert "post_only" not in tb and _sha_text(tb) == TICK_BOOK_ON_TIP
    # the rules module: the switch, the wait and their comment excised (test_e28 does the same for 6c0830d's)
    src = inspect.getsource(rules)
    a = src.index("# A REST THE VENUE REJECTS TICK AFTER TICK BACKS OFF (E30")
    b = src.index('MIRROR_POST_ONLY_BACKOFF_S = min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0)\n') + len(
        'MIRROR_POST_ONLY_BACKOFF_S = min_wait_env("MIRROR_POST_ONLY_BACKOFF_S", 60.0)\n')
    src = (src[:a] + src[b:]).replace('    "MIRROR_POST_ONLY_BACKOFF", "MIRROR_POST_ONLY_BACKOFF_S",\n', "")
    assert "POST_ONLY_BACKOFF" not in src and _sha_text(src) == RULES_ON_TIP


def test_e30_every_name_is_emitted_here(monkeypatch, caplog):
    """The lane's one name, driven (the worker file's coverage read
    imports this)."""
    test_e30_book_1383_the_rejected_rest_keeps_the_body_logs_once_and_the_third_rejection_holds_the_rest(monkeypatch, caplog)


def test_e30_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E30 -- .* \(2026-09-10, FILL lane 30\)", doc, re.M), "the E30 section header"
    sec = doc[doc.index("## 73. E30"):]
    for k in NEW_NAMES + ("post_only_rejected", "MIRROR_POST_ONLY_BACKOFF", "MIRROR_POST_ONLY_BACKOFF_S", "POST_ONLY_BACKOFF_N",
                          "_post_only_receipt", "_post_only_held", "_post_only_note", "_post_only_word", ":cross",
                          "_refusal_receipt", "_SQL_ORDER_RECEIPT", "hold", "since", "until", "test_e30_post_only_body.py",
                          "1383", "8561", "8574", "8576", "0.89", "0.75", "0.76", "145", "book_1383_0234", "BadRequestError",
                          "_handle_error_response", "_post_only_refusal", "take_arms", "hand_held", "receipt"):
        assert k in sec, k
