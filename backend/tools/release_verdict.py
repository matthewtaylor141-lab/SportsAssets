"""THE RELEASE RECEIPT FOR THE RUNNING SHA, BUILT ON EVERY RUN AND JUDGED
EXACTLY AS THE API JUDGES A POSTED ONE -- NEVER POSTED BY THIS FILE (RC6,
2026-10-08).

Production evidence (pm-acceptance 37836393458 on release 69a8a07e): the
red-team RELEASE control read UNKNOWN, NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA,
while that same run had read every fact a receipt carries -- lineage
(descendant of a91be09f, claude/release-api at 69a8a07e), the four gates
success on the exact SHA (backend-tests 37803641044, capital-critical
37803641023, commit-guard 37803641037, engine-diagnostic 37803641160), the
migration fingerprints equal, workers live on 69a8a07e. The receipt is
POSTed only on post_receipt == "on" (C4: the PM review of 2026-10-08 02:30Z
found a receipt appended by a dispatch that never asked to post; the
default is off and stays off), so no receipt existed and the control could
only say UNKNOWN.

WHAT A TRUTHFUL RECEIPT FOR THE RUNNING SHA IS. The body the workflow's
sender would POST (the same fields, read from the same files: lineage.json,
gates.json, render.json, red_team.json), judged by the SAME rules the API
applies when it accepts one:

  * refused (HTTP 422 there, nothing recorded) when a SHA field is not 40
    hex or the body's deployed_sha is not the serving API's own
    RENDER_GIT_COMMIT -- here read from the API's red-team readback
    (data.readiness.implementation_sha), never copied from the input;
  * otherwise red_team.release_guard.release_gate (the API's own code,
    imported from the judge's tree) plus the endpoint's workers rule
    (WORKERS_NOT_ON_RELEASE_SHA when the workers' live commit differs).

It records the ACTUAL verdict, GREEN or RED or REFUSED, whatever it is, and
lands in acc/ before the packet is hash-manifested and attested (Sigstore),
so it is signed with everything else the run read. It is EVIDENCE, never
authority, and it is never sent anywhere: the only sender is still
.github/pm-acceptance/post_receipt.py behind post_receipt == "on".

WHY THE SCORECARD NEEDS A GREEN VERDICT, NOT JUST A RECEIPT. A receipt that
is present and names the running SHA proves only that the release was
recorded; a RED or REFUSED receipt is exactly that kind of record. The
RELEASE unit therefore passes only on GREEN for the release SHA (and never
when the API itself holds a non-GREEN receipt) -- a non-GREEN release cannot
read GREEN through it.

    python3 -I judge/backend/tools/release_verdict.py acc --sha SHA

Reads files only; writes acc/release_verdict.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

# the API's own release gate, from the tree this file sits in (the judge's
# checkout in pm-acceptance): stdlib-only modules, nothing else is imported
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sportsassets.red_team.models import ReleaseEvidence  # noqa: E402
from sportsassets.red_team.release_guard import release_gate  # noqa: E402

VERSION = "RELEASE_VERDICT_V1"
GREEN, RED, REFUSED = "GREEN", "RED", "REFUSED"
_HEX = re.compile(r"^[0-9a-f]{40}$")
#: command_red_team.REQUIRED, the fields the endpoint refuses without
REQUIRED = ("accepted_base_sha", "tested_sha", "release_sha", "deployed_sha",
            "descendant_of_base", "backend_tests_green",
            "capital_critical_green", "commit_guard_green",
            "engine_diagnostic_green")
INPUTS = ("lineage.json", "gates.json", "render.json", "red_team.json")
RUNNING_SOURCE = ("red_team.json data.readiness.implementation_sha (the "
                  "serving API's RENDER_GIT_COMMIT)")


def _load(acc: pathlib.Path, name: str):
    try:
        return json.loads((acc / name).read_text())
    except (OSError, ValueError):
        return None


def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def fingerprint_match(red_team) -> bool:
    """The workflow's jq, field for field: the applied fingerprint exists
    and equals the build's, nothing edited in place, nothing applied that
    the build lacks."""
    e = _get(red_team, "data", "readiness", "controls",
             "MIGRATION_INTEGRITY", "evidence") or {}
    if not isinstance(e, dict):
        return False
    return (e.get("applied_fingerprint") is not None
            and e.get("applied_fingerprint") == e.get("repo_fingerprint")
            and len(e.get("edited_in_place") or []) == 0
            and len(e.get("applied_not_in_build") or []) == 0)


def body(acc: pathlib.Path, sha: str) -> dict:
    """The receipt body the POST step builds (its jq), minus pm_acceptance
    (filed by the API as a separate PM_ACCEPTANCE_CI control receipt; it is
    not part of the release gate)."""
    lin = _load(acc, "lineage.json") or {}
    g = _load(acc, "gates.json") or {}
    ren = _load(acc, "render.json") or {}
    lin = lin if isinstance(lin, dict) else {}
    g = g if isinstance(g, dict) else {}
    return {"accepted_base_sha": lin.get("accepted_base_sha"),
            "tested_sha": sha, "release_sha": lin.get("release_sha"),
            "deployed_sha": sha,
            "descendant_of_base": lin.get("descendant_of_base"),
            "backend_tests_green": g.get("backend_tests_green"),
            "capital_critical_green": g.get("capital_critical_green"),
            "commit_guard_green": g.get("commit_guard_green"),
            "engine_diagnostic_green": g.get("engine_diagnostic_green"),
            "migration_fingerprint_match": fingerprint_match(
                _load(acc, "red_team.json")),
            "workers_deployed_sha": _get(ren, "sportsassets-workers",
                                         "live_commit")}


def validate(b: dict, *, running_sha: str) -> list:
    """command_red_team.validate_release, rule for rule (pinned by test)."""
    bad = [k for k in REQUIRED if k not in b]
    if bad:
        return ["MISSING:%s" % k for k in bad]
    out = []
    for k in ("accepted_base_sha", "tested_sha", "release_sha",
              "deployed_sha"):
        if not _HEX.match(str(b[k]).lower()):
            out.append("NOT_A_FULL_SHA:%s" % k)
    if str(b["deployed_sha"]).lower() != (running_sha or "").lower():
        out.append("DEPLOYED_SHA_IS_NOT_THIS_API")
    return out


def judge(b: dict, *, running_sha: str) -> dict:
    """{status, refused, release_gate}: what the API would record for this
    body -- nothing (REFUSED), or a receipt GREEN / RED with its blockers."""
    refused = validate(b, running_sha=running_sha)
    if refused:
        return {"status": REFUSED, "refused": refused,
                "release_gate": {"green": False, "blockers": []}}
    running = running_sha.lower()
    g = release_gate(ReleaseEvidence(
        tested_sha=str(b["tested_sha"]).lower(),
        release_sha=str(b["release_sha"]).lower(),
        deployed_sha=running,
        accepted_base_sha=str(b["accepted_base_sha"]).lower(),
        # `is True` where the endpoint has bool(): identical for the JSON
        # booleans the workflow writes, and a string such as "false" can
        # never count as green here
        is_descendant_of_base=b["descendant_of_base"] is True,
        backend_tests_green=b["backend_tests_green"] is True,
        capital_critical_green=b["capital_critical_green"] is True,
        commit_guard_green=b["commit_guard_green"] is True,
        engine_diagnostic_green=b["engine_diagnostic_green"] is True,
        migration_fingerprint_match=b.get(
            "migration_fingerprint_match") is True))
    blockers = list(g["blockers"])
    # the endpoint's workers rule: the same release on both services
    wk = str(b.get("workers_deployed_sha") or "").lower()
    if wk != running:
        blockers.append("WORKERS_NOT_ON_RELEASE_SHA:%s" % (wk[:12] or
                                                           "UNKNOWN"))
    green = bool(g["green"]) and not blockers
    return {"status": GREEN if green else RED, "refused": [],
            "release_gate": {"green": green, "blockers": blockers}}


def build(acc: pathlib.Path, sha: str) -> dict:
    acc = pathlib.Path(acc)
    rt = _load(acc, "red_team.json")
    running = _get(rt, "data", "readiness", "implementation_sha") \
        if isinstance(rt, dict) and rt.get("status") == "OK" else None
    running = running if isinstance(running, str) else "UNKNOWN"
    b = body(acc, sha)
    v = judge(b, running_sha=running)
    return {"version": VERSION, "sha": sha, "running_api_sha": running,
            "running_api_sha_source": RUNNING_SOURCE, "body": b,
            "status": v["status"], "refused": v["refused"],
            "release_gate": v["release_gate"],
            "posted": False,
            "inputs_sha256": {n: (hashlib.sha256((acc / n).read_bytes())
                                  .hexdigest() if (acc / n).is_file()
                                  else None) for n in INPUTS}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("acc")
    ap.add_argument("--sha", required=True)
    a = ap.parse_args(argv)
    out = build(pathlib.Path(a.acc), a.sha)
    (pathlib.Path(a.acc) / "release_verdict.json").write_text(
        json.dumps(out, indent=1, sort_keys=True))
    print("release verdict for %s (running %s): %s %s" % (
        a.sha, out["running_api_sha"], out["status"],
        json.dumps(out["refused"] or out["release_gate"]["blockers"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
