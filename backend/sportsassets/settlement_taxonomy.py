"""SETTLEMENT REFUSALS, SPLIT FOUR WAYS. Not every one is a proven conflict.

THE CLAIM THIS CORRECTS. I wrote that "settlement scope fails on 464 of 464
candidates" and presented it as the binding blocker, citing
`VOID_ABANDONMENT_RULE_NOT_ESTABLISHED`. That code is classified
`COULD_NOT_EVALUATE` in the lane's own table: it means the comparison could not be
MADE, not that it came out against us. I then described the whole set as settlement
incompatibility, which is a different and much stronger claim.

    A COMPARISON WE COULD NOT MAKE IS NOT A CONFLICT WE FOUND.

The distinction is not pedantic, because the two have OPPOSITE remedies:

  * an established payout conflict is a fact about the instruments. More capture
    will not dissolve it, and the market is genuinely unsuitable;
  * a missing rule is a fact about OUR EVIDENCE. Capturing the rule may resolve it
    either way, and it may well resolve in our favour.

Reporting the second as the first makes the strategy look more blocked than it is
and points the work at the wrong thing -- away from capture, which is cheap, and
toward abandoning markets that were never shown to be incompatible.

THE FOUR CLASSES, and every settlement code belongs to exactly one:

  C1 ESTABLISHED_PAYOUT_CONFLICT   both sides published a rule and the payouts
                                   differ. Evidence, and decisive against.
  C2 BOOK_RULE_NOT_HELD            the VENUE's rule is in hand; the BOOKMAKER's
                                   is not. Our capture gap.
  C3 VENUE_RULE_OR_SCOPE_NOT_HELD  the venue's own rule or market scope is not
                                   established. Their publication or our read.
  C4 OTHER_UNRESOLVED              a settlement semantic that is open for a
                                   reason none of the above names.

AND A CENSUS WITHOUT ITS WINDOW AND BUILDS IS NOT A MEASUREMENT. A refusal tally
is a statement about a set of candidates observed over an interval by a particular
build. `census_window` carries all three, and `classify_census` refuses to report
without them, because "464 candidates" with no window silently invites being read
as a standing property of the venue.
"""

from __future__ import annotations

VERSION = "SETTLEMENT_TAXONOMY_V1"

#: C1 -- both sides stated a rule and the payouts differ. This is EVIDENCE.
ESTABLISHED_PAYOUT_CONFLICT = "C1_ESTABLISHED_PAYOUT_CONFLICT"
#: C2 -- the bookmaker's side is missing. OUR capture gap.
BOOK_RULE_NOT_HELD = "C2_BOOK_RULE_NOT_HELD"
#: C3 -- the venue's own rule or the market's scope is not established.
VENUE_RULE_OR_SCOPE_NOT_HELD = "C3_VENUE_RULE_OR_SCOPE_NOT_HELD"
#: C4 -- open for some other reason, named rather than folded in.
OTHER_UNRESOLVED = "C4_OTHER_UNRESOLVED"
#: A settlement code this module does not know. Reported BY NAME, never folded.
UNCLASSIFIED = "C0_UNCLASSIFIED_SETTLEMENT_CODE"

CLASSES = (ESTABLISHED_PAYOUT_CONFLICT, BOOK_RULE_NOT_HELD,
           VENUE_RULE_OR_SCOPE_NOT_HELD, OTHER_UNRESOLVED)

#: What each class means for the work, because the classes exist to point it.
REMEDY = {
    ESTABLISHED_PAYOUT_CONFLICT: (
        "NONE on our side. The two published rules pay differently, so the "
        "instrument pair is unsuitable and more capture cannot change it. This "
        "is the only class that is decisive against the market"),
    BOOK_RULE_NOT_HELD: (
        "CAPTURE THE BOOKMAKER'S RULE. The venue's side is already in hand, so "
        "this is our evidence gap and it may resolve either way"),
    VENUE_RULE_OR_SCOPE_NOT_HELD: (
        "READ OR REQUEST THE VENUE'S RULE, or establish the market's scope. "
        "Either their publication or our read of it"),
    OTHER_UNRESOLVED: (
        "DIAGNOSE INDIVIDUALLY. Folding these into a headline count is how a "
        "capture gap gets reported as an incompatibility"),
}

#: refusal code -> class. Derived from the lane's own DECIDED /
#: COULD_NOT_EVALUATE split and then refined, because that split has two
#: buckets and the question has four.
CLASS_OF = {
    # ── C1: both sides stated a rule, and they disagree ───────────────
    "SETTLEMENT_TERMS_CONFLICT": ESTABLISHED_PAYOUT_CONFLICT,
    "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE": (
        ESTABLISHED_PAYOUT_CONFLICT),
    "OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE": ESTABLISHED_PAYOUT_CONFLICT,
    # A segment scope is a DECIDED mismatch too: the contract pays on part of
    # the fixture and this lane prices whole fixtures. Nothing is missing.
    "VENUE_MARKET_SCOPE_IS_A_SEGMENT": ESTABLISHED_PAYOUT_CONFLICT,

    # ── C2: the BOOKMAKER's side is not held ──────────────────────────
    "VOID_ABANDONMENT_BOOK_RULE_NOT_HELD": BOOK_RULE_NOT_HELD,

    # ── C3: the VENUE's rule or the market's scope is not established ──
    "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED": VENUE_RULE_OR_SCOPE_NOT_HELD,
    "VENUE_MARKET_SCOPE_NOT_ESTABLISHED": VENUE_RULE_OR_SCOPE_NOT_HELD,
    "VENUE_MARKET_SCOPE_CONFLICTS_WITH_THE_IDENTIFIER": (
        VENUE_RULE_OR_SCOPE_NOT_HELD),
    "SETTLEMENT_SCOPE_NOT_ESTABLISHED": VENUE_RULE_OR_SCOPE_NOT_HELD,

    # ── C4: open, and the reason is none of the above ─────────────────
    #
    # THE TWO BIG ONES LAND HERE, AND THAT IS THE POINT. `attest` returns UNKNOWN
    # when it cannot complete the comparison, and it does not report WHICH side
    # was missing -- so `VOID_ABANDONMENT_RULE_NOT_ESTABLISHED` cannot be assigned
    # to C2 or C3 from the code alone. Putting it in C4 says "we do not know which
    # side is missing", which is true. Calling it C1 -- which is what my launch
    # decision effectively did -- says the payouts differ, which is not.
    "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED": OTHER_UNRESOLVED,
    "OVERTIME_RULE_NOT_ESTABLISHED": OTHER_UNRESOLVED,
    "DRAW_HANDLING_NOT_RECONCILED": OTHER_UNRESOLVED,
    "UNRESOLVED_SETTLEMENT_SEMANTICS": OTHER_UNRESOLVED,
}

#: The codes my earlier reporting treated as proven incompatibility and which
#: are NOT. Kept as data so the correction is checkable rather than narrative.
I_REPORTED_THESE_AS_CONFLICTS_AND_THEY_ARE_NOT = (
    "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED",
    "OVERTIME_RULE_NOT_ESTABLISHED",
)

R_NO_WINDOW = "NO_OBSERVATION_WINDOW_SUPPLIED"
R_NO_BUILDS = "NO_BUILD_IDENTITY_SUPPLIED"
R_NO_CENSUS = "NO_CENSUS_SUPPLIED"


def settlement_codes(counts) -> dict:
    """The settlement-relevant subset of a refusal census, classified."""
    out = {}
    for code, n in dict(counts or {}).items():
        code = str(code)
        if code in CLASS_OF:
            out[code] = (CLASS_OF[code], int(n))
        elif any(k in code for k in ("SETTLE", "VOID", "OVERTIME", "DRAW",
                                     "ABANDON", "SCOPE")):
            # Looks like a settlement code and is not in the table: report it
            # BY NAME as unclassified rather than dropping or guessing it.
            out[code] = (UNCLASSIFIED, int(n))
    return out


def classify_census(counts, *, candidates: int, window=None,
                    builds=None) -> dict:
    """SPLIT A REFUSAL CENSUS FOUR WAYS, with its window and builds attached.

    `window` is {"from": iso, "to": iso, "source": str}; `builds` is the list of
    build identities that produced the rows. BOTH ARE REQUIRED: a count without
    them reads as a standing property of the venue, and it is not one.

    Per-code counts do not give the intersection of refusal sets, so the totals
    here are per-class UPPER BOUNDS on distinct candidates, marked as such. The
    one exact statement available from counts is whether a single code covered
    every candidate.
    """
    if not counts:
        return {"ok": False, "refusal": R_NO_CENSUS,
                "why": "an empty census classifies nothing"}
    if not window or not all(window.get(k) for k in ("from", "to")):
        return {"ok": False, "refusal": R_NO_WINDOW,
                "why": ("a refusal tally is a statement about candidates "
                        "observed over an interval. Without the interval it "
                        "reads as a standing property of the venue")}
    if not builds:
        return {"ok": False, "refusal": R_NO_BUILDS,
                "why": ("the gate that produced these refusals is part of the "
                        "measurement. A census from a superseded build must not "
                        "be quoted as the current one")}

    n = int(candidates)
    classified = settlement_codes(counts)
    by_class: dict = {c: {} for c in CLASSES}
    by_class[UNCLASSIFIED] = {}
    for code, (cls, cnt) in classified.items():
        by_class[cls][code] = cnt

    per_class_max = {c: (max(v.values()) if v else 0)
                     for c, v in by_class.items()}
    universal = sorted(code for code, (_c, cnt) in classified.items()
                       if n > 0 and cnt >= n)

    conflicts = per_class_max[ESTABLISHED_PAYOUT_CONFLICT]
    return {
        "ok": True, "refusal": None,
        "version": VERSION,
        "candidates": n,
        "window": dict(window),
        "builds": list(builds),
        "by_class": {c: dict(sorted(v.items(), key=lambda kv: -kv[1]))
                     for c, v in by_class.items() if v},
        "at_most_candidates_per_class": per_class_max,
        "and_these_are_UPPER_BOUNDS": (
            "per-code counts do not give the intersection of refusal sets, so "
            "each figure is the largest single code in that class -- the most "
            "that is defensible. Summing codes within a class would "
            "double-count candidates carrying two of them"),
        "codes_on_EVERY_candidate": universal,

        # THE CORRECTION, AS A NUMBER RATHER THAN A SENTENCE.
        "established_payout_conflicts_at_most": conflicts,
        "and_the_rest_are_not_conflicts": (
            "every candidate outside the C1 column failed a comparison we could "
            "not COMPLETE, not one that came out against us. Reporting the whole "
            "census as settlement incompatibility -- which I did -- overstates "
            "it and points the work away from capture"),
        "remedies": dict(REMEDY),
        "unclassified_codes_reported_by_name": sorted(by_class[UNCLASSIFIED]),
    }
