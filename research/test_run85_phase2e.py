#!/usr/bin/env python3
"""Offline tests for Run 85 Phase 2E. Contacts nothing.

The load-bearing rules are the ones a wrong answer would let through silently:
the sport-aware state mapping (a token must not be read outside the family
that gives it meaning), the corroboration downgrade, the quantity-based queue
rule, and the taxonomy keeping three-way markets out of the binary bucket.

Run:  python3 -m pytest research/test_run85_phase2e.py -q
"""
from __future__ import annotations

import importlib.util
import sys
import time
from decimal import Decimal
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "p2e", Path(__file__).with_name("run85_phase2e.py"))
E = importlib.util.module_from_spec(_s)
_s.loader.exec_module(E)


def lvl(px, qty):
    return {"px": {"value": px, "currency": "USD"}, "qty": qty}


def body(bids, offers):
    return {"marketData": {"marketSlug": "m", "bids": bids, "offers": offers}}


# ------------------------------------------------------------- taxonomy
def test_three_way_is_its_own_class_not_binary():
    """DRAWABLE_OUTCOME is the soccer three-way. Folding it into GAME_BINARY
    would assert a binary structure the venue never stated."""
    assert E.classify({"sportsMarketTypeV2": E.THREE_WAY_V2}) == "GAME_THREE_WAY"
    assert E.classify(
        {"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE"}) == "GAME_BINARY"


def test_props_are_counted_but_are_not_game_level():
    assert E.classify({"sportsMarketTypeV2": E.PROP_V2}) == "GAME_PROP"


def test_a_game_shaped_slug_with_no_structured_field_is_not_identified():
    assert E.classify({"slug": "aec-nfl-ne-sea-2026-09-09",
                       "question": "moneyline"}) == "NOT_IDENTIFIED"


def test_the_phase2d_allowlist_is_kept_for_the_side_by_side():
    """2D's frozen list must stay exactly what it was, so the two counts can
    be reported honestly beside each other."""
    assert E.PHASE2D_ALLOWLIST == E.GAME_BINARY_V2
    assert E.THREE_WAY_V2 not in E.PHASE2D_ALLOWLIST


# --------------------------------------------------------- state mapping
def test_exact_tokens_map_in_any_sport():
    for tok, want in (("NS", "PREGAME"), ("FT", "ENDED"), ("SUSP", "SUSPENDED"),
                      ("CAN", "CANCELLED"), ("POST", "POSTPONED"),
                      ("LIVE", "LIVE"), ("Live", "LIVE")):
        assert E.normalize_state(tok, "nfl")[0] == want


def test_an_inning_token_is_live_only_in_baseball():
    """THE RULE THIS SECTION EXISTS FOR. 'IN8' means an inning in baseball.
    In soccer it means nothing established, so it must not be read as LIVE."""
    assert E.normalize_state("IN8", "mlb")[0] == "LIVE"
    assert E.normalize_state("IN8", "epl")[0] == "NOT_IDENTIFIED"
    assert E.normalize_state("IN8", "nfl")[0] == "NOT_IDENTIFIED"


def test_a_minute_token_is_live_only_in_soccer():
    assert E.normalize_state("63'", "epl")[0] == "LIVE"
    assert E.normalize_state("63'", "mlb")[0] == "NOT_IDENTIFIED"


def test_a_segment_token_is_live_only_in_esports():
    assert E.normalize_state("Map 1", "esports")[0] == "LIVE"
    assert E.normalize_state("Map 1", "nfl")[0] == "NOT_IDENTIFIED"


def test_empty_and_unknown_periods_are_not_identified():
    assert E.normalize_state("", "nfl")[0] == "NOT_IDENTIFIED"
    assert E.normalize_state(None, "nfl")[0] == "NOT_IDENTIFIED"
    assert E.normalize_state("WEIRD", "nfl")[0] == "NOT_IDENTIFIED"


def test_every_mapping_carries_a_justification():
    for tok in ("NS", "IN8", "63'", "", "WEIRD"):
        assert E.normalize_state(tok, "mlb")[1]


# ------------------------------------------------------- corroboration
def test_pregame_is_downgraded_when_the_start_time_has_passed():
    """The venue's own clock field checks our reading of its period string.
    A conflict must downgrade, never override."""
    now = time.time()
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7200))
    assert E.corroborate("PREGAME", past, now)[0] == "NOT_IDENTIFIED"


def test_live_is_downgraded_when_the_start_time_is_still_future():
    now = time.time()
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 7200))
    assert E.corroborate("LIVE", future, now)[0] == "NOT_IDENTIFIED"


def test_agreement_is_preserved():
    now = time.time()
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 7200))
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7200))
    assert E.corroborate("PREGAME", future, now)[0] == "PREGAME"
    assert E.corroborate("LIVE", past, now)[0] == "LIVE"


def test_corroboration_is_exact_at_the_boundary_not_an_hour_out():
    """A tight margin, deliberately. The earlier version of this check used a
    two-hour window and would have passed with a DST-sized error in the UTC
    conversion. Sixty seconds either side of kickoff cannot."""
    now = time.time()
    just_future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60))
    just_past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60))
    assert E.corroborate("PREGAME", just_future, now)[0] == "PREGAME"
    assert E.corroborate("PREGAME", just_past, now)[0] == "NOT_IDENTIFIED"
    assert E.corroborate("LIVE", just_past, now)[0] == "LIVE"
    assert E.corroborate("LIVE", just_future, now)[0] == "NOT_IDENTIFIED"


def test_a_missing_or_unparseable_start_time_leaves_the_reading_alone():
    now = time.time()
    assert E.corroborate("PREGAME", None, now)[0] == "PREGAME"
    assert E.corroborate("LIVE", "not-a-date", now)[0] == "LIVE"


def test_terminal_states_are_not_second_guessed_by_the_clock():
    """ENDED/CANCELLED/POSTPONED carry no implied ordering against kickoff."""
    now = time.time()
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 7200))
    for s in ("ENDED", "CANCELLED", "POSTPONED", "SUSPENDED"):
        assert E.corroborate(s, future, now)[0] == s


# ------------------------------------------------------------ near touch
def test_near_touch_bands_use_the_markets_own_tick():
    b = body([lvl("0.50", "10"), lvl("0.49", "20")],
             [lvl("0.51", "10"), lvl("0.52", "20")])
    coarse = E.near_touch(b, "0.01")
    fine = E.near_touch(b, "0.001")
    assert coarse["cum_bid_qty_1t"] == "30"
    assert fine["cum_bid_qty_1t"] == "10"


def test_zero_tick_band_is_the_touch_itself():
    b = body([lvl("0.50", "10"), lvl("0.49", "20")],
             [lvl("0.51", "10"), lvl("0.52", "20")])
    nt = E.near_touch(b, "0.01")
    assert nt["cum_bid_qty_0t"] == nt["touch_bid_qty"] == "10"
    assert nt["cum_ask_qty_0t"] == nt["touch_ask_qty"] == "10"


def test_bands_are_nested_and_cumulative():
    b = body([lvl("0.50", "1"), lvl("0.48", "2"), lvl("0.45", "4")],
             [lvl("0.51", "1"), lvl("0.53", "2"), lvl("0.56", "4")])
    nt = E.near_touch(b, "0.01")
    for side in ("bid", "ask"):
        v = [Decimal(nt["cum_%s_qty_%dt" % (side, k)]) for k in (0, 1, 2, 5)]
        assert v == sorted(v)


def test_near_touch_keeps_notional_but_the_rule_uses_quantity():
    b = body([lvl("0.50", "10")], [lvl("0.60", "5")])
    nt = E.near_touch(b, "0.01")
    assert Decimal(nt["touch_bid_notional"]) == Decimal("5.00")
    assert "cum_bid_notional_1t" in nt


def test_no_whole_ladder_asymmetry_key_is_emitted():
    nt = E.near_touch(body([lvl("0.50", "10")], [lvl("0.60", "5")]), "0.01")
    for k in nt:
        assert "depth_usd" not in k and "asym" not in k and "ratio" not in k


# ------------------------------------------------------------ queue rule
def test_queue_rule_is_quantity_based_and_ignores_price_level():
    """A book with equal QUANTITY one tick out is BALANCED even when the two
    sides sit at very different prices -- which is exactly where a dollar
    measure would have called it lopsided."""
    b = body([lvl("0.05", "100")], [lvl("0.95", "100")])
    nt = E.near_touch(b, "0.01")
    assert E.queue_class(nt)[0] == "BALANCED"


def test_queue_rule_thresholds():
    def mk(bq, aq):
        return E.near_touch(body([lvl("0.50", bq)], [lvl("0.51", aq)]), "0.01")
    assert E.queue_class(mk("100", "10"))[0] == "BID_HEAVY"
    assert E.queue_class(mk("10", "100"))[0] == "ASK_HEAVY"
    assert E.queue_class(mk("100", "100"))[0] == "BALANCED"
    assert E.queue_class(mk("100", "300"))[0] == "BALANCED"     # exactly 3x
    assert E.queue_class(mk("100", "301"))[0] == "ASK_HEAVY"


def test_queue_rule_refuses_a_one_sided_or_tickless_book():
    assert E.queue_class(None)[0] == "NOT_IDENTIFIED"
    assert E.near_touch(body([lvl("0.5", "1")], []), "0.01") is None
    assert E.near_touch(body([lvl("0.5", "1")], [lvl("0.6", "1")]), None) is None


# ------------------------------------------------------------- id hashes
def test_id_set_hash_is_order_independent_and_distinguishes_sets():
    a = E.id_set_hash({"3", "1", "2"})
    b = E.id_set_hash({"1", "2", "3"})
    c = E.id_set_hash({"1", "2", "4"})
    assert a == b and a != c


def test_page_stats_records_a_checkable_id_set_hash():
    ev = [{"id": "9", "markets": []}, {"id": "44391", "markets": []}]
    st = E.page_stats({}, {"events": ev}, 0)
    assert st["event_id_set_sha256"] == E.id_set_hash({"9", "44391"})
    assert st["event_id_min"] == "9" and st["event_id_max"] == "44391"


# --------------------------------------------------------------- budget
def test_budget_raises_rather_than_overspend():
    b = E.Budget(2)
    b.take()
    b.take()
    try:
        b.take()
    except RuntimeError as exc:
        assert "BOUND_REACHED" in str(exc)
    else:
        raise AssertionError("budget exceeded its disclosed bound")


# ------------------------------------------------------- scheduler plan
def test_the_scheduler_plan_shape_respects_the_rate_rule():
    """The offsets the driver uses must leave every scheduled pair at least
    the spacing floor apart, and the mean under the ceiling."""
    starts = [0.0, 2.5, 17.5]
    plan = sorted(s + h for s in starts for h in (0.0,) + E.HORIZONS)
    gaps = [plan[i + 1] - plan[i] for i in range(len(plan) - 1)]
    span = plan[-1] - plan[0]
    assert min(gaps) >= E.MIN_REQUEST_GAP_S
    assert len(plan) / span <= E.RATE_CEILING_RPS
    assert len(plan) <= E.MAX_SCHEDULER_READS


def test_exact_horizons_are_whole_numbers_of_seconds_not_a_lattice():
    """5 s is in the target set precisely because the scheduler is not bound
    to a 2 s grid -- the retraction this phase carries forward."""
    assert 5.0 in E.HORIZONS
    assert 5.0 % E.MIN_REQUEST_GAP_S != 0


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
