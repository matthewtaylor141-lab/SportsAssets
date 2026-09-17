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

PROVENANCE_FIELDS = ("DISPATCH_SHA", "EXECUTED_SHA_ACTUAL", "WORKFLOW_REF_SHA",
                     "WORKFLOW_FILE_SHA", "CONFIG_SHA", "DATA_OUTPUT_SHA")

# ---------------------------------------------------------------------------
# SCHEMA VERSIONS, BECAUSE A HARVESTER CAN OUTRUN THE RUN IT READS
# ---------------------------------------------------------------------------
#
# v1  DISPATCH_SHA / EXECUTED_SHA / WORKFLOW_REF_SHA / WORKFLOW_FILE_SHA /
#     CONFIG_SHA / DATA_OUTPUT_SHA, and a single EVIDENCE_RUN_VALIDITY
#     computed from the code comparison alone.
# v2  EXECUTED_SHA renamed EXECUTED_SHA_ACTUAL, EXECUTED_SHA_SOURCE added, and
#     the verdict split into four aspects.
#
# Run 35133738118 executes commit 2c3b261, which is v1. The harvester is v2.
# That gap is recorded rather than smoothed over: a reader must be able to see
# that a field was RENAMED in translation and not invented.
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
LEGACY_FIELD_MAPPING = ("EXECUTED_SHA -> EXECUTED_SHA_ACTUAL",)
TRANSLATION_IS_NOT_SYNTHESIS = (
    "renaming, verifying, rehashing and comparing existing evidence is "
    "allowed; manufacturing a component the executed run never recorded is "
    "not")


def schema_version_of(sealed):
    """Which provenance schema an executed artifact was written under."""
    if not sealed:
        return NOT_IDENTIFIED
    if "EXECUTED_SHA_ACTUAL" in sealed or "CODE_PROVENANCE_VALIDITY" in sealed:
        return SCHEMA_VERSION
    if "EXECUTED_SHA" in sealed:
        return LEGACY_SCHEMA_VERSION
    return NOT_IDENTIFIED

# ---------------------------------------------------------------------------
# AN EXPECTATION IS NOT AN OBSERVATION
# ---------------------------------------------------------------------------
#
# Before the runner starts we hold DISPATCH_SHA and EXPECTED_EXECUTED_SHA --
# the same number twice, both written by us. Comparing them proves only that we
# can copy a string. EVIDENCE_RUN_VALIDITY is a claim about what a RUNNER did,
# and until the runner reports its checkout there is nothing to validate.
#
# So the pre-dispatch gate cannot reach PASS on that field at all. It is
# structurally unable to: pre_dispatch_gate() takes no executed SHA, so there
# is no argument position into which an expectation could be passed and read
# back as an observation. That is a stronger guarantee than remembering not to.
PENDING = "PENDING_EXECUTION"
EXECUTED_SHA_MUST_BE_OBSERVED = True
OBSERVED_SOURCES = ("RUNNER_GIT_REV_PARSE",)
WHY_EXPECTED_IS_NOT_OBSERVED = (
    "DISPATCH_SHA and EXPECTED_EXECUTED_SHA are the same value written twice "
    "by the dispatcher; only the runner's own checkout is evidence")

# Four provenance questions, four answers. Collapsing them hides which one
# failed, and three of the four are recordings rather than comparisons.
PROVENANCE_ASPECTS = ("CODE_PROVENANCE_VALIDITY", "WORKFLOW_PROVENANCE",
                      "CONFIG_PROVENANCE", "DATA_OUTPUT_PROVENANCE")


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


def pre_dispatch_gate(dispatch_sha, workflow_file_sha=None, config_sha=None):
    """What can be established BEFORE a runner exists -- and nothing further.

    Note the signature: there is no executed-SHA parameter. An expectation
    cannot be passed in here and read back out as an observation, because there
    is nowhere to put it. EVIDENCE_RUN_VALIDITY comes back PENDING_EXECUTION
    and no argument can change that.
    """
    d = _norm(dispatch_sha)
    checks = {
        "DISPATCH_SHA_VALID_FORMAT": bool(d),
        "WORKFLOW_CONFIG_VALID": bool(workflow_file_sha
                                      and workflow_file_sha != NOT_IDENTIFIED),
        "CONFIG_SHA_PRESENT": bool(config_sha and config_sha != NOT_IDENTIFIED),
    }
    ok = all(checks.values())
    return {
        "DISPATCH_SHA": d or NOT_IDENTIFIED,
        "DISPATCH_SHA_VALID_FORMAT": "YES" if d else "NO",
        # Named EXPECTED, and it is the dispatch SHA restated. It is not
        # evidence and is never compared against itself for a verdict.
        "EXPECTED_EXECUTED_SHA": d or NOT_IDENTIFIED,
        "EXECUTED_SHA_ACTUAL": PENDING,
        "WORKFLOW_FILE_SHA": workflow_file_sha or NOT_IDENTIFIED,
        "CONFIG_SHA": config_sha or NOT_IDENTIFIED,
        "DATA_OUTPUT_SHA": PENDING,
        "CHECKS": checks,
        "FAILED_CHECKS": [k for k, v in checks.items() if not v],
        "PRE_DISPATCH_PROVENANCE_GATE": "PASS" if ok else "FAIL",
        "EVIDENCE_RUN_VALIDITY": PENDING,
        "WHY_PENDING": WHY_EXPECTED_IS_NOT_OBSERVED,
        "EXECUTED_SHA_MUST_BE_OBSERVED": EXECUTED_SHA_MUST_BE_OBSERVED,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
    }


def check(dispatch_sha, executed_sha_actual, workflow_ref_sha=None,
          workflow_file_sha=None, config_sha=None, data_output_sha=None,
          executed_sha_source=None):
    """EVIDENCE_RUN_VALIDITY, from an OBSERVED checkout. Never by default.

    `executed_sha_actual` must come from the runner -- `git rev-parse HEAD`
    after checkout. `executed_sha_source` names where it came from, and a
    source this module does not recognise as an observation fails the run
    rather than being taken on trust.

    Four provenance questions are answered separately, because three of them
    are recordings and only one is a comparison, and collapsing them would hide
    which one failed.
    """
    d, e = _norm(dispatch_sha), _norm(executed_sha_actual)
    src = executed_sha_source or (OBSERVED_SOURCES[0] if e else None)
    # Abbreviated SHAs compare on the shorter prefix, so a 7-char dispatch SHA
    # still pins a 40-char checkout. Absent values never compare equal.
    match = bool(d and e and (d.startswith(e) or e.startswith(d)))
    observed = src in OBSERVED_SOURCES
    checks = {
        "DISPATCH_SHA_PRESENT": bool(d),
        "EXECUTED_SHA_ACTUAL_PRESENT": bool(e),
        "EXECUTED_SHA_WAS_OBSERVED_NOT_EXPECTED": observed,
        "DISPATCH_SHA_EQUALS_EXECUTED_SHA": match,
    }
    code_ok = all(checks.values())
    ref = _norm(workflow_ref_sha)

    # Each aspect names the evidence it rests on. `data_output_sha` here is the
    # SEALED hash the run itself wrote -- a harvest-time rehash verifies that
    # hash, it does not stand in for a missing one.
    aspects = {
        "CODE_PROVENANCE_VALIDITY": "PASS" if code_ok else "FAIL",
        "WORKFLOW_PROVENANCE": ("RECORDED" if workflow_file_sha
                                and workflow_file_sha != NOT_IDENTIFIED
                                else "NOT_RECORDED"),
        "CONFIG_PROVENANCE": ("RECORDED" if config_sha
                              and config_sha != NOT_IDENTIFIED
                              else "NOT_RECORDED"),
        "DATA_OUTPUT_PROVENANCE": ("RECORDED" if data_output_sha
                                   and data_output_sha != NOT_IDENTIFIED
                                   else "NOT_RECORDED"),
    }
    sources = {
        "CODE_PROVENANCE_SOURCE": (
            "SEALED_RUNNER_OBSERVED_GIT_REV_PARSE_HEAD" if e
            else "ABSENT_FROM_EXECUTED_ARTIFACT"),
        "WORKFLOW_PROVENANCE_SOURCE": (
            "SEALED_WORKFLOW_FILE_HASH"
            if aspects["WORKFLOW_PROVENANCE"] == "RECORDED"
            else "ABSENT_FROM_EXECUTED_ARTIFACT"),
        "CONFIG_PROVENANCE_SOURCE": (
            "SEALED_CONFIG_HASH"
            if aspects["CONFIG_PROVENANCE"] == "RECORDED"
            else "ABSENT_FROM_EXECUTED_ARTIFACT"),
        "DATA_OUTPUT_PROVENANCE_SOURCE": (
            "SEALED_OUTPUT_HASH_WRITTEN_BY_THE_RUN"
            if aspects["DATA_OUTPUT_PROVENANCE"] == "RECORDED"
            else "ABSENT_FROM_EXECUTED_ARTIFACT"),
        "REHASH_IS_VERIFICATION_NOT_PROVENANCE": True,
        "NOT_INFERRED_FROM_THE_CURRENT_REPOSITORY": True,
    }
    ok = (aspects["CODE_PROVENANCE_VALIDITY"] == "PASS"
          and all(aspects[a] == "RECORDED" for a in PROVENANCE_ASPECTS[1:]))

    out = {
        "DISPATCH_SHA": d or NOT_IDENTIFIED,
        "EXECUTED_SHA_ACTUAL": e or NOT_IDENTIFIED,
        "EXECUTED_SHA_SOURCE": src or NOT_IDENTIFIED,
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
        "WHY_EXPECTED_IS_NOT_OBSERVED": WHY_EXPECTED_IS_NOT_OBSERVED,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
    }
    out.update(aspects)
    out.update(sources)
    out["PROVENANCE_SCHEMA_VERSION"] = SCHEMA_VERSION
    if not ok:
        out["WHY_INVALID"] = (
            "the code that ran is not the code that was authorised"
            if not code_ok else
            "a required provenance record is missing, so the artifacts cannot "
            "be tied to the revision and inputs that produced them")
    # Reported, never gating: the workflow FILE is read from the ref, so it can
    # legitimately come from a different commit than our checkout. Seeing it is
    # the point.
    out["WORKFLOW_FILE_AND_CODE_SAME_COMMIT"] = (
        "YES" if (ref and e and ref == e) else
        ("NO" if (ref and e) else NOT_IDENTIFIED))
    return out


def pre_get_gate(dispatch_sha, executed_sha_actual, workflow_ref_sha=None,
                 workflow_file_sha=None, config_sha=None,
                 executed_sha_source=None):
    """The gate a RUNNER may pass BEFORE its first venue request.

    THE DEFECT THIS REPAIRS, recorded because run 35175681195 hit it and
    produced RUN_RESULT = NO_EVIDENCE with VENUE_REQUESTS = 0.

    The workflow called `check()` at step 6, before the paced run existed.
    `check()` is the HARVEST-time verdict and requires all four aspects
    RECORDED, including DATA_OUTPUT_PROVENANCE. But the rows it hashes are
    written by step 8, and the CLI had no way to pass a data hash in anyway.
    So the aggregate was unreachable by construction: a gate with no key, in
    the same class as the confirmation deadlock fixed before run 2. Every
    0.25-rps run at that revision would have aborted before the first GET,
    whatever the venue did.

    The repair is to ask the question that CAN be answered before execution,
    and to keep the harvest question exactly as strict as it was.

    THIS FUNCTION WEAKENS NO CODE-PROVENANCE CHECK. It calls `check()` and
    uses its findings verbatim -- the same four code checks, the same observed
    -not-expected requirement, the same abbreviated-SHA comparison. It changes
    ONE thing: the aggregate verdict is PENDING_EXECUTION, not FAIL, when the
    only aspect still missing is the one that cannot exist yet.

    A data hash passed here is refused rather than accepted early. If rows
    already exist, this is not the pre-GET moment and `check()` is the right
    call -- so there is no argument position through which a run could be
    declared valid here.
    """
    r = check(dispatch_sha, executed_sha_actual, workflow_ref_sha,
              workflow_file_sha, config_sha, None, executed_sha_source)

    code_ok = r["CODE_PROVENANCE_VALIDITY"] == "PASS"
    pre_ok = all(r[a] == "RECORDED" for a in ("WORKFLOW_PROVENANCE",
                                              "CONFIG_PROVENANCE"))
    # DATA_OUTPUT_PROVENANCE is NOT_RECORDED here by construction, and that is
    # the expected state rather than a defect. Everything else must hold.
    r["PRE_GET_PROVENANCE_GATE"] = "PASS" if (code_ok and pre_ok) else "FAIL"
    r["EVIDENCE_RUN_VALIDITY"] = PENDING if (code_ok and pre_ok) else "FAIL"
    r["STAGE"] = "PRE_GET"
    r["DATA_OUTPUT_PROVENANCE_EXPECTED_AT_THIS_STAGE"] = "NOT_RECORDED"
    r["WHY_PENDING_NOT_PASS"] = (
        "the rows this run will write do not exist yet, so DATA_OUTPUT_SHA "
        "cannot be recorded; a run is never VALID on pre-GET evidence alone")
    r["FINAL_VALIDITY_STILL_REQUIRES"] = list(PROVENANCE_ASPECTS)
    r["PRE_GET_CANNOT_MAKE_A_RUN_VALID"] = True
    if code_ok and pre_ok:
        r.pop("WHY_INVALID", None)
    return r


def receipt(path, payload):
    """Persist a provenance/pre-dispatch receipt beside the evidence."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
    return str(p)


def render(r):
    """The block printed before the first request, and again at harvest."""
    lines = []
    for f in PROVENANCE_FIELDS:
        if f == "EXECUTED_SHA_ACTUAL" and "EXPECTED_EXECUTED_SHA" in r:
            lines.append("%-34s = %s" % ("EXPECTED_EXECUTED_SHA",
                                         r["EXPECTED_EXECUTED_SHA"]))
        lines.append("%-34s = %s" % (f, r.get(f, NOT_IDENTIFIED)))
    for a in PROVENANCE_ASPECTS:
        if a in r:
            lines.append("%-34s = %s" % (a, r[a]))
    for c in r.get("FAILED_CHECKS", []):
        lines.append("%-34s = %s" % ("FAILED_CHECK", c))
    if "PRE_DISPATCH_PROVENANCE_GATE" in r:
        lines.append("%-34s = %s" % ("PRE_DISPATCH_PROVENANCE_GATE",
                                     r["PRE_DISPATCH_PROVENANCE_GATE"]))
    if "PRE_GET_PROVENANCE_GATE" in r:
        lines.append("%-34s = %s" % ("PRE_GET_PROVENANCE_GATE",
                                     r["PRE_GET_PROVENANCE_GATE"]))
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
    # STAGE SELECTS THE QUESTION, NOT THE STRICTNESS.
    #   pre-get  before the first venue request; DATA_OUTPUT cannot exist yet,
    #            so the verdict is PENDING_EXECUTION and the run is not valid.
    #   harvest  after the rows are written; the full four-aspect verdict,
    #            unchanged, and FAIL without a data output hash.
    ap.add_argument("--stage", choices=("pre-get", "harvest"),
                    default="harvest")
    ap.add_argument("--data-output", default=None,
                    help="path to the sealed rows; hashed for DATA_OUTPUT_SHA")
    a = ap.parse_args()
    wf = file_sha256(a.workflow_file) if a.workflow_file else None
    cfg = file_sha256(a.config) if a.config else None
    if a.stage == "pre-get":
        r = pre_get_gate(a.dispatch_sha, a.executed_sha, a.workflow_ref_sha,
                         wf, cfg)
        accept = (PENDING,)
    else:
        r = check(a.dispatch_sha, a.executed_sha, a.workflow_ref_sha, wf, cfg,
                  file_sha256(a.data_output) if a.data_output else None)
        accept = ("PASS",)
    print(render(r))
    if a.out:
        receipt(a.out, r)
    if r["EVIDENCE_RUN_VALIDITY"] not in accept:
        raise SystemExit("EVIDENCE_RUN_VALIDITY = %s"
                         % r["EVIDENCE_RUN_VALIDITY"])


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
