"""E20 (2026-09-08): the desk-wide wrong-sign trip is the GENUINE
inversion's alone; any other sign disagreement freezes the one book.

The incident (hard2/book_663_2132.txt, mirror-state 21:31Z): book 663,
a short of 1,011 shares the fills reconcile to the share (3,351.1 sold
across 12 fills, 2,340 covered across 4), read +69 from the venue's
positions walk from 20:44Z through 21:08Z -- exactly the size of the
last 69-share cover at 20:37:44Z. The shadow had judged the book
`frozen: venue and ledger disagree` from 20:44:58Z (its rows); the
live row's frozen_reason is `wrong_sign_trip`, which the freeze
statement writes only on a book not yet frozen, so the live book was
live at 20:57:19Z, when the one-read wrong-sign branch wrote
`mirror_live` false with the receipt {venue 69, ledger -1012}: the
desk placed nothing from the 20:53:48Z order row on -- 34 minutes and
counting at the 21:31Z mirror-state (`mode_db_off` 5, `books_live` 4
on the 21:27:41Z heartbeat; 123 fills of his ingested in the 21Z hour,
6 on our markets, 0 answered within 30 min: hourly_2131.txt). The
reading could not be the inversion the branch guards against (our leg
booked as the other side), because the magnitude was not the leg's.

THE RULE. A venue read of the other sign whose magnitude is within
VENUE_LEDGER_TOL_SHARES of the leg's is the genuine inversion: the
desk trips off on ONE read exactly as before (the receipt, the freeze
under `wrong_sign_trip`, the short proof's negative verdict on a short
book). Any other sign disagreement freezes THIS book under
`wrong_sign_hold` -- its open orders cancelled, the plan carrying the
reading, the E13 may-hold list naming it, NO frozen exit (E5's
FROZEN_EXIT_REASONS names placement_lost and venue_ledger_disagree
alone: `reason_not_eligible`, as under `wrong_sign_trip`), the D2
two-reads thaw beside `venue_ledger_disagree` -- and the desk keeps
trading. Fail closed on the book, never on the desk for a reading
that is not the inversion.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from sportsassets.workers import mirror_live as ml
from sportsassets.analytics import mirror as mi
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    _LIVE, _OTHER, _ZZ, NOW, SLUG, _Venue, _armed, _cancels, _census, _places, _pool, _short_book, _shorts_on, _tick,
)


def _hold(b):
    h = (b.get("last_plan") or {}).get("wrong_sign_hold")
    return h if isinstance(h, dict) else None


# ------------------------------------------------ the hold (the desk keeps trading)

def test_e20_a_wrong_sign_read_that_is_not_the_leg_freezes_the_book_and_never_trips_the_desk():
    """ledger 10, the walk reads -5: the book freezes `wrong_sign_hold`,
    its resting order is cancelled, nothing is placed on it, the
    `mirror_live` switch is untouched and no trip receipt is written."""
    p = _pool()
    b = p.add_book(ledger=10)
    p.add_order(b)
    v = _Venue(held={SLUG: -5})
    v.rest("oid-1")
    st = _tick(p, v)
    assert p.state.get("mirror_live") is not False and "mirror_live_trip" not in p.state
    assert b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_hold"
    assert _cancels(v) and not _places(v)
    assert _census(st, "wrong_sign_hold") == 1 and _census(st, "wrong_sign_trip") == 0
    assert _census(st, "venue_ledger_suspect") == 0 and _census(st, "venue_ledger_disagree") == 0
    h = _hold(b)
    assert h is not None and {k: h[k] for k in ("venue", "ledger", "manual", "registered")} == {
        "venue": -5, "ledger": 10, "manual": 0.0, "registered": 0.0}
    assert isinstance(h["at"], float)
    assert st["frozen_reasons"] == {"wrong_sign_hold": 1}


def test_e20_book_663s_shape_a_short_of_1011_read_as_69_long_holds_the_book_and_no_short_proof(monkeypatch):
    """The incident's shape: a SHORT of 1,011 (avg 0.6523), the venue's
    walk reads +69. The book freezes `wrong_sign_hold`; the desk is not
    tripped; the short proof's negative verdict (the genuine inversion's
    tally) is NOT recorded, because the magnitude is not the leg's."""
    _shorts_on(monkeypatch)
    p = _pool()
    b = _short_book(p, ledger=-1011, avg=0.6523)
    v = _Venue(held={SLUG: 69})
    st = _tick(p, v)
    assert p.state.get("mirror_live") is not False and "mirror_live_trip" not in p.state
    assert b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_hold"
    assert _census(st, "wrong_sign_hold") == 1 and _census(st, "wrong_sign_trip") == 0
    assert (b["last_plan"] or {}).get("short_proof") != "mismatch"
    assert not _places(v)
    h = _hold(b)
    assert h is not None and h["venue"] == 69 and h["ledger"] == -1011


def test_e20_the_desk_keeps_trading_after_a_hold_on_one_book():
    """The mid-tick shape of the trip pin (test_mirror_live_worker):
    a hold on the first book, a candidate with a target on the fixture
    market. Nothing on the held book; the desk's other work goes on --
    the candidate opens and places, the other book's rest is kept."""
    p = _pool()
    a = p.add_book(ledger=10, updated_ts=NOW - 100, **_ZZ)
    b = p.add_book(ledger=0, updated_ts=NOW - 50, **_OTHER)
    p.markets["0xzz"] = dict(_LIVE)
    p.markets["0xother"] = dict(_LIVE)
    p.add_order(b, order_id="oid-b", us_market_slug=_OTHER["us_market_slug"])
    v = _Venue(held={_ZZ["us_market_slug"]: -5})
    v.rest("oid-b", slug=_OTHER["us_market_slug"])
    st = _tick(p, v)
    assert p.state.get("mirror_live") is not False and "mirror_live_trip" not in p.state
    assert a["state"] == "frozen" and a["frozen_reason"] == "wrong_sign_hold"
    assert _census(st, "wrong_sign_hold") == 1 and _census(st, "wrong_sign_trip") == 0
    # the other book's rest is never cancelled under the hold's name (a
    # re-quote of its own is the tick's ordinary work, not the hold's)
    assert not [r for r in st["recent"] if r.get("book") == b["id"] and r.get("reason") == "wrong_sign_hold"]
    assert all(r.get("reason") != "wrong_sign_hold" for r in st["recent"] if r.get("book") != a["id"])
    assert len(p.books) == 3, "the candidate did not open after a hold"
    assert _places(v), "nothing placed on the desk after a hold on one book"


def test_e20_a_hold_is_a_freeze_like_any_other_re_freeze_counts_nothing():
    """A book already frozen `wrong_sign_hold` re-freezes under the
    same name on the next disagreeing read and emits nothing (V3-2),
    the desk still untouched."""
    p = _pool()
    b = p.add_book(ledger=10)
    v = _Venue(held={SLUG: -5})
    st1 = _tick(p, v)
    assert b["frozen_reason"] == "wrong_sign_hold" and _census(st1, "wrong_sign_hold") == 1
    st2 = _tick(p, _Venue(held={SLUG: -5}), now=NOW + 60)
    assert b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_hold"
    assert _census(st2, "wrong_sign_hold") == 0 and _census(st2, "wrong_sign_trip") == 0
    assert "mirror_live_trip" not in p.state


def test_e20_a_hold_thaws_under_the_two_reads_rule_never_on_one_read():
    """Review HIGH-1. ledger 10 read -5: the hold. The next FRESH read
    agrees at 10: `thaw_held: one_read`, still frozen, nothing placed.
    The read after agrees again: the D2 thaw (`ml-book-thaw-agrees`,
    `thawed_venue_agrees`), the book plans live that tick. A hold is
    the same walk-instability class as `venue_ledger_disagree`; a
    one-read thaw after a one-read hold placed an increase on the
    first agreeing read of a flickering walk."""
    p = _pool()
    b = p.add_book(ledger=10)
    _tick(p, _Venue(held={SLUG: -5}))
    assert b["frozen_reason"] == "wrong_sign_hold"
    v2 = _Venue(held={SLUG: 10})
    st2 = _tick(p, v2, now=NOW + 60)
    assert b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_hold"
    assert b["last_plan"]["thaw_held"] == "one_read" and b["last_plan"]["venue_agrees"]["reads"] == 1
    assert _census(st2, "thaw_held") == 1 and not _places(v2)
    assert not any("ml-book-thaw" in s for _k, s, _a in p.sent)
    v3 = _Venue(held={SLUG: 10})
    st3 = _tick(p, v3, now=NOW + 120)
    assert b["state"] == "live" and b["frozen_reason"] is None and b["last_plan"]["thawed_venue_agrees"] is True
    assert any("ml-book-thaw-agrees" in s for _k, s, _a in p.sent) and _census(st3, "thaw_held") == 0
    assert "wrong_sign_hold" in ml._TWO_READS_THAW_REASONS and "venue_ledger_disagree" in ml._TWO_READS_THAW_REASONS


def test_e20_a_held_book_never_follows_his_exit_reason_not_eligible():
    """Review item 4. E5's first shape (ledger 300 long, he cut 300 ->
    100) on a book frozen `wrong_sign_hold`: the venue read at -600
    (the other sign) is the hold arm, no exit; read at +600 (the same
    sign, disagreeing) the E5 exit is reached and refuses
    `reason_not_eligible` -- FROZEN_EXIT_REASONS names placement_lost
    and venue_ledger_disagree alone. Nothing is placed on either read."""
    from tests.test_e5_frozen_exits import _pool as _e5_pool
    from tests.test_mirror_live_worker import _his, _mkt
    p = _e5_pool(fills=_his(300, sold=200), snap=None)
    b = p.add_book(ledger=300, avg_cost=0.31, state="frozen", frozen_reason="wrong_sign_hold", frozen_ts=NOW - 100)
    v = _Venue(held={SLUG: -600}, bid=0.30, ask=0.32, ioc_fill=500)
    _tick(p, v, http=_mkt(100))
    assert not _places(v) and b["state"] == "frozen" and (b["last_plan"] or {}).get("frozen_exit") is None
    v2 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=500)
    _tick(p, v2, now=NOW + 60, http=_mkt(100))
    assert not _places(v2) and b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_hold"
    assert b["last_plan"]["frozen_exit"] == {"held": "reason_not_eligible", "reason": "wrong_sign_hold"}
    assert "wrong_sign_hold" not in ml.FROZEN_EXIT_REASONS


def test_e20_a_book_frozen_under_another_name_counts_the_hold_once():
    """Review MEDIUM-1 (663's own shape as the docs tell it: frozen
    `venue_ledger_disagree` first). ledger 10 read 14 twice (E16's
    freeze), then -5 twice: the hold is counted and listed ONCE (the
    transition), the second read moves the gauge and the tick count
    only; the first reason sticks on the row (V3-2)."""
    p = _pool()
    b = p.add_book(ledger=10)
    _tick(p, _Venue(held={SLUG: 14}))
    _tick(p, _Venue(held={SLUG: 14}), now=NOW + 60)
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    n0 = len([x for x in ml._RECENT if x["what"] == "frozen"])
    ticks0 = int(b.get("frozen_ticks") or 0)
    st3 = _tick(p, _Venue(held={SLUG: -5}), now=NOW + 120)
    st4 = _tick(p, _Venue(held={SLUG: -5}), now=NOW + 180)
    # (the row's last_reason reads the frozen reason again once the plan is
    # written; the hold's own reading rides on the plan)
    assert b["frozen_reason"] == "venue_ledger_disagree" and _hold(b) is not None
    assert _census(st3, "wrong_sign_hold") == 1 and _census(st4, "wrong_sign_hold") == 0
    assert len([x for x in ml._RECENT if x["what"] == "frozen"]) - n0 == 1
    assert st3["frozen_reasons"] == {"wrong_sign_hold": 1} == st4["frozen_reasons"]
    assert int(b["frozen_ticks"]) == ticks0 + 2 and "mirror_live_trip" not in p.state


# ------------------------------------------------ the genuine inversion (byte for byte)

@pytest.mark.parametrize("venue", [-10, -11, -9])
def test_e20_the_genuine_inversion_trips_the_desk_on_one_read_at_the_tolerance(venue):
    """ledger 10 read as -10, -11 or -9 (the leg's magnitude within
    VENUE_LEDGER_TOL_SHARES = 1.0): the trip, the receipt, the freeze
    under `wrong_sign_trip`, exactly as before E20."""
    assert mi.VENUE_LEDGER_TOL_SHARES == 1.0
    p = _pool()
    b = p.add_book(ledger=10)
    p.add_order(b)
    v = _Venue(held={SLUG: venue})
    v.rest("oid-1")
    st = _tick(p, v)
    assert p.state["mirror_live"] is False and p.state["mirror_live_trip"]["why"] == "wrong_sign_trip"
    assert p.state["mirror_live_trip"]["venue"] == venue and p.state["mirror_live_trip"]["ledger"] == 10
    assert b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_trip"
    assert _cancels(v) and not _places(v)
    assert _census(st, "wrong_sign_trip") == 1 and _census(st, "wrong_sign_hold") == 0
    assert _hold(b) is None


def test_e20_one_share_past_the_tolerance_is_a_hold_not_a_trip():
    """ledger 10 read as -12: past the tolerance, the hold."""
    p = _pool()
    b = p.add_book(ledger=10)
    v = _Venue(held={SLUG: -12})
    st = _tick(p, v)
    assert "mirror_live_trip" not in p.state and b["frozen_reason"] == "wrong_sign_hold"
    assert _census(st, "wrong_sign_hold") == 1 and _census(st, "wrong_sign_trip") == 0


def test_e20_a_short_read_as_its_own_leg_long_is_the_inversion_with_the_short_proofs_verdict(monkeypatch):
    """A SHORT of 300 read as +300: the genuine inversion -- the trip
    and the short proof's negative verdict, exactly as before."""
    _shorts_on(monkeypatch)
    p = _pool()
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v)
    assert p.state["mirror_live"] is False and p.state["mirror_live_trip"]["why"] == "wrong_sign_trip"
    assert b["frozen_reason"] == "wrong_sign_trip" and (b["last_plan"] or {}).get("short_proof") == "mismatch"
    assert _census(st, "wrong_sign_trip") == 1 and _census(st, "wrong_sign_hold") == 0


def test_e20_an_agreeing_sign_never_reaches_either_arm():
    """ledger 10 read as 14 (same sign, past the tolerance): E16's
    suspect on the first read -- neither the trip nor the hold."""
    p = _pool()
    b = p.add_book(ledger=10)
    st = _tick(p, _Venue(held={SLUG: 14}))
    assert b["state"] == "live" and "mirror_live_trip" not in p.state
    assert _census(st, "venue_ledger_suspect") == 1
    assert _census(st, "wrong_sign_hold") == 0 and _census(st, "wrong_sign_trip") == 0


# ------------------------------------------------ the names, the source, the docs

def test_e20_census_place_may_hold_list_and_the_source_shape():
    keys = ml.CENSUS_KEYS
    # E14b, E14, FILL lane 3 (three), T2 (two), FILL lane 5 (three), E22 (FILL lane 22, four) and FILL lane 11 (one) landed after E20 and sit nearer the key (-16/-15/-14 -> -29/-28/-27) -- FILL lane 16 (one name) and E21 (FILL lane 10, six) landed first, so every index past this lane's six moved by seven more
    # E23 (FILL lane 23) placed its six names nearer the key (-29 / -28 / -27 -> -35 / -34 / -33)
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-42 / -41 / -40 -> -46 / -45 / -44)
    assert keys[-55] == "wrong_sign_hold" and keys[-54] == "exit_take_rested" and keys[-13] == "drift_smaller_open"
    assert keys[-53] == "take_in_band"
    assert keys[-12] == "registered_no_increase" and len(set(keys)) == len(keys)
    assert "wrong_sign_hold" in ml._VENUE_MAY_HOLD_REASONS and "wrong_sign_trip" in ml._VENUE_MAY_HOLD_REASONS
    src = inspect.getsource(ml._tick_book)
    # the trip is guarded by the magnitude test; the hold is the other arm
    # E24 (FILL lane 24): the genuine inversion is judged on the book's OWN venue reading -- the walk's
    # figure net of the desk's hand fills on the book's side (`venue_own`); with no hand fill it is r.venue
    i_gen = src.index("genuine = abs(abs(venue_own) - abs(ledger)) <= mi.VENUE_LEDGER_TOL_SHARES")
    i_trip = src.index('await _trip_live_off(t, "wrong_sign_trip", {"book": book["id"], **detail})')
    i_hold = src.index('await _freeze(t, book, "wrong_sign_hold", detail)')
    assert i_gen < i_trip < i_hold
    assert src.count('_trip_live_off(t, "wrong_sign_trip"') == 1
    assert 'await _cancel_open_for(t, book, "wrong_sign_hold")' in src
    # the short proof's verdict is written only on the genuine inversion
    seg = src[i_gen:i_hold]
    assert seg.index("if genuine:") < seg.index("le._record_short_proof(t.pool, ok=False") < seg.index("else:")


def test_e20_the_hold_is_named_in_the_docs():
    doc = Path(ml.__file__).resolve().parents[3] / "docs" / "mirror-coverage.md"
    text = doc.read_text()
    assert re.search(r"^## \d+\. E20 \(2026-09-08\)", text, re.M)
    assert "wrong_sign_hold" in text and "wrong_sign_trip" in text and "663" in text
