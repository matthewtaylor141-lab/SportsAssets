"""P2 rung S0, the SIGN FLIP (owner order 2026-09-05, "we need to make
sure we are mirroring shorts"; brief B8, owner default Q5 (a)).

His net crosses zero while a book is open. The book flattens under the
name `sign_flip` -- a long book by its own rest, a short book by
close_position when sole -- and once flat with no order open the flip
IS the close (2026-09-06, owner 19:33Z "I need more trades firing in
the mirror sleeve"): the episode closes on that tick, no 3600 s flat
wait, and the opposite side opens as a NEW episode on the next tick
under the one-open-per-market index. Driven end to end through
workers.mirror_live.tick_once with test_mirror_live_worker's fakes,
both directions.
"""
import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse fixture arms every tick
    BUY, COVER_AT_CEILING_BIPS, INTENT, M, N, NOW, SELL, SHORT, SLUG, _armed, _census, _his, _kinds, _mkt, _places, _pool,
    _short_book, _shorts_on, _tick, _Venue,
)


def test_a_long_book_flattens_on_his_flip_to_short_and_the_short_episode_opens_once_flat(monkeypatch):
    _shorts_on(monkeypatch)
    # an open long book of 300, and his net now -300 (100 long against 400 other)
    p = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    b = p.add_book(ledger=300)
    # his equivalent is 0.28 (the other token at 0.72); the bid three
    # cents under him is outside E4's one-cent tolerance, so the flatten
    # RESTS at his cent (within a cent it would take at once: section 21
    # of the worker tests, the book-29 replay)
    v = _Venue(bid=0.26, held={SLUG: 300})
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "sign_flip") == 1 and _census(st, "short_side_refused") == 0
    assert b["target"] == 0 and b["last_plan"]["sign_flip"] is True
    # the long book flattens by ITS rule: a SELL_LONG rest at his
    # equivalent's cent (E4; the ask no longer lifts it), never
    # marketed past the cent, never a short
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][6] == INTENT and pl[0][3] == 300
    assert pl[0][5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL" and "close" not in _kinds(v)
    assert pl[0][2] == 0.28 and _census(st, "exit_out_of_tol") == 1
    o = next(iter(p.orders.values()))
    assert (o["side"], o["kind"], o["intent"]) == (SELL, "flatten_paired", "ORDER_INTENT_SELL_LONG")
    # the rest stands: the episode waits on it (orders_open), then closes
    # on the tick the venue reads flat
    assert b["last_plan"]["close"] == "orders_open" and b["state"] == "live"
    assert rules.episode_close_reason(ml._book_state(b), False, False, None, 0,
                                      sign_flipped=True) == "sign_flip"
    assert len(p.books) == 1, "no second book while this one is open"
    # the rest fills: flat, no order open, his net still against the
    # book -- the flip IS the close: the long episode closes cashed_out
    # on this tick, 30 s after the flip, not an hour
    v2 = _Venue(held={}, fills={"oid-1": (300.0, 0.32)})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert b["ledger_net"] == 0 and b["state"] == "closed" and b["last_plan"]["close"] == "cashed_out"
    assert len(p.books) == 1 and not _places(v2), "the candidate waits for the tick after the close"
    assert _census(st2, "sign_flip") == 1 and _census(st2, "closed_cashed_out") == 1
    # the next tick opens the SHORT side as a new episode of the market
    v4 = _Venue(held={})
    st4 = _tick(p, v4, now=NOW + 60, http=_mkt(100.0, 400.0))
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == SHORT and books[1]["episode"] == 2
    assert books[1]["target"] == -300 and books[1]["flat_reopens"] == 1
    pl4 = _places(v4)
    assert len(pl4) == 1 and pl4[0][4] is False and pl4[0][6] == SHORT
    assert _census(st4, "short_open") == 1 and _census(st4, "sign_flip") == 0


def test_a_short_book_flattens_by_close_position_on_his_flip_to_long_and_the_long_episode_follows(monkeypatch):
    _shorts_on(monkeypatch)
    # an open short book of 300, and his net now +300 (the default fixture: 300 long, none other)
    p = _pool()
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300})
    st = _tick(p, v)
    assert _census(st, "sign_flip") == 1 and b["target"] == 0 and b["last_plan"]["sign_flip"] is True
    # sole holder: the one proven short exit, at once
    assert ("close", SLUG, COVER_AT_CEILING_BIPS) in v.calls and "place" not in _kinds(v)
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    assert b["realized_pnl"] == pytest.approx((0.32 - 0.29) * 300)
    # flat by close_position inside the tick: the venue was read at -300
    # BEFORE the cover, so the close waits for the venue's own 0 (review
    # M-1) -- the book stays live, not_due, this tick
    assert b["state"] == "live" and b["last_plan"]["close"] == "not_due" and len(p.books) == 1
    assert _census(st, "closed_cashed_out") == 0
    # next tick the venue reads 0: the flip IS the close (no flat wait)
    st2 = _tick(p, _Venue(held={}), now=NOW + 30)
    assert b["state"] == "closed" and b["last_plan"]["close"] == "cashed_out" and len(p.books) == 1
    assert _census(st2, "closed_cashed_out") == 1
    # and the long side follows as a new episode on the tick after
    v3 = _Venue(held={})
    st3 = _tick(p, v3, now=NOW + 60)
    books = sorted(p.books.values(), key=lambda x: x["id"])
    assert len(books) == 2 and books[1]["intent"] == INTENT and books[1]["episode"] == 2
    assert books[1]["target"] == 300
    pl3 = _places(v3)
    assert len(pl3) == 1 and pl3[0][4] is False and pl3[0][6] == INTENT
    assert _census(st3, "rest_placed") == 1 and _census(st3, "short_open") == 0


def test_with_the_knob_off_a_long_book_never_flips_and_names_the_p1_refusal(monkeypatch):
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    p = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    b = p.add_book(ledger=300)
    v = _Venue(bid=0.26, held={SLUG: 300})      # outside E4's cent of his 0.28: the flatten rests
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "sign_flip") == 0 and _census(st, "short_side_refused") == 1
    assert b["target"] == 0 and "sign_flip" not in (b["last_plan"] or {})
    # the same flatten, under the P1 name, and never a short book after it
    assert _places(v) and _places(v)[0][4] is True and b["last_plan"]["close"] == "orders_open"
    assert rules.episode_close_reason(ml._book_state(b), False, False, None, 0) == "held"
