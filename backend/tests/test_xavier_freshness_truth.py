"""CAPITAL-CRITICAL: XAVIER NEVER SHOWS A STALE RECOMMENDATION AS CURRENT
(owner P0, 2026-10-04).

The defect: a paper position's PinnAPI probability went stale, EXIT / REDUCE
were (correctly) blocked for staleness, and Xavier still visibly recommended
HOLD -- HOLD survived by default, not on fresh evidence. These proofs pin the
correction (sportsassets/xavier_freshness.py and its call sites):

  1  a FRESH HOLD is allowed and reads CURRENT while its probability is
     inside its own freshness limit (the existing 30 s rule, unchanged);
  2  a stale HOLD is never recorded (write time: WAITING_FOR_FRESH_EVIDENCE /
     MANAGEMENT_UNAVAILABLE_STALE_INPUT; migration 222 CHECKs new rows) and a
     stored fresh HOLD reads STALE once now > source_at + limit (read time);
     a historical HOLD on stale evidence reads STALE, history untouched;
  3  stale input still produces no discretionary sale (B_STALE stays);
  4  refreshed input causes a new management decision (VALUATION_CHANGE);
  5  the read shapes (paper read model / management view / position room)
     and the served page code never present a stale decision as CURRENT;
  6  every alternative carries a value or a NAMED missing-evidence reason,
     over the six options (HOLD, EXIT, REDUCE, SAME_VENUE_NETTING,
     DIRECT_HEDGE, INDIRECT_HEDGE);
  7  freshness expiry schedules a review (paper timer + pass trigger; the
     actual mirror's trigger), bounded.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_readmodel as RM
from sportsassets import execmirror as M
from sportsassets import xavier_freshness as XF
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import xavier_management as XM
from sportsassets.agents import xavier_small_live_policy as XSP

from tests import paper_harness as H
from tests import test_xavier_review_probability_freshness as XR

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XR.AT
ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
LIMIT = 30.0          # the existing Pinnacle / PinnAPI rule, read not set


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _val(src_at, *, p=0.7, vid=11, limit=LIMIT):
    return XF.valuation_block({
        "probability": p, "probability_source": "PINNACLE_ONLY_CURRENT",
        "probability_source_at": src_at, "probability_received_at": src_at + 1,
        "probability_age_s": 8.0, "probability_limit_s": limit,
        "valuation_id": vid, "evidence_state": XF.E_FRESH},
        assessed_at=src_at + 8)


def _assess(state, rec, *, p=0.5, at=AT):
    return XM.assessment(
        kind=XM.K_PAPER, group_id="g", review_id="r:%s:%s" % (state, rec),
        thesis=None, at=at,
        lat=XM.latency(kind=XM.K_PAPER, trigger=XM.T_FIRST, at=at,
                       due_at=at - 1, cadence_s=60),
        evidence={"evidence_state": state, "probability": p,
                  "probability_source": "PINNACLE_ONLY_CURRENT",
                  "probability_source_at": at - 8, "probability_age_s": 8.0,
                  "probability_limit_s": LIMIT, "valuation_id": 7},
        venue_economics={}, thesis_state={"state": XM.TH_VALID},
        alternatives=[], recommendation=rec,
        reallocate={"mode": "SHADOW", "recommended": False},
        policy={"status": XSP.STATUS_READY})


# ═════════════════════════════════════════════════════════════════════
# 0 · THE THRESHOLD IS THE EXISTING ONE
# ═════════════════════════════════════════════════════════════════════

def test_the_freshness_limit_is_the_existing_30s_rule_unchanged():
    from sportsassets import bettor_paper_session as S
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    assert LOOP.PINNACLE_MAX_AGE_S == LIMIT
    assert S.default_config()["entry"]["pinnacle_max_age_s"] == LIMIT
    assert XF.default_limit_s() == LIMIT          # read, never defined
    src = (ROOT / "xavier_freshness.py").read_text()
    assert "PINNACLE_MAX_AGE_S =" not in src and "30.0" not in src


# ═════════════════════════════════════════════════════════════════════
# 1 · A FRESH HOLD IS ALLOWED
# ═════════════════════════════════════════════════════════════════════

def test_a_fresh_hold_is_recorded_and_reads_current_inside_its_limit():
    a = _assess(XF.E_FRESH, "HOLD")
    assert a["recommendation"] == "HOLD"
    assert a["recommendation_state"] == XF.S_CURRENT
    v = a["valuation"]
    for k in ("source", "source_at", "observed_at", "age_at_assessment_s",
              "limit_s", "expires_at", "valuation_id", "valuation_hash",
              "valuation_version"):
        assert k in v, k
    assert v["source_at"] == AT - 8 and v["limit_s"] == LIMIT
    assert v["expires_at"] == AT + 22 and v["valuation_id"] == 7
    got = XF.of_assessment(dict(a, valuation=v), now=AT + 10)
    assert got["recommendation_state"] == XF.S_CURRENT
    assert got["current_recommendation"] == "HOLD"
    assert got["display_recommendation"] == "HOLD"
    assert got["valuation"]["age_now_s"] == pytest.approx(18.0)


# ═════════════════════════════════════════════════════════════════════
# 2 · A STALE HOLD IS INVALID: WRITE TIME AND READ TIME
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("state,expect", [
    (XF.E_STALE, XF.REC_WAITING), (XF.E_NONE, XF.REC_UNAVAILABLE)])
@pytest.mark.parametrize("rec", ["HOLD", "EXIT", "REDUCE", "REALLOCATE",
                                 None])
def test_write_time_never_records_an_action_on_stale_evidence(state, expect,
                                                               rec):
    a = _assess(state, rec)
    assert a["recommendation"] == expect
    assert a["recommendation_state"] == expect
    assert a["discretionary_permitted"] is False


def test_actual_alternatives_no_longer_force_hold_on_stale_evidence():
    re_ = {"recommended": False, "blocker": XM.B_STALE}
    got = XM.actual_alternatives(
        evidence={"evidence_state": XM.E_STALE, "probability": 0.9}, held=4,
        exit_px=0.5, fee_fn=H.zero_fee, at=1.0, reallocate=re_)
    assert got["recommendation"] == XF.REC_WAITING
    assert got["recommendation"] != "HOLD"
    src = (ROOT / "agents/xavier_management.py").read_text()
    assert "rec = A_HOLD" not in src
    assert "recommendation = A_HOLD" not in src


def test_read_time_a_stored_fresh_hold_turns_stale_after_its_limit():
    a = dict(_assess(XF.E_FRESH, "HOLD"))
    inside = XF.of_assessment(a, now=AT + 21.9)
    assert inside["recommendation_state"] == XF.S_CURRENT
    after = XF.of_assessment(a, now=AT + 22.1)
    assert after["recommendation_state"] == XF.S_STALE
    assert after["current_recommendation"] is None
    assert after["display_recommendation"] == XF.S_STALE
    assert XF.I_EXPIRED in after["reasons"]
    assert after["recorded_recommendation"] == "HOLD"


def test_read_time_a_historical_hold_on_stale_evidence_reads_stale():
    # a row written BEFORE the correction (no valuation column, HOLD on a
    # stale probability): shown as STALE, never as the current HOLD
    old = {"recommendation": "HOLD", "evidence_state": XF.E_STALE,
           "assessed_at": AT, "probability": 0.62,
           "probability_age_s": 3605.0, "probability_source":
           "ENTRY_TIME_MEASURE"}
    got = XF.of_assessment(old, now=AT + 1, limit_s=LIMIT)
    assert got["recommendation_state"] == XF.S_STALE
    assert got["display_recommendation"] == XF.S_STALE
    assert XF.I_RECORDED_STALE in got["reasons"]
    rev = {"recommendation": "HOLD", "reviewed_at": AT,
           "measure": {"p": 0.62, "stale": True,
                       "evidence_state": XF.E_STALE,
                       "probability_source_at": AT - 3605,
                       "probability_limit_s": LIMIT}}
    g = XF.gated(rev, XF.of_review(rev, now=AT + 1))
    assert g["recommendation"] == XF.S_STALE
    assert g["recorded_recommendation"] == "HOLD"


@pytest.mark.parametrize("ctx,reason", [
    ({"latest_valuation": {"id": 99, "observed_at": AT - 2,
                           "probability": 0.55}}, XF.I_VALUATION),
    ({"feed_change_at": AT - 1}, XF.I_FEED),
    ({"mark_at_assessment": 0.64, "mark_now": 0.66}, XF.I_MARK),
    ({"event_start_at": AT + 3}, XF.I_GAME)])
def test_a_current_recommendation_is_invalidated_by_what_changed(ctx, reason):
    a = dict(_assess(XF.E_FRESH, "HOLD"))
    got = XF.of_assessment(a, now=AT + 5, **ctx)
    assert got["recommendation_state"] == XF.S_INVALID, got["reasons"]
    assert reason in got["reasons"]
    assert got["current_recommendation"] is None
    assert got["management_state"] == XF.S_WAITING


def test_a_newer_review_supersedes_even_a_fresh_one():
    a = dict(_assess(XF.E_FRESH, "HOLD"))
    got = XF.of_assessment(a, now=AT + 5, newer_assessment_id="xma:newer")
    assert got["recommendation_state"] == XF.S_SUPERSEDED
    assert got["management_state"] == XF.S_SUPERSEDED
    assert got["superseded_by"] == "xma:newer"
    assert got["current_recommendation"] is None


# ═════════════════════════════════════════════════════════════════════
# 4b · ONE CURRENT DECISION PER POSITION: THE PRODUCTION CASE
#      (an OLDER review on an 86.466 s probability surfaced in Slack while a
#      NEWER durable review used a 1.418 s one)
# ═════════════════════════════════════════════════════════════════════

def _rev(rid, at, age, rec, *, state=None, vid=None):
    st = state or (XF.E_FRESH if age <= LIMIT else XF.E_STALE)
    return {"review_id": rid, "group_id": "paper_g_prod", "reviewed_at": at,
            "recommendation": rec, "measure": {
                "p": 0.61, "probability": 0.61, "stale": st != XF.E_FRESH,
                "evidence_state": st, "source": "PINNAPI_FEED_CURRENT"
                if st == XF.E_FRESH else "PINNACLE_ONLY_LATEST",
                "probability_source": "PINNAPI_FEED_CURRENT"
                if st == XF.E_FRESH else "PINNACLE_ONLY_LATEST",
                "probability_source_at": at - age, "probability_age_s": age,
                "probability_limit_s": LIMIT, "valuation_id": vid}}


def _assert_production_case(cur, *, old_id, new_id):
    c, olds = cur["current"], cur["superseded"]
    assert c["review_id"] == new_id
    assert c["management_state"] == XF.S_CURRENT
    assert c["recommendation"] == "HOLD"
    d = c["decision"]
    for k in ("review_id", "valuation_id", "valuation_version",
              "valuation_timestamp", "review_timestamp", "age_seconds",
              "freshness_limit", "superseded_by"):
        assert k in d, k
    assert d["age_at_review_seconds"] == pytest.approx(1.418)
    assert d["freshness_limit"] == LIMIT and d["superseded_by"] is None
    (o,) = olds
    assert o["review_id"] == old_id
    assert o["management_state"] == XF.S_SUPERSEDED
    assert o["recommendation"] == XF.S_SUPERSEDED
    assert o["superseded_by"] == new_id
    assert o["decision"]["superseded_by"] == new_id
    assert o["decision"]["age_at_review_seconds"] == pytest.approx(86.466)


def test_production_case_the_newer_fresh_review_is_the_one_current():
    old = _rev("paperrev:old", AT - 60, 86.466, "HOLD", vid=101)
    new = _rev("paperrev:new", AT - 5, 1.418, "HOLD", vid=102)
    for rows in ([old, new], [new, old]):          # any order
        cur = XF.current_decisions(rows, now=AT)["paper_g_prod"]
        _assert_production_case(cur, old_id="paperrev:old",
                                new_id="paperrev:new")


@pytest.mark.parametrize("newest", [
    _rev("paperrev:n2", AT - 5, 86.466, XF.REC_WAITING),   # as now written
    _rev("paperrev:n2", AT - 5, 86.466, "HOLD")])          # a historical row
def test_a_stale_newest_review_waits_even_if_an_older_one_was_fresh(newest):
    older_fresh = _rev("paperrev:o2", AT - 20, 1.418, "HOLD", vid=7)
    cur = XF.current_decisions([older_fresh, newest], now=AT)["paper_g_prod"]
    c = cur["current"]
    assert c["review_id"] == "paperrev:n2"
    assert c["management_state"] == XF.S_WAITING
    assert c["current_recommendation"] is None
    assert c["recommendation"] != "HOLD"
    (o,) = cur["superseded"]
    assert o["management_state"] == XF.S_SUPERSEDED
    assert o["superseded_by"] == "paperrev:n2"


async def _insert_review(conn, a, g, r):
    await conn.execute(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, account_id,"
        " group_id, reviewed_at, trigger, recommendation, alternatives, "
        " exposure, measure, selection) VALUES ($1,$2,$3,$4,to_timestamp($5),"
        " 'SCHEDULED_BACKSTOP',$6,'[]'::jsonb,'{}'::jsonb,$7::jsonb,"
        " '{}'::jsonb)", r["review_id"], a["session_id"], a["account_id"], g,
        float(r["reviewed_at"]), r["recommendation"], json.dumps(r["measure"]))


@pg
@pytest.mark.parametrize("newest_stale", [False, True])
async def test_production_case_api_ops_persona_report_the_newer(newest_stale):
    from sportsassets import bettor_paper_ops as OPS
    from sportsassets.agents import persona_facts as PF
    import time as _t
    conn = await H.connect()
    try:
        now = _t.time()
        a = await H.new_account(conn, "xtrprod", now=now - 600)
        g = "paper_g_%s_prod" % a["account_id"][-10:]
        if newest_stale:
            old = dict(_rev("paperrev:%s:o" % g, now - 60, 1.418, "HOLD",
                            vid=5), group_id=g)
            new = dict(_rev("paperrev:%s:n" % g, now - 5, 86.466,
                            XF.REC_WAITING), group_id=g)
        else:
            old = dict(_rev("paperrev:%s:o" % g, now - 60, 86.466, "HOLD"),
                       group_id=g)
            new = dict(_rev("paperrev:%s:n" % g, now - 5, 1.418, "HOLD",
                            vid=6), group_id=g)
        for r in (old, new):
            await _insert_review(conn, a, g, r)
        # API: GET /api/command/paper/xavier's read model (latest per group)
        p = await RM.xavier_payload(conn, account_id=a["account_id"],
                                    now=now)
        (rec,) = [r for r in p["recommendations"]["data"]
                  if r["group_id"] == g]
        assert rec["review_id"] == new["review_id"]
        want = XF.S_WAITING if newest_stale else XF.S_CURRENT
        assert rec["management_state"] == want
        assert rec["recommendation"] == (XF.S_WAITING if newest_stale
                                         else "HOLD")
        assert rec["decision"]["review_id"] == new["review_id"]
        # the Xavier page's operations read: history labelled SUPERSEDED
        x = await OPS.xavier_operations(conn, account_id=a["account_id"],
                                        now=now)
        revs = {r["review_id"]: r for r in x["reviews"]["data"]}
        assert revs[old["review_id"]]["management_state"] == XF.S_SUPERSEDED
        assert revs[old["review_id"]]["superseded_by"] == new["review_id"]
        assert revs[old["review_id"]]["recommendation"] == XF.S_SUPERSEDED
        assert revs[new["review_id"]]["management_state"] == want
        # Slack / persona chat answer from these facts
        f = PF.Facts()
        assert await PF._xavier_current(conn, f, likes=[],
                                        context_ids=[g], now=now)
        cur = [i for i in f.items if i["field"] ==
               "current_management_decision"]
        sup = [i for i in f.items if i["field"] == "superseded_review"]
        assert len(cur) == 1 and cur[0]["record_id"] == new["review_id"]
        assert cur[0]["text"].startswith("CURRENT Xavier management")
        if newest_stale:
            assert "WAITING_FOR_FRESH_EVIDENCE" in cur[0]["text"]
            assert "HOLD;" not in cur[0]["text"].split("(")[0]
        else:
            assert "decision for paper position %s: HOLD" % g in cur[0]["text"]
            assert "1.418 s old" in cur[0]["text"]
        assert len(sup) == 1 and sup[0]["record_id"] == old["review_id"]
        assert sup[0]["text"].startswith("SUPERSEDED")
        assert "superseded by %s" % new["review_id"] in sup[0]["text"]
        # the raw unordered dump of paper_xavier_reviews is gone
        f2 = PF.Facts()
        await PF._paper(conn, f2, likes=[], context_ids=[g])
        assert not [i for i in f2.items if i["source"] ==
                    "paper_xavier_reviews" and i["field"] == "recommendation"]
    finally:
        await conn.close()


def test_an_unchanged_newer_valuation_or_a_sub_tick_mark_does_not_invalidate():
    a = dict(_assess(XF.E_FRESH, "HOLD"))
    got = XF.of_assessment(
        a, now=AT + 5,
        latest_valuation={"id": 99, "observed_at": AT - 2,
                          "probability": 0.5},
        mark_at_assessment=0.64, mark_now=0.645)
    assert got["recommendation_state"] == XF.S_CURRENT


@pg
async def test_the_table_refuses_a_new_stale_hold_but_keeps_history():
    import asyncpg
    conn = await H.connect()
    try:
        assert await XM.has_valuation_columns(conn)
        tr = conn.transaction()
        await tr.start()
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO xavier_management_assessments (assessment_id,"
                    " position_kind, group_id, review_id, assessed_at, "
                    " trigger, evidence_state, venue_economics, thesis_state,"
                    " thesis_detail, alternatives, recommendation, "
                    " discretionary_permitted, reallocate, policy) VALUES "
                    " ('xma:stalehold','PAPER','g','r',now(),'FIRST_FILL',"
                    " 'STALE_ENTRY_TIME_PROBABILITY','{}','EVIDENCE_EXPIRED',"
                    " '{}','[]','HOLD',false,'{\"mode\":\"SHADOW\"}',"
                    " '{\"status\":\"READY_FOR_OWNER_APPROVAL\"}')")
        finally:
            await tr.rollback()
        # the constraint binds new rows only (NOT VALID): history stays
        assert await conn.fetchval(
            "SELECT NOT convalidated FROM pg_constraint WHERE conname = "
            " 'xavier_assessments_no_stale_action_ck'")
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · STALE INPUT CANNOT PRODUCE A NEW DISCRETIONARY SELL;
# 4 · REFRESHED INPUT CAUSES A NEW MANAGEMENT DECISION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_stale_waits_without_selling_and_fresh_input_re_decides():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XR._held(conn, "xtruth", entry_age_s=3600)
        slugs.append(slug)
        ctx = dict(XR._ctx(a, AT), new_handoffs=[g])
        out = await PX.step(conn, ctx)
        assert out["by_trigger"] == {PX.T_FIRST: 1}
        rv = await conn.fetchrow(
            "SELECT * FROM paper_xavier_reviews WHERE group_id=$1", g)
        # the 0.80 bid out-values holding at the stale 0.62 -- and still:
        assert rv["recommendation"] == XF.REC_WAITING
        sel = _j(rv["selection"])
        assert sel["mechanical_selection"] == "HOLD"     # what was left
        assert sel["recommendation_state"] == XF.S_WAITING
        assert sel["valuation"]["limit_s"] == LIMIT
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
            " IN ('EXIT','REDUCE')", g) == 0
        (st,) = await conn.fetch(
            "SELECT * FROM xavier_management_assessments WHERE group_id=$1",
            g)
        assert st["recommendation"] == XF.REC_WAITING
        assert st["recommendation_state"] == XF.S_WAITING
        assert _j(st["valuation"])["source"] == "ENTRY_TIME_MEASURE"
        alts = {x["option"]: x for x in _j(st["alternatives"])}
        assert alts["EXIT"]["missing_evidence"] == XF.M_NO_FRESH_P
        assert alts["EXIT"]["blocker"] == PX.B_STALE_MEASURE
        assert alts["HOLD"]["missing_evidence"] == XF.M_NO_FRESH_P
        # the probability is REFRESHED: a newer valuation of the contract
        await XR._reading(conn, slug, decided_at=AT + 20, pin_age_s=5.0,
                          p=0.71)
        out2 = await PX.step(conn, XR._ctx(a, AT + 25))
        assert out2["by_trigger"] == {PX.T_VALUATION: 1}, out2
        rv2 = await conn.fetchrow(
            "SELECT * FROM paper_xavier_reviews WHERE group_id=$1 "
            " ORDER BY reviewed_at DESC LIMIT 1", g)
        assert rv2["trigger"] == PX.T_VALUATION
        assert _j(rv2["measure"])["evidence_state"] == XF.E_FRESH
        # a real decision on fresh evidence (the 0.80 bid beats 0.71)
        assert rv2["recommendation"] == "EXIT"
        a2 = await conn.fetchrow(
            "SELECT * FROM xavier_management_assessments WHERE group_id=$1 "
            " ORDER BY assessed_at DESC LIMIT 1", g)
        assert a2["recommendation"] == "EXIT"
        assert a2["recommendation_state"] == XF.S_CURRENT
        v2 = _j(a2["valuation"])
        assert v2["source_at"] == pytest.approx(AT + 15)
        assert v2["expires_at"] == pytest.approx(AT + 45)
        assert v2["valuation_id"] is not None
    finally:
        await XR._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · NO READ SHAPE SHOWS A STALE DECISION AS CURRENT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_read_shapes_gate_a_fresh_hold_that_has_since_expired():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XR._held(conn, "xtrread", entry_age_s=3600,
                                    p_entry=0.62)
        slugs.append(slug)
        # FRESH, and holding at 0.95 out-values the 0.80 bid: a real HOLD
        await XR._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0,
                          p=0.95)
        await PX.review_group(conn, XR._ctx(a, AT), g, trigger=PX.T_BACKSTOP)
        rv = await conn.fetchrow(
            "SELECT * FROM paper_xavier_reviews WHERE group_id=$1", g)
        assert rv["recommendation"] == "HOLD"           # fresh HOLD allowed
        # GET /api/command/paper/xavier's read model, inside the limit
        p1 = await RM.xavier_payload(conn, account_id=a["account_id"],
                                     now=AT + 10)
        (r1,) = p1["recommendations"]["data"]
        assert r1["recommendation"] == "HOLD"
        assert r1["recommendation_state"] == XF.S_CURRENT
        assert r1["freshness"]["valuation"]["expires_at"] == \
            pytest.approx(AT + 22)
        assert {x["option"] for x in r1["alternatives_complete"]} >= set(
            XF.OPTIONS)
        # ... and after it: never the current HOLD
        p2 = await RM.xavier_payload(conn, account_id=a["account_id"],
                                     now=AT + 60)
        (r2,) = p2["recommendations"]["data"]
        assert r2["recommendation"] == XF.S_STALE
        assert r2["recommendation_state"] == XF.S_STALE
        assert r2["current_recommendation"] is None
        assert r2["recorded_recommendation"] == "HOLD"
        # the management view's projection of the same assessment
        asmt = XM._assessment_view(await conn.fetchrow(
            "SELECT * FROM xavier_management_assessments WHERE group_id=$1",
            g))
        th = await XM.thesis_for(conn, XM.K_PAPER, g)
        for now, want in ((AT + 10, "HOLD"), (AT + 60, XF.S_STALE)):
            pv = XM.position_view(
                kind=XM.K_PAPER, group_id=g, ref={"open_qty": 100.0},
                thesis=th, a=asmt, va=None, cadence_s=60.0, now=now)
            assert pv["recommendation"] == want
            assert pv["recorded_recommendation"] == "HOLD"
            assert pv["freshness"]["valuation"]["limit_s"] == LIMIT
        # a newer valuation that moved the probability: INVALID now
        await XR._reading(conn, slug, decided_at=AT + 5, pin_age_s=1.0,
                          p=0.40)
        p3 = await RM.xavier_payload(conn, account_id=a["account_id"],
                                     now=AT + 12)
        (r3,) = p3["recommendations"]["data"]
        assert r3["recommendation_state"] == XF.S_INVALID
        assert XF.I_VALUATION in r3["freshness"]["reasons"]
    finally:
        await XR._purge(conn, slugs)
        await conn.close()


def test_the_position_room_never_renders_a_stale_hold_as_current():
    from sportsassets import position_rooms as P
    from tests import position_room_fixtures as F
    raw = F.raw_paper(fresh=False)
    x = P.xavier_panel(
        group_id=F.G_NYY, kind="PAPER",
        assessment=raw["xavier"]["assessments"][F.G_NYY],
        thesis=raw["xavier"]["theses"][F.G_NYY],
        review=raw["xavier"]["reviews"][F.G_NYY], standing_orders=[],
        cadence_s=60.0, now=F.NOW)
    assert x["display_recommendation"] != "HOLD"
    assert x["recommendation"] is None
    assert x["recommendation_state"] == XF.S_STALE
    assert x["latest_review"]["recommendation"] == XF.S_STALE


def test_the_served_xavier_page_code_renders_the_state_not_a_stale_hold():
    from sportsassets.api import agent_cc_ops as OPS
    js = OPS.OPS_CORE_JS
    for s in XF.STATES:
        assert s in js, s
    assert "data-rec-state" in js and "xrecHtml" in js
    assert "held is not a HOLD recommendation" in js
    # the old line that printed the stored word as the current one is gone
    assert "Current recommendation: <b>' + esc(rc.recommendation" not in js
    css = OPS.OPS_CSS
    assert ".xst.STALE" in css and ".xst.CURRENT" in css


def test_the_frontend_position_room_handles_the_state_when_present():
    """The position room page is published from the frontend branch; when
    this checkout carries it, it must render the gated state."""
    p = ROOT.parents[1] / "frontend" / "public" / "command" / "position.js"
    if not p.exists():
        pytest.skip("frontend/public/command/position.js is on the frontend "
                    "branch, not in this checkout")
    js = p.read_text()
    assert "recommendation_state" in js
    for s in ("CURRENT", "STALE", "INVALID", "WAITING_FOR_FRESH_EVIDENCE"):
        assert s in js, s


# ═════════════════════════════════════════════════════════════════════
# 6 · EVERY ALTERNATIVE: A VALUE OR A NAMED REASON
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("state", [XF.E_FRESH, XF.E_STALE, XF.E_NONE])
def test_every_option_carries_a_value_or_a_named_missing_evidence(state):
    alts = XF.complete_alternatives(
        [{"action": "HOLD", "rankable": True, "value_usd": 63.0},
         {"action": "EXIT", "rankable": True, "value_usd": 64.0},
         {"action": "REDUCE", "value_usd": None, "blocker": "NOTHING_TO_SELL"},
         {"action": "ACQUIRE_INDIRECT_HEDGE", "value_usd": None,
          "blocker": "INDIRECT_HEDGE_SEARCH_NOT_RUN_ON_THE_PAPER_BOOK"},
         {"action": "REALLOCATE", "mode": "SHADOW", "value_usd": None,
          "blocker": "NO_OTHER_CURRENTLY_QUALIFIED_OPPORTUNITY"}],
        evidence_state=state)
    opts = [a["option"] for a in alts]
    for o in XF.OPTIONS:
        assert opts.count(o) == 1, o
    for opt, value, reason in XF.missing_report(alts):
        assert value is not None or reason, opt
        assert reason != XF.M_NO_REASON, opt
    by = {a["option"]: a for a in alts}
    assert by["DIRECT_HEDGE"]["missing_evidence"] == XF.M_NO_HEDGE_ID
    assert by["SAME_VENUE_NETTING"]["missing_evidence"] == XF.M_NO_OPPOSITE
    if state == XF.E_FRESH:
        assert by["HOLD"]["missing_evidence"] is None
    else:
        for o in ("HOLD", "EXIT", "REDUCE"):
            assert by[o]["missing_evidence"] in (XF.M_NO_FRESH_P, XF.M_NO_P)
            assert by[o]["rankable"] is False
    assert XF.complete_alternatives(alts, evidence_state=state) == alts


def test_the_paper_and_actual_assessments_carry_all_six_options():
    re_ = {"recommended": False, "blocker": XM.B_STALE}
    for state in (XM.E_FRESH, XM.E_STALE):
        act = XM.actual_alternatives(
            evidence={"evidence_state": state, "probability": 0.5}, held=4,
            exit_px=0.8, fee_fn=H.zero_fee, at=1.0, reallocate=re_)
        got = {a["option"] for a in act["alternatives"]}
        assert got >= set(XF.OPTIONS)
        net = next(a for a in act["alternatives"]
                   if a["option"] == "SAME_VENUE_NETTING")
        assert net["missing_evidence"] == \
            "IDENTICAL_TO_EXIT_ON_A_ONE_NET_POSITION_VENUE"
    pa = XM.paper_alternatives(
        PX.alternatives(pos={"open_qty": 10, "cost_basis_usd": 4.0},
                        levels=[{"price": 0.8, "wire": 0.8, "qty": 10}],
                        p=0.5, fee_fn=H.zero_fee, at=1.0),
        reallocate={"recommended": False, "blocker": XM.B_STALE},
        evidence_state=XM.E_FRESH)
    assert {a["option"] for a in pa} >= set(XF.OPTIONS)
    for opt, value, reason in XF.missing_report(pa):
        assert value is not None or reason, opt


# ═════════════════════════════════════════════════════════════════════
# 7 · FRESHNESS EXPIRY SCHEDULES A REVIEW (bounded)
# ═════════════════════════════════════════════════════════════════════

def test_the_pass_trigger_fires_freshness_expiry_once():
    last = {"reviewed_at": AT, "measure": {
        "evidence_state": XF.E_FRESH, "probability_source_at": AT - 8,
        "probability_limit_s": LIMIT, "best_exit_at_review": 0.8}}
    kw = dict(group="g", new_handoffs=[], last=last, last_fill_at=None,
              book_at=None, best_exit=None, backstop_s=60.0)
    assert PX.last_evidence_expiry(last) == pytest.approx(AT + 22)
    assert PX._trigger(at=AT + 21, **kw) is None
    assert PX._trigger(at=AT + 22.5, **kw) == PX.T_EXPIRY
    # a review after the expiry has nothing left to expire: no loop
    later = dict(last, reviewed_at=AT + 23)
    assert PX._trigger(**dict(kw, last=later, at=AT + 40)) is None
    # a stale last review has no fresh evidence to expire
    stale = {"reviewed_at": AT, "measure": {"evidence_state": XF.E_STALE}}
    assert PX._trigger(**dict(kw, last=stale, at=AT + 40)) is None
    # order / valuation / game-state requeues
    assert PX._trigger(at=AT + 5, order_event_at=AT + 3, **kw) == PX.T_ORDER
    assert PX._trigger(at=AT + 5, valuation_at=AT + 4, **kw) == \
        PX.T_VALUATION
    assert PX._trigger(at=AT + 5, event_start_at=AT + 4, **kw) == PX.T_GAME


@pg
async def test_a_fresh_review_schedules_its_expiry_review():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XR._held(conn, "xtrexp", entry_age_s=3600)
        slugs.append(slug)
        await XR._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0,
                          p=0.95)
        asked = []
        ctx = dict(XR._ctx(a, AT),
                   schedule_review_at=lambda s, t: asked.append((s, t)))
        await PX.review_group(conn, ctx, g, trigger=PX.T_BACKSTOP)
        assert asked == [(slug, pytest.approx(AT + 22))]
        # a stale review schedules nothing (nothing fresh to expire)
        await XR._purge(conn, slugs)
        asked.clear()
        await PX.review_group(conn, dict(ctx, now=AT + 30,
                                         clock=lambda: AT + 30), g,
                              trigger=PX.T_BACKSTOP)
        assert asked == []
    finally:
        await XR._purge(conn, slugs)
        await conn.close()


async def test_the_expiry_timer_is_one_per_market_and_bounded(monkeypatch):
    fired = []
    monkeypatch.setattr(PR, "_EXPIRY", {"timers": {}, "scheduled": 0,
                                        "replaced": 0, "fired": 0,
                                        "dropped": 0})
    t0 = 1000.0
    got = PR.schedule_expiry_review("m1", t0 + 0.05, now=t0,
                                    fire=fired.append)
    assert got["scheduled"] and got["delay_s"] == pytest.approx(0.55)
    # a newer fresh review replaces the market's timer (one per market)
    PR.schedule_expiry_review("m1", t0 + 0.1, now=t0, fire=fired.append)
    assert PR.expiry_status()["timers"] == 1
    assert PR._EXPIRY["replaced"] == 1
    await asyncio.sleep(0.8)
    assert fired == [["m1"]]
    assert PR.expiry_status()["timers"] == 0
    # bounded: never more timers than EXPIRY_MAX_TIMERS
    monkeypatch.setattr(PR, "EXPIRY_MAX_TIMERS", 2)
    PR.schedule_expiry_review("a", t0 + 100, now=t0, fire=fired.append)
    PR.schedule_expiry_review("b", t0 + 100, now=t0, fire=fired.append)
    third = PR.schedule_expiry_review("c", t0 + 100, now=t0,
                                      fire=fired.append)
    assert third == {"scheduled": False, "why": "EXPIRY_TIMER_BOUND_REACHED"}
    for t in list(PR._EXPIRY["timers"].values()):
        t.cancel()
    # never further out than the cap
    PR._EXPIRY["timers"].clear()
    far = PR.schedule_expiry_review("z", t0 + 1e6, now=t0, fire=fired.append)
    assert far["delay_s"] == PR.EXPIRY_MAX_DELAY_S
    PR._EXPIRY["timers"]["z"].cancel()


def test_the_runtime_hands_the_expiry_scheduler_only_on_the_live_clock():
    src = (ROOT / "agents/paper_runtime.py").read_text()
    assert src.count('"schedule_review_at": (schedule_expiry_review if '
                     'live_clock') == 2
    assert "def schedule_expiry_review" in src


def test_the_actual_mirror_re_reviews_on_freshness_expiry():
    prob = {"evidence_state": XF.E_FRESH, "probability_source_at": AT - 8,
            "probability_limit_s": LIMIT}
    exp = M.live_evidence_expiry(prob)
    assert exp == pytest.approx(AT + 22)
    assert M.live_evidence_expiry(json.dumps(prob)) == pytest.approx(AT + 22)
    assert M.live_evidence_expiry(dict(prob, evidence_state=XF.E_STALE)) \
        is None
    kw = dict(last_reviewed_at=AT, last_held=3, held=3)
    assert M.live_review_trigger(now=AT + 20, evidence_expires_at=exp,
                                 **kw) is None
    assert M.live_review_trigger(now=AT + 23, evidence_expires_at=exp,
                                 **kw) == M.LIVE_T_EXPIRY
    assert M.live_review_trigger(
        now=AT + 23, evidence_expires_at=exp,
        **dict(kw, last_reviewed_at=AT + 22.5)) is None
    assert M.LIVE_T_EXPIRY in M.LIVE_TRIGGER_PRIORITY


# ═════════════════════════════════════════════════════════════════════
# 7b · ONLY FILLED QUANTITY COUNTS AS PROTECTION (coordinator item)
# ═════════════════════════════════════════════════════════════════════

def test_a_resting_protective_order_is_not_matched_protection():
    ex = PX.exposure_view({"open_qty": 100.0, "cost_basis_usd": 40.0},
                          resting_qty=100.0, filled_protection_qty=0.0)
    # before: unmatched = open - resting = 0 (a resting order counted)
    assert ex["unmatched_inventory_qty"] == 100.0
    assert ex["standing_qty"] == 100.0 and ex["resting_is_protection"] is False
    part = PX.exposure_view({"open_qty": 60.0, "cost_basis_usd": 24.0},
                            resting_qty=60.0, filled_protection_qty=40.0)
    # 40 filled (sold, out of open), 60 still resting: all 60 unmatched
    assert part["filled_protection_qty"] == 40.0
    assert part["unmatched_inventory_qty"] == 60.0


@pg
async def test_the_review_record_never_counts_resting_as_protection():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XR._held(conn, "xtrprot", entry_age_s=3600)
        slugs.append(slug)
        await PX.review_group(conn, XR._ctx(a, AT), g, trigger=PX.T_BACKSTOP)
        await PX.review_group(conn, dict(XR._ctx(a, AT + 61)), g,
                              trigger=PX.T_BACKSTOP)
        rv = await conn.fetchrow(
            "SELECT exposure, standing FROM paper_xavier_reviews WHERE "
            " group_id=$1 ORDER BY reviewed_at DESC LIMIT 1", g)
        ex = _j(rv["exposure"])
        resting = await conn.fetchval(
            "SELECT coalesce(sum(qty - filled_qty), 0) FROM paper_orders "
            " WHERE group_id=$1 AND role='STANDING_PROTECTION' "
            "   AND state = ANY($2::text[])", g, list(L.OPEN_STATES))
        assert float(resting) > 0, "the first review placed the protection"
        assert ex["standing_qty"] == pytest.approx(float(resting))
        assert ex["unmatched_inventory_qty"] == pytest.approx(ex["open_qty"])
        assert ex["resting_is_protection"] is False
    finally:
        await XR._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 8 · NO AUTHORITY
# ═════════════════════════════════════════════════════════════════════

def test_the_freshness_module_imports_nothing_and_places_nothing():
    import ast
    src = (ROOT / "xavier_freshness.py").read_text()
    mods = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            mods.add("." * n.level + (n.module or ""))
    assert mods <= {"__future__", "hashlib", "json", "typing", "sys"}, mods
    low = src.lower()
    for word in ("submit_order", "place_order", "cancel", "insert into",
                 "update ", "delete from"):
        assert word not in low, word


def test_listed_as_capital_critical():
    lst = (ROOT.parent / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_xavier_freshness_truth.py" in lst
