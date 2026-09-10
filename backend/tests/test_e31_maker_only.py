"""E31 (2026-09-10, FILL lane 31): THE MIRROR IS A MAKER.

THE OWNER'S ORDER (~03:3xZ, on the desk's forensic study of RN1's book):
"Make all changes based on the case study and ensure we are proportional and
directional with the same rules we have now (become a maker not taker)
mirror him to a tee. Make this flawless and operational immediately when
verified".

THE ROWS. Our life: 1,382 books, $125,100.43 staked, settled -$14,676.67 =
-11.73 % (hard2/byleague_0308.txt:396). The take-band table over 24 h to
03:09Z (hard2/h0309_short.txt): `take` 298 orders / 130 filled / roi -0.4418
+/- 0.3767 (:1301), `take_in_band` 189 / 160 / -0.1180 (:1305), `rest` 381 /
180 / -0.0313 +/- 0.1862 (:1295), `rest_replaced` 402 / 23 filled = 5.7 %
(:1300); the replaced table 1,036 in 24 h -- touch_moved 251, same_quote 322
(:1311). The probe's line: `FVM rn1: filled 269 roi 0.0177 | missed 108 roi
0.3588` (hard2/probe_66144cf_full.log:1447) -- what fills is adverse. The
study: "Bid-only. 4,615,101 buys, 248 sells" and "Rest small bids on both
outcomes near the mid; do not cross the spread; requote as the book moves"
(docs/rn1-book-anatomy.md).

THE RULE (docs 75). (A) Every order is a post-only rest at HIS PRICE OR
BETTER that never crosses the touch: a BUY at min(his cent, ask - a tick),
a SELL at max(his cent, bid + a tick), an unpriced exit at the touch's own
inside tick. (B) Every take path is retired by code and no environment
value can re-arm one; `_place` refuses a tif IOC by name. (C) A standing
rest is re-priced on his level and when the touch makes room TOWARD him,
never away from it; an entry rest at the maker wire past its TTL stands.
(D) A rest the venue rejects as a CROSS is re-priced one tick further off
the touch, at most three steps, then held by name; a 400 the quote does not
explain is E30's case, byte for byte. (D2) The process-wide post-only latch
becomes a DURABLE BLOCK a human clears; the flag never leaves the send.

The world: the worker suite's short world on the E5 pool, and E30's own
1383 fixtures by import (his 100 long against 1,557.4 other at 0.11, so his
level in long space is 0.89; the quote 0.75 / 0.76; the target -145).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import pathlib
import re
import subprocess
import sys

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests import test_e30_post_only_body as e30
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails ride along
    # `_armed` IS the autouse fixture (review MEDIUM-4): an autouse fixture
    # applies to the module it is defined in, so without the NAME in this
    # module's namespace every _tick(...) here runs in SAFE mode and places
    # nothing -- a worker-world test would assert nothing at all. E29 and E30
    # both import it; this file did not.
    BUY, CID, GTC_TIF, IOC_TIF, M, N, NOW, SELL, SHORT, SLUG, _NoClose, _Venue, _armed, _cancels,
    _census, _fill, _his, _kinds, _mkt, _places, _pool, _run, _short_book, _shorts_on, _tick,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
YML = ROOT / ".github" / "workflows" / "render-ops.yml"
NEW_NAMES = ("maker_rest_at_touch", "maker_rest_repriced", "maker_cross_repriced", "maker_cross_held",
             "maker_no_cent", "maker_fill_at_create", "post_only_block", "ioc_refused",
             "rest_reread_capped", "rest_quote_unread")
# the SEVENTEEN the take paths emitted: declared, never emitted again (docs 75).
# `filled_take` is NOT one of them and is NOT retired (review HIGH-2): under
# E31 every row is GTC/GTD and no row is kind 'take', so _finish_order's
# `maker` is False exactly when `taker_at_placement` is True -- the venue
# filling a post-only rest AT CREATE as a taker. It is now the counter that
# says THE FLAG DID NOT HOLD, and it stays reachable, emitted and outside the
# coverage hook's `unreachable` set.
RETIRED = ("take_placed", "take_first", "take_at_his_level", "take_refused_price", "take_capped",
           "take_arm_stale", "take_in_band", "exit_take", "exit_take_rested",
           "exit_take_in_band", "cover_in_band", "short_cover_take", "fast_add_took",
           "ask_moved", "bid_moved", "ioc_reread_capped", "ioc_quote_unread")
# the functions this lane DELETES: no caller is left for any of them
DELETED = ("_entry_take", "_exit_take", "_ioc_reread", "_take_band", "_short_take_band",
           "_exit_band_at", "_exit_band_take", "_exit_band_mark", "_flatten_send",
           "IOC_SKIPPED", "TAKE_ARM_STALE_WAITS", "TAKE_ARM_STALE_MIN_S",
           "_POST_ONLY_OK", "_post_only_enabled")
# the functions this lane leaves byte for byte, hashed on the E30 tree (b57929e)
UNTOUCHED = {
    "_frozen_exit": "ef478fabdfa2ccc0", "_fast_gate": "1932811194268668", "_fast_book": "286e6fa4663c3887",
    "_fast_step_o": "740d5548be35d317", "_disarm_take": "fd41b2f10a4ba39d",
    "_priced_exit_rest": "9ea71055a5ab21e7", "_maybe_close_episode": "59e28ff01f960660",
    "_cancel_and_settle": "953ba5587d2ad423", "_book_delta": "e5363576f6d0a575",
    "_finish_order": "1db222463610e38c", "_tick": "265461d33df59da6", "_fast_tick": "c364a8f6ed7f9b3b",
    "_fast_candidate": "922585ffb6856f70", "_walk_candidate": "9c990feba5fdeb57",
    "_cover_qty": "4662f515286becbe", "_sell_qty": "95533ab0c37193ae", "_room_qty": "458b3fea5e2d235b",
    "_exit_terms": "10411ad7900e6542", "_exit_held": "19e2d3e3f4e57d3e",
    "_post_only_held": "b2ab4a5719d67785", "_post_only_note": "148e45f4f67c0bb6",
    "_post_only_receipt": "97cfc79016173e73", "_post_only_word": "ef47baa3f69039ae",
    "_increases_refusal": "f984cc09584bd265",
}
RULES_UNTOUCHED = {
    "order_decision": "b22c4fbc29d68662", "exit_terms": "2e4cd4a9edeb10a9",
    "take_allowed": "dc3079622052b6ff", "take_arms": "b0712205d38eeea7",
    "at_or_through": "4aece58b61ee21bc", "buy_wire": "861dd6ffd57e1283",
    "sell_wire": "a9cae307d9e6f2b2", "buy_price": "f9b961a63c4d2dc4",
    "sell_price": "b43a9f5c6cdf6b1d", "room_scale": "92e7b5e20e20f05b",
    "admission": "a10630d6d3a3a62c", "open_catchup": "6b9e8f2ffe1d3538",
    "select_flatten": "00e5b189ccb84bdb",
}
PMUS_UNTOUCHED = {"_post_only_refusal": "7becc8060b5ec9de", "_post_only_cross": "41341b4b46075c53",
                  "submit_fok": "7198b96c9a700ce9", "bbo_read": "5f8dd44f222a7c02",
                  "_amount": "4ae6f1c4e71054d4", "close_position": "db12e4ed2d9f0554"}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _code(obj) -> str:
    """The source with every comment and every docstring stripped: a rule
    this lane retired may still be NAMED in a paragraph (that is the record),
    but no line of code may read it."""
    import io, tokenize
    src = inspect.getsource(obj)
    out, prev_end, prev_tok = [], (1, 0), None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_tok in (None, tokenize.NEWLINE, tokenize.NL,
                                                        tokenize.INDENT, tokenize.DEDENT):
            continue
        out.append(tok.string)
        prev_tok = tok.type
    return " ".join(out)


def _reading(**over):
    base = dict(whale="rn1", cid="c", slug=SLUG, la=M, oa=N, fills=[], his_long=0.0, his_other=0.0,
                snap={}, snap_age=None, snap_partial=False, fresh_read=False, fresh=False,
                snap_long=None, snap_other=None, bid=e30.BID, ask=e30.ASK, mark=0.755,
                venue=0.0, manual=0.0, market=None, market_live=True)
    base.update(over)
    return ml._Reading(**base)


# ------------------------------------------------- 1. THE PRICE RULE, pure


def test_e31_the_maker_wire_is_at_his_price_or_better_and_never_at_or_through_the_far_touch():
    """rules.maker_wire, swept over every cent pair: a BUY is never above his
    cent and never at or through the ASK; a SELL is never under his cent and
    never at or through the BID. The clamp reads the OTHER side's touch,
    which no wire of this module read before this lane -- so it holds in a
    LOCKED or INVERTED book too, where buy_price's own docstring says the
    no-cross bound fails."""
    cents = [round(c / 100, 2) for c in range(1, 100)]
    for his in cents:
        for bid in cents:
            for ask in cents:
                w = rules.maker_wire(BUY, his, bid, ask)
                if w is not None:
                    assert 0.01 <= w <= 0.99
                    assert w <= his + 1e-9, (his, bid, ask, w)
                    assert w < ask - 1e-9, (his, bid, ask, w)
                    assert not rules.at_or_through(BUY, bid, ask, w)
                s = rules.maker_wire(SELL, his, bid, ask)
                if s is not None:
                    assert 0.01 <= s <= 0.99
                    assert s >= min(his, 0.99) - 1e-9, (his, bid, ask, s)
                    assert s > bid + 1e-9, (his, bid, ask, s)
                    assert not rules.at_or_through(SELL, bid, ask, s)


def test_e31_the_maker_wire_is_his_cent_inside_the_spread_and_the_touch_bound_at_it():
    # the default worker fixture (his 0.31, bid 0.30 / ask 0.32): AT HIS CENT
    # inside the spread, where buy_price(0.31, 0.30) joined the bid at 0.30
    assert rules.maker_wire(BUY, 0.31, 0.30, 0.32) == 0.31 and rules.buy_price(0.31, 0.30) == 0.30
    # the entry-take fixture (h0309_short.txt:1040, his fill 4,855 @0.50 answered `take`):
    # the ask AT his cent -> one tick under it, never at or through
    assert rules.maker_wire(BUY, 0.50, 0.49, 0.50) == 0.49
    # E27's band shape (the ask two cents over) -> his own cent, inside the spread
    assert rules.maker_wire(BUY, 0.50, 0.49, 0.52) == 0.50
    # book 1383 (his 0.89, the venue 0.75 / 0.76): the short add's offer at his cent
    assert rules.maker_wire(SELL, 0.89, 0.75, 0.76) == 0.89
    # the same book LOCKED at 0.89 / 0.89: one tick over the bid, never at it
    assert rules.maker_wire(SELL, 0.89, 0.89, 0.89) == 0.90
    # the tick's own exit row (mtick_0309.txt:291, book 1393: his 0.49, the bid at 0.48)
    assert rules.maker_wire(SELL, 0.49, 0.48, 0.55) == 0.49
    # the unpriced exit: the touch's own inside tick, no level to honour
    assert rules.maker_wire(SELL, None, 0.48, 0.55, unpriced=True) == 0.49
    assert rules.maker_wire(BUY, None, 0.30, 0.32, unpriced=True) == 0.31
    # no cent on the ladder, and a touch that is not a price: NO order
    assert rules.maker_wire(BUY, 0.50, 0.005, 0.01) is None
    assert rules.maker_wire(SELL, 0.50, 0.99, 0.995) is None
    for bad in (None, float("nan"), float("inf"), True, "0.30", -1.0, 0.0, 1.0):
        assert rules.maker_wire(BUY, 0.31, 0.30, bad) is None
        assert rules.maker_wire(SELL, 0.31, bad, 0.32) is None
    assert rules.maker_wire(BUY, None, 0.30, 0.32) is None, "his level unreadable: no_price"
    assert rules.maker_wire("BUY", 0.31, 0.30, 0.32) is None, "a side that is not a side"


def test_e31_the_short_adds_offer_goes_through_the_executors_own_arithmetic():
    """_short_wire takes the maker cent and returns the executor's number for
    that cost -- rest_tick(wire_limit(1 - T)) -- so the collateral, the day
    cap and the short-model gate read what they always read. It round-trips
    the cent exactly (the 19-cent table's exactness)."""
    for c in range(1, 100):
        px = round(c / 100, 2)
        assert ml._short_wire(px) == px
    assert ml._short_wire(None) is None and ml._short_wire(0.0) is None and ml._short_wire(1.0) is None


def test_e31_the_maker_tick_names_the_ladder_the_clamp_assumes_and_is_not_a_knob():
    assert rules.MAKER_TICK == 0.01 and "MAKER_TICK" in rules.__all__
    src = inspect.getsource(rules)
    assert "MAKER_TICK = 0.01" in src
    for shape in ('capped_env("MAKER_TICK', 'min_wait_env("MAKER_TICK', 'env_switch("MAKER_TICK',
                  '_env_float("MAKER_TICK', 'environ.get("MAKER_TICK'):
        assert shape not in src, shape
    # the assumption's source: pmus formats every price to two decimals
    from sportsassets import pmus
    assert 'f"{price:.2f}"' in inspect.getsource(pmus._amount)


def test_e31_the_compare_wire_replaces_toward_him_and_never_chases_a_touch_that_came_to_the_rest():
    # a BUY: the ask rose, so the rest can sit at his cent now -> the maker wire
    assert rules.maker_compare_wire(BUY, 0.29, 0.30, 0.30) == 0.30
    # the ask FELL to 0.29: the rest is being filled (or the read is stale) -- keep
    assert rules.maker_compare_wire(BUY, 0.29, 0.30, 0.28) == 0.29
    # the rest sits ABOVE him: past him, replaced at once (today's rule)
    assert rules.maker_compare_wire(BUY, 0.31, 0.30, 0.28) == 0.28
    # the SELL mirror
    assert rules.maker_compare_wire(SELL, 0.32, 0.31, 0.31) == 0.31
    assert rules.maker_compare_wire(SELL, 0.32, 0.31, 0.33) == 0.32
    assert rules.maker_compare_wire(SELL, 0.30, 0.31, 0.32) == 0.32
    # fail closed: an unreadable standing wire or side reads as the maker wire
    assert rules.maker_compare_wire(BUY, None, 0.30, 0.30) == 0.30
    assert rules.maker_compare_wire("x", 0.29, 0.30, 0.30) == 0.30
    assert rules.maker_compare_wire(BUY, 0.29, 0.30, None) is None


def test_e31_the_ttl_stands_clause_skips_the_ttl_alone_and_nothing_else():
    """rest_decision's `ttl_stands` (default False = byte for byte): an ENTRY
    rest at the maker wire past MIRROR_REST_TTL_S is not re-placed as its own
    twin -- 322 of 1,036 replaces in 24 h were exactly that
    (h0309_short.txt:1311) and a replaced rest filled 5.7 % against a kept
    rest's 47.2 % (:1300 / :1295). Every other clause is untouched."""
    oo = rules.OpenOrder(BUY, 0.31, 300, 300.0, 0.0, "ORDER_INTENT_BUY_LONG")
    p = rules.Plan(BUY, 300, 0.31, "add", None, None)
    old = float(rules.MIRROR_REST_TTL_S) + 1.0
    assert rules.rest_decision(oo, p, old, wire=0.31)[0] == "replace"
    assert rules.rest_decision(oo, p, old, wire=0.31)[1]["cause"] == "ttl"
    assert rules.rest_decision(oo, p, old, wire=0.31, ttl_stands=True)[0] == "keep"
    # the cent still replaces past the TTL
    assert rules.rest_decision(oo, p, old, wire=0.29, ttl_stands=True)[1]["cause"] == "cent"
    # so does a quantity that fell, at any age
    p2 = rules.Plan(BUY, 100, 0.31, "add", None, None)
    assert rules.rest_decision(oo, p2, old, wire=0.31, ttl_stands=True)[1]["cause"] == "qty"
    # and a side change, and a named cancel
    p3 = rules.Plan(SELL, 300, 0.31, "reduce", None, None)
    assert rules.rest_decision(oo, p3, old, wire=0.31, ttl_stands=True)[1]["cause"] == "side"
    assert rules.rest_decision(oo, p, old, wire=0.31, ttl_stands=True,
                               cancel_reason="drift")[0] == "drift"


# ------------------------------------------- 2. THE GUARD AND THE DELETIONS


def test_e31_place_refuses_an_ioc_by_name_before_any_op_read_row_or_venue_call():
    p = e30._p1383()
    b = e30._b1383(p)
    v = e30._v(place=None)
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    r = _reading()
    plan: dict = {}
    import asyncio
    res = asyncio.run(ml._place(t, b, r, "take", SELL, 0.89, 145, e30.HIS, None, plan, tif="IOC"))
    assert res == "ioc_refused"
    assert ml._MIRROR_CENSUS.get("ioc_refused|rn1") == 1 and t.ops == 0
    assert plan["ioc_refused"]["tif"] == "IOC" and not v.calls and not p.orders


def test_e31_no_take_path_is_left_in_the_worker_and_the_flag_is_a_literal_at_the_send():
    src = inspect.getsource(ml)
    for name in DELETED:
        assert not hasattr(ml, name), name
        assert f"def {name}(" not in src and f"{name} = " not in src, name
    act = _code(ml._act)
    assert 'tif="IOC"' not in act and "IMMEDIATE_OR_CANCEL" not in act
    for gone in ("_entry_take(", "_exit_take(", "_take_band(", "_short_take_band(", "_flatten_send(",
                 "_exit_band_take(", "take_allowed(", "take_armed_ts", "_SQL_BOOK_ARM"):
        assert gone not in act, gone
    pr = inspect.getsource(ml._place_reserved)
    assert "\n    post_only = True\n" in pr and "_post_only_enabled" not in pr
    assert "t.pmus.submit_fok, slug, wire, int(qty), sell," in pr
    assert "venue_tif, intent, post_only, good_till)" in pr
    assert 'venue_tif = "TIME_IN_FORCE_GOOD_TILL_CANCEL"' in pr
    assert "IMMEDIATE_OR_CANCEL" not in _code(ml._place_reserved), "no IOC value is left in the placement"
    # the shell can no longer turn the flag off, and no latch can
    assert "PMUS_MIRROR_POST_ONLY" not in _code(ml)
    place = inspect.getsource(ml._place)
    assert 'if tif != "GTC":' in place and '_mirror_stop("ioc_refused", w)' in place


def test_e31_the_untouched_functions_are_byte_for_byte_on_the_e30_tree():
    for name, want in UNTOUCHED.items():
        assert _sha(getattr(ml, name)) == want, name
    for name, want in RULES_UNTOUCHED.items():
        assert _sha(getattr(rules, name)) == want, name
    from sportsassets import pmus
    for name, want in PMUS_UNTOUCHED.items():
        assert _sha(getattr(pmus, name)) == want, name


# ------------------------------------------------- 3. THE CROSS, read and answered


def test_e31_a_rejection_is_a_cross_by_the_venues_field_or_by_our_own_quote():
    """_maker_cross: the 200 shape's own post_only_cross (E30's ':cross'
    word) or our wire at or through the touch on the read the rest was sent
    against. Book 1383's nine 400s -- a SELL at 0.89 over an ask of 0.76 --
    are NEITHER, which is the case E30 alone covers."""
    cross200 = {"status_code": 200, "post_only_cross": True,
                "execution_type": rules.EXECUTION_TYPE_REJECTED, "order_state": rules.ORDER_STATE_REJECTED}
    assert ml._maker_cross(cross200, SELL, 0.75, 0.76, 0.89) is True
    assert ml._maker_cross(e30.RAW_400, SELL, 0.75, 0.76, 0.89) is False, "1383's shape is not a cross"
    # our own quote: the bid at or over a SELL's cent
    assert ml._maker_cross(e30.RAW_400, SELL, 0.90, 0.91, 0.89) is True
    # a BUY: the ask at or under its cent
    assert ml._maker_cross(e30.RAW_400, BUY, 0.29, 0.30, 0.31) is True
    assert ml._maker_cross(e30.RAW_400, BUY, 0.29, 0.32, 0.31) is False
    for bad in (None, [], "400", 429):
        assert ml._maker_cross(bad, SELL, 0.75, 0.76, 0.89) is False


def test_e31_the_hint_steps_one_tick_further_off_the_touch_and_holds_at_three(monkeypatch):
    p = e30._p1383()
    b = e30._b1383(p)
    t = ml._Tick(pool=p, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    r = _reading()
    ml._cross_hint.clear()
    ent = ml._cross_hint_note(t, b, SELL, 0.89, r)
    assert ent["n"] == 1 and ml._cross_hint_live(t, b, r) is ent
    # the next wire is ONE TICK further from the touch (a SELL steps UP: we are
    # paid more for the contract, so we pay less for the other token than his 0.11)
    assert ml._maker_hint_wire(SELL, 0.89, ent) == 0.90
    for n, wire in ((2, 0.91), (3, 0.92)):
        ent = ml._cross_hint_note(t, b, SELL, round(0.89 + (n - 1) * 0.01, 2), r)
        assert ent["n"] == n and ml._maker_hint_wire(SELL, 0.89, ent) == wire
    ent = ml._cross_hint_note(t, b, SELL, 0.92, r)
    assert ent["n"] == 4 > ml.MAKER_CROSS_MAX_TICKS, "past the bound: the rest is held by name"
    # a BUY steps DOWN (its OWN side: review HIGH-1)
    assert ml._maker_hint_wire(BUY, 0.31, {"wire": 0.31, "side": BUY}) == 0.30
    # the quote moving DROPS the hint (the cross is no longer evidence of anything)
    r2 = _reading(ask=0.77)
    assert ml._cross_hint_live(t, b, r2) is None and not ml._cross_hint
    # so does the hold's wait
    ml._cross_hint_note(t, b, SELL, 0.89, r)
    t2 = ml._Tick(pool=p, pmus=None, http=None, now=NOW + ml.MAKER_CROSS_HOLD_S + 1,
                  stats=ml._new_stats())
    assert ml._cross_hint_live(t2, b, r) is None
    assert ml.MAKER_CROSS_MAX_TICKS == 3 and ml.MAKER_CROSS_HOLD_S == 60.0


def test_e31_a_cross_hint_never_prices_the_other_side_of_the_book():
    """REVIEW HIGH-1. The hint records the SIDE the venue refused
    (_cross_hint_note). A SHORT book alternates a SELL add and a BUY cover on
    one contract, often on one quote inside MAKER_CROSS_HOLD_S: stepping the
    cover a tick from the ADD's refused wire priced it seven cents under his
    buy-back price and the short never covered. A wire refused on one side is
    no evidence at all about the other."""
    sell_hint = {"wire": 0.89, "n": 1, "quote": (0.75, 0.76), "side": SELL, "at": 0.0}
    buy_hint = {"wire": 0.16, "n": 1, "quote": (0.15, 0.17), "side": BUY, "at": 0.0}
    # its OWN side still steps, exactly as the lane's rule says
    assert ml._maker_hint_wire(SELL, 0.89, sell_hint) == 0.90
    assert ml._maker_hint_wire(BUY, 0.16, buy_hint) == 0.15
    # the OTHER side is untouched: the maker wire stands
    assert ml._maker_hint_wire(BUY, 0.95, sell_hint) == 0.95, "the cover, not 0.88"
    assert ml._maker_hint_wire(SELL, 0.12, buy_hint) == 0.12, "the exit, not 0.17"
    assert ml._maker_hint_wire(BUY, 0.95, {**sell_hint, "side": None}) == 0.95
    assert 'hint.get("side") != side' in inspect.getsource(ml._maker_hint_wire)


def test_e31_the_two_cross_bounds_are_worker_constants_and_no_rail_reads_them():
    src = inspect.getsource(ml)
    assert "MAKER_CROSS_MAX_TICKS = 3" in src and "MAKER_CROSS_HOLD_S = 60.0" in src
    for shape in ("MAKER_CROSS_MAX_TICKS", "MAKER_CROSS_HOLD_S"):
        assert shape not in inspect.getsource(rules), shape
        assert f'os.environ.get("{shape}' not in src and f'environ["{shape}' not in src


# ----------------------------------------------- 4. THE FILL AT CREATE


def test_e31_a_fill_at_create_is_a_maker_only_when_every_filling_aggressor_is_the_bool_false():
    ok = {"executions": [{"type": "EXECUTION_TYPE_NEW", "aggressor": None},
                         {"type": "EXECUTION_TYPE_FILL", "aggressor": False}]}
    assert ml._aggressor_maker(ok) is True
    assert ml._aggressor_maker({"executions": [{"type": "EXECUTION_TYPE_PARTIAL_FILL", "aggressor": False},
                                               {"type": "EXECUTION_TYPE_FILL", "aggressor": False}]}) is True
    # any filling record the venue calls the aggressor, or a bool it did not send
    assert ml._aggressor_maker({"executions": [{"type": "EXECUTION_TYPE_FILL", "aggressor": True}]}) is False
    assert ml._aggressor_maker({"executions": [{"type": "EXECUTION_TYPE_FILL", "aggressor": None}]}) is False
    assert ml._aggressor_maker({"executions": [{"type": "EXECUTION_TYPE_FILL"}]}) is False
    # nothing to read is NOT a maker claim: the flag cannot be read back, so
    # this bool is the only witness there is
    for bad in ({}, {"executions": []}, {"executions": None}, {"executions": ["x"]},
                {"executions": [{"type": "EXECUTION_TYPE_NEW", "aggressor": False}]}, None, [], "x"):
        assert ml._aggressor_maker(bad) is False, bad


def test_e31_the_durable_block_is_the_loss_stops_pattern_and_the_preset_clears_it():
    assert ml._STATE_POST_ONLY_BLOCK == "mirror_post_only_block"
    src = inspect.getsource(ml)
    assert "_write_post_only_block" in src
    # RE-PINNED inside this lane (review CRITICAL-1: the builder moved the key
    # and left the pin). The heartbeat's `post_only` bool would read True for
    # ever under the literal flag and carry nothing, so E31 replaces it ONE KEY
    # FOR ONE by `post_only_block` -- the only bool that can vary. The top
    # level is 38 keys, one FEWER than before this lane, which keeps it under
    # the health endpoint's 40-key cap (api/app.py:588) that test_e9_fast_path
    # pins never to breach
    assert 'stats["post_only_block"] = bool(t.post_only_block' in src
    assert 'stats["post_only"]' not in src
    assert len(ml._new_stats()) == 38 and ml._new_stats()["post_only_block"] is False
    guards = inspect.getsource(ml._global_guards)
    assert "_STATE_POST_ONLY_BLOCK" in guards and 'increase_block = "post_only_block"' in guards
    text = YML.read_text()
    m = re.search(r'^ {16}mirror-post-only-rearm\) need_confirm; SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, "the re-arm preset beside mirror-rearm"
    sql = m.group(1)
    assert "DELETE FROM ingestion_state WHERE key = 'mirror_post_only_block'" in sql
    assert "SELECT key, value FROM ingestion_state WHERE key LIKE 'mirror_%'" in sql
    assert "$ARG" not in sql and "UPDATE" not in sql


# --------------------------------------------------- 5. THE REST THAT STANDS


def test_e31_step_o_leaves_an_add_rest_at_the_maker_wire_and_ttls_a_row_from_before_the_lane():
    o = {"id": 1, "side": BUY, "wire": 0.31, "kind": "increase"}
    book = {"id": 7, "intent": "ORDER_INTENT_BUY_LONG",
            "last_plan": {"maker": {"wire": 0.31, "clause": "his_cent"}}}
    assert ml._maker_rest_stands(o, book) is True
    assert ml._maker_rest_stands({**o, "wire": 0.30}, book) is False, "another cent: the TTL runs"
    assert ml._maker_rest_stands(o, {**book, "last_plan": {}}) is False, "a row from before this lane"
    assert ml._maker_rest_stands(o, {**book, "last_plan": None}) is False
    assert ml._maker_rest_stands({**o, "side": SELL}, book) is False, "a reduce is _priced_exit_rest's"
    assert "_maker_rest_stands(o, book)" in inspect.getsource(ml._reconcile_open)


# ------------------------------------------------------ 6. THE CENSUS PLACE


def test_e31_the_ten_census_names_sit_after_e30s_and_before_drift_smaller_open():
    keys = ml.CENSUS_KEYS
    assert keys[-23:-13] == NEW_NAMES
    assert keys[-24] == "post_only_backoff"
    assert keys[-28:-24] == ("hand_exit", "hand_held", "hand_held_unread", "hand_exit_write_failed")
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped"
    assert len(keys) == 257 and len(set(keys)) == len(keys)
    stats = ml._new_stats()
    assert all(stats["census"][k] == 0 for k in NEW_NAMES)
    assert all(k in keys for k in RETIRED), "the retired names stay declared as documentary zeros"
    assert not [k for k in NEW_NAMES if k in ml._INTEG_CENSUS_KEYS]


def test_e31_every_emit_site_is_named_once_and_the_retired_names_have_none():
    src = inspect.getsource(ml)
    for name in NEW_NAMES:
        if name == "post_only_block":
            # the block is an INCREASE REFUSAL: counted by its own name through
            # t.increase_block (the loss stop's pattern), never by a literal
            assert 'increase_block = "post_only_block"' in src
            continue
        assert src.count(f'_mirror_stop("{name}"') >= 1, name
    # the seventeen have NO emit site left at all
    for name in RETIRED:
        assert f'_mirror_stop("{name}"' not in src, name
    # `filled_take` KEEPS its emit site and its MEANING CHANGES (review
    # HIGH-2): under E31 every row is GTC/GTD and no row has kind 'take', so
    # step O's `maker` is False exactly when `taker_at_placement` is True --
    # a post-only rest the venue filled AT CREATE as a taker. It is the flag's
    # own failure signal, so it is neither retired nor in the coverage hook's
    # unreachable set
    assert '_mirror_stop("filled_take", w)' in src
    assert 'o.get("kind") != "take"' in src, "step O's maker read, unchanged"
    assert "filled_take" not in RETIRED


def test_e31_every_name_is_emitted_here(monkeypatch):
    """The E13 convention (review MEDIUM-3): the worker file's coverage hook
    reads this file's names when it runs alone, so every one of the ten must
    be EMITTED here -- driven through the worker's own worlds -- not merely
    spelt in the source. The delivered version asserted the string was in
    `inspect.getsource(ml)`, which emits nothing and would stay green with
    every emit site deleted."""
    seen: set[str] = set()
    for st in _drive_every_maker_name(monkeypatch):
        seen |= {k for k in NEW_NAMES if _census(st, k) >= 1}
    assert seen == set(NEW_NAMES), sorted(set(NEW_NAMES) - seen)


# ------------------------------------------------------------ 7. THE RAILS


def test_e31_no_environment_value_re_arms_a_take_in_a_fresh_interpreter():
    """The retired rails keep their defaults and their directions; what
    changed is that NO value of any of them can send an order that crosses --
    there is no reader left on the money path. The count pins stand: no rail
    is added and none is deleted."""
    code = (
        "import sportsassets.analytics.mirror_live_rules as r, inspect;"
        "s=inspect.getsource(r);"
        "print(r.MIRROR_TAKE_AFTER_S, r.MIRROR_TAKE_BAND, r.MIRROR_TAKE_BAND_FRAC,"
        " r.MIRROR_EXIT_TOL, r.MIRROR_EXIT_TAKE_BAND, r.MIRROR_FLATTEN_REST_S, r.MIRROR_FLATTEN_SLIP,"
        " r.MAKER_TICK, s.count('capped_env('), s.count('min_wait_env('), s.count('env_switch('))"
    )
    env = {**os.environ, "MIRROR_TAKE_AFTER_S": "1e9", "MIRROR_TAKE_BAND": "0.05",
           "MIRROR_TAKE_BAND_FRAC": "0.5", "PMUS_MIRROR_POST_ONLY": "off",
           "PYTHONPATH": str(ROOT / "backend")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, cwd=str(ROOT / "backend"))
    assert out.returncode == 0, out.stderr
    vals = out.stdout.split()
    assert float(vals[0]) == 1e9, "the wait may be lengthened, and nothing reads it"
    assert float(vals[1]) == 0.02 and float(vals[2]) == 0.05, "a wider band is refused (capped_env)"
    assert float(vals[7]) == 0.01, "MAKER_TICK is not a knob"
    assert (int(vals[8]), int(vals[9]), int(vals[10])) == (26, 8, 8), "no rail added, none deleted"
    # the flag itself: nothing in the worker reads the shell variable any more
    assert 'os.environ.get("PMUS_MIRROR_POST_ONLY"' not in inspect.getsource(ml)


def test_e31_the_retired_rails_are_declared_with_no_reader_on_the_money_path():
    # the DECLARATIONS, by source: this module now carries the worker suite's
    # autouse fixture (review MEDIUM-4, so its _tick worlds run armed), and
    # that fixture pins MIRROR_TAKE_BAND at 0.0 for its own rest-first world.
    # The code default is what this test is about, so it is read where it is
    # written; the fresh-interpreter test below reads the same eight again
    # under hostile environment values
    rsrc = inspect.getsource(rules)
    for decl in ('MIRROR_TAKE_AFTER_S = min_wait_env("MIRROR_TAKE_AFTER_S", 0.0)',
                 'MIRROR_TAKE_BAND = capped_env("MIRROR_TAKE_BAND", 0.02, floor=0.0)',
                 'MIRROR_TAKE_BAND_FRAC = capped_env("MIRROR_TAKE_BAND_FRAC", 0.05, floor=0.0)',
                 'MIRROR_EXIT_TAKE_BAND = capped_env("MIRROR_EXIT_TAKE_BAND", 0.01, floor=0.0)',
                 "EXIT_BAND_INERT_AT = 0.01",
                 "MIRROR_FLATTEN_SLIP = 0.02",
                 'MIRROR_FLATTEN_REST_S = min_wait_env("MIRROR_FLATTEN_REST_S", 300.0)',
                 'MIRROR_EXIT_TOL = capped_env("MIRROR_EXIT_TOL", 0.01, floor=0.0)'):
        assert f"\n{decl}\n" in rsrc, decl
    src = _code(ml)
    for gone in ("MIRROR_TAKE_AFTER_S", "MIRROR_TAKE_BAND", "MIRROR_TAKE_BAND_FRAC",
                 "MIRROR_EXIT_TAKE_BAND", "MIRROR_FLATTEN_SLIP", "MIRROR_FLATTEN_REST_S"):
        assert f"rules.{gone}" not in src, gone
    # MIRROR_EXIT_TOL keeps ONE reader: exit_terms, for the plan's record
    assert "MIRROR_EXIT_TOL" in inspect.getsource(rules.exit_terms)


# ------------------------------------------------------------- 8. THE DOCS


def test_e31_the_docs_name_the_rule_and_the_supersessions():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E31 -- .* \(2026-09-10, FILL lane 31\)", doc, re.M), "the E31 section header"
    sec = doc[doc.index("## 75. E31"):]
    for k in NEW_NAMES + ("maker_wire", "maker_compare_wire", "MAKER_TICK", "MAKER_CROSS_MAX_TICKS",
                          "MAKER_CROSS_HOLD_S", "ttl_stands", "_maker_rest_stands", "_rest_reread",
                          "mirror_post_only_block", "mirror-post-only-rearm", "maker-rests",
                          "test_e31_maker_only.py", "aggressor", "participateDontInitiate",
                          "E4's ADDENDUM", "D1 (a)", "E27 ITEM 1", "E21", "S4", "LANE 3", "DOCS 73",
                          "-0.4418", "-0.0313", "0.3588", "1,036", "248 sells"):
        assert k in sec, k
    for name in RETIRED:
        assert name in sec, name


# ---------------------------------------------------------- 9. THE PRESETS


def test_e31_the_maker_rests_preset_is_two_read_only_statements_after_take_band():
    text = YML.read_text()
    m = re.search(r'^ {16}maker-rests\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, "the maker-rests case"
    sql, to = m.group(1), int(m.group(2))
    assert to == 60000 and sql.count("WITH e AS (SELECT") == 2
    for word in ("o.post_only IS TRUE", "o.tif IN ('GTC', 'GTD')", "AS clause", "'his_cent'", "'touch'",
                 "'unpriced'", "AS rests", "AS standing", "AS fill_pct", "AS maker_fill_pct",
                 "AS crossed_at_create", "AS from_his_c_med", "AS from_touch_c_med", "AS ttf_med_s",
                 "AS unfilled_usd", "AS roi", "AS ci95", "GROUP BY ROLLUP (side_w, leg, clause)",
                 "AS touch_moved", "AS same_quote", "AS qty_regrow", "AS rejected_cross",
                 "AS repriced", "AS held_backoff", "b.whale = 'rn1'"):
        assert word in sql, word
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "need_confirm", "$ARG"):
        assert bad not in sql, bad
    # the help arm lists both new presets in the case block's order, hourly last
    line = [ln for ln in text.splitlines() if ln.strip().startswith('*) echo "sql: arg must be one of ')][0]
    names = line.split("one of ")[1].split(" (got")[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels and names[-1] == "hourly" and len(names) == len(set(names))
    assert names.index("maker-rests") == names.index("take-band") + 1
    assert names.index("mirror-post-only-rearm") == names.index("mirror-rearm") + 1
    # the hourly carries it after take-band
    hourly = re.search(r'^ {16}hourly\) SQL="(.*?)"; TO=\d+; HEAD=\d+ ;;', text, re.M | re.S).group(1)
    markers = re.findall(r"SELECT '== ([a-z-]+)' AS section;", hourly)
    assert markers.index("maker-rests") == markers.index("take-band") + 1 and len(markers) == 11


# ------------------------------------------ 10. THE WORKER WORLD (review MEDIUM-4)
#
# The lane rewrites the money path, so its own file must watch orders LEAVE.
# `_armed` is imported at the head of this file: without that name in this
# module's namespace the worker suite's autouse fixture does not apply here
# and every `_tick` below would run in SAFE mode and place nothing.


def _plan_of(book) -> dict:
    lp = book.get("last_plan")
    return json.loads(lp) if isinstance(lp, str) else (lp or {})


def _entry_worlds():
    """Design section 8 group 3 (the entry take from fills-answered
    h0309_short.txt:1040 -- his BUY 4,855 @0.50 answered `take`): the ask
    INSIDE his cent rests at his cent; the ask AT his cent rests one tick
    under it, `maker_rest_at_touch`."""
    p = _pool()
    b1 = p.add_book(ledger=0)
    v = _Venue()                                  # bid 0.30 / ask 0.32, his 0.31
    st1 = _tick(p, v)
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    v2 = _Venue(bid=0.29, ask=0.31)               # the ask AT his cent
    st2 = _tick(p2, v2)
    return (st1, v, b1), (st2, v2, b2)


def test_e31_an_entry_rests_at_his_cent_inside_the_spread_and_a_tick_under_an_ask_that_reached_it():
    """The lane's central price change, end to end. Before E31 the entry
    rested at buy_price(his, bid) = floor(min(0.31, 0.30)) = 0.30 -- it JOINED
    THE BID -- and with the ask at his cent it TOOK (h0309_short.txt:1301: 298
    such orders, roi -0.4418). Now: 0.31 inside the spread, and 0.30 when the
    ask is at 0.31 -- one tick under it, never at or through it."""
    (st1, v1, b1), (st2, v2, b2) = _entry_worlds()
    assert [c[2:8] for c in _places(v1)] == [(0.31, 300, False, GTC_TIF, "ORDER_INTENT_BUY_LONG", True)]
    assert _census(st1, "rest_placed") == 1 and _census(st1, "maker_rest_at_touch") == 0
    assert [c[2:8] for c in _places(v2)] == [(0.30, 300, False, GTC_TIF, "ORDER_INTENT_BUY_LONG", True)]
    assert _census(st2, "maker_rest_at_touch") == 1
    # and no take counter moves on either road
    for st in (st1, st2):
        for name in RETIRED:
            assert _census(st, name) == 0, name
    assert not [c for c in _places(v1) + _places(v2) if c[5] == IOC_TIF]
    # the clause the maker-rests preset reads: his own cent, or the bound
    assert _plan_of(b1)["maker"]["clause"] == "his_cent"
    assert _plan_of(b2)["maker"]["clause"] == "touch"
    # a rest AT the bound is RE-READ once immediately before the send (the
    # record is the plan's); a rest a tick or more INSIDE it is not -- a
    # one-tick move cannot cross it
    assert _plan_of(b2)["rest_quote_at_send"]["ask_at_plan"] == 0.31
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    v3 = _Venue(bid=0.30, ask=0.33)          # his 0.31, the bound 0.32
    _tick(p3, v3)
    assert [c[2:4] for c in _places(v3)] == [(0.31, 300)]
    assert "rest_quote_at_send" not in _plan_of(b3)


def test_e31_no_cent_holds_the_standing_rest_by_name_and_never_cancels_it():
    """REVIEW CRITICAL-2. The clamp reads the NEAR touch, which no wire of
    this file read before E31, so a one-sided book (_bbo names `no_quote` only
    when BOTH sides are None) or the ladder's edge -- an ask at 0.01, a bid at
    0.99 -- can leave no cent for a NEW order. A rest that is ALREADY resting
    cannot cross, and taking it off the book loses the queue this lane exists
    to keep AND leaves the position with no order at all: an exit cancelled at
    the very moment the market reached it, the mandate's directional rule
    broken while he is out. It stands, held `maker_no_cent`."""
    assert rules.maker_wire(SELL, 0.99, 0.99, 0.995) is None      # the bid AT 0.99
    assert rules.maker_wire(SELL, 0.31, None, 0.32) is None       # the bid unread
    assert rules.maker_compare_wire(SELL, 0.31, 0.31, None) is None
    p = _pool(fills=_his(300, sold=100), snap={M: 200.0, N: 0.0})
    b = p.add_book(ledger=300)
    p.add_order(b, side=SELL, wire=0.31, qty=100, kind="reduce")
    v = _Venue(held={SLUG: 300}, bid=None, ask=0.32)
    v.rest("oid-1", "SELL", 0.31, 100)
    st = _tick(p, v)
    assert not _cancels(v), _cancels(v)          # the exit is NOT taken off the book
    assert not _places(v), _places(v)            # and no guessed price goes out
    assert _census(st, "maker_no_cent") == 1 and _census(st, "no_price") == 0
    assert b["last_reason"] == "maker_no_cent"
    assert [o["state"] for o in p.orders.values()] == ["open"]
    assert [o["wire"] for o in p.orders.values()] == [0.31]


def test_e31_the_exit_rests_at_his_cent_and_the_touch_that_comes_to_it_never_moves_it():
    """Design section 8 group 2, from the tick's own rows (mtick_0309.txt:291,
    :389-395, book 1393): ONE exit sold 2 of 5 as a TAKER at his cent less a
    tick and rested the remainder, five IOCs on that book in 1 min 44 s. Now
    the whole reduce is ONE post-only GTC rest at his cent and the taker who
    comes to it pays us the spread."""
    p = _pool(fills=_his(300, sold=100), snap={M: 200.0, N: 0.0})
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v)
    assert [c[2:8] for c in _places(v)] == [(0.31, 100, True, GTC_TIF, "ORDER_INTENT_BUY_LONG", True)]
    assert _census(st, "exit_take") == 0 and _census(st, "take_placed") == 0
    mk = e30._plan(b)["maker"] if hasattr(e30, "_plan") else None
    assert mk is None or mk["clause"] == "his_cent"
    # the bid arriving AT the rest is the rest being FILLED, not a reason to move
    p2 = _pool(fills=_his(300, sold=100), snap={M: 200.0, N: 0.0})
    b2 = p2.add_book(ledger=300)
    p2.add_order(b2, side=SELL, wire=0.31, qty=100, kind="reduce", placed_ts=NOW - 100)
    v2 = _Venue(held={SLUG: 300}, bid=0.31, ask=0.33)
    v2.rest("oid-1", "SELL", 0.31, 100, created=NOW - 100)
    _tick(p2, v2)
    assert not _cancels(v2) and not _places(v2), (v2.calls, "the touch came to it")


def test_e31_a_standing_rest_moves_when_the_touch_makes_room_toward_his_level():
    """E31 (C) and review MEDIUM-5 widened. A BUY clamped at the bound (0.30,
    the ask having been at 0.31) is re-quoted to his own cent when the ask
    rises to 0.32 -- the TOUCH moved, his cent did not -- and the record says
    so (`replaced_by` 'touch', census `maker_rest_repriced`). The
    discriminator is his cent ACROSS THE TWO TICKS, off the book's last plan:
    comparing the standing wire with his cent instead reads this, the ONE
    class the counter exists for, as a move of his level."""
    p = _pool()
    b = p.add_book(ledger=0)
    b["last_plan"] = json.dumps({"maker": {"wire": 0.30, "his_cent": 0.31, "clause": "touch"}})
    p.add_order(b, side=BUY, wire=0.30, qty=300, placed_ts=NOW - 100)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-1", "BUY", 0.30, 300, created=NOW - 100)
    st = _tick(p, v)
    assert len(_cancels(v)) == 1 and [c[2:4] for c in _places(v)] == [(0.31, 300)]
    assert _census(st, "maker_rest_repriced") == 1
    plan = json.loads(b["last_plan"]) if isinstance(b["last_plan"], str) else b["last_plan"]
    assert plan["replaced_by"] == "touch" and plan["replaced"] == "replace_cent"
    # HIS LEVEL moving is the other word, on the same clause
    assert ml._replaced_by({"cause": "cent"}, {"wire": 0.30},
                           0.32, {"last_plan": {"maker": {"his_cent": 0.31}}}) == "his_level"
    assert ml._replaced_by({"cause": "cent"}, {"wire": 0.30}, None, {}) == "touch", "an unpriced exit"


def test_e31_the_touch_bound_re_read_falls_back_to_the_ticks_own_cent_by_name(monkeypatch):
    """E31 (D). A rest AT the bound is re-read once before the send; a read
    that comes back without the side the clamp needs, and an ENTRY's read past
    the call budget, both send the tick's own cent -- a read fact the tick's
    quote already said does not cross, with the venue's flag as the backstop
    -- and both are counted by name."""
    class _Half(_Venue):
        def bbo_read(self, client, slug):
            self.calls.append(("bbo", slug))
            self.n += 1
            if self.n >= 2:
                return {"bid": None, "ask": None, "state": self.state, "error": "RuntimeError"}
            return {"bid": self.bid, "ask": self.ask, "state": self.state, "error": None}
    p = _pool()
    p.add_book(ledger=0)
    v = _Half(bid=0.29, ask=0.31)
    st = _tick(p, v)
    assert _census(st, "rest_quote_unread") == 1 and _census(st, "rest_reread_capped") == 0
    assert [c[2:4] for c in _places(v)] == [(0.30, 300)], "the tick's own cent, never a guess"
    # the budget spent: an ENTRY's re-read is refused, an EXIT's never is (M-1)
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 0)
    p2 = _pool()
    p2.add_book(ledger=0)
    v2 = _Venue(bid=0.29, ask=0.31)
    st2 = _tick(p2, v2)
    assert _census(st2, "rest_reread_capped") == 1
    assert [c[2:4] for c in _places(v2)] == [(0.30, 300)]
    assert 'action != "reduce" and t.guard_calls >= rules.MIRROR_VENUE_CALLS_PER_TICK' \
        in inspect.getsource(ml._rest_reread)


def test_e31_the_durable_block_is_written_by_the_venues_own_aggressor_and_refuses_every_add():
    """Design section 8 group 7, D2. A post-only rest the venue fills AT
    CREATE is read by its own `aggressor` bool: every record False is a MAKER
    fill (a taker hit the fresh rest), anything else is the venue telling us
    the flag did not hold -- and then the ADDS stop, not the flag (task 30,
    order 153 at 17:36Z, where post-only went off for the whole process and
    every later rest went out crossable)."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue(lift=300.0, lift_maker=True)         # aggressor False: a maker fill
    st = _tick(p, v)
    assert _census(st, "maker_fill_at_create") == 1 and _census(st, "post_only_ignored") == 0
    assert b["ledger_net"] == 300 and p.state.get("mirror_post_only_block") is None
    assert st["post_only_block"] is False and _census(st, "filled_take") == 0
    # the venue calling ITSELF the resting side: the block, and the flag stays
    p2 = _pool()
    p2.add_book(ledger=0)
    v2 = _Venue(lift=300.0, lift_maker=False)       # aggressor True
    st2 = _tick(p2, v2)
    assert _census(st2, "post_only_ignored") == 1 and _census(st2, "maker_fill_at_create") == 0
    assert p2.state["mirror_post_only_block"]["filled"] == 300.0
    assert st2["status"] == "degraded" and st2["post_only_block"] is True
    assert _places(v2)[0][7] is True, "the flag never leaves the send"
    # the next process reads the key and refuses the add BY NAME; the exit runs
    p3 = _pool()
    p3.state["mirror_post_only_block"] = {"at": "x", "book": 1}
    p3.add_book(ledger=0)
    v3 = _Venue()
    st3 = _tick(p3, v3)
    assert not _places(v3) and _census(st3, "post_only_block") >= 1
    p4 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    p4.state["mirror_post_only_block"] = {"at": "x", "book": 1}
    p4.add_book(ledger=300)
    v4 = _Venue(held={SLUG: 300})
    _tick(p4, v4)
    assert [c[3:6] for c in _places(v4)] == [(200, True, GTC_TIF)], _places(v4)
    assert _places(v4)[0][7] is True


def test_e31_an_unreadable_block_key_blocks_adds_exactly_as_a_written_one_does():
    """REVIEW HIGH-4 (the mutant M14 this kills). E31 (D2) fails CLOSED on the
    block: `t.post_only_block = bool(err is not None or blocked is not None)`.
    A stop that cannot be read IS a stop -- the loss stop's own stance --
    because the alternative is adding into a venue we cannot confirm is
    honouring `participateDontInitiate`. Dropping the `err` half leaves every
    other test in the suite green and places the add."""
    p = _pool()
    p.state["mirror_post_only_block"] = "{not json"     # _state -> (None, "malformed")
    p.add_book(ledger=0)
    v = _Venue()
    st = _tick(p, v)
    assert not _places(v), _places(v)
    assert _census(st, "post_only_block") >= 1 and st["post_only_block"] is True
    # and the EXIT on a blocked process still goes out, post-only, at his cent
    p2 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    p2.state["mirror_post_only_block"] = "{not json"
    p2.add_book(ledger=300)
    v2 = _Venue(held={SLUG: 300})
    _tick(p2, v2)
    assert [c[3:6] for c in _places(v2)] == [(200, True, GTC_TIF)], _places(v2)
    assert _places(v2)[0][7] is True
    assert "err is not None or blocked is not None" in inspect.getsource(ml._global_guards)


def test_e31_the_unpriced_flatten_rests_at_the_touch_and_no_close_is_ever_sent():
    """Design section 8 group 9. A vanish HE GAVE NO PRICE FOR used to rest at
    the ask for MIRROR_FLATTEN_REST_S and then run the slippage leg --
    close_position at 300 bips, or the co-held IOC at the bid less two cents.
    Now it rests at the touch's own inside tick, re-quoted every tick, and
    `close` never appears in the venue's calls."""
    from tests.test_mirror_live_worker import _gone, _unpriced
    p = _pool(fills=_unpriced(), snap=None)
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=_gone())
    assert "close" not in _kinds(v)
    assert [c[2:8] for c in _places(v)] == [(0.31, 300, True, GTC_TIF, "ORDER_INTENT_BUY_LONG", True)]
    assert _census(st, "flatten_rested") == 1
    plan = json.loads(b["last_plan"]) if isinstance(b["last_plan"], str) else b["last_plan"]
    assert plan["maker"]["clause"] == "unpriced_touch" and plan["maker"]["his_cent"] is None


def _cross_worlds(monkeypatch):
    """Design section 8 group 1 on E30's own 1383 fixtures (book_1383_0234.txt
    rows 4-5, 8-17: his 0.89, bid 0.75 / ask 0.76, target -145). The 200 shape
    -- ORDER_STATE_REJECTED with an EXECUTION_TYPE_REJECTED execution and zero
    filled, the only shape the venue itself calls a cross -- re-prices one
    tick FURTHER from the touch each time (0.89 -> 0.90 -> 0.91 -> 0.92: we
    are paid more for the contract, so we pay less for the other token than
    his 0.11), then holds by name."""
    raw = {"status_code": 200, "post_only_cross": True, "reject_reason": "post_only_cross",
           "execution_type": rules.EXECUTION_TYPE_REJECTED, "order_state": rules.ORDER_STATE_REJECTED}
    out = []
    _shorts_on(monkeypatch)
    ml._cross_hint.clear()
    p = e30._p1383()
    b = e30._b1383(p)
    for i in range(5):
        v = e30._v(place=e30._reject(raw=raw))
        out.append((_tick(p, v, now=NOW + i, http=e30._http_1383()), v, b))
    return out


def test_e31_a_rejection_the_venue_calls_a_cross_is_re_priced_one_tick_off_the_touch_then_held(monkeypatch):
    ticks = _cross_worlds(monkeypatch)
    wires = [[c[2] for c in _places(v)] for _st, v, _b in ticks]
    assert wires == [[0.89], [0.90], [0.91], [0.92], []], wires
    assert [_census(st, "maker_cross_repriced") for st, _v, _b in ticks] == [1, 1, 1, 1, 0]
    assert _census(ticks[-1][0], "maker_cross_held") == 1
    assert not [c for _st, v, _b in ticks for c in _places(v) if c[5] == IOC_TIF]
    b = ticks[-1][2]
    assert b["last_reason"] == "maker_cross_held"
    plan = json.loads(b["last_plan"]) if isinstance(b["last_plan"], str) else b["last_plan"]
    assert plan["cross_hint"]["n"] == 4 > ml.MAKER_CROSS_MAX_TICKS
    # and the arm the old path wrote is not written: no take exists to arm
    assert b.get("take_armed_ts") in (None, 0)


def _drive_every_maker_name(monkeypatch):
    """Every one of E31's ten names, EMITTED (review MEDIUM-3), so the worker
    file's coverage hook sees them when it -- or this file -- runs alone."""
    out = []
    (st1, _v1, _b1), (st2, _v2, _b2) = _entry_worlds()             # maker_rest_at_touch
    out += [st1, st2]
    p = _pool()
    b = p.add_book(ledger=0)
    b["last_plan"] = json.dumps({"maker": {"wire": 0.30, "his_cent": 0.31, "clause": "touch"}})
    p.add_order(b, side=BUY, wire=0.30, qty=300, placed_ts=NOW - 100)
    v = _Venue(bid=0.30, ask=0.32)
    v.rest("oid-1", "BUY", 0.30, 300, created=NOW - 100)
    out.append(_tick(p, v))                                        # maker_rest_repriced
    out += [st for st, _v, _b in _cross_worlds(monkeypatch)]       # cross_repriced / cross_held
    p2 = _pool(fills=_his(300, sold=100), snap={M: 200.0, N: 0.0})
    b2 = p2.add_book(ledger=300)
    p2.add_order(b2, side=SELL, wire=0.31, qty=100, kind="reduce")
    v2 = _Venue(held={SLUG: 300}, bid=None, ask=0.32)
    v2.rest("oid-1", "SELL", 0.31, 100)
    out.append(_tick(p2, v2))                                      # maker_no_cent
    p3 = _pool()
    p3.add_book(ledger=0)
    out.append(_tick(p3, _Venue(lift=300.0, lift_maker=True)))     # maker_fill_at_create
    p4 = _pool()
    p4.state["mirror_post_only_block"] = "{not json"
    p4.add_book(ledger=0)
    out.append(_tick(p4, _Venue()))                                # post_only_block

    class _Half(_Venue):
        def bbo_read(self, client, slug):
            self.calls.append(("bbo", slug))
            self.n += 1
            if self.n >= 2:
                return {"bid": None, "ask": None, "state": self.state, "error": "RuntimeError"}
            return {"bid": self.bid, "ask": self.ask, "state": self.state, "error": None}
    p5 = _pool()
    p5.add_book(ledger=0)
    out.append(_tick(p5, _Half(bid=0.29, ask=0.31)))               # rest_quote_unread
    old = rules.MIRROR_VENUE_CALLS_PER_TICK
    try:
        rules.MIRROR_VENUE_CALLS_PER_TICK = 0
        p6 = _pool()
        p6.add_book(ledger=0)
        out.append(_tick(p6, _Venue(bid=0.29, ask=0.31)))          # rest_reread_capped
    finally:
        rules.MIRROR_VENUE_CALLS_PER_TICK = old
    # ioc_refused: the guard, which no caller in this worker can reach
    p7 = e30._p1383()
    b7 = e30._b1383(p7)
    t = ml._Tick(pool=p7, pmus=e30._v(place=None), http=None, now=NOW, stats=ml._new_stats())
    before = int(ml._MIRROR_CENSUS.get("ioc_refused|rn1") or 0)
    assert _run(ml._place(t, b7, _reading(), "take", SELL, 0.89, 145,
                          e30.HIS, None, {}, tif="IOC")) == "ioc_refused"
    after = int(ml._MIRROR_CENSUS.get("ioc_refused|rn1") or 0)
    out.append({"census": {"ioc_refused": after - before}})     # the process census _mirror_stop writes
    return out


def test_e31_the_ttl_only_stands_for_an_entry_rest_already_at_its_own_maker_wire():
    """REVIEW 2 (M5b). `_act`'s `ttl_stands` has three halves -- an ENTRY
    (`not is_exit`), a readable standing wire, and that wire EQUAL to the
    plan's -- and only the last two are observable through the cent clause,
    which replaces a rest at a different cent anyway. Forcing the whole
    expression to True survives every re-pinned file, so the ENTRY half is
    pinned here: at the worker's own line, and at the rule, where the
    difference is real (an unpriced exit rest past MIRROR_REST_TTL_S is
    REPLACED, and would stand for ever if the worker passed True)."""
    src = inspect.getsource(ml._act)
    assert src.count("ttl_stands = bool(p is not None and not is_exit and ow_now is not None") == 1
    assert "and _num(wire) is not None and abs(ow_now - float(wire)) < 1e-9)" in src
    assert src.count("ttl_stands=ttl_stands") == 1
    oo = rules.OpenOrder(SELL, 0.31, 200, 200.0, 0.0, None)
    pl = rules.Plan(SELL, 200, 0.31, "reduce")
    now = float(rules.MIRROR_REST_TTL_S) + 100.0
    kw = dict(wire=0.31, stands=False, entry=False)
    assert rules.rest_decision(oo, pl, now, ttl_stands=False, **kw) == \
        ("replace", {"cause": "ttl", "rest_age_s": now})
    assert rules.rest_decision(oo, pl, now, ttl_stands=True, **kw) == ("keep", {"cause": "same"})
