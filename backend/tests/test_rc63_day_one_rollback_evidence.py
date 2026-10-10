"""RC6.3 PR #5 PORT -- ROLLBACK EVIDENCE AFTER A PAPER EPOCH EXISTS.

tools/upgrade_path_receipt.compatibility() skips migration 317's seven
BEFORE INSERT guards on existing financial tables as proven dormant when
paper_account_epochs is EMPTY in the database it snapshots -- and the
capital-critical receipt is computed on a fresh scratch database that never
has an epoch. So rollback_ready read COMPATIBLE_BY_UPGRADE_RECEIPT where it
was computed, also after production activated an epoch, when the previous
release (no selector) cannot trade PAPER and never sees the Day One
positions (independent review, integration lens; verified on the real
receipt and scorecard).

THE FIX, PINNED HERE:
- the release readback (GET /api/command/release, acc/release.json in the
  pm-acceptance packet) carries production's read-only `paper_epochs`:
  epochs ever activated, rollbacks, the selected account;
- tools/rollback_readiness.py (when the readback is in ACC) and the judge
  (tools/scorecard_14.rollback_ready, per-service rules untouched): when the
  release ADDS migration 317 over the rollback target, any epoch is
  NOT_READY ROLLBACK_PAPER_EPOCH_ACTIVATED, and an unreadable state is
  NOT_READY ROLLBACK_PAPER_EPOCH_STATE_UNREAD; a release that does not add
  317 is judged exactly as before.
ALL DATA SYNTHETIC; packets are temporary directories; the database test
runs in one rolled-back transaction on a fresh migrated database.
"""
from __future__ import annotations

import json

import pytest

from tests.test_rc6e_deploy_lineage import (
    SC, RB, REL, _packet, _unit, _w, _readback, _receipt)
from tests.test_day_one_paper_epoch import conn, epoch_database  # noqa: F401
from tests.test_day_one_paper_epoch import PROOF
from sportsassets import bettor_paper_day_one as E
from sportsassets import bettor_paper_ledger as L

BASE_FILES = {"001_init.sql": "create table a();\n",
              "002_more.sql": "create table b();\n"}
WITH_EPOCH = dict(BASE_FILES, **{
    "317_paper_day_one_epoch.sql": "create table paper_account_epochs();\n"})
WITHOUT_EPOCH = dict(BASE_FILES, **{
    "003_new.sql": "alter table a add c int;\n"})


def _release(acc, paper_epochs="absent"):
    doc = {"api": {"status": "OK", "sha": REL}}
    if paper_epochs != "absent":
        doc["paper_epochs"] = paper_epochs
    _w(acc, "release.json", doc)


def _epochs(n, selected="paper_acct_main"):
    return {"status": "OK", "migration_317_applied": True, "epochs": n,
            "rollbacks": 0, "selected_account": selected}


def _proven(acc):
    tfp = json.loads((acc / "rollback.json").read_text())[
        "migrations"]["target"]["fingerprint"]
    _w(acc, "upgrade_path.json", _readback(_receipt(base_fp=tfp)))


def _epoch_reasons(unit):
    return [r for r in unit["detail"]["reasons"]
            if r.startswith("ROLLBACK_PAPER_EPOCH")]


def test_no_epoch_keeps_the_receipt_verdict(tmp_path):
    acc = _packet(tmp_path, release_files=WITH_EPOCH)
    _proven(acc)
    _release(acc, _epochs(0))
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is True, u["detail"]["reasons"]
    assert u["detail"]["schema"] == "COMPATIBLE_BY_UPGRADE_RECEIPT"
    assert u["detail"]["paper_epochs"]["read"] is True
    assert u["detail"]["paper_epochs"]["epochs"] == 0


def test_an_activated_epoch_makes_the_rollback_not_ready_by_name(tmp_path):
    acc = _packet(tmp_path, release_files=WITH_EPOCH)
    _proven(acc)
    _release(acc, _epochs(1, "paper_day_one_abc"))
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is False
    assert u["detail"]["status"] == "NOT_READY"
    assert _epoch_reasons(u) == [
        "%s:1:paper_day_one_abc" % SC.R_ROLLBACK_PAPER_EPOCH_ACTIVATED]
    # the per-service rules are untouched: the receipt still reads
    # compatible, it is the epoch that blocks
    assert u["detail"]["schema"] == "COMPATIBLE_BY_UPGRADE_RECEIPT"


def test_a_rolled_back_epoch_still_blocks(tmp_path):
    acc = _packet(tmp_path, release_files=WITH_EPOCH)
    _proven(acc)
    _release(acc, dict(_epochs(1, "paper_acct_main"), rollbacks=1))
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is False
    assert _epoch_reasons(u) == [
        "%s:1:paper_acct_main" % SC.R_ROLLBACK_PAPER_EPOCH_ACTIVATED]


@pytest.mark.parametrize("state", [
    "absent", {"status": "UNAVAILABLE", "why": "UndefinedTableError"},
    {"status": "OK", "epochs": "1"}, {"status": "OK", "epochs": True},
    {"status": "OK", "epochs": -1}, None])
def test_an_unread_epoch_state_is_no_proof(tmp_path, state):
    acc = _packet(tmp_path, release_files=WITH_EPOCH)
    _proven(acc)
    if state is None:
        (acc / "release.json").unlink(missing_ok=True)
    else:
        _release(acc, state)
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is False
    assert _epoch_reasons(u) == [SC.R_ROLLBACK_PAPER_EPOCH_STATE_UNREAD]


def test_a_release_without_the_epoch_migration_is_judged_as_before(tmp_path):
    acc = _packet(tmp_path, release_files=WITHOUT_EPOCH)
    _proven(acc)
    _release(acc)            # an older API: no paper_epochs at all
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is True, u["detail"]["reasons"]
    assert _epoch_reasons(u) == []
    assert u["detail"]["paper_epochs"]["release_adds_epoch_migration"] \
        is False


def test_the_readiness_tool_applies_the_same_rule_when_it_has_the_state(tmp_path):
    acc = _packet(tmp_path, release_files=WITH_EPOCH)
    # in the pm-acceptance job the readback is not collected yet: recorded
    # as not read, the judge decides
    rb = json.loads((acc / "rollback.json").read_text())
    assert rb["paper_epochs"]["read"] is False
    assert rb["paper_epochs"]["why"] == "RELEASE_READBACK_NOT_IN_ACC"
    assert not [r for r in rb["reasons"]
                if r.startswith("ROLLBACK_PAPER_EPOCH")]
    # with the readback present: any epoch is NOT_READY by name
    _release(acc, _epochs(2, "paper_day_one_xyz"))
    tdir, rdir = tmp_path / "target_mig", tmp_path / "release_mig"
    rb = RB.build(acc, sha=REL, target_migrations=tdir,
                  release_migrations=rdir, target_is_ancestor=True)
    assert rb["status"] == RB.NOT_READY
    assert "%s:2:paper_day_one_xyz" % RB.R_PAPER_EPOCH_ACTIVATED \
        in rb["reasons"]
    _release(acc, {"status": "UNAVAILABLE"})
    rb = RB.build(acc, sha=REL, target_migrations=tdir,
                  release_migrations=rdir, target_is_ancestor=True)
    assert rb["status"] == RB.NOT_READY
    assert RB.R_PAPER_EPOCH_STATE_UNREAD in rb["reasons"]
    _release(acc, _epochs(0))
    rb = RB.build(acc, sha=REL, target_migrations=tdir,
                  release_migrations=rdir, target_is_ancestor=True)
    assert not [r for r in rb["reasons"]
                if r.startswith("ROLLBACK_PAPER_EPOCH")]
    assert rb["paper_epochs"] == {
        "release_adds_epoch_migration": True, "read": True, "epochs": 0,
        "selected_account": "paper_acct_main"}


async def test_the_release_readback_carries_the_epoch_state(conn):
    from sportsassets.api import command_release as CR
    before = await CR.paper_epochs(conn)
    assert before["status"] == "OK" and before["migration_317_applied"]
    assert before["epochs"] == 0 and before["selected_account"] \
        == L.ACCOUNT_ID
    r = await E._activate_verified(conn, epoch_id='rc63-rbk',
                                   request_id='rc63-rbk', proof=PROOF)
    out = await CR.build_release(conn)
    assert out["paper_epochs"]["epochs"] == 1
    assert out["paper_epochs"]["selected_account"] == r["account_id"]
    await E.rollback(conn, epoch_id='rc63-rbk', request_id='rc63-rbk-undo')
    after = await CR.paper_epochs(conn)
    assert after["epochs"] == 1 and after["rollbacks"] == 1
    assert after["selected_account"] == L.ACCOUNT_ID
