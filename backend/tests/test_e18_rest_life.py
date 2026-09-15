"""E18 (2026-09-08; PNL program lane 6): the entry rest lives 45 s, the
IOC re-reads the quote before the send, migration 059 records the send.

Owner (~11:50Z, verbatim): "Fix all 3 of these immediately. I want to
know when he makes money we make money. This needs to be right." The
11Z window (hard2/hourly_1129.log rows 2697-2736): 10 of 24 increase
rests cancelled `replace` after lives 14, 14, 15, 16, 19, 21, 27, 30, 64,
424 s (median 20 s) -- book 533's 2725 5@0.43 (16 s), 2729 23@0.48
(15 s), 2731 23@0.48 (30 s) replaced, 2734 23@0.49 FILLED after 100 s
once left alone; book 278's four rests at the SAME cent 0.62 (qty 86 ->
316 -> 493 -> 451) filled nothing. Book 509's cover IOCs 2726 / 2727
expired 0 filled because the ask moved between the tick's quote read
and the send; 2730 filled 11 @0.62 a wake later.

THE RULE. rules.MIRROR_REST_MIN_LIFE_S = min_wait_env(45.0) (env may only
LENGTHEN): an ENTRY rest younger than it is 'keep' when the side and
intent stand and the cent moved under REST_MIN_LIFE_CENT_MOVE (2c); a
quantity-only move is 'keep' with `add_pending` on the plan; a 2c move,
a side or intent change, the TTL and an unreadable fact replace as
before; exits never read the floor. Every IOC re-reads the quote once
through the E11 pacer immediately before the send and is withheld by
name when the level has left (`ask_moved` / `bid_moved`), when the
re-read would pass the call budget (`ioc_reread_capped`, entries) or is
unreadable (`ioc_quote_unread`) -- the rest is placed as today.
Migration 059: ask_at_send / decision / his_fill_id on mirror_orders,
read the 057 way (absent: the 050 INSERT).

Driven against the worker file's fakes (its autouse rails are imported);
the migration against a scratch Postgres where one answers (skips
visibly otherwise -- text is not proof).
"""
import re
import importlib
import inspect
import logging
import os
import pathlib
import uuid

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.scripts import migrate
from sportsassets.workers import mirror_live as ml
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _UndefinedColumn, _Venue, _armed, _cancels, _census, _fill, _his, _places,
    _pool, _run, _tick,
)

GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
MIG_DIR = pathlib.Path(migrate.MIGRATIONS_DIR)
SQL_059 = MIG_DIR / "059_mirror_orders_send_record.sql"
ROOT = pathlib.Path(__file__).resolve().parents[2]
DSN_BASE = os.environ.get(
    "MIRROR_SQL_PIN_DSN",
    os.environ.get("S1_SQL_PIN_DSN",
                   "postgresql://sportsassets:sportsassets@localhost:5432/postgres"))
# E31 (FILL lane 31, 2026-09-10: every order a post-only rest that never crosses) RE-PINNED
# this file. Two things moved and nothing else:
#   (a) THE ENTRY CENT. A BUY rested at buy_price(his, bid) = floor(min(his, bid)), JOINING THE
#       BID; the maker wire is min(buy_wire(his), ask - 0.01) -- his own cent inside the spread
#       while it is under the ask, one tick under the ask when it is not. So on this file's
#       default 0.30 / 0.32 book with his 0.31 the rest is 0.31, not 0.30, and a world that
#       wanted the wire to MOVE now moves the ASK, not the bid.
#   (b) THE RE-READ. `_ioc_reread` (the IOC's) is `_rest_reread` (the touch-bound rest's, E31 D)
#       at the same site, so `ioc_reread_capped` -> `rest_reread_capped`, `ioc_quote_unread` ->
#       `rest_quote_unread`, `ioc_quote_at_send` -> `rest_quote_at_send`, and `ask_moved` /
#       `bid_moved` are gone with the take they withheld: a rest is never withheld, it is
#       RE-PRICED on the fresher quote and goes out.
# The rule this file exists for -- the 45 s floor, `add_pending`, the 2c move, the TTL, and
# migration 059's three columns -- is untouched.
NEW_NAMES = ("kept_min_life", "rest_reread_capped", "rest_quote_unread", "order_cols_guard_unreadable")
RETIRED = ("ask_moved", "bid_moved", "ioc_reread_capped", "ioc_quote_unread")   # E31: declared zeros


class _MovingVenue(_Venue):
    """The fixture venue whose quote on the fixture market MOVES between
    reads: `seq` is the (bid, ask) each successive read of SLUG hands
    back (the tick's read first, the IOC's re-read second); past the
    sequence the last pair stands."""

    def __init__(self, seq, **kw):
        super().__init__(**kw)
        self.seq = list(seq)

    def bbo_read(self, client, slug):
        if slug == SLUG and self.seq:
            self.bid, self.ask = self.seq.pop(0)
        return super().bbo_read(client, slug)


def _bbos(v):
    return [c[1] for c in v.calls if c[0] == "bbo"]


def _inserts(p):
    return [a for k, s, a in p.sent if "ml-order-insert" in s]


def _decisions(p):
    return [tuple(a) for k, s, a in p.sent if "ml-order-decision" in s]


def _one_book(p):
    assert len(p.books) == 1
    return next(iter(p.books.values()))


# ------------------------------------------------------------- the rule

def _rest(wire=0.47, qty=100, leaves=None, placed=1000.0, side=rules.BUY, intent=None):
    return rules.OpenOrder(side, wire, qty, qty if leaves is None else leaves, placed, intent)


def test_e18_an_entry_rest_twenty_seconds_old_keeps_over_a_one_cent_move_and_replaces_over_two():
    p = mi.Plan(rules.BUY, 100, 0.48, "x")
    assert rules.keep_or_replace(_rest(), p, 1020.0, wire=0.48) == "keep"
    v, why = rules.rest_decision(_rest(), p, 1020.0, wire=0.48)
    assert v == "keep" and why["kept_min_life"] is True and why["cause"] == "min_life"
    assert why["cent_moved"] == 0.01 and why["rest_age_s"] == 20.0 and why["floor_s"] == 45.0
    assert "add_pending" not in why
    # two cents: replaced at once, the cause named
    assert rules.rest_decision(_rest(), p, 1020.0, wire=0.49) == ("replace", {"cause": "cent", "cent_moved": 0.02})
    assert rules.rest_decision(_rest(), p, 1020.0, wire=0.45) == ("replace", {"cause": "cent", "cent_moved": 0.02})
    # the same 1c move at 46 s: replaced, as before the floor
    assert rules.rest_decision(_rest(), p, 1046.0, wire=0.48) == ("replace", {"cause": "cent", "cent_moved": 0.01})
    assert rules.rest_decision(_rest(), p, 1045.0, wire=0.48)[0] == "replace", "at the floor is past it"
    assert rules.rest_decision(_rest(), p, 1044.9, wire=0.48)[0] == "keep"


def test_e18_book_278s_quantity_growth_on_a_young_rest_keeps_with_add_pending_and_replaces_past_the_floor():
    # four rests at the SAME cent 0.62, qty 86 -> 316 -> 493 -> 451
    o = _rest(wire=0.62, qty=86, placed=1000.0)
    v, why = rules.rest_decision(o, mi.Plan(rules.BUY, 316, 0.62, "x"), 1020.0, wire=0.62)
    assert v == "keep" and why["kept_min_life"] is True and why["cent_moved"] == 0.0
    assert why["add_pending"] == {"qty": 316, "wire": 0.62, "since": 1020.0}
    assert rules.keep_or_replace(o, mi.Plan(rules.BUY, 316, 0.62, "x"), 1020.0, wire=0.62) == "keep"
    # a 1c move AND a quantity move on a young rest: kept, the growth carried
    v, why = rules.rest_decision(o, mi.Plan(rules.BUY, 493, 0.63, "x"), 1030.0, wire=0.63)
    assert v == "keep" and why["add_pending"]["qty"] == 493 and why["add_pending"]["wire"] == 0.63
    # 46 s: the quantity move replaces, cause 'qty'
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 316, 0.62, "x"), 1046.0, wire=0.62) == ("replace", {"cause": "qty"})
    # within a share or MIN_MOVE_FRAC: the plain keep, no growth to carry
    assert rules.rest_decision(o, mi.Plan(rules.BUY, 87, 0.62, "x"), 1020.0, wire=0.62) == ("keep", {"cause": "same"})


def test_e18_a_side_or_intent_change_the_ttl_and_an_unreadable_fact_replace_a_young_rest_as_before():
    p = mi.Plan(rules.BUY, 100, 0.47, "x")
    assert rules.rest_decision(_rest(side=rules.SELL), p, 1020.0, wire=0.47) == ("replace", {"cause": "side"})
    assert rules.rest_decision(_rest(intent="ORDER_INTENT_BUY_SHORT"), p, 1020.0, wire=0.47,
                               intent="ORDER_INTENT_BUY_LONG") == ("replace", {"cause": "intent"})
    assert rules.rest_decision(_rest(leaves=None), p, 1020.0, wire=0.47)[0] == "keep"
    assert rules.rest_decision(rules.OpenOrder(rules.BUY, 0.47, 100, None, 1000.0), p, 1020.0, wire=0.47) \
        == ("replace", {"cause": "unreadable"})
    assert rules.rest_decision(_rest(placed=1030.0), p, 1020.0, wire=0.47) == ("replace", {"cause": "future"})
    assert rules.rest_decision(_rest(), mi.Plan(rules.BUY, 0, 0.47, "x"), 1020.0, wire=0.47) \
        == ("replace", {"cause": "under_one_share"})
    assert rules.rest_decision(_rest(wire=0.475), p, 1020.0, wire=0.47) == ("replace", {"cause": "cent"})
    assert rules.rest_decision(_rest(), p, 1020.0, wire=None) == ("no_price", {"cause": "no_price"})
    # THE TTL STILL WINS at 600 s, even under a floor lengthened past it
    ttl = float(rules.MIRROR_REST_TTL_S)
    assert ttl == 600.0
    assert rules.rest_decision(_rest(), p, 1000.0 + ttl, wire=0.47) == ("replace", {"cause": "ttl", "rest_age_s": ttl})
    assert rules.rest_decision(_rest(), p, 1000.0 + ttl, wire=0.47, min_life_s=1e9)[0] == "replace"
    assert rules.rest_decision(_rest(), p, 1000.0 + ttl - 1, wire=0.48, min_life_s=1e9)[0] == "keep"
    # the caller's floor can only LENGTHEN: 0 and junk are the constant
    assert rules.rest_decision(_rest(), p, 1020.0, wire=0.48, min_life_s=0.0)[0] == "keep"
    assert rules.rest_decision(_rest(), p, 1020.0, wire=0.48, min_life_s="junk")[0] == "keep"
    assert rules.rest_decision(_rest(), p, 1050.0, wire=0.48, min_life_s=90.0)[0] == "keep"
    assert rules.rest_decision(_rest(), p, 1050.0, wire=0.48)[0] == "replace"
    # a cancel reason and a plan with no side decide before everything, as before
    assert rules.rest_decision(_rest(), p, 1020.0, wire=0.48, cancel_reason="frozen") == ("frozen", {"cause": "cancel_reason"})
    assert rules.rest_decision(_rest(), None, 1020.0, wire=0.48) == ("no_plan", {"cause": "no_plan"})


def test_e18_an_exit_rest_never_reads_the_floor_e4_unchanged():
    # a priced exit rest (`stands`) at 20 s over a 1c move: replaced at once
    o = rules.OpenOrder(rules.SELL, 0.31, 200, 200, 1000.0)
    p = mi.Plan(rules.SELL, 200, 0.32, "reduce")
    assert rules.rest_decision(o, p, 1020.0, wire=0.32, stands=True) == ("replace", {"cause": "cent", "cent_moved": 0.01})
    assert rules.keep_or_replace(o, p, 1020.0, wire=0.32, stands=True) == "replace"
    # an UNPRICED exit rest: the worker says `entry=False` and the floor is off too
    assert rules.rest_decision(o, p, 1020.0, wire=0.32, entry=False) == ("replace", {"cause": "cent", "cent_moved": 0.01})
    assert rules.rest_decision(o, mi.Plan(rules.SELL, 250, 0.31, "reduce"), 1020.0, wire=0.31, entry=False) \
        == ("replace", {"cause": "qty"})
    # `stands` past the TTL at the same cent is still 'keep' (E4 rule 3)
    assert rules.rest_decision(o, mi.Plan(rules.SELL, 200, 0.31, "reduce"), 1000.0 + 5000.0, wire=0.31, stands=True) \
        == ("keep", {"cause": "same"})
    # the worker passes the leg action
    src = inspect.getsource(ml._act)
    # the plan handed in is the CLIPPED plan on an add (the cap is per
    # trade, docs 67: p_cmp is p sized as MIRROR_CLIP_USD would place it)
    assert "entry=not is_exit" in src and "rules.rest_decision(oo, p_cmp, t.now" in src


def test_e18_the_floor_constant_only_lengthens_from_the_environment(monkeypatch):
    assert rules.MIRROR_REST_MIN_LIFE_S == 45.0 and rules.REST_MIN_LIFE_CENT_MOVE == 0.02
    src = inspect.getsource(rules)
    assert 'MIRROR_REST_MIN_LIFE_S = min_wait_env("MIRROR_REST_MIN_LIFE_S", 45.0)' in src
    assert src.count('min_wait_env("MIRROR_REST_MIN_LIFE_S"') == 1 and 'capped_env("MIRROR_REST_MIN_LIFE_S' not in src
    assert "MIRROR_REST_MIN_LIFE_S" in rules.__all__ and "rest_decision" in rules.__all__
    try:
        for raw, want in (("10", 45.0), ("90", 90.0), ("0", 45.0), ("-5", 45.0), ("junk", 45.0),
                          ("inf", 45.0), ("45.5", 45.5)):
            monkeypatch.setenv("MIRROR_REST_MIN_LIFE_S", raw)
            mod = importlib.reload(rules)
            assert mod.MIRROR_REST_MIN_LIFE_S == want, (raw, mod.MIRROR_REST_MIN_LIFE_S)
    finally:
        monkeypatch.delenv("MIRROR_REST_MIN_LIFE_S", raising=False)
        importlib.reload(rules)
    assert rules.MIRROR_REST_MIN_LIFE_S == 45.0
    # the decisions' words
    assert rules.replace_decision({"cause": "cent"}) == "replace_cent"
    assert rules.replace_decision({"cause": "qty"}) == "replace_qty"
    assert rules.replace_decision({"cause": "ttl"}) == "ttl"
    assert rules.replace_decision({"cause": "side"}) == rules.replace_decision({"cause": "intent"}) == "replace_side"
    assert rules.replace_decision({"cause": "unreadable"}) == rules.replace_decision(None) == "replace_unread"
    assert rules.order_decision("add", False, False) == "rest" and rules.order_decision("add", True, False) == "take"
    assert rules.order_decision("reduce", False, False) == "exit_rest" and rules.order_decision("reduce", True, False) == "take"
    assert rules.order_decision("reduce", False, True) == rules.order_decision("reduce", True, True) == "cover"
    # E14 (2026-09-08, FILL lane 2) writes the word 059 reserved, at ONE
    # site in the body (re-pinned from "never written": the rule changed)
    assert inspect.getsource(rules.order_decision).split('"""')[2].count('"take_in_band"') == 1


# ---------------------------------------------------------- the worker

def test_e18_the_worker_keeps_a_young_entry_rest_over_a_one_cent_move_and_replaces_it_past_the_floor():
    """The fixture: his 300 @0.31, the rest at buy_price(0.31, bid). A
    rest at 0.30 placed 20 s ago with the bid now 0.31 (the wire moves
    1c UP to his cent; the rest stays at or under it): kept,
    `kept_min_life` on the plan and the census, no cancel; the same at
    50 s: cancelled `replace`, the row's decision `replace_cent`, a
    fresh rest at 0.31. The wire moving 1c DOWN (the bid at 0.29: the
    rest would stand a cent ABOVE the new wire) replaces at once, as
    before the floor -- the review's MEDIUM-2, folded 2026-09-08 as
    the mandate's reading ("entries at his cent")."""
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.30, qty=300, placed_ts=NOW - 20)
    v = _Venue(bid=0.31, ask=0.33)
    v.rest("oid-1", "BUY", 0.30, 300)
    st = _tick(p, v)
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open"
    assert _census(st, "kept_min_life") == 1 and _census(st, "open_order_pending") == 1
    lp = b["last_plan"]
    assert lp["decision"] == "kept_min_life" and lp["open_order"] == o["id"] and "add_pending" not in lp
    assert lp["rest_life"] == {"age_s": 20.0, "floor_s": 45.0, "cent_moved": 0.01}
    assert _decisions(p) == []
    # 50 s: past the floor, the replace as before -- the cause on the row
    st2 = _tick(p, v, now=NOW + 30)
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and [c[2:6] for c in _places(v)] == [(0.31, 300, False, GTC_TIF)]
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "replace"
    assert _decisions(p) == [(o["id"], "replace_cent")] and b["last_plan"]["replaced"] == "replace_cent"
    assert b["last_plan"]["decision"] == "rest", "the plan's decision names the row placed this tick"
    assert _census(st2, "kept_min_life") == 0 and st2["requotes"] == 1
    # the new rest's row: decision 'rest', ask_at_send NULL, his fill answered
    ins = _inserts(p)
    # FILL lane 9 (migration 061): the fake pool reads the `fast` column present, so the row is the 061
    # shape -- the 059 arguments in their places plus `fast` false (a full tick) as the twenty-third
    assert len(ins) == 1 and len(ins[0]) == 23 and ins[0][18] == "ORDER_INTENT_BUY_LONG"
    assert ins[0][19] is None and ins[0][20] == "rest" and ins[0][21] == str(NOW - 3000) and ins[0][22] is False
    # the 1c move DOWN at 20 s: the rest at 0.30 would stand above the new
    # wire 0.29 -- replaced at once, `replace_cent`, the fresh rest at 0.29.
    # E31 (FILL lane 31): the wire falls because HIS OWN LEVEL is 0.29 and
    # our rest at 0.30 is a cent PAST HIM. (The other way of falling -- the
    # ask coming down onto a rest that is still at or under his cent -- is
    # exactly what rules.maker_compare_wire refuses to chase: a rest is
    # never re-quoted down with a falling ask, because a falling ask is the
    # rest being filled or a stale read. Past HIM is replaced at once, as
    # it always was.)
    p2 = _pool(fills=[_fill(M, "BUY", 300, 0.29, NOW - 3000)])
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2, wire=0.30, qty=300, placed_ts=NOW - 20)
    v2 = _Venue(bid=0.28, ask=0.32)
    v2.rest("oid-1", "BUY", 0.30, 300)
    st3 = _tick(p2, v2)
    assert [c[1] for c in _cancels(v2)] == ["oid-1"] and [c[2:6] for c in _places(v2)] == [(0.29, 300, False, GTC_TIF)]
    assert _census(st3, "kept_min_life") == 0 and _decisions(p2) == [(o2["id"], "replace_cent")]
    assert b2["last_plan"]["replaced"] == "replace_cent" and "rest_life" not in b2["last_plan"]


def test_e18_the_worker_keeps_a_young_rest_over_a_quantity_move_carries_add_pending_and_regrows_past_the_floor():
    # E31 (FILL lane 31): the book is 0.29 / 0.31 so the maker wire is
    # min(buy_wire(his 0.31), 0.31 - 0.01) = 0.30, the rest's own cent --
    # a QUANTITY-ONLY move, which is this test's subject. (The file's
    # default 0.30 / 0.32 book would price the rest at 0.31 and make it a
    # cent move too, which is a different clause.)
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.30, qty=100, placed_ts=NOW - 20)     # his 300: the plan wants 300
    v = _Venue(bid=0.29, ask=0.31)
    v.rest("oid-1", "BUY", 0.30, 100)
    st = _tick(p, v)
    assert not _cancels(v) and not _places(v) and _census(st, "kept_min_life") == 1
    lp = b["last_plan"]
    assert lp["decision"] == "kept_min_life" and lp["add_pending"] == {"qty": 300, "wire": 0.30, "since": NOW}
    st2 = _tick(p, v, now=NOW + 30)
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and [c[2:6] for c in _places(v)] == [(0.30, 300, False, GTC_TIF)]
    assert _decisions(p) == [(o["id"], "replace_qty")] and _census(st2, "kept_min_life") == 0
    assert "add_pending" not in b["last_plan"]
    # a 2c move on a young rest: replaced at once, `replace_cent`
    # (E31: HIS level is 0.28 and our rest at 0.30 is two cents past him --
    # the direction rules.maker_compare_wire always chases)
    p2 = _pool(fills=[_fill(M, "BUY", 300, 0.28, NOW - 3000)])
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2, wire=0.30, qty=300, placed_ts=NOW - 20)
    v2 = _Venue(bid=0.27, ask=0.32)
    v2.rest("oid-1", "BUY", 0.30, 300)
    st3 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.28, 300, False, GTC_TIF)] and _census(st3, "kept_min_life") == 0
    assert _decisions(p2) == [(o2["id"], "replace_cent")]


def test_e18_the_worker_replaces_a_young_priced_exit_rest_at_once_and_writes_ttl_on_the_ttl_cancel():
    # his 300 @0.31 then a SELL of 200 @0.32: our reduce rests at ceil(0.32) = 0.32;
    # the standing exit rest at 0.31 is 20 s old -- replaced now (E4, no floor)
    p = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 200, 0.32, NOW - 1000)],
              snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300, avg_cost=0.31)
    o = p.add_order(b, side=rules.SELL, wire=0.31, qty=200, kind="reduce", placed_ts=NOW - 20)
    v = _Venue(bid=0.29, ask=0.30, held={SLUG: 300})
    v.rest("oid-1", "SELL", 0.31, 200)
    st = _tick(p, v)
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and [c[2:6] for c in _places(v)] == [(0.32, 200, True, GTC_TIF)]
    assert _census(st, "kept_min_life") == 0 and _decisions(p) == [(o["id"], "replace_cent")]
    ins = _inserts(p)
    assert len(ins) == 1 and ins[0][20] == "exit_rest" and ins[0][19] is None
    # an entry rest past its TTL: step O's cancel writes 'ttl'
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2, wire=0.30, qty=300, placed_ts=NOW - float(rules.MIRROR_REST_TTL_S) - 1)
    v2 = _Venue()
    v2.rest("oid-1", "BUY", 0.30, 300)
    _tick(p2, v2)
    assert p2.orders[o2["id"]]["state"] == "cancelled" and p2.orders[o2["id"]]["reason"] == "ttl"
    assert _decisions(p2) == [(o2["id"], "ttl")] and [c[2:6] for c in _places(v2)] == [(0.31, 300, False, GTC_TIF)]


def _take_world(seq, **kw):
    """His 300 @0.30; the book flat; the tick's quote read at the ask ON
    his cent, the re-read as `seq[1]`. E31: the maker wire is min(0.30,
    0.30 - 0.01) = 0.29 -- AT the touch bound, so `_rest_reread` fires at
    exactly the site the IOC's re-read did, on the same worlds."""
    p = _pool(fills=[_fill(M, "BUY", 300, 0.30, NOW - 3000)])
    b = p.add_book(ledger=0)
    v = _MovingVenue(seq, bid=0.29, ask=0.30, ioc_fill=300.0, **kw)
    return p, b, v


def test_e18_the_touch_bound_rest_is_re_priced_when_the_ask_rose_between_the_read_and_the_send():
    """RE-PINNED at E31 (FILL lane 31, 2026-09-10). E18 pinned here that
    the entry IOC was WITHHELD (`ask_moved`) when the ask left his cent
    between the tick's read and the send, and the rest went out at the
    tick's own wire 0.29. There is no IOC to withhold: the rest at the
    touch bound is RE-PRICED on the fresher quote and goes out. The ask
    ROSE from 0.30 to 0.31, so there is a cent of room toward him that
    was not there at the plan: the rest goes out at HIS OWN cent 0.30 --
    min(buy_wire(0.30), 0.31 - 0.01) -- inside the new spread, still a
    tick under the ask. Old: 0.29 with `ask_moved` 1 and ask_at_send
    NULL. New: 0.30, `ask_moved` 0, ask_at_send 0.31 (the read that
    priced it). The two bbo reads, the one op and the GTC row are E18's,
    unchanged."""
    p, b, v = _take_world([(0.29, 0.30), (0.29, 0.31)])
    st = _tick(p, v)
    assert _bbos(v).count(SLUG) == 2, "the tick's read and the one re-read before the send"
    assert [c[2:6] for c in _places(v)] == [(0.30, 300, False, GTC_TIF)], "no IOC; the rest at his cent"
    assert _census(st, "ask_moved") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    lp = b["last_plan"]
    assert "ask_moved" not in lp and lp["maker"]["clause"] == "his_cent" and lp["maker"]["reread"] is True
    assert lp["rest_quote_at_send"] == {"bid": 0.29, "ask": 0.31, "bid_at_plan": 0.29, "ask_at_plan": 0.30}
    assert b["ledger_net"] == 0 and st["ops"] == 1, "one op: the rest"
    ins = _inserts(p)
    assert len(ins) == 1 and ins[0][5] == "GTC" and ins[0][19] == 0.31 and ins[0][20] == "rest"
    assert ins[0][21] == str(NOW - 3000)
    # the re-read is charged to the tick's call budget like a write
    assert _census(st, "venue_calls") >= 3


def test_e18_the_touch_bound_rest_goes_at_the_tick_cent_when_the_ask_still_stands_and_the_row_records_ask_at_send():
    """RE-PINNED at E31: E18's "the entry IOC GOES at 0.30 when the ask
    still stands" is now "the rest goes at 0.29, a tick under the ask
    that still stands" -- an unchanged re-read recomputes the same wire
    and returns it, so nothing is re-quoted and ask_at_send still records
    the read. The second leg is the mirror of the first test: the ask
    FELL to 0.29, so the bound falls with it and the rest goes out at
    0.28 rather than crossing at 0.30."""
    p, b, v = _take_world([(0.29, 0.30), (0.29, 0.30)])
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.29, 300, False, GTC_TIF)] and _census(st, "take_first") == 0
    assert _census(st, "ask_moved") == 0 and b["ledger_net"] == 0
    ins = _inserts(p)
    assert len(ins) == 1 and ins[0][5] == "GTC" and ins[0][19] == 0.30 and ins[0][20] == "rest"
    assert ins[0][21] == str(NOW - 3000) and b["last_plan"]["decision"] == "rest"
    assert b["last_plan"]["rest_quote_at_send"]["ask"] == 0.30 and "ask_moved" not in b["last_plan"]
    # the ask fell further on the re-read: the rest follows it down a tick, never through it
    p2, b2, v2 = _take_world([(0.29, 0.30), (0.28, 0.29)])
    _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.28, 300, False, GTC_TIF)] and _inserts(p2)[0][19] == 0.29


def test_e18_the_re_read_is_refused_by_the_call_budget_and_the_rest_is_still_placed(monkeypatch):
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 0)
    p, b, v = _take_world([(0.29, 0.30), (0.29, 0.30)])
    st = _tick(p, v)
    assert _bbos(v).count(SLUG) == 1, "no re-read under an exhausted budget"
    assert [c[2:6] for c in _places(v)] == [(0.29, 300, False, GTC_TIF)] and b["ledger_net"] == 0
    assert _census(st, "rest_reread_capped") == 1 and _census(st, "take_placed") == 0
    assert b["last_plan"]["rest_reread_capped"] == {"guard_calls": 0, "budget": 0}
    assert _census(st, "ioc_reread_capped") == 0, "E31: the retired name stays a declared zero"


def test_e18_an_unreadable_re_read_withholds_the_ioc_and_the_rest_is_still_placed():
    # the re-read comes back empty (an OPEN market with no makers, a failed
    # read): no IOC on the tick's stale figure (fails closed), the rest placed
    p2, b2, v2 = _take_world([(0.29, 0.30), (None, None)])
    st2 = _tick(p2, v2)
    assert _bbos(v2).count(SLUG) == 2
    assert [c[2:6] for c in _places(v2)] == [(0.29, 300, False, GTC_TIF)] and _census(st2, "rest_quote_unread") == 1
    assert b2["ledger_net"] == 0 and _census(st2, "take_placed") == 0 and _census(st2, "ask_moved") == 0
    assert _census(st2, "ioc_quote_unread") == 0, "E31: the retired name stays a declared zero"
    assert b2["last_plan"]["rest_quote_at_send"] == {"bid": None, "ask": None, "bid_at_plan": 0.29, "ask_at_plan": 0.30}


def test_e18_an_exit_rest_at_the_touch_is_re_read_whatever_the_budget_and_never_crosses(monkeypatch):
    """His 300 @0.31, his SELL of 200 @0.31. RE-PINNED at E31 (FILL lane
    31, 2026-09-10): E18 pinned here that the exit TAKE at 0.30 (his
    price less E4's tolerance) fired on the tick's bid of 0.30 and was
    WITHHELD by the re-read at 0.28 (`bid_moved`), the 200 resting at his
    cent 0.31 on the same tick. There is no exit take: the exit rests at
    max(sell_wire(0.31), bid 0.30 + 0.01) = 0.31 -- his own cent, one tick
    over the bid, which is the SAME 0.31 rest and the same row E18 pinned.
    Because that cent IS the touch bound, the exit's re-read still fires,
    and still fires with the call budget spent (an exit is never capped,
    the L6 review's M-1). The re-read at 0.28 moves the bound DOWN to 0.29
    and max(0.31, 0.29) is 0.31: nothing to re-quote, the rest goes.
    `bid_moved` and `exit_take_rested` are gone with the take."""
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 0)
    p = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 200, 0.31, NOW - 1000)],
              snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300, avg_cost=0.31)
    v = _MovingVenue([(0.30, 0.32), (0.28, 0.32)], held={SLUG: 300}, ioc_fill=200.0)
    st = _tick(p, v)
    assert _bbos(v).count(SLUG) == 2 and b["ledger_net"] == 300
    assert [c[2:6] for c in _places(v)] == [(0.31, 200, True, GTC_TIF)], "the rest at his cent this tick (E14b)"
    assert _census(st, "bid_moved") == 0 and _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0
    assert "bid_moved" not in b["last_plan"] and b["last_plan"]["maker"]["clause"] == "his_cent"
    assert b["last_plan"]["rest_quote_at_send"] == {"bid": 0.28, "ask": 0.32, "bid_at_plan": 0.30, "ask_at_plan": 0.32}
    assert _inserts(p)[0][20] == "exit_rest"
    # the next tick, the bid still away: the same rest stands at his cent, nothing more sent
    st2 = _tick(p, v, now=NOW + 30)
    assert len(_places(v)) == 1 and _census(st2, "bid_moved") == 0 and not _cancels(v)
    assert _census(st2, "exit_take_rested") == 0 and b["ledger_net"] == 300
    # the bid holding on the re-read: the same exit rest goes, ask_at_send recorded
    p2 = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 200, 0.31, NOW - 1000)],
               snap={M: 100.0, N: 0.0})
    b2 = p2.add_book(ledger=300, avg_cost=0.31)
    v2 = _MovingVenue([(0.30, 0.32), (0.30, 0.33)], held={SLUG: 300}, ioc_fill=200.0)
    st3 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.31, 200, True, GTC_TIF)] and _census(st3, "exit_take") == 0
    assert b2["ledger_net"] == 300 and _inserts(p2)[0][19] == 0.33 and _inserts(p2)[0][20] == "exit_rest"


def test_e18_a_standing_rest_at_the_maker_wire_is_kept_and_never_taken_off():
    """RE-PINNED at E31 (FILL lane 31, 2026-09-10). E18 pinned here the
    TAKE OFF A STANDING REST: a rest at 0.29 with the ask arriving at his
    cent 0.30 was cancelled, the take sent, the re-read found the ask gone
    and the rest went back. E31 retired that arm with every other take
    (_act's keep branch no longer cancels a rest to cross), so the rest --
    which IS the maker wire, 0.29 -- is simply KEPT: no cancel, no send,
    no re-read (the re-read happens at the SEND and there is none), and
    the ledger unmoved. The book's own quote is the same world; the venue
    is asked for nothing beyond the tick's read."""
    p, b, v = _take_world([(0.29, 0.30), (0.29, 0.31)])
    o = p.add_order(b, wire=0.29, qty=300, placed_ts=NOW - 100)
    v.rest("oid-1", "BUY", 0.29, 300)
    st = _tick(p, v)
    assert not _cancels(v) and p.orders[o["id"]]["state"] == "open"
    assert not _places(v) and _census(st, "ask_moved") == 0 and _bbos(v).count(SLUG) == 1
    assert _census(st, "take_placed") == 0 and b["ledger_net"] == 0
    assert _census(st, "open_order_pending") == 1


# ---------------------------------------------- the column, the migration

def test_e18_059_absent_sends_the_050_insert_writes_no_decision_says_so_once_and_the_fast_tick_agrees(monkeypatch, caplog):
    monkeypatch.setattr(ml, "_order_cols_absent_logged", False)
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.30, qty=300, placed_ts=NOW - 100)      # a replace: the decision write's site
    p.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v = _Venue(bid=0.29, ask=0.32)
    v.rest("oid-1", "BUY", 0.30, 300)
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert st["order_cols_absent"] == "UndefinedColumnError" and st["status"] == "ok"
    assert _census(st, "order_cols_guard_unreadable") == 0
    assert p.orders[o["id"]]["state"] == "cancelled" and [c[2:6] for c in _places(v)] == [(0.31, 300, False, GTC_TIF)]
    ins = _inserts(p)
    assert len(ins) == 1 and len(ins[0]) == 19 and ins[0][18] == "ORDER_INTENT_BUY_LONG"
    assert _decisions(p) == [] and b["last_plan"]["decision"] == "rest"
    assert all("ask_at_send" not in s for k, s, a in p.sent if "ml-order-cols-guard" not in s)
    assert len([r for r in caplog.records if "migration 059" in r.getMessage()]) == 1
    st2 = _tick(p, v, now=NOW + 30)
    assert st2["order_cols_absent"] == "UndefinedColumnError"
    assert len([r for r in caplog.records if "migration 059" in r.getMessage()]) == 1
    _walk()
    fs = _fast(p, v, now=NOW + 40)
    assert fs["order_cols_absent"] == "UndefinedColumnError" and _skips(fs) == {CID: "order_open"}
    # 050 absent: the 047 INSERT, and the 059 probe is never asked
    p3 = _pool()
    p3.add_book(ledger=0)
    p3.no_intent_column = True
    st3 = _tick(p3, _Venue())
    assert len(_inserts(p3)[0]) == 18 and "ml-order-cols-guard" not in " ".join(s for k, s, a in p3.sent)
    assert "order_cols_absent" not in st3


def test_e18_the_059_probe_failing_for_any_other_reason_refuses_the_tick_by_name():
    p = _pool()
    p.add_book(ledger=0)
    p.raise_on.append(("ml-order-cols-guard", RuntimeError("db")))
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "order_cols_guard_unreadable") == 1 and st["status"] == "degraded"
    assert st["order_cols_guard_unreadable"] == "RuntimeError" and not _places(v)
    assert "ml-books-open" not in " ".join(s for k, s, a in p.sent), "refused before any book read"
    _walk()
    ml._MIRROR_CENSUS.clear()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "order_cols_guard_unreadable"} and not _places(v)
    # the probes' order: the 050 probe, the 057/058 probes, then this one -- in both ticks
    src = inspect.getsource(ml._tick)
    assert src.index("_SQL_INTENT_GUARD") < src.index("_flow_guard(t, stats)") < src.index("_order_cols_guard(t, stats)")
    fsrc = inspect.getsource(ml._fast_tick)
    assert fsrc.index("_flow_guard(t, stats)") < fsrc.index("_order_cols_guard(t, stats)") < fsrc.index("_sql_books_open(t)")


def test_e18_059_exists_sorts_last_and_is_three_nullable_add_column_if_not_exists_with_no_default():
    assert SQL_059.exists()
    files = [x.name for x in sorted(MIG_DIR.glob("*.sql"))]
    i = files.index("058_mirror_books_flow_clock.sql")
    # T2 (FILL lane 4, 2026-09-08) added 060 after this one, FILL lane 9 (2026-09-09) 061 after that:
    # 059 sorts after 058, 060 after 059, 061 last
    assert files[i + 1] == "059_mirror_orders_send_record.sql" and files[i + 2] == "060_mirror_fill_answers.sql"
    assert files[-1] == "064_run833_stream_channels.sql"  # re-pinned 2026-09-12 (run 83.3): run 83.3's 064 is the newest; this lane still adds none
    assert sum(f.startswith("059_") for f in files) == 1
    sql = SQL_059.read_text()
    assert sql.splitlines()[0].startswith("-- 059: MIRROR ORDERS SEND RECORD (E18, 2026-09-08")
    body = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    stmts = [" ".join(s.split()) for s in body.split(";") if s.strip()]
    assert stmts == [
        "ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS ask_at_send DOUBLE PRECISION NULL",
        "ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS decision TEXT NULL",
        "ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS his_fill_id TEXT NULL",
    ]
    up = " ".join(stmts).upper()
    assert "DEFAULT" not in up and "NOT NULL" not in up and "CREATE " not in up and "DROP " not in up
    assert "mirror_books" not in " ".join(stmts) and "live_orders" not in " ".join(stmts)
    start = (ROOT / "backend" / "start.sh").read_text()
    assert "python -m sportsassets.scripts.migrate" in start
    # the worker's shapes: the 059 INSERT names the three past the 050 shape, the guard, the write
    assert ml._SQL_ORDER_INSERT_059.count("$") == 22 and "ask_at_send, decision, his_fill_id" in ml._SQL_ORDER_INSERT_059
    assert "$20, $21, $22" in ml._SQL_ORDER_INSERT_059 and "ml-order-insert" in ml._SQL_ORDER_INSERT_059
    assert "ask_at_send" not in ml._SQL_ORDER_INSERT and "$19" in ml._SQL_ORDER_INSERT
    assert ml._SQL_ORDER_COLS_GUARD == ("SELECT ask_at_send, decision, his_fill_id FROM mirror_orders LIMIT 0 "
                                        "/* ml-order-cols-guard */")
    assert "SET decision = $2" in ml._SQL_ORDER_DECISION and "ml-order-decision" in ml._SQL_ORDER_DECISION
    # no new order kind: the 047 CHECK stands
    sql_047 = (MIG_DIR / "047_mirror_live.sql").read_text()
    assert "kind IN ('increase','reduce','flatten_paired','flatten_vanished','take','adjust')" in sql_047


def test_e18_059_parses_as_postgres_sql():
    pglast = pytest.importorskip("pglast")
    from pglast.enums import AlterTableType, ConstrType
    stmts = pglast.parse_sql(SQL_059.read_text())
    assert len(stmts) == 3 and all(type(s.stmt).__name__ == "AlterTableStmt" for s in stmts)
    seen = []
    for s in stmts:
        n = s.stmt
        assert n.relation.relname == "mirror_orders" and len(n.cmds) == 1
        cmd = n.cmds[0]
        assert cmd.subtype == AlterTableType.AT_AddColumn and cmd.missing_ok is True
        col = cmd.def_
        types = {c.contype for c in (col.constraints or [])}
        assert ConstrType.CONSTR_NULL in types and ConstrType.CONSTR_NOTNULL not in types
        assert ConstrType.CONSTR_DEFAULT not in types
        seen.append((col.colname, [x.sval for x in col.typeName.names][-1]))
    assert seen == [("ask_at_send", "float8"), ("decision", "text"), ("his_fill_id", "text")]


async def _scratch():
    asyncpg = pytest.importorskip("asyncpg")
    try:
        admin = await asyncpg.connect(DSN_BASE, timeout=4)
    except Exception:  # noqa: BLE001 — no local PG: skip, never fake
        pytest.skip("no local postgres for the E18 migration pin")
    name = "e18_send_" + uuid.uuid4().hex[:10]
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
    return admin, conn, name


async def _drop(admin, conn, name):
    await conn.close()
    await admin.execute(f'DROP DATABASE "{name}"')
    await admin.close()


def test_e18_059_applies_on_a_real_postgres_twice_and_the_worker_statements_run_against_it():
    """047's table (a live_orders stub for its FK), 050 (the intent
    column the 059 INSERT carries), then 059 twice; the columns nullable
    with no default; the guard's error before 059 is the absence
    rules.column_missing reads; after it the 059 INSERT, the decision
    UPDATE and the 050 INSERT all run and read back."""
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await conn.execute("CREATE TABLE live_orders (id bigserial PRIMARY KEY, us_market_slug text, lane text)")
            await conn.execute((MIG_DIR / "047_mirror_live.sql").read_text())
            await conn.execute((MIG_DIR / "050_mirror_shorts.sql").read_text())
            await conn.execute("INSERT INTO mirror_books (whale, condition_id, us_market_slug, long_asset, "
                               "other_asset, map_source, ratio, anchor_usd, state) "
                               "VALUES ('rn1', $1, $2, $3, $4, 'ledger', 0.10, 50.0, 'live')", CID, SLUG, M, N)
            book_id = await conn.fetchval("SELECT id FROM mirror_books LIMIT 1")
            try:
                await conn.fetch(ml._SQL_ORDER_COLS_GUARD)
            except Exception as exc:  # noqa: BLE001 — the absence, as Postgres answers it
                assert rules.column_missing(exc, "ask_at_send"), exc
                assert type(exc).__name__ == "UndefinedColumnError"
            else:
                raise AssertionError("the guard read a column 047/050 never declared")
            await conn.execute(SQL_059.read_text())
            await conn.execute(SQL_059.read_text())          # idempotent
            cols = await conn.fetch(
                "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'mirror_orders' AND column_name IN ('ask_at_send', 'decision', 'his_fill_id') "
                "ORDER BY column_name")
            assert [tuple(c) for c in cols] == [("ask_at_send", "double precision", "YES", None),
                                                ("decision", "text", "YES", None),
                                                ("his_fill_id", "text", "YES", None)]
            assert await conn.fetch(ml._SQL_ORDER_COLS_GUARD) == []
            args = [book_id, "rn1", SLUG, "take", rules.BUY, "IOC", False, None, 0.30, 0.30, 0.30, 300, "[]",
                    300, 0, 0.29, 0.30, "take"]
            rid = await conn.fetchval(ml._SQL_ORDER_INSERT_059, *args, "ORDER_INTENT_BUY_LONG", 0.30, "take",
                                      "1757000000.0")
            row = await conn.fetchrow("SELECT ask_at_send, decision, his_fill_id, intent FROM mirror_orders WHERE id = $1", rid)
            assert tuple(row) == (0.30, "take", "1757000000.0", "ORDER_INTENT_BUY_LONG")
            await conn.execute("UPDATE mirror_orders SET state = 'cancelled' WHERE id = $1", rid)
            await conn.execute(ml._SQL_ORDER_DECISION, rid, "replace_cent")
            assert await conn.fetchval("SELECT decision FROM mirror_orders WHERE id = $1", rid) == "replace_cent"
            old = await conn.fetchval(ml._SQL_ORDER_INSERT, *args, "ORDER_INTENT_BUY_LONG")
            got = await conn.fetchrow("SELECT ask_at_send, decision, his_fill_id FROM mirror_orders WHERE id = $1", old)
            assert tuple(got) == (None, None, None), "NULL = nothing: no reader acts on these"
        finally:
            await _drop(admin, conn, name)
    _run(_go())


# ------------------------------------------------------ the names, the docs

def test_e18_the_census_names_sit_before_registered_no_increase_and_the_pins_hold():
    keys = ml.CENSUS_KEYS
    for k in NEW_NAMES:
        assert k in keys and keys.index(k) < keys.index("registered_no_increase"), k
    assert keys.index("venue_market_ended") < keys.index("kept_min_life")
    assert keys[-12] == "registered_no_increase" and keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert all(ml._new_stats()["census"][k] == 0 for k in NEW_NAMES)
    # RE-PINNED at E31: `IOC_SKIPPED` named the two withholdings and the two unread/capped
    # names; the take is gone, so the tuple that survives is the re-read's own two
    assert ml.REST_REREAD_SKIPPED == ("rest_reread_capped", "rest_quote_unread")
    assert not hasattr(ml, "IOC_SKIPPED")
    for k in RETIRED:                       # the four retired names stay DECLARED, at zero
        assert k in keys and ml._new_stats()["census"][k] == 0, k
    # no env knob of the lane's own beyond the floor: the cent move is a constant
    src = inspect.getsource(ml)
    assert '"MIRROR_REST_MIN_LIFE_S"' not in src and 'capped_env("MIRROR_IOC' not in src, "the worker reads no env of its own"
    psrc = inspect.getsource(ml._place_reserved)
    assert "_rest_reread(t, book, r, side, float(wire), action," in psrc and "_ioc_reread" not in psrc
    # the re-read (an await) sits BEFORE the room's read, so E2's "nothing
    # between this read and the take" holds (the review's HIGH-1, folded
    # 2026-09-08: the pin once read against _room_take alone)
    assert psrc.index("wire = await _rest_reread(") < psrc.index("qty = _room_qty(") < psrc.index("_room_take(t, est)"), \
        "refused before the room's read"


def test_e18_every_name_is_emitted_here(monkeypatch, caplog):
    """The lane's six names, each driven once (the worker file's coverage
    read imports this)."""
    test_e18_the_worker_keeps_a_young_entry_rest_over_a_one_cent_move_and_replaces_it_past_the_floor()
    test_e18_the_touch_bound_rest_is_re_priced_when_the_ask_rose_between_the_read_and_the_send()
    test_e18_an_unreadable_re_read_withholds_the_ioc_and_the_rest_is_still_placed()
    test_e18_the_re_read_is_refused_by_the_call_budget_and_the_rest_is_still_placed(monkeypatch)
    test_e18_an_exit_rest_at_the_touch_is_re_read_whatever_the_budget_and_never_crosses(monkeypatch)
    test_e18_the_059_probe_failing_for_any_other_reason_refuses_the_tick_by_name()


def test_e18_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    # the section number is assigned at landing (lane 1 took 35 on the tip; E18 landed as 36)
    assert re.search(r"^## \d+\. E18 \(2026-09-08\)", doc, re.M), "the E18 section header"
    for k in NEW_NAMES + ("MIRROR_REST_MIN_LIFE_S", "add_pending", "059", "ask_at_send", "his_fill_id",
                          "replace_cent", "replace_qty", "test_e18_rest_life.py"):
        assert k in doc, k
