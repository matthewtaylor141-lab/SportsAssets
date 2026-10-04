"""Shared fixtures for the R30B agents stream (migration 234): durable work
queues, scorecards, lesson usage and root-cause clusters.

ALL DATA IS SYNTHETIC TEST DATA in the SHAPES PRODUCTION WRITES (read-only
evidence, research-sql run 37226555657, 2026-10-04 19:00Z): a paper decision
carries the columns paper_benchmark writes; a stale-HOLD review carries the
measure keys production's reviews carry ({at, best_exit_at_review,
book_obs_id, disclosure, economics_label, exceptional_states, internal_model,
measure_is, p, p_internal, source, stale, strategy, void_applied, why} with
stale = true and no probability_age_s). Every scenario runs inside one
transaction that is rolled back.

HAND-BUILT FIXTURES vs THE REAL WRITERS (R30B review). `decision`,
`position`, `exit_fill`, `stale_hold_review` and `audrey_finding` are
HAND-BUILT INSERTs, said so: they reproduce production's row shapes where a
proof needs exact control of times and outcomes. In particular
`stale_hold_review` reproduces the PRE-R30 defect shape (a HOLD on a stale
probability), which no writer on this branch can produce any more: the R30
freshness rule makes paper_xavier record WAITING_FOR_FRESH_EVIDENCE on
stale evidence. Where a real writer exists the proofs use it:
`held_position` / `real_review` (the ledger, the simulator,
paper_xavier.step_handoff and review_group), `derek_decides`
(paper_derek.decide_one), paper_audrey.finding for Audrey's findings, and
the agents' own runners for every queue item, challenge and cluster.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import asyncpg
import pytest

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "234_agent_operations.sql").read_text()
DOWN = (MIG / "rollback" / "234_agent_operations.down.sql").read_text()
NOW = 1_790_500_000.0
#: A TIME NO OTHER PROOF WRITES AT (2033-05-18). The scorecards and the
#: queue producers read EVERY row of a window, and a shared test database
#: keeps rows other proofs commit (seen: test_xavier_thesis_reallocate_
#: value_add's and test_live_parity's accounts at 2026-09-25 / wall clock),
#: so a proof that asserts a window's exact figures runs here.
ISOLATED = 2_000_000_000.0
EXPLORATION = "PINNACLE_EXPLORATION_PAPER"


def j(v):
    return json.loads(v) if isinstance(v, str) else v


async def tx():
    conn = await asyncpg.connect(H.DSN)
    t = conn.transaction()
    await t.start()
    await conn.execute(UP)
    return conn, t


async def done(conn, t):
    await t.rollback()
    await conn.close()


async def expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


async def account(conn, tag="ops", now=NOW - 10 * 86400):
    return await H.new_account(conn, tag, now=now)


async def decision(conn, acct, *, at, slug=None, side="LONG",
                   verdict="ENTER", refusals=(), strategy=EXPLORATION,
                   p=0.6, qty=100, limit=0.40, did=None, pinnacle=None,
                   valuation_id=None, book_obs_id=None,
                   economics=None) -> str:
    """A paper decision row with paper_benchmark's columns. `pinnacle` is
    the decision's pinnacle record ({p, age_s, limit_s, qualification} as
    paper_benchmark writes it); p=None records no probability at all."""
    did = did or "paperdec:%s" % uuid.uuid4().hex[:24]
    refusals = list(refusals)
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, intent, fixture, label, "
        " verdict, refusal, refusals, p_internal, internal_model, "
        " p_pinnacle, pinnacle, p_blended, proposed_qty, limit_price, "
        " qualification_gaps, policy_version, policy_decision, "
        " simulator_version, strategy, economics, valuation_id, "
        " book_obs_id) VALUES ($1,$2,$3,"
        " to_timestamp($4),$5,$6,'ORDER_INTENT_BUY_LONG','fx-1','{}'::jsonb,"
        " $7,$8,$9,NULL,'{}'::jsonb,$10,$14::jsonb,$10,$11,$12,'[]'::jsonb,"
        " 'TEST_POLICY_V1','{}'::jsonb,'TEST',$13,$15::jsonb,$16,$17)",
        did, acct["session_id"], acct["account_id"], float(at),
        slug or "aec-test-%s" % uuid.uuid4().hex[:8], side, verdict,
        None if verdict == "ENTER" else (refusals[0] if refusals
                                         else "BELOW_MIN_GROSS_EDGE"),
        refusals, None if p is None else float(p), float(qty), float(limit),
        strategy, json.dumps(pinnacle or {}), json.dumps(economics or {}),
        valuation_id, book_obs_id)
    return did


SIM_VERSION = "PAPER_SIM_V1"


async def position(conn, acct, *, strategy=EXPLORATION, slug=None, qty=100,
                   price=0.40, at, decision_id=None, outcome=None,
                   payout=None, settle_at=None, fee=0.0) -> dict:
    """A filled paper ENTRY (order + fill, the ledger's own tables; the 223
    trigger classifies its sleeve from the strategy) and, optionally, its
    settlement. {"group_id", "slug", "position_key"}."""
    slug = slug or "aec-test-%s" % uuid.uuid4().hex[:8]
    gid = "paper_group_ops_%s" % uuid.uuid4().hex[:10]
    oid = "paperord:ops%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, filled_qty, state, "
        " decided_at, eligible_at, expires_at, simulator_version, strategy, "
        " terminal_at, decision_id) VALUES ($1,$1,$2,$3,$4,'ENTRY','BUY',"
        " 'LONG','ORDER_INTENT_BUY_LONG',$5,$6,'{}'::jsonb,'MARKETABLE',"
        " 'IOC',true,$7,$8,$8,$7,'FILLED',to_timestamp($9),to_timestamp($9),"
        " to_timestamp($9 + 90),$10,$11,to_timestamp($9 + 2),$12)",
        oid, acct["account_id"], acct["session_id"], gid, slug,
        "fx-" + slug, qty, price, float(at), SIM_VERSION, strategy,
        decision_id)
    fid = "paperfill:ops%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, label, qty, price, wire_price, fee_usd, "
        " gross_usd, filled_at, basis, simulator_version, strategy) VALUES "
        " ($1,$1,$2,$3,$4,$5,'ENTRY','BUY','LONG',$6,$7,'{}'::jsonb,$8,$9,"
        " $9,$10,$11,to_timestamp($12),'DEPTH_WALK_WITHIN_LIMIT',$13,$14)",
        fid, oid, acct["account_id"], acct["session_id"], gid, slug,
        "fx-" + slug, qty, price, fee, round(qty * price, 6), float(at) + 2,
        SIM_VERSION, strategy)
    pk = "paperpos:%s:%s:%s:LONG" % (acct["account_id"], gid, slug)
    if outcome is not None:
        await conn.execute(
            "INSERT INTO paper_settlements (settlement_id, account_id, "
            " position_key, settlement_event_key, version, group_id, "
            " us_market_slug, holding_side, qty, outcome, "
            " payout_per_contract, payout_usd, evidence, evidence_source, "
            " settled_at) VALUES ($1,$2,$3,'test',1,$4,$5,'LONG',$6,$7,$8,"
            " $9,'{}'::jsonb,'TEST_EVIDENCE',to_timestamp($10))",
            "papersettle:ops%s" % uuid.uuid4().hex[:10], acct["account_id"],
            pk, gid, slug, qty, outcome, payout, round(qty * payout, 6),
            float(settle_at if settle_at is not None else at + 3600))
    return {"group_id": gid, "slug": slug, "position_key": pk,
            "order_id": oid}


async def exit_fill(conn, acct, pos: dict, *, qty, price, at,
                    strategy=EXPLORATION, fee=0.0) -> str:
    """Xavier's EXIT of a position: a filled SELL order + fill on the same
    group (the ledger's own tables, as F.position writes the ENTRY)."""
    oid = "paperord:opsx%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, filled_qty, state, "
        " decided_at, eligible_at, expires_at, simulator_version, strategy, "
        " terminal_at) VALUES ($1,$1,$2,$3,$4,'EXIT','SELL','LONG',"
        " 'ORDER_INTENT_SELL_LONG',$5,$6,'{}'::jsonb,'MARKETABLE','IOC',"
        " true,$7,$8,$8,$7,'FILLED',to_timestamp($9),to_timestamp($9),"
        " to_timestamp($9 + 90),$10,$11,to_timestamp($9 + 2))",
        oid, acct["account_id"], acct["session_id"], pos["group_id"],
        pos["slug"], "fx-" + pos["slug"], qty, price, float(at),
        SIM_VERSION, strategy)
    fid = "paperfill:opsx%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, label, qty, price, wire_price, fee_usd, "
        " gross_usd, filled_at, basis, simulator_version, strategy) VALUES "
        " ($1,$1,$2,$3,$4,$5,'EXIT','SELL','LONG',$6,$7,'{}'::jsonb,$8,$9,"
        " $9,$10,$11,to_timestamp($12),'DEPTH_WALK_WITHIN_LIMIT',$13,$14)",
        fid, oid, acct["account_id"], acct["session_id"], pos["group_id"],
        pos["slug"], "fx-" + pos["slug"], qty, price, fee,
        round(qty * price, 6), float(at) + 2, SIM_VERSION, strategy)
    return fid


async def stale_hold_review(conn, acct, *, group_id, at,
                            strategy=EXPLORATION, stale=True,
                            recommendation="HOLD") -> str:
    """A Xavier HOLD review in production's pre-R30 shape (stale = true,
    no probability_age_s), as the HOLD_ON_STALE_PROBABILITY detector reads
    it."""
    rid = "paperrev:%s" % uuid.uuid4().hex[:24]
    measure = {"at": at, "best_exit_at_review": None, "book_obs_id": None,
               "disclosure": "TEST", "economics_label": "TEST",
               "exceptional_states": [], "internal_model": {},
               "measure_is": "HOLD_VALUE", "p": 0.55, "p_internal": None,
               "source": "PINNACLE_DEVIG_V1", "stale": bool(stale),
               "strategy": strategy, "void_applied": False,
               "why": "TEST_FIXTURE"}
    await conn.execute(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, "
        " account_id, group_id, reviewed_at, trigger, recommendation, "
        " measure, alternatives, selection, exposure, strategy) VALUES "
        " ($1,$2,$3,$4,to_timestamp($5),'SCHEDULED_BACKSTOP',$8,"
        " $6::jsonb,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,$7)",
        rid, acct["session_id"], acct["account_id"], group_id, float(at),
        json.dumps(measure), strategy, recommendation)
    return rid


async def requests(conn, kind=None, agent=None) -> list:
    rows = await conn.fetch(
        "SELECT r.*, (SELECT array_agg(e.state ORDER BY e.event_id) FROM "
        "       agent_work_request_events e WHERE e.request_id = "
        "       r.request_id) AS states, (SELECT row_to_json(e) FROM "
        "       agent_work_request_events e WHERE e.request_id = "
        "       r.request_id AND e.state IN ('COMPLETED', 'FAILED')) AS "
        "       terminal, EXISTS (SELECT 1 FROM agent_work_open o WHERE "
        "       o.request_id = r.request_id) AS is_open "
        "  FROM agent_work_requests r "
        " WHERE ($1::text IS NULL OR r.kind = $1) "
        "   AND ($2::text IS NULL OR r.agent_id = $2) "
        " ORDER BY r.enqueued_at, r.request_id", kind, agent)
    out = []
    for r in rows:
        d = dict(r)
        d["terminal"] = j(d["terminal"])
        d["detail"] = j(d["detail"])
        d["evidence_needed"] = j(d["evidence_needed"])
        out.append(d)
    return out


async def attempts(conn, request_id) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT outcome, blocker, extract(epoch FROM at)::float8 AS at, "
        "       extract(epoch FROM next_attempt_at)::float8 AS nxt, detail "
        "  FROM agent_work_request_events WHERE request_id = $1 "
        "   AND state = 'ATTEMPTED' ORDER BY event_id", request_id)]


async def audrey_finding(conn, acct, *, kind, at, severity="WARNING",
                         subject=None, strategy=None) -> str:
    """A paper_audrey_findings row in the operational audit's shape."""
    fid = "paperaud:%s" % uuid.uuid4().hex[:24]
    detail = {"statement": "TEST_FIXTURE"}
    if strategy:
        detail["strategy"] = strategy
    await conn.execute(
        "INSERT INTO paper_audrey_findings (finding_id, session_id, "
        " account_id, found_at, kind, severity, subject, detail) VALUES "
        " ($1,$2,$3,to_timestamp($4),$5,$6,$7,$8::jsonb)",
        fid, acct["session_id"], acct["account_id"], float(at), kind,
        severity, subject, json.dumps(detail))
    return fid


# ═════════════════════════════════════════════════════════════════════
# THE REAL WRITERS (R30B review: production-shaped rows written by the
# writers production runs, not hand-built INSERTs, wherever one exists)
# ═════════════════════════════════════════════════════════════════════

async def held_position(conn, tag, *, at, entry_age_s=3600.0):
    """A held completed-game paper position handed to Xavier through the
    REAL chain (paper_harness account, the ledger's submit_order, the
    simulator's fill, paper_xavier.step_handoff) at time base `at`: the
    entry valuation is `entry_age_s` old at `at`, so every later review of
    it is on STALE probability evidence. (account, group_id, slug)."""
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    from sportsassets.agents import paper_xavier as PX
    from tests import paper_live_fixture as PL
    from tests import test_xavier_review_probability_freshness as XR
    a = await H.new_account(conn, tag, now=at - 5000)
    slug = "%sops-%s" % (PL.SYN, uuid.uuid4().hex[:10])
    g = "paper_g_%s_ops" % a["account_id"][-10:]
    vid = await XR._reading(conn, slug, decided_at=at - entry_age_s,
                            pin_age_s=5.0, p=0.62)
    did = await XR._decision(conn, a, slug=slug, vid=vid, p=0.62,
                             at=at - entry_age_s)
    o = H.order(a, key="e", qty=XR.QTY, limit=0.40, slug=slug, at=at - 60,
                group_id=g)
    o.update(decision_id=did, strategy=XR.CG)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at - 60)
    assert got["ok"], got
    await H.observe(conn, slug, at - 57, offers=[(0.40, XR.QTY)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=at - 56,
                             fee_fn=H.zero_fee)
    await PX.step_handoff(conn, XR._ctx(a, at - 55))
    await H.observe(conn, slug, at - 2, offers=[(0.82, XR.QTY)],
                    bids=[(0.80, XR.QTY)])
    return a, g, slug


async def real_review(conn, acct, group_id, *, at) -> dict:
    """One Xavier review of the position through the REAL review path
    (paper_xavier.review_group, backstop trigger). The review row."""
    from sportsassets.agents import paper_xavier as PX
    from tests import test_xavier_review_probability_freshness as XR
    await PX.review_group(conn, XR._ctx(acct, at), group_id,
                          trigger=PX.T_BACKSTOP)
    return dict(await conn.fetchrow(
        "SELECT * FROM paper_xavier_reviews WHERE group_id=$1 "
        " ORDER BY reviewed_at DESC, review_id DESC LIMIT 1", group_id))


def derek_ctx(acct, transport, *, now) -> dict:
    """The context paper_derek.decide_one runs in (as the paper pass builds
    it), at `now`."""
    import time as _t
    from tests import paper_live_fixture as PL
    ctx = {"session": {"session_id": acct["session_id"],
                       "config": acct["config"]},
           "session_id": acct["session_id"],
           "account_id": acct["account_id"], "config": acct["config"],
           "market_data": PL.client(transport), "books_read": 0,
           "now": float(now), "deadline": _t.monotonic() + 30,
           "first_fills": [], "fills": 0}
    ctx["clock"] = lambda: ctx["now"]
    return ctx


async def derek_decides(conn, acct, transport, *, slug, valuation_at,
                        pin_age_s, decide_at) -> dict:
    """ONE DEREK DECISION THROUGH HIS REAL DECISION PATH
    (paper_derek.decide_one) on a synthetic Pinnacle valuation of `slug`
    stamped at `valuation_at` (its reading `pin_age_s` old then), decided
    at `decide_at`. The paper_decisions row."""
    from sportsassets.agents import paper_derek as PD
    from tests import paper_live_fixture as PL
    v = await PL.valuation(conn, slug=slug, decided_at=valuation_at,
                           pin_age_s=pin_age_s)
    row = await conn.fetchrow("SELECT * FROM external_valuations "
                              " WHERE id=$1", v["valuation_id"])
    PD._CONTEXT_CACHE.clear()
    rec = await PD.decide_one(conn, derek_ctx(acct, transport,
                                              now=decide_at), dict(row))
    return dict(await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE decision_id=$1",
        rec["decision_id"]))
