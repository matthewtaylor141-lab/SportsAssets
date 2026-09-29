"""ONE OUTCOME, ONE CLAUSE, ONE AFFIRMATIVE RELATIONSHIP -- AND A BOUNDED GRAMMAR.

THIS IS NOT A GENERAL PROSE INTERPRETER AND DOES NOT CLAIM TO BE. It recognises
an explicitly listed set of constructions (`SUPPORTED_CONSTRUCTIONS`) and refuses
everything else. A venue sentence this grammar does not cover leaves its outcome
UNESTABLISHED, which propagates as UNESTABLISHABLE -- never as a payout.

── WHAT V1 GOT WRONG, REPRODUCED BY CODEX AGAINST THE COMMITTED READER ─

V1 asked "does this sentence contain an outcome word and a payout word". That is
CO-OCCURRENCE, not a statement that the payout applies to that outcome, and four
inputs showed the difference:

    "If cancelled, contracts are not refunded."
        -> established REFUNDS_THE_PURCHASE_BASIS.  The sentence says the
           opposite of what was recorded. Negation was invisible.

    "A tie resolves 50-50, but cancellation is decided separately."
        -> established a 50-cent CANCELLATION payout. The 50-50 belongs to the
           tie; the cancellation clause states no payout at all.

    "If cancelled, contracts pay $0.50."
        -> established NOTHING, because the sentence splitter broke "$0." from
           "50." and destroyed the amount it was looking for.

    "If cancelled, this market resolves NO."
        -> returned 0 cents for BOTH the long and the short side. The sentence
           describes THE MARKET'S RESOLUTION. Which side the account holds is a
           different fact, and a market resolving NO pays the NO holder $1.00.

── SO V2 SEPARATES FOUR THINGS V1 CONFLATED ──────────────────────────

    1. A SENTENCE from a CLAUSE. "A tie resolves 50-50, but cancellation is
       decided separately" is two clauses with two subjects, and a payout in one
       says nothing about the trigger in the other.

    2. CO-OCCURRENCE from an AFFIRMATIVE RELATIONSHIP. A clause establishes a
       payout only by matching a listed construction in which THIS trigger and
       THIS payout are parts of one predication. Negation, an exception
       ("unless", "except"), a discretionary verb ("may", "at our discretion"),
       a second outcome's trigger, or a construction not on the list all leave
       it unresolved.

    3. AMOUNTS from SENTENCE BOUNDARIES. `$0.50`, `50c`, `0.5` and `50-50` are
       all read, and a decimal point is never a sentence end.

    4. THE MARKET'S RESOLUTION from THE HELD SIDE'S PAYOUT. The prose states the
       former. The latter needs the instrument's semantics AND the account's own
       order intent, and `side_payout_cents` is the only place the two are
       combined. YES/NO polarity is NEVER inferred from team A/B orientation:
       which participant a leg backs and which outcome token it holds are
       independent facts.

Silence is not a rule. Two clauses stating different resolutions for one outcome
is a refusal, not a preference for the first. And a resolution stated for one
outcome is never evidence about another.
"""

from __future__ import annotations

import hashlib
import re

VERSION = "SETTLEMENT_CLAUSES_V2"

# ═════════════════════════════════════════════════════════════════════
# THE FIVE EXCEPTIONAL STATES, EACH WITH ITS OWN TRIGGER VOCABULARY
# ═════════════════════════════════════════════════════════════════════
#
# OVERTIME is not here. It is not an exceptional payout state -- it is a question
# about which INTERVAL the variable is measured over, and
# `bettor_venue_settlement.OVERTIME_PROSE` already owns it. Two readers for one
# rule is two answers for one rule.

TIE = "TIE"                    # played to a drawn result, sport permits it
PUSH = "PUSH"                  # the margin or total lands exactly on the line
CANCELLED = "CANCELLED"        # abandoned, void, no contest -- never completed
POSTPONED = "POSTPONED"        # not played when scheduled, may yet be played
SHORTENED = "SHORTENED"        # called early and graded on a partial result

OUTCOMES = (TIE, PUSH, CANCELLED, POSTPONED, SHORTENED)

#: What a clause must MENTION to be speaking about each outcome.
#:
#: Deliberately narrow and deliberately disjoint: a word that could belong to two
#: outcomes is in neither list, because a trigger matching two states
#: reintroduces the conflation this module removes.
TRIGGERS = {
    TIE: (r"\btie[sd]?\b", r"\bdraw[ns]?\b", r"\bdead\s+heat\b",
          r"\bequal\s+score\b", r"\blevel\s+(?:score|result)\b"),
    PUSH: (r"\bpush(?:e[sd])?\b",
           r"\blands?\s+(?:exactly\s+)?on\s+the\s+"
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

# ═════════════════════════════════════════════════════════════════════
# MARKET RESOLUTIONS -- WHAT THE MARKET DOES, NOT WHAT THE ACCOUNT GETS
# ═════════════════════════════════════════════════════════════════════
#
# THE DISTINCTION V1 LOST. "This market resolves NO" is a fact about the
# instrument. What it pays depends on which outcome token the account holds, and
# V1's own constant was named RESOLVES_NO_FOR_THE_HELD_SIDE while being read from
# a sentence that says nothing about the held side. Both sides got zero.

RES_YES = "THE_MARKET_RESOLVES_YES"
RES_NO = "THE_MARKET_RESOLVES_NO"
RES_HALF = "THE_MARKET_RESOLVES_FIFTY_FIFTY"
RES_REFUND = "THE_MARKET_REFUNDS_THE_PURCHASE_BASIS"
RES_STAYS_OPEN = "THE_MARKET_STAYS_OPEN_UNTIL_THE_FIXTURE_COMPLETES"
RES_ON_PARTIAL = "THE_MARKET_IS_GRADED_ON_THE_RESULT_AT_THE_TIME"
RES_CENTS = "THE_MARKET_RESOLVES_AT_A_STATED_PER_CONTRACT_AMOUNT"

RESOLUTIONS = (RES_YES, RES_NO, RES_HALF, RES_REFUND, RES_STAYS_OPEN,
               RES_ON_PARTIAL, RES_CENTS)

#: V1 NAMES, KEPT AS ALIASES. `PAY_NO`/`PAY_YES` were named for the held side
#: and read from prose about the market; the new names say which they are. The
#: aliases exist so callers keep working, not because the old names were right.
PAY_HALF = RES_HALF
PAY_REFUND_BASIS = RES_REFUND
PAY_NO = RES_NO
PAY_YES = RES_YES
PAY_STAYS_OPEN = RES_STAYS_OPEN
PAY_ON_PARTIAL = RES_ON_PARTIAL

#: THE ONLY RESOLUTIONS WHOSE PER-CONTRACT CENTS ARE SIDE-INDEPENDENT.
#:
#: RES_YES and RES_NO are deliberately ABSENT: they pay 100 to one side and 0 to
#: the other, so a constant here would be wrong for one of them every time --
#: which is exactly the defect. RES_REFUND is absent because a refund returns
#: what was PAID, and `reconcile_settlement` already books a void as the
#: remaining basis, so a constant would make the two halves of this repository
#: disagree about one event. `side_payout_cents` is where the side and the basis
#: are supplied.
PAYOUT_CENTS = {RES_HALF: 50}

# ── THE PAYOUT VOCABULARY, WITH AMOUNTS PRESERVED ────────────────────
#
# Ordered: the first matching pattern in a clause names the resolution. A clause
# matching two DIFFERENT resolutions is ambiguous either way.
_AMT = r"(?:\$\s*)?(?P<amt>\d+(?:\.\d+)?)"

PAYOUT_PATTERNS = (
    (RES_HALF, (r"\b50\s*[-/]\s*50\b", r"\bfifty\s*[-/]\s*fifty\b",
                r"\bhalf\s+the\s+(?:payout|contract|contracts)\b",
                r"\bsplit\s+(?:equally|evenly)\b")),
    # A STATED PER-CONTRACT AMOUNT. `$0.50`, `50 cents`, `0.5 per contract`.
    # Fifty cents is recognised as RES_HALF below, in `_amount_resolution`, so
    # "$0.50" and "50-50" agree instead of being two unrelated classes.
    (RES_CENTS, (r"\bpays?\s+(?:out\s+)?" + _AMT + r"\s*(?:cents?|c\b|per\b)?",
                 r"\bresolves?\s+(?:at\s+)?" + _AMT + r"\s*(?:cents?|c\b)",
                 r"\bsettle[sd]?\s+(?:at\s+)?" + _AMT + r"\s*(?:cents?|c\b)",
                 r"\bworth\s+" + _AMT,
                 r"\bat\s+" + _AMT + r"\s*(?:cents?|c\b)\s+per\s+contract")),
    (RES_REFUND, (r"\brefund(?:ed|s)?\b",
                  r"\breturn(?:ed|s)?\s+(?:the\s+)?"
                  r"(?:stake|stakes|basis|purchase|cost)\b",
                  r"\bstakes?\s+(?:are\s+)?return",
                  r"\bmoney\s+back\b", r"\bpurchase\s+price\s+(?:is\s+)?return")),
    (RES_NO, (r"\bresolve[sd]?\s+(?:to\s+)?no\b",
              r"\bsettle[sd]?\s+(?:as\s+)?no\b",
              r"\bresolve[sd]?\s+in\s+the\s+negative\b")),
    (RES_YES, (r"\bresolve[sd]?\s+(?:to\s+)?yes\b",
               r"\bsettle[sd]?\s+(?:as\s+)?yes\b",
               r"\bresolve[sd]?\s+in\s+the\s+affirmative\b")),
    (RES_STAYS_OPEN, (r"\bremains?\s+open\b", r"\bstays?\s+open\b",
                      r"\buntil\s+(?:it\s+is\s+)?(?:replayed|completed|"
                      r"resumed|played)\b")),
    (RES_ON_PARTIAL, (r"\bat\s+the\s+time\s+(?:of|the)\b",
                      r"\blast\s+completed\b",
                      r"\bresult\s+(?:at|when)\s+(?:the\s+)?"
                      r"(?:stoppage|suspension)\b")),
)

# ── WHAT DISQUALIFIES A CLAUSE, WHATEVER ELSE IT CONTAINS ────────────

#: NEGATION. Checked OUTSIDE the trigger and payout spans, because "no contest"
#: is a cancellation trigger and "resolves NO" is a resolution -- V1's whole
#: problem was reading words without knowing what they were part of.
_NEGATION = (r"\bnot\b", r"\bn't\b", r"\bnever\b", r"\bno\s+refund",
             r"\bnor\b", r"\bwithout\b", r"\bcannot\b", r"\bexcluded\b",
             r"\bdoes\s+not\b", r"\bwill\s+not\b", r"\bshall\s+not\b",
             r"\bis\s+not\b", r"\bare\s+not\b")

#: AN EXCEPTION OR A CARVE-OUT leaves the rule conditional on something this
#: parser cannot evaluate.
_EXCEPTION = (r"\bunless\b", r"\bexcept\b", r"\bother\s+than\b",
              r"\bsave\s+(?:for|that)\b", r"\bsubject\s+to\b",
              r"\bprovided\s+that\b", r"\bnotwithstanding\b")

#: A DISCRETIONARY OR HEDGED VERB is not a stated rule.
_DISCRETION = (r"\bmay\b", r"\bmight\b", r"\bcould\b",
               r"\bat\s+(?:our|its|their)\s+discretion\b",
               r"\bgenerally\b", r"\btypically\b", r"\busually\b",
               r"\bin\s+most\s+cases\b", r"\bdecided\s+separately\b",
               r"\bhandled\s+under\b", r"\bsee\s+(?:our|the)\b")

# ── THE SUPPORTED CONSTRUCTIONS ──────────────────────────────────────
#
# A clause establishes a resolution ONLY by matching one of these. The list is
# the grammar: anything else is R_UNSUPPORTED_CONSTRUCTION, which is a statement
# about this parser and not about the venue.

#: An optional subject the venue puts between the trigger and the verb.
_SUBJ = (r"(?:(?:the|this|that|all|any)\s+)?"
         r"(?:market|markets|contract|contracts|position|positions|bet|bets|"
         r"wager|wagers|stake|stakes|trade|trades|it|they)?\s*"
         r"(?:(?:will|shall|is|are|be|to)\s+)*")

SUPPORTED_CONSTRUCTIONS = (
    # 1. CONDITION FIRST: "If cancelled, contracts pay $0.50."
    ("CONDITION_THEN_RESOLUTION",
     r"^\s*(?:if|when|whenever|where|should|in\s+the\s+event\s+(?:of|that)|"
     r"in\s+case\s+of|on)\s+.{0,80}?{TRIG}.{0,40}?[,;]?\s*" + _SUBJ +
     r".{0,20}?{PAY}"),
    # 2. RESOLUTION FIRST: "Contracts are refunded if the game is cancelled."
    ("RESOLUTION_THEN_CONDITION",
     r"^\s*" + _SUBJ + r".{0,40}?{PAY}.{0,40}?\s+"
     r"(?:if|when|whenever|where|in\s+the\s+event\s+(?:of|that)|"
     r"in\s+case\s+of|on)\s+.{0,80}?{TRIG}"),
    # 3. THE TRIGGER MODIFIES THE SUBJECT: "Cancelled markets are refunded."
    #    TRIED BEFORE 4, because 4's optional noun would otherwise swallow it
    #    and report the wrong construction. Specific before general.
    ("TRIGGER_MODIFIES_THE_SUBJECT",
     r"^\s*(?:a|an|any|the|all)?\s*{TRIG}\s+"
     r"(?:markets?|contracts?|games?|fixtures?|matches?)\s+"
     r"(?:(?:will|shall|is|are|be)\s+)*.{0,20}?{PAY}"),
    # 4. THE TRIGGER IS THE SUBJECT: "A tie resolves 50-50."
    ("TRIGGER_IS_THE_SUBJECT",
     r"^\s*(?:a|an|any|the|each)?\s*{TRIG}\s*"
     r"(?:(?:will|shall|is|are|be)\s+)*.{0,20}?{PAY}"),
)

# ═════════════════════════════════════════════════════════════════════
# REFUSALS. Each names what was not established, never a default.
# ═════════════════════════════════════════════════════════════════════

R_NOT_STATED = "THE_PROSE_STATES_NO_RULE_FOR_THIS_OUTCOME"
R_TRIGGER_WITHOUT_PAYOUT = "A_CLAUSE_NAMES_THIS_OUTCOME_AND_STATES_NO_PAYOUT"
R_CONFLICTING_CLAUSES = "TWO_CLAUSES_STATE_DIFFERENT_PAYOUTS_FOR_THIS_OUTCOME"
R_AMBIGUOUS_CLAUSE = "ONE_CLAUSE_STATES_TWO_DIFFERENT_PAYOUTS"
R_NO_PROSE = "NO_SETTLEMENT_PROSE_WAS_CAPTURED"
R_NEGATED = "THE_CLAUSE_NEGATES_THE_PAYOUT_IT_MENTIONS"
R_EXCEPTION = "THE_CLAUSE_CARRIES_AN_EXCEPTION_THIS_PARSER_CANNOT_EVALUATE"
R_DISCRETIONARY = "THE_CLAUSE_STATES_A_DISCRETION_OR_A_CROSS_REFERENCE"
R_MIXED_TRIGGERS = "ONE_CLAUSE_NAMES_TWO_DIFFERENT_OUTCOMES"
R_UNSUPPORTED_CONSTRUCTION = (
    "NO_SUPPORTED_CONSTRUCTION_RELATES_THIS_TRIGGER_TO_THIS_PAYOUT")
R_SIDE_NOT_STATED = "WHICH_SIDE_OF_THE_INSTRUMENT_IS_HELD_IS_NOT_STATED"
R_BASIS_NOT_STATED = "THE_CONTRACTS_OWN_PURCHASE_BASIS_IS_NOT_STATED"
R_RESOLUTION_HAS_NO_PER_CONTRACT_VALUE = (
    "THIS_RESOLUTION_DOES_NOT_FIX_A_PER_CONTRACT_AMOUNT")

# ═════════════════════════════════════════════════════════════════════
# SPLITTING: SENTENCES THAT KEEP THEIR DECIMALS, THEN CLAUSES
# ═════════════════════════════════════════════════════════════════════
#
# `\.(?!\d)` is the whole repair for defect 3. V1 split on any `.`, so
# "$0.50." became "$0." and "50." and the amount it was looking for no longer
# existed. A period followed by a digit is a decimal point, never a sentence end.
_SENT = re.compile(r".+?(?:\.(?!\d)|[;\n]|$)", re.S)

#: CLAUSE BOUNDARIES INSIDE A SENTENCE. A coordinating conjunction introduces a
#: new predication, and a payout in one clause says nothing about a trigger in
#: another -- which is defect 2. "A tie resolves 50-50, but cancellation is
#: decided separately" is two clauses and only the first states a payout.
_CLAUSE_SPLIT = re.compile(
    r",?\s*\b(?:but|however|whereas|while|although|though)\b\s*", re.I)


def sentences(prose):
    """The sentences of a document, with decimal amounts intact."""
    for raw in _SENT.findall(str(prose or "")):
        s = raw.strip()
        if s:
            yield s


def clauses(sentence):
    """The predications of one sentence."""
    for part in _CLAUSE_SPLIT.split(str(sentence or "")):
        p = part.strip().strip(",;")
        if p:
            yield p


def _norm(text):
    return " ".join(str(text or "").lower().split())


# ═════════════════════════════════════════════════════════════════════
# READING ONE CLAUSE
# ═════════════════════════════════════════════════════════════════════

def _spans(text, patterns):
    """Every (start, end, pattern) a group of patterns matches."""
    out = []
    for p in patterns:
        for m in re.finditer(p, text):
            out.append((m.start(), m.end(), p))
    return out


def _amount_resolution(clause, pattern):
    """A stated per-contract amount, in cents, or None.

    FIFTY CENTS IS `RES_HALF`. "$0.50" and "50-50" are the same rule stated two
    ways, and giving them two classes would make a document that says both look
    like a conflict.
    """
    m = re.search(pattern, clause)
    if m is None:
        return None, None
    try:
        raw = m.group("amt")
    except (IndexError, error_types := (IndexError,)):          # noqa: F841
        return None, None
    if raw is None:
        return None, None
    value = float(raw)
    # Dollars if it carries a decimal point or a $; cents if it is a bare
    # integer next to the word "cents".
    if "." in raw or re.search(r"\$\s*" + re.escape(raw), clause):
        cents = int(round(value * 100))
    elif re.search(re.escape(raw) + r"\s*(?:cents?|c\b)", clause):
        cents = int(round(value))
    else:
        return None, None            # a bare number with no unit is not money
    if cents < 0 or cents > 100:
        return None, cents           # not a per-contract settlement amount
    return cents, cents


def _payouts_in(clause):
    """[(resolution, span, cents_or_None)] for every resolution this clause states."""
    found = []
    for res, pats in PAYOUT_PATTERNS:
        for p in pats:
            m = re.search(p, clause)
            if m is None:
                continue
            cents = None
            if res == RES_CENTS:
                cents, _ = _amount_resolution(clause, p)
                if cents is None:
                    continue         # "pays nothing"/"pays out" with no amount
                if cents == 50:
                    res = RES_HALF
                elif cents == 0:
                    res = RES_CENTS
            found.append((res, (m.start(), m.end()), cents))
            break
    # De-duplicate on the resolution, keeping the earliest span.
    seen, out = set(), []
    for res, span, cents in sorted(found, key=lambda t: t[1][0]):
        if res in seen:
            continue
        seen.add(res)
        out.append((res, span, cents))
    return out


def _outside(text, patterns, protected):
    """Patterns matching outside every protected span. Returns the matches."""
    hits = []
    for p in patterns:
        for m in re.finditer(p, text):
            if any(m.start() < pe and ps < m.end() for ps, pe in protected):
                continue             # inside a trigger or a payout: not this
            hits.append(m.group(0))
    return hits


def _construction(clause, trig_pat, pay_pat):
    """The named construction relating this trigger to this payout, or None.

    THE HEART OF THE REPAIR. V1 asked whether both appeared somewhere in the
    sentence. This asks whether they are parts of ONE predication in a shape this
    parser has been told about, and answers None -- not a guess -- otherwise.
    """
    for name, template in SUPPORTED_CONSTRUCTIONS:
        pattern = template.replace("{TRIG}", "(?:%s)" % trig_pat) \
                          .replace("{PAY}", "(?:%s)" % pay_pat)
        try:
            if re.search(pattern, clause, re.S):
                return name
        except re.error:                                        # noqa: PERF203
            continue
    return None


def read_clause(clause, outcome) -> dict:
    """What ONE clause establishes about ONE outcome, and by which construction.

    Never raises. `established` is True only when a listed construction relates
    a trigger for `outcome` to exactly one resolution, with no negation, no
    exception, no discretion and no second outcome in the clause.
    """
    text = _norm(clause)
    out = {"clause": str(clause)[:300], "outcome": outcome,
           "established": False, "resolution": None, "payout_class": None,
           "stated_cents": None, "matched_triggers": [], "construction": None,
           "refusal": None, "condition": None}

    trigs = _spans(text, TRIGGERS[outcome])
    if not trigs:
        out["refusal"] = R_NOT_STATED
        return out
    out["matched_triggers"] = sorted({t[2] for t in trigs})

    # ── A CLAUSE NAMING TWO OUTCOMES RELATES ITS PAYOUT TO NEITHER ───
    others = sorted(o for o in OUTCOMES
                    if o != outcome and _spans(text, TRIGGERS[o]))
    if others:
        out.update(refusal=R_MIXED_TRIGGERS, also_names=others,
                   why=("this clause names %s and %s. A payout in a clause with "
                        "two subjects is not attached to either of them"
                        % (outcome, " and ".join(others))))
        return out

    # ── WHAT DISQUALIFIES THE CLAUSE, CHECKED BEFORE ITS PAYOUT ─────
    #
    # ORDER MATTERS AND THIS IS WHY. "If cancelled, settlement is at our
    # discretion" states no payout AND is discretionary. Reporting
    # A_CLAUSE_NAMES_THIS_OUTCOME_AND_STATES_NO_PAYOUT sends a reader looking
    # for the payout elsewhere; THE_CLAUSE_STATES_A_DISCRETION says there is
    # none to find. The more specific refusal is the more useful one.
    #
    # PROTECTED BY THE TRIGGER SPANS ONLY. "no contest" is a cancellation
    # trigger and must not read as a negation, so trigger text is excluded --
    # but "there is no refund" must, which is why the payout span is NOT
    # protected here. v1 protected both and so missed that one.
    trig_spans = [(t[0], t[1]) for t in trigs]
    neg = _outside(text, _NEGATION, trig_spans)
    if neg:
        out.update(refusal=R_NEGATED, negations=neg,
                   why=("the clause negates its own payout (%s), so it states "
                        "what does NOT happen. 'If cancelled, contracts are not "
                        "refunded' established a refund in v1" % neg[:3]))
        return out
    exc = _outside(text, _EXCEPTION, trig_spans)
    if exc:
        out.update(refusal=R_EXCEPTION, exceptions=exc,
                   why=("the clause carves out %s, so the rule holds in cases "
                        "this parser cannot identify" % exc[:3]))
        return out
    dis = _outside(text, _DISCRETION, trig_spans)
    if dis:
        out.update(refusal=R_DISCRETIONARY, hedges=dis,
                   why=("the clause is discretionary or defers elsewhere (%s), "
                        "which is not a stated rule" % dis[:3]))
        return out

    pays = _payouts_in(text)
    if not pays:
        out["refusal"] = R_TRIGGER_WITHOUT_PAYOUT
        out["why"] = ("the clause names %s and states no resolution for it, so "
                      "the outcome is acknowledged and its money is not"
                      % outcome)
        return out
    if len({p[0] for p in pays}) > 1:
        out.update(refusal=R_AMBIGUOUS_CLAUSE,
                   states=sorted({p[0] for p in pays}),
                   why=("the clause states %s. Picking one would be a reading "
                        "nobody made" % sorted({p[0] for p in pays})))
        return out

    res, pay_span, cents = pays[0]
    # ── AND THE RELATIONSHIP ITSELF MUST BE ONE WE RECOGNISE ────────
    pay_pat = None
    for r, pats in PAYOUT_PATTERNS:
        for p in pats:
            if re.search(p, text) and (r == res or (r == RES_CENTS
                                                    and res in (RES_HALF,
                                                                RES_CENTS))):
                pay_pat = p
                break
        if pay_pat:
            break
    name = None
    for trig_pat in out["matched_triggers"]:
        name = _construction(text, trig_pat, pay_pat or r"(?!x)x")
        if name:
            break
    if name is None:
        out.update(refusal=R_UNSUPPORTED_CONSTRUCTION,
                   would_have_read=res,
                   why=("the clause names %s and states %s, and no supported "
                        "construction relates them. This is a statement about "
                        "this parser's grammar (%s), not about the venue"
                        % (outcome, res,
                           [n for n, _ in SUPPORTED_CONSTRUCTIONS])))
        return out

    # ── THE CONDITION ATTACHED TO THE TRIGGER, RETAINED ─────────────
    cond = re.search(r"\b(?:if|when|where|in\s+the\s+event\s+(?:of|that))\s+"
                     r"(.{0,120}?)(?:,|$)", text)
    out.update(established=True, resolution=res, payout_class=res,
               stated_cents=cents, construction=name, refusal=None,
               condition=(cond.group(1) if cond else None),
               why="construction %s relates %s to %s" % (name, outcome, res))
    return out


def read_outcome(prose, outcome) -> dict:
    """What THIS prose establishes about THIS outcome, and from which clause.

    Never raises. The returned `resolution` is what the MARKET does;
    `side_payout_cents` turns it into what a held side receives, and nothing
    here pretends to know which side that is.
    """
    out = {"version": VERSION, "outcome": outcome, "established": False,
           "resolution": None, "payout_class": None, "payout_cents": None,
           "stated_cents": None, "clause": None, "construction": None,
           "condition": None, "matched_triggers": [], "candidate_clauses": [],
           "refusal": None,
           "a_payout_for_another_outcome_is_not_evidence_here": (
               "this reads only clauses whose construction relates a %s trigger "
               "to a resolution. V1 let 'A tie resolves 50-50' establish a "
               "50-cent CANCELLATION payout because one blob answered every "
               "question" % outcome),
           "the_resolution_is_not_the_held_sides_payout": (
               "a market resolving YES or NO pays $1.00 to one side and $0.00 "
               "to the other. side_payout_cents combines this resolution with "
               "the account's own order intent; nothing here infers polarity "
               "from which participant a leg backs")}
    if not str(prose or "").strip():
        out["refusal"] = R_NO_PROSE
        return out

    reads = []
    for sentence in sentences(prose):
        for clause in clauses(sentence):
            r = read_clause(clause, outcome)
            if r["refusal"] == R_NOT_STATED:
                continue             # this clause is not about this outcome
            reads.append(r)
    out["candidate_clauses"] = reads
    if not reads:
        out["refusal"] = R_NOT_STATED
        out["why"] = ("no clause in this text names %s. What the text says about "
                      "other outcomes is not evidence about this one" % outcome)
        return out

    established = [r for r in reads if r["established"]]
    if not established:
        # THE FIRST REFUSAL IS THE REPORTED ONE, and the rest travel with it.
        first = reads[0]
        out.update(refusal=first["refusal"], clause=first["clause"],
                   matched_triggers=first["matched_triggers"],
                   why=first.get("why") or "")
        for k in ("negations", "exceptions", "hedges", "also_names", "states",
                  "would_have_read"):
            if k in first:
                out[k] = first[k]
        return out

    kinds = {r["resolution"] for r in established}
    if len(kinds) > 1:
        out.update(refusal=R_CONFLICTING_CLAUSES, states=sorted(kinds),
                   why=("clauses state %s for %s. A settlement conflict cannot "
                        "be repaired by preferring the first sentence"
                        % (sorted(kinds), outcome)))
        return out

    win = established[0]
    res = win["resolution"]
    out.update(established=True, resolution=res, payout_class=res,
               stated_cents=win["stated_cents"],
               payout_cents=PAYOUT_CENTS.get(res),
               clause=win["clause"], construction=win["construction"],
               condition=win["condition"],
               matched_triggers=win["matched_triggers"], refusal=None,
               side_dependent=res in (RES_YES, RES_NO),
               basis_dependent=res == RES_REFUND,
               why=win["why"])
    return out


# ═════════════════════════════════════════════════════════════════════
# THE HELD SIDE'S PAYOUT, WHICH NEEDS THE SIDE
# ═════════════════════════════════════════════════════════════════════
#
# `side` is the account's own ORDER INTENT, as `us_premap.intent` and
# `bettor_funded_intents.order_intent` both spell it. It is NOT derived from
# which participant the leg backs: a leg backing team B can hold either outcome
# token of a market about team B, and conflating the two is how "resolves NO"
# came to pay zero to everyone.

SIDE_LONG = "ORDER_INTENT_BUY_LONG"        # holds the YES token
SIDE_SHORT = "ORDER_INTENT_BUY_SHORT"      # holds the NO token
SIDES = (SIDE_LONG, SIDE_SHORT)

#: Which token each side holds, stated once.
_HOLDS_YES = {SIDE_LONG: True, SIDE_SHORT: False}


def side_payout_cents(resolution, side, *, basis_cents=None) -> dict:
    """What ONE contract of `side` receives under `resolution`, in cents.

    Returns {"cents": int|None, "refusal": str|None, "why": str, ...}. `cents`
    is None whenever the answer is not established, and a caller must never
    substitute a number for it.
    """
    out = {"version": VERSION, "resolution": resolution, "side": side,
           "cents": None, "refusal": None,
           "side_is": "the account's own order intent, never its team orientation"}
    if resolution is None:
        return dict(out, refusal=R_NOT_STATED,
                    why="no resolution was established for this outcome")
    if resolution == RES_STAYS_OPEN:
        return dict(out, refusal=None, cents=None, stays_open=True,
                    why=("the market stays open, so there is no payout in this "
                         "cell rather than an unknown one"))
    if resolution == RES_HALF:
        return dict(out, cents=50,
                    why="fifty cents per contract, the same on both sides")
    if resolution == RES_CENTS:
        return dict(out, refusal=R_RESOLUTION_HAS_NO_PER_CONTRACT_VALUE,
                    why=("a stated amount was recognised but not resolved to a "
                         "per-contract value; read_outcome carries it as "
                         "stated_cents"))
    if resolution == RES_REFUND:
        if basis_cents is None:
            return dict(out, refusal=R_BASIS_NOT_STATED,
                        why=("a refund returns what was PAID -- 30c on a "
                             "contract bought at 30c -- so there is no constant "
                             "and the caller must supply the basis"))
        return dict(out, cents=int(basis_cents), from_basis=True,
                    why="the contract's own basis of %dc is returned"
                        % int(basis_cents))
    if resolution in (RES_YES, RES_NO):
        if side not in SIDES:
            return dict(out, refusal=R_SIDE_NOT_STATED,
                        why=("the market resolves %s, which pays $1.00 to one "
                             "side and $0.00 to the other. Without the "
                             "account's order intent this is not a payout. V1 "
                             "returned 0 for both sides"
                             % ("YES" if resolution == RES_YES else "NO")))
        holds_yes = _HOLDS_YES[side]
        wins = holds_yes if resolution == RES_YES else not holds_yes
        return dict(out, cents=(100 if wins else 0), holds_yes=holds_yes,
                    why=("the market resolves %s and %s holds the %s token, so "
                         "it receives %dc"
                         % ("YES" if resolution == RES_YES else "NO", side,
                            "YES" if holds_yes else "NO",
                            100 if wins else 0)))
    if resolution == RES_ON_PARTIAL:
        return dict(out, refusal=R_RESOLUTION_HAS_NO_PER_CONTRACT_VALUE,
                    why=("grading on the partial result needs that result, "
                         "which is a fact about the fixture and not about the "
                         "rule"))
    return dict(out, refusal=R_RESOLUTION_HAS_NO_PER_CONTRACT_VALUE,
                why="resolution %r is not one this module prices" % resolution)


# ═════════════════════════════════════════════════════════════════════
# THE WHOLE DOCUMENT
# ═════════════════════════════════════════════════════════════════════

def interpret(prose, *, source=None, retrieved_at=None) -> dict:
    """Every outcome read separately, with provenance for the whole document."""
    raw = str(prose or "")
    rules = {o: read_outcome(raw, o) for o in OUTCOMES}
    return {
        "version": VERSION,
        "rules": rules,
        "established": sorted(o for o, r in rules.items() if r["established"]),
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


def _mentions(clause, outcome):
    """Which of `outcome`'s triggers this text names. Used as a guard, not a read.

    `bettor_indirect_structures` calls this before trusting a legacy per-outcome
    string field, so a blob that does not name the outcome cannot price it. It is
    deliberately weaker than `read_clause`: it answers "is this text about this
    outcome at all", which is the only question a field guard needs.
    """
    text = _norm(clause)
    return [p for p in TRIGGERS[outcome] if re.search(p, text)]


def describe() -> dict:
    return {
        "version": VERSION,
        "outcomes": OUTCOMES,
        "resolutions": RESOLUTIONS,
        "supported_constructions": [n for n, _ in SUPPORTED_CONSTRUCTIONS],
        "this_is_a_bounded_parser": (
            "it recognises the constructions listed above and refuses "
            "everything else. A sentence outside that grammar leaves its "
            "outcome unestablished; no claim of general prose interpretation "
            "is made"),
        "overtime_is_not_here": (
            "overtime is which INTERVAL the variable is measured over, not an "
            "exceptional payout state. bettor_venue_settlement.OVERTIME_PROSE "
            "owns it; a second reader here would give this repository two "
            "answers"),
        "a_resolution_is_not_a_payout": (
            "the prose states what the MARKET does. What a held contract "
            "receives needs the account's own order intent, and "
            "side_payout_cents is the only place the two are combined. v1 "
            "named its class RESOLVES_NO_FOR_THE_HELD_SIDE while reading a "
            "sentence about the market, and paid both sides zero"),
        "a_refund_is_not_fifty_cents": (
            "RES_REFUND has no entry in PAYOUT_CENTS on purpose. "
            "reconcile_settlement books a void as remaining_basis, so mapping a "
            "refund to 50 would make the two halves of this repository "
            "disagree about the same event"),
        "what_v1_got_wrong": {
            "negation": "'If cancelled, contracts are not refunded' -> a refund",
            "mixed_triggers": ("'A tie resolves 50-50, but cancellation is "
                               "decided separately' -> a 50c cancellation"),
            "decimals": ("'If cancelled, contracts pay $0.50' -> nothing, "
                         "because the splitter broke the amount"),
            "the_held_side": ("'If cancelled, this market resolves NO' -> 0c "
                              "for BOTH sides"),
        },
    }
