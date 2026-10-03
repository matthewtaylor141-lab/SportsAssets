"""Synthetic rows for the SHADOW intelligence proofs (tests/test_intel_*).

ALL DATA HERE IS SYNTHETIC TEST DATA written into a scratch test database
inside a transaction the test rolls back. Each helper inserts exactly the
columns the intel modules read, under a fresh paper account / experiment id
so other suites' rows never enter a measurement.
"""
from __future__ import annotations

import json
import uuid

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

SIM_VERSION = "PAPER_SIM_V1"
STRATEGY = "PINNACLE_ONLY_PAPER_BENCHMARK"
LONG = "ORDER_INTENT_BUY_LONG"


def uid(tag="x") -> str:
    return "%s%s" % (tag, uuid.uuid4().hex[:10])


def intent_of(direction, side):
    if direction == "BUY":
        return ("ORDER_INTENT_BUY_SHORT" if side == "SHORT"
                else "ORDER_INTENT_BUY_LONG")
    return ("ORDER_INTENT_SELL_SHORT" if side == "SHORT"
            else "ORDER_INTENT_SELL_LONG")


async def valuation(conn, *, experiment_id, at, p, event_key=None, slug=None,
                    sport="baseball", market="h2h", age_s=5.0,
                    refusals=("VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",),
                    outcome=None, basis="VENUE_SETTLEMENT_PRICE",
                    payout_event="HOME") -> int:
    slug = slug or uid("intel-syn-")
    vid = await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, age_s, probability, decision, admissible, refusals, "
        " why, payout_event, payout_is_complement, buy_intent, ladder_side, "
        " record_purpose, decided_at, event_key, calibration_only_evidence) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'test','pinnacle','power','PMUS',$2,$2,'HOME',$3,$4,'FULL_GAME',"
        " '{}'::jsonb,2,2,to_timestamp($5),to_timestamp($5),$6,$7,"
        " 'NO_TRADE',false,$8::text[],'synthetic',$9,false,$10,'ASK',"
        " 'CALIBRATION_ONLY',to_timestamp($5),$11,$12::jsonb) RETURNING id",
        experiment_id, slug, sport, market, float(at), float(age_s),
        None if p is None else float(p), list(refusals), payout_event, LONG,
        event_key or ("e-" + slug),
        json.dumps({"usable_for_orders": False}))
    if outcome is not None:
        await conn.execute(
            "UPDATE external_valuations SET outcome_known = TRUE, outcome=$2,"
            " outcome_basis=$3, outcome_at=to_timestamp($4) WHERE id=$1",
            vid, int(outcome), basis, float(at) + 3600.0)
    return vid


async def decision(conn, acct, *, at, p, verdict="ENTER", slug=None,
                   side="LONG", limit=0.5, qty=100.0, vwap=None,
                   fees_usd=None, depth=None, event_key=None,
                   valuation_id=None, void_upper=None) -> dict:
    did = "paperdec:" + uid()
    slug = slug or uid("intel-mkt-")
    econ = {"depth_within_limit": depth}
    if vwap is not None:
        econ["acquisition"] = {"vwap": vwap, "fees_usd": fees_usd,
                               "settlement_states": {
                                   "void_upper_95": void_upper}}
    label = {"event_key": event_key or ("e-" + slug),
             "market_type": "MONEYLINE", "pays_on": "HOME",
             "home_team": "Test Home", "away_team": "Test Away",
             "competition": "MLB"}
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, fixture, "
        " label, verdict, refusal, p_pinnacle, internal_model, pinnacle, "
        " limit_price, proposed_qty, economics, qualification_gaps, "
        " policy_version, simulator_version, strategy) VALUES ($1,$2,$3,"
        " to_timestamp($4),$5,$6,$7,$8,$9::jsonb,$10,$11,$12,'{}'::jsonb,"
        " '{}'::jsonb,$13,$14,$15::jsonb,'[]'::jsonb,'TEST',$16,$17)",
        did, acct["session_id"], acct["account_id"], float(at), valuation_id,
        slug, side, "fx-" + slug, json.dumps(label), verdict,
        None if verdict == "ENTER" else "TEST_REFUSAL",
        None if p is None else float(p), limit, qty, json.dumps(econ),
        SIM_VERSION, STRATEGY)
    return {"decision_id": did, "slug": slug, "side": side,
            "event_key": label["event_key"]}


async def order(conn, acct, *, group_id, slug, role="ENTRY", direction="BUY",
                side="LONG", qty, price, decision_id=None, at,
                event_key=None) -> str:
    oid = "paperord:" + uid()
    wire = price if side == "LONG" else round(1 - price, 6)
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, filled_qty, state, "
        " decision_id, decided_at, eligible_at, expires_at, "
        " simulator_version, strategy, terminal_at) VALUES ($1,$1,$2,$3,$4,"
        " $5,$6,$7,$8,$9,$10,$11::jsonb,'MARKETABLE','IOC',true,$12,$13,$14,"
        " $12,'FILLED',$15,to_timestamp($16),to_timestamp($16),"
        " to_timestamp($16 + 90),$17,$18,to_timestamp($16 + 2))",
        oid, acct["account_id"], acct["session_id"], group_id, role,
        direction, side, intent_of(direction, side), slug, "fx-" + slug,
        json.dumps({"event_key": event_key or ("e-" + slug)}), qty, price,
        wire, decision_id, float(at), SIM_VERSION, STRATEGY)
    return oid


async def fill(conn, acct, *, order_id, group_id, slug, role="ENTRY",
               direction="BUY", side="LONG", qty, price, fee=0.0, at,
               event_key=None) -> str:
    fid = "paperfill:" + uid()
    wire = price if side == "LONG" else round(1 - price, 6)
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, label, qty, price, wire_price, fee_usd, "
        " gross_usd, filled_at, basis, simulator_version, strategy) VALUES "
        " ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,$16,"
        " to_timestamp($17),'DEPTH_WALK_WITHIN_LIMIT',$18,$19)",
        fid, order_id, acct["account_id"], acct["session_id"], group_id,
        role, direction, side, slug, "fx-" + slug,
        json.dumps({"event_key": event_key or ("e-" + slug)}), qty, price,
        wire, fee, round(qty * price, 6), float(at), SIM_VERSION, STRATEGY)
    return fid


async def position(conn, acct, *, slug, qty, price, fee=0.0, at, side="LONG",
                   group_id=None, decision_id=None, event_key=None) -> str:
    """An ENTRY order + one fill: an open paper position."""
    g = group_id or ("paper_group_" + uid())
    oid = await order(conn, acct, group_id=g, slug=slug, side=side, qty=qty,
                      price=price, decision_id=decision_id, at=at,
                      event_key=event_key)
    await fill(conn, acct, order_id=oid, group_id=g, slug=slug, side=side,
               qty=qty, price=price, fee=fee, at=at + 2, event_key=event_key)
    return g


async def settle(conn, acct, *, group_id, slug, side="LONG", qty, outcome,
                 payout_per_contract, at) -> str:
    sid = "papersettle:" + uid()
    pk = "paperpos:%s:%s:%s:%s" % (acct["account_id"], group_id, slug, side)
    await conn.execute(
        "INSERT INTO paper_settlements (settlement_id, account_id, "
        " position_key, settlement_event_key, version, group_id, "
        " us_market_slug, holding_side, qty, outcome, payout_per_contract, "
        " payout_usd, evidence, evidence_source, settled_at) VALUES ($1,$2,"
        " $3,'test',1,$4,$5,$6,$7,$8,$9,$10,'{}'::jsonb,'TEST_EVIDENCE',"
        " to_timestamp($11))",
        sid, acct["account_id"], pk, group_id, slug, side, qty, outcome,
        payout_per_contract, round(qty * payout_per_contract, 6), float(at))
    return sid


async def equity(conn, acct, *, at, equity_usd):
    await conn.execute(
        "INSERT INTO paper_equity_snapshots (session_id, account_id, at, "
        " cash_usd, reserved_usd, marked_value_usd, equity_usd, "
        " equity_excluding_unmarked_usd, unmarked_positions, "
        " realized_pnl_usd) VALUES ($1,$2,to_timestamp($3),$4,0,0,$4,$4,0,0)",
        acct["session_id"], acct["account_id"], float(at), equity_usd)


async def book(conn, slug, at, *, bids=(), offers=()) -> int:
    return await H.observe(conn, slug, at, bids=bids, offers=offers)


# Tables the shadow layer must never write: order, fill, ledger, limit,
# control, execution and probability tables. Counted before/after a cycle.
PROTECTED_TABLES = (
    "paper_orders", "paper_fills", "paper_order_events", "paper_ledger",
    "paper_settlements", "paper_decisions", "paper_handoffs",
    "paper_xavier_reviews", "paper_control", "paper_accounts",
    "paper_sessions", "paper_equity_snapshots", "external_valuations",
    "external_source_calibration", "execution_intents", "execmirror_orders",
    "execmirror_fills", "execmirror_control", "execmirror_events",
    "kalshi_live_intents", "kalshi_live_fills", "kalshi_smalllive_control",
    "smalllive_handoffs", "smalllive_reviews", "ingestion_state",
    "live_rule_artifacts", "agent_policy_artifacts")


async def protected_counts(conn) -> dict:
    out = {}
    for t in PROTECTED_TABLES:
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t):
            out[t] = await conn.fetchval('SELECT count(*) FROM "%s"' % t)
    for t in [r["relname"] for r in await conn.fetch(
            "SELECT relname FROM pg_class WHERE relkind = 'r' AND "
            " relname LIKE 'bettor_funded%'")]:
        out[t] = await conn.fetchval('SELECT count(*) FROM "%s"' % t)
    return out
