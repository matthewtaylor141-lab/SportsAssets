"""CAPITAL-CRITICAL: EVERY HELD POSITION HAS AN ACCOUNTABLE, CURRENT
MANAGEMENT RECORD, AND EVERY BLOCKER OF ITS PACKET IS NAMED (RC6 xavier-
records).

Production 2026-10-08 20:03Z (pm-acceptance 37836393458): Xavier held three
PAPER positions, 0 of 3 with a complete current packet. The management
readback (GET /api/command/xavier/management) showed each one's evidence
state and gated recommendation but not WHY the probability was not current
(the held PinnAPI read's refusal and `probability_limitation` stayed on the
review row; the assessment never persisted the limitation), not the orders
with their actual states, not the live protection, not the residual
exposure, not the action the review took, not the packet's state NOW; and
the capital-readiness gate `xavier_complete` named three position keys with
no reason at all. PKE (bought at 0.99) could never be protected
(NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR) and nothing said so.

These tests pin the record (agents/xavier_management_record), the persisted
limitation on the assessment, and the gate's named blockers. Nothing here
changes a rule, a limit or an order path: every change is a read or a
record. Synthetic data in a scratch database; no network, no real order.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import xavier_management as XM
from sportsassets.agents import xavier_management_record as XMR
from tests import paper_harness as H
from tests import test_xavier_review_probability_freshness as XRF

#: the strict management entry rail runs its production functions here
MANAGEMENT_RAIL_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT
QTY = XRF.QTY


async def _view_of(conn, g, *, now):
    mv = await XM.management_view(conn, limit=1, now=now)
    pv = next(p for p in mv["positions"]
              if p["group_id"] == g and p["state"] == "OPEN")
    return mv, pv


# ═════════════════════════════════════════════════════════════════════
# 1 · PURE: THE GATE'S INTEGRITY NAMES EVERY INCOMPLETE POSITION'S REASON
# ═════════════════════════════════════════════════════════════════════

def _pos(k, g):
    return {"position_key": k, "group_id": g, "last_fill_at": 1.0}


def test_every_incomplete_position_has_its_reason_and_the_counts_are_whole():
    now = 1_790_000_000.0
    pos = [_pos("p1", "g1"), _pos("p2", "g2"), _pos("p3", "g3"),
           _pos("p4", "g4")]
    complete = {"complete": True, "reviewed_at": now - 5,
                "expires_at": now + 10, "book_observed_at": now - 5}
    packets = {"g1": complete,
               "g2": dict(complete, complete=False),       # recorded gaps
               "g3": dict(complete, expires_at=now - 1)}   # expired
    prot = {k: {"state": PMF.PS_PROTECTED} for k in ("p1", "p2", "p3",
                                                    "p4")}
    v = PMF.integrity_verdict(positions=pos, packets=packets,
                              protections=prot, now=now)
    assert v["packet_complete_now_count"] == 1
    assert v["packet_incomplete_count"] == 3
    why = {x["position_key"]: x["why"] for x in v["packet_incomplete_why"]}
    assert why == {"p2": PMF.PK_RECORDED_INCOMPLETE,
                   "p3": PMF.PK_PROBABILITY_EXPIRED,
                   "p4": PMF.PK_NO_REVIEW}
    # the verdict itself is exactly as before
    assert v["refusal"] == PMF.R_PACKETS_BLOCK_ALLOCATION
    assert sorted(v["packet_incomplete"]) == ["p2", "p3", "p4"]
    # a bare bool packet (already judged) keeps its reason too
    v = PMF.integrity_verdict(positions=pos[:2],
                              packets={"g1": True, "g2": False},
                              protections=prot, now=now)
    assert v["packet_incomplete_why"] == [
        {"position_key": "p2", "why": PMF.PK_RECORDED_INCOMPLETE}]


def test_an_excluded_position_is_counted_apart_never_as_complete():
    now = 1_790_000_000.0
    pos = [_pos("p1", "g1"), _pos("p2", "g2")]
    v = PMF.integrity_verdict(
        positions=pos, packets={}, protections={}, now=now,
        classes={"p2": PMF.EXTERNAL_UNAVAILABLE})
    assert v["open_positions"] == 1
    assert v["excluded_external_unavailable_count"] == 1
    assert v["packet_complete_now_count"] == 0


# ═════════════════════════════════════════════════════════════════════
# 2 · THE RECORD OF A HELD POSITION ON STALE EVIDENCE (Postgres)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_record_carries_every_element_of_a_held_position():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xmrstale", entry_age_s=3600)
        slugs.append(slug)
        rv, m, _alts, sales = await XRF._review(conn, a, g)
        assert m["evidence_state"] == PX.E_STALE and sales == 0
        mv, pv = await _view_of(conn, g, now=AT + 2)
        rec = pv["management_record"]
        assert rec["version"] == XMR.VERSION
        # identity and remaining quantity
        pk = next(p["position_key"] for p in await L.positions(
            conn, a["account_id"]) if p["group_id"] == g)
        assert rec["position"]["position_key"] == pk
        assert rec["position"]["remaining_qty"] == pytest.approx(QTY)
        assert rec["position"]["us_market_slug"] == slug
        # the probability: its three instants apart, and why it is stale
        pr = rec["probability"]
        assert pr["evidence_state"] == PX.E_STALE
        assert pr["source"] == "ENTRY_TIME_MEASURE"
        assert pr["source_event_at"] == pytest.approx(AT - 3605)
        assert pr["received_at"] == pytest.approx(AT - 3604)
        assert pr["verified_at"] == pytest.approx(AT)
        assert len({pr["source_event_at"], pr["received_at"],
                    pr["verified_at"]}) == 3
        assert pr["current_now"] is False
        assert "NOT a current expected value" in pr["limitation"]
        # no feed owner in this process: the held read's refusal, named
        assert pr["feed_refusal"] == "FEED_OWNERSHIP_NOT_HELD"
        # the decision, the action taken and every alternative considered
        d = rec["decision"]
        assert d["recorded_recommendation"] == rv["recommendation"]
        assert d["action_taken"] == H.j(rv["action"])["taken"]
        acts = {x["action"] for x in d["alternatives_considered"]}
        assert {"HOLD", "EXIT", "REDUCE"} <= acts
        assert all(x["rankable"] is False for x in d["alternatives_considered"]
                   if x["action"] in ("HOLD", "EXIT", "REDUCE"))
        assert d["rationale"]
        # the next review instant, and the resting price condition
        assert rec["next"]["review_due_at"] == pv["next_review_due_at"]
        assert rec["next"]["probability_expires_at"] is None
        pc = rec["next"]["price_condition"]
        assert pc["kind"] == "STANDING_PROTECTIVE_SALE_RESTING"
        # the orders with their ACTUAL states; the protection, live
        roles = [o["role"] for o in rec["orders"]]
        assert "ENTRY" in roles and "STANDING_PROTECTION" in roles
        sp = next(o for o in rec["orders"]
                  if o["role"] == "STANDING_PROTECTION")
        assert sp["state"] == "RESTING" and sp["live_now"] is True
        assert sp["protects_now"] is True
        assert pc["order_id"] == sp["order_id"]
        assert pc["at_or_above"] == pytest.approx(sp["limit_price"])
        assert rec["protection"]["state"] == PMF.PS_PROTECTED
        assert rec["protection"]["valid_now"] is True
        # residual exposure: a resting sale protects nothing until it fills
        ex = rec["exposure"]
        assert ex["resting_is_protection"] is False
        assert ex["unmatched_inventory_qty"] == pytest.approx(QTY)
        assert ex["resting_protection_qty"] == pytest.approx(QTY)
        # the packet NOW, by the gate's own rule, and every blocker named
        assert rec["packet"]["recorded_complete"] is False
        assert "NO_FRESH_PROBABILITY" in rec["packet"]["recorded_missing"]
        assert rec["packet"]["complete_now"] is False
        assert rec["packet"]["why_not_complete_now"] == \
            PMF.PK_RECORDED_INCOMPLETE
        assert rec["complete_current_packet"] is False
        assert "NO_FRESH_PROBABILITY" in rec["blockers"]
        assert PMF.PK_RECORDED_INCOMPLETE in rec["blockers"]
        assert "PINNAPI_HELD_READ:FEED_OWNERSHIP_NOT_HELD" in rec["blockers"]
        # the summary counts it, as incomplete, with its blockers
        s = mv["summary"]["management_records"]
        assert s["open_positions"] == mv["summary"]["open_positions"] - sum(
            1 for p in mv["positions"] if p["position_kind"] != XM.K_PAPER
            and p["state"] == "OPEN")
        assert s["incomplete_count"] >= 1
        assert s["complete_current_packet"] + s["incomplete_count"] + \
            s["records_unread"] == s["open_positions"]
        # the listing is bounded; the counts are whole
        assert len(s["incomplete"]) == min(s["incomplete_count"],
                                           XMR.SUMMARY_LISTED)
        assert s["incomplete_by_blocker"]["NO_FRESH_PROBABILITY"] >= 1
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_the_assessment_persists_why_the_probability_is_not_current():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xmrlim", entry_age_s=3600)
        slugs.append(slug)
        await XRF._review(conn, a, g)
        row = await conn.fetchrow(
            "SELECT valuation, evidence_state FROM "
            " xavier_management_assessments WHERE group_id=$1 "
            " ORDER BY assessed_at DESC LIMIT 1", g)
        assert row["evidence_state"] == PX.E_STALE
        lim = H.j(row["valuation"])["limitation"]
        assert "NOT a current expected value" in lim["probability_limitation"]
        assert lim["feed_refusal"] == "FEED_OWNERSHIP_NOT_HELD"
        _, pv = await _view_of(conn, g, now=AT + 2)
        assert pv["evidence"]["limitation"]["feed_refusal"] == \
            "FEED_OWNERSHIP_NOT_HELD"
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · AN EXPIRED PROTECTIVE ORDER IS NOT PROTECTION (Postgres)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_an_expired_protective_order_is_named_and_protects_nothing():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xmrexp", entry_age_s=3600)
        slugs.append(slug)
        await XRF._review(conn, a, g)
        sp = await conn.fetchrow(
            "SELECT order_id, expires_at FROM paper_orders WHERE group_id=$1"
            " AND role='STANDING_PROTECTION' AND state='RESTING'", g)
        later = L._epoch(sp["expires_at"]) + 5.0
        # past its GTD expiry, not yet terminated by the simulator
        _, pv = await _view_of(conn, g, now=later)
        rec = pv["management_record"]
        assert rec["protection"]["state"] == PMF.PS_EXPIRED
        assert rec["protection"]["valid_now"] is False
        o = next(x for x in rec["orders"] if x["order_id"] == sp["order_id"])
        assert o["state"] == "RESTING"
        assert o["live_now"] is False and o["protects_now"] is False
        assert "PROTECTION:%s" % PMF.PS_EXPIRED in rec["blockers"]
        assert rec["next"]["price_condition"] is None
        assert rec["next"]["price_condition_absent_because"] == \
            "PROTECTION:%s" % PMF.PS_EXPIRED
        # terminated EXPIRED: listed with its actual state, never protection
        await conn.execute(
            "UPDATE paper_orders SET state='EXPIRED', terminal_at="
            " to_timestamp($2), terminal_reason='GOOD_TILL_DATE_EXPIRED' "
            " WHERE order_id=$1", sp["order_id"], later)
        _, pv = await _view_of(conn, g, now=later + 1)
        rec = pv["management_record"]
        o = next(x for x in rec["orders"] if x["order_id"] == sp["order_id"])
        assert o["state"] == "EXPIRED" and o["remaining_qty"] == 0.0
        assert o["protects_now"] is False
        assert rec["protection"]["state"] == PMF.PS_UNPROTECTED
        assert rec["complete_current_packet"] is False
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · A POSITION THAT CAN NEVER BE PROTECTED SAYS SO (the PKE shape)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_an_unprotectable_position_names_its_protective_price_refusal():
    """Bought at 0.99: no cent <= 0.99 recovers cost + fees + the buffer,
    so the standing protection can never be placed and the packet can
    never be complete -- production atc-idnsl-pke-mau-2026-10-09-pke."""
    import uuid
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "xmrpke", now=AT - 5000)
        slug = "xmr-pke-%s" % uuid.uuid4().hex[:8]
        g = "paper_g_%s_pke" % a["account_id"][-10:]
        o = H.order(a, key="e", qty=50, limit=0.99, slug=slug, at=AT - 60,
                    group_id=g)
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=AT - 60)
        assert got["ok"], got
        await H.observe(conn, slug, AT - 57, offers=[(0.99, 50)])
        await SIM.simulate_order(conn, got["order"]["order_id"],
                                 now=AT - 56, fee_fn=H.zero_fee)
        await PX.step_handoff(conn, XRF._ctx(a, AT - 55))
        await H.observe(conn, slug, AT - 2, offers=[(0.45, 50)],
                        bids=[(0.43, 50)])
        await PX.review_group(conn, XRF._ctx(a, AT), g,
                              trigger=PX.T_BACKSTOP)
        _, pv = await _view_of(conn, g, now=AT + 2)
        rec = pv["management_record"]
        assert rec["protection"]["state"] == PMF.PS_UNPROTECTED
        assert rec["protection"]["protective_price_at_review"] == {
            "ok": False, "refusal": "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"}
        assert "PROTECTIVE_PRICE:NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR" in \
            rec["blockers"]
        assert "NO_VALID_ACTIVE_PROTECTION" in rec["blockers"]
        assert rec["next"]["price_condition"] is None
        assert rec["next"]["price_condition_absent_because"] == \
            "PROTECTIVE_PRICE:NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"
        assert [x["role"] for x in rec["orders"]] == ["ENTRY"]
        assert rec["complete_current_packet"] is False
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · COMPLETE NOW ON THE RECORD == COMPLETE NOW ON THE GATE (Postgres)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_record_and_the_gate_agree_while_current_and_after_expiry():
    from sportsassets.capital_readiness import feeds as CRF
    from tests import test_xavier_management_held_population_and_current_packets as HP
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await HP._held_hold(conn, "xmrgate")
        slugs.append(slug)
        acct = a["account_id"]
        _, pv = await _view_of(conn, g, now=AT + 5)
        rec = pv["management_record"]
        assert rec["complete_current_packet"] is True, rec["blockers"]
        assert rec["blockers"] == []
        assert rec["probability"]["current_now"] is True
        assert rec["next"]["probability_expires_at"] == pytest.approx(AT + 22)
        gate = await CRF.gate_xavier_complete(conn, {"account_id": acct,
                                                     "now": AT + 5})
        assert gate["value"] is True
        assert gate["evidence"]["complete_current_packets"] == 1
        assert gate["evidence"]["applicable_open_positions"] == 1
        assert gate["evidence"]["blockers"] == []
        # the probability expired at AT+22; no review since
        _, pv = await _view_of(conn, g, now=AT + 120)
        rec = pv["management_record"]
        assert rec["complete_current_packet"] is False
        assert rec["packet"]["why_not_complete_now"] == \
            PMF.PK_PROBABILITY_EXPIRED
        assert PMF.PK_PROBABILITY_EXPIRED in rec["blockers"]
        gate = await CRF.gate_xavier_complete(conn, {"account_id": acct,
                                                     "now": AT + 120})
        assert gate["value"] is False
        assert gate["evidence"]["complete_current_packets"] == 0
        b = gate["evidence"]["blockers"]
        assert b == [{"position_key": rec["position"]["position_key"],
                      "why": PMF.PK_PROBABILITY_EXPIRED,
                      "protection_state": PMF.PS_PROTECTED,
                      "latest_review_missing": []}]
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_the_gate_names_the_recorded_gaps_of_a_stale_position():
    from sportsassets.capital_readiness import feeds as CRF
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xmrgst", entry_age_s=3600)
        slugs.append(slug)
        await XRF._review(conn, a, g)
        gate = await CRF.gate_xavier_complete(
            conn, {"account_id": a["account_id"], "now": AT + 2})
        assert gate["value"] is False
        ev = gate["evidence"]
        assert ev["applicable_open_positions"] == 1
        assert ev["complete_current_packets"] == 0
        assert len(ev["blockers"]) == 1
        bl = ev["blockers"][0]
        assert bl["why"] == PMF.PK_RECORDED_INCOMPLETE
        assert "NO_FRESH_PROBABILITY" in bl["latest_review_missing"]
        assert ev["rule"] == PMF.PACKET_CURRENCY_RULE
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()
