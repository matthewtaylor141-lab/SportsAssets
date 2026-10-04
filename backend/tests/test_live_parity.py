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
    # R30A (section 8; review finding 1): the same sale at scale -- but the
    # paper review evaluated no INDIRECT_HEDGE and LIVE consumes its set, so
    # the alternative is evaluated on NEITHER side and exact parity is not
    # claimed. Was EXPECTED_SCALE_DIFFERENCE before the alternative set was
    # part of the classification; the scale classification is kept beside it.
    assert res["parity_state"] == LP.INCOMPLETE, res
    assert res["comparison"]["state_if_alternatives_were_complete"] == LP.SCALE
    assert "INDIRECT_HEDGE" in res["comparison"]["why_not_exact"]
    assert res["comparison"]["exact_parity_claimed"] is False
    assert res["divergence_fields"] == []


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
    # R30A: identical protection at scale, alternative set incomplete on both
    # sides (no indirect-hedge search) -> INCOMPLETE_COMPARISON, never a
    # claimed match (was EXPECTED_SCALE_DIFFERENCE before section 8)
    assert res["parity_state"] == LP.INCOMPLETE, res
    assert res["comparison"]["state_if_alternatives_were_complete"] == LP.SCALE


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


# ═════════════════════════════════════════════════════════════════════
# R30A · THE COMPLETE INTENT, ITS VALIDITY WINDOW, THE LIVE POLICY, ALLIE /
# EDDIE / ALTERNATIVES IN PARITY, THE LATENCY CHAIN, THE CUTOVER ENDPOINT
# ═════════════════════════════════════════════════════════════════════

def test_the_opportunity_id_is_fixture_slug_side_line_scope():
    k = CI.opportunity_key(fixture="f1", us_market_slug="aec-x",
                           holding_side="LONG", line=3.5, scope="FULL_GAME")
    assert k == "f1|aec-x|LONG|3.5|FULL_GAME"
    # one line, written three ways, is one opportunity
    for line in (Decimal("3.50"), 3.50, Decimal("3.5")):
        assert CI.opportunity_key(fixture="f1", us_market_slug="aec-x",
                                  holding_side="LONG", line=line,
                                  scope="FULL_GAME") == k
    # missing parts are stated as empty, a '|' inside a part never splits it
    assert CI.opportunity_key(fixture=None, us_market_slug="a|b",
                              holding_side="SHORT", line=None,
                              scope=None) == "|a/b|SHORT||"
    it = _intent(contract={"fixture": "f1", "line": Decimal("-1.5"),
                           "scope": "FULL_GAME"})
    assert it["opportunity_id"] == \
        "f1|aec-nfl-kc-buf-2026-10-04|LONG|-1.5|FULL_GAME"
    with pytest.raises(ValueError):
        _intent(opportunity_id="not-a-key")


@pytest.mark.parametrize("field,value", [
    ("opportunity_id", "f1|other|LONG||FULL_GAME"),
    ("policy", {"policy_sha": "0" * 64}),
    ("probability", {"status": "MEASURED", "value": 0.7}),
    ("book", {"status": "MEASURED", "obs_id": 8}),
    ("risk_rails", {"live": {"live_max_order_usd": 26}}),
    ("binding_constraints", {"allie_final_binding": "OTHER"}),
    ("evidence_refs", [{"kind": "paper_decisions", "id": "x"}]),
    ("latency_stages", {"decision_start_at": 1.0}),
    ("expires_at", NOW + 99.0),
    ("expiry", {"status": "DERIVED", "expires_at": NOW + 99.0}),
    ("allie", {"status": "MEASURED", "final_allocatable_usd": 1.0}),
    ("eddie", {"status": "MEASURED", "recommendation": "WAIT"}),
    ("karen", {"state": "OPEN_CHALLENGE"}),
    ("opportunity_score", {"status": "MEASURED", "opportunity_score": 0.9}),
])
def test_every_r30a_field_is_inside_the_sha(field, value):
    it = _intent()
    assert LP.verify_intent(it)
    assert not LP.verify_intent(dict(it, **{field: value})), field


def test_the_intent_records_its_policy_probability_book_rails_and_refs():
    it = _intent(evidence_refs=[{"kind": "paper_decisions", "id": "d"}],
                 latency_stages={"decision_start_at": NOW})
    assert it["intent_version"] == CI.INTENT_VERSION == \
        "CANONICAL_DECISION_INTENT_V2"
    pol = it["policy"]
    assert pol["strategy_version"] == CG3
    assert pol["parameters_version_id"] == PARAMS_V2["version_id"]
    assert pol["row_sha_matches"] is True and pol["fallback"] is None
    assert it["probability"]["limit_s"] == 30.0
    assert it["book"]["obs_id"] == 7
    assert it["risk_rails"]["live"] == {"live_max_order_usd": 25,
                                        "live_scale": 1000}
    bc = it["binding_constraints"]
    assert bc["allie_binding_constraint"] == "CAPITAL_REQUIRED"
    assert bc["allie_final_binding"] == "CAPITAL_REQUIRED"
    assert it["evidence_refs"] == [{"kind": "paper_decisions", "id": "d"}]
    assert it["latency_stages"] == {"decision_start_at": NOW}
    # an unrecorded block is UNAVAILABLE with its reason, never a zero
    bare = _intent(probability=None, book=None, risk_rails=None)
    for k in ("probability", "book", "risk_rails"):
        assert bare[k]["status"] == "UNAVAILABLE" and bare[k]["why"], k


def test_the_validity_window_is_the_earlier_of_the_two_admission_rules():
    e = CI.decision_expiry(probability_observed_at=NOW - 5,
                           probability_limit_s=30.0,
                           book_observed_at=NOW - 1, book_max_age_s=10.0)
    assert e["status"] == "DERIVED" and e["binding_term"] == "BOOK_AGE"
    assert e["expires_at"] == pytest.approx(NOW + 9.0)
    e = CI.decision_expiry(probability_observed_at=NOW - 25,
                           probability_limit_s=30.0,
                           book_observed_at=NOW - 1, book_max_age_s=10.0)
    assert e["binding_term"] == "PROBABILITY_FRESHNESS"
    assert e["expires_at"] == pytest.approx(NOW + 5.0)
    for miss in ({"probability_observed_at": None},
                 {"probability_limit_s": None}, {"book_observed_at": None},
                 {"book_max_age_s": None}):
        kw = dict(probability_observed_at=NOW - 5, probability_limit_s=30.0,
                  book_observed_at=NOW - 1, book_max_age_s=10.0)
        kw.update(miss)
        u = CI.decision_expiry(**kw)
        assert u["status"] == "UNAVAILABLE" and u["expires_at"] is None
        assert u["why"].startswith("EXPIRY_UNDERIVABLE_MISSING:")
    it = _intent()
    assert it["expires_at"] == pytest.approx(NOW + 9.0)
    assert CI.intent_expiry_refusal(it, now=it["expires_at"]) is None
    assert CI.intent_expiry_refusal(it, now=it["expires_at"] + 0.001) == \
        CI.R_INTENT_EXPIRED
    assert CI.intent_expiry_refusal(it, now=None) == CI.R_INTENT_EXPIRED
    assert CI.intent_expiry_refusal(dict(it, expires_at=None), now=NOW) == \
        CI.R_EXPIRY_UNAVAILABLE
    # the window is never longer than the 30 s probability rule
    assert it["expires_at"] <= it["probability"]["observed_at"] + 30.0


def test_both_adapters_refuse_an_expired_intent():
    it = _intent()
    late = it["expires_at"] + 1.0
    live = _live(it, now=late)
    assert live["state"] == LP.S_EXCLUDED
    assert live["exclusion"] == CI.R_INTENT_EXPIRED
    assert live["plan_state"] == "PLANNED"        # the logic is still built
    res = _pair(it, live=live)
    assert res["parity_state"] == LP.VENUE_DIFF
    assert res["comparison"]["live_expiry_refusal"] == CI.R_INTENT_EXPIRED
    no_window = _intent(book=None)
    assert _live(no_window)["exclusion"] == CI.R_EXPIRY_UNAVAILABLE
    # the PAPER adapter's refusal of an expired intent is a timing fact too
    res = _pair(it, paper_state=LP.P_REFUSED, refusal=CI.R_INTENT_EXPIRED)
    assert res["parity_state"] == LP.VENUE_DIFF


def test_the_live_policy_fails_closed_and_the_paper_fallback_is_labelled():
    ok = CI.policy_block(strategy=CG, strategy_version=CG3, params=PARAMS_V2)
    v = CI.live_policy_verdict(ok, approved_policy_shas={ok["policy_sha"]})
    assert v["admissible"] is True and v["refusal"] is None
    assert [c["check"] for c in v["checks"]] == [
        "policy_row_present_and_readable", "policy_row_sha_matches",
        "policy_version_approved_for_live"]
    # PAPER MAY FALL BACK -- explicitly and labelled -- LIVE never does
    fb = CI.policy_block(strategy=CG, strategy_version=CG3, params=dict(
        PARAMS_V2, source=CI.POLICY_FALLBACK,
        fallback_reason="PARAMETER_READ_FAILED:TimeoutError"))
    assert fb["fallback"] == {
        "explicit": True, "label": "PAPER_SHIPPED_RESEARCH_DEFAULT_FALLBACK",
        "reason": "PARAMETER_READ_FAILED:TimeoutError",
        "live_admissible": False}
    assert CI.live_policy_verdict(fb, approved_policy_shas={
        fb["policy_sha"]})["refusal"] == CI.R_POLICY_MISSING
    none = CI.policy_block(strategy=CG, strategy_version=CG3, params=None)
    assert none["parameters_source"] == CI.POLICY_CODE_CONSTANT
    assert CI.live_policy_verdict(none, approved_policy_shas={
        none["policy_sha"]})["refusal"] == CI.R_POLICY_MISSING
    assert CI.live_policy_verdict(None)["refusal"] == CI.R_POLICY_MISSING
    bad = CI.policy_block(strategy=CG, strategy_version=CG3,
                          params=dict(PARAMS_V2, params_sha256="0" * 64))
    assert bad["row_sha_matches"] is False
    assert CI.live_policy_verdict(bad, approved_policy_shas={
        bad["policy_sha"]})["refusal"] == CI.R_POLICY_SHA
    # the shipped V2 row's paper-only authorization is NOT a LIVE approval
    assert CI.live_policy_verdict(ok)["refusal"] == CI.R_POLICY_UNAPPROVED
    robot = CI.policy_block(strategy=CG, strategy_version=CG3,
                            params=dict(PARAMS_V2, approved_by="system"))
    assert CI.live_policy_verdict(robot, approved_policy_shas={
        robot["policy_sha"]})["refusal"] == CI.R_POLICY_UNAPPROVED
    # the policy sha names strategy version + parameter version + values
    for other in (CI.policy_block(strategy=CG, strategy_version="V9",
                                  params=PARAMS_V2),
                  CI.policy_block(strategy=CG, strategy_version=CG3,
                                  params=dict(PARAMS_V2, version_id="v3")),
                  CI.policy_block(strategy=CG, strategy_version=CG3,
                                  params=dict(PARAMS_V2, values={
                                      "min_gross_edge_pp": 0.6}))):
        assert other["policy_sha"] != ok["policy_sha"]


def test_a_governance_refusal_is_shadow_excluded_but_still_measured():
    it = _intent()
    for gov, first in ((None, CI.R_POLICY_UNAPPROVED),
                       ({"approved_policy_shas": {it["policy"]["policy_sha"]},
                         "approved_gates": set()}, LP.R_GATE_APPROVAL)):
        live = _live(it, governance=gov)
        assert live["state"] == LP.S_EXCLUDED and live["exclusion"] == first
        assert live["governance"]["new_exposure_refused"] is True
        assert live["plan_state"] == "PLANNED" and live["live_qty"] == 2
        res = _pair(it, live=live)
        # the logic is compared on the would-be order: identical -> SCALE
        assert res["parity_state"] == LP.SCALE, res
        assert first in res["comparison"]["live_governance_refusals"]
        r = LP.readiness([{"intent_kind": "DECISION", "sleeve": "INVESTMENT",
                           "parity_state": res["parity_state"],
                           "comparison": res["comparison"]}] * 30
                         + [_row("MANAGEMENT")] * 30, halted=False,
                         profitability={"profitability_verdict":
                                        "SUPPORTED_BY_FORWARD_EVIDENCE"})
        assert r["recommendation"] == LP.NOT_READY
        assert "LIVE_GOVERNANCE_REFUSED:%s:30" % first in r["blockers"]
        assert r["governance_gate"] == "FAIL" and r["parity_gate"] == "PASS"


def test_allie_and_eddie_are_compared_at_capital_scale():
    it = _intent()
    res = _pair(it)
    f = res["comparison"]["fields"]
    assert f["allie_final_allocation"]["equal"] is True
    assert f["allie_final_allocation"]["scale_difference"] is True
    assert f["allie_final_allocation"]["live"] == "2"       # 2000 / 1000
    assert f["eddie_estimate"]["equal"] is True
    assert f["eddie_estimate"]["live"]["expected_executable_ev_usd"] == "0.04"
    assert res["parity_state"] == LP.SCALE
    # a live allocation bounded by the $25 live rail is a capital bound
    big = _intent(allie={"status": "MEASURED", "final_allocatable_usd": 60000,
                         "final_binding": "CAPACITY",
                         "binding_constraint": "CAPACITY"})
    rb = _pair(big)
    fa = rb["comparison"]["fields"]["allie_final_allocation"]
    # R30A review: the live bound is LIVE CAPITAL STATE -- the rail or the
    # live buying power -- so the basis was renamed from LIVE_RAIL_BOUND
    assert fa["basis"] == "LIVE_CAPITAL_BOUND" and fa["equal"] is True
    assert fa["live_capital_bound_by"] == "LIVE_RAIL"
    assert rb["parity_state"] == LP.SCALE
    # the live account's buying power below the rail bounds it instead
    poor = _pair(big, live=_live(big, buying_power=10.0))
    fp = poor["comparison"]["fields"]["allie_final_allocation"]
    assert fp["live"] == "10" and fp["live_capital_bound_by"] == \
        "LIVE_BUYING_POWER" and fp["equal"] is True
    # WHAT THIS COMPARISON IS: a scale-consistency check of two views of one
    # intent, never an independent LIVE evaluation -- stated on every row
    # and on the readiness report
    assert fa["comparison_kind"] == LP.ALLIE_EDDIE_BASIS["kind"] == \
        "SCALE_CONSISTENCY_CHECK_NOT_AN_INDEPENDENT_EVALUATION"
    assert rb["comparison"]["fields"]["eddie_estimate"]["comparison_kind"] \
        == LP.ALLIE_EDDIE_BASIS["kind"]
    assert "cannot_detect" in LP.ALLIE_EDDIE_BASIS
    assert LP.readiness([], halted=False)["allie_eddie_basis"] == \
        LP.ALLIE_EDDIE_BASIS


@pytest.mark.parametrize("side,key,value,field", [
    ("allie", "final_allocatable_usd", "3", "allie_final_allocation"),
    ("allie", "final_binding", "OTHER", "allie_final_allocation"),
    ("eddie", "recommendation", "WAIT", "eddie_estimate"),
    ("eddie", "expected_executable_ev_usd", "0.05", "eddie_estimate"),
    ("eddie", "expected_fill_probability", "0.5", "eddie_estimate")])
def test_an_allie_or_eddie_difference_beyond_scale_is_a_divergence(
        side, key, value, field):
    it = _intent()
    live = _live(it)
    ae = json.loads(json.dumps(live["requested"]["allie_eddie"]))
    ae[side][key] = value
    live = dict(live, requested=dict(live["requested"], allie_eddie=ae))
    res = _pair(it, live=live)
    assert res["parity_state"] == LP.DIVERGENCE
    assert field in res["divergence_fields"]


def _alts():
    return {"candidates": [
        {"action": "HOLD", "qty": 2400, "value_usd": 1488.0,
         "expected_net_usd": 168.0, "ev_basis": "FRESH", "ev_is_current": True},
        {"action": "EXIT", "qty": 2400, "value_usd": 1440.0,
         "expected_net_usd": 120.0, "fees_usd": 10.0,
         "walk": {"worst_price": 0.60}}],
        "not_rankable": [{"action": "REDUCE", "blocker": "NO_BIDS_FOR_REDUCE"}]}


def test_every_management_alternative_is_valued_or_unavailable_with_a_reason():
    decided = LP.management_action(
        chosen="HOLD", fresh=True, stale=False, p_missing=False,
        protection_ok=True, standing_live=False, candidate=None,
        protective={"ok": True, "price": 0.40}, open_qty=2400)
    s = CI.management_alternatives(
        alts=_alts(), decided=decided, mechanical_selection="HOLD",
        standing_live=False, protective={"ok": True, "price": 0.40,
                                         "floor_usd": 960.0},
        open_qty=2400, reallocate={"position_efficiency": 0.1,
                                   "alternative_efficiency": 0.2,
                                   "advantage": 0.1, "recommended": True,
                                   "position_ev_from_here_usd": 168.0,
                                   "best_opportunity": {"decision_id": "d9"}})
    assert set(s) == set(CI.ALTERNATIVES) and len(s) == 8
    for name, e in s.items():
        assert e["status"] in (CI.EVALUATED, "UNAVAILABLE"), name
        if e["status"] == "UNAVAILABLE":
            assert e["why"], name                 # a reason, never a zero
    assert s["HOLD"]["status"] == CI.EVALUATED and s["HOLD"]["value_usd"] == 1488.0
    assert s["SELL_EXIT"]["value_usd"] == 1440.0
    assert s["SELL_REDUCE"]["why"] == "NO_BIDS_FOR_REDUCE"
    assert s["CANCEL_PROTECTION_BEFORE_EXIT"]["why"] == \
        "NO_STANDING_PROTECTION_TO_CANCEL"
    assert s["MAINTAIN_STANDING_PROTECTION"]["status"] == CI.EVALUATED
    assert s["MAINTAIN_STANDING_PROTECTION"]["floor_is_realized"] is False
    assert s["INDIRECT_HEDGE"]["status"] == "UNAVAILABLE"
    assert s["REALLOCATE"]["status"] == CI.EVALUATED
    assert s["REALLOCATE"]["mode"] == "SHADOW"
    assert s["NO_ORDER"]["value_usd"] == 1488.0
    chosen = [k for k, e in s.items() if e["chosen"]]
    assert chosen == [decided["action"]] == ["MAINTAIN_STANDING_PROTECTION"]
    mi = LP.build_management_intent(
        review_id="paperrev:alts", group_id="g1", position_key="pk",
        strategy=CG, valuation={"valuation_id": 9, "valuation_hash": "h"},
        evidence_state="FRESH_CURRENT_PROBABILITY", recommendation="HOLD",
        mechanical_selection="HOLD", decided=decided,
        us_market_slug="aec-nfl-kc-buf-2026-10-04", holding_side="LONG",
        alternatives=_alts(), reason={"selection_reason": "HIGHEST_EV"},
        created_at=NOW, alternative_set=s,
        policy={"small_live_management_policy": {"status": "X"}})
    assert mi["chosen"] == mi["action"] == "MAINTAIN_STANDING_PROTECTION"
    assert mi["chosen_why"]["selection_reason"] == "HIGHEST_EV"
    assert mi["chosen_why"]["action_rule"] == "PROTECTIVE_PRICE"
    # the set, the choice, the why and the policy are inside the sha
    tampered = dict(mi, alternative_set=dict(s, HOLD=dict(s["HOLD"],
                                                          value_usd=1.0)))
    assert CI.content_sha({k: tampered[k] for k in CI._MGMT_FIELDS}) != \
        mi["content_sha"]
    with pytest.raises(ValueError):
        LP.build_management_intent(
            review_id="paperrev:x", group_id="g1", position_key="pk",
            strategy=CG, valuation={}, evidence_state="X",
            recommendation="HOLD", mechanical_selection="HOLD",
            decided=decided, us_market_slug="s", holding_side="LONG",
            alternatives={}, reason={}, created_at=NOW,
            alternative_set={"HOLD": s["HOLD"]})
    # a caller that computed no set still records all eight
    assert set(_mi()["alternative_set"]) == set(CI.ALTERNATIVES)


def test_an_alternative_evaluated_on_one_side_only_is_a_divergence():
    mi = _mi()
    paper_req = LP.paper_management_request(mi, {"taken": "SUBMIT_EXIT",
                                                 "ok": True})
    # LIVE consumes the intent's own set: equal, but INDIRECT_HEDGE was
    # evaluated on neither side -> exact parity is NOT claimed
    live = LP.live_management_proposal(mi, scale=1000, open_qty=2400)
    assert live["alternatives_basis"] == \
        "THE_CANONICAL_MANAGEMENT_INTENT_S_SET"
    res = LP.compare(kind="MANAGEMENT", intent=mi,
                     paper={"state": LP.P_SUBMITTED, "requested": paper_req},
                     live=live, scale=1000, open_qty=2400)
    alt = res["comparison"]["alternatives"]
    assert res["parity_state"] != LP.DIVERGENCE
    assert alt["exact_parity_claimed"] is False
    assert "INDIRECT_HEDGE" in alt["evaluated_on_neither_side"]
    assert alt["why_not_exact"].startswith("NOT_EVALUATED_ON_EITHER_SIDE")
    # a LIVE-side evaluator that evaluated the indirect hedge where PAPER
    # did not: LOGIC_DIVERGENCE
    own = dict(CI.evaluated_set(mi["alternative_set"]),
               INDIRECT_HEDGE=CI.EVALUATED)
    live2 = LP.live_management_proposal(mi, scale=1000, open_qty=2400,
                                        live_alternatives=own)
    res2 = LP.compare(kind="MANAGEMENT", intent=mi,
                      paper={"state": LP.P_SUBMITTED, "requested": paper_req},
                      live=live2, scale=1000, open_qty=2400)
    assert res2["parity_state"] == LP.DIVERGENCE
    assert "alternative:INDIRECT_HEDGE" in res2["divergence_fields"]
    assert res2["comparison"]["alternatives"]["exact_parity_claimed"] is False
    # ... and one PAPER evaluated that LIVE did not
    assert CI.evaluated_set(mi["alternative_set"])["SELL_EXIT"] == \
        CI.EVALUATED
    fewer = dict(CI.evaluated_set(mi["alternative_set"]),
                 SELL_EXIT="UNAVAILABLE")
    live3 = LP.live_management_proposal(mi, scale=1000, open_qty=2400,
                                        live_alternatives=fewer)
    res3 = LP.compare(kind="MANAGEMENT", intent=mi,
                      paper={"state": LP.P_SUBMITTED, "requested": paper_req},
                      live=live3, scale=1000, open_qty=2400)
    assert "alternative:SELL_EXIT" in res3["divergence_fields"]
    # readiness reports the alternatives evaluated on neither side
    r = LP.readiness([{"intent_kind": "MANAGEMENT", "sleeve": "INVESTMENT",
                       "parity_state": res["parity_state"],
                       "comparison": res["comparison"]}], halted=False)
    ma = r["management_alternatives"]
    assert ma["exact_parity_claimed"] == 0 and ma["of"] == 1
    assert ma["evaluated_on_neither_side"]["INDIRECT_HEDGE"] == 1


def test_the_latency_chain_reports_distributions_and_unavailable_reasons():
    t = NOW

    def row(**over):
        r = {"pinnacle_observed_at": t, "ingest_at": t + 0.5,
             "probability_qualified_at": t + 1.0, "book_observed_at": t + 1.2,
             "decision_start_at": t + 1.5, "intent_recorded_at": t + 1.6,
             "paper_submit_at": t + 1.7, "paper_fill_at": t + 6.7}
        r.update(over)
        return r
    rows = [row(), row(ingest_at=t + 1.5),
            row(paper_fill_at=None, paper_fill_at_why="NOT_FILLED_YET"),
            row(ingest_at=t - 1.0)]               # two clocks disagree
    rep = LP.latency_report(rows)
    assert rep["decisions"] == 4 and rep["units"] == "seconds"
    assert rep["stages"] == list(LP.LATENCY_STAGES)
    s = rep["spans"]["pinnacle_to_ingest"]
    assert s["n"] == 3 and s["status"] == "MEASURED"
    assert s["p50_s"] == pytest.approx(0.5) and s["max_s"] == pytest.approx(1.5)
    assert s["p90_s"] == pytest.approx(1.3)
    assert s["unavailable"] == {"CLOCK_DISAGREEMENT": 1}
    f = rep["spans"]["paper_submit_to_paper_fill"]
    assert f["n"] == 3 and f["unavailable"] == {"NOT_FILLED_YET": 1}
    e2e = rep["spans"]["decision_start_to_paper_submit"]
    assert e2e["p50_s"] == pytest.approx(0.2)
    # two stamps of one instant, rounded to the microsecond differently, are
    # a zero span -- not a clock disagreement
    same = LP.latency_report([row(decision_start_at=t + 1.7000004,
                                  paper_submit_at=t + 1.7)])
    sp0 = same["spans"]["decision_start_to_paper_submit"]
    assert sp0["n"] == 1 and sp0["max_s"] == 0.0 and sp0["unavailable"] == {}
    # nothing recorded: UNAVAILABLE with the default reason, never a zero
    empty = LP.latency_report([{}])
    sp = empty["spans"]["pinnacle_to_ingest"]
    assert sp["status"] == "UNAVAILABLE" and sp["n"] == 0
    assert sp["p50_s"] is None and sp["max_s"] is None
    assert sp["unavailable"] == {"PINNACLE_OBSERVED_AT_NOT_RECORDED": 1}
    assert LP.latency_report([])["spans"]["pinnacle_to_ingest"]["n"] == 0


@pytest.mark.asyncio
async def test_the_cutover_endpoint_refuses_without_a_named_human():
    from fastapi import HTTPException
    from sportsassets.api import command_live_parity as CLP
    for body in (None, {}, {"actor": "release engineer"},
                 {"release_sha": SHA}):
        with pytest.raises(HTTPException) as e:
            await CLP.record_cutover(body)
        assert e.value.status_code == 400
        assert e.value.detail["reason"] == "ACTOR_AND_RELEASE_SHA_REQUIRED"
    for robot in ("system", "Xavier", "agent:release", "claude"):
        with pytest.raises(HTTPException) as e:
            await CLP.record_cutover({"actor": robot, "release_sha": SHA})
        assert e.value.detail["reason"] == "ACTOR_MUST_BE_A_NAMED_HUMAN"
    # the route is admin-only
    route = [r for r in CLP.router.routes
             if getattr(r, "path", "") == "/api/admin/live-parity/cutover"]
    assert route and "POST" in route[0].methods
    deps = [d.call for d in route[0].dependant.dependencies]
    assert CLP._require_admin in deps
    lat = [r for r in CLP.router.routes
           if getattr(r, "path", "") == "/api/command/live-parity/latency"]
    assert lat and lat[0].methods == {"GET"}


@pg
@pytest.mark.asyncio
async def test_the_cutover_endpoint_runs_every_check_in_the_serving_process(
        monkeypatch):
    """No check can be supplied from the request: this test process has no
    hooks installed and does not run the release sha, so the endpoint
    refuses with the server's own checks and writes nothing."""
    from fastapi import HTTPException
    from sportsassets.api import command_live_parity as CLP
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        class _Pool:
            def acquire(self):
                class _Ctx:
                    async def __aenter__(self_):
                        return conn

                    async def __aexit__(self_, *a):
                        return False
                return _Ctx()

        async def pool():
            return _Pool()
        monkeypatch.setattr(CLP, "_pool", pool)
        before = await conn.fetchval("SELECT count(*) FROM live_parity_cutover")
        with pytest.raises(HTTPException) as e:
            await CLP.record_cutover({"actor": "release engineer",
                                      "release_sha": SHA,
                                      # request-supplied overrides are ignored
                                      "api_sha": SHA,
                                      "hooks_here": list(LP.HOOK_NAMES)})
        assert e.value.status_code == 409
        assert e.value.detail["reason"] == "CUTOVER_REFUSED"
        assert "HOOKS_INSTALLED_IN_THIS_PROCESS" in e.value.detail["refused"]
        assert await conn.fetchval(
            "SELECT count(*) FROM live_parity_cutover") == before
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# R30A · ADVERSARIAL REVIEW FIXES: exact parity over an incomplete
# alternative set, governance as a current-state check, a rollback is a
# cutover too, the derived decision-logic list, the serving build's logic,
# every server-side cutover check, the named-human rule, the floored window
# ═════════════════════════════════════════════════════════════════════

_NOT_SEARCHED = "INDIRECT_HEDGE_SEARCH_NOT_RUN_ON_THE_PAPER_BOOK"


def _review_alts(*, indirect_searched: bool):
    """A review's ranking as paper_xavier builds it: HOLD valued, both sales
    evaluated and blocked (no bids) -- and the indirect hedge either NOT
    SEARCHED (the paper book today: `incomplete_search`) or searched and
    valued."""
    alts = {"candidates": [{"action": "HOLD", "qty": 2400,
                            "value_usd": 1488.0, "expected_net_usd": 168.0}],
            "not_rankable": [{"action": "EXIT", "blocker": "NO_BIDS"},
                             {"action": "REDUCE", "blocker": "NO_BIDS"}]}
    if indirect_searched:
        alts["candidates"].append({"action": "ACQUIRE_INDIRECT_HEDGE",
                                   "value_usd": 1400.0,
                                   "expected_net_usd": 90.0})
    else:
        alts["not_rankable"].append({"action": "ACQUIRE_INDIRECT_HEDGE",
                                     "blocker": _NOT_SEARCHED})
        alts["incomplete_search"] = {"complete": False, "why": _NOT_SEARCHED}
    return alts


def _no_order_mi(*, indirect_searched: bool):
    decided = LP.management_action(
        chosen=None, fresh=True, stale=False, p_missing=False,
        protection_ok=False, standing_live=False, candidate=None,
        protective=None, open_qty=2400)
    assert decided["action"] == LP.ACT_NONE
    alts = _review_alts(indirect_searched=indirect_searched)
    aset = CI.management_alternatives(
        alts=alts, decided=decided, mechanical_selection=None,
        standing_live=False,
        protective={"ok": False, "refusal": "NO_PROTECTIVE_PRICE_TEST"},
        open_qty=2400,
        reallocate={"position_efficiency": 0.1,
                    "alternative_efficiency": 0.05, "advantage": -0.05,
                    "recommended": False, "position_ev_from_here_usd": 168.0,
                    "best_opportunity": {"decision_id": "d1"}})
    return LP.build_management_intent(
        review_id="paperrev:none:%s" % indirect_searched, group_id="g1",
        position_key="pk", strategy=CG,
        valuation={"valuation_id": 9, "valuation_hash": "h"},
        evidence_state="FRESH_CURRENT_PROBABILITY", recommendation=None,
        mechanical_selection=None, decided=decided,
        us_market_slug="aec-nfl-kc-buf-2026-10-04", holding_side="LONG",
        alternatives=alts, reason={}, created_at=NOW, alternative_set=aset)


def test_unavailable_alternatives_say_whether_their_evaluation_ran():
    mi = _no_order_mi(indirect_searched=False)
    s = mi["alternative_set"]
    assert s["INDIRECT_HEDGE"]["evaluation"] == CI.NOT_RUN
    assert s["SELL_EXIT"]["status"] == "UNAVAILABLE"
    assert s["SELL_EXIT"]["evaluation"] == CI.RAN          # evaluated: no bids
    assert s["CANCEL_PROTECTION_BEFORE_EXIT"]["evaluation"] == CI.RAN
    assert CI.not_run_set(s) == ["INDIRECT_HEDGE"]
    assert CI.not_run_set(_no_order_mi(indirect_searched=True)[
        "alternative_set"]) == []
    # an UNAVAILABLE entry that does not say counts as not run (fail closed)
    assert CI.not_run_set({"HOLD": {"status": "UNAVAILABLE", "why": "x"}}) \
        == ["HOLD"]
    # a caller that computed nothing: every unrun evaluation is NOT_RUN
    assert {"INDIRECT_HEDGE", "REALLOCATE"} <= set(CI.not_run_set(
        _mi()["alternative_set"]))


@pytest.mark.parametrize("scale", [1, 1000])
def test_an_incomplete_alternative_set_is_never_claimed_as_exact_parity(scale):
    """Review finding 1: a NO_ORDER pair (quantity None on both sides) whose
    INDIRECT_HEDGE was evaluated on neither side was MATCHED -- at scale 1
    and at the production 1:1,000 -- and readiness counted it as an exact
    Xavier match."""
    mi = _no_order_mi(indirect_searched=False)
    live = LP.live_management_proposal(mi, scale=scale, open_qty=2400)
    res = LP.compare(kind="MANAGEMENT", intent=mi,
                     paper={"state": LP.P_NO_ORDER,
                            "requested": LP.paper_management_request(
                                mi, {"taken": "NONE"})},
                     live=live, scale=scale, open_qty=2400)
    assert res["parity_state"] == LP.INCOMPLETE, res
    assert res["parity_state"] != LP.MATCHED
    assert res["comparison"]["state_if_alternatives_were_complete"] == \
        LP.MATCHED
    assert res["comparison"]["why_not_exact"] == \
        "NOT_EVALUATED_ON_EITHER_SIDE:INDIRECT_HEDGE"
    assert res["comparison"]["exact_parity_claimed"] is False
    assert res["divergence_fields"] == []          # never a halt
    rows = ([_row("DECISION")] * 30
            + [{"intent_kind": "MANAGEMENT", "sleeve": "INVESTMENT",
                "parity_state": res["parity_state"],
                "comparison": res["comparison"]}] * 30)
    r = LP.readiness(rows, halted=False, profitability={
        "profitability_verdict": "SUPPORTED_BY_FORWARD_EVIDENCE"})
    xm = r["xavier_management_match"]
    assert xm["matched"] == 0 and xm["of"] == 30 and xm["rate"] == 0.0
    assert xm["incomplete_comparisons"] == 30
    assert r["management_alternatives"]["not_run"] == {"INDIRECT_HEDGE": 30}
    assert "MANAGEMENT_PARITY_NOT_EXACT:INCOMPLETE_COMPARISON:30" in \
        r["blockers"]
    assert r["parity_gate"] == "FAIL" and r["recommendation"] == LP.NOT_READY
    # WITH the indirect hedge searched (evaluated on both sides), the same
    # pair is exact -- the sales and the cancel, UNAVAILABLE on both sides
    # after their evaluation RAN, are evaluated facts and do not withhold it
    full = _no_order_mi(indirect_searched=True)
    assert full["alternative_set"]["INDIRECT_HEDGE"]["status"] == CI.EVALUATED
    live = LP.live_management_proposal(full, scale=scale, open_qty=2400)
    ok = LP.compare(kind="MANAGEMENT", intent=full,
                    paper={"state": LP.P_NO_ORDER,
                           "requested": LP.paper_management_request(
                               full, {"taken": "NONE"})},
                    live=live, scale=scale, open_qty=2400)
    assert ok["parity_state"] == LP.MATCHED, ok
    assert ok["comparison"]["exact_parity_claimed"] is True
    assert "why_not_exact" not in ok["comparison"]
    assert "SELL_EXIT" in ok["comparison"]["alternatives"][
        "evaluated_on_neither_side"]
    r2 = LP.readiness([_row("DECISION")] * 30 + [
        {"intent_kind": "MANAGEMENT", "sleeve": "INVESTMENT",
         "parity_state": ok["parity_state"],
         "comparison": ok["comparison"]}] * 30, halted=False,
        profitability={"profitability_verdict":
                       "SUPPORTED_BY_FORWARD_EVIDENCE"})
    assert r2["xavier_management_match"]["matched"] == 30
    assert r2["recommendation"] == LP.READY, r2["blockers"]
    # a LIVE side that evaluated the hedge PAPER never searched: divergence
    own = dict(CI.evaluated_set(mi["alternative_set"]),
               INDIRECT_HEDGE=CI.EVALUATED)
    live3 = LP.live_management_proposal(mi, scale=scale, open_qty=2400,
                                        live_alternatives=own)
    res3 = LP.compare(kind="MANAGEMENT", intent=mi,
                      paper={"state": LP.P_NO_ORDER,
                             "requested": LP.paper_management_request(
                                 mi, {"taken": "NONE"})},
                      live=live3, scale=scale, open_qty=2400)
    assert res3["parity_state"] == LP.DIVERGENCE


def _gov_row(t, refusals=(), *, kind="DECISION", policy_sha="p1"):
    return {"intent_kind": kind, "sleeve": "INVESTMENT",
            "parity_state": LP.SCALE, "created_at": t, "policy_sha": policy_sha,
            "comparison": {"fields": {}, "live_governance_refusals":
                           list(refusals)}}


def test_governance_is_a_current_state_check_not_a_permanent_record():
    """Review finding 2: every row recorded before the owner's approval
    carries a governance refusal, so counting them all made readiness
    unpassable for the whole window after an approval -- only a decision-
    logic edit (restarting the window) would clear it."""
    good = {"profitability_verdict": "SUPPORTED_BY_FORWARD_EVIDENCE"}
    pre = [_gov_row(100.0 + i, [CI.R_POLICY_UNAPPROVED, LP.R_GATE_APPROVAL])
           for i in range(5)]
    post = [_gov_row(1000.0 + i) for i in range(30)]
    mgt = [dict(_row("MANAGEMENT"), created_at=2000.0)] * 30
    approved = {"gates_approved": True, "approved_policy_shas": {"p1"},
                "approvals_changed_at": 500.0}
    # refused rows, THEN the approval, THEN a clean sample: no blocker
    r = LP.readiness(pre + post + mgt, halted=False, profitability=good,
                     governance_now=approved)
    assert not [b for b in r["blockers"] if b.startswith("LIVE_GOVERNANCE")]
    assert r["governance_gate"] == "PASS"
    assert r["recommendation"] == LP.READY, r["blockers"]
    g = r["live_governance_refusals"]
    assert g["historical_not_counted"] == {CI.R_POLICY_UNAPPROVED: 5,
                                           LP.R_GATE_APPROVAL: 5}
    assert g["window"]["rows_before_the_change"] == 5
    # a refusal recorded AFTER the approvals last changed still blocks
    late = post + [_gov_row(1500.0, [CI.R_POLICY_SHA])]
    r = LP.readiness(pre + late + mgt, halted=False, profitability=good,
                     governance_now=approved)
    assert "LIVE_GOVERNANCE_REFUSED:%s:1" % CI.R_POLICY_SHA in r["blockers"]
    # the state NOW: gates not approved (a REVOKE, or a gate config changed
    # by a release) blocks even with no refused row in the window
    r = LP.readiness(post + mgt, halted=False, profitability=good,
                     governance_now=dict(approved, gates_approved=False))
    assert "LIVE_GOVERNANCE_NOT_IN_FORCE:%s" % LP.R_GATE_APPROVAL in \
        r["blockers"]
    assert r["governance_gate"] == "FAIL"
    # ... and the latest decision's policy no longer approved
    r = LP.readiness(post + mgt, halted=False, profitability=good,
                     governance_now=dict(approved,
                                         approved_policy_shas=set()))
    assert "LIVE_GOVERNANCE_NOT_IN_FORCE:%s" % CI.R_POLICY_UNAPPROVED in \
        r["blockers"]
    # nothing ever approved: every refusal counts and the state blocks too
    r = LP.readiness(pre + post + mgt, halted=False, profitability=good,
                     governance_now={"gates_approved": False,
                                     "approved_policy_shas": set(),
                                     "approvals_changed_at": None})
    assert "LIVE_GOVERNANCE_REFUSED:%s:5" % CI.R_POLICY_UNAPPROVED in \
        r["blockers"]
    # a pure caller that supplies no approvals state: fail closed (all count)
    r = LP.readiness(pre + post + mgt, halted=False, profitability=good)
    assert "LIVE_GOVERNANCE_REFUSED:%s:5" % LP.R_GATE_APPROVAL in r["blockers"]


@pg
@pytest.mark.asyncio
async def test_the_approvals_change_instant_is_read_from_the_database():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        from tests import admission_fixture as AF
        before = await LP.approvals_changed_at(conn)
        gnow = await LP.governance_now(conn)
        assert gnow["gates_approved"] is False             # none recorded
        await AF.record_test_gate_approvals(conn, [LAP.GATE_SETTLEMENT])
        after = await LP.approvals_changed_at(conn)
        assert after is not None and (before is None or after >= before)
        assert abs(after - __import__("time").time()) < 300
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_rollback_redeploy_appends_a_cutover_and_restarts_the_window(
        monkeypatch):
    """Review finding 3: A -> B -> A could not be recorded (release_sha was
    UNIQUE and record_cutover answered `already` for any earlier row), so
    the effective cutover stayed on B while A's logic decided again."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        if await conn.fetchval(
                "SELECT to_regclass('agent_work_requests')") is None:
            await conn.execute("CREATE TABLE agent_work_requests (x int)")
        real = LP.decision_logic_hash()
        a_sha, b_sha = "1" * 40, "2" * 40
        hashes = {a_sha: dict(real, hash="a" * 64),
                  b_sha: dict(real, hash="b" * 64)}

        async def record(sha):
            await _cutover_world(conn, sha=sha, workers=sha)
            monkeypatch.setattr(LP, "decision_logic_hash",
                                lambda root=None: hashes[sha])
            return await LP.record_cutover(conn, release_sha=sha,
                                           recorded_by="release engineer",
                                           api_sha=sha,
                                           hooks_here=list(LP.HOOK_NAMES))
        first = await record(a_sha)
        assert first["recorded"] is True and first["restarts_forward_window"]
        second = await record(b_sha)
        assert second["recorded"] is True and second["restarts_forward_window"]
        assert (await LP.production_cutover(conn))["release_sha"] == b_sha
        # the ROLLBACK to A: a third row, and the window restarts on it
        back = await record(a_sha)
        assert back["recorded"] is True, back
        assert back["restarts_forward_window"] is True
        eff = await LP.production_cutover(conn)
        assert eff["release_sha"] == a_sha
        assert eff["cutover_id"] == back["cutover"]["cutover_id"]
        assert eff["cutover_id"] != first["cutover"]["cutover_id"]
        assert eff["releases_recorded"] == 3
        assert (await LP.release_cutover(conn, a_sha))["cutover_id"] == \
            eff["cutover_id"]
        # recording A again while it IS the latest: already, nothing appended
        again = await record(a_sha)
        assert again["recorded"] is False and again["already"] is True
        assert await conn.fetchval(
            "SELECT count(*) FROM live_parity_cutover") == 3
        # and the database refuses a consecutive duplicate on its own
        await _expect(conn, asyncpg.exceptions.UniqueViolationError,
                      "INSERT INTO live_parity_cutover (release_sha, api_sha,"
                      " workers_sha, migrations, decision_logic_hash,"
                      " decision_logic_files, hook_install_id,"
                      " small_live_mode, small_live_halted, capital_activated,"
                      " evidence, recorded_by) SELECT release_sha, api_sha,"
                      " workers_sha, migrations, decision_logic_hash,"
                      " decision_logic_files, hook_install_id,"
                      " small_live_mode, small_live_halted, capital_activated,"
                      " evidence, 'release engineer' FROM live_parity_cutover"
                      " WHERE cutover_id = $1", eff["cutover_id"])
        rows = await conn.fetch("SELECT * FROM live_parity_cutover")
        assert LP.effective_cutover_of(rows)["cutover_id"] == eff["cutover_id"]
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_every_server_side_cutover_check_refuses_on_its_own(monkeypatch):
    """Review finding 10 / 16: the checks the first refusal test did not
    exercise -- DECISION_LOGIC_HASH_COMPUTED, MIGRATIONS_225_226_APPLIED,
    SMALL_LIVE_IS_SHADOW, READBACK_OBJECTS_PRESENT and
    READBACK_NO_LOGIC_DIVERGENCE -- each refuse a cutover alone.

    READBACK_NO_LOGIC_DIVERGENCE, A DELIBERATE CHANGE (stated): with the R30
    singleton it counted divergences EVER; with a row per deployment "ever"
    would make every later release unrecordable after the first divergence,
    including the release that fixes it. It now counts divergences recorded
    after the last named-human halt clear (SMALL_LIVE_NOT_HALTED refuses
    while the halt itself stands). Both sides are proven here."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        if await conn.fetchval(
                "SELECT to_regclass('agent_work_requests')") is None:
            await conn.execute("CREATE TABLE agent_work_requests (x int)")

        async def attempt():
            return await LP.record_cutover(conn, release_sha=SHA,
                                           recorded_by="release engineer",
                                           api_sha=SHA,
                                           hooks_here=list(LP.HOOK_NAMES))

        async def refused_alone(check, setup):
            sp = conn.transaction()
            await sp.start()
            try:
                await _cutover_world(conn)
                await setup()
                got = await attempt()
                assert got["recorded"] is False, got
                assert got["refused"] == [check], got["refused"]
            finally:
                await sp.rollback()

        real_hash = LP.decision_logic_hash

        async def no_hash():
            monkeypatch.setattr(LP, "decision_logic_hash", lambda root=None: {
                "hash": None, "files": {}, "missing": ["canonical_intent.py"]})
        await refused_alone("DECISION_LOGIC_HASH_COMPUTED", no_hash)
        monkeypatch.setattr(LP, "decision_logic_hash", real_hash)

        async def no_226():
            await conn.execute("DELETE FROM schema_migrations "
                               " WHERE version LIKE '226%'")
        await refused_alone("MIGRATIONS_225_226_APPLIED", no_226)

        async def not_shadow():
            monkeypatch.setattr(LP, "SMALL_LIVE_MODE", "NOT_SHADOW_TEST_ONLY")
        await refused_alone("SMALL_LIVE_IS_SHADOW", not_shadow)
        monkeypatch.setattr(LP, "SMALL_LIVE_MODE", LP.MODE_SHADOW)

        async def no_view():
            await conn.execute("DROP VIEW live_approvals_current")
        await refused_alone("READBACK_OBJECTS_PRESENT", no_view)

        async def ledger_divergence(cleared_after: bool):
            it = _intent(decision_id="papercg:div:%s" % cleared_after)
            assert await LP.record_decision_intent(conn, it)
            for adapter, mode, state, scale in (
                    ("PAPER", "SIMULATED", "PAPER_SUBMITTED", 1),
                    ("SMALL_LIVE", "SHADOW", "SHADOW_PROPOSED", 1000)):
                await conn.execute(
                    "INSERT INTO canonical_intent_executions (execution_id,"
                    " intent_kind, intent_id, intent_sha, adapter, mode,"
                    " adapter_version, state, requested, capital_scale)"
                    " VALUES ($1,'DECISION',$2,$3,$4,$5,'T',$6,'{}',$7)",
                    "cie_%s_%s" % (adapter, cleared_after), it["intent_id"],
                    it["content_sha"], adapter, mode, state, scale)
            await conn.execute(
                "INSERT INTO live_parity_ledger (parity_id, parity_version,"
                " intent_kind, intent_id, intent_sha, sleeve,"
                " paper_execution_id, live_execution_id, capital_scale,"
                " parity_state, divergence_fields, comparison)"
                " VALUES ($1,'T','DECISION',$2,$3,'INVESTMENT',$4,$5,1000,"
                " 'LOGIC_DIVERGENCE', ARRAY['qty'], '{}')",
                "lpl_%s" % cleared_after, it["intent_id"], it["content_sha"],
                "cie_PAPER_%s" % cleared_after,
                "cie_SMALL_LIVE_%s" % cleared_after)

        async def divergence_after_the_clear():
            # cleared first (by _cutover_world's named human), THEN diverged
            await conn.execute(
                "UPDATE small_live_control SET halted = true, halted_at = now(),"
                " halt_reason = 'LOGIC_DIVERGENCE' WHERE id = 1")
            await LP.clear_halt(conn, actor="release engineer",
                                reason="test: cleared before the divergence")
            await ledger_divergence(True)
        await refused_alone("READBACK_NO_LOGIC_DIVERGENCE",
                            divergence_after_the_clear)
        # a divergence recorded BEFORE a named human cleared its halt does
        # not block a later cutover
        sp = conn.transaction()
        await sp.start()
        try:
            await _cutover_world(conn)
            await ledger_divergence(False)
            await conn.execute(
                "UPDATE small_live_control SET halted = true, halted_at = now(),"
                " halt_reason = 'LOGIC_DIVERGENCE' WHERE id = 1")
            await LP.clear_halt(conn, actor="release engineer",
                                reason="test: reviewed and cleared")
            got = await attempt()
            assert got["recorded"] is True, got.get("refused")
            div = got["checks"]["READBACK_NO_LOGIC_DIVERGENCE"]
            assert div["passed"] is True and div["value"][
                "uncleared_divergences"] == 0
        finally:
            await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_serving_build_whose_logic_has_no_cutover_blocks_readiness(
        monkeypatch):
    """Review finding 8: nothing compared the serving build's decision-logic
    hash with the effective cutover's."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        if await conn.fetchval(
                "SELECT to_regclass('agent_work_requests')") is None:
            await conn.execute("CREATE TABLE agent_work_requests (x int)")
        await _cutover_world(conn)
        ok = await LP.record_cutover(conn, release_sha=SHA,
                                     recorded_by="release engineer",
                                     api_sha=SHA,
                                     hooks_here=list(LP.HOOK_NAMES))
        assert ok["recorded"] is True
        rep = await LP.readiness_report(conn)
        assert rep["build_logic"]["matches"] is True
        assert "CURRENT_BUILD_LOGIC_HAS_NO_CUTOVER" not in rep["blockers"]
        # the build now decides with logic no cutover names
        changed = dict(LP.decision_logic_hash(), hash="c" * 64)
        monkeypatch.setattr(LP, "decision_logic_hash",
                            lambda root=None: changed)
        rep = await LP.readiness_report(conn)
        assert rep["build_logic"]["serving_hash"] == "c" * 64
        assert "CURRENT_BUILD_LOGIC_HAS_NO_CUTOVER" in rep["blockers"]
        assert rep["parity_gate"] == "FAIL"
    finally:
        await tx.rollback()
        await conn.close()


def test_the_profitability_verdict_needs_the_serving_logic_to_have_a_cutover():
    from sportsassets import decision_logic as DL
    from sportsassets.profitability import validation as V
    data = {"positions": [], "econ": [], "attribution": [], "scores": [],
            "refusals": [], "exec_outcomes": [], "value_add": []}
    real = DL.decision_logic_hash()
    match = DL.build_logic_check(real["hash"], logic=real)
    assert match["matches"] is True and match["refusal"] is None
    out = V.compute(data, now=NOW, since=None, cutover=NOW - 100,
                    build_logic=match)
    assert out["profitability_verdict"]["why"] != DL.R_LOGIC_HAS_NO_CUTOVER
    moved = DL.build_logic_check("d" * 64, logic=real)
    assert moved["refusal"] == DL.R_LOGIC_HAS_NO_CUTOVER
    out = V.compute(data, now=NOW, since=None, cutover=NOW - 100,
                    build_logic=moved)
    v = out["profitability_verdict"]
    assert v["verdict"] == V.NOT_ESTABLISHED
    assert v["why"] == DL.R_LOGIC_HAS_NO_CUTOVER
    assert out["build_logic"]["refusal"] == DL.R_LOGIC_HAS_NO_CUTOVER
    nohash = DL.build_logic_check("d" * 64, logic={"hash": None,
                                                   "missing": ["x.py"]})
    assert nohash["refusal"] == DL.R_LOGIC_HASH_UNAVAILABLE
    # the readiness gate says the same
    r = LP.readiness([], halted=False, build_logic=moved)
    assert "CURRENT_BUILD_LOGIC_HAS_NO_CUTOVER" in r["blockers"]
    r = LP.readiness([], halted=False, build_logic=nohash)
    assert "CURRENT_BUILD_LOGIC_HASH_UNAVAILABLE" in r["blockers"]


def test_the_decision_logic_list_is_derived_from_the_decision_roots():
    """Review finding 4: modules the ENTER, sizing, freshness, settlement
    and management decisions read were missing from the pinned list. Every
    package module a decision root imports must be pinned or excused with a
    reason; a new import that is neither fails here."""
    from sportsassets import decision_logic as DL
    imports = DL.decision_logic_imports()
    assert set(imports) == set(DL.DECISION_LOGIC_ROOTS)
    unclassified = sorted({(r, m) for r, ms in imports.items() for m in ms
                           if m not in DL.DECISION_LOGIC_FILES
                           and m not in DL.NOT_DECISION_LOGIC})
    assert unclassified == [], unclassified
    # every excuse is still needed and carries its reason
    used = {m for ms in imports.values() for m in ms}
    assert set(DL.NOT_DECISION_LOGIC) <= used
    assert all(len(why) > 20 for why in DL.NOT_DECISION_LOGIC.values())
    assert not set(DL.NOT_DECISION_LOGIC) & set(DL.DECISION_LOGIC_FILES)
    # the modules the review named are pinned
    for m in ("bettor_paper_ledger.py", "bettor_settlement_terms.py",
              "bettor_paper_session.py", "workers/ext_pinnacle_loop.py",
              "pinnapi_primary.py", "bettor_xavier_standing_orders.py"):
        assert m in DL.DECISION_LOGIC_FILES, m
    # the 30 s rule's own constant is inside a pinned file
    src = (pathlib.Path(LP.__file__).resolve().parent
           / "workers" / "ext_pinnacle_loop.py").read_text()
    assert "PINNACLE_MAX_AGE_S = 30.0" in src
    # every root is itself pinned, every pinned file exists, the identity is
    # one object whichever module a caller reads it from
    assert set(DL.DECISION_LOGIC_ROOTS) <= set(DL.DECISION_LOGIC_FILES)
    assert DL.decision_logic_hash()["missing"] == []
    assert LP.DECISION_LOGIC_FILES is DL.DECISION_LOGIC_FILES
    # decision_logic is pure: the standard library only
    import ast
    tree = ast.parse(pathlib.Path(DL.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not node.level and node.module in (
                "__future__",), node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                assert a.name in ("ast", "hashlib", "os", "pathlib"), a.name


def test_each_intent_records_the_build_that_decided_it():
    ident = LP.serving_build_identity()
    assert ident["decision_logic_hash"] == LP.decision_logic_hash()["hash"]
    assert set(ident) == {"decision_logic_hash", "release_sha"}


@pytest.mark.parametrize("actor", [
    "github-actions[bot]", "dependabot[bot]", "bot", "ci", "CI", "cron",
    "codex", "assistant", "openai", "gpt", "automation", "service", "root",
    "admin", "scheduler", "render-deploy", "deploy-bot", "ci_runner",
    "claude", "system", "Xavier", "agent:release", "unknown", "none", "",
    "   ", None])
def test_machine_identities_are_never_named_humans(actor):
    """Review finding 11: the rule was an R30 prefix deny-list, so 'github-
    actions[bot]', 'ci', 'cron', 'codex', 'openai', 'root' and 'admin'
    passed as named humans for recorded_by and for the LIVE approver."""
    assert CI.is_named_human(actor) is False, actor


@pytest.mark.parametrize("actor", [
    "release engineer", "owner@example", "Matt Taylor", "Jane Doe (owner)",
    "OWNER (account holder): written paper-only authorization",
    "test human"])
def test_named_humans_still_pass(actor):
    assert CI.is_named_human(actor) is True, actor


@pg
@pytest.mark.asyncio
async def test_the_database_applies_the_same_named_human_rule():
    conn = await asyncpg.connect(H.DSN)
    try:
        for actor in ("github-actions[bot]", "ci", "cron", "root", "admin",
                      "codex", "system", "release engineer", "owner@example",
                      " system", "Matt Taylor", "deploy-bot", ""):
            db = await conn.fetchval("SELECT live_parity_named_human($1)",
                                     actor)
            assert db is CI.is_named_human(actor), actor
        assert await conn.fetchval(
            "SELECT live_parity_named_human(NULL)") is False
        # the CHECKs use the function; the function carries the pattern
        assert CI.NON_HUMAN_ACTOR_PATTERN in UP
        for ck in ("lpc_named_human_ck CHECK (live_parity_named_human(",
                   "la_named_human_ck CHECK (live_parity_named_human(",
                   "OR live_parity_named_human(actor))",
                   "live_parity_named_human(NEW.cleared_by)"):
            assert ck in UP, ck
    finally:
        await conn.close()


def test_the_validity_window_is_floored_never_extended():
    """Review finding 12: round(stamp + 30, 3) could end the window up to
    0.5 ms AFTER the true end."""
    e = CI.decision_expiry(probability_observed_at=1791130000.0006,
                           probability_limit_s=30.0,
                           book_observed_at=1791130000.0006,
                           book_max_age_s=60.0)
    assert e["expires_at"] == 1791130030.0                 # not ...030.001
    assert e["expires_at"] <= 1791130000.0006 + 30.0
    it = {"expires_at": e["expires_at"]}
    assert CI.intent_expiry_refusal(it, now=1791130030.0002) == \
        CI.R_INTENT_EXPIRED
    for stamp in (NOW, NOW + 0.0004, NOW + 0.0005, NOW + 0.9999, NOW + 0.123):
        got = CI.decision_expiry(probability_observed_at=stamp,
                                 probability_limit_s=30.0,
                                 book_observed_at=stamp, book_max_age_s=99.0)
        assert got["expires_at"] <= stamp + 30.0 + 1e-9, stamp
        assert stamp + 30.0 - got["expires_at"] < 0.001 + 1e-9, stamp


def test_the_book_stage_is_its_signed_age_and_only_two_clocks_disagree():
    """Review finding 7: decide_one stamps decision_start and THEN reads the
    book, so a fresh read gave a negative book_observed -> decision_start
    span, discarded as CLOCK_DISAGREEMENT although both stamps are ours."""
    t = NOW
    fresh = {"pinnacle_observed_at": t, "ingest_at": t + 0.5,
             "probability_qualified_at": t + 1.0, "decision_start_at": t + 1.5,
             "book_observed_at": t + 1.8,          # read inside the decision
             "intent_recorded_at": t + 2.0, "paper_submit_at": t + 2.1,
             "paper_fill_at": t + 3.0}
    cached = dict(fresh, book_observed_at=t + 1.2)
    rep = LP.latency_report([fresh, cached])
    age = rep["spans"]["book_age_at_decision_start"]
    assert age["kind"] == LP.SIGNED and age["n"] == 2
    assert age["unavailable"] == {}
    assert age["min_s"] == pytest.approx(-0.3)
    assert age["max_s"] == pytest.approx(0.3)
    b2i = rep["spans"]["book_observed_to_intent_recorded"]
    assert b2i["n"] == 2 and b2i["max_s"] == pytest.approx(0.8)
    assert "book_observed_to_decision_start" not in rep["spans"]
    # a same-clock inversion is a stage-order fact, not two clocks
    inv = LP.latency_report([dict(fresh, intent_recorded_at=t + 1.0)])
    s = inv["spans"]["decision_start_to_intent_recorded"]
    assert s["unavailable"] == {"STAGE_ORDER_INVERTED": 1}
    # the provider's stamp against ours is the one cross-clock span family
    cross = LP.latency_report([dict(fresh, ingest_at=t - 1.0)])
    assert cross["spans"]["pinnacle_to_ingest"]["unavailable"] == {
        "CLOCK_DISAGREEMENT": 1}
    assert cross["spans"]["pinnacle_to_ingest"]["clocks"] == \
        "PROVIDER_CLOCK->OUR_CLOCK"


@pg
@pytest.mark.asyncio
async def test_the_latency_read_path_is_indexed_and_reads_one_lateral():
    """Review finding 13: two correlated subqueries per intent, each a
    sequential scan, under a 6 s statement timeout."""
    import inspect
    src = inspect.getsource(LP.latency_rows)
    assert "LEFT JOIN LATERAL" in src and "WITH sel AS" in src
    conn = await asyncpg.connect(H.DSN)
    try:
        idx = {r["indexname"] for r in await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename IN "
            " ('paper_orders', 'paper_fills')")}
        assert {"paper_orders_decision_role_idx",
                "paper_fills_order_idx"} <= idx
        rows = await LP.latency_rows(conn, limit=50)
        assert isinstance(rows, list)
        for r in rows:
            assert "paper_fill_at" in r
    finally:
        await conn.close()
