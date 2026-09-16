#!/usr/bin/env python3
"""Offline tests for Run 85 Phase 2G. Contacts nothing.

The one thing 2G exists to fix is the stop condition, so the tests are about
the selector and the gate: anti-domination, determinism, one-per-event, a null
tag never counting as a sport, and WAIVED_BY_ABSENCE never masking a real FAIL.

Run:  python3 -m pytest research/test_run85_phase2g.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "p2g", Path(__file__).with_name("run85_phase2g.py"))
G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(G)


def ev(eid, sport, band="P_MID", spread="S_1T", tick="0.01",
       state="PREGAME", slug=None):
    return str(eid), {
        "native_event_id": str(eid), "native_event_slug": "e%s" % eid,
        "sport": sport, "sport_bucket": sport, "league": [],
        "raw_period": "NS", "normalized_state": state,
        "state_justification": "t", "event_start_time": None,
        "candidates": [{"market_slug": slug or "m%s" % eid,
                        "sports_market_type_v2": "SPORTS_MARKET_TYPE_MONEYLINE",
                        "tick_size": tick, "best_bid": "0.50",
                        "best_ask": "0.51", "mid": "0.505", "spread": "0.01",
                        "spread_bucket": spread, "price_band": band,
                        "fee_coefficient_field": 0.06,
                        "scheduled_game_start": None, "end_date": None}]}


def frame(*evs):
    return dict(evs)


# ------------------------------------------------------- anti-domination
def test_no_sport_may_exceed_half_the_cohort():
    """THE 2F FAILURE, pinned. Twenty NFL events and two others: 2F's selector
    returned 7 of 8 NFL. S4 caps any one sport at ceil(cap/2)."""
    f = frame(*([ev(i, "nfl") for i in range(1, 21)]
                + [ev(100, "soccer"), ev(101, "other")]))
    c = G.select_cohort(f, cap=8)
    by = {}
    for x in c:
        by[x["sport_bucket"]] = by.get(x["sport_bucket"], 0) + 1
    assert by.get("nfl", 0) <= 4, by
    assert len(by) >= 2, by


def test_a_single_sport_universe_still_selects_up_to_the_per_sport_cap():
    f = frame(*[ev(i, "nfl") for i in range(1, 21)])
    c = G.select_cohort(f, cap=8)
    assert len(c) == 4          # ceil(8/2); it cannot fill past the cap


# ------------------------------------------------------------ determinism
def test_selection_is_deterministic_under_input_reordering():
    evs = [ev(i, ["nfl", "soccer", "mlb", "other"][i % 4]) for i in range(1, 13)]
    a = G.select_cohort(dict(evs), cap=6)
    b = G.select_cohort(dict(reversed(evs)), cap=6)
    assert [x["market_slug"] for x in a] == [x["market_slug"] for x in b]


def test_selection_never_orders_by_slug():
    """Event 1 carries a slug that sorts last; it must still be taken first,
    because ordering is by native event id."""
    f = frame(ev(1, "nfl", slug="zzz"), ev(2, "nfl", slug="aaa"))
    c = G.select_cohort(f, cap=1)
    assert c[0]["market_slug"] == "zzz"


def test_one_market_per_native_event():
    f = frame(ev(1, "nfl"), ev(2, "soccer"))
    c = G.select_cohort(f, cap=8)
    assert len({x["native_event_id"] for x in c}) == len(c)


def test_a_null_sport_bucket_is_never_selected():
    f = frame(ev(1, None), ev(2, "nfl"))
    c = G.select_cohort(f, cap=8)
    assert all(x["sport_bucket"] for x in c)
    assert {x["native_event_id"] for x in c} == {"2"}


# ------------------------------------------------------------- the gate
def test_gate_fails_a_single_sport_cohort_when_the_frame_had_more():
    f = frame(*([ev(i, "nfl") for i in range(1, 6)] + [ev(90, "soccer")]))
    cohort = [dict(f["1"], **f["1"]["candidates"][0]),
              dict(f["2"], **f["2"]["candidates"][0]),
              dict(f["3"], **f["3"]["candidates"][0]),
              dict(f["4"], **f["4"]["candidates"][0])]
    ok, res, _ = G.score_cohort(cohort, f)
    assert res["sport"] == "FAIL"
    assert not ok


def test_a_dimension_the_frame_cannot_vary_is_waived_not_passed():
    """WAIVED_BY_ABSENCE must be distinguishable from PASS in the record."""
    f = frame(ev(1, "nfl", tick="0.01"), ev(2, "soccer", tick="0.01"))
    cohort = [dict(f["1"], **f["1"]["candidates"][0]),
              dict(f["2"], **f["2"]["candidates"][0])]
    ok, res, avail = G.score_cohort(cohort, f)
    assert res["tick"] == "WAIVED_BY_ABSENCE"
    assert avail["frame_ticks"] == ["0.01"]


def test_a_waiver_never_masks_a_real_failure():
    """Tick is waived, but the single-band cohort still FAILs on price band."""
    f = frame(ev(1, "nfl", band="P_MID"), ev(2, "soccer", band="P_LOW"),
              ev(3, "mlb", band="P_MID"), ev(4, "other", band="P_MID"))
    cohort = [dict(f[k], **f[k]["candidates"][0]) for k in ("1", "3", "4")]
    ok, res, _ = G.score_cohort(cohort, f)
    assert res["tick"] == "WAIVED_BY_ABSENCE"
    assert res["price_band"] == "FAIL"
    assert not ok


def test_live_is_never_forced_when_the_frame_has_none():
    f = frame(ev(1, "nfl", band="P_LOW"), ev(2, "soccer", band="P_MID"),
              ev(3, "mlb", band="P_HIGH"), ev(4, "other", band="P_LOW"))
    cohort = [dict(f[k], **f[k]["candidates"][0]) for k in ("1", "2", "3", "4")]
    ok, res, _ = G.score_cohort(cohort, f)
    assert res["live_state"] == "WAIVED_BY_ABSENCE"
    assert ok


def test_live_is_required_when_the_frame_actually_has_it():
    f = frame(ev(1, "nfl", band="P_LOW"), ev(2, "soccer", band="P_MID"),
              ev(3, "mlb", band="P_HIGH"), ev(4, "other", band="P_LOW"),
              ev(9, "nfl", band="P_MID", state="LIVE"))
    cohort = [dict(f[k], **f[k]["candidates"][0]) for k in ("1", "2", "3", "4")]
    ok, res, _ = G.score_cohort(cohort, f)
    assert res["live_state"] == "FAIL"
    assert not ok


def test_a_tiny_cohort_cannot_pass_on_technicalities():
    f = frame(ev(1, "nfl", band="P_LOW"), ev(2, "soccer", band="P_MID"))
    cohort = [dict(f[k], **f[k]["candidates"][0]) for k in ("1", "2")]
    ok, res, _ = G.score_cohort(cohort, f)
    assert res["size"] == "FAIL"
    assert not ok


# ------------------------------------------------------------- helpers
def test_spread_bucket_uses_the_markets_own_tick():
    from decimal import Decimal
    assert G.spread_bucket(Decimal("0.01"), "0.01") == "S_1T"
    assert G.spread_bucket(Decimal("0.01"), "0.005") == "S_2_3T"
    assert G.spread_bucket(Decimal("0.05"), "0.005") == "S_4T_PLUS"
    assert G.spread_bucket(None, "0.01") is None
    assert G.spread_bucket(Decimal("0.01"), None) is None


def test_the_stop_condition_is_the_selected_cohort_not_the_frame():
    """A frame rich in every dimension whose SELECTION is still one-sport must
    not pass. This is the whole point of 2G."""
    f = frame(*([ev(i, "nfl", band=("P_LOW" if i % 2 else "P_HIGH"))
                 for i in range(1, 30)]))
    c = G.select_cohort(f, cap=8)
    ok, res, _ = G.score_cohort(c, f)
    assert {x["sport_bucket"] for x in c} == {"nfl"}
    assert res["sport"] == "WAIVED_BY_ABSENCE"   # frame truly has one sport
    # and with a second sport present, a one-sport cohort would FAIL instead
    f2 = dict(f)
    k, v = ev(500, "soccer")
    f2[k] = v
    ok2, res2, _ = G.score_cohort(c, f2)
    assert res2["sport"] == "FAIL"
    assert not ok2


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
