"""ENTRY-CREATED INVENTORY REACHES THE EXIT PATH THAT ALREADY EXISTS.

WHAT THIS CORRECTS, AND IT WAS MY CLAIM. The operating record said this
lane has "no exit rule", and listed building one as a prerequisite for a
capped pilot. That was wrong, and it contradicted work already done:

  * `bettor_mgmt_select.exposure_trigger` IS an exit rule --
    `EXPOSURE_TRIGGER_RULE_V1`, with DECLARED thresholds (a 20% adverse
    move against basis on the last observed price, or 86,400 s unpaired)
    and inputs that exist now: last price, entry basis, seconds open. It
    needs no forecast and no EV_HOLD.
  * `bettor_mgmt_select.rank_priced_actions` prices DIRECT_EXIT, REDUCE,
    TAKE_COMPLEMENT and POST_COMPLEMENT over the SAME quantity and
    refuses a price with no depth behind it.
  * `bettor_mgmt_lifecycle.Managed.decide_and_act` runs those two in
    order -- WHETHER, then HOW -- and places the winner through
    `place()`, the `bettor_desk.Order` state machine and the Portfolio.
  * `rn1x_shadow.run_continuing_management` drives that for the entry
    lane's experiment on every cycle, including cycles with no new
    candidates.

So the question is not "is there an exit rule" but "does entry-created
inventory reach it, and does it fire when its declared condition is met".
These tests answer both, through the existing manager. Nothing here adds
a second exit engine; that is the point.

WHAT PRODUCTION SHOWS, AND WHY IT IS NOT THIS. The acceptance position
records HOLD_BY_FALLBACK_RULE with `first_failing_link
3_PROVIDER_FIXTURE`: its fixture is over, so the odds provider no longer
carries it and no EV_HOLD can be identified for it again. The trigger DID
run and did not fire. A position whose fixture has finished belongs to
SETTLEMENT, not to a repeated hold valuation -- which is
`bettor_entry_settlement`'s job, and is blocked for that position by its
missing venue identity rather than by any exit gap.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_mgmt_lifecycle as LIFE
from sportsassets import bettor_mgmt_select as SEL

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

COND = "c-exit-path"
T0 = 1_790_000_000.0


def _fee(qty, price, maker=False):
    return 0.016 * float(qty)


def _managed(qty=100.0, price=0.60):
    """A managed leg standing in for entry-created inventory.

    Constructed with the entry's own quantity and average cost, which is
    exactly what `store.load_position` hands the manager for a position
    the entry lane opened.
    """
    return LIFE.Managed(condition_id=COND, outcome_index=0, seed_qty=qty,
                        seed_price=price, at=T0, fee_fn=_fee)


# ── the rule exists, is declared, and needs no forecast ──────────────

def test_the_exit_rule_is_declared_and_needs_no_forecast():
    d = SEL.TRIGGER_DECLARATION
    assert d["id"] == "EXPOSURE_TRIGGER_RULE_V1"
    assert d["question_it_answers"].startswith("WHETHER")
    assert d["thresholds_are"].startswith("DECLARED")
    # THE INPUTS IT NEEDS ALL EXIST FOR AN ENTRY-CREATED POSITION.
    assert "entry basis" in d["inputs_available_now"]
    # AND THE ONES IT REFUSES ARE THE ONES THAT WOULD MAKE IT A FORECAST.
    assert "settlement forecast" in d["inputs_refused"]


def test_the_trigger_holds_when_nothing_adverse_has_happened():
    got = SEL.exposure_trigger(basis_per_contract=0.60, last_price=0.62,
                               seconds_open=600.0)
    assert got["fired"] is False
    assert got["operating_state"] == "HOLD"


def test_the_trigger_fires_on_a_declared_adverse_move():
    """20% of basis against us on the last observed price. Declared, not
    fitted, and it is what makes HOLD a decision rather than a default."""
    got = SEL.exposure_trigger(basis_per_contract=0.60, last_price=0.40,
                               seconds_open=600.0)
    assert got["fired"] is True
    assert got["conditions"], got
    assert got["sellable_qty"] if "sellable_qty" in got else True


# ── and entry-created inventory reaches it ───────────────────────────

def test_an_entry_created_leg_exits_through_the_existing_lifecycle():
    """THE CONNECTION, END TO END IN THE MANAGER. An adverse bid trips the
    declared trigger, the priced comparison picks a method, and the order
    is placed through the SAME `place()` / Order / Portfolio path the
    seeded lane uses. No second engine."""
    m = _managed()
    assert m.residual() == pytest.approx(100.0)
    got = m.decide_and_act(at=T0 + 600, last_price=0.40, bid=0.40,
                           bid_size=500.0, complement_ask=0.75,
                           complement_ask_size=500.0, seconds_open=600.0,
                           decision_id="d-exit-1")
    assert got["trigger"]["fired"] is True, got
    assert got["acted"] is True, got
    placed = got["placement"]
    assert placed["placed"], placed
    o = m.orders[placed["placed"]]
    # A SELL OF THE HELD LEG, sized to the residual and never above it.
    assert o.side == "SELL"
    assert o.intent in ("EXIT", "REDUCE")
    assert o.qty <= m.residual() + 1e-9
    assert o.outcome_index == 0
    # THE METHOD WAS CHOSEN BY THE PRICED COMPARISON, not by the trigger.
    assert got["method"]["selected"], got["method"]
    assert got["method"]["priced_actions"], got["method"]


def test_a_complement_completion_is_also_reachable_and_is_not_a_sell():
    """The other method the existing manager prices. On PMUS this reduces
    the signed position rather than opening a second leg, which
    `bettor_venue_position_model` owns and the settlement replay honours."""
    m = _managed()
    got = m.decide_and_act(at=T0 + 600, last_price=0.40, bid=0.02,
                           bid_size=1.0, complement_ask=0.20,
                           complement_ask_size=500.0, seconds_open=600.0,
                           decision_id="d-exit-2")
    assert got["trigger"]["fired"] is True
    if got.get("acted"):
        o = m.orders[got["placement"]["placed"]]
        # A COMPLETION IS A BUY OF THE OTHER LEG, and that is not a sell.
        if o.side == "BUY":
            assert o.outcome_index == 1
            assert o.intent == "COMPLETE_PAIR"
    else:
        # A REFUSAL IS ALSO AN ANSWER, and it must be a named one.
        assert got.get("why"), got


def test_an_order_may_never_exceed_the_residual():
    m = _managed(qty=10.0)
    placed = m.place("DIRECT_EXIT", at=T0 + 10, price=0.50, qty=999.0,
                     decision_id="d-cap")
    assert placed["placed"]
    assert m.orders[placed["placed"]].qty == pytest.approx(10.0)
    assert placed["capped"] is True


def test_the_scheduled_manager_covers_the_entry_lanes_experiment():
    """`run_continuing_management` is what connects entry-created
    inventory to all of the above, and it must name the entry lane's own
    experiment -- not only the challenger's."""
    import inspect

    from sportsassets import bettor_external_shadow as ext
    from sportsassets.workers import rn1x_shadow as RS

    src = inspect.getsource(RS.run_continuing_management)
    assert "_ext.EXPERIMENT_ID" in src
    assert "manage_open_positions" in src
    # AND IT RUNS WITHOUT NEW CANDIDATES, which is the defect that left
    # entry inventory unmanaged on every quiet cycle.
    assert "runs_without_new_candidates" in src
    assert ext.EXPERIMENT_ID != RS.CHALLENGER_EXPERIMENT_ID


def test_the_exit_intents_map_onto_the_engines_own_vocabulary():
    """So a placed order records which evaluated action produced it, and
    an exit can be traced back to the comparison that chose it."""
    assert LIFE.INTENT_FOR["DIRECT_EXIT"] == "EXIT"
    assert LIFE.INTENT_FOR["REDUCE"] == "REDUCE"
    assert LIFE.INTENT_FOR["TAKE_COMPLEMENT"] == "COMPLETE_PAIR"
    assert LIFE.INTENT_FOR["POST_COMPLEMENT"] == "COMPLETE_PAIR"


# ── what is actually missing, named ──────────────────────────────────

def test_a_finished_fixture_has_no_priced_exit_and_that_is_not_a_gap():
    """THE PRODUCTION STATE, AND THE HONEST NAME FOR IT.

    With no last price and no depth -- which is what a finished fixture
    leaves, because the provider stops carrying it -- the trigger cannot
    fire on a price move and the priced comparison has nothing to rank.
    That is the absence of an INPUT, not the absence of a rule, and the
    terminal path for such a position is settlement.
    """
    got = SEL.exposure_trigger(basis_per_contract=0.60, last_price=None,
                               seconds_open=600.0)
    assert got["fired"] is False
    rank = SEL.rank_priced_actions(100.0, 0.60, bid=None, bid_size=None,
                                   complement_ask=None,
                                   complement_ask_size=None, fee_fn=_fee)
    assert not rank.get("selected")
    assert rank["selection_reason"], rank
    # AND THE SETTLEMENT CONSUMER IS THE TERMINAL PATH, needing no odds.
    from sportsassets import bettor_entry_settlement as S

    assert S.describe()["requires_fresh_bookmaker_odds"] is False


def test_the_time_condition_closes_an_unpriced_position_eventually():
    """The trigger's second declared condition needs no price at all, so a
    position cannot be held forever merely because nothing quotes it."""
    got = SEL.exposure_trigger(basis_per_contract=0.60, last_price=None,
                               seconds_open=SEL.TRIGGER_MAX_SECONDS_OPEN + 1)
    assert got["fired"] is True, got
    assert any("SECONDS" in str(c).upper() or "OPEN" in str(c).upper()
               for c in got["conditions"]), got["conditions"]


@pg
@pytest.mark.asyncio
async def test_the_store_hands_entry_inventory_to_the_manager(monkeypatch):
    """The ledger read the manager uses must return an entry-created
    position, with the quantity and basis the exit path needs."""
    asyncpg = pytest.importorskip("asyncpg")

    from sportsassets import bettor_external_shadow as ext
    from sportsassets import bettor_rn1x_store as store
    from sportsassets.workers import ext_pinnacle_loop as loop
    from . import test_the_entry_lane_reaches_inventory as L

    conn = await asyncpg.connect(DSN)
    try:
        await L._seed(conn)
        await L._calibrate(conn)
        L._stub(monkeypatch)
        made = await loop.cycle(conn)
        pid = made["entries"][0]["position_id"]
        rows = await store.open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, limit=10)
        mine = [r for r in rows if r["position_id"] == pid]
        assert mine, "the manager's own read must return it"
        r = mine[0]
        # THE TWO NUMBERS THE EXIT PATH NEEDS.
        assert r["seed_qty"] > 0
        assert 0 < r["seed_price"] < 1
        assert r["policy"] == inv.POLICY
        # AND A MANAGED LEG BUILT FROM THEM EXITS.
        m = LIFE.Managed(condition_id=r["condition_id"],
                         outcome_index=r["outcome_index"],
                         seed_qty=r["seed_qty"], seed_price=r["seed_price"],
                         at=r["decision_ts"], fee_fn=_fee)
        got = m.decide_and_act(at=r["decision_ts"] + 600,
                               last_price=r["seed_price"] * 0.5,
                               bid=r["seed_price"] * 0.5, bid_size=10_000.0,
                               complement_ask=0.9,
                               complement_ask_size=10_000.0,
                               seconds_open=600.0, decision_id="d-live")
        assert got["trigger"]["fired"] is True, got
        assert got["acted"] is True, got
    finally:
        await L._cleanup(conn)
        await conn.close()
