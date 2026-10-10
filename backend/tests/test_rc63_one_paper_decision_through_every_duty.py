"""ONE PAPER DECISION THROUGH EVERY DUTY, BY decision_id (RC6.3 lifecycle-proof).

    REHEARSAL -- SYNTHETIC -- NOT PRODUCTION ACTIVITY -- NO VENUE ORDER

A single PINNACLE_COMPLETED_GAME_PAPER decision is driven through the real
paper pass (`paper_runtime.paper_pass`) on a migrated Postgres, with the
release-tree modules and the canonical hooks installed as the executing
process installs them (`live_parity.install`), on a scratch `paper_test_*`
account. Every duty is asserted on the records it writes, and every record is
tied back to the one decision_id:

   1 DEREK      the ENTER decision: the model price (the de-vigged Pinnacle
                probability), the depth walk it priced, its fees, the all-in
                executable price per contract and the expected net after fees
   2 ARCHER     the execution estimate at the decision (the intent's `eddie`
                component, MEASURED, its estimate id)
   3 ALLIE      the capital-efficiency allocation at the decision (MEASURED:
                event start, settlement-lag sample, capital required, hours
                to release, binding constraint, final allocatable amount)
   4 RISK       the ledger's verdict under the account lock: the LEDGER-stage
                evaluation (ENTER) on the order's key, the capital-authority
                summary on the SUBMITTED event (no stopping rule firing), the
                rails on the intent
   5 LEDGER     the reservation (ORDER_SUBMITTED) of limit x qty + max fees
   6 ORDER      the paper ENTRY order -- the intent's order (qty, limit, wire)
   7 FILL       the simulated DEPTH WALK across two displayed levels after the
                decision-to-execution delay, each fill debited at qty x price +
                fee and released from the reservation
   8 XAVIER     the first-fill handoff, his review, ONE standing protective
                order, the canonical management intents (SMALL LIVE SHADOW)
   9 SETTLEMENT authoritative venue evidence -> settled exactly once, the
                protection released
  10 TERMINAL   the group's disposition: no open quantity, no live order,
                nothing reserved, realized P&L = payout - cost - fees
  11 AUDREY     her daily report reconciles to the one ledger and audits the
                first fill, the handoff and the settlement; at the DAY TURN the
                closing version is asserted `final` only when the code under
                test supports it (34cb9544 does not: rc6/audrey-final 6d8cd731
                fixes it) -- otherwise the known defect is asserted and recorded
  12 KAREN      her detectors re-applied to every record of this lifecycle
                (karen_runner.rule_holds) hold for none, and her real pass opens
                no challenge on them (the pass runs inside a rolled-back
                transaction: Karen's records are append-only in a shared DB)
  13 COMMAND    GET /api/command/paper/learning/chain/{fill_id} and
     CENTER     /learning/decision/{decision_id} through the real FastAPI app
                show the same ids and states, every link PRESENT

THE STRATEGY IS ACTIVE IN THIS TEST DATABASE ONLY: the scratch account has no
lifecycle event, so its state is the initial ACTIVE_CHALLENGER; no production
quarantine is read or touched (the test asserts no row of the main paper
account changed). SMALL LIVE stays SHADOW (the parity adapter records SHADOW
proposals only; small_live_order_events stays empty); no venue is called
(the book transport is substituted and records zero mutation attempts).

WHAT IS SEEDED, BY THE SUITE (tests/conftest.py), AND THE EVIDENCE SAYS SO:
the learned-state gates a scratch account cannot pass without a forward
history -- the capital authority's forward-economics verdict (seeded
POSITIVE), the profitability bind (quantity passed through, regime SEEDED --
visible on the recorded evaluations), its economic control inputs and
record check, and the deterioration quarantine. The stopping rules are
evaluated for real at the entry. WHAT THIS TEST SUPPLIES: a synthetic
valuation and books (tests/paper_live_fixture), the decision market's
pre-map game start, and SIX synthetic prior settled markets so Allie's
settlement-lag input has its minimum sample (allie_capital.MIN_LAG_SAMPLE =
5). ALL DATA SYNTHETIC; nothing here is evidence about any market or of
profitability.
"""
from __future__ import annotations

import datetime as _dt
import inspect
import json
import time
import uuid
from zoneinfo import ZoneInfo

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_strategy_lifecycle as LC
from sportsassets import canonical_components as CC
from sportsassets import live_parity as LP
from sportsassets.agents import karen_runner as KR
from sportsassets.agents import paper_audrey as PA
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: the strict management entry rail runs its production functions here
MANAGEMENT_RAIL_ENFORCED = True

LABEL = "REHEARSAL -- SYNTHETIC -- NOT PRODUCTION ACTIVITY -- NO VENUE ORDER"
CG = PB.CG_STRATEGY
#: THE DEPLOYED FEE SCHEDULE (bettor_paper_ledger.default_fee_fn: the
#: published PMUS taker schedule) -- not a test fee: Audrey's operational fee
#: audit recomputes every fill from the published schedule
FEE = None
P_PIN = 0.62
LEVELS = [(0.50, 700), (0.51, 5000)]
HIST = PL.SYN + "rc63hist-"
NY = "America/New_York"
#: the Karen detectors whose table holds a record of this lifecycle
KAREN_DETECTORS = ("ENTRY_WITHOUT_PROBABILITY", "HOLD_ON_STALE_PROBABILITY",
                   "AUDIT_DISCREPANCY_LEFT_OPEN", "DECISION_WITHOUT_EVIDENCE")


async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, client, steps=None):
    transport.t = max(transport.t, float(now))
    got = await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                              market_data=client, config=acct["config"],
                              force=True, fee_fn=FEE, sleep=_nosleep,
                              steps=steps)
    assert got["ran"] and not got["errors"], got.get("errors")
    return got


def _ep(v):
    return None if v is None else (v.timestamp() if hasattr(v, "timestamp")
                                   else float(v))


@pytest.fixture
def cg_with_canonical_hooks(monkeypatch, new_strategies_off):
    """The completed-game policy on, the other policies off (their counts
    are not this proof's), the canonical hooks installed as the executing
    process installs them."""
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    CC.reset_cache()
    LP.install()
    yield
    LP.uninstall()
    CC.reset_cache()
    PB._CONTEXT_CACHE.clear()


async def _seed_allie_inputs(conn, *, slug: str, now: float) -> dict:
    """The decision market's pre-map game start, and six SYNTHETIC prior
    settled markets (their own scratch account) with theirs, so the
    settlement-lag sample Allie reads has its minimum (5)."""
    hist = await H.new_account(conn, "rc63hist", now=now - 86400.0)
    await conn.execute(
        "INSERT INTO us_premap (identifier, side_norm, market_slug, "
        " game_start, updated_at) VALUES ($1, 'LONG', $2, to_timestamp($3), "
        " to_timestamp($4))", HIST + "ident-" + slug, slug, now + 3 * 3600.0,
        now - 600.0)
    for i in range(6):
        hs = "%s%s-%d" % (HIST, uuid.uuid4().hex[:8], i)
        gs = now - 86400.0 * (2 + i)
        await conn.execute(
            "INSERT INTO us_premap (identifier, side_norm, market_slug, "
            " game_start, updated_at) VALUES ($1, 'LONG', $2, "
            " to_timestamp($3), to_timestamp($4))", HIST + "ident-" + hs, hs,
            gs, gs - 3600.0)
        pk = "paperpos:%s:%sgrp:LONG" % (hist["account_id"], hs)
        await conn.execute(
            "INSERT INTO paper_settlements (settlement_id, account_id, "
            " position_key, settlement_event_key, version, group_id, "
            " us_market_slug, holding_side, qty, outcome, "
            " payout_per_contract, payout_usd, evidence, evidence_source, "
            " settled_at, recorded_at) VALUES ($1,$2,$3,$4,1,$5,$6,'LONG',"
            " 10,'WON',1,10,'{}'::jsonb,'RC63_SYNTHETIC_PRIOR_SETTLEMENT',"
            " to_timestamp($7),to_timestamp($8))",
            "paperset:" + hs, hist["account_id"], pk, "venue-final:" + hs,
            hs + "grp", hs, gs + 3.5 * 3600.0 + 60.0 * i,
            gs + 3.5 * 3600.0 + 60.0 * i + 30.0)
    return hist


async def _clean_allie_inputs(conn) -> None:
    await conn.execute("DELETE FROM us_premap WHERE identifier LIKE $1",
                       HIST + "%")
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM paper_settlements WHERE settlement_id LIKE $1",
            "paperset:" + HIST + "%")


async def _clean_settlements(conn, account_id) -> None:
    """THIS LIFECYCLE'S SETTLEMENT, removed afterwards: the canonical
    INVESTMENT path (intent -> ENTRY order -> the group's settlement) is
    counted per sport by every later proof in a shared database
    (canonical_components.exceptional_at_decision), so a settled synthetic
    baseball group left behind would become their sport's sample."""
    if not account_id:
        return
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM paper_settlements WHERE account_id=$1",
                           account_id)


async def _main_account_counts(conn) -> dict:
    q = {"lifecycle_events": "SELECT count(*) FROM "
         "paper_strategy_lifecycle_events WHERE account_id=$1",
         "orders": "SELECT count(*) FROM paper_orders WHERE account_id=$1",
         "fills": "SELECT count(*) FROM paper_fills WHERE account_id=$1",
         "decisions": "SELECT count(*) FROM paper_decisions "
         "WHERE account_id=$1",
         "ledger": "SELECT count(*) FROM paper_ledger WHERE account_id=$1"}
    return {k: int(await conn.fetchval(s, L.ACCOUNT_ID)) for k, s in q.items()}


def _next_midnight(at: float) -> float:
    tz = ZoneInfo(NY)
    d = _dt.datetime.fromtimestamp(at, tz).date() + _dt.timedelta(days=1)
    return _dt.datetime(d.year, d.month, d.day, tzinfo=tz).timestamp()


async def _command_center(monkeypatch, paths: dict) -> dict:
    from sportsassets.api import app as A
    from tests import test_agent_workspaces_show_runtime_records as WS
    c = await WS._client(monkeypatch, A)
    out = {}
    try:
        for k, path in paths.items():
            r = await c.get(path)
            assert r.status_code == 200, (k, r.status_code, r.text[:400])
            out[k] = r.json()
    finally:
        await c.aclose()
        await WS._close_pool()
    return out


@pg
async def test_one_paper_decision_runs_every_duty_and_links_by_decision_id(
        cg_with_canonical_hooks, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    karen_opened: list = []
    aid = None
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        await _clean_allie_inputs(conn)
        ctl = await LP.control(conn)
        if ctl.get("halted"):
            await LP.clear_halt(conn, actor="test harness (human operator)",
                                reason="isolate this rehearsal")
        if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
            await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
        main_before = await _main_account_counts(conn)
        live_events_before = await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events")

        acct = await PL.new_account(conn, "rc63duty", now=now)
        aid = acct["account_id"]
        assert aid.startswith("paper_test_") and aid != L.ACCOUNT_ID
        # ── THE STRATEGY IS ACTIVE HERE ONLY: no lifecycle event, initial
        # state; the decision-level gate admits full size ─────────────────
        st = await LC.current_state(conn, aid, CG)
        assert st["ok"] and st["state"] == LC.ACTIVE_CHALLENGER, st
        assert st["basis"] == "NO_EVENT_ROW_INITIAL_STATE", st
        gate = await LC.decision_gate(conn, account_id=aid, strategy=CG,
                                      at=now)
        assert gate["refusal"] is None and gate["size_factor"] == 1.0, gate

        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=P_PIN,
                               compatibility="INCOMPATIBLE")
        slug = v["slug"]
        await _seed_allie_inputs(conn, slug=slug, now=now)
        t.set(slug, offers=LEVELS, bids=[(0.48, 2000)])
        client = PL.client(t)

        # ══ PASS 1: DEREK DECIDES; THE INTENT, THE RISK VERDICT, THE ORDER ═
        await _pass(conn, acct, t, now, client)
        d = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"],
            v["valuation_id"], CG)
        assert d is not None and d["verdict"] == "ENTER", (
            d and (d["refusal"], d["refusals"]))
        did = d["decision_id"]
        # 1 · DEREK: model price, the walk he priced, fees, all-in price
        econ_rec = H.j(d["economics"])
        econ = econ_rec["acquisition"]            # the walk he priced
        assert float(d["p_pinnacle"]) == pytest.approx(P_PIN)
        assert econ_rec["probability"] == pytest.approx(P_PIN)
        assert econ_rec["limit_price"] == pytest.approx(LEVELS[-1][0])
        assert H.j(d["pinnacle"])["p"] == pytest.approx(P_PIN)
        qty = sum(q for _, q in LEVELS)
        cost = sum(px * q for px, q in LEVELS)
        level_fees = [float(L._fee(None, q, px, _ep(d["decided_at"])))
                      for px, q in LEVELS]
        fees = sum(level_fees)
        assert fees > 0
        assert float(d["proposed_qty"]) == qty == econ["qty"]
        assert float(d["limit_price"]) == pytest.approx(LEVELS[-1][0])
        assert econ["acquisition_cost_usd"] == pytest.approx(cost)
        assert econ["vwap"] == pytest.approx(cost / qty)
        assert econ["fees_usd"] == pytest.approx(fees)
        assert [(r["price"], r["qty"]) for r in econ["fee_basis"]] == [
            (px, q) for px, q in LEVELS]
        assert [r["fee_usd"] for r in econ["fee_basis"]] == pytest.approx(
            level_fees)
        all_in = (cost + fees) / qty
        assert econ["expected_net_profit_usd"] == pytest.approx(
            qty * P_PIN - cost - fees)
        assert econ["expected_net_profit_usd"] > 0

        it = await conn.fetchrow(
            "SELECT * FROM canonical_decision_intents WHERE decision_id=$1",
            did)
        assert it is not None and LP.verify_intent(dict(it))
        assert it["strategy"] == CG and it["sleeve"] == "INVESTMENT"
        assert H.j(it["derek"])["verdict"] == "ENTER"
        # 2 · ARCHER: his estimate at the decision instant
        eddie = H.j(it["eddie"])
        assert eddie["status"] == "MEASURED", eddie
        assert eddie["estimate_id"] and eddie["estimator_version"]
        assert isinstance(eddie["recommendation"], str) and \
            eddie["recommendation"], eddie
        assert eddie["hard_rule_overrode"] in (True, False)
        assert eddie["execution_style"] and eddie["execution_evidence"]
        # 3 · ALLIE: measured, inside her (unchanged) component bound
        assert CC.COMPONENT_TIMEOUT_S == 2.0
        allie = H.j(it["allie"])
        assert allie["status"] == "MEASURED", allie
        assert allie.get("why") is None
        assert int(allie["evidence"]["settlement_lag_samples"]) >= 5
        assert allie["evidence"]["measured"]["settlement_lag"] is True
        assert float(allie["expected_capital_required_usd"]) == \
            pytest.approx(cost + fees, abs=0.01)
        assert float(allie["expected_hours_to_capital_release"]) > 0
        assert allie["binding_constraint"] and allie["final_binding"]
        assert float(allie["final_allocatable_usd"]) >= 0.0
        # recorded beside the order, never resizing it (SHADOW authority)
        assert allie["authority"] == "SHADOW_PENDING_OWNER_APPROVAL"
        assert allie["order_vs_allocation"]["verdict"] in (
            "ORDER_WITHIN_ALLOCATION", "ORDER_EXCEEDS_ALLOCATION")
        bc = H.j(it["binding_constraints"])
        assert bc["allie_status"] == "MEASURED"
        karen_at = H.j(it["karen"])
        assert karen_at["state"] == \
            "NO_OPEN_CHALLENGE_ON_THIS_MARKET_OR_STRATEGY", karen_at
        for comp in ("opportunity_score",):
            c = H.j(it[comp])
            assert c["status"] == "MEASURED" or c.get("why"), (comp, c)
        refs = H.j(it["evidence_refs"])
        assert {"kind": "paper_decisions", "id": did} in refs
        assert {"kind": "eddie_estimate", "id": eddie["estimate_id"]} in refs
        assert {"kind": "valuation", "id": v["valuation_id"]} in refs
        tour = await conn.fetch(
            "SELECT decision_id, intent_id FROM opportunity_score_tournament "
            " WHERE decision_id=$1", did)
        assert [(r["decision_id"], r["intent_id"]) for r in tour] == [
            (did, it["intent_id"])], [dict(r) for r in tour]

        # 6 · THE ORDER IS THE INTENT'S ORDER, AND NAMES THE DECISION
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1 AND role='ENTRY'", did)
        assert o is not None and o["account_id"] == aid
        gid, oid = o["group_id"], o["order_id"]
        assert o["idempotency_key"] == did + ":ENTRY"
        assert float(o["qty"]) == float(it["target_qty"]) == qty
        assert float(o["limit_price"]) == float(it["limit_price"])
        assert float(o["wire_price"]) == float(it["wire_price"])
        assert o["intent"] == it["order_intent"] and o["strategy"] == CG
        assert _ep(d["recorded_at"]) <= _ep(o["created_at"])
        # 4 · RISK: the ledger's verdict under the lock, on the order's key
        lev = await conn.fetchrow(
            "SELECT * FROM paper_profitability_evaluations WHERE "
            " decision_id=$1 AND stage='LEDGER'", did)
        assert lev is not None and lev["verdict"] == "ENTER"
        assert lev["order_key"] == o["idempotency_key"]
        assert lev["account_id"] == aid and lev["strategy"] == CG
        # the seeded learned-state gate says so on the record itself
        assert (H.j(lev["detail"]).get("bind") or {}).get("regime") == \
            "SEEDED"
        sub = await conn.fetchrow(
            "SELECT * FROM paper_order_events WHERE order_id=$1 AND "
            " kind='SUBMITTED'", oid)
        assert sub is not None
        ca = (H.j(sub["detail"]) or {}).get("capital_authority") or {}
        assert ca.get("rules_firing") == [], ca
        assert ca.get("forward_verdict") == "POSITIVE", ca
        rails = H.j(it["risk_rails"])
        assert float(rails["per_order_cap_usd"]) >= float(o["reserved_usd"])
        # 5 · LEDGER: the reservation of limit x qty + max fees
        res = await conn.fetchrow(
            "SELECT * FROM paper_ledger WHERE order_id=$1 AND "
            " kind='ORDER_SUBMITTED'", oid)
        assert res is not None and res["account_id"] == aid
        assert float(res["reserved_delta_usd"]) == pytest.approx(float(
            L.reservation_for(qty, LEVELS[-1][0], at=_ep(o["decided_at"])))
        ) == float(o["reserved_usd"])
        assert float(res["cash_delta_usd"]) == 0.0
        assert res["group_id"] == gid

        # ══ PASSES 2-4: THE DEPTH-WALK FILL, THE HANDOFF, XAVIER ═══════════
        for k in (5, 70, 140):
            await _pass(conn, acct, t, now + k, client)
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " order_id=$1", oid)
        assert o["state"] == "FILLED" and float(o["filled_qty"]) == qty
        fills = await conn.fetch(
            "SELECT * FROM paper_fills WHERE order_id=$1 ORDER BY price", oid)
        # 7 · TWO DISPLAYED LEVELS WALKED, AFTER THE DELAY
        assert [(float(f["qty"]), float(f["price"])) for f in fills] == [
            (float(q), px) for px, q in LEVELS]
        assert {f["basis"] for f in fills} == {"DEPTH_WALK_WITHIN_LIMIT"}
        assert {f["event_source"] for f in fills} == {"SIMULATOR"}
        assert all(_ep(f["book_observed_at"]) >= _ep(o["eligible_at"])
                   for f in fills)
        assert [float(f["fee_usd"]) for f in fills] == pytest.approx(
            [float(L._fee(None, f["qty"], f["price"], _ep(f["filled_at"])))
             for f in fills])
        assert sum(float(f["fee_usd"]) for f in fills) == pytest.approx(fees)
        fill_ids = [f["fill_id"] for f in fills]
        led = {r["fill_id"]: r for r in await conn.fetch(
            "SELECT * FROM paper_ledger WHERE account_id=$1 AND kind='FILL'",
            aid)}
        for f in fills:
            e = led[f["fill_id"]]
            assert e["order_id"] == oid and e["group_id"] == gid
            assert float(e["cash_delta_usd"]) == pytest.approx(
                -(float(f["qty"]) * float(f["price"]) + float(f["fee_usd"])))
        assert float(o["reserved_remaining_usd"]) == 0.0
        # the decision priced exactly what was filled
        assert sum(float(f["qty"]) * float(f["price"]) for f in fills) == \
            pytest.approx(econ["acquisition_cost_usd"])
        # 8 · XAVIER: handoff (naming the decision), review, protection
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", gid)
        assert h is not None and h["decision_id"] == did
        assert h["owner"] == "XAVIER" and h["entry_order_id"] == oid
        assert float(h["confirmed_qty"]) == qty
        assert h["first_fill_id"] in fill_ids
        reviews = await conn.fetch(
            "SELECT * FROM paper_xavier_reviews WHERE group_id=$1 "
            " ORDER BY reviewed_at, review_id", gid)
        first = [r for r in reviews if r["trigger"] == "FIRST_FILL"]
        assert len(first) == 1
        act = H.j(first[0]["action"])
        assert act["taken"] == "PLACE_STANDING" and act["ok"] is True
        # the canonical management intent's SMALL LIVE side is a SHADOW
        # proposal; the paper side placed the protection
        assert act["live_parity"]["live"].startswith("SHADOW_"), act
        prot = await conn.fetch(
            "SELECT * FROM paper_orders WHERE group_id=$1 AND "
            " role='STANDING_PROTECTION'", gid)
        assert len(prot) == 1, "ONE standing protective order"
        prot = prot[0]
        assert prot["order_id"] == act["order_id"]
        assert prot["direction"] == "SELL" and float(prot["qty"]) == qty
        assert prot["state"] == "RESTING"
        assert float(prot["limit_price"]) == pytest.approx(
            act["protective_price"])
        mis = await conn.fetch(
            "SELECT * FROM canonical_management_intents WHERE group_id=$1",
            gid)
        assert {m["review_id"] for m in mis} <= {r["review_id"]
                                                 for r in reviews}
        assert first[0]["review_id"] in {m["review_id"] for m in mis}
        for m in mis:
            ex = {r["adapter"]: r for r in await conn.fetch(
                "SELECT * FROM canonical_intent_executions WHERE "
                " intent_id=$1", m["intent_id"])}
            assert set(ex) == {"PAPER", "SMALL_LIVE"}
            assert ex["SMALL_LIVE"]["mode"] == "SHADOW"
        dex = {r["adapter"]: r for r in await conn.fetch(
            "SELECT * FROM canonical_intent_executions WHERE intent_id=$1",
            it["intent_id"])}
        assert dex["PAPER"]["mode"] == "SIMULATED"
        assert dex["SMALL_LIVE"]["mode"] == "SHADOW"
        assert "venue_order_id" not in H.j(dex["SMALL_LIVE"]["refs"])

        # ══ 9 · SETTLEMENT FROM AUTHORITATIVE EVIDENCE, EXACTLY ONCE ═══════
        await PL.settle_valuation(conn, v["valuation_id"], outcome=1)
        p_set = await _pass(conn, acct, t, now + 240, client)
        assert p_set["steps"]["settle"]["settled"] == 1, p_set["steps"][
            "settle"]
        p_again = await _pass(conn, acct, t, now + 360, client)
        assert p_again["steps"]["settle"]["settled"] == 0
        sets = await conn.fetch("SELECT * FROM paper_settlements WHERE "
                                " group_id=$1", gid)
        assert len(sets) == 1
        s = sets[0]
        assert (s["outcome"], float(s["qty"]), float(s["payout_usd"])) == (
            "WON", qty, qty * 1.0)
        assert s["account_id"] == aid
        sled = await conn.fetch(
            "SELECT * FROM paper_ledger WHERE account_id=$1 AND "
            " kind='SETTLEMENT' AND group_id=$2", aid, gid)
        assert len(sled) == 1 and float(sled[0]["cash_delta_usd"]) == \
            pytest.approx(qty)
        prot = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                   " order_id=$1", prot["order_id"])
        assert prot["state"] == "CANCELED"
        assert prot["terminal_reason"] == "MARKET_SETTLED"
        assert float(prot["filled_qty"]) == 0.0

        # ══ 10 · THE TERMINAL DISPOSITION ═════════════════════════════════
        pos = [p for p in await L.positions(conn, aid, include_closed=True)
               if p["group_id"] == gid]
        assert len(pos) == 1
        pos = pos[0]
        assert pos["open_qty"] == 0.0 and LC.closed_at(pos) is not None
        assert pos["settlement"]["outcome"] == "WON"
        realized = qty * 1.0 - cost - fees
        assert pos["realized_pnl_usd"] == pytest.approx(realized)
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND "
            " state = ANY($2::text[])", gid, list(L.OPEN_STATES)) == 0
        b = await L.balances(conn, aid, now=now + 400)
        assert b["ledger_consistent"] is True and b["reserved_usd"] == 0.0
        assert not b["open_positions"]
        assert b["realized_pnl_usd"] == pytest.approx(realized)
        assert b["cash_usd"] == pytest.approx(500000.0 + realized)

        # ══ 11 · AUDREY: THE DAY'S REPORT AND THE DAY TURN ═════════════════
        # the report's next due version, then the DAY TURN: passes running
        # Audrey's step alone (the pass machinery, her module), so no other
        # agent writes a future-dated record or watermark in a shared DB
        audrey_only = [x for x in PR.default_steps() if x[0] == "audrey"]
        assert len(audrey_only) == 1
        rec_at = now + PA.REPORT_EVERY_S + 60.0
        await _pass(conn, acct, t, rec_at, client, steps=audrey_only)
        turn_at = _next_midnight(rec_at) + 120.0
        await _pass(conn, acct, t, turn_at, client, steps=audrey_only)
        fill_day = PA.day_bounds(_ep(fills[0]["filled_at"]), NY)[0]
        reps = [dict(r) for r in await conn.fetch(
            "SELECT * FROM paper_audrey_reports WHERE session_id=$1 "
            " ORDER BY report_day, version", acct["session_id"])]
        assert reps and all(r["account_id"] == aid for r in reps)
        assert all(r["reconciles"] is True for r in reps), [
            (r["report_day"], r["version"], r["reconciles"]) for r in reps]
        day_reps = [r for r in reps if r["report_day"] == fill_day]
        last = day_reps[-1]
        body = H.j(last["report"])
        assert body["reconciliation"]["reconciles"] is True
        assert body["acquisition_volume"]["entries_usd"] == pytest.approx(
            cost + fees)
        assert body["acquisition_volume"]["hedges_usd"] == 0.0
        assert body["sale_proceeds"]["never_acquisition"] is True
        assert body["breadth"]["distinct_markets"] == 1
        assert body["reporting_tz"] == NY
        # the closing version of the fill's day: FINAL only where supported
        supports_final = "closed_at" in inspect.signature(
            PA.write_report).parameters
        audrey_final = {"report_day": str(fill_day),
                        "versions": [(r["version"], r["final"])
                                     for r in day_reps],
                        "code_supports_closing_final": supports_final}
        if supports_final:
            assert last["final"] is True, audrey_final
            audrey_final["status"] = "FINAL_RECORDED"
        else:
            # 34cb9544: the closing version is written with now just before
            # midnight and final = now >= the day's end, so no version of a
            # closed day is ever final (fixed on rc6/audrey-final 6d8cd731)
            assert not any(r["final"] for r in day_reps), audrey_final
            audrey_final["status"] = ("KNOWN_DEFECT_CLOSING_VERSION_NOT_FINAL"
                                      "_FIXED_ON_rc6/audrey-final_6d8cd731")
        findings = {(r["kind"], r["subject"]): dict(r) for r in
                    await conn.fetch(
                        "SELECT * FROM paper_audrey_findings WHERE "
                        " account_id=$1", aid)}
        pos_key = pos["position_key"]
        subjects = ({did, gid, oid, prot["order_id"], h["handoff_id"],
                     s["settlement_id"], pos_key} | set(fill_ids))
        life = {k: f for k, f in findings.items() if k[1] in subjects}
        for kind, subject in (("PAPER_EVENT_HANDOFF", h["handoff_id"]),
                              ("PAPER_EVENT_SETTLEMENT", s["settlement_id"])):
            assert (kind, subject) in life, (kind, subject, sorted(findings))
        assert any(k == "PAPER_EVENT_FIRST_FILL" and sub in fill_ids
                   for k, sub in life)
        # Audrey found no discrepancy in this lifecycle
        assert {f["severity"] for f in life.values()} <= {"INFO"}, [
            (f["kind"], f["severity"], f["subject"]) for f in life.values()]
        assert not any((H.j(f["detail"]) or {}).get("passed") is False
                       for f in life.values())
        # the account / day level findings: each WARNING is assigned -- a
        # task from the daily report, a recommendation from the operational
        # audit (paper_ops_audit opens recommendations, not tasks)
        other = [f for k, f in findings.items() if k not in life]
        for f in other:
            if f["severity"] not in ("WARNING", "CRITICAL", "ERROR"):
                continue
            if f["kind"].startswith("OPS_AUDIT_"):
                assert await conn.fetchval(
                    "SELECT count(*) FROM paper_recommendations WHERE "
                    " finding_id=$1", f["finding_id"]) >= 1, f
            else:
                assert f["improvement_task_id"], f

        # ══ 12 · KAREN: HER RULES OVER EVERY RECORD OF THIS LIFECYCLE ══════
        reviews = await conn.fetch(
            "SELECT * FROM paper_xavier_reviews WHERE group_id=$1 "
            " ORDER BY reviewed_at, review_id", gid)
        targets = ([("ENTRY_WITHOUT_PROBABILITY", "paper_decisions", did)]
                   + [("HOLD_ON_STALE_PROBABILITY", "paper_xavier_reviews",
                       r["review_id"]) for r in reviews]
                   + [("AUDIT_DISCREPANCY_LEFT_OPEN", "paper_audrey_findings",
                       f["finding_id"]) for f in life.values()])
        for det, kind, tid in targets:
            assert await KR.rule_holds(conn, det, kind, tid) is False, (
                det, kind, tid)
        ours = {tid for _, _, tid in targets}
        tx = conn.transaction()
        await tx.start()
        try:
            summ = await KR.pass_once(
                conn, now=turn_at + 60.0,
                detectors=[x for x in KR.DETECTORS
                           if x[0] in KAREN_DETECTORS])
            karen_opened = list(summ.get("opened") or [])
            on_ours = await conn.fetch(
                "SELECT challenge_id, detector, target_id FROM "
                " karen_challenges WHERE target_id = ANY($1::text[])",
                sorted(ours))
            karen_status = await conn.fetchrow(
                "SELECT state, activity FROM agent_status WHERE "
                " agent_id='KAREN'")
        finally:
            await tx.rollback()
        assert not summ.get("detector_errors"), summ
        assert summ["status"] in ("NOTHING_TO_CHALLENGE", "CHALLENGES_OPENED")
        assert [dict(r) for r in on_ours] == [], \
            "Karen challenged a record of a legitimate lifecycle"
        assert karen_status is not None

        # ══ 13 · THE COMMAND CENTER: THE SAME IDS AND STATES ══════════════
        got = await _command_center(monkeypatch, {
            "chain": "/api/command/paper/learning/chain/%s" % fill_ids[0],
            "decision": "/api/command/paper/learning/decision/%s" % did})
        chain = got["chain"]
        assert chain["result"]["status"] == "OK", chain
        assert chain["data_label"] == L.DATA_LABEL
        ch = chain["result"]["data"]
        assert ch["found"] is True, chain
        links = {x["link"]: x for x in ch["links"]}
        assert {k: x["status"] for k, x in links.items()} == {
            "DEREK_DECISION": "PRESENT", "ENTRY_ORDER": "PRESENT",
            "ENTRY_FILLS": "PRESENT", "LEDGER_CASH_DEBIT": "PRESENT",
            "XAVIER_HANDOFF": "PRESENT", "XAVIER_REVIEWS": "PRESENT",
            "XAVIER_ACTIONS": "PRESENT", "EXIT_OR_SETTLEMENT": "PRESENT",
            "LEDGER_RESULT": "PRESENT", "AUDREY_AUDIT": "PRESENT"}, links
        assert links["DEREK_DECISION"]["ids"] == [did]
        assert links["DEREK_DECISION"]["detail"]["verdict"] == "ENTER"
        assert links["DEREK_DECISION"]["detail"]["strategy"] == CG
        assert links["ENTRY_ORDER"]["ids"] == [oid]
        assert links["ENTRY_ORDER"]["detail"]["state"] == "FILLED"
        assert sorted(links["ENTRY_FILLS"]["ids"]) == sorted(fill_ids)
        assert sorted(links["LEDGER_CASH_DEBIT"]["ids"]) == sorted(
            led[f]["seq"] for f in fill_ids)
        assert links["XAVIER_HANDOFF"]["ids"] == [h["handoff_id"]]
        assert links["XAVIER_REVIEWS"]["ids"] == [r["review_id"]
                                                  for r in reviews]
        assert links["XAVIER_ACTIONS"]["ids"] == [prot["order_id"]]
        assert links["XAVIER_ACTIONS"]["detail"]["orders"][0]["state"] == \
            "CANCELED"
        assert links["EXIT_OR_SETTLEMENT"]["ids"] == [s["settlement_id"]]
        assert links["LEDGER_RESULT"]["ids"] == [sled[0]["seq"]]
        assert links["LEDGER_RESULT"]["detail"]["realized_pnl_usd"] == \
            pytest.approx(realized)
        assert set(links["AUDREY_AUDIT"]["ids"]) == {
            f["finding_id"] for f in life.values()}
        dec = got["decision"]
        assert dec["result"]["status"] == "OK", dec
        dd = dec["result"]["data"]
        assert dd["found"] is True, dec
        assert dd["decision_id"] == did
        assert dd["record"]["decision_id"] == did
        assert dd["record"]["verdict"] == "ENTER"
        assert dd["record"]["strategy"] == CG

        # ══ EVERY RECORD, BY decision_id ══════════════════════════════════
        by_decision = {
            "paper_decisions": did,
            "canonical_decision_intents": it["decision_id"],
            "paper_orders(ENTRY)": o["decision_id"],
            "paper_profitability_evaluations(LEDGER)": lev["decision_id"],
            "paper_handoffs": h["decision_id"]}
        assert set(by_decision.values()) == {did}, by_decision
        for e in await conn.fetch(
                "SELECT stage, decision_id FROM "
                " paper_profitability_evaluations WHERE account_id=$1", aid):
            assert e["decision_id"] == did, dict(e)
        # through the decision's ENTRY order: its group and its order id
        assert {f["group_id"] for f in fills} == {gid}
        assert {r["group_id"] for r in reviews} == {gid}
        assert prot["group_id"] == gid and s["group_id"] == gid
        assert {m["group_id"] for m in mis} == {gid}
        assert {r["order_id"] for r in await conn.fetch(
            "SELECT order_id FROM paper_ledger WHERE group_id=$1 AND "
            " kind IN ('ORDER_SUBMITTED', 'FILL')", gid)} == {oid}
        assert {r["intent_id"] for r in await conn.fetch(
            "SELECT intent_id FROM live_parity_ledger WHERE intent_id=$1",
            it["intent_id"])} == {it["intent_id"]}
        slv = await conn.fetch(
            "SELECT decision_id, group_id, sleeve FROM "
            " paper_sleeve_classifications WHERE group_id=$1", gid)
        assert [(r["decision_id"], r["sleeve"]) for r in slv] == [
            (did, "INVESTMENT")], [dict(r) for r in slv]
        # NOTHING ELSE TRADED: one decision, one group, this account only
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1 AND "
            " role='ENTRY'", aid) == 1
        assert await _main_account_counts(conn) == main_before
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == \
            live_events_before
        assert client.mutation_attempts == 0

        evidence = {
            "label": LABEL, "account_id": aid, "decision_id": did,
            "lifecycle_state_in_test_db": st["state"],
            "derek": {"p_pinnacle": float(d["p_pinnacle"]),
                      "levels": LEVELS, "vwap": econ["vwap"],
                      "all_in_price_per_contract": round(all_in, 9),
                      "fees_usd": econ["fees_usd"],
                      "expected_net_usd": econ["expected_net_profit_usd"]},
            "archer": {"estimate_id": eddie["estimate_id"],
                       "recommendation": eddie["recommendation"]},
            "allie": {k: allie.get(k) for k in (
                "status", "binding_constraint", "final_binding",
                "final_allocatable_usd", "expected_hours_to_capital_release")}
            | {"lag_samples": allie["evidence"]["settlement_lag_samples"]},
            "risk": {"ledger_eval_id": lev["eval_id"], "verdict":
                     lev["verdict"], "rules_firing": ca.get("rules_firing"),
                     "forward_verdict": ca.get("forward_verdict"),
                     "seeded": ["forward_economics", "profitability_bind",
                                "economic_controls", "quarantine"]},
            "ledger_reservation": {"seq": res["seq"], "reserved_usd":
                                   float(res["reserved_delta_usd"])},
            "order": {"order_id": oid, "group_id": gid},
            "fills": [[f["fill_id"], float(f["qty"]), float(f["price"]),
                       float(f["fee_usd"])] for f in fills],
            "xavier": {"handoff_id": h["handoff_id"],
                       "reviews": [[r["review_id"], r["trigger"],
                                    r["recommendation"]] for r in reviews],
                       "protection_order_id": prot["order_id"],
                       "protection_terminal": prot["terminal_reason"],
                       "management_intents": len(mis)},
            "settlement": {"settlement_id": s["settlement_id"],
                           "outcome": s["outcome"],
                           "payout_usd": float(s["payout_usd"])},
            "terminal": {"open_qty": pos["open_qty"],
                         "realized_pnl_usd": pos["realized_pnl_usd"]},
            "audrey": {"reports": [(str(r["report_day"]), r["version"],
                                    r["final"], r["reconciles"])
                                   for r in reps], "final": audrey_final},
            "audrey_lifecycle_findings": sorted(
                (k[0], k[1]) for k in life),
            "audrey_account_findings": sorted(
                (f["kind"], f["severity"]) for f in other),
            "karen": {"targets_checked": len(targets),
                      "status": summ["status"],
                      "opened_elsewhere_rolled_back": len(karen_opened)},
            "command_center": {k: (x["status"], x["ids"][:3])
                               for k, x in links.items()}}
        print("\n%s\nTHE LIFECYCLE, BY decision_id:\n%s" % (
            LABEL, json.dumps(evidence, indent=1, default=str)))
    finally:
        await PL.purge_everything(conn)
        await _clean_allie_inputs(conn)
        await _clean_settlements(conn, aid)
        await conn.close()
