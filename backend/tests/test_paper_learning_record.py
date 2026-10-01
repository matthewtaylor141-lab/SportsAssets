"""THE PAPER AGENTS' LEARNING RECORD, THROUGH THE REAL ORCHESTRATION.

What is proved here (agents/paper_learning.py, migration 185):

  1 · every evaluated opportunity keeps, on its decision record, the inputs
      as read (with their SHA-256), versions, decision, alternatives, prices,
      fees and a plain explanation -- going forward; an older record reads
      NOT_RECORDED_BEFORE_MIGRATION_185 and is never rewritten;
  2 · ONE read returns a filled completed-game position's whole chain --
      decision, order, fill, ledger debit, handoff, Xavier's reviews, the
      venue-price settlement, the ledger result and Audrey's findings --
      with ids and timestamps, and a missing link is listed as MISSING;
  3 · Audrey audits each meaningful event once, as it happens, inside the
      scheduled pass, across repeated passes and a restart;
  4 · lessons land in each agent's memory with provenance that reproduces;
  5 · a proposal's training period must end before its later evaluation
      period (refused otherwise, by the code and by the database), and the
      evaluation reports INSUFFICIENT_FORWARD_DATA honestly, uses forward
      records only, labels never-executed results COUNTERFACTUAL_NOT_
      EXECUTABLE_PROOF, and activation stays behind the explicit control.

SYNTHETIC: valuations from `paper_live_fixture.valuation`, books from the
fixture transport, scratch test accounts only. No real money, no venue order:
the market-data client counts mutation attempts and the proofs assert zero.
"""
from __future__ import annotations

import hashlib
import json
import time

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_learning as PLRN
from sportsassets.agents import paper_runtime as PR

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

V2_ACT = "paperact:%s:V2:OWNER_DECISION" % PB.CG_STRATEGY
V1_ACT = "paperact:%s:V1" % PB.CG_STRATEGY


async def _set_head(conn, version_id, activation_id) -> None:
    """The shared parameter head, set directly (proof scaffolding only)."""
    await conn.execute(
        "UPDATE paper_policy_parameter_heads SET active_version_id=$2, "
        " activation_id=$3 WHERE policy_key=$1", PB.CG_STRATEGY, version_id,
        activation_id)


@pytest.fixture(scope="module", autouse=True)
def _v1_baseline_for_these_proofs():
    """THESE PROOFS EXERCISE THE LEARNING MECHANISM FROM V1 (5.0 pp), which
    stays a valid, restorable version inside the 0.5..6.0 pp bounds. The
    production head is the owner's decision V2 (0.5 pp, migration 188): it
    is put back afterwards, with the proofs' own rollback audit rows gone,
    so the rest of the suite sees exactly what migration 188 left."""
    import asyncio

    async def go(to_v1: bool):
        if not H.DSN:
            return
        conn = await H.connect()
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL session_replication_role = "
                                   "replica")
                if to_v1:
                    await _set_head(conn, PB.CG_V1_VERSION_ID, V1_ACT)
                else:
                    await conn.execute(
                        "DELETE FROM paper_policy_parameter_activations "
                        " WHERE actor = 'test-cleanup' AND policy_key=$1",
                        PB.CG_STRATEGY)
                    await _set_head(conn, PB.CG_V2_VERSION_ID, V2_ACT)
        finally:
            await conn.close()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go(True))
        yield
        loop.run_until_complete(go(False))
    finally:
        loop.close()
FEE = H.flat_fee(0.01)
CG = PB.CG_STRATEGY


async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, client, **kw):
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, fee_fn=FEE, sleep=_nosleep, **kw)


@pytest.fixture
def both_on(monkeypatch):
    # THE PRODUCTION SELECTION since migration 184: the completed-game
    # policy's row on, the strict benchmark's off (one entry experiment)
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


async def _cleanup(conn, accounts) -> None:
    """The proofs' own learning records (scratch accounts only)."""
    accts = [a for a in accounts if a]
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        # the parameter versions / audit rows these proofs' proposals made
        # (the proofs roll back first: the head is on V1 again by now)
        assert await conn.fetchval(
            "SELECT active_version_id FROM paper_policy_parameter_heads "
            " WHERE policy_key=$1", PB.CG_STRATEGY) == PB.CG_V1_VERSION_ID
        props = [r["proposal_id"] for r in await conn.fetch(
            "SELECT proposal_id FROM paper_improvement_proposals WHERE "
            " account_id = ANY($1::text[])", accts)]
        vers = [r["version_id"] for r in await conn.fetch(
            "SELECT version_id FROM paper_policy_parameter_versions WHERE "
            " proposal_id = ANY($1::text[])", props)]
        await conn.execute(
            "DELETE FROM paper_policy_parameter_activations WHERE "
            " proposal_id = ANY($1::text[]) OR version_id = ANY($2::text[]) "
            " OR previous_version_id = ANY($2::text[])", props, vers)
        await conn.execute("DELETE FROM paper_policy_parameter_versions "
                           " WHERE version_id = ANY($1::text[])", vers)
        await conn.execute(
            "UPDATE paper_policy_parameter_heads SET activation_id=$2 "
            " WHERE policy_key=$1", PB.CG_STRATEGY,
            "paperact:%s:V1" % PB.CG_STRATEGY)
        await conn.execute(
            "DELETE FROM paper_improvement_proposal_events WHERE proposal_id"
            " IN (SELECT proposal_id FROM paper_improvement_proposals WHERE "
            " account_id = ANY($1::text[]))", accts)
        await conn.execute("DELETE FROM paper_improvement_proposals WHERE "
                           " account_id = ANY($1::text[])", accts)
        await conn.execute("DELETE FROM paper_agent_lessons WHERE "
                           " account_id = ANY($1::text[])", accts)
        await conn.execute(
            "DELETE FROM agent_task_events WHERE task_id IN (SELECT task_id "
            " FROM agent_tasks WHERE spec->>'account_id' = ANY($1::text[]) "
            " OR (kind = 'PAPER_AUDIT_FINDING' AND spec->>'session_id' IN "
            " (SELECT session_id FROM paper_sessions WHERE account_id = "
            " ANY($1::text[]))))", accts)
        await conn.execute(
            "DELETE FROM agent_tasks WHERE spec->>'account_id' = "
            " ANY($1::text[]) OR (kind = 'PAPER_AUDIT_FINDING' AND "
            " spec->>'session_id' IN (SELECT session_id FROM paper_sessions "
            " WHERE account_id = ANY($1::text[])))", accts)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           [PLRN.WATERMARK_KEY % a for a in accts])


async def _rollback_to_v1(conn) -> None:
    """Leave the shared policy head on V1 whatever a proof did."""
    for _ in range(10):
        if await conn.fetchval(
                "SELECT active_version_id FROM paper_policy_parameter_heads "
                " WHERE policy_key=$1", PB.CG_STRATEGY) == \
                PB.CG_V1_VERSION_ID:
            return
        await PLRN.rollback_policy_parameters(conn, actor="test-cleanup",
                                              reason="proof cleanup")


async def _venue_price_path(conn, tag, now):
    """ENTRY -> SIMULATED FILL -> HANDOFF -> XAVIER -> POSTPONED (pending) ->
    THE VENUE PUBLISHES ITS LAST-FAIR-MARKET PRICE (0.43) -> SETTLED, through
    five real paper passes. Returns what the proofs need."""
    acct = await PL.new_account(conn, tag, now=now)
    t = PL.Transport(now)
    v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                           compatibility="INCOMPATIBLE")
    t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
    client = PL.client(t)
    passes = []
    for dt in (0, 5):
        passes.append(await _pass(conn, acct, t, now + dt, client))
    o = await conn.fetchrow(
        "SELECT * FROM paper_orders WHERE account_id=$1 AND strategy=$2 "
        "   AND role='ENTRY'", acct["account_id"], CG)
    assert o is not None and o["state"] == "FILLED", o
    first = await conn.fetchval(
        "SELECT fill_id FROM paper_fills WHERE order_id=$1 "
        " ORDER BY filled_at, fill_id LIMIT 1", o["order_id"])
    pending_chain = await PLRN.fill_chain(conn, first)
    passes.append(await _pass(conn, acct, t, now + 10, client))
    await conn.execute(
        "UPDATE external_valuations SET settlement_read='0.43', "
        " settlement_read_at=now() WHERE id=$1", v["valuation_id"])
    passes.append(await _pass(conn, acct, t, now + 15, client))
    for p in passes:
        assert p["ran"] and not p["errors"], p["errors"]
    assert client.mutation_attempts == 0
    return {"acct": acct, "transport": t, "client": client, "order": o,
            "first_fill": first, "valuation": v, "passes": passes,
            "pending_chain": pending_chain}


# ═════════════════════════════════════════════════════════════════════
# 1 · THE DECISION RECORD RETAINS WHAT WAS KNOWN AT THE INSTANT
# ═════════════════════════════════════════════════════════════════════

def test_a_record_from_before_185_reads_as_such_and_is_not_reconstructed():
    old = {"decision_id": "paperdec:x", "verdict": "REFUSE",
           "refusal": "PROBABILITY_EVIDENCE_STALE", "valuation_id": 1,
           "pinnacle": {"p": 0.6}, "policy_version": "V", "book_obs_id": None,
           "internal_model": {"refusal": "NO_RESEARCH_MODEL_CANDIDATE_EXISTS"},
           "alternatives": {"NO_TRADE": {}}, "provenance": None}
    a = PLRN.decision_record_audit(old)
    assert a["provenance"] == PLRN.NOT_BEFORE_185
    assert a["complete"] is False
    for k in ("inputs_snapshot", "explanation", "versions"):
        assert a["fields"][k] == PLRN.NOT_BEFORE_185
    assert a["fields"]["prices"] == PLRN.NOT_APPLICABLE
    assert a["fields"]["inputs"] == PLRN.PRESENT


@pg
async def test_every_decision_keeps_inputs_versions_prices_fees_and_why(
        both_on):
    conn = await H.connect()
    now = time.time() + 5.0
    acct = None
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "lrdec", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        p = await _pass(conn, acct, t, now, PL.client(t))
        assert not p["errors"], p["errors"]
        rows = {r["strategy"]: r for r in await conn.fetch(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2", acct["session_id"], v["valuation_id"])}
        # both deciding strategies recorded the valuation: CG enters, the
        # two-model strategy refuses (no model); the strict benchmark's
        # entries are off since migration 184
        assert set(rows) == {CG, PD.STRATEGY}
        row_v = dict(await conn.fetchrow(
            "SELECT * FROM external_valuations WHERE id=$1",
            v["valuation_id"]))
        want_sha = PLRN.valuation_snapshot(row_v)["inputs_sha256"]
        for strat, d in rows.items():
            prov = H.j(d["provenance"])
            assert prov["record_version"] == PLRN.RECORD_VERSION, strat
            assert not prov.get("error"), prov
            assert prov["inputs"]["inputs_sha256"] == want_sha, strat
            assert prov["inputs"]["valuation"]["id"] == v["valuation_id"]
            assert prov["inputs"]["session_config_sha"]
            assert prov["versions"]["policy"] == d["policy_version"]
            assert prov["versions"]["strategy"] == strat
            assert prov["decision"]["verdict"] == d["verdict"]
            assert set(prov["alternatives_considered"]) >= {"NO_TRADE"}
            assert prov["explanation"].startswith(d["verdict"])
            audit = PLRN.decision_record_audit(dict(d))
            assert audit["complete"] is True, (strat, audit)
        cg = rows[CG]
        prov = H.j(cg["provenance"])
        assert cg["verdict"] == "ENTER"
        assert prov["versions"]["code"] == PB.CG_VERSION
        assert prov["versions"]["model"]["available"] is False
        assert prov["prices"]["limit_price"] == pytest.approx(0.50)
        assert prov["prices"]["best_level_price"] == pytest.approx(0.50)
        assert prov["fees"]["fees_usd"] is not None
        assert prov["expected"]["net_expected_profit_usd"] > 0
        assert "OPPOSITE_SIDE_SAME_MARKET" in prov["alternatives_considered"]
        assert "buy" in prov["explanation"]
        st = H.j(rows[PD.STRATEGY]["provenance"])
        assert st["explanation"].startswith("REFUSE")
        assert rows[PD.STRATEGY]["refusal"] in st["explanation"]
        assert st["fees"]["basis"].startswith("NOT_COMPUTED")
        assert prov["versions"]["parameters"]["version_id"] == \
            PB.CG_V1_VERSION_ID
        # THE RECORD IS NEVER REWRITTEN (append-only, as before)
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE paper_decisions SET provenance=NULL "
                               " WHERE decision_id=$1", cg["decision_id"])
    finally:
        await _cleanup(conn, [acct and acct["account_id"]])
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · THE LINKED CHAIN OF A FILLED COMPLETED-GAME POSITION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_chain_of_a_filled_cg_position_is_complete_through_venue_price_settlement(  # noqa: E501
        both_on):
    conn = await H.connect()
    now = time.time() + 5.0
    acct = None
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        got = await _venue_price_path(conn, "lrchain", now)
        acct = got["acct"]
        o = got["order"]
        # BEFORE THE SETTLEMENT: the open position's exit/settlement and its
        # ledger result are MISSING and pending -- listed, not omitted
        pc = got["pending_chain"]
        assert pc["complete"] is False
        assert set(pc["pending"]) == {"EXIT_OR_SETTLEMENT", "LEDGER_RESULT"}
        assert pc["defects"] == [], pc["defects"]
        es = next(x for x in pc["links"] if x["link"] == "EXIT_OR_SETTLEMENT")
        assert es["status"] == PLRN.MISSING and es["why"].startswith(
            "PENDING")

        c = await PLRN.fill_chain(conn, got["first_fill"])
        assert c["found"] and c["strategy"] == CG
        assert c["complete"] is True, [(x["link"], x["status"], x["why"])
                                       for x in c["links"]]
        assert c["missing"] == [] and c["defects"] == []
        links = {x["link"]: x for x in c["links"]}
        assert list(links) == [
            "DEREK_DECISION", "ENTRY_ORDER", "ENTRY_FILLS",
            "LEDGER_CASH_DEBIT", "XAVIER_HANDOFF", "XAVIER_REVIEWS",
            "XAVIER_ACTIONS", "EXIT_OR_SETTLEMENT", "LEDGER_RESULT",
            "AUDREY_AUDIT"]
        for name, x in links.items():
            if x["status"] == PLRN.PRESENT:
                assert x["ids"] and x["at"] is not None, name
        d = links["DEREK_DECISION"]
        assert d["ids"] == [o["decision_id"]]
        assert d["detail"]["record_audit"]["complete"] is True
        assert d["detail"]["explanation"].startswith("ENTER")
        assert links["ENTRY_ORDER"]["ids"] == [o["order_id"]]
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " order_id=$1", o["order_id"])
        assert set(links["ENTRY_FILLS"]["ids"]) == {f["fill_id"]
                                                     for f in fills}
        debit = sum(e["cash_delta_usd"] for e in
                    links["LEDGER_CASH_DEBIT"]["detail"]["entries"])
        assert debit == pytest.approx(-sum(
            float(f["gross_usd"]) + float(f["fee_usd"]) for f in fills))
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", o["group_id"])
        assert links["XAVIER_HANDOFF"]["ids"] == [h["handoff_id"]]
        assert links["XAVIER_REVIEWS"]["detail"]["reviews"][0][
            "trigger"] == "FIRST_FILL"
        # Xavier's standing protection is his management action on the group
        assert links["XAVIER_ACTIONS"]["status"] in (PLRN.PRESENT,
                                                     PLRN.NOT_APPLICABLE)
        assert links["XAVIER_ACTIONS"]["why"] or links["XAVIER_ACTIONS"][
            "ids"]
        s = links["EXIT_OR_SETTLEMENT"]["detail"]["settlements"]
        assert len(s) == 1 and s[0]["outcome"] == "SETTLED_AT_VENUE_PRICE"
        qty = float(o["filled_qty"])
        lr = links["LEDGER_RESULT"]
        assert lr["detail"]["entries"][0]["kind"] == "SETTLEMENT"
        assert lr["detail"]["entries"][0]["cash_delta_usd"] == \
            pytest.approx(0.43 * qty)
        cost = sum(float(f["gross_usd"]) + float(f["fee_usd"]) for f in fills)
        assert lr["detail"]["realized_pnl_usd"] == pytest.approx(
            0.43 * qty - cost)
        aud = links["AUDREY_AUDIT"]["detail"]
        assert all(r["audited"] for r in aud["required_event_audits"])
        kinds = {f["kind"] for f in aud["findings"]}
        assert {PLRN.EV_FIRST_FILL, PLRN.EV_HANDOFF, PLRN.EV_SETTLEMENT,
                PLRN.EV_VENUE_PRICE, PLRN.EV_EXCEPTIONAL} <= kinds
        assert [t["at"] for t in c["timeline"]] == sorted(
            t["at"] for t in c["timeline"])
        assert (await PLRN.fill_chain(conn, "paperfill:nope"))["refusal"] \
            == PLRN.R_NO_FILL
    finally:
        await _cleanup(conn, [acct and acct["account_id"]])
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_missing_link_is_flagged_missing_not_omitted(both_on):
    """A pass cut short before the handoff: the filled entry's chain names
    XAVIER_HANDOFF and XAVIER_REVIEWS as MISSING defects."""
    conn = await H.connect()
    now = time.time() + 5.0
    acct = None
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "lrmiss", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        client = PL.client(t)
        cut = [s for s in PR.default_steps()
               if s[0] not in ("handoff", "xavier", "audrey_events",
                               "learning")]
        for dt in (0, 5):
            p = await _pass(conn, acct, t, now + dt, client, steps=cut)
            assert not p["errors"], p["errors"]
        fid = await conn.fetchval(
            "SELECT f.fill_id FROM paper_fills f JOIN paper_orders o ON "
            " o.order_id=f.order_id WHERE o.account_id=$1 AND o.role='ENTRY'"
            " ORDER BY f.filled_at LIMIT 1", acct["account_id"])
        assert fid
        c = await PLRN.fill_chain(conn, fid)
        links = {x["link"]: x for x in c["links"]}
        assert c["complete"] is False
        assert links["XAVIER_HANDOFF"]["status"] == PLRN.MISSING
        assert links["XAVIER_HANDOFF"]["defect"] is True
        assert links["XAVIER_HANDOFF"]["why"] == \
            "A_FILLED_ENTRY_WITHOUT_A_HANDOFF"
        assert "XAVIER_HANDOFF" in c["defects"]
        assert links["AUDREY_AUDIT"]["status"] == PLRN.MISSING
        assert "NOT_YET_AUDITED" in links["AUDREY_AUDIT"]["why"]
        assert links["LEDGER_CASH_DEBIT"]["status"] == PLRN.PRESENT
    finally:
        await _cleanup(conn, [acct and acct["account_id"]])
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · AUDREY AUDITS EACH EVENT ONCE, AS IT HAPPENS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_audrey_audits_each_event_once_across_passes_and_a_restart(
        both_on):
    conn = await H.connect()
    now = time.time() + 5.0
    acct = None
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        got = await _venue_price_path(conn, "lrevt", now)
        acct = got["acct"]
        o = got["order"]
        steps = [p["steps"].get("audrey_events") or {} for p in
                 got["passes"]]
        # AS IT HAPPENS: the fill (simulated after the 2 s delay inside the
        # first pass) and the handoff are audited in that same pass, the
        # settlement in the pass that booked it -- and nothing in between
        assert got["passes"][0]["fills"] >= 1
        assert steps[0]["audited"].get(PLRN.EV_FIRST_FILL) == 1
        assert steps[0]["audited"].get(PLRN.EV_HANDOFF) == 1
        assert steps[1]["events_audited"] == 0
        assert steps[2]["events_audited"] == 0
        assert steps[3]["audited"].get(PLRN.EV_SETTLEMENT) == 1
        assert steps[3]["audited"].get(PLRN.EV_VENUE_PRICE) == 1
        assert steps[3]["audited"].get(PLRN.EV_EXCEPTIONAL) == 1

        async def snapshot():
            return [(r["kind"], r["subject"], r["n"]) for r in
                    await conn.fetch(
                        "SELECT kind, subject, count(*) AS n FROM "
                        " paper_audrey_findings WHERE account_id=$1 AND "
                        " kind = ANY($2::text[]) GROUP BY 1, 2 ORDER BY 1, 2",
                        acct["account_id"], list(PLRN.EVENT_KINDS))]
        before = await snapshot()
        assert before and all(n == 1 for _, _, n in before), before
        # REPEATED PASSES
        for dt in (20, 25):
            p = await _pass(conn, acct, got["transport"], now + dt,
                            got["client"])
            assert not p["errors"], p["errors"]
            assert p["steps"]["audrey_events"]["events_audited"] == 0
        # A RESTART: a new connection, every in-process lock and cache gone
        await conn.close()
        conn = await H.connect()
        PR._LOCK.update(lock=None, loop=None)
        PB._CONTEXT_CACHE.clear()
        PD._CONTEXT_CACHE.clear()
        p = await _pass(conn, acct, got["transport"], now + 30,
                        PL.client(got["transport"]))
        assert not p["errors"], p["errors"]
        assert p["steps"]["audrey_events"]["events_audited"] == 0
        assert await snapshot() == before
        # each finding passed its checks and names the event's records
        rows = await conn.fetch(
            "SELECT kind, severity, detail FROM paper_audrey_findings WHERE "
            " account_id=$1 AND kind = ANY($2::text[])", acct["account_id"],
            list(PLRN.EVENT_KINDS))
        for r in rows:
            assert r["severity"] == "INFO", (r["kind"], r["detail"])
            assert H.j(r["detail"])["passed"] is True
        vp = next(H.j(r["detail"]) for r in rows
                  if r["kind"] == PLRN.EV_VENUE_PRICE)
        assert vp["payout_per_contract"] == pytest.approx(0.43)
        assert vp["venue_price_checks_passed"] is True
        assert vp["never_an_assumed_refund"] is True
        exc = next(H.j(r["detail"]) for r in rows
                   if r["kind"] == PLRN.EV_EXCEPTIONAL)
        assert exc["result_usd"] < 0 and exc["expected_net_at_entry_usd"] > 0
        ff = next(H.j(r["detail"]) for r in rows
                  if r["kind"] == PLRN.EV_FIRST_FILL)
        assert ff["order_id"] == o["order_id"]
        assert ff["ledger_debit_matches"] is True
        # THE SAME AUDIT CALLED DIRECTLY AGAIN WRITES NOTHING
        ctx = {"account_id": acct["account_id"],
               "session_id": acct["session_id"], "now": now + 31}
        again = await PLRN.step_audit_events(conn, ctx)
        assert again["events_audited"] == 0
    finally:
        await _cleanup(conn, [acct and acct["account_id"]])
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_ledger_inconsistency_is_audited_critical_once():
    """A synthetic settlement row with no ledger entry (written directly, as
    a defect would leave it) is one CRITICAL event, and opens a task."""
    conn = await H.connect()
    acct = None
    try:
        acct = await H.new_account(conn, "lrledger")
        sid = "paperset:test-%s" % acct["account_id"][-10:]
        await conn.execute(
            "INSERT INTO paper_settlements (settlement_id, account_id, "
            " position_key, settlement_event_key, version, group_id, "
            " us_market_slug, holding_side, qty, outcome, "
            " payout_per_contract, payout_usd, evidence, evidence_source, "
            " settled_at) VALUES ($1,$2,'paperpos:x','k',1,'papergrp:x',"
            " 'mkt','LONG',10,'WON',1,10,'{}'::jsonb,'test',now())", sid,
            acct["account_id"])
        ctx = {"account_id": acct["account_id"],
               "session_id": acct["session_id"], "now": H.T0}
        a = await PLRN.step_audit_events(conn, ctx)
        b = await PLRN.step_audit_events(conn, ctx)
        assert a["audited"].get(PLRN.EV_LEDGER) == 1
        assert b["events_audited"] == 0
        f = await conn.fetchrow(
            "SELECT * FROM paper_audrey_findings WHERE account_id=$1 AND "
            " kind=$2 AND subject=$3", acct["account_id"], PLRN.EV_LEDGER,
            sid)
        assert f["severity"] == "CRITICAL"
        assert f["improvement_task_id"]
    finally:
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            await conn.execute("DELETE FROM paper_settlements WHERE "
                               " account_id=$1", acct["account_id"])
        await _cleanup(conn, [acct and acct["account_id"]])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · LESSONS IN EACH AGENT'S MEMORY, WITH PROVENANCE
# ═════════════════════════════════════════════════════════════════════

def _reproduce(ids_by_table: dict) -> str:
    lines = sorted({"%s:%s" % (t, i) for t, ids in ids_by_table.items()
                    for i in ids if i is not None})
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


@pg
async def test_lessons_are_written_with_provenance_and_management_reads_them(
        both_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    acct = None
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        monkeypatch.setattr(PLRN, "LEARNING_EVERY_S", 0.0)
        got = await _venue_price_path(conn, "lrles", now)
        acct = got["acct"]
        a = acct["account_id"]
        o = got["order"]
        last = got["passes"][-1]["steps"]["learning"]
        assert last["ran"] is True and last["error_count"] == 0, last
        les = {(x["agent_id"], x["kind"], x["strategy"]): x
               for x in await PLRN.lessons(conn, account_id=a)}
        for key in ((PLRN.DEREK, PLRN.L_REFUSALS, CG),
                    (PLRN.DEREK, PLRN.L_REFUSALS, PD.STRATEGY),
                    (PLRN.DEREK, PLRN.L_SETTLED, CG),
                    (PLRN.DEREK, PLRN.L_FILLS, CG),
                    (PLRN.DEREK, PLRN.L_EXCEPTIONAL, CG),
                    (PLRN.XAVIER, PLRN.L_MANAGEMENT, CG),
                    (PLRN.AUDREY, PLRN.L_COVERAGE, None)):
            assert key in les, (key, sorted(les))
            x = les[key]
            assert x["basis"] == PLRN.FORWARD_ONLY
            assert x["provenance"]["record_count"] >= 1
            assert len(x["provenance"]["ids_sha256"]) == 64
        # THE SETTLED-ENTRY LESSON NAMES THE RECORDS THAT PRODUCED IT, AND
        # ITS HASH REPRODUCES FROM THEM
        s = les[(PLRN.DEREK, PLRN.L_SETTLED, CG)]
        sid = await conn.fetchval("SELECT settlement_id FROM "
                                  " paper_settlements WHERE group_id=$1",
                                  o["group_id"])
        assert s["provenance"]["ids_sample"]["paper_decisions"] == [
            o["decision_id"]]
        assert s["provenance"]["ids_sample"]["paper_settlements"] == [sid]
        assert s["provenance"]["ids_sha256"] == _reproduce({
            "paper_decisions": [o["decision_id"]],
            "paper_settlements": [sid], "paper_groups": [o["group_id"]]})
        m = s["metrics"]
        assert m["closed_positions"] == 1
        assert m["by_outcome"] == {"SETTLED_AT_VENUE_PRICE": 1}
        assert m["realized_minus_expected_usd"] < 0
        assert m["exceptional_settlements"] == 1
        # an actionable lesson opened ONE improvement task for its agent
        task = await conn.fetchrow("SELECT * FROM agent_tasks WHERE "
                                   " task_id=$1", s["improvement_task_id"])
        assert task["kind"] == PLRN.TASK_KIND and task["assignee"] == "DEREK"
        assert task["kind"] != "IMPROVEMENT"
        f = les[(PLRN.DEREK, PLRN.L_FILLS, CG)]["metrics"]
        assert f["fill_ratio_vs_optimistic"] == pytest.approx(1.0)
        x = les[(PLRN.XAVIER, PLRN.L_MANAGEMENT, CG)]
        assert x["metrics"]["reviews"] >= 1
        cf = x["metrics"]["counterfactual_exit_at_first_review"]
        assert cf["label"] == PLRN.COUNTERFACTUAL
        if cf["positions_compared"]:
            assert "COUNTERFACTUAL (not executable proof)" in x["statement"]
        au = les[(PLRN.AUDREY, PLRN.L_COVERAGE, None)]["metrics"]
        assert au["chains_complete"] == 1 and au["chains_with_defects"] == 0
        # UNCHANGED CONTENT WRITES NOTHING; THE MEMORY IS APPEND-ONLY
        n0 = await conn.fetchval("SELECT count(*) FROM paper_agent_lessons "
                                 " WHERE account_id=$1", a)
        p = await _pass(conn, acct, got["transport"], now + 20,
                        got["client"])
        assert not p["errors"], p["errors"]
        assert p["steps"]["learning"]["lessons_written"] == 0, \
            p["steps"]["learning"]
        assert await conn.fetchval("SELECT count(*) FROM paper_agent_lessons"
                                   " WHERE account_id=$1", a) == n0
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE paper_agent_lessons SET statement='x'"
                               " WHERE lesson_id=$1", s["lesson_id"])
        # THE THROTTLE: at the default cadence the next pass is NOT_DUE
        monkeypatch.setattr(PLRN, "LEARNING_EVERY_S", 3600.0)
        p = await _pass(conn, acct, got["transport"], now + 25,
                        got["client"])
        assert p["steps"]["learning"]["why"] == "NOT_DUE"

        # THE MANAGEMENT READ: per agent, learned / proposed / evaluation /
        # active -- each section OK, EMPTY or UNAVAILABLE
        summ = await PLRN.learning_summary(conn, account_id=a)
        for ag in PLRN.AGENT_IDS:
            sec = summ["agents"][ag]
            assert sec["status"] == "OK", (ag, sec)
            view = sec["data"]
            assert view["learned"] and all(
                y["statement"] and y["ids_sha256"] for y in view["learned"])
            assert view["proposed_change"]["status"] == "NONE"
            assert view["proposed_change"]["why"]
            assert view["evaluation"] is None and view["active"] is False
        assert summ["event_audits"]["status"] == "OK"
        assert summ["chains"]["status"] == "OK"
        assert summ["chains"]["data"][0]["complete"] is True
        assert summ["activation_control"]["data"]["enabled"] is False
    finally:
        await _cleanup(conn, [acct and acct["account_id"]])
        await PL.purge_everything(conn)
        await conn.close()


async def test_a_failed_read_is_unavailable_never_a_zero():
    class Broken:
        async def fetchval(self, sql, *a):
            return True

        async def fetch(self, sql, *a):
            raise RuntimeError("the read failed")

        async def fetchrow(self, sql, *a):
            raise RuntimeError("the read failed")
    s = await PLRN.learning_summary(Broken(), account_id="paper_x")
    for ag in PLRN.AGENT_IDS:
        assert s["agents"][ag]["status"] == "UNAVAILABLE"
        assert "the read failed" in s["agents"][ag]["why"]
        assert s["agents"][ag]["data"] is None
    assert s["event_audits"]["status"] == "UNAVAILABLE"
    assert s["chains"]["status"] == "UNAVAILABLE"


# ═════════════════════════════════════════════════════════════════════
# 5 · PROPOSALS: TRAIN STRICTLY BEFORE A LATER EVALUATION, FORWARD ONLY
# ═════════════════════════════════════════════════════════════════════

def test_the_split_rule_refuses_overlap_peeking_and_empty_periods():
    ok = dict(training_start=0, training_end=10, proposed_at=10,
              evaluation_start=10, evaluation_end=20)
    assert PLRN.check_protocol(**ok) is None
    assert PLRN.check_protocol(**dict(ok, evaluation_start=5)) in (
        PLRN.R_OVERLAP,)
    assert PLRN.check_protocol(**dict(ok, training_end=15, proposed_at=15,
                                      evaluation_start=12)) == PLRN.R_OVERLAP
    assert PLRN.check_protocol(**dict(ok, proposed_at=12)) == PLRN.R_PEEK
    assert PLRN.check_protocol(**dict(ok, proposed_at=8)) == \
        PLRN.R_TRAIN_AFTER_PROPOSAL
    assert PLRN.check_protocol(**dict(ok, evaluation_end=10)) == \
        PLRN.R_EMPTY_PERIOD


async def _decision_row(conn, acct, *, strategy, at, slug, edge_pp,
                        price=0.50, enter=False):
    did = "papertest:%s" % hashlib.sha256(
        ("%s:%s:%s" % (acct["account_id"], slug, at)).encode()
    ).hexdigest()[:20]
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, verdict, refusal, "
        " refusals, internal_model, pinnacle, book, qualification_gaps, "
        " policy_version, policy_decision, simulator_version, strategy) "
        "VALUES ($1,$2,$3,to_timestamp($4),$5,'LONG',$10,$11,$12::text[],"
        " '{}'::jsonb,'{}'::jsonb,$6::jsonb,'[]'::jsonb,$7,$8::jsonb,"
        " 'PAPER_SIM_V1',$9)",
        did, acct["session_id"], acct["account_id"], float(at), slug,
        json.dumps({"levels": [{"price": price, "qty": 100}]}),
        PB.CG_VERSION, json.dumps({"gross_edge_pp": edge_pp}), strategy,
        "ENTER" if enter else "REFUSE",
        None if enter else "BELOW_MIN_GROSS_EDGE",
        [] if enter else ["BELOW_MIN_GROSS_EDGE"])
    return did


@pg
async def test_a_proposal_refuses_overlapping_periods_and_reports_insufficient_forward_data():  # noqa: E501
    conn = await H.connect()
    t0 = time.time()
    accts = []
    try:
        await PL.purge_everything(conn)
        a = await H.new_account(conn, "lrprop", now=t0)
        b = await H.new_account(conn, "lrprop2", now=t0)
        accts = [a["account_id"], b["account_id"]]
        change = {"parameter": "min_gross_edge_pp", "from": 5.0, "to": 5.5}
        # BELOW THE OWNER'S 0.5 pp FLOOR: refused by name
        low = await PLRN.create_proposal(
            conn, account_id=a["account_id"], proposed_at=t0,
            training=(t0 - 100, t0), evaluation=(t0, t0 + 100),
            agent_id="DEREK", strategy=CG, change_class=PLRN.C_EDGE,
            proposed_change={"parameter": "min_gross_edge_pp", "from": 0.5,
                             "to": 0.0}, rationale="test",
            proposed_by="DEREK")
        assert low["refusal"] == PLRN.R_OUT_OF_BOUNDS
        kw = dict(agent_id="DEREK", strategy=CG, change_class=PLRN.C_EDGE,
                  proposed_change=change, rationale="test",
                  proposed_by="DEREK")
        # ── OVERLAPPING / PEEKING PROTOCOLS ARE REFUSED BY NAME ──────────
        r = await PLRN.create_proposal(
            conn, account_id=a["account_id"], proposed_at=t0,
            training=(t0 - 100, t0), evaluation=(t0 - 50, t0 + 100), **kw)
        assert r["ok"] is False and r["refusal"] == PLRN.R_OVERLAP
        r = await PLRN.create_proposal(
            conn, account_id=a["account_id"], proposed_at=t0 + 10,
            training=(t0 - 100, t0), evaluation=(t0 + 5, t0 + 100), **kw)
        assert r["refusal"] == PLRN.R_PEEK
        # ...and by the database, for a writer that skips the code
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO paper_improvement_proposals (proposal_id, "
                " account_id, agent_id, change_class, proposed_change, "
                " rationale, proposed_by, proposed_at, training_start, "
                " training_end, evaluation_start, evaluation_end, protocol)"
                " VALUES ('paperprop:bad',$1,'DEREK',$2,'{}'::jsonb,'x',"
                " 'DEREK',to_timestamp($3),to_timestamp($3-100),"
                " to_timestamp($3),to_timestamp($3-50),to_timestamp($3+100),"
                " '{}'::jsonb)", a["account_id"], PLRN.C_EDGE, t0)
        # ...and an evaluation of an overlapping protocol is refused
        bad = {"change_class": PLRN.C_EDGE, "training_start": t0 - 100,
               "training_end": t0, "evaluation_start": t0 - 1,
               "evaluation_end": t0 + 100, "proposed_at": t0,
               "protocol": {}, "account_id": a["account_id"],
               "strategy": CG, "proposed_change": change}
        ev = await PLRN.run_evaluation(conn, bad, now=t0 + 200)
        assert ev["ok"] is False and ev["refusal"] == PLRN.R_OVERLAP
        assert ev["evaluated"] is False

        # ── FORWARD RECORDS: two in TRAINING, three in EVALUATION: entries
        # at 5.2 pp (admitted under 5.0, removed under 5.5) that LOST ──────
        week = 7 * 86400.0
        training, evaluation = (t0 - 1000, t0), (t0, t0 + week)
        for acc in (a, b):
            for k, at in enumerate((t0 - 500, t0 - 400, t0 + 100, t0 + 200,
                                    t0 + 300)):
                v = await PL.valuation(conn, decided_at=at, p_pin=0.552)
                # the venue's outcome, a minute after the valuation
                await conn.execute(
                    "UPDATE external_valuations SET outcome_known=TRUE, "
                    " outcome=0, outcome_basis='VENUE_SETTLEMENT_PRICE', "
                    " outcome_at=to_timestamp($2) WHERE id=$1",
                    v["valuation_id"], at + 60)
                await _decision_row(conn, acc, strategy=CG, at=at,
                                    slug=v["slug"], edge_pp=5.2, enter=True)
        # A: the protocol needs 10 forward outcomes
        ra = await PLRN.create_proposal(
            conn, account_id=a["account_id"], proposed_at=t0,
            training=training, evaluation=evaluation, **kw)
        assert ra["ok"] and ra["created"], ra
        pid = ra["proposal_id"]
        # BEFORE THE EVALUATION PERIOD ENDS: INSUFFICIENT, with the counts
        e1 = await PLRN.evaluate_proposal(conn, pid, now=t0 + 400)
        assert e1["status"] == PLRN.S_INSUFFICIENT
        assert e1["reason"] == PLRN.R_PERIOD_OPEN
        assert e1["counts"]["affected_settled"] == 3
        assert e1["training_records_used"] == 0
        # AFTER IT ENDS: still INSUFFICIENT -- 3 forward outcomes, 10 needed
        e2 = await PLRN.evaluate_proposal(conn, pid, now=t0 + week + 1)
        assert e2["status"] == PLRN.S_INSUFFICIENT
        assert e2["reason"] == PLRN.R_TOO_FEW
        assert e2["outcomes"] == 3 and e2["min_evaluation_outcomes"] == 10
        assert "3 forward outcomes" in e2["why"]
        row = await PLRN.proposal(conn, pid)
        assert row["status"] == PLRN.S_INSUFFICIENT
        assert row["verdict"] is None and row["active"] is False
        assert row["last_attempt"]["counts"]["affected_settled"] == 3
        # one open proposal per change: a second is refused by name
        dup = await PLRN.create_proposal(
            conn, account_id=a["account_id"], proposed_at=t0 + 1,
            training=(t0 - 1000, t0 + 1), evaluation=(t0 + 1, t0 + week),
            **kw)
        assert dup["ok"] is False and dup["refusal"] == PLRN.R_OPEN_EXISTS

        # B: the protocol needs 3: evaluated ONCE, on the 3 forward records
        rb = await PLRN.create_proposal(
            conn, account_id=b["account_id"], proposed_at=t0,
            training=training, evaluation=evaluation,
            min_evaluation_outcomes=3, **kw)
        pb = rb["proposal_id"]
        early = await PLRN.evaluate_proposal(conn, pb, now=t0 + 400)
        assert early["status"] == PLRN.S_INSUFFICIENT, \
            "never scored before the evaluation period ends"
        done = await PLRN.evaluate_proposal(conn, pb, now=t0 + week + 1)
        assert done["status"] == PLRN.S_EVALUATED, done
        assert done["outcomes"] == 3          # the training records: unused
        assert done["label"] == PLRN.COUNTERFACTUAL
        assert done["executable"] is False
        # every removed trade lost: removing them improves -> PASS
        assert done["verdict"] == PLRN.V_PASS
        assert done["counts"]["direction"] == "TIGHTEN"
        ev_dids = {r["decision_id"] for r in done["results"]}
        trn = {r["decision_id"] for r in await conn.fetch(
            "SELECT decision_id FROM paper_decisions WHERE account_id=$1 "
            "   AND decided_at < to_timestamp($2)", b["account_id"], t0)}
        assert len(trn) == 2 and not (ev_dids & trn)
        again = await PLRN.evaluate_proposal(conn, pb, now=t0 + week + 99)
        assert again["already_evaluated"] is True
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE paper_improvement_proposals SET "
                               " verdict='FAIL_HARM' WHERE proposal_id=$1",
                               pb)
        # ── ACTIVATION: NEVER AUTOMATIC, ONLY BEHIND THE CONTROL ─────────
        row = await PLRN.proposal(conn, pb)
        assert row["active"] is False and row["scope"] == "PAPER_ONLY"
        off = await PLRN.activate_proposal(conn, pb, approver="owner",
                                           now=t0 + week + 2)
        assert off["refusal"] == PLRN.R_ACTIVATION_OFF
        await conn.execute("UPDATE paper_control SET enabled=TRUE WHERE "
                           " control_key=$1", PLRN.ACTIVATION_CONTROL_KEY)
        try:
            assert (await PLRN.activate_proposal(
                conn, pb, approver="DEREK"))["refusal"] == \
                PLRN.R_AGENT_APPROVER
            assert (await PLRN.activate_proposal(
                conn, pid, approver="owner"))["refusal"] == PLRN.R_NOT_PASSED
            on = await PLRN.activate_proposal(conn, pb, approver="owner",
                                              now=t0 + week + 3)
            assert on["ok"] and on["active"] and on["scope"] == "PAPER_ONLY"
            assert on["params"] == {"min_gross_edge_pp": 5.5}
            assert on["previous_version_id"] == PB.CG_V1_VERSION_ID
            assert all(c["passed"] for c in on["checks"])
        finally:
            await conn.execute("UPDATE paper_control SET enabled=FALSE "
                               " WHERE control_key=$1",
                               PLRN.ACTIVATION_CONTROL_KEY)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("UPDATE paper_improvement_proposals SET "
                               " active=TRUE WHERE proposal_id=$1", pid)
        # THE MANAGEMENT READ SAYS SO, PER AGENT
        sa = (await PLRN.learning_summary(conn, account_id=a["account_id"])
              )["agents"]["DEREK"]["data"]
        assert sa["proposed_change"]["proposal_id"] == pid
        assert sa["evaluation"]["status"] == PLRN.S_INSUFFICIENT
        assert sa["evaluation"]["last_attempt"]["counts"][
            "affected_settled"] == 3
        assert sa["active"] is False
        sb = (await PLRN.learning_summary(conn, account_id=b["account_id"])
              )["agents"]["DEREK"]["data"]
        assert sb["evaluation"]["verdict"] == PLRN.V_PASS
        assert sb["evaluation"]["result"]["label"] == PLRN.COUNTERFACTUAL
        assert sb["active"] is True
        rb = await PLRN.rollback_policy_parameters(
            conn, actor="owner", reason="end of proof", now=t0 + week + 4)
        assert rb["ok"] and rb["active_version_id"] == PB.CG_V1_VERSION_ID
    finally:
        await _rollback_to_v1(conn)
        await _cleanup(conn, accts)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_near_misses_never_propose_below_the_owners_floor(
        both_on, monkeypatch):
    """FIVE NEAR MISSES (0.3 pp against the owner's 0.5 pp threshold, the
    PRODUCTION head V2) on the completed-game policy, through a real pass:
    they would argue for LOWERING the edge threshold, which the owner's
    0.5 pp floor forbids -- so Derek's lesson records the proposal as
    NOT_APPLICABLE with the reason, and no proposal is created (now or on
    later passes)."""
    conn = await H.connect()
    now = time.time() + 5.0
    acct = None
    try:
        await PL.purge_everything(conn)
        await _set_head(conn, PB.CG_V2_VERSION_ID, V2_ACT)
        monkeypatch.setattr(PLRN, "LEARNING_EVERY_S", 0.0)
        acct = await PL.new_account(conn, "lrauto", now=now)
        t = PL.Transport(now)
        for _ in range(5):
            v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.503,
                                   compatibility="INCOMPATIBLE")
            t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        client = PL.client(t)
        p = await _pass(conn, acct, t, now, client)
        assert not p["errors"], p["errors"]
        assert p["steps"]["benchmark_completed_game"]["refusals"].get(
            "BELOW_MIN_GROSS_EDGE") == 5
        assert p["steps"]["learning"]["proposals_created"] == 0, \
            p["steps"]["learning"]
        assert await PLRN.proposals(conn, account_id=acct["account_id"]) \
            == []
        les = {(x["kind"], x["strategy"]): x for x in await PLRN.lessons(
            conn, account_id=acct["account_id"])}[(PLRN.L_REFUSALS, CG)]
        m = les["metrics"]
        assert m["near_miss_edge_refusals"] == 5
        assert m["proposal"]["status"] == "NOT_APPLICABLE"
        assert "separate owner decision" in m["proposal"]["why"]
        p2 = await _pass(conn, acct, t, now + 5, client)
        assert not p2["errors"], p2["errors"]
        assert p2["steps"]["learning"]["proposals_created"] == 0
        assert client.mutation_attempts == 0
    finally:
        await _set_head(conn, PB.CG_V1_VERSION_ID, V1_ACT)
        await _cleanup(conn, [acct and acct["account_id"]])
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 6 · THE ROUTES MANAGEMENT CONSUMES
# ═════════════════════════════════════════════════════════════════════

class _Cfg:
    admin_token = "admin-secret-for-the-learning-routes-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


@pg
def test_the_learning_routes_need_the_command_credential_and_read_sections(
        monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from sportsassets.api import app as A
    from sportsassets.api import paper_learning_routes as R
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    pools = {}

    async def _pool():
        import asyncio
        loop = asyncio.get_running_loop()
        if loop not in pools:
            pools[loop] = await asyncpg.create_pool(H.DSN, min_size=1,
                                                    max_size=2)
        return pools[loop]
    monkeypatch.setattr(R, "_conn_pool", _pool)
    app = FastAPI()
    app.include_router(R.router)
    c = TestClient(app, raise_server_exceptions=False)
    paths = ("/api/command/paper/learning",
             "/api/command/paper/learning/chains",
             "/api/command/paper/learning/lessons",
             "/api/command/paper/learning/proposals",
             "/api/command/paper/learning/events",
             "/api/command/paper/learning/chain/paperfill:none",
             "/api/command/paper/learning/decision/paperdec:none")
    for path in paths:
        assert c.get(path).status_code == 401, path
    hdr = {"X-Admin-Token": _Cfg.admin_token}
    for path in paths:
        r = c.get(path, headers=hdr)
        assert r.status_code == 200, (path, r.text)
        body = r.json()
        assert body["data_label"] == L.DATA_LABEL
        secs = ([body["result"]] if "result" in body else
                list(body["agents"].values())
                + [body["event_audits"], body["chains"]])
        for s in secs:
            assert s["status"] in ("OK", "EMPTY", "UNAVAILABLE"), (path, s)
            if s["status"] != "OK":
                assert s["why"], (path, s)
    assert c.get("/api/command/paper/learning?account_id=acct-real",
                 headers=hdr).status_code == 400
    r = c.get("/api/command/paper/learning/policy", headers=hdr)
    assert r.json()["result"]["status"] == "OK"
    st = r.json()["result"]["data"]
    # this module's V1 baseline (production runs V2: see the V2 proofs)
    assert st["active"]["version_id"] == PB.CG_V1_VERSION_ID
    assert st["bounds"] == {"min_gross_edge_pp": [0.5, 6.0]}
    # THE TWO WRITES: POST only, the command CONTROL credential, audited;
    # refused by name when their conditions are not met
    writes = [r for r in R.router.routes
              if set(getattr(r, "methods", ())) - {"GET", "HEAD"}]
    assert sorted(r.path for r in writes) == [
        "/api/command/paper/learning/policy/rollback",
        "/api/command/paper/learning/proposals/{proposal_id}/activate"]
    act = "/api/command/paper/learning/proposals/paperprop:none/activate"
    rbk = "/api/command/paper/learning/policy/rollback"
    assert c.post(act, json={"approver": "owner"}).status_code in (401, 403)
    assert c.post(rbk, json={"actor": "owner", "reason": "x"}
                  ).status_code in (401, 403)
    r = c.post(act, json={"approver": "owner"}, headers=hdr)
    assert r.status_code == 409
    assert r.json()["detail"]["refusal"] == PLRN.R_ACTIVATION_OFF
    r = c.post(rbk, json={"actor": "owner", "reason": "x"}, headers=hdr)
    assert r.status_code == 409
    assert r.json()["detail"]["refusal"] == PLRN.R_NOTHING_TO_ROLL_BACK


# ═════════════════════════════════════════════════════════════════════
# 7 · AN ACTIVATED CANDIDATE CHANGES THE RUNNING AGENT'S DECISION
# ═════════════════════════════════════════════════════════════════════

def test_the_bounds_are_one_rule_in_code_twice_and_in_the_database():
    """THE OWNER'S 0.5 pp FLOOR (2026-10-01), everywhere: both code copies
    and migration 188's CHECK; nothing below 0.5 validates or can be
    proposed. V1 (5.0 pp) stays a valid, restorable version."""
    import pathlib
    assert PB.CG_PARAMETER_BOUNDS == PLRN.PARAM_BOUNDS == {
        "min_gross_edge_pp": (0.5, 6.0)}
    assert PB.CG_PARAMETERS_V2 == PLRN.PARAM_DEFAULTS == {
        "min_gross_edge_pp": 0.5}
    assert PB.CG_PARAMETERS_V1 == {"min_gross_edge_pp": 5.0}
    assert PB.CG_PARAMETER_GRID_PP == PLRN.PARAM_GRID_PP == 0.5
    sql = (pathlib.Path(PB.__file__).resolve().parents[2] / "migrations"
           / "188_completed_game_owner_threshold_0_5pp.sql").read_text()
    assert "BETWEEN 0.5 AND 6.0" in sql
    assert "BETWEEN 0.0" not in sql and "BETWEEN 0.4" not in sql
    assert "'{\"min_gross_edge_pp\": 0.5}'::jsonb" in sql
    for bad in ({"min_gross_edge_pp": 0.0}, {"min_gross_edge_pp": 0.25},
                {"min_gross_edge_pp": 0.49}, {"min_gross_edge_pp": -0.5},
                {"min_gross_edge_pp": 6.5}, {"min_gross_edge_pp": 5.25},
                {"min_gross_edge_pp": "5"},
                {"min_gross_edge_pp": 5.0, "max_qty": 1}, {}):
        assert PB.validate_cg_parameters(bad) is not None, bad
        assert PLRN.validate_parameters(bad) is not None, bad
    for ok in (0.5, 1.0, 2.0, 5.0, 5.5, 6.0):
        assert PB.validate_cg_parameters({"min_gross_edge_pp": ok}) is None
    ch = {"parameter": "min_gross_edge_pp", "from": 5.0, "to": 5.5}
    assert PLRN.check_change(PLRN.C_EDGE, CG, ch) is None
    # AUTOMATIC LEARNING NEVER TAKES THE THRESHOLD BELOW 0.5 pp
    lo = {"parameter": "min_gross_edge_pp", "from": 0.5, "to": 0.0}
    assert PLRN.check_change(PLRN.C_EDGE, CG, lo) == PLRN.R_OUT_OF_BOUNDS
    assert PLRN.check_change(PLRN.C_EDGE, CG, dict(lo, from_=1.0,
                                                   to=-0.5)) == \
        PLRN.R_OUT_OF_BOUNDS
    assert PLRN.check_change(PLRN.C_EDGE, CG, {
        "parameter": "min_gross_edge_pp", "from": 1.0, "to": 0.5}) is None
    assert PLRN.check_change(PLRN.C_EDGE, CG, dict(ch, to=6.5)) == \
        PLRN.R_OUT_OF_BOUNDS
    assert PLRN.check_change(PLRN.C_EDGE, CG, dict(ch, to=5.0)) == \
        PLRN.R_STEP_TOO_LARGE                   # an empty change
    assert PLRN.check_change(PLRN.C_EDGE, CG, {
        "parameter": "min_gross_edge_pp", "from": 6.0, "to": 5.0}) is None
    assert PLRN.check_change(PLRN.C_EDGE, PB.STRATEGY, ch) == \
        PLRN.R_CHANGE_NOT_SUPPORTED
    assert PLRN.check_change(PLRN.C_EDGE, CG, dict(
        ch, parameter="per_order_cap_usd")) == PLRN.R_NOT_WHITELISTED


async def test_a_failed_parameter_read_falls_back_to_the_shipped_default():
    class Broken:
        async def fetchval(self, *a):
            raise RuntimeError("down")
    ctx = {"now": 1.0}
    got = await PB.cg_parameters(Broken(), ctx)
    assert got["source"] == PB.P_FALLBACK
    assert got["values"] == {"min_gross_edge_pp": 0.5}
    assert got["version_id"] == PB.CG_V2_VERSION_ID
    assert got["fallback_reason"].startswith("PARAMETER_READ_FAILED")
    assert await PB.cg_parameters(Broken(), ctx) is got   # cached per pass


def test_no_funded_module_reads_or_imports_the_policy_parameters():
    """IMPORT ISOLATION: only the paper completed-game decision path, the
    paper learning module and its routes name the parameter tables; no
    module outside the paper modules imports either reader."""
    import ast
    import pathlib
    root = pathlib.Path(PB.__file__).resolve().parents[1]
    readers = set()
    importers = set()
    for p in root.rglob("*.py"):
        rel = str(p.relative_to(root))
        src = p.read_text()
        if "paper_policy_parameter" in src:
            readers.add(rel)
        for node in ast.walk(ast.parse(src)):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(n and n.split(".")[-1] in ("paper_learning",
                                              "paper_learning_routes")
                   for n in names):
                importers.add(rel)
    assert readers == {"agents/paper_benchmark.py",
                       "agents/paper_learning.py"}, readers
    assert not any("funded" in r or "live" in r for r in readers)
    # bettor_paper_ops is the management pages' PAPER read model: Audrey's
    # page reads the learning summary and event audits through it (read-only)
    assert importers <= {"agents/paper_runtime.py", "agents/paper_derek.py",
                         "api/app.py", "api/paper_learning_routes.py",
                         "bettor_paper_ops.py"}, \
        importers
    assert not any("funded" in i for i in importers)


async def _passing_proposal(conn, acct, t0):
    """A TIGHTENING proposal (5.0 -> 5.5 pp) whose protocol lies entirely in
    the past, with its evaluation FORWARD RECORDS CONSTRUCTED IN THE TEST
    DATABASE (3 entries at 5.2 pp in the evaluation period that settled
    LOST; production never fabricates them), evaluated by the real
    evaluator to a PASS (removing losing trades improves the result)."""
    tr, ev = (t0 - 3000, t0 - 2000), (t0 - 2000, t0 - 1000)
    for at in (t0 - 1800, t0 - 1600, t0 - 1400):
        v = await PL.valuation(conn, decided_at=at, p_pin=0.552)
        await conn.execute(
            "UPDATE external_valuations SET outcome_known=TRUE, outcome=0, "
            " outcome_basis='VENUE_SETTLEMENT_PRICE', "
            " outcome_at=to_timestamp($2) WHERE id=$1", v["valuation_id"],
            at + 60)
        await _decision_row(conn, acct, strategy=CG, at=at, slug=v["slug"],
                            edge_pp=5.2, enter=True)
    r = await PLRN.create_proposal(
        conn, account_id=acct["account_id"], agent_id="DEREK", strategy=CG,
        change_class=PLRN.C_EDGE, proposed_change={
            "parameter": "min_gross_edge_pp", "from": 5.0, "to": 5.5},
        rationale="test", proposed_by="DEREK", proposed_at=tr[1],
        training=tr, evaluation=ev, min_evaluation_outcomes=3)
    assert r["ok"] and r["created"], r
    e = await PLRN.evaluate_proposal(conn, r["proposal_id"], now=t0 - 10)
    assert e["status"] == PLRN.S_EVALUATED and e["verdict"] == PLRN.V_PASS
    return r["proposal_id"], e["evaluation_id"]


async def _controls(conn) -> dict:
    return {r["control_key"]: (r["enabled"], r["why"], r["updated_by"],
                               r["updated_at"])
            for r in await conn.fetch("SELECT * FROM paper_control")}


@pg
async def test_an_activated_candidate_changes_a_future_decision_and_rollback_restores_it(  # noqa: E501
        both_on):
    """5.2 pp of edge: ENTER under V1 (5.0 pp); after the explicit, approved
    activation of 5.5 pp, a NEW valuation with the same edge REFUSES, with
    the version and its provenance on the decision; the rollback (made with
    the activation control OFF) restores V1 and a further new valuation
    ENTERS again. Historical rows unchanged; paper_control untouched."""
    conn = await H.connect()
    now = time.time() + 5.0
    acct = None
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "lrpolicy", now=now)
        pid, eid = await _passing_proposal(conn, acct, now)
        t = PL.Transport(now)
        client = PL.client(t)

        async def decide(at):
            v = await PL.valuation(conn, decided_at=at - 10, p_pin=0.552,
                                   compatibility="INCOMPATIBLE")
            t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
            p = await _pass(conn, acct, t, at, client)
            assert not p["errors"], p["errors"]
            return await conn.fetchrow(
                "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
                " valuation_id=$2 AND strategy=$3", acct["session_id"],
                v["valuation_id"], CG)

        # ── V1: 5.2 pp clears the shipped 5.0 pp ───────────────────────
        d1 = await decide(now)
        assert d1["verdict"] == "ENTER", (d1["refusal"], d1["refusals"])
        pd1 = H.j(d1["policy_decision"])
        assert pd1["parameters"]["version_id"] == PB.CG_V1_VERSION_ID
        assert pd1["parameters"]["source"] == PB.P_ACTIVE
        assert pd1["parameters"]["values"] == {"min_gross_edge_pp": 5.0}
        assert pd1["gross_edge_pp"] == pytest.approx(5.2)
        frozen = dict(d1)

        # ── ACTIVATION: refused while the control is off; then explicit ──
        off = await PLRN.activate_proposal(conn, pid, approver="owner")
        assert off["refusal"] == PLRN.R_ACTIVATION_OFF
        await conn.execute("UPDATE paper_control SET enabled=TRUE WHERE "
                           " control_key=$1", PLRN.ACTIVATION_CONTROL_KEY)
        try:
            assert (await PLRN.activate_proposal(
                conn, pid, approver="XAVIER"))["refusal"] == \
                PLRN.R_AGENT_APPROVER
            on = await PLRN.activate_proposal(conn, pid, approver="owner",
                                              now=now + 1)
        finally:
            await conn.execute("UPDATE paper_control SET enabled=FALSE "
                               " WHERE control_key=$1",
                               PLRN.ACTIVATION_CONTROL_KEY)
        assert on["ok"], on
        v2 = on["version_id"]
        assert on["params"] == {"min_gross_edge_pp": 5.5}
        assert on["evaluation_id"] == eid
        ver = await conn.fetchrow("SELECT * FROM "
                                  " paper_policy_parameter_versions WHERE "
                                  " version_id=$1", v2)
        assert (ver["proposal_id"], ver["evaluation_id"],
                ver["approved_by"]) == (pid, eid, "owner")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE paper_policy_parameter_versions SET "
                               " params='{\"min_gross_edge_pp\": 6.0}' "
                               " WHERE version_id=$1", v2)
        # THE DATABASE REFUSES ANY VERSION BELOW THE 0.5 pp FLOOR
        for below in ("0.0", "-0.5"):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO paper_policy_parameter_versions ("
                        " version_id, policy_key, version_no, params, "
                        " params_sha256, source, proposal_id, evaluation_id,"
                        " approved_by, created_at) VALUES ('paperparam:x', "
                        " $1, 999, $3::jsonb, 'x', 'EVALUATED_PROPOSAL', $2,"
                        " 'e', 'owner', now())", CG, pid,
                        '{"min_gross_edge_pp": %s}' % below)

        # ── V2: the SAME edge on a NEW valuation now REFUSES ────────────
        d2 = await decide(now + 30)
        assert d2["verdict"] == "REFUSE"
        assert d2["refusal"] == "BELOW_MIN_GROSS_EDGE"
        pd2 = H.j(d2["policy_decision"])
        par = pd2["parameters"]
        assert par["version_id"] == v2 and par["source"] == PB.P_ACTIVE
        assert par["values"] == {"min_gross_edge_pp": 5.5}
        assert (par["proposal_id"], par["evaluation_id"],
                par["approved_by"]) == (pid, eid, "owner")
        assert pd2["shortfall"]["edge_threshold_pp"] == 5.5
        assert H.j(d2["economics"])["parameters"]["version_id"] == v2
        assert H.j(d2["economics"])["threshold_edge_pp"] == 5.5
        assert H.j(d2["provenance"])["versions"]["parameters"][
            "version_id"] == v2
        cond = {c["condition"]: c for c in pd2["conditions"]}
        assert cond["edge_at_least_min_gross_edge_pp_at_every_level_used"][
            "threshold"] == 5.5
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE decision_id=$1",
            d2["decision_id"]) == 0
        # THE EARLIER DECISION IS UNTOUCHED
        again = await conn.fetchrow("SELECT * FROM paper_decisions WHERE "
                                    " decision_id=$1", d1["decision_id"])
        assert dict(again) == frozen

        # ── MANAGEMENT SEES THE ACTIVE VERSION, CANDIDATES, HISTORY ──────
        st = await PLRN.policy_parameter_state(conn)
        assert st["active"]["version_id"] == v2
        assert st["active"]["proposal_id"] == pid
        assert {v["version_id"]: v["state"] for v in st["versions"]}[v2] \
            == "ACTIVE"
        assert st["activation_history"][0]["kind"] == "ACTIVATE"
        assert st["activation_history"][0]["actor"] == "owner"
        cand = next(c for c in st["candidates"] if c["proposal_id"] == pid)
        assert cand["active"] is True and cand["evaluation_id"] == eid

        # ── ROLLBACK, ACTIVATION CONTROL OFF: atomic, audited, restores V1,
        # touches no paper_control row; a NEW valuation ENTERS again ──────
        before = await _controls(conn)
        assert before[PLRN.ACTIVATION_CONTROL_KEY][0] is False
        assert (await PLRN.rollback_policy_parameters(
            conn, actor="owner", reason=""))["refusal"] == \
            PLRN.R_ROLLBACK_NEEDS_REASON
        assert (await PLRN.rollback_policy_parameters(
            conn, actor="", reason="x"))["refusal"] == PLRN.R_NO_APPROVER
        rb = await PLRN.rollback_policy_parameters(
            conn, actor="owner", reason="proof: restore V1", now=now + 40)
        assert rb["ok"] and rb["active_version_id"] == PB.CG_V1_VERSION_ID
        assert rb["rolled_back_version_id"] == v2
        assert await _controls(conn) == before
        assert (await PLRN.proposal(conn, pid))["active"] is False
        d3 = await decide(now + 60)
        assert d3["verdict"] == "ENTER", (d3["refusal"], d3["refusals"])
        assert H.j(d3["policy_decision"])["parameters"]["version_id"] == \
            PB.CG_V1_VERSION_ID
        hist = (await PLRN.policy_parameter_state(conn))[
            "activation_history"]
        assert [h["kind"] for h in hist[:2]] == ["ROLLBACK", "ACTIVATE"]
        assert hist[0]["previous_version_id"] == v2
        assert hist[0]["actor"] == "owner"
        assert hist[0]["reason"] == "proof: restore V1"
        assert (await PLRN.rollback_policy_parameters(
            conn, actor="owner", reason="again"))["refusal"] == \
            PLRN.R_NOTHING_TO_ROLL_BACK
        assert client.mutation_attempts == 0
    finally:
        await _rollback_to_v1(conn)
        await _cleanup(conn, [acct and acct["account_id"]])
        await PL.purge_everything(conn)
        await conn.close()


@pg
def test_rollback_never_enables_a_disabled_strategy_and_works_with_activation_off(  # noqa: E501
        monkeypatch):
    """THROUGH THE AUTHENTICATED ROUTE: with the completed-game policy's own
    control row OFF and the activation control OFF, a rollback still
    restores the previous approved version, writes its audit row, and turns
    neither row on (no paper_control row changes at all). Without the
    command control credential it is refused."""
    import asyncio

    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from sportsassets.api import app as A
    from sportsassets.api import paper_learning_routes as R

    state: dict = {}

    async def setup():
        conn = await H.connect()
        try:
            await PL.purge_everything(conn)
            acct = await H.new_account(conn, "lrrbk", now=time.time())
            state["acct"] = acct
            pid, _ = await _passing_proposal(conn, acct, time.time())
            await conn.execute("UPDATE paper_control SET enabled=TRUE WHERE "
                               " control_key=$1", PLRN.ACTIVATION_CONTROL_KEY)
            try:
                on = await PLRN.activate_proposal(conn, pid, approver="owner")
            finally:
                await conn.execute(
                    "UPDATE paper_control SET enabled=FALSE WHERE "
                    " control_key=$1", PLRN.ACTIVATION_CONTROL_KEY)
            assert on["ok"], on
            state["v2"] = on["version_id"]
            state["cg_prior"] = await conn.fetchval(
                "SELECT enabled FROM paper_control WHERE control_key=$1",
                PB.CG_POLICY["control_key"])
            await conn.execute("UPDATE paper_control SET enabled=FALSE WHERE "
                               " control_key=$1", PB.CG_POLICY["control_key"])
            state["before"] = await _controls(conn)
        finally:
            await conn.close()

    async def check():
        conn = await H.connect()
        try:
            state["after"] = await _controls(conn)
            state["head"] = await conn.fetchval(
                "SELECT active_version_id FROM paper_policy_parameter_heads "
                " WHERE policy_key=$1", CG)
            state["audit"] = await conn.fetchrow(
                "SELECT * FROM paper_policy_parameter_activations WHERE "
                " policy_key=$1 ORDER BY at DESC, recorded_at DESC LIMIT 1",
                CG)
        finally:
            await conn.close()

    async def teardown():
        conn = await H.connect()
        try:
            await _rollback_to_v1(conn)
            if "cg_prior" in state:
                await conn.execute(
                    "UPDATE paper_control SET enabled=$2 WHERE "
                    " control_key=$1", PB.CG_POLICY["control_key"],
                    state["cg_prior"])
            await _cleanup(conn, [state.get("acct", {}).get("account_id")])
            await PL.purge_everything(conn)
        finally:
            await conn.close()

    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    pools = {}

    async def _pool():
        loop = asyncio.get_running_loop()
        if loop not in pools:
            pools[loop] = await asyncpg.create_pool(H.DSN, min_size=1,
                                                    max_size=2)
        return pools[loop]
    monkeypatch.setattr(R, "_conn_pool", _pool)
    app = FastAPI()
    app.include_router(R.router)
    c = TestClient(app, raise_server_exceptions=False)
    rbk = "/api/command/paper/learning/policy/rollback"
    try:
        asyncio.run(setup())
        assert state["before"][PB.CG_POLICY["control_key"]][0] is False
        assert state["before"][PLRN.ACTIVATION_CONTROL_KEY][0] is False
        # NO CREDENTIAL: refused, nothing changes
        assert c.post(rbk, json={"operator": "owner", "reason": "x"}
                      ).status_code in (401, 403)
        r = c.post(rbk, json={"operator": "owner",
                              "reason": "restore the approved V1"},
                   headers={"X-Admin-Token": _Cfg.admin_token})
        assert r.status_code == 200, r.text
        got = r.json()["result"]
        assert got["rolled_back_version_id"] == state["v2"]
        assert got["active_version_id"] == PB.CG_V1_VERSION_ID
        asyncio.run(check())
        assert state["head"] == PB.CG_V1_VERSION_ID
        a = state["audit"]
        assert (a["kind"], a["actor"], a["reason"]) == (
            "ROLLBACK", "owner", "restore the approved V1")
        assert a["previous_version_id"] == state["v2"]
        # THE STRATEGY STAYS DISABLED; NO paper_control ROW CHANGED
        assert state["after"] == state["before"]
        assert state["after"][PB.CG_POLICY["control_key"]][0] is False
    finally:
        asyncio.run(teardown())


@pg
async def test_without_enough_forward_outcomes_management_reads_insufficient(
        both_on):
    """A candidate with too few forward outcomes is INSUFFICIENT_FORWARD_
    DATA in the management read, cannot be activated even with the control
    on, and the running version stays V1."""
    conn = await H.connect()
    t0 = time.time()
    acct = None
    try:
        acct = await H.new_account(conn, "lrinsuf", now=t0)
        r = await PLRN.create_proposal(
            conn, account_id=acct["account_id"], agent_id="DEREK",
            strategy=CG, change_class=PLRN.C_EDGE, proposed_change={
                "parameter": "min_gross_edge_pp", "from": 5.0, "to": 5.5},
            rationale="test", proposed_by="DEREK", proposed_at=t0 - 2000,
            training=(t0 - 3000, t0 - 2000), evaluation=(t0 - 2000,
                                                         t0 - 1000))
        e = await PLRN.evaluate_proposal(conn, r["proposal_id"], now=t0)
        assert e["status"] == PLRN.S_INSUFFICIENT
        assert e["reason"] == PLRN.R_TOO_FEW
        await conn.execute("UPDATE paper_control SET enabled=TRUE WHERE "
                           " control_key=$1", PLRN.ACTIVATION_CONTROL_KEY)
        try:
            got = await PLRN.activate_proposal(conn, r["proposal_id"],
                                               approver="owner")
        finally:
            await conn.execute("UPDATE paper_control SET enabled=FALSE "
                               " WHERE control_key=$1",
                               PLRN.ACTIVATION_CONTROL_KEY)
        assert got["refusal"] == PLRN.R_NOT_PASSED
        s = await PLRN.learning_summary(conn, account_id=acct["account_id"])
        pp = s["policy_parameters"]["data"]
        assert pp["active"]["version_id"] == PB.CG_V1_VERSION_ID
        c = next(x for x in pp["candidates"]
                 if x["proposal_id"] == r["proposal_id"])
        assert c["status"] == "INSUFFICIENT_FORWARD_DATA"
        assert c["evaluation_reason"] == PLRN.R_TOO_FEW
        assert c["evaluation_counts"]["affected_settled"] == 0
        assert c["activatable_now"] is False
        assert "EVALUATED_ONCE_WITH_A_PASS" in c["failed_checks"]
        d = s["agents"]["DEREK"]["data"]
        assert d["evaluation"]["status"] == "INSUFFICIENT_FORWARD_DATA"
        assert d["active"] is False
    finally:
        await _cleanup(conn, [acct and acct["account_id"]])
        await conn.close()
