"""ONE GROUP, ONE DECISION AT A TIME, ON THE QUANTITIES IT WAS DECIDED ON.

WHAT THIS PROVES, AND THROUGH WHAT. Every test drives
`bettor_funded_pair_cycle.pass_once` against a migrated PostgreSQL database,
with the venue substituted at `pmus._get_client` and nowhere above it, through
the harness of `test_the_scheduled_pair_lifecycle.py` (its supplier contract
and fixture). Submission switches are patched True inside the tests only.

  1. TWO CONCURRENT REVIEWS of one group -- two connections running the pass
     at once, one choosing to buy the hedge and the other to sell the primary
     -- produce at most one dispatch and no double reservation: the second
     cannot take the group's lock, reads nothing, decides nothing, sends
     nothing, and its record says so.
  2. A PARTIAL HEDGE (depth covers 6 of 10) is valued with its 4 unmatched,
     and the next review values the group whole -- matched 6, unpaired 4 --
     without opening a second hedge; both legs carry their payout and
     settlement identity.
  3. RESTART RECOVERY: a new connection and a fresh pass after a lost answer
     send nothing again; the group stays ORDER_UNRESOLVED with its exposure
     counted, and the gate names why.
  4. A FILL BETWEEN THE DECISION AND THE SEND (a working hedge order's fill,
     or a working entry's) changes what the plan was valued on: it is not
     sent, the refusal is recorded as a NOT_SENT execution event, and the next
     review decides again -- and sends.

ALL MARKET DATA AND PROBABILITIES HERE ARE SYNTHETIC.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_indirect_pair as FIP
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_funded_reservations as RSV
from sportsassets import bettor_xavier as XV

from tests import test_the_scheduled_pair_lifecycle as SPL
from tests import test_xavier_manages_every_position_through_the_scheduled_pass as XM

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

#: SYNTHETIC: the settlement rule a valuation row would carry for the
#: primary (in production `manage` reads it from the valuation row).
PRIMARY_RULE = {"overall_established": True, "unmet": [],
                "attested": ["OVERTIME", "PUSH", "VOID"],
                "book_rule": "MONEYLINE_REGULATION_PLUS_OVERTIME",
                "synthetic": True}


async def _start(conn):
    await XM.spl_clean(conn)
    await SPL._seed(conn)
    await SPL._primary(conn)
    # THE INPUTS A FUNDED ACQUISITION NEEDS SINCE 880377f: an approved
    # conditional model and a measured void rate (SYNTHETIC observations;
    # tests/approved_conditional_model). `XM.spl_clean` purges them.
    from tests import approved_conditional_model as ACM
    await ACM.approve(conn)


def _step(got, intent_id):
    return next(s for s in got["considered"]
                if str(s.get("intent_id")) == str(intent_id))


def _acquire_supplier(*, depth=25, decision_id=None, operation_id=None,
                      gate_event=None, release_event=None, calls=None,
                      exit_px=0.66):
    """The lifecycle's supplier with the hedge winning (the best standalone
    exit is beaten), optionally depth-limited, optionally blocking inside the
    review until another review has run.

    DISTRIBUTION-PRICED since 880377f (SPL `_distribution_pair_facts`): a
    funded acquisition is dispatched only when priced from an approved
    conditional model on a measured void rate and robust over that range.
    `_start` approves the model."""
    base = SPL._distribution_pair_facts(
        SPL._robust_hold_ranking(exit_px=exit_px))

    async def _supply(conn, pos, *, at):
        if calls is not None:
            calls.append(pos["intent_id"])
        if gate_event is not None:
            gate_event.set()
            await asyncio.wait_for(release_event.wait(), 20)
        f = await base(conn, pos, at=at)
        # WHAT `manage`'S PER-POSITION RECORD CARRIES IN PRODUCTION: the
        # basis (read here from the fills ledger, the same function) and the
        # valuation's settlement rule (SYNTHETIC here).
        rb = await FB.remaining_basis(conn, str(pos["intent_id"]))
        f["management_evidence"] = {
            "basis": {"basis_per_contract": rb.get("basis_per_contract"),
                      "remaining_basis_usd": rb.get("remaining_basis_usd")},
            "decision_evidence": {
                "settlement_rule": PRIMARY_RULE,
                "settlement_source": "SYNTHETIC valuation row",
                "valuation_row_id": 1}}
        if decision_id:
            f["decision_id"] = decision_id
        if operation_id:
            f["operation_id"] = operation_id
        if depth != 25:
            f["candidate_leg_details"] = [
                dict(d, depth_qty=depth) for d in f["candidate_leg_details"]]
            f["depth"] = FIP.depth_supports(wanted_qty=SPL.HEDGE_QTY,
                                            depth_qty_at_price=depth)
        return f
    return _supply


def _counting(supplier, calls):
    async def _s(conn, pos, *, at):
        calls.append(pos["intent_id"])
        return await supplier(conn, pos, at=at)
    return _s


# ════════════════════════════════════════════════════════════════════
# 1 · TWO CONCURRENT REVIEWS OF ONE GROUP
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("second", ["SELL_THE_PRIMARY", "BUY_THE_HEDGE_TOO"])
async def test_two_concurrent_reviews_of_one_group_dispatch_at_most_once(
        monkeypatch, second):
    """Review A takes the group's lock and is held INSIDE its review (its
    supplier waits) while review B runs the pass on another connection. B
    would sell the primary -- or buy the same hedge under its own decision
    and operation -- and does neither: it cannot take the lock. One order
    reaches the venue, one leg claim exists, and B read no inputs at all."""
    conn_a, conn_b = await XM._connect(), await XM._connect()
    try:
        await _start(conn_a)
        sent, client = XM.venue(monkeypatch, executions=[SPL._fill(
            10, 0.59, vid="vf-h", state="ORDER_STATE_FILLED")])
        a_inside, b_done = asyncio.Event(), asyncio.Event()
        sup_a = _acquire_supplier(gate_event=a_inside, release_event=b_done)
        b_calls: list = []
        if second == "SELL_THE_PRIMARY":
            sup_b = _counting(XM.exit_supplier(), b_calls)
        else:
            sup_b = _counting(_acquire_supplier(
                decision_id="dec:pairlife-B", operation_id="op:pairlife-B"),
                b_calls)
        t0 = time.time()

        async def _a():
            return await PC.pass_once(conn_a, account_id=SPL.ACCT,
                                      venue=SPL.VENUE, pair_inputs=sup_a,
                                      venue_positions=SPL.EMPTY_VENUE,
                                      now=t0)

        async def _b():
            await asyncio.wait_for(a_inside.wait(), 20)
            try:
                return await PC.pass_once(conn_b, account_id=SPL.ACCT,
                                          venue=SPL.VENUE, pair_inputs=sup_b,
                                          venue_positions=SPL.EMPTY_VENUE,
                                          now=t0 + 1.0)
            finally:
                b_done.set()

        got_a, got_b = await asyncio.gather(_a(), _b())
        sa, sb = _step(got_a, SPL.PRIMARY_INTENT), _step(got_b,
                                                         SPL.PRIMARY_INTENT)
        # B WAS REFUSED BY THE LOCK, BEFORE READING ANYTHING.
        assert sb["refusal"] == XV.R_GROUP_REVIEW_IN_PROGRESS, sb
        assert sb["dispatched"] is None and b_calls == [], (sb, b_calls)
        assert got_b["acquisitions"] == [] and not got_b.get("exits")
        # A DECIDED AND SENT ITS ONE ORDER.
        assert sa["decision"]["action"] == PC.ACTION_ACQUIRE, sa
        cr = XM.creates(sent)
        assert len(cr) == 1, sent
        assert cr[0]["marketSlug"] == SPL.SLUG_HEDGE
        # ONE LEG CLAIM, ONE HEDGE INTENT, NO EXIT.
        assert await conn_a.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations "
            " WHERE group_id=$1", SPL.GROUP) == 1
        assert await conn_a.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE "
            " portfolio_group_id=$1 AND leg_role='HEDGE'", SPL.GROUP) == 1
        assert await conn_a.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE kind='EXIT' "
            " AND parent_intent_id=$1", SPL.PRIMARY_INTENT) == 0
        # B'S REVIEW IS ON THE RECORD, AS A REFUSAL, UNDER ITS OWN ID.
        recs = await XM.records(conn_a, SPL.PRIMARY_INTENT)
        refused = [r for r in recs if r["execution_eligibility"] == (
            "%s:%s" % (XV.E_NOT_DISPATCHED, XV.R_GROUP_REVIEW_IN_PROGRESS))]
        assert len(refused) == 1, [r["execution_eligibility"] for r in recs]
        assert refused[0]["chosen_action"] is None
        # AND THE LOCK WAS RELEASED: a later review of the group takes it.
        lock = await XV.try_group_lock(conn_b, SPL.GROUP)
        assert lock["ok"] is True, lock
        await XV.release_group_lock(conn_b, lock)
    finally:
        await XM.spl_clean(conn_a)
        await conn_a.close()
        await conn_b.close()


# ════════════════════════════════════════════════════════════════════
# 2 · A PARTIAL HEDGE, THEN THE GROUP VALUED WHOLE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_partial_hedge_is_valued_with_its_unmatched_remainder_and_no_second_is_opened(
        monkeypatch):
    conn = await XM._connect()
    try:
        await _start(conn)
        sent, client = XM.venue(monkeypatch, order_id="venue-hedge",
                                executions=[SPL._fill(
                                    6, 0.59, vid="vf-h6",
                                    state="ORDER_STATE_FILLED")])
        # A 6-CONTRACT HEDGE's increment is smaller than a full one's, so the
        # standalone exit here bids 0.58 (a loss on the 0.62 basis) and the
        # partial hedge still wins at both ends of the void range (SYNTHETIC).
        sup = _acquire_supplier(depth=6, exit_px=0.58)
        # ── REVIEW 1: THE BOOK COVERS 6 OF THE 10 HELD ───────────────
        got = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                 pair_inputs=sup,
                                 venue_positions=SPL.EMPTY_VENUE)
        s1 = _step(got, SPL.PRIMARY_INTENT)
        assert s1["decision"]["action"] == PC.ACTION_ACQUIRE, s1
        assert int(s1["acquisition_plan"]["quantity"]) == 6
        rec1 = (await XM.records(conn, SPL.PRIMARY_INTENT))[0]
        win = next(a for a in rec1["alternatives"] if a.get("plan_digest")
                   == rec1["chosen_plan_digest"])
        # VALUED WITH THE 4 THE HEDGE DOES NOT COVER, not as a matched slice.
        assert win["unpaired_residual_qty"] == pytest.approx(4.0), win
        assert win["unpaired_value_at_risk_usd"] == pytest.approx(
            4 * SPL.PRIMARY_PX), win
        cr = XM.creates(sent)
        assert len(cr) == 1 and int(cr[0]["quantity"]) == 6, sent
        hedge = await conn.fetchrow(
            "SELECT intent_id, residual_qty::float8 AS r, payout_event, "
            "       decision_ref FROM bettor_funded_intents "
            " WHERE portfolio_group_id=$1 AND leg_role='HEDGE'", SPL.GROUP)
        assert hedge["r"] == pytest.approx(6.0), dict(hedge)
        # ── REVIEW 2: BOTH LEGS HELD, ONE GROUP DECISION ─────────────
        got2 = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                  pair_inputs=sup,
                                  venue_positions=SPL.EMPTY_VENUE)
        s2 = _step(got2, SPL.PRIMARY_INTENT)
        g = s2["group"]
        assert g["primary_residual_qty"] == pytest.approx(10.0)
        assert g["hedge_residual_qty"] == pytest.approx(6.0)
        assert g["matched_units"] == pytest.approx(6.0)
        assert g["unpaired_qty"] == pytest.approx(4.0)
        assert g["unpaired_role"] == "PRIMARY"
        assert g["unpaired_value_at_risk_usd"] == pytest.approx(
            4 * SPL.PRIMARY_PX)
        assert g["orders_in_flight"] == []
        # NO SECOND HEDGE: named before any plan exists.
        assert s2["acquisition_ineligible"] == \
            XV.R_GROUP_ALREADY_HOLDS_A_HEDGE_LEG
        assert len(XM.creates(sent)) == 1, sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE "
            " portfolio_group_id=$1 AND leg_role='HEDGE'", SPL.GROUP) == 1
        # THE HEDGE ROW IS DECIDED WITH THE GROUP, NOT ALONE.
        sh = _step(got2, hedge["intent_id"])
        assert sh["refusal"] == XV.R_DECIDED_WITH_THE_GROUP, sh
        hrec = (await XM.records(conn, hedge["intent_id"]))[0]
        assert hrec["execution_eligibility"] == XV.E_DECIDED_BY_GROUP
        # THE RECORD VALUES THE GROUP WHOLE, ON ITS OWN ROWS.
        rec2 = (await XM.records(conn, SPL.PRIMARY_INTENT))[0]
        rg = rec2["residual_exposure"]["group"]
        assert rg["matched_units"] == pytest.approx(6.0)
        assert rg["unpaired_qty"] == pytest.approx(4.0)
        # BOTH LEGS CARRY THEIR PAYOUT AND SETTLEMENT IDENTITY.
        ids = rg["legs_identity"]
        assert ids["PRIMARY"]["payout_event"] == SPL.PAYS_ON
        assert ids["HEDGE"]["payout_event"] == hedge["payout_event"]
        # (The lifecycle fixture's leg states its settlement terms as tie and
        # void clauses; a leg built from venue prose also carries the parsed
        # per-outcome `rules`.)
        hsi = ids["HEDGE"]["settlement_identity"]
        assert hsi["void_rule"] and hsi["tie_rule"], hsi
        assert hsi["period"] == "FULL_GAME" and hsi["fixture_id"] == \
            SPL.FIXTURE, hsi
        assert ids["PRIMARY"]["gaps"] == [] and ids["HEDGE"]["gaps"] == [], ids
    finally:
        await XM.spl_clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 3 · RESTART RECOVERY
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_after_a_restart_a_lost_answer_stays_unresolved_counted_and_unsent(
        monkeypatch):
    """The hedge's answer is lost. THE RESTART: the connection is closed (no
    session lock or connection state survives) and the next pass builds all
    of its state again from the database -- `pass_once` keeps none between
    passes. Nothing is sent again; the group is ORDER_UNRESOLVED with the
    exposure counted on the unresolved intent; recovery leaves the claim
    unresolved (the order it created is itself unresolved); and an exit that
    now wins is recorded but gated, naming why."""
    conn = await XM._connect()
    try:
        await _start(conn)
        sent, client = XM.venue(
            monkeypatch, order_id="venue-hedge",
            raise_on_create=TimeoutError("the answer never came back"))
        got = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                 pair_inputs=_acquire_supplier(),
                                 venue_positions=SPL.EMPTY_VENUE)
        assert got["acquisitions"][0]["refusal"] == FX.R_LOST_ACKNOWLEDGEMENT
        assert len(XM.creates(sent)) == 1
        first = (await XM.records(conn, SPL.PRIMARY_INTENT))[0]
        assert (await XV.execution_state(
            conn, xavier_decision_id=first["xavier_decision_id"]))[
            "status"] == XV.X_UNRESOLVED
        await conn.close()

        # ── THE RESTART ──────────────────────────────────────────────
        conn = await XM._connect()
        client.orders._raise = None
        got2 = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                  pair_inputs=XM.exit_supplier(),
                                  venue_positions=SPL.EMPTY_VENUE,
                                  now=time.time() + 1.0)
        assert len(XM.creates(sent)) == 1, sent          # NOTHING NEW SENT
        assert got2["resubmitted_anything"] is False
        rc = got2["xavier_claim_recovery"]
        assert first["xavier_decision_id"] in [
            x["xavier_decision_id"] for x in rc["left_unresolved"]], rc
        resp = await XV.responsibilities(conn, account_id=SPL.ACCT,
                                         venue=SPL.VENUE)
        p = {x["intent_id"]: x for x in resp["positions"]}[SPL.PRIMARY_INTENT]
        assert p["state"] == XV.ORDER_UNRESOLVED, p
        names = {o["obligation"] for o in p["obligations"]}
        assert {XV.OB_CLAIM_UNRESOLVED, XV.OB_DISPATCH_UNRESOLVED} <= names
        exp = await FB.exposure(conn, account_id=SPL.ACCT, venue=SPL.VENUE)
        assert any(r["us_market_slug"] == SPL.SLUG_HEDGE
                   for r in exp["outstanding_orders"]), exp
        assert (await RSV.get(conn, SPL.OP_HEDGE))["reservation"][
            "state"] == RSV.AMBIGUOUS
        # THE EXIT THAT NOW WINS IS RECORDED, AND GATED BY NAME.
        s2 = _step(got2, SPL.PRIMARY_INTENT)
        assert s2["decision"]["action"] == "EXIT", s2["decision"]
        assert s2["refusal"] == XV.R_GROUP_ORDER_IN_FLIGHT, s2
        rec2 = (await XM.records(conn, SPL.PRIMARY_INTENT))[0]
        assert rec2["responsibility_state"] == XV.ORDER_UNRESOLVED
        assert rec2["execution_eligibility"].startswith(
            "%s:%s" % (XV.E_BLOCKED, XV.G_GROUP_ORDER_IN_FLIGHT))
        assert rec2["chosen_action"] == "EXIT"
        assert (await XV.execution_state(
            conn, xavier_decision_id=rec2["xavier_decision_id"]))[
            "claimed"] is False
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE kind='EXIT' "
            " AND parent_intent_id=$1", SPL.PRIMARY_INTENT) == 0
    finally:
        await XM.spl_clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_recovery_leaves_a_claim_alone_while_its_group_is_under_review():
    """A claim with nothing after it is answered from the book only when no
    review of its group is in flight; while another session holds the group's
    lock, it is skipped and stays unresolved."""
    conn, other = await XM._connect(), await XM._connect()
    try:
        await _start(conn)
        rec = await XV.record_decision(
            conn, account_id=SPL.ACCT, venue=SPL.VENUE,
            intent_id=SPL.PRIMARY_INTENT, decided_at=time.time(),
            responsibility_state=XV.HELD,
            execution_eligibility=XV.E_DISPATCHED, alternatives=[],
            reasoning={}, expected_economics={}, residual_exposure={},
            evidence={}, obligations=[], chosen_action="EXIT",
            chosen_plan_digest="plan-orphan", decision_id="dec:orphan",
            portfolio_group_id=SPL.GROUP)
        xid = rec["xavier_decision_id"]
        assert (await XV.claim_dispatch(conn, xavier_decision_id=xid,
                                        plan_digest="plan-orphan"))["claimed"]
        lock = await XV.try_group_lock(other, SPL.GROUP)
        assert lock["ok"]
        busy = await XV.recover_claims(conn, account_id=SPL.ACCT,
                                       venue=SPL.VENUE)
        assert xid in busy["skipped_in_flight"], busy
        assert (await XV.execution_state(conn, xavier_decision_id=xid))[
            "status"] == XV.X_CLAIMED_OUTCOME_UNRECORDED
        await XV.release_group_lock(other, lock)
        free = await XV.recover_claims(conn, account_id=SPL.ACCT,
                                       venue=SPL.VENUE)
        assert [r["resolution"] for r in free["recovered"]] == [
            XV.RECOVERY_NO_INTENT], free
        assert (await XV.execution_state(conn, xavier_decision_id=xid))[
            "status"] == XV.X_NOT_SENT
    finally:
        await XM.spl_clean(conn)
        await conn.close()
        await other.close()


# ════════════════════════════════════════════════════════════════════
# 4 · A FILL BETWEEN THE DECISION AND THE SEND
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("leg", ["THE_HEDGE_ORDER", "THE_ENTRY_ORDER"])
async def test_a_fill_between_the_decision_and_the_send_stops_the_stale_plan(
        monkeypatch, leg):
    """An order still working on one leg fills further AFTER the decision was
    recorded and BEFORE the send (the fill is ingested at the claim, the last
    moment before the send). The plan was valued on the old quantities, so
    it is not sent: the claim is spent with NOT_SENT naming THE_POSITION_
    CHANGED_AFTER_THE_DECISION_REVALIDATE. The next review decides again on
    what is held -- and sends."""
    conn = await XM._connect()
    try:
        await XM.spl_clean(conn)
        await SPL._seed(conn)
        sent, client = XM.venue(monkeypatch, order_id="venue-hedge")
        if leg == "THE_HEDGE_ORDER":
            # THE PRIMARY IS HELD (10) AND THE HEDGE ORDER IS WORKING: 6 OF
            # 10 FILLED, through the pass's own acquisition.
            await SPL._primary(conn)
            # the acquisition's production inputs since 880377f (SYNTHETIC)
            from tests import approved_conditional_model as ACM
            await ACM.approve(conn)
            client.orders._exec = [SPL._fill(
                6, 0.59, vid="vf-h6", state="ORDER_STATE_PARTIALLY_FILLED")]
            got = await PC.pass_once(conn, account_id=SPL.ACCT,
                                     venue=SPL.VENUE,
                                     pair_inputs=_acquire_supplier(),
                                     venue_positions=SPL.EMPTY_VENUE)
            assert got["acquisitions"][0]["submitted"] is True, got
            working = await conn.fetchval(
                "SELECT intent_id FROM bettor_funded_intents WHERE "
                " portfolio_group_id=$1 AND leg_role='HEDGE'", SPL.GROUP)
            assert await conn.fetchval(
                "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
                working) == "PARTIALLY_FILLED"
            position = SPL.PRIMARY_INTENT
        else:
            # THE ENTRY ITSELF IS WORKING: 6 OF 10 FILLED, the rest resting.
            await XM._entry(conn, intent_id="xa-working", qty=10, filled=6)
            working = position = "xa-working"
        sends_before = len(XM.creates(sent))
        real_claim = XV.claim_dispatch
        injected: list = []

        async def _fill_arrives_then_claim(conn_, **kw):
            if not injected:
                injected.append(await FB.ingest_fills(conn_, working, [
                    {"qty": 2.0, "price": 0.59 if working != position
                     else SPL.PRIMARY_PX, "venue_fill_id": "vf-late-2"}]))
            return await real_claim(conn_, **kw)

        monkeypatch.setattr(XV, "claim_dispatch", _fill_arrives_then_claim)
        got = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                 pair_inputs=XM.exit_supplier(),
                                 venue_positions=SPL.EMPTY_VENUE)
        s = _step(got, position)
        assert injected, "the fill was not injected"
        assert s["decision"]["action"] == "EXIT", s.get("decision")
        assert s["dispatch_claim"]["claimed"] is True
        assert s["refusal"] == XV.R_POSITION_CHANGED, s
        changed = {c["what"] for c in s["revalidation"]["changed"]}
        if leg == "THE_HEDGE_ORDER":
            assert "residual:%s" % working in changed, changed
            assert "filled_by_role:HEDGE" in changed, changed
            assert "matched_units" in changed, changed
        else:
            assert "residual:%s" % working in changed, changed
            assert "intent_filled" in changed, changed
        # NOTHING REACHED THE ADAPTER, AND NO EXIT INTENT WAS WRITTEN.
        assert len(XM.creates(sent)) == sends_before, sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE kind='EXIT' "
            " AND parent_intent_id=$1", position) == 0
        # THE REFUSAL IS AN EXECUTION EVENT ON THE DECISION'S HISTORY.
        rec = (await XM.records(conn, position))[0]
        assert rec["residual_exposure"]["valued_on"]["legs"], rec
        evs = await XV.execution_events(
            conn, xavier_decision_id=rec["xavier_decision_id"])
        assert [e["event_kind"] for e in evs] == [XV.K_CLAIMED, XV.K_NOT_SENT]
        ev = evs[1]["evidence"]
        ev = json.loads(ev) if isinstance(ev, str) else ev
        assert ev["refusal"] == XV.R_POSITION_CHANGED, ev
        assert (await XV.execution_state(
            conn, xavier_decision_id=rec["xavier_decision_id"]))[
            "status"] == XV.X_NOT_SENT
        # ── THE NEXT REVIEW DECIDES AGAIN, ON WHAT IS HELD, AND SENDS ──
        monkeypatch.setattr(XV, "claim_dispatch", real_claim)
        held_now = await conn.fetchval(
            "SELECT residual_qty::float8 FROM bettor_funded_intents "
            " WHERE intent_id=$1", position)
        client.orders._exec = [SPL._fill(int(held_now), 0.70, vid="vf-exit",
                                         state="ORDER_STATE_FILLED")]
        got3 = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                  pair_inputs=XM.exit_supplier(),
                                  venue_positions=SPL.EMPTY_VENUE,
                                  now=time.time() + 1.0)
        s3 = _step(got3, position)
        assert s3["revalidation"]["ok"] is True, s3
        assert s3["dispatched"] == "EXIT", s3
        cr = XM.creates(sent)
        assert len(cr) == sends_before + 1, sent
        assert int(cr[-1]["quantity"]) == int(held_now)
        assert cr[-1]["intent"] == "ORDER_INTENT_SELL_LONG"
    finally:
        await XM.spl_clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_lock_left_by_a_pass_that_raised_is_released_and_no_other_lock_is():
    """A pass that raised before releasing leaves its session holding the
    group's lock (twice, if it was re-entered). The next pass on that session
    releases every Xavier group lock it holds -- and only those: an advisory
    lock another module took on the same session is untouched."""
    conn, other = await XM._connect(), await XM._connect()
    try:
        for _ in range(2):
            assert (await XV.try_group_lock(conn, "grp:stale"))["ok"]
        await conn.fetchval("SELECT pg_advisory_lock(424242)")
        assert (await XV.try_group_lock(other, "grp:stale"))["ok"] is False
        assert await XV.release_session_group_locks(conn) == 2
        got = await XV.try_group_lock(other, "grp:stale")
        assert got["ok"] is True, got
        await XV.release_group_lock(other, got)
        assert await other.fetchval(
            "SELECT pg_try_advisory_lock(424242)") is False
    finally:
        await conn.fetchval("SELECT pg_advisory_unlock_all()")
        await conn.close()
        await other.close()
