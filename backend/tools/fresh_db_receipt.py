"""THE FRESH-DATABASE RECEIPT: THE HALF OF MIGRATION INTEGRITY THE RUNNING
API CANNOT ESTABLISH ABOUT ITSELF (RC6, 2026-10-08).

Production evidence (pm-acceptance 37836393458 on release 69a8a07e): the
red-team MIGRATION_INTEGRITY control read applied 224 = repo 224, applied
fingerprint f442188e... = repo fingerprint, nothing edited in place and
nothing applied that the build lacks -- and was still UNKNOWN with
FRESH_DB_RESULT_NOT_IN_THIS_PROCESS (evidence fresh_db "UNPROVEN_HERE"), so
the scorecard's Deployment infrastructure row failed its migration unit at
5/6. The serving API cannot know whether its migrations build an EMPTY
database; only a fresh PostgreSQL can. The capital-critical gate already
builds exactly that (PostgreSQL 16, production's own runner `python -m
sportsassets.scripts.migrate`, the exact SHA) and kept nothing of it but the
job's conclusion. This file turns that build into a receipt.

Two modes, standard library only:

  build     capital-critical, from the release's own tree, right after the
            fresh build: reads what production's runner recorded in the
            fresh database's schema_migrations ({version: content_sha}, as
            psql dumped it), hashes the tree's migrations/*.sql exactly as
            the runner does, and writes the receipt -- the SHA, the run, the
            server version, the counts, the FULL applied map, and the
            fingerprint computed EXACTLY as the API's own control computes
            its applied fingerprint (redteam.controls.migrations; pinned by
            test). The result is PASSED only when the build step succeeded
            and the fresh database holds every file of the tree, nothing
            else, each with the tree's own hash; otherwise FAILED with the
            named reasons. A failed build is a FAILED receipt, never no
            receipt. The job attests the file (Sigstore) and keeps it in its
            artifact.

  readback  pm-acceptance, from the judge's own commit: records the receipt
            of the capital-critical run that the gate conclusions name for
            the release SHA, with that run's provenance (workflow path, head
            SHA, conclusion) and whether its attestation verified, as
            acc/fresh_db.json -- a declared readback inside the attested
            evidence packet. It judges nothing: the scorecard binds it
            against the running API's own fingerprint (absent or unverified
            = UNPROVEN, a different fingerprint = RED).

    python tools/fresh_db_receipt.py build --applied A.json --migrations DIR
        --sha SHA --run-id N --build-outcome success --out R.json
    python3 -I tools/fresh_db_receipt.py readback --run-id N --run RUN.json
        --receipt R.json --attestation-verified true --out acc/fresh_db.json

Nothing here connects to anything: it reads files and writes one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

VERSION = "FRESH_DB_RECEIPT_V1"
READBACK_VERSION = "FRESH_DB_READBACK_V1"
PASSED, FAILED = "PASSED", "FAILED"
RUNNER = "python -m sportsassets.scripts.migrate"
#: the only workflow whose run may carry this receipt
WORKFLOW = ".github/workflows/capital-critical.yml"
_SHA = re.compile(r"^[0-9a-f]{40}$")

# ── why a fresh build is not PASSED (evidence reasons, never refusals of a
#    trading path) ────────────────────────────────────────────────────────
R_BUILD_STEP_FAILED = "FRESH_DB_BUILD_STEP_NOT_SUCCESS"
R_APPLIED_UNREADABLE = "FRESH_DB_APPLIED_MIGRATIONS_UNREADABLE"
R_TREE_HAS_NO_MIGRATIONS = "FRESH_DB_TREE_HAS_NO_MIGRATIONS"
R_NOT_APPLIED = "FRESH_DB_MIGRATION_NOT_APPLIED"
R_APPLIED_NOT_IN_TREE = "FRESH_DB_APPLIED_NOT_IN_TREE"
R_CONTENT_DIFFERS = "FRESH_DB_CONTENT_SHA_DIFFERS_FROM_TREE"
R_NOT_A_FULL_SHA = "FRESH_DB_NOT_A_FULL_SHA"
# ── why pm-acceptance holds no receipt for the release ────────────────────
R_NO_GATE_RUN = "FRESH_DB_NO_CAPITAL_CRITICAL_RUN_FOR_THE_SHA"
R_RECEIPT_NOT_IN_ARTIFACT = "FRESH_DB_RECEIPT_NOT_IN_THE_RUN_ARTIFACT"
R_RECEIPT_NOT_JSON = "FRESH_DB_RECEIPT_NOT_JSON"


def fingerprint(applied: dict) -> str:
    """EXACTLY redteam.controls.migrations' applied fingerprint over a
    {version: content_sha} map (evidence_hash of the sorted items): a fresh
    database that holds the same rows as production yields the same hex."""
    return hashlib.sha256(json.dumps(sorted(applied.items()), sort_keys=True,
                                     default=str).encode()).hexdigest()


def tree_migrations(directory: pathlib.Path) -> dict:
    """{file name: sha256 of its text}: the runner's content_sha
    (scripts.migrate.content_sha) and the API's repo_migrations(), byte for
    byte."""
    return {p.name: hashlib.sha256(p.read_text().encode("utf-8")).hexdigest()
            for p in sorted(pathlib.Path(directory).glob("*.sql"))}


def build(applied, tree: dict, *, sha: str, run_id=None, run_attempt=None,
          build_outcome=None, server_version=None) -> dict:
    """The receipt of one fresh build. `applied` is the fresh database's
    {version: content_sha} (None when it could not be read)."""
    reasons = []
    if not _SHA.match(str(sha or "")):
        reasons.append(R_NOT_A_FULL_SHA)
    if build_outcome != "success":
        reasons.append("%s:%s" % (R_BUILD_STEP_FAILED, build_outcome))
    if not tree:
        reasons.append(R_TREE_HAS_NO_MIGRATIONS)
    ok_map = isinstance(applied, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in applied.items())
    if not ok_map:
        reasons.append(R_APPLIED_UNREADABLE)
        applied = {}
    missing = sorted(set(tree) - set(applied))
    extra = sorted(set(applied) - set(tree))
    differs = sorted(v for v in set(applied) & set(tree)
                     if applied[v] != tree[v])
    reasons += ["%s:%s" % (R_NOT_APPLIED, v) for v in missing[:20]]
    reasons += ["%s:%s" % (R_APPLIED_NOT_IN_TREE, v) for v in extra[:20]]
    reasons += ["%s:%s" % (R_CONTENT_DIFFERS, v) for v in differs[:20]]
    return {"version": VERSION, "sha": sha, "run_id": run_id,
            "run_attempt": run_attempt, "workflow": WORKFLOW,
            "runner": RUNNER, "server_version": server_version,
            "build_outcome": build_outcome,
            "migrations_in_tree": len(tree),
            "migrations_applied": len(applied),
            "not_applied": missing, "applied_not_in_tree": extra,
            "content_differs": differs,
            "fingerprint": fingerprint(applied),
            "tree_fingerprint": fingerprint(tree),
            "applied": dict(sorted(applied.items())),
            "result": PASSED if not reasons else FAILED,
            "reasons": reasons}


def readback(*, run_id, run: dict | None, receipt_path, attestation_verified,
             receipt_sha256=None) -> dict:
    """acc/fresh_db.json: the receipt as downloaded and where it came from.
    It decides nothing; a missing receipt is recorded with its reason."""
    run = run if isinstance(run, dict) else {}
    receipt, reason = None, None
    if not re.fullmatch(r"[0-9]{1,20}", str(run_id or "")):
        reason = R_NO_GATE_RUN
    elif not receipt_path or not pathlib.Path(receipt_path).is_file():
        reason = R_RECEIPT_NOT_IN_ARTIFACT
    else:
        raw = pathlib.Path(receipt_path).read_bytes()
        receipt_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            receipt = json.loads(raw)
        except ValueError:
            reason = R_RECEIPT_NOT_JSON
    return {"version": READBACK_VERSION, "reason": reason,
            "provenance": {
                "run_id": str(run_id) if run_id else None,
                "workflow_path": run.get("path"),
                "repository": (run.get("repository") or {}).get("full_name")
                if isinstance(run.get("repository"), dict) else None,
                "head_sha": run.get("head_sha"), "event": run.get("event"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "signer_workflow": WORKFLOW,
                "attestation_verified": attestation_verified is True,
                "receipt_sha256": receipt_sha256 if receipt is not None
                else None},
            "receipt": receipt}


def _json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    b = sub.add_parser("build")
    b.add_argument("--applied", required=True)
    b.add_argument("--migrations", required=True)
    b.add_argument("--sha", required=True)
    b.add_argument("--run-id")
    b.add_argument("--run-attempt")
    b.add_argument("--build-outcome")
    b.add_argument("--server-version")
    b.add_argument("--out", required=True)
    r = sub.add_parser("readback")
    r.add_argument("--run-id", default="")
    r.add_argument("--run", default="")
    r.add_argument("--receipt", default="")
    r.add_argument("--attestation-verified", default="false")
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.mode == "build":
        out = build(_json(a.applied), tree_migrations(a.migrations),
                    sha=a.sha, run_id=a.run_id, run_attempt=a.run_attempt,
                    build_outcome=a.build_outcome,
                    server_version=(a.server_version or "").strip() or None)
        summary = {k: out[k] for k in (
            "sha", "result", "migrations_in_tree", "migrations_applied",
            "fingerprint", "tree_fingerprint", "server_version")}
        summary["reasons"] = out["reasons"][:10]
    else:
        out = readback(run_id=a.run_id, run=_json(a.run) if a.run else None,
                       receipt_path=a.receipt or None,
                       attestation_verified=a.attestation_verified == "true")
        rec = out["receipt"] if isinstance(out["receipt"], dict) else {}
        summary = {"reason": out["reason"], "provenance": out["provenance"],
                   "result": rec.get("result"), "sha": rec.get("sha"),
                   "fingerprint": rec.get("fingerprint")}
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True))
    print("fresh database %s: %s" % (a.mode, json.dumps(summary)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
