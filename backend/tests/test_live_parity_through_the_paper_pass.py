"""R30 LIVE PARITY THROUGH THE REAL PAPER PASS (Postgres).

The completed-game investment policy ENTERS on a real pass with the canonical
hooks installed (as `execution_intent.start` installs them in the API):

  * ONE canonical decision intent is recorded, sha-verified, carrying every
    agent component (measured or with its reason);
  * the PAPER order is the intent's order (side, quantity, limit, wire,
    order form) -- read from the intent, not from local variables;
  * the SMALL LIVE adapter records a SHADOW proposal built from the SAME
    intent, at capital scale; nothing reaches the venue;
  * the parity ledger classifies the pair and it is not a divergence;
  * after the fill and handoff, Xavier's review records ONE canonical
    management intent consumed by both adapters, with its parity row.

SYNTHETIC: valuations and books from tests/paper_live_fixture.
"""
from __future__ import annotations

import json
import time

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import decision_hooks as DH
from sportsassets import live_parity as LP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)
CG = PB.CG_STRATEGY


async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, client):
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, fee_fn=FEE, sleep=_nosleep)


@pytest.fixture
def cg_on_with_parity(monkeypatch, new_strategies_off):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    LP.install()
    yield
    LP.uninstall()
    PB._CONTEXT_CACHE.clear()


def test_the_paper_adapter_field_map_is_the_canonical_one():
    it = LP.build_decision_intent(
        decision_id="d", strategy=CG, strategy_version=PB.CG_VERSION,
        evidence={}, opportunity_score=None, derek={"verdict": "ENTER"},
        karen=None, allie=None, eddie=None, us_market_slug="s", contract={},
        holding_side="LONG", order_intent="ORDER_INTENT_BUY_LONG",
        order_type="MARKETABLE", time_in_force="IOC", limit_price=0.5,
        wire_price=0.5, target_qty=10, sizing_basis={}, created_at=1.0)
    assert {k: it[f] for k, f in PB.CANONICAL_ORDER_FIELDS.items()} == \
        LP.paper_entry_fields(it)
    from sportsassets import canonical_intent as CI
    from sportsassets import execmirror as M
    assert {M.EXIT_FOR["ORDER_INTENT_BUY_LONG"],
            M.EXIT_FOR["ORDER_INTENT_BUY_SHORT"]} == set(CI.SELL_INTENT.values())
    assert CI.SELL_INTENT["LONG"] == M.EXIT_FOR["ORDER_INTENT_BUY_LONG"]


def test_the_hooks_are_installed_by_the_executing_process_only():
    LP.uninstall()
    assert DH.CANONICAL_DECISION is None
    LP.install()
    try:
        assert DH.CANONICAL_DECISION is LP.canonical_decision
        assert DH.CANONICAL_ENTRY_ADAPTERS is LP.entry_adapters
        assert DH.CANONICAL_MANAGEMENT_RECORD is LP.record_management_intent
        assert DH.CANONICAL_MANAGEMENT_ADAPTERS is LP.management_adapters
    finally:
        LP.uninstall()


@pg
async def test_one_intent_two_adapters_parity_through_the_real_pass(
        cg_on_with_parity):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        ctl = await LP.control(conn)
        if ctl.get("halted"):
            await LP.clear_halt(conn, actor="test harness (human operator)",
                                reason="isolate this test")
        if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
            await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
        await conn.execute(
            "INSERT INTO execmirror_snapshots (at, balances, positions, "
            " open_orders) VALUES (now(), $1::jsonb, '[]', 0)",
            json.dumps([{"currency": "USD", "buyingPower": 500}]))
        acct = await PL.new_account(conn, "parity", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        d = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"],
            v["valuation_id"], CG)
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])

        # ── ONE CANONICAL DECISION INTENT ───────────────────────────────
        it = await conn.fetchrow(
            "SELECT * FROM canonical_decision_intents WHERE decision_id=$1",
            d["decision_id"])
        assert it is not None, "the investment ENTER recorded no intent"
        assert LP.verify_intent(dict(it)), "the stored sha must verify"
        assert it["strategy"] == CG and it["strategy_version"] == PB.CG_VERSION
        assert it["sleeve"] == "INVESTMENT"
        for comp in ("opportunity_score", "derek", "karen", "allie", "eddie"):
            c = H.j(it[comp])
            assert c, comp
            assert (c.get("status") in ("MEASURED", "UNAVAILABLE")
                    or c.get("state")), (comp, c)
            if c.get("status") == "UNAVAILABLE" or c.get("state") == "UNAVAILABLE":
                assert c.get("why"), (comp, c)        # a reason, never a zero
        assert H.j(it["derek"])["verdict"] == "ENTER"
        ev = H.j(it["evidence"])
        assert ev["valuation_id"] == v["valuation_id"]
        assert ev["book_obs_id"] == d["book_obs_id"]

        # ── THE PAPER ADAPTER: THE ORDER IS THE INTENT'S ORDER ──────────
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1 AND role='ENTRY'",
                                d["decision_id"])
        assert float(o["qty"]) == float(it["target_qty"])
        assert float(o["limit_price"]) == float(it["limit_price"])
        assert float(o["wire_price"]) == float(it["wire_price"])
        assert o["holding_side"] == it["holding_side"]
        assert o["intent"] == it["order_intent"]
        assert o["time_in_force"] == it["time_in_force"]

        # ── BOTH ADAPTERS, ONE INTENT SHA, AND THE PARITY ROW ───────────
        ex = {r["adapter"]: r for r in await conn.fetch(
            "SELECT * FROM canonical_intent_executions WHERE intent_id=$1",
            it["intent_id"])}
        assert set(ex) == {"PAPER", "SMALL_LIVE"}
        assert ex["PAPER"]["mode"] == "SIMULATED"
        assert ex["PAPER"]["state"] == "PAPER_SUBMITTED"
        assert ex["SMALL_LIVE"]["mode"] == "SHADOW"
        assert ex["SMALL_LIVE"]["state"] in ("SHADOW_PROPOSED",
                                             "SHADOW_EXCLUDED")
        assert {r["intent_sha"] for r in ex.values()} == {it["content_sha"]}
        assert "venue_order_id" not in H.j(ex["SMALL_LIVE"]["refs"])
        par = await conn.fetchrow(
            "SELECT * FROM live_parity_ledger WHERE intent_id=$1",
            it["intent_id"])
        assert par is not None
        assert par["parity_state"] != "LOGIC_DIVERGENCE", H.j(par["comparison"])
        assert client.mutation_attempts == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0

        # ── FILL, HANDOFF, AND XAVIER'S CANONICAL MANAGEMENT INTENT ─────
        for k in (5, 70, 140):
            p = await _pass(conn, acct, t, now + k, client)
            assert not p["errors"], p["errors"]
        mi = await conn.fetch(
            "SELECT * FROM canonical_management_intents WHERE group_id=$1 "
            " ORDER BY created_at", o["group_id"])
        assert mi, "Xavier's review recorded no canonical management intent"
        last = mi[-1]
        assert last["sleeve"] == "INVESTMENT"
        assert last["action"] in ("SELL_EXIT", "SELL_REDUCE",
                                  "CANCEL_PROTECTION_BEFORE_EXIT",
                                  "MAINTAIN_STANDING_PROTECTION", "NO_ORDER")
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " review_id=$1", last["review_id"])
        assert rv is not None, "the intent names the review that made it"
        assert H.j(rv["action"]).get("canonical_intent_id") == last["intent_id"]
        mex = {r["adapter"]: r for r in await conn.fetch(
            "SELECT * FROM canonical_intent_executions WHERE intent_id=$1",
            last["intent_id"])}
        assert set(mex) == {"PAPER", "SMALL_LIVE"}
        mpar = await conn.fetchrow(
            "SELECT * FROM live_parity_ledger WHERE intent_id=$1",
            last["intent_id"])
        assert mpar is not None
        assert mpar["parity_state"] != "LOGIC_DIVERGENCE", H.j(
            mpar["comparison"])
        assert not (await LP.control(conn))["halted"]
        assert client.mutation_attempts == 0
        b = await L.balances(conn, acct["account_id"], now=now + 141)
        assert b["ledger_consistent"] is True
    finally:
        await PL.purge_everything(conn)
        await conn.close()
