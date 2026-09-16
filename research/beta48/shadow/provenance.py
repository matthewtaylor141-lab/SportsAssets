#!/usr/bin/env python3
"""EVIDENCE-RUN PROVENANCE. An evidence run executes an IMMUTABLE revision.

WHY THIS EXISTS. Run 35131981591 sat queued for twenty-five minutes. Its
workflow checked out `ref: claude/session-njaewf` -- the BRANCH HEAD, resolved
when the runner starts, not the commit the run was dispatched against. Three
commits landed on that branch while it waited. Nothing broke: the executed
surface happened to be byte-identical, and that was verified by diff rather than
hoped for. But "happened to be" is not a property of an experiment.

A queued job that checks out a moving ref is an experiment whose code is decided
after the experiment is authorised. The dispatch receipt then describes a run
that may not be the run that happened, and no artifact records the difference.
That is exactly the class of defect this programme keeps finding in its own
measurements, one layer down.

SO: an evidence-producing workflow checks out an EXPLICIT SHA passed at
dispatch, and asserts at runtime that the checkout is that SHA.

    DISPATCH_SHA   the commit the run was authorised against
    EXECUTED_SHA   `git rev-parse HEAD` on the runner, after checkout
    DISPATCH_SHA == EXECUTED_SHA   or  EVIDENCE_RUN_VALIDITY = FAIL

The comparison is made ON THE RUNNER, before the first venue request, so a
mismatch costs nothing but a failed job. Checking afterwards would tell us the
evidence was invalid only once we had spent the window collecting it.

WHAT ELSE TRAVELS WITH THE EVIDENCE. A SHA pins the code; it does not pin the
inputs or prove the outputs are the ones described. So each artifact carries:

    WORKFLOW_REF_SHA   the branch head GitHub resolved for the run. Recorded
                       because the workflow FILE is read from the ref, not from
                       our checkout -- so if this differs from EXECUTED_SHA the
                       job definition and the code came from different commits,
                       and that is worth seeing even when it is benign.
    WORKFLOW_FILE_SHA  the workflow file's content hash at the executed SHA.
    CONFIG_SHA         the universe/config the run read.
    DATA_OUTPUT_SHA    the rows the run wrote.

None of this module contacts anything. It hashes files and compares strings.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

NOT_IDENTIFIED = "NOT_IDENTIFIED"
THIS_MODULE_CONTACTS_NOTHING = True

MUTABLE_REF_IS_NOT_ALLOWED_FOR_EVIDENCE = True
WHY = ("a queued job that checks out a moving ref is an experiment whose code "
       "is chosen after the experiment is authorised")

PROVENANCE_FIELDS = ("DISPATCH_SHA", "EXECUTED_SHA", "WORKFLOW_REF_SHA",
                     "WORKFLOW_FILE_SHA", "CONFIG_SHA", "DATA_OUTPUT_SHA")


def file_sha256(path):
    """Content hash of a file, or NOT_IDENTIFIED if it is not there."""
    p = Path(path)
    if not p.is_file():
        return NOT_IDENTIFIED
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm(sha):
    """A git SHA, lowercased. Anything that is not one comes back None."""
    if not sha:
        return None
    s = str(sha).strip().lower()
    if len(s) < 7 or any(c not in "0123456789abcdef" for c in s):
        return None
    return s


def check(dispatch_sha, executed_sha, workflow_ref_sha=None,
          workflow_file_sha=None, config_sha=None, data_output_sha=None):
    """EVIDENCE_RUN_VALIDITY. PASS only when the executed code is the
    authorised code, and never by default."""
    d, e = _norm(dispatch_sha), _norm(executed_sha)
    # Abbreviated SHAs compare on the shorter prefix, so a 7-char dispatch SHA
    # still pins a 40-char checkout. Absent values never compare equal.
    match = bool(d and e and (d.startswith(e) or e.startswith(d)))
    checks = {
        "DISPATCH_SHA_PRESENT": bool(d),
        "EXECUTED_SHA_PRESENT": bool(e),
        "DISPATCH_SHA_EQUALS_EXECUTED_SHA": match,
    }
    ok = all(checks.values())
    ref = _norm(workflow_ref_sha)
    out = {
        "DISPATCH_SHA": d or NOT_IDENTIFIED,
        "EXECUTED_SHA": e or NOT_IDENTIFIED,
        "WORKFLOW_REF_SHA": ref or NOT_IDENTIFIED,
        "WORKFLOW_FILE_SHA": workflow_file_sha or NOT_IDENTIFIED,
        "CONFIG_SHA": config_sha or NOT_IDENTIFIED,
        "DATA_OUTPUT_SHA": data_output_sha or NOT_IDENTIFIED,
        "CHECKS": checks,
        "FAILED_CHECKS": [k for k, v in checks.items() if not v],
        "EVIDENCE_RUN_VALIDITY": "PASS" if ok else "FAIL",
        "MUTABLE_REF_IS_NOT_ALLOWED_FOR_EVIDENCE":
            MUTABLE_REF_IS_NOT_ALLOWED_FOR_EVIDENCE,
        "WHY_IMMUTABLE": WHY,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
    }
    if not ok:
        out["WHY_INVALID"] = (
            "the code that ran is not the code that was authorised; the "
            "artifacts describe a different revision")
    # Reported, never gating: the workflow FILE is read from the ref, so it can
    # legitimately come from a different commit than our checkout. Seeing it is
    # the point.
    out["WORKFLOW_FILE_AND_CODE_SAME_COMMIT"] = (
        "YES" if (ref and e and ref == e) else
        ("NO" if (ref and e) else NOT_IDENTIFIED))
    return out


def receipt(path, payload):
    """Persist a provenance/pre-dispatch receipt beside the evidence."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
    return str(p)


def render(r):
    """The block printed before the first request."""
    lines = []
    for f in PROVENANCE_FIELDS:
        lines.append("%-34s = %s" % (f, r.get(f)))
    for c in r.get("FAILED_CHECKS", []):
        lines.append("%-34s = %s" % ("FAILED_CHECK", c))
    lines.append("%-34s = %s" % ("EVIDENCE_RUN_VALIDITY",
                                 r["EVIDENCE_RUN_VALIDITY"]))
    return "\n".join(lines)


def _cli():                                                   # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dispatch-sha", required=True)
    ap.add_argument("--executed-sha", required=True)
    ap.add_argument("--workflow-ref-sha", default=None)
    ap.add_argument("--workflow-file", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    r = check(a.dispatch_sha, a.executed_sha, a.workflow_ref_sha,
              file_sha256(a.workflow_file) if a.workflow_file else None,
              file_sha256(a.config) if a.config else None)
    print(render(r))
    if a.out:
        receipt(a.out, r)
    if r["EVIDENCE_RUN_VALIDITY"] != "PASS":
        raise SystemExit("EVIDENCE_RUN_VALIDITY = FAIL")


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
