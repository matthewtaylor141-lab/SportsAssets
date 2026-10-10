"""DEREK -> XAVIER -> AUDREY: ONE LINKED, INDEPENDENTLY OBSERVABLE TRACE.

REHEARSAL: substituted venue transport; no venue order was sent. Every
"order", "fill" and "execution" below is the empty-book fixture's venue
answering at `pmus._get_client`; nothing leaves this process.

WHAT THIS PROVES, through the REAL orchestration on a migrated database, as
one chain of exact ids (never "the latest" of anything) with the quantities
and money checked against the authoritative accounting tables
(`bettor_funded_fills`, `bettor_funded_economics`, `bettor_funded_intents`):

  Derek decision -> entry intent -> fill events -> position / group ->
  Xavier decision -> execution outcome -> Audrey review -> workspace records

  1. DEREK. `ext_pinnacle_loop.cycle()` discovers the empty-book fixture's
     Astros moneyline and the owner's entry policy
     (`agents.derek_policy.gate_for_funded_entry`, reached through the
     guarded runtime on the one real entry path -- NOT the lifecycle's
     stand-in) records an ENTER decision (`derek_entry_decisions`, indexed in
     `agent_decisions`). The funded intent is committed BEFORE the venue is
     asked and names Derek's decision id on its `decision_ref`.
  2. THE HANDOFF. The venue answers the order with its FIRST execution only
     (50 of 90; the rest is on the venue's own order record).
     `bettor_funded_book.ingest_fills` -> `agents.handoff.on_fills` gives
     Xavier ONLY the confirmed 50; the next servicing pass's recovery reads
     the venue's record, ingests the other 40, and the SAME handoff row
     becomes 90 confirmed / 0 outstanding, citing both ledger fills.
  3. XAVIER. That servicing pass records Xavier's review (HOLD, 90) on the
     entry intent, with decision_policy, shadow_comparison and search
     completeness. The market then moves above fair value; the next pass's
     review chooses EXIT 90 at 0.93 and dispatches exactly that plan through
     the substituted venue -- the EXECUTION OUTCOME: an EXIT intent naming
     Xavier's decision, a claim event, and 50 + 40 contracts filled at 0.93.
  4. A RESTART (fresh worker state, a new connection, the servicing task,
     the same fill reports replayed through the production ingest and the
     handoff hook, then a collection cycle) duplicates no ownership row, no
     handoff fill, no Xavier action, claim or order.
  5. AUDREY. `agents.runtime.slow_half` (-> `audrey_audit.run_due`) on a
     CONTROLLED clock just after the local day (AUDREY_TIMEZONE) ends writes
     that day's report: it names Derek's decision (and the funded intent that
     executed it), the position by its intent / group, and both Xavier
     decisions; the ACTUAL executed P&L equals the ledger's, apart from the
     decision-time (SIMULATED) valuations.
  6. THE ENDPOINTS (httpx ASGITransport, X-Admin-Token): the index and the
     Derek / Xavier / Audrey workspaces return the same ids and quantities.
  7. `agent_status` rows for all three were written by the runtime during
     these passes (the agent tables are emptied first; nothing is seeded).

WHAT IS SUBSTITUTED OR SUPPLIED -- `tests/_emptybook_fixture` names each:
the odds provider, the league schedule and the venue at their transport
boundaries; its SUPPLIED_ASSUMPTIONS (book currency, a calibration row,
DEMONSTRATION activation, the submission switches, and -- for the exit -- the
measured void rate recorded through the production observation recorder,
`tests/measured_void_rate`). The fixture's stand-in for Derek's gate is
REMOVED: the real policy decides, on an internal entry model that is
SYNTHETIC TEST EVIDENCE fitted, registered, evaluated and promoted ONLY by
`bettor_funded_model.promote` with a named approver
(`test_derek_enters_on_conservative_agreement._approve_entry_model`). No agent
decision, handoff, Xavier record, execution outcome or audit finding is
written by this test. ALL DATA SYNTHETIC; nothing here is evidence about any
market.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from decimal import Decimal

import pytest

from sportsassets import bettor_fee_schedule as FEES
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_xavier as XV
from sportsassets.agents import audrey_audit as AA
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import handoff as AH
from sportsassets.agents import registry as R
from sportsassets.agents import runtime as AR
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import _emptybook_fixture as F
from tests import agents_core_harness as H
from tests import audrey_helpers as AUH
from tests import measured_void_rate as MVR
from tests import test_derek_enters_on_conservative_agreement as DT
from tests import test_xavier_manages_positions_through_the_scheduled_path as XH

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

REHEARSAL = ("REHEARSAL: substituted venue transport; no venue order was "
             "sent")
#: THE PRODUCTION RUNTIME GATE, captured before any fixture replaces it.
REAL_RUNTIME_GATE = AR.gate_for_funded_entry
NY = "America/New_York"
VOID_PREFIX = "trace-void"
#: The venue matches each FILL_OR_KILL in two executions; its create answer
#: carries only the first.
FIRST, REST = 50, 40
QTY = FIRST + REST
ENTRY_PX = F.OFFERS[0][0]            # 0.62, the best offer
EXIT_BIDS = [(0.93, 400), (0.91, 300)]
EXIT_PX = EXIT_BIDS[0][0]


class PartialVenue(F.Venue):
    """THE EMPTY-BOOK VENUE, ANSWERING WITH A PARTIAL FILL (REHEARSAL).

    Its matching is the fixture's; the create ANSWER carries only the first
    execution, state PARTIALLY_FILLED, while the venue's own order record
    (`orders.retrieve`, which recovery reads) holds every execution, FILLED.
    Each create is timestamped, so the intent can be shown committed first."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.create_at: list = []
        real = self.orders_api.create
        v = self

        def create(params):
            v.create_at.append(time.time())
            got = real(params)
            ex = list(got.get("executions") or [])
            if len(ex) > 1:
                first = dict(ex[0], order={
                    "state": "ORDER_STATE_PARTIALLY_FILLED"})
                got = dict(got, executions=[first])
            return got

        self.orders_api.create = create


def _order_fee(qty, price) -> float:
    """The published schedule's fee for ONE order of this size."""
    return float(FEES.LATEST.fill_fee(Decimal(qty), Decimal(str(price)),
                                      maker=False))


async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


def _restart(monkeypatch):
    """A PROCESS RESTART: every process-local cache and the servicing and
    execution-lock state are fresh (as test_agent_lost_acks_and_restarts_...
    does)."""
    XH.reset_process_state()
    monkeypatch.setattr(loop, "_SERVICING", loop._servicing_state())
    monkeypatch.setattr(loop, "_EXEC_LOCK", {"lock": None, "loop": None})


async def _league_reports_not_started(conn):
    """THE LEAGUE'S "NOT STARTED" REPORT, RESTATED BEFORE CYCLE 2 (as
    test_derek_enters_on_conservative_agreement.league_reports_not_started
    does for its own cycle).

    Cycle 1 acquires the fixture row (`fixture_metadata.retrieved_at`), and
    `bettor_fixture_store.needs_acquisition` keeps a row younger than 300 s,
    so cycle 2 is judged against cycle 1's report. A quote is pre-game only
    if it was observed at or before that report
    (`bettor_fixture_metadata.context_for`), and the substituted odds carry
    a 2-second stamp age -- so cycle 2's valuation, the row cycle 3's exit is
    valued on, was pre-game only when cycle 2 began within about 2 s of
    cycle 1's report. A slower run (3.5 s before cycle 2, measured) made its
    context UNKNOWN, the terminal rule unestablished
    (THE_SETTLEMENT_RULE_IS_NOT_ESTABLISHED_FOR_A_FUNDED_ACTION) and no exit
    was sent. In production a 900 s cycle always meets a re-acquired report;
    the harness's premise is that fresh report, and this states it."""
    await conn.execute("UPDATE fixture_metadata SET retrieved_at = now() "
                       " WHERE condition_id = $1", F.CONDITION)


async def _clean(conn, *, since=None):
    """Everything this test (or an earlier run of it) wrote. `since`: the
    test's start -- the Audrey rows the runtime wrote from then on (the
    cycles' own slow halves audit the last completed day on the real clock;
    the controlled pass audits today)."""
    if await AA.has_schema(conn):
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            if since is not None:
                await conn.execute(
                    "DELETE FROM audrey_audit_reports "
                    " WHERE recorded_at >= to_timestamp($1)", float(since))
                await conn.execute(
                    "DELETE FROM audrey_collection_samples "
                    " WHERE sampled_at >= to_timestamp($1)", float(since))
            else:
                # A REPORT OF TODAY OR YESTERDAY left by an earlier run would
                # make this run's report a version 2.
                from zoneinfo import ZoneInfo
                today = AA.local_day(time.time(), ZoneInfo(NY))
                for d in (today, today - _dt.timedelta(days=1)):
                    await conn.execute(
                        "DELETE FROM audrey_audit_reports WHERE report_id=$1",
                        AA.report_id_for(d, NY))
    await MVR.purge(conn, prefix=VOID_PREFIX)
    await F.clean(conn)
    await DT._cleanup(conn)
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("bettor_xavier_execution_events", "bettor_xavier_decisions"):
            await conn.execute("DELETE FROM %s WHERE account_id=$1" % t,
                               F.ACCT)
        await conn.execute(
            "DELETE FROM derek_entry_decisions WHERE us_market_slug=$1",
            F.US_SLUG)
    await H.clean_agents(conn)
    if await AA.has_schema(conn):
        await AUH.purge(conn)


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _ep(v):
    return None if v is None else (v.timestamp() if hasattr(v, "timestamp")
                                   else float(v))


async def _intents(conn):
    return [dict(r, decision_ref=_j(r["decision_ref"])) for r in
            await conn.fetch(
                "SELECT intent_id, kind, state, parent_intent_id, "
                "       portfolio_group_id, leg_role, order_intent, "
                "       quantity::float8 AS quantity, "
                "       residual_qty::float8 AS residual_qty, "
                "       limit_price::float8 AS limit_price, venue_order_id, "
                "       decision_ref, created_at, sent_at, closed_at "
                "  FROM bettor_funded_intents WHERE account_id=$1 "
                " ORDER BY created_at, intent_id", F.ACCT)]


async def _fills(conn, intent_id):
    return [dict(r) for r in await conn.fetch(
        "SELECT fill_id, venue_order_id, venue_fill_id, direction, "
        "       qty::float8 AS qty, price::float8 AS price, "
        "       cash_usd::float8 AS cash_usd, fee_usd::float8 AS fee_usd "
        "  FROM bettor_funded_fills WHERE intent_id=$1 "
        " ORDER BY at, fill_id", intent_id)]


async def _economics(conn, intent_id):
    """The ledger for one position. An exit's proceeds and fees are booked
    against the ENTRY it consumes."""
    return [dict(r) for r in await conn.fetch(
        "SELECT event_id, kind, amount_usd::float8 AS a, qty::float8 AS q, "
        "       provisional FROM bettor_funded_economics WHERE intent_id=$1 "
        " ORDER BY at, event_id", intent_id)]


async def _handoff(conn, iid):
    r = await conn.fetchrow(
        "SELECT * FROM agent_position_handoffs WHERE entry_intent_id=$1", iid)
    return None if r is None else {
        k: (float(v) if hasattr(v, "as_tuple") else v)
        for k, v in dict(r).items()}


async def _counts(conn):
    """Everything a replay or a restart could duplicate."""
    q = {"handoffs": "SELECT count(*) FROM agent_position_handoffs",
         "handoff_fills": "SELECT count(*) FROM agent_handoff_fills",
         "intents": ("SELECT count(*) FROM bettor_funded_intents "
                     " WHERE account_id='%s'" % F.ACCT),
         "exit_intents": ("SELECT count(*) FROM bettor_funded_intents "
                          " WHERE account_id='%s' AND kind='EXIT'" % F.ACCT),
         "fills": ("SELECT count(*) FROM bettor_funded_fills WHERE intent_id "
                   " IN (SELECT intent_id FROM bettor_funded_intents "
                   "      WHERE account_id='%s')" % F.ACCT),
         "economics": ("SELECT count(*) FROM bettor_funded_economics WHERE "
                       " intent_id IN (SELECT intent_id FROM "
                       " bettor_funded_intents WHERE account_id='%s')"
                       % F.ACCT),
         "xavier_orders": ("SELECT count(*) FROM bettor_xavier_decisions "
                           " WHERE account_id='%s' AND chosen_action IS NOT "
                           " NULL AND chosen_action <> 'HOLD'" % F.ACCT),
         "claims": ("SELECT count(*) FROM bettor_xavier_execution_events "
                    " WHERE account_id='%s' AND event_kind='DISPATCH_CLAIMED'"
                    % F.ACCT),
         "derek_enter_decisions": (
             "SELECT count(*) FROM derek_entry_decisions WHERE "
             " us_market_slug='%s' AND verdict='ENTER'" % F.US_SLUG)}
    return {k: int(await conn.fetchval(s)) for k, s in q.items()}


async def _statuses(conn):
    return {a: await H.status(conn, a) for a in ("DEREK", "XAVIER",
                                                  "AUDREY")}


@pg
@pytest.mark.asyncio
async def test_derek_xavier_and_audrey_form_one_linked_trace_by_id(
        monkeypatch):
    monkeypatch.setenv(AA.TZ_ENV, NY)
    conn = await _connect()
    started = time.time()
    venue = PartialVenue(split=[FIRST, REST])
    try:
        await _clean(conn)
        await DT._ensure_schema(conn)
        await F.seed(conn)
        # THE INTERNAL ENTRY MODEL, APPROVED ONLY THROUGH `promote`.
        ap = await DT._approve_entry_model(conn)
        assert ap["model"]["approved_by"] == DT.APPROVER
        # NOTHING OF THE AGENTS' IS SEEDED: every identity, status, decision
        # index and handoff row below is written by the runtime.
        await H.clean_agents(conn)
        assert await conn.fetchval("SELECT count(*) FROM agent_status") == 0
        F.substitute(monkeypatch, venue)
        # THE REAL ENTRY POLICY, not the lifecycle fixture's stand-in.
        monkeypatch.setattr(AR, "gate_for_funded_entry", REAL_RUNTIME_GATE)
        t0 = time.time()

        # ══ CYCLE 1: DEREK ENTERS; THE VENUE ANSWERS WITH A PARTIAL FILL ══
        c1 = await loop.cycle(conn)
        assert c1["ran"] is True, c1.get("why")
        assert c1["refusals"].get("ADMITTED") == 1, c1["refusals"]
        assert c1["refusals"].get("FUNDED:SUBMITTED") == 1, c1["refusals"]
        creates = venue.creates_sent()
        assert len(creates) == 1
        (intent,) = await _intents(conn)
        iid, gid = intent["intent_id"], intent["portfolio_group_id"]
        assert (intent["kind"], intent["state"], intent["leg_role"]) == (
            "ENTRY", "PARTIALLY_FILLED", "PRIMARY")
        # THE ORDER THE VENUE RECEIVED IS THE INTENT
        buy = creates[0]
        assert (buy["marketSlug"], buy["intent"], int(buy["quantity"])) == (
            F.US_SLUG, intent["order_intent"], QTY)
        assert float(buy["price"]["value"]) == pytest.approx(
            intent["limit_price"])
        assert intent["quantity"] == QTY

        # ── (1) THE INTENT NAMES DEREK'S DECISION ──────────────────────
        did = intent["decision_ref"].get("derek_decision_id")
        assert did, ("the funded intent does not name the Derek decision "
                     "that authorised it: %s" % intent["decision_ref"])
        drow = dict(await conn.fetchrow(
            "SELECT * FROM derek_entry_decisions WHERE decision_id=$1", did))
        assert drow["verdict"] == DP.ENTER and drow["refusal"] is None
        assert drow["decided_by"] == DP.DECIDED_BY_GATE
        assert drow["us_market_slug"] == F.US_SLUG
        assert drow["side"] == intent["order_intent"]
        assert drow["model_version"] == "derek-entry-test-v1"
        assert drow["gross_edge_pp"] >= DP.DEFAULT_PARAMS["min_gross_edge_pp"]
        assert drow["expected_net_profit_usd"] > 0
        vrow = await conn.fetchrow(
            "SELECT id, condition_id, record_purpose FROM external_valuations "
            " WHERE id=$1", drow["valuation_id"])
        assert (vrow["condition_id"], vrow["record_purpose"]) == (
            F.CONDITION, "ENTRY_DECISION")
        link = await conn.fetchrow(
            "SELECT * FROM agent_decisions WHERE decision_ref=$1", did)
        assert (link["agent_id"], link["verdict"]) == ("DEREK", DP.ENTER)
        # ── PERSISTED BEFORE ANY SUBMISSION ────────────────────────────
        assert _ep(drow["recorded_at"]) <= _ep(intent["created_at"]) <= \
            _ep(intent["sent_at"]) <= venue.create_at[0], (
            "Derek's decision and the intent must be committed before the "
            "venue is asked")
        # ── THE POSITION'S GROUP ───────────────────────────────────────
        assert gid == "grp:" + iid
        grp = await conn.fetchrow(
            "SELECT * FROM bettor_funded_portfolio_groups WHERE group_id=$1",
            gid)
        assert grp["account_id"] == F.ACCT

        # ── (2) THE PARTIAL FILL: ONLY CONFIRMED QUANTITY IS XAVIER'S ───
        fills1 = await _fills(conn, iid)
        assert [(f["qty"], f["price"], f["direction"]) for f in fills1] == [
            (float(FIRST), ENTRY_PX, "ENTRY")]
        h1 = await _handoff(conn, iid)
        assert (h1["ordered_qty"], h1["confirmed_qty"],
                h1["outstanding_qty"]) == (QTY, FIRST, REST)
        assert h1["first_fill_id"] == h1["last_fill_id"] == \
            fills1[0]["fill_id"]
        assert (h1["from_agent"], h1["owner_agent"], h1["portfolio_group_id"]
                ) == ("DEREK", "XAVIER", gid)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_position_handoffs") == 1
        assert (await XV.history(conn, intent_id=iid))["decisions"] == [], (
            "cycle 1 serviced before the entry existed")
        s1 = await _statuses(conn)

        # ══ CYCLE 2: RECOVERY FILLS THE REST; XAVIER REVIEWS (HOLD) ═══════
        await _league_reports_not_started(conn)
        c2 = await loop.cycle(conn)
        assert len(venue.creates_sent()) == 1, "nothing further was sent"
        assert ("orders.retrieve", intent["venue_order_id"]) in venue.sent
        efills = await _fills(conn, iid)
        assert [(f["qty"], f["price"]) for f in efills] == [
            (float(FIRST), ENTRY_PX), (float(REST), ENTRY_PX)]
        assert efills[0]["fill_id"] == fills1[0]["fill_id"]
        entry_fill_ids = [f["fill_id"] for f in efills]
        h2 = await _handoff(conn, iid)
        # THE SAME OWNERSHIP ROW, ITS QUANTITY RAISED BY THE NEW FILL
        assert (h2["confirmed_qty"], h2["outstanding_qty"]) == (QTY, 0.0)
        assert h2["handoff_at"] == h1["handoff_at"], \
            "ownership began at the first confirmed fill"
        assert (h2["first_fill_id"], h2["last_fill_id"]) == tuple(
            entry_fill_ids)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_position_handoffs") == 1
        assert sorted(r["fill_id"] for r in await conn.fetch(
            "SELECT fill_id FROM agent_handoff_fills WHERE entry_intent_id=$1",
            iid)) == sorted(entry_fill_ids)
        # THE ENTRY'S MONEY, FROM THE LEDGER
        eco = await _economics(conn, iid)
        cost = [e for e in eco if e["kind"] == "ENTRY_COST"]
        fees_in = [e for e in eco if e["kind"] == "FEE"]
        assert [e["q"] for e in cost] == [float(FIRST), float(REST)]
        entry_cash = sum(e["a"] for e in cost)
        assert entry_cash == pytest.approx(-QTY * ENTRY_PX)
        fee_in = -sum(e["a"] for e in fees_in)
        assert fee_in == pytest.approx(sum(f["fee_usd"] for f in efills))
        assert fee_in == pytest.approx(_order_fee(QTY, ENTRY_PX))
        assert sum(f["cash_usd"] for f in efills) == pytest.approx(-entry_cash)

        # ── (3) XAVIER'S FIRST REVIEW, ON THE INTENT ───────────────────
        (xh,) = (await XV.history(conn, intent_id=iid))["decisions"]
        xid_hold = xh["xavier_decision_id"]
        assert (xh["intent_id"], xh["account_id"], xh["portfolio_group_id"]
                ) == (iid, F.ACCT, gid)
        assert (xh["chosen_action"], xh["execution_eligibility"],
                xh["responsibility_state"]) == ("HOLD", "HOLD_NEEDS_NO_ORDER",
                                                XV.HELD)
        rx = xh["residual_exposure"]
        assert (rx["held_qty"], rx["filled_qty"]) == (QTY, QTY)
        # BASIS PER CONTRACT: the ledger's entry cost over the held quantity
        assert rx["basis_per_contract"] == pytest.approx(-entry_cash / QTY)
        assert rx["remaining_basis_usd"] == pytest.approx(-entry_cash)
        hold = [a for a in xh["alternatives"] if a.get("action") == "HOLD"]
        assert len(hold) == 1 and hold[0]["qty"] == QTY
        p_win = hold[0]["payout_table"]["measure"]["p_win_given_normal"]
        assert hold[0]["expected_net_usd"] == pytest.approx(
            QTY * p_win + entry_cash)
        assert xh["expected_economics"]["expected_net_usd"] == pytest.approx(
            hold[0]["expected_net_usd"])
        assert (await XV.execution_state(
            conn, xavier_decision_id=xid_hold))["status"] == XV.X_NOT_CLAIMED
        rsn = xh["reasoning"]
        assert rsn["decision_policy"]["decision_function"]
        assert rsn["shadow_comparison"]["is"] == "DISPLAYED_NEVER_DISPATCHED"
        sc = rsn["xavier_ladder"]["search_completeness"]
        assert "complete" in sc and sc["stop_reason"] and \
            sc["comparison_scope"], sc
        assert rsn["selection_scope"]
        steps = ((c2.get("funded_servicing") or {}).get("pair_cycle")
                 or {}).get("considered") or []
        assert [s["intent_id"] for s in steps] == [iid]
        s2 = await _statuses(conn)

        # ══ CYCLE 3: THE MARKET MOVES; XAVIER EXITS THROUGH THE VENUE ═════
        venue.bids = list(EXIT_BIDS)
        await MVR.seed(conn, prefix=VOID_PREFIX)      # a SUPPLIED assumption
        c3 = await loop.cycle(conn)
        creates = venue.creates_sent()
        assert len(creates) == 2, creates
        sell = creates[1]
        xrecs = (await XV.history(conn, intent_id=iid))["decisions"]
        assert [x["xavier_decision_id"] for x in xrecs][1:] == [xid_hold]
        xe = xrecs[0]
        xid_exit = xe["xavier_decision_id"]
        assert (xe["chosen_action"], xe["execution_eligibility"]) == (
            "EXIT", XV.E_DISPATCHED)
        plan = xe["evidence"]["chosen_plan"]
        assert plan["digest"] == xe["chosen_plan_digest"]
        assert (plan["intent_id"], plan["quantity"], plan["limit_price"],
                plan["order_intent"]) == (iid, float(QTY), EXIT_PX,
                                          "ORDER_INTENT_SELL_LONG")
        # THE VENUE RECEIVED EXACTLY THE PERSISTED PLAN
        assert (sell["marketSlug"], sell["intent"], int(sell["quantity"])) \
            == (plan["us_market_slug"], plan["order_intent"], QTY)
        assert float(sell["price"]["value"]) == pytest.approx(EXIT_PX)
        (step3,) = ((c3.get("funded_servicing") or {}).get("pair_cycle")
                    or {}).get("considered")
        assert (step3["intent_id"], step3["dispatched"]) == (iid, "EXIT")
        assert step3["decision"]["selected"]["qty"] == QTY
        # THE EXECUTION OUTCOME: an EXIT intent naming Xavier's decision
        exits = [i for i in await _intents(conn) if i["kind"] == "EXIT"]
        assert len(exits) == 1
        ex = exits[0]
        exit_iid = ex["intent_id"]
        assert ex["parent_intent_id"] == iid
        assert ex["decision_ref"]["xavier_decision_id"] == xid_exit
        assert ex["decision_ref"]["plan_digest"] == xe["chosen_plan_digest"]
        assert (ex["quantity"], ex["limit_price"]) == (QTY, EXIT_PX)
        ev3 = await XV.execution_events(conn, xavier_decision_id=xid_exit)
        assert [e["event_kind"] for e in ev3].count(XV.K_CLAIMED) == 1
        assert {e["order_intent_id"] for e in ev3
                if e["order_intent_id"]} == {exit_iid}
        assert [(f["qty"], f["price"], f["direction"]) for f in
                await _fills(conn, exit_iid)] == [
            (float(FIRST), EXIT_PX, "EXIT")]
        s3 = await _statuses(conn)

        # ══ (4) RESTART: FRESH PROCESS STATE, A NEW CONNECTION ════════════
        before_restart = await _counts(conn)
        _restart(monkeypatch)
        await conn.close()
        conn = await _connect()
        svc = await loop._service_once(conn, now=time.time(),
                                       source=loop.SOURCE_SERVICING_TASK)
        assert svc["ran"] is True, svc
        # the servicing task's recovery read the exit order back
        assert ("orders.retrieve", ex["venue_order_id"]) in venue.sent
        xfills = await _fills(conn, exit_iid)
        assert [(f["qty"], f["price"]) for f in xfills] == [
            (float(FIRST), EXIT_PX), (float(REST), EXIT_PX)]
        exit_fill_ids = [f["fill_id"] for f in xfills]
        # THE SAME FILL REPORTS, REPLAYED THROUGH THE PRODUCTION INGEST AND
        # THE HANDOFF HOOK (the venue's own record, read through the
        # substituted transport), and the servicing-side repair.
        from sportsassets import pmus
        replay_counts = await _counts(conn)
        for it, vid, direction in ((iid, intent["venue_order_id"], "ENTRY"),
                                   (exit_iid, ex["venue_order_id"], "EXIT")):
            read = FB.executions_of(pmus.order_status(vid))
            assert len(read["executions"]) == 2
            got = await FB.ingest_fills(conn, it, read["executions"],
                                        at=time.time(), direction=direction)
            assert got["ok"] is True and not got.get("written"), got
        # and the create answers, exactly as the venue first sent them
        for it, o, direction in ((iid, "venue-emptybook-1", "ENTRY"),
                                 (exit_iid, "venue-emptybook-2", "EXIT")):
            ans = {"executions": venue.orders[o]["executions"][:1]}
            got = await FB.ingest_fills(
                conn, it, FB.executions_of({"raw": {"response": ans}})[
                    "executions"], at=time.time(), direction=direction)
            assert not got.get("written"), got
        replay = await AH.on_fills(conn, intent_id=iid, now=time.time())
        assert replay["created"] is False and replay["fills_recorded"] == 0
        assert (await AH.on_fills(conn, intent_id=exit_iid))[
            "skipped"] == AH.S_NOT_AN_ENTRY
        rep = await AH.reconcile_unowned(conn, now=time.time())
        assert rep["examined"] == 0 and rep["created"] == 0, rep
        assert await _counts(conn) == replay_counts
        # A SECOND RESTART, THEN THE COLLECTION CYCLE
        _restart(monkeypatch)
        c4 = await loop.cycle(conn)
        assert c4["ran"] is True
        after = await _counts(conn)
        assert len(venue.creates_sent()) == 2, "a restart sent nothing"
        for k in ("handoffs", "handoff_fills", "intents", "exit_intents",
                  "xavier_orders", "claims"):
            assert after[k] == before_restart[k], (k, before_restart, after)
        assert (after["handoffs"], after["handoff_fills"], after["claims"],
                after["exit_intents"], after["xavier_orders"]) == (
            1, 2, 1, 1, 1)
        # the only new ledger rows are the exit's second execution and its
        # economics (proceeds, fee)
        assert after["fills"] == before_restart["fills"] + 1
        assert after["economics"] == before_restart["economics"] + 2
        h4 = await _handoff(conn, iid)
        assert {k: h4[k] for k in ("confirmed_qty", "outstanding_qty",
                                   "handoff_at", "first_fill_id",
                                   "last_fill_id")} == \
            {k: h2[k] for k in ("confirmed_qty", "outstanding_qty",
                                "handoff_at", "first_fill_id",
                                "last_fill_id")}, "an exit changes no owner"
        # THE POSITION IS CLOSED BY THE EXIT, TO THE CONTRACT
        closed = {i["intent_id"]: i for i in await _intents(conn)}
        assert closed[iid]["residual_qty"] == pytest.approx(0.0)
        assert closed[exit_iid]["state"] == "FILLED"
        # THE EXECUTION OUTCOME, IN MONEY, FROM THE LEDGER
        eco = await _economics(conn, iid)
        proceeds = [e for e in eco if e["kind"] == "EXIT_PROCEEDS"]
        assert [e["q"] for e in proceeds] == [float(FIRST), float(REST)]
        exit_cash = sum(e["a"] for e in proceeds)
        assert exit_cash == pytest.approx(QTY * EXIT_PX)
        assert exit_cash == pytest.approx(sum(f["cash_usd"] for f in xfills))
        fee_all = -sum(e["a"] for e in eco if e["kind"] == "FEE")
        fee_out = fee_all - fee_in
        assert fee_out == pytest.approx(sum(f["fee_usd"] for f in xfills))
        assert fee_out == pytest.approx(_order_fee(QTY, EXIT_PX))
        assert fee_out == pytest.approx(
            step3["decision"]["selected"]["fees_usd"])
        realised = sum(e["a"] for e in eco)
        assert realised == pytest.approx(
            QTY * (EXIT_PX - ENTRY_PX) - fee_in - fee_out)
        # Xavier's decided net (proceeds less exit fees less the basis) is
        # what was realised before the entry fees Derek paid
        assert step3["decision"]["selected"]["expected_net_usd"] == \
            pytest.approx(realised + fee_in)
        xstate = await XV.execution_state(conn, xavier_decision_id=xid_exit)
        assert xstate["claimed"] is True
        assert xstate["venue_order_ids"] == [ex["venue_order_id"]]
        assert xstate["filled_qty"] == pytest.approx(QTY), xstate
        xrecs = (await XV.history(conn, intent_id=iid))["decisions"]
        # ONE ACTION EVER TAKEN (the EXIT), ONE HOLD; the passes after the
        # restart reviewed the closed position and selected nothing
        acted = [x for x in xrecs if x["chosen_action"] not in (None, "HOLD")]
        assert [x["xavier_decision_id"] for x in acted] == [xid_exit]
        assert [x["xavier_decision_id"] for x in xrecs
                if x["chosen_action"] == "HOLD"] == [xid_hold]
        later = [x for x in xrecs
                 if x["xavier_decision_id"] not in (xid_exit, xid_hold)]
        assert later, "the restarted passes still review the position"
        for x in later:
            assert x["chosen_action"] is None and x["chosen_plan_digest"] \
                is None, x
            assert x["execution"]["status"] == XV.X_NOT_CLAIMED
            assert x["responsibility_state"] == "SETTLEMENT_PENDING"
            assert _ep(x["decided_at"]) > _ep(xe["decided_at"])

        # ══ (5) AUDREY, FOR THE LOCAL DAY THAT HOLDS THESE DECISIONS ══════
        name, tz, _note = AA.audit_timezone()
        assert name == NY
        day = AA.local_day(_ep(drow["decided_at"]), tz)
        for x in xrecs:
            assert AA.local_day(_ep(x["decided_at"]), tz) == day
        report_id = AA.report_id_for(day, name)
        _start, day_end = AA.day_bounds(day, tz)
        audit_now = day_end + 1800.0         # the controlled clock
        await conn.execute("DELETE FROM audrey_audit_watermarks")
        slow = await AR.slow_half(conn, now=audit_now)
        assert slow["audrey_audit"]["ok"] is True, slow
        rep_row = await AA.report(conn, report_id)
        assert rep_row is not None and rep_row["version"] == 1
        rep = rep_row["report"]
        assert rep["window"]["start"] <= _ep(drow["decided_at"]) < \
            rep["window"]["end"]
        # DEREK'S DECISION, by id, with the funded intent that executed it
        sel = [r for r in rep["derek"]["entries"]["selected_rows"]
               if did in r.get("derek_decision_ids", [])]
        assert len(sel) == 1, rep["derek"]["entries"]["selected_rows"][:3]
        assert sel[0]["valuation_id"] == drow["valuation_id"]
        assert (sel[0]["funded_intent_ids"], sel[0]["funded_order_sent"]) \
            == ([iid], True)
        assert rep["derek"]["entries"]["funded_orders_sent"] >= 1
        # BOTH XAVIER DECISIONS, on the handed-over intent and its group
        xrows = {r["xavier_decision_id"]: r
                 for r in rep["xavier"]["decision_rows"]
                 if r["intent_id"] == iid}
        assert set(xrows) == {x["xavier_decision_id"] for x in xrecs}
        assert (xrows[xid_hold]["chosen_action"],
                xrows[xid_exit]["chosen_action"]) == ("HOLD", "EXIT")
        assert {r["group"] for r in xrows.values()} == {gid}
        assert xrows[xid_exit]["execution"] == xstate["status"]
        assert xrows[xid_hold]["search_completeness"]["ended"] == \
            str(sc["stop_reason"]).upper()
        # THE POSITION (the handoff's key and its group) AND ITS MONEY
        (pos,) = [p for p in rep["positions"]["rows"] if iid in p["legs"]]
        assert (pos["position"], pos["legs"]) == (gid, [iid])
        assert pos["realised_net_usd"] == pytest.approx(realised)
        # ACTUAL EXECUTED P&L IS THE LEDGER'S; FEES STILL PROVISIONAL ARE
        # REPORTED APART
        led = [dict(r) for r in await conn.fetch(
            "SELECT e.kind, e.amount_usd::float8 AS a, e.provisional "
            "  FROM bettor_funded_economics e WHERE e.at >= to_timestamp($1) "
            "   AND e.at < to_timestamp($2)", _start, day_end)]
        book = rep["book"]
        assert book["realized"]["category"] == AA.ACTUAL
        assert book["realized"]["net_usd"] == pytest.approx(
            sum(e["a"] for e in led if not e["provisional"]))
        assert book["provisional"]["net_usd"] == pytest.approx(
            sum(e["a"] for e in led if e["provisional"]))
        assert book["realized"]["by_kind"]["ENTRY_COST"] == pytest.approx(
            entry_cash)
        assert book["realized"]["by_kind"]["EXIT_PROCEEDS"] == pytest.approx(
            exit_cash)
        ev = rep["evidence"]
        assert ev["never_merged"] is True
        assert ev[AA.ACTUAL]["book_realized_net_usd"] == pytest.approx(
            book["realized"]["net_usd"])

        # ACTUAL AND HYPOTHETICAL KEPT APART: Derek's decision-time valuation
        # and Xavier's HOLD (outcome not known) are SIMULATED, at their
        # recorded decision-time values; neither is ACTUAL or merged.
        def _in(cat, key, val):
            return [r for r in ev[cat]["rows"] if r.get(key) == val]
        (dsim,) = _in(AA.SIMULATED, "valuation_id", drow["valuation_id"])
        assert dsim["value_per_contract"] == \
            sel[0]["simulated_value_per_contract"]
        (xsim,) = _in(AA.SIMULATED, "xavier_decision_id", xid_hold)
        assert xsim["value_usd"] == pytest.approx(hold[0]["expected_net_usd"])
        for cat in (AA.ACTUAL, AA.KNOWN_SETTLEMENT):
            assert not _in(cat, "valuation_id", drow["valuation_id"])
            assert not _in(cat, "xavier_decision_id", xid_hold)

        # ══ (6) THE ENDPOINTS: THE SAME IDS AND QUANTITIES, BY ID ALONE ═══
        from sportsassets.api import app as A
        from tests import test_agent_workspaces_show_runtime_records as WS
        c = await WS._client(monkeypatch, A)
        try:
            got = {}
            for k, path in (
                    ("index", "/api/command/agents"),
                    ("derek", "/api/command/agents/derek"),
                    ("derek_decision",
                     "/api/command/agents/derek/decisions/%s" % did),
                    ("derek_index",
                     "/api/command/agents/decisions?agent=DEREK&limit=500"),
                    ("handoff", "/api/command/agents/handoffs"
                                "?entry_intent_id=%s" % iid),
                    ("xavier", "/api/command/agents/xavier"),
                    ("xavier_exit",
                     "/api/command/agents/xavier/decisions/%s" % xid_exit),
                    ("xavier_hold",
                     "/api/command/agents/xavier/decisions/%s" % xid_hold),
                    ("audrey", "/api/command/agents/audrey"),
                    ("audrey_report",
                     "/api/command/agents/audrey/reports/%s" % report_id)):
                r = await c.get(path)
                assert r.status_code == 200, (k, r.status_code, r.text[:400])
                got[k] = r.json()
        finally:
            await c.aclose()
            await WS._close_pool()

        # DEREK: decision -> intent -> fills, by the intent's own reference.
        # (The workspace's `decisions` section lists the 50 newest, and the
        # synthetic prospective model cohort is dated after this decision --
        # as in test_derek_enters_on_conservative_agreement -- so the
        # decision is reached by id: plans_fills and /decisions/{id}.)
        dws = got["derek"]["sections"]
        assert dws["decisions"]["status"] == "OK"
        (pf,) = [p for p in dws["plans_fills"]["data"]
                 if p["decision_id"] == did]
        assert (pf["intent_id"], pf["fill_ids"], pf["filled_qty"],
                pf["quantity"]) == (iid, entry_fill_ids, float(QTY),
                                    float(QTY))
        assert [h["derek_decision_id"] for h in dws["handoffs"]["data"]
                if h["entry_intent_id"] == iid] == [did]
        dd = got["derek_decision"]
        assert dd["decision"]["decision_id"] == did
        assert dd["decision"]["verdict"] == DP.ENTER
        assert dd["registry_link"]["decision_ref"] == did
        assert dd["valuation"]["id"] == drow["valuation_id"]
        assert [d["verdict"] for d in got["derek_index"]["decisions"]["data"]
                if d["decision_ref"] == did] == [DP.ENTER]
        # THE HANDOFF: intent -> fills, citing Derek's decision
        for rows in (got["handoff"]["handoffs"]["data"],
                     [h for h in got["index"]["handoffs"]["data"]
                      if h["entry_intent_id"] == iid]):
            (hr,) = rows
            assert (hr["entry_intent_id"], hr["derek_decision_id"],
                    hr["portfolio_group_id"]) == (iid, did, gid)
            assert (hr["from_agent"], hr["owner_agent"]) == ("DEREK",
                                                              "XAVIER")
            assert (hr["ordered_qty"], hr["confirmed_qty"],
                    hr["outstanding_qty"]) == (float(QTY), float(QTY), 0.0)
            assert [(f["fill_id"], f["qty"]) for f in hr["fills"]] == [
                (entry_fill_ids[0], float(FIRST)),
                (entry_fill_ids[1], float(REST))]
            assert hr["position_open"] is False       # closed by the exit
            evk = {(e["kind"], e["id"]) for e in hr["evidence"]}
            assert ("derek_entry_decisions", did) in evk
            assert ("bettor_funded_intents", iid) in evk
            assert {("bettor_funded_fills", f) for f in entry_fill_ids} <= evk
        # XAVIER: intent -> Xavier's decisions -> the execution outcome
        xws = got["xavier"]["sections"]
        (rv,) = [r for r in xws["reviews"]["data"]["current"]
                 if r["intent_id"] == iid]
        assert rv["xavier_decision_id"] == xrecs[0]["xavier_decision_id"]
        assert rv["portfolio_group_id"] == gid
        assert rv["responsibility_state"] == "SETTLEMENT_PENDING"
        assert [h["entry_intent_id"] for h in
                xws["positions"]["data"]["handoffs"] or []] in ([iid], []), \
            "the handoff shown with the position is this intent's"
        xd = got["xavier_exit"]["decision"]
        assert xd["reasoning"]["decision_policy"]["decision_function"]
        assert xd["reasoning"]["shadow_comparison"]["is"] == \
            "DISPLAYED_NEVER_DISPATCHED"
        assert xd["reasoning"]["xavier_ladder"]["search_completeness"][
            "stop_reason"]
        assert xd["evidence"]["chosen_plan"]["quantity"] == float(QTY)
        assert (xd["xavier_decision_id"], xd["intent_id"], xd["chosen_action"],
                xd["chosen_plan_digest"]) == (xid_exit, iid, "EXIT",
                                              plan["digest"])
        assert xd["execution"]["filled_qty"] == pytest.approx(QTY)
        assert xd["execution"]["venue_order_ids"] == [ex["venue_order_id"]]
        assert [e["event_kind"] for e in xd["execution_events"]].count(
            XV.K_CLAIMED) == 1
        assert {e["order_intent_id"] for e in xd["execution_events"]
                if e["order_intent_id"]} == {exit_iid}
        xhd = got["xavier_hold"]["decision"]
        assert (xhd["intent_id"], xhd["chosen_action"],
                xhd["execution"]["status"]) == (iid, "HOLD",
                                                XV.X_NOT_CLAIMED)
        # AUDREY: the day's report, naming all of them by id
        assert report_id in {r["report_id"] for r in
                             got["audrey"]["sections"]["daily_reports"]["data"]}
        ar = got["audrey_report"]["report"]
        assert [r["funded_intent_ids"] for r in
                ar["derek"]["entries"]["selected_rows"]
                if did in r["derek_decision_ids"]] == [[iid]]
        assert sorted(r["xavier_decision_id"] for r in
                      ar["xavier"]["decision_rows"]
                      if r["intent_id"] == iid) == sorted(xrows)
        assert [(p["position"], p["realised_net_usd"]) for p in
                ar["positions"]["rows"] if iid in p["legs"]] == [
            (gid, pytest.approx(realised))]

        # ══ (7) THE RUNTIME WROTE EVERY STATUS ROW ════════════════════════
        fin = await _statuses(conn)
        st = {a["agent_id"]: a for a in got["index"]["agents"]}
        for aid in ("DEREK", "XAVIER", "AUDREY"):
            assert st[aid]["state"] == fin[aid]["state"], (aid, st[aid])
            assert s1[aid] and s1[aid]["errors"] == 0, (aid, s1[aid])
            assert t0 <= _ep(s1[aid]["last_heartbeat_at"]) <= \
                _ep(intent["sent_at"]) + 60.0, (aid, s1[aid])
        # cycle 1: Derek recorded the decision; Xavier's pass ran before the
        # entry existed and said so
        assert s1["DEREK"]["state"] == R.S_DECISION_RECORDED
        assert s1["DEREK"]["activity"].startswith("VALUATION_ROWS_WRITTEN:")
        assert (s1["XAVIER"]["state"], s1["XAVIER"]["activity"]) == (
            R.S_IDLE, AR.A_NO_OWNED_INVENTORY)
        # cycles 2 and 3: Xavier's review, each pass's own heartbeat
        for s in (s2, s3):
            assert (s["XAVIER"]["state"], s["XAVIER"]["activity"]) == (
                R.S_DECISION_RECORDED, "MANAGEMENT_DECISIONS_RECORDED:1")
        assert _ep(s1["XAVIER"]["last_heartbeat_at"]) < _ep(
            s2["XAVIER"]["last_heartbeat_at"]) < _ep(
            s3["XAVIER"]["last_heartbeat_at"])
        assert s3["XAVIER"]["runs"] == s1["XAVIER"]["runs"] + 2
        # Derek's later passes: each wrote its own end state (a fresh
        # observation is judged again, a repeated one skipped -- both are
        # truthful), and no later Derek decision reached an order: the one
        # funded intent names `did` alone.
        assert s2["DEREK"]["state"] in (R.S_DECISION_RECORDED,
                                        R.S_WAITING_FOR_EVIDENCE), s2["DEREK"]
        assert s2["DEREK"]["runs"] == s1["DEREK"]["runs"] + 1
        assert _ep(s2["DEREK"]["last_heartbeat_at"]) > _ep(
            s1["DEREK"]["last_heartbeat_at"])
        assert [r["d"] for r in await conn.fetch(
            "SELECT decision_ref->>'derek_decision_id' AS d "
            "  FROM bettor_funded_intents WHERE account_id=$1 "
            "   AND decision_ref->>'derek_decision_id' IS NOT NULL",
            F.ACCT)] == [did]
        # Audrey: written by the slow half on the controlled clock
        assert _ep(fin["AUDREY"]["last_heartbeat_at"]) == pytest.approx(
            audit_now)
        assert (fin["AUDREY"]["state"], fin["AUDREY"]["activity"]) == (
            R.S_DECISION_RECORDED, "AUDIT_REPORT_WRITTEN:%s" % report_id)
        assert fin["AUDREY"]["runs"] > s1["AUDREY"]["runs"]
        for aid in ("DEREK", "XAVIER", "AUDREY"):
            assert fin[aid]["errors"] == 0, (aid, fin[aid])

        chain = {
            "label": REHEARSAL,
            "derek": {"decision_id": did, "verdict": drow["verdict"],
                      "valuation_id": drow["valuation_id"],
                      "agent_decisions.decision_ref": link["decision_ref"],
                      "gross_edge_pp": drow["gross_edge_pp"],
                      "expected_net_profit_usd":
                          drow["expected_net_profit_usd"]},
            "entry_intent": {"intent_id": iid, "order_intent":
                             intent["order_intent"], "quantity": QTY,
                             "limit_price": intent["limit_price"],
                             "venue_order_id": intent["venue_order_id"],
                             "names_derek_decision": did},
            "entry_fills": [[f["fill_id"], f["qty"], f["price"],
                             f["fee_usd"]] for f in efills],
            "position": {"portfolio_group_id": gid,
                         "basis_per_contract": rx["basis_per_contract"],
                         "entry_cash_usd": entry_cash,
                         "entry_fees_usd": fee_in},
            "handoff": {"entry_intent_id": iid,
                        "after_partial_fill": [h1["confirmed_qty"],
                                               h1["outstanding_qty"]],
                        "after_rest": [h2["confirmed_qty"],
                                       h2["outstanding_qty"]],
                        "fills": entry_fill_ids,
                        "rows_after_restart_and_replay": after["handoffs"]},
            "xavier": [
                {"xavier_decision_id": xid_hold, "action": "HOLD",
                 "qty": hold[0]["qty"],
                 "expected_net_usd": hold[0]["expected_net_usd"],
                 "execution": XV.X_NOT_CLAIMED},
                {"xavier_decision_id": xid_exit, "action": "EXIT",
                 "qty": plan["quantity"], "limit_price": plan["limit_price"],
                 "plan_digest": plan["digest"],
                 "expected_net_usd":
                     step3["decision"]["selected"]["expected_net_usd"],
                 "execution": xstate["status"],
                 "executed_qty": xstate["filled_qty"]},
                {"later_reviews_after_restart": [
                    [x["xavier_decision_id"], x["responsibility_state"],
                     x["chosen_action"], x["execution"]["status"]]
                    for x in later]}],
            "execution_outcome": {
                "exit_intent_id": exit_iid,
                "venue_order_id": ex["venue_order_id"],
                "fills": [[f["fill_id"], f["qty"], f["price"], f["fee_usd"]]
                          for f in xfills],
                "exit_proceeds_usd": exit_cash, "exit_fees_usd": fee_out,
                "realised_net_usd": realised},
            "restart": {"before": before_restart, "after": after,
                        "venue_creates": len(venue.creates_sent())},
            "audrey": {"report_id": report_id,
                       "version": rep_row["version"],
                       "derek_row": {k: sel[0][k] for k in (
                           "valuation_id", "derek_decision_ids",
                           "funded_intent_ids", "category")},
                       "xavier_rows": sorted(xrows),
                       "position": [pos["position"],
                                    pos["realised_net_usd"]],
                       "book_realized_actual_usd":
                           book["realized"]["net_usd"],
                       "book_provisional_usd": book["provisional"]["net_usd"]},
            "agent_status": {a: [r["state"], r["activity"]]
                             for a, r in fin.items()}}
        assert chain["label"] == REHEARSAL
        print("\n%s\nTHE CHAIN, BY ID:\n%s" % (
            REHEARSAL, json.dumps(chain, indent=1, default=str)))
    finally:
        await _clean(conn, since=started)
        await conn.close()
