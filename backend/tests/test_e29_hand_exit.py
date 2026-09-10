"""E29 (2026-09-10, FILL lane 29): THE DESK'S EXIT ENDS THE BOOK'S ADDS.
Owner (~00:20Z), verbatim: "I've manually cashed out the same play 4
different times and money keeps getting added to it (Tormo)".

THE ROWS (hard2/book_1317_0022.txt, the book=1317 read at 00:22:53Z).
Book 1317 aec-wta-kaique-sartor-2026-09-09 (his wta-quevedo-tormo-2026-09-
09; h2317_db row 1199), ORDER_INTENT_BUY_SHORT, live, last_reason 'on
target', opened 21:56:42Z, his_net -24,600.6, target -2,460, ledger -2,460,
venue -2,460, avg_cost 0.41, his_level 0.29 (row 239); the standing row
412342 BUY 2,460.78 @0.41 (row 281). The increases that FILLED after the
desk's hand covers (rows 270-276): 8285 2,136 @0.28 (23:56:23 -> 00:04:25),
8320 1,404 @0.31 reason 'hand_reduce' (00:18:18 -> 00:18:39), 8322 take IOC
1,602 avg 0.3802 (00:19:58 -> 00:19:59), 8323 take IOC 2,460 avg 0.41
(00:21:05 -> 00:21:07) -- the last one the WHOLE target, 106 s after the
desk had cashed the play out (the brief's figure; the cover's clock is in
the venue's trade log, not in the file); 8310 increase 1,039 @0.28 cancelled
'hand_reduce' (00:06:28 -> 00:12:12, row 271) is E24's own cancel at the
adoption. THE MECHANISM (docs 64): the hand reduce is adopted as the
book's exit, the ledger falls, the target stands (his net unchanged), and
the next plan reads 'increase toward target'. THE RULE (docs 71): an
adopted hand reduce MARKS the book hand-exited (the plan's `hand_exit`,
the process flag, the `mirror_hand_exit` memo); a hand-exited book never
adds again (`hand_held`); the market opens no new book for the same whale
while the memo holds; his exits still move ours; the operator's
mirror-hand-release preset lifts the hold by book id.

The worlds are E24's fixtures on the E5 pool (the venue trade-log fake
with 'manual' rows printed as our own aggressor order); the 1317 shape is
his 100 long against 24,700.6 other (= -24,600.6; his last move the other
token at 0.71, so his level in long space is 0.29), a BUY_SHORT book of
2,460 at 0.41, ratio 0.1, the quote 0.30 / 0.31 (8320's 0.31 wire).
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e24_hand_fills import (  # noqa: F401 -- the autouse rails ride along
    AT, HAND_SINCE, _hand, _hand_reads, _reads, _record, _recent, _venue,
)
from tests.test_e5_frozen_exits import _E5Pool
from tests.test_e9_fast_path import _fast, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, GTC_TIF, M, N, NOW, SELL, SHORT, SLUG, _NoClose, _armed, _census, _fill, _his, _mkt, _places,
    _ratio_fills, _run, _short_book, _shorts_on, _tick,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
YML = ROOT / ".github" / "workflows" / "render-ops.yml"
NEW_NAMES = ("hand_exit", "hand_held", "hand_held_unread", "hand_exit_write_failed")
KEY = f"rn1:{CID}"
HIS_NET = -24600.6                  # book_1317_0022 row 239: his_net -24600.6
OTHER = 24700.6                     # his 100 long against 24,700.6 other = -24,600.6
LEDGER = -2460                      # row 239: ledger -2460, target -2460 at ratio 0.1
COVER_1 = 1039.0                    # 8310's quantity (row 271): the desk's first cover on the fixture
COVER_2 = 1421.0                    # the rest of the leg: 2,460 - 1,039
# the functions this lane leaves byte-identical, hashed on 6c0830d in the lane's worktree; re-pinned at landing
# over E28 (cdf0742) where E28's guarded ledger write moved _book_hand_reduce (676757a66cee3a0a -> 9e883d268cdb80c4)
UNTOUCHED = {
    "_book_hand_reduce": "9e883d268cdb80c4", "_hand_closing": "533d514f88e0dd1d", "_act": "2e7043299fbf834a",
    "_frozen_exit": "ef478fabdfa2ccc0", "_flatten_vanished": "7f27e3b041da0c76",
    "_maybe_close_episode": "59e28ff01f960660", "_fast_book": "286e6fa4663c3887",
    "_fast_candidate": "922585ffb6856f70", "_fast_gate": "1932811194268668", "_walk_candidate": "9c990feba5fdeb57",
    "_note_candidate_refusal": "03294f12328e88af", "_note_reopen_refused": "7cd3a2894da0516c",
    "_reopen_of": "db0a83b7629c4254", "_flip_since": "3cdab3ea4d75e7c5", "_cancel_open_for": "639e841a3d2109eb",
    "_place": "f559a52bfb610ef3", "_place_reserved": "a83a3e9473eb7112",
    "_lost_fill_adopt": "61c67ae8946f3af4",
    "_disagree_fill_adopt": "4f1f100ae137d248", "_thaw": "62950633c3de6c96", "_quiet_skip": "d32699f6460eb856",
    "_hand_log_fills": "c101574ac7409810", "_hand_adopted_record": "2622dd2388dd7fe9",
    "_load_cand_memo": "297e1bf68db09237", "_persist_cand_memo": "d6ff73ade8bba13c",
}
HAND_NET_ON_TIP = "dc37d19b62d8c6fd"      # _hand_net on 6c0830d and on cdf0742 (E28 left it), the ONE new call site excised
# E31 (FILL lane 31, 2026-09-10): the read-only `maker-rests` preset beside take-band (and its copy in the
# hourly) and the need_confirm `mirror-post-only-rearm` beside mirror-rearm -- dea4c2e5a9b03439 ->
# e6d09c17b185163b with this lane's block still excised; every other read-only preset byte for byte
# then the ops secrets commit (2026-09-10 13:51Z, 605cef3: `arg` and `render_key` read from the event payload
# on disk and masked first) moved the file without re-pinning here, so this constant was already red on the
# clean tip -- e6d09c17b185163b -> bbc132a2bb0a7980, measured; this lane's block still excised, no preset moved
# THE PAIR PROBE (2026-09-10): the read-only `pair-candidates` preset beside his-matched and its one help
# token -- bbc132a2bb0a7980 -> 63b8c5f373ca7383; mirror-hand-release's own block and every other read-only
# preset byte for byte, which is exactly what this assertion is here to prove
RENDER_OPS_ON_TIP = "206fb2f32dc6b32d"    # render-ops.yml minus this lane's block: 0b09a1ed2074c6fd on cdf0742 (the landing tip), then
                                          # 95b58cdb2a82406f when the heartbeat value column widened 2,400 -> 8,000 (2026-09-10 02:1xZ),
                                          # then re-cut for the read-only nfl-team preset beside nfl-rows (02:5xZ; b7d553a6aa4204c2)
                                          # and again for mirror-by-league / his-matched beside it (03:1xZ)


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _sha_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- fixtures


def _pool_1317(**kw):
    """The 1317 world: his 100 long against 24,700.6 other at 0.71."""
    kw.setdefault("fills", _his(100.0, other_size=OTHER, other_px=0.71))
    kw.setdefault("snap", {M: 100.0, N: OTHER})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _E5Pool(**kw)


def _http_1317():
    return _mkt(100.0, OTHER)


def _book_1317(p, ledger=LEDGER, **over):
    return _short_book(p, ledger=ledger, avg=0.41, ratio=0.1, target=LEDGER, **over)


def _cover(oid, qty, px, ts=AT):
    """The desk covering the short by hand: a MANUAL BUY of the long token,
    our own aggressor order (the SELL_SHORT intent), dated inside the book."""
    return _hand(oid, "BUY", "SELL_SHORT", qty, px, ts=ts, order_qty=qty, order_px=round(px + 0.01, 2),
                 tif="IMMEDIATE_OR_CANCEL")


def _v1317(held, trades=None, **kw):
    return _venue(held, trades=trades, bid=0.30, ask=0.31, **kw)


def _memo(p):
    return p.state.get("mirror_hand_exit")


def _hx(b):
    return (b.get("last_plan") or {}).get("hand_exit")


def _held_plan(memo=True, **over):
    """A hand-exited book's row as a restart finds it: the last plan carrying
    the mark and the hold beside the target."""
    plan = {"kind": "increase", "reason": "hand_held", "hold": "hand_held", "target": LEDGER, "net": HIS_NET,
            "at": NOW - 51.0, "ledger": -1421, "reduce_ref": {"target": LEDGER, "at": NOW - 100.0},
            "hand_exit": {"at": NOW - 300.0, "shares": COVER_1, "px": 0.31, "orders": ["H-1"],
                          "ledger_after": -1421, "memo": memo}}
    plan.update(over)
    return plan


def _memo_entry(at=NOW - 300.0, book_id=41):
    return {"at": at, "book_id": book_id, "slug": SLUG, "shares": COVER_1, "px": 0.31}


def _sells(v):
    return [c for c in _places(v) if c[4] is False]


def _buys(v):
    return [c for c in _places(v) if c[4] is True]


def _refusals(p, name):
    return [r for r in p.cand_refusals if r["refusal"] == name]


def _bbos(v):
    return [c for c in v.calls if c[0] == "bbo"]


# ------------------------------------------------ (1) the 1317 shape: the mark, then the hold


def test_e29_book_1317_the_hand_cover_marks_the_book_and_the_next_tick_holds_the_add(monkeypatch):
    """Ledger -2,460 on his -24,600.6; the desk covers 1,039 by hand @0.31
    (8310's quantity); the fresh walk reads -1,421 -> E24 adopts 1,039
    (hand_adopted 1, the ledger -1,421) AND this lane marks: hand_exit 1,
    the plan's hand_exit {shares 1039, px 0.31, orders [H-1], ledger_after
    -1421, memo True}, the memo 'mirror_hand_exit' carrying rn1:<cid>. The
    NEXT tick with his net unchanged: the target -2,460, the ledger
    -1,421, and NO SELL_LONG increase, no IOC, no rest -- hold hand_held,
    hand_held 1, the row's reason hand_held. A standing increase rest on
    the tick after is cancelled under hand_held."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    b = _book_1317(p)
    v = _v1317(-1421, trades=[_cover("H-1", COVER_1, 0.31)])
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_adopted") == 1 and _census(st, "hand_exit") == 1
    assert _census(st, "hand_held") == 0 and _census(st, "hand_exit_write_failed") == 0
    assert b["ledger_net"] == -1421 and b["last_plan"]["kind"] == "hand_adopted" and not _places(v)
    assert b["realized_pnl"] == pytest.approx((0.41 - 0.31) * COVER_1, abs=1e-4)
    assert _hx(b) == {"at": NOW, "shares": COVER_1, "px": 0.31, "orders": ["H-1"], "ledger_after": -1421, "memo": True}
    assert _memo(p) == {KEY: {"at": NOW, "book_id": b["id"], "slug": SLUG, "shares": COVER_1, "px": 0.31}}
    assert _record(p, b)["adopted"] == {"H-1": COVER_1}
    ent = _recent("hand_exit", b["id"])
    assert len(ent) == 1 and ent[0]["shares"] == COVER_1 and ent[0]["memo"] is True and ent[0]["key"] == KEY
    assert [c[1:] for c in _hand_reads(v)] == [(SLUG, HAND_SINCE)]
    # THE PIN THAT NAMES THE DEFECT: the next tick reads 'increase toward target' and is HELD
    st2 = _tick(p, v, now=NOW + 30, http=_http_1317())
    lp = b["last_plan"]
    assert lp["target"] == LEDGER and lp["ledger"] == -1421 and lp["kind"] == "increase"
    assert lp["hold"] == "hand_held" and lp["qty"] == 0 and lp["side"] is None and lp["reason"] == "hand_held"
    assert b["last_reason"] == "hand_held" and _census(st2, "hand_held") == 1 and _census(st2, "hand_exit") == 0
    assert not _places(v), "no SELL_LONG increase, no IOC, no rest"
    assert _hx(b)["shares"] == COVER_1 and _hx(b)["memo"] is True, "the record carried onto the held plan"
    assert len(_reads(v)) == 1, "the venue -1,421 agrees with the ledger: no hand read"
    assert b["state"] == "live" and _census(st2, "venue_ledger_suspect") == 0
    # a standing increase rest (placed by a tick between the hand fill and its read): cancelled under hand_held
    o = p.add_order(b, side=SELL, wire=0.31, qty=500, order_id="oid-add", state="open", kind="increase")
    v.rest("oid-add", "SELL", 0.31, 500)
    st3 = _tick(p, v, now=NOW + 60, http=_http_1317())
    assert p.orders[o["id"]]["state"] == "cancelled" and ("cancel", "oid-add", SLUG) in v.calls
    assert p.orders[o["id"]]["reason"] == "hand_held" and not _places(v)
    assert _census(st3, "hand_held") == 1 and b["last_plan"]["hold"] == "hand_held" and b["ledger_net"] == -1421


def test_e29_with_the_switch_off_the_same_tick_re_enters_as_8320_did(monkeypatch):
    """6c0830d byte for byte: no mark, no memo, and the tick after the
    adoption places the 1,039 SELL_LONG increase at 0.31 -- 8320's shape
    (1,404 @0.31 reason 'hand_reduce', book_1317_0022 row 274)."""
    _shorts_on(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_HAND_EXIT", False)
    p = _pool_1317()
    b = _book_1317(p)
    v = _v1317(-1421, trades=[_cover("H-1", COVER_1, 0.31)])
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_adopted") == 1 and b["ledger_net"] == -1421
    assert all(_census(st, k) == 0 for k in NEW_NAMES) and _memo(p) is None and "hand_exit" not in b["last_plan"]
    st2 = _tick(p, v, now=NOW + 30, http=_http_1317())
    pl = _sells(v)
    assert len(pl) == 1 and pl[0][1:4] == (SLUG, 0.31, int(COVER_1)) and pl[0][6] == SHORT, "the re-entry, as today"
    assert b["last_plan"]["kind"] == "increase" and "hold" not in b["last_plan"] and _census(st2, "hand_held") == 0
    assert "hand_exit" not in b["last_plan"] and _memo(p) is None


def test_e29_the_whole_book_covered_by_hand_stays_flat_and_held_and_closes_as_today_with_the_memo_standing(monkeypatch):
    """After the first cover (held on -1,421) the desk covers the remaining
    1,421 @0.41: adopted (hand_exit again, the record moved to the new
    cover), the ledger flat -- and while HE still holds -24,600.6 the flat
    book is HELD, never re-bought (8323's 2,460 never goes); the market
    then ends and the closing branch's own close takes the flat row
    (closed_cashed_out, as today), the memo entry standing after it."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    b = _book_1317(p)
    v = _v1317(-1421, trades=[_cover("H-1", COVER_1, 0.31)])
    _tick(p, v, http=_http_1317())
    _tick(p, v, now=NOW + 30, http=_http_1317())
    assert b["ledger_net"] == -1421 and b["last_plan"]["hold"] == "hand_held"
    v.portfolio.held[SLUG] = 0
    v.trades = [_cover("H-1", COVER_1, 0.31), _cover("H-2", COVER_2, 0.41, ts=AT + 200)]
    st3 = _tick(p, v, now=NOW + 60, http=_http_1317())
    assert _census(st3, "hand_adopted") == 1 and _census(st3, "hand_exit") == 1 and b["ledger_net"] == 0
    assert _record(p, b)["adopted"] == {"H-1": COVER_1, "H-2": COVER_2} and not _places(v)
    assert _hx(b)["shares"] == COVER_2 and _hx(b)["orders"] == ["H-2"] and _hx(b)["ledger_after"] == 0
    assert _memo(p)[KEY]["shares"] == COVER_2 and _memo(p)[KEY]["at"] == NOW + 60
    # flat at target -2,460 while he holds: held every tick, the whole target never re-bought
    st4 = _tick(p, v, now=NOW + 90, http=_http_1317())
    assert _census(st4, "hand_held") == 1 and not _places(v) and b["state"] == "live"
    assert b["last_plan"]["target"] == LEDGER and b["last_plan"]["ledger"] == 0 and b["last_plan"]["hold"] == "hand_held"
    st5 = _tick(p, v, now=NOW + 120, http=_http_1317())
    assert _census(st5, "hand_held") == 1 and not _places(v) and b["ledger_net"] == 0
    # the market ends: the closing branch's own close of the flat row, the existing path
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st6 = _tick(p, v, now=NOW + 150, http=_http_1317())
    assert b["state"] == "closed" and _census(st6, "closed_cashed_out") == 1 and not _places(v)
    assert _memo(p)[KEY]["book_id"] == b["id"], "the memo holds for the market's life"


# ------------------------------------------------ (2) the market stays off


def test_e29_a_candidate_on_a_hand_exited_market_is_refused_before_any_venue_read(monkeypatch):
    """The memo names rn1:<cid>; the short world opens its book through the
    candidate stage -- refused hand_held BEFORE the mapping and before any
    venue read (no quote read, no trade-log read), the 054 row queued with
    the memo's slug, never reopen_refused (no mark); no book opens. The
    fast wake on the same market takes the same refusal."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    v = _v1317(0)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held") == 1 and _census(st, "hand_held_unread") == 0 and not p.books
    assert not _bbos(v) and not _reads(v) and not _places(v), "no venue read, no budget spent"
    rows = _refusals(p, "hand_held")
    assert len(rows) == 1 and rows[0]["us_slug"] == SLUG and rows[0]["mark"] is None and rows[0]["condition_id"] == CID
    assert _census(st, "reopen_refused") == 0 and _census(st, "unmapped") == 0
    # the wake: the same refusal through _fast_candidate -> _walk_candidate -> _tick_candidate
    _walk({})
    fs = _fast(p, v, http=_http_1317())
    assert _census(fs, "hand_held") == 1 and not p.books and not _bbos(v)


def test_e29_his_reduce_on_a_hand_exited_book_still_covers_as_today(monkeypatch):
    """The held book on -1,421 (the row's hand_exit and the memo); his net
    falls -24,600.6 -> -10,000.6 on his SELL of 14,600 of the other token
    (witnessed: clocked after the reference) and the venue's per-market
    read shows it (E25 confirms): the target -1,000 and the cover of 421
    -- a BUY of the long token -- goes out as today. Nothing held: the
    desk sold; his exits still move ours."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100.0, 0.31, NOW - 3000), _fill(N, "BUY", OTHER, 0.71, NOW - 2000),
             _fill(N, "SELL", 14600.0, 0.71, NOW - 10, detected_at=NOW - 5, source="s1")]
    p = _pool_1317(fills=fills, snap={M: 100.0, N: OTHER - 14600.0})
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    b = _book_1317(p, ledger=-1421, last_plan=_held_plan())
    v = _v1317(-1421)
    st = _tick(p, v, http=_mkt(100.0, OTHER - 14600.0))
    assert b["last_plan"]["target"] == -1000 and b["last_plan"]["kind"] == "reduce"
    assert len(_buys(v)) == 1 and _buys(v)[0][3] == 421 and not _sells(v)
    assert _census(st, "hand_held") == 0 and _census(st, "exit_confirmed") == 1 and "hold" not in b["last_plan"]
    assert _hx(b)["shares"] == COVER_1 and _memo(p)[KEY]["book_id"] == 41, "the mark and the memo stand across the cover"


def test_e29_his_flip_flattens_and_closes_as_today_and_the_reopen_is_refused_hand_held(monkeypatch):
    """Book 467's shape (lane 5) on a hand-exited book: his net flips to
    +29,900 against our short of 1,421 -> the flip's paired flatten covers
    as today (never held); the venue at 0 next tick -> the flip IS the
    close, the turn on the closed row; the tick after, the long side does
    NOT open: refused hand_held before any read (the whole market is off,
    both sides), no reopen_refused."""
    _shorts_on(monkeypatch)
    p = _pool_1317(fills=_his(30000.0, other_size=100.0), snap={M: 30000.0, N: 100.0})
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    b = _book_1317(p, ledger=-1421, last_plan=_held_plan())
    # E31 (FILL lane 31, 2026-09-10): the flip's paired flatten is a POST-ONLY
    # REST, not an IOC, so `ioc_fill` no longer fills it -- `lift` does, the
    # venue filling the fresh rest at create with `aggressor` False (a taker
    # hit us: a maker fill, booked as the IOC's fill booked). What this test
    # pins -- the flip covers, is never held, closes on the venue's zero and
    # the reopen is refused hand_held -- is unchanged.
    v = _NoClose(held={SLUG: -1421}, bid=0.30, ask=0.31, ioc_fill=1421.0, lift=1421.0)
    st = _tick(p, v, http=_mkt(30000.0, 100.0))
    assert _census(st, "sign_flip") == 1 and b["last_plan"]["sign_flip"] is True and b["ledger_net"] == 0
    assert len(_buys(v)) == 1 and _buys(v)[0][3] == 1421 and _census(st, "hand_held") == 0
    v2 = _NoClose(held={}, bid=0.30, ask=0.31)
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(30000.0, 100.0))
    assert b["state"] == "closed" and b["last_plan"]["close"] == "cashed_out" and _census(st2, "closed_cashed_out") == 1
    assert b["last_plan"]["turn"]["to"] == "ORDER_INTENT_BUY_LONG" and _hx(b) is not None
    v3 = _NoClose(held={}, bid=0.30, ask=0.31)
    st3 = _tick(p, v3, now=NOW + 60, http=_mkt(30000.0, 100.0))
    assert len(p.books) == 1 and _census(st3, "hand_held") == 1 and not _places(v3) and not _bbos(v3)
    assert _census(st3, "reopen_refused") == 0 and "reopen_refused" not in b["last_plan"]
    assert len(_refusals(p, "hand_held")) == 1


def test_e29_the_fast_wake_on_a_hand_exited_book_adds_nothing(monkeypatch):
    """The fast tick plans through _tick_book: the same hold, no add."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    b = _book_1317(p, ledger=-1421, last_plan=_held_plan())
    v = _v1317(-1421)
    _walk({SLUG: -1421.0})
    fs = _fast(p, v, http=_http_1317())
    assert _census(fs, "hand_held") == 1 and not _places(v) and _census(fs, "fast_tick_placed") == 0
    assert b["last_plan"]["hold"] == "hand_held" and b["last_plan"]["target"] == LEDGER and b["ledger_net"] == -1421


# ------------------------------------------------ (3) a restart, the memo, the row


def test_e29_a_restart_holds_both_ways_the_memo_alone_and_the_row_alone(monkeypatch):
    """A fresh process (the flag gone): (a) the memo names the market while
    the row's plan carries no mark -> held; (b) the row's plan carries the
    mark with its memo write still pending (memo False) while the memo
    lacks the entry -> held, and the entry is written this tick (the
    retry lands: the memo gains the key, the record reads memo True)."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    plan_a = {k: val for k, val in _held_plan().items() if k != "hand_exit"}
    b = _book_1317(p, ledger=-1421, last_plan=plan_a)
    v = _v1317(-1421)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held") == 1 and not _places(v) and b["last_plan"]["hold"] == "hand_held"
    assert "hand_exit" not in b["last_plan"], "the memo alone holds; the row gains no record it never had"
    # (b) the row alone, the write pending
    p2 = _pool_1317()
    p2.state["mirror_hand_exit"] = {}
    b2 = _book_1317(p2, ledger=-1421, last_plan=_held_plan(memo=False))
    v2 = _v1317(-1421)
    st2 = _tick(p2, v2, http=_http_1317())
    assert _census(st2, "hand_held") == 1 and not _places(v2) and b2["last_plan"]["hold"] == "hand_held"
    assert _memo(p2) == {KEY: {"at": NOW - 300.0, "book_id": b2["id"], "slug": SLUG, "shares": COVER_1, "px": 0.31}}
    assert _hx(b2)["memo"] is True and _census(st2, "hand_exit_write_failed") == 0 and _census(st2, "hand_exit") == 0


def test_e29_the_memo_unreadable_the_row_holds(monkeypatch):
    """The key malformed at the tick's start -> t.hand_exits None: the
    row's own record holds the add (hand_held), nothing written, the
    unreadable memo named once per process."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    p.state["mirror_hand_exit"] = "not an object"
    b = _book_1317(p, ledger=-1421, last_plan=_held_plan())
    v = _v1317(-1421)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held") == 1 and _census(st, "hand_held_unread") == 0 and not _places(v)
    assert b["last_plan"]["hold"] == "hand_held" and _hx(b)["memo"] is True
    assert p.state["mirror_hand_exit"] == "not an object", "nothing written over a memo nobody could read"
    st2 = _tick(p, v, now=NOW + 30, http=_http_1317())
    assert _census(st2, "hand_held") == 1 and not _places(v)


def test_e29_both_unreadable_on_a_live_book_holds_the_add_hand_held_unread(monkeypatch):
    """The memo malformed AND the row's last_plan unreadable: a read that
    cannot be made buys nothing -- hand_held_unread, no rest, no IOC."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    p.state["mirror_hand_exit"] = 7
    b = _book_1317(p, ledger=-1421, last_plan="{not json")
    v = _v1317(-1421)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held_unread") == 1 and _census(st, "hand_held") == 0 and not _places(v)
    assert b["last_plan"]["hold"] == "hand_held_unread" and b["last_reason"] == "hand_held_unread"
    assert b["last_plan"]["target"] == LEDGER and b["last_plan"]["kind"] == "increase"
    # the memo readable again and empty, the row still unreadable: the add as today (no record holds it)
    p.state["mirror_hand_exit"] = {}
    b["last_plan"] = "{not json"
    st2 = _tick(p, v, now=NOW + 30, http=_http_1317())
    assert all(_census(st2, k) == 0 for k in NEW_NAMES) and len(_sells(v)) == 1


def test_e29_the_closed_row_decides_a_candidate_when_the_memo_cannot_be_read(monkeypatch):
    """The memo malformed: a candidate on a market whose NEWEST CLOSED
    book's plan carries hand_exit is refused hand_held by that read (one
    bounded read, no venue read); the row read failing -> hand_held_unread
    (a read that cannot be made opens nothing); no closed book with the
    mark -> the candidate as today (a book opens)."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    p.state["mirror_hand_exit"] = "junk"
    p.add_book(ledger=0, state="closed", intent=SHORT, last_plan={"kind": "closing", "hand_exit": _held_plan()["hand_exit"]},
               closed_at=NOW - 500)
    v = _v1317(0)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held") == 1 and not _bbos(v) and len(p.books) == 1
    assert len(_refusals(p, "hand_held")) == 1 and [s for k, s, a in p.sent if "ml-book-flip" in s]
    # the row read raising
    p.raise_on.append(("ml-book-flip", RuntimeError("db")))
    st2 = _tick(p, v, now=NOW + 30, http=_http_1317())
    assert _census(st2, "hand_held_unread") == 1 and _census(st2, "hand_held") == 0 and not _bbos(v) and len(p.books) == 1
    assert len(_refusals(p, "hand_held_unread")) == 1
    # no closed book with the mark (a fresh world, the memo still unreadable): the book opens as today
    p3 = _pool_1317()
    p3.state["mirror_hand_exit"] = "junk"
    p3.add_book(ledger=0, state="closed", intent=SHORT, last_plan={"kind": "closing"}, closed_at=NOW - 500)
    v3 = _v1317(0)
    st3 = _tick(p3, v3, http=_http_1317())
    assert all(_census(st3, k) == 0 for k in NEW_NAMES) and len(p3.books) == 2 and _bbos(v3)


def test_e29_the_memo_write_failing_is_named_the_row_holds_and_the_write_lands_later(monkeypatch):
    """The 'mirror_hand_exit' write raising at the mark: hand_exit 1 and
    hand_exit_write_failed 1, the plan's record with memo False, no memo;
    the next tick holds (hand_held) and fails the write again (named
    again); the tick after, the write landing: the memo gains the key,
    the record reads memo True, nothing more named."""
    _shorts_on(monkeypatch)
    real = ml._write_state
    fail = {"on": True}

    async def _ws(pool, key, value):
        if key == "mirror_hand_exit" and fail["on"]:
            raise RuntimeError("db down")
        return await real(pool, key, value)
    monkeypatch.setattr(ml, "_write_state", _ws)
    p = _pool_1317()
    b = _book_1317(p)
    v = _v1317(-1421, trades=[_cover("H-1", COVER_1, 0.31)])
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_adopted") == 1 and _census(st, "hand_exit") == 1 and _census(st, "hand_exit_write_failed") == 1
    assert _hx(b)["memo"] is False and _memo(p) is None and b["ledger_net"] == -1421
    assert _record(p, b)["adopted"] == {"H-1": COVER_1}, "the adoption's own record is E24's, untouched"
    st2 = _tick(p, v, now=NOW + 30, http=_http_1317())
    assert _census(st2, "hand_held") == 1 and _census(st2, "hand_exit_write_failed") == 1 and not _places(v)
    assert _hx(b)["memo"] is False and _memo(p) is None and b["last_plan"]["hold"] == "hand_held"
    fail["on"] = False
    st3 = _tick(p, v, now=NOW + 60, http=_http_1317())
    assert _census(st3, "hand_held") == 1 and _census(st3, "hand_exit_write_failed") == 0 and not _places(v)
    assert _hx(b)["memo"] is True and _memo(p)[KEY]["shares"] == COVER_1 and _memo(p)[KEY]["book_id"] == b["id"]


def test_e29_the_release_lifts_the_hold_and_a_tick_that_read_the_row_before_it_carries_nothing_back(monkeypatch):
    """The preset removes the memo entry and the row's hand_exit in one
    statement: the next tick adds as today. The race: a tick that read the
    row BEFORE the release (its plan still carrying the record with memo
    True) does not carry it back -- the readable memo no longer names the
    market, so the record is dropped and the add goes out; the memo alone
    decides once it reads."""
    _shorts_on(monkeypatch)
    p = _pool_1317()
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    b = _book_1317(p, ledger=-1421, last_plan=_held_plan())
    v = _v1317(-1421)
    st = _tick(p, v, http=_http_1317())
    assert _census(st, "hand_held") == 1 and not _places(v)
    # the operator's release: both removed (the preset's statement, in the fake's terms)
    p.state["mirror_hand_exit"] = {}
    b["last_plan"] = {k: val for k, val in b["last_plan"].items() if k != "hand_exit"}
    st2 = _tick(p, v, now=NOW + 30, http=_http_1317())
    assert all(_census(st2, k) == 0 for k in NEW_NAMES) and len(_sells(v)) == 1 and _sells(v)[0][3] == int(COVER_1)
    assert "hand_exit" not in b["last_plan"] and "hold" not in b["last_plan"]
    # the race: the row still carries the record (memo True) while the memo no longer names the market
    p3 = _pool_1317()
    p3.state["mirror_hand_exit"] = {}
    b3 = _book_1317(p3, ledger=-1421, last_plan=_held_plan(memo=True))
    v3 = _v1317(-1421)
    st3 = _tick(p3, v3, http=_http_1317())
    assert all(_census(st3, k) == 0 for k in NEW_NAMES) and len(_sells(v3)) == 1
    assert "hand_exit" not in b3["last_plan"], "released: the record is not carried back onto the row"


# ------------------------------------------------ (4) the rails, the bound, the memo write


def test_e29_the_switch_reads_off_from_the_environment_only_in_a_fresh_interpreter():
    """Lane 0a's fresh-interpreter pattern: MIRROR_HAND_EXIT absent / on /
    1 / true / junk / blank -> True (the default; env_switch reads blank
    and a typo as the default, as every switch here); off / 0 / no /
    false -> False. The environment may only turn it OFF."""
    src = inspect.getsource(rules)
    # env_switch 5 -> 6 in the lane's worktree; 7 on the landed tree (E28's MIRROR_WALK_REREAD landed first)
    assert src.count('env_switch("MIRROR_HAND_EXIT", True)') == 1 and src.count("env_switch(") == 8
    assert "MIRROR_HAND_EXIT" in rules.__all__ and rules.MIRROR_HAND_EXIT is True
    code = "import json; from sportsassets.analytics import mirror_live_rules as r; print(json.dumps(r.MIRROR_HAND_EXIT))"
    for raw, want in ((None, True), ("on", True), ("1", True), ("true", True), ("junk", True), ("", True),
                      ("off", False), ("0", False), ("no", False), ("false", False)):
        env = {k: v for k, v in os.environ.items() if k != "MIRROR_HAND_EXIT"}
        if raw is not None:
            env["MIRROR_HAND_EXIT"] = raw
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True,
                             cwd=str(ROOT / "backend"))
        assert json.loads(out.stdout.strip()) is want, (raw, out.stdout, out.stderr[-300:])


def test_e29_the_memo_is_bounded_and_written_whole(monkeypatch):
    """HAND_EXIT_MEMO_MAX 500: the 501st entry drops the oldest `at`; a
    memo this tick could not read is read once more before the write so
    no other market's entry is lost; the write raising is False."""
    assert ml.HAND_EXIT_MEMO_MAX == 500 and ml._STATE_HAND_EXIT == "mirror_hand_exit"
    p = _pool_1317()
    t = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    t.hand_exits = {f"rn1:c{i}": {"at": float(i), "book_id": i, "slug": f"s{i}", "shares": 1.0, "px": 0.5}
                    for i in range(500)}
    assert _run(ml._hand_exit_memo_write(t, "rn1:new", {"at": 999.0, "book_id": 999, "slug": "sn", "shares": 2.0, "px": 0.4}))
    assert len(t.hand_exits) == 500 and "rn1:c0" not in t.hand_exits and "rn1:new" in t.hand_exits and "rn1:c1" in t.hand_exits
    assert p.state["mirror_hand_exit"] == t.hand_exits
    # the memo unreadable this tick: one re-read before the write, the standing entries kept
    t2 = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    t2.hand_exits = None
    assert _run(ml._hand_exit_memo_write(t2, "rn1:x", {"at": 1000.0}))
    assert "rn1:x" in p.state["mirror_hand_exit"] and "rn1:new" in p.state["mirror_hand_exit"] and t2.hand_exits is not None
    p.state["mirror_hand_exit"] = "junk"
    t3 = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    assert _run(ml._hand_exit_memo_write(t3, "rn1:y", {"at": 1.0})) is False and p.state["mirror_hand_exit"] == "junk"
    p.state["mirror_hand_exit"] = {}
    p.raise_on.append(("ml-state-write", RuntimeError("db down")))
    t4 = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    assert _run(ml._hand_exit_memo_write(t4, "rn1:z", {"at": 1.0})) is False and t4.hand_exits is None
    # the tick's read: an object, else None (named once per process)
    p.raise_on.clear()
    p.state["mirror_hand_exit"] = {KEY: _memo_entry()}
    t5 = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    _run(ml._load_hand_exits(t5))
    assert t5.hand_exits == {KEY: _memo_entry()}
    monkeypatch.setattr(rules, "MIRROR_HAND_EXIT", False)
    t6 = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    _run(ml._load_hand_exits(t6))
    assert t6.hand_exits is None and ml._hand_exited(t6, {"whale": "rn1", "condition_id": CID, "last_plan": None},
                                                         _held_plan()) is None


# ------------------------------------------------ (5) the release preset


def _release_sql(text: str | None = None) -> str:
    text = YML.read_text() if text is None else text
    s = text[text.index("mirror-hand-release) need_confirm"):]
    return s[s.index('SQL="') + 5:s.index('"; TO=')]


def test_e29_the_release_preset_one_statement_beside_mirror_register_and_the_read_only_presets_untouched():
    pglast = pytest.importorskip("pglast")
    text = YML.read_text()
    case = text[text.index("                mirror-hand-release) need_confirm"):]
    case = case[:case.index('"; TO=30000 ;;\n') + len('"; TO=30000 ;;\n')]
    assert case.count("need_confirm") == 1 and 'if [[ ! "$ARG" =~ ^[0-9]+$ ]]; then echo "mirror-hand-release: arg must be <book_id>' in case
    sql = _release_sql(text)
    assert sql.count("$ARG") >= 3 and "$RB" not in sql and "$RS" not in sql
    tree = pglast.parse_sql(sql.replace("$ARG", "1317"))
    assert len(tree) == 1 and type(tree[0].stmt).__name__ == "SelectStmt", "ONE statement: its own transaction under psql -c"
    for word in ("UPDATE ingestion_state s SET value = s.value - b.k", "s.key = 'mirror_hand_exit'",
                 "jsonb_typeof(s.value) = 'object'", "s.value ? b.k", "UPDATE mirror_books x SET last_plan = x.last_plan - 'hand_exit'",
                 "x.last_plan ? 'hand_exit'", "b.whale || ':' || b.condition_id AS k", "b.last_plan -> 'hand_exit' AS row_hold",
                 "'no hold on book $ARG'", "'REFUSED: no mirror_books row with id $ARG'", "'released: memo '"):
        assert word in sql, word
    assert "updated_at" not in sql and "DELETE" not in sql and "INSERT" not in sql
    # the help arm lists it after mirror-register; the labels' order is the help line's; hourly last
    line = [ln for ln in text.splitlines() if ln.strip().startswith('*) echo "sql: arg must be one of ')][0]
    names = line.split("one of ")[1].split(" (got")[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels and names[-1] == "hourly" and len(names) == len(set(names))
    assert names.index("mirror-hand-release") == names.index("mirror-register") - 1, "beside mirror-register (before it)"
    # every other preset byte for byte: the file with this lane's case block and its help token removed is the landing tip's (cdf0742)
    blk_lo = text.index("                # THE HAND'S RELEASE (E29")
    blk_hi = text.index('"; TO=30000 ;;\n', text.index("mirror-hand-release) need_confirm")) + len('"; TO=30000 ;;\n')
    assert case in text[blk_lo:blk_hi]
    tip = (text[:blk_lo] + text[blk_hi:]).replace("|mirror-hand-release|", "|")
    assert _sha_text(tip) == RENDER_OPS_ON_TIP, "a read-only preset moved"
    from tests import test_e27_take_tolerance as e27
    assert hashlib.sha256(YML.read_bytes()).hexdigest()[:16] == e27.RENDER_OPS_SHA
    assert text.count("last_plan->'hand'") == 3, "E24's presets untouched"


def test_e29_the_release_statement_runs_on_a_real_postgres_and_removes_both_records():
    """On the scratch database (skipped without one): 047's mirror_books and
    an ingestion_state stub; a hand-exited row beside another market's memo
    entry -> the statement removes THIS book's entry and the row's
    hand_exit, keeps the other entry and the plan's other keys, prints the
    entries it removed; run again -> 'no hold on book N', nothing touched;
    an unknown id -> REFUSED, nothing touched."""
    asyncpg = pytest.importorskip("asyncpg")
    from tests.test_e12_flow_only import DSN_BASE, MIG_DIR
    sql = _release_sql()

    async def _go():
        try:
            admin = await asyncpg.connect(DSN_BASE, timeout=4)
        except Exception:  # noqa: BLE001 — no local PG: skip, never fake
            pytest.skip("no local postgres for the E29 preset pin")
        name = "e29_hand_" + uuid.uuid4().hex[:10]
        await admin.execute(f'CREATE DATABASE "{name}"')
        conn = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
        try:
            await conn.execute("CREATE TABLE live_orders (id bigserial PRIMARY KEY, us_market_slug text, lane text)")
            await conn.execute((MIG_DIR / "047_mirror_live.sql").read_text())
            await conn.execute("CREATE TABLE ingestion_state (key text PRIMARY KEY, value jsonb)")
            bid = await conn.fetchval(
                "INSERT INTO mirror_books (whale, condition_id, us_market_slug, long_asset, intent, state, last_plan) "
                "VALUES ('rn1', $1, $2, 'tok-m', 'ORDER_INTENT_BUY_SHORT', 'live', $3::jsonb) RETURNING id",
                CID, SLUG, json.dumps({"kind": "increase", "hold": "hand_held", "target": -2460,
                                       "hand_exit": {"at": 1.0, "shares": 1039.0, "px": 0.31, "memo": True}}))
            memo = {KEY: {"at": 1.0, "book_id": bid, "slug": SLUG, "shares": 1039.0, "px": 0.31},
                    "rn1:0xother": {"at": 2.0, "book_id": 9, "slug": "other", "shares": 5.0, "px": 0.5}}
            await conn.execute("INSERT INTO ingestion_state (key, value) VALUES ('mirror_hand_exit', $1::jsonb)",
                               json.dumps(memo))
            row = await conn.fetchrow(sql.replace("$ARG", str(bid)))
            assert row["book"] == bid and row["key"] == KEY and row["memo_rows"] == 1 and row["plan_rows"] == 1
            assert row["verdict"].startswith("released: memo {") and '"shares": 1039.0' in row["verdict"]
            left = json.loads(await conn.fetchval("SELECT value FROM ingestion_state WHERE key = 'mirror_hand_exit'"))
            assert left == {"rn1:0xother": memo["rn1:0xother"]}, "the other market's entry kept"
            lp = json.loads(await conn.fetchval("SELECT last_plan FROM mirror_books WHERE id = $1", bid))
            assert lp == {"kind": "increase", "hold": "hand_held", "target": -2460}, "the plan minus hand_exit alone"
            row2 = await conn.fetchrow(sql.replace("$ARG", str(bid)))
            assert row2["verdict"] == f"no hold on book {bid}" and row2["memo_rows"] == 0 and row2["plan_rows"] == 0
            row3 = await conn.fetchrow(sql.replace("$ARG", str(bid + 1000)))
            assert row3["verdict"].startswith("REFUSED: no mirror_books row") and row3["memo_rows"] == 0
            left2 = json.loads(await conn.fetchval("SELECT value FROM ingestion_state WHERE key = 'mirror_hand_exit'"))
            assert left2 == {"rn1:0xother": memo["rn1:0xother"]}
        finally:
            await conn.close()
            await admin.execute(f'DROP DATABASE "{name}"')
            await admin.close()
    asyncio.run(_go())


# ------------------------------------------------ (6) the census place, the emit sites, the untouched


def test_e29_the_census_place_the_emit_sites_the_rails_and_no_migration():
    keys = ml.CENSUS_KEYS
    # landed over E28 (cdf0742): E28's five names sit between E25's four and these (241 in the worktree -> 246)
    assert keys[-28:-24] == NEW_NAMES and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-33:-28] == ("walk_row_moved", "walk_row_unread", "walk_row_gone", "ledger_stale_reread", "ledger_stale_refused")
    assert keys[-37:-33] == ("exit_unconfirmed", "exit_confirmed", "exit_confirm_expired", "exit_flap_averted")
    assert keys[-41:-37] == ("hand_explained", "hand_adopted", "hand_unread", "hand_ambiguous")
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys) and len(keys) == 257
    assert all(ml._new_stats()["census"][k] == 0 for k in NEW_NAMES)
    assert all(k not in ml._INTEG_CENSUS_KEYS for k in NEW_NAMES)
    src = inspect.getsource(ml)
    assert src.count('_mirror_stop("hand_exit", w)') == 1 and src.count('_mirror_stop("hand_exit_write_failed", w)') == 1
    mark = inspect.getsource(ml._hand_exit_mark)
    assert '_mirror_stop("hand_exit", w)' in mark and 'book["_hand_exit"] = True' in mark and 'plan["hand_exit"] = rec' in mark
    assert '_mirror_stop("hand_exit_write_failed", w)' in inspect.getsource(ml._hand_exit_write_failed)
    tb = inspect.getsource(ml._tick_book)
    assert tb.count('inc_refusal = "hand_held" if hand_hold == "held" else "hand_held_unread"') == 1
    assert tb.count("hand_hold = _hand_exited(t, book, prior_plan)") == 1 and tb.count('plan["hold"] = inc_refusal') == 1
    assert tb.index("hand_hold = _hand_exited(") < tb.index("await _increase_recheck(t, book, r, his_px)"), "before the recheck's venue read"
    assert tb.index("inc_refusal = _increases_refusal(t, w)") < tb.index("hand_hold = _hand_exited(")
    cand = inspect.getsource(ml._hand_held_candidate)
    assert cand.count('return "hand_held"') == 2 and cand.count('return "hand_held_unread"') == 1 and "_SQL_BOOK_FLIP" in cand
    tc = inspect.getsource(ml._tick_candidate)
    assert tc.count("await _hand_held_candidate(t, w, cid, d)") == 1
    assert tc.index("_hand_held_candidate(") < tc.index("ms.map_market(") and tc.index("_hand_held_candidate(") < tc.index("_read_market(")
    assert "hand_held" not in ml._CAND_NOT_RECORDED and "hand_held_unread" not in ml._CAND_NOT_RECORDED
    assert "hand_exit" not in ml._SKIP_CARRIED and "hand" not in ml._SKIP_CARRIED
    # the one new call site in _hand_net, on the booking; the memo read once at each tick's start
    hn = inspect.getsource(ml._hand_net)
    assert hn.count("await _hand_exit_mark(t, book, shares, px, sorted(adopt), plan)") == 1
    assert hn.index('_mirror_stop("hand_adopted", w)') < hn.index("await _hand_exit_mark(")
    assert inspect.getsource(ml._tick).count("await _load_hand_exits(t)") == 1
    assert inspect.getsource(ml._fast_tick).count("await _load_hand_exits(t)") == 1
    wp = inspect.getsource(ml._write_plan)
    assert 'if "hand_exit" not in plan:' in wp and "released = (rules.MIRROR_HAND_EXIT" in wp
    # the rails: one switch, no capped_env / min_wait_env moved, the bound a module constant, no migration, 059 stands
    rsrc = inspect.getsource(rules)
    assert rsrc.count("capped_env(") == 26 and rsrc.count("min_wait_env(") == 8 and rsrc.count("env_switch(") == 8
    assert "HAND_EXIT_MEMO_MAX" not in rsrc and ml.HAND_EXIT_MEMO_MAX == 500
    assert sorted(x.name for x in (ROOT / "backend" / "migrations").glob("*.sql"))[-1].startswith("061_")
    assert "hand" not in (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "hand_held" not in inspect.getsource(rules.order_decision)


def test_e29_the_untouched_functions_are_byte_identical_and_hand_net_with_the_call_site_excised():
    for name, digest in UNTOUCHED.items():
        assert _sha(getattr(ml, name)) == digest, name
    src = inspect.getsource(ml._hand_net)
    lo = src.index("        if rules.MIRROR_HAND_EXIT:\n")
    hi = src.index("await _hand_exit_mark(t, book, shares, px, sorted(adopt), plan)\n", lo)
    hi = hi + len("await _hand_exit_mark(t, book, shares, px, sorted(adopt), plan)\n")
    excised = src[:lo] + src[hi:]
    assert _sha_text(excised) == HAND_NET_ON_TIP, "E24's reading and arithmetic byte for byte, the one call site aside"
    assert "_mirror_stop" not in src[lo:hi] and "_recent" not in src[lo:hi]


def test_e29_every_name_is_emitted_here(monkeypatch):
    """The lane's four names, each driven (the worker file's coverage read
    imports this)."""
    for fn in (test_e29_book_1317_the_hand_cover_marks_the_book_and_the_next_tick_holds_the_add,
               test_e29_both_unreadable_on_a_live_book_holds_the_add_hand_held_unread,
               test_e29_the_memo_write_failing_is_named_the_row_holds_and_the_write_lands_later):
        ml._hand_read_at.clear()                       # book ids repeat across this file's pools
        ml._disagree_fill_read_at.clear()
        ml._lost_fill_read_at.clear()
        fn(monkeypatch)
    ml._hand_read_at.clear()


def test_e29_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E29 -- .* \(2026-09-10, FILL lane 29\)", doc, re.M), "the E29 section header"
    sec = doc[doc.index("## 71. E29"):]
    for k in NEW_NAMES + ("_hand_exit_mark", "_hand_exited", "_hand_held_candidate", "_load_hand_exits", "mirror_hand_exit",
                          "HAND_EXIT_MEMO_MAX", "MIRROR_HAND_EXIT", "mirror-hand-release", "hand_exit", "hold",
                          "test_e29_hand_exit.py", "1317", "8285", "8320", "8322", "8323", "8310", "2,460", "1,039",
                          "book_1317_0022", "_SQL_BOOK_FLIP", "_write_plan", "memo", "both sides"):
        assert k in sec, k
