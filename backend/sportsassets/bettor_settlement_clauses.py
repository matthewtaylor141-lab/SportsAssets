"""ONE OUTCOME, ONE CLAUSE. Settlement prose read per exceptional state.

── THE DEFECT THIS EXISTS TO END ─────────────────────────────────────

`build_leg` passed the ENTIRE captured prose into both `tie_rule` and
`void_rule`, and `_leg_payout_cents` searches those strings for "50-50". So
this text:

    "This market resolves on the final score and includes any extra innings
     played. A tie resolves 50-50."

established a 50-CENT CANCELLATION PAYOUT. Reproduced before this module
existed: `_leg_payout_cents(leg, VOID_REGION)` returned 50.

The prose says nothing whatever about cancellation. A tie is a fixture that was
played to a drawn result; a void is a fixture that did not happen. They are
different events with different money, and one phrase was answering for both
because both fields held the same blob.

THE SHAPE OF THE ERROR is the one that keeps recurring here: a container that
holds "everything we read" is consulted as though it held "what was established
about this specific question". A search over the whole document cannot tell which
sentence it matched or what that sentence was about.

── SO EACH OUTCOME IS READ SEPARATELY, AND CARRIES ITS SOURCE ────────

An outcome's rule is established only by a clause that NAMES THAT OUTCOME'S
TRIGGER and states a payout. The clause travels with the interpretation, so a
reviewer can see which sentence was relied on, and a later reader can tell "the
venue said nothing about this" from "the venue said this".

Silence is not a rule. Two clauses stating different payouts for one outcome is a
REFUSAL, not a preference for the first. And a payout stated for one outcome is
never evidence about another -- which is the whole content of the bug above.
"""

from __future__ import annotations

import hashlib
import re

VERSION = "SETTLEMENT_CLAUSES_V1"

# ═════════════════════════════════════════════════════════════════════
# THE FIVE EXCEPTIONAL STATES, EACH WITH ITS OWN TRIGGER VOCABULARY
# ═════════════════════════════════════════════════════════════════════
#
# OVERTIME is not in this list. It is not an exceptional payout state -- it is a
# question about which INTERVAL the variable is measured over, and
# `bettor_venue_settlement.OVERTIME_PROSE` already owns it against declared
# patterns. Putting it here would give the repository two overtime readers.

TIE = "TIE"                    # played to a drawn result, sport permits it
PUSH = "PUSH"                  # the margin or total lands exactly on the line
CANCELLED = "CANCELLED"        # abandoned, void, no contest -- never completed
POSTPONED = "POSTPONED"        # not played when scheduled, may yet be played
SHORTENED = "SHORTENED"        # called early and graded on a partial result

OUTCOMES = (TIE, PUSH, CANCELLED, POSTPONED, SHORTENED)

#: What a clause must MENTION to be speaking about each outcome.
#:
#: Deliberately narrow and deliberately disjoint. `\bvoid` belongs to CANCELLED
#: and nothing else; `\btie|\bdraw` to TIE and nothing else. If a word could
#: plausibly belong to two outcomes it is in neither list, because a trigger that
#: matches two states reintroduces exactly the conflation this module removes.
TRIGGERS = {
    TIE: (r"\btie[sd]?\b", r"\bdraw[ns]?\b", r"\bdead\s+heat\b",
          r"\bequal\s+score\b", r"\blevel\s+(?:score|result)\b"),
    PUSH: (r"\bpush(?:e[sd])?\b", r"\blands?\s+(?:exactly\s+)?on\s+the\s+"
           r"(?:line|number|spread|total)\b",
           r"\bexactly\s+on\s+the\s+(?:line|number)\b"),
    CANCELLED: (r"\bvoid(?:ed|s)?\b", r"\bcancel(?:led|ed|s|lation)?\b",
                r"\babandon(?:ed|ment|s)?\b", r"\bno\s+contest\b",
                r"\bnever\s+(?:completed|played)\b",
                r"\bdoes\s+not\s+take\s+place\b"),
    POSTPONED: (r"\bpostpone[sd]?\b", r"\bpostponement\b",
                r"\brescheduled?\b", r"\bsuspended\b", r"\bdelayed\b"),
    SHORTENED: (r"\bshortened\b", r"\bcalled\s+(?:early|off)\b",
                r"\bstopped\s+(?:early|before)\b",
                r"\bmade\s+official\b", r"\bpartial\s+(?:result|game)\b"),
}

# ── WHAT A CLAUSE CAN SAY THE PAYOUT IS ──────────────────────────────

PAY_HALF = "PAYS_FIFTY_CENTS_PER_CONTRACT"
PAY_REFUND_BASIS = "REFUNDS_THE_PURCHASE_BASIS"
PAY_NO = "RESOLVES_NO_FOR_THE_HELD_SIDE"
PAY_YES = "RESOLVES_YES_FOR_THE_HELD_SIDE"
PAY_STAYS_OPEN = "STAYS_OPEN_UNTIL_THE_FIXTURE_COMPLETES"
PAY_ON_PARTIAL = "GRADES_ON_THE_RESULT_AT_THE_TIME"

#: A REFUND OF BASIS IS NOT FIFTY CENTS, and conflating them is the second half
#: of the reported defect. `_leg_payout_cents` returns a per-contract payout in
#: cents; a refund returns whatever was PAID, which on a contract bought at 30c
#: is 30c and on one bought at 55c is 55c. Mapping a refund to 50 would invent a
#: gain on the cheap side and a loss on the dear one, and `reconcile_settlement`
#: already books a void as `remaining_basis`, so the two halves of this
#: repository would disagree about the same event.
PAYOUT_PATTERNS = (
    # Order matters only for reporting; a clause matching two DIFFERENT payouts
    # is a refusal either way.
    (PAY_HALF, (r"\b50\s*[-/]\s*50\b", r"\bfifty\s*[-/]\s*fifty\b",
                r"\bhalf\s+the\s+(?:payout|contract)\b",
                r"\b50\s*cents?\b", r"\b\$?0\.50\b")),
    (PAY_REFUND_BASIS, (r"\brefund(?:ed|s)?\b", r"\breturn(?:ed|s)?\s+"
                        r"(?:the\s+)?(?:stake|basis|purchase|cost)\b",
                        r"\bstakes?\s+(?:are\s+)?return",
                        r"\bmoney\s+back\b")),
    (PAY_NO, (r"\bresolve[sd]?\s+(?:to\s+)?no\b",
              r"\bsettle[sd]?\s+(?:as\s+)?no\b",
              r"\bpays?\s+(?:out\s+)?nothing\b", r"\bzero\b")),
    (PAY_YES, (r"\bresolve[sd]?\s+(?:to\s+)?yes\b",
               r"\bsettle[sd]?\s+(?:as\s+)?yes\b")),
    (PAY_STAYS_OPEN, (r"\bremains?\s+open\b", r"\bstays?\s+open\b",
                      r"\buntil\s+(?:it\s+is\s+)?(?:replayed|completed|"
                      r"resumed|played)\b")),
    (PAY_ON_PARTIAL, (r"\bat\s+the\s+time\s+(?:of|the)\b",
                      r"\blast\s+completed\b",
                      r"\bresult\s+(?:at|when)\s+(?:the\s+)?"
                      r"(?:stoppage|suspension)\b")),
)

#: The per-contract payout in cents, where the class fixes one. PAY_REFUND_BASIS
#: is deliberately absent: it is not a constant, it is the contract's own cost,
#: and the caller has to supply that. `None` means "this class does not name a
#: constant payout" and the caller must not substitute one.
PAYOUT_CENTS = {
    PAY_HALF: 50,
    PAY_NO: 0,
    PAY_YES: 100,
}

R_NOT_STATED = "THE_PROSE_STATES_NO_RULE_FOR_THIS_OUTCOME"
R_TRIGGER_WITHOUT_PAYOUT = "A_CLAUSE_NAMES_THIS_OUTCOME_AND_STATES_NO_PAYOUT"
R_CONFLICTING_CLAUSES = "TWO_CLAUSES_STATE_DIFFERENT_PAYOUTS_FOR_THIS_OUTCOME"
R_AMBIGUOUS_CLAUSE = "ONE_CLAUSE_STATES_TWO_DIFFERENT_PAYOUTS"
R_NO_PROSE = "NO_SETTLEMENT_PROSE_WAS_CAPTURED"

_SENT = re.compile(r"[^.;\n]+[.;\n]?")


def sentences(prose):
    """The clauses of a document. Same splitting `bettor_settlement_terms` uses."""
    for raw in _SENT.findall(str(prose or "")):
        s = raw.strip()
        if s:
            yield s


def _payouts_in(clause):
    low = " ".join(clause.lower().split())
    found = []
    for cls, pats in PAYOUT_PATTERNS:
        if any(re.search(p, low) for p in pats):
            found.append(cls)
    return found


def _mentions(clause, outcome):
    low = " ".join(clause.lower().split())
    return [p for p in TRIGGERS[outcome] if re.search(p, low)]


def read_outcome(prose, outcome) -> dict:
    """What THIS prose establishes about THIS outcome, and from which clause.

    Never raises. Returns
    {"established": bool, "payout_class": str|None, "payout_cents": int|None,
     "clause": str|None, "refusal": str|None, ...}.

    A clause establishes the rule only if it NAMES the outcome's trigger and
    states one payout. Silence is `R_NOT_STATED` -- never a default.
    """
    out = {"version": VERSION, "outcome": outcome, "established": False,
           "payout_class": None, "payout_cents": None, "clause": None,
           "matched_triggers": [], "candidate_clauses": [], "refusal": None,
           "a_payout_for_another_outcome_is_not_evidence_here": (
               "this reads only clauses naming %s. The bug this replaces let "
               "'A tie resolves 50-50' establish a 50-cent CANCELLATION "
               "payout, because one blob answered every question" % outcome)}
    if not str(prose or "").strip():
        out["refusal"] = R_NO_PROSE
        return out

    hits = []
    for clause in sentences(prose):
        trig = _mentions(clause, outcome)
        if not trig:
            continue
        pays = _payouts_in(clause)
        hits.append({"clause": clause[:300], "triggers": trig,
                     "payouts": pays})
    out["candidate_clauses"] = hits
    if not hits:
        out["refusal"] = R_NOT_STATED
        out["why"] = ("no clause in this text names %s. The text may be about "
                      "other outcomes entirely, and what it says about them is "
                      "not evidence about this one" % outcome)
        return out

    # ── ONE CLAUSE SAYING TWO THINGS IS AMBIGUOUS, NOT A CHOICE ──────
    for h in hits:
        if len(set(h["payouts"])) > 1:
            out.update(refusal=R_AMBIGUOUS_CLAUSE, clause=h["clause"],
                       matched_triggers=h["triggers"],
                       why=("the clause %r states %s. Picking one would be a "
                            "reading nobody made"
                            % (h["clause"][:120], sorted(set(h["payouts"])))))
            return out

    stated = [h for h in hits if h["payouts"]]
    if not stated:
        out.update(refusal=R_TRIGGER_WITHOUT_PAYOUT,
                   clause=hits[0]["clause"],
                   matched_triggers=hits[0]["triggers"],
                   why=("a clause names %s and states no payout for it, so the "
                        "outcome is acknowledged and its money is not" % outcome))
        return out

    classes = {h["payouts"][0] for h in stated}
    if len(classes) > 1:
        out.update(refusal=R_CONFLICTING_CLAUSES,
                   candidate_clauses=stated,
                   why=("clauses state %s for %s. A settlement conflict cannot "
                        "be repaired by preferring the first sentence"
                        % (sorted(classes), outcome)))
        return out

    cls = classes.pop()
    win = stated[0]
    out.update(established=True, payout_class=cls,
               payout_cents=PAYOUT_CENTS.get(cls),
               clause=win["clause"], matched_triggers=win["triggers"],
               refusal=None,
               payout_is_not_a_constant=(
                   None if cls != PAY_REFUND_BASIS else
                   "a refund returns what was PAID -- 30c on a contract bought "
                   "at 30c, 55c at 55c -- so there is no constant to put in "
                   "PAYOUT_CENTS and the caller supplies the basis"),
               why="clause %r states %s for %s" % (win["clause"][:120], cls,
                                                   outcome))
    return out


def interpret(prose, *, source=None, retrieved_at=None) -> dict:
    """Every outcome read separately, with provenance for the whole document.

    The returned `provenance` is what makes a historical decision auditable
    without rereading prose that may since have changed: the raw text, who it
    came from, when it was retrieved, a content hash, and this module's version.
    """
    raw = str(prose or "")
    rules = {o: read_outcome(raw, o) for o in OUTCOMES}
    return {
        "version": VERSION,
        "rules": rules,
        "established": sorted(o for o, r in rules.items()
                              if r["established"]),
        "unestablished": {o: r["refusal"] for o, r in rules.items()
                          if not r["established"]},
        "provenance": {
            "raw_text": raw,
            "chars": len(raw),
            "content_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "source": source,
            "retrieved_at": retrieved_at,
            "interpretation_version": VERSION,
            "why_persisted": (
                "a decision made on this reading must stay auditable without "
                "rereading prose that may since have changed. The hash is what "
                "proves the text is the same text"),
        },
    }


def describe() -> dict:
    return {"version": VERSION, "outcomes": OUTCOMES,
            "overtime_is_not_here": (
                "overtime is which INTERVAL the variable is measured over, not "
                "an exceptional payout state. bettor_venue_settlement."
                "OVERTIME_PROSE owns it; a second reader here would give this "
                "repository two answers"),
            "a_refund_is_not_fifty_cents": (
                "PAY_REFUND_BASIS has no entry in PAYOUT_CENTS on purpose. "
                "reconcile_settlement books a void as remaining_basis, so "
                "mapping a refund to 50 would make the two halves of this "
                "repository disagree about the same event")}
