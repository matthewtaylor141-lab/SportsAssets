"""R30 LIVE PARITY: one canonical intent, two execution adapters, the parity
ledger, the halt and the readiness gate.

Pure tests run everywhere. The Postgres tests (migration 225, append-only,
SHADOW-only CHECKs, the halt, the end-to-end record) need RN1X_TEST_DSN and
run inside one transaction that is rolled back.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import asyncpg
import pytest

from sportsassets import execmirror as M
from sportsassets import live_parity as LP
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "225_live_parity.sql").read_text()
DOWN = (MIG / "rollback" / "225_live_parity.down.sql").read_text()

CG = "PINNACLE_COMPLETED_GAME_PAPER"
CG3 = "PINNACLE_COMPLETED_GAME_PAPER_V3"


def _intent(**over):
    kw = dict(
        decision_id="papercg:abc", strategy=CG, strategy_version=CG3,
        evidence={"valuation_id": 101, "book_obs_id": 7, "probability": 0.62},
        opportunity_score={"status": "MEASURED", "opportunity_score": 0.4},
        derek={"status": "MEASURED", "verdict": "ENTER"},
        karen={"state": "NO_OPEN_CHALLENGE_ON_THIS_MARKET_OR_STRATEGY"},
        allie={"status": "MEASURED", "shadow_usd": 2.0},
        eddie={"status": "MEASURED", "recommendation": "EXECUTE_NOW"},
        us_market_slug="aec-nfl-kc-buf-2026-10-04", contract={"fixture": "f1"},
        holding_side="LONG", order_intent="ORDER_INTENT_BUY_LONG",
        order_type="MARKETABLE", time_in_force="IOC", limit_price=0.55,
        wire_price=0.55, target_qty=2400, sizing_basis={"rule": "x"},
        created_at=1791130000.0)
    kw.update(over)
    return LP.build_decision_intent(**kw)


def _paper_order(intent):
    o = {"idempotency_key": "k", "account_id": "paper_acct_main"}
    o.update(LP.paper_entry_fields(intent))
    return o


# ── the canonical decision intent ─────────────────────────────────────

def test_the_intent_is_deterministic_sha_stamped_and_tamper_evident():
    a, b = _intent(), _intent()
    assert a["content_sha"] == b["content_sha"]
    assert a["intent_id"] == LP.decision_intent_id("papercg:abc")
    assert a["intent_id"].startswith("cdi_") and len(a["intent_id"]) == 28
    assert LP.verify_intent(a)
    assert not LP.verify_intent(dict(a, limit_price=Decimal("0.56")))
    assert not LP.verify_intent(dict(a, holding_side="SHORT"))
    assert _intent(target_qty=2401)["content_sha"] != a["content_sha"]
    assert a["sleeve"] == "INVESTMENT"


def test_the_intent_carries_every_component_or_refuses():
    with pytest.raises(ValueError):
        _intent(derek={"status": "MEASURED"})            # no verdict
    with pytest.raises(ValueError):
        _intent(target_qty=0)
    with pytest.raises(ValueError):
        _intent(wire_price=1.0)
    with pytest.raises(ValueError):
        _intent(holding_side="UP")
    it = _intent(eddie=None, allie=None, opportunity_score=None)
    assert it["eddie"]["status"] == "UNAVAILABLE"        # reason, never a 0
    assert it["allie"]["status"] == "UNAVAILABLE"
    assert it["opportunity_score"]["status"] == "UNAVAILABLE"


def test_the_paper_adapter_reads_every_order_field_from_the_intent():
    it = _intent()
    f = LP.paper_entry_fields(it)
    assert f["qty"] == it["target_qty"] and f["limit_price"] == it["limit_price"]
    assert f["wire_price"] == it["wire_price"]
    assert f["holding_side"] == "LONG" and f["intent"] == "ORDER_INTENT_BUY_LONG"
    assert f["order_type"] == "MARKETABLE" and f["time_in_force"] == "IOC"
    assert f["strategy"] == CG and f["decision_id"] == "papercg:abc"


# ── the SMALL LIVE adapter (SHADOW) ───────────────────────────────────

def test_small_live_is_shadow_and_the_real_venue_refuses_any_new_order():
    assert LP.SMALL_LIVE_MODE == LP.MODE_SHADOW

    class Client:
        class orders:                                         # noqa: N801
            @staticmethod
            def create(params):
                raise AssertionError("the venue must never be reached")
    v = M.Venue(client=Client())
    with pytest.raises(M.LegacyOriginationRetired):
        v.place({"marketSlug": "x"})
    with pytest.raises(M.LegacyOriginationRetired):
        v.place({"marketSlug": "x"},
                canonical_live_authorization=type("T", (), {
                    "issued_by": LP.LIVE_ADAPTER_VERSION})())
    assert M._classify(M.LegacyOriginationRetired("x")) == "REJECTED"


def test_the_live_entry_is_the_same_order_at_capital_scale():
    it = _intent()
    live = LP.live_entry_proposal(it, scale=1000, buying_power=100.0,
                                  max_order_usd=25)
    assert live["state"] == LP.S_PROPOSED and live["live_qty"] == 2
    assert live["params"] == M.venue_params(
        {"us_market_slug": it["us_market_slug"], "intent": it["order_intent"],
         "wire_price": it["wire_price"], "time_in_force": "IOC",
         "order_type": "MARKETABLE"}, 2)
    r = live["requested"]
    assert r["limit_price"] == "0.55" and r["holding_side"] == "LONG"
    assert r["intent_sha"] == it["content_sha"]


def test_capital_bounds_are_exclusions_not_logic():
    small = LP.live_entry_proposal(_intent(target_qty=300), scale=1000,
                                   buying_power=100.0, max_order_usd=25)
    assert small["exclusion"] == M.BELOW_VENUE_MINIMUM
    cap = LP.live_entry_proposal(_intent(target_qty=60000), scale=1000,
                                 buying_power=100.0, max_order_usd=25)
    assert cap["exclusion"] == M.ABOVE_ORDER_CAP
    acct = LP.live_entry_proposal(_intent(), scale=1000, buying_power=None,
                                  max_order_usd=25)
    assert acct["exclusion"] == "ACCOUNT_STATE_NOT_CURRENT"
    off = LP.live_entry_proposal(_intent(strategy="PINNACLE_EXPLORATION_PAPER",
                                         strategy_version="X"),
                                 scale=1000, buying_power=100.0,
                                 max_order_usd=25)
    assert off["state"] == LP.S_NO_ORDER
    halted = LP.live_entry_proposal(_intent(), scale=1000, buying_power=100.0,
                                    max_order_usd=25, halted=True)
    assert halted["state"] == LP.S_HALTED and halted["params"] is None


# ── parity classification ─────────────────────────────────────────────

def _pair(it, *, paper_state=LP.P_SUBMITTED, refusal=None, live=None,
          order=None):
    order = order or _paper_order(it)
    live = live or LP.live_entry_proposal(it, scale=1000, buying_power=100.0,
                                          max_order_usd=25)
    return LP.compare(kind="DECISION", intent=it,
                      paper={"state": paper_state, "refusal": refusal,
                             "requested": LP.paper_entry_request(it, order)},
                      live=live, scale=1000)


def test_identical_logic_at_scale_is_an_expected_scale_difference():
    res = _pair(_intent())
    assert res["parity_state"] == LP.SCALE and res["divergence_fields"] == []
    f = res["comparison"]["fields"]
    assert all(f[k]["equal"] for k in LP.LOGIC_FIELDS)
    assert f["qty"]["expected_live_at_scale"] == 2


def test_scale_one_identical_quantities_match():
    it = _intent(target_qty=3)
    live = LP.live_entry_proposal(it, scale=1, buying_power=100.0,
                                  max_order_usd=25)
    res = LP.compare(kind="DECISION", intent=it,
                     paper={"state": LP.P_SUBMITTED,
                            "requested": LP.paper_entry_request(
                                it, _paper_order(it))},
                     live=live, scale=1)
    assert res["parity_state"] == LP.MATCHED


@pytest.mark.parametrize("field,value", [
    ("holding_side", "SHORT"), ("limit_price", Decimal("0.56")),
    ("wire_price", Decimal("0.57")), ("time_in_force", "FOK"),
    ("strategy", "OTHER"), ("intent", "ORDER_INTENT_BUY_SHORT")])
def test_any_logic_field_that_differs_is_a_divergence(field, value):
    it = _intent()
    order = dict(_paper_order(it), **{field: value})
    res = _pair(it, order=order)
    assert res["parity_state"] == LP.DIVERGENCE
    assert res["divergence_fields"]


def test_a_quantity_that_is_not_the_scaled_quantity_is_a_divergence():
    it = _intent()
    live = LP.live_entry_proposal(it, scale=1000, buying_power=100.0,
                                  max_order_usd=25)
    live = dict(live, live_qty=3)
    res = _pair(it, live=live)
    assert res["parity_state"] == LP.DIVERGENCE and "qty" in res[
        "divergence_fields"]


def test_a_different_intent_sha_is_a_divergence():
    it = _intent()
    other = _intent(target_qty=2600)
    live = LP.live_entry_proposal(other, scale=1000, buying_power=100.0,
                                  max_order_usd=25)
    res = _pair(it, live=live)
    assert res["parity_state"] == LP.DIVERGENCE
    assert "intent_sha" in res["divergence_fields"]


def test_exclusions_and_refusals_are_classified_by_cause():
    it = _intent(target_qty=300)
    assert _pair(it)["parity_state"] == LP.SCALE                # venue min
    it = _intent()
    venue = dict(LP.live_entry_proposal(it, scale=1000, buying_power=100.0,
                                        max_order_usd=25),
                 state=LP.S_EXCLUDED, exclusion=M.UNSUPPORTED_ORDER)
    assert _pair(it, live=venue)["parity_state"] == LP.VENUE_DIFF
    assert _pair(it, paper_state=LP.P_REFUSED,
                 refusal="INSUFFICIENT_AVAILABLE_PAPER_CASH")[
        "parity_state"] == LP.SCALE
    res = _pair(it, paper_state=LP.P_REFUSED,
                refusal="ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE")
    assert res["parity_state"] == LP.DIVERGENCE


# ── the canonical management intent ───────────────────────────────────

def _cand(qty=1500):
    return {"action": "EXIT", "qty": qty,
            "walk": {"worst_price": 0.61, "worst_wire": 0.61}}


def test_management_action_is_the_branch_the_review_takes():
    prot = {"ok": True, "price": 0.40}
    kw = dict(fresh=True, stale=False, p_missing=False, protection_ok=True,
              standing_live=False, candidate=_cand(), protective=prot,
              open_qty=2400)
    assert LP.management_action(chosen="EXIT", **kw)["action"] == LP.ACT_EXIT
    assert LP.management_action(chosen="REDUCE", **kw)["action"] == LP.ACT_REDUCE
    assert LP.management_action(chosen="EXIT", **dict(kw, standing_live=True))[
        "action"] == LP.ACT_CANCEL_FIRST
    assert LP.management_action(chosen="HOLD", **kw)["action"] == LP.ACT_PROTECT
    stale = dict(kw, fresh=False, stale=True)
    assert LP.management_action(chosen=None, **stale)["action"] == LP.ACT_PROTECT
    assert LP.management_action(chosen="EXIT", **stale)["action"] == LP.ACT_NONE
    assert LP.management_action(chosen="EXIT", **dict(
        kw, candidate={"qty": None}))["action"] == LP.ACT_NONE


def _mi(action_kw=None, **over):
    decided = LP.management_action(
        chosen=over.pop("chosen", "EXIT"), fresh=True, stale=False,
        p_missing=False, protection_ok=True, standing_live=False,
        candidate=over.pop("cand", _cand()), protective={"ok": True,
                                                         "price": 0.40},
        open_qty=2400)
    return LP.build_management_intent(
        review_id="paperrev:1", group_id="g1", position_key="pk",
        strategy=CG, valuation={"valuation_id": 9, "valuation_hash": "h"},
        evidence_state="FRESH_CURRENT_PROBABILITY", recommendation="EXIT",
        mechanical_selection="EXIT", decided=decided,
        us_market_slug="aec-nfl-kc-buf-2026-10-04", holding_side="LONG",
        alternatives={"candidates": [{"action": "EXIT", "qty": 1500}]},
        reason={}, created_at=1791130000.0)


def test_the_live_management_order_is_the_same_order_on_the_scaled_position():
    mi = _mi()
    assert mi["intent_id"].startswith("cmi_")
    live = LP.live_management_proposal(mi, scale=1000, open_qty=2400)
    assert live["live_held"] == 2 and live["state"] == LP.S_PROPOSED
    # REDUCE 1500 of 2400 -> 0.625 of 2 held -> 1 (half-even)
    assert live["live_qty"] == 1
    assert live["inventory_basis"].endswith("SHADOW_ONLY")
    taken = {"taken": "SUBMIT_EXIT", "ok": True, "requested": {
        "qty": 1500, "limit_price": 0.61, "wire_price": 0.61,
        "time_in_force": "IOC", "order_type": "MARKETABLE",
        "intent": "ORDER_INTENT_SELL_LONG"}}
    res = LP.compare(kind="MANAGEMENT", intent=mi,
                     paper={"state": LP.P_SUBMITTED,
                            "requested": LP.paper_management_request(mi, taken)},
                     live=live, scale=1000, open_qty=2400)
    assert res["parity_state"] == LP.SCALE, res


def test_a_paper_sale_that_differs_from_the_intent_is_a_divergence():
    mi = _mi()
    live = LP.live_management_proposal(mi, scale=1000, open_qty=2400)
    taken = {"taken": "SUBMIT_EXIT", "ok": True, "requested": {
        "qty": 1500, "limit_price": 0.59, "wire_price": 0.59,
        "time_in_force": "IOC", "order_type": "MARKETABLE",
        "intent": "ORDER_INTENT_SELL_LONG"}}
    res = LP.compare(kind="MANAGEMENT", intent=mi,
                     paper={"state": LP.P_SUBMITTED,
                            "requested": LP.paper_management_request(mi, taken)},
                     live=live, scale=1000, open_qty=2400)
    assert res["parity_state"] == LP.DIVERGENCE
    assert "limit_price" in res["divergence_fields"]


def test_standing_protection_is_one_resting_sale_of_the_scaled_holding():
    mi = _mi(chosen="HOLD")
    assert mi["action"] == LP.ACT_PROTECT
    live = LP.live_management_proposal(mi, scale=1000, open_qty=2400)
    assert live["live_qty"] == 2
    assert live["params"]["tif"] == M.TIF["GTD"]
    assert live["params"]["participateDontInitiate"] is True
    res = LP.compare(kind="MANAGEMENT", intent=mi,
                     paper={"state": LP.P_NO_ORDER,
                            "requested": LP.paper_management_request(
                                mi, {"taken": "KEEP_STANDING"})},
                     live=live, scale=1000, open_qty=2400)
    assert res["parity_state"] == LP.SCALE, res


# ── the venue lifecycle (venue record only) ───────────────────────────

@pytest.mark.parametrize("rec,state", [
    ({"state": "ORDER_STATE_REJECTED"}, "REJECTED"),
    ({"state": "ORDER_STATE_NEW", "quantity": 5, "cumQuantity": 0}, "RESTING"),
    ({"state": "ORDER_STATE_NEW", "quantity": 5, "cumQuantity": 2}, "PARTIAL"),
    ({"state": "ORDER_STATE_FILLED", "quantity": 5, "cumQuantity": 5}, "FILLED"),
    ({"state": "ORDER_STATE_PENDING_CANCEL", "quantity": 5}, "CANCEL_PENDING"),
    ({"state": "ORDER_STATE_CANCELED", "quantity": 5}, "CANCELLED")])
def test_live_lifecycle_states_come_from_the_venue_record(rec, state):
    assert LP.live_state_of(rec) == state
    assert state in LP.LIVE_STATES
    assert LP.live_state_of(None) is None


# ── the readiness gate ────────────────────────────────────────────────

def _row(kind, st=LP.SCALE, sleeve="INVESTMENT"):
    f = {k: {"equal": st != LP.DIVERGENCE} for k in LP.LOGIC_FIELDS}
    return {"intent_kind": kind, "sleeve": sleeve, "parity_state": st,
            "comparison": {"fields": f}}


def test_readiness_needs_samples_zero_divergence_and_forward_profit():
    rows = [_row("DECISION")] * 30 + [_row("MANAGEMENT")] * 30
    good = {"profitability_verdict": "SUPPORTED_BY_FORWARD_EVIDENCE"}
    r = LP.readiness(rows, halted=False, profitability=good)
    assert r["recommendation"] == LP.READY and r["blockers"] == []
    assert r["exact_side_match"]["rate"] == 1.0
    assert LP.readiness(rows, halted=False, profitability={
        "profitability_verdict": "POSITIVE_BUT_INSUFFICIENT_SAMPLE"})[
        "recommendation"] == LP.NOT_READY
    assert LP.readiness(rows, halted=True, profitability=good)[
        "recommendation"] == LP.NOT_READY
    bad = rows + [_row("DECISION", LP.DIVERGENCE)]
    rb = LP.readiness(bad, halted=False, profitability=good)
    assert rb["recommendation"] == LP.NOT_READY and rb["logic_divergences"] == 1
    few = LP.readiness(rows[:10], halted=False, profitability=good)
    assert "DECISION_SAMPLE:10_OF_30" in few["blockers"]
    # training rows never count as activation evidence
    tr = LP.readiness([_row("DECISION", sleeve="TRAINING")] * 50, halted=False,
                      profitability=good)
    assert tr["candidate_count"] == 0
    assert tr["excluded_from_activation_evidence"]["non_investment_rows"] == 50


# ── Postgres ──────────────────────────────────────────────────────────

async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


@pg
@pytest.mark.asyncio
async def test_225_is_idempotent_append_only_and_shadow_only():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute(UP)
        it = _intent()
        assert await LP.record_decision_intent(conn, it)
        assert not await LP.record_decision_intent(conn, it)   # one per decision
        await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                      "UPDATE canonical_decision_intents SET target_qty = 1")
        await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                      "DELETE FROM canonical_decision_intents")
        await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                      "TRUNCATE canonical_decision_intents")
        await _expect(conn, asyncpg.CheckViolationError,
                      "UPDATE small_live_control SET mode = 'LIVE'")
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO canonical_intent_executions (execution_id,
                           intent_kind, intent_id, intent_sha, adapter, mode,
                           adapter_version, state, requested, capital_scale)
                         VALUES ('x','DECISION',$1,$2,'SMALL_LIVE','LIVE','v',
                                 'SHADOW_PROPOSED','{}',1000)""",
                      it["intent_id"], it["content_sha"])
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO small_live_order_events (execution_id,
                           venue_order_id, state, source, venue_record)
                         VALUES ('nope','v','FILLED','PAPER_FILL','{}')""")
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO canonical_management_intents (intent_id,
                           intent_version, review_id, group_id, position_key,
                           sleeve, evidence_state, action, us_market_slug,
                           holding_side, target_qty, target_limit, alternatives,
                           freshness, reason, created_at, content_sha)
                         VALUES ('cmi_000000000000000000000000','v','r','g','p',
                           'INVESTMENT','STALE_ENTRY_TIME_PROBABILITY',
                           'SELL_EXIT','s','LONG',5,'{}','{}','{}','{}',now(),
                           repeat('a',64))""")
        # the rollback refuses while records exist
        await _expect(conn, asyncpg.exceptions.RaiseError, DOWN)
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_both_adapters_parity_and_the_halt_end_to_end():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute("UPDATE small_live_control SET halted = false, "
                           "cleared_by = 'test human' WHERE id = 1")
        if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
            await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
        await conn.execute(
            """INSERT INTO execmirror_snapshots (at, balances, positions,
                 open_orders) VALUES (now(), $1::jsonb, '[]', 0)""",
            json.dumps([{"currency": "USD", "buyingPower": 100}]))
        it = _intent()
        assert await LP.record_decision_intent(conn, it)
        order = _paper_order(it)
        got = await LP.entry_adapters(conn, it, paper_order=order,
                                      paper_result={"ok": True, "order": {
                                          "order_id": "paper_o1"}})
        assert got["paper"] == LP.P_SUBMITTED and got["live"] == LP.S_PROPOSED
        assert got["parity"] == LP.SCALE
        rows = await conn.fetch(
            "SELECT adapter, mode, state, intent_sha FROM "
            "canonical_intent_executions WHERE intent_id = $1 ORDER BY adapter",
            it["intent_id"])
        assert [(r["adapter"], r["mode"]) for r in rows] == [
            ("PAPER", "SIMULATED"), ("SMALL_LIVE", "SHADOW")]
        assert {r["intent_sha"] for r in rows} == {it["content_sha"]}
        assert await conn.fetchval("SELECT count(*) FROM small_live_order_events") == 0
        # a divergent pair halts SMALL LIVE in the same transaction
        it2 = _intent(decision_id="papercg:def")
        await LP.record_decision_intent(conn, it2)
        bad = dict(_paper_order(it2), holding_side="SHORT")
        got2 = await LP.entry_adapters(conn, it2, paper_order=bad,
                                       paper_result={"ok": True})
        assert got2["parity"] == LP.DIVERGENCE
        ctl = await LP.control(conn)
        assert ctl["halted"] and ctl["halt_reason"] == "LOGIC_DIVERGENCE"
        # halted: the next intent gets no live proposal
        it3 = _intent(decision_id="papercg:ghi")
        await LP.record_decision_intent(conn, it3)
        got3 = await LP.entry_adapters(conn, it3, paper_order=_paper_order(it3),
                                       paper_result={"ok": True})
        assert got3["live"] == LP.S_HALTED
        # only a named human clears it
        await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                      "UPDATE small_live_control SET halted = false, "
                      "cleared_by = 'system' WHERE id = 1")
        await _expect(conn, asyncpg.CheckViolationError,
                      "INSERT INTO small_live_control_events (action, actor) "
                      "VALUES ('CLEAR_HALT', 'Xavier')")
        ctl = await LP.clear_halt(conn, actor="Matt (owner)", reason="fixed")
        assert not ctl["halted"]
        rep = await LP.readiness_report(conn, since=0)
        assert rep["logic_divergences"] == 1
        assert rep["recommendation"] == LP.NOT_READY
    finally:
        await tx.rollback()
        await conn.close()
