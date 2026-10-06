"""AUDREY'S AUTOMATIC POSTMORTEMS (migration 209): every closed position,
PAPER and ACTUAL separately, decomposed into selection edge, execution
slippage, fees, management (Xavier vs hold) and outcome variance -- and the
parts always sum to the realized P&L, with any unmeasured part NULL (with its
reason) and carried in `unexplained`, never 0.

ALL DATA IS SYNTHETIC TEST DATA in a scratch database, inside a transaction
that is rolled back.
"""
from __future__ import annotations

import json
import random

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import postmortems as PM
from tests import paper_harness as H
from tests import paper_ops_seed as SEED

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
T0 = H.T0 + 7_000_000.0


def _sum(d) -> float:
    return sum(c["value"] for c in d["components"].values()
               if c["value"] is not None) + d["unexplained_usd"]


# ════════════════════════════════════════════════════════════════════
# THE DECOMPOSITION IS AN IDENTITY (pure)
# ════════════════════════════════════════════════════════════════════

def test_a_held_winner_decomposes_exactly():
    d = PM.decompose(qty=400, decision_prob=0.58, decision_price=0.50,
                     fill_price=0.50, buy_fees=0.4, sale_fees=0.0,
                     realized_gross=400 * 1.0 - 200.0,
                     payout_per_contract=1.0)
    c = {k: v["value"] for k, v in d["components"].items()}
    assert c == {"selection_edge_usd": 32.0, "execution_slippage_usd": 0.0,
                 "fees_usd": -0.4, "outcome_variance_usd": 168.0,
                 "management_value_usd": 0.0}
    assert d["realized_pnl_usd"] == 199.6
    assert d["unexplained_usd"] == 0.0 and d["decomposition_complete"]
    assert abs(_sum(d) - d["realized_pnl_usd"]) < 1e-9


def test_an_early_sale_is_managements_and_slippage_is_executions():
    # bought at 0.52 against a decision price of 0.50, sold all at 0.60,
    # the contract later paid 1.0: Xavier's sale gave up 0.40 per contract
    d = PM.decompose(qty=100, decision_prob=0.55, decision_price=0.50,
                     fill_price=0.52, buy_fees=0.1, sale_fees=0.1,
                     realized_gross=100 * (0.60 - 0.52),
                     payout_per_contract=1.0)
    c = {k: v["value"] for k, v in d["components"].items()}
    assert c["execution_slippage_usd"] == pytest.approx(-2.0)
    assert c["management_value_usd"] == pytest.approx(-40.0)
    assert c["selection_edge_usd"] == pytest.approx(5.0)
    assert c["outcome_variance_usd"] == pytest.approx(45.0)
    assert d["realized_pnl_usd"] == pytest.approx(7.8)
    assert abs(_sum(d) - d["realized_pnl_usd"]) < 1e-9


def test_unmeasured_parts_are_null_with_reasons_and_never_zero():
    d = PM.decompose(qty=100, decision_prob=None, decision_price=None,
                     fill_price=0.40, buy_fees=0.2, sale_fees=0.0,
                     realized_gross=100 * (0.45 - 0.40),
                     payout_per_contract=None,
                     missing={"P": PM.R_NO_DECISION, "pd": PM.R_NO_DECISION})
    comps = d["components"]
    for k in ("selection_edge_usd", "execution_slippage_usd",
              "outcome_variance_usd", "management_value_usd"):
        assert comps[k]["value"] is None and comps[k]["why"], k
    assert comps["selection_edge_usd"]["why"] == PM.R_NO_DECISION
    assert comps["management_value_usd"]["why"] == PM.R_NO_Y
    assert comps["fees_usd"]["value"] == -0.2
    assert not d["decomposition_complete"]
    # the measured part (fees) plus the stated remainder is the realized P&L
    assert d["unexplained_usd"] == pytest.approx(5.0)
    assert abs(_sum(d) - d["realized_pnl_usd"]) < 1e-9


def test_the_identity_holds_for_any_inputs():
    rnd = random.Random(209)
    for _ in range(500):
        q = rnd.choice([0, 1, 37, 400, 2500])
        args = dict(qty=q,
                    decision_prob=rnd.choice([None, rnd.random()]),
                    decision_price=rnd.choice([None, rnd.random()]),
                    fill_price=rnd.random(), buy_fees=rnd.random(),
                    sale_fees=rnd.random(),
                    realized_gross=rnd.uniform(-500, 500),
                    payout_per_contract=rnd.choice([None, 0.0, 1.0,
                                                    rnd.random()]))
        d = PM.decompose(**args)
        assert d["sums_to_realized"], args
        assert abs(_sum(d) - d["realized_pnl_usd"]) < 1e-6, args


# ════════════════════════════════════════════════════════════════════
# PAPER AND ACTUAL, RECONSTRUCTED FROM THE RECORDS
# ════════════════════════════════════════════════════════════════════

async def _entry_and_exit(conn, acct, *, slug, key, decision_id, at,
                          qty=100.0, buy=0.50, sell=0.60):
    e = await SEED._entry(conn, acct, key=key, slug=slug,
                          strategy=SEED.CG, decision_id=decision_id, at=at,
                          qty=qty, limit=buy)
    o = H.order(acct, key=key + "-exit", slug=slug, direction="SELL",
                qty=qty, limit=sell, role="EXIT", group_id=e["group_id"],
                at=at + 20)
    o["strategy"] = SEED.CG
    got = await L.submit_order(conn, o, fee_fn=H.flat_fee(0.001), now=at + 20)
    assert got["ok"], got
    await H.observe(conn, slug, at + 23, bids=[(sell, qty)],
                    offers=[(round(sell + 0.02, 2), qty)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 24,
                             fee_fn=H.flat_fee(0.001))
    return e


@pg
@pytest.mark.asyncio
async def test_closed_paper_and_actual_positions_are_reconstructed_apart(
        monkeypatch):
    # seeded HISTORY through the real writers: the management-integrity rail
    # (P0 closeout; its own tests are test_p0_xavier_packet_and_
    # reconciliation) is held at "no refusal" so the seed is not refused
    from sportsassets import bettor_paper_freshness as _PMF

    async def _ok(conn_, account_id, strategy, *, now):
        return {"refusal": None, "strategy": strategy}
    monkeypatch.setattr(_PMF, "strategy_management_integrity", _ok)
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        acct = await H.new_account(conn, "pm", now=T0)
        seeded = await SEED.seed(conn, acct, t0=T0)
        strict = seeded["entries"]["strict"]
        # A POSITION SOLD BEFORE SETTLEMENT, whose contract another paper
        # position later settled WON: management is measurable
        slug = "seed-%s-mlb-early" % acct["account_id"][-10:]
        d1 = await SEED._decision(
            conn, acct, n=40, strategy=SEED.CG, verdict="ENTER",
            refusal=None, at=T0 + 200, slug=slug, fail_at=None, edge=7.0,
            ev=5.0, p_pin=0.55)
        early = await _entry_and_exit(conn, acct, slug=slug, key="early",
                                      decision_id=d1, at=T0 + 210)
        d2 = await SEED._decision(
            conn, acct, n=41, strategy=SEED.CG, verdict="ENTER",
            refusal=None, at=T0 + 300, slug=slug, fail_at=None, edge=7.0,
            ev=5.0, p_pin=0.55)
        held = await SEED._entry(conn, acct, key="held", slug=slug,
                                 strategy=SEED.CG, decision_id=d2,
                                 at=T0 + 310, qty=50.0, limit=0.5)
        await L.settle(conn, account_id=acct["account_id"],
                       group_id=held["group_id"], slug=slug,
                       holding_side="LONG",
                       settlement_event_key="venue-final:" + slug,
                       outcome="WON", evidence={"test": "synthetic"},
                       evidence_source="TEST_FIXTURE_SYNTHETIC",
                       at=T0 + 400, session_id=acct["session_id"])
        # AN ACTUAL (small-live) position on the strict group, closed by a
        # venue sale: synthetic venue fills
        await conn.execute(
            "INSERT INTO execmirror_orders (mirror_id, role, us_market_slug,"
            " intent, order_type, tif, state, group_id, wire_price, live_qty,"
            " cum_qty) VALUES ('mir-pm-buy','ENTRY',$1,"
            " 'ORDER_INTENT_BUY_LONG','LIMIT','IOC','FILLED',$2,0.51,4,4),"
            " ('mir-pm-sell','EXIT',$1,'ORDER_INTENT_SELL_LONG','LIMIT',"
            " 'IOC','FILLED',$2,0.70,4,4)", strict["slug"],
            strict["group_id"])
        await conn.execute(
            "INSERT INTO execmirror_fills (fill_key, mirror_id, "
            " venue_order_id, group_id, us_market_slug, intent, qty, price, "
            " fee_usd, observed_at, source) VALUES ('fk-pm-1','mir-pm-buy',"
            " 'v-1',$2,$1,'ORDER_INTENT_BUY_LONG',4,0.51,0.02,now(),'TEST'),"
            " ('fk-pm-2','mir-pm-sell','v-2',$2,$1,'ORDER_INTENT_SELL_LONG',"
            " 4,0.70,0.02,now(),'TEST')", strict["slug"], strict["group_id"])
        await conn.execute(
            "INSERT INTO smalllive_handoffs (handoff_id, venue, group_id, "
            " us_market_slug, entry_mirror_id, opened_intent, live_held, "
            " live_bought, avg_entry_px, fees_usd, first_live_fill_at, state)"
            " VALUES ('livehand:test:pm','POLYMARKET',$1,$2,'mir-pm-buy',"
            " 'ORDER_INTENT_BUY_LONG',0,4,0.51,0.04,now(),'CLOSED')",
            strict["group_id"], strict["slug"])

        paper = await PM.paper_postmortems(conn, acct["account_id"])
        by_group = {r["group_id"]: r for r in paper}
        # the still-open protected position is not closed, so not here
        assert seeded["entries"]["cg2"]["group_id"] not in by_group
        s = by_group[strict["group_id"]]
        assert s["book"] == "PAPER" and s["decision_id"]
        assert s["realized_pnl_usd"] == pytest.approx(199.6)
        assert s["selection_edge_usd"] == pytest.approx(32.0)
        assert s["outcome_variance_usd"] == pytest.approx(168.0)
        assert s["management_value_usd"] == pytest.approx(0.0)
        assert s["fees_usd"] == pytest.approx(-0.4)
        assert s["decomposition_complete"] and s["unexplained_usd"] == 0.0
        e = by_group[early["group_id"]]
        assert e["detail"]["outcome"]["basis"].startswith(
            "SAME_CONTRACT_SIDE_SETTLEMENT")
        # sold at 0.60 a contract that paid 1.00: -0.40 x 100 vs holding
        assert e["management_value_usd"] == pytest.approx(-40.0)
        assert e["detail"]["management"]["sales"]["EXIT"]["qty"] == 100.0
        for r in paper:
            parts = sum(r[c] or 0 for c in PM.COMPONENTS) + \
                r["unexplained_usd"]
            assert parts == pytest.approx(r["realized_pnl_usd"], abs=1e-6)
            # the ledger's own realized figure agrees
            assert r["realized_pnl_usd"] == pytest.approx(
                r["detail"]["ledger_realized_pnl_usd"], abs=1e-6)

        actual = await PM.actual_postmortems(conn)
        a = next(r for r in actual if r["position_key"] == "livehand:test:pm")
        assert a["book"] == "ACTUAL" and a["venue"] == "POLYMARKET"
        # (0.70 - 0.51) x 4 - 0.04 fees
        assert a["realized_pnl_usd"] == pytest.approx(0.72)
        assert a["decision_id"] == s["decision_id"]
        # the same contract settled WON on paper: holding would have paid 1
        assert a["management_value_usd"] == pytest.approx(
            4 * (0.70 - 0.51) - 4 * (1.0 - 0.51))
        assert a["execution_slippage_usd"] == pytest.approx(-4 * 0.01)

        # PERSISTED: once, then UNCHANGED on a replay
        res = await PM.run(conn, account_id=acct["account_id"], now=T0 + 500)
        assert res["ran"] and not res["errors"], res
        assert res["paper"]["WRITTEN"] == len(paper)
        again = await PM.run(conn, account_id=acct["account_id"],
                             now=T0 + 600)
        assert again["paper"].get("WRITTEN") is None
        assert again["paper"]["UNCHANGED"] == len(paper)
        n = await conn.fetchval(
            "SELECT count(*) FROM position_postmortems WHERE book='PAPER' "
            " AND account_id=$1", acct["account_id"])
        assert n == len(paper)
        body = await PM.postmortems_payload(conn)
        assert body["paper"]["summary"]["positions"] >= len(paper)
        assert body["actual"]["positions"][0]["book"] == "ACTUAL"
        assert body["books_never_summed"] is True
        assert all(p["book"] == "PAPER" for p in body["paper"]["positions"])
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_database_refuses_parts_that_do_not_sum():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO position_postmortems (book, position_key, venue,"
                " realized_pnl_usd, selection_edge_usd, unexplained_usd, "
                " decomposition_complete, components, detail, content_sha, "
                " version, computed_at) VALUES ('PAPER','pk-bad','PAPER',"
                " 10, 5, 0, false, '{}'::jsonb, '{}'::jsonb, 'x', 'v', now())")
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_schedule_runs_only_on_the_main_account_and_never_raises():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        got = await PM.step(conn, {"account_id": "paper_test_other",
                                   "now": T0})
        assert got == {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           PM.WATERMARK_KEY)
        first = await PM.step(conn, {"account_id": L.ACCOUNT_ID, "now": T0})
        assert first["ran"] is True, first
        second = await PM.step(conn, {"account_id": L.ACCOUNT_ID,
                                      "now": T0 + 10})
        assert second["why"] == "NOT_DUE"
        assert json.loads(await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1",
            PM.WATERMARK_KEY))["at"] == T0
    finally:
        await tx.rollback()
        await conn.close()
