"""A SHORT LEG MUST BE PRICED OFF THE BID LADDER, NOT THE OFFER LADDER.

THE DEFECT THIS EXISTS FOR (acceptance run 28). `venue_quote` accepted an
`outcome_index` argument and never used it -- it returned BEST_ASK for the
market whatever the caller asked. On the `aec-` family both sides of a
market carry the SAME identifier (equal to the slug) and the side is
carried ONLY by the order intent, so eleven of run 28's forty-one
candidates resolved to a real venue contract with intent
ORDER_INTENT_BUY_SHORT and were refused, because pricing p(outcome)
against the long book's ask is a sign error.

The refusal was correct. The reader was one-sided, and a parameter that
looks like it selects a side while doing nothing is what let that hide.

THE CONVERSION IS NOT GUESSED HERE. `pmus.slug_bid` settled the quote
shape against five live markets, exact to the cent on all five:

    long.price  == bestAsk        (5/5)
    short.price == 1 - bestBid    (5/5)

and `live_executor.cost_per_share` is the pure per-share arithmetic
already used for money. `acquisition_ladder` is the ladder form of that
same definition and delegates to it, so the two cannot drift.
"""
import json
import pathlib

import pytest

from sportsassets import bettor_book_snapshot as BS

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"

FIXTURE = (pathlib.Path(__file__).resolve().parents[2]
           / "research" / "fixtures" / "run85_pmus_book_fixture.json")


def book(bids=None, offers=None):
    """A venue-shaped payload: {"px": {"value": ...}, "qty": ...}."""
    def side(levels):
        if levels is None:
            return None
        return [{"px": {"value": str(p), "currency": "USD"},
                 "qty": str(q)} for p, q in levels]
    md = {"marketSlug": "aec-test", "state": "MARKET_STATE_OPEN"}
    if bids is not None:
        md["bids"] = side(bids)
    if offers is not None:
        md["offers"] = side(offers)
    return md


# ── THE WORKED EXAMPLE, PINNED EXACTLY ───────────────────────────────

def test_bid_60_ask_63_gives_short_cost_40_on_bid_quantity():
    """The number this whole repair is judged on.

    YES bid 0.60 (qty 500) / ask 0.63 (qty 900).

      SHORT acquisition cost = 1 - 0.60 = 0.40, quantity 500.

    NOT 0.37 (= 1 - ask, a price nobody is offering) and NOT 900 (the
    offer quantity, which belongs to the other side of the book).
    """
    md = book(bids=[(0.60, 500)], offers=[(0.63, 900)])

    s = BS.acquisition_ladder(md, intent=SHORT)
    assert s["ok"] is True
    assert s["side_consumed"] == BS.SIDE_BID
    assert s["levels"][0]["acquisition_price"] == pytest.approx(0.40)
    assert s["levels"][0]["api_price"] == pytest.approx(0.60)
    assert s["levels"][0]["qty"] == pytest.approx(500.0)
    assert s["displayed_depth"] == pytest.approx(500.0)
    # the two wrong answers, named
    assert s["levels"][0]["acquisition_price"] != pytest.approx(0.37)
    assert s["levels"][0]["qty"] != pytest.approx(900.0)

    lg = BS.acquisition_ladder(md, intent=LONG)
    assert lg["side_consumed"] == BS.SIDE_ASK
    assert lg["levels"][0]["acquisition_price"] == pytest.approx(0.63)
    assert lg["levels"][0]["api_price"] == pytest.approx(0.63)
    assert lg["levels"][0]["qty"] == pytest.approx(900.0)


def test_the_two_price_spaces_are_both_kept():
    md = book(bids=[(0.60, 500)], offers=[(0.63, 900)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    lv = s["levels"][0]
    assert lv["api_price"] != lv["acquisition_price"], (
        "a short's venue price and its cost are different numbers")
    assert lv["api_price"] + lv["acquisition_price"] == pytest.approx(1.0)
    assert "api_price is the venue's" in s["price_spaces"]


def test_it_delegates_to_the_money_arithmetic_already_in_use():
    from sportsassets.live_executor import cost_per_share

    md = book(bids=[(0.6853, 10)], offers=[(0.71, 10)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    assert s["levels"][0]["acquisition_price"] == pytest.approx(
        cost_per_share(0.6853, SHORT), abs=1e-9)


# ── ASYMMETRIC BOOKS ─────────────────────────────────────────────────

def test_an_asymmetric_book_prices_each_side_off_its_own_ladder():
    """Deliberately lopsided: one deep bid, thin offers."""
    md = book(bids=[(0.55, 10000), (0.54, 5000)],
              offers=[(0.80, 7)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    lg = BS.acquisition_ladder(md, intent=LONG)
    assert s["displayed_depth"] == pytest.approx(15000.0)
    assert lg["displayed_depth"] == pytest.approx(7.0)
    assert s["levels"][0]["acquisition_price"] == pytest.approx(0.45)
    assert lg["levels"][0]["acquisition_price"] == pytest.approx(0.80)


def test_cheapest_acquisition_comes_first_on_both_sides():
    """Offers ascend and bids descend in API space, so sorting on the
    ACQUISITION price is the single rule correct for both."""
    md = book(bids=[(0.50, 10), (0.58, 20), (0.55, 30)],
              offers=[(0.70, 10), (0.66, 20)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    lg = BS.acquisition_ladder(md, intent=LONG)
    assert [r["acquisition_price"] for r in s["levels"]] == [
        pytest.approx(0.42), pytest.approx(0.45), pytest.approx(0.50)]
    assert [r["acquisition_price"] for r in lg["levels"]] == [
        pytest.approx(0.66), pytest.approx(0.70)]


# ── EMPTY / MISSING REQUIRED SIDE ────────────────────────────────────

def test_an_absent_required_side_refuses_by_name():
    md = book(offers=[(0.63, 900)])            # no bids key at all
    s = BS.acquisition_ladder(md, intent=SHORT)
    assert s["ok"] is False
    assert s["refusal"] == BS.R_SIDE_EMPTY
    assert s["parse_status"] == BS.PARSE_NO_LEVELS
    # and the LONG side of the same payload still reads
    assert BS.acquisition_ladder(md, intent=LONG)["ok"] is True


def test_a_present_but_empty_side_is_a_valid_book_with_nobody_on_it():
    md = book(bids=[], offers=[(0.63, 900)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    assert s["ok"] is False
    assert s["book_was"] == "VALID_BUT_EMPTY"
    assert s["parse_status"] == BS.PARSE_OK, (
        "an empty side parsed correctly; that is not a parse failure")


def test_malformed_levels_are_distinguished_from_an_empty_book():
    md = {"bids": [{"px": {"value": "not-a-number"}, "qty": "5"}],
          "offers": []}
    s = BS.acquisition_ladder(md, intent=SHORT)
    assert s["ok"] is False
    assert s["book_was"] == "MALFORMED_LEVELS"
    assert BS.R_PX_UNPARSEABLE in s["reasons"]


def test_a_zero_quantity_level_is_not_treated_as_depth():
    md = book(bids=[(0.60, 0)], offers=[(0.63, 900)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    assert s["ok"] is False
    assert BS.R_NO_SIZE_AT_PRICE in s["reasons"]


def test_an_unrecognised_intent_refuses_rather_than_defaulting():
    md = book(bids=[(0.60, 500)], offers=[(0.63, 900)])
    got = BS.acquisition_ladder(md, intent=None)
    assert got["ok"] is False
    assert got["refusal"] == BS.R_INTENT_UNKNOWN


# ── SIZING ACROSS MULTIPLE LEVELS ────────────────────────────────────

def test_sizing_walks_several_levels_and_weights_the_price():
    md = book(bids=[(0.60, 100), (0.55, 100), (0.50, 100)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    # acquisition costs 0.40, 0.45, 0.50
    got = BS.fill_across_levels(s, 250)
    assert got["covers_requested"] is True
    assert got["filled"] == pytest.approx(250.0)
    assert got["levels_used"] == 3
    # 100*.40 + 100*.45 + 50*.50 = 40 + 45 + 25 = 110
    assert got["cost"] == pytest.approx(110.0)
    assert got["vwap_acquisition_price"] == pytest.approx(0.44)


def test_insufficient_depth_is_named_not_quietly_partial():
    md = book(bids=[(0.60, 10)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    got = BS.fill_across_levels(s, 250)
    assert got["covers_requested"] is False
    assert got["refusal"] == BS.R_NOT_ENOUGH_DEPTH
    assert got["filled"] == pytest.approx(10.0)
    assert "10.000000 of the 250.000000" in got["why"]


def test_sizing_a_refused_ladder_carries_the_refusal_through():
    md = book(offers=[(0.63, 900)])
    s = BS.acquisition_ladder(md, intent=SHORT)
    got = BS.fill_across_levels(s, 100)
    assert got["covers_requested"] is False
    assert got["refusal"] == BS.R_SIDE_EMPTY


# ── DEPTH IS NOT SILENTLY TRUNCATED TO FIVE ──────────────────────────

def test_all_levels_are_read_by_default_not_the_display_five():
    md = book(offers=[(0.50 + i / 100.0, 10) for i in range(21)])
    lg = BS.acquisition_ladder(md, intent=LONG)
    assert BS.LADDER_LEVELS == 5
    assert lg["levels_published"] == 21
    assert lg["levels_read"] == 21, (
        "the snapshot's 5-level DISPLAY truncation must not be "
        "inherited by a depth calculation")
    assert lg["truncated"] is False
    assert len(lg["levels"]) == 21


def test_an_explicit_limit_is_reported_as_truncated():
    md = book(offers=[(0.50 + i / 100.0, 10) for i in range(21)])
    lg = BS.acquisition_ladder(md, intent=LONG, limit=5)
    assert lg["levels_read"] == 5
    assert lg["truncated"] is True


# ── THE REAL VENUE PAYLOAD ───────────────────────────────────────────

@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not in tree")
def test_the_verbatim_venue_fixture_parses_on_both_sides():
    """run85_pmus_book_fixture.json is an anonymised VERBATIM gateway
    response -- only the slug was replaced. Its levels are
    {"px": {"value": "0.0020"}, "qty": "65.0000"}, which is the nested
    shape my incentive adapter was failing to read."""
    payload = json.loads(FIXTURE.read_text())
    md = payload["body"]["marketData"]
    assert isinstance(md["bids"], list) and md["bids"]
    assert isinstance(md["offers"], list) and len(md["offers"]) == 21

    lg = BS.acquisition_ladder(md, intent=LONG)
    assert lg["ok"] is True, lg
    assert lg["levels_read"] == 21
    assert lg["levels"], "the real payload produced no readable level"

    # The fixture's ONE bid is 0.0010 x 65 and its best offer is
    # 0.0020 x 44620 -- read off the payload, not off the stats block
    # (my first draft of this assertion took 0.0020 from stats.openPx
    # and was wrong about which number it was checking).
    s = BS.acquisition_ladder(md, intent=SHORT)
    assert s["ok"] is True, s
    assert s["levels_published"] == 1
    assert s["levels"][0]["api_price"] == pytest.approx(0.0010)
    assert s["levels"][0]["acquisition_price"] == pytest.approx(0.9990)
    assert s["levels"][0]["qty"] == pytest.approx(65.0)
    # and the long side reads the deep offer book, cheapest first
    assert lg["levels"][0]["api_price"] == pytest.approx(0.0020)
    assert lg["levels"][0]["qty"] == pytest.approx(44620.0)
    assert lg["levels"][-1]["api_price"] == pytest.approx(0.9990)


# ── THE EXTRACTION MUST NOT DRIFT ────────────────────────────────────

def test_the_extracted_arithmetic_equals_the_executors():
    """`bettor_book_snapshot.cost_per_share` is a deliberate duplicate of
    `live_executor.cost_per_share`.

    The shadow loop is guarded by a test asserting that no
    order-submission module is even NAMEABLE from it, so importing the
    executor there -- even for one line -- would defeat a guard that
    matters more than the duplication. The cost of duplicating is drift,
    and this is the check that pays it: the two must agree exactly.
    """
    from sportsassets import live_executor as LE

    for px in (0.0, 0.0010, 0.1234, 0.5, 0.6853, 0.9990, 1.0):
        for intent in (LONG, SHORT, "ORDER_INTENT_SELL_SHORT", None):
            assert BS.cost_per_share(px, intent) == pytest.approx(
                LE.cost_per_share(px, intent), abs=1e-12), (px, intent)
            assert BS.is_short_intent(intent) is LE.is_short_intent(intent)


def test_the_shadow_loop_names_no_order_submission_module():
    """The guard this extraction exists to keep intact."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L

    src = inspect.getsource(L)
    for bad in ("post_order", "place_order", "submit_order",
                "create_order", "live_executor", "OrderArgs",
                "sign_order", "api_creds"):
        assert bad not in src, bad
