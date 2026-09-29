"""THE PLAN THAT WON IS THE ORDER THAT IS SENT -- OR NOTHING IS.

WHAT THIS PROVES, AND THROUGH WHAT. Every database test here drives
`bettor_funded_pair_cycle.pass_once`, the function the scheduled worker calls,
against a migrated PostgreSQL database. The venue is substituted at
`pmus._get_client` and nowhere above it; discovery, the whole-position valuation,
the ranking, the decision write, the admission record, the reservation, the
rails and the adapter are the deployed code.

THE TWO DEFECTS THESE PIN.

  1. A WINNING HEDGE COULD NOT REACH A VENUE IN PRODUCTION. The scheduled
     supplier sets `hedge_decision_record` to None, so the pass handed
     `submit_for_decision` a decision-LEDGER result -- measured null -- plus a
     decision id. That is not an admitted, sized decision, and
     `plan_from_decision` has to refuse it. Where a record WAS supplied,
     `acquire_second_leg` merged the plan into it with `setdefault`, so the
     record OVERRODE the ranked plan: a SHORT ranked at a wire price of 0.59 was
     reserved at 0.59 and ordered at 0.41, LONG.

  2. NOTHING BOUND THE SIDE. The reservation stored no side, `PLAN_FIELDS`
     compared none, and the rails stripped the plan's side before comparing. At a
     wire price of 0.50 a LONG and a SHORT on one slug agree on slug, quantity,
     price and collateral, so a plan for one side netted against a reservation for
     the other.

WHAT IS SUPPLIED AND SAID SO. The region probabilities (p_middle 0.50) are a
controlled input chosen so the comparison has a known answer; they are not a
measured probability and nothing here claims a middle occurs half the time. The
three submission switches are turned on in-process by `_transport`, as in every
lifecycle test; the shipped code keeps all three off and a test asserts that.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_funded_reservations as RSV
from sportsassets import bettor_indirect_structures as IS

from tests import test_the_scheduled_pair_lifecycle as L

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

SHORT = "ORDER_INTENT_BUY_SHORT"
LONG = FX.LONG


async def _connect():
    import asyncpg

    return await asyncpg.connect(DSN)


def _supplier(**overrides):
    """THE PAIR LIFECYCLE'S OWN SUPPLIER, with named fields replaced.

    Built on `L._pair_facts` so every test here runs the same fixture the
    lifecycle proof runs, and the ONLY difference between a passing and a
    refusing case is the field the test names.
    """
    base = L._pair_facts(L._exit_is_the_standalone_winner())

    async def _supply(conn, pos, *, at):
        facts = await base(conn, pos, at=at)
        for k, v in overrides.items():
            facts[k] = v(facts, at) if callable(v) else v
        return facts

    return _supply


async def _run(conn, monkeypatch, supplier):
    await L._clean(conn)
    await L._seed(conn)
    await L._primary(conn)
    _, sent, _client = L._transport(monkeypatch, order_id="venue-hedge")
    got = await PC.pass_once(conn, account_id=L.ACCT, venue=L.VENUE,
                             pair_inputs=supplier,
                             venue_positions=L.EMPTY_VENUE)
    return got, got["considered"][0], sent


def _creates(sent):
    return [c[1] for c in sent if c[0] == "create"]


# ═════════════════════════════════════════════════════════════════════
# 1 · THE PERSISTED WINNER REACHES THE ADAPTER, EXACTLY
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_persisted_winner_is_the_order_the_venue_receives(monkeypatch):
    """With the PRODUCTION supplier shape -- no separate order record -- the hedge
    that won is sent, on the side it won on, at the wire price its plan states.

    This path was unreachable before: the record handed to the execution module
    was a ledger row with a decision id, which is not an admitted decision.
    """
    conn = await _connect()
    try:
        got, step, sent = await _run(conn, monkeypatch, _supplier(
            hedge_decision_record=None))
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE, step
        plan = step["acquisition_plan"]
        # EVERY ADMISSION CHECK PASSED, BY NAME -- being selected is not enough.
        adm = step["admission"]
        assert adm["ok"] is True, adm
        assert sorted(adm["passed"]) == sorted(PC.HEDGE_ADMISSION_CHECKS)

        # ── THE DECISION THAT WAS PERSISTED NAMES THIS PLAN ──────────
        row = await conn.fetchrow(
            "SELECT action FROM bettor_funded_decisions "
            " WHERE decision_id=$1", L.DECISION)
        assert row is not None and row["action"] == "ACQUIRE_HEDGE"
        assert step["decision"]["selected"]["plan_digest"] == plan["digest"]

        # ── AND THE VENUE RECEIVED EXACTLY THAT ORDER ────────────────
        creates = _creates(sent)
        assert len(creates) == 1, sent
        c = creates[0]
        assert c["intent"] == plan["side"] == SHORT
        assert c["marketSlug"] == plan["venue_slug"] == L.SLUG_HEDGE
        assert float(c["price"]["value"]) == pytest.approx(plan["limit_price"])
        assert int(c["quantity"]) == int(plan["quantity"])

        # ── AND THE BOOK HOLDS THE SAME ORDER ────────────────────────
        iid = step["acquisition"]["intent_id"]
        intent = await conn.fetchrow(
            "SELECT order_intent, us_market_slug, quantity, "
            "       limit_price::float8 AS lp, leg_role, portfolio_group_id "
            "  FROM bettor_funded_intents WHERE intent_id=$1", iid)
        assert intent["order_intent"] == SHORT
        assert intent["us_market_slug"] == L.SLUG_HEDGE
        assert intent["quantity"] == int(plan["quantity"])
        assert intent["lp"] == pytest.approx(plan["limit_price"])
        assert intent["leg_role"] == "HEDGE"
        # AND THE RESERVATION CLAIMED THE SAME SIDE (migration 136).
        res = await conn.fetchrow(
            "SELECT order_intent, intent_id FROM bettor_funded_leg_reservations"
            " WHERE operation_id=$1", L.OP_HEDGE)
        assert res["order_intent"] == SHORT
        assert res["intent_id"] == iid
    finally:
        await L._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_short_costing_41_cents_is_sent_at_59_with_41_cents_collateral(
        monkeypatch):
    """THE WIRE PRICE IS YES-DENOMINATED ON BOTH SIDES.

    The hedge costs $0.41 per contract on its own (SHORT) side. The venue is
    told 0.59, and the collateral it takes is (1 - 0.59) x 10 = $4.10 -- the
    cost the ranking valued. Sending 0.41 would post (1 - 0.41) x 10 = $5.90.
    """
    conn = await _connect()
    try:
        got, step, sent = await _run(conn, monkeypatch, _supplier(
            hedge_decision_record=None))
        plan = step["acquisition_plan"]
        assert plan["side"] == SHORT
        assert plan["limit_price"] == pytest.approx(0.59)
        assert plan["collateral_usd"] == pytest.approx(4.10)
        assert FX.collateral_for(plan["limit_price"], int(plan["quantity"]),
                                 SHORT) == pytest.approx(4.10)
        assert float(_creates(sent)[0]["price"]["value"]) == pytest.approx(0.59)
    finally:
        await L._clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · A MISMATCH PREVENTS DISPATCH, AND NOTHING IS LEFT BEHIND
# ═════════════════════════════════════════════════════════════════════

async def _nothing_happened(conn, sent):
    assert _creates(sent) == [], sent
    assert [c for c in sent if c[0] == "preview"] == [], sent
    n = await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_leg_reservations "
        " WHERE operation_id=$1", L.OP_HEDGE)
    assert n == 0, "a refused acquisition must not leave a claim on the leg"
    n = await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_intents "
        " WHERE portfolio_group_id=$1 AND leg_role='HEDGE'", L.GROUP)
    assert n == 0


@pg
@pytest.mark.asyncio
async def test_a_supplied_record_on_the_other_side_is_refused(monkeypatch):
    """The ranking chose SHORT. A record saying LONG is a different order."""
    conn = await _connect()
    try:
        rec = dict(L._hedge_decision_record(), order_intent=LONG)
        rec.pop("execution_plan", None)
        got, step, sent = await _run(conn, monkeypatch, _supplier(
            hedge_decision_record=rec))
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE
        assert step["refusal"] == PC.R_HEDGE_RECORD_CONFLICTS_WITH_PLAN, step
        fields = [c["field"] for c in step["admission"]["conflicts"]]
        assert fields == ["order_intent"], step["admission"]
        await _nothing_happened(conn, sent)
        assert got["opened_anything"] is False
    finally:
        await L._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_supplied_record_at_another_price_is_refused(monkeypatch):
    """THE DEFECT, EXACTLY AS MEASURED: the legacy record prices the hedge at
    its cost (0.41) where the plan's wire price is 0.59. It used to override the
    plan through `setdefault`; now it refuses before anything is reserved."""
    conn = await _connect()
    try:
        got, step, sent = await _run(conn, monkeypatch, _supplier(
            hedge_decision_record=L._hedge_decision_record()))
        assert step["refusal"] == PC.R_HEDGE_RECORD_CONFLICTS_WITH_PLAN, step
        fields = {c["field"] for c in step["admission"]["conflicts"]}
        # LONG, and 0.41 where the plan says 0.59 -- both named.
        assert "order_intent" in fields
        assert "execution_plan.execution.limit_price" in fields
        await _nothing_happened(conn, sent)
    finally:
        await L._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_expired_evidence_prevents_dispatch(monkeypatch):
    """The quote's inputs died before the order would leave. Agreement between
    two records about an expiry is not evidence the inputs are alive, so the
    clock is asked -- and nothing is sent on a stale price."""
    conn = await _connect()
    try:
        def _stale(facts, at):
            return [dict(d, inputs_expire_at=float(at) - 1.0)
                    for d in facts["candidate_leg_details"]]

        got, step, sent = await _run(conn, monkeypatch, _supplier(
            hedge_decision_record=None, candidate_leg_details=_stale))
        # THE HEDGE IS NOT OFFERED AS AN ELIGIBLE OPTION AT ALL, and says why.
        refused = step["hedge_decision_inputs"]["not_eligible"]
        assert [r["refusal"] for r in refused] == [
            "ACQUISITION_INPUTS_EXPIRED"], refused
        assert step["decision"]["action"] != PC.ACTION_ACQUIRE
        await _nothing_happened(conn, sent)
    finally:
        await L._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_price_not_read_from_this_side_is_not_a_hedge_option(
        monkeypatch):
    """A reading keyed by slug alone cannot price either side of it. The hedge
    drops out of the comparison with its reason, and HOLD, EXIT and REDUCE --
    which do not depend on that reading -- are still decided between."""
    conn = await _connect()
    try:
        def _slug_only(facts, at):
            return [{k: v for k, v in d.items() if k != "candidate_id"}
                    | {"market_slug": L.SLUG_HEDGE}
                    for d in facts["candidate_leg_details"]]

        got, step, sent = await _run(conn, monkeypatch, _supplier(
            hedge_decision_record=None, candidate_leg_details=_slug_only))
        refused = step["hedge_decision_inputs"]["not_eligible"]
        assert [r["refusal"] for r in refused] == [
            "SIDE_SPECIFIC_QUOTE_NOT_ESTABLISHED"], refused
        # THE POSITION IS STILL DECIDED -- and the exit wins on its number.
        assert step["decision"]["action"] == "EXIT", step["decision"]
        assert _creates(sent) == [] or all(
            c.get("marketSlug") != L.SLUG_HEDGE for c in _creates(sent))
    finally:
        await L._clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · THE HEDGE WINS ON ITS NUMBER, AND LOSES ON IT
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_same_fixture_loses_to_the_exit_when_its_middle_is_unlikely(
        monkeypatch):
    """ONLY THE PROBABILITY OF THE MIDDLE CHANGES.

    The whole-position expected value is -0.60 + 10 x P(middle): the structure
    pays $20 when both legs win and $10 otherwise, against $10.30 of cost and a
    $0.30 fee. At 0.50 that is +$4.40 and beats the exit's +$2.40; at 0.20 it is
    +$1.40 and the exit wins. The hedge is in the comparison both times -- it is
    beaten on its value, not withheld.
    """
    conn = await _connect()
    try:
        def _unlikely(facts, at):
            admitted = PC.discover(
                held_leg=facts["held_leg"],
                candidate_legs=facts["candidate_legs"],
                sport_permits_tie=False, fixture_can_void=False,
                fixture_can_postpone=False)["admitted"][0]
            return L._region_probabilities(admitted["structure"]["table"],
                                           p_middle=0.20)

        got, step, sent = await _run(conn, monkeypatch, _supplier(
            hedge_decision_record=None, region_probabilities=_unlikely))
        ids = step["hedge_decision_inputs"]["candidate_ids"]
        assert ids == [L.HEDGE_ID], "the hedge must be IN the comparison"
        assert step["decision"]["action"] == "EXIT", step["decision"]
        assert step["decision"]["selected"]["value_usd"] == pytest.approx(2.40)
        await _nothing_happened(conn, sent)
    finally:
        await L._clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · BEING SELECTED IS NOT ADMISSION  (pure)
# ═════════════════════════════════════════════════════════════════════

def _plan():
    from tests.test_the_selected_side_is_the_side_that_is_ordered import plan
    return plan()


def _selected(p, **kw):
    return dict({"candidate_id": p.candidate_id, "plan_digest": p.digest,
                 "rankable": True, "value_usd": 1.0, "fees_usd": 0.1,
                 "position_includes_uncovered_inventory": True}, **kw)


def _admitted(**kw):
    st = {"taxonomy": IS.MIDDLE, "missing_facts": [],
          "undetermined_regions": []}
    st.update(kw)
    return {"structure": st, "taxonomy": st["taxonomy"]}


def _row(p, **kw):
    return dict({"price_is_this_sides_own": True,
                 "depth_qty": float(p.quantity) + 5}, **kw)


def _admit(p, **kw):
    args = dict(plan=p, selected=_selected(p), admitted=_admitted(),
                ranked_row=_row(p),
                position={"event_key": "ev", "us_market_slug": "held-slug"},
                decision_id="d", now=p.inputs_expire_at - 1)
    args.update(kw)
    return PC.hedge_admission_record(**args)


def test_the_record_reproduces_the_plan_through_the_execution_reader():
    p = _plan()
    got = _admit(p)
    assert got["ok"] is True, got
    rep = FX.plan_from_decision(got["record"])
    assert rep["ok"] is True
    assert rep["us_market_slug"] == p.venue_slug
    assert rep["intent"] == p.side
    assert rep["quantity"] == int(p.quantity)
    assert rep["limit_price"] == pytest.approx(p.limit_price)
    assert rep["collateral_usd"] == pytest.approx(p.collateral_usd)
    # AND THE RECORD SAYS WHY IT IS ADMISSIBLE, rather than asserting it.
    assert got["record"]["admitted_because"] == list(PC.HEDGE_ADMISSION_CHECKS)


@pytest.mark.parametrize("override,check", [
    ({"admitted": _admitted(missing_facts=["OVERTIME_RULE"])},
     "SETTLEMENT_RELATIONSHIP_ESTABLISHED"),
    ({"admitted": _admitted(taxonomy=IS.UNESTABLISHABLE)},
     "SETTLEMENT_RELATIONSHIP_ESTABLISHED"),
    ({"position": {"us_market_slug": "held-slug"}}, "EVENT_KEY_STATED"),
])
def test_an_unestablished_input_refuses_by_name(override, check):
    got = _admit(_plan(), **override)
    assert got["ok"] is False
    assert got["refusal"] == PC.R_HEDGE_NOT_ADMISSIBLE
    assert [f["check"] for f in got["failed"]] == [check], got


def test_a_quote_from_another_side_refuses():
    p = _plan()
    got = _admit(p, ranked_row=_row(p, price_is_this_sides_own=False))
    assert [f["check"] for f in got["failed"]] == ["QUOTE_IS_THIS_SIDES_OWN"]


def test_a_book_thinner_than_the_order_refuses():
    p = _plan()
    got = _admit(p, ranked_row=_row(p, depth_qty=float(p.quantity) - 1))
    assert [f["check"] for f in got["failed"]] == [
        "DEPTH_SUPPORTS_THE_QUANTITY"]


def test_an_unpriced_fee_or_a_valueless_selection_refuses():
    p = _plan()
    got = _admit(p, selected=_selected(p, fees_usd=None))
    assert "FEE_PRICED" in [f["check"] for f in got["failed"]]
    got = _admit(p, selected=_selected(p, value_usd=None))
    assert "VALUED_ON_THE_WHOLE_POSITION" in [f["check"]
                                              for f in got["failed"]]
    got = _admit(p, selected=_selected(p, value_usd=float("nan")))
    assert "VALUED_ON_THE_WHOLE_POSITION" in [f["check"]
                                              for f in got["failed"]]


def test_expired_inputs_refuse_even_when_everything_else_passes():
    p = _plan()
    got = _admit(p, now=p.inputs_expire_at + 0.001)
    assert [f["check"] for f in got["failed"]] == ["INPUTS_NOT_EXPIRED"]


def test_a_plan_that_is_not_the_selected_one_refuses():
    p = _plan()
    got = _admit(p, selected=_selected(p, plan_digest="another-plan"))
    assert "PLAN_IS_THE_SELECTED_ONE" in [f["check"] for f in got["failed"]]


def test_a_hedge_on_the_held_instrument_is_netting_not_hedging():
    p = _plan()
    got = _admit(p, position={"event_key": "ev",
                              "us_market_slug": p.venue_slug})
    assert [f["check"] for f in got["failed"]] == [
        "DOES_NOT_NET_THE_HELD_INSTRUMENT"]


def test_a_hedge_on_a_position_with_no_event_is_not_dispatched():
    """MAX_EVENT_EXPOSURE is enforced per event, and a key inferred from the
    slug would let the rail pass on a guess."""
    p = _plan()
    got = _admit(p, position={"us_market_slug": "held-slug"})
    assert got["ok"] is False
    assert [f["check"] for f in got["failed"]] == ["EVENT_KEY_STATED"]


# ═════════════════════════════════════════════════════════════════════
# 5 · THE SIDE IS BOUND THROUGH THE RESERVATION  (migration 136)
# ═════════════════════════════════════════════════════════════════════

def test_at_the_money_the_four_old_fields_cannot_tell_the_sides_apart():
    """THE HOLE, SHOWN IN NUMBERS. At wire 0.50, 10 contracts:
    LONG collateral 0.50 x 10 = $5.00; SHORT (1 - 0.50) x 10 = $5.00."""
    assert FX.collateral_for(0.50, 10, LONG) == FX.collateral_for(0.50, 10,
                                                                  SHORT)
    res = {"us_market_slug": "m", "quantity": 10.0, "limit_price": 0.50,
           "collateral_usd": 5.0, "order_intent": SHORT}
    plan = {"us_market_slug": "m", "quantity": 10.0, "limit_price": 0.50,
            "collateral_usd": 5.0, "intent": LONG}
    got = RSV.plan_matches(res, plan)
    assert got["ok"] is False
    assert [c["field"] for c in got["conflicts"]] == ["order_intent"]
    # AND THE SAME SIDE MATCHES, so the check is not simply refusing everything.
    assert RSV.plan_matches(res, dict(plan, intent=SHORT))["ok"] is True
    # A PLAN THAT STATES NO SIDE IS NOT AGREEMENT WITH ONE THAT DOES.
    assert RSV.plan_matches(res, dict(plan, intent=None))["ok"] is False


@pg
@pytest.mark.asyncio
async def test_the_database_refuses_a_short_claim_committed_to_a_long_order():
    """THE BOUNDARY WHERE THE CLAIM BECOMES AN ORDER, enforced by trigger.

    Every field 131 compared agrees -- same group, role, slug, quantity, price
    0.50 and collateral $5.00 -- and only the side differs. The database refuses
    the commit, so no caller can route around it.
    """
    import asyncpg

    conn = await _connect()
    try:
        await L._clean(conn)
        await L._seed(conn)
        await L._primary(conn)
        held = await RSV.hold(
            conn, operation_id="op:atm-side", group_id=L.GROUP,
            leg_role="HEDGE", us_market_slug=L.SLUG_HEDGE, quantity=10,
            limit_price=0.50, collateral_usd=5.0, order_intent=SHORT)
        assert held["ok"] is True, held
        assert held["reservation"]["order_intent"] == SHORT
        wrong = await FB.record_intent(
            conn, intent_id="fpi-atm-long", account_id=L.ACCT, venue=L.VENUE,
            venue_class="FUNDED", us_market_slug=L.SLUG_HEDGE,
            event_key=L.EVENT, order_intent=LONG, limit_price=0.50,
            quantity=10, collateral_usd=5.0, effective_digest="d",
            portfolio_group_id=L.GROUP, leg_role="HEDGE")
        if not wrong.get("ok"):
            pytest.skip("the book refused the LONG intent for its own reason "
                        "(%s); the trigger is not reached" % wrong.get("refusal"))
        with pytest.raises(asyncpg.PostgresError, match="is not an order on"):
            await conn.execute(
                "UPDATE bettor_funded_leg_reservations "
                "   SET state='COMMITTED', intent_id=$1 "
                " WHERE operation_id=$2", "fpi-atm-long", "op:atm-side")
    finally:
        await conn.execute("DELETE FROM bettor_funded_leg_reservations "
                           " WHERE operation_id='op:atm-side'")
        await conn.execute("DELETE FROM bettor_funded_intents "
                           " WHERE intent_id='fpi-atm-long'")
        await L._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_replay_that_changes_only_the_side_is_a_different_acquisition():
    conn = await _connect()
    try:
        await L._clean(conn)
        await L._seed(conn)
        await L._primary(conn)
        kw = dict(operation_id="op:side-replay", group_id=L.GROUP,
                  leg_role="HEDGE", us_market_slug=L.SLUG_HEDGE, quantity=10,
                  limit_price=0.50, collateral_usd=5.0)
        first = await RSV.hold(conn, order_intent=SHORT, **kw)
        assert first["ok"] is True and first["already"] is False
        again = await RSV.hold(conn, order_intent=SHORT, **kw)
        assert again["ok"] is True and again["already"] is True
        other = await RSV.hold(conn, order_intent=LONG, **kw)
        assert other["ok"] is False, other
        assert "order_intent" in str(other.get("conflicts") or other)
        # AND THE DATABASE WILL NOT LET THE SIDE BE EDITED IN PLACE.
        import asyncpg
        with pytest.raises(asyncpg.PostgresError, match="identity is fixed"):
            await conn.execute(
                "UPDATE bettor_funded_leg_reservations SET order_intent=$1 "
                " WHERE operation_id=$2", LONG, "op:side-replay")
    finally:
        await conn.execute("DELETE FROM bettor_funded_leg_reservations "
                           " WHERE operation_id='op:side-replay'")
        await L._clean(conn)
        await conn.close()


def test_hold_refuses_a_side_that_is_not_a_venue_intent():
    import asyncio

    got = asyncio.run(RSV.hold(
        None, operation_id="x", group_id="g", leg_role="HEDGE",
        us_market_slug="m", quantity=1, limit_price=0.5, collateral_usd=0.5,
        order_intent="ORDER_INTENT_SELL"))
    assert got["ok"] is False
    assert got["refusal"] == RSV.R_SIDE_NOT_A_VENUE_INTENT
