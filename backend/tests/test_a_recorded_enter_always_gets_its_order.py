"""P0 INCIDENT (2026-10-04): A RECORDED ENTER ALWAYS GETS ITS PAPER ORDER, OR A
NAMED FINDING.

Measured in production on 191b299: the in-cycle hook wrapped the WHOLE
decision in asyncio.wait_for(8 s). A decision INSERTs its row first, then runs
the execution-intent hook, builds the canonical intent and only then submits
the paper order -- so a deadline that fell after the INSERT left a recorded
ENTER with no order and no PAPER_RISK_REFUSED finding (~1 a day, 4% of the
completed-game policy's ENTERs). Proved here:

  * `bounded_decision`: a decision cut BEFORE its ENTER row is cancelled
    exactly as before (TimeoutError, nothing left running); once the ENTER is
    recorded its order sequence completes past the deadline, up to the grace;
    beyond the grace it is cancelled and named (EnterOrderGraceExceeded);
    an outer cancellation leaves no orphan task on the connection;
  * end to end on the real completed-game decision, with a SLOW execution
    hook that pushes the order past the decision deadline: the ENTER gets its
    paper order, the canonical order is intact (execution hook -> canonical
    intent -> paper order) and the order's fields are read from the intent;
  * the backstop: an ENTER with no order and no order refusal older than
    ENTER_WITHOUT_ORDER_AFTER_S becomes ONE finding ENTER_WITHOUT_ORDER;
    an ENTER with an order, with a recorded refusal, or younger than the
    threshold is never named.
SYNTHETIC valuations and books; no venue is contacted.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from sportsassets import decision_hooks as DH
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)


# ── bounded_decision, pure asyncio ──────────────────────────────────
def test_a_decision_cut_before_its_insert_is_cancelled_as_before():
    ran = []

    async def slow_decision(ctx):
        await asyncio.sleep(1.0)
        ran.append("INSERT")            # never reached
        return {"verdict": "ENTER"}

    async def go():
        t0 = time.monotonic()
        with pytest.raises(asyncio.TimeoutError) as ei:
            await PD.bounded_decision(slow_decision, {}, timeout_s=0.1)
        assert not isinstance(ei.value, PD.EnterOrderGraceExceeded)
        await asyncio.sleep(1.1)
        return time.monotonic() - t0
    took = asyncio.run(go())
    assert ran == [] and took < 1.6


def test_a_recorded_enter_completes_its_order_past_the_deadline():
    steps = []

    async def decision(ctx):
        steps.append("INSERT")
        PD.enter_recorded(ctx, "paperbench:abc")
        await asyncio.sleep(0.4)        # the execution hook, slow
        steps.append("EXECUTION_HOOK")
        steps.append("CANONICAL_INTENT")
        steps.append("PAPER_ORDER")
        return {"decision_id": "paperbench:abc", "verdict": "ENTER",
                "order_id": "paperord:1"}

    rec = asyncio.run(PD.bounded_decision(decision, {}, timeout_s=0.1,
                                          grace_s=5.0))
    assert steps == ["INSERT", "EXECUTION_HOOK", "CANONICAL_INTENT",
                     "PAPER_ORDER"]
    assert rec["order_id"] == "paperord:1"
    late = rec["order_after_decision_deadline"]
    assert late["decision_id"] == "paperbench:abc"
    assert late["decision_deadline_s"] == 0.1 and late["grace_s"] == 5.0
    assert late["elapsed_s"] >= 0.4


def test_the_grace_bounds_a_wedged_order_sequence_and_names_the_decision():
    seen = {}

    async def decision(ctx):
        PD.enter_recorded(ctx, "paperbench:wedged")
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise
        return {"order_id": "never"}

    async def go():
        with pytest.raises(PD.EnterOrderGraceExceeded) as ei:
            await PD.bounded_decision(decision, {}, timeout_s=0.1,
                                      grace_s=0.2)
        return ei.value
    exc = asyncio.run(go())
    assert isinstance(exc, asyncio.TimeoutError)   # old handlers still match
    assert exc.decision_id == "paperbench:wedged"
    assert seen == {"cancelled": True}             # nothing left running


def test_a_decisions_own_outcome_and_an_outer_cancel_are_kept():
    async def raises(ctx):
        raise ValueError("own failure")

    async def own_timeout(ctx):
        raise asyncio.TimeoutError("inside the decision")

    async def finished(ctx):
        return {"verdict": "REFUSE"}

    with pytest.raises(ValueError):
        asyncio.run(PD.bounded_decision(raises, {}, timeout_s=1.0))
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(PD.bounded_decision(own_timeout, {}, timeout_s=1.0))
    assert asyncio.run(PD.bounded_decision(finished, {}, timeout_s=1.0)) == \
        {"verdict": "REFUSE"}
    # the caller's ctx is not mutated: the signal lives on a copy
    ctx = {"books_by_slug": {}}

    async def shares(c):
        c["books_by_slug"]["s"] = 1
        PD.enter_recorded(c, "d")
        return {}
    asyncio.run(PD.bounded_decision(shares, ctx, timeout_s=1.0))
    assert PD.CTX_ENTER_RECORDED not in ctx and ctx["books_by_slug"] == {"s": 1}
    # an OUTER cancellation (the reactive evaluation's own deadline) cancels
    # the decision too: no orphan coroutine keeps the connection
    state = {}

    async def order_seq(c):
        PD.enter_recorded(c, "d2")
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            state["inner_cancelled"] = True
            raise

    async def outer():
        t = asyncio.ensure_future(PD.bounded_decision(order_seq, {},
                                                      timeout_s=0.05,
                                                      grace_s=30.0))
        await asyncio.sleep(0.2)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(outer())
    assert state == {"inner_cancelled": True}


# ── end to end, the real completed-game decision ────────────────────
@pytest.fixture
def cg_on(monkeypatch, new_strategies_off):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


def _hooks(monkeypatch, *, hook_sleep_s: float, calls: list):
    async def slow_execution_hook(conn, payload):
        calls.append(("EXECUTION_HOOK", payload["decision_id"]))
        await asyncio.sleep(hook_sleep_s)
        calls.append(("EXECUTION_HOOK_DONE", payload["decision_id"]))
        return {"intent_id": "execint:test", "actual_lane": "SHADOW_TEST"}

    async def canonical(conn, **kw):
        calls.append(("CANONICAL_INTENT", kw["did"]))
        sized, cand, ent = kw["sized"], kw["cand"], kw["ent"]
        return {"intent_id": "canon:test", "holding_side": kw["side"],
                "order_intent": cand.get("side"),
                "us_market_slug": cand["us_market_slug"],
                "order_type": ent["order_type"],
                "time_in_force": ent["time_in_force"],
                "target_qty": sized["qty"], "limit_price": sized["limit"],
                "wire_price": sized["wire"], "decision_id": kw["did"],
                "strategy": kw["strategy"]}

    async def adapters(conn, intent, *, paper_order, paper_result):
        calls.append(("ADAPTERS", intent["decision_id"],
                      bool(paper_result.get("ok"))))
        return {"recorded": True}
    monkeypatch.setattr(DH, "DECISION_HOOK", slow_execution_hook)
    monkeypatch.setattr(DH, "CANONICAL_DECISION", canonical)
    monkeypatch.setattr(DH, "CANONICAL_ENTRY_ADAPTERS", adapters)


@pg
async def test_a_slow_execution_hook_no_longer_strands_the_enter(
        cg_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    calls: list = []
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "enterord", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        # THE PRODUCTION SHAPE: the decision is inside its deadline when its
        # row is written; the execution hook then outlasts what is left
        monkeypatch.setattr(PR, "VALUATION_HOOK_TIMEOUT_S", 2.0)
        _hooks(monkeypatch, hook_sleep_s=2.5, calls=calls)
        g = await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
        cg = g["benchmark_completed_game"]
        assert cg["verdict"] == "ENTER" and cg.get("order_id"), cg
        late = cg["order_after_decision_deadline"]
        assert late["decision_deadline_s"] == 2.0 and late["elapsed_s"] > 2.0
        did = cg["decision_id"]
        # THE CANONICAL ORDER IS INTACT: execution hook, canonical intent,
        # paper order (the adapters record what the paper order did)
        seq = [c[0] for c in calls if c[1] == did]
        assert seq == ["EXECUTION_HOOK", "EXECUTION_HOOK_DONE",
                       "CANONICAL_INTENT", "ADAPTERS"], calls
        assert [c for c in calls if c[0] == "ADAPTERS"][0][2] is True
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                "decision_id=$1", did)
        assert o is not None and o["order_id"] == cg["order_id"]
        # the paper order's fields came FROM the intent
        assert o["strategy"] == PB.CG_STRATEGY
        assert o["us_market_slug"] == v["slug"]
        d = await conn.fetchrow("SELECT verdict, limit_price, proposed_qty "
                                " FROM paper_decisions WHERE decision_id=$1",
                                did)
        assert d["verdict"] == "ENTER"
        assert float(o["limit_price"]) == float(d["limit_price"])
        assert float(o["qty"]) == float(d["proposed_qty"])
        # not a hook failure: the decision and its order were both made
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_hook_failures WHERE valuation_id=$1 "
            "  AND strategy=$2", v["valuation_id"], PB.CG_STRATEGY) == 0
        # and the backstop has nothing to name, later
        ctx = {"account_id": acct["account_id"], "now": now + 600.0}
        got = await PD.step_enter_backstop(conn, ctx)
        assert got["enter_without_order"] == 0
    finally:
        await PL.purge_everything(conn)
        await conn.execute("DELETE FROM paper_hook_failures WHERE "
                           " account_id LIKE 'paper_test_%'")
        await conn.close()


@pg
async def test_a_wedged_order_sequence_is_named_by_the_backstop_once(
        cg_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    calls: list = []
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "enterwedge", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        monkeypatch.setattr(PR, "VALUATION_HOOK_TIMEOUT_S", 2.0)
        monkeypatch.setattr(PD, "ENTER_ORDER_GRACE_S", 0.5)
        _hooks(monkeypatch, hook_sleep_s=30.0, calls=calls)
        g = await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
        cg = g["benchmark_completed_game"]
        assert cg["timeout"] is True and cg["enter_recorded_without_order"]
        did = cg["decision_id"]
        assert did and cg["verdict"] == "ENTER"
        assert ("EXECUTION_HOOK_DONE", did) not in calls
        assert await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                   " decision_id=$1", did) == 0
        att = await conn.fetchrow(
            "SELECT outcome, decision_id, verdict FROM "
            " paper_evaluation_attempts WHERE valuation_id=$1 AND strategy=$2",
            v["valuation_id"], PB.CG_STRATEGY)
        assert (att["outcome"], att["decision_id"], att["verdict"]) == \
            ("TIMEOUT", did, "ENTER")
        # YOUNGER THAN THE THRESHOLD: not named yet (an order may still land)
        ctx = {"account_id": acct["account_id"], "now": now + 10.0}
        assert (await PD.step_enter_backstop(conn, ctx))[
            "enter_without_order"] == 0
        # PAST IT: one named finding, and only one however often it runs
        ctx["now"] = now + PD.ENTER_WITHOUT_ORDER_AFTER_S + 5.0
        got = await PD.step_enter_backstop(conn, ctx)
        assert got["enter_without_order"] == 1 and got["decision_ids"] == \
            [did]
        assert (await PD.step_enter_backstop(conn, ctx))[
            "enter_without_order"] == 0
        f = await conn.fetchrow(
            "SELECT * FROM paper_audrey_findings WHERE kind=$1 AND "
            " subject=$2", PD.F_ENTER_WITHOUT_ORDER, did)
        assert f["severity"] == "WARNING"
        detail = H.j(f["detail"])
        assert detail["strategy"] == PB.CG_STRATEGY
        assert detail["valuation_id"] == v["valuation_id"]
        assert detail["threshold_s"] == PD.ENTER_WITHOUT_ORDER_AFTER_S
        # nothing was placed late
        assert await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                   " decision_id=$1", did) == 0
    finally:
        await PL.purge_everything(conn)
        await conn.execute("DELETE FROM paper_hook_failures WHERE "
                           " account_id LIKE 'paper_test_%'")
        await conn.close()


@pg
async def test_the_backstop_never_names_an_enter_with_an_order_or_a_refusal(
        cg_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "enterok", now=now)
        t = PL.Transport(now)
        v1 = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                                compatibility="INCOMPATIBLE")
        t.set(v1["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        g = await PR.decide_valuation(
            conn, valuation_id=v1["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
        did1 = g["benchmark_completed_game"]["decision_id"]
        assert g["benchmark_completed_game"].get("order_id")
        # an ENTER whose order the paper risk check refused carries that
        # finding, and is not an ENTER without an order
        row = await conn.fetchrow("SELECT * FROM paper_decisions WHERE "
                                  " decision_id=$1", did1)
        did2 = did1 + "x"
        await conn.execute(
            "INSERT INTO paper_decisions (decision_id, session_id, "
            " account_id, decided_at, valuation_id, us_market_slug, "
            " holding_side, intent, fixture, label, verdict, refusal, "
            " refusals, internal_model, pinnacle, qualification_gaps, "
            " policy_version, simulator_version, strategy) VALUES ($1,$2,$3,"
            " $4,NULL,$5,$6,$7,$8,'{}'::jsonb,'ENTER',NULL,'{}'::text[],$9,"
            " $10,$11,$12,$13,$14)",
            did2, row["session_id"], row["account_id"], row["decided_at"],
            row["us_market_slug"], row["holding_side"], row["intent"],
            row["fixture"], row["internal_model"], row["pinnacle"],
            row["qualification_gaps"], row["policy_version"],
            row["simulator_version"], row["strategy"])
        ctx = {"session_id": row["session_id"],
               "account_id": acct["account_id"], "now": now}
        await PD._finding(conn, ctx, kind=PD.R_ORDER_REFUSED, subject=did2,
                          detail={"refusal": "ABOVE_THE_PER_ORDER_CAP"})
        late = {"account_id": acct["account_id"], "now": now + 3600.0}
        got = await PD.step_enter_backstop(conn, late)
        assert got["enter_without_order"] == 0 and got["examined"] == 0
        # beyond the lookback an ENTER is no longer this pass's business
        far = {"account_id": acct["account_id"],
               "now": now + PD.ENTER_BACKSTOP_LOOKBACK_S + 60.0}
        assert (await PD.step_enter_backstop(conn, far))["examined"] == 0
        # the step is on the paper pass, after the delayed-fill step
        names = [n for n, _ in PR.default_steps()]
        assert names.index("enter_backstop") > names.index(
            "simulate_after_delay")
    finally:
        await PL.purge_everything(conn)
        await conn.close()
