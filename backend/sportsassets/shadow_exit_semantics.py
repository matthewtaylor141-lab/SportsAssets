"""WHAT EXIT_RULE_HORIZON ALREADY SAYS, AND WHAT IT DOES NOT SAY.

Owner directive 2026-09-20 §2:

    "The exit implementation must execute the EXISTING FROZEN X1 rule...
    First write down exactly what EXIT_RULE_HORIZON already means in the
    frozen X1 specification. Then implement that literal rule. If the
    frozen declaration is insufficient to determine WHEN TO EXIT, WHAT
    ACTION TO TAKE, WHAT PRICE TO USE, HOW MUCH QUANTITY TO EXIT, stop
    and name the missing semantics. Do not fill them in after seeing
    results."

THE FROZEN TEXT, VERBATIM, from shadow_experiment_registry:

    EXIT_RULE_HORIZON = (
        "exit at the frozen horizon by marketable reconstruction against
        the book observed at that instant; no discretionary hold, no
        averaging down, no re-entry inside the horizon")

    X1.horizon            = "60S"
    X1.target             = "mid at +60s versus mid at arrival"
    X1.latency_assumption = "SCENARIO 250ms from signal to simulated
                             arrival, charged against the book observed
                             at arrival rather than at decision"

TWO OF THE FOUR QUESTIONS ARE ANSWERED. TWO ARE NOT, AND THIS MODULE
REFUSES TO ANSWER THEM. The exit trigger is therefore NOT implemented
here; what is implemented is the lifecycle ledger that will carry it,
and a declaration of the blockage that a later authorisation can lift
without anyone having to reconstruct this reasoning.

THIS IS NOT A COUNSEL OF PERFECTION. The missing pieces are not
stylistic. Each one, chosen differently, changes the realised P&L of
the first cohort -- and the first cohort is already known to be
negative. Choosing them now is exactly the act §2 forbids.
"""

from __future__ import annotations

from . import shadow_experiment_registry as reg

ANSWERED = "ANSWERED_BY_THE_FROZEN_SPECIFICATION"
MISSING = "MISSING_FROM_THE_FROZEN_SPECIFICATION"
INCONSISTENT = "FROZEN_SPECIFICATION_AND_IMPLEMENTATION_DISAGREE"

BLOCKED = "BLOCKED_PENDING_EXIT_SEMANTICS_AUTHORISATION"
READY = "READY"


# ── the four questions §2 requires be answerable ─────────────────────

QUESTIONS = (
    {
        "question": "WHEN TO EXIT",
        "status": ANSWERED,
        "frozen_basis": (
            "'exit at the frozen horizon' with horizon = '60S'; the "
            "anchor is ARRIVAL, stated twice -- target is 'mid at +60s "
            "versus mid at ARRIVAL' and the latency assumption charges "
            "value 'against the book observed at ARRIVAL rather than at "
            "decision'"),
        "literal_rule": "exit_at = modeled_arrival_timestamp + 60 seconds",
        "caveat": (
            "THE IMPLEMENTED MARKOUT DISAGREES. "
            "shadow_experimental_markouts.target_at anchors on "
            "decision_timestamp, not arrival. The gap is the 250ms "
            "modelled arrival latency, so the numbers barely move -- but "
            "the two are not the same instant, and under the open clock "
            "defect decision_timestamp currently precedes the model that "
            "produced it by ~6s. Named, not silently reconciled: "
            "re-anchoring the markout would alter evidence already "
            "recorded."),
        "caveat_status": INCONSISTENT,
    },
    {
        "question": "WHAT ACTION TO TAKE",
        "status": ANSWERED,
        "frozen_basis": (
            "'marketable reconstruction'; pairing_rule is "
            "PAIRING_RULE_NONE -- 'a single leg is taken and exited on "
            "the same leg; the complement is never bought to lock a "
            "loss' -- and cashout_rule is CASHOUT_RULE_NONE"),
        "literal_rule": (
            "SELL the same leg that was bought, on the same instrument; "
            "never buy the complement, never cash out"),
        "caveat": None,
        "caveat_status": None,
    },
    {
        "question": "WHAT PRICE TO USE",
        "status": ANSWERED,
        "frozen_basis": (
            "'by marketable reconstruction against the book observed at "
            "that instant' -- walk the BID side of the observed "
            "institutional book for the position's size, exactly as the "
            "entry walks the offer side"),
        "literal_rule": (
            "shadow.marketable_fill(SELL, qty, None, book) against the "
            "admissible book; never a mid, never a last price"),
        "caveat": None,
        "caveat_status": None,
    },
    {
        "question": "HOW MUCH QUANTITY TO EXIT",
        "status": MISSING,
        "frozen_basis": (
            "the rule says 'exit', which implies the whole position, and "
            "forbids 'discretionary hold'. It does not say what happens "
            "when the observed book cannot absorb the whole position"),
        "literal_rule": None,
        "caveat": (
            "PRODUCTION ALREADY PRODUCES PARTIALS. Every X1 entry filled "
            "partially ($920 of $1,000; $600 of $1,000) because the book "
            "was thin, and the markout path already reports exitableQty "
            "below position qty. So a partial exit is not hypothetical. "
            "The frozen rule does not say whether a partial exit is "
            "permitted at all, and if it is, what becomes of the "
            "residual: re-offered on the next admissible book, carried "
            "to settlement, or the exit refused entire. 'No "
            "discretionary hold' arguably forbids carrying it, but the "
            "rule offers no alternative disposal."),
        "caveat_status": MISSING,
    },
)


# ── the fifth gap, which §2's four questions do not name but which
#    blocks the trigger just as hard ────────────────────────────────

ADDITIONAL_GAPS = (
    {
        "name": "EXIT_BOOK_TOLERANCE",
        "status": MISSING,
        "why": (
            "'the book observed at that instant'. Direct institutional "
            "books arrive about 61s apart (P50 61.30s, P95 65.43s "
            "measured), so there is essentially NEVER a book at the "
            "exact instant. The markout path carries a declared "
            "tolerance -- max(horizon/2, 30s) -- but that tolerance "
            "belongs to the MARKOUT rule, not to the EXIT rule, and the "
            "frozen exit text declares none. Without one the exit can "
            "never fire; with one, the figure chosen decides which book "
            "prices the exit and therefore decides the realised P&L."),
        "why_it_cannot_be_chosen_now": (
            "the first cohort is known to be negative, so any tolerance "
            "picked today is a tolerance picked after seeing the result "
            "it will change"),
    },
    {
        "name": "EXIT_RETRY_ON_NO_ADMISSIBLE_BOOK",
        "status": "SUPPLIED_BY_THE_DIRECTIVE",
        "why": (
            "§4 answers this: 'If necessary book evidence is absent: "
            "EXIT_EXECUTION = NOT_IDENTIFIED. Do not interpolate an "
            "exit. Do not mark the position closed merely because the "
            "horizon expired.' That is an instruction, not a reading of "
            "the frozen spec, and it is recorded as such."),
        "why_it_cannot_be_chosen_now": None,
    },
)


def blockers() -> list:
    """The gaps that stop the exit trigger being implemented."""
    out = [q for q in QUESTIONS if q["status"] == MISSING]
    out += [g for g in ADDITIONAL_GAPS if g["status"] == MISSING]
    return out


def exit_mechanism_status() -> dict:
    """Whether the declared exit rule can be executed literally today."""
    missing = blockers()
    return {
        "X1_EXIT_RULE_FROZEN_DEFINITION": reg.EXIT_RULE_HORIZON,
        "X1_EXIT_MECHANISM_STATUS": BLOCKED if missing else READY,
        "answered": [q["question"] for q in QUESTIONS
                     if q["status"] == ANSWERED],
        "missing": [q.get("question") or q.get("name") for q in missing],
        "inconsistencies": [
            {"question": q["question"], "detail": q["caveat"]}
            for q in QUESTIONS if q.get("caveat_status") == INCONSISTENT],
        "questions": list(QUESTIONS),
        "additionalGaps": list(ADDITIONAL_GAPS),
        "why": (
            "the frozen rule determines WHEN, WHAT and WHAT PRICE, but "
            "not HOW MUCH when the book is thin, and declares no "
            "tolerance for 'the book observed at that instant'. Both "
            "gaps change realised P&L, and the first cohort's result is "
            "already known, so neither may be filled in now. The "
            "lifecycle ledger is built and live; the trigger waits."),
        "whatIsBuilt": (
            "the append-only position lifecycle ledger, the "
            "EXIT_BECAME_ELIGIBLE marker, and the COMMAND lifecycle "
            "view -- everything except the decision that needs the "
            "missing semantics"),
    }
