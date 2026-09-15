#!/usr/bin/env python3
"""Offline tests for Run 85 Phase 2G-R. Contacts nothing.

2G-R exists to repair two defects, so the tests are aimed squarely at them:
concentration measured against the FINAL cohort, and a selector whose ordering
makes balance emergent rather than bolted on.

Run:  python3 -m pytest research/test_run85_phase2gr.py -q
"""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "p2gr", Path(__file__).with_name("run85_phase2gr.py"))
R = importlib.util.module_from_spec(_s); _s.loader.exec_module(R)


def ev(eid, sport, band="P_MID", spread="S_1T", tick="0.01",
       state="PREGAME", league=None, slug=None):
    return str(eid), {
        "native_event_id": str(eid), "native_event_slug": "e%s" % eid,
        "sport": sport, "sport_bucket": sport, "league": [league] if league else [],
        "raw_period": "NS", "normalized_state": state, "state_justification": "t",
        "event_start_time": None,
        "candidates": [{"market_slug": slug or "m%s" % eid,
                        "sports_market_type_v2": "SPORTS_MARKET_TYPE_MONEYLINE",
                        "tick_size": tick, "best_bid": "0.50", "best_ask": "0.51",
                        "mid": "0.505", "spread": "0.01", "spread_bucket": spread,
                        "price_band": band, "fee_coefficient_field": 0.06,
                        "scheduled_game_start": None, "end_date": None}]}


def frame(*e): return dict(e)


# --------------------------------------------- max_balanced_size arithmetic
def test_max_balanced_size_when_no_sport_dominates():
    assert R.max_balanced_size([3, 3, 2]) == 8


def test_max_balanced_size_when_one_sport_swamps_the_rest():
    """14 NFL and 1 boxing: you can only draw 1+1 without exceeding half."""
    assert R.max_balanced_size([14, 1]) == 2


def test_max_balanced_size_of_a_single_sport_universe_is_zero():
    assert R.max_balanced_size([20]) == 0
    assert R.max_balanced_size([]) == 0


# ------------------------------------------------ the selector balances
def test_the_2g_failure_case_now_balances():
    """THE DEFECT, pinned. 20 NFL against 2 others gave 2G a 4/5 = 80% NFL
    cohort. Round-robin over SPORTS first must not."""
    f = frame(*([ev(i, "nfl") for i in range(1, 21)]
                + [ev(100, "soccer"), ev(101, "mlb"), ev(102, "soccer"),
                   ev(103, "mlb"), ev(104, "boxing"), ev(105, "boxing")]))
    c = R.select_cohort(f, cap=8)
    counts, share = R.concentration(c, "sport_bucket")
    assert len(c) == 8
    assert share <= 0.5 + 1e-9, counts


def test_a_sport_with_many_strata_cannot_buy_extra_slots():
    """2G round-robined over strata, so a sport spread across many strata won
    many slots. Here NFL has 6 distinct strata and soccer 1."""
    evs = []
    for i, (b, s) in enumerate([("P_LOW", "S_1T"), ("P_MID", "S_1T"),
                                ("P_HIGH", "S_1T"), ("P_LOW", "S_2_3T"),
                                ("P_MID", "S_2_3T"), ("P_HIGH", "S_2_3T")]):
        evs.append(ev(10 + i, "nfl", band=b, spread=s))
    evs += [ev(50, "soccer"), ev(51, "soccer"), ev(52, "soccer"), ev(53, "soccer")]
    c = R.select_cohort(frame(*evs), cap=8)
    counts, share = R.concentration(c, "sport_bucket")
    assert share <= 0.5 + 1e-9, counts


def test_selection_is_deterministic_under_reordering():
    evs = [ev(i, ["nfl", "soccer", "mlb"][i % 3]) for i in range(1, 16)]
    a = R.select_cohort(dict(evs), cap=8)
    b = R.select_cohort(dict(reversed(evs)), cap=8)
    assert [x["market_slug"] for x in a] == [x["market_slug"] for x in b]


def test_ordering_is_by_event_id_not_slug():
    f = frame(ev(1, "nfl", slug="zzz"), ev(2, "nfl", slug="aaa"))
    assert R.select_cohort(f, cap=1)[0]["market_slug"] == "zzz"


def test_one_market_per_native_event():
    f = frame(*[ev(i, ["nfl", "mlb"][i % 2]) for i in range(1, 9)])
    c = R.select_cohort(f, cap=8)
    assert len({x["native_event_id"] for x in c}) == len(c)


def test_a_null_sport_is_never_selected():
    f = frame(ev(1, None), ev(2, "nfl"), ev(3, "mlb"))
    assert all(x["sport_bucket"] for x in R.select_cohort(f, cap=8))


# ------------------------------------------------------ concentration gate
def test_gate_fails_a_concentrated_cohort_the_universe_could_have_balanced():
    f = frame(*([ev(i, "nfl") for i in range(1, 9)]
                + [ev(50 + i, "soccer") for i in range(6)]))
    bad = [dict(f[str(i)], **f[str(i)]["candidates"][0],
                league_label="nfl") for i in range(1, 6)]
    ok, target, res, avail = R.score_cohort(bad, f)
    assert res["sport_concentration"] == "FAIL"
    assert not ok


def test_gate_waives_only_when_the_universe_truly_cannot_balance():
    """14 NFL and 1 boxing: max balanced size is 2, below the gate floor of 4,
    so concentration is WAIVED_BY_UNIVERSE_CONCENTRATION -- never a silent
    PASS, and the waiver name says why."""
    f = frame(*([ev(i, "nfl") for i in range(1, 15)] + [ev(99, "boxing")]))
    c = R.select_cohort(f, cap=8)
    ok, target, res, avail = R.score_cohort(c, f)
    assert avail["max_balanced_size"] == 2
    assert res["sport_concentration"] == "WAIVED_BY_UNIVERSE_CONCENTRATION"


def test_a_waiver_is_textually_distinct_from_a_pass():
    assert "WAIVED" in "WAIVED_BY_UNIVERSE_CONCENTRATION"
    assert "WAIVED_BY_UNIVERSE_CONCENTRATION" != "PASS"


def test_league_concentration_is_gated_too():
    """Two sport labels can still be one league family."""
    f = frame(*([ev(i, "nfl", league="nfl") for i in range(1, 9)]
                + [ev(50 + i, "soccer", league="nfl") for i in range(6)]))
    c = [dict(f[k], **f[k]["candidates"][0], league_label="nfl")
         for k in list(f)[:6]]
    ok, target, res, avail = R.score_cohort(c, f)
    assert avail["max_league_share"] == 1.0
    assert res["league_concentration"] in ("FAIL", "WAIVED_BY_UNIVERSE_CONCENTRATION")


def test_a_small_cohort_cannot_pass_concentration():
    f = frame(ev(1, "nfl"), ev(2, "soccer"))
    c = R.select_cohort(f, cap=8)
    ok, target, res, _ = R.score_cohort(c, f)
    assert res["size"] == "FAIL" and not ok


# --------------------------------------------------------- target quality
def test_target_quality_requires_eight_not_merely_a_pass():
    f = frame(*([ev(i, "nfl", band="P_LOW") for i in range(1, 4)]
                + [ev(20 + i, "soccer", band="P_MID") for i in range(3)]))
    c = R.select_cohort(f, cap=8)
    ok, target, res, _ = R.score_cohort(c, f)
    assert len(c) == 6
    assert ok is True and target is False, (ok, target, len(c))


def test_target_quality_true_at_eight_with_balance_and_bands():
    f = frame(*([ev(i, "nfl", band="P_LOW") for i in range(1, 6)]
                + [ev(20 + i, "soccer", band="P_MID") for i in range(5)]))
    c = R.select_cohort(f, cap=8)
    ok, target, res, avail = R.score_cohort(c, f)
    assert len(c) == 8 and avail["max_sport_share"] <= 0.5
    assert target is True, res


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try: f()
        except Exception as exc: bad.append((n, exc))
    for n, exc in bad: print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)
