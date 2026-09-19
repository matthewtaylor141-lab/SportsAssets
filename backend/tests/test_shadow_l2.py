"""THE INSTITUTIONAL L2 CONTRACT, PINNED TO WHAT PRODUCTION ACTUALLY SENT.

Every fixture below is the VERBATIM body of production run 35471198997
(2026-09-19 21:42:26Z, api.prod.polymarketexchange.com, scope
read:l2marketdata, evidenceClass OBSERVED_PRODUCTION). Not a shape
someone expected -- the shape the venue answered.

THE FAILURE THESE PREVENT is the one the owner's §3 names: "Do not
adapt from guessed field names." The production read lane's own summary
had been written against bidPrice / bidQty / bids / asks, and this venue
sends px / qty / bids / offers. A reader looking for `asks` finds
nothing, reports an empty book, and every experimental trade then
refuses on EXECUTABLE_DEPTH_NOT_IDENTIFIED -- which looks exactly like
a market with no liquidity and is in fact a parser that cannot read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_l2 as l2

# ── the verbatim production bodies ───────────────────────────────────

SYMBOL = "astatc-mls-sje-laf-2026-09-19-sh-ftts-laf"

BOOK = {
    "symbol": SYMBOL,
    "state": "INSTRUMENT_STATE_OPEN",
    "transactTime": "2026-09-19T21:42:26Z",
    "bids": [{"px": "97", "qty": "1", "symbolSubType": ""},
             {"px": "39", "qty": "407", "symbolSubType": ""},
             {"px": "1", "qty": "285000", "symbolSubType": ""}],
    "offers": [{"px": "98", "qty": "5000", "symbolSubType": ""},
               {"px": "99", "qty": "2500", "symbolSubType": ""}],
    "stats": {},
}

BBO = {
    "symbol": SYMBOL,
    "state": "INSTRUMENT_STATE_OPEN",
    "transactTime": "2026-09-19T21:42:26Z",
    "midPrice": "97.5",
    "spread": "1",
    "bestBid": {"px": "97", "qty": "1", "symbolSubType": ""},
    "bestOffer": {"px": "98", "qty": "5000", "symbolSubType": ""},
}

# The scales this fixture is converted with. NOT a claim about MLS --
# see test_the_scales_are_never_assumed.
PS, QS = 100, 100


def book(**kw):
    return l2.book_from(BOOK, price_scale=PS, qty_scale=QS, **kw)


# ── §3's seven questions, each answered by a test ────────────────────


def test_the_price_field_is_px_and_is_scaled():
    """`px` is a STRING of integer ticks. "97" is 97 ticks, which at
    priceScale 100 is $0.97 -- not 97, and not 9.7."""
    assert l2.F_PX == "px"
    assert book()["bids"][0][0] == pytest.approx(0.97)
    assert book()["asks"][0][0] == pytest.approx(0.98)


def test_the_quantity_field_is_qty_and_is_scaled():
    assert l2.F_QTY == "qty"
    # "5000" at fractionalQtyScale 100 is 50 contracts, not 5000.
    assert book()["asks"][0][1] == pytest.approx(50.0)


def test_the_sell_side_is_named_offers_not_asks():
    """THE SINGLE MOST LOAD-BEARING NAME. A parser looking for `asks`
    reads this book as having no sell side at all."""
    assert l2.SIDE_OFFERS == "offers"
    assert "asks" not in BOOK
    assert book()["askLevels"] == 2


def test_the_sort_order_is_best_first_on_both_sides():
    """Observed: bids descend 97, 39, 1; offers ascend 98, 99. Index 0
    is the touch on both sides, and the order is preserved rather than
    re-sorted so a venue that changed direction would be visible."""
    assert [p for p, _ in book()["bids"]] == [0.97, 0.39, 0.01]
    assert [p for p, _ in book()["asks"]] == [0.98, 0.99]


def test_the_timestamp_field_is_transact_time():
    assert l2.F_TRANSACT_TIME == "transactTime"
    assert book()["l2SourceTimestamp"] == "2026-09-19T21:42:26Z"


def test_the_symbol_echoes_the_retail_slug():
    assert book()["symbol"] == SYMBOL


def test_an_open_instrument_with_no_levels_is_empty_not_an_error():
    """§3's empty-book semantics. An OPEN market with no depth is a real
    state; it means no experimental trade can execute, which §4 requires
    be recorded rather than filled at an assumption."""
    out = l2.book_from({"symbol": SYMBOL, "state": l2.STATE_OPEN,
                        "bids": [], "offers": []},
                       price_scale=PS, qty_scale=QS)
    assert out["empty"] is True
    assert out["readable"] is True
    assert out["bids"] == [] and out["asks"] == []


# ── the scales are never assumed ─────────────────────────────────────


def test_the_scales_are_never_assumed():
    """px and qty are scaled integers and the scale is PER INSTRUMENT
    (100 for esports/ATP, 1000 for MLB and NFL). Assuming 100 would
    misprice an NFL row by 10x, and nothing downstream would look
    wrong."""
    for ps, qs in ((None, QS), (PS, None), (None, None)):
        with pytest.raises(l2.ScalesRequired):
            l2.book_from(BOOK, price_scale=ps, qty_scale=qs)


def test_a_different_scale_gives_a_different_book():
    """Proves the scale is load-bearing rather than decorative."""
    at_1000 = l2.book_from(BOOK, price_scale=1000, qty_scale=1000)
    assert at_1000["asks"][0] != book()["asks"][0]


# ── §2's provenance envelope ─────────────────────────────────────────


def test_every_provenance_field_the_directive_names_is_preserved():
    now = datetime(2026, 9, 19, 21, 42, 26, tzinfo=timezone.utc)
    out = book(request_id="3daf53df-f0a3-4b38-8ecf-40714575a6e5",
               requested_at=now - timedelta(milliseconds=116.5),
               received_at=now)
    assert out["l2Source"] == l2.L2_SOURCE_INSTITUTIONAL
    assert out["l2RequestId"] == "3daf53df-f0a3-4b38-8ecf-40714575a6e5"
    assert out["l2SourceTimestamp"] == "2026-09-19T21:42:26Z"
    assert out["l2ReceivedTimestamp"] == now
    assert out["l2LatencyMs"] == pytest.approx(116.5, abs=0.6)
    # The venue supplies no sequence on this endpoint. Saying so is not
    # the same as having one.
    assert out["l2BookVersion"] == sh.NOT_IDENTIFIED


def test_bbo_is_not_collapsed_into_l2():
    """§2: "Never collapse BBO into L2." A one-level book built from the
    touch would walk exactly the touch size and call everything beyond
    it unfilled -- an artefact of the endpoint, dressed as a
    measurement."""
    touch = l2.bbo_from(BBO, price_scale=PS, qty_scale=QS)
    assert touch["isDepth"] is False
    assert touch["l2Source"] == l2.L2_SOURCE_INSTITUTIONAL_BBO
    assert touch["l2Source"] != l2.L2_SOURCE_INSTITUTIONAL
    assert "bids" not in touch and "asks" not in touch


# ── §4: the walk over the real book ──────────────────────────────────


def test_the_thousand_dollar_walk_over_the_observed_book():
    """THE WHOLE POINT, on real production depth.

    $1,000 intended against this actual book fills 50 contracts at 0.98
    and 25 at 0.99 -- $73.75 -- and stops, because that is all the depth
    there was. "Do not manufacture the remaining."
    """
    out = sh.marketable_fill(sh.BUY, quantity=1000 / 0.98,
                             limit_price=0.99, arrival_book=book(),
                             decision_price=0.98)
    assert out["status"] == sh.PARTIAL
    assert out["shadowFilledQty"] == pytest.approx(75.0)
    assert out["vwap"] == pytest.approx(0.98333, abs=1e-4)
    executed = out["shadowFilledQty"] * out["vwap"]
    assert executed == pytest.approx(73.75, abs=0.01)
    # and the rest is UNFILLED, not quietly filled deeper
    assert out["unfilledQty"] > 900


def test_an_empty_book_yields_no_pnl_bearing_fill():
    """§4: "If no valid arrival L2 exists: SHADOW_EXECUTION =
    NOT_IDENTIFIED and no P&L-bearing trade is created." """
    empty = l2.book_from({"symbol": SYMBOL, "state": l2.STATE_OPEN,
                          "bids": [], "offers": []},
                         price_scale=PS, qty_scale=QS)
    out = sh.marketable_fill(sh.BUY, 1000, 0.99, empty)
    assert out["status"] == sh.NOT_IDENTIFIED
    assert out["shadowFilledQty"] is None


def test_a_level_with_an_unreadable_price_is_dropped_not_zeroed():
    """A zero-priced level would be taken first by any walk, and would
    make a free fill look like a real one."""
    out = l2.book_from(
        {"symbol": SYMBOL, "state": l2.STATE_OPEN, "bids": [],
         "offers": [{"px": None, "qty": "100"},
                    {"px": "98", "qty": "5000"},
                    {"px": "99", "qty": None}]},
        price_scale=PS, qty_scale=QS)
    assert out["asks"] == [(0.98, 50.0)]


def test_a_zero_or_negative_size_level_is_dropped():
    out = l2.book_from(
        {"symbol": SYMBOL, "state": l2.STATE_OPEN, "bids": [],
         "offers": [{"px": "98", "qty": "0"}, {"px": "99", "qty": "2500"}]},
        price_scale=PS, qty_scale=QS)
    assert out["asks"] == [(0.99, 25.0)]


def test_the_retail_and_institutional_sources_are_never_interchangeable():
    """A FINDING, RECORDED. On the verification symbol at 21:42Z the
    institutional book quoted 0.97/0.98 while our retail collector had
    read 0.59/0.60 six minutes earlier. They may be the same market at
    different instants or different contracts wearing one slug -- that
    is exactly why the source name travels on every row instead of the
    two being pooled."""
    assert l2.L2_SOURCE_INSTITUTIONAL != l2.L2_SOURCE_INSTITUTIONAL_BBO
    assert book()["l2Source"] == l2.L2_SOURCE_INSTITUTIONAL


# ── the binding must accept what the FEATURE BUILDER actually emits ───


def test_bind_leg_reads_the_features_the_collector_really_produces():
    """THE DEFECT THIS PINS, found in production and not in a fixture.

    `bind_leg` is called on shadow_bettor.microstructure_of's output,
    not on a raw market-state record. That output carries `status`
    MEASURED and NO `readable` key, so a gate reading only `readable`
    sent every row down the market-level branch: 127 opportunities in
    the first production hour, 71 symbols, not one of them YES-bound,
    and the experimental lane's eligible population was therefore
    empty while every part of it reported healthy.

    The fixtures passed because they were hand-built with
    `readable: True` -- which the real producer never sets. So this
    test builds its input with the real producer.
    """
    from datetime import datetime, timezone

    from sportsassets import shadow_bettor as bettor
    from sportsassets import shadow_store as store

    state = store.market_state_record(
        captured_at=datetime(2026, 9, 19, 23, 40, tzinfo=timezone.utc),
        symbol="astatc-mls-sje-laf-2026-09-19-sh-ftts-laf",
        evidence_source="PMUS_BBO", readable=True, bid=0.40, ask=0.41)
    features = bettor.microstructure_of(state)
    assert "readable" not in features        # the shape that caused it
    assert features["status"] == "MEASURED"

    bound = l2.bind_leg(features, "yes")
    assert bound["bboBinding"] == l2.BIND_YES
    assert l2.leg_is_execution_bound(bound) is True

    no_side = l2.bind_leg(bettor.microstructure_of(state), "no")
    assert no_side["bboBinding"] == l2.BIND_NOT_IDENTIFIED
    assert no_side["bid"] is None

    # and an unreadable book still goes to market level
    unreadable = bettor.microstructure_of(None)
    assert l2.bind_leg(unreadable, "yes")["bboBinding"] == \
        l2.BIND_MARKET_LEVEL
