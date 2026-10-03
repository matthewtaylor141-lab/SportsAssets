"""CAPITAL-CRITICAL: THE LIVE BOOK RULE IS ADMISSIBLE ONLY BY OWNER APPROVAL
OF ITS EXACT TEXT (migration 204), AND PRODUCTION STAYS CLOSED.

Fake venue only. Every database case runs in a transaction that is rolled
back, so no approval record ever persists.

  §1  migration 204 stores exactly the hashed rule text, READY_FOR_OWNER_
      APPROVAL, never APPROVED
  §2  the table: cannot APPROVE without the owner record; an agent, a system
      actor or the creator is refused as approver; the text, hash and
      identity are immutable; the hash must be the text's; status forward
      only; insert only as DRAFT/READY; approval write-once; never deleted;
      the rollback refuses over an approval record
  §3  the approved set: approved by artifact + matching hash -> admissible;
      hash moved / other version / not approved / table missing / read error
      -> not; a failed read never poisons the caller's transaction
  §4  the lanes: the PRODUCTION DEFAULT (row READY_FOR_OWNER_APPROVAL) still
      refuses ADMISSION_BOOK_CURRENCY_NOT_LIVE_ADMISSIBLE at the intent and in
      the lane, Venue.place ZERO times; an owner-approved matching artifact
      admits the rule (one fake placement); a superseded approval and a stale
      verdict are refused inside the lane before any claim
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import time
import uuid

import asyncpg
import pytest

from sportsassets import actual_admission as AA
from sportsassets import execution_intent as EI
from sportsassets import live_book_currency as LBC
from sportsassets import live_rule_artifacts as LRA

try:
    from tests import admission_fixture as AF
    from tests import paper_harness as H
    from tests.test_actual_admission import _close, _env
except ImportError:                                             # pragma: no cover
    import admission_fixture as AF
    import paper_harness as H
    from test_actual_admission import _close, _env

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "migrations"
UP = (MIG / "204_live_rule_artifacts.sql").read_text()
DOWN = (MIG / "rollback" / "204_live_rule_artifacts.down.sql").read_text()
PINNED_SHA256 = (
    "b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a")
RID = LBC.RULE_ID

APPROVE = ("UPDATE live_rule_artifacts SET status=$1, owner_approval_actor=$2,"
           " owner_approved_at=$3, owner_approval_statement=$4"
           " WHERE rule_id='P5_LIVE_STREAM_BOOK_V1' AND version='1'")
STATEMENT = ("I approve P5_LIVE_STREAM_BOOK_V1 v1 sha256 " + PINNED_SHA256
             + " and accept its not_established residuals.")


def _now():
    return dt.datetime.now(dt.timezone.utc)


# ═════════════════════════════════════════════════════════════════════
# §1 PURE: THE MIGRATION STORES THE HASHED TEXT, NOT APPROVED
# ═════════════════════════════════════════════════════════════════════

def test_migration_204_stores_exactly_the_hashed_rule_text():
    assert LBC.SHA256 == PINNED_SHA256
    assert LBC.canonical_json(LBC.DOCUMENT) in UP
    assert UP.count(PINNED_SHA256) >= 2
    assert "'READY_FOR_OWNER_APPROVAL', 'migration 204'" in UP
    assert "'APPROVED', 'migration" not in UP
    cols = UP.split("INSERT INTO live_rule_artifacts")[-1].split("SELECT")[0]
    assert "owner_approv" not in cols and "status" in cols


def test_no_code_path_writes_the_rule_artifact_table():
    for f in (ROOT / "sportsassets").rglob("*.py"):
        s = f.read_text(errors="ignore").lower()
        if "live_rule_artifacts" not in s:
            continue
        assert "insert into live_rule_artifacts" not in s, f
        assert "update live_rule_artifacts" not in s, f
        assert "delete from live_rule_artifacts" not in s, f


def test_admissible_rows_is_strict():
    ok = {"rule_id": RID, "version": "1", "sha256": PINNED_SHA256,
          "status": "APPROVED", "owner_approval_actor": "owner@example",
          "owner_approved_at": _now(), "owner_approval_statement": "yes",
          "created_by": "migration 204"}
    assert LRA.admissible_rows([ok]) == {RID}
    for bad in (dict(ok, status="READY_FOR_OWNER_APPROVAL"),
                dict(ok, status="SUPERSEDED"),
                dict(ok, sha256="0" * 64), dict(ok, version="2"),
                dict(ok, rule_id="SOME_OTHER_RULE"),
                dict(ok, owner_approval_actor=None),
                dict(ok, owner_approval_actor="XAVIER"),
                dict(ok, owner_approval_actor="agent:derek"),
                dict(ok, owner_approval_actor="migration 204"),
                dict(ok, owner_approved_at=None),
                dict(ok, owner_approval_statement="  "),
                {"garbage": True}, None):
        assert LRA.admissible_rows([bad]) == frozenset(), bad


class _RaisingConn:
    def is_in_transaction(self):
        return False

    async def fetchval(self, *a, **k):
        raise RuntimeError("db down")


class _NoTableConn:
    def is_in_transaction(self):
        return False

    async def fetchval(self, *a, **k):
        return None


@pytest.mark.asyncio
async def test_any_read_failure_is_the_empty_code_constant():
    assert await LRA.approved_live_book_rules(_RaisingConn()) == frozenset()
    assert await LRA.approved_live_book_rules(_NoTableConn()) == frozenset()
    assert await LRA.approved_live_book_rules(object()) == frozenset()


# ═════════════════════════════════════════════════════════════════════
# §2 THE TABLE (one transaction, rolled back)
# ═════════════════════════════════════════════════════════════════════

async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


def _ins(doc: dict, *, sha=None, rid="T_RULE", status="DRAFT", by="t"):
    text = LBC.canonical_json(doc)
    return ("INSERT INTO live_rule_artifacts (rule_id, version, title, "
            " canonical_json, document, sha256, status, created_by) "
            "VALUES ($1,'1','t',$2::text,$2::text::jsonb,$3,$4,$5)",
            rid, text, sha or hashlib.sha256(text.encode()).hexdigest(),
            status, by)


@pg
@pytest.mark.asyncio
async def test_204_is_idempotent_and_approval_needs_a_non_agent_owner_record():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                                   # idempotent
        rows = await conn.fetch(
            "SELECT * FROM live_rule_artifacts WHERE rule_id=$1", RID)
        assert len(rows) == 1
        r = rows[0]
        assert r["status"] == "READY_FOR_OWNER_APPROVAL"
        assert r["version"] == "1" and r["sha256"] == PINNED_SHA256
        assert r["canonical_json"] == LBC.canonical_json(LBC.DOCUMENT)
        assert r["owner_approval_actor"] is None
        assert r["owner_approved_at"] is None
        assert r["owner_approval_statement"] is None
        # the production default admits nothing
        assert await LRA.approved_live_book_rules(conn) == frozenset()

        now = _now()
        CV = asyncpg.CheckViolationError
        # APPROVED WITHOUT THE COMPLETE OWNER RECORD: refused, every way
        await _expect(conn, CV, APPROVE, "APPROVED", None, None, None)
        await _expect(conn, CV, APPROVE, "APPROVED", "owner@example", None,
                      STATEMENT)
        await _expect(conn, CV, APPROVE, "APPROVED", None, now, STATEMENT)
        await _expect(conn, CV, APPROVE, "APPROVED", "owner@example", now,
                      None)
        await _expect(conn, CV, APPROVE, "APPROVED", "owner@example", now,
                      "   ")
        await _expect(conn, CV, APPROVE, "APPROVED", "   ", now, STATEMENT)
        # NEVER AN AGENT, A SYSTEM ACTOR OR THE CREATOR
        for agent in ("XAVIER", "audrey", " Derek ", "AGENT:X", "claude",
                      "Claude Code", "system", "migration 204",
                      "MIGRATION 999"):
            await _expect(conn, CV, APPROVE, "APPROVED", agent, now, STATEMENT)
        # an approval record on a row that is not approved
        await _expect(conn, CV, APPROVE, "READY_FOR_OWNER_APPROVAL",
                      "owner@example", now, STATEMENT)
        # TEXT IMMUTABLE; nothing deleted; status forward only
        R = asyncpg.RaiseError
        await _expect(conn, R, "UPDATE live_rule_artifacts SET title='x'")
        await _expect(conn, R, "UPDATE live_rule_artifacts SET "
                               "canonical_json=canonical_json || ' '")
        await _expect(conn, R, "UPDATE live_rule_artifacts SET "
                               "document=document || '{\"x\":1}'::jsonb")
        await _expect(conn, R, "UPDATE live_rule_artifacts SET version='2'")
        await _expect(conn, R, "DELETE FROM live_rule_artifacts")
        await _expect(conn, R, "UPDATE live_rule_artifacts SET status='DRAFT'")
        # THE HASH MUST BE THE TEXT'S; forbidden keys; mismatched document
        doc = {"rule_id": "T_RULE", "version": "1"}
        await _expect(conn, CV, *_ins(doc, sha="0" * 64))
        await _expect(conn, CV, *_ins(doc, sha=PINNED_SHA256))
        await _expect(conn, CV, *_ins(dict(doc, risk_limits={"x": 1})))
        await _expect(conn, CV, *_ins(dict(doc, approved=True)))
        await _expect(conn, CV, *_ins(dict(doc, status="APPROVED")))
        await _expect(conn, CV, *_ins({"rule_id": "OTHER", "version": "1"}))
        await _expect(conn, (CV, R), *_ins(doc, status="ACTIVE"))
        # NEVER INSERTED APPROVED (an approval is a later act on stored text)
        await _expect(conn, R, *_ins(doc, status="APPROVED"))

        # WITH THE OWNER RECORD it can be approved (savepoint, rolled back)
        sp = conn.transaction()
        await sp.start()
        await conn.execute(APPROVE, "APPROVED", "owner@example", now,
                           STATEMENT)
        assert await LRA.approved_live_book_rules(conn) == {RID}
        # the approval record is write-once; the text still immutable
        await _expect(conn, R, "UPDATE live_rule_artifacts SET "
                               "owner_approval_actor='someone@else'")
        await _expect(conn, R, "UPDATE live_rule_artifacts SET "
                               "owner_approval_statement='changed'")
        await _expect(conn, R, "UPDATE live_rule_artifacts SET "
                               "canonical_json=canonical_json || ' '")
        await _expect(conn, R, "UPDATE live_rule_artifacts SET "
                               "status='READY_FOR_OWNER_APPROVAL'")
        # THE ROLLBACK REFUSES OVER AN APPROVAL RECORD
        await _expect(conn, R, DOWN)
        # APPROVED -> SUPERSEDED is allowed and admits nothing
        await conn.execute("UPDATE live_rule_artifacts SET status='SUPERSEDED'")
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        await sp.rollback()

        # rollback with no approval record drops cleanly; 204 re-applies
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('live_rule_artifacts')") is None
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        await conn.execute(DOWN)                             # idempotent too
        await conn.execute(UP)
        assert await conn.fetchval(
            "SELECT status FROM live_rule_artifacts WHERE rule_id=$1",
            RID) == "READY_FOR_OWNER_APPROVAL"
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §3 THE APPROVED SET
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_approved_by_artifact_with_matching_hash_is_the_only_way_in(
        monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        await conn.execute(APPROVE, "APPROVED", "owner@example", _now(),
                           STATEMENT)
        assert await LRA.approved_live_book_rules(conn) == {RID}
        # THE CODE'S RULE TEXT MOVED (a new hash) -> the old approval admits
        # nothing
        monkeypatch.setattr(LBC, "CODE_RULES", {RID: {"1": "f" * 64}})
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        monkeypatch.setattr(LBC, "CODE_RULES", {RID: {"2": PINNED_SHA256}})
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        monkeypatch.undo()
        assert await LRA.approved_live_book_rules(conn) == {RID}
        # the code constant still unions in (and stays empty in production)
        monkeypatch.setattr(AA, "APPROVED_LIVE_BOOK_RULES",
                            frozenset({"X_TEST"}))
        assert await LRA.approved_live_book_rules(conn) == {RID, "X_TEST"}
        monkeypatch.undo()
        # a failing read INSIDE a transaction never poisons it
        monkeypatch.setattr(LRA, "_SQL", "SELECT no_such_column FROM "
                                         "live_rule_artifacts")
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        assert await conn.fetchval("SELECT 1") == 1
        monkeypatch.undo()
        # the table missing
        sp = conn.transaction()
        await sp.start()
        await conn.execute("ALTER TABLE live_rule_artifacts RENAME TO "
                           "live_rule_artifacts_gone")
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        await sp.rollback()
        d = await LRA.describe(conn)
        assert d["approved_live_book_rules"] == [RID]
        assert d["code_constant"] == []
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 THE LANES
# ═════════════════════════════════════════════════════════════════════

def _live_facts(slug, *, evaluated_at=None, verdict="ESTABLISHED"):
    f = AF.admissible_facts(slug=slug)
    f["book"]["book_currency"] = {
        "verdict": verdict, "rule": RID, "rule_version": "1",
        "rule_sha256": PINNED_SHA256, "subscription_state": "RUNNING",
        "connection_epoch": 1, "receipt_age_s": 0.3, "venue_ts_age_s": 0.4,
        "gap_since_snapshot": False,
        "evaluated_at": time.time() if evaluated_at is None else evaluated_at}
    return f


async def _intent(conn, facts, slug, *, qty=2702, wire=0.55):
    now = time.time()
    did = "dec_lra_%s" % uuid.uuid4().hex[:12]
    from sportsassets.agents import paper_benchmark as PB
    return await EI.create(
        conn, decision_id=did, valuation_id=None, strategy=PB.CG_STRATEGY,
        policy_version=PB.CG_VERSION, slug=slug,
        order_intent="ORDER_INTENT_BUY_LONG", holding_side="LONG",
        group_id="grp_" + did, order_type="MARKETABLE", time_in_force="IOC",
        paper_target_qty=qty, limit_price=wire, wire_price=wire,
        book_obs_id=None, book_observed_at=now - 0.3, decided_at=now - 0.3,
        evidence={"admission_facts": facts}, timeline={})


async def _production_env(monkeypatch):
    """The fake-venue lane with the code constant EMPTY, as in production."""
    e = await _env(monkeypatch)
    monkeypatch.setattr(AA, "APPROVED_LIVE_BOOK_RULES", frozenset())
    return e


@pg
async def test_production_default_still_refuses_and_never_places(monkeypatch):
    e = await _production_env(monkeypatch)
    try:
        assert await e.conn.fetchval(
            "SELECT status FROM live_rule_artifacts WHERE rule_id=$1",
            RID) == "READY_FOR_OWNER_APPROVAL"
        assert await LRA.approved_live_book_rules(e.conn) == frozenset()
        slug = "mlb-lra-%s" % uuid.uuid4().hex[:6]
        it = await _intent(e.conn, _live_facts(slug), slug)
        assert it["live_eligible"] is False
        assert it["actual_state"] == EI.A_REFUSED
        assert it["actual_refusal"] == AA.R_BOOK_CURRENCY \
            == "ADMISSION_BOOK_CURRENCY_NOT_LIVE_ADMISSIBLE"
        lel = it["live_eligibility"]
        lel = json.loads(lel) if isinstance(lel, str) else lel
        assert lel["admission"]["book_currency"]["approved_live_rules"] == []
        assert EI.dispatch(it) is False
        assert (await e.lane._run(e.conn, it["intent_id"]))["state"] == \
            "NOT_DISPATCHED"
        assert e.venue.placed == []                 # Venue.place: ZERO times
        assert await e.conn.fetchval(
            "SELECT count(*) FROM execmirror_orders WHERE execution_intent_id=$1",
            it["intent_id"]) == 0
    finally:
        await _close(e)


@pg
async def test_an_owner_approved_matching_artifact_admits_the_rule(monkeypatch):
    e = await _production_env(monkeypatch)
    tx = e.conn.transaction()
    await tx.start()
    try:
        await e.conn.execute(APPROVE, "APPROVED", "owner@example", _now(),
                             STATEMENT)
        slug = "mlb-lra-%s" % uuid.uuid4().hex[:6]
        it = await _intent(e.conn, _live_facts(slug), slug)
        assert it["live_eligible"] is True, it["live_eligibility"]
        assert it["actual_state"] == EI.A_DISPATCHED
        got = await e.lane._run(e.conn, it["intent_id"])
        assert got["state"] in (EI.A_SUBMITTED, EI.A_UNKNOWN), got
        assert len(e.venue.placed) == 1                 # the FAKE venue only
        # idempotent: a second run finds the claim
        again = await e.lane._run(e.conn, it["intent_id"])
        assert again["state"] == "NOT_DISPATCHED"
        assert len(e.venue.placed) == 1
    finally:
        await tx.rollback()
        await _close(e)


@pg
async def test_a_superseded_approval_or_a_moved_hash_is_refused_in_the_lane(
        monkeypatch):
    e = await _production_env(monkeypatch)
    tx = e.conn.transaction()
    await tx.start()
    try:
        await e.conn.execute(APPROVE, "APPROVED", "owner@example", _now(),
                             STATEMENT)
        slug = "mlb-lra-%s" % uuid.uuid4().hex[:6]
        a = await _intent(e.conn, _live_facts(slug), slug)
        b = await _intent(e.conn, _live_facts(slug), slug)
        assert a["actual_state"] == b["actual_state"] == EI.A_DISPATCHED
        # the code's rule hash moved after the intent was written
        monkeypatch.setattr(LBC, "CODE_RULES", {RID: {"1": "e" * 64}})
        got = await e.lane._run(e.conn, a["intent_id"])
        assert got["state"] == EI.A_REFUSED
        assert got["refusal"] == AA.R_BOOK_CURRENCY
        monkeypatch.setattr(LBC, "CODE_RULES", {RID: {"1": PINNED_SHA256}})
        # the approval is superseded after the intent was written
        await e.conn.execute(
            "UPDATE live_rule_artifacts SET status='SUPERSEDED' "
            " WHERE rule_id=$1", RID)
        got = await e.lane._run(e.conn, b["intent_id"])
        assert got["state"] == EI.A_REFUSED
        assert got["refusal"] == AA.R_BOOK_CURRENCY
        assert e.venue.placed == []
    finally:
        await tx.rollback()
        await _close(e)


@pg
async def test_a_stale_live_verdict_is_refused_before_any_claim(monkeypatch):
    e = await _production_env(monkeypatch)
    tx = e.conn.transaction()
    await tx.start()
    try:
        await e.conn.execute(APPROVE, "APPROVED", "owner@example", _now(),
                             STATEMENT)
        slug = "mlb-lra-%s" % uuid.uuid4().hex[:6]
        it = await _intent(
            e.conn, _live_facts(slug, evaluated_at=time.time() - 5.0), slug)
        assert it["actual_state"] == EI.A_DISPATCHED
        got = await e.lane._run(e.conn, it["intent_id"])
        assert got["state"] == EI.A_REFUSED
        assert got["refusal"] == LBC.R_VERDICT_STALE
        assert got["limit_s"] == LBC.MAX_VERDICT_AGE_AT_SUBMIT_S
        # a non-ESTABLISHED live verdict is refused at the intent
        it2 = await _intent(e.conn, _live_facts(slug, verdict="STALE"), slug)
        assert it2["actual_refusal"] == AA.R_BOOK_CURRENCY
        assert e.venue.placed == []
    finally:
        await tx.rollback()
        await _close(e)
