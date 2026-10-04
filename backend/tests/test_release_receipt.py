"""THE RELEASE RECEIPT TELLS THE TRUTH ABOUT A GATE RUN, OR REFUSES TO.

tools/release_receipt.py turns a gate output directory into an immutable,
self-hashed receipt. These tests build synthetic gate directories and prove
the precedence the owner asked for:

  * any disk-full signature (errno 28 / ENOSPC / DiskFullError / "No space
    left on device") anywhere in the artifacts makes the run VOID -- even
    when the report itself looks clean;
  * no report yet is INCOMPLETE, never "no failures";
  * a failure the baseline does not have is a REJECTED run naming the exact
    node id; a failure the baseline has is listed with the baseline SHA and
    report path that excuse it;
  * a deploy slot is filled only from evidence that quotes its source; a
    7-character SHA is a PREFIX match, never an exact one;
  * the receipt hash verifies, a tampered receipt does not, a re-run with the
    same inputs writes nothing, and a receipt file is never overwritten;
  * the committed receipts are what they claim: f31 GATED (pending sign-off,
    commit guard red), f33 VOID on errno 28.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(BACKEND, "tools"))
import release_receipt as RR  # noqa: E402

RECEIPTS = os.path.join(BACKEND, "sportsassets", "release_receipts")
HEAD_SHA = subprocess.run(["git", "-C", BACKEND, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _node(outcome, longrepr=None):
    return {"outcome": outcome, "longrepr": longrepr, "decided_by": None,
            "phases": {"setup": "passed", "call": outcome if outcome in (
                "passed", "failed") else "passed", "teardown": "passed"}}


def _report(nodes):
    fails = sum(1 for v in nodes.values() if v["outcome"] == "failed")
    return {"schema": "GATE_REPORT_V1", "session_complete": True,
            "interrupted": None, "exitstatus": 1 if fails else 0,
            "collect_errors": [], "collected": sorted(nodes),
            "deselected": [], "nodes": nodes,
            "collected_count": len(nodes), "executed_count": len(nodes),
            "counts": {"passed": len(nodes) - fails, "failed": fails},
            "environment": {"argv": ["tests/"]}}


def _gate(tmp, name, nodes, sha, console="", jsonl="", migrate=True):
    d = tmp / name
    d.mkdir()
    if nodes is not None:
        (d / "head_report.json").write_text(json.dumps(_report(nodes)))
        (d / "head_meta.json").write_text(json.dumps(
            {"commit": sha, "report_present": True,
             "migration_max": "x.sql"}))
        (d / "timeline.txt").write_text(
            "=== head 2026-10-04T00:00:00Z start\n"
            "=== head 2026-10-04T00:30:00Z pytest-exit=1\n")
    else:
        (d / "timeline.txt").write_text("=== head 2026-10-04T00:00:00Z start\n")
    (d / "head_console.txt").write_text(console)
    (d / "head_report.json.failures.jsonl").write_text(jsonl)
    (d / "head.migrate.log").write_text(
        "INFO:__main__:applying 999_x.sql\nINFO:__main__:migrations complete\n"
        if migrate else "Traceback (most recent call last):\n")
    return str(d)


BASE_NODES = {"tests/test_a.py::test_ok": _node("passed"),
              "tests/test_a.py::test_known": _node(
                  "failed", "E   AssertionError: old"),
              "tests/test_crit.py::test_c": _node("passed")}


@pytest.fixture
def baseline(tmp_path):
    return _gate(tmp_path, "base", BASE_NODES, "b" * 40)


def _build(gdir, baseline, **kw):
    kw.setdefault("critical_entries", ["tests/test_crit.py"])
    kw.setdefault("generated_at", "2026-10-04T00:00:00+00:00")
    return RR.build("t1", gdir, "candX", HEAD_SHA, None, [baseline], **kw)


def test_a_clean_run_whose_failures_the_baseline_has_is_gated(tmp_path,
                                                              baseline):
    g = _gate(tmp_path, "head", dict(BASE_NODES), HEAD_SHA)
    doc = _build(g, baseline)
    assert doc["state"] == "GATED"
    assert doc["acceptance"] == "GATED_PENDING_SIGNOFF"     # no approver
    assert doc["new_regressions"] == []
    k = doc["known_baseline_failures"]
    assert [x["node"] for x in k] == ["tests/test_a.py::test_known"]
    assert k[0]["baselines"][0]["baseline_sha"] == "b" * 40
    assert k[0]["baselines"][0]["report_path"].endswith("head_report.json")
    assert k[0]["identical_in_every_baseline"] is True
    assert doc["critical"]["passed"] == 1 and not doc["critical"]["not_passed"]
    assert doc["stages"]["GATED"]["status"] == "PASS"
    assert doc["stages"]["DEPLOYED"]["status"] == "PENDING"
    assert doc["approver"]["status"] == "PENDING"
    assert RR.verify(doc)


@pytest.mark.parametrize("where,text", [
    ("console", "E   OSError: [Errno 28] No space left on device"),
    ("jsonl", '{"longrepr": "asyncpg.exceptions.DiskFullError: could not '
              'extend file"}'),
    ("console", "write failed: ENOSPC"),
])
def test_any_disk_full_signature_voids_even_a_clean_report(tmp_path, baseline,
                                                           where, text):
    g = _gate(tmp_path, "head", dict(BASE_NODES), HEAD_SHA,
              **{where: text})
    doc = _build(g, baseline)
    assert doc["state"] == "VOID" and doc["acceptance"] == "VOID"
    assert any(v["tripped"] for v in doc["void_conditions"])
    assert doc["void_hits"][0]["code"] == "DISK_FULL"
    assert doc["stages"]["TESTED"]["status"] == "VOID"
    assert doc["stages"]["GATED"]["status"] == "VOID"


def test_an_unfinished_migration_voids(tmp_path, baseline):
    g = _gate(tmp_path, "head", dict(BASE_NODES), HEAD_SHA, migrate=False)
    assert _build(g, baseline)["state"] == "VOID"


def test_no_report_is_incomplete_not_clean(tmp_path, baseline):
    g = _gate(tmp_path, "head", None, HEAD_SHA)
    doc = _build(g, baseline)
    assert doc["state"] == "INCOMPLETE"
    assert doc["stages"]["TESTED"]["status"] == "PENDING"
    assert doc["new_regressions"] == []      # empty because nothing ran


def test_a_new_failure_is_rejected_by_its_exact_node_id(tmp_path, baseline):
    nodes = dict(BASE_NODES)
    nodes["tests/test_a.py::test_ok"] = _node("failed", "E   ValueError: new")
    doc = _build(_gate(tmp_path, "head", nodes, HEAD_SHA), baseline)
    assert doc["state"] == "REJECTED"
    assert doc["new_regressions"] == ["tests/test_a.py::test_ok"]


def test_a_failed_critical_proof_is_rejected_even_if_the_baseline_failed_it(
        tmp_path):
    nodes = dict(BASE_NODES)
    nodes["tests/test_crit.py::test_c"] = _node("failed", "E   boom")
    base = _gate(tmp_path, "base2", nodes, "c" * 40)
    doc = _build(_gate(tmp_path, "head", dict(nodes), HEAD_SHA), base)
    assert doc["state"] == "REJECTED"
    assert doc["critical"]["not_passed"][0]["node"] == "tests/test_crit.py::test_c"


def test_the_wrong_sha_is_invalid(tmp_path, baseline):
    g = _gate(tmp_path, "head", dict(BASE_NODES), "d" * 40)
    doc = _build(g, baseline)
    assert doc["state"] == "INVALID"
    assert any("not the named" in p for p in doc["gate"]["validity_problems"])


def test_a_changed_cause_under_the_same_id_is_surfaced(tmp_path, baseline):
    nodes = dict(BASE_NODES)
    nodes["tests/test_a.py::test_known"] = _node("failed", "E   KeyError: x")
    doc = _build(_gate(tmp_path, "head", nodes, HEAD_SHA), baseline)
    assert doc["state"] == "GATED"          # the gate rule is identity
    b = [x for x in doc["blockers"]
         if x["code"] == "BASELINE_FAILURE_CAUSE_CHANGED"]
    assert b and b[0]["nodes"] == ["tests/test_a.py::test_known"]


def test_deploy_slots_need_a_quoted_source_and_a_prefix_is_not_exact(
        tmp_path, baseline):
    g = _gate(tmp_path, "head", dict(BASE_NODES), HEAD_SHA)
    src = {"url": "https://example.invalid/run/1", "lines": ["live x"]}
    ev = {"api_deploy": {"status": "LIVE", "sha": HEAD_SHA[:7],
                         "source": src},
          "worker_deploy": {"status": "LIVE", "sha": HEAD_SHA},     # no source
          "approver": {"name": "someone"}}                          # no time
    doc = _build(g, baseline, evidence=ev)
    assert doc["deploy"]["api"]["match"] == "PREFIX_7"
    assert doc["deploy"]["workers"]["status"] == "PENDING"
    assert doc["stages"]["DEPLOYED"]["status"] == "PARTIAL"
    assert doc["approver"]["status"] == "PENDING"
    ev["worker_deploy"]["source"] = src
    ev["worker_deploy"]["sha"] = "e" * 40
    doc = _build(g, baseline, evidence=ev)
    assert doc["deploy"]["workers"]["match"] == "DIFFERENT_BUILD"
    assert doc["stages"]["DEPLOYED"]["status"] == "PARTIAL"


def test_accepted_needs_a_named_approver_and_no_open_blocker(tmp_path,
                                                             baseline):
    g = _gate(tmp_path, "head", dict(BASE_NODES), HEAD_SHA)
    ev = {"approver": {"name": "owner", "at": "2026-10-04T12:00:00Z",
                       "source": "signed in the evidence file"}}
    assert _build(g, baseline, evidence=ev)["acceptance"] == "ACCEPTED"


def test_the_hash_detects_tampering_and_writes_are_immutable(tmp_path,
                                                             baseline):
    g = _gate(tmp_path, "head", dict(BASE_NODES), HEAD_SHA)
    out = tmp_path / "receipts"
    doc = _build(g, baseline)
    path, written = RR.write(doc, str(out))
    assert written and os.path.basename(path).startswith("t1__")
    again = _build(g, baseline, generated_at="2026-10-05T00:00:00+00:00")
    path2, written2 = RR.write(again, str(out))
    assert (path2, written2) == (path, False)       # same content: nothing new
    on_disk = json.load(open(path))
    assert RR.verify(on_disk)
    on_disk["state"] = "GATED_BUT_EDITED"
    assert not RR.verify(on_disk)
    idx = json.load(open(out / "index.json"))
    assert idx["receipts"][0]["hash_verified"] is True
    # new evidence makes a NEW receipt that names the one it supersedes
    ev = {"approver": {"name": "owner", "at": "2026-10-04T12:00:00Z",
                       "source": "x"}}
    newer = _build(g, baseline, evidence=ev)
    path3, written3 = RR.write(newer, str(out))
    assert written3 and path3 != path
    assert json.load(open(path3))["supersedes"] == doc["receipt_sha256"]


# ── the committed receipts ───────────────────────────────────────────

def _committed():
    out = {}
    for f in sorted(os.listdir(RECEIPTS)):
        if "__" in f and f.endswith(".json"):
            out[f] = json.load(open(os.path.join(RECEIPTS, f)))
    return out


def test_every_committed_receipt_verifies_and_is_indexed():
    docs = _committed()
    assert docs, "no receipts committed"
    idx = json.load(open(os.path.join(RECEIPTS, "index.json")))
    assert {r["file"] for r in idx["receipts"]} == set(docs)
    for f, d in docs.items():
        assert RR.verify(d), f
        assert f == "%s__%s.json" % (d["gate_id"], d["receipt_sha256"][:12])


def test_f31_is_gated_with_its_blockers_named():
    d = next(d for d in _committed().values() if d["gate_id"] == "f31")
    assert d["sha"] == "fd6cc5b1390dc181bb34a8e441f3ef4fd2ac29ad"
    assert d["state"] == "GATED" and d["new_regressions"] == []
    assert d["acceptance"] == "GATED_PENDING_SIGNOFF"
    assert len(d["known_baseline_failures"]) == 70
    assert {"COMMIT_GUARD_FAIL", "GITHUB_CI_RED"} <= {
        b["code"] for b in d["blockers"]}
    assert d["deploy"]["api"]["match"] == "PREFIX_7"
    assert d["deploy"]["workers"]["status"] == "REQUESTED"
    assert d["stages"]["DEPLOYED"]["status"] == "PARTIAL"
    assert d["migrations"]["new_vs_base"] == [
        "216_profitability_warehouse.sql", "217_eddie_scout_agents.sql",
        "218_model_agent_tournaments.sql", "219_digital_twin_evidence.sql"]


def test_f33_is_void_on_errno_28():
    d = next(d for d in _committed().values() if d["gate_id"] == "f33")
    assert d["sha"] == "8e627493cd40e7b63f0b57af23a84019c02b044b"
    assert d["state"] == "VOID" and d["acceptance"] == "VOID"
    assert any(h["code"] == "DISK_FULL" for h in d["void_hits"])
    assert d["stages"]["GATED"]["status"] == "VOID"
