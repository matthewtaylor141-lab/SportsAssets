#!/usr/bin/env python3
"""Offline tests for the Run 85 Phase 2D discovery. Contacts nothing.

Three things here are load-bearing and are tested against hand-built inputs
whose right answer is known before the code runs:

  * classify() must never infer a market type from a slug or a title. A slug
    reading "nfl-ne-sea-moneyline" with no structured type field is
    NOT_IDENTIFIED, not GAME_LEVEL. Section C forbids substring inference and
    the whole run's verdict rests on that line holding.
  * near_touch() must measure in the MARKET'S OWN tick and must never produce
    a whole-ladder aggregate -- the measure Phase 2C retracted.
  * Budget must stop the run rather than exceed the disclosed bound.

Run:  python3 -m pytest research/test_run85_phase2d.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "p2d", Path(__file__).with_name("run85_phase2d.py"))
D = importlib.util.module_from_spec(_s)
_s.loader.exec_module(D)


def lvl(px, qty):
    return {"px": {"value": px, "currency": "USD"}, "qty": qty}


def body(bids, offers):
    return {"marketData": {"marketSlug": "m", "bids": bids, "offers": offers}}


# ------------------------------------------------------------- classify
def test_a_structured_game_type_is_game_level():
    assert D.classify({"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE"})[0] \
        == "GAME_LEVEL"
    assert D.classify({"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_SPREAD"})[0] \
        == "GAME_LEVEL"
    assert D.classify({"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL"})[0] \
        == "GAME_LEVEL"


def test_a_futures_market_is_futures_under_either_field():
    assert D.classify({"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_FUTURE"})[0] \
        == "FUTURES"
    assert D.classify({"sportsMarketType": "futures"})[0] == "FUTURES"


def test_a_game_shaped_slug_with_no_structured_field_is_not_identified():
    """THE RULE SECTION C EXISTS FOR. This slug is a real PMUS game-market
    shape. Without a structured type field the answer is NOT_IDENTIFIED, and
    any code that reads GAME_LEVEL off the slug has broken the section."""
    m = {"slug": "aec-nfl-ne-sea-2026-09-09",
         "question": "Seahawks vs Patriots moneyline",
         "title": "Moneyline"}
    assert D.classify(m)[0] == "NOT_IDENTIFIED"


def test_unspecified_is_not_identified_not_other():
    assert D.classify(
        {"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_UNSPECIFIED"})[0] \
        == "NOT_IDENTIFIED"


def test_an_unknown_structured_type_is_other_not_game():
    """A type we have never seen must not be silently promoted to GAME_LEVEL."""
    assert D.classify({"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_SOMETHING_NEW"})[0] \
        == "OTHER_STRUCTURED_SPORTS"


def test_classify_always_returns_a_reason():
    for m in ({"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_FUTURE"}, {}, {"x": 1}):
        assert D.classify(m)[1]


# ------------------------------------------------------------ near_touch
def test_near_touch_uses_the_markets_own_tick():
    """Same book, two tick sizes, different 1-tick bands. If the band were a
    hardcoded price distance this test could not pass."""
    b = body([lvl("0.50", "10"), lvl("0.49", "20"), lvl("0.45", "40")],
             [lvl("0.51", "10"), lvl("0.52", "20"), lvl("0.55", "40")])
    coarse = D.near_touch(b, "0.01")
    fine = D.near_touch(b, "0.001")
    assert coarse["cum_bid_qty_within_1t"] == "30"    # 0.50 and 0.49
    assert fine["cum_bid_qty_within_1t"] == "10"      # 0.50 only


def test_near_touch_reports_quantity_and_notional_for_both_sides():
    b = body([lvl("0.50", "10")], [lvl("0.60", "5")])
    nt = D.near_touch(b, "0.01")
    assert nt["best_bid_qty"] == "10"
    assert Decimal(nt["best_bid_notional"]) == Decimal("5.00")
    assert nt["best_ask_qty"] == "5"
    assert Decimal(nt["best_ask_notional"]) == Decimal("3.00")


def test_near_touch_bands_are_cumulative_and_nested():
    b = body([lvl("0.50", "1"), lvl("0.48", "2"), lvl("0.45", "4")],
             [lvl("0.51", "1"), lvl("0.53", "2"), lvl("0.56", "4")])
    nt = D.near_touch(b, "0.01")
    for side in ("bid", "ask"):
        one = Decimal(nt["cum_%s_qty_within_1t" % side])
        two = Decimal(nt["cum_%s_qty_within_2t" % side])
        five = Decimal(nt["cum_%s_qty_within_5t" % side])
        assert one <= two <= five


def test_near_touch_emits_no_whole_ladder_aggregate():
    """Phase 2C retracted whole-ladder dollar asymmetry. It must not come back
    through this function under any key."""
    nt = D.near_touch(body([lvl("0.50", "10")], [lvl("0.60", "5")]), "0.01")
    for k in nt:
        assert "depth_usd" not in k
        assert "ratio" not in k
        assert "asym" not in k


def test_near_touch_refuses_a_one_sided_book():
    assert D.near_touch(body([lvl("0.50", "10")], []), "0.01") is None
    assert D.near_touch(body([], [lvl("0.50", "10")]), "0.01") is None


def test_near_touch_refuses_to_guess_without_a_tick():
    b = body([lvl("0.50", "10")], [lvl("0.60", "5")])
    assert D.near_touch(b, None) is None
    assert D.near_touch(b, 0) is None


def test_near_touch_picks_the_real_touch_from_an_unsorted_ladder():
    """Wire order is not guaranteed. Best bid is the HIGHEST bid and best ask
    the LOWEST ask, whatever order the venue sent them in."""
    b = body([lvl("0.45", "40"), lvl("0.50", "10")],
             [lvl("0.60", "40"), lvl("0.55", "10")])
    nt = D.near_touch(b, "0.01")
    assert nt["best_bid_px"] == "0.50"
    assert nt["best_ask_px"] == "0.55"


# ---------------------------------------------------------------- budget
def test_budget_stops_at_the_disclosed_bound():
    b = D.Budget(3)
    for _ in range(3):
        b.take()
    try:
        b.take()
    except RuntimeError as exc:
        assert "DISCOVERY_BOUND_REACHED" in str(exc)
    else:
        raise AssertionError("budget let the run exceed its disclosed bound")


def test_budget_counts_every_request():
    b = D.Budget(10)
    for _ in range(4):
        b.take()
    assert b.spent == 4


# ----------------------------------------------------------- page_record
def test_page_record_counts_binary_eligibility_structurally():
    ev = [{"id": "1", "period": "NS", "markets": [
        {"slug": "a", "marketSides": [{"long": True}, {"long": False}]},
        {"slug": "b", "marketSides": [{"long": True}]},
        {"slug": "c"}]}]
    r = D.page_record({}, {"events": ev}, 0)
    assert r["binary_eligible_market_rows"] == 1
    assert r["unique_market_slugs"] == 3
    assert r["unique_native_event_ids"] == 1


def test_page_record_survives_an_empty_or_malformed_page():
    for b in ({}, {"events": []}, {"events": None}, None):
        r = D.page_record({}, b, 0)
        assert r["events_returned"] == 0
        assert r["market_rows"] == 0


def test_page_record_reports_the_id_range_numerically_not_lexically():
    """'9' sorts after '44391' as a string. The id range must be numeric or
    the pagination walk reads the wrong end of the list."""
    ev = [{"id": "9", "markets": []}, {"id": "44391", "markets": []}]
    r = D.page_record({}, {"events": ev}, 0)
    assert r["event_ids_min"] == "9"
    assert r["event_ids_max"] == "44391"


# -------------------------------------------------------------- scheduler
def test_the_round_robin_table_still_says_exact_5s_is_unreachable():
    """The retraction is about the SAFE RATE, not about round-robin. Under a
    uniform round-robin exact 5 s really is unreachable, and that half of the
    finding stands."""
    out = {}
    D.scheduler_analysis(out)
    rr = out["scheduler"]["round_robin"]
    assert all(not v["5"] if "5" in v else not v[5] for v in rr.values())


def test_the_scheduler_offers_a_feasible_exact_5s_plan():
    out = {}
    D.scheduler_analysis(out)
    plans = out["scheduler"]["exact_horizon_plans"]
    five = [p for p in plans if p["span_s"] == 5.0 and p["feasible"]]
    assert five, "no feasible exact-5s plan was produced"
    for p in five:
        assert p["rps"] <= 0.5 + 1e-9


def test_every_feasible_plan_stays_inside_the_rate_ceiling():
    out = {}
    D.scheduler_analysis(out)
    for p in out["scheduler"]["exact_horizon_plans"]:
        if p["feasible"]:
            assert p["rps"] <= 0.5 + 1e-9, p


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
        except Exception as exc:                       # noqa: BLE001
            bad.append((n, exc))
    for n, exc in bad:
        print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)
