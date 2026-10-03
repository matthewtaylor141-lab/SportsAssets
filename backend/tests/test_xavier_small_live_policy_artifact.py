"""XAVIER_SMALL_LIVE_MANAGEMENT_V1: A VERSIONED ARTIFACT, READY FOR OWNER
APPROVAL, NEVER SILENTLY APPROVED OR ACTIVATED (migration 201).

  * the content hash is deterministic and pinned; migration 201 stores the
    exact canonical text this module hashes;
  * the artifact grants no limit or authority;
  * on Postgres (one rolled-back transaction): 201 is idempotent; status
    cannot become APPROVED without an owner approval record (actor,
    approved_at), nor with an agent as actor; content is immutable; the
    rollback refuses over an approval record and otherwise drops cleanly;
  * the runtime reports READY_FOR_OWNER_APPROVAL, approved False, activated
    False -- and Xavier's own policy load is unchanged by the artifact;
  * the agents index says CODE_DEFAULT is not an approved policy.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import asyncpg
import pytest

from sportsassets.agents import xavier_small_live_policy as XSP
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "migrations"
UP = (MIG / "201_xavier_policy_artifact.sql").read_text()
DOWN = (MIG / "rollback" / "201_xavier_policy_artifact.down.sql").read_text()

#: PINNED. Changing the document is a NEW VERSION (a new id/version and a
#: new migration), never an edit of V1: an applied migration is immutable
#: and the owner approves a specific text.
PINNED_SHA256 = (
    "c59f3957e01691787c3ebccfbb67b5b7786fcc76929f0ffcc52ba2a5e416375e")


# ════════════════════════════════════════════════════════════════════
# PURE
# ════════════════════════════════════════════════════════════════════

def test_the_hash_is_deterministic_and_pinned():
    assert XSP.POLICY_ID == "XAVIER_SMALL_LIVE_MANAGEMENT_V1"
    assert XSP.VERSION == "1"
    assert XSP.SHA256 == PINNED_SHA256
    for _ in range(3):
        assert XSP.sha256_of(XSP.document()) == PINNED_SHA256
    text = XSP.canonical_json(XSP.DOCUMENT)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == PINNED_SHA256
    # key order of the input never moves the hash
    rev = json.loads(text, object_pairs_hook=lambda kv: dict(reversed(kv)))
    assert list(rev) != list(json.loads(text))
    assert XSP.sha256_of(rev) == PINNED_SHA256
    # any change to the content does
    other = XSP.document()
    other["rules"][0]["statement"] += " "
    assert XSP.sha256_of(other) != PINNED_SHA256
    assert XSP.document() is not XSP.DOCUMENT       # a copy, never the source


def test_migration_201_stores_exactly_the_hashed_text():
    assert XSP.canonical_json(XSP.DOCUMENT) in UP
    assert UP.count(PINNED_SHA256) >= 2
    assert "'READY_FOR_OWNER_APPROVAL', 'migration 201'" in UP
    assert "APPROVED', 'migration" not in UP


def test_the_artifact_describes_behaviour_and_grants_nothing():
    a = XSP.artifact()
    assert a["status"] == XSP.STATUS_READY
    assert XSP.forbidden_keys(a["document"]) == []
    d = a["document"]
    assert d["authority_granted"] == "NONE"
    assert d["describes"] == "BEHAVIOUR_ALREADY_IMPLEMENTED_AND_TESTED"
    ids = [r["id"] for r in d["rules"]]
    assert len(ids) == len(set(ids)) == 8
    for r in d["rules"]:
        assert r["statement"] and r["implemented_by"] and r["tested_by"]
    assert set(d["evidence_states"]) == {
        "FRESH_CURRENT_PROBABILITY", "STALE_ENTRY_TIME_PROBABILITY",
        "PROBABILITY_UNAVAILABLE"}
    # every key the table refuses is refused here too
    for k in XSP.FORBIDDEN_TOP_LEVEL_KEYS:
        assert "'%s'" % k in UP, k


def test_every_named_test_exists():
    """The artifact cites tests; each cited test function is real."""
    for r in XSP.DOCUMENT["rules"]:
        for ref in r["tested_by"]:
            path, name = ref.split("::")
            src = (ROOT / path).read_text()
            assert ("def %s(" % name) in src, ref


def test_the_evidence_state_names_are_the_codes():
    from sportsassets import execmirror_view as V
    from sportsassets.agents import paper_xavier as PX
    assert set(XSP.DOCUMENT["evidence_states"]) == {
        PX.E_FRESH, PX.E_STALE, PX.E_NONE}
    assert set(V.PROBABILITY_EVIDENCE_STATES) == set(
        XSP.DOCUMENT["evidence_states"])


def test_the_code_view_is_ready_and_never_approved_or_activated():
    v = XSP.code_view(why="x")
    assert v["status"] == "READY_FOR_OWNER_APPROVAL"
    assert v["approved"] is False and v["activated"] is False
    assert v["governs_runtime"] is False
    assert v["sha256"] == PINNED_SHA256
    assert {"policy_id", "version", "sha256", "status"} <= set(v)


def test_no_code_path_writes_the_artifact_table():
    """Only migration 201 inserts; nothing in the application updates or
    approves an artifact."""
    pkg = ROOT / "sportsassets"
    for f in pkg.rglob("*.py"):
        s = f.read_text(errors="ignore")
        if "agent_policy_artifacts" not in s:
            continue
        low = s.lower()
        assert "insert into agent_policy_artifacts" not in low, f
        assert "update agent_policy_artifacts" not in low, f
        assert "delete from agent_policy_artifacts" not in low, f


class _NoTableConn:
    async def fetchval(self, *a, **k):
        return None


@pytest.mark.asyncio
async def test_the_agents_index_says_code_default_is_not_approved():
    from sportsassets.api import agents_core as AC
    agents = [{"agent_id": "DEREK", "policy_version": "CODE_DEFAULT"},
              {"agent_id": "XAVIER", "policy_version": "CODE_DEFAULT"},
              {"agent_id": "AUDREY", "policy_version": "audit@v2"}]
    await AC._annotate_policy(_NoTableConn(), agents)
    d, x, a = agents
    assert d["policy_version"] == "CODE_DEFAULT"          # unchanged
    assert d["policy_version_approved"] is False
    assert "NOT a management-approved" in d["policy_version_meaning"]
    assert x["policy_version_approved"] is False
    mp = x["management_policy"]
    assert mp["policy_id"] == "XAVIER_SMALL_LIVE_MANAGEMENT_V1"
    assert mp["version"] == "1" and mp["sha256"] == PINNED_SHA256
    assert mp["status"] == "READY_FOR_OWNER_APPROVAL"
    assert mp["activated"] is False and mp["approved"] is False
    assert "management_policy" not in d and "management_policy" not in a
    assert a["policy_version_approved"] is True


def test_the_read_route_requires_the_command_credential():
    from fastapi.testclient import TestClient
    from sportsassets.api import app as A
    c = TestClient(A.app)
    assert c.get("/api/command/agents/xavier/management-policy"
                 ).status_code == 401


# ════════════════════════════════════════════════════════════════════
# POSTGRES (one transaction, rolled back)
# ════════════════════════════════════════════════════════════════════

async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


SET = ("UPDATE agent_policy_artifacts SET status=$1, "
       " owner_approval_actor=$2, owner_approved_at=$3 "
       " WHERE policy_id='XAVIER_SMALL_LIVE_MANAGEMENT_V1' AND version='1'")


def _ins(doc: dict, *, sha: str | None = None, pid="T_ART", status="DRAFT"):
    text = XSP.canonical_json(doc)
    return ("INSERT INTO agent_policy_artifacts (policy_id, version, "
            " agent_id, title, canonical_json, document, sha256, status, "
            " created_by) VALUES ($1,'1','XAVIER','t',$2::text,$2::text::jsonb,$3,$4,'t')",
            pid, text, sha or hashlib.sha256(text.encode()).hexdigest(),
            status)


@pg
@pytest.mark.asyncio
async def test_201_is_idempotent_and_approval_needs_an_owner_record():
    import datetime as dt
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        from sportsassets.agents import xavier_policy as XP
        before = await XP.load(conn)
        await conn.execute(UP)
        await conn.execute(UP)                                   # idempotent
        rows = await conn.fetch(
            "SELECT * FROM agent_policy_artifacts "
            " WHERE policy_id='XAVIER_SMALL_LIVE_MANAGEMENT_V1'")
        assert len(rows) == 1
        r = rows[0]
        assert r["status"] == "READY_FOR_OWNER_APPROVAL"
        assert r["sha256"] == PINNED_SHA256
        assert r["canonical_json"] == XSP.canonical_json(XSP.DOCUMENT)
        assert r["owner_approval_actor"] is None
        assert r["owner_approved_at"] is None

        # THE RUNTIME: READY, NOT APPROVED, NOT ACTIVATED -- and Xavier's own
        # policy load is exactly what it was without the artifact.
        v = await XSP.load_view(conn)
        assert v["status"] == "READY_FOR_OWNER_APPROVAL"
        assert v["source"] == XSP.SRC_STORED
        assert v["integrity"] == XSP.INTEGRITY_OK
        assert v["approved"] is False and v["activated"] is False
        assert v["owner_approval"] is None
        after = await XP.load(conn)
        assert after == before
        full = await XSP.read_artifact(conn)
        assert full["document"] == XSP.DOCUMENT
        assert full["status"] == "READY_FOR_OWNER_APPROVAL"

        now = dt.datetime.now(dt.timezone.utc)
        CV = asyncpg.CheckViolationError
        # APPROVED WITHOUT THE OWNER RECORD: refused, every way
        await _expect(conn, CV, SET, "APPROVED", None, None)
        await _expect(conn, CV, SET, "APPROVED", "owner@example", None)
        await _expect(conn, CV, SET, "APPROVED", None, now)
        await _expect(conn, CV, SET, "APPROVED", "   ", now)
        # NEVER AN AGENT
        for agent in ("XAVIER", "audrey", " Derek ", "AGENT:X"):
            await _expect(conn, CV, SET, "APPROVED", agent, now)
        # an approval record on a row that is not approved
        await _expect(conn, CV, SET, "READY_FOR_OWNER_APPROVAL",
                      "owner@example", now)
        # content is immutable; nothing is deleted; status moves forward only
        await _expect(conn, asyncpg.RaiseError,
                      "UPDATE agent_policy_artifacts SET title='x'")
        await _expect(conn, asyncpg.RaiseError,
                      "UPDATE agent_policy_artifacts SET canonical_json="
                      "canonical_json || ' '")
        await _expect(conn, asyncpg.RaiseError,
                      "DELETE FROM agent_policy_artifacts")
        await _expect(conn, asyncpg.RaiseError,
                      "UPDATE agent_policy_artifacts SET status='DRAFT'")
        # a wrong hash, a forbidden key, a mismatched document: refused
        await _expect(conn, CV, *_ins({"policy_id": "T_ART", "version": "1"},
                                      sha="0" * 64))
        await _expect(conn, CV, *_ins({"policy_id": "T_ART", "version": "1",
                                       "risk_limits": {"x": 1}}))
        await _expect(conn, CV, *_ins({"policy_id": "OTHER", "version": "1"}))
        await _expect(conn, CV, *_ins({"policy_id": "T_ART", "version": "1"},
                                      status="ACTIVE"))

        # WITH THE OWNER RECORD it can be approved (savepoint, rolled back):
        # and even then the runtime only REPORTS it -- activated stays False.
        sp = conn.transaction()
        await sp.start()
        await conn.execute(SET, "APPROVED", "owner@example", now)
        v = await XSP.load_view(conn)
        assert v["status"] == "APPROVED" and v["approved"] is True
        assert v["activated"] is False and v["governs_runtime"] is False
        assert v["owner_approval"]["actor"] == "owner@example"
        assert await XP.load(conn) == before
        # the approval record is write-once
        await _expect(conn, asyncpg.RaiseError,
                      "UPDATE agent_policy_artifacts SET "
                      "owner_approval_actor='someone@else'")
        # THE ROLLBACK REFUSES OVER AN APPROVAL RECORD
        await _expect(conn, asyncpg.RaiseError, DOWN)
        await sp.rollback()

        # rollback with no approval record drops cleanly; 201 re-applies
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('agent_policy_artifacts')") is None
        v = await XSP.load_view(conn)
        assert v["status"] == "READY_FOR_OWNER_APPROVAL"
        assert v["source"] == XSP.SRC_CODE and v["approved"] is False
        await conn.execute(DOWN)                         # idempotent too
        await conn.execute(UP)
        assert (await XSP.load_view(conn))["integrity"] == XSP.INTEGRITY_OK
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_agents_index_carries_the_stored_artifact():
    from sportsassets.api import agents_core as AC
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        agents = [{"agent_id": "XAVIER", "policy_version": "CODE_DEFAULT"}]
        await AC._annotate_policy(conn, agents)
        mp = agents[0]["management_policy"]
        assert mp["source"] == XSP.SRC_STORED
        assert mp["status"] == "READY_FOR_OWNER_APPROVAL"
        assert mp["sha256"] == PINNED_SHA256 and mp["activated"] is False
    finally:
        await tx.rollback()
        await conn.close()
