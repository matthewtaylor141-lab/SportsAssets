"""EV AND PRICING METHODOLOGY AUDIT (RC6 lane ev-audit): evidence reads over
a MIGRATED database. ALL DATA HERE IS SYNTHETIC TEST DATA written into a
scratch database inside a transaction each test rolls back.

  §1  PROBABILITY LABELS ARE A POSITION'S LATEST SETTLEMENT VERSION.
      paper_settlements is versioned (correct_settlement appends v2). The
      label CTE read EVERY version: a WON v1 corrected to VOID_REFUND kept
      scoring the decision as a win, and a WON v1 corrected to LOST dropped
      the market (min(y) <> max(y)). Production has no correction yet
      (research read 2026-10-09: 194 settlements, all version 1), so this is
      latent there -- and silent the day one happens.
  §2  THE TWIN'S OPTIMISTIC DIAGNOSIS SEES THE PRE-ACTIVATION BOOK. TWIN_SQL
      loaded only the [eligible, expires] books, so the optimistic replay
      (the package twin, which crossed on the last book AT OR BEFORE
      activation) never had one and named every PAPER fill
      CANCELLED_BEFORE_FIRST_ELIGIBLE_BOOK -- production RC5: 7 of 7. The
      repaired replay is unchanged: it never reads a pre-eligibility book.
  §3  CAPACITY COUNTS AN OPPORTUNITY ONCE. Re-evaluating one contract added
      its capital and expected net again on every evaluation.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest

DSN = os.environ.get("RN1X_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

SIM = "PAPER_SIM_V1"
STRATEGY = "PINNACLE_ONLY_PAPER_BENCHMARK"


def _uid(tag):
    return "%s%s" % (tag, uuid.uuid4().hex[:10])


def run_rolled_back(fn):
    import asyncpg

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                return await fn(conn)
            finally:
                await tr.rollback()
        finally:
            await conn.close()
    return asyncio.run(go())


async def _decision(conn, acct, *, slug, side, at, obs_id, p):
    did = "paperdec:" + _uid("ev")
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, verdict, internal_model, "
        " pinnacle, qualification_gaps, policy_version, simulator_version, "
        " strategy, book_obs_id, economics) VALUES ($1,$2,$3,to_timestamp($4),"
        " $5,$6,'ENTER','{}'::jsonb,'{}'::jsonb,'[]'::jsonb,'TEST',$7,$8,$9,"
        " $10::jsonb)",
        did, acct["session_id"], acct["account_id"], float(at), slug, side,
        SIM, STRATEGY, obs_id, json.dumps({"probability": p}))
    return did


async def _settlement(conn, acct, *, slug, side, version, outcome, per, at,
                      supersedes=None):
    pk = "paperpos:%s:g-%s:%s:%s" % (acct["account_id"], slug, slug, side)
    sid = "paperset:" + _uid("ev")
    await conn.execute(
        "INSERT INTO paper_settlements (settlement_id, account_id, "
        " position_key, settlement_event_key, version, supersedes, group_id,"
        " us_market_slug, holding_side, qty, outcome, payout_per_contract, "
        " payout_usd, evidence, evidence_source, settled_at) VALUES ($1,$2,"
        " $3,'test-event',$4,$5,$6,$7,$8,10,$9,$10,$11,'{}'::jsonb,"
        " 'TEST_EVIDENCE',to_timestamp($12))",
        sid, acct["account_id"], pk, version, supersedes, "g-" + slug, slug,
        side, outcome, per, 10 * per, float(at))
    return sid


# ── §1 labels are the latest settlement version ──────────────────────

def test_a_corrected_settlement_labels_by_its_latest_version():
    from sportsassets.completion import evidence as EV

    async def fn(conn):
        acct = await H.new_account(conn, "evlab")
        now = time.time()
        out = {}
        for case, chain in (
                ("won_then_void", (("WON", 1.0), ("VOID_REFUND", 0.42))),
                ("won_then_lost", (("WON", 1.0), ("LOST", 0.0))),
                ("won_only", (("WON", 1.0),))):
            slug = _uid("evlab-%s-" % case)
            obs = await H.observe(conn, slug, now - 3600,
                                  bids=[(0.40, 50)], offers=[(0.44, 50)])
            did = await _decision(conn, acct, slug=slug, side="LONG",
                                  at=now - 3600, obs_id=obs, p=0.47)
            prev = None
            for v, (oc, per) in enumerate(chain, start=1):
                prev = await _settlement(conn, acct, slug=slug, side="LONG",
                                         version=v, outcome=oc, per=per,
                                         at=now - 1800 + v, supersedes=prev)
            out[case] = did
        rows = [dict(r) for r in await conn.fetch(EV.PROB_SQL, 30, 100000)]
        got = {r["decision_id"]: r["y_long"] for r in rows}
        return out, got
    out, got = run_rolled_back(fn)
    # VOID is not an outcome: the superseded WON must not label it
    assert out["won_then_void"] not in got
    # the correction to LOST is the label; the market is not dropped
    assert got.get(out["won_then_lost"]) == 0.0
    # an uncorrected settlement still labels as before
    assert got.get(out["won_only"]) == 1.0


# ── §2 the twin's optimistic diagnosis sees the pre-activation book ──

def test_the_optimistic_diagnosis_is_given_the_pre_activation_book():
    from sportsassets.completion import evidence as EV

    async def fn(conn):
        acct = await H.new_account(conn, "evtwin")
        now = time.time()
        slug = _uid("evtwin-")
        # the last book BEFORE activation crosses the limit, and so does the
        # first book in the window: PAPER filled, both twins would have
        await H.observe(conn, slug, now - 5, bids=[(0.48, 100)],
                        offers=[(0.50, 100)])
        await H.observe(conn, slug, now + 1, bids=[(0.48, 100)],
                        offers=[(0.50, 100)])
        oid = "paperord:" + _uid("ev")
        g = "g-" + slug
        await conn.execute(
            "INSERT INTO paper_orders (order_id, idempotency_key, account_id,"
            " session_id, group_id, role, direction, holding_side, intent, "
            " us_market_slug, order_type, time_in_force, allow_partial, qty, "
            " limit_price, wire_price, filled_qty, state, decided_at, "
            " eligible_at, expires_at, simulator_version, strategy, "
            " terminal_at, terminal_reason) VALUES ($1,$1,$2,$3,$4,'ENTRY',"
            " 'BUY','LONG','ORDER_INTENT_BUY_LONG',$5,'MARKETABLE','IOC',"
            " true,10,0.50,0.50,10,'FILLED',to_timestamp($6),to_timestamp($6),"
            " to_timestamp($6 + 90),$7,$8,to_timestamp($6 + 2),'FILLED')",
            oid, acct["account_id"], acct["session_id"], g, slug, now, SIM,
            STRATEGY)
        await conn.execute(
            "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
            " account_id, session_id, group_id, role, direction, "
            " holding_side, us_market_slug, qty, price, wire_price, fee_usd,"
            " gross_usd, filled_at, basis, simulator_version, strategy) "
            "VALUES ($1,$1,$2,$3,$4,$5,'ENTRY','BUY','LONG',$6,10,0.50,0.50,"
            " 0,5,to_timestamp($7),'DEPTH_WALK_WITHIN_LIMIT',$8,$9)",
            "paperfill:" + _uid("ev"), oid, acct["account_id"],
            acct["session_id"], g, slug, now + 2, SIM, STRATEGY)
        rows = await conn.fetch(EV.TWIN_SQL, float(
            EV.TWIN_DIAGNOSIS_WINDOW_END), 100000)
        mine = [json.loads(r["j"]) for r in rows]
        return [o for o in mine if o["order_id"] == oid], now
    orders, now = run_rolled_back(fn)
    assert len(orders) == 1
    books = orders[0]["books"]
    assert any(float(b["at"]) < now for b in books), \
        "the last pre-activation book is loaded"
    rep = EV.twin_block(orders)
    # the repaired replay is unchanged: it fills on the first ELIGIBLE book
    assert rep["agree"] == 1 and rep["lookahead_violations"] == 0
    # and the optimistic diagnosis no longer invents an early cancel
    assert rep["optimistic_twin_mismatch_taxonomy"] == {}


# ── §3 capacity counts one opportunity once ──────────────────────────

def test_re_evaluating_one_opportunity_adds_no_capacity():
    from sportsassets.redteam import readiness as R

    async def fn(conn):
        slug = _uid("evcap-")

        async def ev(at):
            await conn.execute(
                "INSERT INTO paper_profitability_evaluations (account_id, "
                " strategy, stage, us_market_slug, holding_side, fixture, "
                " qty_in, qty_out, ev_per_contract_usd, fill_probability, "
                " all_in_ev_usd, market_price, expected_hold_hours, verdict, "
                " refusal, evaluated_at, detail, label, authority) VALUES "
                " ('paper_test_evcap',$1,'DECISION',$2,'LONG','fx-evcap',"
                " 7,7,0.03,0.9,0.21,0.4,2,'CASH','TEST_SYNTHETIC',"
                " to_timestamp($3),'{}'::jsonb,'PAPER',"
                " 'PAPER_ONLY_NO_CAPITAL_AUTHORITY')",
                STRATEGY, slug, float(at))
        now = time.time()
        await ev(now - 600)
        before = await R.capacity_points(conn)
        for i in range(29):
            await ev(now - 590 + i)
        after = await R.capacity_points(conn)
        return before, after
    before, after = run_rolled_back(fn)
    b = next(p for p in before if p["bucket"] == [1, 10])
    a = next(p for p in after if p["bucket"] == [1, 10])
    # 29 more evaluations of the SAME opportunity are not 29 more positions
    assert a["capital_usd"] == b["capital_usd"]
    assert a["expected_net"] == b["expected_net"]
