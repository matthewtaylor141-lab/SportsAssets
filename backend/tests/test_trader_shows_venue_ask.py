"""Trader shows the venue ASK beside the bid, on every card and on the focus
desk (2026-10-08 device acceptance: the trader-mode API returns quote.ask and
the page rendered only the bid, so the quote on screen was half the book;
trader_accept.js named ASK_NOT_SHOWN on every device). No new data path: the
value is the snapshot's own quote.ask, "—" when the book carries none."""
from __future__ import annotations

import pathlib

JS = (pathlib.Path(__file__).resolve().parents[2] / "frontend/public/command/trader.js").read_text()


def test_every_card_renders_the_ask_beside_the_bid():
    card = JS[JS.index('<div class="card-prices">'):JS.index('<div class="price-block target">')]
    assert "${C.cents(q.bid)}" in card
    assert 'class="ask-line">Ask ${C.cents(q.ask)}' in card


def test_the_focus_desk_renders_the_ask_and_when_it_was_observed():
    desk = JS[JS.index('<div class="screen-value">'):]
    desk = desk[:desk.index("</div>")]
    assert desk.startswith('<div class="screen-value">${C.cents(q.bid)}')
    assert "Ask ${C.cents(q.ask)}" in desk and "observed" in desk


def test_the_ask_comes_from_the_snapshot_only():
    # no request, no derived or simulated ask: the same quote object as the bid
    assert JS.count("C.cents(q.ask)") == 2
    assert "q.ask=" not in JS.replace(" ", "")
