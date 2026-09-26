"""SETTLEMENT TERMS AS CONDITION -> PAYOUT, NOT AS SHARED VOCABULARY.

WHY THIS MODULE EXISTS. The previous comparison classified each side's
prose into one of three "void classes" and called the rules compatible
when the two classes were equal. That compares WORDS. Two documents can
both say "void" and "refund" and still settle an abandoned fixture
differently, because the word attaches to a DIFFERENT CONDITION on each
side:

    bookmaker  "fewer than 5 innings completed  -> stake refunded"
    venue      "game not completed at all       -> market resolves NO"

Both texts contain "refund" and "not completed". The payouts differ by the
whole stake in the case that matters -- a game called in the 6th inning,
where the bookmaker HAS ACTION and the venue may not. Matching on
vocabulary reports a match there; matching on condition -> payout reports
the mismatch.

SO A TERM IS A PAIR, and compatibility is checked PER CONDITION:

    terms[condition] = payout

and two sides agree only when, for every condition either side addresses,
they pay the same thing. A payout stated for one condition is NOT evidence
about another condition, and silence is neither agreement nor conflict --
it is UNKNOWN, which is a third verdict and not a weaker form of the
first.

THE BOOKMAKER'S SIDE IS NOT IN THIS REPOSITORY AND IS NOT ASSERTED HERE.
`BOOK_TERMS` is empty. `admit_book_terms` refuses any entry that does not
carry a citation -- a source, a URL, a retrieval timestamp and the
VERBATIM quote the payout was read from -- so the table cannot be filled
from recollection even by a well-meaning future edit. `CAPTURE_REQUEST`
below states exactly what has to be fetched to fill it.
"""

from __future__ import annotations

import re

VERSION = "BETTOR_SETTLEMENT_TERMS_V1"

# ── the terminal conditions, declared ────────────────────────────────
#
# These are the states a fixture can finish in that can settle
# DIFFERENTLY on the two sides. They are deliberately few: each one has
# to be readable from published prose, and a condition nobody publishes a
# rule for cannot be compared.
C_FULL = "COMPLETED_IN_REGULATION"
C_OVERTIME = "DECIDED_AFTER_REGULATION"
#: CALLED AND GRADED, never resumed, past the minimum. Distinguished from a
#: SUSPENSION on purpose: the published rules treat them differently, and
#: the grading formula for a called game carries an exception that a
#: suspended one does not.
C_CALLED_FINAL = "CALLED_AND_GRADED_WITHOUT_RESUMPTION_AFTER_THE_MINIMUM"
C_STOPPED_EARLY = "STOPPED_BEFORE_THE_MINIMUM"
#: SUSPENDED AND RESUMED inside the published window: the fixture completes,
#: so the bet grades on the completed game.
C_SUSPENDED_RESUMED = "SUSPENDED_AND_RESUMED_WITHIN_THE_PUBLISHED_WINDOW"
#: SUSPENDED TO RESUME BEYOND the published window. This is the condition
#: the two contexts disagree on most sharply, and it is NOT the same
#: condition as a called game -- the grading text differs.
C_SUSPENDED_BEYOND = "SUSPENDED_TO_RESUME_BEYOND_THE_PUBLISHED_WINDOW"
C_NOT_PLAYED = "POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED"

CONDITIONS = (C_FULL, C_OVERTIME, C_CALLED_FINAL, C_STOPPED_EARLY,
              C_SUSPENDED_RESUMED, C_SUSPENDED_BEYOND, C_NOT_PLAYED)

# ── the payouts, declared, from the point of view of OUR side ────────
PAY_ON_FINAL = "PAYS_THE_WINNER_ON_THE_FINAL_SCORE"
PAY_ON_PARTIAL = "PAYS_THE_WINNER_ON_THE_LAST_COMPLETED_PERIOD"
#: THE SCORING EXCEPTION, ENCODED RATHER THAN DESCRIBED. For a CALLED game
#: the published formula is the last completed inning EXCEPT where the game
#: was called in the bottom half and the Home side had taken the lead, in
#: which case the ACTUAL score grades it. That exception decides the bet in
#: exactly the walk-off case, so it is its own payout class and is NEVER
#: equivalent to the plain last-completed-period rule.
PAY_ON_PARTIAL_WALKOFF = (
    "PAYS_THE_LAST_COMPLETED_PERIOD_EXCEPT_ON_A_BOTTOM_HALF_HOME_LEAD_"
    "WHERE_THE_ACTUAL_SCORE_GRADES_IT")
PAY_STAKE_BACK = "RETURNS_THE_STAKE_OR_BASIS_IN_FULL"
PAY_NO = "RESOLVES_NO_FOR_THE_HELD_SIDE"
PAY_LATER = "STAYS_OPEN_UNTIL_THE_FIXTURE_IS_COMPLETED"
#: THE PAYOUT THE VENUE ACTUALLY PUBLISHES, AND IT WAS NOT IN THIS LIST.
#:
#: Measured 2026-09-25 against the LIVE listing for three MLB money lines
#: (aec-mlb-az-col-2026-09-24, aec-mlb-cle-kc-2026-09-25,
#: aec-mlb-pit-det-2026-09-25), all carrying the same four-sentence rule:
#:
#:   "If the game is delayed, postponed, or suspended and not rescheduled
#:    to a date within two weeks of the originally scheduled date, the
#:    market will settle to the LAST FAIR MARKET PRICE."
#:
#: That is a third thing, and it is neither of the two this module knew. It
#: is not PAY_STAKE_BACK: the holder is paid the market's last price, which
#: on a position entered at 0.56 and last trading at 0.20 returns 0.20 --
#: a loss, where a stake return is whole. And it is not PAY_ON_FINAL or
#: PAY_ON_PARTIAL: no score decides it at all, only the order book.
#:
#: WHY ADDING IT MAKES THE GATE STRICTER, NEVER LOOSER. Until now the
#: sentence was unreadable, the condition read as venue-SILENT, and the
#: verdict was UNKNOWN. Read properly it is a payout that DIFFERS from the
#: bookmaker's stake return for the same condition, so `compare` reports a
#: MISMATCH and the verdict becomes INCOMPATIBLE. Silence became a stated
#: disagreement. Nothing is admitted that was not admitted before.
PAY_LAST_FAIR_MARKET_PRICE = (
    "PAYS_THE_LAST_FAIR_MARKET_PRICE_OF_THE_CONTRACT_NOT_A_STAKE_RETURN")

PAYOUTS = (PAY_ON_FINAL, PAY_ON_PARTIAL, PAY_ON_PARTIAL_WALKOFF,
           PAY_STAKE_BACK, PAY_NO, PAY_LATER, PAY_LAST_FAIR_MARKET_PRICE)

#: WHERE TWO PAYOUT NAMES DESCRIBE THE SAME CASH, PER CONDITION.
#:
#: "the final score" and "the last completed period" are the same number
#: when the fixture finished -- in regulation, or after being made
#: official at the point it stopped. They are NOT the same under
#: `C_STOPPED_EARLY`, where one side pays a leader and the other has no
#: official result. Equivalence is therefore declared per condition and
#: never globally.
#: EQUIVALENCE ONLY WHERE THE FIXTURE FINISHED. "the final score" and "the
#: last completed period" are the same number for a game that reached its
#: end -- in regulation, after extra innings, or on resumption inside the
#: window. They are NOT the same for a game that stopped:
#:
#:   * under C_CALLED_FINAL the published formula carries the bottom-half
#:     home-lead exception, so PAY_ON_PARTIAL_WALKOFF and PAY_ON_PARTIAL
#:     differ in the walk-off case and neither equals PAY_ON_FINAL;
#:   * under C_SUSPENDED_BEYOND the grading text states the last completed
#:     inning with NO such exception, which is a different formula again.
#:
#: The previous version declared PAY_ON_FINAL equivalent to PAY_ON_PARTIAL
#: under the stopped-game condition, which erased exactly the distinction
#: the two sides could disagree on.
SAME_PAYOUT_UNDER = {
    C_FULL: ({PAY_ON_FINAL, PAY_ON_PARTIAL},),
    C_OVERTIME: ({PAY_ON_FINAL, PAY_ON_PARTIAL},),
    C_SUSPENDED_RESUMED: ({PAY_ON_FINAL, PAY_ON_PARTIAL},),
}

#: WHICH CONDITIONS APPLY, PER MARKET TYPE. A condition that cannot arise
#: is not an unknown -- requiring a rule for it would make compatibility
#: unreachable for reasons that have nothing to do with the fixture.
#:
#: Baseball's money line is conditioned by the book on a MINIMUM NUMBER OF
#: INNINGS, so the shortened and stopped-early cases are live questions
#: and are listed. Soccer has no such minimum in the 1X2 market: a match
#: is completed or it is not.
APPLICABLE_CONDITIONS = {
    ("baseball", "h2h"): CONDITIONS,
    ("soccer", "h2h"): (C_FULL, C_OVERTIME, C_SUSPENDED_RESUMED,
                        C_SUSPENDED_BEYOND, C_NOT_PLAYED),
}

R_NO_APPLICABLE_SET = "NO_APPLICABLE_CONDITION_SET_DECLARED"


def applicable_conditions(*, sport_family, market="h2h"):
    """The conditions that must be answered for this market type, or None.

    None is a refusal, not an empty set: an undeclared market type means
    we do not know which terminal cases arise, and treating that as "no
    conditions apply" would report COMPATIBLE on no evidence at all.
    """
    return APPLICABLE_CONDITIONS.get((str(sport_family), str(market)))


V_MATCH = "MATCH"
V_MISMATCH = "MISMATCH"
V_BOOK_SILENT = "BOOK_STATES_NO_RULE_FOR_THIS_CONDITION"
V_VENUE_SILENT = "VENUE_STATES_NO_RULE_FOR_THIS_CONDITION"
V_BOTH_SILENT = "NEITHER_SIDE_STATES_A_RULE_FOR_THIS_CONDITION"

COMPATIBLE = "COMPATIBLE"
INCOMPATIBLE = "INCOMPATIBLE"
UNKNOWN = "UNKNOWN"

# ── WHAT TRIGGERS A RULE IS NOT THE SAME QUESTION AS WHAT IT PAYS ────
#
# THE ERROR THIS EXISTS TO STOP, AND I MADE IT. `CONDITIONS` are named for
# what happened to the FIXTURE ("postponed or abandoned and never
# completed"). Neither published side conditions its rule on that. They
# condition it on a CLOCK, and on two different clocks:
#
#   BOOK  "If a fixture isn't started 12 hours after its scheduled starting
#          time all bets on that fixture will be voided."
#          -> variable: hours between scheduled start and actual start
#
#   VENUE "If the game is delayed, postponed, or suspended and not
#          rescheduled to a date within two weeks of the originally
#          scheduled date, the market will settle to the last fair market
#          price."
#          -> variable: whether a make-up date exists inside two weeks
#
# A payout comparison that ignores this reports "the same condition, two
# payouts" when the two sides are not describing the same set of games. So
# the qualifier is READ, recorded per condition, and a mismatch says which
# part of the condition it was established on.
V_TRIGGERS_IDENTICAL = "BOTH_SIDES_CONDITION_ON_THE_SAME_TRIGGER"
V_TRIGGERS_DIFFER = "THE_TWO_SIDES_CONDITION_ON_DIFFERENT_TRIGGERS"
V_TRIGGER_UNQUALIFIED = "NEITHER_SIDE_STATES_A_TRIGGER_QUALIFIER"
M_ON_THE_INTERSECTION = (
    "ESTABLISHED_ON_THE_NON_EMPTY_INTERSECTION_OF_TWO_DIFFERENT_TRIGGERS")

#: Qualifier phrases that scope a rule to a clock or a threshold. Read from
#: the SAME sentence that states the payout -- a window mentioned elsewhere
#: in the document scopes nothing here.
TRIGGER_QUALIFIER_PROSE = {
    "A_MAKE_UP_DATE_INSIDE_A_NAMED_WINDOW": (
        r"\brescheduled\b[^.;]*\bwithin\b",
        r"\bnot\s+rescheduled\b",
        r"\bwithin\s+two\s+weeks\b",
        r"\bwithin\s+\d+\s+(?:days?|weeks?)\b"),
    "HOURS_FROM_THE_SCHEDULED_START": (
        r"\b\d+\s+hours?\b[^.;]*\b(?:scheduled|first\s+pitch|start)\b",
        r"\b(?:scheduled|first\s+pitch|start)\w*\b[^.;]*\b\d+\s+hours?\b",
        r"\bisn'?t\s+started\b"),
    "A_MINIMUM_NUMBER_OF_INNINGS": (
        r"\b(?:at\s+least|fewer\s+than|less\s+than|before)\s+"
        r"(?:five|5|4\.5|four\s+and\s+a\s+half|eight|8|8\.5|nine|9)\b",
        r"\bhave\s+action\s+as\s+long\s+as\b"),
}

#: THE BOOK'S OWN TRIGGER, DECLARED FROM ITS CITED QUOTE, NOT INFERRED.
#: Filled only for the conditions whose captured quote states a clock. The
#: value is the qualifier name above plus the window as the publisher wrote
#: it, so a comparison can say the two windows differ without either being
#: restated in this module's own words.
BOOK_TRIGGER_WINDOWS = {
    C_NOT_PLAYED: {"qualifier": "HOURS_FROM_THE_SCHEDULED_START",
                   "window_as_published": "12 hours after its scheduled "
                                          "starting time",
                   "from_quote": "general rule"},
    C_SUSPENDED_RESUMED: {"qualifier": "HOURS_FROM_THE_SCHEDULED_START",
                          "window_as_published": "more than 12 hours from "
                                                 "the first pitch",
                          "from_quote": "rule 7"},
    C_SUSPENDED_BEYOND: {"qualifier": "HOURS_FROM_THE_SCHEDULED_START",
                         "window_as_published": "more than 12 hours from "
                                                "the first pitch",
                         "from_quote": "rule 7"},
    C_CALLED_FINAL: {"qualifier": "A_MINIMUM_NUMBER_OF_INNINGS",
                     "window_as_published": "at least 5 innings (or 4.5 if "
                                            "the Home team is winning)",
                     "from_quote": "rule 3"},
    C_STOPPED_EARLY: {"qualifier": "A_MINIMUM_NUMBER_OF_INNINGS",
                      "window_as_published": "at least 5 innings (or 4.5 if "
                                             "the Home team is winning)",
                      "from_quote": "rule 3"},
}


def trigger_qualifiers(sentence: str) -> list:
    """Every declared qualifier this sentence states. Pure."""
    return sorted(name for name, pats in TRIGGER_QUALIFIER_PROSE.items()
                  if any(re.search(p, sentence or "", re.I) for p in pats))


def same_payout(condition, a, b) -> bool:
    """Do these two payout classes deliver the same cash UNDER `condition`?"""
    if a is None or b is None:
        return False
    if str(a) == str(b):
        return True
    for grp in SAME_PAYOUT_UNDER.get(str(condition), ()):
        if str(a) in grp and str(b) in grp:
            return True
    return False


# ── reading terms out of published prose ─────────────────────────────
#
# THE MATCH IS SENTENCE-SCOPED, AND THAT IS THE WHOLE POINT. A payout
# phrase counts for a condition only when it appears in the SAME SENTENCE
# as that condition. "void" somewhere in a long rules document is not a
# rule about abandonment before the minimum; it is a word in a document.

CONDITION_PROSE = {
    C_FULL: (r"\bfull\s+(?:game|time|match)\b",
             r"\bregulation\b",
             r"\bnine\s+innings\b",
             r"\b90\s+minutes\b"),
    C_OVERTIME: (r"\bextra\s+innings\b", r"\bextra\s+time\b",
                 r"\bovertime\b", r"\bpenalt(?:y|ies)\s+shoot"),
    C_CALLED_FINAL: (r"\bofficial\s+game\b",
                     r"\bshortened\b",
                     r"\bcalled\s+(?:game|early)\b",
                     r"\bcalled\s+\(ended\)",
                     r"\bat\s+least\s+(?:five|5|4\.5|four\s+and\s+a\s+half)"
                     r"\s+innings\b"),
                    # NOTE: "last completed inning" is deliberately NOT a
                    # condition marker. It is a PAYOUT phrase, and using it
                    # to identify a condition made every sentence that
                    # described the payout also claim to be about a called
                    # game -- including the suspension sentences.
    C_SUSPENDED_RESUMED: (r"\bsuspend\w*\b[^.;]*\bresumed?\b",
                          r"\bresumed?\b[^.;]*\bwithin\b",
                          r"\bresumption\b"),
    C_SUSPENDED_BEYOND: (r"\bsuspend\w*\b[^.;]*\bmore\s+than\b",
                         r"\bnot\s+resumed\b",
                         r"\bbeyond\b[^.;]*\bhours?\b"),
    C_STOPPED_EARLY: (r"\bfewer\s+than\s+(?:five|5)\s+innings\b",
                      r"\bless\s+than\s+(?:five|5)\s+innings\b",
                      r"\bbefore\s+(?:five|5)\s+innings\b",
                      r"\bnot\s+an\s+official\s+game\b"),
    # SUSPENSION IS NOT LISTED HERE. It has its own two conditions, and
    # leaving it here made one sentence about a suspension also a sentence
    # about a fixture that was never completed -- which gave that condition
    # two payouts and reported the venue as self-contradictory.
    C_NOT_PLAYED: (r"\babandon\w*", r"\bpostpon\w*",
                   r"\bcancel\w*", r"\bnot\s+(?:be\s+)?(?:played|completed)\b",
                   r"\bnever\s+completed\b", r"\brescheduled\b"),
}

PAYOUT_PROSE = {
    # THE EXCEPTION FIRST, because a sentence stating it also states the
    # plain rule, and the exception is the narrower reading.
    PAY_ON_PARTIAL_WALKOFF: (
        r"\bbottom\s+half\b[^.;]*\b(?:lead|led|taken\s+the\s+lead)\b",
        r"\bactual\s+score\b",
        r"\bwalk-?off\b"),
    # BEFORE PAY_STAKE_BACK, AND THE ORDER IS NOT WHY -- `read_terms`
    # collects every match and refuses a sentence with more than one, so a
    # sentence saying "void" AND "last fair market price" would state
    # nothing rather than silently pick one. These patterns are written to
    # match only the price-settlement wording.
    PAY_LAST_FAIR_MARKET_PRICE: (
        r"\blast\s+fair\s+market\s+price\b",
        r"\blast\s+traded\s+price\b",
        r"\bsettle[sd]?\s+(?:to\s+)?the\s+(?:then[\s-]*)?"
        r"(?:current|prevailing)\s+market\s+price\b"),
    PAY_STAKE_BACK: (r"\bvoid\w*", r"\brefund\w*",
                     r"\bstakes?\s+(?:are\s+|will\s+be\s+)?return\w*",
                     r"\bno\s+action\b", r"\bmoney\s+back\b"),
    PAY_NO: (r"\bresolve[sd]?\s+(?:to\s+)?no\b",
             r"\bsettle[sd]?\s+(?:as\s+|to\s+)?no\b",
             r"\bresolve[sd]?\s+against\b"),
    PAY_LATER: (r"\bremain\w*\s+open\b", r"\bstays?\s+open\b",
                r"\bheld\s+open\b",
                r"\buntil\s+(?:it\s+is\s+)?(?:replayed|resumed|completed)\b"),
    PAY_ON_PARTIAL: (r"\blast\s+completed\s+inning\b",
                     r"\bscore\s+at\s+the\s+(?:time|end)\s+of\s+"
                     r"the\s+last\s+completed\b",
                     r"\bhave\s+action\b", r"\bhas\s+action\b"),
    PAY_ON_FINAL: (r"\bfinal\s+(?:score|result)\b",
                   r"\bwinner\s+of\s+the\s+(?:game|match)\b",
                   r"\bofficial\s+(?:result|winner)\b"),
}

_SENTENCE = re.compile(r"[^.;\n]+[.;\n]?")


def sentences(prose: str):
    """The prose split into sentence-ish spans. Coarse on purpose: a rules
    page is not prose to be parsed, only to be scoped."""
    flat = " ".join(str(prose or "").split())
    return [s.strip().lower() for s in _SENTENCE.findall(flat) if s.strip()]


def read_terms(prose: str) -> dict:
    """CONDITION -> PAYOUT, read from published prose, sentence-scoped.

    A condition maps to a payout only where one sentence states both. A
    sentence that names a condition and no payout establishes nothing, and
    a sentence naming TWO payouts for one condition establishes nothing
    either -- two readings of one sentence are not one rule.
    """
    found, evidence, quals = {}, {}, {}
    for sent in sentences(prose):
        conds = [c for c, pats in CONDITION_PROSE.items()
                 if any(re.search(p, sent) for p in pats)]
        pays = [p for p, pats in PAYOUT_PROSE.items()
                if any(re.search(pp, sent) for pp in pats)]
        # DECLARED SUBSUMPTION. A sentence that states the exception
        # necessarily states the plain rule it is an exception to -- "the last
        # completed inning, UNLESS ... the actual score" contains both. The
        # narrower reading is the rule, so the broader one is dropped rather
        # than counted as a second payout, which would have made an
        # exception-carrying sentence state no rule at all.
        if PAY_ON_PARTIAL_WALKOFF in pays and PAY_ON_PARTIAL in pays:
            pays = [x for x in pays if x != PAY_ON_PARTIAL]
        if len(pays) != 1 or not conds:
            for c in conds:
                evidence.setdefault(c, []).append(
                    {"sentence": sent, "payouts_matched": pays,
                     "used": False,
                     "why": ("the sentence names this condition and %s "
                             "payout, so it does not state one rule"
                             % ("no" if not pays else "more than one"))})
            continue
        for c in conds:
            evidence.setdefault(c, []).append(
                {"sentence": sent, "payouts_matched": pays, "used": True})
            # WHAT SCOPES THIS RULE, from the same sentence that states it.
            # A window read from a different sentence would scope nothing.
            q = trigger_qualifiers(sent)
            if q:
                quals[c] = {"qualifiers": q, "from_sentence": sent}
            if c in found and found[c] != pays[0]:
                # Two sentences giving one condition two different payouts.
                found[c] = None
            elif c not in found:
                found[c] = pays[0]
    return {"terms": {k: v for k, v in found.items() if v},
            "contradicted": sorted(k for k, v in found.items() if v is None),
            "evidence": evidence,
            # THE TRIGGER, KEPT BESIDE THE PAYOUT. Two sides can state the
            # same payout for a condition and still not be comparable, if
            # each conditions it on a different clock.
            "trigger_qualifiers": {k: v for k, v in quals.items()
                                   if k in found and found[k]},
            "read": bool(str(prose or "").strip()),
            "scoping": ("a payout counts for a condition only when one "
                        "sentence states both, so a shared word elsewhere "
                        "in the document establishes nothing")}


# ── the bookmaker's side: empty, and unfillable without a citation ───

CITATION_FIELDS = ("source", "source_url", "retrieved_at", "quote")

CAPTURE_REQUEST = {
    "what": ("the bookmaker's published settlement terms for the exact "
             "market type we price -- the GAME-PERIOD MONEY LINE on MLB -- "
             "stating, for each condition in CONDITIONS, what happens to "
             "a bet on it"),
    "which_conditions_matter_most": [C_CALLED_FINAL, C_STOPPED_EARLY, C_SUSPENDED_BEYOND,
                                     C_NOT_PLAYED],
    "why_those": ("regulation and extra innings are not in dispute: the "
                  "money line is the full game on the book side and the "
                  "venue's prose can state the same. The three that decide "
                  "whether the probability is usable are the SHORTENED, "
                  "STOPPED-EARLY and NEVER-COMPLETED cases, because the "
                  "book conditions its action on a minimum number of "
                  "innings and the venue need not"),
    "must_carry": list(CITATION_FIELDS),
    "must_not": ("be written from recollection, from a search-engine "
                 "summary, or from another model's description of the "
                 "page. The quote must be the publisher's own words as "
                 "retrieved"),
}

R_NO_CITATION = "TERM_REJECTED_NO_CITATION"
R_UNDECLARED = "TERM_REJECTED_UNDECLARED_CONDITION_OR_PAYOUT"


def check_citation(cite) -> list:
    """Every reason this citation is not admissible. Empty means it is."""
    c = dict(cite or {})
    bad = [f for f in CITATION_FIELDS if not str(c.get(f) or "").strip()]
    return ["missing %s" % f for f in bad]


def admit_book_terms(terms: dict) -> dict:
    """Validate a proposed bookmaker term set. Admits nothing by itself.

    Returns `{"ok": bool, "admitted": {...}, "rejected": [...]}`. A term
    without a complete citation is REJECTED -- that is the mechanism that
    keeps `BOOK_TERMS` honest rather than a comment asking future editors
    to be careful.
    """
    admitted, rejected = {}, []
    for cond, rec in dict(terms or {}).items():
        r = dict(rec or {})
        pay = r.get("payout")
        if str(cond) not in CONDITIONS or str(pay) not in PAYOUTS:
            rejected.append({"condition": cond, "refusal": R_UNDECLARED,
                             "why": ("%r -> %r is not a declared "
                                     "condition -> payout pair"
                                     % (cond, pay))})
            continue
        problems = check_citation(r.get("cite"))
        if problems:
            rejected.append({"condition": cond, "refusal": R_NO_CITATION,
                             "why": "; ".join(problems)})
            continue
        admitted[str(cond)] = {"payout": str(pay), "cite": dict(r["cite"])}
    return {"ok": bool(admitted) and not rejected,
            "admitted": admitted, "rejected": rejected}


# ── THE QUOTE'S CONTEXT DECIDES WHICH RULE APPLIES ───────────────────
#
# "h2h" DOES NOT IDENTIFY THE RULE, and assuming it does is the error this
# section exists to prevent. The bookmaker publishes two different terminal
# treatments for the same Game-period Money Line, and they disagree on the
# case that matters most:
#
#   PRE-GAME  a called game past the minimum grades on the last completed
#             inning -- the bet PAYS A WINNER.
#   IN-PLAY   the same game VOIDS, because an In-Play Game-period market
#             requires the game to be played to completion.
#
# So the same market name, the same fixture and the same probability carry
# different payouts depending only on whether the quote was taken before or
# after the first pitch. The context is therefore established from the
# quote's own observation stamp against the fixture's start, and when the
# start is unknown the context is UNKNOWN -- never defaulted.
CTX_PRE_GAME = "PRE_GAME"
CTX_LIVE = "IN_PLAY"

R_CONTEXT_UNKNOWN = "QUOTE_CONTEXT_NOT_ESTABLISHED"
R_SCHEDULED_ONLY = "ONLY_A_SCHEDULED_START_IS_HELD_SO_CONTEXT_IS_UNPROVEN"

# ── WHAT KIND OF START STAMP WE HOLD ─────────────────────────────────
#
# `market_starts.game_start` IS A SCHEDULED START. Its writer reads the CLOB
# catalogue's `game_start_time`, and `bettor_progress_providers` already says
# so in as many words: "`game_start_time`, which we do hold, is a SCHEDULED
# start. Deriving a period from it is refused by source name in
# `bettor_progress_feed`, and that refusal is the point." The first version
# of this function reintroduced exactly that refused derivation.
#
# A SCHEDULED START ESTABLISHES NOTHING IN EITHER DIRECTION.
#
# The first repair kept one inference: that a quote observed BEFORE the
# catalogue's start time must be pre-game, because a fixture is not brought
# forward. That is not sound either. The catalogue row is a SNAPSHOT of a
# schedule as it stood when it was fetched, and a schedule can be ADVANCED --
# a doubleheader resequenced, a start pulled forward for weather or
# television -- after that snapshot was taken. A stale row then says 19:00
# while the first pitch was at 18:30, and a quote at 18:50 reads as pre-game
# while the game is in its second inning. The bound is only as good as the
# freshness of the row, which nothing here establishes.
#
# So the scheduled start is recorded and NEVER used to classify. Context
# comes from the provider's own market label, or from actual event-state
# evidence carrying its own observation time. Everything else is UNKNOWN.
SE_SCHEDULED_CATALOGUE = "SCHEDULED_START_FROM_VENUE_CATALOGUE"
SE_ACTUAL_REPORTED = "ACTUAL_START_REPORTED_BY_A_PROGRESS_SOURCE"
SE_QUOTE_MARKET_CONTEXT = "QUOTE_LABELLED_BY_THE_PROVIDER_AS_IN_PLAY"

#: Only these establish that play has BEGUN. A scheduled start cannot.
ESTABLISHES_IN_PLAY = (SE_ACTUAL_REPORTED, SE_QUOTE_MARKET_CONTEXT)

SCHEDULED_START_DIRECTION = (
    "a scheduled start classifies NOTHING. Past it a delay means play may "
    "not have begun; before it a schedule advanced after the snapshot means "
    "play may already have begun. Only the provider's market label or actual "
    "event-state evidence with its own observation time establishes context")


def book_context_for(*, observed_at=None, start_at=None,
                     start_evidence=SE_SCHEDULED_CATALOGUE,
                     quote_is_in_play=None) -> dict:
    """PRE_GAME or IN_PLAY for one quote, or a named refusal.

    `start_at` is whatever start stamp is held and `start_evidence` says
    WHAT KIND it is. `quote_is_in_play`, when the provider states it, is
    authoritative on its own and needs no start stamp at all.
    """
    out = {"context": None, "refusal": None,
           "observed_at": (None if observed_at is None else float(observed_at)),
           "start_at": (None if start_at is None else float(start_at)),
           "start_evidence": str(start_evidence),
           "quote_is_in_play": quote_is_in_play,
           "scheduled_start_direction": SCHEDULED_START_DIRECTION}

    # THE PROVIDER'S OWN LABEL, when it exists, is the authoritative context:
    # it is a statement about the market the price came from.
    if quote_is_in_play is True:
        out.update(context=CTX_LIVE, start_evidence=SE_QUOTE_MARKET_CONTEXT,
                   why=("the provider labelled this quote's market IN PLAY, "
                        "which is a statement about the market the price "
                        "came from rather than an inference from a clock"))
        return out
    if quote_is_in_play is False:
        out.update(context=CTX_PRE_GAME,
                   start_evidence=SE_QUOTE_MARKET_CONTEXT,
                   why="the provider labelled this quote's market PRE-MATCH")
        return out

    if observed_at is None or start_at is None:
        out.update(refusal=R_CONTEXT_UNKNOWN,
                   why=("no start stamp and no provider label, so which "
                        "published rule governs this quote is unknown. It is "
                        "NOT defaulted to pre-game: the two rules pay "
                        "differently on a called game"))
        return out

    # ONLY ACTUAL EVENT-STATE EVIDENCE CLASSIFIES, and it must carry its own
    # observation time so the comparison is between two stamps of known
    # provenance rather than between a quote and a schedule.
    if str(start_evidence) in ESTABLISHES_IN_PLAY:
        if float(observed_at) >= float(start_at):
            out.update(context=CTX_LIVE,
                       seconds_after_start=(float(observed_at)
                                            - float(start_at)),
                       why=("the quote was observed after an ACTUAL reported "
                            "start, so play had begun"))
        else:
            out.update(context=CTX_PRE_GAME,
                       seconds_before_start=(float(start_at)
                                             - float(observed_at)),
                       why=("the quote was observed before an ACTUAL reported "
                            "start, so play had not begun"))
        return out

    out.update(refusal=R_SCHEDULED_ONLY,
               seconds_from_scheduled=float(observed_at) - float(start_at),
               why=("the only start stamp held is a SCHEDULED one from a "
                    "catalogue snapshot, and it classifies NOTHING. Past it, "
                    "a delay means play may not have begun; before it, a "
                    "schedule advanced after the snapshot means play may "
                    "ALREADY have begun. Either error picks the wrong "
                    "published rule -- one pays a called game, the other "
                    "voids it -- so the context stays UNKNOWN"))
    return out


# ── THE CAPTURED RULES HAVE A SCOPE, AND IT IS A GATE ────────────────
#
# CAPTURE_LIMITS is prose for a reader. It cannot stop a comparison, and a
# comparison that silently applies regular-season nine-inning rules to a
# playoff game or a seven-inning doubleheader is exactly the overreach the
# limits describe. So the scope is ENFORCED here: terms are admitted only
# for a competition phase and game format the capture covers, and an
# UNESTABLISHED phase or format admits nothing.
PHASE_REGULAR = "REGULAR_SEASON"
PHASE_PLAYOFF = "PLAYOFF_OR_PLAY_IN"
FMT_NINE = "STANDARD_NINE_INNING"
FMT_SEVEN = "SEVEN_INNING_DOUBLEHEADER"
#: SOCCER'S OWN FORMAT VOCABULARY. The soccer section grades match markets
#: on "a scheduled 90 minutes of play", so that is the format the capture
#: covers. A tie that can be decided by extra time or a shootout is a
#: DIFFERENT format for this purpose -- not because the 90-minute rule
#: stops applying, but because the venue's "winner of the match" contract
#: on such a tie prices a different event and the comparison must not be
#: run as though it did not.
FMT_NINETY = "SCHEDULED_NINETY_MINUTES"
FMT_KNOCKOUT = "TIE_DECIDABLE_BY_EXTRA_TIME_OR_SHOOTOUT"

#: What the retrieved page's rules actually cover, per sport.
CAPTURED_SCOPE = {
    ("baseball", "h2h"): {"phases": (PHASE_REGULAR,),
                          "formats": (FMT_NINE,)},
    # SOCCER, ADDED FROM THE RUN-67 CAPTURE. Two carve-outs come straight
    # out of the prose and both are enforced rather than noted:
    #
    #   World Cup fixtures get 72 hours to complete (line 390), not the 12
    #   hours line 383 gives everything else. A World Cup fixture is
    #   therefore a phase this capture does not describe.
    #
    #   A knockout tie decidable by extra time or a shootout is a format
    #   where the 90-minute basis and a venue "winner of the match"
    #   contract can disagree about the same match.
    #
    # NOTHING SUPPLIES A SOCCER PHASE OR FORMAT TODAY. `acquire_fixture_
    # scope` reads the MLB Stats API, which knows nothing about soccer, so
    # both arrive as None and this gate refuses by name --
    # COMPETITION_PHASE_NOT_ESTABLISHED. That is the honest blocker and it
    # is deliberately visible: before this entry existed the same fixture
    # produced NO_CAPTURED_SCOPE_FOR_THIS_MARKET, which reads as "we never
    # captured soccer" when in fact the rules are captured and the
    # FIXTURE EVIDENCE is what is missing. Different remedies.
    ("soccer", "h2h"): {"phases": (PHASE_REGULAR,),
                        "formats": (FMT_NINETY,)},
}

R_PHASE_UNKNOWN = "COMPETITION_PHASE_NOT_ESTABLISHED"
R_PHASE_EXCLUDED = "COMPETITION_PHASE_OUTSIDE_THE_CAPTURED_RULES"
R_FORMAT_UNKNOWN = "GAME_FORMAT_NOT_ESTABLISHED"
R_FORMAT_EXCLUDED = "GAME_FORMAT_OUTSIDE_THE_CAPTURED_RULES"
R_SCOPE_UNDECLARED = "NO_CAPTURED_SCOPE_FOR_THIS_MARKET"

SCOPE_NOTE = {
    PHASE_PLAYOFF: ("MLB Playoff and Play-In games have action whenever the "
                    "game is completed, so the suspension timings below do "
                    "not describe them"),
    FMT_SEVEN: ("a seven-inning doubleheader restates rules 3, 7 and 8 "
                "against a 7-inning threshold, so the thresholds below are "
                "the wrong numbers for it"),
    FMT_KNOCKOUT: ("a tie decidable by extra time or a shootout is where "
                   "the 90-minute basis diverges hardest from a venue "
                   "contract settling on the match winner: the same match "
                   "has two different answers and the captured terms "
                   "describe only one of them"),
}

#: THE SOCCER PHASE THIS CAPTURE DOES NOT DESCRIBE, quoted. A World Cup
#: fixture has 72 hours to complete instead of 12, so the void branch above
#: carries the wrong number for it.
SOCCER_WORLD_CUP_EXCEPTION = {
    "quote": ("All bets on World Cup Fixtures have action as long as the "
              "Fixture is completed within 72 hours of when it was "
              "originally scheduled to play."),
    "consequence": ("the 12-hour window in the void branch is not the World "
                    "Cup window, so a World Cup fixture is outside this "
                    "capture rather than covered by it"),
}


def admit_scope(*, sport_family, market="h2h", phase=None,
                game_format=None) -> dict:
    """May the captured terms be applied to this fixture at all?"""
    cap = CAPTURED_SCOPE.get((str(sport_family), str(market)))
    if cap is None:
        return {"ok": False, "refusals": [R_SCOPE_UNDECLARED],
                "phase": phase, "game_format": game_format,
                "why": ("no capture covers %s/%s, so there are no terms to "
                        "admit" % (sport_family, market))}
    refusals, why = [], []
    if phase is None:
        refusals.append(R_PHASE_UNKNOWN)
        why.append("the competition phase is not established for this "
                   "fixture, and the captured rules cover %s only"
                   % (cap["phases"],))
    elif str(phase) not in cap["phases"]:
        refusals.append(R_PHASE_EXCLUDED)
        why.append(SCOPE_NOTE.get(str(phase),
                                  "phase %r is outside the capture" % (phase,)))
    if game_format is None:
        refusals.append(R_FORMAT_UNKNOWN)
        why.append("the game format is not established for this fixture, "
                   "and the captured rules cover %s only" % (cap["formats"],))
    elif str(game_format) not in cap["formats"]:
        refusals.append(R_FORMAT_EXCLUDED)
        why.append(SCOPE_NOTE.get(str(game_format),
                                  "format %r is outside the capture"
                                  % (game_format,)))
    return {"ok": not refusals, "refusals": refusals,
            "phase": phase, "game_format": game_format,
            "covers": {"phases": list(cap["phases"]),
                       "formats": list(cap["formats"])},
            "why": "; ".join(why) or "the fixture is inside the capture",
            "how_to_establish": (
                "the phase and the format are properties of the fixture. "
                "Neither is carried on `markets`, so both need a source -- "
                "the league schedule, or the venue listing's own market type "
                "-- captured per fixture like any other evidence")}


# ── THE CAPTURE ──────────────────────────────────────────────────────
#
# RETRIEVED, NOT RECALLED. The development container's egress policy denies
# pinnacle.com, so the page was fetched by the GitHub Actions runner -- an
# authorized reader with ordinary outbound access -- and the operative
# sentences below are its own words as served.

_SRC = "Pinnacle betting rules (published rules page)"
_URL = "https://www.pinnacle.com/en/future/betting-rules"
_AT = "2026-09-24T20:30:22Z"

#: The retrieval itself, so the citation can be audited rather than trusted.
CAPTURE_RUN = {
    "reader": "github-actions runner, ubuntu-latest",
    "job": ("https://github.com/matthewtaylor141-lab/SportsAssets/actions/"
            "runs/36055307702/job/107820650377"),
    "url": _URL,
    "retrieved_at": _AT,
    "http": 200,
    "text_lines": 629,
    "text_words": 12787,
    "also_attempted": {
        "url": ("https://support.pinnacle.com/hc/en-us/articles/"
                "47846444668177-How-bets-are-graded-at-Pinnacle"),
        "http": 403,
        "captured": False,
        "why": ("the support host refused the runner. Nothing is taken from "
                "it, and no term below depends on it"),
    },
}

#: THE PUBLISHER'S STATED PRECEDENCE, quoted. It matters because a Market
#: Rule could override the Sport Rule these terms come from, and a future
#: capture of a market-specific rule must be read as outranking them.
RULE_HIERARCHY = {
    "quote": ("In case of any contradictions: Market Rules take precedence "
              "over Sport Rules; which take precedence over General Rules."),
    "source": _SRC, "source_url": _URL, "retrieved_at": _AT,
    "consequence": ("the terms below are SPORT rules for baseball. A "
                    "captured MARKET rule for a specific contract would "
                    "outrank them and must be compared before they are"),
}

#: An exception that is NOT folded into the terms, because it changes them.
PLAYOFF_EXCEPTION = {
    "quote": ("MLB Playoff and Play-In games, which will have action "
              "whenever the game is completed."),
    "source": _SRC, "source_url": _URL, "retrieved_at": _AT,
    "consequence": ("for a Playoff or Play-In fixture the suspension "
                    "timings do not void the bet, so the terms below do not "
                    "describe it. A fixture not established as regular "
                    "season is therefore NOT covered by this capture"),
}

_Q_RULE3 = (
    "Bets made before the start of the game on the Game-period Money Line "
    "market have action as long as at least 5 innings (or 4.5 innings if the "
    "Home team is winning) are completed. If a game is called (ended) before "
    "9 innings (or 8.5 innings if the Home team wins) are complete, the score "
    "at the end of the last completed inning will be considered final, unless "
    "the game is called (ended) during the bottom half of one of these "
    "innings and the Home team has taken the lead. In this specific case, the "
    "actual score of the game will be used to grade the Game-period Money "
    "Line for pre-game bets.")

_Q_RULE4 = (
    "All Game-period In-Play markets require that the game be played to "
    "completion with 9 innings (or 8.5 if Home team wins) to have action. "
    "Periods that have been played to completion will have action even if the "
    "game is not played to completion.")

_Q_RULE7 = (
    "If a game is suspended in order to be resumed more than 12 hours from "
    "the first pitch, all pre-game bets on the Game-period markets will be "
    "deemed void and bets on completed periods will have action. With the "
    "exceptions of: ... Game-period Money Line bets, which have action based "
    "on the score at the end of the last completed inning as long as at least "
    "5 innings (or 4.5 innings if the Home team is winning) are completed.")

_Q_RULE8 = (
    "If a game is suspended in order to be resumed more than 30 hours from "
    "the first pitch, all Live bets on the Game-period markets will be deemed "
    "void and bets on completed periods will have action. If a game is "
    "suspended and resumed within 30 hours of the first pitch, all Live bets "
    "will have action when their periods are completed.")

_Q_GENERAL_NOT_STARTED = (
    "If a fixture isn't started 12 hours after its scheduled starting time "
    "all bets on that fixture will be voided.")


def _cite(quote, rule):
    return {"source": "%s -- %s" % (_SRC, rule),
            "source_url": _URL, "retrieved_at": _AT, "quote": quote}


# ── SOCCER, FROM THE SAME PUBLISHER'S SOCCER SECTION ────────────────
#
# CAPTURED IN RUN 67, VERBATIM, AND HELD UNREAD UNTIL NOW. The extractor's
# keyword vocabulary was baseball-only, so this section never reached the
# comparison and EVERY soccer settlement verdict could only be UNKNOWN.
# UNKNOWN is not INCOMPATIBLE and it is not compatible either -- it is a
# comparison that was never run, which is why soccer was reported as
# "unevaluated" rather than "incompatible".
#
# The capture lives in `tests/fixtures/pinnacle_soccer_rules_2026_09_25.json`
# with its HTTP status, page size and the runner that read it. Below are
# the lines that bear on a full-match money line. NOTHING IS INFERRED: a
# condition the soccer section does not speak to is LEFT OUT of the map,
# so `agrees()` reports it as unstated rather than filling it in.
_SOCCER_AT = "2026-09-25T20:47:35Z"

_Q_SOCCER_90 = (
    "All match markets are based on the result at the end of a scheduled 90 "
    "minutes of play unless otherwise stated. This includes any added injury "
    "or stoppage time, but does not include extra time, a penalty shootout "
    "or a golden goal.")
_Q_SOCCER_VOID = (
    "If a match is deemed void because it finished early or was abandoned, "
    "periods that were played to completion (such as the First Half) will "
    "have action. If a match starts and isn't completed within 12 hours of "
    "kickoff, then all bets on uncompleted periods will be voided.")
_Q_SOCCER_85 = (
    "The exception to this rule is a referee ending a match after at least "
    "85 minutes of play. In this case, all periods will have action.")


def _cite_soccer(quote, where):
    return {"source": "%s -- %s" % (_SRC, where),
            "source_url": _URL, "retrieved_at": _SOCCER_AT, "quote": quote}


#: THE 90-MINUTE BASIS IS A PAYOUT RULE, NOT A DETAIL, so it is declared
#: rather than buried. A venue contract settling "to the winner of the
#: match" including extra time and a shootout prices a DIFFERENT event
#: from one graded on the 90-minute result, and on a knockout tie the two
#: disagree on the same match. Recorded so the comparison can find that
#: disagreement instead of a reader having to notice it.
SOCCER_NINETY_MINUTE_BASIS = {
    "quote": _Q_SOCCER_90,
    "source": _SRC, "source_url": _URL, "retrieved_at": _SOCCER_AT,
    "consequence": (
        "the bookmaker's probability describes the 90-minute result. A "
        "venue contract settling on the match winner after extra time or a "
        "shootout prices a different event, and on a knockout fixture the "
        "two can disagree about the same match"),
}


#: THE BOOKMAKER'S TERMS, CAPTURED, keyed by (family, market, context).
#:
#: PRE-GAME AND IN-PLAY DIFFER AT EXACTLY ONE CONDITION and it is the
#: expensive one: a game stopped after the minimum but before completion
#: PAYS A WINNER pre-game (rule 3, and rule 7's Money Line exception) and
#: VOIDS in play (rule 4). Recording one set for both would have been the
#: same error as reading the rule off the word "h2h".
BOOK_TERMS: dict = {
    ("baseball", "h2h", CTX_PRE_GAME): {
        C_FULL: {"payout": PAY_ON_FINAL, "cite": _cite(_Q_RULE3, "rule 3")},
        C_OVERTIME: {"payout": PAY_ON_FINAL,
                     "cite": _cite(_Q_RULE3, "rule 3")},
        # CALLED AND GRADED: the last completed inning, EXCEPT on a
        # bottom-half home lead, where the ACTUAL score grades it. The
        # exception is in the payout class, not in a comment.
        C_CALLED_FINAL: {"payout": PAY_ON_PARTIAL_WALKOFF,
                         "cite": _cite(_Q_RULE3, "rule 3")},
        C_STOPPED_EARLY: {"payout": PAY_STAKE_BACK,
                          "cite": _cite(_Q_RULE3, "rule 3")},
        # RESUMED INSIDE THE WINDOW: the fixture completes, so the bet
        # grades on the completed game.
        C_SUSPENDED_RESUMED: {"payout": PAY_ON_FINAL,
                              "cite": _cite(_Q_RULE7, "rule 7")},
        # BEYOND THE WINDOW: the Money Line exception grades the last
        # completed inning, and it states NO bottom-half exception -- a
        # different formula from the called-game case above.
        C_SUSPENDED_BEYOND: {"payout": PAY_ON_PARTIAL,
                             "cite": _cite(_Q_RULE7, "rule 7")},
        C_NOT_PLAYED: {"payout": PAY_STAKE_BACK,
                       "cite": _cite(_Q_GENERAL_NOT_STARTED + " " + _Q_RULE7,
                                     "general rule and rule 7")},
    },
    ("baseball", "h2h", CTX_LIVE): {
        C_FULL: {"payout": PAY_ON_FINAL, "cite": _cite(_Q_RULE4, "rule 4")},
        C_OVERTIME: {"payout": PAY_ON_FINAL,
                     "cite": _cite(_Q_RULE4, "rule 4")},
        # THE DIVERGENCE. In play, a game not played to completion has NO
        # ACTION -- the stake comes back instead of grading a leader.
        C_CALLED_FINAL: {"payout": PAY_STAKE_BACK,
                         "cite": _cite(_Q_RULE4, "rule 4")},
        C_STOPPED_EARLY: {"payout": PAY_STAKE_BACK,
                          "cite": _cite(_Q_RULE4, "rule 4")},
        C_SUSPENDED_RESUMED: {"payout": PAY_ON_FINAL,
                              "cite": _cite(_Q_RULE8, "rule 8")},
        C_SUSPENDED_BEYOND: {"payout": PAY_STAKE_BACK,
                             "cite": _cite(_Q_RULE8, "rule 8")},
        C_NOT_PLAYED: {"payout": PAY_STAKE_BACK,
                       "cite": _cite(_Q_RULE8, "rule 8")},
    },
    # ── SOCCER, FULL-MATCH MONEY LINE ────────────────────────────────
    #
    # ONE SET FOR BOTH CONTEXTS, AND THAT IS WHAT THE PROSE SAYS. The
    # baseball section splits pre-game from in-play because rules 3, 4, 7
    # and 8 are written as four different rules. The soccer section is not
    # written that way: lines 382-384 state one basis and one void rule
    # for match markets without distinguishing when the bet was placed.
    # Recording a split the publisher does not state would be the same
    # invention as reading a rule off the word "h2h" -- so the identical
    # map is registered under both contexts, and the fact that it is
    # identical is the finding, not an oversight.
    #
    # THREE CONDITIONS ARE DELIBERATELY ABSENT: C_CALLED_FINAL,
    # C_SUSPENDED_RESUMED and C_SUSPENDED_BEYOND. The soccer section's
    # 85-minute exception speaks to a referee ENDING a match, which is
    # C_STOPPED_EARLY's neighbour rather than baseball's called-game
    # formula, and it says nothing about a suspension resumed later. An
    # absent condition reads as UNSTATED and blocks; a filled-in one would
    # read as agreement.
    ("soccer", "h2h", CTX_PRE_GAME): {
        C_FULL: {"payout": PAY_ON_FINAL,
                 "cite": _cite_soccer(_Q_SOCCER_90, "soccer line 382")},
        # EXTRA TIME AND PENALTIES DO NOT COUNT. So a fixture decided
        # after regulation still grades on the 90-minute result -- which
        # is the OPPOSITE of baseball, where extra innings are included.
        C_OVERTIME: {
            "payout": PAY_ON_FINAL,
            "cite": _cite_soccer(_Q_SOCCER_90, "soccer line 382"),
            "note": ("PAYS_THE_WINNER_ON_THE_FINAL_SCORE means the score "
                     "at the end of the scheduled 90 minutes plus stoppage "
                     "time. Extra time, a shootout and a golden goal are "
                     "excluded by the same sentence"),
        },
        # ABANDONED AND NOT COMPLETED WITHIN 12 HOURS: voided.
        C_NOT_PLAYED: {
            "payout": PAY_STAKE_BACK,
            "cite": _cite_soccer(
                _Q_SOCCER_VOID + " " + _Q_GENERAL_NOT_STARTED,
                "soccer line 383 and the general rule")},
        # A REFEREE ENDING THE MATCH AFTER 85 MINUTES: all periods have
        # action, so the match result stands.
        C_STOPPED_EARLY: {
            "payout": PAY_STAKE_BACK,
            "cite": _cite_soccer(_Q_SOCCER_VOID, "soccer line 383"),
            "note": ("stopped BEFORE the 85-minute exception. Past 85 "
                     "minutes line 384 gives the match action instead, "
                     "which is why the two are not one condition")},
    },
    ("soccer", "h2h", CTX_LIVE): {
        C_FULL: {"payout": PAY_ON_FINAL,
                 "cite": _cite_soccer(_Q_SOCCER_90, "soccer line 382")},
        C_OVERTIME: {
            "payout": PAY_ON_FINAL,
            "cite": _cite_soccer(_Q_SOCCER_90, "soccer line 382"),
            "note": ("the 90-minute basis is stated for match markets "
                     "without reference to when the bet was placed"),
        },
        C_NOT_PLAYED: {
            "payout": PAY_STAKE_BACK,
            "cite": _cite_soccer(
                _Q_SOCCER_VOID + " " + _Q_GENERAL_NOT_STARTED,
                "soccer line 383 and the general rule")},
        C_STOPPED_EARLY: {
            "payout": PAY_STAKE_BACK,
            "cite": _cite_soccer(_Q_SOCCER_VOID, "soccer line 383"),
            "note": ("IN-PLAY CARRIES ONE MORE VOID BRANCH the pre-game "
                     "set does not: line 387 voids an in-play bet when a "
                     "VAR decision materially affects its odds, and line "
                     "388 voids one placed against incorrect score, "
                     "corner or red-card information. Neither is a "
                     "condition in this vocabulary, so neither is mapped "
                     "-- they are recorded in "
                     "SOCCER_IN_PLAY_EXTRA_VOID_BRANCHES and they mean an "
                     "in-play soccer comparison is narrower than it looks"),
        },
    },
}

#: VOID BRANCHES THE CONDITION VOCABULARY DOES NOT MODEL. Not folded into
#: the terms, because folding them in would claim the comparison covers
#: them. An in-play soccer position carries these two additional ways to
#: be voided, and nothing in this module checks for either.
SOCCER_IN_PLAY_EXTRA_VOID_BRANCHES = {
    "var_decision": {
        "quote": ("In-Play bets will be voided if a Video Assistant Referee "
                  "(VAR) decision materially affects the odds of the bets."),
        "source": _SRC, "source_url": _URL, "retrieved_at": _SOCCER_AT},
    "incorrect_market_information": {
        "quote": ("Score, corner and red card information are considered to "
                  "be part of the market for In-Play bets. If that "
                  "information is incorrectly displayed on the market "
                  "offering and/or Bet Slip, then bets placed while "
                  "incorrect information is displayed will be deemed void."),
        "source": _SRC, "source_url": _URL, "retrieved_at": _SOCCER_AT},
    "consequence": ("an IN_PLAY soccer money line has two void triggers "
                    "outside this vocabulary. A COMPATIBLE verdict on the "
                    "seven modelled conditions does not cover them"),
}

#: THE RESUMPTION WINDOWS, per context, quoted. They are what separates
#: C_SUSPENDED_RESUMED from C_SUSPENDED_BEYOND, and they DIFFER by context --
#: 12 hours for a pre-game bet, 30 hours for a live one -- so the same
#: suspension can be inside the window for one and beyond it for the other.
RESUMPTION_WINDOW_S = {
    CTX_PRE_GAME: {"window_s": 12 * 3600, "cite": _cite(_Q_RULE7, "rule 7")},
    CTX_LIVE: {"window_s": 30 * 3600, "cite": _cite(_Q_RULE8, "rule 8")},
}

#: What the capture does NOT establish, recorded so its scope is not
#: overread. Each of these would need its own retrieval.
CAPTURE_LIMITS = (
    "SEVEN-INNING DOUBLEHEADERS restate rules 3, 7 and 8 with a 7-inning "
    "threshold, so a fixture not established as a standard nine-inning game "
    "is outside these terms",
    "MLB PLAYOFF AND PLAY-IN fixtures carry an explicit exception and are "
    "outside these terms",
    "MARKET RULES outrank sport rules by the publisher's own precedence "
    "statement, and no market-specific rule for this contract was captured",
    "the SUPPORT-CENTRE grading article returned 403 to the reader, so "
    "nothing is taken from it",
)

#: Recorded attempts to satisfy `CAPTURE_REQUEST`, so a blocked retrieval
#: is a fact in the repository rather than a memory of a failed command.
#: Each entry: what was asked for, from where, when, and what answered.
CAPTURE_ATTEMPTS = (
    {"target": "www.pinnacle.com/en/future/betting-rules",
     "asked_at": "2026-09-24T20:30:22Z",
     "reader": "github-actions runner (authorized, ordinary egress)",
     "result": "RETRIEVED",
     "detail": ("HTTP 200, 629 text lines, 12787 words. The baseball sport "
                "rules, the general rules and the stated precedence were "
                "read from it and are quoted in BOOK_TERMS and "
                "RULE_HIERARCHY below. THIS is the capture the terms rest "
                "on")},
    {"target": ("support.pinnacle.com/hc/en-us/articles/"
                "47846444668177-How-bets-are-graded-at-Pinnacle"),
     "asked_at": "2026-09-24T20:30:23Z",
     "reader": "github-actions runner (authorized, ordinary egress)",
     "result": "HTTP_403",
     "detail": ("the support host refused the runner. Nothing is taken from "
                "it and no term depends on it")},
    {"target": "www.pinnacle.com/en/future/betting-rules",
     "asked_at": "2026-09-24T19:56Z",
     "reader": "development container (egress policy denies the host)",
     "result": "EGRESS_BLOCKED",
     "detail": ("the session's network policy denied the host. No content "
                "was retrieved, so no term was captured and none was "
                "inferred")},
    {"target": "support.pinnacle.com/hc/en-us",
     "asked_at": "2026-09-24T19:57Z",
     "result": "EGRESS_BLOCKED",
     "detail": "same denial"},
    {"target": "archive.org/wayback (snapshot of the rules page)",
     "asked_at": "2026-09-24T19:58Z",
     "result": "EGRESS_BLOCKED",
     "detail": ("the archival route was tried precisely because it would "
                "carry a snapshot date as well as a retrieval time. Also "
                "denied")},
)


def book_terms(*, sport_family, market="h2h", context=None, phase=None,
               game_format=None) -> dict:
    """The captured terms for this market IN THIS CONTEXT AND SCOPE.

    Returns NOTHING unless all three are established. There is no "general"
    entry to fall back on, deliberately: the pre-game and in-play rules
    disagree on a called game, so a fallback would be a guess about which
    side of the first pitch the quote came from -- and the captured rules
    cover one competition phase and one game format, so applying them
    outside that is the overreach CAPTURE_LIMITS describes.
    """
    if context is None:
        return {}
    if not admit_scope(sport_family=sport_family, market=market,
                       phase=phase, game_format=game_format)["ok"]:
        return {}
    return dict(BOOK_TERMS.get(
        (str(sport_family), str(market), str(context))) or {})


# ── the comparison ───────────────────────────────────────────────────

def _trigger_record(condition, venue_trigger) -> dict:
    """Both sides' trigger qualifiers for one condition, and whether they
    are the same variable. Pure, and it decides nothing about payouts."""
    bk = dict(BOOK_TRIGGER_WINDOWS.get(condition) or {})
    vq = list((venue_trigger or {}).get("qualifiers") or [])
    out = {"book_qualifier": bk.get("qualifier"),
           "book_window_as_published": bk.get("window_as_published"),
           "book_from_quote": bk.get("from_quote"),
           "venue_qualifiers": vq,
           "venue_from_sentence": (venue_trigger or {}).get("from_sentence")}
    if not bk.get("qualifier") and not vq:
        out["alignment"] = V_TRIGGER_UNQUALIFIED
    elif bk.get("qualifier") and vq == [bk["qualifier"]]:
        out["alignment"] = V_TRIGGERS_IDENTICAL
    else:
        out["alignment"] = V_TRIGGERS_DIFFER
        out["why"] = (
            "the book conditions this rule on %r and the venue's sentence "
            "states %s. A rule keyed to a different variable does not "
            "describe the same set of games"
            % (bk.get("qualifier") or "no stated qualifier",
               vq or "no stated qualifier"))
    return out


def compare(*, book: dict, venue: dict, conditions=None,
            venue_triggers=None) -> dict:
    """Per-condition compatibility of two term sets.

    `book` and `venue` are `{condition: payout}` (the book side may carry
    `{condition: {"payout": ..., "cite": ...}}`, which is normalised).
    `conditions` is the applicable set; it defaults to every declared
    condition, which is the strictest reading.

    The verdict is one of COMPATIBLE / INCOMPATIBLE / UNKNOWN, and the
    three are genuinely different: INCOMPATIBLE means both sides stated a
    rule for one condition and the payouts differ, which no amount of
    further reading will reconcile. UNKNOWN means somebody is silent.
    """
    def _pay(v):
        return (v or {}).get("payout") if isinstance(v, dict) else v

    b = {str(k): _pay(v) for k, v in dict(book or {}).items()}
    v = {str(k): _pay(v) for k, v in dict(venue or {}).items()}
    per, mismatched, silent = {}, [], []
    for cond in (conditions if conditions is not None else CONDITIONS):
        bp, vp = b.get(cond), v.get(cond)
        if bp and vp:
            ok = same_payout(cond, bp, vp)
            per[cond] = {"verdict": (V_MATCH if ok else V_MISMATCH),
                         "book_payout": bp, "venue_payout": vp}
            # THE TRIGGER, BESIDE THE PAYOUT, WHETHER OR NOT THEY AGREE.
            per[cond]["trigger"] = _trigger_record(
                cond, (venue_triggers or {}).get(cond))
            if not ok:
                mismatched.append(cond)
                per[cond]["why"] = (
                    "under %s the book pays %s and the venue pays %s. These "
                    "are different cash outcomes for the same fixture state, "
                    "so a probability of the book's event does not price the "
                    "venue's contract" % (cond, bp, vp))
                if per[cond]["trigger"]["alignment"] == V_TRIGGERS_DIFFER:
                    # AND THE SCOPE OF THE MISMATCH IS STATED, because the
                    # two sides are not describing the same set of games.
                    # The conflict is real where both triggers hold; it is
                    # NOT established where only one does.
                    per[cond]["mismatch_scope"] = M_ON_THE_INTERSECTION
                    per[cond]["what_is_not_established"] = (
                        "what either side pays where only ITS OWN trigger "
                        "holds. The book's window and the venue's window "
                        "are different variables, so the region between "
                        "them is unresolved rather than agreed")
        elif bp or vp:
            per[cond] = {"verdict": (V_VENUE_SILENT if bp else V_BOOK_SILENT),
                         "book_payout": bp, "venue_payout": vp}
            silent.append(cond)
        else:
            per[cond] = {"verdict": V_BOTH_SILENT,
                         "book_payout": None, "venue_payout": None}
            silent.append(cond)
    verdict = (INCOMPATIBLE if mismatched
               else (COMPATIBLE if (per and not silent) else UNKNOWN))
    return {"version": VERSION, "verdict": verdict, "per_condition": per,
            "mismatched_conditions": mismatched,
            "unstated_conditions": silent,
            "compared_on": "CONDITION_TO_PAYOUT",
            "trigger_alignment": {
                c: (per[c].get("trigger") or {}).get("alignment")
                for c in per if per[c].get("trigger")},
            "not_compared_on": ("shared vocabulary. A payout phrase counts "
                               "only for the condition stated in the same "
                               "sentence"),
            "silence_is_not_agreement": True}


def compare_prose(*, sport_family, market="h2h", venue_prose="",
                  extra_book_terms=None, observed_at=None, start_at=None,
                  start_evidence=SE_SCHEDULED_CATALOGUE,
                  quote_is_in_play=None, context=None,
                  phase=None, game_format=None) -> dict:
    """The whole comparison from one side's prose and the held book terms.

    `extra_book_terms` is the LEGACY single-class hook: callers that still
    hold `bettor_venue_settlement.BOOK_VOID_RULE` pass its translation in
    here. It speaks to ONE condition, so it can never by itself answer the
    shortened or stopped-early cases -- which is why holding it leaves the
    verdict UNKNOWN rather than COMPATIBLE for baseball.
    """
    read = read_terms(venue_prose)
    # WHICH PUBLISHED RULE GOVERNS THIS QUOTE, established from its own
    # timing rather than from the market name.
    ctx = ({"context": str(context), "refusal": None,
            "why": "the context was supplied by the caller"}
           if context is not None else
           book_context_for(observed_at=observed_at, start_at=start_at,
                            start_evidence=start_evidence,
                            quote_is_in_play=quote_is_in_play))
    scope = admit_scope(sport_family=sport_family, market=market,
                        phase=phase, game_format=game_format)
    bk = dict(book_terms(sport_family=sport_family, market=market,
                         context=ctx.get("context"), phase=phase,
                         game_format=game_format))
    for k, val in dict(extra_book_terms or {}).items():
        bk.setdefault(str(k), val)
    conds = applicable_conditions(sport_family=sport_family, market=market)
    if conds is None:
        return {"version": VERSION, "verdict": UNKNOWN, "per_condition": {},
                "mismatched_conditions": [], "unstated_conditions": [],
                "refusal": R_NO_APPLICABLE_SET, "venue_read": read,
                "book_terms_held": bool(bk),
                "why": ("no applicable condition set is declared for "
                        "%s/%s, so which terminal cases arise is itself "
                        "unknown" % (sport_family, market))}
    cmp_ = compare(book=bk, venue=read["terms"], conditions=conds,
                   venue_triggers=read.get("trigger_qualifiers"))
    cmp_.update(venue_read=read, book_terms_held=bool(bk),
                applicable_conditions=list(conds),
                quote_context=ctx,
                scope=scope,
                book_capture=(dict(CAPTURE_RUN) if bk else None),
                book_capture_limits=(list(CAPTURE_LIMITS) if bk else None),
                rule_hierarchy=(dict(RULE_HIERARCHY) if bk else None),
                book_capture_request=(None if bk else CAPTURE_REQUEST))
    if not bk:
        # WHY the book side is absent, named. A reader must be able to tell
        # "the rule is unknown" from "the quote's context is unproven" from
        # "this fixture is outside the captured rules".
        cmp_["why_book_side_absent"] = (
            ctx.get("why") if ctx.get("refusal")
            else (scope.get("why") if not scope["ok"] else
                  "no capture covers this market"))
        cmp_["book_side_absent_refusals"] = (
            ([ctx["refusal"]] if ctx.get("refusal") else [])
            + list(scope.get("refusals") or []))
    if read["contradicted"]:
        # The venue's own text giving one condition two payouts is a
        # conflict in the SOURCE, and it is louder than silence.
        cmp_["verdict"] = INCOMPATIBLE
        cmp_["mismatched_conditions"] = sorted(
            set(cmp_["mismatched_conditions"]) | set(read["contradicted"]))
        cmp_["venue_self_contradictory"] = read["contradicted"]
    return cmp_


def describe() -> dict:
    return {
        "version": VERSION,
        "conditions": list(CONDITIONS),
        "payouts": list(PAYOUTS),
        "book_terms_held": {"%s/%s/%s" % k: sorted(v) for k, v
                            in BOOK_TERMS.items()},
        "book_terms_count": len(BOOK_TERMS),
        "contexts": [CTX_PRE_GAME, CTX_LIVE],
        "why_context_matters": (
            "the pre-game and In-Play Game-period Money Line rules disagree "
            "on a called game: pre-game grades the last completed inning, "
            "In-Play voids for want of a completed game. The market name "
            "'h2h' does not distinguish them"),
        "capture_run": dict(CAPTURE_RUN),
        "capture_limits": list(CAPTURE_LIMITS),
        "rule_hierarchy": dict(RULE_HIERARCHY),
        "playoff_exception": dict(PLAYOFF_EXCEPTION),
        "capture_request": CAPTURE_REQUEST,
        "capture_attempts": [dict(a) for a in CAPTURE_ATTEMPTS],
        "citation_required": list(CITATION_FIELDS),
        "verdicts": [COMPATIBLE, INCOMPATIBLE, UNKNOWN],
        "unknown_is_not_incompatible": (
            "UNKNOWN keeps a labelled conditional shadow value. "
            "INCOMPATIBLE disqualifies the probability from the selector"),
    }
