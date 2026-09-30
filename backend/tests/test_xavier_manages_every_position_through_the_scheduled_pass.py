"""XAVIER MANAGES EVERY POSITION THROUGH THE ONE SCHEDULED PASS.

WHAT THIS PROVES, AND THROUGH WHAT. Every database test drives the scheduled
path against a migrated PostgreSQL database: `ext_pinnacle_loop.cycle(conn)`
(which runs `_funded_service` -> `manage(defer_dispatch=True)` -> `pass_once`)
through the harness of `test_xavier_management_defects_reproduced_through_the_
cycle.py`, or `bettor_funded_pair_cycle.pass_once` through the harness of
`test_the_scheduled_pair_lifecycle.py`. The venue is substituted at
`pmus._get_client` (and its boundary gate) and nowhere above it. Submission
switches are patched True INSIDE the tests only; the shipped code keeps them
off and `test_the_shipped_switches_are_still_off` asserts it.

Where a failure has to be induced (a Xavier write that fails, a claim the
database refuses), it is induced IN THE DATABASE -- a trigger scoped to this
test's account, dropped afterwards -- so the code under test is unchanged.

ALL MARKET DATA HERE IS SYNTHETIC. Nothing here is evidence about any market.

  A. ONE EXECUTION AUTHORITY: the order the adapter receives is the persisted
     Xavier decision's plan, field for field; a failed Xavier write, a refused
     or duplicate claim, or a plan that is not the ranked one sends nothing;
     `manage` never sends on the scheduled path.
  C. The spec's list: responsibility from a partial fill and from a lost
     acknowledgement, every exit action with a plan or a blocker, management
     blockers and hedge refusals on the record, the whole-position fields on
     every alternative, the real-clock refusal before a hedge send, the
     refused-hedge claim resolution, the entry stop not stopping servicing,
     the heartbeat brief, the search order, and full-game vs first-five.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import pathlib
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_learning as FL
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_funded_reservations as RSV
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_xavier as XV

from tests import test_the_scheduled_pair_lifecycle as SPL
from tests import test_xavier_management_defects_reproduced_through_the_cycle as XD

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


# ════════════════════════════════════════════════════════════════════
# SHARED HELPERS (also used by test_xavier_decides_each_group_once.py)
# ════════════════════════════════════════════════════════════════════

async def purge_xavier(conn, account_id):
    """Xavier's rows are append-only by trigger; TEST rows are removed with
    triggers disabled for this one transaction only."""
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("bettor_xavier_execution_events", "bettor_xavier_decisions"):
            await conn.execute("DELETE FROM %s WHERE account_id=$1" % t,
                               account_id)


async def spl_clean(conn):
    from tests import approved_conditional_model as ACM
    await ACM.purge(conn)
    await purge_xavier(conn, SPL.ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_operation_evidence WHERE operation_id IN "
        "(SELECT operation_id FROM bettor_funded_leg_reservations WHERE "
        " group_id IN (SELECT group_id FROM bettor_funded_portfolio_groups "
        " WHERE account_id=$1))", SPL.ACCT)
    await SPL._clean(conn)


async def hedge_supplier(conn, hold_ranking=None, **kw):
    """THE SUPPLIER FOR A TEST WHOSE PREMISE IS A DISPATCHED ACQUISITION.

    Since the common valuation gates funded dispatch (880377f), a hedge is
    sent only when it was priced through the payout-state distribution from
    an APPROVED conditional model on a MEASURED void rate, and wins robustly
    over that rate's range. This approves one on SYNTHETIC observations
    (tests/approved_conditional_model, `promote` with a named approver) and
    returns SPL's distribution-priced facts: the exit is still the best
    standalone action, and the acquisition beats it. `spl_clean` purges it."""
    from tests import approved_conditional_model as ACM
    await ACM.approve(conn)
    return SPL._distribution_pair_facts(
        hold_ranking or SPL._robust_hold_ranking(),
        calibration=await ACM.calibration(conn), **kw)


async def records(conn, intent_id):
    """Every Xavier review of one position, newest first."""
    return (await XV.history(conn, intent_id=intent_id, limit=100))[
        "decisions"]


def creates(sent):
    return [c[1] for c in sent if c[0] == "create"]


class _Orders(SPL._Orders):
    """The lifecycle harness's order surface (counts every create)."""


class _Client:
    def __init__(self, sent, *, bids=None, **kw):
        self.orders = _Orders(sent, **kw)
        self.markets = XD._Markets(bids or XD.EXIT_BOOK)


def venue(monkeypatch, *, bids=None, **kw):
    """THE SUBSTITUTED TRANSPORT, with a market surface (a sell on an `aec-`
    market reads the market's sides before it is sent) and every switch this
    path needs turned on IN THIS TEST ONLY."""
    from sportsassets import pmus
    sent: list = []
    client = _Client(sent, bids=bids, **kw)
    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
    return sent, client


def exit_plan(pos, *, at, qty, px=0.70, action="DIRECT_EXIT"):
    """ONE EXIT'S COMPLETE ORDER, built by the production `plan_for`."""
    return PC.plan_for(
        action=action, position=pos, account_id=pos["account_id"],
        venue=pos["venue"],
        selection={"selected": action, "selected_qty": float(qty),
                   "limit_price": px, "proceeds_per_contract": px,
                   "inputs_expire_at": float(at) + 120.0,
                   "assessed_at": float(at),
                   "us_market_slug": pos["us_market_slug"],
                   "intent_id": pos["intent_id"]})


def exit_supplier(*, exit_value=None, hold_value=0.10, hedge_hold=0.0,
                  qty=None, px=0.70, plan_for_candidate=None):
    """A SUPPLIER WHOSE WINNER IS AN EXIT WITH A COMPLETE PLAN.

    Built on `pass_once`'s supplier contract exactly as the production
    supplier fills it for a position with no hedge reader: HOLD and a
    DIRECT_EXIT carrying the digest of the plan it was ranked with. A hedge
    leg's row is supplied HOLD only. `plan_for_candidate(plan, pos, at)` lets
    a test file a DIFFERENT plan under the winner's digest (a substitution).

    THE ONE-MEASURE INPUTS (880377f). Since the common valuation gates funded
    dispatch, the supplier carries what the production one carries: the held
    leg's settlement terms (tests/held_contract_terms, SYNTHETIC), HOLD's
    probability and the position's real basis (`FB.remaining_basis`, the
    fills ledger), and the exit's net proceeds. HOLD keeps its stated value
    (`hold_value`), its probability is the one that value implies, and the
    exit's value is its proceeds plus any retained contracts at that same
    probability, less the basis -- one measure, so the valuation that gates
    the send and the ranking agree. With the default prices the exit (0.70)
    beats HOLD (0.63) at every void rate: a void pays 0.50."""
    from tests import held_contract_terms as HCT

    async def _supply(conn, pos, *, at):
        residual = float(pos.get("residual_qty") or 0)
        rb = await FB.remaining_basis(conn, str(pos["intent_id"]))
        bpc = rb.get("basis_per_contract")
        # A leg with nothing filled (a hedge whose answer was lost) states no
        # basis; it is supplied HOLD only below and carries no measure.
        measured = bpc is not None and residual > 0
        bpc = float(bpc) if measured else None
        p_hold = ((float(hold_value) + residual * bpc) / residual
                  if measured else None)
        base = {"ok": True,
                "held_leg": (HCT.held_leg(cost_cents=round(bpc * 100),
                                          qty=int(residual))
                             if measured else None),
                "sport_permits_tie": HCT.SPORT_PERMITS_TIE,
                "candidate_legs": [],
                "decision_id": "dec:xs:%s:%.3f" % (pos["intent_id"], at),
                "operation_id": "op:xs:%s:%.3f" % (pos["intent_id"], at),
                "region_probabilities": None,
                "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                "holding_policy": FL.POLICY_MAY_EXIT_EARLY,
                "limits": None, "fee_usd": None, "depth": None,
                "incremental": None, "capital_duration_h": None,
                "hedge_decision_record": None}
        hold = {"action": "HOLD", "qty": residual, "value_usd": hold_value,
                "expected_net_usd": hold_value,
                "value_per_contract": p_hold,
                "basis_per_contract_valued": bpc,
                "downside_usd": (-round(residual * bpc, 6) if measured
                                 else -6.20),
                "incremental_capital_usd": 0.0, "capital_duration_h": 26.0,
                "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                "execution_secured": True}
        if str(pos.get("leg_role") or "PRIMARY") == "HEDGE":
            return dict(base, hold_ranking={
                "version": "T", "not_rankable": [],
                "candidates": [dict(hold, value_usd=hedge_hold,
                                    expected_net_usd=hedge_hold,
                                    value_per_contract=(
                                        (float(hedge_hold) + residual * bpc)
                                        / residual if measured else None))]})
        plan = exit_plan(pos, at=at, qty=qty or residual, px=px)
        cash = round(float(plan.quantity) * float(plan.proceeds_per_contract),
                     6)
        value = (round(cash + (residual - float(plan.quantity)) * p_hold
                       - residual * bpc, 6)
                 if exit_value is None else exit_value)
        cand = {"action": "DIRECT_EXIT", "qty": plan.quantity,
                "value_usd": value, "expected_net_usd": value,
                "downside_usd": value, "incremental_capital_usd": 0.0,
                "capital_duration_h": 0.0, "cash_now_usd": cash,
                "evidence_quality": FD.EVIDENCE_VENUE_IMPLIED,
                "execution_secured": False, "plan_digest": plan.digest,
                "limit_price": plan.limit_price,
                "proceeds_per_contract": plan.proceeds_per_contract,
                "inputs_expire_at": plan.inputs_expire_at,
                "intent_id": pos["intent_id"]}
        filed = plan if plan_for_candidate is None else plan_for_candidate(
            plan, pos, at)
        return dict(base, hold_ranking={"version": "T", "not_rankable": [],
                                        "candidates": [hold, cand]},
                    executable_plans_by_digest={plan.digest: filed},
                    executable_plans_by_action={"DIRECT_EXIT": filed})
    return _supply


async def refuse_inserts(conn, *, table, account_id, when="TRUE"):
    """INDUCE A DATABASE FAILURE for this account only: a BEFORE INSERT
    trigger that raises. Returns the name to drop."""
    name = "xa_test_refuse_%s" % table
    await conn.execute(
        "CREATE OR REPLACE FUNCTION %s() RETURNS trigger AS $$ BEGIN "
        "RAISE EXCEPTION 'induced by the test: this insert is refused'; "
        "END; $$ LANGUAGE plpgsql" % name)
    await conn.execute("DROP TRIGGER IF EXISTS %s_trg ON %s" % (name, table))
    await conn.execute(
        "CREATE TRIGGER %s_trg BEFORE INSERT ON %s FOR EACH ROW WHEN "
        "(NEW.account_id = '%s' AND (%s)) EXECUTE FUNCTION %s()"
        % (name, table, account_id, when, name))
    return name, table


async def drop_refusal(conn, handle):
    name, table = handle
    await conn.execute("DROP TRIGGER IF EXISTS %s_trg ON %s" % (name, table))
    await conn.execute("DROP FUNCTION IF EXISTS %s()" % name)


def _step(out, intent_id):
    pcy = (out.get("funded_servicing") or {}).get("pair_cycle") or out
    return next(s for s in pcy["considered"]
                if str(s.get("intent_id")) == str(intent_id))


# ════════════════════════════════════════════════════════════════════
# 0 · THE SWITCHES ARE STILL OFF IN THE SHIPPED CODE
# ════════════════════════════════════════════════════════════════════

def test_the_shipped_switches_are_still_off():
    import importlib
    for mod, name in ((FX, "FUNDED_SUBMISSION_ENABLED"),
                      (EX, "REAL_ORDER_SUBMISSION_ENABLED"),
                      (FM, "FUNDED_EXIT_SUBMISSION_ENABLED")):
        fresh = importlib.import_module(mod.__name__)
        src = pathlib.Path(fresh.__file__).read_text()
        assert "%s = False" % name in src, (mod.__name__, name)


def test_the_exit_side_rule_is_the_adapters():
    from sportsassets import pmus
    for opened, side in XV.EXIT_SIDE_OF.items():
        assert pmus._exit_intent("aec-x", opened) == side


# ════════════════════════════════════════════════════════════════════
# C · RESPONSIBILITY
# ════════════════════════════════════════════════════════════════════

async def _entry(conn, *, intent_id, qty, filled=None, lost=False,
                 slug=SPL.SLUG_PRIMARY, event=SPL.EVENT):
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=SPL.ACCT, venue=SPL.VENUE,
        venue_class="FUNDED", us_market_slug=slug,
        event_key=event, order_intent=FX.LONG,
        limit_price=SPL.PRIMARY_PX, quantity=qty,
        collateral_usd=FX.collateral_for(SPL.PRIMARY_PX, qty, FX.LONG),
        effective_digest="d", payout_event=SPL.PAYS_ON, held_is_long=True,
        portfolio_group_id=None, leg_role="PRIMARY",
        group_structure="INDIRECT_MIDDLE")
    assert got.get("ok"), got
    if lost:
        await FB.mark_send_attempted(conn, intent_id)
        await FB.mark_unresolved(conn, intent_id,
                                 reason="the answer never came back")
        return got
    await FB.record_acknowledgement(conn, intent_id,
                                    venue_order_id="vo-%s" % intent_id,
                                    status="open")
    if filled:
        await FB.ingest_fills(conn, intent_id, [
            {"qty": float(filled), "price": SPL.PRIMARY_PX,
             "venue_fill_id": "vf-%s" % intent_id}])
    return got


async def _no_inputs(conn, pos, *, at):
    return {"ok": False, "refusal": "TEST_SUPPLIES_NO_DECISION_INPUTS"}


@pg
@pytest.mark.asyncio
async def test_a_partial_entry_fill_makes_the_position_xaviers():
    """An acknowledged entry with NO fill is not yet Xavier's; its first
    fill -- 4 of 10, the rest still working -- makes it his: ORDER_OUTSTANDING,
    residual 4, and the scheduled pass records it."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        # ACKNOWLEDGED, NOTHING FILLED: not yet Xavier's.
        await _entry(conn, intent_id="xa-partial", qty=10)
        resp = await XV.responsibilities(conn, account_id=SPL.ACCT,
                                         venue=SPL.VENUE)
        assert resp["ok"] is True, resp
        assert "xa-partial" not in {p["intent_id"]
                                    for p in resp["positions"]}, resp
        # THE FIRST FILL -- 4 of 10 -- makes it his.
        await FB.ingest_fills(conn, "xa-partial", [
            {"qty": 4.0, "price": SPL.PRIMARY_PX, "venue_fill_id": "vf-p4"}])
        resp = await XV.responsibilities(conn, account_id=SPL.ACCT,
                                         venue=SPL.VENUE)
        mine = {p["intent_id"]: p for p in resp["positions"]}
        p = mine["xa-partial"]
        assert p["filled_qty"] == pytest.approx(4.0)
        assert p["residual_qty"] == pytest.approx(4.0)
        assert p["state"] == XV.ORDER_OUTSTANDING
        names = {o["obligation"] for o in p["obligations"]}
        assert {XV.OB_RESIDUAL, XV.OB_ENTRY_OUTSTANDING} <= names, names
        got = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                 pair_inputs=_no_inputs,
                                 venue_positions=SPL.EMPTY_VENUE)
        assert got["ok"] is True, got
        rec = (await records(conn, "xa-partial"))[0]
        assert rec["responsibility_state"] == XV.ORDER_OUTSTANDING
        assert rec["residual_exposure"]["held_qty"] == pytest.approx(4.0)
    finally:
        await spl_clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_lost_acknowledgement_with_nothing_filled_stays_xaviers():
    """Zero residual, zero fills, the answer lost: the venue may hold an
    order or a fill, so the position is Xavier's (ORDER_UNRESOLVED) and the
    pass records it with the obligation named."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await _entry(conn, intent_id="xa-lost", qty=10, lost=True)
        resp = await XV.responsibilities(conn, account_id=SPL.ACCT,
                                         venue=SPL.VENUE)
        p = {x["intent_id"]: x for x in resp["positions"]}["xa-lost"]
        assert p["residual_qty"] == 0.0 and p["filled_qty"] == 0.0
        assert p["state"] == XV.ORDER_UNRESOLVED
        assert XV.OB_ENTRY_UNRESOLVED in {o["obligation"]
                                          for o in p["obligations"]}
        await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                           pair_inputs=_no_inputs,
                           venue_positions=SPL.EMPTY_VENUE)
        rec = (await records(conn, "xa-lost"))[0]
        assert rec["responsibility_state"] == XV.ORDER_UNRESOLVED
        assert XV.OB_ENTRY_UNRESOLVED in {
            o["obligation"] for o in rec["obligations"]}
        assert rec["chosen_action"] is None
    finally:
        await spl_clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# A · THE ORDER SENT IS THE PERSISTED XAVIER PLAN -- THROUGH cycle()
# ════════════════════════════════════════════════════════════════════

async def _xd_start(conn):
    await XD._clean(conn)
    await XD._seed(conn)
    await XD._entry(conn, qty=10, limit=0.60)
    await XD._probability(conn, p=0.55)


@pg
@pytest.mark.asyncio
async def test_the_exit_sent_is_the_persisted_xavier_plan_field_for_field(
        monkeypatch):
    """REDUCE wins through the scheduled cycle, with only the transport
    substituted. The adapter's create carries exactly the persisted plan:
    instrument, side, quantity and price bound; the exit intent names the
    Xavier decision, the ledger decision and the digest; the record and the
    claim precede the order; `manage` itself sent nothing."""
    conn = await _connect()
    try:
        await _xd_start(conn)
        out, sent = await XD._run_cycle(conn, monkeypatch,
                                        bids=XD.REDUCE_BOOK)
        svc = out["funded_servicing"]
        assert svc and svc["ok"] is True, svc
        rec = (await records(conn, "xdf-a"))[0]
        assert rec["chosen_action"] == "REDUCE", rec["chosen_action"]
        plan = rec["evidence"]["chosen_plan"]
        assert plan["digest"] == rec["chosen_plan_digest"]
        cr = creates(sent)
        assert len(cr) == 1, sent
        c = cr[0]
        assert c["marketSlug"] == plan["us_market_slug"] == XD.SLUG
        assert c["intent"] == plan["order_intent"] == "ORDER_INTENT_SELL_LONG"
        assert int(c["quantity"]) == int(plan["quantity"]) == 7
        assert float(c["price"]["value"]) == pytest.approx(plan["limit_price"])
        # THE EVIDENCE REFERENCE, on the order itself.
        ex = await conn.fetchrow(
            "SELECT decision_ref, created_at FROM bettor_funded_intents "
            " WHERE parent_intent_id='xdf-a' AND kind='EXIT'")
        ref = ex["decision_ref"]
        ref = json.loads(ref) if isinstance(ref, str) else ref
        assert ref["xavier_decision_id"] == rec["xavier_decision_id"]
        assert ref["plan_digest"] == rec["chosen_plan_digest"]
        assert ref["decision_id"] == rec["decision_id"]
        led = await XD._ledger(conn)
        assert led["decision_id"] == rec["decision_id"]
        won = [x for x in led["ranked"] if x.get("plan_digest")
               == rec["chosen_plan_digest"]]
        assert won and won[0]["action"] == "REDUCE", led["ranked"]
        # THE RECORD, THEN THE CLAIM, THEN THE ORDER.
        assert rec["recorded_at"] <= ex["created_at"]
        evs = await XV.execution_events(
            conn, xavier_decision_id=rec["xavier_decision_id"])
        kinds = [e["event_kind"] for e in evs]
        assert kinds[0] == XV.K_CLAIMED, kinds
        assert evs[0]["plan_digest"] == rec["chosen_plan_digest"]
        assert XV.K_ACK in kinds
        # `manage` SELECTED AND DEFERRED; IT SENT NOTHING ITSELF.
        assert svc.get("exits") == [], svc.get("exits")
        assert svc["defer_dispatch"] is True
        # AND THE ONE ORDER CAME THROUGH THE PASS'S DISPATCH.
        assert _step(out, "xdf-a")["dispatched"] == "REDUCE"
    finally:
        await XD._clean(conn)
        await conn.close()


def test_manage_is_never_called_to_send_on_a_production_path():
    """No production module calls `manage` without `defer_dispatch=True`: an
    exit is selected there and sent only as the persisted winner of the one
    ranking. (Tests may call it directly to exercise `submit_exit`.)"""
    root = pathlib.Path(PC.__file__).resolve().parent
    offenders, calls = [], 0
    import warnings
    for path in root.rglob("*.py"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(
                    f, "id", None)
                if name != "manage":
                    continue
                calls += 1
                kw = {k.arg: k.value for k in node.keywords}
                v = kw.get("defer_dispatch")
                if not (isinstance(v, ast.Constant) and v.value is True):
                    offenders.append("%s:%d" % (path, node.lineno))
    assert calls >= 1, "the scheduled caller of manage was not found"
    assert offenders == [], offenders


@pg
@pytest.mark.asyncio
async def test_the_hedge_sent_is_the_persisted_xavier_plan_field_for_field(
        monkeypatch):
    """The acquisition that wins is sent exactly as Xavier persisted it, and
    the hedge intent names the decision, the digest and how it settles."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        _, sent, _c = SPL._transport(monkeypatch, order_id="venue-hedge")
        got = await PC.pass_once(
            conn, account_id=SPL.ACCT, venue=SPL.VENUE,
            pair_inputs=await hedge_supplier(conn),
            venue_positions=SPL.EMPTY_VENUE)
        step = _step(got, SPL.PRIMARY_INTENT)
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE, step
        rec = (await records(conn, SPL.PRIMARY_INTENT))[0]
        plan = rec["evidence"]["chosen_plan"]
        assert plan["kind"] == "ACQUISITION"
        assert plan["digest"] == rec["chosen_plan_digest"] == step[
            "decision"]["selected"]["plan_digest"]
        cr = creates(sent)
        assert len(cr) == 1, sent
        c = cr[0]
        assert c["marketSlug"] == plan["us_market_slug"] == SPL.SLUG_HEDGE
        assert c["intent"] == plan["order_intent"] == plan["side"]
        assert int(c["quantity"]) == int(plan["quantity"])
        assert float(c["price"]["value"]) == pytest.approx(plan["limit_price"])
        h = await conn.fetchrow(
            "SELECT decision_ref, payout_event FROM bettor_funded_intents "
            " WHERE intent_id=$1", step["acquisition"]["intent_id"])
        ref = h["decision_ref"]
        ref = json.loads(ref) if isinstance(ref, str) else ref
        assert ref["xavier_decision_id"] == rec["xavier_decision_id"]
        assert ref["plan_digest"] == rec["chosen_plan_digest"]
        assert ref["decision_id"] == rec["decision_id"] == SPL.DECISION
        # BOTH HALVES OF ITS IDENTITY: what it pays on and how it settles.
        assert h["payout_event"]
        si = ref.get("settlement_identity") or {}
        assert si.get("payout_event") == h["payout_event"], ref
        assert si.get("period") == IS.PERIOD_FULL
        assert si.get("fixture_id") == SPL.FIXTURE
        evs = await XV.execution_events(
            conn, xavier_decision_id=rec["xavier_decision_id"])
        assert evs[0]["event_kind"] == XV.K_CLAIMED
        assert evs[0]["plan_digest"] == plan["digest"]
    finally:
        await spl_clean(conn)
        await conn.close()


# ── A · WHAT STOPS A SEND ─────────────────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_a_xavier_write_the_database_refuses_sends_nothing(monkeypatch):
    conn = await _connect()
    handle = None
    try:
        await _xd_start(conn)
        handle = await refuse_inserts(conn, table="bettor_xavier_decisions",
                                      account_id=XD.ACCT)
        out, sent = await XD._run_cycle(conn, monkeypatch,
                                        bids=XD.REDUCE_BOOK)
        step = _step(out, "xdf-a")
        assert step["refusal"] == XV.R_XAVIER_RECORD_NOT_PERSISTED, step
        assert step["dispatched"] is None
        assert creates(sent) == [], sent
        assert [k for k, _ in sent if k == "preview"] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE "
            " parent_intent_id='xdf-a'") == 0
        # THE LEDGER DECISION EXISTS -- it is Xavier's record that failed.
        assert (await XD._ledger(conn))["action"] == "REDUCE"
    finally:
        if handle:
            await drop_refusal(conn, handle)
        await XD._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_dispatch_claim_the_database_refuses_sends_nothing(
        monkeypatch):
    conn = await _connect()
    handle = None
    try:
        await _xd_start(conn)
        handle = await refuse_inserts(
            conn, table="bettor_xavier_execution_events", account_id=XD.ACCT,
            when="NEW.event_kind = 'DISPATCH_CLAIMED'")
        out, sent = await XD._run_cycle(conn, monkeypatch,
                                        bids=XD.REDUCE_BOOK)
        step = _step(out, "xdf-a")
        assert step["refusal"] == XV.R_DISPATCH_NOT_CLAIMED, step
        assert step["dispatch_claim"]["claimed"] is False
        assert creates(sent) == [], sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE "
            " parent_intent_id='xdf-a'") == 0
        rec = (await records(conn, "xdf-a"))[0]
        assert rec["chosen_action"] == "REDUCE"
        st = await XV.execution_state(
            conn, xavier_decision_id=rec["xavier_decision_id"])
        assert st["claimed"] is False
    finally:
        if handle:
            await drop_refusal(conn, handle)
        await XD._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_replayed_review_is_refused_by_its_claim_and_sends_nothing(
        monkeypatch):
    """THE PROCESS DIES IMMEDIATELY AFTER THE CLAIM COMMITS, before anything
    is sent. The same review replayed (same position, same instant) finds its
    record, recovery answers the orphaned claim from the book (no intent
    names it, so nothing was sent) -- and the claim, already spent, refuses
    the second send. The adapter receives nothing in either run."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        _, sent, _c = SPL._transport(monkeypatch, order_id="venue-hedge")
        real_claim = XV.claim_dispatch

        async def _claim_then_die(conn_, **kw):
            got = await real_claim(conn_, **kw)
            assert got["claimed"] is True, got
            raise RuntimeError("the process died after the claim committed")

        monkeypatch.setattr(XV, "claim_dispatch", _claim_then_die)
        t0 = time.time()
        supplier = await hedge_supplier(conn)
        with pytest.raises(RuntimeError, match="died after the claim"):
            await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                               pair_inputs=supplier,
                               venue_positions=SPL.EMPTY_VENUE, now=t0)
        assert creates(sent) == []
        monkeypatch.setattr(XV, "claim_dispatch", real_claim)
        # THE RESTART: a new connection, the same review replayed.
        await conn.close()
        conn = await _connect()
        got = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                 pair_inputs=supplier,
                                 venue_positions=SPL.EMPTY_VENUE, now=t0)
        rec_ = got["xavier_claim_recovery"]
        assert rec_["recovered"] and rec_["recovered"][0]["resolution"] == \
            XV.RECOVERY_NO_INTENT, rec_
        step = _step(got, SPL.PRIMARY_INTENT)
        assert step["refusal"] == XV.R_DISPATCH_NOT_CLAIMED, step
        assert step["dispatch_claim"]["refusal"] == XV.R_ALREADY_CLAIMED
        assert creates(sent) == [] and [k for k, _ in sent] == [], sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations WHERE "
            " group_id=$1", SPL.GROUP) == 0
    finally:
        await spl_clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_plan_that_is_not_the_ranked_one_is_never_sent(monkeypatch):
    """The winner's digest names one plan; a DIFFERENT plan (twice the size)
    is filed under it. Xavier's record does not adopt it, the binding refuses,
    no claim is taken and the adapter receives nothing."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        sent, _c = venue(monkeypatch)

        def _swap(plan, pos, at):
            return exit_plan(pos, at=at, qty=float(plan.quantity) / 2)

        got = await PC.pass_once(
            conn, account_id=SPL.ACCT, venue=SPL.VENUE,
            pair_inputs=exit_supplier(plan_for_candidate=_swap),
            venue_positions=SPL.EMPTY_VENUE)
        step = _step(got, SPL.PRIMARY_INTENT)
        assert step["decision"]["action"] == "EXIT", step
        assert step["refusal"] == PC.R_PLAN_NOT_THE_RANKED_ONE, step
        assert "dispatch_claim" not in step
        assert creates(sent) == [], sent
        rec = (await records(conn, SPL.PRIMARY_INTENT))[0]
        assert rec["chosen_action"] is None
        assert rec["execution_eligibility"] == "BLOCKED:%s" % XV.G_NO_PLAN
    finally:
        await spl_clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_positive_control_the_same_exit_unsubstituted_is_sent(
        monkeypatch):
    """The harness above, with the ranked plan filed under its own digest:
    the exit IS sent, as persisted. The refusal above is the substitution."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        sent, _c = venue(monkeypatch, executions=[SPL._fill(
            10, 0.70, vid="vf-exit", state="ORDER_STATE_FILLED")])
        got = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                 pair_inputs=exit_supplier(),
                                 venue_positions=SPL.EMPTY_VENUE)
        step = _step(got, SPL.PRIMARY_INTENT)
        assert step["dispatched"] == "EXIT", step
        cr = creates(sent)
        assert len(cr) == 1, sent
        plan = (await records(conn, SPL.PRIMARY_INTENT))[0]["evidence"][
            "chosen_plan"]
        assert int(cr[0]["quantity"]) == int(plan["quantity"]) == 10
        assert cr[0]["intent"] == plan["order_intent"]
        assert float(cr[0]["price"]["value"]) == pytest.approx(
            plan["limit_price"])
    finally:
        await spl_clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# C · EVERY ACTION VISIBLE ON THE RECORD, WITH THE SAME ECONOMICS
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_every_exit_action_and_management_blocker_is_on_the_record(
        monkeypatch):
    conn = await _connect()
    try:
        await _xd_start(conn)
        await XD._run_cycle(conn, monkeypatch, bids=XD.REDUCE_BOOK)
        rec = (await records(conn, "xdf-a"))[0]
        alts = rec["alternatives"]
        by_action: dict = {}
        for a in alts:
            by_action.setdefault(a.get("action"), []).append(a)
        # EVERY EXIT ACTION: ranked WITH its plan, or not rankable WITH its
        # exact blocker -- never absent, never ranked without a plan.
        for act in ("DIRECT_EXIT", "REDUCE"):
            assert act in by_action, sorted(by_action)
            for a in by_action[act]:
                if a["rankable"]:
                    assert a.get("plan_digest"), a
                else:
                    assert a.get("blocker"), a
        # MANAGEMENT'S OWN BLOCKERS reach Xavier's record.
        mgmt = {"HOLD_TO_SETTLEMENT", "POST_COMPLEMENT", "MERGE"}
        assert mgmt & set(by_action), sorted(by_action)
        for act in mgmt & set(by_action):
            assert all(a.get("blocker") and a["rankable"] is False
                       for a in by_action[act]), by_action[act]
        for a in by_action.get("TAKE_COMPLEMENT") or []:
            assert a["blocker"] in (
                "ON_PMUS_BUYING_THE_OTHER_SIDE_NETS_THE_POSITION_SEE_REDUCE",
            ) or a["rankable"] is False, a
    finally:
        await XD._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_every_alternative_carries_the_whole_position_fields(
        monkeypatch):
    """The same standard fields on every rankable alternative, each either a
    number or None WITH its reason; HOLD's worst case is the position losing
    its basis; a depth-limited REDUCE's worst case is the realised slice plus
    the RETAINED contracts losing theirs (not held at their expectation)."""
    conn = await _connect()
    try:
        await _xd_start(conn)
        await XD._run_cycle(conn, monkeypatch, bids=XD.REDUCE_BOOK)
        rec = (await records(conn, "xdf-a"))[0]
        basis = rec["residual_exposure"]["basis_per_contract"]
        assert basis == pytest.approx(0.60)
        ranked = [a for a in rec["alternatives"] if a.get("rankable")]
        assert {a["action"] for a in ranked} >= {"HOLD", "REDUCE"}, ranked
        for a in ranked:
            for f in XV.ECONOMIC_FIELDS:
                assert f in a, (a["action"], f)
                if a[f] is None:
                    assert f in (a.get("field_reasons") or {}), (
                        a["action"], f, a.get("field_reasons"))
            assert a["limits_check"]["status"], a
            assert a["execution_uncertainty"] in (XV.EXEC_NO_ORDER,
                                                  XV.EXEC_FOK)
        hold = next(a for a in ranked if a["action"] == "HOLD")
        assert hold["worst_case_net_usd"] == pytest.approx(-0.60 * 10)
        assert hold["worst_case_remaining_loss_usd"] == pytest.approx(6.0)
        assert hold["capital_required_usd"] == 0.0
        assert hold["execution_uncertainty"] == XV.EXEC_NO_ORDER
        red = next(a for a in ranked if a["action"] == "REDUCE")
        kept = 10 - float(red["qty"])
        assert kept == pytest.approx(3.0)
        assert red["unpaired_residual_qty"] == pytest.approx(kept)
        assert red["unpaired_value_at_risk_usd"] == pytest.approx(0.60 * kept)
        # THE RETAINED 3 LOSING THEIR 0.60 BASIS, beside the realised slice:
        # worst = expected-at-settlement-of-the-kept-part replaced by -1.80.
        assert red["worst_case_net_usd"] is not None, red
        assert red["worst_case_net_usd"] < red["expected_net_usd"]
        assert red["worst_case_remaining_loss_usd"] == pytest.approx(
            max(0.0, -red["worst_case_net_usd"]))
        slice_v = red["worst_case_net_usd"] + 0.60 * kept
        # the realised slice is 4 @ 0.75 + 3 @ 0.70 less fees: positive and
        # no more than its gross.
        assert 0.0 < slice_v <= 4 * 0.75 + 3 * 0.70 + 1e-9, (slice_v, red)
        assert red["capital_released_usd"] is not None
        # THE CAPITAL DURATION IS NOT INVENTED: no scheduled end is stated.
        assert hold["capital_duration_h"] is None
        assert hold["field_reasons"]["capital_duration_h"] in (
            XV.CAPITAL_END_NOT_STATED, "THE_CATALOGUE_GAME_START_IS_NOT_KNOWN")
        assert rec["reasoning"]["tie_break"] == FD.TIE_BREAK
    finally:
        await XD._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_hedge_searchs_refusals_reach_the_record(monkeypatch):
    """Three decoys are rejected by discovery (not distinct, another fixture,
    another period). HOLD wins here, and the record still carries every
    refusal the search made, by name and stage."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        _, sent, _c = SPL._transport(monkeypatch, order_id="venue-hedge")
        hr = SPL._hold_ranking()
        for c in hr["candidates"]:
            if c["action"] == "HOLD":
                c["value_usd"] = c["expected_net_usd"] = 9.50
        await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                           pair_inputs=SPL._pair_facts(hr),
                           venue_positions=SPL.EMPTY_VENUE)
        rec = (await records(conn, SPL.PRIMARY_INTENT))[0]
        assert rec["chosen_action"] == "HOLD"
        assert rec["execution_eligibility"] == XV.E_HOLD
        hs = [a for a in rec["alternatives"]
              if a.get("blocker") == "HEDGE_SEARCH_REFUSALS"]
        assert len(hs) == 1, rec["alternatives"]
        assert sum(hs[0]["refusals_by_stage"]["discover"].values()) == 3
        assert hs[0]["total_refused"] >= 3
        # THE ACQUISITION ITSELF WAS RANKED (it lost on its number).
        acq = [a for a in rec["alternatives"] if a.get("candidate_id")
               == SPL.HEDGE_ID and a.get("rankable")]
        assert acq and acq[0]["capital_required_usd"] > 0, rec["alternatives"]
        assert acq[0]["search_screen"]["is_a_purchase_rule"] is False
        assert creates(sent) == []
    finally:
        await spl_clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# C · EXECUTION REALITIES
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_hedge_is_refused_on_the_real_clock_immediately_before_it_is_sent(
        monkeypatch):
    """The pass started long enough ago that the quote's inputs, alive at
    the pass-start instant, are dead on the real clock by the time the hedge
    would leave. Every earlier check used the pass-start instant; the one
    immediately before boundary 1 reads the clock and refuses. No intent row,
    no live claim on the leg, nothing sent."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        _, sent, _c = SPL._transport(monkeypatch, order_id="venue-hedge")
        delay = SPL._processing_delay_bound()
        got = await PC.pass_once(
            conn, account_id=SPL.ACCT, venue=SPL.VENUE,
            pair_inputs=await hedge_supplier(conn),
            venue_positions=SPL.EMPTY_VENUE, now=time.time() - delay - 5.0)
        step = _step(got, SPL.PRIMARY_INTENT)
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE, step
        acq = got["acquisitions"][0]
        assert acq["refusal"] == FX.R_INPUTS_EXPIRED_AT_SEND, acq
        sub = acq.get("submission") or {}
        assert sub.get("inputs_expiry_at_send", {}).get("clock") == "REAL"
        assert creates(sent) == [] and [k for k, _ in sent] == [], sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE "
            " portfolio_group_id=$1 AND leg_role='HEDGE'", SPL.GROUP) == 0
        live = [r for r in await RSV.live(conn, group_id=SPL.GROUP)]
        assert live == [], live
    finally:
        await spl_clean(conn)
        await conn.close()


def _refused_in_the_answer():
    """The venue answered, named no order, and REFUSED it explicitly."""
    return [{"id": "vx-rej", "type": "EXECUTION_TYPE_REJECTED",
             "order": {"state": "ORDER_STATE_REJECTED"}}]


class _NoIdOrders(SPL._Orders):
    def create(self, params):
        self.sent.append(("create", dict(params)))
        self.creates += 1
        return {"executions": list(self._exec or ())}


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["EXPLICIT_REFUSAL", "RAISED"])
async def test_a_hedge_refused_in_the_answer_releases_its_claim_and_an_unknown_does_not(
        monkeypatch, answer):
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        if answer == "RAISED":
            _, sent, client = SPL._transport(
                monkeypatch, order_id="venue-hedge",
                raise_on_create=TimeoutError("the answer never came back"))
        else:
            _, sent, client = SPL._transport(monkeypatch)
            client.orders = _NoIdOrders(sent,
                                        executions=_refused_in_the_answer())
        got = await PC.pass_once(
            conn, account_id=SPL.ACCT, venue=SPL.VENUE,
            pair_inputs=await hedge_supplier(conn),
            venue_positions=SPL.EMPTY_VENUE)
        assert len(creates(sent)) == 1
        res = (await RSV.get(conn, SPL.OP_HEDGE))["reservation"]
        intent = await conn.fetchrow(
            "SELECT state, venue_order_id FROM bettor_funded_intents "
            " WHERE portfolio_group_id=$1 AND leg_role='HEDGE'", SPL.GROUP)
        rec = (await records(conn, SPL.PRIMARY_INTENT))[0]
        st = await XV.execution_state(
            conn, xavier_decision_id=rec["xavier_decision_id"])
        if answer == "EXPLICIT_REFUSAL":
            assert intent["state"] == "REJECTED" and \
                intent["venue_order_id"] is None, dict(intent)
            assert res["state"] == RSV.RELEASED, res
            ev = await conn.fetchval(
                "SELECT count(*) FROM bettor_funded_operation_evidence "
                " WHERE operation_id=$1 AND kind=$2", SPL.OP_HEDGE,
                RSV.EV_REFUSED_IN_ANSWER)
            assert ev == 1
            assert st["status"] == XV.X_REFUSED, st
        else:
            assert intent["state"] == "UNRESOLVED", dict(intent)
            assert res["state"] == RSV.AMBIGUOUS, res
            assert st["status"] == XV.X_UNRESOLVED, st
            exp = await FB.exposure(conn, account_id=SPL.ACCT,
                                    venue=SPL.VENUE)
            assert any(r["us_market_slug"] == SPL.SLUG_HEDGE
                       for r in exp["outstanding_orders"]), exp
        assert got["resubmitted_anything"] is False
    finally:
        await spl_clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# C · THE ENTRY STOP DOES NOT STOP XAVIER; THE HEARTBEAT SAYS WHAT HE DID
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_entry_stop_does_not_stop_xavier_and_the_heartbeat_carries_his_brief(
        monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await _connect()
    try:
        await _xd_start(conn)
        out, sent = await XD._run_cycle(conn, monkeypatch,
                                        bids=XD.REDUCE_BOOK)
        # THE ENTRY LOOP IS STOPPED (`_running` says so) ...
        assert out["state"] == "STOPPED" and out["ran"] is False, out
        # ... AND XAVIER STILL REVIEWED, RECORDED AND ACTED.
        assert (await records(conn, "xdf-a"))
        assert len(creates(sent)) == 1
        dig = L._servicing_digest(out["funded_servicing"])
        briefs = [b for b in dig["xavier"] if b.get("intent_id") == "xdf-a"]
        assert briefs, dig["xavier"]
        b = briefs[0]
        for k in ("state", "chosen_action", "eligibility", "top_blockers",
                  "next_review_at", "xavier_decision_id"):
            assert k in b, (k, b)
        assert b["chosen_action"] == "REDUCE"
        assert b["next_review_at"] == pytest.approx(
            out["funded_servicing"]["pair_cycle"]["at"] + L.CYCLE_S)
        hb = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", L.HEARTBEAT_KEY)
        hb = json.loads(hb) if isinstance(hb, str) else hb
        assert [x for x in (hb.get("funded_servicing") or {}).get("xavier")
                or [] if x.get("intent_id") == "xdf-a"], hb.get(
            "funded_servicing")
    finally:
        await XD._clean(conn)
        await conn.close()


def test_the_default_review_interval_is_the_scheduled_cycle():
    from sportsassets.workers import ext_pinnacle_loop as L
    assert XV.DEFAULT_REVIEW_INTERVAL_S == float(L.CYCLE_S)


# ════════════════════════════════════════════════════════════════════
# C · THE SEARCH ORDER, AND FULL GAME VS FIRST FIVE
# ════════════════════════════════════════════════════════════════════

def test_a_first_five_contract_is_never_admitted_as_protection_for_a_full_game_position():
    """A first-five winner is not a graded variable here at all (no period of
    ours is 'first five innings'), so no leg is built from it; and a leg of
    ANY other period is rejected by the grading key against a full-game
    holding -- never admitted, whatever its title says."""
    from sportsassets import bettor_funded_hedge_supply as HS
    for st in ("baseball_team_first_five_innings_winner",
               "baseball_team_first_five_winner",
               "baseball_team_first_five_spread"):
        got = HS.derive_kind({"sports_type": st, "side_norm": "hou"})
        assert got["kind"] is None and got["refusal"], (st, got)
    held = dataclasses.replace(IS.BEARS_MONEYLINE, condition_id="held#L",
                               quantity=10, cost_cents_per_unit=55)
    for period in (IS.PERIOD_H1, IS.PERIOD_H2, IS.PERIOD_Q1):
        other = dataclasses.replace(IS.PANTHERS_PLUS_4_5,
                                    condition_id="p-%s#S" % period,
                                    period=period, quantity=10,
                                    cost_cents_per_unit=40)
        found = PC.discover(held_leg=held, candidate_legs=[other],
                            sport_permits_tie=False, fixture_can_void=False,
                            fixture_can_postpone=False)
        assert found["admitted"] == [], (period, found)
        assert found["rejected"], found


def _gap_leg():
    """Panthers -10.5: never wins together with the Bears moneyline."""
    from fractions import Fraction
    return dataclasses.replace(
        IS.PANTHERS_PLUS_4_5,
        condition_id="aec-nfl-chi-car-2026-09-13-car-minus-10h#"
                     "ORDER_INTENT_BUY_LONG",
        backs="B", line=Fraction(21, 2), quantity=10,
        cost_cents_per_unit=20)


@pg
@pytest.mark.asyncio
async def test_the_search_examines_an_under_a_dollar_middle_first_and_value_still_decides(
        monkeypatch):
    """TWO ADMITTED STRUCTURES, catalogue order: a non-middle first, then a
    middle whose matched units cost 95 cents. The search examines the middle
    FIRST and screens every claimed floor -- and the decision still chooses
    the higher whole-position value, which here is NOT the middle. Under
    $1.00 buys nothing by itself."""
    conn = await _connect()
    try:
        await spl_clean(conn)
        await SPL._seed(conn)
        await SPL._primary(conn)
        _, sent, _c = SPL._transport(monkeypatch, order_id="venue-hedge")
        held = dataclasses.replace(SPL._held_leg(), cost_cents_per_unit=55)
        middle = dataclasses.replace(SPL._hedge_leg(), cost_cents_per_unit=40)
        other = _gap_leg()
        cands = [other, middle]
        tabs = {}
        for leg in cands:
            st = IS.classify(held, leg, sport_permits_tie=False,
                             fixture_can_void=False,
                             fixture_can_postpone=False)
            tabs[leg.condition_id] = st
        assert tabs[middle.condition_id].taxonomy == IS.MIDDLE
        assert tabs[other.condition_id].taxonomy != IS.MIDDLE
        assert tabs[middle.condition_id].cost_cents < 100

        def _measure(st, favour):
            p = {r["region"]: 0.0 for r in st.table}
            pays = sorted(st.table, key=lambda r: -float(r["joint_cents"]))
            if favour:
                p[pays[0]["region"]] = 1.0
            else:
                p[pays[-1]["region"]] = 1.0
            return p

        # HOLD 1.80, EXIT 0.40, REDUCE 1.10 (the lifecycle's own ranking).
        base = SPL._pair_facts(SPL._hold_ranking())

        async def _supply(conn_, pos, *, at):
            f = await base(conn_, pos, at=at)
            f["held_leg"] = held
            f["candidate_legs"] = cands
            f["region_probabilities"] = None
            # THE OTHER STRUCTURE PAYS ITS BEST REGION; THE MIDDLE ITS WORST.
            f["region_probabilities_by_candidate"] = {
                other.condition_id: _measure(tabs[other.condition_id], True),
                middle.condition_id: _measure(tabs[middle.condition_id],
                                              False)}
            f["candidate_leg_details"] = [
                {"candidate_id": leg.condition_id,
                 "price": leg.cost_cents_per_unit / 100.0, "depth_qty": 25,
                 "fee_usd": SPL.HEDGE_FEE,
                 "inputs_expire_at": float(at) + SPL._processing_delay_bound()}
                for leg in cands]
            return f

        got = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                 pair_inputs=_supply,
                                 venue_positions=SPL.EMPTY_VENUE)
        step = _step(got, SPL.PRIMARY_INTENT)
        assert step["search_order"][0] == middle.condition_id, step[
            "search_order"]
        scr = step["structure_screens"][middle.condition_id]
        assert scr["matched_cost_under_one_dollar"] is True
        assert scr["overlapping_winning_region"] is True
        assert scr["search_rank"] == 0
        assert set(scr["floor_survives"]) == {
            "fees", "executable_depth", "quantities", "settlement_states"}
        for v in scr["floor_survives"].values():
            assert v["survives"] in (True, False, None) and v["why"]
        assert step["structure_screens"][other.condition_id][
            "search_rank"] == 2
        # BOTH STRUCTURES WERE COMPARED ON WHOLE-POSITION VALUE ...
        rec = (await records(conn, SPL.PRIMARY_INTENT))[0]
        values = {a.get("candidate_id"): a.get("expected_net_usd")
                  for a in rec["alternatives"] if a.get("rankable")
                  and a.get("candidate_id")}
        assert {middle.condition_id, other.condition_id} <= set(values), (
            values, step["hedge_decision_inputs"])
        assert values[other.condition_id] > values[middle.condition_id]
        # ... AND THE ONE SEARCHED SECOND WON ON ITS NUMBER.
        sel = step["decision"]["selected"]
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE, (
            step["decision"], values)
        assert sel.get("candidate_id") == other.condition_id, (
            step["decision"], values)
        mid_alt = [a for a in rec["alternatives"]
                   if a.get("candidate_id") == middle.condition_id]
        assert mid_alt and mid_alt[0]["search_screen"]["search_rank"] == 0
    finally:
        await spl_clean(conn)
        await conn.close()
