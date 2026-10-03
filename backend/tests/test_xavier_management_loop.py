"""XAVIER'S MANAGEMENT LOOP: EVERY NEW POSITION IS REVIEWED PROMPTLY AND ON
CADENCE, ON EXPLICIT PROBABILITY EVIDENCE, UNDER A RECORDED POLICY.

  * paper: a newly handed-off position gets its FIRST review within the
    bound even when the shared pass budget is already spent (Xavier's
    reserved budget), due reviews go first (FIRST before BACKSTOP whatever
    the group id), and a re-review lands on the backstop cadence;
  * actual: the mirror tick that hands an actual position off reviews it
    (FIRST, latency within ACTUAL_FIRST_REVIEW_BOUND_S), the next tick does
    not repeat it, the cadence re-reviews it, reviews continue while the
    lane is disabled (records only: nothing is placed), and one failing
    review never stops the others;
  * FRESH / STALE / UNAVAILABLE are explicit; a stale or absent probability
    never drives EXIT / REDUCE / REALLOCATE (in code and by the table's
    CHECK);
  * the management policy: reviews and the agents endpoint record the
    artifact APPROVED with its exact id / version / sha256 / approver only
    when the stored row is owner-approved AND its hash matches the code;
    otherwise READY_FOR_OWNER_APPROVAL -- never self-approved;
  * GET /api/command/xavier/management requires the command credential and
    returns positions with evidence, thesis, alternatives, policy and
    value-add.

SYNTHETIC data in a scratch test database; the venue is a fake (no network,
no real order).
"""
from __future__ import annotations

import inspect
import json
import time

import pytest

from sportsassets import execmirror as M
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import xavier_management as XM
from sportsassets.agents import xavier_small_live_policy as XSP
from tests import paper_harness as H
from tests import test_xavier_review_probability_freshness as XF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XF.AT


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def _assessments(conn, group_id, kind="PAPER"):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM xavier_management_assessments WHERE group_id=$1 "
        "   AND position_kind=$2 ORDER BY assessed_at, assessment_id",
        group_id, kind)]


# ═════════════════════════════════════════════════════════════════════
# THE LOOP (pure)
# ═════════════════════════════════════════════════════════════════════

def test_the_bounds_and_the_latency_record():
    first = XM.latency(kind=XM.K_PAPER, trigger=XM.T_FIRST, at=1000.0,
                       due_at=990.0, cadence_s=60.0)
    assert first["review_latency_s"] == 10.0
    assert first["latency_bound_s"] == XM.PAPER_FIRST_REVIEW_BOUND_S
    assert first["within_bound"] is True
    late = XM.latency(kind=XM.K_ACTUAL, trigger=XM.T_FIRST, at=1000.0,
                      due_at=900.0, cadence_s=60.0)
    assert late["latency_bound_s"] == XM.ACTUAL_FIRST_REVIEW_BOUND_S
    assert late["within_bound"] is False
    re = XM.latency(kind=XM.K_PAPER, trigger=XM.T_BACKSTOP, at=1000.0,
                    due_at=950.0, cadence_s=60.0)
    assert re["within_bound"] is True and re["latency_bound_s"] == 60.0
    unknown = XM.latency(kind=XM.K_PAPER, trigger=XM.T_BACKSTOP, at=1000.0,
                         due_at=None, cadence_s=60.0)
    assert unknown["review_latency_s"] is None
    assert unknown["within_bound"] is None             # unknown, not "late"


def test_the_actual_review_triggers():
    t = M.live_review_trigger
    assert t(last_reviewed_at=None, last_held=None, held=3, now=100.0) == \
        M.LIVE_T_FIRST
    assert t(last_reviewed_at=90.0, last_held=3, held=2, now=100.0) == \
        M.LIVE_T_FILL
    assert t(last_reviewed_at=90.0, last_held=3, held=3, now=100.0) is None
    assert t(last_reviewed_at=40.0, last_held=3, held=3,
             now=40.0 + M.MANAGEMENT_EVERY_S) == M.LIVE_T_BACKSTOP


# ═════════════════════════════════════════════════════════════════════
# PAPER: FIRST REVIEW WITHIN THE BOUND, CADENCE, PRIORITY
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_new_paper_position_is_reviewed_within_the_bound_even_on_a_spent_budget():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XF._held(conn, "xmfirst", entry_age_s=3600)
        slugs.append(slug)
        ctx = dict(XF._ctx(a, AT - 50), deadline=0.0)   # pass budget spent
        out = await PX.step(conn, ctx)
        assert out["reviews"] == 1 and out["by_trigger"] == {PX.T_FIRST: 1}
        assert out["first_review_latency_s"] == [pytest.approx(6.0)]
        rows = await _assessments(conn, g)
        assert len(rows) == 1
        r = rows[0]
        assert r["trigger"] == PX.T_FIRST
        assert r["review_latency_s"] == pytest.approx(6.0)
        assert r["latency_bound_s"] == XM.PAPER_FIRST_REVIEW_BOUND_S
        assert r["within_bound"] is True
        # not due again until the cadence
        out = await PX.step(conn, dict(XF._ctx(a, AT - 20), deadline=0.0))
        assert out["reviews"] == 0 and out["due"] == 0
        # the backstop re-review, on cadence, within its bound
        out = await PX.step(conn, dict(XF._ctx(a, AT + 11), deadline=0.0))
        assert out["by_trigger"] == {PX.T_BACKSTOP: 1}
        rows = await _assessments(conn, g)
        assert [x["trigger"] for x in rows] == [PX.T_FIRST, PX.T_BACKSTOP]
        assert rows[1]["review_latency_s"] == pytest.approx(1.0)
        assert rows[1]["within_bound"] is True
    finally:
        await XF._purge(conn, slugs)
        await conn.close()


@pg
async def test_due_reviews_go_first_whatever_the_group_id(monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        a, g1, s1 = await XF._held(conn, "xmprio", entry_age_s=3600)
        slugs.append(s1)
        # g1 was reviewed after its fill and is now BACKSTOP due; a second
        # group of the same account is brand new (FIRST) and sorts AFTER g1
        await PX.review_group(conn, XF._ctx(a, AT - 40), g1,
                              trigger=PX.T_FIRST)
        g2 = g1 + "_zz"
        slug2 = s1 + "b"
        slugs.append(slug2)
        from sportsassets import bettor_paper_ledger as L
        from sportsassets import bettor_paper_simulator as SIM
        o = H.order(a, key="e2", qty=50, limit=0.40, slug=slug2, at=AT - 30,
                    group_id=g2)
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=AT - 30)
        await H.observe(conn, slug2, AT - 27, offers=[(0.40, 50)])
        await SIM.simulate_order(conn, got["order"]["order_id"], now=AT - 26,
                                 fee_fn=H.zero_fee)
        await PX.step_handoff(conn, XF._ctx(a, AT - 25))
        seen = []

        async def review(conn_, ctx_, group_id, *, trigger, due_at=None):
            seen.append((group_id, trigger))
            return {"group_id": group_id, "reviews": []}
        monkeypatch.setattr(PX, "review_group", review)
        out = await PX.step(conn, dict(XF._ctx(a, AT + 30), deadline=0.0))
        assert seen[0] == (g2, PX.T_FIRST)
        assert (g1, PX.T_BACKSTOP) in seen
        assert out["due"] == 2
    finally:
        await XF._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# FRESH vs STALE vs UNAVAILABLE; STALE NEVER DRIVES A DISCRETIONARY ACTION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_fresh_stale_and_unavailable_are_explicit_on_the_assessment(
        monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        # FRESH: a reading for the same contract 8 s old; the 0.80 bid
        # out-values holding at 0.71, and on fresh evidence EXIT may rank
        a, g, slug = await XF._held(conn, "xmfresh", entry_age_s=3600)
        slugs.append(slug)
        await XF._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0, p=0.71)
        await PX.review_group(conn, XF._ctx(a, AT), g, trigger=PX.T_BACKSTOP)
        (fr,) = await _assessments(conn, g)
        assert fr["evidence_state"] == XM.E_FRESH
        assert fr["probability"] == pytest.approx(0.71)
        assert fr["probability_source"] == "PINNACLE_ONLY_CURRENT"
        assert fr["discretionary_permitted"] is True
        assert fr["recommendation"] == PX.A_EXIT
        # STALE: only the entry reading; the same 0.80 bid sells nothing
        a2, g2, s2 = await XF._held(conn, "xmstale", entry_age_s=3600)
        slugs.append(s2)
        await PX.review_group(conn, XF._ctx(a2, AT), g2,
                              trigger=PX.T_BACKSTOP)
        (st,) = await _assessments(conn, g2)
        assert st["evidence_state"] == XM.E_STALE
        assert st["discretionary_permitted"] is False
        assert st["recommendation"] == PX.A_HOLD
        alts = {x["action"]: x for x in _j(st["alternatives"])}
        assert alts["EXIT"]["rankable"] is False
        assert alts["EXIT"]["blocker"] == PX.B_STALE_MEASURE
        assert alts["REALLOCATE"]["recommended"] is False
        assert _j(st["reallocate"])["blocker"] == XM.B_STALE
        assert alts["VERIFIED_HEDGE"]["rankable"] is False
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
            " IN ('EXIT','REDUCE')", g2) == 0
        # UNAVAILABLE: null, never 0, nothing recommended
        a3, g3, s3 = await XF._held(conn, "xmnone", entry_age_s=3600)
        slugs.append(s3)

        async def measure(conn_, ctx_, *, pos, levels_buy):
            return {"p": None, "source": None, "stale": True,
                    "why": PX.R_NO_MEASURE}
        monkeypatch.setattr(PX, "_measure", measure)
        await PX.review_group(conn, XF._ctx(a3, AT), g3,
                              trigger=PX.T_BACKSTOP)
        (un,) = await _assessments(conn, g3)
        assert un["evidence_state"] == XM.E_NONE
        assert un["probability"] is None
        assert un["recommendation"] is None
        assert un["discretionary_permitted"] is False
    finally:
        await XF._purge(conn, slugs)
        await conn.close()


def test_a_stale_assessment_never_carries_a_discretionary_action():
    for state in (XM.E_STALE, XM.E_NONE):
        for rec in XM.DISCRETIONARY:
            a = XM.assessment(
                kind=XM.K_PAPER, group_id="g", review_id="r", thesis=None,
                at=1.0, lat=XM.latency(kind=XM.K_PAPER, trigger=XM.T_FIRST,
                                       at=1.0, due_at=0.0, cadence_s=60),
                evidence={"evidence_state": state, "probability": 0.5},
                venue_economics={}, thesis_state={"state": XM.TH_EXPIRED},
                alternatives=[], recommendation=rec,
                reallocate={"mode": "SHADOW", "recommended": True},
                policy={"status": XSP.STATUS_READY})
            assert a["recommendation"] == XM.A_HOLD
            assert a["discretionary_permitted"] is False
            assert a["reallocate"]["recommended"] is False
            if state == XM.E_NONE:
                assert a["probability"] is None


def test_the_actual_alternatives_rank_a_sale_only_on_fresh_evidence():
    re = {"recommended": False, "blocker": XM.B_STALE}
    fresh = XM.actual_alternatives(
        evidence={"evidence_state": XM.E_FRESH, "probability": 0.5}, held=4,
        exit_px=0.8, fee_fn=H.zero_fee, at=1.0, reallocate=re)
    assert fresh["recommendation"] == XM.A_EXIT
    stale = XM.actual_alternatives(
        evidence={"evidence_state": XM.E_STALE, "probability": 0.5}, held=4,
        exit_px=0.8, fee_fn=H.zero_fee, at=1.0, reallocate=re)
    assert stale["recommendation"] == XM.A_HOLD
    ex = next(x for x in stale["alternatives"] if x["action"] == XM.A_EXIT)
    assert ex["rankable"] is False and ex["blocker"] == XM.B_STALE
    none = XM.actual_alternatives(
        evidence={"evidence_state": XM.E_NONE, "probability": None}, held=4,
        exit_px=0.8, fee_fn=H.zero_fee, at=1.0, reallocate=re)
    assert none["recommendation"] is None


@pg
async def test_the_table_refuses_a_discretionary_action_on_stale_evidence():
    import asyncpg
    conn = await H.connect()
    try:
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
                    " ('xma:t','PAPER','g','r',now(),'FIRST_FILL',"
                    " 'STALE_ENTRY_TIME_PROBABILITY','{}','EVIDENCE_EXPIRED',"
                    " '{}','[]','EXIT',false,'{\"mode\":\"SHADOW\"}',"
                    " '{\"status\":\"READY_FOR_OWNER_APPROVAL\"}')")
        finally:
            await tr.rollback()
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# ACTUAL: FIRST ON THE HANDOFF TICK, CADENCE, LANE OFF, ISOLATION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_an_actual_position_is_reviewed_on_its_handoff_tick_and_on_cadence(
        monkeypatch):
    from tests import test_execmirror as TE
    conn = await H.connect()
    po = None
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        mirror._probability_reader = PX.live_position_evidence
        mirror._management_assessor = XM.actual_review_hook
        po = await TE._paper_order(conn, acct, qty=2702)
        now = time.time()
        vid = await XF._reading(conn, po["slug"], decided_at=now - 3600,
                                pin_age_s=5.0, p=0.62)
        did = await XF._decision(conn, acct, slug=po["slug"], vid=vid,
                                 p=0.62, at=now - 3600)
        await conn.execute("UPDATE paper_orders SET decision_id=$2 WHERE "
                           " order_id=$1", po["order_id"], did)
        await conn.execute("UPDATE execution_intents SET decision_id=$2 "
                           " WHERE group_id=$1", po["group_id"], did)
        await TE._paper_fill(conn, acct, po, qty=2702)
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        h = await conn.fetchrow("SELECT * FROM smalllive_handoffs WHERE "
                                " group_id=$1", po["group_id"])
        revs = await conn.fetch("SELECT * FROM smalllive_reviews WHERE "
                                " handoff_id=$1 ORDER BY reviewed_at",
                                h["handoff_id"])
        assert len(revs) == 1
        d = _j(revs[0]["detail"])
        assert d["review_trigger"] == M.LIVE_T_FIRST
        assert 0 <= d["review_latency_s"] <= XM.ACTUAL_FIRST_REVIEW_BOUND_S
        assert d["management_policy"]["status"] == XSP.STATUS_READY
        assert d["management"]["ok"] is True
        (a,) = await _assessments(conn, po["group_id"], "ACTUAL")
        assert a["trigger"] == M.LIVE_T_FIRST and a["within_bound"] is True
        assert a["evidence_state"] == XM.E_STALE
        assert a["recommendation"] == XM.A_HOLD
        th = await conn.fetchrow("SELECT * FROM xavier_entry_theses WHERE "
                                 " position_kind='ACTUAL' AND group_id=$1",
                                 po["group_id"])
        assert th is not None and a["thesis_id"] == th["thesis_id"]
        assert th["entry_probability"] == pytest.approx(0.62)
        cf = _j(th["counterfactuals"])
        assert cf["IMMEDIATE_EXIT"]["available"] is True
        assert cf["IMMEDIATE_EXIT"]["exit_vwap"] == pytest.approx(0.40)
        # the next tick does not repeat it
        await mirror.tick(conn)
        assert await conn.fetchval("SELECT count(*) FROM smalllive_reviews "
                                   " WHERE handoff_id=$1",
                                   h["handoff_id"]) == 1
        # the cadence re-reviews it
        t0 = time.time()
        mirror._now = lambda: t0 + M.MANAGEMENT_EVERY_S + 1
        await mirror.tick(conn)
        revs = await conn.fetch("SELECT detail FROM smalllive_reviews WHERE "
                                " handoff_id=$1 ORDER BY reviewed_at",
                                h["handoff_id"])
        assert [_j(r["detail"])["review_trigger"] for r in revs] == [
            M.LIVE_T_FIRST, M.LIVE_T_BACKSTOP]
        # the lane is switched off: the position is still reviewed (records
        # only) and nothing is placed
        placed = len(venue.placed)
        await conn.execute("UPDATE execmirror_control SET enabled=false "
                           " WHERE id=1")
        mirror._now = lambda: t0 + 2 * M.MANAGEMENT_EVERY_S + 2
        out = await mirror.tick(conn)
        assert out["state"] == "DISABLED" and out["xavier_live_reviews"] == 1
        assert len(venue.placed) == placed and venue.cancelled == []
    finally:
        await conn.execute("UPDATE execmirror_control SET enabled=false "
                           " WHERE id=1")
        if po is not None:
            await XF._purge(conn, [po["slug"]])
        await conn.close()


@pg
async def test_one_failing_actual_review_never_stops_the_others(monkeypatch):
    from tests import test_execmirror as TE
    conn = await H.connect()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        for g in ("grp_a_fail", "grp_b_ok"):
            await conn.execute(
                "INSERT INTO smalllive_handoffs (handoff_id, venue, group_id, "
                " us_market_slug, entry_mirror_id, opened_intent, live_held, "
                " live_bought, avg_entry_px, first_live_fill_at) VALUES "
                " ($1,'POLYMARKET',$2,$3,'em:x','ORDER_INTENT_BUY_LONG',3,3,"
                " 0.5, now())", "livehand:t:" + g, g, "slug-" + g)
        seen = []

        async def one(conn_, h, *, trigger, due_at):
            if h["group_id"] == "grp_a_fail":
                raise RuntimeError("boom")
            seen.append(h["group_id"])
            return "r"
        monkeypatch.setattr(mirror, "_review_one", one)
        n = await mirror.xavier_live_reviews(conn)
        assert n == 1 and seen == ["grp_b_ok"]
        assert await conn.fetchval(
            "SELECT count(*) FROM execmirror_events WHERE kind = "
            " 'XAVIER_LIVE_REVIEW_FAILED' AND detail->>'group_id' = "
            " 'grp_a_fail'") == 1
    finally:
        await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                           " smalllive_reconciliations")
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE MANAGEMENT POLICY RECORD (artifact, not CODE_DEFAULT)
# ═════════════════════════════════════════════════════════════════════

def _view(**k):
    base = dict(status=XSP.STATUS_APPROVED, source=XSP.SRC_STORED,
                integrity=XSP.INTEGRITY_OK, stored_sha=XSP.SHA256,
                approval={"actor": "OWNER_MATT", "approved_at": 1.79e9})
    base.update(k)
    return XSP._view(**base)


def test_the_review_record_is_approved_only_on_an_owner_row_with_the_codes_hash():
    ok = XSP.review_record(_view())
    assert ok["approved"] is True and ok["status"] == XSP.STATUS_APPROVED
    assert (ok["policy_id"], ok["version"], ok["sha256"]) == (
        XSP.POLICY_ID, XSP.VERSION, XSP.SHA256)
    assert ok["sha256"] == \
        "c59f3957e01691787c3ebccfbb67b5b7786fcc76929f0ffcc52ba2a5e416375e"
    assert ok["approved_by"] == "OWNER_MATT" and ok["approved_at"] == 1.79e9
    mism = XSP.review_record(_view(integrity=XSP.INTEGRITY_MISMATCH,
                                   stored_sha="0" * 64))
    assert mism["approved"] is False
    assert mism["status"] == XSP.STATUS_READY
    assert mism["approved_by"] is None and "differs" in mism[
        "why_not_approved"]
    ready = XSP.review_record(_view(status=XSP.STATUS_READY, approval=None))
    assert ready["approved"] is False and ready["status"] == XSP.STATUS_READY
    nostore = XSP.review_record(XSP.code_view(why="NO_STORED_ROW"))
    assert nostore["approved"] is False
    assert nostore["status"] == XSP.STATUS_READY
    rej = XSP.review_record(_view(status=XSP.STATUS_REJECTED, approval=None))
    assert rej["approved"] is False and rej["status"] == XSP.STATUS_REJECTED
    # CODE_DEFAULT is never approval
    assert XSP.review_record({"status": "CODE_DEFAULT"})["approved"] is False


def test_no_management_path_writes_an_approval():
    import sportsassets.agents.xavier_management as A
    import sportsassets.execmirror as B
    for mod in (A, B, PX, XSP):
        src = inspect.getsource(mod)
        assert "UPDATE agent_policy_artifacts" not in src
        assert "INSERT INTO agent_policy_artifacts" not in src


async def _approve(conn):
    await conn.execute(
        "UPDATE agent_policy_artifacts SET status='APPROVED', "
        " owner_approval_actor='OWNER_TEST', owner_approved_at=now(), "
        " owner_approval_statement='test approval' "
        " WHERE policy_id=$1 AND version=$2", XSP.POLICY_ID, XSP.VERSION)


@pg
@pytest.mark.parametrize("case", ["approved", "hash_mismatch",
                                  "not_approved"])
async def test_reviews_carry_the_exact_approved_policy_or_say_not_approved(
        case, monkeypatch):
    conn = await H.connect()
    slugs = []
    tr = conn.transaction()
    await tr.start()
    try:
        if case != "not_approved":
            await _approve(conn)
        if case == "hash_mismatch":
            monkeypatch.setattr(XSP, "SHA256", "f" * 64)
        a, g, slug = await XF._held(conn, "xmpol" + case[:3],
                                    entry_age_s=3600)
        slugs.append(slug)
        await PX.review_group(conn, XF._ctx(a, AT), g, trigger=PX.T_BACKSTOP)
        rv = await conn.fetchrow("SELECT selection FROM paper_xavier_reviews "
                                 " WHERE group_id=$1", g)
        pol = _j(rv["selection"])["management_policy"]
        (asm,) = await _assessments(conn, g)
        apol = _j(asm["policy"])
        assert apol == json.loads(json.dumps(pol, default=str))
        if case == "approved":
            assert pol["approved"] is True and pol["status"] == "APPROVED"
            assert pol["policy_id"] == "XAVIER_SMALL_LIVE_MANAGEMENT_V1"
            assert pol["version"] == "1"
            assert pol["sha256"] == XSP.SHA256
            assert pol["approved_by"] == "OWNER_TEST"
            assert pol["approved_at"] is not None
        else:
            assert pol["approved"] is False
            assert pol["status"] == "READY_FOR_OWNER_APPROVAL"
            assert pol["approved_by"] is None and pol["approved_at"] is None
        # the agents endpoint reads the same artifact
        from sportsassets.api import agents_core as AC
        agents = [{"agent_id": "XAVIER", "policy_version": "CODE_DEFAULT"}]
        await AC._annotate_policy(conn, agents)
        x = agents[0]
        assert x["management_policy_approved"] is (case == "approved")
        assert x["management_policy_record"]["status"] == pol["status"]
        assert x["policy_version_approved"] is False   # CODE_DEFAULT stays so
        if case == "approved":
            assert XSP.SHA256 in x["management_policy_label"]
        # the Command Centre management section shows it
        from sportsassets import execmirror_view as V
        row = {"group_id": g}
        mg = await V._management(conn, [row])
        xm = V._management_section(row, mg)["xavier_management"]
        assert xm["management_policy_status"] == pol["status"]
        assert xm["management_policy_approved"] is (case == "approved")
        assert xm["paper"]["evidence_state"] == XM.E_STALE
        assert xm["paper"]["thesis_state"] == XM.TH_EXPIRED
    finally:
        await tr.rollback()
        await XF._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE READ API
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_management_read_needs_the_command_credential_and_has_the_shape(
        monkeypatch):
    from tests import test_xavier_workspace_reads_truthfully as WT
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XF._held(conn, "xmapi", entry_age_s=3600)
        slugs.append(slug)
        await PX.step(conn, dict(XF._ctx(a, AT - 50), deadline=0.0))
        c = WT._client(monkeypatch)
        WT._pool(monkeypatch)
        assert c.get("/api/command/xavier/management").status_code == 401
        r = c.get("/api/command/xavier/management?limit=500", headers=WT.AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["read_only"] is True and body["places_orders"] is False
        assert body["policy"]["status"] in ("READY_FOR_OWNER_APPROVAL",
                                            "APPROVED")
        for k in ("loop", "rules", "summary", "positions"):
            assert k in body
        p = next(x for x in body["positions"] if x["group_id"] == g)
        assert p["position_kind"] == "PAPER" and p["state"] == "OPEN"
        assert p["latest_review"]["trigger"] == PX.T_FIRST
        assert p["latest_review"]["within_bound"] is True
        assert p["evidence"]["state"] == XM.E_STALE
        assert p["thesis"]["thesis_id"] == "xth:paper:%s" % g
        assert p["thesis"]["state"] == XM.TH_EXPIRED
        assert {x["action"] for x in p["alternatives"]} >= {
            "HOLD", "EXIT", "REDUCE", "VERIFIED_HEDGE", "REALLOCATE"}
        assert p["reallocate"]["mode"] == "SHADOW"
        assert p["policy"]["policy_id"] == XSP.POLICY_ID
        assert set(p["counterfactuals_at_entry"]) >= {
            "HOLD_TO_SETTLEMENT", "IMMEDIATE_EXIT", "ACTUAL_XAVIER"}
    finally:
        await XF._purge(conn, slugs)
        await conn.close()


def test_the_route_is_registered_on_the_app():
    from sportsassets.api import agents_xavier as AX
    from sportsassets.api import app as A
    from tests import test_agent_workspaces_show_runtime_records as AW
    assert "/api/command/xavier/management" in set(
        AW._route_paths(A.app.routes))
    # the application answers it with Xavier's router (nothing captures it)
    assert AW._first_match(A.app, "/api/command/xavier/management") is \
        AX.router
    from pathlib import Path
    src = (Path(M.__file__).parent / "api" / "app.py").read_text()
    assert "management_assessor=_XM.actual_review_hook" in \
        src.replace("\n", "").replace(" ", "")
