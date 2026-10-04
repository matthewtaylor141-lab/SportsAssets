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

from sportsassets import canonical_intent as CI
from sportsassets import execmirror as M
from sportsassets import live_approvals as LAP
from sportsassets import live_parity as LP
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "225_live_parity.sql").read_text()
DOWN = (MIG / "rollback" / "225_live_parity.down.sql").read_text()

CG = "PINNACLE_COMPLETED_GAME_PAPER"
SHA = "a" * 40
CG3 = "PINNACLE_COMPLETED_GAME_PAPER_V3"
NOW = 1791130000.0

#: the completed-game policy's ACTIVE parameter row as paper_benchmark.
#: cg_parameters reads it (migration 188's V2: its stored sha is the sha of
#: json.dumps(values, sort_keys=True))
PARAMS_V2 = {
    "policy_key": CG, "source": "ACTIVE_VERSION",
    "version_id": "paperparam:%s:V2" % CG, "version_no": 2,
    "values": {"min_gross_edge_pp": 0.5},
    "params_sha256": CI.params_row_sha({"min_gross_edge_pp": 0.5}),
    "version_source": "OWNER_DECISION",
    "approved_by": "OWNER (account holder): written paper-only authorization",
    "activation_id": "paperact:%s:V2:OWNER_DECISION" % CG,
    "activation_kind": "OWNER_DECISION", "fallback_reason": None}


def _intent(now=NOW, **over):
    kw = dict(
        decision_id="papercg:abc", strategy=CG, strategy_version=CG3,
        evidence={"valuation_id": 101, "book_obs_id": 7, "probability": 0.62},
        opportunity_score={"status": "MEASURED", "opportunity_score": 0.4},
        derek={"status": "MEASURED", "verdict": "ENTER"},
        karen={"state": "NO_OPEN_CHALLENGE_ON_THIS_MARKET_OR_STRATEGY"},
        allie={"status": "MEASURED", "final_allocatable_usd": 2000.0,
               "final_binding": "CAPITAL_REQUIRED",
               "binding_constraint": "CAPITAL_REQUIRED"},
        eddie={"status": "MEASURED", "recommendation": "EXECUTE_NOW",
               "expected_executable_ev_usd": 40.0,
               "expected_net_executable_edge_pp": 3.2,
               "expected_fill_probability": 0.9},
        us_market_slug="aec-nfl-kc-buf-2026-10-04",
        contract={"fixture": "f1", "line": None, "scope": "FULL_GAME"},
        holding_side="LONG", order_intent="ORDER_INTENT_BUY_LONG",
        order_type="MARKETABLE", time_in_force="IOC", limit_price=0.55,
        wire_price=0.55, target_qty=2400, sizing_basis={"rule": "x"},
        created_at=now,
        policy=CI.policy_block(strategy=CG, strategy_version=CG3,
                               params=PARAMS_V2),
        probability={"status": "MEASURED", "value": 0.62,
                     "source": "pinnapi.com/raw-websocket",
                     "source_version": "PINNACLE_DEVIG_V1",
                     "observed_at": now - 5.0, "received_at": now - 4.5,
                     "age_at_decision_s": 5.0, "limit_s": 30.0},
        book={"status": "MEASURED", "obs_id": 7, "observed_at": now - 1.0,
              "age_at_decision_s": 1.0, "max_age_s": 10.0},
        risk_rails={"paper_per_order_cap_usd": 5000, "live": {
            "live_max_order_usd": 25, "live_scale": 1000}})
    kw.update(over)
    return LP.build_decision_intent(**kw)


def _gov(it):
    """Everything approved for THIS intent (pure adapter tests; the
    governance refusals have their own tests)."""
    return {"approved_policy_shas": {it["policy"]["policy_sha"]},
            "approved_gates": set(LAP.GATES)}


def _live(it, *, now=None, governance="OK", **kw):
    kw.setdefault("scale", 1000)
    kw.setdefault("buying_power", 100.0)
    kw.setdefault("max_order_usd", 25)
    return LP.live_entry_proposal(
        it, now=(it["created_at"] + 1.0) if now is None else now,
        governance=_gov(it) if governance == "OK" else governance, **kw)


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
    live = _live(it, scale=1000, buying_power=100.0,
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
    small = _live(_intent(target_qty=300), scale=1000,
                                   buying_power=100.0, max_order_usd=25)
    assert small["exclusion"] == M.BELOW_VENUE_MINIMUM
    cap = _live(_intent(target_qty=60000), scale=1000,
                                 buying_power=100.0, max_order_usd=25)
    assert cap["exclusion"] == M.ABOVE_ORDER_CAP
    acct = _live(_intent(), scale=1000, buying_power=None,
                                  max_order_usd=25)
    assert acct["exclusion"] == "ACCOUNT_STATE_NOT_CURRENT"
    off = _live(_intent(strategy="PINNACLE_EXPLORATION_PAPER",
                                         strategy_version="X"),
                                 scale=1000, buying_power=100.0,
                                 max_order_usd=25)
    assert off["state"] == LP.S_NO_ORDER
    halted = _live(_intent(), scale=1000, buying_power=100.0,
                                    max_order_usd=25, halted=True)
    assert halted["state"] == LP.S_HALTED and halted["params"] is None


# ── parity classification ─────────────────────────────────────────────

def _pair(it, *, paper_state=LP.P_SUBMITTED, refusal=None, live=None,
          order=None):
    order = order or _paper_order(it)
    live = live or _live(it, scale=1000, buying_power=100.0,
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
    # R30A: Allie's final allocation ($2,000) is compared too, so the live
    # rail must sit above it for a scale-1 pair to be identical in every
    # compared field (with a $25 rail it is a capital bound: SCALE)
    live = _live(it, scale=1, buying_power=10_000.0, max_order_usd=5000)
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
    live = _live(it, scale=1000, buying_power=100.0,
                                  max_order_usd=25)
    live = dict(live, live_qty=3)
    res = _pair(it, live=live)
    assert res["parity_state"] == LP.DIVERGENCE and "qty" in res[
        "divergence_fields"]


def test_a_different_intent_sha_is_a_divergence():
    it = _intent()
    other = _intent(target_qty=2600)
    live = _live(other, scale=1000, buying_power=100.0,
                                  max_order_usd=25)
    res = _pair(it, live=live)
    assert res["parity_state"] == LP.DIVERGENCE
    assert "intent_sha" in res["divergence_fields"]


def test_exclusions_and_refusals_are_classified_by_cause():
    it = _intent(target_qty=300)
    assert _pair(it)["parity_state"] == LP.SCALE                # venue min
    it = _intent()
    venue = dict(_live(it, scale=1000, buying_power=100.0,
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
        mi_sql = """INSERT INTO canonical_management_intents (intent_id,
                       intent_version, review_id, group_id, position_key,
                       sleeve, evidence_state, action, us_market_slug,
                       holding_side, target_qty, target_limit, alternatives,
                       alternative_set, chosen, chosen_why, policy,
                       freshness, reason, created_at, content_sha)
                     VALUES ('cmi_000000000000000000000000','v','r','g','p',
                       'INVESTMENT',$1,$2,'s','LONG',5,'{}','{}',$3::jsonb,
                       $2,'{}','{}','{}','{}',now(),repeat('a',64))"""
        full = json.dumps({a: {"status": "EVALUATED"} for a in CI.ALTERNATIVES})
        # a discretionary sale on stale evidence is refused
        await _expect(conn, asyncpg.CheckViolationError, mi_sql,
                      "STALE_ENTRY_TIME_PROBABILITY", "SELL_EXIT", full)
        # R30A: the alternative set must name all eight alternatives ...
        seven = json.loads(full)
        seven.pop("INDIRECT_HEDGE")
        await _expect(conn, asyncpg.CheckViolationError, mi_sql,
                      "FRESH_CURRENT_PROBABILITY", "SELL_EXIT",
                      json.dumps(seven))
        # ... and an UNAVAILABLE alternative must carry its reason
        noreason = dict(json.loads(full), INDIRECT_HEDGE={
            "status": "UNAVAILABLE"})
        await _expect(conn, asyncpg.CheckViolationError, mi_sql,
                      "FRESH_CURRENT_PROBABILITY", "SELL_EXIT",
                      json.dumps(noreason))
        # a decision intent's expires_at is NULL exactly when its window is
        # underivable (the reason in `expiry`); the opportunity id is the
        # five-part key
        for bad in (dict(it, decision_id="papercg:x1",
                         intent_id=LP.decision_intent_id("papercg:x1"),
                         expires_at=None),
                    dict(it, decision_id="papercg:x2",
                         intent_id=LP.decision_intent_id("papercg:x2"),
                         opportunity_id="no-key")):
            sp = conn.transaction()
            await sp.start()
            try:
                with pytest.raises(asyncpg.CheckViolationError):
                    await LP.record_decision_intent(conn, bad)
            finally:
                await sp.rollback()
        # R30A: an owner LIVE approval is append-only and needs a named human
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO live_approvals (subject_kind, subject_id,
                           subject_version, config_sha256, decision,
                           approved_by, statement) VALUES ('LIVE_GATE',
                           'SETTLEMENT_COMPATIBILITY_V1', '1', repeat('a',64),
                           'APPROVE', 'Xavier', 'self-approval')""")
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO live_approvals (subject_kind, subject_id,
                           subject_version, config_sha256, decision,
                           approved_by, statement) VALUES ('LIVE_GATE',
                           'SETTLEMENT_COMPATIBILITY_V1', '1', repeat('a',64),
                           'APPROVE', 'system', 'x')""")
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
        # a recorded production cutover just before these intents (the
        # database stamps recorded_at with its own clock)
        await _record_cutover_row(conn, SHA, "1" * 64)
        t0 = __import__("time").time()
        it = _intent(now=t0)
        assert await LP.record_decision_intent(conn, it)
        order = _paper_order(it)
        got = await LP.entry_adapters(conn, it, paper_order=order,
                                      paper_result={"ok": True, "order": {
                                          "order_id": "paper_o1"}},
                                      now=t0 + 0.5,
                                      stages={"intent_recorded_at": t0 + 0.1,
                                              "paper_submit_at": t0 + 0.2})
        # R30A: no LIVE approval of this policy and no live gate approval is
        # recorded, so LIVE refuses the exposure (SHADOW_EXCLUDED, the
        # governance reason) -- but the would-be order is still compared:
        # identical logic at scale, an expected scale difference
        assert got["paper"] == LP.P_SUBMITTED and got["live"] == LP.S_EXCLUDED
        assert got["parity"] == LP.SCALE
        lrec = await conn.fetchrow(
            "SELECT exclusion, refs FROM canonical_intent_executions "
            " WHERE intent_id = $1 AND adapter = 'SMALL_LIVE'", it["intent_id"])
        assert lrec["exclusion"] == CI.R_POLICY_UNAPPROVED
        assert H.j(lrec["refs"])["new_exposure_refused"] is True
        par = await conn.fetchrow(
            "SELECT comparison FROM live_parity_ledger WHERE intent_id = $1",
            it["intent_id"])
        comp = H.j(par["comparison"])
        assert comp["live_governance_refusals"] == [
            CI.R_POLICY_UNAPPROVED, LP.R_GATE_APPROVAL]
        assert comp["fields"]["allie_final_allocation"]["equal"] is True
        assert comp["fields"]["eddie_estimate"]["equal"] is True
        rows = await conn.fetch(
            "SELECT adapter, mode, state, intent_sha FROM "
            "canonical_intent_executions WHERE intent_id = $1 ORDER BY adapter",
            it["intent_id"])
        assert [(r["adapter"], r["mode"]) for r in rows] == [
            ("PAPER", "SIMULATED"), ("SMALL_LIVE", "SHADOW")]
        assert {r["intent_sha"] for r in rows} == {it["content_sha"]}
        assert await conn.fetchval("SELECT count(*) FROM small_live_order_events") == 0
        # a divergent pair halts SMALL LIVE in the same transaction
        it2 = _intent(decision_id="papercg:def", now=t0)
        await LP.record_decision_intent(conn, it2)
        bad = dict(_paper_order(it2), holding_side="SHORT")
        got2 = await LP.entry_adapters(conn, it2, paper_order=bad,
                                       paper_result={"ok": True},
                                       now=t0 + 0.5)
        assert got2["parity"] == LP.DIVERGENCE
        ctl = await LP.control(conn)
        assert ctl["halted"] and ctl["halt_reason"] == "LOGIC_DIVERGENCE"
        # halted: the next intent gets no live proposal
        it3 = _intent(decision_id="papercg:ghi", now=t0)
        await LP.record_decision_intent(conn, it3)
        got3 = await LP.entry_adapters(conn, it3, paper_order=_paper_order(it3),
                                       paper_result={"ok": True},
                                       now=t0 + 0.5)
        assert got3["live"] == LP.S_HALTED
        # only a named human clears it
        await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                      "UPDATE small_live_control SET halted = false, "
                      "cleared_by = 'system' WHERE id = 1")
        await _expect(conn, asyncpg.CheckViolationError,
                      "INSERT INTO small_live_control_events (action, actor) "
                      "VALUES ('CLEAR_HALT', 'Xavier')")
        rep = await LP.readiness_report(conn, since=0)
        assert rep["logic_divergences"] == 1
        assert "SMALL_LIVE_HALTED_BY_LOGIC_DIVERGENCE" in rep["blockers"]
        assert rep["recommendation"] == LP.NOT_READY
        # R30A: an intent LIVE refused for governance is never activation
        # evidence: the gate names it
        assert any(b.startswith("LIVE_GOVERNANCE_REFUSED:%s"
                                % CI.R_POLICY_UNAPPROVED)
                   for b in rep["blockers"]), rep["blockers"]
        assert rep["governance_gate"] == "FAIL"
        ctl = await LP.clear_halt(conn, actor="Matt (owner)", reason="fixed")
        assert not ctl["halted"]
        # a cleared halt restarts the consecutive sample (the divergence stays
        # in the ledger, before the new window)
        rep = await LP.readiness_report(conn, since=0)
        assert rep["logic_divergences"] == 0 and rep["candidate_count"] == 0
        assert rep["recommendation"] == LP.NOT_READY
    finally:
        await tx.rollback()
        await conn.close()


# ── Allie: the capital-efficiency methodology DETERMINES the allocation ──

from sportsassets import allie_capital as AC                     # noqa: E402

ALLIE_FIELDS = (
    "expected_executable_net_profit_usd", "expected_capital_required_usd",
    "expected_hours_to_capital_release", "expected_capital_hours",
    "expected_profit_per_capital_hour", "profit_per_1000_per_hour",
    "capacity_ceiling_usd", "correlation_concentration", "opportunity_cost",
    "allie_proposed_allocation_usd", "hard_risk_rail_cap_usd",
    "final_allocatable_usd", "confidence", "evidence", "binding_constraint")


def _alloc(**over):
    kw = dict(eddie_ev_usd=60.0, modelled_net_usd=70.0,
              capital_required_usd=1000.0, event_start_at=1000.0 + 2 * 3600,
              decided_at=1000.0, median_lag_s=3600.0, lag_n=40,
              eddie_max_qty=5000, limit_price=0.5, displayed_depth_qty=8000,
              fixture_open_groups=0, fixture_open_usd=0.0,
              book_open_usd=10_000.0, idle_capital_usd=400.0,
              recent_adjusted_ppch=[0.001 * i for i in range(1, 21)],
              paper_rail_usd=5000.0, live_rail_usd=25.0, live_scale=1000.0,
              order_cost_usd=1000.0)
    kw.update(over)
    return AC.allocate(**kw)


def test_allie_carries_every_capital_efficiency_field():
    a = _alloc()
    for f in ALLIE_FIELDS:
        assert f in a, f
    assert a["status"] == "MEASURED" and a["version"] == AC.VERSION
    assert a["expected_executable_net_profit_usd"] == 60.0
    assert a["net_basis"].startswith("EDDIE")
    assert a["expected_capital_required_usd"] == 1000.0
    assert a["expected_hours_to_capital_release"] == 3.0     # 2 h + 1 h lag
    assert a["expected_capital_hours"] == 3000.0
    assert a["expected_profit_per_capital_hour"] == pytest.approx(0.02)
    assert a["profit_per_1000_per_hour"] == pytest.approx(20.0)
    assert a["capacity_ceiling_usd"] == 2500.0               # 5000 x 0.50
    # capital is scarce (idle 400 < 1,000): the hurdle is the 75th pct of
    # recent candidates (0.01525) and 0.02 beats it
    oc = a["opportunity_cost"]
    assert oc["basis"].startswith("CAPITAL_SCARCE")
    assert oc["per_capital_hour"] == pytest.approx(0.01525)
    assert oc["candidate_beats_it"] is True
    assert a["allie_proposed_allocation_usd"] == 400.0       # idle capital
    assert a["binding_constraint"] == "IDLE_CAPITAL"
    assert a["final_allocatable_usd"] == 400.0
    assert a["live_rail"]["paper_equivalent_usd"] == 25000.0
    assert a["order_vs_allocation"]["verdict"] == "ORDER_EXCEEDS_ALLOCATION"
    assert a["confidence"] == AC.HIGH
    assert a["authority"] == "SHADOW_PENDING_OWNER_APPROVAL"


def test_profit_per_capital_hour_against_opportunity_cost_decides_funding():
    funded = _alloc(eddie_ev_usd=60.0)                         # 0.020 / ch
    starved = _alloc(eddie_ev_usd=30.0)                        # 0.010 / ch
    assert funded["allie_proposed_allocation_usd"] > 0
    assert starved["allie_proposed_allocation_usd"] == 0.0
    assert starved["binding_constraint"] == \
        "PROFIT_PER_CAPITAL_HOUR_NOT_ABOVE_OPPORTUNITY_COST"
    # same net, but the capital is locked 4x longer: capital-hours rise,
    # profit per capital-hour falls below the hurdle -> NOT funded
    slow = _alloc(event_start_at=1000.0 + 11 * 3600)           # 12 h hold
    assert slow["expected_capital_hours"] == 12000.0
    assert slow["allie_proposed_allocation_usd"] == 0.0
    # more capital for the same net: also below the hurdle
    heavy = _alloc(capital_required_usd=4000.0)
    assert heavy["allie_proposed_allocation_usd"] == 0.0


def test_idle_capital_makes_the_opportunity_cost_idle_cash():
    a = _alloc(idle_capital_usd=100_000.0, eddie_ev_usd=10.0)
    assert a["opportunity_cost"]["per_capital_hour"] == 0.0
    assert a["opportunity_cost"]["basis"].startswith("IDLE_CAPITAL")
    assert a["allie_proposed_allocation_usd"] == 1000.0       # capital req.
    assert a["binding_constraint"] == "CAPITAL_REQUIRED"


def test_capacity_concentration_and_the_rail_bind_the_amount():
    rich = dict(idle_capital_usd=100_000.0)
    cap = _alloc(eddie_max_qty=600, **rich)                     # $300 depth
    assert cap["binding_constraint"] == "CAPACITY_CEILING"
    assert cap["allie_proposed_allocation_usd"] == 300.0
    fx = _alloc(fixture_open_usd=AC.FIXTURE_CAP_USD - 200, **rich)
    assert fx["binding_constraint"] == "FIXTURE_HEADROOM"
    assert fx["allie_proposed_allocation_usd"] == 200.0
    rail = _alloc(paper_rail_usd=250.0, **rich)
    assert rail["allie_proposed_allocation_usd"] == 1000.0
    assert rail["final_allocatable_usd"] == 250.0
    assert rail["final_binding"] == "HARD_RISK_RAIL"
    # correlation: open groups on the fixture haircut the efficiency until
    # it no longer beats a scarce-capital hurdle
    corr = _alloc(fixture_open_groups=2)
    assert corr["correlation_concentration"]["haircut"] == 0.5
    assert corr["correlation_concentration"][
        "adjusted_profit_per_capital_hour"] == pytest.approx(0.01)
    assert corr["allie_proposed_allocation_usd"] == 0.0


def test_unmeasured_efficiency_allocates_nothing_and_says_why():
    a = _alloc(lag_n=2)
    assert a["status"] == "UNAVAILABLE"
    assert a["allie_proposed_allocation_usd"] == 0.0
    assert a["binding_constraint"].startswith("UNMEASURED_CAPITAL_EFFICIENCY")
    assert "expected_hours_to_capital_release" in a["unmeasured"]
    assert a["confidence"] == AC.UNMEASURED
    b = _alloc(eddie_ev_usd=None)
    assert b["net_basis"].startswith("DECISION_MODELLED_NET")
    assert b["confidence"] != AC.HIGH
    c = _alloc(eddie_ev_usd=-5.0)
    assert c["binding_constraint"] == "NON_POSITIVE_EXPECTED_EXECUTABLE_NET"


# ── the production cutover: one row per release, every condition verified ──


async def _record_cutover_row(conn, sha, logic_hash, *, by="release engineer"):
    iid = await conn.fetchval(
        "INSERT INTO live_parity_hook_installs (process, commit_sha, hooks)"
        " VALUES ('api', $1, $2) RETURNING install_id", sha,
        list(LP.HOOK_NAMES))
    return await conn.fetchrow(
        "INSERT INTO live_parity_cutover (release_sha, api_sha, workers_sha,"
        " migrations, decision_logic_hash, decision_logic_files,"
        " hook_install_id, small_live_mode, small_live_halted,"
        " capital_activated, evidence, recorded_by)"
        " VALUES ($1, $1, $1, ARRAY['225','226'], $2, '{}'::jsonb, $3,"
        " 'SHADOW', false, false, '{}', $4) RETURNING *",
        sha, logic_hash, iid, by)


async def _cutover_world(conn, *, sha=SHA, workers=SHA, migrations=True,
                         hooks=True, halted=False, stopped=True,
                         venue_order=False):
    await conn.execute("UPDATE small_live_control SET halted = false, "
                       "cleared_by = 'test human' WHERE id = 1")
    # "no venue order ever": a shared test database holds the FakeVenue
    # orders other files' lane tests leave behind (they TRUNCATE at setup,
    # not at teardown), so this world states the condition explicitly --
    # inside the caller's rolled-back transaction, nothing persists
    await conn.execute(
        "TRUNCATE smalllive_reviews, smalllive_handoffs, "
        "smalllive_reconciliations, execmirror_fills, execmirror_events, "
        "execmirror_snapshots, execmirror_orders")
    if venue_order:
        await conn.execute(
            "INSERT INTO execmirror_orders (mirror_id, role, us_market_slug, "
            " intent, order_type, tif, state, venue_order_id) VALUES "
            " ('lp-cutover-test', 'ENTRY', 'lp-cutover-test', "
            " 'ORDER_INTENT_BUY_LONG', 'LIMIT', 'IOC', 'OPEN', 'v-1')")
    if halted:
        await conn.execute(
            "UPDATE small_live_control SET halted = true, halted_at = now(), "
            "halt_reason = 'LOGIC_DIVERGENCE' WHERE id = 1")
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ('workers_boot', $1::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        json.dumps({"commit_sha": workers}))
    if migrations:
        for v in ("226_agent_work_queue.sql",):
            await conn.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1) "
                "ON CONFLICT DO NOTHING", v)
    if hooks:
        await conn.execute(
            "INSERT INTO live_parity_hook_installs (process, commit_sha, hooks)"
            " VALUES ('api', $1, $2)", sha, list(LP.HOOK_NAMES))
    if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
        await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
    await conn.execute("UPDATE execmirror_control SET stopped = $1", stopped)


@pg
@pytest.mark.asyncio
async def test_the_cutover_is_refused_until_every_condition_holds(monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        if await conn.fetchval("SELECT to_regclass('agent_work_requests')") is None:
            await conn.execute("CREATE TABLE agent_work_requests (x int)")
        await conn.execute("UPDATE execmirror_control SET enabled = true")
        cases = [
            ({"workers": "b" * 40}, "WORKERS_RUN_THE_RELEASE_SHA"),
            ({"hooks": False}, "HOOKS_INSTALLED_ON_THE_RELEASE_SHA"),
            ({"halted": True}, "SMALL_LIVE_NOT_HALTED"),
            ({"stopped": False}, "NO_CAPITAL_ACTIVATED"),
            ({"venue_order": True}, "NO_CAPITAL_ACTIVATED")]
        for kw, failing in cases:
            sp = conn.transaction()
            await sp.start()
            await _cutover_world(conn, **kw)
            got = await LP.record_cutover(conn, release_sha=SHA,
                                          recorded_by="release engineer",
                                          api_sha=SHA,
                                          hooks_here=list(LP.HOOK_NAMES))
            assert got["recorded"] is False and failing in got["refused"], got
            assert await LP.production_cutover(conn) is None
            await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        await _cutover_world(conn)
        wrong_api = await LP.record_cutover(conn, release_sha=SHA,
                                            recorded_by="release engineer",
                                            api_sha="c" * 40,
                                            hooks_here=list(LP.HOOK_NAMES))
        assert "API_RUNS_THE_RELEASE_SHA" in wrong_api["refused"]
        no_hooks_here = await LP.record_cutover(conn, release_sha=SHA,
                                                recorded_by="release engineer",
                                                api_sha=SHA, hooks_here=[])
        assert "HOOKS_INSTALLED_IN_THIS_PROCESS" in no_hooks_here["refused"]
        # R30A: a system / agent actor never records a cutover
        for robot in ("system", "Xavier", "agent:release", ""):
            r = await LP.record_cutover(conn, release_sha=SHA,
                                        recorded_by=robot, api_sha=SHA,
                                        hooks_here=list(LP.HOOK_NAMES))
            assert "RECORDED_BY_IS_A_NAMED_HUMAN" in r["refused"], robot
        ok = await LP.record_cutover(conn, release_sha=SHA,
                                     recorded_by="release engineer",
                                     api_sha=SHA,
                                     hooks_here=list(LP.HOOK_NAMES))
        assert ok["recorded"] is True, ok
        cut = ok["cutover"]
        assert cut["release_sha"] == cut["api_sha"] == cut["workers_sha"] == SHA
        assert cut["small_live_mode"] == "SHADOW"
        assert cut["capital_activated"] is False
        assert set(["225", "226"]) <= set(cut["migrations"])
        # the decision-logic hash of THIS build, over the pinned files
        logic = LP.decision_logic_hash()
        assert cut["decision_logic_hash"] == logic["hash"]
        assert set(H.j(cut["decision_logic_files"])) == set(
            LP.DECISION_LOGIC_FILES)
        assert ok["restarts_forward_window"] is True      # the first release
        again = await LP.record_cutover(conn, release_sha=SHA,
                                        recorded_by="someone else",
                                        api_sha=SHA,
                                        hooks_here=list(LP.HOOK_NAMES))
        assert again["recorded"] is False and again["already"] is True
        assert again["cutover"]["recorded_at"] == cut["recorded_at"]
        await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                      "UPDATE live_parity_cutover SET recorded_by = 'x'")
        await _expect(conn, asyncpg.exceptions.RestrictViolationError,
                      "DELETE FROM live_parity_cutover")
        # the parity gate starts AT the cutover: earlier rows never count
        rep = await LP.readiness_report(conn, since=0)
        assert rep["since"] >= cut["recorded_at"].timestamp() - 1e-6
        first_eff = await LP.production_cutover(conn)
        assert first_eff["cutover_id"] == cut["cutover_id"]

        # ── R30A: A RELEASE THAT CHANGES NO DECISION LOGIC ──────────────
        sha2 = "d" * 40
        await _cutover_world(conn, sha=sha2, workers=sha2)
        same = await LP.record_cutover(conn, release_sha=sha2,
                                       recorded_by="release engineer",
                                       api_sha=sha2,
                                       hooks_here=list(LP.HOOK_NAMES))
        assert same["recorded"] is True, same
        assert same["restarts_forward_window"] is False
        eff = await LP.production_cutover(conn)
        assert eff["cutover_id"] == cut["cutover_id"]    # the sample stands
        assert eff["releases_recorded"] == 2
        latest = await LP.latest_release_cutover(conn)
        assert latest["release_sha"] == sha2

        # ── ... AND ONE THAT DOES ────────────────────────────────────────
        sha3 = "e" * 40
        await _cutover_world(conn, sha=sha3, workers=sha3)
        changed = dict(logic, hash="f" * 64)
        monkeypatch.setattr(LP, "decision_logic_hash", lambda root=None: changed)
        moved = await LP.record_cutover(conn, release_sha=sha3,
                                        recorded_by="release engineer",
                                        api_sha=sha3,
                                        hooks_here=list(LP.HOOK_NAMES))
        assert moved["recorded"] is True, moved
        assert moved["restarts_forward_window"] is True
        eff = await LP.production_cutover(conn)
        assert eff["release_sha"] == sha3                # the sample restarts
        rep = await LP.readiness_report(conn, since=0)
        assert rep["since"] >= eff["recorded_at"].timestamp() - 1e-6
        assert rep["latest_release_cutover"]["release_sha"] == sha3
        # the pure rule is the view's rule
        rows = await conn.fetch("SELECT * FROM live_parity_cutover")
        assert LP.effective_cutover_of(rows)["cutover_id"] == eff["cutover_id"]
        # recorded_at is the DATABASE's clock, never the caller's
        stamped = await _record_cutover_row(conn, "9" * 40, "2" * 64)
        assert abs(stamped["recorded_at"].timestamp()
                   - __import__("time").time()) < 300
        await sp.rollback()
        # no cutover -> no forward sample at all
        rep = await LP.readiness_report(conn)
        assert "NO_PRODUCTION_CUTOVER_RECORDED" in rep["blockers"]
        assert rep["recommendation"] == LP.NOT_READY
    finally:
        await tx.rollback()
        await conn.close()


def test_the_effective_cutover_is_the_latest_logic_change():
    rows = [{"cutover_id": 1, "recorded_at": 1.0, "decision_logic_hash": "a"},
            {"cutover_id": 2, "recorded_at": 2.0, "decision_logic_hash": "a"},
            {"cutover_id": 3, "recorded_at": 3.0, "decision_logic_hash": "b"},
            {"cutover_id": 4, "recorded_at": 4.0, "decision_logic_hash": "b"}]
    assert LP.effective_cutover_of(rows)["cutover_id"] == 3
    assert LP.effective_cutover_of(rows[:2])["cutover_id"] == 1
    # a revert to an earlier hash is a change too
    rows.append({"cutover_id": 5, "recorded_at": 5.0,
                 "decision_logic_hash": "a"})
    assert LP.effective_cutover_of(rows)["cutover_id"] == 5
    assert LP.effective_cutover_of([]) is None


def test_the_decision_logic_hash_covers_every_pinned_file(tmp_path):
    got = LP.decision_logic_hash()
    assert got["hash"] and not got["missing"]
    assert set(got["files"]) == set(LP.DECISION_LOGIC_FILES)
    # a missing file is never a hash
    assert LP.decision_logic_hash(root=tmp_path)["hash"] is None
    # one changed byte in one file is a different hash
    import shutil
    base = pathlib.Path(LP.__file__).resolve().parent
    for rel in LP.DECISION_LOGIC_FILES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(base / rel, tmp_path / rel)
    assert LP.decision_logic_hash(root=tmp_path)["hash"] == got["hash"]
    f = tmp_path / LP.DECISION_LOGIC_FILES[0]
    f.write_bytes(f.read_bytes() + b"\n# changed\n")
    assert LP.decision_logic_hash(root=tmp_path)["hash"] != got["hash"]


def test_the_sleeve_map_is_the_paper_sleeve_classifiers():
    from sportsassets import bettor_paper_sleeves as BPS
    from sportsassets import canonical_intent as CI
    assert CI.STRATEGY_SLEEVE == BPS.STRATEGY_SLEEVE
    assert (CI.INVESTMENT, CI.TRAINING, CI.BENCHMARK, CI.UNCLASSIFIED) == (
        BPS.INVESTMENT, BPS.TRAINING, BPS.BENCHMARK, BPS.UNCLASSIFIED)
