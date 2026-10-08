"""THE RELEASE IS THE BRANCH THE SERVICES TRACK, NOT WHATEVER WAS DEPLOYED.

Production 2026-10-07: sportsassets-api and sportsassets-workers were deployed
by commit id at 08828d04 while claude/release-api -- the branch BOTH services
track (render-ops api-branch-get: branch=claude/release-api autoDeploy=no) --
still pointed at a91be09f. Nothing noticed, because pm-acceptance.yml wrote
the input SHA into tested_sha, release_sha AND deployed_sha, so the release
gate's RELEASE_SHA_DIFFERS_FROM_TESTED_SHA could never fire.

These tests hold the receipt to the branch: release_sha is read from
claude/release-api, and a release whose branch was left behind is a blocker.
They read files only; nothing here reaches GitHub, Render or a database."""
from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "pm-acceptance.yml"
A91 = "a91be09f125f0ab0d1f29a0d0866b5cb6f5765fd"
D088 = "08828d04766017e520ffd4a34740162a2c792d36"


def _steps():
    doc = yaml.safe_load(WF.read_text())
    return {s.get("name", ""): s for s in doc["jobs"]["accept"]["steps"]}


def _step(prefix):
    hits = [s for n, s in _steps().items() if n.startswith(prefix)]
    assert len(hits) == 1, (prefix, list(_steps()))
    return hits[0]


def test_release_sha_is_read_from_claude_release_api():
    lin = _step("Lineage")
    run = lin["run"]
    assert "repos/$REPO/branches/claude/release-api" in run
    assert "release_sha:$r" in run.replace(" ", "")
    assert (lin.get("env") or {}).get("GH_TOKEN") == "${{ github.token }}"


def test_the_receipt_never_copies_the_input_into_release_sha():
    body = _step("POST the release receipt")["run"]
    assert not re.search(r"release_sha:\s*\$s\b", body), (
        "release_sha must come from the tracked branch, not the input SHA")
    assert re.search(r"release_sha:\s*\$l\[0\]\.release_sha", body)


def test_the_harness_rerun_uses_the_same_release_sha_as_the_receipt():
    run = _step("Re-read PM acceptance and re-run the package harness")["run"]
    assert "H.evaluate" in run
    assert 'release_sha=lin["release_sha"]' in run
    assert "release_sha=sha," not in run


def test_a_branch_left_behind_is_a_release_blocker():
    from sportsassets.red_team.models import ReleaseEvidence
    from sportsassets.red_team.release_guard import release_gate
    green = dict(backend_tests_green=True, capital_critical_green=True,
                 commit_guard_green=True, engine_diagnostic_green=True,
                 migration_fingerprint_match=True, is_descendant_of_base=True,
                 accepted_base_sha=A91)
    # the 2026-10-07 state: deployed and tested 08828d04, branch at a91be09f
    behind = release_gate(ReleaseEvidence(tested_sha=D088, release_sha=A91,
                                          deployed_sha=D088, **green))
    assert behind["green"] is False
    assert "RELEASE_SHA_DIFFERS_FROM_TESTED_SHA" in behind["blockers"]
    assert "DEPLOYED_SHA_DIFFERS_FROM_RELEASE_SHA" in behind["blockers"]
    # fast-forwarded: one SHA everywhere, and only then green
    ff = release_gate(ReleaseEvidence(tested_sha=D088, release_sha=D088,
                                      deployed_sha=D088, **green))
    assert ff["green"] is True and ff["blockers"] == ()


def test_the_accepted_base_is_a_full_sha_and_an_ancestor_check():
    doc = yaml.safe_load(WF.read_text())
    base = doc["jobs"]["accept"]["env"]["ACCEPTED_BASE"]
    assert re.fullmatch(r"[0-9a-f]{40}", base)
    assert 'merge-base --is-ancestor "$ACCEPTED_BASE" "$SHA"' in \
        _step("Lineage")["run"]
