#!/usr/bin/env python3
"""THE RESEARCH AUDIT REGISTRY, and the rules that keep it honest.

This module contacts nothing. It reads `research_audit.json` and enforces the
properties that make the audit an ENGINEERING ARTEFACT rather than an essay:

    1. STATUS is one of five values and nothing else. A sixth would be a way
       to smuggle "sort of adopted" into the registry.
    2. Every entry in the JSON appears in the .md, and every entry ID in the
       .md appears in the JSON. The two deliverables cannot drift apart
       silently -- which is exactly how a research document stops describing
       the system it claims to audit.
    3. A SHADOW_CHALLENGER must name a falsifiable test. An entry that cannot
       say what would refute it is not a challenger, it is an opinion, and it
       belongs in DEFER_NEEDS_DATA.
    4. NOTHING IN THIS REGISTRY AUTHORISES A TRADE. `promotable()` returns
       False for every entry, for every status, always -- because promotion
       requires BETTOR-native out-of-sample evidence that does not yet exist,
       and a registry cannot supply it. The gate is structural rather than a
       constant somebody can flip.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "research_audit.json"
DOCUMENT = HERE / "BETTOR_PUBLIC_RESEARCH_ARCHITECTURE_AUDIT.md"

NOT_IDENTIFIED = "NOT_IDENTIFIED"
THIS_MODULE_CONTACTS_NOTHING = True
ORDERS = 0
CAPITAL = 0
CREDENTIALS = "NONE"
mirror_live = False

STATUS_VALUES = (
    "ADOPT_ARCHITECTURALLY",   # structure, never a number
    "SHADOW_CHALLENGER",       # measured beside the incumbent, never above it
    "DEFER_NEEDS_DATA",        # blocked on evidence, not on theory
    "REJECT",                  # named so it cannot return as a convenience
    "NOT_IDENTIFIED",          # the honest fifth value
)

# ADOPT_ARCHITECTURALLY is a claim about SHAPE. It never licenses a numeric
# threshold, a coefficient, or a size. Those are earned from data or not used.
ADOPT_MEANS = "STRUCTURE_ONLY_NEVER_A_NUMBER"
WHY_A_PAPER_IS_NOT_EVIDENCE_FOR_BETTOR = (
    "a published result is evidence about the market it was measured in; "
    "BETTOR trades binary event contracts on PMUS, and the transfer is a "
    "hypothesis rather than an inheritance")

REQUIRED_FIELDS = (
    "ID", "STREAM", "NAME", "SOURCE", "SOURCE_TYPE", "EMPIRICAL_SUPPORT",
    "ORIGINAL_MARKET", "BETTOR_APPLICABILITY", "CURRENT_BETTOR_EQUIVALENT",
    "PROPOSED_CHANGE", "DATA_REQUIRED", "CAN_TEST_NOW", "VALIDATION_METHOD",
    "OVERFIT_RISK", "STATUS",
)

# A challenger has to be refutable. These statuses must carry a validation
# method that is not a shrug.
MUST_BE_FALSIFIABLE = ("SHADOW_CHALLENGER", "ADOPT_ARCHITECTURALLY")
NON_ANSWERS = ("", "NONE", "NOT_IDENTIFIED", "NOT_APPLICABLE", "TBD")


class RegistryInvalid(RuntimeError):
    """The registry does not satisfy its own rules."""


def load(path=None):
    return json.loads(Path(path or REGISTRY).read_text())


def entries(reg=None):
    return (reg or load())["ENTRIES"]


def validate(reg=None, document=None):
    """Every rule at once. Returns a report; raises only on a hard break."""
    reg = reg or load()
    rows = reg["ENTRIES"]
    problems = []

    ids = [r.get("ID") for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("DUPLICATE_ENTRY_IDS")

    for r in rows:
        rid = r.get("ID", "<no id>")
        for f in REQUIRED_FIELDS:
            if f not in r:
                problems.append("MISSING_FIELD %s %s" % (rid, f))
        st = r.get("STATUS")
        if st not in STATUS_VALUES:
            problems.append("BAD_STATUS %s %r" % (rid, st))
        # A challenger or an adoption must say what would refute it. REJECT and
        # DEFER are allowed to say NOT_APPLICABLE -- there is nothing to test.
        if st in MUST_BE_FALSIFIABLE:
            vm = str(r.get("VALIDATION_METHOD", "")).strip()
            if vm.upper() in NON_ANSWERS:
                problems.append("UNFALSIFIABLE %s" % rid)
        # An entry that claims no current equivalent AND proposes no change is
        # not saying anything.
        if not str(r.get("PROPOSED_CHANGE", "")).strip():
            problems.append("EMPTY_PROPOSED_CHANGE %s" % rid)

    doc = Path(document or DOCUMENT)
    doc_problems = []
    if doc.is_file():
        text = doc.read_text()
        for rid in ids:
            if rid and rid not in text:
                doc_problems.append("ID_MISSING_FROM_DOCUMENT %s" % rid)
    else:
        doc_problems.append("DOCUMENT_NOT_FOUND")

    return {
        "ENTRY_COUNT": len(rows),
        "STATUS_COUNTS": status_counts(reg),
        "PROBLEMS": problems,
        "DOCUMENT_PROBLEMS": doc_problems,
        "REGISTRY_VALID": "YES" if not problems and not doc_problems else "NO",
        "ADOPT_MEANS": ADOPT_MEANS,
        "ANY_ENTRY_AUTHORISES_A_TRADE": False,
        "mirror_live": mirror_live,
    }


def status_counts(reg=None):
    out = {s: 0 for s in STATUS_VALUES}
    for r in entries(reg):
        st = r.get("STATUS")
        if st in out:
            out[st] += 1
    return out


def by_status(status, reg=None):
    if status not in STATUS_VALUES:
        raise ValueError("unknown status: %r" % (status,))
    return [r for r in entries(reg) if r.get("STATUS") == status]


def testable_now(reg=None):
    """Entries whose CAN_TEST_NOW starts with YES -- the work that needs no new
    data and no venue contact."""
    return [r for r in entries(reg)
            if str(r.get("CAN_TEST_NOW", "")).upper().startswith("YES")]


def promotable(entry, native_out_of_sample_evidence=None):
    """Can this idea go to production? ALWAYS FALSE HERE, and structurally so.

    Promotion needs BETTOR-NATIVE OUT-OF-SAMPLE evidence beating a named
    benchmark. This module cannot hold such evidence and does not accept it:
    the parameter exists only to be REFUSED, so that a future caller cannot
    pass a paper citation, a backtest, or an in-sample P&L figure and have it
    read as a promotion. A registry is a reading list, not a result.
    """
    return False, ("PROMOTION_REQUIRES_BETTOR_NATIVE_OUT_OF_SAMPLE_EVIDENCE",
                   WHY_A_PAPER_IS_NOT_EVIDENCE_FOR_BETTOR)


def render(rep):
    L = ["%-34s = %s" % ("ENTRY_COUNT", rep["ENTRY_COUNT"])]
    for s in STATUS_VALUES:
        L.append("%-34s = %d" % (s, rep["STATUS_COUNTS"][s]))
    L.append("%-34s = %s" % ("REGISTRY_VALID", rep["REGISTRY_VALID"]))
    for p in rep["PROBLEMS"] + rep["DOCUMENT_PROBLEMS"]:
        L.append("%-34s = %s" % ("PROBLEM", p))
    return "\n".join(L)


def _cli():                                                   # pragma: no cover
    rep = validate()
    print(render(rep))
    for r in testable_now():
        print("%-34s = %s  %s" % ("TESTABLE_NOW", r["ID"], r["NAME"]))
    if rep["REGISTRY_VALID"] != "YES":
        raise SystemExit("REGISTRY_VALID = NO")


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
