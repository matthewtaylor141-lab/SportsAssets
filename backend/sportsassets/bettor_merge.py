"""IS THERE A MERGE, AND ON WHICH VENUE? §12's determination, answered.

Owner directive, "CONTINUE THE BUILD" §12:

    "Do NOT pretend merge exists on PMUS until the venue mechanism is
    confirmed. First determine: PMUS_NATIVE_MERGE_AVAILABLE =
    YES/NO/NOT_IDENTIFIED... Never create a fictional venue action."

THE ANSWER IS DIFFERENT ON THE TWO VENUES, AND THAT IS THE FINDING.
Answering it once, for "the venue", would blend retail and
institutional -- which the standing separation forbids for exactly
this kind of reason: a capability confirmed on one is not evidence
about the other.

────────────────────────────────────────────────────────────────────
RETAIL: NO MERGE CALL, BECAUSE THE VENUE ALREADY NETS.

`docs/rn1-two-sided-design.md` works the arithmetic through and lands
on a mechanism, not an assumption:

    buy YES at 0.48, buy NO at 0.51        -> +1 YES, +1 NO, -0.99
    buying NO at 0.51 IS selling YES at 0.49 -> FLAT, -0.48 +0.49

Both make +0.01. The second realises it AT THE SECOND FILL and returns
the capital immediately, where the first waits for settlement. The doc
states the consequence plainly: "That is the merge engine the case
study describes... except the venue does it for us automatically
instead of us having to call a merge."

So on retail the answer is NO -- no merge action exists -- and the
economically equivalent lifecycle is already what happens. §12's "if
NO: model the economically equivalent lifecycle honestly" is satisfied
by recording the pair as realised at the second fill rather than by
inventing a redemption step.

THIS IS ALSO THE CONFOUND §3 WARNED ABOUT. "Never let complement
acquisition look like a direct sell merely because the venue's
implementation nets economically." The netting is real at the cash
level and it is NOT a reason to record the trade as a YES sale: it was
a NO acquisition, with a NO fill, NO fees and NO queue. The ledger
keeps the leg that was actually traded, and the auto-netting is a fact
about settlement mechanics, not about what we did.

INSTITUTIONAL (PMUS): NOT_IDENTIFIED, AND THE SILENCE PROVES NOTHING.

`pmx_institutional.py` contains no merge, redeem or split path. That
is not evidence the venue lacks one: the module is MARKET DATA ONLY,
ORDER_SUBMISSION_IMPLEMENTATION = NONE. A module that cannot place an
order would not contain a merge call whether or not the venue offers
it, so its silence is uninformative in both directions.

Nothing else in the repository records an observed PMUS merge
capability -- no endpoint, no documented mechanism, no executed
example. The answer is therefore NOT_IDENTIFIED, which is different
from NO and must not be written as NO: "no mechanism has been
observed" and "the venue has no mechanism" are different claims, and
only the first is supported.
────────────────────────────────────────────────────────────────────

WHAT THE LEDGER MUST STILL DO EITHER WAY. §12: "the portfolio ledger
must still recognize MATCHED INVENTORY as economically distinct from
residual directional inventory." That holds whether or not a merge
call exists, and `bettor_inventory` already enforces it -- matched and
residual capital are separate figures and the legs are never netted.
"""

from __future__ import annotations

YES = "YES"
NO = "NO"
NOT_IDENTIFIED = "NOT_IDENTIFIED"

RETAIL = "RETAIL"
INSTITUTIONAL = "INSTITUTIONAL"

# ── the determinations, per venue ────────────────────────────────────

RETAIL_NATIVE_MERGE_AVAILABLE = NO
INSTITUTIONAL_NATIVE_MERGE_AVAILABLE = NOT_IDENTIFIED

# The name §12 asked for, answered for the venue it names.
PMUS_NATIVE_MERGE_AVAILABLE = INSTITUTIONAL_NATIVE_MERGE_AVAILABLE

DETERMINATIONS = {
    RETAIL: {
        "PMUS_NATIVE_MERGE_AVAILABLE": RETAIL_NATIVE_MERGE_AVAILABLE,
        "mechanism": "VENUE_AUTO_NETS_AT_FILL",
        "evidence": "docs/rn1-two-sided-design.md",
        "why": (
            "buying NO at 0.51 IS selling YES at 0.49 on this venue, so "
            "a second-leg fill leaves the book FLAT rather than holding "
            "both legs. The pair's profit is realised at that fill and "
            "the capital returns immediately -- the same economics the "
            "merge case study describes, without a merge call"),
        "equivalentLifecycle": (
            "record the pair as REALISED AT THE SECOND FILL. No "
            "redemption step is invented, because none occurs"),
        "doesNotLicense": (
            "recording the complement acquisition as a sale of the "
            "first leg. The netting is real at the cash level and says "
            "nothing about what was traded: a NO buy had a NO fill, NO "
            "fees and NO queue position, and the ledger keeps the leg "
            "that was actually traded"),
    },
    INSTITUTIONAL: {
        "PMUS_NATIVE_MERGE_AVAILABLE": INSTITUTIONAL_NATIVE_MERGE_AVAILABLE,
        "mechanism": NOT_IDENTIFIED,
        "evidence": "no observed endpoint, mechanism or executed example",
        "why": (
            "pmx_institutional.py contains no merge, redeem or split "
            "path, and that is uninformative: the module is market "
            "data only, ORDER_SUBMISSION_IMPLEMENTATION = NONE. A "
            "module that cannot place an order would not carry a merge "
            "call whether or not the venue offers one"),
        "notTheSameAsNo": (
            "'no mechanism has been observed' and 'the venue has no "
            "mechanism' are different claims. Only the first is "
            "supported, so the answer is NOT_IDENTIFIED and MERGE stays "
            "blocked rather than being recorded as unavailable"),
        "whatWouldSettleIt": (
            "venue documentation of a merge or redemption endpoint, or "
            "an observed execution. Neither exists in this repository"),
    },
}

SILENCE_IS_NOT_EVIDENCE = (
    "the absence of a merge call in a market-data-only module is not "
    "evidence about the venue. Reading it as NO would manufacture a "
    "venue fact out of our own missing order path")

MATCHED_IS_STILL_DISTINCT = (
    "whether or not a merge call exists, the ledger recognises MATCHED "
    "inventory as economically distinct from RESIDUAL directional "
    "inventory. Matched capital is locked and earning; residual capital "
    "carries outcome risk. bettor_inventory keeps them apart and never "
    "nets the legs")

NO_FICTIONAL_ACTION = (
    "MERGE remains blocked on the institutional venue. It is never "
    "modelled as though it had succeeded, and no capital is released "
    "by an action the venue has not been shown to offer")


def availability(venue=INSTITUTIONAL) -> dict:
    """Is there a native merge on this venue, and what settles it."""
    row = DETERMINATIONS.get(venue)
    if row is None:
        return {"venue": venue, "PMUS_NATIVE_MERGE_AVAILABLE": NOT_IDENTIFIED,
                "why": "unknown venue"}
    return {"venue": venue, **row,
            "silenceIsNotEvidence": SILENCE_IS_NOT_EVIDENCE,
            "matchedIsStillDistinct": MATCHED_IS_STILL_DISTINCT}


def merge_permitted(venue=INSTITUTIONAL) -> dict:
    """May a MERGE action be modelled as releasing capital?"""
    row = DETERMINATIONS.get(venue) or {}
    answer = row.get("PMUS_NATIVE_MERGE_AVAILABLE", NOT_IDENTIFIED)
    if answer == YES:
        return {"venue": venue, "permitted": True,
                "shadowOnly": True,
                "why": ("the mechanism is confirmed; implement it in "
                        "SHADOW first")}
    if answer == NO:
        return {
            "venue": venue, "permitted": False,
            "why": ("no merge action exists here because the venue "
                    "already nets at fill. The equivalent lifecycle is "
                    "recorded instead of a fictional redemption"),
            "equivalentLifecycle": row.get("equivalentLifecycle"),
        }
    return {"venue": venue, "permitted": False,
            "why": ("no merge mechanism has been observed, which is not "
                    "the same as the venue lacking one. MERGE stays "
                    "blocked"),
            "noFictionalAction": NO_FICTIONAL_ACTION}


# ══════════════════════════════════════════════════════════════════════
# §12. THE CAPITAL RECYCLING CHAIN, WHICH IS A DIFFERENT QUESTION
#
# Owner directive:
#
#     "Once merge/netting is legitimately available: PAIR COMPLETE ->
#     MERGE / NET -> RELEASE CAPITAL -> RECORD LOCKED PAIR PNL ->
#     CAPITAL RETURNS TO ALLOCATOR. This is a core RN1/Ferrari lesson."
#
# `merge_permitted` answers "is there a merge ACTION to call". That is
# NOT the same question as "does capital come back", and reading the
# first as the second gets RETAIL exactly backwards: there is no merge
# call on retail AND capital recycles immediately, because the venue
# nets at the second fill. An allocator that consulted merge_permitted
# alone would conclude that retail capital never returns, which is the
# opposite of the truth.
#
# So the chain is answered per step, per venue, and the two questions
# are kept apart by name.
# ══════════════════════════════════════════════════════════════════════

RECYCLING_CHAIN = (
    "PAIR_COMPLETE",
    "MERGE_OR_NET",
    "RELEASE_CAPITAL",
    "RECORD_LOCKED_PAIR_PNL",
    "CAPITAL_RETURNS_TO_ALLOCATOR",
)

MERGE_CALL_IS_NOT_CAPITAL_RECYCLING = (
    "'is there a merge ACTION' and 'does capital come back' are "
    "different questions. On RETAIL the answers are NO and YES: no "
    "merge call exists and the capital returns at the second fill, "
    "because the venue nets. An allocator reading merge availability as "
    "capital availability would conclude retail capital never returns, "
    "which is the opposite of the truth")

CHAIN_BY_VENUE = {
    RETAIL: {
        "PAIR_COMPLETE": "AVAILABLE",
        "MERGE_OR_NET": "AVAILABLE_AS_AUTOMATIC_NETTING_NO_CALL",
        "RELEASE_CAPITAL": "AVAILABLE_AT_SECOND_FILL",
        "RECORD_LOCKED_PAIR_PNL": "AVAILABLE",
        "CAPITAL_RETURNS_TO_ALLOCATOR": "AVAILABLE",
        "CAPITAL_RECYCLING_AVAILABLE": YES,
        "why": ("the venue nets at the second fill, so the pair's "
                "profit is realised there and the capital returns "
                "immediately. No merge call is involved and none is "
                "invented"),
    },
    INSTITUTIONAL: {
        "PAIR_COMPLETE": "AVAILABLE",
        # The chain breaks here and everything after it inherits.
        "MERGE_OR_NET": NOT_IDENTIFIED,
        "RELEASE_CAPITAL": NOT_IDENTIFIED,
        "RECORD_LOCKED_PAIR_PNL": "AVAILABLE_INDEPENDENTLY_OF_MERGE",
        "CAPITAL_RETURNS_TO_ALLOCATOR": NOT_IDENTIFIED,
        "CAPITAL_RECYCLING_AVAILABLE": NOT_IDENTIFIED,
        "brokenAt": "MERGE_OR_NET",
        "why": ("no merge or netting mechanism has been observed, so "
                "whether completing a pair returns capital is "
                "NOT_IDENTIFIED. It is not zero and it is not NO: "
                "either would be a claim about the venue we have not "
                "established"),
        "pnlIsStillRecordable": (
            "RECORD_LOCKED_PAIR_PNL does not wait on the merge. Matched "
            "inventory has a locked economic outcome whether or not the "
            "venue lets us realise it early, and §12 requires "
            "matched-pair accounting to stay independent of merge "
            "execution"),
    },
}

FERRARI_LESSON = (
    "capital that cannot be recycled is capital that keeps earning the "
    "residual's risk instead of the next pair's margin. Ferrari's pair "
    "machine worked; what it could not do was turn completed pairs back "
    "into deployable capital fast enough to stop the residual book "
    "growing. A chain that breaks at MERGE_OR_NET is the same failure "
    "with a different name")


def capital_recycling(venue=INSTITUTIONAL) -> dict:
    """Does completing a pair return capital on this venue, step by step.

    Distinct from `merge_permitted`, which answers whether a merge
    ACTION exists. See MERGE_CALL_IS_NOT_CAPITAL_RECYCLING.
    """
    row = CHAIN_BY_VENUE.get(venue)
    if row is None:
        return {
            "venue": venue,
            "CAPITAL_RECYCLING_AVAILABLE": NOT_IDENTIFIED,
            "why": "unknown venue",
            "chain": list(RECYCLING_CHAIN),
        }
    broken = [s for s in RECYCLING_CHAIN if row.get(s) == NOT_IDENTIFIED]
    return {
        "venue": venue,
        "chain": list(RECYCLING_CHAIN),
        "byStep": {s: row.get(s, NOT_IDENTIFIED) for s in RECYCLING_CHAIN},
        "CAPITAL_RECYCLING_AVAILABLE": row["CAPITAL_RECYCLING_AVAILABLE"],
        "stepsNotIdentified": broken,
        "mergeCallIsNotCapitalRecycling": MERGE_CALL_IS_NOT_CAPITAL_RECYCLING,
        "matchedIsStillDistinct": MATCHED_IS_STILL_DISTINCT,
        "noFictionalAction": NO_FICTIONAL_ACTION,
        "ferrariLesson": FERRARI_LESSON,
        "why": row.get("why"),
    }


def describe() -> dict:
    return {
        "PMUS_NATIVE_MERGE_AVAILABLE": PMUS_NATIVE_MERGE_AVAILABLE,
        "retail": RETAIL_NATIVE_MERGE_AVAILABLE,
        "institutional": INSTITUTIONAL_NATIVE_MERGE_AVAILABLE,
        "whyTheyDiffer": (
            "they are different venues. A capability confirmed on one "
            "is not evidence about the other, and answering once for "
            "'the venue' would blend retail and institutional"),
        "silenceIsNotEvidence": SILENCE_IS_NOT_EVIDENCE,
        "matchedIsStillDistinct": MATCHED_IS_STILL_DISTINCT,
        "noFictionalAction": NO_FICTIONAL_ACTION,
        "recyclingChain": list(RECYCLING_CHAIN),
        "mergeCallIsNotCapitalRecycling": MERGE_CALL_IS_NOT_CAPITAL_RECYCLING,
        "capitalRecycling": {v: capital_recycling(v)
                             for v in (RETAIL, INSTITUTIONAL)},
        "ferrariLesson": FERRARI_LESSON,
    }
