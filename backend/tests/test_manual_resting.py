"""Desk resting orders (owner order 2026-08-28, venue parity).

A GTC limit ticket is money PROMISED to the book, so every pin here is
about the promise being counted, capped, and never double-spent — the
gates themselves (per-order cap, kill switch, daily budget, venue-named
side) are the same stack every manual order already passes.
"""

import inspect

import sportsassets.live_executor as lx
from sportsassets import pmus


def test_migration_adds_exactly_the_two_new_states():
    src = open("migrations/036_manual_resting.sql").read()
    assert "'open'" in src and "'cancelled'" in src
    for kept in ("'submitting'", "'filled'", "'unfilled'", "'rejected'",
                 "'error'", "'settled'", "'cashed_out'", "'exiting'"):
        assert kept in src, kept


def test_day_budget_counts_open_commitments():
    """A resting order consumes the 24h budget the moment it rests —
    otherwise $1000 of GTCs could ride beside a full day's fills."""
    src = inspect.getsource(lx._manual_day_spent)
    assert "'open'" in src and "'submitting'" in src
    assert "GREATEST" in src and "requested_usd" in src
    # and the FOK path spends from the same account
    assert "_manual_day_spent" in inspect.getsource(lx._execute_manual)
    assert "_manual_day_spent" in inspect.getsource(lx._execute_manual_limit)


def test_limit_ticket_rides_the_full_submit_safety_stack():
    """The GTC goes through submit_fok — venue-named side (or the
    ambiguous_side refusal), preview cost agreement, execution
    accounting — only the time-in-force differs."""
    src = inspect.getsource(lx._execute_manual_limit)
    assert "pmus.submit_fok" in src
    assert "TIME_IN_FORCE_GOOD_TILL_CANCEL" in src
    assert "MANUAL_MAX_PER_ORDER_USD" in src
    assert "_is_paused" in src
    assert "0.01 <= limit <= 0.99" in src


def test_cancel_records_a_raced_fill_never_erases_it():
    src = inspect.getsource(lx.cancel_manual_open)
    assert '"filled" if filled > 0 else "cancelled"' in src
    src2 = inspect.getsource(lx.sync_open_manual_orders)
    assert "AND status = 'open'" in src2, \
        "the sync's UPDATE is guarded so it can never regress a row " \
        "another path already settled"


def test_open_states_are_the_venues_working_states():
    assert "partially_filled" in lx._MANUAL_OPEN_STATES
    assert "new" in lx._MANUAL_OPEN_STATES
    assert "canceled" not in lx._MANUAL_OPEN_STATES


def test_norm_order_reads_the_venue_shape():
    row = pmus._norm_order({
        "id": "o1", "marketSlug": "aec-atp-x-y-2026-08-28",
        "intent": "ORDER_INTENT_BUY_LONG",
        "price": {"value": "0.44"}, "quantity": 100,
        "cumQuantity": 25, "leavesQuantity": 75,
        "state": "ORDER_STATE_PARTIALLY_FILLED",
        "avgPx": {"value": "0.43"},
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL"})
    assert row["order_id"] == "o1" and row["side"] == "BUY"
    assert row["price"] == 0.44 and row["filled_shares"] == 25
    assert row["state"] == "partially_filled"
    assert row["avg_px"] == 0.43 and row["tif"] == "GOOD_TILL_CANCEL"
    assert row["state"] in pmus.OPEN_ORDER_STATES
