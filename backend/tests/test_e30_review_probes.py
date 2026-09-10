"""E30 (FILL lane 30) -- the adversarial review's probes (FILL_L30_review.md).

Each probe pins an invariant of docs 73 that the lane's own file
(tests/test_e30_post_only_body.py) does not drive, found by mutation:

  1. the WARNING is once per process per (book, STATUS CODE) -- a second
     code on the same book logs once more, the first code never again
     (mutant M21: the key without the code survived the lane's tests);
  2. a REDUCE's GTC rest -- the exit rest at his cent on a long book, and
     E5's frozen reduce rest -- goes out on a book whose ADD streak is
     three deep (mutant M08: a guard reading the live streak for any GTC
     survived the lane's tests, whose reduce and flip covers are IOCs);
  3. the ':cross' word rides on the bool True alone, as rules.take_arms
     reads the field (mutant M27: a truthy 1 / "true" survived);
  4. a `hold` carried on the PRIOR plan is never a hold this tick -- the
     hold is judged every tick from the streak, never remembered (a
     guard against any future carry of the plan's `hold`);
  5. (LOW-3's hardening, passes only with its delta) a status code the
     adapter never sends -- unhashable -- must not raise out of the
     placement after the row was written 'rejected': the record never
     refuses the tick.

The world: the lane's own 1383 fixtures, e14b's exit world, e5's frozen
long. The autouse rails of the worker suite ride along.
"""
from __future__ import annotations

import logging

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e14b_exit_take_rests import _exit_world
from tests.test_e18_rest_life import _MovingVenue
from tests.test_e30_post_only_body import (
    ASK, BID, QTY, RAW_400, TARGET, WAIT, WIRE, _b1383, _http_1383, _p1383, _reject, _rows, _seed, _streak, _v, _warns,
)
from tests.test_e5_frozen_exits import _frozen_long, _pool as _pool5
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails ride along
    BUY, GTC_TIF, IOC_TIF, NOW, SELL, SLUG, _Venue, _armed, _census, _gone, _his, _mkt, _places, _shorts_on, _tick,
)

RAW_403 = {"status_code": 403, "error": "forbidden", "error_type": "PermissionDeniedError",
           "body": {"message": "forbidden"}}


# ------------------------------------------------------ (1) the WARNING's key


def test_e30r_the_warning_is_once_per_book_and_per_status_code(monkeypatch, caplog):
    """400, then 403, then 400 on ONE book at one side and wire: two
    WARNINGs (one per code), the third rejection logging nothing. The
    count stays under three whichever identity the streak keys on (the
    codes differ), so no tick is held and every tick places its rest."""
    caplog.set_level(logging.WARNING, logger="sportsassets.workers.mirror_live")
    _shorts_on(monkeypatch)
    p = _p1383()
    b = _b1383(p)
    for i, (raw, want) in enumerate(((RAW_400, 1), (RAW_403, 2), (RAW_400, 2))):
        v = _v(place=_reject(raw=raw))
        st = _tick(p, v, now=NOW + 30 * i, http=_http_1383())
        assert _census(st, "post_only_rejected") == 1 and _census(st, "post_only_backoff") == 0
        assert [c[1:6] for c in _places(v)] == [(SLUG, WIRE, QTY, False, GTC_TIF)]
        assert len(_warns(caplog)) == want, (i, raw["status_code"])
    msgs = [r.getMessage() for r in _warns(caplog)]
    assert "status 400" in msgs[0] and "status 403" in msgs[1] and f"book {b['id']}" in msgs[1]
    assert [o["reason"] for o in _rows(p)] == ["post_only_rejected:400", "post_only_rejected:403", "post_only_rejected:400"]
    assert all(o["receipt"]["status_code"] == o_code for o, o_code in zip(_rows(p), (400, 403, 400)))


# ------------------------------------------------------ (2) a reduce's GTC rest under the streak


def test_e30r_the_exit_rest_at_his_cent_goes_out_on_a_long_book_whose_add_streak_is_three_deep():
    """e14b's book 334 shape (his 0.549: take 0.54, rest 0.55; the IOC
    withheld `bid_moved` -> the 358 REST at 0.55, a SELL GTC) with the
    book's ADD streak seeded three deep five seconds ago (side BUY, the
    long's add side): the exit rest goes out exactly as e14b pins it --
    a reduce is never judged, never held; post_only_backoff 0."""
    p, b = _exit_world(0.549, 358)
    _seed(b, side=BUY, wire=0.60, ledger=716.0)
    v = _MovingVenue([(0.54, 0.56), (0.53, 0.56)], held={SLUG: 716}, ioc_fill=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)], "the exit rest at his cent, a GTC"
    assert _census(st, "rest_placed") == 1 and _census(st, "exit_take_rested") == 1 and _census(st, "bid_moved") == 1
    assert _census(st, "post_only_backoff") == 0 and "hold" not in b["last_plan"]
    assert b["last_reason"] != "post_only_backoff" and b["open_order_id"] is not None and b["ledger_net"] == 716


def test_e30r_the_frozen_reduce_rest_goes_out_on_a_frozen_book_whose_add_streak_is_three_deep():
    """e5's frozen long (ledger 300, venue 600, he sold out; the bid
    outside the cent -> the venue-sized reduce RESTS at his cent 0.31,
    a SELL GTC with reason frozen_reduce) with the ADD streak seeded
    three deep: the frozen exit's rest goes out as e5 pins it."""
    p = _pool5(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    _seed(b, side=BUY, wire=0.30, ledger=300.0)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    st = _tick(p, v, http=_gone())
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.31, 600, True, GTC_TIF, "ORDER_INTENT_BUY_LONG", True, None)]
    assert _census(st, "frozen_reduce") == 1 and _census(st, "rest_placed") == 1
    assert _census(st, "post_only_backoff") == 0 and b["state"] == "frozen"


# ------------------------------------------------------ (3) the word's spelling


def test_e30r_the_cross_word_rides_on_the_bool_true_alone():
    """rules.take_arms reads `post_only_cross is True`; the word must
    read the same field the same way -- a truthy 1 / "true" / [True] is
    not the crossing shape on either code."""
    for v in (1, "true", "True", "yes", [True], 1.0):
        assert ml._post_only_word({"status_code": 400, "post_only_cross": v}) == "", v
        assert ml._post_only_word({"status_code": 200, "post_only_cross": v,
                                   "execution_type": "EXECUTION_TYPE_REJECTED"}) == "", v
    assert ml._post_only_word({"status_code": 400, "post_only_cross": True}) == ":cross"
    assert ml._post_only_word({"status_code": 200, "post_only_cross": True,
                               "execution_type": "EXECUTION_TYPE_REJECTED"}) == ":cross"


# ------------------------------------------------------ (4) the hold is judged, never remembered


def test_e30r_a_hold_carried_on_the_prior_plan_is_not_a_hold_this_tick(monkeypatch):
    """The row's last_plan reads `hold: post_only_backoff` (a held plan
    written before a restart; the process's streak is empty): the tick
    judges the streak afresh -- no hold, the rest placed and accepted,
    the new plan carrying no `hold`."""
    _shorts_on(monkeypatch)
    p = _p1383()
    held_plan = {"hold": "post_only_backoff", "kind": "increase", "target": TARGET, "at": NOW - 30.0,
                 "post_only_backoff": {"since": NOW - 30.0, "until": NOW + WAIT, "n": 3, "code": 400}}
    b = _b1383(p, last_plan=held_plan)
    assert _streak(b) is None
    v = _Venue(bid=BID, ask=ASK)
    st = _tick(p, v, http=_http_1383())
    assert [c[1:6] for c in _places(v)] == [(SLUG, WIRE, QTY, False, GTC_TIF)]
    assert _census(st, "post_only_backoff") == 0 and _census(st, "rest_placed") == 1
    assert "hold" not in b["last_plan"] and "post_only_backoff" not in b["last_plan"]
    assert b["open_order_id"] is not None and b["last_reason"] != "post_only_backoff"


# ------------------------------------------------------ (5) LOW-3: the record never refuses the tick


def test_e30r_an_unhashable_status_code_never_raises_out_of_the_placement(monkeypatch, caplog):
    """A status code the adapter never sends (a list): the row is written
    'rejected' with its reason, the receipt carries it, the count
    advances, and the placement RETURNS -- the once-per-process log key
    must not raise TypeError on the set. Passes with LOW-3's delta
    (the log key reads a non-int, non-str code by its repr)."""
    caplog.set_level(logging.WARNING, logger="sportsassets.workers.mirror_live")
    _shorts_on(monkeypatch)
    p = _p1383()
    b = _b1383(p)
    raw = dict(RAW_400, status_code=[400])
    v = _v(place=_reject(raw=raw))
    st = _tick(p, v, now=NOW, http=_http_1383())
    assert _census(st, "post_only_rejected") == 1 and _census(st, "books_error") == 0
    assert [o["reason"] for o in _rows(p)] == ["post_only_rejected:[400]"]
    assert _streak(b)["n"] == 1 and _streak(b)["code"] == [400] and len(_warns(caplog)) == 1
    assert b["last_reason"] == "post_only_rejected"
