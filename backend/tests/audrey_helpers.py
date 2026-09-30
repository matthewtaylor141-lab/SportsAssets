"""SHARED HARNESS FOR AUDREY'S TESTS (not a test module).

* THE CORE TABLES (migration 152) are owned by the core stream. Until it is
  merged, `ensure_core_tables` creates the ones Audrey uses from the shared
  contract (AGENTS_CONTRACT.md), with the core migration's NOT NULLs and its
  "an ACTIVE policy names its approver" check, ONLY IF ABSENT -- `CREATE
  TABLE IF NOT EXISTS`, nothing more. When 152 is present this does
  nothing.
* SYNTHETIC LEDGER ROWS, all dated in 2032 and prefixed `audt-`, so no
  production-shaped row can fall in an audited window. They are test
  evidence only, never a production substitute.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os

import pytest

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-audt"
VENUE = "PMUS_TEST"
PFX = "audt-"
LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"
DAY = 86400.0

CORE_152_DDL = """
CREATE TABLE IF NOT EXISTS agent_identities (
    agent_id text PRIMARY KEY CHECK (agent_id IN ('DEREK','XAVIER','AUDREY')),
    display_name text, mandate text, policy_version text,
    model_version text, code_version text, tool_permissions jsonb,
    updated_at timestamptz);
CREATE TABLE IF NOT EXISTS agent_status (
    agent_id text PRIMARY KEY REFERENCES agent_identities,
    state text CHECK (state IN ('IDLE','EVALUATING','WAITING_FOR_EVIDENCE',
        'WAITING_FOR_PROVIDER','BLOCKED','DECISION_RECORDED','RECOVERING',
        'FAILED')),
    activity text, waiting_on jsonb, dependencies jsonb,
    last_heartbeat_at timestamptz, last_run_started_at timestamptz,
    last_run_finished_at timestamptz, last_run_elapsed_s numeric,
    runs bigint, errors bigint, last_error text, cadence jsonb);
CREATE TABLE IF NOT EXISTS agent_policy_versions (
    agent_id text NOT NULL REFERENCES agent_identities(agent_id),
    policy_key text NOT NULL, version text NOT NULL,
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    state text NOT NULL
        CHECK (state IN ('ACTIVE','CANDIDATE','REJECTED','RETIRED')),
    created_by text NOT NULL, approved_by text, approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, policy_key, version),
    CHECK (state <> 'ACTIVE' OR (approved_by IS NOT NULL
                                 AND approved_at IS NOT NULL)));
CREATE UNIQUE INDEX IF NOT EXISTS agent_policy_one_active
    ON agent_policy_versions (agent_id, policy_key) WHERE state = 'ACTIVE';
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id text PRIMARY KEY,
    assignee text NOT NULL REFERENCES agent_identities(agent_id),
    created_by text NOT NULL, kind text NOT NULL, title text NOT NULL,
    spec jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN',
        'IN_PROGRESS','WAITING','CANDIDATE_READY','EVALUATING','REJECTED',
        'APPROVAL_READY','APPROVED','RELEASED','ROLLED_BACK',
        'CLOSED_NO_CHANGE','CANCELLED')),
    directive_id text, evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    outcome jsonb, created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS agent_task_events (
    event_id bigserial PRIMARY KEY,
    task_id text NOT NULL REFERENCES agent_tasks(task_id),
    at timestamptz NOT NULL DEFAULT now(), kind text NOT NULL,
    actor text NOT NULL, detail jsonb NOT NULL DEFAULT '{}'::jsonb);
"""
CORE_152_APPEND_ONLY = """
CREATE OR REPLACE FUNCTION agent_task_events_append_only_test()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION 'agent_task_events is append-only'; END; $$
LANGUAGE plpgsql;
CREATE TRIGGER agent_task_events_append_only_test_trg
  BEFORE UPDATE OR DELETE ON agent_task_events
  FOR EACH ROW EXECUTE FUNCTION agent_task_events_append_only_test();
"""


async def ensure_core_tables(c) -> bool:
    """True when this created them (152 absent), False when present."""
    if await c.fetchval("SELECT to_regclass('agent_tasks')") is not None:
        return False
    await c.execute(CORE_152_DDL)
    await c.execute(CORE_152_APPEND_ONLY)
    for a in ("DEREK", "XAVIER", "AUDREY"):
        await c.execute(
            "INSERT INTO agent_identities (agent_id, display_name) "
            "VALUES ($1,$2) ON CONFLICT DO NOTHING", a, a.title())
    return True


@contextlib.asynccontextmanager
async def connect():
    import asyncpg
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


async def purge(c):
    """Removes ONLY this harness's rows; append-only tables with triggers
    suspended for this transaction."""
    async with c.transaction():
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute("DELETE FROM audrey_audit_reports "
                        " WHERE audit_day >= '2032-01-01'")
        await c.execute("DELETE FROM audrey_audit_watermarks")
        await c.execute("DELETE FROM audrey_collection_samples "
                        " WHERE pass_at >= '2032-01-01'")
        for t in ("improvement_events", "improvement_releases",
                  "improvement_trials"):
            await c.execute("DELETE FROM %s" % t)
        await c.execute("DELETE FROM improvement_candidates")
        await c.execute("DELETE FROM improvement_holdouts")
        if await c.fetchval("SELECT to_regclass('agent_tasks')"):
            mine = ("SELECT task_id FROM agent_tasks WHERE kind='IMPROVEMENT' "
                    "AND (task_id LIKE 'imp:%' OR task_id LIKE 'audt%')")
            await c.execute("DELETE FROM agent_task_events WHERE task_id IN "
                            "(%s)" % mine)
            await c.execute("DELETE FROM agent_tasks WHERE task_id IN (%s)"
                            % mine)
            await c.execute(
                "DELETE FROM agent_policy_versions WHERE policy_key = ANY($1)",
                ["collection.pass_limit", "derek.entry_threshold",
                 "audrey.report_thresholds"])
        await c.execute("DELETE FROM bettor_xavier_execution_events "
                        " WHERE account_id=$1", ACCT)
        await c.execute("DELETE FROM bettor_xavier_decisions "
                        " WHERE account_id=$1", ACCT)
        await c.execute("DELETE FROM bettor_funded_settlement_rechecks "
                        " WHERE intent_id LIKE $1", PFX + "%")
        for t in ("bettor_funded_economics", "bettor_funded_fills",
                  "bettor_funded_intents"):
            await c.execute("DELETE FROM %s WHERE intent_id LIKE $1" % t,
                            PFX + "%")
        await c.execute("DELETE FROM bettor_funded_portfolio_groups "
                        " WHERE group_id LIKE $1", PFX + "%")
        await c.execute("DELETE FROM bettor_pair_observation_attempts "
                        " WHERE pass_id LIKE $1", PFX + "%")
        if await c.fetchval("SELECT to_regclass('derek_entry_decisions')"):
            await c.execute("DELETE FROM derek_entry_decisions "
                            " WHERE decision_id LIKE $1", PFX + "derek-%")
        await c.execute("DELETE FROM external_valuations "
                        " WHERE experiment_id LIKE $1", PFX + "%")
        await c.execute("DELETE FROM ext_candidate_outcomes "
                        " WHERE cycle_id LIKE $1", PFX + "%")
        await c.execute("DELETE FROM ingestion_state WHERE key=$1",
                        "ext_pinnacle_last_cycle")


async def purge_report(c, report_id: str):
    """One report's versions (a test that audits the CURRENT day, whose
    report id is outside the 2032 range `purge` clears)."""
    async with c.transaction():
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute("DELETE FROM audrey_audit_reports WHERE report_id=$1",
                        report_id)


def ts(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc)


def side_payout(long_price, side):
    return long_price if side == LONG else 1.0 - long_price


async def position(c, n, *, fill_at, settle_at=None, long_price=1.0,
                   side=LONG, provisional_fee=False, qty=10, price=0.5,
                   void=False):
    """ONE SYNTHETIC SINGLE-LEG POSITION: `qty` contracts at `price`,
    filled at `fill_at`, settled by the venue at `settle_at` (None: open)."""
    gid, iid = PFX + "g%d" % n, PFX + "i%d" % n
    ev, slug = PFX + "ev%d" % n, PFX + "slug%d" % n
    open_ = settle_at is None
    cash = qty * price
    await c.execute(
        "INSERT INTO bettor_funded_portfolio_groups (group_id, account_id, "
        " venue, event_key, structure, hedge_intent) VALUES "
        " ($1,$2,$3,$4,'SINGLE_LEG','NOT_APPLICABLE')", gid, ACCT, VENUE, ev)
    reading = "EXPLICIT_VOID" if void else "REPORTED_SETTLEMENT"
    settlement = {} if open_ else {
        "payout_price": None if void else long_price,
        "terminal_reading": reading, "at": settle_at}
    await c.execute(
        "INSERT INTO bettor_funded_intents (intent_id, account_id, venue, "
        " venue_class, us_market_slug, event_key, order_intent, limit_price, "
        " quantity, collateral_usd, effective_digest, state, kind, "
        " residual_qty, closed_at, closed_reason, settlement, "
        " portfolio_group_id, leg_role, created_at) VALUES ($1,$2,$3,"
        " 'FUNDED',$4,$5,$6,$7,$8,$9,'d','FILLED','ENTRY',$10,$11,$12,"
        " $13::jsonb,$14,'PRIMARY',$15)",
        iid, ACCT, VENUE, slug, ev, side, price, qty, cash,
        (qty if open_ else 0), None if open_ else ts(settle_at),
        None if open_ else ("VOIDED_BY_THE_VENUE" if void
                            else "SETTLED_BY_THE_VENUE"),
        json.dumps(settlement), gid, ts(fill_at - 60))
    fid = "fvf:vo-%s:1" % iid
    await c.execute(
        "INSERT INTO bettor_funded_fills (fill_id, intent_id, venue_order_id, "
        " venue_fill_id, at, qty, price, cash_usd, fee_usd, fee_basis) "
        " VALUES ($1,$2,$3,'1',$4,$5,$6,$7,0.1,'TEST_SYNTHETIC')",
        fid, iid, "vo-" + iid, ts(fill_at), qty, price, cash)
    rows = [("fev:%s:CASH" % fid, fill_at, "ENTRY_COST", -cash, False),
            ("fev:%s:FEE" % fid, fill_at, "FEE", -0.1, provisional_fee)]
    if not open_:
        pay = cash if void else side_payout(long_price, side) * qty
        rows.append(("fev:%s:SETTLEMENT:%s" % (iid, reading), settle_at,
                     "SETTLEMENT", pay, False))
    for eid, at, kind, amt, prov in rows:
        await c.execute(
            "INSERT INTO bettor_funded_economics (event_id, intent_id, at, "
            " kind, amount_usd, qty, basis, provisional) VALUES "
            " ($1,$2,$3,$4,$5,$6,'TEST_SYNTHETIC',$7)", eid, iid, ts(at),
            kind, amt, qty if kind == "SETTLEMENT" else None, prov)
    if not open_:
        await c.execute(
            "UPDATE bettor_funded_portfolio_groups SET closed_at=$2, "
            " closure='BOTH_LEGS_SETTLED' WHERE group_id=$1", gid,
            ts(settle_at))
    return {"group_id": gid, "intent_id": iid, "slug": slug, "fixture": ev,
            "fill_id": fid}


async def decide(c, pos, *, decided, p, chosen="HOLD", alts=None,
                 elig=None, digest=None, qty=10, basis=5.0, reasoning=None):
    from sportsassets import bettor_xavier as X
    alts = alts if alts is not None else [
        {"action": "HOLD", "qty": qty, "value_usd": round(qty * p - basis, 6),
         "cash_at_settlement_usd": qty * p},
        {"action": "DIRECT_EXIT", "qty": qty, "value_usd": 0.9,
         "slice_value_usd": 0.9, "cash_now_usd": 5.9, "fees_usd": 0.1,
         "limit_price": 0.6, "depth": 25, "quote_at": decided - 5,
         "us_market_slug": pos["slug"],
         "plan_digest": "pd-%s-%d" % (pos["intent_id"], int(decided))},
        {"action": "REDUCE", "blocker": "NO_EXECUTABLE_DEPTH",
         "value_usd": None}]
    got = await X.record_decision(
        c, account_id=ACCT, venue=VENUE, intent_id=pos["intent_id"],
        decided_at=decided, responsibility_state=X.HELD,
        execution_eligibility=elig or (X.E_HOLD if chosen == "HOLD"
                                       else X.E_SUBMISSION_DISABLED),
        alternatives=alts, reasoning=reasoning or {
            "why": "synthetic test decision"},
        expected_economics={}, residual_exposure={"held_qty": qty,
                                                  "unpaired_qty": qty},
        evidence={"probability_source": "SYNTHETIC_TEST", "probability": p,
                  "model_version": "synthetic-model-v1"},
        obligations=[], chosen_action=chosen, chosen_plan_digest=digest,
        portfolio_group_id=pos["group_id"], us_market_slug=pos["slug"])
    assert got["ok"], got
    return got["xavier_decision_id"]


_EV_ID = [0]


async def valuation(c, *, fixture, decided, decision="NO_TRADE",
                    refusals=("NO_ACTION_HAS_POSITIVE_NET_EDGE",), p=0.5,
                    price=None, cost=None, edge=None, outcome=None,
                    outcome_at=None, submitted=False, size=None,
                    internal_p=None):
    """ONE SYNTHETIC ENTRY_DECISION VALUATION. Inserted BEFORE its outcome
    (the table refuses otherwise); the outcome is joined by UPDATE.

    `internal_p`: the internal probability Derek recorded for it (a
    synthetic REFUSE decision row carrying model_p), which Derek's active
    policy (V2) averages with the valuation's Pinnacle probability `p`. None
    records no Derek decision, so the row has no internal probability."""
    _EV_ID[0] += 1
    admissible = decision == "BUY"
    rid = await c.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, us_market_slug, "
        " contract_selection, sport_family, market, event_key, raw_odds, "
        " outcomes_priced, expected_outcomes, observed_at, mapped_outcome, "
        " probability, executable_price, cost_per_contract, "
        " estimated_edge_per_contract, decision, admissible, refusals, "
        " proposed_size, decided_at, age_s) VALUES ($1,'synthetic-v1',"
        " 'EXTERNAL_BOOKMAKER_VALUATION','test','test','power','PMUS',$2,"
        " 'HOME','soccer','moneyline',$3,'{}'::jsonb,2,2,$4,'HOME',$5,$6,$7,"
        " $8,$9,$10,$11::text[],$12,$13,3.0) RETURNING id",
        PFX + "exp", "%sv-%d" % (PFX, _EV_ID[0]), fixture, ts(decided - 2),
        p, price, cost, edge, decision, admissible,
        list(refusals if not admissible else []), size, ts(decided))
    if internal_p is not None:
        await c.execute(
            "INSERT INTO derek_entry_decisions (decision_id, valuation_id, "
            " fixture, us_market_slug, side, decided_at, policy_version, "
            " pinnacle_p, model_p, model_version, checks, verdict, refusal, "
            " decided_by) VALUES ($1,$2,$3,$4,'ORDER_INTENT_BUY_LONG',$5,"
            " 'DEREK_ENTRY_POLICY_V2',$6,$7,'synthetic-model','[]'::jsonb,"
            " 'REFUSE','SYNTHETIC_TEST_RECORD','AFTER_CYCLE')",
            "%sderek-%d" % (PFX, rid), rid, fixture,
            "%sv-%d" % (PFX, _EV_ID[0]), ts(decided), p, float(internal_p))
    if outcome is not None:
        await c.execute(
            "UPDATE external_valuations SET outcome_known=true, outcome=$2, "
            " outcome_at=$3 WHERE id=$1", rid, int(outcome), ts(outcome_at))
    return rid
