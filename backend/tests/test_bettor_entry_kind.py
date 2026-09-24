"""ENTRY CLASSIFICATION, and the invention it exists to prevent.

The failure it replaces is specific: with no classification, every buy
read as a new entry, so an account that entered once and added four
times looked like five positions. The tests below pin each distinction
and, more importantly, pin the cases where the honest answer is UNKNOWN.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from sportsassets import bettor_entry_kind as EK               # noqa: E402


def f(ts, oi, side, size, price=0.5, det=None, tid=None):
    return EK.Fill(source_ts=ts, detected_ts=det if det is not None
                   else ts + 2.0, outcome_index=oi, side=side,
                   size=size, price=price, trade_id=tid)


def kinds(fills, **kw):
    return [c.kind for c in EK.classify_condition(fills, **kw)]


# ── the four distinctions that were missing ──────────────────────────

def test_a_first_buy_from_flat_is_an_initial_entry():
    assert kinds([f(100, 0, "BUY", 50)]) == [EK.INITIAL_ENTRY]


def test_buying_more_of_the_same_leg_is_an_ADDITION_not_a_new_entry():
    """THE CONFLATION THIS ENDS. Five buys of one leg is ONE position
    with four additions, not five positions."""
    fills = [f(100, 0, "BUY", 50), f(200, 0, "BUY", 25),
             f(300, 0, "BUY", 25), f(400, 0, "BUY", 10),
             f(500, 0, "BUY", 10)]
    k = kinds(fills)
    assert k == [EK.INITIAL_ENTRY] + [EK.ADDITION] * 4
    assert k.count(EK.INITIAL_ENTRY) == 1


def test_buying_the_other_leg_while_long_one_is_a_PAIR_COMPLETION():
    fills = [f(100, 0, "BUY", 50), f(200, 1, "BUY", 50)]
    assert kinds(fills) == [EK.INITIAL_ENTRY, EK.PAIR_COMPLETION]


def test_selling_part_is_a_REDUCTION_and_selling_out_is_an_EXIT():
    fills = [f(100, 0, "BUY", 50), f(200, 0, "SELL", 20),
             f(300, 0, "SELL", 30)]
    assert kinds(fills) == [EK.INITIAL_ENTRY, EK.REDUCTION, EK.EXIT]


def test_a_later_buy_after_a_full_exit_is_a_new_INITIAL_ENTRY():
    """CONTROL on the ADDITION rule: it must key on the position held,
    not on whether any buy happened earlier."""
    fills = [f(100, 0, "BUY", 50), f(200, 0, "SELL", 50),
             f(300, 0, "BUY", 40)]
    assert kinds(fills) == [EK.INITIAL_ENTRY, EK.EXIT, EK.INITIAL_ENTRY]


# ── UNKNOWN, which is the point ──────────────────────────────────────

def test_a_first_observed_SELL_is_UNKNOWN_not_an_exit_from_nothing():
    """They held inventory before we were watching. Its size and cost
    are not recoverable, and pretending otherwise would seed a position
    that never existed."""
    out = EK.classify_condition([f(100, 0, "SELL", 30)])
    assert out[0].kind == EK.UNKNOWN
    assert out[0].unknown_reason == EK.U_FIRST_IS_SELL
    assert out[0].position_is_a_lower_bound is True


def test_selling_more_than_we_ever_saw_bought_is_UNKNOWN():
    fills = [f(100, 0, "BUY", 20), f(200, 0, "SELL", 50)]
    out = EK.classify_condition(fills)
    assert out[0].kind == EK.INITIAL_ENTRY
    assert out[1].kind == EK.UNKNOWN
    assert out[1].unknown_reason == EK.U_OVERSOLD


def test_UNKNOWN_is_STICKY_over_the_rest_of_the_condition():
    """THE SUBTLE ONE. Once the position is known to be a lower bound,
    later arithmetic cannot recover confidence -- so a later buy must not
    come back as a clean ADDITION computed from a position we know to be
    wrong."""
    fills = [f(100, 0, "BUY", 20), f(200, 0, "SELL", 50),
             f(300, 0, "BUY", 10), f(400, 0, "SELL", 5)]
    k = kinds(fills)
    assert k == [EK.INITIAL_ENTRY, EK.UNKNOWN, EK.UNKNOWN, EK.UNKNOWN]
    out = EK.classify_condition(fills)
    assert out[2].unknown_reason == EK.U_TAINTED
    assert all(c.position_is_a_lower_bound for c in out[1:])


def test_a_missing_outcome_index_is_UNKNOWN_and_taints_the_condition():
    fills = [f(100, None, "BUY", 20), f(200, 0, "BUY", 10)]
    out = EK.classify_condition(fills)
    assert out[0].unknown_reason == EK.U_NO_LEG
    assert out[1].kind == EK.UNKNOWN


def test_a_first_fill_at_the_edge_of_our_window_is_UNKNOWN():
    """'Flat beforehand' is an observation only if we were watching. At
    the very start of everything we ever saw from an account, it is an
    assumption about the edge of the window."""
    out = EK.classify_condition([f(1000, 0, "BUY", 50)],
                                account_first_seen_ts=990.0)
    assert out[0].kind == EK.UNKNOWN
    assert out[0].unknown_reason == EK.U_WINDOW_EDGE
    # CONTROL: comfortably inside the window it classifies cleanly.
    ok = EK.classify_condition([f(1000, 0, "BUY", 50)],
                               account_first_seen_ts=100.0)
    assert ok[0].kind == EK.INITIAL_ENTRY


# ── ordering, clocks and the census ──────────────────────────────────

def test_fills_are_classified_in_SOURCE_order_whatever_order_they_arrive():
    """Out-of-order input would otherwise make an addition look like an
    entry and vice versa."""
    late = f(300, 0, "BUY", 25)
    early = f(100, 0, "BUY", 50)
    assert kinds([late, early]) == [EK.INITIAL_ENTRY, EK.ADDITION]


def test_both_clocks_survive_onto_every_result():
    out = EK.classify_condition([f(100, 0, "BUY", 50, det=107.5)])
    assert out[0].source_ts == 100
    assert out[0].detected_ts == 107.5
    assert out[0].detected_ts > out[0].source_ts, (
        "detection cannot precede the event; a replay that decided at "
        "source_ts would grant itself the detection latency for free")


def test_the_census_reports_the_unknown_fraction_rather_than_hiding_it():
    fills = [f(100, 0, "BUY", 20), f(200, 0, "SELL", 50),
             f(300, 0, "BUY", 10)]
    c = EK.census(EK.classify_condition(fills))
    assert c["by_kind"][EK.UNKNOWN] == 2
    assert abs(c["unknown_fraction"] - 2 / 3) < 1e-9
    assert c["by_unknown_reason"][EK.U_OVERSOLD] == 1
    assert c["by_unknown_reason"][EK.U_TAINTED] == 1


def test_the_position_before_and_after_are_both_carried():
    out = EK.classify_condition([f(100, 0, "BUY", 50),
                                 f(200, 0, "BUY", 25)])
    assert out[1].position_before == {0: 50.0}
    assert out[1].position_after == {0: 75.0}
