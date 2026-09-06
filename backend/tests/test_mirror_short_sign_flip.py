"""P2 rung S0, the SIGN FLIP (owner order 2026-09-05, "we need to make
sure we are mirroring shorts"; brief B8, owner default Q5 (a)).

His net crosses zero while a book is open. The book flattens under the
name `sign_flip` -- a long book by its own rest, a short book by
close_position when sole -- waits the flat close like any flat book,
and only THEN does the opposite side open as a NEW episode: the
one-open-per-market index and the 3600 s flat close decide when, never
the flip. Driven end to end through workers.mirror_live.tick_once with
test_mirror_live_worker's fakes, both directions.
"""
import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse fixture arms every tick
    BUY, INTENT, M, N, NOW, SELL, SHORT, SLUG, _armed, _census, _his, _kinds, _mkt, _places, _pool,
    _short_book, _shorts_on, _tick, _Venue,
)


def test_a_long_book_flattens_on_his_flip_to_short_and_the_short_episode_opens_after_the_flat_close(monkeypatch):
    _shorts_on(monkeypatch)
    # an open long book of 300, and his net now -300 (100 long against 400 other)
    p = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "sign_flip") == 1 and _census(st, "short_side_refused") == 0
    assert b["target"] == 0 and b["last_plan"]["sign_flip"] is True
    # the long book flattens by ITS rule: a SELL_LONG rest at his
    # equivalent or the ask, never marketed, never a short
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][6] == INTENT and pl[0][3] == 300
    assert pl[0][5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL" and "close" not in _kinds(v)
    o = next(iter(p.orders.values()))
    assert (o["side"], o["kind"], o["intent"]) == (SELL, "flatten_paired", "ORDER_INTENT_SELL_LONG")
    # the rest stands: the episode waits on it, then on the flat close
    assert b["last_plan"]["close"] == "orders_open" and b["state"] == "live"
    assert rules.episode_close_reason(ml._book_state(b), False, False, None, 0,
                                      sign_flipped=True) == "sign_flip"
    assert len(p.books) == 1, "no second book while this one is open"
    # the rest fills: flat, and the flat clock starts -- the flip does not shorten it
    v2 = _Venue(held={}, fills={"oid-1": (300.0, 0.32)})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(100.0, 400.0))
    assert b["ledger_net"] == 0 and b["state"] == "live" and b["last_plan"]["close"] == "not_due"
    assert len(p.books) == 1 and not _places(v2)
    assert _census(st2, "sign_flip") == 1
    # the flat hour passes: the long episode closes cashed_out
    st3 = _tick(p, _Venue(held={}), now=NOW + 30 + rules.MIRROR_FLAT_CLOSE_S + 1, http=_mkt(100.0, 400.0))
    assert b["state"] == "closed" and _census(st3, "closed_cashed_out") == 1
    assert len(p.books) == 1, "the candidate waits for the tick after the close"
    # the next tick opens the SHORT side as a new episode of the market
    v4 = _Venue(held={})
    st4 = _tick(p, v4, now=NOW + 60 + rules.MIRROR_FLAT_CLOSE_S, http=_mkt(100.0, 400.0))
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
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls and "place" not in _kinds(v)
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    assert b["realized_pnl"] == pytest.approx((0.32 - 0.29) * 300)
    assert b["state"] == "live" and b["last_plan"]["close"] == "not_due" and len(p.books) == 1
    # the flat close, then the long side as a new episode
    st2 = _tick(p, _Venue(held={}), now=NOW + rules.MIRROR_FLAT_CLOSE_S + 1)
    assert b["state"] == "closed" and _census(st2, "closed_cashed_out") == 1 and len(p.books) == 1
    v3 = _Venue(held={})
    st3 = _tick(p, v3, now=NOW + rules.MIRROR_FLAT_CLOSE_S + 31)
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
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "sign_flip") == 0 and _census(st, "short_side_refused") == 1
    assert b["target"] == 0 and "sign_flip" not in (b["last_plan"] or {})
    # the same flatten, under the P1 name, and never a short book after it
    assert _places(v) and _places(v)[0][4] is True and b["last_plan"]["close"] == "orders_open"
    assert rules.episode_close_reason(ml._book_state(b), False, False, None, 0) == "held"
