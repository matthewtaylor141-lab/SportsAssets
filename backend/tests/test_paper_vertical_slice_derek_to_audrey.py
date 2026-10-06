"""THE PAPER VERTICAL SLICE, THROUGH THE REAL PAPER PASS.

Derek's V2 paper decision on a live-shaped valuation -> the decision
persisted with its evidence BEFORE any simulated order -> the simulated fill
after the decision-to-execution delay -> the first-fill handoff to Xavier ->
Xavier's review (HOLD / EXIT / REDUCE / NETTING / INDIRECT on one measure)
and ONE standing protective order -> a touch does not fill it; a strict cross
after the queue does -> settlement from authoritative evidence, exactly once
-> Audrey's daily report reconciles to the one ledger -> a restart resumes
without duplicate orders or fills and without losing positions.

Only the book transport behind the real PaperMarketDataClient is substituted
(SYNTHETIC books); the research model is fitted and registered through the
real research path (a CANDIDATE, never promoted). Scratch test accounts.
"""
from __future__ import annotations

import time

import pytest

from sportsassets import bettor_funded_model as FM
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)


async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, **kw):
    # the transport's observation clock follows the pass clock
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=PL.client(transport),
                               config=acct["config"], force=True,
                               fee_fn=FEE, sleep=_nosleep, **kw)


async def _decision(conn, acct, vid):
    return await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 "
        "   AND valuation_id=$2", acct["session_id"], vid)


@pg
async def test_without_a_research_model_every_decision_refuses_by_name(monkeypatch):
    conn = await H.connect()
    now = time.time()
    try:
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "nomodel", now=now)
        v = await PL.valuation(conn, decided_at=now - 10)
        stale = await PL.valuation(conn, decided_at=now - 10, pin_age_s=600)
        t = PL.Transport(now)
        t.set(v["slug"], offers=[(0.40, 3000)], bids=[(0.38, 3000)])
        got = await _pass(conn, acct, t, now)
        assert got["ran"] and not got["errors"], got["errors"]
        d = await _decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "REFUSE"
        assert d["refusal"] == PD.R_NO_RESEARCH_MODEL
        im = H.j(d["internal_model"])
        assert im["p"] is None and im["label"] == PD.MODEL_LABEL
        # the existing daily fit was attempted and recorded, not faked
        att = im["model_attempt"]
        assert att is not None and att["promoted"] is False
        assert att["outcome"] or att["already_ran"]
        assert d["p_internal"] is None and d["p_blended"] is None
        assert d["p_pinnacle"] == pytest.approx(0.62)
        gaps = [g["gap"] for g in H.j(d["qualification_gaps"])]
        assert gaps == [PD.GAP_MODEL, PD.GAP_CALIBRATION, PD.GAP_P5,
                        PD.GAP_EXECUTION, PD.GAP_SETTLEMENT]
        ds = await _decision(conn, acct, stale["valuation_id"])
        assert ds["refusal"] == "PROBABILITY_EVIDENCE_STALE"
        # nothing invented, nothing read, nothing ordered, cash untouched
        assert t.calls == []
        assert await conn.fetchval("SELECT count(*) FROM paper_orders "
                                   " WHERE account_id=$1",
                                   acct["account_id"]) == 0
        b = await L.balances(conn, acct["account_id"], now=now)
        assert b["cash_usd"] == 500000.0 and b["reserved_usd"] == 0.0
        # a second pass records nothing twice
        again = await _pass(conn, acct, t, now + 60)
        assert again["steps"]["derek"]["decisions_recorded"] == 0
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_vertical_slice_decides_fills_hands_off_protects_settles_and_reconciles(monkeypatch):
    conn = await H.connect()
    try:
        mid = "derek-research-model-paper-slice"
        # (182) this proof is about the two-model strategy's entries: its
        # paper entry switch (off by default) is turned on for it
        prev_entries = await PL.two_model_entries(conn, True)
        await PL.train_model(conn, monkeypatch, model_id=mid)
        now = time.time() + 5.0
        acct = await PL.new_account(conn, "slice", now=now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62)
        slug = v["slug"]
        t = PL.Transport(now)
        t.set(slug, offers=[(0.40, 3000), (0.42, 3000), (0.60, 5000)],
              bids=[(0.38, 5000)])

        # ── PASS 1: decide, persist, order, delay, fill, hand off, review ──
        p1 = await _pass(conn, acct, t, now)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        d = await _decision(conn, acct, v["valuation_id"])
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        im = H.j(d["internal_model"])
        assert im["label"] == "EXPERIMENTAL_RESEARCH_MODEL"
        assert im["model_id"] == mid and im["approval_status"] == "CANDIDATE"
        assert im["provenance_verified"] is True and im["promoted"] is False
        assert d["p_internal"] is not None and d["p_pinnacle"] == \
            pytest.approx(0.62)
        assert d["p_blended"] == pytest.approx(
            (d["p_internal"] + 0.62) / 2)
        pin = H.j(d["pinnacle"])
        assert pin["qualification"] == "FRESH" and pin["at"] is not None
        book = H.j(d["book"])
        assert book["basis"] == "OBSERVED_BOOK_LEVELS_NOT_THE_HEADLINE_PRICE"
        assert [lv["price"] for lv in book["levels"]][:2] == [0.40, 0.42]
        assert float(d["limit_price"]) == 0.42
        assert float(d["proposed_qty"]) == 6000
        pd = H.j(d["policy_decision"])
        assert pd["policy_name"] == "DEREK_ENTRY_POLICY_V2" and pd["admitted"]
        assert H.j(d["economics"])["fees_usd"] == pytest.approx(60.0)
        assert len(H.j(d["qualification_gaps"])) == 5
        assert H.j(d["alternatives"])["NO_TRADE"] == {"expected_net_usd": 0.0}
        assert H.j(d["optimistic"])["basis"] == \
            "OPTIMISTIC_SENSITIVITY_NOT_A_FILL"
        # THE DECISION WAS PERSISTED BEFORE THE ORDER, AND THE ORDER NAMES IT
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", d["decision_id"])
        assert o is not None and d["recorded_at"] <= o["created_at"]
        assert o["role"] == "ENTRY" and o["state"] == "FILLED"
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE order_id=$1"
                                 " ORDER BY price", o["order_id"])
        assert [(float(f["qty"]), float(f["price"])) for f in fills] == [
            (3000.0, 0.40), (3000.0, 0.42)]
        assert {f["event_source"] for f in fills} == {"SIMULATOR"}
        assert all(L._epoch(f["book_observed_at"]) >=
                   L._epoch(o["eligible_at"]) for f in fills)
        b = await L.balances(conn, acct["account_id"], now=now + 5)
        assert b["cash_usd"] == 500000.0 - 2460.0 - 60.0
        # the research model is untouched: still a CANDIDATE
        assert await conn.fetchval("SELECT state FROM bettor_funded_models "
                                   " WHERE model_id=$1", mid) == "CANDIDATE"
        # FIRST-FILL HANDOFF AND XAVIER'S REVIEW
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", o["group_id"])
        assert h["owner"] == "XAVIER" and float(h["confirmed_qty"]) == 6000
        r = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                " group_id=$1 ORDER BY reviewed_at",
                                o["group_id"])
        # THE FIRST REVIEW FINDS NO STANDING PROTECTION YET: its packet is
        # incomplete (UNPROTECTED_NO_STANDING_ORDER is never protection), so
        # nothing is ranked -- the review places the cost-recovery
        # protection and records MANAGEMENT_UNAVAILABLE (P0 closeout)
        assert r["trigger"] == "FIRST_FILL"
        assert r["recommendation"] == "MANAGEMENT_UNAVAILABLE_STALE_INPUT"
        assert "NO_VALID_ACTIVE_PROTECTION" in H.j(r["measure"])[
            "management_packet"]["missing"]
        assert H.j(r["action"])["taken"] == "PLACE_STANDING"
        alts = H.j(r["alternatives"])
        acts = {c["action"] for c in alts["candidates"]} | {
            c["action"] for c in alts["not_rankable"]}
        assert {"HOLD", "EXIT", "REDUCE", "NETTING",
                "ACQUIRE_INDIRECT_HEDGE"} <= acts
        assert H.j(r["incomplete_search"])["complete"] is False
        assert H.j(r["exposure"])["floors_are_not_realized_pnl"] is True
        st = await conn.fetch("SELECT * FROM paper_orders WHERE group_id=$1 "
                              "   AND role='STANDING_PROTECTION'",
                              o["group_id"])
        assert len(st) == 1 and st[0]["state"] == "RESTING"
        assert float(st[0]["limit_price"]) == 0.44       # recovers 2,580
        assert float(st[0]["qty"]) == 6000
        assert float(st[0]["queue_ahead_qty"]) == 6000   # offers <= 0.44

        # ── PASS 2: a TOUCH at the protective price is not a fill ─────────
        t.set(slug, offers=[(0.45, 1000)], bids=[(0.44, 50000)])
        p2 = await _pass(conn, acct, t, now + 120)
        assert not p2["errors"], p2["errors"]
        st = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                 " order_id=$1", st[0]["order_id"])
        assert float(st["filled_qty"]) == 0 and st["state"] == "RESTING"
        ev = await conn.fetchval(
            "SELECT detail->>'reason' FROM paper_order_events WHERE "
            " order_id=$1 AND kind='NO_FILL_EVIDENCE' ORDER BY event_id DESC "
            " LIMIT 1", st["order_id"])
        assert ev == "TOUCH_IS_NOT_A_FILL"
        # still exactly one live standing order for the group
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role="
            "'STANDING_PROTECTION' AND state IN ('PENDING_SIMULATION',"
            "'RESTING','PARTIALLY_FILLED','CANCEL_PENDING')",
            o["group_id"]) == 1

        # ── SETTLEMENT FROM AUTHORITATIVE EVIDENCE, EXACTLY ONCE ─────────
        await PL.settle_valuation(conn, v["valuation_id"], outcome=1)
        p3 = await _pass(conn, acct, t, now + 240)
        assert p3["steps"]["settle"]["settled"] == 1, p3["steps"]["settle"]
        p4 = await _pass(conn, acct, t, now + 360)
        assert p4["steps"]["settle"]["settled"] == 0
        b = await L.balances(conn, acct["account_id"], now=now + 400)
        assert b["cash_usd"] == 500000.0 - 2520.0 + 6000.0
        assert b["reserved_usd"] == 0.0 and not b["open_positions"]
        assert b["realized_pnl_usd"] == pytest.approx(3480.0)
        kinds = await H.ledger_kinds(conn, acct["account_id"])
        assert kinds.count("SETTLEMENT") == 1
        # the standing order on the settled market was released
        assert (await conn.fetchval("SELECT state FROM paper_orders WHERE "
                                    " order_id=$1", st["order_id"])) == \
            "CANCELED"

        # ── AUDREY: THE DAY'S REPORT RECONCILES TO THE ONE LEDGER ────────
        rep = await conn.fetchrow(
            "SELECT * FROM paper_audrey_reports WHERE session_id=$1 "
            " ORDER BY version DESC LIMIT 1", acct["session_id"])
        assert rep is not None and rep["reconciles"] is True
        body = H.j(rep["report"])
        assert body["acquisition_volume"]["entries_usd"] == \
            pytest.approx(2520.0)
        assert body["acquisition_volume"]["hedges_usd"] == 0.0
        assert body["breadth"] == dict(body["breadth"], distinct_markets=1,
                                       distinct_fixtures=1)
        assert body["reporting_tz"] == "America/New_York"
        assert body["targets"]["binding"] is False
        assert body["sale_proceeds"]["never_acquisition"] is True
        # MUTATION ATTEMPTS: ZERO
        h = await S.health(conn, acct["session_id"])
        assert h["mutation_attempts"] == 0 and h["passes"] >= 4
    finally:
        await PL.restore_two_model_entries(conn, prev_entries)
        await PL.purge_research_models(conn)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_strict_cross_after_the_queue_fills_the_protection_and_a_restart_duplicates_nothing(monkeypatch):
    conn = await H.connect()
    try:
        mid = "derek-research-model-paper-restart"
        # (182) this proof is about the two-model strategy's entries: its
        # paper entry switch (off by default) is turned on for it
        prev_entries = await PL.two_model_entries(conn, True)
        await PL.train_model(conn, monkeypatch, model_id=mid)
        now = time.time() + 5.0
        acct = await PL.new_account(conn, "restart", now=now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62)
        slug = v["slug"]
        t = PL.Transport(now)
        t.set(slug, offers=[(0.40, 3000), (0.42, 3000)], bids=[(0.38, 5000)])
        # A PASS CUT SHORT after Derek: the decision and the order exist, the
        # delay step never ran (the process "died").
        steps = [s for s in PR.default_steps()
                 if s[0] in ("books", "simulate", "derek")]
        cut = await _pass(conn, acct, t, now, steps=steps)
        assert cut["steps"]["derek"]["orders_submitted"] == 1
        before = await conn.fetchval("SELECT count(*) FROM paper_orders "
                                     " WHERE account_id=$1",
                                     acct["account_id"])
        # A NEW PROCESS: fresh connection, fresh client, same database.
        await conn.close()
        conn = await H.connect()
        PR._LOCK.update(lock=None, loop=None)
        r1 = await _pass(conn, acct, t, now + 30)
        assert r1["resumed"] is True and not r1["errors"], r1["errors"]
        n_dec = await conn.fetchval("SELECT count(*) FROM paper_decisions "
                                    " WHERE session_id=$1",
                                    acct["session_id"])
        r2 = await _pass(conn, acct, t, now + 30)      # replayed pass
        assert not r2["errors"], r2["errors"]
        # (other proofs' synthetic valuations in the shared window are
        # decided too; what matters is that nothing is decided twice)
        assert await conn.fetchval("SELECT count(*) FROM paper_decisions "
                                   " WHERE session_id=$1",
                                   acct["session_id"]) == n_dec
        assert await conn.fetchval("SELECT count(*) FROM paper_decisions "
                                   " WHERE session_id=$1 AND valuation_id=$2",
                                   acct["session_id"],
                                   v["valuation_id"]) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1 "
            "   AND role='ENTRY'", acct["account_id"]) == before
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders o JOIN paper_decisions d ON "
            " d.decision_id=o.decision_id WHERE d.valuation_id=$1 "
            "   AND o.account_id=$2", v["valuation_id"],
            acct["account_id"]) == 1
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " account_id=$1", acct["account_id"])
        assert sum(float(f["qty"]) for f in fills) == 6000.0
        kinds = await H.ledger_kinds(conn, acct["account_id"])
        # one reservation: the entry (a protective SALE reserves inventory,
        # never cash)
        assert kinds.count("ORDER_SUBMITTED") == 1
        pos = await L.positions(conn, acct["account_id"])
        assert len(pos) == 1 and pos[0]["open_qty"] == 6000.0
        # a strict cross beyond the queue fills the protection, at its limit
        t.set(slug, offers=[(0.47, 1000)], bids=[(0.46, 20000)])
        await _pass(conn, acct, t, now + 200)
        sp = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                 " account_id=$1 AND role="
                                 "'STANDING_PROTECTION'", acct["account_id"])
        assert sp["state"] == "FILLED", sp["state"]
        sf = await conn.fetch("SELECT * FROM paper_fills WHERE order_id=$1",
                              sp["order_id"])
        assert {float(f["price"]) for f in sf} == {0.44}
        assert sum(float(f["qty"]) for f in sf) == 6000.0
        assert {f["basis"] for f in sf} == {"CROSSING_LIQUIDITY_AFTER_QUEUE"}
        b = await L.balances(conn, acct["account_id"], now=now + 300)
        assert b["cash_usd"] == pytest.approx(500000.0 - 2520.0 + 2640.0
                                              - 60.0)
        assert b["realized_pnl_usd"] == pytest.approx(60.0)
    finally:
        await PL.restore_two_model_entries(conn, prev_entries)
        await PL.purge_research_models(conn)
        await PL.purge_everything(conn)
        await conn.close()
