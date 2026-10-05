"""THE REFUSAL TAXONOMY (R30A incident: coverage -> trade starvation). Pure.

ONE ANSWER TO "WAS THIS A SOFTWARE PROBLEM OR AN ECONOMIC DECISION?" for every
refusal code the code base can write. The owner (2026-10-04): "I specifically
want to know whether we have hundreds of valid candidates upstream and only a
few are reaching Derek/Archer/Xavier", and "there must be no generic
'unsupported' bucket if a more precise reason can be identified". Before this
module every report re-derived the split on its own -- a keyword list in
lost_opportunity.classify, a CASE expression in each research SQL file, a
hand-picked set in each agent page -- and they disagreed (the same code was
CAPABILITY in one place and FRESHNESS in another; codes no list named fell
into a generic bucket).

THE TWO CLASSES:
  SOFTWARE   the software could not establish something it needs: a
             CAPABILITY it lacks (an unsupported market family, no model, a
             missing schema), DATA it does not have or could not read (an
             unreadable book, an absent probability), a MAPPING it could not
             make (fixture, contract, outcome), SETTLEMENT equivalence it
             could not prove from cited terms, FRESHNESS_PLUMBING that did not
             deliver timely evidence (the 30 s rule is a deliberate gate; a
             STALE refusal says the plumbing did not deliver inside it), or an
             INTEGRITY check (idempotency, malformed or contradictory records).
             Every SOFTWARE refusal is engineering work: a candidate refused
             for one of these never had its economics judged.
  ECONOMIC   the candidate was judged on its economics or by a deliberate
             risk control: EDGE (gross edge against the threshold), EV
             (expected value after fees), PRICE (price structure), DEPTH
             (displayed liquidity within the limit) or a RISK_RAIL (caps,
             exposure, kill switches and owner switches, the same-contract
             rule, capital authorization). An ECONOMIC refusal is the system
             working as designed; no threshold here is ever moved to change a
             count.

THE STAGE is where in the funnel the refusal is raised (INGESTION ->
NORMALIZATION -> EVENT_IDENTITY -> VENUE_MAPPING -> MARKET_FAMILY ->
SETTLEMENT_COMPATIBILITY -> PROBABILITY -> VENUE_BOOK -> FRESHNESS -> EV ->
AGENT_EVALUATION -> RISK_ADMISSION -> ORDER -> FILL -> MANAGEMENT ->
ACCOUNTING), or OUT_OF_FUNNEL for governance / learning / chat refusals that
never stand between a candidate and a trade.

THE TABLE (`refusal_taxonomy_table.TABLE`) classifies EVERY `R_*` string
constant defined anywhere under sportsassets/ (tests/test_refusal_taxonomy.py
parses every module, enumerates them and FAILS on any code that is neither
classified nor explicitly listed as not-a-refusal), plus the literal codes
that reach decision, order, collector or review records without a constant
(INLINE). A code the table does not know is reported as UNCLASSIFIED -- by
name and count, never guessed into a class.

DECISION-LEVEL CLASS (`decision_class`): a refusal with ANY software code is
REJECTED_SOFTWARE (its economics were never established -- or were
established on inputs the software could not vouch for); one whose every
code is economic is REJECTED_ECONOMIC; one with an unknown code and no
software code is REJECTED_UNCLASSIFIED. ENTER is ENTER.

Pure: no I/O, imports nothing from the paper, execution or funded paths, so
the lost-opportunity layer, the command centre and research tooling can all
read the one table.
"""
from __future__ import annotations

import re

from . import refusal_taxonomy_table as T

VERSION = "REFUSAL_TAXONOMY_V1"

SOFTWARE = "SOFTWARE"
ECONOMIC = "ECONOMIC"
CLASSES = (SOFTWARE, ECONOMIC)
UNCLASSIFIED = "UNCLASSIFIED"

FAMILIES = {
    SOFTWARE: ("CAPABILITY", "DATA", "MAPPING", "SETTLEMENT",
               "FRESHNESS_PLUMBING", "INTEGRITY"),
    ECONOMIC: ("EDGE", "EV", "PRICE", "DEPTH", "RISK_RAIL"),
}
STAGES = ("INGESTION", "NORMALIZATION", "EVENT_IDENTITY", "VENUE_MAPPING",
          "MARKET_FAMILY", "SETTLEMENT_COMPATIBILITY", "PROBABILITY",
          "VENUE_BOOK", "FRESHNESS", "EV", "AGENT_EVALUATION",
          "RISK_ADMISSION", "ORDER", "FILL", "MANAGEMENT", "ACCOUNTING",
          "OUT_OF_FUNNEL")

#: the decision-level outcomes of the per-agent receipt
ENTER = "ENTER"
REJECTED_SOFTWARE = "REJECTED_SOFTWARE"
REJECTED_ECONOMIC = "REJECTED_ECONOMIC"
REJECTED_UNCLASSIFIED = "REJECTED_UNCLASSIFIED"

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def normalize(code) -> str | None:
    """The code token of a recorded refusal: the leading identifier, without
    a ':detail' suffix or trailing prose ("MODEL_RUN_RAISED:ValueError" ->
    "MODEL_RUN_RAISED"). None for an empty value."""
    if code is None:
        return None
    m = _TOKEN.match(str(code).strip())
    return m.group(0) if m else None


def lookup(code) -> tuple | None:
    """(class, family, stage) of a code, or None when the table does not
    know it (or it is a declared non-refusal)."""
    c = normalize(code)
    if c is None:
        return None
    return T.TABLE.get(c)


def classify(code) -> dict:
    """One code's classification, or UNCLASSIFIED by name. Never guesses."""
    c = normalize(code)
    row = T.TABLE.get(c) if c is not None else None
    if row is None:
        why = ("DECLARED_NOT_A_REFUSAL_CODE: %s" % T.NOT_REFUSAL[c]
               if c in T.NOT_REFUSAL else
               "A_WRAPPER_IS_CLASSIFIED_BY_THE_CODE_IT_WRAPS: %s"
               % T.WRAPPERS[c] if c in T.WRAPPERS
               else "NOT_IN_THE_REFUSAL_TAXONOMY")
        return {"code": c, "class": UNCLASSIFIED, "family": None,
                "stage": None, "classified": False, "why": why}
    return {"code": c, "class": row[0], "family": row[1], "stage": row[2],
            "classified": True}


def classify_wrapped(code, inner) -> dict:
    """A wrapper code (`refusal_taxonomy_table.WRAPPERS`, e.g. PAPER_RISK_
    REFUSED_THE_ORDER) classified by the code it carries (`inner`), named as
    `wrapped_by`; an unknown or absent inner code is UNCLASSIFIED, never
    economic. Any other code is classified as itself."""
    c = normalize(code)
    if c not in T.WRAPPERS:
        return classify(code)
    k = classify(inner) if normalize(inner) else {
        "code": None, "class": UNCLASSIFIED, "family": None, "stage": None,
        "classified": False, "why": "THE_WRAPPER_CARRIES_NO_INNER_CODE"}
    return dict(k, wrapped_by=c)


def decision_class(verdict, refusals) -> str:
    """ENTER / REJECTED_SOFTWARE / REJECTED_ECONOMIC / REJECTED_UNCLASSIFIED
    for one decision record. A REFUSE with no code at all is UNCLASSIFIED
    (the record does not say why -- never assumed economic)."""
    if str(verdict or "").upper() == ENTER:
        return ENTER
    codes = [c for c in (normalize(x) for x in (refusals or [])) if c]
    if not codes:
        return REJECTED_UNCLASSIFIED
    classes = [(T.TABLE.get(c) or (None,))[0] for c in codes]
    if SOFTWARE in classes:
        return REJECTED_SOFTWARE
    if all(k == ECONOMIC for k in classes):
        return REJECTED_ECONOMIC
    return REJECTED_UNCLASSIFIED


def binding(refusals) -> dict | None:
    """The FIRST software code of a refusal list (what to fix first), else
    the first code. None for an empty list."""
    codes = [c for c in (normalize(x) for x in (refusals or [])) if c]
    if not codes:
        return None
    for c in codes:
        if (T.TABLE.get(c) or (None,))[0] == SOFTWARE:
            return classify(c)
    return classify(codes[0])


def summarize(counts: dict) -> dict:
    """{code: n} -> per class / family / stage totals plus the unclassified
    codes by name. Pure; for the receipt and research tooling."""
    out = {"by_class": {}, "by_family": {}, "by_stage": {},
           "unclassified": {}}
    for code, n in (counts or {}).items():
        k = classify(code)
        n = int(n or 0)
        if not k["classified"]:
            out["unclassified"][k["code"] or str(code)] = (
                out["unclassified"].get(k["code"] or str(code), 0) + n)
            out["by_class"][UNCLASSIFIED] = out["by_class"].get(
                UNCLASSIFIED, 0) + n
            continue
        out["by_class"][k["class"]] = out["by_class"].get(k["class"], 0) + n
        fk = "%s/%s" % (k["class"], k["family"])
        out["by_family"][fk] = out["by_family"].get(fk, 0) + n
        out["by_stage"][k["stage"]] = out["by_stage"].get(k["stage"], 0) + n
    return out


def describe() -> dict:
    by = {}
    for cls, fam, _st in T.TABLE.values():
        by.setdefault(cls, {}).setdefault(fam, 0)
        by[cls][fam] += 1
    return {"version": VERSION, "classes": list(CLASSES),
            "families": {k: list(v) for k, v in FAMILIES.items()},
            "stages": list(STAGES), "codes": len(T.TABLE),
            "declared_not_refusals": len(T.NOT_REFUSAL),
            "wrappers": sorted(T.WRAPPERS),
            "codes_by_class_and_family": by}
