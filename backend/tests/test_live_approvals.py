"""CAPITAL-CRITICAL (R30A sections 23-24, audit P0 #8/#9): LIVE GATES AND
THE LIVE POLICY ARE ADMITTED ONLY BY A VERSIONED, HASH-MATCHED OWNER
APPROVAL, AND NOTHING IN THE APPLICATION CREATES ONE.

  §1  the gate configuration: every enforced constant is in it (read from
      the module that applies it), its sha is deterministic, and changing
      ANY enforced constant changes the sha
  §2  the pure rules: a gate is admitted only by a named human's APPROVE of
      exactly the code's version and config sha; REVOKE, another version,
      another sha, a robot approver, a blank statement or another kind admit
      nothing; a policy sha only by its own APPROVE
  §3  the database (migration 225, one rolled-back transaction): append-only,
      named humans only, recorded_at is the database clock, the latest
      decision wins, a REVOKE withdraws, a changed constant invalidates the
      stored approval, a failed read admits nothing and never poisons the
      caller's transaction
  §4  the gates stay: the settlement gate refuses COMPATIBLE facts without
      its approval; the book gate needs BOTH its 204 rule document and its
      configuration approved
  §5  nothing in sportsassets writes live_approvals; production approves no
      gate and no policy by code constant
"""
from __future__ import annotations

import pathlib

import asyncpg
import pytest

from sportsassets import actual_admission as AA
from sportsassets import canonical_intent as CI
from sportsassets import live_approvals as LAP
from sportsassets import live_book_currency as LBC
from sportsassets import live_parity as LP
from sportsassets import live_rule_artifacts as LRA

from tests import admission_fixture as AF
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(LAP.__file__).resolve().parent
MIG = ROOT.parent / "migrations"


def _row(**over):
    g = LAP.GATE_BOOK
    r = {"subject_kind": LAP.KIND_GATE, "subject_id": g,
         "subject_version": LAP.gate_version(g),
         "config_sha256": LAP.config_sha256(g), "decision": "APPROVE",
         "approved_by": "owner@example", "statement": "I approve this config"}
    r.update(over)
    return r


# ═════════════════════════════════════════════════════════════════════
# §1 THE GATE CONFIGURATION
# ═════════════════════════════════════════════════════════════════════

def test_each_gate_config_names_what_it_enforces():
    book = LAP.gate_config(LAP.GATE_BOOK)
    assert book["version"] == LBC.VERSION
    assert book["rule_document_sha256"] == LBC.SHA256
    assert book["max_receipt_age_s"] == LBC.MAX_RECEIPT_AGE_S
    assert book["max_venue_receipt_skew_s"] == LBC.MAX_VENUE_RECEIPT_SKEW_S
    assert book["max_verdict_age_at_submit_s"] == \
        LBC.MAX_VERDICT_AGE_AT_SUBMIT_S
    assert book["admission_version"] == AA.VERSION
    settle = LAP.gate_config(LAP.GATE_SETTLEMENT)
    assert settle["version"] == AA.SETTLEMENT_GATE_VERSION
    assert settle["rule"] == AA.SETTLEMENT_GATE_RULE
    assert settle["rule"]["compatibility_required"] == "COMPATIBLE"
    assert settle["rule"]["research_disclosure_counts"] is False
    with pytest.raises(KeyError):
        LAP.gate_config("SOME_OTHER_GATE")


def test_the_config_sha_is_deterministic_and_hex():
    for g in LAP.GATES:
        a, b = LAP.config_sha256(g), LAP.config_sha256(g)
        assert a == b and len(a) == 64 and int(a, 16) >= 0
    assert LAP.config_sha256(LAP.GATE_BOOK) != LAP.config_sha256(
        LAP.GATE_SETTLEMENT)
    code = LAP.code_gates()
    assert set(code) == set(LAP.GATES)


@pytest.mark.parametrize("mod,name,value", [
    (LBC, "MAX_RECEIPT_AGE_S", lambda v: v + 1.0),
    (LBC, "MAX_VENUE_RECEIPT_SKEW_S", lambda v: v + 1.0),
    (LBC, "MAX_VERDICT_AGE_AT_SUBMIT_S", lambda v: v + 1.0),
    (LBC, "RECEIPT_FUTURE_TOLERANCE_S", lambda v: v + 1.0),
    (LBC, "SHA256", lambda v: "f" * 64),
    (LBC, "VERSION", lambda v: "2"),
    (AA, "TRADABLE_MARKET_STATES", lambda v: frozenset(v) | {"X"}),
    (AA, "VERSION", lambda v: v + "_CHANGED"),
])
def test_changing_any_enforced_book_constant_changes_the_book_sha(
        monkeypatch, mod, name, value):
    before = LAP.config_sha256(LAP.GATE_BOOK)
    monkeypatch.setattr(mod, name, value(getattr(mod, name)))
    assert LAP.config_sha256(LAP.GATE_BOOK) != before


def test_changing_the_settlement_rule_changes_the_settlement_sha(monkeypatch):
    before = LAP.config_sha256(LAP.GATE_SETTLEMENT)
    monkeypatch.setattr(AA, "SETTLEMENT_GATE_RULE",
                        dict(AA.SETTLEMENT_GATE_RULE, blockers_allowed=1))
    assert LAP.config_sha256(LAP.GATE_SETTLEMENT) != before
    monkeypatch.undo()
    monkeypatch.setattr(AA, "SETTLEMENT_GATE_VERSION", "2")
    assert LAP.config_sha256(LAP.GATE_SETTLEMENT) != before


# ═════════════════════════════════════════════════════════════════════
# §2 THE PURE RULES
# ═════════════════════════════════════════════════════════════════════

def test_a_gate_is_admitted_only_by_an_exact_named_human_approve():
    assert LAP.admissible_gates([_row()]) == {LAP.GATE_BOOK}
    both = [_row(), _row(subject_id=LAP.GATE_SETTLEMENT,
                         subject_version=LAP.gate_version(LAP.GATE_SETTLEMENT),
                         config_sha256=LAP.config_sha256(LAP.GATE_SETTLEMENT))]
    assert LAP.admissible_gates(both) == set(LAP.GATES)
    for bad in (_row(decision="REVOKE"), _row(subject_version="2"),
                _row(config_sha256="0" * 64),
                _row(config_sha256=LAP.config_sha256(LAP.GATE_SETTLEMENT)),
                _row(subject_id="SOME_OTHER_GATE"),
                _row(subject_kind=LAP.KIND_POLICY),
                _row(approved_by="system"), _row(approved_by="Xavier"),
                _row(approved_by="claude"), _row(approved_by="  "),
                _row(approved_by=None), _row(statement="   "),
                {"garbage": True}, None):
        assert LAP.admissible_gates([bad]) == frozenset(), bad


def test_a_policy_sha_is_approved_only_by_its_own_approve():
    sha = "ab" * 32
    ok = _row(subject_kind=LAP.KIND_POLICY, subject_id="S",
              subject_version="V|P", config_sha256=sha)
    assert LAP.admissible_policy_shas([ok]) == {sha}
    for bad in (dict(ok, decision="REVOKE"), dict(ok, approved_by="agent:x"),
                dict(ok, statement=""), dict(ok, subject_kind=LAP.KIND_GATE)):
        assert LAP.admissible_policy_shas([bad]) == frozenset(), bad


def test_the_named_human_rule_matches_the_database_check():
    for robot in ("system", "SYSTEM", "derek", "Xavier:x", "audrey", "karen",
                  "allie", "chief_allocator", "eddie", "scout", "bettor",
                  "claude code", "agent:release", "migration 225",
                  "test_harness_system", "", "   ", None):
        assert CI.is_named_human(robot) is False, robot
    for human in ("owner@example", "release engineer", "M. Owner"):
        assert CI.is_named_human(human) is True, human
    sql = (MIG / "225_live_parity.sql").read_text()
    assert CI.NON_HUMAN_ACTOR_PATTERN.lstrip("^") in sql.replace("'^", "'")


# ═════════════════════════════════════════════════════════════════════
# §3 THE DATABASE
# ═════════════════════════════════════════════════════════════════════

INS = ("INSERT INTO live_approvals (subject_kind, subject_id, subject_version,"
       " config_sha256, decision, approved_by, statement)"
       " VALUES ($1,$2,$3,$4,$5,$6,$7)")


def _args(r):
    return (r["subject_kind"], r["subject_id"], r["subject_version"],
            r["config_sha256"], r["decision"], r["approved_by"],
            r["statement"])


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


@pg
@pytest.mark.asyncio
async def test_the_table_is_append_only_named_human_and_db_stamped(
        monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        assert await LAP.approved_gates(conn) == frozenset()
        CV = asyncpg.CheckViolationError
        for robot in ("system", "Xavier", "claude", "agent:x", "migration 1",
                      "  "):
            await _expect(conn, (CV, asyncpg.RaiseError,
                                 asyncpg.RestrictViolationError), INS,
                          *_args(_row(approved_by=robot)))
        await _expect(conn, CV, INS, *_args(_row(config_sha256="xyz")))
        await _expect(conn, CV, INS, *_args(_row(decision="MAYBE")))
        await _expect(conn, CV, INS, *_args(_row(statement=" ")))
        await _expect(conn, CV, INS, *_args(_row(subject_kind="OTHER")))
        # a back-dated recorded_at is replaced by the database clock
        await conn.execute(
            INS.replace("statement)", "statement, recorded_at)").replace(
                "$7)", "$7, '2000-01-01')"), *_args(_row()))
        at = await conn.fetchval(
            "SELECT recorded_at FROM live_approvals ORDER BY approval_id DESC "
            " LIMIT 1")
        assert at.year >= 2026
        # the latest decision wins; the code's config sha is matched
        assert await LAP.approved_gates(conn) == {LAP.GATE_BOOK}
        # an ENFORCED CONSTANT CHANGES: the stored approval stops admitting
        monkeypatch.setattr(LBC, "MAX_RECEIPT_AGE_S",
                            LBC.MAX_RECEIPT_AGE_S + 1.0)
        assert await LAP.approved_gates(conn) == frozenset()
        monkeypatch.undo()
        assert await LAP.approved_gates(conn) == {LAP.GATE_BOOK}
        # append-only: never updated, deleted or truncated
        R = (asyncpg.RaiseError, asyncpg.RestrictViolationError)
        await _expect(conn, R, "UPDATE live_approvals SET decision='REVOKE'")
        await _expect(conn, R, "DELETE FROM live_approvals")
        await _expect(conn, R, "TRUNCATE live_approvals")
        # a REVOKE as the latest decision withdraws
        await conn.execute(INS, *_args(_row(decision="REVOKE",
                                            statement="withdrawn")))
        assert await LAP.approved_gates(conn) == frozenset()
        # a policy approval names a policy sha
        sha = "cd" * 32
        await conn.execute(INS, *_args(_row(
            subject_kind=LAP.KIND_POLICY, subject_id="S",
            subject_version="V|P", config_sha256=sha)))
        assert await LAP.approved_policy_shas(conn) == {sha}
        d = await LAP.describe(conn)
        assert d["approved_policy_shas"] == [sha]
        assert {g["gate"]: g["approved"] for g in d["gates"]} == {
            LAP.GATE_BOOK: False, LAP.GATE_SETTLEMENT: False}
        assert d["writes"].startswith("NONE")
        # a failing read INSIDE the transaction admits nothing and never
        # poisons it
        async def broken(conn_):
            raise RuntimeError("down")
        monkeypatch.setattr(LAP, "_current_rows", broken)
        assert await LAP.approved_gates(conn) == frozenset()
        assert await LAP.approved_policy_shas(conn) == frozenset()
        monkeypatch.undo()
        sp = conn.transaction()
        await sp.start()
        await conn.execute("ALTER VIEW live_approvals_current RENAME TO "
                           "live_approvals_current_gone")
        assert await LAP.approved_policy_shas(conn) == frozenset()
        await sp.rollback()
        assert await conn.fetchval("SELECT 1") == 1
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 THE GATES STAY
# ═════════════════════════════════════════════════════════════════════

def test_the_settlement_gate_refuses_compatible_facts_without_its_approval():
    st = {"compatibility": "COMPATIBLE", "overall_established": True,
          "blockers": [], "lane_refusals": []}
    assert AA.APPROVED_SETTLEMENT_GATES == frozenset()
    got = AA.settlement_admission(st)
    assert got["status"] == AA.NOT_ADMISSIBLE and got["gate_approved"] is False
    assert got["why"] == "SETTLEMENT_GATE_APPROVAL_ABSENT_OR_STALE"
    ok = AA.settlement_admission(st, approved={AA.SETTLEMENT_GATE_ID})
    assert ok["status"] == AA.LIVE_ADMISSIBLE
    # the gate approval never stands in for the facts
    bad = AA.settlement_admission(dict(st, compatibility="UNKNOWN"),
                                  approved={AA.SETTLEMENT_GATE_ID})
    assert bad["status"] == AA.NOT_ADMISSIBLE


@pg
@pytest.mark.asyncio
async def test_the_book_gate_needs_its_document_and_its_configuration():
    from tests.test_live_rule_artifacts import APPROVE, STATEMENT, UP, _now
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        # the configuration alone
        sp = conn.transaction()
        await sp.start()
        await AF.record_test_gate_approvals(conn, [LAP.GATE_BOOK])
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        await sp.rollback()
        # the document alone
        await conn.execute(APPROVE, "APPROVED", "owner@example", _now(),
                           STATEMENT)
        assert await LRA.approved_live_book_rules(conn) == frozenset()
        # both
        await AF.record_test_gate_approvals(conn)
        assert await LRA.approved_live_book_rules(conn) == {LBC.RULE_ID}
        assert await LAP.approved_settlement_gates(conn) == {
            AA.SETTLEMENT_GATE_ID}
        gin = await LP.governance_in_force(conn)
        assert gin["approved_gates"] == set(LAP.GATES)
        assert gin["approved_policy_shas"] == frozenset()
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 NOTHING CREATES AN APPROVAL
# ═════════════════════════════════════════════════════════════════════

def test_no_code_path_writes_live_approvals():
    for f in ROOT.rglob("*.py"):
        s = f.read_text(errors="ignore").lower()
        if "live_approvals" not in s:
            continue
        for verb in ("insert into live_approvals", "update live_approvals",
                     "delete from live_approvals",
                     "truncate live_approvals"):
            assert verb not in s, (f, verb)


def test_production_approves_nothing_by_code_constant():
    assert AA.APPROVED_SETTLEMENT_GATES == frozenset()
    assert AA.APPROVED_LIVE_BOOK_RULES == frozenset()
    up = (MIG / "225_live_parity.sql").read_text().lower()
    assert "insert into live_approvals" not in up
