"""THE COMPLETE POLICY: pairing AND the second-half loss exit.

Pairing alone is not acceptance. These tests drive the §2 loss trigger
through the SAME application path the experiment uses -- the real
`bettor_mgmt_lifecycle.Managed` over `bettor_desk.Order`/`Portfolio`, the
real `bettor_rn1x_policy` -- and cover the four things a loss exit has to
get right beyond firing at all: cancellation acknowledgement, a fill that
lands late, the inventory left behind, and whether the book still adds up.

EVERY TEST HERE IS A CONTROLLED TEST AND SAYS SO.

The event-progress observation and the executable bid/depth are SUPPLIED
BY THE TEST. Neither is available in production: no progress feed is
connected (only the CLOB market record's `game_start_time`, a scheduled
start instant), and no contemporaneous book is retained for these
instants. So these demonstrate that the MECHANISM is implemented and
correct; they are not observed opportunities and they are not evidence
that the loss exit has ever fired on real evidence. It has not.

`PROGRESS_FEED_CONNECTED` is monkeypatched to admit one sport, because
that registry is empty by construction in production and admitting a
sport for real requires a feed, not a test.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_desk as DK
from sportsassets import bettor_mgmt_lifecycle as lc
from sportsassets import bettor_rn1x_policy as pol

# The frozen schedule's taker side, as the experiment books it.
THETA_TAKER = 0.0695


def fee(qty, price, maker=False):
    """The production shape: theta * C * p * (1-p), taker side."""
    q, p = float(qty), float(price)
    return THETA_TAKER * q * p * (1.0 - p)


SPORT = "soccer"
SECOND_HALF = {"observed_at": 1_000.0, "period": 2, "period_type": "HALF",
               "in_play": True}
FIRST_HALF = {"observed_at": 500.0, "period": 1, "period_type": "HALF",
              "in_play": True}


@pytest.fixture
def admitted(monkeypatch):
    """Admit ONE sport, the way a connected feed would.

    Patching both registries rather than one, because
    `SECOND_HALF_MAPPING` is computed as the intersection at import.
    """
    rule = pol.DOCUMENTED_MAPPINGS[SPORT]
    monkeypatch.setattr(pol, "PROGRESS_FEED_CONNECTED", {SPORT: "TEST_FEED"})
    monkeypatch.setattr(pol, "SECOND_HALF_MAPPING", {SPORT: rule})
    return SPORT


def _managed(qty=100.0, price=0.57, at=0.0):
    return lc.Managed(condition_id="c-complete", outcome_index=0,
                      seed_qty=qty, seed_price=price, at=at, fee_fn=fee,
                      queue_share=0.25, expiry_s=900.0, account="cp")


# ── the arithmetic management confirmed ─────────────────────────────
def test_the_gross_and_net_trigger_levels_are_reported_separately(admitted):
    """0.4788 is 84% OF COST. It is NOT where the rule fires.

    Management confirmed 0.4788 (and declined 0.41, which is 16 POINTS of
    the $1 payout rather than 16% of cost). But §2 tests NET proceeds
    "including fees", so the exit actually triggers at a HIGHER bid --
    1.74 cents higher on a 0.57 basis, because the exit fee has to come
    out of the proceeds. Reporting only the gross figure understated the
    exposure: a reader would expect the exit at 0.4788 when it fires at
    0.49.
    """
    stop = pol.loss_trigger(allocated_cost_usd=57.0, qty=100.0,
                            bid=0.4788, bid_size=100.0, fee_fn=fee)
    assert stop["trigger_fraction"] == 0.84
    assert stop["fired"] is True, stop

    # the gross level is management's confirmed reading, unchanged
    assert abs(stop["trigger_level_bid_gross"] - 0.4788) < 5e-3, stop
    # the operative level is higher, and they are not the same number
    assert stop["trigger_level_bid"] > stop["trigger_level_bid_gross"], stop
    assert stop["trigger_level_bid"] == 0.49, stop
    # the declined 0.41 reading is below both
    assert 0.41 < stop["trigger_level_bid_gross"]


def test_the_reported_trigger_level_is_the_exact_firing_boundary(admitted):
    """At the level it fires; one tick above it does not.

    Without this the level could be off by a tick in either direction and
    every other test here would still pass.
    """
    stop = pol.loss_trigger(allocated_cost_usd=57.0, qty=100.0,
                            bid=0.47, bid_size=100.0, fee_fn=fee)
    level = stop["trigger_level_bid"]
    at = pol.loss_trigger(allocated_cost_usd=57.0, qty=100.0, bid=level,
                          bid_size=100.0, fee_fn=fee)
    above = pol.loss_trigger(allocated_cost_usd=57.0, qty=100.0,
                             bid=round(level + pol.TICK, 2),
                             bid_size=100.0, fee_fn=fee)
    assert at["fired"] is True, at
    assert above["fired"] is False, above


def test_a_bid_above_the_trigger_does_not_fire(admitted):
    stop = pol.loss_trigger(allocated_cost_usd=57.0, qty=100.0,
                            bid=0.55, bid_size=100.0, fee_fn=fee)
    assert stop["fired"] is False, stop
    assert stop["proceeds_over_cost"] > 0.84


def test_no_bid_and_no_depth_are_refusals_not_zeros(admitted):
    """A book we cannot read is not a reason to sell."""
    for kw in ({"bid": None, "bid_size": 100.0},
               {"bid": 0.30, "bid_size": 0.0}):
        stop = pol.loss_trigger(allocated_cost_usd=57.0, qty=100.0,
                               fee_fn=fee, **kw)
        assert stop["fired"] is False, kw
        assert stop["status"] == pol.NOT_IDENTIFIED
        assert stop["blocker"] in ("NO_EXECUTABLE_BID",
                                   "NO_EXECUTABLE_DEPTH"), stop


# ── the phase gate, through the lifecycle ───────────────────────────
def test_the_first_half_never_exits_however_bad_the_bid(admitted):
    """§2 is second half ONLY. A worse bid does not buy an exception."""
    m = _managed()
    d = m.manage_policy(at=10.0, decision_id="d1", sport=SPORT,
                        progress=FIRST_HALF, bid=0.20, bid_size=500.0)
    assert d["phase"]["phase"] == pol.FIRST_HALF
    assert d["phase"]["loss_exit_available"] is False
    assert d.get("selected_action") != "DIRECT_EXIT", d


def test_an_unadmitted_sport_is_excluded_from_the_complete_policy():
    """No monkeypatch here: production's real state.

    The exposure is retained visibly rather than exited on a guess, and
    the row says which absence applies.
    """
    m = _managed()
    d = m.manage_policy(at=10.0, decision_id="d2", sport="cricket",
                        progress=SECOND_HALF, bid=0.20, bid_size=500.0)
    ph = d["phase"]
    assert ph["loss_exit_available"] is False
    assert ph["admitted_to_complete_policy"] is False
    assert ph["absence"] in (pol.NO_RULE_WRITTEN, pol.PROGRESS_FEED_ABSENT)
    assert d.get("selected_action") != "DIRECT_EXIT", d


def test_a_stoppage_is_not_a_phase(admitted):
    m = _managed()
    d = m.manage_policy(at=10.0, decision_id="d3", sport=SPORT,
                        progress={**SECOND_HALF, "in_play": False},
                        bid=0.20, bid_size=500.0)
    assert d["phase"]["absence"] == "EVENT_NOT_IN_PLAY"
    assert d["phase"]["loss_exit_available"] is False


# ── THE EXIT, END TO END ────────────────────────────────────────────
def test_the_loss_exit_fires_and_places_one_sell(admitted):
    m = _managed()
    d = m.manage_policy(at=10.0, decision_id="exit-1", sport=SPORT,
                        progress=SECOND_HALF, bid=0.47, bid_size=100.0)
    assert d["phase"]["phase"] == pol.SECOND_HALF
    assert d["loss_trigger"]["fired"] is True, d["loss_trigger"]
    assert d["selected_action"] == "DIRECT_EXIT", d
    assert d["acted"] is True, d

    open_orders = m.open_orders()
    assert len(open_orders) == 1, [o.to_dict() for o in open_orders]
    o = open_orders[0]
    assert o.side == "SELL"
    assert o.outcome_index == 0            # the leg we hold
    assert o.limit_price == 0.47
    # THE TRIGGER PRICE IS NOT THE EXECUTION PRICE, and both are reported.
    assert "trigger_level_bid" in d["loss_trigger"]
    assert d["loss_trigger"]["trigger_price_is_not_execution_price"]


def test_depth_caps_the_sell_and_leaves_the_rest_as_inventory(admitted):
    """A depth-limited exit is not a full exit, and must not read as one."""
    m = _managed(qty=100.0)
    d = m.manage_policy(at=10.0, decision_id="exit-2", sport=SPORT,
                        progress=SECOND_HALF, bid=0.47, bid_size=40.0)
    assert d["loss_trigger"]["fired"] is True
    assert d["loss_trigger"]["depth_limited"] is True
    assert d["loss_trigger"]["sellable_qty"] == 40.0
    o = m.open_orders()[0]
    assert o.qty == 40.0, o.to_dict()
    # 60 remain held and unpaired
    assert m.residual() == pytest.approx(100.0)   # nothing filled yet
    assert m.held(0) == pytest.approx(100.0)


def test_the_exit_fills_and_the_book_reconciles(admitted):
    m = _managed(qty=100.0)
    m.manage_policy(at=10.0, decision_id="exit-3", sport=SPORT,
                    progress=SECOND_HALF, bid=0.47, bid_size=100.0)
    # An OBSERVED print at or above our limit, large enough that our
    # queue share covers the order.
    fills = m.on_print(at=20.0, outcome_index=0, price=0.47, size=1000.0,
                       evidence_id="print-1")
    assert fills, "the resting sell did not consume a crossing print"
    got = sum(f["qty"] for f in fills)
    assert got == pytest.approx(100.0), fills

    st = m.state()
    assert st["invariant"]["ok"] is True, st["invariant"]
    assert m.held(0) == pytest.approx(0.0)
    assert st["residual_qty"] == pytest.approx(0.0)
    # A REALISED LOSS, and it is not 16%. The trigger is a level; what a
    # sale achieves is a separate number.
    realized = st["portfolio"]["realized_pnl_usd"]
    assert realized < 0.0, st["portfolio"]
    loss_frac = -realized / 57.0
    assert 0.15 < loss_frac < 0.30, (
        "a 16%% trigger does not guarantee a 16%% realised loss; measured "
        "%.4f" % loss_frac)


def test_a_fill_landing_while_the_cancel_is_pending_is_booked(admitted):
    """THE CANCEL/FILL RACE. An order in CANCEL_PENDING can still fill.

    Ignoring it would under-report inventory and the book would not
    reconcile. `place` returns WAIT_CANCEL_ACK rather than replacing an
    incumbent, so nothing is sized against inventory that a late fill is
    about to move.
    """
    m = _managed(qty=100.0)
    m.manage_policy(at=10.0, decision_id="race-1", sport=SPORT,
                    progress=SECOND_HALF, bid=0.47, bid_size=100.0)
    first = m.open_orders()[0]

    # A different intent arrives: the incumbent is asked to cancel and the
    # replacement is REFUSED until the cancel is acknowledged.
    placed = m.place("DIRECT_EXIT", at=15.0, price=0.46, qty=100.0,
                     decision_id="race-2")
    assert placed["refused"] == "WAIT_CANCEL_ACK", placed
    assert first.order_id in placed["cancelled"]
    assert first.state == DK.CANCEL_PENDING
    # exactly one order is live, and it is the pending-cancel one
    assert len(m.open_orders()) == 1
    assert m.open_orders()[0].state == DK.CANCEL_PENDING

    # IT FILLS ANYWAY, before the venue acknowledges.
    fills = m.on_print(at=16.0, outcome_index=0, price=0.47, size=1000.0,
                       evidence_id="print-race")
    assert fills, "a CANCEL_PENDING order must still be fillable"
    assert fills[0]["raced_a_pending_cancel"] is True, fills
    st = m.state()
    assert st["invariant"]["ok"] is True, st["invariant"]
    assert m.held(0) == pytest.approx(0.0)


def test_a_partial_exit_leaves_accounted_residual_inventory(admitted):
    """Depth-capped, partially filled: the remainder stays under
    management and the accounting shows it at cost."""
    m = _managed(qty=100.0)
    m.manage_policy(at=10.0, decision_id="part-1", sport=SPORT,
                    progress=SECOND_HALF, bid=0.47, bid_size=40.0)
    o = m.open_orders()[0]
    assert o.qty == 40.0
    # a small print: our queue share of 80 is 20
    fills = m.on_print(at=20.0, outcome_index=0, price=0.47, size=80.0,
                       evidence_id="print-part")
    assert fills and sum(f["qty"] for f in fills) == pytest.approx(20.0)

    st = m.state()
    assert m.held(0) == pytest.approx(80.0), st["held"]
    assert st["invariant"]["ok"] is True
    # inventory carried at COST, never marked
    assert st["open_inventory_valuation"]["mark_usd"] == "NOT_IDENTIFIED"
    assert st["open_inventory_valuation"]["inventory_cost_usd"] > 0.0
    # and the order is partially filled, not closed
    assert o.state == DK.PARTIALLY_FILLED, o.to_dict()
    assert o.remaining == pytest.approx(20.0)


def test_cancel_acknowledgement_closes_the_order_and_frees_the_slot(admitted):
    m = _managed(qty=100.0)
    m.manage_policy(at=10.0, decision_id="ack-1", sport=SPORT,
                    progress=SECOND_HALF, bid=0.47, bid_size=100.0)
    m.place("DIRECT_EXIT", at=15.0, price=0.46, qty=100.0,
            decision_id="ack-2")
    done = m.acknowledge_cancels(16.0)
    assert done, "no cancel was acknowledged"
    assert m.open_orders() == []
    # only NOW can a replacement be placed, sized against current inventory
    again = m.place("DIRECT_EXIT", at=17.0, price=0.46, qty=100.0,
                    decision_id="ack-3")
    assert again["placed"] is not None, again
    assert again["qty"] == pytest.approx(100.0)


def test_the_complete_policy_still_prefers_the_pair_when_no_exit_fires(admitted):
    """Both halves in one decision path: no trigger, so pairing stands."""
    m = _managed(qty=100.0, price=0.57)
    d = m.manage_policy(at=10.0, decision_id="both-1", sport=SPORT,
                        progress=SECOND_HALF, bid=0.56, bid_size=100.0)
    assert d["loss_trigger"]["fired"] is False
    assert d["pair_target"]["feasible"] is True
    assert d["selected_action"] == "POST_COMPLEMENT", d
    assert d["pair_target"]["limit"] == 0.32, d["pair_target"]
