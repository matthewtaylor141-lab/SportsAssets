"""REDUCE was advertised, selectable, and could not fill its chosen size.

Owner requirement: "Every advertised action must either have a verified
executable path or be explicitly unavailable."

THE DEFECT. `select_exit` took the wire limit from the ladder's BEST level
for both DIRECT_EXIT and REDUCE. For DIRECT_EXIT that is right -- its
quantity is `size_at_best`, so the best level's price clears all of it. For
a REDUCE whose quantity spans several levels it is wrong: a sell limit at
the best price matches only the best level's depth. A REDUCE selected for
10 contracts on a vwap of 0.578 was submitted bounded at 0.62 and could
fill 4.

It lost no money -- a limit is never crossed downward -- but REDUCE is in
EXECUTABLE_ACTIONS, so it was allowed to WIN the ranking on an advantage
the order could not realise. That is the same defect class as the
complement case, in the one action that was believed executable.

`bettor_book_snapshot.as_sale_ladder` also dropped `api_price`, so the
dispatch could not have bounded correctly even had it tried.
"""

import pytest

from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_mgmt_select as MS

HOLD = {"probability": 0.50, "status": "IDENTIFIED"}


def _fee(*, qty, price):
    return 0.0


def _ladder(levels):
    return {"levels": [{"acquisition_price": p, "api_price": p, "qty": q}
                       for p, q in levels]}


def _rank(levels, *, bid_size, qty=24, basis=0.40):
    return MS.rank_with_hold(
        qty, basis, ev_hold=dict(HOLD), bid=levels[0][0], bid_size=bid_size,
        complement_ask=None, complement_ask_size=None, fee_fn=_fee,
        venue="polymarket-us", us_market_slug="x", held_is_long=True,
        sale_ladder=_ladder(levels),
        executable_actions=FM.EXECUTABLE_ACTIONS)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE CASE WHERE REDUCE IS A DISTINCT ACTION AT ALL
# ═════════════════════════════════════════════════════════════════════

def test_one_level_above_the_hurdle_makes_reduce_identical_to_direct_exit():
    """Not a defect: with one level there is nothing to split.

    Both score the same and DIRECT_EXIT wins the tie, so nothing depends
    on the marginal-level logic here.
    """
    r = _rank([(0.62, 4), (0.48, 20)], bid_size=4)
    got = {c["action"]: c for c in r["candidates"]}
    assert got["REDUCE"]["qty"] == pytest.approx(4.0)
    assert got["DIRECT_EXIT"]["qty"] == pytest.approx(4.0)
    assert got["REDUCE"]["value_usd"] == pytest.approx(
        got["DIRECT_EXIT"]["value_usd"])
    assert r["marginal_sale"]["needs_a_marginal_wire_price"] is False


def test_two_levels_above_the_hurdle_make_reduce_strictly_win():
    """THE case the dispatch could not execute."""
    r = _rank([(0.62, 4), (0.55, 6), (0.45, 20)], bid_size=4)
    assert r["selected"] == "REDUCE"
    assert r["selected_qty"] == pytest.approx(10.0)
    got = {c["action"]: c for c in r["candidates"]}
    # It wins BECAUSE it sells more than the best level's depth.
    assert got["REDUCE"]["value_usd"] > got["DIRECT_EXIT"]["value_usd"]
    assert got["DIRECT_EXIT"]["qty"] == pytest.approx(4.0)


# ═════════════════════════════════════════════════════════════════════
# 2 · THE MARGINAL LEVEL, WHICH IS WHAT AN ORDER MUST BE BOUNDED AT
# ═════════════════════════════════════════════════════════════════════

def test_the_marginal_level_is_the_worst_level_taken():
    r = _rank([(0.62, 4), (0.55, 6), (0.45, 20)], bid_size=4)
    m = r["marginal_sale"]
    assert m["levels_spanned"] == 2
    assert m["needs_a_marginal_wire_price"] is True
    assert m["marginal_api_price"] == pytest.approx(0.55)
    assert m["marginal_proceeds_per_contract"] == pytest.approx(0.55)
    # AND THE VWAP IS A DIFFERENT NUMBER: (4*0.62 + 6*0.55)/10.
    assert m["vwap"] == pytest.approx(0.578)
    assert "clears every better level" in m[
        "why_the_marginal_level_bounds_the_order"]


def test_the_best_level_price_would_fill_only_the_best_level():
    """The arithmetic behind the defect, stated as a test.

    A sell limit matches bids at or above it. Bounding 10 contracts at
    0.62 reaches only the 4 resting at 0.62.
    """
    levels = [(0.62, 4), (0.55, 6), (0.45, 20)]
    r = _rank(levels, bid_size=4)
    bound_at_best = 0.62
    fillable = sum(q for p, q in levels if p >= bound_at_best - 1e-9)
    assert fillable == 4
    assert r["selected_qty"] == pytest.approx(10.0)
    assert fillable < r["selected_qty"], (
        "if this ever stops holding the defect is gone and this test "
        "should be re-derived, not deleted")
    # Bounded at the MARGINAL level, the whole quantity is reachable.
    bound_at_marginal = r["marginal_sale"]["marginal_api_price"]
    reachable = sum(q for p, q in levels if p >= bound_at_marginal - 1e-9)
    assert reachable == pytest.approx(10.0)


def test_a_ladder_with_no_wire_price_yields_no_marginal_wire():
    """`as_sale_ladder` used to drop api_price entirely."""
    r = MS.rank_with_hold(
        24, 0.40, ev_hold=dict(HOLD), bid=0.62, bid_size=4,
        complement_ask=None, complement_ask_size=None, fee_fn=_fee,
        venue="polymarket-us", us_market_slug="x", held_is_long=True,
        sale_ladder={"levels": [{"acquisition_price": 0.62, "qty": 4},
                                {"acquisition_price": 0.55, "qty": 6},
                                {"acquisition_price": 0.45, "qty": 20}]},
        executable_actions=FM.EXECUTABLE_ACTIONS)
    m = r["marginal_sale"]
    assert m["needs_a_marginal_wire_price"] is True
    assert m["marginal_api_price"] is None


# ═════════════════════════════════════════════════════════════════════
# 3 · THE SALE LADDER NOW CARRIES THE WIRE PRICE
# ═════════════════════════════════════════════════════════════════════

def test_as_sale_ladder_carries_the_wire_price_per_level():
    from sportsassets import bettor_book_snapshot as BS

    exit_lad = {"ok": True, "levels": [
        {"level": 0, "exit_price": 0.62, "api_price": 0.62, "qty": 4},
        {"level": 1, "exit_price": 0.55, "api_price": 0.55, "qty": 6}]}
    sl = BS.as_sale_ladder(exit_lad)
    assert [lv["api_price"] for lv in sl["levels"]] == [0.62, 0.55]
    assert sl["wire_price_space"] == "VENUE_WIRE_CONTRACT_PRICE"
    # The two spaces stay named apart.
    assert sl["price_space"] == "EXIT_PROCEEDS_PER_CONTRACT"


# ═════════════════════════════════════════════════════════════════════
# 4 · AND THE DISPATCH REFUSES RATHER THAN UNDER-FILLING
# ═════════════════════════════════════════════════════════════════════

def test_the_refusal_code_exists_and_says_nothing_is_sent():
    assert FM.R_REDUCE_MARGINAL_WIRE_NOT_SUPPLIED == \
        "THE_SALE_LADDER_SUPPLIED_NO_WIRE_PRICE_FOR_THE_MARGINAL_LEVEL"


def test_select_exit_bounds_a_multi_level_reduce_at_the_margin():
    """SOURCE-LEVEL, because the seam is inside select_exit's pricing.

    A behavioural test needs a held position plus a venue book that
    produces a stepped ladder through `bettor_book_snapshot`; that is the
    integration test in the demonstration suite. This pins the three
    facts that make the behaviour possible, and the integration test
    exercises them together.
    """
    import inspect

    src = inspect.getsource(FM.select_exit)
    assert "needs_a_marginal_wire_price" in src
    assert "marginal_api_price" in src
    # The guard compares the MARGIN, not the average -- my first version
    # compared the vwap and refused every multi-level REDUCE.
    i = src.index("marginal_proceeds_per_contract")
    j = src.index("rounded = safe_exit_cent")
    assert i < j, "the guard must be given the marginal proceeds first"
    assert "reduce_expected_vwap" in src


def test_the_vwap_is_reported_as_not_being_the_bound():
    import inspect

    src = inspect.getsource(FM.select_exit)
    assert "reduce_vwap_is_not_the_bound" in src
    assert "bounded at the marginal level" in src
