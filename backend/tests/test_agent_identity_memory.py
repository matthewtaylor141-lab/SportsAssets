"""THE SEVEN AGENTS' IDENTITY, MEMORY, EXPERIENCE, VOICE AND EVENTS (224).

Section 15 of the HQ2 backend directive, one test (or more) per line:

  * independent memory isolation; one agent cannot read another's private
    memory (a lesson crosses only by an explicit MEMORY_HANDOFF)
  * memory requires evidence (and the evidence must exist)
  * superseded memory remains historical; self-correction creates a new
    record (the database refuses DELETE and any other UPDATE)
  * voice profiles cannot silently fall back to another agent
  * an activity event must have a durable basis (a heartbeat is not one)
  * Xavier: one CURRENT review per position; stale -> WAITING_FOR_FRESH_
    EVIDENCE; a resting order is not protection in his facts or memory
  * the agent API returns UNAVAILABLE rather than fabricated zeros
  * identity versions are immutable (and approvals are not forged)
  * no memory write changes financial state
  * no personality setting grants financial permission

Every row is SYNTHETIC TEST DATA in a scratch paper account (the paper
harness rules); each DB test runs in one transaction that is rolled back.
"""
from __future__ import annotations

import copy
import json
import time
import uuid

import pytest

from sportsassets.agents import agent_activity as AA
from sportsassets.agents import agent_context as AC
from sportsassets.agents import agent_memory as M
from sportsassets.agents import identity as I
from sportsassets.agents import registry as R
from sportsassets import xavier_freshness as XF
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
T = H.T0


# ═════════════════════════════════════════════════════════════════════
# 0 · THE SYNTHETIC WORLD
# ═════════════════════════════════════════════════════════════════════

async def _world(conn) -> dict:
    """One paper position end to end: Derek's ENTER decision, its fill and
    the hand-off, a RESTING (unfilled) standing protection, three Xavier
    reviews (fresh HOLD, a HOLD recorded on stale evidence, a newest
    WAITING_FOR_FRESH_EVIDENCE), the LOST settlement, the Chief Allocator's
    SHADOW allocation of the decision and a Karen challenge of the
    decision that Audrey upheld."""
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    from sportsassets.agents import karen as K

    tag = uuid.uuid4().hex[:8]
    slug = "test-ident-%s" % tag
    a = await H.new_account(conn, "ident")
    g = "paper_g_ident_%s" % tag
    fee = H.flat_fee(0.0)
    e = H.order(a, key="e", slug=slug, holding_side="LONG", qty=100,
                limit=0.50, group_id=g, at=T)
    ge = await L.submit_order(conn, e, fee_fn=fee, now=T)
    assert ge["ok"], ge
    await H.observe(conn, slug, T + 3, bids=[(0.48, 500)],
                    offers=[(0.50, 500)])
    r = await SIM.simulate_order(conn, ge["order"]["order_id"], now=T + 4,
                                 fee_fn=fee)
    assert r["state"] == "FILLED", r
    p = H.order(a, key="p", slug=slug, holding_side="LONG", qty=100,
                limit=0.70, direction="SELL", role="STANDING_PROTECTION",
                group_id=g, order_type="RESTING", tif="GTD", delay=0.0,
                ttl=10 ** 8, at=T + 10, queue_ahead=0.0)
    gp = await L.submit_order(conn, p, fee_fn=fee, now=T + 10)
    assert gp["ok"], gp
    r = await SIM.simulate_order(conn, gp["order"]["order_id"], now=T + 11,
                                 fee_fn=fee)
    assert r["state"] == "RESTING", r
    fill = await conn.fetchrow(
        "SELECT fill_id, filled_at FROM paper_fills WHERE group_id=$1", g)
    did = "paper_dec_ident_%s" % tag
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, intent, verdict, "
        " internal_model, pinnacle, qualification_gaps, policy_version, "
        " simulator_version, proposed_qty, limit_price, p_blended) VALUES "
        " ($1,$2,$3,to_timestamp($4),$5,'LONG','ORDER_INTENT_BUY_LONG',"
        " 'ENTER','{}','{}','[]','TEST','TEST',100,0.50,0.62)",
        did, a["session_id"], a["account_id"], T - 5, slug)
    hid = "paper_h_ident_%s" % tag
    await conn.execute(
        "INSERT INTO paper_handoffs (handoff_id, session_id, account_id, "
        " group_id, decision_id, entry_order_id, first_fill_id, "
        " first_fill_at, confirmed_qty, outstanding_qty) VALUES "
        " ($1,$2,$3,$4,$5,$6,$7,$8,100,0)", hid, a["session_id"],
        a["account_id"], g, did, ge["order"]["order_id"], fill["fill_id"],
        fill["filled_at"])
    reviews = {}
    for key, at, rec, measure in (
            ("fresh", T + 30, "HOLD",
             {"p": 0.62, "stale": False,
              "evidence_state": XF.E_FRESH, "probability_source_at": T + 29,
              "probability_limit_s": 30, "valuation_id": 101}),
            ("stale_hold", T + 60, "HOLD",
             {"p": 0.62, "stale": True, "evidence_state": XF.E_STALE,
              "probability_source_at": T + 1, "probability_limit_s": 30,
              "valuation_id": 101}),
            ("waiting", T + 100, XF.REC_WAITING,
             {"p": 0.62, "stale": True, "evidence_state": XF.E_STALE,
              "probability_source_at": T + 1, "probability_limit_s": 30,
              "valuation_id": 101})):
        rid = "paper_rev_ident_%s_%s" % (key, tag)
        reviews[key] = rid
        await conn.execute(
            "INSERT INTO paper_xavier_reviews (review_id, session_id, "
            " account_id, group_id, reviewed_at, trigger, recommendation, "
            " alternatives, exposure, measure) VALUES ($1,$2,$3,$4,"
            " to_timestamp($5),'MARKET_EVENT',$6,'[]','{}',$7::jsonb)",
            rid, a["session_id"], a["account_id"], g, at, rec,
            json.dumps(measure))
    sid = "paper_s_ident_%s" % tag
    await conn.execute(
        "INSERT INTO paper_settlements (settlement_id, account_id, "
        " position_key, settlement_event_key, version, group_id, "
        " us_market_slug, holding_side, qty, outcome, payout_per_contract, "
        " payout_usd, evidence, evidence_source, settled_at) VALUES "
        " ($1,$2,$3,'evt-%s',1,$4,$5,'LONG',100,'LOST',0,0,'{}','TEST',"
        " to_timestamp($6))" % tag, sid, a["account_id"], g + ":LONG", g,
        slug, T + 500)
    run = "ident-run-%s" % tag
    await conn.execute(
        "INSERT INTO intel_runs (run_id, component, started_at, finished_at,"
        " status, summary, version) VALUES ($1,'ALLOCATOR',to_timestamp($2),"
        " to_timestamp($2),'OK','{}'::jsonb,'T')", run, T - 2)
    await conn.execute(
        "INSERT INTO intel_allocations (run_id, candidate_id, computed_at, "
        " rank, candidate_kind, us_market_slug, decision_id, score, "
        " shadow_weight, shadow_usd, reasons) VALUES ($1,'c1',"
        " to_timestamp($2),1,'NEW_DECISION',$3,$4,0.04,0.1,25,'[]'::jsonb)",
        run, T - 2, slug, did)
    ref = {"kind": "paper_decisions", "id": did}
    ch = await K.open_challenge(
        conn, target_agent="DEREK", target_kind="paper_decisions",
        target_id=did, detector="IDENT_TEST_DETECTOR_%s" % tag,
        claim="the entry cites no invalidation condition", severity="HIGH",
        evidence_refs=[ref], record_at=T - 5, at=T + 1)
    assert ch["ok"], ch
    cid = ch["challenge_id"]
    got = await K.respond(conn, cid, agent="DEREK", stance="DISPUTE",
                          response="the record names one", at=T + 2,
                          evidence_refs=[ref])
    assert got["ok"], got
    got = await K.resolve(conn, cid, resolver="AUDREY", outcome="UPHELD",
                          reason="no invalidation condition is recorded",
                          at=T + 3, evidence_refs=[ref])
    assert got["ok"], got
    return {"tag": tag, "account": a, "group": g, "decision": did,
            "handoff": hid, "settlement": sid, "reviews": reviews,
            "entry_order": ge["order"]["order_id"],
            "protection_order": gp["order"]["order_id"], "run": run,
            "challenge": cid, "slug": slug}


async def _tx():
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


async def _done(conn, tx):
    try:
        await tx.rollback()
    finally:
        await conn.close()


RECORD_TIME_CURSORS = ("challenge_outcomes", "scout_tournament_verdicts")


async def _since(conn):
    """Per-deriver cursors just before this scenario's rows: rows stamped
    with their recording time (recorded_at / created_at = this transaction's
    start) and rows stamped on the scenario clock (challenge resolutions,
    tournament verdicts). Rows other tests committed earlier stay out."""
    start = float(await conn.fetchval("SELECT extract(epoch FROM now())"))
    return {name: (T - 10 if name in RECORD_TIME_CURSORS else start - 1)
            for name in M.DERIVERS}


# ═════════════════════════════════════════════════════════════════════
# 1 · IDENTITY: one canonical, immutable, unapproved-until-approved record
# ═════════════════════════════════════════════════════════════════════

def test_eight_identities_each_complete_and_valid():
    assert I.AGENTS == ("DEREK", "XAVIER", "AUDREY", "KAREN",
                        "CHIEF_ALLOCATOR", "EDDIE", "SCOUT", "ADRIANA")
    sigs = set()
    for a in I.AGENTS:
        row = I.identity_row(a)
        assert I.validate_identity(row) is None, a
        for k in I.IDENTITY_FIELDS:
            assert row[k] not in (None, "", []), (a, k)
        assert row["approved_by"] == I.PENDING and row["approved_at"] is None
        sigs.add(row["signature"])
    assert len(sigs) == 8                      # eight distinct personalities
    assert I.IDENTITY_SPEC["EDDIE"]["authority_status"] == "SHADOW_ONLY"
    assert I.IDENTITY_SPEC["SCOUT"]["authority_status"] == \
        "RESEARCH_SHADOW_ONLY"
    assert I.IDENTITY_SPEC["KAREN"]["authority_status"] == \
        "CHALLENGE_ONLY_ZERO_AUTHORITY"
    # the allocator keeps the id the rest of the system already uses
    from sportsassets.agents import karen as K
    assert I.CHIEF_ALLOCATOR == K.CHIEF_ALLOCATOR
    assert I.agent_of("allocator") == I.agent_of("Chief Allocator") == \
        "CHIEF_ALLOCATOR"


def test_no_personality_setting_grants_financial_permission():
    before = {(a, t): I.permits(a, t) for a in I.AGENTS for t in R.TOOLS}
    saved = copy.deepcopy(I.IDENTITY_SPEC)
    try:
        for a in I.AGENTS:
            I.IDENTITY_SPEC[a]["personality_traits"] = [
                "authorized to approve limits", "may place orders"]
            I.IDENTITY_SPEC[a]["communication_style"] = "I approve my model"
            I.IDENTITY_SPEC[a]["may"] = ["write.approvals", "deploy"]
        after = {(a, t): I.permits(a, t) for a in I.AGENTS for t in R.TOOLS}
    finally:
        I.IDENTITY_SPEC.clear()
        I.IDENTITY_SPEC.update(saved)
    assert before == after                 # character never reaches permits
    for a in I.AGENTS:
        for t in R.NEVER_GRANTED:
            assert I.permits(a, t) is False, (a, t)
    bad = dict(I.identity_row("SCOUT"),
               personality_traits=["increase the risk limit to $500"])
    assert str(I.validate_identity(bad)).startswith(
        "PERSONALITY_REQUESTS_AUTHORITY")
    bad = dict(I.identity_row("KAREN"), may=["write.approvals of policy"])
    assert str(I.validate_identity(bad)).startswith("MAY_LIST_NAMES")


@pg
async def test_the_migration_seed_equals_the_code_and_versions_are_immutable():
    import asyncpg
    conn, tx = await _tx()
    try:
        for a in I.AGENTS:
            row = await I.current_identity(conn, a)
            spec = I.identity_row(a)
            for k in I.IDENTITY_FIELDS + ("identity_version", "content_sha",
                                          "source_directive", "source_ref"):
                assert row[k] == spec[k], (a, k)
            assert row["approved_by"] == "PENDING_OWNER_APPROVAL"
            assert row["approved_at"] is None
            vp = await I.current_voice_profile(conn, a)
            for k in ("voice_profile_id", "provider", "provider_voice_alias",
                      "provider_voice_id", "locale", "speaking_rate",
                      "assignment", "style_instructions"):
                assert vp[k] == I.VOICE_SPEC[a][k], (a, k)
        for sql in ("UPDATE agent_identity_versions SET title='CEO' "
                    " WHERE agent_id='DEREK'",
                    "DELETE FROM agent_identity_versions "
                    " WHERE agent_id='DEREK'",
                    "UPDATE agent_voice_profiles SET display_name='x'"):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(sql)
        row = I.identity_row("DEREK")
        cols = ("agent_id, identity_version, display_name, title, "
                "presentation, role, "
                "mission, personality_traits, communication_style, "
                "default_voice_profile, expertise_domains, "
                "decision_principles, may, may_not, signature, "
                "authority_status, content_sha, approved_by, approved_at")

        async def ins(version, approved_by, approved_at, auth=False,
                      status="ENTRY_REQUEST_THROUGH_GATED_PATH"):
            await conn.execute(
                "INSERT INTO agent_identity_versions (%s, "
                " grants_financial_authority) VALUES ('DEREK',$1,'Derek','t',"
                "'MALE','r','m','[]','s','vp','[]','[]','[]','[\"x\"]','sig',$5,$2,"
                "$3,$4,$6)" % cols, version, row["content_sha"], approved_by,
                approved_at, status, auth)
        # a rewrite of version 1, a gap, a forged approval without a time,
        # a pending row with a time, financial authority, a widened status:
        for args in ((1, "PENDING_OWNER_APPROVAL", None),
                     (3, "PENDING_OWNER_APPROVAL", None),
                     (2, "Some Person", None),
                     (2, "PENDING_OWNER_APPROVAL", time.time()),
                     (2, "PENDING_OWNER_APPROVAL", None, True),
                     (2, "PENDING_OWNER_APPROVAL", None, False,
                      "SHADOW_ONLY")):
            a2 = list(args)
            if a2[2] is not None:
                from datetime import datetime, timezone
                a2[2] = datetime.fromtimestamp(a2[2], timezone.utc)
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await ins(*a2)
        # the next version is an APPEND; version 1 stays as it was
        await ins(2, "PENDING_OWNER_APPROVAL", None)
        vers = await I.identity_versions(conn, "DEREK")
        assert [v["identity_version"] for v in vers] == [1, 2]
        assert vers[0]["title"] == row["title"]
    finally:
        await _done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# 2 · VOICE: one per agent, never another agent's
# ═════════════════════════════════════════════════════════════════════

def test_one_voice_profile_per_agent_and_no_secret_fields():
    ids = {I.VOICE_SPEC[a]["voice_profile_id"] for a in I.AGENTS}
    aliases = {I.VOICE_SPEC[a]["provider_voice_alias"] for a in I.AGENTS}
    assert len(ids) == len(aliases) == 8
    for a in I.AGENTS:
        assert I.IDENTITY_SPEC[a]["default_voice_profile"] == \
            I.VOICE_SPEC[a]["voice_profile_id"]
        for k in I.VOICE_SPEC[a]:
            assert not any(s in k.lower() for s in ("key", "secret", "token",
                                                    "password")), k
    assert I.VOICE_SPEC["CHIEF_ALLOCATOR"]["assignment"] == I.A_UNASSIGNED
    assert I.VOICE_SPEC["CHIEF_ALLOCATOR"]["speaking_rate"] is None


def test_allie_is_the_chief_allocator_a_woman_with_her_own_voice():
    """The owner's HQ3 directive: the Chief Allocator is Allie, a woman,
    displayed "Allie / Chief Allocator"; the id CHIEF_ALLOCATOR and the
    /allocator slug are unchanged; her voice is her own (UNASSIGNED until
    one exists) and is never Audrey's, nor any other agent's."""
    s = I.IDENTITY_SPEC["CHIEF_ALLOCATOR"]
    assert (s["agent_id"], s["display_name"], s["title"],
            s["presentation"]) == ("CHIEF_ALLOCATOR", "Allie",
                                   "Chief Allocator", I.PRESENTATION_FEMALE)
    assert I.SLUGS["CHIEF_ALLOCATOR"] == "allocator"
    assert I.agent_of("allocator") == I.agent_of("CHIEF_ALLOCATOR") \
        == "CHIEF_ALLOCATOR"
    v = I.VOICE_SPEC["CHIEF_ALLOCATOR"]
    assert v["assignment"] == I.A_UNASSIGNED
    assert v["provider_voice_id"] is None and v["persona_voice_ref"] is None
    others = [I.VOICE_SPEC[a] for a in I.AGENTS if a != "CHIEF_ALLOCATOR"]
    assert v["provider_voice_alias"] not in {o["provider_voice_alias"]
                                             for o in others}
    assert "AUDREY" not in v["provider_voice_alias"]
    assert "Audrey" not in v["display_name"] + v["style_instructions"]
    # a resolution that would hand Allie Audrey's voice id is refused
    latest = {"AUDREY": {"status": "RESOLVED", "voice_id": "v-audrey",
                         "resolved_at": 1.0},
              "CHIEF_ALLOCATOR": {"status": "RESOLVED",
                                  "voice_id": "v-audrey",
                                  "resolved_at": 2.0}}
    got = I.voice_status("CHIEF_ALLOCATOR", profile=v, latest=latest,
                         server_key_configured=True)
    assert got["status"] == I.V_UNASSIGNED and got["voice_id"] is None
    assert got["audio"]["status"] == I.V_UNAVAILABLE
    # ...and even were a runtime voice configured for her, Audrey's voice
    # id stays Audrey's: Allie is VOICE_SHARED, never handed it
    configured = dict(v, assignment=I.A_RUNTIME)
    got = I.voice_status("CHIEF_ALLOCATOR", profile=configured,
                         latest=latest, server_key_configured=True)
    assert got["status"] == I.V_SHARED and got["voice_id"] is None, got
    assert got["reason"] == "VOICE_ID_ALREADY_SPOKEN_BY:AUDREY"
    for a in I.AGENTS:
        assert I.IDENTITY_SPEC[a]["presentation"] in I.PRESENTATIONS


def test_a_shared_voice_is_never_silently_given_to_a_second_agent():
    latest = {
        "DEREK": {"status": "RESOLVED", "voice_id": "LiamVoice0001",
                  "voice_name": "Liam", "resolved_at": 100.0},
        "SCOUT": {"status": "RESOLVED", "voice_id": "LiamVoice0001",
                  "voice_name": "Liam", "resolved_at": 200.0},
        "XAVIER": {"status": "UNRESOLVED", "voice_id": None,
                   "reason": "NO_ELIGIBLE_VOICE_MATCHES_THE_PROFILE"}}
    d = I.voice_status("DEREK", profile=None, latest=latest,
                       server_key_configured=True)
    s = I.voice_status("SCOUT", profile=None, latest=latest,
                       server_key_configured=True)
    x = I.voice_status("XAVIER", profile=None, latest=latest,
                       server_key_configured=True)
    al = I.voice_status("CHIEF_ALLOCATOR", profile=None, latest=latest,
                        server_key_configured=True)
    assert d["status"] == I.V_ASSIGNED and d["voice_id"] == "LiamVoice0001"
    assert d["audio"]["status"] == "AVAILABLE"
    assert s["status"] == I.V_SHARED and s["voice_id"] is None
    assert s["reason"] == "VOICE_ID_ALREADY_SPOKEN_BY:DEREK"
    assert s["audio"]["status"] == I.V_UNAVAILABLE
    assert s["display_name"] == "UNASSIGNED"
    assert x["status"] == I.V_UNASSIGNED and x["audio"]["status"] == \
        I.V_UNAVAILABLE and "VOICE_NOT_RESOLVED" in x["reason"]
    assert al["status"] == I.V_UNASSIGNED
    assert al["reason"] == "NO_VOICE_CONFIGURED_FOR_THIS_AGENT"
    # no key: assigned but audio unavailable, said so
    d2 = I.voice_status("DEREK", profile=None, latest=latest,
                        server_key_configured=False)
    assert d2["audio"] == {"status": I.V_UNAVAILABLE, "reason":
                           "VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED"}
    resp = I.response_voice("SCOUT", message_id="m1", text="hi", voice=s)
    assert resp["voice_profile_id"] == "vp-scout-v1"
    assert resp["audio"]["status"] == I.V_UNAVAILABLE


@pg
async def test_the_database_refuses_another_agents_voice_id():
    import asyncpg
    conn, tx = await _tx()
    try:
        cols = ("voice_profile_id, agent_id, version, provider, "
                "provider_voice_alias, provider_voice_id, display_name, "
                "locale, style_instructions, assignment, content_sha, "
                "approved_by")
        sha = "0" * 64
        await conn.execute(
            "INSERT INTO agent_voice_profiles (%s) VALUES ('vp-derek-v2',"
            "'DEREK',2,'elevenlabs','ELEVENLABS_VOICE_ID_DEREK',"
            "'LiamVoice0001','d','en-US','s','CONFIGURED',$1,"
            "'PENDING_OWNER_APPROVAL')" % cols, sha)
        for vid, alias in (("LiamVoice0001", "ELEVENLABS_VOICE_ID_SCOUT"),
                           ("OtherVoice001", "ELEVENLABS_VOICE_ID_DEREK")):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO agent_voice_profiles (%s) VALUES "
                        "('vp-scout-v2','SCOUT',2,'elevenlabs',$2,$1,'s',"
                        "'en-US','s','CONFIGURED',$3,"
                        "'PENDING_OWNER_APPROVAL')" % cols, vid, alias, sha)
        cols_db = {r[0] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            " WHERE table_name='agent_voice_profiles'")}
        assert not [c for c in cols_db if any(
            s in c for s in ("key", "secret", "token", "password"))]
    finally:
        await _done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# 3 · MEMORY: evidence, isolation, supersession, self-correction
# ═════════════════════════════════════════════════════════════════════

def _cand(**kw):
    c = {"agent_id": "DEREK", "memory_kind": M.CASE,
         "subject_type": "paper_decisions", "subject_id": "paper_x",
         "summary": "a recorded fact", "confidence": 1.0,
         "evidence_refs": [{"kind": "paper_decisions", "id": "paper_x"}],
         "deriver": "test"}
    c.update(kw)
    return c


def test_memory_requires_evidence_confidence_and_mandate():
    assert M.validate_candidate(_cand()) is None
    assert M.validate_candidate(_cand(evidence_refs=[])) == M.R_NO_EVIDENCE
    assert M.validate_candidate(_cand(evidence_refs=None)) == M.R_NO_EVIDENCE
    assert M.validate_candidate(_cand(evidence_refs=[{"kind": "made_up",
                                                      "id": "1"}])) == \
        M.R_BAD_EVIDENCE
    assert M.validate_candidate(_cand(confidence=None)) == M.R_NO_CONFIDENCE
    assert M.validate_candidate(_cand(confidence=0)) == M.R_NO_CONFIDENCE
    assert M.validate_candidate(_cand(confidence=True)) == M.R_NO_CONFIDENCE
    # Derek may not remember an Eddie execution estimate as his own subject
    assert M.validate_candidate(_cand(
        subject_type="eddie_execution_estimates")) == M.R_OUT_OF_MANDATE
    # a memory is not a setting
    assert M.validate_candidate(_cand(facts={"new_threshold": 0.02})) == \
        M.R_AUTHORITY
    assert M.validate_candidate(_cand(
        summary="raise the risk limit to $500")) == M.R_AUTHORITY


def test_the_learner_cursor_never_skips_rows_sharing_the_batch_instant():
    n = M.DERIVE_LIMIT
    rows = [{"t": 100.0 + i // 10} for i in range(n)]
    assert M._cursor(rows[:3], "t", 999.0) == 999.0     # not full: all read
    c = M._cursor(rows, "t", 999.0)
    assert c < rows[-1]["t"] and c > rows[-11]["t"]     # re-read last instant
    same = [{"t": 5.0}] * n
    assert M._cursor(same, "t", 999.0) == 5.0           # never stalls
    lo, hi = M.wilson(15, 30)
    assert 0.31 < lo < 0.34 and 0.66 < hi < 0.69
    assert M.wilson(0, 0) is None


def test_resting_is_not_protection_and_stale_is_not_hold_in_xavier_facts():
    orders = [{"order_ref": "o1", "role": "STANDING_PROTECTION",
               "direction": "SELL", "qty": 100, "filled_qty": 0,
               "raw_state": "RESTING", "source": "paper_orders"}]
    base = {"agent_id": "XAVIER", "subject_type": "paper_position",
            "subject_id": "g", "evidence_refs": [{"kind": "paper_orders",
                                                  "id": "o1"}]}
    # resting quantity counted as protection: refused
    bad = _cand(**base, facts={"held_qty": 100, "orders": orders,
                               "filled_protection_qty": 100,
                               "unprotected_qty": 0})
    assert M.validate_candidate(bad) == M.R_RESTING_IS_NOT_PROTECTION
    assert M.validate_candidate(_cand(**base, facts={
        "protected_qty": 100, "filled_protection_qty": 0})) == \
        M.R_RESTING_IS_NOT_PROTECTION
    good = _cand(**base, facts={"held_qty": 100, "orders": orders,
                                "filled_protection_qty": 0,
                                "unprotected_qty": 100})
    assert M.validate_candidate(good) is None
    # an action word on a non-current review: refused; the state itself ok
    assert M.validate_candidate(_cand(**base, facts={
        "recommendation": "HOLD", "recommendation_state": "STALE"})) == \
        M.R_XAVIER_STALE_ACTION
    assert M.validate_candidate(_cand(**base, facts={
        "recommendation": XF.REC_WAITING,
        "recommendation_state": XF.S_WAITING})) is None
    assert M.validate_candidate(_cand(**base, facts={
        "recommendation": "HOLD", "recommendation_state": "CURRENT"})) is None


@pg
async def test_memory_is_private_and_crosses_only_by_an_explicit_handoff():
    import asyncpg
    conn, tx = await _tx()
    try:
        w = await _world(conn)
        res = await M.learn_once(conn, now=time.time() + 10,
                                 cursors=await _since(conn),
                                 only=("derek_settled_entries",))
        assert res["derivers"]["derek_settled_entries"]["created"] >= 1, res
        mine = await M.private_memories(conn, reader="DEREK", owner="DEREK")
        mem = next(m for m in mine if m["subject_id"] == w["decision"])
        # Xavier asks for Derek's memory: refused before any read
        with pytest.raises(M.PrivateMemoryRefused):
            await M.private_memories(conn, reader="XAVIER", owner="DEREK")
        with pytest.raises(M.PrivateMemoryRefused):
            await M.legacy_lessons(conn, reader="AUDREY", owner="DEREK")
        ctx = await AC.build_context(conn, "XAVIER", now=time.time())
        assert all(m["agent_id"] == "XAVIER"
                   for m in ctx["own_memories"] or [])
        assert mem["memory_id"] not in json.dumps(ctx, default=str)
        # the explicit hand-off: Xavier now reads the MESSAGE, not the table
        got = await M.share_memory(conn, from_agent="DEREK",
                                   to_agent="XAVIER",
                                   memory_id=mem["memory_id"])
        assert got["ok"] and got["created"], got
        ho = await M.handoffs_to(conn, "XAVIER")
        h = next(x for x in ho if x["shared_memory_id"] == mem["memory_id"])
        assert h["from_agent"] == "DEREK" and h["message_kind"] == \
            "MEMORY_HANDOFF"
        assert {"kind": "agent_memory_events", "id": mem["memory_id"]} in \
            h["evidence_refs"]
        # nobody may "share" a memory they do not own (module and database)
        bad = await M.share_memory(conn, from_agent="KAREN",
                                   to_agent="XAVIER",
                                   memory_id=mem["memory_id"])
        assert bad["refusal"] == M.R_NO_SUCH_MEMORY
        with pytest.raises(asyncpg.PostgresError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO agent_conversation_messages (message_id, "
                    " conversation_id, from_agent, to_agent, message_kind, "
                    " subject_type, subject_id, summary, evidence_refs, "
                    " shared_memory_id, status, created_at) VALUES ('mx',"
                    " 'c','KAREN','XAVIER','MEMORY_HANDOFF','s','s','s',"
                    " '[{\"kind\":\"k\",\"id\":\"i\"}]',$1,'INFORMATION',"
                    " now())", mem["memory_id"])
    finally:
        await _done(conn, tx)


@pg
async def test_evidence_must_exist_and_the_database_refuses_ungrounded_rows():
    import asyncpg
    conn, tx = await _tx()
    try:
        got = await M.promote(conn, _cand(
            subject_id="paper_nope", evidence_refs=[
                {"kind": "paper_decisions", "id": "paper_does_not_exist"}]))
        assert got["ok"] is False and got["refusal"] == M.R_EVIDENCE_MISSING
        for refs in ("[]", "[{\"kind\": \"paper_decisions\"}]", "{}"):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO agent_memory_events (memory_id, "
                        " agent_id, memory_kind, subject_type, subject_id, "
                        " summary, evidence_refs, confidence, learned_at, "
                        " identity_version, deriver) VALUES ('m-raw','DEREK',"
                        " 'CASE','paper_decisions','x','s',$1::jsonb,1,"
                        " now(),1,'raw')", refs)
    finally:
        await _done(conn, tx)


@pg
async def test_self_correction_is_a_new_record_and_the_old_stays_history():
    import asyncpg
    conn, tx = await _tx()
    try:
        w = await _world(conn)
        await M.learn_once(conn, now=time.time() + 10, cursors=await _since(conn),
                           only=("derek_settled_entries",))
        old = next(m for m in await M.private_memories(
            conn, reader="DEREK", owner="DEREK")
            if m["subject_id"] == w["decision"])
        assert old["claim_value"] == "LOST" and not old["historical"]
        # THE SETTLEMENT IS CORRECTED (v2 supersedes v1: WON). The deriver
        # sees the new version; the old memory is superseded, never deleted.
        sid2 = w["settlement"] + "_v2"
        await conn.execute(
            "INSERT INTO paper_settlements (settlement_id, account_id, "
            " position_key, settlement_event_key, version, supersedes, "
            " group_id, us_market_slug, holding_side, qty, outcome, "
            " payout_per_contract, payout_usd, evidence, evidence_source, "
            " settled_at) SELECT $1, account_id, position_key, "
            " settlement_event_key, 2, settlement_id, group_id, "
            " us_market_slug, holding_side, qty, 'WON', 1, 100, evidence, "
            " 'TEST', settled_at + interval '1 minute' "
            " FROM paper_settlements WHERE settlement_id=$2", sid2,
            w["settlement"])
        res = await M.learn_once(conn, now=time.time() + 20,
                                 cursors=await _since(conn),
                                 only=("derek_settled_entries",))
        assert res["derivers"]["derek_settled_entries"]["corrected"] == 1
        rows = [m for m in await M.private_memories(
            conn, reader="DEREK", owner="DEREK")
            if m["subject_id"] == w["decision"]]
        assert len(rows) == 2
        new = next(m for m in rows if m["memory_kind"] == M.SELF_CORRECTION)
        old2 = next(m for m in rows if m["memory_id"] == old["memory_id"])
        assert new["supersedes"] == old["memory_id"]
        assert old2["superseded_by"] == new["memory_id"]
        assert old2["historical"] and old2["summary"] == old["summary"]
        assert {"kind": "agent_memory_events", "id": old["memory_id"]} in \
            new["evidence_refs"]
        # a replay learns nothing new
        res = await M.learn_once(conn, now=time.time() + 30,
                                 cursors=await _since(conn),
                                 only=("derek_settled_entries",))
        assert res["derivers"]["derek_settled_entries"]["created"] == 0
        # the explicit API: a further correction, cited, by the owner only
        got = await M.self_correct(
            conn, "XAVIER", new["memory_id"], summary="x",
            evidence_refs=[{"kind": "paper_settlements", "id": sid2}],
            confidence=1.0)
        assert got["refusal"] == M.R_NO_SUCH_MEMORY
        got = await M.self_correct(
            conn, "DEREK", old["memory_id"], summary="x",
            evidence_refs=[{"kind": "paper_settlements", "id": sid2}],
            confidence=1.0)
        assert got["refusal"] == M.R_ALREADY_SUPERSEDED
        # the database: no delete, no edit, no second supersession
        for sql in ("DELETE FROM agent_memory_events WHERE memory_id=$1",
                    "UPDATE agent_memory_events SET summary='rewritten' "
                    " WHERE memory_id=$1",
                    "UPDATE agent_memory_events SET superseded_by=memory_id "
                    " WHERE memory_id=$1"):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(sql, old["memory_id"])
    finally:
        await _done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# 4 · ONE OUTCOME, SEVEN DIFFERENT LESSONS; XAVIER'S C28 RULES
# ═════════════════════════════════════════════════════════════════════

FIN_TABLES = ("paper_orders", "paper_fills", "paper_settlements",
              "paper_ledger", "paper_decisions", "paper_handoffs",
              "paper_xavier_reviews", "bettor_funded_intents",
              "bettor_funded_fills", "execution_intents", "intel_allocations",
              "karen_challenges", "agent_policy_versions", "agent_status",
              "execmirror_orders", "kalshi_live_intents")


async def _fin_snapshot(conn) -> dict:
    out = {}
    for t in FIN_TABLES:
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t):
            out[t] = await conn.fetchval(
                "SELECT md5(coalesce(string_agg(md5(x::text), ',' ORDER BY "
                " md5(x::text)), '')) FROM %s x" % t)
    return out


@pg
async def test_each_agent_learns_its_own_lesson_and_no_financial_state_moves():
    conn, tx = await _tx()
    try:
        w = await _world(conn)
        before = await _fin_snapshot(conn)
        res = await M.learn_once(conn, now=time.time() + 10,
                                 cursors=await _since(conn))
        after = await _fin_snapshot(conn)
        assert before == after                # no financial record changed
        for name, rep in res["derivers"].items():
            assert "error" not in rep, (name, rep)
            assert not rep["refused"], (name, rep)

        async def mem(agent):
            return await M.private_memories(conn, reader=agent, owner=agent,
                                            limit=200)
        derek = [m for m in await mem("DEREK")
                 if m["subject_id"] == w["decision"]]
        assert any(m["deriver"] == "derek_settled_entries" for m in derek)
        # the challenge of his decision is a RELATIONSHIP memory of his
        rel = [m for m in await mem("DEREK")
               if m["subject_id"] == w["challenge"]]
        assert rel and rel[0]["memory_kind"] == M.RELATIONSHIP
        assert rel[0]["facts"]["counterpart"] == "KAREN"
        xav = [m for m in await mem("XAVIER") if m["subject_id"] == w["group"]]
        kinds = {m["deriver"] for m in xav}
        assert {"xavier_settled_positions",
                "xavier_protection_at_settlement",
                "xavier_stale_action_recorded"} <= kinds
        man = next(m for m in xav
                   if m["deriver"] == "xavier_settled_positions")
        # ONE CURRENT review (the newest), the two older SUPERSEDED, and its
        # stale evidence is WAITING_FOR_FRESH_EVIDENCE -- never HOLD
        assert man["facts"]["review_id"] == w["reviews"]["waiting"]
        assert set(man["facts"]["superseded_review_ids"]) == {
            w["reviews"]["fresh"], w["reviews"]["stale_hold"]}
        assert man["facts"]["management_state"] == XF.S_WAITING
        assert man["facts"]["recommendation"] == XF.S_WAITING
        assert "HOLD" not in man["claim_value"]
        prot = next(m for m in xav
                    if m["deriver"] == "xavier_protection_at_settlement")
        # the protective sale RESTED, unfilled: nothing is protected
        assert prot["facts"]["filled_protection_qty"] == 0
        assert prot["facts"]["standing_order_qty"] == 100
        assert prot["facts"]["unprotected_qty"] == 100
        assert "Resting quantity never reduced" in prot["summary"]
        stale = next(m for m in xav
                     if m["deriver"] == "xavier_stale_action_recorded")
        assert stale["facts"]["recorded_recommendation"] == "HOLD"
        assert stale["facts"]["management_state"] == XF.S_WAITING
        # ...and Audrey was handed that protection lesson explicitly
        assert any(h["shared_memory_id"] == prot["memory_id"]
                   for h in await M.handoffs_to(conn, "AUDREY"))
        alloc = [m for m in await mem("CHIEF_ALLOCATOR")
                 if m["subject_id"] == "%s/c1" % w["run"]]
        assert alloc and "SHADOW" in alloc[0]["summary"]
        karen = [m for m in await mem("KAREN")
                 if m["subject_id"] == w["challenge"]]
        assert karen and karen[0]["claim_value"] == "UPHELD"
        aud = [m for m in await mem("AUDREY")
               if m["subject_id"] == w["challenge"]]
        assert aud and aud[0]["deriver"] == "audrey_evaluations"
        # every memory cites existing evidence and its identity version
        for agent in I.AGENTS:
            for m in await mem(agent):
                assert m["evidence_refs"]
                assert not await M.verify_evidence(conn, m["evidence_refs"])
                assert m["identity_version"] == 1
                assert 0 < m["confidence"] <= 1
    finally:
        await _done(conn, tx)


@pg
async def test_the_rollback_refuses_people_decisions_and_drops_only_224():
    import asyncpg
    import pathlib
    down = (pathlib.Path(__file__).resolve().parents[1] / "migrations" /
            "rollback" / "224_agent_identity_memory.down.sql").read_text()
    conn, tx = await _tx()
    try:
        async with conn.transaction():
            await conn.execute(down)
            for t in ("agent_identity_versions", "agent_voice_profiles",
                      "agent_memory_events", "agent_conversation_messages"):
                assert not await conn.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL", t)
            assert await conn.fetchval(
                "SELECT to_regclass('agent_persona_versions') IS NOT NULL")
            raise asyncpg.PostgresError("undo")
    except asyncpg.PostgresError:
        pass
    try:
        assert await conn.fetchval(
            "SELECT to_regclass('agent_identity_versions') IS NOT NULL")
        row = I.identity_row("SCOUT")
        await conn.execute(
            "INSERT INTO agent_identity_versions (agent_id, identity_version,"
            " display_name, title, presentation, role, mission, "
            " personality_traits, "
            " communication_style, default_voice_profile, expertise_domains,"
            " decision_principles, may, may_not, signature, "
            " authority_status, content_sha, approved_by) SELECT agent_id, "
            " 2, display_name, title, presentation, role, mission, "
            " personality_traits, "
            " communication_style, default_voice_profile, expertise_domains,"
            " decision_principles, may, may_not, signature, "
            " authority_status, content_sha, approved_by "
            " FROM agent_identity_versions WHERE agent_id='SCOUT'")
        assert row["agent_id"] == "SCOUT"
        with pytest.raises(asyncpg.PostgresError):
            async with conn.transaction():
                await conn.execute(down)
    finally:
        await _done(conn, tx)


@pg
async def test_eddie_and_scout_learn_from_their_own_outcomes():
    """Eddie learns realized vs predicted execution loss (and hands it to
    Derek explicitly); Scout learns the evaluator's verdict on his
    hypothesis -- an OBSERVATION he did not judge."""
    from sportsassets.agents import scout as S
    conn, tx = await _tx()
    try:
        w = await _world(conn)
        await R.ensure_identities(conn)
        eid, oid = "eex:ident-%s" % w["tag"], "eeo:ident-%s" % w["tag"]
        await conn.execute(
            "INSERT INTO eddie_execution_estimates (estimate_id, decision_id,"
            " estimator_version, estimated_at, recommendation, "
            " recommendation_reason, unmeasured, evidence_refs) VALUES "
            " ($1,$2,'T',to_timestamp($3),'WAIT','unmeasured',$4::jsonb,"
            " jsonb_build_array(jsonb_build_object('kind','paper_decisions',"
            " 'id',$2::text)))", eid, w["decision"], T, json.dumps(
                {k: "t" for k in ("theoretical_edge", "fees", "spread_cost",
                                  "slippage", "adverse_selection",
                                  "fill_probability", "time_to_fill",
                                  "capital_hours", "max_executable_size",
                                  "net_executable_edge")}))
        await conn.execute(
            "INSERT INTO eddie_execution_outcomes (outcome_id, estimate_id, "
            " decision_id, source, measured_at, realized_execution_loss_pp, "
            " predicted_execution_loss_pp, evidence_refs) VALUES ($1,$2,$3,"
            " 'PAPER',to_timestamp($4),1.5,0.5,jsonb_build_array("
            " jsonb_build_object('kind','paper_decisions','id',$3::text)))",
            oid, eid, w["decision"], T + 20)
        now = time.time()
        await S.register_sources(conn, now=now)
        feats = await S.register_features(conn, now=now)
        assert feats
        tid = await conn.fetchval(
            "SELECT tournament_id FROM scout_feature_tournaments "
            " ORDER BY frozen_at DESC LIMIT 1")
        await conn.execute(
            "UPDATE scout_feature_tournaments SET n=min_sample, "
            " baseline_score=0.25, challenger_score=0.2499, "
            " improvement=0.0001, verdict='REJECTED', verdict_reason='no "
            " value over PinnAPI', evaluated_by='CALIBRATION_ENGINE', "
            " evaluated_at=to_timestamp($2) WHERE tournament_id=$1", tid,
            now + 1)
        cur = await _since(conn)
        cur["scout_tournament_verdicts"] = now - 1
        res = await M.learn_once(conn, now=now + 10, cursors=cur,
                                 only=("eddie_execution_outcomes",
                                       "scout_tournament_verdicts"))
        for name in ("eddie_execution_outcomes",
                     "scout_tournament_verdicts"):
            assert res["derivers"][name]["created"] >= 1, res
        ed = [m for m in await M.private_memories(conn, reader="EDDIE",
                                                  owner="EDDIE")
              if m["subject_id"] == eid]
        assert ed and ed[0]["facts"]["realized_pp"] == 1.5
        assert "SHADOW" in ed[0]["summary"]
        assert any(h["shared_memory_id"] == ed[0]["memory_id"]
                   for h in await M.handoffs_to(conn, "DEREK"))
        sc = [m for m in await M.private_memories(conn, reader="SCOUT",
                                                  owner="SCOUT")
              if m["subject_id"] == tid]
        assert sc and sc[0]["claim_value"] == "REJECTED"
        assert sc[0]["summary"].startswith("OBSERVATION")
        assert "the evaluator did" in sc[0]["summary"]
    finally:
        await _done(conn, tx)


@pg
async def test_the_scheduled_step_runs_on_its_cadence_and_keeps_cursors():
    conn, tx = await _tx()
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           M.WATERMARK_KEY)
        now = time.time()
        first = await M.step(conn, {"now": now})
        assert first["ran"] is True and "error" not in first, first
        again = await M.step(conn, {"now": now + 5})
        assert again == {"ran": False, "why": "NOT_DUE", "last_at": now}
        wm = json.loads(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            M.WATERMARK_KEY))
        assert set(wm["cursors"]) == set(M.DERIVERS)
        later = await M.step(conn, {"now": now + M.RUN_EVERY_S + 1})
        assert later["ran"] is True
        # the step is on the paper pass, last, and cannot raise into it
        from sportsassets.agents import paper_runtime as PR
        names = [n for n, _ in PR.default_steps()]
        assert names[-1] == "agent_memory"
    finally:
        await _done(conn, tx)


@pg
async def test_xavier_context_carries_one_current_review_per_position():
    conn, tx = await _tx()
    try:
        w = await _world(conn)
        ctx = await AC.build_context(conn, "XAVIER", now=T + 200)
        cur = [c for c in ctx["current_reviews"] if c["group_id"] == w["group"]]
        assert len(cur) == 1                  # exactly one CURRENT decision
        c = cur[0]
        assert c["review_id"] == w["reviews"]["waiting"]
        assert c["valuation_id"] == 101 and c["valuation_version"]
        assert c["management_state"] == XF.S_WAITING
        assert c["current_recommendation"] is None
        assert {s["review_id"] for s in c["superseded"]} == {
            w["reviews"]["fresh"], w["reviews"]["stale_hold"]}
        assert all(s["superseded_by"] == w["reviews"]["waiting"]
                   for s in c["superseded"])
        assert ctx["protection_rule"].startswith("only FILLED quantity")
        # the fresh HOLD alone, 10 s after it: CURRENT with its action
        rows = [dict(r) for r in await conn.fetch(
            "SELECT review_id, group_id, extract(epoch FROM reviewed_at) "
            " AS reviewed_at, recommendation, refusal, measure, selection "
            " FROM paper_xavier_reviews WHERE review_id=$1",
            w["reviews"]["fresh"])]
        one = XF.current_decisions(rows, now=T + 40)[w["group"]]
        assert one["current"]["decision"]["current_recommendation"] == "HOLD"
        # ...and 60 s later the same review is no longer current
        late = XF.current_decisions(rows, now=T + 100)[w["group"]]
        assert late["current"]["decision"]["current_recommendation"] is None
        assert late["current"]["decision"]["management_state"] == XF.S_WAITING
    finally:
        await _done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# 5 · EVENTS, EXPERIENCE, UNAVAILABLE-NOT-ZERO
# ═════════════════════════════════════════════════════════════════════

def test_an_event_without_a_durable_basis_is_refused():
    ok = {"kind": "HANDOFF", "agent": "DEREK", "at": 1.0,
          "basis": {"table": "paper_handoffs", "id": "h1"}}
    assert AA.validate_event(ok) is None
    assert AA.validate_event(dict(ok, basis={})) == AA.R_NO_BASIS
    assert AA.validate_event(dict(ok, basis={"table": "agent_status",
                                             "id": "DEREK"})) == AA.R_NO_BASIS
    assert AA.validate_event(dict(ok, basis={"table": "heartbeat",
                                             "id": "x"})) == AA.R_NO_BASIS
    assert AA.validate_event(dict(ok, kind="ANIMATION")) is not None
    assert AA.validate_event(dict(ok, at=None)) is not None
    assert "agent_status" not in AA.DURABLE_SOURCES


@pg
async def test_events_stand_on_durable_rows_and_a_heartbeat_makes_none():
    conn, tx = await _tx()
    try:
        w = await _world(conn)
        await M.learn_once(conn, now=time.time() + 10, cursors=await _since(conn))
        cursor = "%r|" % (T - 10)
        hi = time.time() + 20                 # one fixed read horizon
        # page through the stream with its own cursor (other tests may have
        # left rows in the same window): every page is after the last
        seen, nxt, pages = [], cursor, 0
        while pages < 100:
            page = await AA.events(conn, since=nxt, limit=200, now=hi)
            assert page["rejected_without_durable_basis"] == 0
            if not page["events"]:
                break
            keys = [(e["at"], e["event_id"]) for e in page["events"]]
            assert keys == sorted(keys)
            c_at, c_id = AA.parse_cursor(nxt)
            assert keys[0] > (c_at, c_id or "")
            seen += page["events"]
            nxt, pages = page["next_cursor"], pages + 1
        assert len({e["event_id"] for e in seen}) == len(seen)  # no repeats
        got = {"events": seen, "next_cursor": nxt}
        kinds = {e["kind"] for e in got["events"]}
        assert {"DECISION_RECORDED", "REVIEW_RECORDED",
                "WAITING_FOR_EVIDENCE", "HANDOFF", "CHALLENGE_RAISED",
                "CHALLENGE_ANSWERED", "MEMORY_LEARNED"} <= kinds, kinds
        for e in got["events"]:
            assert AA.validate_event(e) is None
            assert e["basis"]["table"] in AA.DURABLE_SOURCES
        ids = {e["basis"]["id"] for e in got["events"]}
        assert {w["decision"], w["handoff"], w["challenge"],
                w["reviews"]["waiting"]} <= ids
        # the stale HOLD is never shown as a current HOLD
        sh = next(e for e in got["events"]
                  if e["basis"]["id"] == w["reviews"]["stale_hold"])
        assert sh["recommendation_state"] == XF.S_SUPERSEDED
        assert not sh["summary"].startswith("Reviewed %s: HOLD" % w["group"])
        # a heartbeat alone adds nothing to the stream
        nxt = got["next_cursor"]
        await R.heartbeat(conn, "DEREK", state="EVALUATING",
                          activity="BUSY_LOOKING", now=hi - 5)
        await R.heartbeat(conn, "KAREN", state="WAITING_FOR_EVIDENCE",
                          activity="NOTHING_TO_CHALLENGE", now=hi - 4)
        again = await AA.events(conn, since=nxt, limit=200, now=hi)
        assert again["events"] == [], again["events"]
        # the agent filter keeps the agent's own events and counterparts
        mine = await AA.events(conn, agent="KAREN", since=cursor, limit=200,
                               now=hi)
        assert mine["events"] and all(
            "KAREN" in (e["agent"], e["counterpart"]) for e in mine["events"])
    finally:
        await _done(conn, tx)


@pg
async def test_experience_is_reproducible_and_unavailable_is_never_zero(
        monkeypatch):
    conn, tx = await _tx()
    try:
        await _world(conn)
        exp = await AA.experience(conn, "KAREN")
        by = {m["key"]: m for m in exp["metrics"]}
        assert by["challenges_raised"]["value"] == await conn.fetchval(
            "SELECT count(*) FROM karen_challenges")
        assert exp["events"] == await conn.fetchval(
            "SELECT count(*) FROM karen_challenges WHERE state IN "
            " ('UPHELD','REJECTED','WITHDRAWN')")
        for a in I.AGENTS:
            e = await AA.experience(conn, a)
            for m in e["metrics"]:
                assert m["status"] in (AA.OK, AA.ABSENT, AA.UNAVAILABLE)
                if m["status"] != AA.OK:
                    assert m["value"] is None and m["why"]
                assert m["sql"] and m["source"] and m["definition"]
        # a missing table is ABSENT with its name, the value null -- not 0
        real = AA._exists

        async def no_scout(c, t):
            return False if t.startswith("scout_") else await real(c, t)
        monkeypatch.setattr(AA, "_exists", no_scout)
        e = await AA.experience(conn, "SCOUT")
        assert e["events"] is None and e["events_status"] == AA.ABSENT
        for m in e["metrics"]:
            assert m["value"] is None and m["status"] == AA.ABSENT
            assert m["why"].startswith("TABLE_NOT_DEPLOYED:scout_")
        ev = await AA.evaluation(conn, "SCOUT")
        cal = next(d for d in ev["matrix"] if d["dimension"] == "calibration")
        assert cal["value"] is None and cal["status"] == AA.ABSENT
        assert ev["single_score"] is None
        assert {d["dimension"] for d in ev["matrix"]} == set(AA.DIMENSIONS)
    finally:
        await _done(conn, tx)


@pg
async def test_the_identity_api_answers_for_every_agent_with_unavailable_not_zero(
        monkeypatch):
    from sportsassets.api import agents_identity as API
    conn, tx = await _tx()
    try:
        w = await _world(conn)
        await M.learn_once(conn, now=time.time() + 10, cursors=await _since(conn))
        for a in I.AGENTS:
            got = await API.build_identity(conn, a)
            assert got["schema"] == "bettor.agent.identity.v1"
            for k in ("agent", "identity", "voice", "state", "memory",
                      "experience", "relationships", "provenance",
                      "computed_at"):
                assert k in got, (a, k)
            assert got["identity"]["agent_id"] == a
            assert got["identity"]["source"] == "agent_identity_versions"
            assert got["provenance"]["approved_by"] == "PENDING_OWNER_APPROVAL"
            assert got["provenance"]["approved_at"] is None
            assert got["voice"]["voice_profile_id"] == \
                I.VOICE_SPEC[a]["voice_profile_id"]
            assert got["authority"]["grants_financial_authority"] is False
            assert isinstance(got["memory"]["count"], int)
            assert got["experience"]["events"] is None or isinstance(
                got["experience"]["events"], int)
        k = await API.build_identity(conn, "karen")
        assert any(r["counterpart"] == "DEREK" for r in k["relationships"])
        x = await API.build_identity(conn, "xavier")
        assert x["memory"]["count"] >= 3
        assert x["context_bundle"]["requires"] == [
            "current_review_identity", "filled_only_protection"]
        # BEFORE MIGRATION 224: the code identity, labelled; memory null
        async def no_schema(c):
            return False
        monkeypatch.setattr(I, "has_schema", no_schema)
        monkeypatch.setattr(M, "has_schema", no_schema)
        got = await API.build_identity(conn, "allocator")
        assert got["identity"]["source"] == "CODE_SPEC_NOT_RECORDED"
        assert got["memory"]["count"] is None
        assert got["memory"]["status"] == AA.UNAVAILABLE
        assert got["memory"]["why"] == M.R_NO_SCHEMA
        assert w["group"]                      # the world was there
    finally:
        await _done(conn, tx)
