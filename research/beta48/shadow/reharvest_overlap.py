#!/usr/bin/env python3
"""OFFLINE RE-HARVEST OF A SEALED RATE-CONFIRMATION RUN'S OVERLAP AUDIT.

WHY THIS EXISTS. Run 35180590124 measured cleanly -- 301 requests, 301
successes, zero 429s, 1200 s of paced exposure -- and then sealed an overlap
audit that had never run. The harvest guarded the audit on FIRST_VENUE_GET_TIME
in confirm_report.json; `confirm()` did not write that key; the guard fell
through and sealed VENUE_REQUEST_WINDOWS = {}. An empty object and an object
nobody built are the same bytes, and the empty one reads as "nothing
overlapped".

WHAT THIS DOES, AND WHAT IT REFUSES TO DO. It reads the evidence that run
already sealed, derives the window from the receipts of the rows themselves,
and runs the SAME frozen `venue_domain.overlap_audit` -- through
`overlap_harvest`, so a missing window can no longer masquerade as a clean
result. It contacts nothing. It sends no venue request. It does not re-measure,
re-interpret or adjust one operational number: REQUESTS, SUCCESSES, HTTP_429,
OTHER_FAILURES, DURATION_S, the rate and the provenance verdicts are carried
across from the sealed report verbatim, and this module has no code path that
can alter them.

The window is derived, never synthesised. It comes from RECEIPT_UTC on the rows
the run wrote; min and max rather than first and last, because an extremum can
only widen the window, and widening fails towards detection.

DERIVATION_TYPE = OFFLINE_REHARVEST_FROM_ALREADY_SEALED_EVIDENCE. The amended
artifact says so on its face, names the source run and the source data-output
hash, and is never to be read as a second run.
"""
import argparse
import hashlib
import json
from pathlib import Path

import venue_domain as VD

NOT_IDENTIFIED = "NOT_IDENTIFIED"
DERIVATION_TYPE = "OFFLINE_REHARVEST_FROM_ALREADY_SEALED_EVIDENCE"
THIS_IS_NOT = "A_SECOND_RUN"
WHY_NOT_A_SECOND_RUN = (
    "no venue request was made; every number here is either read from the "
    "sealed evidence or derived from it by the frozen audit")

# Carried across verbatim. Named explicitly so that a reader can check the
# amended artifact against the sealed one field by field.
CARRIED_VERBATIM = (
    "RATE_RPS", "REQUESTS", "SUCCESSES", "HTTP_429", "OTHER_FAILURES",
    "DURATION_S", "NOMINAL_PACED_EXPOSURE_S", "POLL_ORDER_FAIRNESS",
    "STARVED_MARKETS", "EVIDENCE_RUN_VALIDITY", "DISPATCH_SHA",
    "EXECUTED_SHA", "COLLECTOR_RATE_OPERATIONALLY_VALIDATED",
)


def derive_window(rows):
    """FIRST/LAST venue GET from the rows' own receipts. Absent if no row
    carries one -- never a zero-length window standing in for absence."""
    stamps = [r["RECEIPT_UTC"] for r in rows if r.get("RECEIPT_UTC")]
    if not stamps:
        return NOT_IDENTIFIED, NOT_IDENTIFIED
    return min(stamps), max(stamps)


def read_rows(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines()
            if l.strip()]


def file_sha256(path):
    p = Path(path)
    if not p.exists():
        return NOT_IDENTIFIED
    return hashlib.sha256(p.read_bytes()).hexdigest()


def reharvest(evidence_dir, run_id, root=".."):
    """Re-run the frozen overlap audit over one run's sealed evidence."""
    d = Path(evidence_dir)
    report = json.loads((d / "confirm_report.json").read_text())
    history = json.loads((d / "run_history.json").read_text())
    rows = read_rows(d / "confirm_rows.jsonl")
    first, last = derive_window(rows)

    known = sorted(VD.venue_touching_workflows(root))
    audit = VD.overlap_harvest(
        None if first == NOT_IDENTIFIED else first,
        None if last == NOT_IDENTIFIED else last,
        history.get("workflow_runs", []), known, len(rows),
        self_run_id=run_id,
        venue_windows=history.get("VENUE_REQUEST_WINDOWS") or {},
        more_pages=history.get("MORE_PAGES_AVAILABLE"),
        pages_fetched=history.get("HISTORY_PAGES_FETCHED"))

    out = {
        "SOURCE_RUN_ID": str(run_id),
        "SOURCE_DATA_OUTPUT_SHA": _sealed_data_sha(d),
        "SOURCE_CONFIRM_ROWS_SHA": file_sha256(d / "confirm_rows.jsonl"),
        "DERIVATION_TYPE": DERIVATION_TYPE,
        "NO_NEW_VENUE_REQUESTS": "YES",
        "THIS_IS_NOT": THIS_IS_NOT,
        "WHY_NOT_A_SECOND_RUN": WHY_NOT_A_SECOND_RUN,
        "THIS_MODULE_CONTACTS_NOTHING": True,

        "WINDOW_SOURCE": "SEALED_CONFIRM_ROWS_RECEIPT_UTC",
        "WINDOW_DERIVED_NOT_SYNTHESISED": True,
        "WINDOW_EXTREMA_WIDEN_NEVER_NARROW": True,
        "FIRST_VENUE_GET_TIME": first,
        "LAST_VENUE_GET_TIME": last,
        "VENUE_REQUESTS": len(rows),
        "REPORT_CARRIED_THE_WINDOW": (
            "YES" if report.get("FIRST_VENUE_GET_TIME") else "NO"),
        "WHY_THE_SEALED_AUDIT_WAS_EMPTY": (
            NOT_IDENTIFIED if report.get("FIRST_VENUE_GET_TIME") else
            "the sealed confirm_report.json carried no FIRST/LAST venue GET "
            "time, so the harvest's guard skipped the audit and sealed an "
            "empty overlap object; AUDIT_SKIPPED, not NO_OVERLAP"),

        "AUDIT_EXECUTED": audit["AUDIT_EXECUTED"],
        "OVERLAP_AUDIT_STATUS": audit["OVERLAP_AUDIT_STATUS"],
        "VENUE_REQUEST_WINDOWS": audit["VENUE_REQUEST_WINDOWS"],
        "EVIDENCE_WINDOW": audit.get("EVIDENCE_WINDOW", NOT_IDENTIFIED),

        "STAGE_1_JOB_INTERVAL_CANDIDATES": audit.get(
            "STAGE_1_JOB_INTERVAL_CANDIDATES", NOT_IDENTIFIED),
        "DIRECT_OVERLAP_COUNT": audit.get("DIRECT_OVERLAP_COUNT",
                                          NOT_IDENTIFIED),
        "DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW": audit.get(
            "DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW", []),
        "POSSIBLE_DIRECT_WORKFLOW_OVERLAP": audit.get(
            "POSSIBLE_DIRECT_WORKFLOW_OVERLAP", NOT_IDENTIFIED),
        "CONFIRMED_DIRECT_REQUEST_OVERLAP": audit.get(
            "CONFIRMED_DIRECT_REQUEST_OVERLAP", NOT_IDENTIFIED),
        "DIRECT_RUN_HISTORY_COVERAGE_COMPLETE": audit.get(
            "DIRECT_RUN_HISTORY_COVERAGE_COMPLETE", NOT_IDENTIFIED),
        "DIRECT_CONFLICT_STARTED_DURING_RUN": audit.get(
            "DIRECT_CONFLICT_STARTED_DURING_RUN", NOT_IDENTIFIED),
        "DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN": audit[
            "DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
        "KNOWN_DIRECT_RUNS_CONSIDERED": audit.get(
            "KNOWN_DIRECT_RUNS_CONSIDERED", NOT_IDENTIFIED),
        "WHY_NOT_IDENTIFIED": audit.get("WHY_NOT_IDENTIFIED", NOT_IDENTIFIED),

        # UNCHANGED, AND UNCHANGEABLE FROM HERE.
        "OPERATIONAL_MEASUREMENTS_CARRIED_VERBATIM": {
            k: report.get(k, NOT_IDENTIFIED) for k in CARRIED_VERBATIM},
        "OPERATIONAL_MEASUREMENTS_NOT_RE_DERIVED": True,
        "INDIRECT_BETTOR_PMUS_LOAD_ISOLATION": "NOT_ESTABLISHED",
        "ABSOLUTE_VENUE_ISOLATION": "REFUSED_NOT_KNOWABLE",
    }
    return out


def _sealed_data_sha(d):
    try:
        return json.loads(
            (d / "provenance.json").read_text()).get("DATA_OUTPUT_SHA",
                                                     NOT_IDENTIFIED)
    except Exception:
        return NOT_IDENTIFIED


def render(a):
    L = []
    for k in ("SOURCE_RUN_ID", "SOURCE_DATA_OUTPUT_SHA", "DERIVATION_TYPE",
              "NO_NEW_VENUE_REQUESTS", "FIRST_VENUE_GET_TIME",
              "LAST_VENUE_GET_TIME", "VENUE_REQUESTS", "AUDIT_EXECUTED",
              "OVERLAP_AUDIT_STATUS", "VENUE_REQUEST_WINDOWS",
              "STAGE_1_JOB_INTERVAL_CANDIDATES", "DIRECT_OVERLAP_COUNT",
              "POSSIBLE_DIRECT_WORKFLOW_OVERLAP",
              "CONFIRMED_DIRECT_REQUEST_OVERLAP",
              "DIRECT_RUN_HISTORY_COVERAGE_COMPLETE",
              "DIRECT_CONFLICT_STARTED_DURING_RUN",
              "DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN",
              "INDIRECT_BETTOR_PMUS_LOAD_ISOLATION"):
        v = a[k]
        L.append("%-52s = %s" % (k, v if isinstance(v, (str, int)) else
                                 json.dumps(v, sort_keys=True)))
    return "\n".join(L)


def _cli():                                                   # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--root", default="..")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rep = reharvest(a.evidence_dir, a.run_id, a.root)
    Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=True))
    print(render(rep))


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
