"""XAVIER'S EXPIRING THESIS, SHADOW REALLOCATION AND VALUE-ADD.

  * THESIS (upgrade B): every new paper / actual position gets ONE immutable
    entry thesis at entry (probability + source stamp, EV, assumptions,
    evidence refs, expiry by the Pinnacle 30 s rule / event start); never
    back-filled after the entry window; reviews classify STILL_VALID /
    THESIS_CHANGED / EVIDENCE_EXPIRED (NO_ENTRY_THESIS when none);
  * REALLOCATE (upgrade C): the position's EV per dollar freed per hour
    against the best currently qualified, not-held ENTER decision; SHADOW
    only (no order), never on stale evidence;
  * VALUE-ADD (upgrade H): HOLD_TO_SETTLEMENT, IMMEDIATE_EXIT and
    ACTUAL_XAVIER frozen at entry; P&L, drawdown, fees and turnover once the
    outcome is known, by the predeclared rules; persisted, immutable, read
    by `value_add()`.

SYNTHETIC data in a scratch test database; no network, no real order.
"""
from __future__ import annotations

import json
import uuid

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import xavier_management as XM
from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_xavier_review_probability_freshness as XF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XF.AT
QTY = 100


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


THESIS = {"thesis_id": "xth:paper:g", "position_kind": "PAPER",
          "group_id": "g", "entry_probability": 0.62,
          "entry_cost_per_contract": 0.40, "entry_qty": 100.0,
          "entry_cost_usd": 40.0, "evidence_expires_at": 1030.0,
          "thesis_expires_at": None}


# ═════════════════════════════════════════════════════════════════════
# THE THESIS (pure)
# ═════════════════════════════════════════════════════════════════════

def test_expiry_follows_the_pinnacle_rule_and_the_event_start():
    e = XM.expiry(entered_at=1000.0, probability_source_at=995.0,
                  limit_s=30.0, event_start_at=5000.0)
    assert e["evidence_expires_at"] == 1025.0
    assert e["thesis_expires_at"] == 5000.0
    assert e["expiry_basis"].startswith("EVENT_START")
    e = XM.expiry(entered_at=1000.0, probability_source_at=995.0,
                  limit_s=30.0, event_start_at=900.0)
    assert e["thesis_expires_at"] is None
    assert e["expiry_basis"].startswith("PINNACLE_30S_RULE")
    assert "already started" in e["expiry_basis"]
    e = XM.expiry(entered_at=1000.0, probability_source_at=None,
                  limit_s=30.0, event_start_at=None)
    assert e["evidence_expires_at"] is None


def test_the_thesis_states():
    fresh = {"evidence_state": XM.E_FRESH}
    c = XM.classify_thesis
    v = c(THESIS, evidence=dict(fresh, probability=0.64), at=2000.0)
    assert v["state"] == XM.TH_VALID and v["delta_p"] == pytest.approx(0.02)
    ch = c(THESIS, evidence=dict(fresh, probability=0.70), at=2000.0)
    assert ch["state"] == XM.TH_CHANGED and ch["direction"] == "IMPROVED"
    flip = c(THESIS, evidence=dict(fresh, probability=0.38), at=2000.0)
    assert flip["state"] == XM.TH_CHANGED and flip["ev_sign_flipped"] is True
    assert flip["direction"] == "DETERIORATED"
    # stale or absent after the entry evidence expired: EXPIRED, never changed
    for st in (XM.E_STALE, XM.E_NONE):
        ex = c(THESIS, evidence={"evidence_state": st, "probability": 0.10},
               at=2000.0)
        assert ex["state"] == XM.TH_EXPIRED
        assert ex["current_probability"] is None
    # inside the entry evidence's own 30 s life: still valid
    w = c(THESIS, evidence={"evidence_state": XM.E_STALE, "probability": 0.62},
          at=1010.0)
    assert w["state"] == XM.TH_VALID
    assert w["basis"] == "ENTRY_EVIDENCE_WITHIN_ITS_OWN_FRESHNESS_LIFE"
    # a pre-event thesis whose horizon passed is not kept alive by its
    # entry evidence
    pre = dict(THESIS, thesis_expires_at=1005.0)
    assert c(pre, evidence={"evidence_state": XM.E_STALE}, at=1010.0)[
        "state"] == XM.TH_EXPIRED
    assert c(None, evidence=fresh, at=1.0)["state"] == XM.TH_NONE


def test_a_thesis_is_hashed_and_carries_the_three_counterfactuals():
    t = XM.build_thesis(
        kind="PAPER", group_id="g", position_ref="h", decision_id="d",
        strategy="S", slug="s", holding_side="LONG", entered_at=1000.0,
        first_fill_at=1001.0, qty=100, entry_cost_usd=40.0,
        entry_fees_usd=0.0, entry_price_per_contract=0.40, p=0.62,
        probability_source="ENTRY_DECISION_PINNACLE",
        probability_source_at=995.0, limit_s=30.0, event_start_at=None,
        assumptions={}, evidence_refs=[],
        exit_walk={"sold": 100.0, "unsold": 0.0, "proceeds_usd": 38.0,
                   "fees_usd": 0.0}, exit_basis={"obs_id": 1})
    assert t["entry_ev_usd"] == pytest.approx(22.0)
    assert t["entry_ev_per_contract_usd"] == pytest.approx(0.22)
    assert t["evidence_expires_at"] == 1025.0
    assert set(t["counterfactuals"]) >= set(XM.COUNTERFACTUALS)
    assert t["counterfactuals"]["IMMEDIATE_EXIT"]["pnl_if_fully_sold_usd"] \
        == pytest.approx(-2.0)
    assert len(t["content_sha256"]) == 64


# ═════════════════════════════════════════════════════════════════════
# THE THESIS IN THE DATABASE: AT ENTRY, ONCE, IMMUTABLE
# ═════════════════════════════════════════════════════════════════════

async def _held(conn, tag, *, bid_at_entry=0.38, p_entry=0.62,
                entry_age_s=3600, fresh_p=None, bid_now=0.80):
    """A held completed-game paper position handed to Xavier at AT - 55; the
    entry book has bids (an executable exit at entry)."""
    a = await H.new_account(conn, tag, now=AT - 5000)
    slug = "%sxt-%s" % (PL.SYN, uuid.uuid4().hex[:10])
    g = "paper_g_%s_xt" % a["account_id"][-10:]
    vid = await XF._reading(conn, slug, decided_at=AT - entry_age_s,
                            pin_age_s=5.0, p=p_entry)
    did = await XF._decision(conn, a, slug=slug, vid=vid, p=p_entry,
                             at=AT - entry_age_s)
    o = H.order(a, key="e", qty=QTY, limit=0.40, slug=slug, at=AT - 60,
                group_id=g)
    o.update(decision_id=did, strategy=PB.CG_STRATEGY)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=AT - 60)
    assert got["ok"], got
    await H.observe(conn, slug, AT - 57, offers=[(0.40, QTY)],
                    bids=[(bid_at_entry, QTY)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=AT - 56,
                             fee_fn=H.zero_fee)
    out = await PX.step_handoff(conn, XF._ctx(a, AT - 55))
    # the valid standing protection a complete management packet needs
    await H.protect(conn, XF._ctx(a, AT - 54), g, at=AT - 54)
    if fresh_p is not None:
        await XF._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0,
                          p=fresh_p)
    await H.observe(conn, slug, AT - 2, offers=[(bid_now + 0.02, QTY)],
                    bids=[(bid_now, QTY)])
    return a, g, slug, vid, out


@pg
async def test_the_entry_thesis_is_written_at_handoff_and_is_immutable():
    import asyncpg
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, vid, out = await _held(conn, "xtth")
        slugs.append(slug)
        assert out["theses_written"] == 1
        t = await conn.fetchrow("SELECT * FROM xavier_entry_theses WHERE "
                                " position_kind='PAPER' AND group_id=$1", g)
        assert t["entry_qty"] == QTY and float(t["entry_cost_usd"]) == 40.0
        assert t["entry_probability"] == pytest.approx(0.62)
        assert t["probability_source"] == "ENTRY_DECISION_PINNACLE"
        assert t["probability_source_at"].timestamp() == pytest.approx(
            AT - 3605)
        assert t["evidence_expires_at"].timestamp() == pytest.approx(
            AT - 3575)
        assert t["entry_ev_usd"] == pytest.approx(22.0)
        refs = {r["kind"] for r in _j(t["evidence_refs"])}
        assert {"paper_handoffs", "paper_decisions", "external_valuations",
                "paper_book_observations", "paper_fills"} <= refs
        cf = _j(t["counterfactuals"])
        assert cf["IMMEDIATE_EXIT"]["available"] is True
        assert cf["IMMEDIATE_EXIT"]["exit_vwap"] == pytest.approx(0.38)
        assert cf["HOLD_TO_SETTLEMENT"]["expected_pnl_at_entry_usd"] == \
            pytest.approx(22.0)
        # a second handoff pass writes nothing new
        again = await PX.step_handoff(conn, XF._ctx(a, AT - 50))
        assert again["theses_written"] == 0
        for sql in ("UPDATE xavier_entry_theses SET entry_probability = 0.9"
                    " WHERE group_id = $1",
                    "DELETE FROM xavier_entry_theses WHERE group_id = $1"):
            with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                await conn.execute(sql, g)
        # the review classifies it: only the hour-old entry reading -> the
        # evidence expired; nothing discretionary
        await PX.review_group(conn, XF._ctx(a, AT), g, trigger=PX.T_BACKSTOP)
        st = await conn.fetchval(
            "SELECT thesis_state FROM xavier_management_assessments WHERE "
            " group_id=$1", g)
        assert st == XM.TH_EXPIRED
    finally:
        await XF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_fresh_reading_confirms_or_changes_the_thesis():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, _, _ = await _held(conn, "xtvalid", fresh_p=0.63,
                                       bid_now=0.55)
        slugs.append(slug)
        await PX.review_group(conn, XF._ctx(a, AT), g, trigger=PX.T_BACKSTOP)
        r = await conn.fetchrow("SELECT * FROM xavier_management_assessments"
                                " WHERE group_id=$1", g)
        assert r["evidence_state"] == XM.E_FRESH
        assert r["thesis_state"] == XM.TH_VALID
        a2, g2, s2, _, _ = await _held(conn, "xtchg", fresh_p=0.75,
                                       bid_now=0.55)
        slugs.append(s2)
        await PX.review_group(conn, XF._ctx(a2, AT), g2,
                              trigger=PX.T_BACKSTOP)
        r2 = await conn.fetchrow("SELECT * FROM xavier_management_assessments"
                                 " WHERE group_id=$1", g2)
        assert r2["thesis_state"] == XM.TH_CHANGED
        assert _j(r2["thesis_detail"])["direction"] == "IMPROVED"
    finally:
        await XF._purge(conn, slugs)
        await conn.close()


@pg
async def test_no_thesis_is_back_filled_after_the_entry_window():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, _, _ = await _held(conn, "xtlate")
        slugs.append(slug)
        await conn.execute("SET session_replication_role = replica")
        await conn.execute("DELETE FROM xavier_entry_theses WHERE "
                           " group_id=$1", g)
        await conn.execute("SET session_replication_role = DEFAULT")
        late = XF._ctx(a, AT - 55 + XM.THESIS_ENTRY_WINDOW_S + 5)
        got = await XM.record_paper_thesis(conn, late, g)
        assert got["ok"] is False
        assert got["why"] == "OUTSIDE_THE_ENTRY_WINDOW_NO_HINDSIGHT"
        assert (await PX.step_handoff(conn, late))["theses_written"] == 0
        await PX.review_group(conn, late, g, trigger=PX.T_BACKSTOP)
        st = await conn.fetchval(
            "SELECT thesis_state FROM xavier_management_assessments WHERE "
            " group_id=$1", g)
        assert st == XM.TH_NONE
    finally:
        await conn.execute("SET session_replication_role = DEFAULT")
        await XF._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# REALLOCATE (SHADOW)
# ═════════════════════════════════════════════════════════════════════

HOURS = {"hours": 3.0, "basis": "test"}


def test_reallocate_is_shadow_and_needs_fresh_evidence_and_a_margin():
    best = {"efficiency": 0.05, "decision_id": "d"}
    stale = XM.reallocation(evidence_state=XM.E_STALE, qty=100, p=0.71,
                            liquidation_usd=69.0, hours=HOURS, best=best)
    assert stale["recommended"] is False and stale["blocker"] == XM.B_STALE
    assert stale["mode"] == "SHADOW" and stale["orders_placed"] == 0
    rec = XM.reallocation(evidence_state=XM.E_FRESH, qty=100, p=0.71,
                          liquidation_usd=69.0, hours=HOURS, best=best)
    # position: (71 - 69) / 69 / 3 h = 0.00966 per $ per h
    assert rec["position_efficiency"] == pytest.approx(2 / 69 / 3, rel=1e-6)
    assert rec["recommended"] is True and rec["blocker"] is None
    worse = XM.reallocation(evidence_state=XM.E_FRESH, qty=100, p=0.71,
                            liquidation_usd=69.0, hours=HOURS,
                            best={"efficiency": 0.011})
    assert worse["recommended"] is False and worse["blocker"] == XM.B_NO_EDGE
    none = XM.reallocation(evidence_state=XM.E_FRESH, qty=100, p=0.71,
                           liquidation_usd=69.0, hours=HOURS, best=None)
    assert none["blocker"] == XM.B_NO_OPPORTUNITY
    noliq = XM.reallocation(evidence_state=XM.E_FRESH, qty=100, p=0.71,
                            liquidation_usd=None, hours=HOURS, best=best)
    assert noliq["blocker"] == XM.B_NO_LIQUIDATION


def test_an_opportunity_is_ev_per_dollar_per_hour():
    o = XM.opportunity_from({
        "decision_id": "d", "us_market_slug": "s", "strategy": "S",
        "policy_decision": {"net_expected_profit_usd": 10.0},
        "economics": {"acquisition_cost_usd": 95.0, "fees_usd": 5.0}},
        at=1.0)
    assert o["capital_usd"] == 100.0
    assert o["efficiency"] == pytest.approx(10 / 100 / XM.DEFAULT_HORIZON_H)
    assert XM.opportunity_from({"policy_decision": {}, "economics": {}},
                               at=1.0) is None


async def _enter_decision(conn, a, *, slug, at, ev, cap):
    did = "papercg:%s" % uuid.uuid4().hex[:24]
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_internal, "
        " internal_model, p_pinnacle, pinnacle, p_blended, proposed_qty, "
        " limit_price, qualification_gaps, policy_version, policy_decision, "
        " simulator_version, strategy, economics) VALUES ($1,$2,$3,"
        " to_timestamp($4),NULL,$5,'LONG',$6,'fx-2','{}'::jsonb,'ENTER',NULL,"
        " '{}',NULL,'{}'::jsonb,0.6,'{}'::jsonb,NULL,100,0.50,'[]'::jsonb,$7,"
        " $8::jsonb,'TEST',$9,$10::jsonb)",
        did, a["session_id"], a["account_id"], float(at), slug, PL.LONG,
        PB.CG_VERSION, json.dumps({"net_expected_profit_usd": ev}),
        PB.CG_STRATEGY, json.dumps({"acquisition_cost_usd": cap,
                                    "fees_usd": 0.0}))
    return did


async def _purge_opportunities(conn):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM paper_decisions WHERE fixture = 'fx-2' "
            "   AND decided_at BETWEEN to_timestamp($1) AND to_timestamp($2)",
            AT - 400, AT + 1)


@pg
async def test_a_fresh_review_records_a_shadow_reallocation_and_places_nothing():
    conn = await H.connect()
    slugs = []
    await _purge_opportunities(conn)
    try:
        a, g, slug, _, _ = await _held(conn, "xtre", fresh_p=0.71,
                                       bid_now=0.69)
        slugs.append(slug)
        other = "%sxt-%s" % (PL.SYN, uuid.uuid4().hex[:10])
        old = "%sxt-%s" % (PL.SYN, uuid.uuid4().hex[:10])
        best = await _enter_decision(conn, a, slug=other, at=AT - 5, ev=10.0,
                                     cap=100.0)
        # stale (outside the 30 s rule) and held opportunities are ignored
        await _enter_decision(conn, a, slug=old, at=AT - 300, ev=90.0,
                              cap=100.0)
        await _enter_decision(conn, a, slug=slug, at=AT - 5, ev=90.0,
                              cap=100.0)
        orders_before = await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1",
            a["account_id"])
        await PX.review_group(conn, XF._ctx(a, AT), g, trigger=PX.T_BACKSTOP)
        r = await conn.fetchrow("SELECT * FROM xavier_management_assessments"
                                " WHERE group_id=$1", g)
        assert r["evidence_state"] == XM.E_FRESH
        assert r["recommendation"] == PX.A_HOLD          # the review held
        re = _j(r["reallocate"])
        assert re["mode"] == "SHADOW" and re["recommended"] is True
        assert re["best_opportunity"]["decision_id"] == best
        assert re["position_capital_freed_usd"] == pytest.approx(69.0)
        alts = {x["action"]: x for x in _j(r["alternatives"])}
        assert alts["REALLOCATE"]["recommended"] is True
        assert alts["REALLOCATE"]["rankable"] is False   # never selected
        # the shadow recommendation placed nothing beyond the protection
        after = await conn.fetch(
            "SELECT role FROM paper_orders WHERE account_id=$1",
            a["account_id"])
        assert len(after) - orders_before <= 1
        assert {x["role"] for x in after} <= {"ENTRY", "STANDING_PROTECTION"}
    finally:
        await _purge_opportunities(conn)
        await XF._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# VALUE-ADD
# ═════════════════════════════════════════════════════════════════════

def _thesis_with_exit(sold=100.0, proceeds=38.0):
    t = dict(THESIS, holding_side="LONG", us_market_slug="s")
    t["counterfactuals"] = XM.counterfactual_plan(
        qty=100, entry_cost_usd=40.0, entry_fees_usd=0.0,
        entry_price_per_contract=0.40, p=0.62,
        exit_walk={"sold": sold, "unsold": 100 - sold,
                   "proceeds_usd": proceeds, "fees_usd": 0.0},
        exit_basis={})
    return t


def test_value_add_by_the_predeclared_rules():
    t = _thesis_with_exit()
    marks = [{"hold_mark_pnl_usd": -12.0, "actual_mark_pnl_usd": -6.0},
             {"hold_mark_pnl_usd": 5.0, "actual_mark_pnl_usd": 3.0}]
    # Xavier sold half at 0.70 and the rest settled WON
    actual = {"pnl_usd": 50 * 0.70 + 50 * 1.0 - 40.0, "fees_usd": 0.0,
              "turnover_usd": 40.0 + 35.0, "outcome_basis": "SETTLED"}
    won = {"outcome": "WON", "payout_per_contract": 1.0}
    v = XM.value_add_compute(t, actual=actual, settle=won, marks=marks)
    cf = v["counterfactuals"]
    assert v["status"] == "FINAL"
    assert cf["HOLD_TO_SETTLEMENT"]["pnl_usd"] == pytest.approx(60.0)
    assert cf["HOLD_TO_SETTLEMENT"]["max_drawdown_usd"] == pytest.approx(-12.0)
    assert cf["IMMEDIATE_EXIT"]["pnl_usd"] == pytest.approx(-2.0)
    assert cf["IMMEDIATE_EXIT"]["turnover_usd"] == pytest.approx(78.0)
    assert cf["ACTUAL_XAVIER"]["pnl_usd"] == pytest.approx(45.0)
    assert cf["ACTUAL_XAVIER"]["max_drawdown_usd"] == pytest.approx(-6.0)
    inc = v["incremental"]
    assert inc["ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT"]["pnl_usd"] == \
        pytest.approx(-15.0)
    assert inc["ACTUAL_XAVIER_minus_IMMEDIATE_EXIT"]["pnl_usd"] == \
        pytest.approx(47.0)
    assert inc["ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT"][
        "max_drawdown_usd"] == pytest.approx(6.0)
    # not yet settled: HOLD pending, never guessed
    p = XM.value_add_compute(t, actual=dict(actual, outcome_basis="EXITED"),
                             settle=None, marks=[])
    assert p["status"] == "PENDING_SETTLEMENT_FOR_HOLD_COUNTERFACTUAL"
    assert p["counterfactuals"]["HOLD_TO_SETTLEMENT"]["pnl_usd"] is None
    assert p["incremental"]["ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT"][
        "available"] is False
    # Xavier's own outcome unknown: nothing computed
    assert XM.value_add_compute(t, actual={"pnl_usd": None}, settle=won,
                                marks=[]) is None
    # a void refunds the entry price (fees not refunded)
    vd = XM.value_add_compute(t, actual=actual, settle={
        "outcome": "VOID_REFUND", "refund": True}, marks=[])
    assert vd["counterfactuals"]["HOLD_TO_SETTLEMENT"]["pnl_usd"] == \
        pytest.approx(0.0)
    # the rules are the thesis's: the same inputs give the same row
    again = XM.value_add_compute(t, actual=actual, settle=won, marks=marks)
    assert again["content_sha256"] == v["content_sha256"]


@pg
async def test_value_add_is_persisted_when_the_position_settles():
    import asyncpg
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, vid, _ = await _held(conn, "xtva")
        slugs.append(slug)
        ctx = XF._ctx(a, AT)
        await PX.review_group(conn, ctx, g, trigger=PX.T_BACKSTOP)
        # still open: nothing to compute yet
        await XM.step_value_add(conn, ctx)
        assert await XM.value_add(conn, group_id=g) == []
        await PL.settle_valuation(conn, vid, outcome=1,
                                  basis="VENUE_REPORTED_OUTCOME")
        got = await PX.step_settle(conn, ctx)
        assert got["settled"] >= 1
        out = await XM.step_value_add(conn, ctx)
        assert out["written"] >= 1
        (v,) = await XM.value_add(conn, group_id=g)
        assert v["status"] == "FINAL" and v["position_kind"] == "PAPER"
        cf = v["counterfactuals"]
        assert cf["HOLD_TO_SETTLEMENT"]["pnl_usd"] == pytest.approx(60.0)
        assert cf["IMMEDIATE_EXIT"]["pnl_usd"] == pytest.approx(-2.0)
        assert cf["ACTUAL_XAVIER"]["pnl_usd"] == pytest.approx(60.0)
        assert v["incremental"]["ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT"][
            "pnl_usd"] == pytest.approx(0.0)
        assert v["incremental"]["ACTUAL_XAVIER_minus_IMMEDIATE_EXIT"][
            "pnl_usd"] == pytest.approx(62.0)
        # idempotent and immutable
        await XM.step_value_add(conn, ctx)
        assert await conn.fetchval("SELECT count(*) FROM xavier_value_add "
                                   " WHERE group_id=$1", g) == 1
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute("UPDATE xavier_value_add SET status='FINAL' "
                               " WHERE group_id=$1", g)
        # the read model carries it
        mv = await XM.management_view(conn, limit=500, now=AT + 10)
        p = next(x for x in mv["positions"] if x["group_id"] == g)
        assert p["state"] == "CLOSED"
        assert p["value_add"]["status"] == "FINAL"
    finally:
        await XF._purge(conn, slugs)
        await conn.close()


@pg
async def test_value_add_is_not_starved_by_an_exhausted_pass_budget():
    """Production 2026-10-07: Xavier's reviews exhausted every pass budget,
    the value-add step checked 0 theses per pass, and 57 settled positions
    with a thesis had no row. With the budget ALREADY exhausted the step
    still computes VALUE_ADD_MIN_PER_PASS per pass, and pending theses rotate
    out of the head of the queue -- the settled position's row is written
    within a bounded number of passes."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, vid, _ = await _held(conn, "xtvastarve")
        slugs.append(slug)
        ctx = XF._ctx(a, AT)
        await PX.review_group(conn, ctx, g, trigger=PX.T_BACKSTOP)
        await PL.settle_valuation(conn, vid, outcome=1,
                                  basis="VENUE_REPORTED_OUTCOME")
        assert (await PX.step_settle(conn, ctx))["settled"] >= 1
        spent = dict(ctx, deadline=0.0)          # the budget is gone
        first = await XM.step_value_add(conn, spent)
        assert first["checked"] >= min(XM.VALUE_ADD_MIN_PER_PASS,
                                       first["candidates"])
        for _ in range(60):
            if await XM.value_add(conn, group_id=g):
                break
            await XM.step_value_add(conn, spent)
        (v,) = await XM.value_add(conn, group_id=g)
        assert v["status"] == "FINAL"
    finally:
        await XF._purge(conn, slugs)
        await conn.close()


def test_live_cash_is_collateral_space():
    fills = [{"intent": "ORDER_INTENT_BUY_SHORT", "qty": 3, "price": 0.70,
              "fee_usd": 0.03},
             {"intent": "ORDER_INTENT_SELL_SHORT", "qty": 1, "price": 0.60,
              "fee_usd": 0.01}]
    lc = XM.live_cash(fills)
    assert lc["cash_usd"] == pytest.approx(-0.9 - 0.03 + 0.4 - 0.01)
    assert lc["held"] == 2 and lc["fees_usd"] == pytest.approx(0.04)


@pg
async def test_an_actual_positions_value_add_uses_venue_fills_and_settlement(
        monkeypatch):
    import time as _time

    from tests import test_execmirror as TE
    conn = await H.connect()
    po = None
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        mirror._probability_reader = PX.live_position_evidence
        mirror._management_assessor = XM.actual_review_hook
        # rc6.3 pmus-sizing: a whole 3,000 (3 contracts at 1:1000); 2,702 is
        # now ROUNDED DOWN to 2 contracts, never enlarged to 3
        po = await TE._paper_order(conn, acct, qty=3000)
        now = _time.time()
        vid = await XF._reading(conn, po["slug"], decided_at=now - 3600,
                                pin_age_s=5.0, p=0.62)
        did = await XF._decision(conn, acct, slug=po["slug"], vid=vid,
                                 p=0.62, at=now - 3600)
        await conn.execute("UPDATE execution_intents SET decision_id=$2 "
                           " WHERE group_id=$1", po["group_id"], did)
        await TE._paper_fill(conn, acct, po, qty=3000)
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        t = await XM.thesis_for(conn, XM.K_ACTUAL, po["group_id"])
        assert t is not None and t["entry_qty"] == 3
        # 3 bought at 0.55 + 0.03 fees; the exit at entry was the 0.40 bid
        assert t["entry_cost_usd"] == pytest.approx(1.68)
        assert await XM.compute_value_add(conn, t) == {
            "ok": False, "why": "ACTUAL_POSITION_OPEN"}
        await PL.settle_valuation(conn, vid, outcome=1,
                                  basis="VENUE_REPORTED_OUTCOME")
        got = await XM.compute_value_add(conn, t)
        assert got["ok"] is True and got["status"] == "FINAL"
        (v,) = await XM.value_add(conn, thesis_id=t["thesis_id"])
        cf = v["counterfactuals"]
        assert cf["ACTUAL_XAVIER"]["pnl_usd"] == pytest.approx(3 - 1.68)
        assert cf["HOLD_TO_SETTLEMENT"]["pnl_usd"] == pytest.approx(3 - 1.68)
        assert cf["IMMEDIATE_EXIT"]["available"] is True
        assert v["position_kind"] == "ACTUAL"
    finally:
        if po is not None:
            await XF._purge(conn, [po["slug"]])
        await conn.close()
