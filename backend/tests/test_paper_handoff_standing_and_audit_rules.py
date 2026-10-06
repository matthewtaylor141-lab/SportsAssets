"""XAVIER'S HANDOFF AND STANDING-ORDER INVARIANT ON THE PAPER BOOK, AND
AUDREY'S DEFINITIONS.

  * the handoff happens from the FIRST partial simulated fill: confirmed is
    the filled quantity, the rest is outstanding; later fills update the SAME
    row; replays never create a second owner;
  * at most ONE live-or-potentially-live standing protective order per group:
    the funded gate (`second_live_hedge_permitted`) refuses a second while
    one is live or cancel-pending, and the database index refuses one
    written directly;
  * Audrey's acquisition volume counts FILLED PURCHASES only (entries and
    hedges separately), sale proceeds never; distinct markets count a market
    once however many orders or sides; fixtures separately; and the report
    reconciles to the one ledger.
"""
from __future__ import annotations

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import bettor_xavier_standing_orders as SPO
from sportsassets.agents import paper_audrey as PA
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def G(a, n):
    """Group ids are unique per test account (handoffs and the standing
    order index are keyed by group across the whole database)."""
    return "paper_g_%s_%s" % (a["account_id"][-10:], n)


def SL(a, n):
    return "%s:%s" % (a["account_id"], n)


def _ctx(a, now):
    return {"account_id": a["account_id"], "session_id": a["session_id"],
            "config": a["config"], "now": now, "clock": lambda: now,
            "session": {"session_id": a["session_id"], "config": a["config"],
                        "reporting_tz": "America/New_York"},
            "fee_fn": H.zero_fee, "deadline": 1e18}


@pg
async def test_the_handoff_starts_at_the_first_partial_fill_and_later_fills_update_it():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "handoff")
        s = SL(a, "m")
        # a RESTING entry, partially filled by a crossing book
        o = H.order(a, key="e1", qty=1000, limit=0.40, slug=s, at=H.T0,
                    order_type="RESTING", tif="GTD", delay=0.0, ttl=3600,
                    queue_ahead=0.0, group_id=G(a, "hand"))
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        await H.observe(conn, s, H.T0 + 1, offers=[(0.39, 300)])
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 2,
                                     fee_fn=H.zero_fee)
        assert r["first_fill"] is True
        ctx = _ctx(a, H.T0 + 3)
        got = await PX.step_handoff(conn, ctx)
        assert got["handoffs_created"] == 1
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", G(a, "hand"))
        assert float(h["confirmed_qty"]) == 300.0
        assert float(h["outstanding_qty"]) == 700.0 and h["owner"] == "XAVIER"
        # replays: no second owner, nothing changes
        for _ in range(3):
            again = await PX.step_handoff(conn, ctx)
            assert again["handoffs_created"] == 0
        await H.observe(conn, s, H.T0 + 4, offers=[(0.38, 500)])
        await SIM.simulate_order(conn, oid, now=H.T0 + 5, fee_fn=H.zero_fee)
        up = await PX.step_handoff(conn, _ctx(a, H.T0 + 6))
        assert up["handoffs_created"] == 0 and up["handoffs_updated"] == 1
        h2 = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                 " group_id=$1", G(a, "hand"))
        assert h2["handoff_id"] == h["handoff_id"]
        assert float(h2["confirmed_qty"]) == 800.0
        assert float(h2["outstanding_qty"]) == 200.0
        assert h2["first_fill_id"] == h["first_fill_id"]
        # an order that never filled hands nothing over
        o3 = H.order(a, key="e3", qty=10, limit=0.10, slug=SL(a, "n"),
                     at=H.T0, group_id=G(a, "none"))
        await L.submit_order(conn, o3, fee_fn=H.zero_fee, now=H.T0)
        await PX.step_handoff(conn, _ctx(a, H.T0 + 7))
        assert await conn.fetchval("SELECT count(*) FROM paper_handoffs "
                                   " WHERE group_id=$1", G(a, "none")) == 0
    finally:
        await conn.close()


@pg
async def test_one_live_standing_order_per_group_on_the_paper_book():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "spo")
        s = SL(a, "m")
        g = G(a, "spo")
        e = H.order(a, key="e", qty=100, limit=0.40, slug=s, at=H.T0,
                    group_id=g)
        ge = await L.submit_order(conn, e, fee_fn=H.zero_fee, now=H.T0)
        await H.observe(conn, s, H.T0 + 3, offers=[(0.40, 100)],
                        bids=[(0.38, 100)])
        await SIM.simulate_order(conn, ge["order"]["order_id"], now=H.T0 + 4,
                                 fee_fn=H.zero_fee)
        await PX.step_handoff(conn, _ctx(a, H.T0 + 5))
        pos = (await L.positions(conn, a["account_id"]))[0]
        prot = PX.protective_price(qty=100, cost_basis=pos["cost_basis_usd"],
                                   fee_fn=H.zero_fee, at=H.T0)
        assert prot["ok"] and prot["price"] == 0.41
        ctx = _ctx(a, H.T0 + 6)
        placed = await PX._maintain_standing(conn, ctx, pos=pos, standing=[],
                                             prot=prot, md=None, at=H.T0 + 6,
                                             SPO=SPO)
        assert placed["taken"] == "PLACE_STANDING" and placed["ok"]
        live = await conn.fetch("SELECT * FROM paper_orders WHERE group_id=$1"
                                " AND role='STANDING_PROTECTION'", g)
        assert len(live) == 1
        # the gate refuses a second while one is live, whatever is claimed
        gate = SPO.second_live_hedge_permitted(live_or_potentially_live=1,
                                               claimed_exclusivity=True)
        assert gate["permitted"] is False
        # a changed protective price: cancel first, never a second order
        p2 = dict(prot, price=0.42)
        step = await PX._maintain_standing(
            conn, ctx, pos=pos, standing=[dict(r) for r in live], prot=p2,
            md=None, at=H.T0 + 7, SPO=SPO)
        assert step["taken"] == "CANCEL_FOR_REPLACEMENT"
        pend = [dict(r) for r in await conn.fetch(
            "SELECT * FROM paper_orders WHERE group_id=$1 AND role="
            "'STANDING_PROTECTION'", g)]
        assert [r["state"] for r in pend] == ["CANCEL_PENDING"]
        wait = await PX._maintain_standing(conn, ctx, pos=pos, standing=pend,
                                           prot=p2, md=None, at=H.T0 + 8,
                                           SPO=SPO)
        assert wait["taken"] == "WAIT_FOR_TERMINAL"
        assert wait["gate"]["permitted"] is False
        # THE DATABASE REFUSES A SECOND LIVE ONE WRITTEN DIRECTLY
        dup = H.order(a, key="dup-standing", direction="SELL", qty=1,
                      limit=0.42, slug=s, role="STANDING_PROTECTION",
                      group_id=g, order_type="RESTING", tif="GTD",
                      delay=0.0, ttl=3600)
        got = await L.submit_order(conn, dup, now=H.T0 + 9)
        assert got["ok"] is False and got["refusal"] == L.R_NOT_HELD
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO paper_orders (order_id, idempotency_key, "
                " account_id, session_id, group_id, role, direction, "
                " holding_side, intent, us_market_slug, order_type, "
                " time_in_force, allow_partial, qty, limit_price, wire_price,"
                " state, decided_at, eligible_at, expires_at, "
                " simulator_version) VALUES ('paperord:direct', 'direct', $1,"
                " $2, $3, 'STANDING_PROTECTION', 'SELL', 'LONG', 'X', $4, "
                " 'RESTING', 'GTD', true, 1, 0.42, 0.42, 'RESTING', now(), "
                " now(), now(), 'PAPER_SIM_V1')", a["account_id"],
                a["session_id"], g, s)
        # the simulator confirms the cancel; only then is a replacement placed
        await SIM.simulate_order(conn, pend[0]["order_id"], now=H.T0 + 10,
                                 fee_fn=H.zero_fee)
        repl = await PX._maintain_standing(conn, ctx, pos=pos, standing=[],
                                           prot=p2, md=None, at=H.T0 + 11,
                                           SPO=SPO)
        assert repl["taken"] == "PLACE_STANDING" and repl["ok"]
        n_live = await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role="
            "'STANDING_PROTECTION' AND state = ANY($2::text[])", g,
            list(L.OPEN_STATES))
        assert n_live == 1
    finally:
        await conn.close()


@pg
async def test_audrey_counts_purchases_not_sales_and_markets_once_and_reconciles():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "audrey")
        m1, m2 = SL(a, "m1"), SL(a, "m2")

        async def buy(key, slug, side, qty, px, role="ENTRY", fixture="fxA",
                      group=None):
            o = H.order(a, key=key, qty=qty, limit=px, slug=slug,
                        holding_side=side, role=role, fixture=fixture,
                        group_id=group or G(a, key), at=H.T0)
            g = await L.submit_order(conn, o, fee_fn=H.flat_fee(0.01),
                                     now=H.T0)
            return g["order"]["order_id"]
        # repeat orders on one market and BOTH sides of it: one market
        ids = [await buy("a1", m1, "LONG", 100, 0.40),
               await buy("a2", m1, "LONG", 100, 0.40),
               await buy("a3", m1, "SHORT", 100, 0.62)]
        # a hedge on another market of the same fixture
        ids.append(await buy("h1", m2, "LONG", 100, 0.30, role="HEDGE",
                             group=G(a, "a1")))
        await H.observe(conn, m1, H.T0 + 3, offers=[(0.40, 500)],
                        bids=[(0.38, 500)])
        await H.observe(conn, m2, H.T0 + 3, offers=[(0.30, 500)])
        for oid in ids:
            await SIM.simulate_order(conn, oid, now=H.T0 + 4,
                                     fee_fn=H.flat_fee(0.01))
        # a SALE: proceeds, never acquisition
        so = H.order(a, key="s1", direction="SELL", qty=100, limit=0.38,
                     slug=m1, role="EXIT", group_id=G(a, "a1"),
                     at=H.T0 + 5)
        sg = await L.submit_order(conn, so, now=H.T0 + 5)
        await H.observe(conn, m1, H.T0 + 8, bids=[(0.38, 500)])
        await SIM.simulate_order(conn, sg["order"]["order_id"],
                                 now=H.T0 + 9, fee_fn=H.flat_fee(0.01))
        day, start, end = PA.day_bounds(H.T0 + 10)
        rep = await PA.build_report(
            conn, session=_ctx(a, H.T0 + 10)["session"],
            account_id=a["account_id"], day=day, start=start, end=end,
            now=H.T0 + 10)
        acq = rep["acquisition_volume"]
        # BUY_SHORT at bid 0.38 costs 0.62 a contract
        assert acq["entries_usd"] == pytest.approx(40 + 1 + 40 + 1 + 62 + 1)
        assert acq["hedges_usd"] == pytest.approx(30 + 1)
        assert acq["total_usd"] == pytest.approx(176.0)
        assert rep["sale_proceeds"]["EXIT"] == pytest.approx(38 - 1)
        assert rep["breadth"]["distinct_markets"] == 2
        assert rep["breadth"]["distinct_fixtures"] == 1
        rec = rep["reconciliation"]
        assert rec["reconciles"] is True, [c for c in rec["checks"]
                                           if not c["passed"]]
        chk = {c["check"]: c for c in rec["checks"]}
        assert chk["ACQUISITION_EQUALS_LEDGER_FILL_DEBITS"][
            "ledger_fill_debits_for_these_fills_usd"] == pytest.approx(176.0)
        assert chk["SALE_PROCEEDS_EQUAL_LEDGER_SALE_CREDITS"]["passed"]
        assert rep["targets"]["binding"] is False
        assert rep["targets"]["acquisition_volume"]["met"] is False
        assert rep["shortfall_causes"]
        # net P&L is equity change, from the one derived-figures function
        b = await L.balances(conn, a["account_id"], now=H.T0 + 10)
        assert rep["equity"]["cash_usd"] == b["cash_usd"]
    finally:
        await conn.close()


@pg
async def test_audrey_opens_improvement_tasks_from_findings_and_promotes_nothing():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "tasks")
        ctx = _ctx(a, H.T0)
        f = await PA.finding(conn, ctx, kind="GROUP_WITHOUT_AN_OWNER",
                             subject="paper_g_x", severity="WARNING",
                             detail={"test": True})
        t1 = await PA.open_task(conn, ctx, f, detail={})
        t2 = await PA.open_task(conn, ctx, f, detail={})
        assert t1["ok"] and t1["created"] is True
        assert t2["created"] is False and t2["task_id"] == t1["task_id"]
        row = await conn.fetchrow("SELECT * FROM agent_tasks WHERE "
                                  " task_id=$1", t1["task_id"])
        assert row["kind"] == PA.TASK_KIND and row["kind"] != "IMPROVEMENT"
        assert row["assignee"] == "XAVIER"
        spec = H.j(row["spec"])
        assert spec["evidence_category"] == \
            "SIMULATED_WITH_DISCLOSED_ASSUMPTIONS"
        assert spec["promotion"].startswith("NONE")
        link = await conn.fetchval("SELECT improvement_task_id FROM "
                                   " paper_audrey_findings WHERE "
                                   " finding_id=$1", f["finding_id"])
        assert link == t1["task_id"]
    finally:
        await conn.close()


@pg
@pytest.mark.parametrize("stale,p,expect", [
    # stale: no discretionary sale, the position stays held + protected, and
    # NO management action is recommended (owner P0: never a default HOLD)
    (True, 0.2, "WAITING_FOR_FRESH_EVIDENCE"),
    (False, 0.2, "EXIT"),      # fresh: the same book and p rank EXIT
    # absent: nothing ranked, protection kept, management unavailable
    (True, None, "MANAGEMENT_UNAVAILABLE_STALE_INPUT"),
])
async def test_a_stale_or_absent_measure_never_drives_a_discretionary_sale(
        monkeypatch, stale, p, expect):
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "stalem")
        s = SL(a, "m")
        g = G(a, "stalem")
        e = H.order(a, key="e", qty=100, limit=0.40, slug=s, at=H.T0,
                    group_id=g)
        # the settlement identity Xavier's management packet requires
        e["decision_id"] = await PL.entry_identity(conn, a, slug=s,
                                                   at=H.T0)
        ge = await L.submit_order(conn, e, fee_fn=H.zero_fee, now=H.T0)
        await H.observe(conn, s, H.T0 + 3, offers=[(0.40, 100)],
                        bids=[(0.38, 100)])
        await SIM.simulate_order(conn, ge["order"]["order_id"], now=H.T0 + 4,
                                 fee_fn=H.zero_fee)
        await PX.step_handoff(conn, _ctx(a, H.T0 + 5))
        # the valid standing protection a complete packet needs
        await H.protect(conn, _ctx(a, H.T0 + 5.2), g, at=H.T0 + 5.2)
        # a book whose bid would make selling worth more than holding at p
        await H.observe(conn, s, H.T0 + 6, offers=[(0.82, 100)],
                        bids=[(0.80, 100)])

        async def measure(conn_, ctx_, *, pos, levels_buy):
            # a fresh reading carries its persisted valuation id (packet)
            return {"p": p, "source": "PINNACLE_ONLY_LATEST" if stale
                    else "PINNACLE_ONLY_CURRENT", "stale": stale,
                    "valuation_id": None if stale else 1}
        monkeypatch.setattr(PX, "_measure", measure)
        out = await PX.review_group(conn, _ctx(a, H.T0 + 7), g,
                                    trigger="SCHEDULED_BACKSTOP")
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " group_id=$1 ORDER BY reviewed_at DESC "
                                 " LIMIT 1", g)
        assert rv["recommendation"] == expect, out
        sales = await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
            " IN ('EXIT', 'REDUCE')", g)
        alts = H.j(rv["alternatives"])
        blocked = [x for x in alts["not_rankable"]
                   if x.get("blocker") == PX.B_STALE_MEASURE]
        if expect == "EXIT":
            # decided on fresh evidence; the valid protection is cancelled
            # first (terminal before the sale), so no sale order yet
            assert not blocked
            assert H.j(rv["action"])["taken"] == \
                "CANCEL_STANDING_BEFORE_EXIT"
            assert sales == 0
        else:
            assert sales == 0, "no sale on stale or absent evidence"
            # HOLD, EXIT and REDUCE all leave the rankable set (P0)
            assert {x["action"] for x in blocked} <= {"HOLD", "EXIT",
                                                      "REDUCE"}
            assert stale and (blocked or p is None)
            act = H.j(rv["action"])
            assert act["taken"] in ("PLACE_STANDING", "KEEP_STANDING"), act
    finally:
        await conn.close()
