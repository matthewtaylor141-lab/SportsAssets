"""Synthetic rows for the learning-layer proofs (tests/test_poslearn_*).

ALL DATA HERE IS SYNTHETIC TEST DATA written into a scratch test database
inside a transaction the test rolls back. Migration 218 is applied INSIDE
that transaction (it is idempotent), so the template database stays at its
migrated head.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import uuid

import asyncpg

DSN = os.environ.get("RN1X_TEST_DSN", "")
ROOT = pathlib.Path(__file__).resolve().parents[1]
UP = (ROOT / "migrations" / "218_model_agent_tournaments.sql").read_text()
DOWN = (ROOT / "migrations" / "rollback" /
        "218_model_agent_tournaments.down.sql").read_text()
LONG = "ORDER_INTENT_BUY_LONG"


def uid(tag="x") -> str:
    return "%s%s" % (tag, uuid.uuid4().hex[:10])


async def open_tx():
    conn = await asyncpg.connect(DSN)
    tr = conn.transaction()
    await tr.start()
    await conn.execute(UP)
    return conn, tr


async def close_tx(conn, tr):
    try:
        await tr.rollback()
    finally:
        await conn.close()


async def valuation(conn, *, at, p, price, fee=0.01, event_key=None,
                    slug=None, sport="baseball", market="h2h",
                    payout_event="HOME", age_s=2.0) -> dict:
    """A CALIBRATION_ONLY valuation, recorded BEFORE its outcome (the table's
    prospective trigger), carrying the DISPLAYED price as evidence only."""
    slug = slug or uid("pl-mkt-")
    event_key = event_key or ("pl-ev-" + slug)
    ev = {"usable_for_orders": False,
          "compared_at_the_displayed_price": {
              "price": price, "cost_per_contract": fee,
              "edge_per_contract": p - price - fee,
              "usable_for_orders": False}}
    vid = await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, age_s, probability, decision, admissible, refusals, "
        " why, payout_event, payout_is_complement, buy_intent, ladder_side, "
        " record_purpose, decided_at, event_key, calibration_only_evidence) "
        "VALUES ('POSLEARN_TEST','PINNACLE_DEVIG_V1',"
        " 'EXTERNAL_BOOKMAKER_VALUATION','test','pinnacle','power','PMUS',"
        " $1,$1,'HOME',$2,$3,'FULL_GAME','{}'::jsonb,2,2,to_timestamp($4),"
        " to_timestamp($4),$5,$6,'NO_TRADE',false,"
        " ARRAY['VENUE_BOOK_CURRENCY_NOT_ESTABLISHED']::text[],'synthetic',"
        " $7,false,$8,'ASK','CALIBRATION_ONLY',to_timestamp($4),$9,"
        " $10::jsonb) RETURNING id",
        slug, sport, market, float(at), float(age_s), float(p), payout_event,
        LONG, event_key, json.dumps(ev))
    return {"id": vid, "slug": slug, "event_key": event_key}


async def resolve(conn, vid, outcome, *, at,
                  basis="VENUE_SETTLEMENT_PRICE"):
    await conn.execute(
        "UPDATE external_valuations SET outcome_known = TRUE, outcome = $2, "
        " outcome_basis = $3, outcome_at = to_timestamp($4) WHERE id = $1",
        vid, int(outcome), basis, float(at))


def truth(p, shrink=0.4):
    """The synthetic world: the raw PinnAPI p is overconfident."""
    return 0.5 + shrink * (p - 0.5)


async def seed_history(conn, *, now, n=260, seed=7, sport="baseball"):
    """n resolved valuations in the past, outcomes KNOWN before now."""
    rng = random.Random(seed)
    for i in range(n):
        p = (0.03, 0.97)[i % 2]
        q = truth(p)
        at = now - 20 * 86400.0 + i * 600.0
        v = await valuation(conn, at=at, p=p, price=round(q - 0.02, 4),
                            sport=sport)
        await resolve(conn, v["id"], 1 if rng.random() < q else 0,
                      at=at + 3600.0)


async def protected_counts(conn) -> dict:
    try:
        from tests import intel_fixture as F
    except ImportError:                                         # pragma: no cover
        import intel_fixture as F
    out = await F.protected_counts(conn)
    for t in ("paper_policy_parameter_heads",
              "paper_policy_parameter_versions", "paper_improvement_proposals",
              "intel_calibration_overlays", "karen_challenges",
              "agent_finding_stages"):
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t):
            out[t] = await conn.fetchval('SELECT count(*) FROM "%s"' % t)
    out["external_valuations_content"] = await conn.fetchval(
        "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) "
        "  FROM external_valuations t")
    return out
