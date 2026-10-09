"""pm-acceptance reads each gate from the newest run that CONCLUDED.

pm-acceptance 37884042844 (release 732cc0c6, 2026-10-09) graded the release's
capital-critical gate from run 37875940138, a duplicate dispatch the operator
cancelled at once, instead of the push-triggered run 37875926135 on the same
SHA that concluded success (CI_VERDICT ACCEPT, 21,935 tests, 0 unexpected):
the selector took the newest run of a name whatever its conclusion. That
failed GitHub CI's capital_critical unit, Red-team RELEASE and both
MIGRATION_INTEGRITY units (a cancelled run carries no fresh-database receipt).

.github/pm-acceptance/gate_runs.jq now skips runs that carry no verdict
(cancelled, skipped) and lists them under no_verdict. A failed run is still
the gate's run and still fails it; a run still in progress is still not
green; nothing concluded at all is still absent. The judge reads the file
from its own checkout (judge/), never from the release under test.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JQ = ROOT / ".github" / "pm-acceptance" / "gate_runs.jq"
WF = ROOT / ".github" / "workflows" / "pm-acceptance.yml"


def _gates(runs: list) -> dict:
    jq = shutil.which("jq")
    assert jq, "jq is required (the workflow uses it)"
    got = subprocess.run([jq, "-n", "--argjson", "x", json.dumps(runs),
                          "-f", str(JQ), "--slurpfile", "r", "/dev/stdin"],
                         input=json.dumps(runs), capture_output=True, text=True, timeout=30)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def _run(name, rid, at, status="completed", conclusion="success"):
    return {"name": name, "id": rid, "event": "push", "status": status,
            "conclusion": conclusion, "created_at": at, "head_sha": "a" * 40,
            "html_url": "u"}


BASE = [_run("backend-tests", 1, "2026-10-09T02:40:00Z"),
        _run("commit-guard", 2, "2026-10-09T02:40:00Z"),
        _run("engine-diagnostic", 3, "2026-10-09T02:40:00Z")]


def test_the_production_case_reads_the_run_that_concluded():
    runs = BASE + [_run("capital-critical", 37875926135, "2026-10-09T02:40:01Z"),
                   _run("capital-critical", 37875940138, "2026-10-09T02:40:30Z",
                        conclusion="cancelled")]
    g = _gates(runs)
    assert g["capital_critical_green"] is True
    assert g["runs"]["capital_critical"]["id"] == 37875926135
    assert [r["id"] for r in g["no_verdict"]] == [37875940138]


def test_a_newer_failed_run_still_fails_the_gate():
    runs = BASE + [_run("capital-critical", 10, "2026-10-09T02:40:01Z"),
                   _run("capital-critical", 11, "2026-10-09T03:00:00Z", conclusion="failure")]
    g = _gates(runs)
    assert g["capital_critical_green"] is False
    assert g["runs"]["capital_critical"]["id"] == 11


def test_a_run_in_progress_is_not_green_and_only_cancelled_is_absent():
    g = _gates(BASE + [_run("capital-critical", 20, "2026-10-09T02:40:01Z",
                            status="in_progress", conclusion=None)])
    assert g["capital_critical_green"] is False and g["runs"]["capital_critical"]["id"] == 20
    g = _gates(BASE + [_run("capital-critical", 21, "2026-10-09T02:40:01Z", conclusion="cancelled")])
    assert g["capital_critical_green"] is False and g["runs"]["capital_critical"] is None
    assert [r["id"] for r in g["no_verdict"]] == [21]


def test_the_judge_reads_its_own_grading_file():
    wf = WF.read_text(encoding="utf-8")
    assert '-f "$GITHUB_WORKSPACE/judge/.github/pm-acceptance/gate_runs.jq"' in wf
    assert "def latest(n): ([$r[0][] | select(.name == n)] | sort_by(.created_at) | last);" not in wf


def test_every_gate_reader_in_the_judge_uses_the_one_grading_file():
    """The release's gates, the implementation SHA's gates and the rollback
    target's gates are all read by gate_runs.jq: no inline copy of an older
    rule (one that let a cancelled run grade a gate) survives a merge."""
    wf = WF.read_text(encoding="utf-8")
    for out in ("> acc/gates.json", "> acc/gates_implementation.json",
                "> acc/gates_rollback_target.json"):
        line = next(ln for ln in wf.splitlines() if out in ln and "jq -n" in ln)
        assert '-f "$GITHUB_WORKSPACE/judge/.github/pm-acceptance/gate_runs.jq"' in line, line
    assert "def latest(" not in wf and "def green(" not in wf
